# Data licences

The project's **code** is GPL-3.0 (see `/LICENSE`). The **data files in this folder are not covered by the GPL**.
Each keeps its own licence, listed below. All are used for a non-commercial student hackathon project.

| File | Made from | Licence and credit |
|---|---|---|
| `raw/canopy_usyd.tif` | Greater Sydney Region Tree Canopy 2024/25 (GDA2020), cut to the USYD area with `scripts/clip_canopy.py`. Pixels are unchanged. | **CC BY-NC-ND 4.0**. © State of New South Wales (NSW Department of Planning, Housing and Infrastructure). Non-commercial use only, no modified versions may be shared. Source: https://www.planningportal.nsw.gov.au/opendata/dataset/greater-sydney-region-tree-canopy-202425 |
| `area/cdsm.tif` | Tree heights derived from the canopy file | **Not in the repo.** It is a modified version of the canopy data, which the licence does not allow us to share. The server builds it locally from `raw/canopy_usyd.tif` the first time it starts. |
| `raw/osm.json`, `area/buildings.geojson`, `area/landcover.tif`, building heights in `area/dsm.tif` | OpenStreetMap via the Overpass API | **ODbL 1.0**. © OpenStreetMap contributors, https://www.openstreetmap.org/copyright. These derived files are also offered under ODbL. |
| `area/dem.tif`, ground heights in `area/dsm.tif` | Copernicus DEM GLO-30 | Copernicus DEM licence (free use and redistribution with credit). © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA; all rights reserved. |

If you use this repository for anything commercial, delete `raw/canopy_usyd.tif` first.
