# Hikari

**Variable star photometry pipeline for AAVSO reporting.**

Hikari processes calibrated FITS images, performs ensemble aperture photometry, and produces [AAVSO Extended Format](https://www.aavso.org/aavso-extended-file-format) reports ready for submission. A web dashboard lets you review job history, inspect calibration quality, and reject bad frames before exporting.

---

## Features

| Area | Details |
|---|---|
| **FITS processing** | Auto-discovers and groups files by `OBJECT` + `FILTER` header |
| **Target resolution** | Simbad catalog lookup with on-disk caching |
| **Comparison stars** | AAVSO VSP API download with magnitude filtering and blacklist support |
| **Source detection** | DAOStarFinder with configurable FWHM and SNR threshold |
| **Aperture photometry** | Circular aperture + annular sky subtraction, saturation flagging |
| **Ensemble calibration** | Linear fit across N comparison stars nearest in brightness to target |
| **Error estimation** | Check-star residual (preferred) or ensemble fit RMS |
| **Diagnostic plots** | Calibration scatter, target cutout, star-map overlay, lightcurve |
| **AAVSO output** | Extended Format CSV written to configurable output directory |
| **Observation history** | SQLite database — query past results, track trends |
| **Web dashboard** | FastAPI + SPA for job monitoring and per-observation quality control |
| **Config system** | `hikari.toml` / `HIKARI_*` env vars |

---

## Installation

Hikari uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/dokeeffe/hikari.git
cd hikari
uv sync
```

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

### 2. Process a night's observations (auto-detect targets)

```bash
uv run hikari run /path/to/fits/folder/
```

Hikari will:
- Discover all `.fits` files and group them by `OBJECT` + `FILTER` header
- Resolve each target via Simbad
- Download comparison stars from the AAVSO VSP API
- Run the full photometry pipeline on each file
- Write an AAVSO CSV and lightcurve PNG to `output_dir` (default: `~/Downloads/`)
- Save all results to the local SQLite database

### 3. Process a specific star and filter

```bash
uv run hikari reduce /path/to/fits/ --star "YZ LMi" --filter V
```

Optional overrides:

```bash
uv run hikari reduce /path/to/fits/ \
  --star "SS Cyg" \
  --filter V \
  --aperture 5.0 \
  --ensemble 6 \
  --brightest 8.0 \
  --dimmest 16.0 \
  --output-dir ~/Desktop/output/
```

### 4. Preview without processing

```bash
uv run hikari run /path/to/fits/ --dry-run
```

### 5. View recent jobs

```bash
uv run hikari jobs
```

### 6. Launch the web dashboard

```bash
uv run hikari dashboard
# Open http://127.0.0.1:8765
```

---

## How It Works

### Comparison Star Selection

Comparison stars are sourced from the **AAVSO VSP API** for the target field and cached on disk. Hikari filters the catalog to stars within the configured magnitude window (`brightest_comp_star` to `dimmest_comp_star`) and removes any AUIDs on the blacklist (`excluded_auids`).

For each FITS image, DAOStarFinder detects sources and a KDTree match associates them with the catalog. From the detected comparison stars, the **ensemble** is chosen by selecting the N stars (configurable via `ensemble_size`) whose **instrumental magnitude is closest to the target's instrumental magnitude**. This brightness-matching strategy minimises systematic errors from differential colour and non-linearity that would be introduced by mixing very bright and very faint comparisons. Saturated stars are excluded from the ensemble automatically.

After the ensemble is selected, one additional comparison star (the first remaining valid star outside the ensemble) is reserved as a **check star** for independent error estimation.

### Ensemble Calibration

A first-order polynomial (linear fit) is computed across the N ensemble comparison stars:

```
published_mag = slope × instrumental_mag + intercept
```

The fit is performed with `numpy.polyfit`. The target's calibrated magnitude is then derived by evaluating this polynomial at its instrumental magnitude.

### Error Estimation

The reported magnitude error is determined in one of two ways, in order of preference:

1. **Check-star residual (preferred):** The check star — a comparison star intentionally excluded from the ensemble fit — is independently calibrated using the ensemble polynomial. The error is the absolute difference between its derived magnitude and its published catalog magnitude:

   ```
   error = |calibrated_check_mag − published_check_mag|
   ```

   This is a true end-to-end test of the calibration under the same atmospheric conditions and is the most meaningful single-frame error estimate available without a proper ensemble of check stars.

2. **Fit RMS fallback:** If no check star is available (e.g. the VSP field returned very few comparison stars), the error is the RMS of the ensemble fit residuals — i.e. how tightly the comparison stars scatter around the linear fit.

### Calibration Quality

The calibration plot (`*_calibration.png`) is the primary quality diagnostic:

- Blue dots: comparison stars (instrumental mag vs. published mag)
- Dashed line: the linear ensemble fit
- Red star: the target, placed on the fitted line

**Good photometry** — dots sit tightly along the fit line, RMS ≲ 0.03 mag
**Bad photometry** — wide scatter or curvature indicating a WCS problem, wrong aperture, poor sky subtraction, or guiding error

The web dashboard colour-codes the `RMS FIT` column:

| Colour | RMS | Interpretation |
|---|---|---|
| Green | < 0.03 mag | Excellent |
| Amber | 0.03 – 0.07 mag | Acceptable |
| Red | > 0.07 mag | Suspect — inspect plot before submitting |

---

## Web Dashboard

```bash
uv run hikari dashboard
# Open http://127.0.0.1:8765
```

- **Job list** (left panel) — status badges, observation counts, auto-refreshes every 30 s
- **Observation table** (right panel) — per-frame JD, magnitude, error, fit RMS, ensemble size, airmass
- **Ensemble fit panel** — interactive scatter plot showing comparison stars, fit line, and target position for the selected frame
- **Target cutout** — 120×120 px ZScale-stretched crop from the original FITS centred on the target
- **Reject / Accept** — mark bad observations before exporting
- **Download AAVSO CSV** — exports only accepted observations for the selected job

---

## Configuration

Run `hikari config` to generate an annotated `hikari.toml`. All settings can be overridden with `HIKARI_*` environment variables.

```toml
obs_code = "MUY"                  # Your AAVSO observer code
observer_notes = "CDK14, STL-11000M, Bortle 4"

[photometry]
aperture_radius = 4.0             # pixels
sky_inner_radius = 6.0            # pixels
sky_outer_radius = 10.0           # pixels
fwhm = 3.0                        # pixels (for DAOStarFinder)
source_snr = 50.0                 # detection threshold (sigma)
ensemble_size = 5                 # comparison stars in the ensemble fit
linear_limit = 60000.0            # ADU — above this, saturation is flagged

[comparison_stars]
brightest_comp_star = 7.0
dimmest_comp_star = 17.0
field_of_view = 18.5              # arcminutes (VSP query radius)
excluded_auids = []               # AUIDs to permanently exclude

[output]
output_dir = "~/Downloads"

[database]
database_url = "sqlite:////Users/you/.local/share/hikari/hikari.db"

[web]
host = "127.0.0.1"
port = 8765
```

### Environment variable overrides

```bash
HIKARI_OBS_CODE=MUY
HIKARI_APERTURE_RADIUS=5.0
HIKARI_DATABASE_URL=sqlite:////data/hikari.db
```

---

## Data Flow

```
FITS files
    │
    ▼
Simbad → resolve target RA/DEC (cached)
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
    │
Linear fit (instrumental mag → published mag)
    │
Calibrate target + check-star error estimate
    │
    ├── calibration.png   (quality check plot)
    ├── cutout.png        (100×100 px target image)
    ├── starmap.png       (full image with aperture overlays)
    └── lightcurve.png    (time series across all files)
    │
    ▼
AAVSO CSV  →  output_dir/AAVSO-webops-file-<timestamp>.csv
SQLite DB  →  ~/.local/share/hikari/hikari.db
```

---

## Coming Soon

### Transformation Coefficients

Currently Hikari reports **instrumental (untransformed) magnitudes**. This is acceptable for many variable star programs, but observers with colour-sensitive detectors or who need maximum accuracy should apply transformation coefficients to standardise results to the Johnson-Cousins system.

Planned work:
- Accept user-supplied first-order transformation coefficients (e.g. T_v, T_bv) in `hikari.toml`
- Apply the standard AAVSO/Henden transformation equations to each observation before writing the AAVSO CSV
- Report both the raw and transformed magnitude in the database for comparison
- Generate transformation diagnostic plots

Until this is implemented, if your telescope/filter combination has significant colour response differences from the standard system, you may want to note this in the AAVSO submission (`observer_notes`).

---

## CLI Reference

```
hikari run       PATH    Auto-detect targets from FITS headers and reduce all groups
hikari reduce    PATH    Reduce files for a specific star + filter
hikari dashboard         Start the web dashboard (default: http://127.0.0.1:8765)
hikari config    [DEST]  Write a default hikari.toml config file
hikari jobs              List recent batch jobs from the database
```

Global options available on all commands:

| Flag | Description |
|---|---|
| `--config / -c` | Path to a `hikari.toml` config file |
| `--verbose / -v` | Enable DEBUG-level console output |

---

## Tech Stack

| Library | Purpose |
|---|---|
| [astropy](https://www.astropy.org/) | FITS I/O, WCS, time handling, image stretching |
| [photutils](https://photutils.readthedocs.io/) | DAOStarFinder, aperture photometry |
| [astroquery](https://astroquery.readthedocs.io/) | Simbad catalog queries |
| [httpx](https://www.python-httpx.org/) | AAVSO VSP API requests |
| [scipy](https://scipy.org/) | KDTree for star matching |
| [numpy](https://numpy.org/) / [matplotlib](https://matplotlib.org/) | Numerics and plotting |
| [pydantic](https://docs.pydantic.dev/) | Data models and config validation |
| [SQLAlchemy](https://www.sqlalchemy.org/) | ORM and SQLite persistence |
| [FastAPI](https://fastapi.tiangolo.com/) | Web dashboard API |
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
│   │   ├── catalog.py      # Simbad + AAVSO VSP API (disk-cached)
│   │   ├── detection.py    # DAOStarFinder + KDTree star matching
│   │   ├── photometry.py   # Aperture photometry + sky subtraction
│   │   ├── calibration.py  # Ensemble selection, linear fit, error
│   │   └── pipeline.py     # Per-file + batch orchestration
│   ├── output/
│   │   ├── aavso.py        # AAVSO Extended Format CSV writer
│   │   ├── plots.py        # Calibration, cutout, star-map, lightcurve
│   │   └── database.py     # SQLAlchemy ORM + session management
│   └── web/
│       ├── app.py          # FastAPI dashboard + REST API
│       └── static/
│           └── index.html  # Single-page dashboard
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

## Licence

MIT
