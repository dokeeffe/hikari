"""Aperture photometry with local background subtraction.

Implements circular aperture measurement + annular sky subtraction
and derives instrumental magnitudes.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from loguru import logger
from photutils.aperture import (
    ApertureStats,
    CircularAnnulus,
    CircularAperture,
    aperture_photometry,
)

from hikari.core.models import ApertureResult, MatchedStar

# Default aperture / annulus geometry (pixels)
DEFAULT_APERTURE_RADIUS = 4.0
DEFAULT_SKY_INNER = 6.0
DEFAULT_SKY_OUTER = 10.0
DEFAULT_LINEAR_LIMIT = 60_000.0   # ADU


def measure_aperture(
    data: np.ndarray,
    star: MatchedStar,
    aperture_radius: float = DEFAULT_APERTURE_RADIUS,
    sky_inner_radius: float = DEFAULT_SKY_INNER,
    sky_outer_radius: float = DEFAULT_SKY_OUTER,
    linear_limit: float = DEFAULT_LINEAR_LIMIT,
) -> ApertureResult:
    """Measure background-subtracted flux for a single star.

    Parameters
    ----------
    data:
        2-D float image array (counts / ADU).
    star:
        Matched star with pixel coordinates.
    aperture_radius:
        Circular aperture radius in pixels.
    sky_inner_radius, sky_outer_radius:
        Inner and outer radii of the annular sky aperture.
    linear_limit:
        Peak ADU value above which the detector is considered non-linear.

    Returns
    -------
    ApertureResult
        Raw photometry result including background-subtracted flux and
        instrumental magnitude.
    """
    position = (star.x, star.y)

    src_aperture = CircularAperture(position, r=aperture_radius)
    sky_annulus = CircularAnnulus(position, r_in=sky_inner_radius, r_out=sky_outer_radius)

    # Measure source flux
    phot_table = aperture_photometry(data, src_aperture)
    raw_flux = float(phot_table["aperture_sum"][0])

    # Measure sky background (median per pixel in annulus)
    # ApertureStats.median is a scalar for a single aperture, array for multiple
    sky_stats = ApertureStats(data, sky_annulus)
    med = sky_stats.median
    if med is None:
        sky_per_pixel = 0.0
    else:
        sky_per_pixel = float(np.atleast_1d(med)[0])

    n_pixels = src_aperture.area
    sky_contribution = sky_per_pixel * n_pixels
    bg_subtracted_flux = raw_flux - sky_contribution

    saturated = star.peak_flux >= linear_limit

    inst_mag: Optional[float] = None
    if bg_subtracted_flux > 0:
        inst_mag = -2.5 * math.log10(bg_subtracted_flux)
    else:
        logger.warning(
            "Star {} has non-positive flux ({:.1f}) after sky subtraction — skipping magnitude",
            star.auid,
            bg_subtracted_flux,
        )

    if saturated:
        logger.warning(
            "Star {} peak flux {:.0f} exceeds linear limit {:.0f} — possible saturation",
            star.auid,
            star.peak_flux,
            linear_limit,
        )

    return ApertureResult(
        auid=star.auid,
        x=star.x,
        y=star.y,
        aperture_sum=bg_subtracted_flux,
        sky_background=sky_per_pixel,
        peak_flux=star.peak_flux,
        instrumental_magnitude=inst_mag,
        saturated=saturated,
    )


def measure_all(
    data: np.ndarray,
    stars: list[MatchedStar],
    aperture_radius: float = DEFAULT_APERTURE_RADIUS,
    sky_inner_radius: float = DEFAULT_SKY_INNER,
    sky_outer_radius: float = DEFAULT_SKY_OUTER,
    linear_limit: float = DEFAULT_LINEAR_LIMIT,
) -> list[ApertureResult]:
    """Run aperture photometry on a list of matched stars.

    Stars that could not be measured are excluded from the result list
    (a warning is logged for each).
    """
    results: list[ApertureResult] = []
    for star in stars:
        try:
            res = measure_aperture(
                data,
                star,
                aperture_radius=aperture_radius,
                sky_inner_radius=sky_inner_radius,
                sky_outer_radius=sky_outer_radius,
                linear_limit=linear_limit,
            )
            results.append(res)
        except Exception as exc:
            logger.warning("Photometry failed for star {}: {}", star.auid, exc)
    return results
