"""CoolBlocks backend: a FastAPI server around SOLWEIG.

Run (from backend/):
    pip install -r requirements.txt
    uvicorn server:app --reload
    open http://127.0.0.1:8000/docs   (interactive API page)

Area data lives in backend/data/area/ (made by scripts/build_area.py), all on the same 1 m grid in EPSG:7856:
    dsm.tif         ground + buildings, metres above sea level
    dem.tif         bare ground, metres above sea level
    cdsm.tif        tree heights ABOVE GROUND (0 = no tree)
    landcover.tif   0 paving/cobble, 1 asphalt, 2 roof, 5 grass, 6 bare soil, 7 water
    buildings.geojson  footprints with a "height" property (lon/lat), for the 3D view
If dsm.tif is missing, a synthetic demo block centred on USYD is used so the frontend can be built in parallel.

Simulation flow (/simulate):
    1. cut a patch around the edits (+ buffer so nearby buildings/trees still shade it)
    2. apply the edits to copies of the patch maps (trees, surfaces, buildings)
    3. run SOLWEIG before and after for the requested hours
    4. return per-hour stats + PNG overlays (after-UTCI, change, shadow) with lon/lat bounds
"""
from __future__ import annotations

import base64
import copy
import functools
import io
import json
import logging
import os
import threading
import shutil
import tempfile
import uuid
from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import Literal

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
import solweig
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pyproj import Transformer
from rasterio.features import rasterize, shapes
from rasterio.transform import from_origin
from rasterio.warp import transform_geom

import live_weather

logging.getLogger("solweig").setLevel(logging.WARNING)
log = logging.getLogger("coolblocks")

CRS = "EPSG:7856"  # GDA2020 / MGA zone 56, metres
HERE = Path(__file__).parent
DATA = Path(os.environ.get("COOLBLOCKS_AREA", HERE / "data" / "area"))
BUFFER_M = 100  # context around edits so surrounding buildings/trees still shade the patch
TREE_SIZES = {"small": (6.0, 3.0), "medium": (10.0, 4.0), "large": (15.0, 6.0)}  # (height m, crown radius m)
SURFACES = {"paving": 0, "asphalt": 1, "grass": 5, "soil": 6, "water": 7, "cool_asphalt": 3}
DEFAULT_HOURS = list(range(9, 19))
UTCI_RANGE = (25, 50)  # colour scale for heat images, degC
CHANGE_RANGE = 8  # +/- degC for change images

# ------------------------------------------------------------------ area


