"""Tests for ensemble photometry calibration module."""

from __future__ import annotations

import pytest

from hikari.core.calibration import (
    CalibrationError,
    fit_ensemble,
    run_calibration,
    select_ensemble,
)
from hikari.core.models import (
    ApertureResult,
    CalibrationFit,
    FitsMetadata,
    MatchedStar,
    PhotometricFilter,
)
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _aperture(auid: str, instr_mag: float, peak: float = 1000.0) -> ApertureResult:
    return ApertureResult(
        auid=auid,
        x=0.0,
        y=0.0,
        aperture_sum=100.0,
        sky_background=5.0,
        peak_flux=peak,
        instrumental_magnitude=instr_mag,
        saturated=False,
    )


def _star(auid: str, pub_mag: float) -> MatchedStar:
    return MatchedStar(
        auid=auid,
        x=0.0,
        y=0.0,
        peak_flux=1000.0,
        ra="00 00 00",
        dec="00 00 00",
        published_magnitude=pub_mag,
    )


def _fits_meta() -> FitsMetadata:
    return FitsMetadata(
        file_path=Path("test.fits"),
        object_name="Test Star",
        filter_raw="V",
        filter=PhotometricFilter.V,
        exposure_time=60.0,
        date_obs="2024-01-01T00:00:00",
        julian_date=2460000.0,
        airmass=1.2,
        image_width=1024,
        image_height=1024,
    )


# ---------------------------------------------------------------------------
# fit_ensemble tests
# ---------------------------------------------------------------------------

class TestFitEnsemble:
    def test_perfect_linear_relation(self):
        """When pub_mag = 2 * instr_mag + 1, fit should recover those coefficients."""
        comp_results = [_aperture(f"c{i}", float(i)) for i in range(1, 6)]
        comp_stars = [_star(f"c{i}", 2.0 * i + 1) for i in range(1, 6)]
        fit = fit_ensemble(comp_results, comp_stars)
        assert abs(fit.slope - 2.0) < 0.01
        assert abs(fit.intercept - 1.0) < 0.01
        assert fit.residual_rms < 0.001

    def test_residual_rms_nonzero_for_scatter(self):
        """Noisy data should produce non-zero RMS."""
        comp_results = [_aperture(f"c{i}", float(i)) for i in range(1, 6)]
        # Introduce scatter in published mags
        comp_stars = [_star(f"c{i}", float(i) + (0.1 if i % 2 == 0 else -0.1)) for i in range(1, 6)]
        fit = fit_ensemble(comp_results, comp_stars)
        assert fit.residual_rms > 0.0

    def test_raises_with_fewer_than_2_stars(self):
        comp_results = [_aperture("c1", 1.0)]
        comp_stars = [_star("c1", 2.0)]
        with pytest.raises(CalibrationError):
            fit_ensemble(comp_results, comp_stars)

    def test_n_stars_count(self):
        comp_results = [_aperture(f"c{i}", float(i)) for i in range(5)]
        comp_stars = [_star(f"c{i}", float(i) + 5.0) for i in range(5)]
        fit = fit_ensemble(comp_results, comp_stars)
        assert fit.n_stars == 5


# ---------------------------------------------------------------------------
# select_ensemble tests
# ---------------------------------------------------------------------------

class TestSelectEnsemble:
    def test_selects_n_closest(self):
        target = _aperture("target", 10.0)
        comp_results = [_aperture(f"c{i}", float(i)) for i in range(20)]
        comp_stars = [_star(f"c{i}", float(i) + 5.0) for i in range(20)]
        sel_results, sel_stars = select_ensemble(comp_results, comp_stars, target, ensemble_size=5)
        assert len(sel_results) == 5
        # All selected stars should be closer to target (instr mag 10) than excluded ones
        for r in sel_results:
            assert abs(r.instrumental_magnitude - 10.0) <= 10.0

    def test_excludes_saturated(self):
        target = _aperture("target", 5.0)
        comp_results = [
            _aperture("c1", 5.1, peak=70_000.0),  # saturated
            _aperture("c2", 5.2),
            _aperture("c3", 5.3),
        ]
        comp_results[0] = ApertureResult(
            auid="c1", x=0, y=0, aperture_sum=1e6, sky_background=0,
            peak_flux=70_000.0, instrumental_magnitude=5.1, saturated=True
        )
        comp_stars = [_star(f"c{i}", float(i) + 5.0) for i in range(1, 4)]
        sel_results, _ = select_ensemble(comp_results, comp_stars, target, ensemble_size=5)
        assert all(not r.saturated for r in sel_results)

    def test_raises_when_no_valid_comps(self):
        target = _aperture("target", 10.0)
        with pytest.raises(CalibrationError):
            select_ensemble([], [], target, ensemble_size=5)


# ---------------------------------------------------------------------------
# run_calibration integration
# ---------------------------------------------------------------------------

class TestRunCalibration:
    def test_end_to_end(self):
        # A simple linear scene: pub = instr + 10
        target = _aperture("target", 8.0)
        comp_results = [_aperture(f"c{i}", float(i)) for i in range(1, 8)]
        comp_stars = [_star(f"c{i}", float(i) + 10.0) for i in range(1, 8)]
        meta = _fits_meta()

        cal, fit, ens_results, ens_stars = run_calibration(
            comp_results=comp_results,
            comp_stars=comp_stars,
            target_result=target,
            fits_meta=meta,
            ensemble_size=5,
        )
        # Expected: target calibrated mag ≈ 8 + 10 = 18
        assert abs(cal.magnitude - 18.0) < 0.5
        assert cal.filter == PhotometricFilter.V
        assert cal.julian_date == 2460000.0
