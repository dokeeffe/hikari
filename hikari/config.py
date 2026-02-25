"""Configuration management for Hikari.

Supports loading from:
  - hikari.toml or hikari.yaml in the current directory
  - ~/.config/hikari/config.toml
  - Environment variables (HIKARI_* prefix)
  - CLI overrides
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Optional

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Default config paths
# ---------------------------------------------------------------------------

CONFIG_SEARCH_PATHS = [
    Path.cwd() / "hikari.toml",
    Path.cwd() / "hikari.yaml",
    Path.home() / ".config" / "hikari" / "config.toml",
    Path.home() / ".config" / "hikari" / "config.yaml",
]


class HikariSettings(BaseSettings):
    """Application-wide settings with env-var override support."""

    model_config = SettingsConfigDict(
        env_prefix="HIKARI_",
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

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
    ensemble_size: int = Field(default=5, gt=0, description="Number of comparison stars for ensemble")
    linear_limit: float = Field(default=60000.0, description="ADU limit for linearity check")

    # --- Comparison star selection ---
    brightest_comp_star: float = Field(default=7.0, description="Brightest allowed comparison star (mag)")
    dimmest_comp_star: float = Field(default=17.0, description="Dimmest allowed comparison star (mag)")
    field_of_view: float = Field(default=18.5, description="VSP query field of view (arcminutes)")
    excluded_auids: list[str] = Field(default_factory=list, description="Comparison star AUIDs to exclude")

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
    cache_dir: Optional[Path] = Field(
        default=None,
        description="Directory for API response cache (None = ~/.cache/hikari)",
    )

    @property
    def resolved_cache_dir(self) -> Path:
        if self.cache_dir:
            return self.cache_dir
        return Path.home() / ".cache" / "hikari"


def load_settings(config_file: Optional[Path] = None) -> HikariSettings:
    """Load settings, merging file config with environment variables.

    Priority (highest first):
      1. Environment variables (HIKARI_*)
      2. Explicitly provided config file
      3. Auto-discovered config file (hikari.toml / hikari.yaml)
      4. Built-in defaults
    """
    file_data: dict = {}

    # Determine config file to load
    paths_to_try = ([config_file] if config_file else []) + CONFIG_SEARCH_PATHS
    for path in paths_to_try:
        if path and path.exists():
            file_data = _load_config_file(path)
            break

    # Pydantic-settings handles env vars automatically; merge file data as init kwargs
    return HikariSettings(**file_data)


def _load_config_file(path: Path) -> dict:
    """Parse a TOML or YAML config file and return a flat dict."""
    suffix = path.suffix.lower()
    if suffix == ".toml":
        with open(path, "rb") as f:
            return tomllib.load(f)
    elif suffix in {".yaml", ".yml"}:
        with open(path) as f:
            data = yaml.safe_load(f)
        return data or {}
    else:
        raise ValueError(f"Unsupported config file format: {suffix}")


def write_default_config(dest: Path = Path("hikari.toml")) -> None:
    """Write a commented default config file to *dest*."""
    settings = HikariSettings()
    content = f"""# Hikari configuration file
# Environment variables with HIKARI_ prefix override these values.

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
    dest.write_text(content)
