"""Source detection and star matching.

Uses DAOStarFinder for point source detection and a KDTree for matching
catalog stars to detected sources.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.stats import mad_std
from astropy.wcs import WCS
from loguru import logger
from photutils.detection import DAOStarFinder
from scipy.spatial import KDTree

from hikari.core.models import ComparisonStar, DetectedSource, MatchedStar, TargetStar


# Tolerance (pixels) for matching a catalog position to a detected source
MATCH_TOLERANCE_PX = 5.0


def detect_sources(
    data: np.ndarray,
    fwhm: float = 1.5,
    source_snr: float = 2.0,
) -> list[DetectedSource]:
    """Run DAOStarFinder on *data* and return detected point sources.

    Parameters
    ----------
    data:
        2-D float array (background-inclusive image data).
    fwhm:
        Approximate FWHM of stars in pixels.
    source_snr:
        Detection threshold in units of the background RMS.
    """
    bkg_sigma = mad_std(data)
    finder = DAOStarFinder(fwhm=fwhm, threshold=source_snr * bkg_sigma)
    sources_table = finder(data)

    if sources_table is None or len(sources_table) == 0:
        logger.warning("DAOStarFinder found no sources")
        return []

    results: list[DetectedSource] = [
        DetectedSource(
            x=float(row["xcentroid"]),
            y=float(row["ycentroid"]),
            peak_flux=float(row["peak"]),
        )
        for row in sources_table
    ]
    logger.debug("Detected {} sources", len(results))
    return results


def match_catalog_stars(
    sources: list[DetectedSource],
    wcs: WCS,
    catalog_stars: list[ComparisonStar],
    target: TargetStar,
    tolerance_px: float = MATCH_TOLERANCE_PX,
) -> tuple[list[MatchedStar], list[MatchedStar]]:
    """Match comparison stars and target to detected sources via WCS + KDTree.

    Parameters
    ----------
    sources:
        Detected point sources in pixel space.
    wcs:
        World Coordinate System for the image.
    catalog_stars:
        AAVSO comparison stars to match.
    target:
        Target variable star.
    tolerance_px:
        Maximum pixel distance for a valid match.

    Returns
    -------
    (matched_comps, matched_targets)
        Two lists of :class:`MatchedStar` — comparison stars and target stars
        that were successfully matched.
    """
    if not sources:
        return [], []

    # Build KDTree from detected source positions
    source_coords = np.array([[s.x, s.y] for s in sources])
    tree = KDTree(source_coords)

    def _find_match(ra: str, dec: str) -> Optional[DetectedSource]:
        coord = SkyCoord(ra, dec, unit=(u.hourangle, u.deg))
        px, py = coord.to_pixel(wcs, origin=0)
        dist, idx = tree.query([float(px), float(py)])
        if dist <= tolerance_px:
            return sources[int(idx)]
        return None

    # Match comparison stars
    matched_comps: list[MatchedStar] = []
    for cs in catalog_stars:
        src = _find_match(cs.ra, cs.dec)
        if src is None:
            logger.debug("Comp star {} not found in image", cs.auid)
            continue
        matched_comps.append(
            MatchedStar(
                auid=cs.auid,
                x=src.x,
                y=src.y,
                peak_flux=src.peak_flux,
                ra=cs.ra,
                dec=cs.dec,
                published_magnitude=cs.magnitude,
                published_magnitude_error=cs.magnitude_error,
                is_target=False,
            )
        )

    # Match target
    matched_targets: list[MatchedStar] = []
    src = _find_match(target.ra, target.dec)
    if src is not None:
        matched_targets.append(
            MatchedStar(
                auid=target.name,
                x=src.x,
                y=src.y,
                peak_flux=src.peak_flux,
                ra=target.ra,
                dec=target.dec,
                is_target=True,
            )
        )
    else:
        logger.warning("Target star '{}' not found in image", target.name)

    logger.debug(
        "Matched {}/{} comparison stars; target {}",
        len(matched_comps),
        len(catalog_stars),
        "found" if matched_targets else "NOT FOUND",
    )
    return matched_comps, matched_targets
