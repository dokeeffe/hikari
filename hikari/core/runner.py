"""Core processing runner — shared by CLI and web API.

Provides ``process_group`` and ``run_directory`` as the main entry points.
Both the CLI (with Rich progress) and the web API (background thread) call
these functions; UI concerns live in the callers.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from hikari.config import HikariSettings
from hikari.core.catalog import download_comparison_stars, resolve_target
from hikari.core.fits_reader import discover_fits_files, group_by_object_and_filter
from hikari.core.models import AavsoObservation, PhotometricFilter
from hikari.core.pipeline import BatchResult, process_batch
from hikari.output.aavso import calibrated_to_observation
from hikari.output.database import get_session, save_batch_job, save_observation

# Signature: (message, current, total)
ProgressCallback = Callable[[str, int, int], None]


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class GroupResult:
    object_name: str
    filter: str
    success: bool
    n_files: int
    n_succeeded: int
    n_failed: int
    aavso_obs: list[AavsoObservation] = field(default_factory=list)
    error: Optional[str] = None
    job_id: Optional[int] = None
    failed_files: list[tuple[str, str]] = field(default_factory=list)  # (filename, error)


@dataclass
class RunResult:
    source_path: str
    groups: list[GroupResult] = field(default_factory=list)

    @property
    def total_obs(self) -> int:
        return sum(g.n_succeeded for g in self.groups)

    @property
    def all_aavso_obs(self) -> list[AavsoObservation]:
        obs: list[AavsoObservation] = []
        for g in self.groups:
            obs.extend(g.aavso_obs)
        return obs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def process_group(
    fits_files: list[Path],
    star_name: str,
    filter_band: PhotometricFilter,
    settings: HikariSettings,
    run_id: Optional[int] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> GroupResult:
    """Resolve star, download comp stars, run pipeline, save results to DB.

    Parameters
    ----------
    fits_files:
        FITS files for this object/filter group.
    star_name:
        Target star name (resolved via AAVSO VSP).
    filter_band:
        Photometric filter.
    settings:
        HikariSettings instance.
    run_id:
        Optional RunORM id to link the created BatchJobORM to.
    on_progress:
        Optional callback(message, current, total) for progress updates.

    Returns
    -------
    GroupResult
    """
    def _prog(msg: str, cur: int = 0, tot: int = 1) -> None:
        if on_progress:
            on_progress(msg, cur, tot)

    _prog(f"Resolving {star_name}…")
    try:
        target = resolve_target(star_name, cache_dir=settings.resolved_cache_dir)
    except ValueError as exc:
        return GroupResult(
            object_name=star_name,
            filter=filter_band.value,
            success=False,
            n_files=len(fits_files),
            n_succeeded=0,
            n_failed=len(fits_files),
            error=str(exc),
        )

    _prog(f"Downloading comp stars for {star_name}…")
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
        return GroupResult(
            object_name=star_name,
            filter=filter_band.value,
            success=False,
            n_files=len(fits_files),
            n_succeeded=0,
            n_failed=len(fits_files),
            error=str(exc),
        )

    if not vsp_chart.comparison_stars:
        return GroupResult(
            object_name=star_name,
            filter=filter_band.value,
            success=False,
            n_files=len(fits_files),
            n_succeeded=0,
            n_failed=len(fits_files),
            error="No comparison stars downloaded — cannot calibrate.",
        )

    n_files = len(fits_files)
    batch_result = process_batch(
        fits_files=fits_files,
        target=target,
        vsp_chart=vsp_chart,
        settings=settings,
        progress=on_progress or (lambda msg, cur, tot: None),
    )

    job_id = _save_results(batch_result, vsp_chart.chart_id, settings, run_id=run_id)

    aavso_obs = [
        calibrated_to_observation(
            cal=fr.calibrated_magnitude,  # type: ignore[arg-type]
            star_name=batch_result.object_name,
            chart_id=vsp_chart.chart_id,
            obs_code=settings.obs_code,
            notes=settings.observer_notes,
        )
        for fr in batch_result.successful
        if fr.calibrated_magnitude
    ]

    failed_files = [(str(fr.file_path.name), fr.error or "") for fr in batch_result.failed]

    return GroupResult(
        object_name=star_name,
        filter=filter_band.value,
        success=len(batch_result.successful) > 0,
        n_files=n_files,
        n_succeeded=len(batch_result.successful),
        n_failed=len(batch_result.failed),
        aavso_obs=aavso_obs,
        job_id=job_id,
        failed_files=failed_files,
    )


def run_directory(
    source_path: str,
    settings: HikariSettings,
    run_id: Optional[int] = None,
    on_group_start: Optional[Callable[[str, int, int], None]] = None,
    on_group_done: Optional[GroupResult] = None,
) -> RunResult:
    """Process all object-filter groups in *source_path*.

    Parameters
    ----------
    source_path:
        Directory (or glob) of FITS files to process.
    settings:
        HikariSettings instance.
    run_id:
        Optional RunORM id to associate created jobs with.
    on_group_start:
        Callback(group_label, group_index, total_groups) called before each group.
    on_group_done:
        Ignored — kept for future use.
    """
    files = discover_fits_files(source_path)
    groups = group_by_object_and_filter(files)
    run_result = RunResult(source_path=source_path)

    total = len(groups)
    for idx, ((obj, filt), grp_files) in enumerate(groups.items()):
        label = f"{obj} / {filt.value}"
        logger.info("Processing group {}/{}: {}", idx + 1, total, label)
        if on_group_start:
            on_group_start(label, idx, total)

        group_result = process_group(
            fits_files=grp_files,
            star_name=obj,
            filter_band=filt,
            settings=settings,
            run_id=run_id,
        )
        run_result.groups.append(group_result)

    return run_result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _save_results(
    result: BatchResult,
    chart_id: str,
    settings: HikariSettings,
    run_id: Optional[int] = None,
) -> Optional[int]:
    """Persist a BatchResult to the DB. Returns the job_id, or None."""
    if not result.successful:
        return None

    with get_session() as db:
        job_data: dict = {
            "object_name": result.object_name,
            "filter": result.filter,
            "source_path": (
                str(result.file_results[0].file_path.parent) if result.file_results else ""
            ),
            "status": "success",
            "obs_code": settings.obs_code,
            "notes": settings.observer_notes,
            "chart_id": chart_id,
            "completed_at": datetime.datetime.utcnow(),
            "aperture_radius": settings.aperture_radius,
            "fwhm": settings.fwhm,
            "ensemble_size": settings.ensemble_size,
        }
        if run_id is not None:
            job_data["run_id"] = run_id

        job_orm = save_batch_job(db, job_data)

        for fr in result.file_results:
            if fr.success and fr.calibrated_magnitude:
                cal = fr.calibrated_magnitude
                extra: dict = {}
                if fr.target_aperture_result:
                    extra["target_x"] = fr.target_aperture_result.x
                    extra["target_y"] = fr.target_aperture_result.y
                    extra["target_instrumental_mag"] = (
                        fr.target_aperture_result.instrumental_magnitude
                    )
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
                        "check_star_published_magnitude": cal.check_star_published_magnitude,
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

        return job_orm.id