class Area:
    def __init__(self):
        if (DATA / "dsm.tif").exists():
            with rasterio.open(DATA / "dsm.tif") as r:
                self.dsm, self.transform = r.read(1).astype(np.float32), r.transform
            self.dem = _read(DATA / "dem.tif", np.float32) if (DATA / "dem.tif").exists() else None
            self.lc = _read(DATA / "landcover.tif", np.int32)
            self.cdsm = self._load_trees()
            self.source = "data/area"
        else:
            self._synthetic()
            self.source = "synthetic demo"
        if self.dem is None:  # rough ground: DSM where not a building
            self.dem = np.where(self.lc == 2, np.nan, self.dsm)
            self.dem = np.where(np.isnan(self.dem), np.nanmedian(self.dem), self.dem).astype(np.float32)
        self.h, self.w = self.dsm.shape
        self.to_lonlat = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)

    def _load_trees(self):
        """Tree heights. cdsm.tif isn't committed (canopy licence forbids sharing derived data), so it is rebuilt
        from the committed canopy cut-out data/raw/canopy_usyd.tif the first time the server starts."""
        path = DATA / "cdsm.tif"
        if path.exists():
            return _read(path, np.float32)
        canopy = DATA.parent / "raw" / "canopy_usyd.tif"
        if not canopy.exists():
            log.warning("no cdsm.tif and no %s: existing trees left out", canopy)
            return np.zeros(self.dsm.shape, np.float32)
        import sys
        sys.path.insert(0, str(HERE / "scripts"))
        from build_area import canopy_to_cdsm

        cdsm = canopy_to_cdsm(str(canopy), self.transform, self.dsm.shape, self.lc)
        with rasterio.open(DATA / "dsm.tif") as r:
            profile = r.profile
        with rasterio.open(path, "w", **(profile | {"dtype": "float32", "nodata": None})) as dst:
            dst.write(cdsm, 1)
        log.warning("built %s from %s (canopy cover %.0f%%)", path, canopy, 100 * (cdsm > 0).mean())
        return cdsm

    def _synthetic(self):
        """400 x 400 m block centred on USYD: buildings, an E-W road, a park with trees."""
        x0, y0 = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(151.187, -33.888)
        n = 400
        self.transform = from_origin(round(x0) - n / 2, round(y0) + n / 2, 1.0, 1.0)
        rng = np.random.default_rng(1)
        self.dem = np.full((n, n), 20.0, np.float32)
        self.dsm = self.dem.copy()
        self.lc = np.zeros((n, n), np.int32)  # paving
        self.lc[185:220, :] = 1  # road (asphalt)
        for _ in range(70):
            r, c = rng.integers(0, n - 40, 2)
            hh, ww = rng.integers(15, 40, 2)
            if r + hh > 180 and r < 225 or (r + hh > 250 and r < 360 and c + ww > 40 and c < 160):
                continue  # keep road and park clear
            self.dsm[r : r + hh, c : c + ww] = 20 + rng.uniform(8, 30)
            self.lc[r : r + hh, c : c + ww] = 2
        self.lc[260:350, 50:150] = 5  # park
        self.cdsm = np.zeros((n, n), np.float32)
        for r, c in rng.integers((262, 52), (348, 148), (25, 2)):
            _stamp_tree(self.cdsm, r, c, 10.0, 4.0)

    def bounds_lonlat(self, r0=0, r1=None, c0=0, c1=None):
        r1 = self.h if r1 is None else r1
        c1 = self.w if c1 is None else c1
        west, north = self.transform * (c0, r0)
        east, south = self.transform * (c1, r1)
        (lon_w, lon_e), (lat_s, lat_n) = self.to_lonlat.transform([west, east], [south, north])
        return [[lon_w, lat_s], [lon_e, lat_n]]  # [[west, south], [east, north]]


def _read(path, dtype):
    with rasterio.open(path) as r:
        return r.read(1).astype(dtype)


def _stamp_tree(cdsm, r, c, height, radius):
    """Round crown, a bit lower at the edge; never lowers an existing taller tree."""
    rr, cc = np.ogrid[: cdsm.shape[0], : cdsm.shape[1]]
    d = np.hypot(rr - r, cc - c) / radius
    crown = np.where(d <= 1, height * (1 - 0.3 * d**2), 0).astype(np.float32)
    np.maximum(cdsm, crown, out=cdsm)


def _materials_with_cool_asphalt(path: Path) -> Path:
    """Default materials + class 3 'Cool_asphalt' = dark asphalt with albedo 0.45."""
    m = json.loads((Path(solweig.__file__).parent / "data" / "default_materials.json").read_text(encoding="utf-8"))
    m["Names"]["Value"]["3"] = "Cool_asphalt"
    m["Code"]["Value"]["Cool_asphalt"] = 3
    for key, block in m.items():
        vals = block.get("Value") if isinstance(block, dict) else None
        if key != "Code" and isinstance(vals, dict) and "Dark_asphalt" in vals:
            vals["Cool_asphalt"] = copy.deepcopy(vals["Dark_asphalt"])
    m["Albedo"]["Effective"]["Value"]["Cool_asphalt"] = 0.45
    path.write_text(json.dumps(m))
    return path


AREA = Area()
WORK = Path(tempfile.mkdtemp(prefix="coolblocks_"))
MATERIALS = solweig.load_materials(_materials_with_cool_asphalt(WORK / "materials.json"))

# ------------------------------------------------------------------ weather


