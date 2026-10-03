"""Cut the USYD study area out of the big Greater Sydney canopy file, so you can keep a small copy.

Usage (from backend/):
    python scripts/clip_canopy.py "data/raw/GSR_canopy_download.zip"
    -> writes data/raw/canopy_usyd.tif (a few MB), which build_area.py can use with --canopy

Accepts the downloaded .zip, an unzipped folder, or a .tif. Keeps the original pixels and projection.
Licence: the canopy layer is CC BY-NC-ND 4.0. The unchanged cut-out may be shared non-commercially with credit
(see data/LICENSES.md); tree heights derived from it (cdsm.tif) must not be shared.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

from build_area import USYD_BBOX, canopy_sources

PAD_DEG = 0.002  # ~200 m extra around the study area


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("canopy", help="canopy .zip, folder or .tif")
    ap.add_argument("--bbox", type=float, nargs=4, default=USYD_BBOX, metavar=("W", "S", "E", "N"))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "data" / "raw" / "canopy_usyd.tif"))
    a = ap.parse_args()
    w, s, e, n = a.bbox
    for f in canopy_sources(a.canopy):
        with rasterio.open(f) as src:
            l, b, r, t = transform_bounds("EPSG:4326", src.crs, w - PAD_DEG, s - PAD_DEG, e + PAD_DEG, n + PAD_DEG)
            win = from_bounds(l, b, r, t, src.transform).round_offsets().round_lengths()
            col0, row0 = max(0, int(win.col_off)), max(0, int(win.row_off))
            col1, row1 = min(src.width, int(win.col_off + win.width)), min(src.height, int(win.row_off + win.height))
            if col1 <= col0 or row1 <= row0:
                continue
            win = Window(col0, row0, col1 - col0, row1 - row0)
            data = src.read(window=win)
            profile = src.profile | dict(driver="GTiff", width=win.width, height=win.height,
                                         transform=src.window_transform(win), compress="deflate", tiled=True,
                                         blockxsize=256, blockysize=256, BIGTIFF="NO")
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(a.out, "w", **profile) as dst:
                dst.write(data)
            print(f"wrote {a.out}: {win.width} x {win.height} px, {Path(a.out).stat().st_size / 1e6:.1f} MB")
            return
    raise SystemExit("the canopy file doesn't cover the study area")


if __name__ == "__main__":
    main()
