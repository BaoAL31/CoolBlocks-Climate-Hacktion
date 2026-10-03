# SOLWEIG accuracy check for Sydney (3 Oct 2026)

Package tested: `solweig` 0.1.0b96 (pip), CPU. Location: Parramatta (−33.815, 151.003). Test script: `solweig_sydney_checks.py` (in this folder).

## Summary
- **Sun and shadows: correct for Sydney.** Sun position matches an independent NOAA formula to within about 0.7°. Shadows point the right way and are the right length.
- **Bug for the Southern Hemisphere: trees lose their leaves in summer by default.** The leaf season is hard-coded to day 97–300 (April–October, a Northern Hemisphere summer). In Sydney's January, trees are therefore treated as bare branches that let 50% of the sun through, instead of 3%. **Fix: pass `conifer=True` to `solweig.calculate()`** (always in leaf; most Australian street trees are evergreen anyway). Without it, tree cooling is understated by more than half.
- **Absolute numbers: plausible, but not validated for Sydney.** The package is only validated against Gothenburg, Sweden, where it is off by about 3–7 °C (Tmrt RMSE) and tends to run warm (+0.5 to +3.8 °C bias). Use it for **before vs after differences**, not as an exact temperature reading.

## A. Sun position (SOLWEIG vs independent NOAA formula)
| Local time | SOLWEIG alt / az | NOAA alt / az |
|---|---|---|
| 15 Jan 09:00 AEDT | 35.0 / 93.6 | 35.2 / 93.7 |
| 15 Jan 13:00 AEDT | 77.3 / 5.5 | 77.5 / 4.8 |
| 15 Jan 16:00 AEDT | 49.6 / 276.7 | 49.5 / 276.3 |
| 21 Jun 12:00 AEST | 32.8 / 359.4 | 32.7 / 359.2 |
| 21 Jun 08:00 AEST | 9.7 / 53.2 | 9.7 / 53.1 |

At noon the sun is in the north, as it should be in Sydney.

Note: SOLWEIG treats each weather timestamp as the *end* of the hour and places the sun 30 min earlier. A row labelled 15:00 uses the 14:30 sun.

## B. Shadow from a 20 m pole
| Time | Shadow direction (expected) | Length (expected) |
|---|---|---|
| 21 Jun 12:00 | 182° (179°) points south ✓ | 31.0 m (31.1 m) |
| 15 Jan 09:00 | 275° (274°) points west ✓ | 29.2 m (28.6 m) |
| 15 Jan 17:00 | 86° (88°) points east ✓ | 26.1 m (26.4 m) |

## C. Heatwave street test (synthetic)
The street is 30 m wide and runs east–west between 10 m buildings, with 8 m trees on the south footpath. The weather is a 15 Jan heatwave (air temperature 37 °C at noon, 42 °C at 3pm, low humidity, clear sky).

| | Open street | Under a tree (default, leafless bug) | Under a tree (`conifer=True`) |
|---|---|---|---|
| 12:00 Tmrt | 65.5 °C | 53.9 °C | **39.8 °C** |
| 12:00 UTCI | 43.2 °C | 40.4 °C | **37.1 °C** |
| 15:00 Tmrt | 69.7 °C | 59.7 °C | **45.6 °C** |
| 15:00 UTCI | 48.1 °C | 45.7 °C | **42.5 °C** |

- The open-street values (Tmrt about 65–70 °C, UTCI 43–48 °C, i.e. "very strong" to "extreme" heat stress) are in the range usually reported for hot, clear summer afternoons. That comparison is from general knowledge of the literature, not checked against a specific Sydney study.
- With the fix, tree shade cuts Tmrt by about 25 °C and UTCI by about 6 °C, a plausible size.
- Trees warm the *sunlit* part of the street slightly (+0.5 to +1 °C UTCI), because they block the view of the cooler sky. This is real physics in the model, not a bug, but expect it in the maps.

## What this means for the app
1. Always run with `conifer=True` for Sydney.
2. Show **changes** (before vs after), and say the absolute values are model estimates.
3. Use realistic Sydney weather (a recent heatwave day from BOM data or a Sydney EPW file).
4. Accuracy will depend most on the quality of the height map (DSM and tree heights) you feed it.

## D. Can SOLWEIG change trees, pavement and roofs? (tested 3 Oct)
SOLWEIG has **no built-in "add tree" or "change pavement" tool**. It takes maps as input, and you make an intervention by editing those maps yourself and re-running:

| Intervention | How | Works? |
|---|---|---|
| Add or remove trees | Edit the canopy-height array (`cdsm`): set heights to add trees, 0 to remove them. `solweig.io.rasterise_gdf()` turns drawn polygons into a height grid. | ✓ tested (section C) |
| Change ground surface (asphalt → grass, soil, water, cobbles) | Edit the `land_cover` array, using codes 0 cobble, 1 dark asphalt, 2 roofs, 5 grass, 6 bare soil, 7 water | ✓ tested |
| "Cool"/reflective pavement | Custom materials JSON with a higher albedo for the class, passed via `materials=solweig.load_materials(...)` | ✓ runs, see the warning below |
| Cool roofs | Roof albedo class 2 in the materials file | Little effect at street level. **SOLWEIG doesn't model indoor or air-con energy**, so the cool-roof energy saving has to come from a separate estimate |

3pm heatwave test, centre of a 30 m street:

| Street surface | Tmrt | UTCI |
|---|---|---|
| Dark asphalt (default) | 73.1 °C | 49.0 °C |
| Grass instead | 64.0 °C | 46.7 °C |
| Reflective asphalt (albedo 0.45) | 80.0 °C | 50.7 °C |

⚠️ **Reflective pavement makes it feel *hotter* for a person standing on it in the sun**, because the extra reflected sunlight hits their body. As far as I recall, real cool-pavement trials (e.g. in Los Angeles) reported the same trade-off; check before citing. Caveat: SOLWEIG's ground-temperature scheme may not lower the pavement's own temperature when albedo rises, so it may overstate this penalty. Present cool pavement as a trade-off (cooler surface and nights, more glare and radiant heat at midday), not a simple win. Trees and grass are the clear wins.
