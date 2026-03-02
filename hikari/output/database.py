"""SQLAlchemy ORM models and session management.

Provides persistent storage for batch jobs and observation results,
enabling history queries and multi-session tracking.
"""

from __future__ import annotations

import datetime
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from hikari.core.models import ObservationStatus


# ---------------------------------------------------------------------------
# ORM base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------

class BatchJobORM(Base):
    """Represents one batch photometry run."""

    __tablename__ = "batch_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    object_name: Mapped[str] = mapped_column(String(200))
    filter: Mapped[str] = mapped_column(String(10))
    source_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=ObservationStatus.PENDING.value)
    obs_code: Mapped[str] = mapped_column(String(20), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    aavso_csv_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chart_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Configuration snapshot
    aperture_radius: Mapped[float] = mapped_column(Float, default=4.0)
    fwhm: Mapped[float] = mapped_column(Float, default=3.0)
    ensemble_size: Mapped[int] = mapped_column(Integer, default=5)

    observations: Mapped[list["ObservationORM"]] = relationship(
        "ObservationORM", back_populates="job", cascade="all, delete-orphan"
    )


class ObservationORM(Base):
    """One calibrated magnitude measurement (one FITS file → one row)."""

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("batch_jobs.id"))
    file_name: Mapped[str] = mapped_column(String(500))

    # Core photometry result
    star_name: Mapped[str] = mapped_column(String(200))
    julian_date: Mapped[float] = mapped_column(Float)
    magnitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    magnitude_error: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    filter: Mapped[str] = mapped_column(String(10))
    airmass: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Calibration quality
    fit_slope: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fit_intercept: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fit_rms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    n_ensemble_stars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Check star
    check_star_auid: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    check_star_magnitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    check_star_published_magnitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Status
    status: Mapped[str] = mapped_column(String(20), default=ObservationStatus.SUCCESS.value)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rejected: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Extra metadata (JSON blob for flexibility)
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    job: Mapped["BatchJobORM"] = relationship("BatchJobORM", back_populates="observations")


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

_engine = None
_SessionLocal: Optional[sessionmaker] = None


def _migrate(engine) -> None:
    """Apply additive schema migrations for existing databases."""
    from sqlalchemy import text
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(observations)"))}
        if "check_star_published_magnitude" not in existing:
            conn.execute(text("ALTER TABLE observations ADD COLUMN check_star_published_magnitude REAL"))
            conn.commit()


def init_db(database_url: str = "sqlite:///hikari.db") -> None:
    """Initialise the database engine and create all tables.

    For SQLite URLs, the parent directory is created automatically if it
    does not already exist.
    """
    global _engine, _SessionLocal

    # Ensure the SQLite directory exists before SQLAlchemy tries to open the file
    if database_url.startswith("sqlite:///"):
        db_path = Path(database_url[len("sqlite:///"):])
        db_path.parent.mkdir(parents=True, exist_ok=True)

    _engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False} if "sqlite" in database_url else {},
        echo=False,
    )
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    Base.metadata.create_all(_engine)
    _migrate(_engine)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Provide a transactional session scope."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    session: Session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Repository helpers
# ---------------------------------------------------------------------------

def save_batch_job(session: Session, job_data: dict) -> BatchJobORM:
    """Create and persist a BatchJobORM from a dict of field values."""
    job = BatchJobORM(**job_data)
    session.add(job)
    session.flush()  # get the auto-generated id
    return job


def save_observation(session: Session, obs_data: dict) -> ObservationORM:
    """Create and persist an ObservationORM."""
    obs = ObservationORM(**obs_data)
    session.add(obs)
    session.flush()
    return obs


def list_jobs(session: Session, limit: int = 50) -> list[BatchJobORM]:
    return (
        session.query(BatchJobORM)
        .order_by(BatchJobORM.created_at.desc())
        .limit(limit)
        .all()
    )


def get_job(session: Session, job_id: int) -> Optional[BatchJobORM]:
    return session.get(BatchJobORM, job_id)


def list_observations(
    session: Session,
    job_id: Optional[int] = None,
    star_name: Optional[str] = None,
    limit: int = 200,
) -> list[ObservationORM]:
    q = session.query(ObservationORM)
    if job_id is not None:
        q = q.filter(ObservationORM.job_id == job_id)
    if star_name is not None:
        q = q.filter(ObservationORM.star_name == star_name)
    return q.order_by(ObservationORM.julian_date.asc()).limit(limit).all()


def clear_all_data(session: Session) -> int:
    """Delete all batch jobs and their observations.

    Returns the number of jobs deleted.
    """
    n_jobs = session.query(BatchJobORM).count()
    session.query(ObservationORM).delete()
    session.query(BatchJobORM).delete()
    return n_jobs
