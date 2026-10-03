"""Agent-readable spatial state: bounded, paginated cells over actual model grids."""
import math

import numpy as np

from heat_analysis import hottest_ground_point


SURFACES = {0: 'paving', 1: 'asphalt', 2: 'roof', 3: 'cool_asphalt', 5: 'grass', 6: 'soil', 7: 'water'}


def finite_stats(values):
    values = values[np.isfinite(values)]
    if not values.size:
        return {'min': None, 'mean': None, 'max': None}
    return {'min': round(float(values.min()), 3), 'mean': round(float(values.mean()), 3),
            'max': round(float(values.max()), 3)}


def grid_state(dem, dsm, canopy, lc, before, after, transform, to_lonlat,
               window=None, cell_size_m=50, offset=0, limit=32, before_landcover=None):
    """All arrays use the same 1m grid. `before`/`after`: hour -> utci/shadow grids."""
    r0, r1, c0, c1 = window or (0, lc.shape[0], 0, lc.shape[1])
    rows, cols = math.ceil((r1 - r0) / cell_size_m), math.ceil((c1 - c0) / cell_size_m)
    total = rows * cols
    full_window = (slice(r0, r1), slice(c0, c1))
    full_land = lc[full_window]
    codes, counts = np.unique(full_land, return_counts=True)
    overview = {'area_m2': int(full_land.size),
                'surfaces_m2': {SURFACES.get(int(code), str(code)): int(n) for code, n in zip(codes, counts)},
                'canopy_cover_fraction': round(float((canopy[full_window] > 0).mean()), 3),
                'ground_utci_c_by_hour': {hour: finite_stats((after[hour] if after else value)['utci'][full_window][full_land != 2])
                                         for hour, value in before.items()}}
    cells = []
    for index in range(offset, min(total, offset + limit)):
        rr, cc = divmod(index, cols)
        top, left = r0 + rr * cell_size_m, c0 + cc * cell_size_m
        bottom, right = min(r1, top + cell_size_m), min(c1, left + cell_size_m)
        win = (slice(top, bottom), slice(left, right))
        land = lc[win]
        ground = land != 2
        original_land = before_landcover[win] if before_landcover is not None else land
        original_ground = original_land != 2
        codes, counts = np.unique(land, return_counts=True)
        corners = [to_lonlat(* (transform * p)) for p in ((left, top), (right, top), (right, bottom), (left, bottom))]
        west, east = min(p[0] for p in corners), max(p[0] for p in corners)
        south, north = min(p[1] for p in corners), max(p[1] for p in corners)
        center = to_lonlat(* (transform * ((left + right) / 2, (top + bottom) / 2)))
        item = {'cell_id': f'{top}:{left}', 'pixel_window': [top, bottom, left, right],
                'bounds': [[west, south], [east, north]], 'center': list(center),
                'area_m2': int(land.size), 'ground_m2': int(ground.sum()),
                'surfaces_m2': {SURFACES.get(int(code), str(code)): int(n) for code, n in zip(codes, counts)},
                'elevation_m': finite_stats(dem[win]),
                'building_height_m': finite_stats((dsm[win] - dem[win])[land == 2]),
                'canopy_cover_fraction': round(float((canopy[win] > 0).mean()), 3),
                'canopy_height_m': finite_stats(canopy[win][canopy[win] > 0]), 'hours': []}
        from affine import Affine
        cell_transform = transform * Affine.translation(left, top)
        for hour, baseline in before.items():
            original = baseline['utci'][win]
            edited = after[hour]['utci'][win] if after else original
            original_shadow = baseline['shadow'][win]
            edited_shadow = after[hour]['shadow'][win] if after else original_shadow
            finite = ground & np.isfinite(edited)
            original_finite = original_ground & np.isfinite(original)
            maximum = hottest_ground_point(edited, land, cell_transform, to_lonlat) if finite.any() else None
            if maximum:
                maximum['row'] += top
                maximum['col'] += left
            record = {'hour': hour, 'before_utci_c': finite_stats(original[original_ground]),
                      'after_utci_c': finite_stats(edited[ground]),
                      'change_utci_c': finite_stats((edited - original)[ground & original_ground]),
                      'before_shaded_fraction': round(float((original_shadow[original_finite] < .5).mean()), 3) if original_finite.any() else None,
                      'after_shaded_fraction': round(float((edited_shadow[finite] < .5).mean()), 3) if finite.any() else None,
                      'maximum_ground_point': maximum}
            item['hours'].append(record)
        cells.append(item)
    return {'overview': overview, 'cell_size_m': cell_size_m, 'total_cells': total, 'offset': offset, 'cells': cells,
            'next_offset': offset + len(cells) if offset + len(cells) < total else None,
            'units': {'utci': 'degrees Celsius; feels-like index, not air temperature', 'heights': 'metres',
                      'areas': 'square metres', 'coordinates': '[longitude,latitude]',
                      'change': 'after minus before; negative = cooling'},
            'sampling': 'Each cell aggregates all native 1m pixels; maximum_ground_point is an exact ground pixel center.'}
