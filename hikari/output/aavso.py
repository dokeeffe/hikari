"""AAVSO Extended Format CSV writer.

Specification: https://www.aavso.org/aavso-extended-file-format
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from hikari import __version__
from hikari.core.models import AavsoObservation, CalibratedMagnitude, PhotometricFilter

# ---------------------------------------------------------------------------
# AAVSO Extended Format header
# ---------------------------------------------------------------------------

AAVSO_HEADER = """\
#TYPE=EXTENDED
#OBSCODE={obs_code}
#SOFTWARE=Hikari v{version}
#DELIM=,
#DATE=JD
#OBSTYPE=CCD
#NAME,DATE,MAG,MERR,FILT,TRANS,MTYPE,CNAME,CMAG,KNAME,KMAG,AMASS,GROUP,CHART,NOTES
"""

AAVSO_COLUMNS = [
    "NAME", "DATE", "MAG", "MERR", "FILT", "TRANS",
    "MTYPE", "CNAME", "CMAG", "KNAME", "KMAG", "AMASS",
    "GROUP", "CHART", "NOTES",
]


def _fmt(value: object, precision: int = 3) -> str:
    """Format a number or return 'na' for None."""
    if value is None:
        return "na"
    if isinstance(value, float):
        return f"{value:.{precision}f}"
    return str(value)


def calibrated_to_observation(
    cal: CalibratedMagnitude,
    star_name: str,
    chart_id: str,
    obs_code: str = "",
    notes: str = "",
    check_star_auid: Optional[str] = None,
    check_star_magnitude: Optional[float] = None,
) -> AavsoObservation:
    """Convert a :class:`CalibratedMagnitude` to an :class:`AavsoObservation`."""
    return AavsoObservation(
        name=star_name,
        date=cal.julian_date,
        magnitude=cal.magnitude,
        magnitude_error=cal.magnitude_error,
        filter=cal.filter,
        transformed="NO",
        mtype="STD",
        cname="ENSEMBLE",
        cmag="na",
        kname=check_star_auid or cal.check_star_auid,
        kmag=check_star_magnitude or cal.check_star_magnitude,
        airmass=cal.airmass,
        group="na",
        chart=chart_id,
        notes=notes,
    )


def write_aavso_csv(
    observations: list[AavsoObservation],
    obs_code: str,
    dest: Optional[Path] = None,
) -> Path:
    """Write observations to an AAVSO Extended Format CSV file.

    Parameters
    ----------
    observations:
        List of observations to write (one row each).
    obs_code:
        AAVSO observer code for the file header.
    dest:
        Output file path. If ``None``, a timestamped file is created in
        ``~/Downloads/``.

    Returns
    -------
    Path
        Absolute path of the written file.
    """
    if dest is None:
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        dest = Path.home() / "Downloads" / f"AAVSO-webops-file-{ts}.csv"

    dest.parent.mkdir(parents=True, exist_ok=True)

    with open(dest, "w", newline="", encoding="utf-8") as fh:
        # Write the AAVSO header block
        fh.write(
            AAVSO_HEADER.format(obs_code=obs_code, version=__version__)
        )

        writer = csv.writer(fh, lineterminator="\n")
        for obs in observations:
            writer.writerow(_observation_to_row(obs))

    logger.info("AAVSO CSV written → {}", dest)
    return dest


def _observation_to_row(obs: AavsoObservation) -> list[str]:
    return [
        obs.name,
        _fmt(obs.date, 5),
        _fmt(obs.magnitude, 3),
        _fmt(obs.magnitude_error, 3),
        obs.filter.value,
        obs.transformed,
        obs.mtype,
        obs.cname,
        obs.cmag,
        obs.kname or "na",
        _fmt(obs.kmag, 3),
        _fmt(obs.airmass, 3),
        obs.group,
        obs.chart,
        obs.notes,
    ]


def format_aavso_csv(
    observations: list[AavsoObservation],
    obs_code: str,
) -> str:
    """Return the AAVSO CSV content as a string (for preview / API use)."""
    buf = io.StringIO()
    buf.write(AAVSO_HEADER.format(obs_code=obs_code, version=__version__))
    writer = csv.writer(buf, lineterminator="\n")
    for obs in observations:
        writer.writerow(_observation_to_row(obs))
    return buf.getvalue()