def get_weather(day: str | None, hours: list[int]):
    """Live/past weather from Open-Meteo, falling back to a built-in hot day if offline."""
    try:
        rows, offset = live_weather.fetch_hourly(day)
        ws = live_weather.to_solweig(rows, hours=hours, date=day)
        if ws:
            return ws, offset, "open-meteo"
    except Exception as e:  # noqa: BLE001 - the demo must keep working offline
        log.warning("weather fetch failed (%s); using fallback hot day", e)
    d = datetime.fromisoformat(day) if day else datetime(2026, 1, 15)
    ws = []
    for h in hours:
        ta = 26 + 12 * max(0.0, np.sin(np.pi * (h - 7) / 14))
        sun = max(0.0, 1000 * np.sin(np.pi * (h - 6.5) / 14))
        ws.append(solweig.Weather(datetime=d.replace(hour=h), ta=float(ta), rh=35.0, global_rad=float(sun), ws=2.0))
    offset = live_weather.TZ.utcoffset(d.replace(hour=12)).total_seconds() / 3600
    return ws, offset, "fallback hot day"


# ------------------------------------------------------------------ SOLWEIG helpers


# Several visitors can ask at once. SOLWEIG runs one at a time (it shares the GPU and writes temporary
# files), and each run gets its own folder so runs never trip over each other's files.
_SOLWEIG_LOCK = threading.Lock()
_BASELINE_LOCK = threading.Lock()


def _run(name, dsm, dem, cdsm, lc, weather, location):
    with _SOLWEIG_LOCK:
        out = WORK / f"{name}_{uuid.uuid4().hex[:8]}"
        try:
            return _run_in(out, dsm, dem, cdsm, lc, weather, location)
        finally:
            shutil.rmtree(out, ignore_errors=True)


def _run_in(out, dsm, dem, cdsm, lc, weather, location):
    # SOLWEIG modifies input arrays in place (e.g. cdsm), so always hand it copies
    dsm, dem, cdsm, lc = (None if a is None else a.copy() for a in (dsm, dem, cdsm, lc))
    surface = solweig.SurfaceData.prepare(
        dsm=dsm, dem=dem, cdsm=cdsm, land_cover=lc, pixel_size=1.0, working_dir=str(out / "cache"), force_recompute=True
    )
    solweig.calculate(
        surface, weather=weather, location=location, output_dir=str(out), outputs=["utci", "shadow"],
        conifer=True,  # IMPORTANT for Sydney: default leaf season is Northern Hemisphere
        materials=MATERIALS,
    )
    res = {}
    for w in weather:
        stamp = f"{w.datetime:%Y%m%d_%H%M}"
        res[w.datetime.hour] = {
            "utci": _read(out / "utci" / f"utci_{stamp}.tif", np.float32),
            "shadow": _read(out / "shadow" / f"shadow_{stamp}.tif", np.float32),
        }
    return res


def _png(arr, cmap, vmin, vmax, alpha=None):
    rgba = plt.get_cmap(cmap)(np.clip((arr - vmin) / (vmax - vmin), 0, 1))
    a = np.where(np.isnan(arr), 0.0, 1.0) if alpha is None else alpha
    rgba[..., 3] = a
    buf = io.BytesIO()
    plt.imsave(buf, (rgba * 255).astype(np.uint8), format="png")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _shadow_png(shadow, roofs):
    """Dark, semi-transparent where shaded (shadow: 1 = sunlit, 0 = shade); transparent on roofs."""
    a = np.where(roofs, 0.0, (1 - np.nan_to_num(shadow, nan=1.0)) * 0.55)
    return _png(np.zeros_like(shadow), "gray", 0, 1, alpha=a)


def _location(offset):
    return solweig.Location(latitude=live_weather.LAT, longitude=live_weather.LON, utc_offset=offset)


# ------------------------------------------------------------------ API models


class Edit(BaseModel):
    type: Literal["add_tree", "remove_trees", "surface", "building"]
    geometry: dict = Field(..., description="GeoJSON in lon/lat. add_tree: Point; others: Polygon")
    size: Literal["small", "medium", "large"] = "medium"
    surface: Literal["paving", "asphalt", "grass", "soil", "water", "cool_asphalt"] | None = None
    height: float | None = Field(None, ge=0, le=250, description="building: new height in m above ground; 0 = demolish")


class SimRequest(BaseModel):
    edits: list[Edit]
    date: str | None = Field(None, description="YYYY-MM-DD; omit for live/today")
    hours: list[int] = Field(default_factory=lambda: list(DEFAULT_HOURS))
    images: bool = True


