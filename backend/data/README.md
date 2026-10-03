# data/

Not committed: rebuild with `python scripts/build_area.py` (see the main README).

- `raw/`: downloaded inputs (DEM tile, canopy tile, cached OSM JSON)
- `area/`: generated SOLWEIG inputs (`dsm.tif`, `dem.tif`, `cdsm.tif`, `landcover.tif`, `buildings.geojson`)

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
