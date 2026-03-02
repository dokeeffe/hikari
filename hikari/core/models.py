"""Core Pydantic data models for Hikari."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class PhotometricFilter(str, Enum):
    V = "V"
    B = "B"
    R = "R"
    I = "I"
    U = "U"
    TG = "TG"  # Tri-color green (often used as V substitute)


# Map from camera-specific filter names to AAVSO standard
FILTER_MAP: dict[str, PhotometricFilter] = {
    "PV": PhotometricFilter.V,
    "PB": PhotometricFilter.B,
    "LUM": PhotometricFilter.V,
    "V": PhotometricFilter.V,
    "B": PhotometricFilter.B,
    "R": PhotometricFilter.R,
    "I": PhotometricFilter.I,
    "U": PhotometricFilter.U,
}


class ObservationStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    REJECTED = "rejected"   # User-rejected after visual inspection


# ---------------------------------------------------------------------------
# Catalog / star models
# ---------------------------------------------------------------------------

class ComparisonStar(BaseModel):
    """A comparison star from the AAVSO VSP catalog."""

    auid: str
    ra: str                     # HH MM SS.ss
    dec: str                    # ±DD MM SS.s
    magnitude: float
    magnitude_error: float
    filter: PhotometricFilter


class TargetStar(BaseModel):
    """Resolved target variable star."""

    name: str
    ra: str                     # HH MM SS.ss
    dec: str                    # ±DD MM SS.s


class VspChart(BaseModel):
    """AAVSO VSP chart metadata."""

    chart_id: str
    ra: str
    dec: str
    field_of_view: float        # arcminutes
    comparison_stars: list[ComparisonStar] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# FITS / image models
# ---------------------------------------------------------------------------

class FitsMetadata(BaseModel):
    """Key header values extracted from a FITS file."""

    file_path: Path
    object_name: str
    filter_raw: str             # As stored in FITS header
    filter: PhotometricFilter
    exposure_time: float        # seconds
    date_obs: str               # ISO 8601 string from header
    julian_date: float
    airmass: Optional[float] = None
    image_width: int
    image_height: int


# ---------------------------------------------------------------------------
# Detection / photometry models
# ---------------------------------------------------------------------------

class DetectedSource(BaseModel):
    """A point source detected by DAOStarFinder."""

    x: float
    y: float
    peak_flux: float


class MatchedStar(BaseModel):
    """A catalog star matched to a detected source in an image."""

    auid: str
    x: float
    y: float
    peak_flux: float
    ra: str
    dec: str
    published_magnitude: Optional[float] = None
    published_magnitude_error: Optional[float] = None
    is_target: bool = False


class ApertureResult(BaseModel):
    """Raw aperture photometry result for a single star."""

    auid: str
    x: float
    y: float
    aperture_sum: float         # Background-subtracted flux
    sky_background: float       # Per-pixel sky level
    peak_flux: float
    instrumental_magnitude: Optional[float] = None
    saturated: bool = False


# ---------------------------------------------------------------------------
# Calibration models
# ---------------------------------------------------------------------------

class CalibrationFit(BaseModel):
    """Result of the linear ensemble calibration fit."""

    slope: float
    intercept: float
    residual_rms: float         # RMS of fit residuals (mag)
    n_stars: int                # Number of comparison stars used


class CalibratedMagnitude(BaseModel):
    """Final calibrated magnitude for the target star in one image."""

    file_path: Path
    julian_date: float
    magnitude: float
    magnitude_error: float
    filter: PhotometricFilter
    airmass: Optional[float]
    calibration_fit: CalibrationFit
    check_star_auid: Optional[str] = None
    check_star_magnitude: Optional[float] = None
    check_star_published_magnitude: Optional[float] = None


# ---------------------------------------------------------------------------
# Pipeline / batch models
# ---------------------------------------------------------------------------

@dataclass
class PipelineContext:
    """Mutable context passed through the pipeline stages."""

    # Inputs
    fits_files: list[Path] = field(default_factory=list)
    target: TargetStar | None = None
    comparison_stars: list[ComparisonStar] = field(default_factory=list)
    vsp_chart: VspChart | None = None

    # Per-file results
    fits_metadata: list[FitsMetadata] = field(default_factory=list)
    aperture_results: list[list[ApertureResult]] = field(default_factory=list)
    calibrated_magnitudes: list[CalibratedMagnitude] = field(default_factory=list)

    # Errors
    failed_files: list[tuple[Path, str]] = field(default_factory=list)


class BatchJob(BaseModel):
    """Describes a batch photometry job."""

    id: Optional[int] = None
    object_name: str
    filter: PhotometricFilter
    source_path: str            # Glob pattern or directory
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: ObservationStatus = ObservationStatus.PENDING
    obs_code: str = ""          # AAVSO observatory code
    brightest_comp_star: float = 7.0
    dimmest_comp_star: float = 17.0
    aperture_radius: float = 4.0
    fwhm: float = 3.0
    source_snr: float = 50.0
    ensemble_size: int = 5
    field_of_view: float = 18.5  # arcminutes for VSP query
    output_dir: Optional[str] = None  # defaults to ~/Downloads
    notes: str = ""


# ---------------------------------------------------------------------------
# AAVSO output model
# ---------------------------------------------------------------------------

class AavsoObservation(BaseModel):
    """One row in the AAVSO Extended Format CSV."""

    name: str                   # Star name
    date: float                 # Julian Date
    magnitude: float
    magnitude_error: float
    filter: PhotometricFilter
    transformed: str = "NO"
    mtype: str = "STD"
    cname: str = "ENSEMBLE"
    cmag: str = "na"
    kname: Optional[str] = None  # Check star AUID
    kmag: Optional[float] = None # Check star magnitude
    airmass: Optional[float] = None
    group: str = "na"
    chart: str = ""
    notes: str = ""