# ------------------------------------------------------------------ edits


def _to_area_crs(geometry):
    return transform_geom("EPSG:4326", CRS, geometry)


def _all_xy(g):
    t = g["type"]
    if t == "Point":
        return [g["coordinates"][:2]]
    if t == "Polygon":
        return [p for ring in g["coordinates"] for p in ring]
    if t == "MultiPolygon":
        return [p for poly in g["coordinates"] for ring in poly for p in ring]
    raise HTTPException(400, f"unsupported geometry {t}")


def _edit_window(edits):
    """Pixel window covering all edits plus a buffer that grows with building height."""
    xy = np.array([p for e in edits for p in _all_xy(_to_area_crs(e.geometry))], dtype=float)
    cols, rows = ~AREA.transform * (xy[:, 0], xy[:, 1])
    tallest = max([e.height or 0 for e in edits if e.type == "building"] + [0])
    pad = max(BUFFER_M, 2 * tallest) + max(r for _, r in TREE_SIZES.values())
    r0, r1 = int(max(0, rows.min() - pad)), int(min(AREA.h, rows.max() + pad + 1))
    c0, c1 = int(max(0, cols.min() - pad)), int(min(AREA.w, cols.max() + pad + 1))
    if r1 - r0 < 10 or c1 - c0 < 10:
        raise HTTPException(400, "edits are outside the study area")
    return r0, r1, c0, c1


def _apply_edits(edits, dsm, dem, cdsm, lc, transform):
    """Apply edits in place to the patch arrays; return a mask of edited cells."""
    touched = np.zeros(cdsm.shape, bool)
    for e in edits:
        g = _to_area_crs(e.geometry)
        if e.type == "add_tree":
            if g["type"] != "Point":
                raise HTTPException(400, "add_tree needs a Point")
            c, r = ~transform * tuple(g["coordinates"][:2])
            height, radius = TREE_SIZES[e.size]
            before = cdsm.copy()
            _stamp_tree(cdsm, r, c, height, radius)
            cdsm[lc == 2] = before[lc == 2]  # no trees on roofs
            touched |= cdsm != before
            continue
        mask = rasterize([(g, 1)], out_shape=cdsm.shape, transform=transform, fill=0, dtype="uint8").astype(bool)
        if e.type == "remove_trees":
            cdsm[mask] = 0
        elif e.type == "surface":
            if e.surface is None:
                raise HTTPException(400, "surface edit needs 'surface'")
            mask &= lc != 2  # never repave roofs
            lc[mask] = SURFACES[e.surface]
        elif e.type == "building":
            if e.height is None:
                raise HTTPException(400, "building edit needs 'height' (0 = demolish)")
            if e.height > 0:
                dsm[mask] = dem[mask] + e.height
                lc[mask] = 2
                cdsm[mask] = 0
            else:  # demolish: ground becomes paving
                dsm[mask] = dem[mask]
                lc[mask & (lc == 2)] = 0
        touched |= mask
    return touched


# ------------------------------------------------------------------ endpoints

