#!/usr/bin/env python3
"""check_raster_parity.py: is strip-wise rasterising identical to the old single call?

The new `get_raster` rasterises the buffered network in strips of 256 rows, which
is claimed to be faster than one `rasterio.features.rasterize` call with no change
in the result. That claim is load-bearing: any pixel difference would change every
downstream output, so it is worth checking directly against the original call.

Usage: .venv/bin/python bench/check_raster_parity.py
"""

import os
import warnings

import geopandas as gp
import numpy as np
import rasterio.features as rif

warnings.filterwarnings("ignore")

from parenx.shared import get_base_geojson, get_geometry_buffer
from parenx.skeletonize import get_affine_transform, get_raster

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASETS = {
    "princes_street": "rnet_princes_street.geojson",
    "doncaster_rail": "rnet_doncaster_rail.geojson",
    "leeds_1km": "leeds_1km.geojson",
    "leeds_3km": "leeds_3km.geojson",
    "leeds_5km": "leeds_5km.geojson",
}


def compare(name, filename, scale):
    source = get_base_geojson(os.path.join(ROOT, "data", filename))
    buffered = get_geometry_buffer(source["geometry"], radius=8.0)
    transform, _, shape = get_affine_transform(buffered, scale)
    old = rif.rasterize(buffered.values, transform=transform, out_shape=shape)
    new = get_raster(np.asarray(buffered.values), transform, shape)
    differing = int((old != new).sum())
    old_foreground = int((old >= 1).sum())
    new_foreground = int((new >= 1).sum())
    print(
        f"{name:16s} scale={scale:<4} shape={str(shape):>16s} "
        f"fg old={old_foreground:9d} new={new_foreground:9d} "
        f"differing px={differing} "
        f"{'IDENTICAL' if differing == 0 else 'DIFFERENT'}"
    )
    return differing


def main():
    total = 0
    for name, filename in DATASETS.items():
        total += compare(name, filename, 1.0)
    for name in ("princes_street", "doncaster_rail", "leeds_1km"):
        total += compare(name, DATASETS[name], 2.0)
    print(f"total differing pixels across all comparisons: {total}")


if __name__ == "__main__":
    main()
