"""Hikari command-line interface.

Entry point: ``hikari`` (defined in pyproject.toml [project.scripts])

Commands
--------
hikari run       – Process a directory of FITS files (auto-detect targets)
hikari reduce    – Process files for a specific star + filter
hikari dashboard – Start the web dashboard
hikari config    – Generate a default config file
hikari jobs      – List recent batch jobs (quick table view)
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from typing import Optional

import typer
import uvicorn
from loguru import logger
from rich import print as rprint
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from hikari.config import HikariSettings, load_settings, write_default_config
from hikari.core.catalog import download_comparison_stars, resolve_target
from hikari.core.fits_reader import discover_fits_files, group_by_object_and_filter
from hikari.core.models import AavsoObservation, PhotometricFilter
from hikari.core.pipeline import BatchResult, process_batch
from hikari.output.aavso import calibrated_to_observation, write_aavso_csv
from hikari.output.database import (
    init_db,
    get_session,
    list_jobs,
    save_batch_job,
    save_observation,
)

console = Console()
app = typer.Typer(
    name="hikari",
    help="Variable star photometry for AAVSO reporting.",
    add_completion=False,
)

# ---------------------------------------------------------------------------
# Shared config option
# ---------------------------------------------------------------------------

CONFIG_OPTION = typer.Option(None, "--config", "-c", help="Path to hikari.toml or hikari.yaml")


# ---------------------------------------------------------------------------
# run – auto-detect targets from FITS headers
# ---------------------------------------------------------------------------

@app.command()
def run(
    path: str = typer.Argument(..., help="Directory or glob of FITS files"),
    config: Optional[Path] = CONFIG_OPTION,
    dry_run: bool = typer.Option(False, "--dry-run", help="Discover files but do not process"),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", "-o"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Auto-detect targets from FITS headers and process each group."""
    settings = _load(config, output_dir, verbose)
    _ensure_db(settings)

    files = discover_fits_files(path)
    rprint(f"[bold]Found {len(files)} FITS files[/bold] in [cyan]{path}[/cyan]")

    groups = group_by_object_and_filter(files)
    if not groups:
        rprint("[red]No valid FITS files found.[/red]")
        raise typer.Exit(1)

    rprint(f"[bold]{len(groups)} object-filter groups detected:[/bold]")
    for (obj, filt), grp_files in groups.items():
        rprint(f"  • [cyan]{obj}[/cyan] / {filt.value} — {len(grp_files)} files")

    if dry_run:
        rprint("[yellow]Dry-run: stopping before processing.[/yellow]")
        return

    all_aavso_obs: list[AavsoObservation] = []
    failed_groups: list[str] = []
    for (obj, filt), grp_files in groups.items():
        rprint(f"\n[bold green]Processing:[/bold green] {obj} / {filt.value}")
        try:
            obs = _run_group(
                fits_files=grp_files,
                star_name=obj,
                filter_band=filt,
                settings=settings,
            )
            all_aavso_obs.extend(obs)
        except Exception as exc:
            rprint(f"[red]✗ {obj} / {filt.value} failed: {exc}[/red]")
            failed_groups.append(f"{obj}/{filt.value}")

    if failed_groups:
        rprint(f"\n[yellow]Skipped {len(failed_groups)} group(s): {', '.join(failed_groups)}[/yellow]")

    if all_aavso_obs:
        folder_name = Path(path).resolve().name
        ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        dest = Path.home() / "Downloads" / f"AAVSO-webops-{folder_name}-{ts}.csv"
        csv_path = write_aavso_csv(all_aavso_obs, obs_code=settings.obs_code, dest=dest)
        rprint(f"\n[bold green]Combined AAVSO CSV ({len(all_aavso_obs)} observations) → [cyan]{csv_path}[/cyan][/bold green]")
    else:
        rprint("\n[yellow]No observations succeeded — no AAVSO CSV written.[/yellow]")


# ---------------------------------------------------------------------------
# reduce – explicit star + filter
# ---------------------------------------------------------------------------