app = FastAPI(title="CoolBlocks API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _warm_up():
    """On a hosted server, work out the heat maps for the example day and today in the background,
    so the first visitor doesn't wait. Set COOLBLOCKS_WARMUP=1 to turn this on (the Dockerfile does)."""
    if os.environ.get("COOLBLOCKS_WARMUP") != "1":
        return

    def run():
        for day in ("2025-12-19", None):
            try:
                _baseline_once(day)
            except Exception as e:  # never stop the server over a warm-up
                print(f"coolblocks: warm-up for {day or 'today'} failed ({e})")

    threading.Thread(target=run, daemon=True).start()


@app.get("/api/area")
def area():
    return {"source": AREA.source, "size_m": [AREA.w, AREA.h], "bounds": AREA.bounds_lonlat(),
            "tree_sizes": TREE_SIZES, "surfaces": list(SURFACES), "hours": DEFAULT_HOURS,
            "utci_range": UTCI_RANGE, "change_range": CHANGE_RANGE}


@functools.lru_cache(maxsize=1)
def _vectors():
    """Building footprints (with height) and tree crowns as lon/lat GeoJSON for the 3D views."""
    if (DATA / "buildings.geojson").exists():
        buildings = json.loads((DATA / "buildings.geojson").read_text(encoding="utf-8"))
    else:
        height = (AREA.dsm - AREA.dem).round()
        feats = [{"type": "Feature", "properties": {"height": float(v)}, "geometry": transform_geom(CRS, "EPSG:4326", g)}
                 for g, v in shapes(height.astype(np.float32), mask=(AREA.lc == 2) & (height > 2), transform=AREA.transform)]
        buildings = {"type": "FeatureCollection", "features": feats}
    for i, f in enumerate(buildings["features"]):
        f.setdefault("properties", {})["idx"] = i  # lets the frontend hide a demolished building
    return buildings, _tree_points(AREA.cdsm, AREA.transform)


def _tree_points(cdsm, transform, min_height=3.0, spacing=5):
    """Individual trees as points (top of each crown), so the frontend can draw a tree shape for each.

    A tree top is a cell that is the tallest within `spacing` cells. Crown radius is guessed from height."""
    h = np.nan_to_num(cdsm.astype(np.float32))
    k = 2 * spacing + 1
    win = np.lib.stride_tricks.sliding_window_view(np.pad(h, spacing, constant_values=0), (k, k))
    local_max = win.max(axis=(2, 3))
    rows, cols = np.nonzero((h >= min_height) & (h >= local_max))
    taken = np.zeros_like(h, dtype=bool)  # flat-topped crowns give ties: keep one per spacing window
    to_ll = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform
    feats = []
    for r, c in zip(rows, cols):
        if taken[r, c]:
            continue
        taken[max(0, r - spacing):r + spacing + 1, max(0, c - spacing):c + spacing + 1] = True
        x, y = transform * (c + 0.5, r + 0.5)
        lon, lat = to_ll(x, y)
        height = float(round(h[r, c], 1))
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]},
                      "properties": {"height": height, "radius": round(float(np.clip(0.35 * height, 2, 7)), 1)}})
    return {"type": "FeatureCollection", "features": feats}


@app.get("/api/buildings")
def buildings():
    return _vectors()[0]


@app.get("/api/trees")
def trees():
    return _vectors()[1]


@app.get("/api/weather")
def weather(date: str | None = None):
    ws, offset, src = get_weather(date, list(range(24)))
    return {"source": src, "utc_offset": offset,
            "hours": [{"hour": w.datetime.hour, "air_temp": w.ta, "humidity": w.rh, "sun_wm2": w.global_rad} for w in ws]}


_BASE: dict = {}  # day -> {hour: {"utci", "shadow", "ta"}} full-area arrays, for /simulate shade redraws and /point
_SIMS: dict = {}  # sim_id -> patch results of a recent /simulate, for /point after edits


@functools.lru_cache(maxsize=4)
def _baseline(day: str | None, hours: tuple[int, ...]):
    ws, offset, src = get_weather(day, list(hours))
    res = _run(f"baseline_{day or 'live'}", AREA.dsm, AREA.dem, AREA.cdsm, AREA.lc, ws, _location(offset))
    roofs = AREA.lc == 2
    out = []
    _BASE[day] = {w.datetime.hour: {**res[w.datetime.hour], "ta": w.ta} for w in ws}
    while len(_BASE) > 4:  # same limit as the cache above
        _BASE.pop(next(iter(_BASE)))
    for w in ws:
        h = w.datetime.hour
        u = res[h]["utci"]
        out.append({"hour": h, "air_temp": w.ta,
                    "utci_ground_mean": float(np.nanmean(u[~roofs])),
                    "utci_png": _png(np.where(roofs, np.nan, u), "inferno", *UTCI_RANGE),
                    "shadow_png": _shadow_png(res[h]["shadow"], roofs)})
    return {"weather_source": src, "bounds": AREA.bounds_lonlat(), "hours": out}


@app.get("/api/baseline")
def baseline(date: str | None = None):
    """Heat + shadow images for the whole area, before any edits. First call takes a while; then cached."""
    if date:
        Date.fromisoformat(date)
    return _baseline_once(date)


