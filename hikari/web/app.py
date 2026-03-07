"""FastAPI web dashboard for Hikari.

Provides a REST API consumed by the single-page HTML dashboard,
enabling monitoring of batch jobs and browsing observation history.
"""

from __future__ import annotations

import datetime
import shutil
import threading
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
    RunORM,
    clear_all_data,
    create_run,
    get_run,
    get_session,
    get_job,
    init_db,
    list_jobs,
    list_observations,
    list_observations_for_run,
    list_runs,
    update_run,
)
from hikari.output.aavso import format_aavso_csv
from hikari.core.models import AavsoObservation, PhotometricFilter
from hikari.core.runner import run_directory
from hikari.config import load_settings

# ---------------------------------------------------------------------------
# FastAPI app with lifespan (initialise DB on startup)
# ---------------------------------------------------------------------------

_settings = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _settings
    _settings = load_settings()
    init_db(_settings.database_url)
    logger.info("Hikari dashboard started — DB: {}", _settings.database_url)
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
    check_star_published_magnitude: Optional[float] = None
    status: str
    rejected: bool
    rejection_reason: Optional[str]
    extra: Optional[dict] = None

    model_config = {"from_attributes": True}


class RunJobSummary(BaseModel):
    id: int
    object_name: str
    filter: str
    status: str
    n_observations: int
    n_failed: int

    model_config = {"from_attributes": True}


class RunSummary(BaseModel):
    id: int
    source_path: str
    status: str
    created_at: datetime.datetime
    completed_at: Optional[datetime.datetime]
    progress: Optional[dict]
    n_jobs: int
    n_observations: int
    notes: Optional[str] = None

    model_config = {"from_attributes": True}


class CreateRunRequest(BaseModel):
    source_path: str


# ---------------------------------------------------------------------------
# DB dependency
# ---------------------------------------------------------------------------

def get_db():
    with get_session() as session:
        yield session


# ---------------------------------------------------------------------------
# Run endpoints
# ---------------------------------------------------------------------------

def _run_background(run_id: int, source_path: str) -> None:
    """Background thread: process a directory and update run progress in DB."""
    try:
        settings = load_settings()
        groups_done = 0
        total_groups = 0

        def on_group_start(label: str, idx: int, total: int) -> None:
            nonlocal total_groups
            total_groups = total
            with get_session() as db:
                run = db.get(RunORM, run_id)
                if run is None:
                    return
                prog = dict(run.progress or {})
                prog["total"] = total
                prog["completed"] = idx
                prog["current_group"] = label
                run.progress = prog

        result = run_directory(
            source_path=source_path,
            settings=settings,
            run_id=run_id,
            on_group_start=on_group_start,
        )

        # Build final progress summary
        groups_summary = {}
        for g in result.groups:
            groups_summary[f"{g.object_name}/{g.filter}"] = {
                "n_succeeded": g.n_succeeded,
                "n_failed": g.n_failed,
                "error": g.error,
            }

        with get_session() as db:
            update_run(
                db,
                run_id,
                status="complete",
                completed_at=datetime.datetime.utcnow(),
                progress={
                    "total": len(result.groups),
                    "completed": len(result.groups),
                    "current_group": None,
                    "groups": groups_summary,
                },
            )
    except Exception as exc:
        logger.exception("Run {} failed: {}", run_id, exc)
        with get_session() as db:
            update_run(db, run_id, status="failed", completed_at=datetime.datetime.utcnow())


@app.post("/api/runs", response_model=RunSummary)
def api_create_run(payload: CreateRunRequest, db: Session = Depends(get_db)):
    """Create a new processing run and start it in the background."""
    source_path = str(Path(payload.source_path.strip()).expanduser())
    if not Path(source_path).exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {source_path}")

    run = create_run(db, source_path=source_path)
    run_id = run.id

    t = threading.Thread(
        target=_run_background,
        args=(run_id, source_path),
        daemon=True,
        name=f"hikari-run-{run_id}",
    )
    t.start()

    return RunSummary(
        id=run.id,
        source_path=run.source_path,
        status=run.status,
        created_at=run.created_at,
        completed_at=run.completed_at,
        progress=run.progress,
        n_jobs=0,
        n_observations=0,
    )


