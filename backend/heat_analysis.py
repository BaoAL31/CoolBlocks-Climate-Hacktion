"""Spatial queries over model outputs, excluding rooftops and invalid cells."""
import numpy as np


def hottest_ground_point(utci, landcover, transform, to_lonlat):
    valid = np.isfinite(utci) & (landcover != 2)
    if not valid.any():
        raise ValueError('No valid ground temperatures are available')
    row, col = np.unravel_index(np.argmax(np.where(valid, utci, -np.inf)), utci.shape)
    x, y = transform * (int(col) + .5, int(row) + .5)
    lon, lat = to_lonlat(x, y)
    return {'lon': float(lon), 'lat': float(lat), 'utci': float(utci[row, col]),
            'row': int(row), 'col': int(col)}