def _baseline_once(day: str | None):
    """Two people opening the app together wait for one heat map instead of both computing it."""
    with _BASELINE_LOCK:
        return _baseline(day, tuple(DEFAULT_HOURS))


@app.post("/api/simulate")
def simulate(req: SimRequest):
    if not req.edits:
        raise HTTPException(400, "no edits")
    if req.date:
        Date.fromisoformat(req.date)
    r0, r1, c0, c1 = _edit_window(req.edits)
    t = AREA.transform * rasterio.Affine.translation(c0, r0)
    win = (slice(r0, r1), slice(c0, c1))
    dem = AREA.dem[win].copy()
    dsm0, cdsm0, lc0 = AREA.dsm[win].copy(), AREA.cdsm[win].copy(), AREA.lc[win].copy()
    dsm1, cdsm1, lc1 = dsm0.copy(), cdsm0.copy(), lc0.copy()
    edited = _apply_edits(req.edits, dsm1, dem, cdsm1, lc1, t)

    ws, offset, src = get_weather(req.date, sorted(set(req.hours)))
    before = _run("before", dsm0, dem, cdsm0, lc0, ws, _location(offset))
    after = _run("after", dsm1, dem, cdsm1, lc1, ws, _location(offset))

    roofs = lc1 == 2
    ground = ~roofs & ~(lc0 == 2)
    near = np.zeros_like(edited)  # edited cells + 30 m around them: where people feel the change
    if edited.any():
        rr, cc = np.nonzero(edited)
        near[max(0, rr.min() - 30): rr.max() + 31, max(0, cc.min() - 30): cc.max() + 31] = True
    zone = near & ground
    hours = []
    for w in ws:
        h = w.datetime.hour
        ub, ua = before[h]["utci"], after[h]["utci"]
        d = ua - ub
        item = {"hour": h, "air_temp": w.ta,
                "utci_before_mean": float(np.nanmean(ub[zone])), "utci_after_mean": float(np.nanmean(ua[zone])),
                "utci_change_mean": float(np.nanmean(d[zone])),
                "utci_change_edited_cells": float(np.nanmean(d[edited & ground])) if (edited & ground).any() else 0.0,
                "utci_change_min": float(np.nanmin(d[zone])), "utci_change_max": float(np.nanmax(d[zone]))}
        if req.images:
            # only changed cells are drawn, so the full-area baseline shows through everywhere else
            unchanged = roofs | (np.abs(d) < 0.2)
            item["utci_png"] = _png(np.where(unchanged, np.nan, ua), "inferno", *UTCI_RANGE)
            item["change_png"] = _png(np.where(unchanged, np.nan, d), "RdBu_r", -CHANGE_RANGE, CHANGE_RANGE)
            base_shadow = _BASE.get(req.date, {}).get(h, {}).get("shadow")
            if base_shadow is not None:
                # whole-area shade map with the edited patch swapped in, so removed trees lose their shadow too
                full, full_roofs = base_shadow.copy(), AREA.lc == 2
                full[win], full_roofs[win] = after[h]["shadow"], roofs
                item["shadow_full_png"] = _shadow_png(full, full_roofs)
            else:  # baseline not computed for this day: draw only the new shade on top
                newly_shaded = after[h]["shadow"] < before[h]["shadow"] - 0.1
                item["shadow_png"] = _shadow_png(np.where(newly_shaded, after[h]["shadow"], 1.0), roofs)
        hours.append(item)
    sim_id = uuid.uuid4().hex[:12]
    _SIMS[sim_id] = {"date": req.date, "r0": r0, "c0": c0, "lc": lc1,
                     "hours": {h: {"utci": after[h]["utci"], "shadow": after[h]["shadow"]} for h in after}}
    while len(_SIMS) > 8:
        _SIMS.pop(next(iter(_SIMS)))
    return {"sim_id": sim_id, "weather_source": src, "bounds": AREA.bounds_lonlat(r0, r1, c0, c1), "hours": hours}


