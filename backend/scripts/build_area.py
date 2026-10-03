"""Build the SOLWEIG input maps for a study area from open data.

Produces backend/data/area/{dsm,dem,cdsm,landcover}.tif (1 m, EPSG:7856) + buildings.geojson.

Inputs
  --bbox      west south east north, in lon/lat (default: USYD Camperdown + surrounds, with buffer)
  --dem       ground elevation GeoTIFF in any CRS (NSW ELVIS 1 m DEM is best). Optional: if omitted, the
              Copernicus GLO-30 tile for Sydney is read straight from its public AWS bucket
  --canopy    tree canopy raster in any CRS, non-zero = canopy (e.g. Greater Sydney Tree Canopy 2024/25 tile)
  --osm-json  optional cached Overpass response; otherwise it's downloaded from the Overpass API

Buildings, roads, parks and water come from OpenStreetMap (ODbL, attribute "© OpenStreetMap contributors").
Building height: OSM `height` tag, else `building:levels` x 3.2 m, else --default-height.
Tree height: canopy pixels get --tree-height (no per-tree heights in the canopy layer), tapered at crown edges.

Licence note: the Greater Sydney canopy layer is CC BY-NC-ND 4.0. Don't commit the generated cdsm.tif
(data/ is git-ignored); commit this script instead so anyone can rebuild it.

Example:
  python scripts/build_area.py                                   # OSM buildings + GLO-30 ground, no trees
  python scripts/build_area.py --canopy data/raw/canopy_SYDNEY.tif
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import rasterio
import requests
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import reproject
from shapely.geometry import LineString, Polygon, mapping
from shapely.ops import transform as shp_transform

CRS = "EPSG:7856"
OUT = Path(__file__).resolve().parents[1] / "data" / "area"
USYD_BBOX = (151.1830, -33.8912, 151.1920, -33.8838)  # ~830 x 820 m: campus + edge of Victoria Park + Parramatta/City Rd

GLO30 = ("/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/"
         "Copernicus_DSM_COG_10_S34_00_E151_00_DEM/Copernicus_DSM_COG_10_S34_00_E151_00_DEM.tif")

ROAD_WIDTH = {  # metres, total carriageway width; (code: 1 asphalt, 0 paving)
    "motorway": (20, 1), "trunk": (16, 1), "primary": (14, 1), "secondary": (12, 1), "tertiary": (10, 1),
    "residential": (8, 1), "unclassified": (8, 1), "service": (5, 1), "living_street": (6, 1),
    "pedestrian": (6, 0), "footway": (3, 0), "path": (2.5, 0), "cycleway": (2.5, 1),
}
GRASS = {("leisure", "park"), ("leisure", "pitch"), ("leisure", "garden"), ("leisure", "playground"),
         ("landuse", "grass"), ("landuse", "recreation_ground"), ("landuse", "meadow"), ("natural", "grassland")}


OVERPASS_URLS = ["https://overpass-api.de/api/interpreter",
                 "https://overpass.kumi.systems/api/interpreter",
                 "https://overpass.private.coffee/api/interpreter"]


def overpass(bbox):
    w, s, e, n = bbox
    b = f"{s},{w},{n},{e}"
    q = f"""[out:json][timeout:90];
    (way["building"]({b}); relation["building"]({b});
     way["highway"]({b});
     way["leisure"~"park|pitch|garden|playground"]({b}); relation["leisure"~"park|pitch|garden"]({b});
     way["landuse"~"grass|recreation_ground|meadow"]({b}); way["natural"~"water|grassland"]({b});
     relation["natural"="water"]({b}););
    out geom;"""
    # Overpass rejects requests with no proper User-Agent (HTTP 406), and the
    # main server is often busy, so identify ourselves and try mirrors in turn.
    headers = {"User-Agent": "CoolBlocks-hackathon/1.0 (Climate Hack-tion; USYD heat map)",
               "Accept": "application/json, */*"}
    errors = []
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": q}, headers=headers, timeout=120)
            r.raise_for_status()
            data = r.json()
            print(f"OSM data from {url}")
            return data
        except Exception as ex:
            print(f"  {url} failed: {ex}")
            errors.append(f"{url}: {ex}")
    raise SystemExit("All Overpass servers failed. Download the OSM data by hand "
                     "(see backend/data/README.md) and pass it with --osm-json.\n" + "\n".join(errors))


def _ring(geom):
    return [(p["lon"], p["lat"]) for p in geom]


def osm_features(data):
    """Yield (tags, shapely geometry in lon/lat) for ways and multipolygon relations."""
    for el in data["elements"]:
        tags = el.get("tags", {})
        if el["type"] == "way" and "geometry" in el:
            pts = _ring(el["geometry"])
            if len(pts) >= 4 and pts[0] == pts[-1] and "highway" not in tags:
                yield tags, Polygon(pts)
            elif len(pts) >= 2:
                yield tags, LineString(pts)
        elif el["type"] == "relation":
            for m in el.get("members", []):
                pts = _ring(m.get("geometry", []))
                if m.get("role") == "outer" and len(pts) >= 4 and pts[0] == pts[-1]:
                    yield tags, Polygon(pts)


def building_height(tags, default):
    for key in ("height", "building:height"):
        if key in tags:
            m = re.match(r"[\d.]+", tags[key])
            if m:
                return float(m.group())
    if "building:levels" in tags:
        m = re.match(r"[\d.]+", tags["building:levels"])
        if m:
            return float(m.group()) * 3.2
    return default


def grid_for(bbox):
    to_m = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
    xs, ys = to_m.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
    x0, x1, y0, y1 = np.floor(min(xs)), np.ceil(max(xs)), np.floor(min(ys)), np.ceil(max(ys))
    width, height = int(x1 - x0), int(y1 - y0)
    return from_origin(x0, y1, 1.0, 1.0), (height, width)


def warp_to_grid(path, transform, shape, resampling):
    out = np.full(shape, np.nan, np.float32)
    with rasterio.open(path) as src:
        reproject(rasterio.band(src, 1), out, src_nodata=src.nodata, dst_transform=transform, dst_crs=CRS,
                  dst_nodata=np.nan, resampling=resampling)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bbox", type=float, nargs=4, default=USYD_BBOX, metavar=("W", "S", "E", "N"))
    ap.add_argument("--dem", default=GLO30)
    ap.add_argument("--canopy")
    ap.add_argument("--osm-json")
    ap.add_argument("--default-height", type=float, default=10.0)
    ap.add_argument("--tree-height", type=float, default=10.0)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    transform, shape = grid_for(a.bbox)
    print(f"grid {shape[1]} x {shape[0]} m")

    # ground
    dem = warp_to_grid(a.dem, transform, shape, Resampling.bilinear)
    if np.isnan(dem).all():
        raise SystemExit("DEM doesn't cover the bbox")
    dem = np.where(np.isnan(dem), np.nanmedian(dem), dem).astype(np.float32)

    # OSM
    if a.osm_json and Path(a.osm_json).exists():
        data = json.loads(Path(a.osm_json).read_text(encoding="utf-8"))
    else:
        data = overpass(a.bbox)
        if a.osm_json:
            Path(a.osm_json).parent.mkdir(parents=True, exist_ok=True)
            Path(a.osm_json).write_text(json.dumps(data), encoding="utf-8")
    to_m = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform
    buildings, grass, water, asphalt, paving = [], [], [], [], []
    for tags, g in osm_features(data):
        gm = shp_transform(to_m, g)
        if "building" in tags and gm.geom_type == "Polygon":
            buildings.append((gm, building_height(tags, a.default_height), g))
        elif "highway" in tags and gm.geom_type == "LineString":
            width, code = ROAD_WIDTH.get(tags["highway"], (0, 1))
            if width:
                (asphalt if code == 1 else paving).append(gm.buffer(width / 2, cap_style=2))
        elif any((k, tags.get(k)) in GRASS for k in ("leisure", "landuse", "natural")) and gm.geom_type == "Polygon":
            grass.append(gm)
        elif (tags.get("natural") == "water" or "water" in tags) and gm.geom_type == "Polygon":
            water.append(gm)
    print(f"OSM: {len(buildings)} buildings, {len(asphalt)} roads, {len(paving)} paths, {len(grass)} grass, {len(water)} water")

    def burn(geoms, value, out_arr):
        if geoms:
            rasterize([(mapping(g), value) for g in geoms], out=out_arr, transform=transform)

    lc = np.zeros(shape, np.int32)  # default: paving
    burn(grass, 5, lc)
    burn(paving, 0, lc)
    burn(asphalt, 1, lc)
    burn(water, 7, lc)
    bh = np.zeros(shape, np.float32)
    for gm, h, _ in sorted(buildings, key=lambda b: b[1]):  # taller last so they win overlaps
        burn([gm], h, bh)
    lc[bh > 0] = 2
    dsm = (dem + bh).astype(np.float32)

    # trees
    cdsm = np.zeros(shape, np.float32)
    if not a.canopy:
        print("no --canopy given: existing trees left out (add the Greater Sydney canopy tile to include them)")
    if a.canopy:
        frac = warp_to_grid(a.canopy, transform, shape, Resampling.average)  # mean of 0/1 mask = fraction
        tree = (np.nan_to_num(frac) >= 0.5) & (lc != 2)
        # taper crowns: cells 1-3 m from the crown edge are lower than the centre
        depth = tree.astype(np.int32)
        cur = tree.copy()
        for _ in range(3):
            cur = cur & np.roll(cur, 1, 0) & np.roll(cur, -1, 0) & np.roll(cur, 1, 1) & np.roll(cur, -1, 1)
            depth += cur
        cdsm = np.where(tree, a.tree_height * (0.7 + 0.1 * (depth - 1)), 0).astype(np.float32)
        print(f"canopy cover {tree.mean():.0%}")

    profile = dict(driver="GTiff", height=shape[0], width=shape[1], count=1, crs=CRS, transform=transform, compress="deflate")
    for name, arr in [("dsm", dsm), ("dem", dem), ("cdsm", cdsm), ("landcover", lc)]:
        with rasterio.open(out / f"{name}.tif", "w", dtype=arr.dtype, **profile) as dst:
            dst.write(arr, 1)

    feats = [{"type": "Feature", "properties": {"height": round(h, 1)}, "geometry": mapping(g)} for _, h, g in buildings]
    (out / "buildings.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
