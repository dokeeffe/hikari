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

import typer
import uvicorn
from loguru import logger
from rich import print as rprint
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from hikari.config import HikariSettings, load_settings, write_default_config
from hikari.core.fits_reader import discover_fits_files, group_by_object_and_filter
from hikari.core.models import AavsoObservation, PhotometricFilter
from hikari.core.runner import GroupResult, process_group, run_directory
from hikari.output.aavso import write_aavso_csv
from hikari.output.database import (
    init_db,
    get_session,
    list_jobs,
)

console = Console()
app = typer.Typer(
    name="hikari",
    help="Variable star photometry for AAVSO reporting.",
    add_completion=False,
)

# ---------------------------------------------------------------------------
# run – auto-detect targets from FITS headers
# ---------------------------------------------------------------------------

@app.command()
def run(
    path: str = typer.Argument(..., help="Directory or glob of FITS files"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Discover files but do not process"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Auto-detect targets from FITS headers and process each group."""
    settings = _load(verbose)
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
            group_result = _run_group_with_progress(
                fits_files=grp_files,
                star_name=obj,
                filter_band=filt,
                settings=settings,
            )
            all_aavso_obs.extend(group_result.aavso_obs)
            _print_group_summary(group_result)
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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Reduce FITS files for a specific star and filter."""
    settings = _load(verbose)

    _ensure_db(settings)
    files = discover_fits_files(path)
    rprint(f"[bold]Found {len(files)} FITS files[/bold]")
    group_result = _run_group_with_progress(
        fits_files=files, star_name=star, filter_band=filter_band, settings=settings
    )
    if group_result.aavso_obs:
        csv_path = write_aavso_csv(group_result.aavso_obs, obs_code=settings.obs_code, dest=None)
        rprint(f"  AAVSO CSV → [cyan]{csv_path}[/cyan]")


# ---------------------------------------------------------------------------
# dashboard – launch web UI
# ---------------------------------------------------------------------------

@app.command()
def dashboard() -> None:
    """Start the Hikari web dashboard."""
    settings = _load()
    _ensure_db(settings)
    h = settings.web_host
    p = settings.web_port
    rprint(f"[bold green]Starting Hikari dashboard at http://{h}:{p}[/bold green]")
    uvicorn.run("hikari.web.app:app", host=h, port=p, reload=False)


# ---------------------------------------------------------------------------
# config – generate default config file
# ---------------------------------------------------------------------------

@app.command("config")
def init_config() -> None:
    """Write a default hikari.toml configuration file."""
    write_default_config()
    rprint("[green]Config written to[/green] [cyan]hikari.toml[/cyan]")


# ---------------------------------------------------------------------------
# jobs – list recent jobs from the database
# ---------------------------------------------------------------------------

@app.command()
def jobs(
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """List recent batch jobs from the database."""
    settings = _load()
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
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Delete all jobs and observations from the database."""
    settings = _load()
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

def _load(verbose: bool = False) -> HikariSettings:
    settings = load_settings()
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


def _run_group_with_progress(
    fits_files: list[Path],
    star_name: str,
    filter_band: PhotometricFilter,
    settings: HikariSettings,
) -> GroupResult:
    """Wrap process_group with a Rich progress bar."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        n_files = len(fits_files)
        task = progress.add_task(f"Processing {star_name} / {filter_band.value}…", total=n_files)

        def on_progress(msg: str, cur: int, total: int) -> None:
            progress.update(task, completed=cur, description=msg)

        return process_group(
            fits_files=fits_files,
            star_name=star_name,
            filter_band=filter_band,
            settings=settings,
            on_progress=on_progress,
        )


def _print_group_summary(result: GroupResult) -> None:
    if not result.success:
        rprint(f"[red]✗ {result.object_name} / {result.filter}: {result.error}[/red]")
        return

    rprint(f"[bold green]✓ {result.n_succeeded}/{result.n_files} files succeeded[/bold green]")

    if result.failed_files:
        rprint(f"\n[yellow]Failed files ({len(result.failed_files)}):[/yellow]")
        for fname, err in result.failed_files:
            rprint(f"  [red]✗[/red] {fname}: {err}")


if __name__ == "__main__":
    app()
