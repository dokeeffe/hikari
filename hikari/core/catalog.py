"""Catalog queries: AAVSO VSP (target resolution + comparison stars).

Results are cached on disk to avoid hammering external APIs on repeated runs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from hikari.core.models import ComparisonStar, PhotometricFilter, TargetStar, VspChart

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(cache_dir: Path, key: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / (hashlib.md5(key.encode()).hexdigest() + ".json")


def _cache_get(cache_dir: Path, key: str) -> Optional[dict]:
    p = _cache_path(cache_dir, key)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return None


def _cache_set(cache_dir: Path, key: str, data: dict) -> None:
    _cache_path(cache_dir, key).write_text(json.dumps(data, indent=2))


VSP_BASE_URL = "https://app.aavso.org/vsp/api/chart/"

# ---------------------------------------------------------------------------
# Target resolution via AAVSO VSP
# ---------------------------------------------------------------------------

def resolve_target(
    name: str,
    cache_dir: Optional[Path] = None,
) -> TargetStar:
    """Resolve a star name to RA/DEC via the AAVSO VSP API.

    Queries ``/vsp/api/chart/?star={name}`` and reads the ``ra``/``dec``
    fields from the top-level chart response.  Results are cached to
    *cache_dir* (default ``~/.cache/hikari/aavso-target``).

    Raises ``ValueError`` if the star is not found in the AAVSO catalog.
    """
    effective_cache = (cache_dir or Path.home() / ".cache" / "hikari") / "aavso-target"
    cache_key = f"aavso-target:{name}"
    cached = _cache_get(effective_cache, cache_key)
    if cached:
        logger.debug("AAVSO target cache hit for '{}'", name)
        return TargetStar(**cached)

    logger.info("Querying AAVSO VSP for '{}'", name)
    params = {
        "format": "json",
        "star": name,
        "fov": 60,
        "maglimit": 14.5,
    }
    try:
        resp = httpx.get(VSP_BASE_URL, params=params, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPError as exc:
        raise ValueError(f"AAVSO VSP API request failed for '{name}': {exc}") from exc

    if "ra" not in result or not result["ra"]:
        raise ValueError(f"Object '{name}' not found in AAVSO VSP catalog")

    ra: str = result["ra"]
    dec: str = result["dec"]

    target = TargetStar(name=name, ra=ra, dec=dec)
    _cache_set(effective_cache, cache_key, target.model_dump())
    logger.info("Resolved '{}' → RA {} DEC {}", name, ra, dec)
    return target


# ---------------------------------------------------------------------------
# AAVSO VSP API – comparison stars
# ---------------------------------------------------------------------------

def download_comparison_stars(
    ra: str,
    dec: str,
    filter_band: PhotometricFilter,
    field_of_view: float = 18.5,
    brightest: float = 7.0,
    dimmest: float = 17.0,
    excluded_auids: Optional[list[str]] = None,
    cache_dir: Optional[Path] = None,
    mag_limit: float = 18.5,
) -> VspChart:
    """Download comparison star photometry from the AAVSO VSP API.

    Parameters
    ----------
    ra, dec:
        Target coordinates (used as chart centre).
    filter_band:
        Photometric filter (V, B, ...).
    field_of_view:
        FOV for the chart query, in arcminutes.
    brightest, dimmest:
        Magnitude limits for comparison star selection.
    excluded_auids:
        AUIDs to exclude (blacklist).
    cache_dir:
        On-disk cache directory.
    mag_limit:
        Upper limit sent to VSP API (should be ≥ dimmest).

    Returns
    -------
    VspChart
        Chart metadata plus the filtered list of comparison stars.
    """
    excluded = set(excluded_auids or [])
    effective_cache = (cache_dir or Path.home() / ".cache" / "hikari") / "vsp"
    cache_key = f"vsp:{ra}:{dec}:{filter_band}:{field_of_view}:{mag_limit}"
    cached = _cache_get(effective_cache, cache_key)

    if cached:
        logger.debug("VSP cache hit for RA={} DEC={} filter={}", ra, dec, filter_band)
        raw_chart = cached
    else:
        params = {
            "format": "json",
            "fov": field_of_view,
            "maglimit": mag_limit,
            "ra": ra,
            "dec": dec,
        }
        logger.info("Querying AAVSO VSP API {} params {}", VSP_BASE_URL, params)
        try:
            resp = httpx.get(VSP_BASE_URL, params=params, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            raw_chart = resp.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"AAVSO VSP API request failed: {exc}") from exc

        _cache_set(effective_cache, cache_key, raw_chart)
    chart_id = raw_chart.get("chartid", "unknown")
    comp_stars: list[ComparisonStar] = []

    for star in raw_chart.get("photometry", []):
        logger.debug("Checking star {} from chart", star)
        auid = star.get("auid", "")
        if auid in excluded:
            continue
        star_ra = star.get("ra", "")
        star_dec = star.get("dec", "")

        # Find the requested band in the bands list
        mag: Optional[float] = None
        err: Optional[float] = None
        for band in star.get("bands", []):
            if band.get("band") == filter_band.value:
                mag = _safe_float(band.get("mag"))
                err = _safe_float(band.get("error"))
                break

        if mag is None:
            continue  # No data for this filter
        if not (brightest <= mag <= dimmest):
            continue

        comp_stars.append(
            ComparisonStar(
                auid=auid,
                ra=star_ra,
                dec=star_dec,
                magnitude=mag,
                magnitude_error=err or 0.0,
                filter=filter_band,
            )
        )

    logger.info(
        "VSP chart {} – {} comparison stars (filter={}, {:.1f}–{:.1f} mag)",
        chart_id,
        len(comp_stars),
        filter_band,
        brightest,
        dimmest,
    )
    return VspChart(
        chart_id=chart_id,
        ra=ra,
        dec=dec,
        field_of_view=field_of_view,
        comparison_stars=comp_stars,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(value: object) -> Optional[float]:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
