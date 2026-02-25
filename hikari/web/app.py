"""FastAPI web dashboard for Hikari.

Provides a REST API consumed by the single-page HTML dashboard,
enabling monitoring of batch jobs and browsing observation history.
"""

from __future__ import annotations

import datetime
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from astropy.io import fits as astropy_fits
from astropy.visualization import simple_norm
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hikari.output.database import (
    BatchJobORM,
    ObservationORM,
    clear_all_data,
    get_session,
    get_job,
    init_db,
    list_jobs,
    list_observations,
)
from hikari.output.aavso import format_aavso_csv
from hikari.core.models import AavsoObservation, PhotometricFilter
from hikari.config import load_settings

# ---------------------------------------------------------------------------
# FastAPI app with lifespan (initialise DB on startup)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    init_db(settings.database_url)
    logger.info("Hikari dashboard started — DB: {}", settings.database_url)
    yield


app = FastAPI(
    title="Hikari Dashboard",
    description="Variable star photometry batch monitoring",
    version="0.1.0",
    lifespan=lifespan,
)

STATIC_DIR = Path(__file__).parent / "static"

# Serve the SPA at /
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


# ---------------------------------------------------------------------------
# Pydantic response schemas
# ---------------------------------------------------------------------------

class JobSummary(BaseModel):
    id: int
    object_name: str
    filter: str
    status: str
    created_at: datetime.datetime
    completed_at: Optional[datetime.datetime]
    n_observations: int
    n_failed: int
    chart_id: Optional[str]
    aavso_csv_path: Optional[str]

    model_config = {"from_attributes": True}


class ObservationSummary(BaseModel):
    id: int
    job_id: int
    file_name: str
    star_name: str
    julian_date: float
    magnitude: Optional[float]
    magnitude_error: Optional[float]
    filter: str
    airmass: Optional[float]
    fit_rms: Optional[float]
    n_ensemble_stars: Optional[int]
    check_star_auid: Optional[str]
    check_star_magnitude: Optional[float]
    status: str
    rejected: bool
    rejection_reason: Optional[str]
    extra: Optional[dict] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# DB dependency
# ---------------------------------------------------------------------------

def get_db():
    with get_session() as session:
        yield session


# ---------------------------------------------------------------------------
# Jobs endpoints
# ---------------------------------------------------------------------------

@app.get("/api/jobs", response_model=list[JobSummary])
def api_list_jobs(limit: int = 50, db: Session = Depends(get_db)):
    """List recent batch jobs."""
    jobs = list_jobs(db, limit=limit)
    summaries = []
    for job in jobs:
        obs = job.observations
        n_ok = sum(1 for o in obs if o.status == "success" and not o.rejected)
        n_fail = sum(1 for o in obs if o.status == "failed")
        summaries.append(
            JobSummary(
                id=job.id,
                object_name=job.object_name,
                filter=job.filter,
                status=job.status,
                created_at=job.created_at,
                completed_at=job.completed_at,
                n_observations=n_ok,
                n_failed=n_fail,
                chart_id=job.chart_id,
                aavso_csv_path=job.aavso_csv_path,
            )
        )
    return summaries


@app.get("/api/jobs/{job_id}", response_model=JobSummary)
def api_get_job(job_id: int, db: Session = Depends(get_db)):
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    obs = job.observations
    return JobSummary(
        id=job.id,
        object_name=job.object_name,
        filter=job.filter,
        status=job.status,
        created_at=job.created_at,
        completed_at=job.completed_at,
        n_observations=sum(1 for o in obs if o.status == "success"),
        n_failed=sum(1 for o in obs if o.status == "failed"),
        chart_id=job.chart_id,
        aavso_csv_path=job.aavso_csv_path,
    )


@app.get("/api/jobs/{job_id}/observations", response_model=list[ObservationSummary])
def api_job_observations(job_id: int, db: Session = Depends(get_db)):
    """List all observations for a job."""
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    obs = list_observations(db, job_id=job_id)
    return [ObservationSummary.model_validate(o) for o in obs]


# ---------------------------------------------------------------------------
# Observations endpoints
# ---------------------------------------------------------------------------

