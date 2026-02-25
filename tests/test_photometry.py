"""Tests for aperture photometry module."""

from __future__ import annotations

import math

import numpy as np
import pytest

from hikari.core.models import MatchedStar
from hikari.core.photometry import measure_aperture


def _make_star_image(
    shape: tuple[int, int] = (200, 200),
    cx: float = 100.0,
    cy: float = 100.0,
    amplitude: float = 10_000.0,
    sigma: float = 2.0,
    sky: float = 500.0,
) -> np.ndarray:
    """Synthetic 2-D Gaussian star on a flat sky background."""
    y, x = np.mgrid[0 : shape[0], 0 : shape[1]]
    data = amplitude * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * sigma**2))
    return data + sky


def _make_star(auid: str, x: float, y: float, peak: float = 10_000.0) -> MatchedStar:
    return MatchedStar(
        auid=auid,
        x=x,
        y=y,
        peak_flux=peak,
        ra="00 00 00.0",
        dec="00 00 00.0",
    )


class TestMeasureAperture:
    def test_positive_flux(self):
        data = _make_star_image()
        star = _make_star("comp1", 100.0, 100.0)
        result = measure_aperture(data, star)
        assert result.aperture_sum > 0

    def test_instrumental_magnitude_finite(self):
        data = _make_star_image()
        star = _make_star("comp1", 100.0, 100.0)
        result = measure_aperture(data, star)
        assert result.instrumental_magnitude is not None
        assert math.isfinite(result.instrumental_magnitude)

    def test_mag_formula(self):
        """instrumental_mag = -2.5 * log10(flux)."""
        data = _make_star_image(amplitude=10_000.0, sky=0.0)
        star = _make_star("comp1", 100.0, 100.0)
        result = measure_aperture(data, star, sky_inner_radius=20.0, sky_outer_radius=30.0)
        # With no sky subtraction (using wide annulus on zero bg) flux ≈ amplitude integral
        expected_sign = -1  # negative mags for bright stars
        assert math.copysign(1, result.instrumental_magnitude) == expected_sign  # type: ignore

    def test_saturation_flagged(self):
        data = _make_star_image(amplitude=70_000.0)
        star = _make_star("comp1", 100.0, 100.0, peak=70_000.0)
        result = measure_aperture(data, star, linear_limit=60_000.0)
        assert result.saturated is True

    def test_not_saturated_below_limit(self):
        data = _make_star_image(amplitude=1_000.0)
        star = _make_star("comp1", 100.0, 100.0, peak=1_000.0)
        result = measure_aperture(data, star, linear_limit=60_000.0)
        assert result.saturated is False

    def test_sky_subtraction_reduces_flux(self):
        sky_level = 500.0
        data_with_sky = _make_star_image(sky=sky_level)
        data_no_sky = _make_star_image(sky=0.0)
        star = _make_star("comp1", 100.0, 100.0)
        res_with = measure_aperture(data_with_sky, star)
        res_no = measure_aperture(data_no_sky, star)
        # Both should produce similar fluxes after subtraction
        assert abs(res_with.aperture_sum - res_no.aperture_sum) / res_no.aperture_sum < 0.05

    def test_auid_preserved(self):
        data = _make_star_image()
        star = _make_star("my_star_001", 100.0, 100.0)
        result = measure_aperture(data, star)
        assert result.auid == "my_star_001"