@app.get("/api/example")
def example(n: int = 10, spacing: float = 7.0):
    """Points for a strip of street trees (for the "Try an example" button).

    Tries road cells near the middle of the area, follows the street's direction, snaps each tree to open
    ground (road or paving, no existing tree, no roof) within 4 m, and keeps the strip with the most trees."""
    open_ground = np.isin(AREA.lc, (0, 1)) & (np.nan_to_num(AREA.cdsm) < 1)
    road = (AREA.lc == 1) & open_ground
    rr, cc = np.nonzero(road)
    if len(rr) == 0:
        raise HTTPException(404, "no open road in this area")
    order = np.argsort((rr - AREA.h / 2) ** 2 + (cc - AREA.w / 2) ** 2)
    canopy = np.nan_to_num(AREA.cdsm) >= 1
    best, best_score = [], None
    for i in order[:1200:10]:  # 120 candidate starting points, nearest the middle first
        r0, c0 = rr[i], cc[i]
        near = ((rr - r0) ** 2 + (cc - c0) ** 2) < 40 ** 2
        if near.sum() < 20:
            continue
        direction = np.linalg.eigh(np.cov(np.stack([rr[near] - r0, cc[near] - c0])))[1][:, -1]
        pts = []
        for k in np.arange(n) - (n - 1) / 2:
            r, c = int(round(r0 + k * spacing * direction[0])), int(round(c0 + k * spacing * direction[1]))
            if not (0 <= r < AREA.h and 0 <= c < AREA.w):
                continue
            ra, ca = max(0, r - 4), max(0, c - 4)
            ys, xs = np.nonzero(open_ground[ra:r + 5, ca:c + 5])
            if len(ys):
                j = np.argmin((ys + ra - r) ** 2 + (xs + ca - c) ** 2)  # nearest open cell
                pts.append((ys[j] + ra, xs[j] + ca))
        # prefer full strips that sit away from existing trees, so the new shade shows up clearly
        crowd = np.mean([canopy[max(0, r - 10):r + 11, max(0, c - 10):c + 11].mean() for r, c in pts]) if pts else 1
        score = (len(pts), -crowd)
        if best_score is None or score > best_score:
            best, best_score = pts, score
    to_ll = AREA.to_lonlat.transform
    return {"points": [list(to_ll(*(AREA.transform * (c + 0.5, r + 0.5)))) for r, c in best], "date": "2025-12-19"}


SURFACE_NAMES = {0: "paving", 1: "dark asphalt", 2: "roof", 3: "cool asphalt", 5: "grass", 6: "bare soil", 7: "water"}


@app.get("/api/point")
def point(lon: float, lat: float, hour: int, date: str | None = None, sim_id: str | None = None):
    """'Feels like' temperature at one spot: before edits (from the baseline) and after (from a /simulate result)."""
    base = _BASE.get(date, {}).get(hour)
    if base is None:
        raise HTTPException(409, "heat map for this day isn't ready yet")
    x, y = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(lon, lat)
    c, r = ~AREA.transform * (x, y)
    r, c = int(r), int(c)
    if not (0 <= r < AREA.h and 0 <= c < AREA.w):
        raise HTTPException(404, "outside the study area")

    def num(v):
        return None if v is None or not np.isfinite(v) else round(float(v), 1)

    out = {"hour": hour, "air_temp": num(base["ta"]), "before": num(base["utci"][r, c]),
           "shade_before": bool(base["shadow"][r, c] < 0.5), "surface": SURFACE_NAMES.get(int(AREA.lc[r, c]), "other"),
           "surface_before": SURFACE_NAMES.get(int(AREA.lc[r, c]), "other"), "after": None, "shade_after": None}
    sim = _SIMS.get(sim_id) if sim_id else None
    if sim and sim["date"] == date and hour in sim["hours"]:
        a = sim["hours"][hour]
        pr, pc = r - sim["r0"], c - sim["c0"]
        if 0 <= pr < a["utci"].shape[0] and 0 <= pc < a["utci"].shape[1]:
            out.update(after=num(a["utci"][pr, pc]), shade_after=bool(a["shadow"][pr, pc] < 0.5),
                       surface=SURFACE_NAMES.get(int(sim["lc"][pr, pc]), "other"))
        else:  # outside the edited patch: nothing changed there
            out.update(after=out["before"], shade_after=out["shade_before"])
    return out


# Serve the frontend from the same server (http://127.0.0.1:8000/)
FRONTEND = HERE.parent / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
