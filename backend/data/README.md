# data/

Committed so the app runs straight after cloning. Licences for each file: [LICENSES.md](LICENSES.md).

- `raw/canopy_usyd.tif`: USYD cut-out of the NSW canopy layer (unchanged pixels)
- `raw/osm.json`: cached OpenStreetMap download
- `area/`: SOLWEIG inputs (`dsm.tif`, `dem.tif`, `landcover.tif`, `buildings.geojson`). `cdsm.tif` (tree heights) is
  **not committed** because the canopy licence forbids sharing derived data; the server builds it on first start.

If `area/dsm.tif` is missing, the server falls back to a synthetic demo block at USYD.

## If the OSM download fails

`build_area.py` tries three Overpass servers. If all of them fail, download the data by hand:

1. Open https://overpass-turbo.eu
2. Paste this query and press Run:
   ```
   [out:json][timeout:90];
   (way["building"](-33.8912,151.1830,-33.8838,151.1920); relation["building"](-33.8912,151.1830,-33.8838,151.1920);
    way["highway"](-33.8912,151.1830,-33.8838,151.1920);
    way["leisure"~"park|pitch|garden|playground"](-33.8912,151.1830,-33.8838,151.1920); relation["leisure"~"park|pitch|garden"](-33.8912,151.1830,-33.8838,151.1920);
    way["landuse"~"grass|recreation_ground|meadow"](-33.8912,151.1830,-33.8838,151.1920); way["natural"~"water|grassland"](-33.8912,151.1830,-33.8838,151.1920);
    relation["natural"="water"](-33.8912,151.1830,-33.8838,151.1920););
   out geom;
   ```
3. Export, then "raw OSM data", and save it as `backend/data/raw/osm.json`
4. Run `python scripts/build_area.py --osm-json data/raw/osm.json` again. It uses the saved file and skips the download.

## Keeping only the USYD part of the canopy file

The Greater Sydney canopy download is ~600 MB. Cut out the study area once:

    python scripts/clip_canopy.py "data/raw/<canopy download>.zip"     # -> data/raw/canopy_usyd.tif

then use `--canopy data/raw/canopy_usyd.tif`. A deployed server needs `data/area/` plus `raw/canopy_usyd.tif` (a few MB in total), not the big download.
The canopy licence is CC BY-NC-ND 4.0: the unchanged cut-out may be shared non-commercially with credit, but never commit `area/cdsm.tif` (a derivative).
