"""Tests for FITS reader utilities (metadata parsing, grouping)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.time import Time

from hikari.core.fits_reader import (
    _parse_julian_date,
    discover_fits_files,
    group_by_object_and_filter,
    read_fits_metadata,
)
from hikari.core.models import PhotometricFilter


def _write_fits(
    path: Path,
    object_name: str = "YZ LMi",
    filter_name: str = "V",
    exptime: float = 60.0,
    date_obs: str = "2024-06-01T00:00:00",
    airmass: float = 1.2,
) -> Path:
    """Write a minimal synthetic FITS file."""
    hdr = fits.Header()
    hdr["OBJECT"] = object_name
    hdr["FILTER"] = filter_name
    hdr["EXPTIME"] = exptime
    hdr["DATE-OBS"] = date_obs
    hdr["AIRMASS"] = airmass
    data = np.zeros((100, 100), dtype=np.float32)
    hdu = fits.PrimaryHDU(data=data, header=hdr)
    hdu.writeto(str(path), overwrite=True)
    return path


class TestDiscoverFitsFiles:
    def test_finds_fits_in_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _write_fits(p / "a.fits")
            _write_fits(p / "b.fits")
            files = discover_fits_files(p)
            assert len(files) == 2

    def test_single_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = _write_fits(Path(tmp) / "c.fits")
            files = discover_fits_files(f)
            assert files == [f]

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = discover_fits_files(Path(tmp))
            assert files == []


class TestGroupByObjectAndFilter:
    def test_groups_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _write_fits(p / "a.fits", object_name="YZ LMi", filter_name="V")
            _write_fits(p / "b.fits", object_name="YZ LMi", filter_name="V")
            _write_fits(p / "c.fits", object_name="SS Cyg", filter_name="B")
            files = discover_fits_files(p)
            groups = group_by_object_and_filter(files)
            assert len(groups) == 2
            assert ("YZ LMi", PhotometricFilter.V) in groups
            assert ("SS Cyg", PhotometricFilter.B) in groups
            assert len(groups[("YZ LMi", PhotometricFilter.V)]) == 2

    def test_filter_map_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _write_fits(p / "a.fits", filter_name="PV")  # Should map to V
            files = discover_fits_files(p)
            groups = group_by_object_and_filter(files)
            assert any(filt == PhotometricFilter.V for _, filt in groups)


class TestReadFitsMetadata:
    def test_extracts_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = _write_fits(Path(tmp) / "t.fits", object_name="RR Lyr")
            meta = read_fits_metadata(f)
            assert meta.object_name == "RR Lyr"

    def test_extracts_exptime(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = _write_fits(Path(tmp) / "t.fits", exptime=120.0)
            meta = read_fits_metadata(f)
            assert meta.exposure_time == pytest.approx(120.0)

    def test_julian_date_from_date_obs(self):
        with tempfile.TemporaryDirectory() as tmp:
            date = "2024-06-01T00:00:00"
            f = _write_fits(Path(tmp) / "t.fits", date_obs=date)
            meta = read_fits_metadata(f)
            expected_jd = Time(date, format="isot", scale="utc").jd
            assert meta.julian_date == pytest.approx(expected_jd, abs=0.001)

    def test_filter_mapped(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = _write_fits(Path(tmp) / "t.fits", filter_name="PB")
            meta = read_fits_metadata(f)
            assert meta.filter == PhotometricFilter.B

    def test_airmass(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = _write_fits(Path(tmp) / "t.fits", airmass=1.45)
            meta = read_fits_metadata(f)
            assert meta.airmass == pytest.approx(1.45)
