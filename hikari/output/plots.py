"""Diagnostic plot generation.

Produces four types of plots:
  1. Calibration scatter plot (primary quality check)
  2. Cropped stellar image of the target
  3. Comparison star overlay map (full image with aperture circles)
  4. Lightcurve (magnitude vs. time for multi-file sets)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # non-interactive backend for batch use

import matplotlib.pyplot as plt
import numpy as np
from astropy.visualization import simple_norm
from loguru import logger

from hikari.core.models import ApertureResult, CalibratedMagnitude, MatchedStar


# ---------------------------------------------------------------------------
# 1. Calibration plot
# ---------------------------------------------------------------------------

def plot_calibration(
    ensemble_results: list[ApertureResult],
    ensemble_stars: list[MatchedStar],
    target_result: ApertureResult,
    target_cal: CalibratedMagnitude,
    output_path: Path,
    title: str = "",
) -> Path:
    """Scatter plot of instrumental vs. published comp-star magnitudes.

    Overlays the linear fit and marks the target star.  This is the
    primary quality-check plot: a good observation shows all comp-star
    points tightly clustered around the fit line.

    Returns the path of the saved PNG.
    """
    fig, ax = plt.subplots(figsize=(8, 6))

    instr_mags = []
    pub_mags = []
    auids = []
    for res, star in zip(ensemble_results, ensemble_stars):
        if res.instrumental_magnitude is not None and star.published_magnitude is not None:
            instr_mags.append(res.instrumental_magnitude)
            pub_mags.append(star.published_magnitude)
            auids.append(star.auid)

    if instr_mags:
        ax.scatter(instr_mags, pub_mags, color="steelblue", s=60, zorder=3, label="Comp stars")

        # Annotate comp stars
        for x, y, auid in zip(instr_mags, pub_mags, auids):
            ax.annotate(
                f"{auid}\n{y:.2f}",
                xy=(x, y),
                xytext=(4, 4),
                textcoords="offset points",
                fontsize=7,
                color="steelblue",
            )

        # Fit line
        fit = target_cal.calibration_fit
        x_range = np.linspace(min(instr_mags) - 0.5, max(instr_mags) + 0.5, 100)
        y_fit = fit.slope * x_range + fit.intercept
        ax.plot(
            x_range,
            y_fit,
            color="gray",
            linestyle="--",
            linewidth=1.2,
            label=f"Fit: y = {fit.slope:.3f}x + {fit.intercept:.3f}  (RMS={fit.residual_rms:.3f})",
        )

    # Target star position on the fit line
    if target_result.instrumental_magnitude is not None:
        target_instr = target_result.instrumental_magnitude
        target_pub_est = target_cal.calibration_fit.slope * target_instr + target_cal.calibration_fit.intercept
        ax.scatter(
            [target_instr],
            [target_pub_est],
            marker="*",
            color="crimson",
            s=200,
            zorder=4,
            label=f"Target ({target_cal.magnitude:.3f} mag)",
        )

    ax.set_xlabel("Instrumental magnitude")
    ax.set_ylabel("Published magnitude")
    ax.set_title(title or "Photometric calibration")
    ax.legend(fontsize=8)
    ax.invert_yaxis()
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    logger.debug("Calibration plot → {}", output_path)
    return output_path


# ---------------------------------------------------------------------------
# 2. Cropped stellar image
# ---------------------------------------------------------------------------

def plot_target_cutout(
    data: np.ndarray,
    target_result: ApertureResult,
    output_path: Path,
    size: int = 100,
    title: str = "",
) -> Path:
    """Save a 100×100 px crop centred on the target star.

    Applies a square-root stretch normalisation for better visibility of
    faint-star detail and potential saturation spikes.
    """
    cx, cy = int(round(target_result.x)), int(round(target_result.y))
    half = size // 2
    h, w = data.shape

    x0, x1 = max(0, cx - half), min(w, cx + half)
    y0, y1 = max(0, cy - half), min(h, cy + half)
    crop = data[y0:y1, x0:x1]

    norm = simple_norm(crop, stretch="sqrt", percent=99.5)

    fig, ax = plt.subplots(figsize=(4, 4))
    ax.imshow(crop, origin="lower", cmap="gray", norm=norm)
    ax.set_title(title or "Target cutout")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    logger.debug("Target cutout → {}", output_path)
    return output_path


# ---------------------------------------------------------------------------
# 3. Comparison star overlay map
# ---------------------------------------------------------------------------

def plot_star_map(
    data: np.ndarray,
    comp_results: list[ApertureResult],
    comp_stars: list[MatchedStar],
    target_result: ApertureResult,
    aperture_radius: float,
    output_path: Path,
    title: str = "",
) -> Path:
    """Full-image display with circular aperture overlays.

    Blue circles = comparison stars, red circle = target star.
    """
    norm = simple_norm(data, stretch="sqrt", percent=99.5)

    fig, ax = plt.subplots(figsize=(10, 10))
    ax.imshow(data, origin="lower", cmap="gray", norm=norm)

    # Comparison stars
    for res, star in zip(comp_results, comp_stars):
        circ = plt.Circle(
            (res.x, res.y),
            aperture_radius,
            color="deepskyblue",
            fill=False,
            linewidth=1.2,
        )
        ax.add_patch(circ)
        label = f"{star.auid}\n{star.published_magnitude:.2f}" if star.published_magnitude else star.auid
        ax.annotate(
            label,
            xy=(res.x, res.y),
            xytext=(aperture_radius + 3, 0),
            textcoords="offset points",
            fontsize=6,
            color="deepskyblue",
        )

    # Target star
    circ = plt.Circle(
        (target_result.x, target_result.y),
        aperture_radius,
        color="crimson",
        fill=False,
        linewidth=2,
    )
    ax.add_patch(circ)
    ax.annotate(
        "TARGET",
        xy=(target_result.x, target_result.y),
        xytext=(aperture_radius + 3, 0),
        textcoords="offset points",
        fontsize=7,
        color="crimson",
        fontweight="bold",
    )

    ax.set_title(title or "Comparison star map")
    ax.set_xlabel("x (px)")
    ax.set_ylabel("y (px)")
    fig.tight_layout()
    fig.savefig(output_path, dpi=100)
    plt.close(fig)
    logger.debug("Star map → {}", output_path)
    return output_path


# ---------------------------------------------------------------------------
# 4. Lightcurve
# ---------------------------------------------------------------------------

def plot_lightcurve(
    calibrated_mags: list[CalibratedMagnitude],
    output_path: Path,
    star_name: str = "",
    filter_label: str = "",
) -> Path:
    """Time-series magnitude plot with error bars.

    Parameters
    ----------
    calibrated_mags:
        Sorted list of calibrated magnitude results (one per file).
    output_path:
        PNG output path.
    """
    if not calibrated_mags:
        logger.warning("No calibrated magnitudes for lightcurve plot")
        return output_path

    jds = [c.julian_date for c in calibrated_mags]
    mags = [c.magnitude for c in calibrated_mags]
    errs = [c.magnitude_error for c in calibrated_mags]

    # Normalise JD to first observation for a cleaner x-axis
    jd_offset = min(jds)
    jds_plot = [j - jd_offset for j in jds]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.errorbar(
        jds_plot,
        mags,
        yerr=errs,
        fmt="o",
        color="steelblue",
        ecolor="lightsteelblue",
        elinewidth=1.5,
        capsize=4,
        markersize=6,
    )
    ax.invert_yaxis()
    ax.set_xlabel(f"JD - {jd_offset:.1f}")
    ax.set_ylabel("Magnitude")
    title = star_name
    if filter_label:
        title += f" ({filter_label})"
    ax.set_title(title or "Lightcurve")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    logger.debug("Lightcurve plot → {}", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Convenience: generate all plots for a single file result
# ---------------------------------------------------------------------------

def generate_all_plots(
    data: np.ndarray,
    comp_results: list[ApertureResult],
    comp_stars: list[MatchedStar],
    target_result: ApertureResult,
    target_cal: CalibratedMagnitude,
    ensemble_results: list[ApertureResult],
    ensemble_stars: list[MatchedStar],
    aperture_radius: float,
    output_dir: Path,
    stem: str,
    star_name: str = "",
) -> dict[str, Path]:
    """Generate calibration, cutout, and star-map plots for one FITS file.

    Returns a dict mapping plot type to file path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    paths["calibration"] = plot_calibration(
        ensemble_results=ensemble_results,
        ensemble_stars=ensemble_stars,
        target_result=target_result,
        target_cal=target_cal,
        output_path=output_dir / f"{stem}_calibration.png",
        title=f"{star_name} – calibration",
    )
    paths["cutout"] = plot_target_cutout(
        data=data,
        target_result=target_result,
        output_path=output_dir / f"{stem}_cutout.png",
        title=f"{star_name} – target cutout",
    )
    paths["starmap"] = plot_star_map(
        data=data,
        comp_results=comp_results,
        comp_stars=comp_stars,
        target_result=target_result,
        aperture_radius=aperture_radius,
        output_path=output_dir / f"{stem}_starmap.png",
        title=f"{star_name} – star map",
    )
    return paths
