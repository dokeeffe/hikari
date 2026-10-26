"""Load Hikari settings from the current directory's hikari.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


class HikariSettings(BaseModel):
    """Application-wide settings loaded from hikari.toml."""

    # --- Observer ---
    obs_code: str = Field(
        default="",
        description="AAVSO observatory code (e.g. 'MUY')",
    )
    observer_notes: str = Field(
        default="",
        description="Free-text notes appended to each AAVSO observation",
    )

    # --- Photometry defaults ---
    aperture_radius: float = Field(default=4.0, gt=0, description="Aperture radius in pixels")
    sky_inner_radius: float = Field(default=6.0, gt=0, description="Inner sky annulus radius (px)")
    sky_outer_radius: float = Field(default=10.0, gt=0, description="Outer sky annulus radius (px)")
    fwhm: float = Field(default=1.5, gt=0, description="FWHM for DAOStarFinder (pixels)")
    source_snr: float = Field(default=2.0, gt=0, description="Detection threshold in sigma")
    ensemble_size: int = Field(
        default=5, gt=0, description="Number of comparison stars for ensemble"
    )
    linear_limit: float = Field(default=60000.0, description="ADU limit for linearity check")

    # --- Comparison star selection ---
    brightest_comp_star: float = Field(
        default=7.0, description="Brightest allowed comparison star (mag)"
    )
    dimmest_comp_star: float = Field(
        default=17.0, description="Dimmest allowed comparison star (mag)"
    )
    field_of_view: float = Field(default=18.5, description="VSP query field of view (arcminutes)")
    excluded_auids: list[str] = Field(
        default_factory=list, description="Comparison star AUIDs to exclude"
    )

    # --- File I/O ---
    output_dir: Path = Field(
        default=Path.home() / "Downloads",
        description="Directory for generated files (CSV, plots)",
    )

    # --- Database ---
    # Use an absolute path by default so the DB location is consistent
    # regardless of the working directory the process is launched from.
    database_url: str = Field(
        default=f"sqlite:///{Path.home() / '.local' / 'share' / 'hikari' / 'hikari.db'}",
        description="SQLAlchemy database URL",
    )

    # --- Web dashboard ---
    web_host: str = Field(default="127.0.0.1", description="Web dashboard host")
    web_port: int = Field(default=8765, description="Web dashboard port")

    # --- Caching ---
    cache_dir: Path | None = Field(
        default=None,
        description="Directory for API response cache (None = ~/.cache/hikari)",
    )

    @property
    def resolved_cache_dir(self) -> Path:
        if self.cache_dir:
            return self.cache_dir
        return Path.home() / ".cache" / "hikari"


def load_settings() -> HikariSettings:
    """Load settings from ./hikari.toml, using model defaults for omitted values."""
    config_path = Path.cwd() / "hikari.toml"
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}. Run `hikari config` to create it."
        )
    with config_path.open("rb") as config_file:
        file_data = tomllib.load(config_file)
    return HikariSettings.model_validate(_flatten_config(file_data))


def _flatten_config(data: dict[str, object]) -> dict[str, object]:
    """Flatten the supported TOML sections into HikariSettings fields.

    The [web] section keys are prefixed with 'web_' (host → web_host).
    All other sections are merged directly into the top level.
    """
    flat: dict[str, object] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            prefix = "web_" if key == "web" else ""
            for subkey, subvalue in value.items():
                flat[f"{prefix}{subkey}"] = subvalue
        else:
            flat[key] = value
    return flat


def write_default_config() -> None:
    """Write the default configuration to ./hikari.toml."""
    settings = HikariSettings()
    content = f"""# Hikari configuration file

# AAVSO observer code (required for submission)
obs_code = "{settings.obs_code}"
observer_notes = "{settings.observer_notes}"

[photometry]
aperture_radius = {settings.aperture_radius}       # pixels
sky_inner_radius = {settings.sky_inner_radius}     # pixels
sky_outer_radius = {settings.sky_outer_radius}    # pixels
fwhm = {settings.fwhm}                            # pixels (for source detection)
source_snr = {settings.source_snr}                # detection threshold (sigma)
ensemble_size = {settings.ensemble_size}           # comparison stars in ensemble fit
linear_limit = {settings.linear_limit}            # ADU linearity limit

[comparison_stars]
brightest_comp_star = {settings.brightest_comp_star}
dimmest_comp_star = {settings.dimmest_comp_star}
field_of_view = {settings.field_of_view}          # arcminutes
excluded_auids = []

[output]
output_dir = "{settings.output_dir}"

[database]
database_url = "{settings.database_url}"

[web]
host = "{settings.web_host}"
port = {settings.web_port}
"""
    (Path.cwd() / "hikari.toml").write_text(content)
