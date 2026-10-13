#!/usr/bin/env python3
"""Stage-by-stage CPU-time profile of parenx skeletonize_frame internals."""
import resource
import sys
import time
from functools import partial

import geopandas as gp
import numpy as np
from shapely.affinity import affine_transform

from parenx.shared import get_base_geojson, get_geometry_buffer, get_primal
from parenx.skeletonize import (
    get_affine_transform,
    get_raster_line,
    get_raster_point,
    get_skeleton,
    sx_to_nx,
)

f = sys.argv[1]
scale = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
radius = 8.0

base = get_base_geojson(f)
gs = base["geometry"]
print(f"input: {f}  n={len(gs)}  km={gs.to_crs(27700).length.sum()/1000:.1f}", flush=True)

rows = []


def run(label, fn):
    w0, c0 = time.perf_counter(), time.process_time()
    r = fn()
    w1, c1 = time.perf_counter(), time.process_time()
    rows.append((label, w1 - w0, c1 - c0))
    print(f"  {label:28s} wall={w1-w0:9.2f}s cpu={c1-c0:9.2f}s", flush=True)
    return r


buf = run("buffer(union->buffer->union)", lambda: get_geometry_buffer(gs, radius=radius))
tr, sm, shape = get_affine_transform(buf, scale)
tot = float(np.prod(shape))
print(f"  raster shape (h,w)={shape} = {tot/1e6:.1f} Mpx", flush=True)
skel = run("rasterize+fill+skel", lambda: get_skeleton(buf, tr, shape, scale))
npx = int((skel >= 1).sum())
print(f"  foreground px = {npx/1e6:.2f} M = {100*npx/tot:.1f}% of raster", flush=True)
pts = run("px->Shapely Points", lambda: get_raster_point(skel))
line = run("px->lines(squares+query)", lambda: get_raster_line(pts, False))
sh = partial(affine_transform, matrix=sm)
out = run("transform+precision", lambda: sx_to_nx(line, sh, simplify=0.0))
# NOTE: get_primal takes a GeoSeries of lines, not the GeoDataFrame sx_to_nx returns.
run("get_primal", lambda: get_primal(out["geometry"]))

w = sum(r[1] for r in rows)
c = sum(r[2] for r in rows)
print(f"  TOTAL wall={w:.2f}s cpu={c:.2f}s", flush=True)
print(f"  peak RSS = {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1048576:.2f} GB", flush=True)
print(f"  per-foreground-pixel cpu = {1e6*c/max(npx,1):.2f} us/px", flush=True)
