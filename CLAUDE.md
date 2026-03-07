# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Install with dev dependencies
uv sync --extra dev

# Run all tests
uv run pytest

# Run a single test file or test
uv run pytest tests/test_calibration.py
uv run pytest tests/test_calibration.py::test_select_ensemble

# Lint
uv run ruff check hikari/ tests/

# Type check
uv run mypy hikari/

# Run the CLI
uv run hikari --help
uv run hikari run /path/to/fits/
uv run hikari dashboard
```

## Architecture

### Pipeline flow (per FITS file)

```
catalog.py        → resolve_target() + download_comparison_stars()   [cached to ~/.cache/hikari/]
fits_reader.py    → validate_fits_file() → read_fits_metadata() + load_fits_data()
detection.py      → detect_sources() → match_catalog_stars()         [DAOStarFinder + KDTree]
photometry.py     → measure_all()                                    [circular aperture + annular sky]
calibration.py    → run_calibration()                                [select_ensemble → fit_ensemble → calibrate_target]
```

`pipeline.py` orchestrates these steps in `_process_single_file()`, wrapping each file in a try/except so one failure doesn't abort the batch. Results accumulate in `BatchResult` / `SingleFileResult` dataclasses.

### Data models (`core/models.py`)

All pipeline stages communicate via Pydantic models. The key chain is:

`ComparisonStar` / `TargetStar` → `MatchedStar` → `ApertureResult` → `CalibrationFit` → `CalibratedMagnitude`

`SingleFileResult` holds all intermediate results for one FITS file. `FILTER_MAP` normalises camera-specific filter names (e.g. `PV` → `V`) to `PhotometricFilter` enum values.

### Calibration and error estimation

`select_ensemble()` picks the N comparison stars closest in **instrumental magnitude** to the target (brightness-matched), excluding saturated stars. One additional comp star outside the ensemble is reserved as a check star.

Error priority:
1. `|calibrated_check_mag − published_check_mag|` when a check star is available
2. RMS of ensemble fit residuals as fallback

### Configuration (`config.py`)

`HikariSettings` (pydantic-settings) loads from, in priority order:
1. `HIKARI_*` environment variables
2. Explicitly passed config file
3. Auto-discovered `hikari.toml` / `hikari.yaml` (cwd, then `~/.config/hikari/`)
4. Built-in defaults

The `[photometry]`, `[comparison_stars]`, `[output]`, `[database]`, and `[web]` TOML sections are flattened into `HikariSettings` fields (pydantic-settings handles nested TOML as flat kwargs).

### Database (`output/database.py`)

SQLite via SQLAlchemy. Two tables: `batch_jobs` and `observations` (one row per FITS file). `init_db()` must be called before `get_session()`. The `extra` JSON column on `ObservationORM` stores flexible per-observation data (target pixel coordinates, ensemble points) used by the web dashboard for the calibration plot and cutout.

### Web dashboard (`web/app.py` + `web/static/index.html`)

FastAPI SPA. Key endpoints:
- `GET /api/jobs` — job list
- `GET /api/jobs/{id}/observations` — observation rows
- `PATCH /api/observations/{id}/reject` — reject/accept
- `GET /api/jobs/{id}/aavso-csv` — download AAVSO CSV (accepted only)
- `GET /api/observations/{id}/cutout` — dynamically generates a ZScale PNG from the original FITS file using `target_x`/`target_y` from `extra`

### External APIs

Both external calls go through `catalog.py` and are disk-cached (MD5-keyed JSON files):
- **AAVSO VSP** (`app.aavso.org/vsp/api/chart/`) — used for both target resolution and comparison star download
- Cache lives at `~/.cache/hikari/aavso-target/` and `~/.cache/hikari/vsp/`
