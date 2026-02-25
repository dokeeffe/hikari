"""FITS file reading, validation, and metadata extraction."""

from __future__ import annotations

import warnings
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS, FITSFixedWarning
from loguru import logger

from hikari.core.models import FILTER_MAP, FitsMetadata, PhotometricFilter


# Suppress benign WCS header warnings
warnings.filterwarnings("ignore", category=FITSFixedWarning)


class FitsValidationError(Exception):
    """Raised when a FITS file fails validation."""


def discover_fits_files(path: str | Path) -> list[Path]:
    """Return all *.fits / *.fit files under *path*.

    *path* can be:
      - A directory: all FITS files within it are returned.
      - A glob pattern (e.g. ``/data/night1/*.fits``).
      - A single file path.
    """
    p = Path(path)
    if p.is_dir():
        files = sorted(p.glob("**/*.fit")) + sorted(p.glob("**/*.fits"))
        return sorted(set(files))
    if p.is_file():
        return [p]
    # Treat as glob relative to cwd
    import glob as _glob
    matched = [Path(f) for f in _glob.glob(str(path))]
    if not matched:
        raise FileNotFoundError(f"No FITS files found matching: {path}")
    return sorted(matched)


def group_by_object_and_filter(
    files: list[Path],
) -> dict[tuple[str, PhotometricFilter], list[Path]]:
    """Group FITS files by (OBJECT, filter) from their headers.

    Returns a dict keyed by ``(object_name, filter)`` → list of file paths.
    Files whose headers cannot be read are skipped with a warning.
    """
    groups: dict[tuple[str, PhotometricFilter], list[Path]] = {}
    for f in files:
        try:
            with fits.open(f, memmap=True) as hdulist:
                hdr = hdulist[0].header
                obj = str(hdr.get("OBJECT", "UNKNOWN")).strip()
                raw_filt = str(hdr.get("FILTER", "V")).strip()
                filt = FILTER_MAP.get(raw_filt, PhotometricFilter.V)
                groups.setdefault((obj, filt), []).append(f)
        except Exception as exc:
            logger.warning("Could not read header from {}: {}", f.name, exc)
    return groups


def read_fits_metadata(file_path: Path) -> FitsMetadata:
    """Extract key metadata from a FITS file header."""
    with fits.open(file_path, memmap=True) as hdulist:
        hdr = hdulist[0].header
        data = hdulist[0].data

        raw_filt = str(hdr.get("FILTER", "V")).strip()
        filt = FILTER_MAP.get(raw_filt, PhotometricFilter.V)
        date_obs = str(hdr.get("DATE-OBS", ""))
        jd = _parse_julian_date(hdr, date_obs)

        return FitsMetadata(
            file_path=file_path,
            object_name=str(hdr.get("OBJECT", "UNKNOWN")).strip(),
            filter_raw=raw_filt,
            filter=filt,
            exposure_time=float(hdr.get("EXPTIME", 0.0)),
            date_obs=date_obs,
            julian_date=jd,
            airmass=_safe_float(hdr.get("AIRMASS")),
            image_width=int(data.shape[-1]),
            image_height=int(data.shape[-2]),
        )


def load_fits_data(file_path: Path) -> tuple[np.ndarray, WCS, fits.Header]:
    """Load the image array, WCS, and header from a FITS file."""
    with fits.open(file_path, memmap=False) as hdulist:
        hdr = hdulist[0].header
        data = hdulist[0].data.astype(float)
        wcs = WCS(hdr)
    return data, wcs, hdr


def validate_fits_file(
    file_path: Path,
    target_ra: str,
    target_dec: str,
) -> None:
    """Validate that a FITS file has a usable WCS and contains the target.

    Raises :class:`FitsValidationError` on failure.
    """
    logger.debug("Validating {}", file_path.name)
    try:
        _, wcs, _ = load_fits_data(file_path)
    except Exception as exc:
        raise FitsValidationError(f"Cannot open {file_path.name}: {exc}") from exc

    if not wcs.has_celestial:
        raise FitsValidationError(f"{file_path.name}: no valid WCS found in header")

    # Check that the target falls within the image footprint
    try:
        coord = SkyCoord(target_ra, target_dec, unit=(u.hourangle, u.deg))
        px, py = coord.to_pixel(wcs, origin=0)
        # Read image shape without keeping file open
        with fits.open(file_path, memmap=True) as h:
            h_px, w_px = h[0].data.shape[-2], h[0].data.shape[-1]
        if not (0 <= float(px) <= w_px and 0 <= float(py) <= h_px):
            raise FitsValidationError(
                f"{file_path.name}: target star is outside image bounds "
                f"(x={px:.1f}, y={py:.1f}, image {w_px}×{h_px})"
            )
    except FitsValidationError:
        raise
    except Exception as exc:
        raise FitsValidationError(
            f"{file_path.name}: WCS coordinate transform failed: {exc}"
        ) from exc

    logger.debug("{}: validation passed", file_path.name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_julian_date(hdr: fits.Header, date_obs: str) -> float:
    """Return Julian Date from JD header or DATE-OBS string."""
    if "JD" in hdr:
        return float(hdr["JD"])
    if date_obs:
        try:
            return Time(date_obs, format="isot", scale="utc").jd
        except Exception:
            pass
    return 0.0


def _safe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