@app.command()
def reduce(
    path: str = typer.Argument(..., help="Directory or glob of FITS files"),
    star: str = typer.Option(..., "--star", "-s", help="Target star name (resolved via AAVSO VSP)"),
    filter_band: PhotometricFilter = typer.Option(PhotometricFilter.V, "--filter", "-f"),
    config: Optional[Path] = CONFIG_OPTION,
    brightest: Optional[float] = typer.Option(None, "--brightest"),
    dimmest: Optional[float] = typer.Option(None, "--dimmest"),
    ensemble: Optional[int] = typer.Option(None, "--ensemble"),
    aperture: Optional[float] = typer.Option(None, "--aperture"),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", "-o"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Reduce FITS files for a specific star and filter."""
    settings = _load(config, output_dir, verbose)

    # CLI overrides
    if brightest is not None:
        settings.brightest_comp_star = brightest
    if dimmest is not None:
        settings.dimmest_comp_star = dimmest
    if ensemble is not None:
        settings.ensemble_size = ensemble
    if aperture is not None:
        settings.aperture_radius = aperture

    _ensure_db(settings)
    files = discover_fits_files(path)
    rprint(f"[bold]Found {len(files)} FITS files[/bold]")
    obs = _run_group(fits_files=files, star_name=star, filter_band=filter_band, settings=settings)
    if obs:
        csv_path = write_aavso_csv(obs, obs_code=settings.obs_code, dest=None)
        rprint(f"  AAVSO CSV → [cyan]{csv_path}[/cyan]")


# ---------------------------------------------------------------------------
# dashboard – launch web UI
# ---------------------------------------------------------------------------

@app.command()
def dashboard(
    config: Optional[Path] = CONFIG_OPTION,
    host: Optional[str] = typer.Option(None, "--host"),
    port: Optional[int] = typer.Option(None, "--port"),
) -> None:
    """Start the Hikari web dashboard."""
    settings = _load(config)
    _ensure_db(settings)
    h = host or settings.web_host
    p = port or settings.web_port
    rprint(f"[bold green]Starting Hikari dashboard at http://{h}:{p}[/bold green]")
    uvicorn.run("hikari.web.app:app", host=h, port=p, reload=False)


# ---------------------------------------------------------------------------
# config – generate default config file
# ---------------------------------------------------------------------------

@app.command("config")
def init_config(
    dest: Path = typer.Argument(Path("hikari.toml"), help="Output config file path"),
) -> None:
    """Write a default hikari.toml configuration file."""
    write_default_config(dest)
    rprint(f"[green]Config written to[/green] [cyan]{dest}[/cyan]")


# ---------------------------------------------------------------------------
# jobs – list recent jobs from the database
# ---------------------------------------------------------------------------

@app.command()
def jobs(
    config: Optional[Path] = CONFIG_OPTION,
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """List recent batch jobs from the database."""
    settings = _load(config)
    _ensure_db(settings)
    with get_session() as db:
        job_list = list_jobs(db, limit=limit)

        if not job_list:
            rprint("[muted]No jobs found.[/muted]")
            return

        table = Table(title="Recent Batch Jobs")
        table.add_column("ID", style="dim")
        table.add_column("Object")
        table.add_column("Filter")
        table.add_column("Status")
        table.add_column("Created")
        table.add_column("Obs", justify="right")
        table.add_column("Failed", justify="right")

        for j in job_list:
            obs = j.observations
            n_ok = sum(1 for o in obs if o.status == "success")
            n_fail = sum(1 for o in obs if o.status == "failed")
            status_style = {"success": "green", "failed": "red", "pending": "yellow"}.get(j.status, "white")
            table.add_row(
                str(j.id),
                j.object_name,
                j.filter,
                f"[{status_style}]{j.status}[/{status_style}]",
                j.created_at.strftime("%Y-%m-%d %H:%M"),
                str(n_ok),
                f"[red]{n_fail}[/red]" if n_fail else "0",
            )
    console.print(table)


# ---------------------------------------------------------------------------
# clear – wipe all data from the database
# ---------------------------------------------------------------------------

@app.command()
def clear(
    config: Optional[Path] = CONFIG_OPTION,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Delete all jobs and observations from the database."""
    settings = _load(config)
    _ensure_db(settings)
    if not yes:
        confirm = typer.confirm("This will delete ALL jobs and observations. Continue?")
        if not confirm:
            rprint("[yellow]Aborted.[/yellow]")
            raise typer.Exit()
    from hikari.output.database import clear_all_data
    with get_session() as db:
        n = clear_all_data(db)
    rprint(f"[green]Cleared {n} job(s) and all associated observations.[/green]")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load(
    config: Optional[Path],
    output_dir: Optional[Path] = None,
    verbose: bool = False,
) -> HikariSettings:
    settings = load_settings(config)
    if output_dir:
        settings.output_dir = output_dir
    _configure_logging(verbose)
    return settings


def _configure_logging(verbose: bool) -> None:
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level, colorize=True, format="<level>{level}</level> {message}")
    # Always write full debug log to file
    log_file = Path.home() / ".local" / "share" / "hikari" / "hikari.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.add(log_file, level="DEBUG", rotation="10 MB", retention="30 days")


def _ensure_db(settings: HikariSettings) -> None:
    init_db(settings.database_url)


def _run_group(
    fits_files: list[Path],
    star_name: str,
    filter_band: PhotometricFilter,
    settings: HikariSettings,
) -> list[AavsoObservation]:
    """Resolve star, download comps, run pipeline, save results.

    Returns the list of AAVSO observations generated for this group
    (empty list when nothing succeeded).
    """
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        # Resolve target
        task = progress.add_task("Resolving target via AAVSO…", total=None)
        try:
            target = resolve_target(star_name, cache_dir=settings.resolved_cache_dir)
        except ValueError as exc:
            rprint(f"[red]AAVSO target resolution failed: {exc}[/red]")
            return []
        progress.remove_task(task)

        # Download comparison stars
        task = progress.add_task("Downloading comparison stars from AAVSO VSP…", total=None)
        try:
            vsp_chart = download_comparison_stars(
                ra=target.ra,
                dec=target.dec,
                filter_band=filter_band,
                field_of_view=settings.field_of_view,
                brightest=settings.brightest_comp_star,
                dimmest=settings.dimmest_comp_star,
                excluded_auids=settings.excluded_auids,
                cache_dir=settings.resolved_cache_dir,
            )
        except RuntimeError as exc:
            rprint(f"[red]VSP API failed: {exc}[/red]")
            return []
        progress.remove_task(task)

        if not vsp_chart.comparison_stars:
            rprint("[red]No comparison stars downloaded — cannot calibrate.[/red]")
            return []

        # Run pipeline with progress updates
        n_files = len(fits_files)
        task = progress.add_task("Reducing FITS files…", total=n_files)

        def on_progress(msg: str, cur: int, total: int) -> None:
            progress.update(task, completed=cur, description=msg)

        batch_result = process_batch(
            fits_files=fits_files,
            target=target,
            vsp_chart=vsp_chart,
            settings=settings,
            progress=on_progress,
        )
        progress.update(task, completed=n_files)

    return _save_results(batch_result, vsp_chart.chart_id, settings)


def _save_results(
    result: BatchResult,
    chart_id: str,
    settings: HikariSettings,
) -> list[AavsoObservation]:
    """Persist results to DB, generate lightcurve, and print summary.

    Returns the list of :class:`AavsoObservation` objects for this group
    so the caller can accumulate them into a combined CSV.
    """
    if not result.successful:
        rprint("[yellow]No successful observations — nothing to save.[/yellow]")
        _print_failures(result)
        return []

    with get_session() as db:
        # Create job record
        job_orm = save_batch_job(
            db,
            {
                "object_name": result.object_name,
                "filter": result.filter,
                "source_path": str(result.file_results[0].file_path.parent) if result.file_results else "",
                "status": "success",
                "obs_code": settings.obs_code,
                "notes": settings.observer_notes,
                "chart_id": chart_id,
                "completed_at": datetime.datetime.utcnow(),
                "aperture_radius": settings.aperture_radius,
                "fwhm": settings.fwhm,
                "ensemble_size": settings.ensemble_size,
            },
        )

        # Save per-file observations
        for fr in result.file_results:
            if fr.success and fr.calibrated_magnitude:
                cal = fr.calibrated_magnitude
                extra: dict = {}
                if fr.target_aperture_result:
                    extra["target_x"] = fr.target_aperture_result.x
                    extra["target_y"] = fr.target_aperture_result.y
                    extra["target_instrumental_mag"] = fr.target_aperture_result.instrumental_magnitude
                if fr.ensemble_comps and fr.ensemble_stars:
                    extra["ensemble_points"] = [
                        {
                            "auid": ap.auid,
                            "instrumental_mag": ap.instrumental_magnitude,
                            "published_mag": ms.published_magnitude,
                        }
                        for ap, ms in zip(fr.ensemble_comps, fr.ensemble_stars)
                        if ap.instrumental_magnitude is not None
                    ]
                save_observation(
                    db,
                    {
                        "job_id": job_orm.id,
                        "file_name": str(fr.file_path),
                        "star_name": result.object_name,
                        "julian_date": cal.julian_date,
                        "magnitude": cal.magnitude,
                        "magnitude_error": cal.magnitude_error,
                        "filter": cal.filter.value,
                        "airmass": cal.airmass,
                        "fit_slope": cal.calibration_fit.slope,
                        "fit_intercept": cal.calibration_fit.intercept,
                        "fit_rms": cal.calibration_fit.residual_rms,
                        "n_ensemble_stars": cal.calibration_fit.n_stars,
                        "check_star_auid": cal.check_star_auid,
                        "check_star_magnitude": cal.check_star_magnitude,
                        "status": "success",
                        "extra": extra or None,
                    },
                )
            else:
                save_observation(
                    db,
                    {
                        "job_id": job_orm.id,
                        "file_name": str(fr.file_path),
                        "star_name": result.object_name,
                        "julian_date": 0.0,
                        "filter": result.filter,
                        "status": "failed",
                        "error_message": fr.error,
                    },
                )

    # Build AAVSO observations
    aavso_obs = [
        calibrated_to_observation(
            cal=fr.calibrated_magnitude,  # type: ignore[arg-type]
            star_name=result.object_name,
            chart_id=chart_id,
            obs_code=settings.obs_code,
            notes=settings.observer_notes,
        )
        for fr in result.successful
        if fr.calibrated_magnitude
    ]

    # Lightcurve
    from hikari.output.plots import plot_lightcurve
    lc_path = settings.output_dir / f"lightcurve_{result.object_name.replace(' ', '_')}.png"
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    plot_lightcurve(
        calibrated_mags=result.calibrated_magnitudes,
        output_path=lc_path,
        star_name=result.object_name,
        filter_label=result.filter,
    )

    # Summary
    rprint(f"\n[bold green]✓ {len(result.successful)}/{len(result.file_results)} files succeeded[/bold green]")
    rprint(f"  Lightcurve → [cyan]{lc_path}[/cyan]")
    _print_failures(result)

    # Print magnitude table
    table = Table(title=f"{result.object_name} Observations")
    table.add_column("File")
    table.add_column("JD", justify="right")
    table.add_column("Mag", justify="right")
    table.add_column("±Err", justify="right")
    table.add_column("Fit RMS", justify="right")
    for fr in result.successful:
        if fr.calibrated_magnitude:
            cal = fr.calibrated_magnitude
            table.add_row(
                fr.file_path.name,
                f"{cal.julian_date:.5f}",
                f"{cal.magnitude:.3f}",
                f"{cal.magnitude_error:.3f}",
                f"{cal.calibration_fit.residual_rms:.4f}",
            )
    console.print(table)

    return aavso_obs


def _print_failures(result: BatchResult) -> None:
    if result.failed:
        rprint(f"\n[yellow]Failed files ({len(result.failed)}):[/yellow]")
        for fr in result.failed:
            rprint(f"  [red]✗[/red] {fr.file_path.name}: {fr.error}")


if __name__ == "__main__":
    app()
