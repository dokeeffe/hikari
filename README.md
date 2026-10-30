# Hikari

**Variable star photometry pipeline for AAVSO reporting.**

Hikari processes calibrated FITS images, performs ensemble aperture photometry, and produces [AAVSO Extended Format](https://www.aavso.org/aavso-extended-file-format) reports ready for submission. A web interface lets you trigger processing, monitor progress, inspect calibration quality, annotate runs, and export CSV files — all without touching the command line.

---

## Features

| Area | Details |
|---|---|
| **FITS processing** | Auto-discovers and groups files by `OBJECT` + `FILTER` header |
| **Target resolution** | AAVSO VSP API lookup with on-disk caching |
| **Comparison stars** | AAVSO VSP download with magnitude filtering and blacklist support |
| **Source detection** | DAOStarFinder with configurable FWHM and SNR threshold |
| **Aperture photometry** | Circular aperture + annular sky subtraction, saturation flagging |
| **Ensemble calibration** | Linear fit across N comparison stars nearest in brightness to target |
| **Check star selection** | Best brightness-matched non-ensemble star; sacrifices an ensemble slot if needed |
| **Error estimation** | Check-star residual (preferred) or ensemble fit RMS |
| **AAVSO output** | Extended Format CSV, downloadable per-run from the web UI or CLI |
| **Observation history** | SQLite database — all results persisted across sessions |
| **Web interface** | Trigger runs, live progress, per-frame quality control, CSV export, run notes |
| **Config system** | `hikari.toml` in the current directory |

---

## Installation

Hikari uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and build the dashboard, then install Python dependencies
git clone https://github.com/dokeeffe/hikari.git
cd hikari
npm ci
npm run build
uv sync
```

The web dashboard source is in `hikari/web/frontend/` and uses Vite and TypeScript. Run `npm run build` after changing the frontend and before running or packaging Hikari; the compiled files are written to `hikari/web/static/` and included in the Python package.

For frontend development, start the FastAPI dashboard as usual and run `npm run dev` in another terminal. Vite serves the frontend and proxies `/api` requests to `http://127.0.0.1:8765`.

The `hikari` command is then available via:

```bash
uv run hikari --help
```

---

## Quick Start

### 1. Generate a config file

```bash
uv run hikari config
# Writes hikari.toml to the current directory — edit obs_code, aperture, etc.
```

### 2. Launch the web interface

```bash
uv run hikari dashboard
# Open http://127.0.0.1:8765
```

From the web interface you can:
- Paste or browse to a directory of calibrated FITS files
- Click **Process** to start a run — progress updates live as each object/filter group completes
- Expand each star section to review individual observations
- Reject bad frames and add notes to the run
- Download the combined AAVSO CSV when satisfied

### 3. Process from the command line (optional)

```bash
# Auto-detect targets from FITS headers
uv run hikari run /path/to/fits/folder/

# Process a specific star and filter
uv run hikari reduce /path/to/fits/ --star "SS Cyg" --filter V

# Preview groups without processing
uv run hikari run /path/to/fits/ --dry-run
```

---

## How It Works

### Pipeline (per FITS file)

```
catalog.py    → resolve_target() + download_comparison_stars()   [AAVSO VSP, disk-cached]
fits_reader.py → validate_fits_file() → read_fits_metadata() + load_fits_data()
detection.py  → detect_sources() → match_catalog_stars()         [DAOStarFinder + KDTree]
photometry.py → measure_all()                                    [circular aperture + annular sky]
calibration.py → run_calibration()                               [select_ensemble → fit_ensemble → calibrate_target]
```

`runner.py` orchestrates these steps for all groups in a directory. The web API calls `runner.run_directory()` in a background thread; the CLI wraps the same function with a Rich progress bar.

### Comparison Star Selection

Comparison stars are sourced from the **AAVSO VSP API** for the target field and cached on disk. Hikari filters the catalog to stars within the configured magnitude window (`brightest_comp_star` to `dimmest_comp_star`) and removes any AUIDs on the exclusion list (`excluded_auids`).

For each FITS image, DAOStarFinder detects sources and a KDTree match associates them with the catalog. The **ensemble** is chosen by selecting the N stars (configurable via `ensemble_size`) whose **instrumental magnitude is closest to the target's instrumental magnitude**. This brightness-matching strategy minimises systematic errors from differential colour and non-linearity. Saturated stars are excluded automatically.

### Check Star

After ensemble selection, the **check star** is the brightness-matched non-ensemble comparison star closest to the target's instrumental magnitude. If no non-ensemble candidate exists and the ensemble has more than two stars, the algorithm sacrifices the closest ensemble star for the check role so an independent quality estimate is always available.

### Ensemble Calibration

A first-order polynomial is fit across the N ensemble comparison stars:

```
published_mag = slope × instrumental_mag + intercept
```

The target's calibrated magnitude is derived by evaluating this polynomial at its instrumental magnitude.

### Error Estimation

1. **Check-star residual (preferred):** The check star is independently calibrated with the ensemble polynomial. The error is the absolute difference between its derived and published catalog magnitude:
   ```
   error = |calibrated_check_mag − published_check_mag|
   ```
2. **Fit RMS fallback:** Used when no check star is available — the RMS scatter of the ensemble comparison stars around the linear fit.

---

## Web Interface

```bash
uv run hikari dashboard
# Open http://127.0.0.1:8765
```

### Layout

| Column | Content |
|---|---|
| **Left** | Inline form to start a new run (path input + 📁 directory browser), list of past runs with status and note indicator |
| **Centre** | Selected run detail: progress bar (while processing), collapsible per-star observation tables, run notes |
| **Right** | Per-observation detail: interactive ensemble fit scatter chart, 120×120 px ZScale target cutout |

### Key actions

- **Start a run** — enter or browse to a directory, click Process; progress updates every 2 seconds
- **Expand a star** — click any star/filter row to reveal its observation table
- **Reject / Accept** — mark individual frames before exporting
- **Notes** — free-text note on each run (e.g. "Uploaded to AAVSO", "Poor seeing, check RMS"); saved on blur
- **Download AAVSO CSV** — run-level export of all accepted observations in AAVSO Extended Format
- **Delete Run** — removes the run and all its observations from the database
- **Clear Cache** — deletes disk-cached AAVSO VSP responses (forces fresh API calls on next run)

### Calibration quality colour coding

| Colour | RMS fit | Interpretation |
|---|---|---|
| Green | < 0.03 mag | Excellent |
| Amber | 0.03 – 0.07 mag | Acceptable |
| Red | > 0.07 mag | Suspect — inspect ensemble chart before submitting |

---

## Configuration

Run `hikari config` to generate an annotated `hikari.toml` in the current directory. In the web dashboard, select **Settings** to view and edit the values in that same file; changes are validated before saving. Settings are read from the current working directory, so launch the dashboard from the directory containing `hikari.toml`. Database URL and web host/port changes require a dashboard restart; other settings apply to the next processing run.

```toml
obs_code = "MUY"                  # Your AAVSO observer code
observer_notes = "CDK14, STL-11000M, Bortle 4"

[photometry]
aperture_radius = 4.0             # pixels
sky_inner_radius = 6.0            # pixels
sky_outer_radius = 10.0           # pixels
fwhm = 3.0                        # pixels (for DAOStarFinder)
source_snr = 2.0                  # detection threshold (sigma)
ensemble_size = 5                 # comparison stars in the ensemble fit
linear_limit = 60000.0            # ADU — above this, saturation is flagged

[comparison_stars]
brightest_comp_star = 7.0
dimmest_comp_star = 17.0
field_of_view = 18.5              # arcminutes (VSP query radius)
excluded_auids = []               # AUIDs to permanently exclude

[output]
output_dir = "~/Downloads"        # used by CLI CSV output

[database]
database_url = "sqlite:////Users/you/.local/share/hikari/hikari.db"

[web]
host = "127.0.0.1"
port = 8765
```

---

## Data Flow

```
FITS files
    │
    ▼
AAVSO VSP → resolve target RA/DEC (cached)
    │
    ▼
AAVSO VSP → download comparison stars (cached)
    │
    ▼  (per file)
DAOStarFinder → detect sources
    │
WCS + KDTree → match catalog stars to detected sources
    │
Circular aperture + annular sky → flux → instrumental magnitude
    │
Select N ensemble comps (closest in brightness to target)
Select check star (best brightness-matched non-ensemble comp)
    │
Linear fit (instrumental mag → published mag)
    │
Calibrate target + check-star error estimate
    │
    ▼
SQLite DB  →  ~/.local/share/hikari/hikari.db
Web UI     →  review, annotate, reject, download AAVSO CSV
CLI        →  AAVSO CSV written to output_dir/
```

---

## CLI Reference

```
hikari run       PATH    Auto-detect targets from FITS headers and reduce all groups
hikari reduce    PATH    Reduce files for a specific star + filter
hikari dashboard         Start the web interface (default: http://127.0.0.1:8765)
hikari config            Write a default hikari.toml config file
hikari jobs              List recent batch jobs from the database
hikari clear             Delete all data from the database
```

Logging option for `run` and `reduce`:

| Flag | Description |
|---|---|
| `--verbose / -v` | Enable DEBUG-level console output |

---

## Tech Stack

| Library | Purpose |
|---|---|
| [astropy](https://www.astropy.org/) | FITS I/O, WCS, time handling, image stretching |
| [photutils](https://photutils.readthedocs.io/) | DAOStarFinder, aperture photometry |
| [httpx](https://www.python-httpx.org/) | AAVSO VSP API requests |
| [scipy](https://scipy.org/) | KDTree for star matching |
| [numpy](https://numpy.org/) / [matplotlib](https://matplotlib.org/) | Numerics and target cutout rendering |
| [pydantic](https://docs.pydantic.dev/) | Data models and config validation |
| [SQLAlchemy](https://www.sqlalchemy.org/) | ORM and SQLite persistence |
| [FastAPI](https://fastapi.tiangolo.com/) | Web API |
| [Typer](https://typer.tiangolo.com/) + [Rich](https://rich.readthedocs.io/) | CLI and terminal output |
| [loguru](https://loguru.readthedocs.io/) | Structured logging |

---

## Project Structure

```
hikari/
├── hikari/
│   ├── cli.py              # Typer CLI entry points
│   ├── config.py           # Pydantic Settings (TOML / env vars)
│   ├── core/
│   │   ├── models.py       # Pydantic data models
│   │   ├── fits_reader.py  # FITS I/O and header extraction
│   │   ├── catalog.py      # AAVSO VSP API (disk-cached)
│   │   ├── detection.py    # DAOStarFinder + KDTree star matching
│   │   ├── photometry.py   # Aperture photometry + sky subtraction
│   │   ├── calibration.py  # Ensemble selection, linear fit, error
│   │   ├── pipeline.py     # Per-file + batch orchestration
│   │   └── runner.py       # Directory-level orchestration (CLI + web shared)
│   ├── output/
│   │   ├── aavso.py        # AAVSO Extended Format CSV writer
│   │   └── database.py     # SQLAlchemy ORM + session management
│   └── web/
│       ├── app.py          # FastAPI application + REST API
│       └── static/
│           └── index.html  # Single-page web interface
└── tests/
    ├── test_photometry.py
    ├── test_calibration.py
    ├── test_aavso.py
    └── test_fits_reader.py
```

---

## Development

```bash
# Install with dev dependencies
uv sync --extra dev

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=hikari --cov-report=term-missing

# Lint
uv run ruff check hikari/ tests/

# Type check
uv run mypy hikari/
```

---

## Coming Soon

### Transformation Coefficients

Hikari currently reports **instrumental (untransformed) magnitudes**. Planned work:
- Accept first-order transformation coefficients (e.g. Tv, Tbv) in `hikari.toml`
- Apply standard AAVSO/Henden transformation equations before writing the AAVSO CSV
- Report both raw and transformed magnitude in the database

Until then, note any significant colour response differences in `observer_notes`.

---

## Licence

MIT
