# Plan: Ensemble Fit Plot + Target Cutout in Dashboard

## Constraint: minimum changes to the photometry pipeline

All photometry code (detection, calibration, photometry, pipeline) is working well so maintain its functionallity.
We only save more of the data that is *already computed* into the existing `extra` JSON column.

---

## 1 – `cli.py` `_save_results`: populate `extra` on each ObservationORM

`SingleFileResult` already holds everything needed:
- `fr.ensemble_comps` → list[ApertureResult] with `auid`, `instrumental_magnitude`
- `fr.ensemble_stars` → list[MatchedStar]  with `auid`, `published_magnitude`
- `fr.target_aperture_result` → ApertureResult with `x`, `y`, `instrumental_magnitude`

Build an `extra` dict per successful observation and pass it to `save_observation`:

```python
extra: dict = {}
if fr.target_aperture_result:
    extra["target_x"] = fr.target_aperture_result.x
    extra["target_y"] = fr.target_aperture_result.y
    extra["target_instrumental_mag"] = fr.target_aperture_result.instrumental_magnitude
if fr.ensemble_comps and fr.ensemble_stars:
    extra["ensemble_points"] = [
        {"auid": ap.auid,
         "instrumental_mag": ap.instrumental_magnitude,
         "published_mag": ms.published_magnitude}
        for ap, ms in zip(fr.ensemble_comps, fr.ensemble_stars)
        if ap.instrumental_magnitude is not None
    ]
```

No schema migration needed – `extra` is already a JSON column on ObservationORM.

---

## 2 – `web/app.py`: two additions

### 2a  Expose `extra` in ObservationSummary
Add `extra: Optional[dict] = None` to the Pydantic model.
`from_attributes=True` means SQLAlchemy maps it automatically.

### 2b  New endpoint: `GET /api/observations/{obs_id}/cutout`
- Loads FITS file from `obs.file_name` (already an absolute path)
- Gets `target_x`, `target_y` from `obs.extra`
- Extracts 120 × 120 pixel crop centred on target
- Applies `ZScaleInterval` + `SqrtStretch` (same astropy.visualization already used in plots.py)
- Saves to BytesIO as PNG and returns `Response(content=..., media_type="image/png")`
- Returns HTTP 404 if FITS missing or `extra` lacks coords

---

## 3 – `web/static/index.html`: visualisation panel

### Chart.js (loaded from CDN, no build step)
```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
```

### Layout addition below the obs table
```
┌─ detail panel ─────────────────────────────────────────────┐
│  [header: EE Cep • V • chart X41914BS]                      │
│  [toolbar: Download AAVSO CSV | 5 obs, 0 rejected]          │
│  [obs table – rows are now clickable, row highlights]        │
├─ viz panel (hidden until a row is clicked) ─────────────────│
│  EE_Cep_001.fits                                            │
│  ┌──────────────────────────┐  ┌──────────────────────┐    │
│  │  Ensemble fit            │  │  Target star cutout  │    │
│  │  Chart.js scatter+line   │  │  <img> from /cutout  │    │
│  │  ● comp stars (blue)     │  │  120×120 px, ZScale  │    │
│  │  ★ target (red)          │  │  stretched           │    │
│  │  -- linear fit (dashed)  │  │                      │    │
│  └──────────────────────────┘  └──────────────────────┘    │
└────────────────────────────────────────────────────────────┘
```

### Ensemble fit plot (Chart.js scatter)
Data comes from `obs.extra.ensemble_points` + `obs.extra.target_instrumental_mag`.
Fit line drawn from `obs.fit_slope` / `obs.fit_intercept` (already in ObservationSummary).
- Dataset 1: comp stars – `{x: instrumental_mag, y: published_mag}` – blue circles
- Dataset 2: target – `{x: target_instrumental_mag, y: obs.magnitude}` – red triangle
- Dataset 3: fit line – two points computed from slope/intercept over the x range – grey dashed

### Target cutout
```html
<img id="cutout-img" src="/api/observations/{id}/cutout" />
```
Shows "No cutout available" placeholder if 404.

---

## Files changed

| File | Change |
|------|--------|
| `hikari/cli.py` | Populate `extra` dict in `_save_results` (≈ 10 lines) |
| `hikari/web/app.py` | Add `extra` to schema + new `/cutout` endpoint |
| `hikari/web/static/index.html` | Chart.js + clickable rows + viz panel |

## Files NOT changed
`detection.py`, `calibration.py`, `photometry.py`, `pipeline.py`,
`catalog.py`, `models.py`, `database.py`, `aavso.py`, `config.py`