@app.get("/api/observations", response_model=list[ObservationSummary])
def api_list_observations(
    star_name: Optional[str] = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """Query observations across all jobs, optionally filtered by star name."""
    obs = list_observations(db, star_name=star_name, limit=limit)
    return [ObservationSummary.model_validate(o) for o in obs]


class RejectPayload(BaseModel):
    reason: str = ""


@app.post("/api/observations/{obs_id}/reject")
def api_reject_observation(
    obs_id: int,
    payload: RejectPayload,
    db: Session = Depends(get_db),
):
    """Mark an observation as rejected (user quality check)."""
    obs = db.get(ObservationORM, obs_id)
    if not obs:
        raise HTTPException(status_code=404, detail="Observation not found")
    obs.rejected = True
    obs.rejection_reason = payload.reason
    db.flush()
    return {"status": "rejected", "id": obs_id}


@app.post("/api/observations/{obs_id}/accept")
def api_accept_observation(obs_id: int, db: Session = Depends(get_db)):
    """Un-reject a previously rejected observation."""
    obs = db.get(ObservationORM, obs_id)
    if not obs:
        raise HTTPException(status_code=404, detail="Observation not found")
    obs.rejected = False
    obs.rejection_reason = None
    db.flush()
    return {"status": "accepted", "id": obs_id}


@app.get("/api/observations/{obs_id}/cutout")
def api_observation_cutout(obs_id: int, db: Session = Depends(get_db)):
    """Return a 120×120 px PNG cutout of the target star from the FITS file."""
    obs = db.get(ObservationORM, obs_id)
    if not obs:
        raise HTTPException(status_code=404, detail="Observation not found")
    extra = obs.extra or {}
    x = extra.get("target_x")
    y = extra.get("target_y")
    if x is None or y is None:
        raise HTTPException(status_code=404, detail="No target coordinates available")
    fits_path = Path(obs.file_name)
    if not fits_path.exists():
        raise HTTPException(status_code=404, detail="FITS file not found")

    with astropy_fits.open(str(fits_path)) as hdul:
        data = hdul[0].data.astype(float)

    half = 60
    ix, iy = int(round(x)), int(round(y))
    ny, nx = data.shape
    x0, x1 = max(0, ix - half), min(nx, ix + half)
    y0, y1 = max(0, iy - half), min(ny, iy + half)
    cutout = data[y0:y1, x0:x1]

    norm = simple_norm(cutout, stretch="sqrt")
    fig, ax = plt.subplots(figsize=(3, 3), dpi=40)
    ax.imshow(cutout, norm=norm, cmap="gray", origin="lower")
    ax.axis("off")
    fig.tight_layout(pad=0)

    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="image/png")


# ---------------------------------------------------------------------------
# AAVSO CSV download
# ---------------------------------------------------------------------------

@app.get("/api/jobs/{job_id}/aavso-csv", response_class=PlainTextResponse)
def api_aavso_csv(job_id: int, db: Session = Depends(get_db)):
    """Return the AAVSO CSV text for the given job (accepted observations only)."""
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    obs_rows = [
        o for o in list_observations(db, job_id=job_id)
        if o.status == "success" and not o.rejected
    ]
    aavso_obs = [
        AavsoObservation(
            name=o.star_name,
            date=o.julian_date,
            magnitude=o.magnitude or 0.0,
            magnitude_error=o.magnitude_error or 0.0,
            filter=PhotometricFilter(o.filter),
            kname=o.check_star_auid,
            kmag=o.check_star_magnitude,
            airmass=o.airmass,
            chart=job.chart_id or "",
        )
        for o in obs_rows
    ]
    return format_aavso_csv(aavso_obs, obs_code=job.obs_code)


# ---------------------------------------------------------------------------
# Data management
# ---------------------------------------------------------------------------

@app.delete("/api/data")
def api_clear_all_data(db: Session = Depends(get_db)):
    """Delete all batch jobs and observations from the database."""
    n_jobs = clear_all_data(db)
    return {"status": "cleared", "jobs_deleted": n_jobs}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0"}
