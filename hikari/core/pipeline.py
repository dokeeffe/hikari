"""Main photometry pipeline orchestrator.

Ties together FITS reading, source detection, photometry, and calibration.
Processes a list of FITS files for a single (object, filter) combination
and returns calibrated magnitudes plus diagnostic data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from hikari.config import HikariSettings
from hikari.core.calibration import CalibrationError, run_calibration
from hikari.core.detection import detect_sources, match_catalog_stars
from hikari.core.fits_reader import (
    FitsValidationError,
    load_fits_data,
    read_fits_metadata,
    validate_fits_file,
)
from hikari.core.models import (
    ApertureResult,
    CalibrationFit,
    CalibratedMagnitude,
    ComparisonStar,
    FitsMetadata,
    MatchedStar,
    TargetStar,
    VspChart,
)
from hikari.core.photometry import measure_all


# ---------------------------------------------------------------------------
# Per-file result
# ---------------------------------------------------------------------------

@dataclass
class SingleFileResult:
    """All intermediate and final results for one FITS file."""

    file_path: Path
    fits_meta: Optional[FitsMetadata] = None
    all_matched_comps: list[MatchedStar] = field(default_factory=list)
    all_matched_targets: list[MatchedStar] = field(default_factory=list)
    comp_aperture_results: list[ApertureResult] = field(default_factory=list)
    target_aperture_result: Optional[ApertureResult] = None
    ensemble_comps: list[ApertureResult] = field(default_factory=list)
    ensemble_stars: list[MatchedStar] = field(default_factory=list)
    calibration_fit: Optional[CalibrationFit] = None
    calibrated_magnitude: Optional[CalibratedMagnitude] = None
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None and self.calibrated_magnitude is not None


# ---------------------------------------------------------------------------
# Batch result
# ---------------------------------------------------------------------------

@dataclass
class BatchResult:
    """Aggregated results for one (object, filter) batch."""

    object_name: str
    filter: str
    vsp_chart: Optional[VspChart] = None
    target: Optional[TargetStar] = None
    file_results: list[SingleFileResult] = field(default_factory=list)

    @property
    def successful(self) -> list[SingleFileResult]:
        return [r for r in self.file_results if r.success]

    @property
    def failed(self) -> list[SingleFileResult]:
        return [r for r in self.file_results if not r.success]

    @property
    def calibrated_magnitudes(self) -> list[CalibratedMagnitude]:
        return [r.calibrated_magnitude for r in self.successful if r.calibrated_magnitude]


# ---------------------------------------------------------------------------
# Progress callback type
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[str, int, int], None]  # (message, current, total)


def _noop(msg: str, cur: int, total: int) -> None:
    pass


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def process_batch(
    fits_files: list[Path],
    target: TargetStar,
    vsp_chart: VspChart,
    settings: HikariSettings,
    progress: ProgressCallback = _noop,
) -> BatchResult:
    """Process a batch of FITS files for a single target + filter.

    Each file is processed independently so that a failure in one file
    does not abort the rest.

    Parameters
    ----------
    fits_files:
        Ordered list of FITS files to process.
    target:
        Resolved target star (RA/DEC).
    vsp_chart:
        VSP chart with comparison star list.
    settings:
        Active HikariSettings instance.
    progress:
        Optional callback ``(message, current, total)`` for progress updates.

    Returns
    -------
    BatchResult
    """
    n = len(fits_files)
    result = BatchResult(
        object_name=target.name,
        filter=vsp_chart.comparison_stars[0].filter.value if vsp_chart.comparison_stars else "",
        vsp_chart=vsp_chart,
        target=target,
    )

    for i, fits_path in enumerate(fits_files, start=1):
        progress(f"Processing {fits_path.name}", i, n)
        file_result = _process_single_file(
            fits_path=fits_path,
            target=target,
            vsp_chart=vsp_chart,
            settings=settings,
        )
        result.file_results.append(file_result)
        if file_result.success:
            logger.success(
                "[{}/{}] {} → {:.3f} ± {:.3f} mag",
                i,
                n,
                fits_path.name,
                file_result.calibrated_magnitude.magnitude,  # type: ignore[union-attr]
                file_result.calibrated_magnitude.magnitude_error,  # type: ignore[union-attr]
            )
        else:
            logger.error("[{}/{}] {} FAILED: {}", i, n, fits_path.name, file_result.error)

    logger.info(
        "Batch complete: {}/{} files succeeded",
        len(result.successful),
        n,
    )
    return result


def _process_single_file(
    fits_path: Path,
    target: TargetStar,
    vsp_chart: VspChart,
    settings: HikariSettings,
) -> SingleFileResult:
    """Full photometry pipeline for one FITS file."""
    result = SingleFileResult(file_path=fits_path)

    try:
        # 1. Validate
        validate_fits_file(fits_path, target.ra, target.dec)

        # 2. Read metadata + data
        result.fits_meta = read_fits_metadata(fits_path)
        data, wcs, _ = load_fits_data(fits_path)

        # 3. Detect sources
        sources = detect_sources(data, fwhm=settings.fwhm, source_snr=settings.source_snr)

        # 4. Match stars
        comp_stars_matched, target_matched = match_catalog_stars(
            sources=sources,
            wcs=wcs,
            catalog_stars=vsp_chart.comparison_stars,
            target=target,
        )
        result.all_matched_comps = comp_stars_matched
        result.all_matched_targets = target_matched

        if not target_matched:
            raise CalibrationError("Target star not detected in image")
        if len(comp_stars_matched) < 2:
            raise CalibrationError(
                f"Only {len(comp_stars_matched)} comparison stars detected; need ≥ 2"
            )

        # 5. Aperture photometry
        all_stars = comp_stars_matched + target_matched
        all_phot = measure_all(
            data,
            all_stars,
            aperture_radius=settings.aperture_radius,
            sky_inner_radius=settings.sky_inner_radius,
            sky_outer_radius=settings.sky_outer_radius,
            linear_limit=settings.linear_limit,
        )
        # Split back into comp / target results
        n_comps = len(comp_stars_matched)
        comp_phot = all_phot[:n_comps]
        target_phot = all_phot[n_comps:]
        if not target_phot:
            raise CalibrationError("Aperture photometry failed for target star")
        result.comp_aperture_results = comp_phot
        result.target_aperture_result = target_phot[0]

        # 6. Calibration
        cal, fit, ens_results, ens_stars = run_calibration(
            comp_results=comp_phot,
            comp_stars=comp_stars_matched,
            target_result=target_phot[0],
            fits_meta=result.fits_meta,
            ensemble_size=settings.ensemble_size,
        )
        result.calibration_fit = fit
        result.calibrated_magnitude = cal
        result.ensemble_comps = ens_results
        result.ensemble_stars = ens_stars

    except (FitsValidationError, CalibrationError) as exc:
        result.error = str(exc)
    except Exception as exc:
        logger.exception("Unexpected error processing {}", fits_path.name)
        result.error = f"Unexpected error: {exc}"

    return result
