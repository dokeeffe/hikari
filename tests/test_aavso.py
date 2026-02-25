"""Tests for AAVSO CSV output module."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from hikari.core.models import AavsoObservation, PhotometricFilter
from hikari.output.aavso import (
    AAVSO_HEADER,
    _observation_to_row,
    format_aavso_csv,
    write_aavso_csv,
)


def _obs(**kwargs) -> AavsoObservation:
    defaults = dict(
        name="YZ LMi",
        date=2460000.12345,
        magnitude=14.321,
        magnitude_error=0.045,
        filter=PhotometricFilter.V,
        chart="X12345",
        kname="123-456",
        kmag=14.1,
        airmass=1.23,
    )
    defaults.update(kwargs)
    return AavsoObservation(**defaults)


class TestObservationToRow:
    def test_row_length(self):
        row = _observation_to_row(_obs())
        assert len(row) == 15

    def test_name_first(self):
        row = _observation_to_row(_obs(name="V404 Cyg"))
        assert row[0] == "V404 Cyg"

    def test_jd_precision(self):
        row = _observation_to_row(_obs(date=2460000.12345))
        # 5 decimal places
        assert "." in row[1]
        assert len(row[1].split(".")[1]) == 5

    def test_filter_value(self):
        row = _observation_to_row(_obs(filter=PhotometricFilter.B))
        assert row[4] == "B"

    def test_none_airmass_becomes_na(self):
        row = _observation_to_row(_obs(airmass=None))
        assert row[11] == "na"

    def test_transformed_default(self):
        row = _observation_to_row(_obs())
        assert row[5] == "NO"

    def test_mtype_default(self):
        row = _observation_to_row(_obs())
        assert row[6] == "STD"

    def test_cname_ensemble(self):
        row = _observation_to_row(_obs())
        assert row[7] == "ENSEMBLE"


class TestFormatAavsoCsv:
    def test_header_present(self):
        obs = [_obs(), _obs(date=2460001.0, magnitude=14.5)]
        csv = format_aavso_csv(obs, obs_code="TEST")
        assert "#TYPE=EXTENDED" in csv
        assert "#OBSCODE=TEST" in csv

    def test_data_rows(self):
        obs = [_obs(name="YZ LMi"), _obs(name="SS Cyg")]
        csv = format_aavso_csv(obs, obs_code="MUY")
        assert "YZ LMi" in csv
        assert "SS Cyg" in csv

    def test_row_count(self):
        obs = [_obs() for _ in range(5)]
        csv = format_aavso_csv(obs, obs_code="MUY")
        # Count data lines (skip header lines starting with #)
        # The column names are embedded in the #-prefixed header block
        data_lines = [l for l in csv.splitlines() if l and not l.startswith("#")]
        assert len(data_lines) == 5  # 5 data rows


class TestWriteAavsoCsv:
    def test_file_written(self):
        obs = [_obs()]
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "test.csv"
            result = write_aavso_csv(obs, obs_code="MUY", dest=dest)
            assert result == dest
            assert dest.exists()

    def test_file_content(self):
        obs = [_obs(name="RR Lyr", magnitude=7.5)]
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out.csv"
            write_aavso_csv(obs, obs_code="TST", dest=dest)
            content = dest.read_text()
            assert "RR Lyr" in content
            assert "7.500" in content

    def test_creates_parent_dirs(self):
        obs = [_obs()]
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "nested" / "dir" / "out.csv"
            write_aavso_csv(obs, obs_code="TST", dest=dest)
            assert dest.exists()
