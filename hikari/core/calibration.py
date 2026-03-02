"""Ensemble photometry calibration.

Fits instrumental magnitudes of comparison stars against published magnitudes
using a first-order polynomial (linear fit), then applies the transformation
to calibrate the target star magnitude.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import numpy as np
from loguru import logger

from hikari.core.models import (
    ApertureResult,
    CalibrationFit,
    CalibratedMagnitude,
    FitsMetadata,
    MatchedStar,
    PhotometricFilter,
)


class CalibrationError(Exception):
    """Raised when calibration cannot be performed."""


def select_ensemble(
    comp_results: list[ApertureResult],
    comp_stars: list[MatchedStar],
    target_result: ApertureResult,
    ensemble_size: int = 5,
) -> tuple[list[ApertureResult], list[MatchedStar]]:
    """Select *ensemble_size* comparison stars closest in instrumental mag to target.

    Parameters
    ----------
    comp_results:
        Aperture photometry results for comparison stars.
    comp_stars:
        Corresponding MatchedStar objects (same order as comp_results).
    target_result:
        Aperture photometry result for the target star.
    ensemble_size:
        Maximum number of comparison stars to include in the ensemble.

    Returns
    -------
    (selected_results, selected_stars)
        Sorted by closeness to target instrumental magnitude.
    """
    if target_result.instrumental_magnitude is None:
        raise CalibrationError("Target has no instrumental magnitude — cannot select ensemble")

    target_mag = target_result.instrumental_magnitude

    # Filter: must have an instrumental magnitude
    valid_pairs = [
        (r, s)
        for r, s in zip(comp_results, comp_stars)
        if r.instrumental_magnitude is not None and not r.saturated
    ]

    if not valid_pairs:
        raise CalibrationError("No valid comparison stars available for ensemble selection")

    # Sort by proximity to target instrumental magnitude
    valid_pairs.sort(key=lambda pair: abs((pair[0].instrumental_magnitude or 0.0) - target_mag))

    selected = valid_pairs[:ensemble_size]
    sel_results = [p[0] for p in selected]
    sel_stars = [p[1] for p in selected]

    logger.debug(
        "Ensemble: {} comp stars selected (of {} valid)",
        len(sel_results),
        len(valid_pairs),
    )
    return sel_results, sel_stars


def fit_ensemble(
    comp_results: list[ApertureResult],
    comp_stars: list[MatchedStar],
) -> CalibrationFit:
    """Fit a first-order polynomial: published_mag = slope * instr_mag + intercept.

    Parameters
    ----------
    comp_results:
        Aperture photometry for the ensemble comparison stars.
    comp_stars:
        Corresponding MatchedStar objects with published magnitudes.

    Returns
    -------
    CalibrationFit
        Fit coefficients and RMS residual.

    Raises
    ------
    CalibrationError
        If fewer than 2 comp stars have valid data.
    """
    pairs = [
        (r.instrumental_magnitude, s.published_magnitude)
        for r, s in zip(comp_results, comp_stars)
        if r.instrumental_magnitude is not None and s.published_magnitude is not None
    ]

    if len(pairs) < 2:
        raise CalibrationError(
            f"Need at least 2 comparison stars for fit, got {len(pairs)}"
        )

    instr_mags = np.array([p[0] for p in pairs], dtype=float)
    pub_mags = np.array([p[1] for p in pairs], dtype=float)

    coeffs = np.polyfit(instr_mags, pub_mags, deg=1)
    slope, intercept = float(coeffs[0]), float(coeffs[1])

    predicted = np.polyval(coeffs, instr_mags)
    residuals = pub_mags - predicted
    rms = float(np.sqrt(np.mean(residuals**2)))

    logger.debug(
        "Calibration fit: slope={:.4f}, intercept={:.4f}, RMS={:.4f} mag ({} stars)",
        slope,
        intercept,
        rms,
        len(pairs),
    )
    return CalibrationFit(
        slope=slope,
        intercept=intercept,
        residual_rms=rms,
        n_stars=len(pairs),
    )


def calibrate_target(
    target_result: ApertureResult,
    fit: CalibrationFit,
    fits_meta: FitsMetadata,
    check_star_result: Optional[ApertureResult] = None,
    check_star: Optional[MatchedStar] = None,
) -> CalibratedMagnitude:
    """Apply the ensemble fit to derive the target's calibrated magnitude.

    Parameters
    ----------
    target_result:
        Aperture photometry for the target star.
    fit:
        Linear calibration fit from the ensemble.
    fits_meta:
        FITS file metadata (for JD, filter, airmass, file path).
    check_star_result, check_star:
        Optional secondary comparison star for error estimation.

    Returns
    -------
    CalibratedMagnitude
    """
    if target_result.instrumental_magnitude is None:
        raise CalibrationError("Target star has no instrumental magnitude")

    cal_mag = fit.slope * target_result.instrumental_magnitude + fit.intercept
    mag_error = fit.residual_rms  # Use RMS of fit as magnitude error

    ck_auid: Optional[str] = None
    ck_mag: Optional[float] = None
    ck_pub_mag: Optional[float] = None
    if check_star_result and check_star:
        if check_star_result.instrumental_magnitude is not None:
            ck_cal_mag = (
                fit.slope * check_star_result.instrumental_magnitude + fit.intercept
            )
            logger.debug(
                "Check star {}: published_mag={}, cal_mag={:.4f}",
                check_star.auid,
                check_star.published_magnitude,
                ck_cal_mag,
            )
            if check_star.published_magnitude is not None:
                mag_error = abs(ck_cal_mag - check_star.published_magnitude)
                ck_pub_mag = check_star.published_magnitude
            ck_auid = check_star.auid
            ck_mag = ck_cal_mag

    logger.info(
        "Calibrated magnitude for {}: {:.3f} ± {:.3f} (filter={})",
        fits_meta.object_name,
        cal_mag,
        mag_error,
        fits_meta.filter,
    )
    return CalibratedMagnitude(
        file_path=fits_meta.file_path,
        julian_date=fits_meta.julian_date,
        magnitude=cal_mag,
        magnitude_error=mag_error,
        filter=fits_meta.filter,
        airmass=fits_meta.airmass,
        calibration_fit=fit,
        check_star_auid=ck_auid,
        check_star_magnitude=ck_mag,
        check_star_published_magnitude=ck_pub_mag,
    )


def run_calibration(
    comp_results: list[ApertureResult],
    comp_stars: list[MatchedStar],
    target_result: ApertureResult,
    fits_meta: FitsMetadata,
    ensemble_size: int = 5,
) -> tuple[CalibratedMagnitude, CalibrationFit, list[ApertureResult], list[MatchedStar]]:
    """End-to-end calibration: select ensemble → fit → calibrate target.

    Returns
    -------
    (calibrated_mag, fit, ensemble_results, ensemble_stars)
        The calibrated magnitude, the fit, and the ensemble members used.
    """
    # Select ensemble (leaves one out for check star if possible)
    ensemble_results, ensemble_stars = select_ensemble(
        comp_results, comp_stars, target_result, ensemble_size=ensemble_size
    )

    # Pick a check star: best brightness-matched non-ensemble candidate
    target_mag = target_result.instrumental_magnitude  # already checked non-None above
    ensemble_auids = {r.auid for r in ensemble_results}
    check_pairs = sorted(
        [
            (r, s)
            for r, s in zip(comp_results, comp_stars)
            if r.auid not in ensemble_auids and r.instrumental_magnitude is not None
        ],
        key=lambda pair: abs((pair[0].instrumental_magnitude or 0.0) - target_mag),
    )

    check_result: Optional[ApertureResult] = None
    check_star: Optional[MatchedStar] = None

    if check_pairs:
        check_result, check_star = check_pairs[0]
    elif len(ensemble_results) > 2:
        # Sacrifice the brightness-closest ensemble star for the check role
        check_result, check_star = ensemble_results[0], ensemble_stars[0]
        ensemble_results = ensemble_results[1:]
        ensemble_stars = ensemble_stars[1:]
        logger.debug("No non-ensemble check star available; sacrificed ensemble[0] as check star")

    fit = fit_ensemble(ensemble_results, ensemble_stars)
    cal = calibrate_target(
        target_result,
        fit,
        fits_meta,
        check_star_result=check_result,
        check_star=check_star,
    )
    return cal, fit, ensemble_results, ensemble_stars