@app.get("/api/runs", response_model=list[RunSummary])
def api_list_runs(limit: int = 50, db: Session = Depends(get_db)):
    """List recent processing runs."""
    runs = list_runs(db, limit=limit)
    result = []
    for run in runs:
        jobs = run.jobs
        n_obs = sum(
            sum(1 for o in j.observations if o.status == "success" and not o.rejected)
            for j in jobs
        )
        result.append(RunSummary(
            id=run.id,
            source_path=run.source_path,
            status=run.status,
            created_at=run.created_at,
            completed_at=run.completed_at,
            progress=run.progress,
            n_jobs=len(jobs),
            n_observations=n_obs,
            notes=run.notes,
        ))
    return result


@app.get("/api/runs/{run_id}", response_model=RunSummary)
def api_get_run(run_id: int, db: Session = Depends(get_db)):
    """Get a single run with its progress and job summaries."""
    run = get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    jobs = run.jobs
    n_obs = sum(
        sum(1 for o in j.observations if o.status == "success" and not o.rejected)
        for j in jobs
    )
    return RunSummary(
        id=run.id,
        source_path=run.source_path,
        status=run.status,
        created_at=run.created_at,
        completed_at=run.completed_at,
        progress=run.progress,
        n_jobs=len(jobs),
        n_observations=n_obs,
        notes=run.notes,
    )


@app.get("/api/runs/{run_id}/jobs", response_model=list[JobSummary])
def api_run_jobs(run_id: int, db: Session = Depends(get_db)):
    """List all batch jobs belonging to a run."""
    run = get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    summaries = []
    for job in run.jobs:
        obs = job.observations
        n_ok = sum(1 for o in obs if o.status == "success" and not o.rejected)
        n_fail = sum(1 for o in obs if o.status == "failed")
        summaries.append(JobSummary(
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
        ))
    return summaries


class PatchRunRequest(BaseModel):
    notes: str


@app.patch("/api/runs/{run_id}")
def api_patch_run(run_id: int, payload: PatchRunRequest, db: Session = Depends(get_db)):
    """Update mutable fields on a run (currently: notes)."""
    run = update_run(db, run_id, notes=payload.notes)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"status": "ok"}


@app.delete("/api/runs/{run_id}")
def api_delete_run(run_id: int, db: Session = Depends(get_db)):
    """Delete a run and all its jobs and observations."""
    run = get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    for job in run.jobs:
        db.query(ObservationORM).filter(ObservationORM.job_id == job.id).delete()
        db.delete(job)
    db.delete(run)
    db.flush()
    return {"status": "deleted", "run_id": run_id}


@app.get("/api/runs/{run_id}/aavso-csv", response_class=PlainTextResponse)
def api_run_aavso_csv(run_id: int, db: Session = Depends(get_db)):
    """Return a combined AAVSO CSV for all accepted observations in a run."""
    run = get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    all_obs = list_observations_for_run(db, run_id)
    accepted = [o for o in all_obs if o.status == "success" and not o.rejected]

    # Build obs_code from first job's setting (they all share the same settings)
    obs_code = ""
    if run.jobs:
        obs_code = run.jobs[0].obs_code

    # Need chart_id per-job; build a lookup
    job_chart: dict[int, str] = {j.id: (j.chart_id or "") for j in run.jobs}

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
            chart=job_chart.get(o.job_id, ""),
        )
        for o in accepted
    ]
    return format_aavso_csv(aavso_obs, obs_code=obs_code)


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


@app.delete("/api/cache")
def api_clear_cache():
    """Delete all on-disk API response caches (Simbad, AAVSO VSP)."""
    cache_dir = _settings.resolved_cache_dir if _settings else Path.home() / ".cache" / "hikari"
    deleted = 0
    if cache_dir.exists():
        for entry in cache_dir.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry)
                deleted += 1
            else:
                entry.unlink()
                deleted += 1
    logger.info("Cache cleared: {} entries removed from {}", deleted, cache_dir)
    return {"status": "cleared", "entries_deleted": deleted, "cache_dir": str(cache_dir)}


# ---------------------------------------------------------------------------
# Directory browser
# ---------------------------------------------------------------------------

@app.get("/api/browse")
def api_browse(path: Optional[str] = None):
    """Return subdirectories of *path* for the directory-picker UI."""
    p = Path(path).expanduser().resolve() if path else Path.home()
    if not p.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")

    entries = []
    try:
        for entry in sorted(p.iterdir(), key=lambda e: e.name.lower()):
            if entry.is_dir() and not entry.name.startswith("."):
                entries.append({"name": entry.name, "path": str(entry)})
    except PermissionError:
        pass

    parent = str(p.parent) if p != p.parent else None
    return {"path": str(p), "parent": parent, "entries": entries}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0"}
