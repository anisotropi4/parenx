#!/usr/bin/env python3
"""simplify_check.py: reproduce the issue #4 before/after numbers in one command.

  .venv/bin/python bench/simplify_check.py [DATASET ...]

Runs the two small datasets first, then the 1, 3 and 5 km Leeds circles, then
prints one summary table. For each dataset this:

  - times the new skeletonize_frame at the default parameters, wall and CPU time,
    and on the small datasets only the legacy pixel-to-vector stage (Point, square
    buffer, STRtree, kept in parenx.skeletonize as get_raster_point,
    get_raster_line and sx_to_nx). The legacy stage is skipped on the Leeds
    circles, where it would take most of the run
  - measures the new output with the bench/simplify_metrics.py oracle at the
    default parameters (no "simplify" key) and at explicit simplify 0.0
  - re-scores the frozen baseline with only exactly collinear vertices removed: the
    same point set, so any coverage change on that row is oracle sampling, as the
    oracle densifies to 1 m and averages over vertices rather than length
  - checks that simplification itself changes no topology: identical end-point
    graph, no non-simple lines, no zero-length lines, no new crossings between lines
  - reports the component count of the buffered input, which is the component
    count a buffer-and-skeletonize method can preserve
  - on the Leeds circles, reports the vertex density of OS Open Roads clipped to
    the same circle (data/openroads_leeds_*km.geojson), the reference for what a
    simplified network looks like, and the ratio of the new output to it

Reads bench/reference/baseline.json. Writes nothing.
"""

import json
import os
import sys
import time
from functools import partial

import geopandas as gp
import numpy as np
from shapely import LineString, get_coordinates, is_simple, unary_union
from shapely.affinity import affine_transform

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from simplify_metrics import CRS, DATA, DATASETS, REF, coverage, metrics  # noqa: E402

from parenx import skeletonize as sk  # noqa: E402
from parenx.shared import get_geometry_buffer  # noqa: E402

BASE = {"buffer": 8.0, "scale": 1.0, "knot": False, "segment": False}
SMALL = ("princes_street", "doncaster_rail")


def timed(f, *arg):
    wall, cpu = time.perf_counter(), time.process_time()
    r = f(*arg)
    return r, time.perf_counter() - wall, time.process_time() - cpu


def legacy(geometry, parameter):
    """the pre-change skeletonize_frame, assembled from the retained legacy stages"""
    radius, scale = parameter["buffer"], parameter["scale"]
    nx_geometry = get_geometry_buffer(geometry, radius=radius)
    r_matrix, s_matrix, out_shape = sk.get_affine_transform(nx_geometry, scale)
    skeleton_im = sk.get_skeleton(nx_geometry, r_matrix, out_shape, scale)
    point = sk.get_raster_point(skeleton_im)
    line = sk.get_raster_line(point, parameter["knot"])
    transform = partial(affine_transform, matrix=s_matrix)
    return sk.sx_to_nx(line, transform, simplify=0.0)


def end_key(geoms):
    """sorted multiset of snapped end-point pairs, the end-point graph"""
    r = []
    for g in geoms:
        c = np.round(get_coordinates(g)[[0, -1]] / 0.1).astype(np.int64)
        r.append(tuple(sorted(map(tuple, c))))
    return sorted(r)


def crossing(geoms):
    """number of noded pieces beyond the input lines, a proxy for crossings"""
    return len(getattr(unary_union(list(geoms)), "geoms", [None])) - len(geoms)


def row(label, geoms, source):
    r = metrics(geoms, label)
    for d in (1.0, 2.0, 5.0):
        r[f"within_{d:g}m"] = coverage(gp.GeoSeries(geoms, crs=CRS), source, d)
    return r


def openroads(name):
    """OS Open Roads metrics on the same circle, or None if there is no reference"""
    path = os.path.join(DATA, f"openroads_{name}.geojson")
    if not os.path.exists(path):
        return None
    return metrics(gp.read_file(path).to_crs(CRS)["geometry"].values, name)


def summary(table):
    """one line per dataset: the default output against the baseline and reference"""
    head = ["dataset", "v/km base", "v/km new", "reduce", "comp", "deg3+",
            "within 2m", "OR v/km", "new/OR", "wall s", "cpu s"]
    width = [16, 10, 9, 7, 10, 14, 16, 8, 7, 7, 7]
    print("\n== summary, new default against frozen baseline (base -> new)")
    print("".join(h.rjust(w) if i else h.ljust(w) for i, (h, w) in enumerate(zip(head, width))))
    for name, was, new, ref, wall, cpu in table:
        cell = [name,
                f"{was['verts_per_km']:.1f}",
                f"{new['verts_per_km']:.1f}",
                f"{was['verts_per_km'] / new['verts_per_km']:.1f}x",
                f"{was['components']}->{new['components']} ",
                f"{was['degree3plus']}->{new['degree3plus']}",
                f"{was['within_2m']:.4f}->{new['within_2m']:.4f}",
                f"{ref['verts_per_km']:.1f}" if ref else "-",
                f"{new['verts_per_km'] / ref['verts_per_km']:.2f}" if ref else "-",
                f"{wall:.1f}", f"{cpu:.1f}"]
        print("".join(c.rjust(w) if i else c.ljust(w) for i, (c, w) in enumerate(zip(cell, width))))


def main():
    with open(os.path.join(REF, "baseline.json")) as handle:
        baseline = json.load(handle)
    keys = ["lines", "vertices", "verts_per_km", "components", "degree1",
            "degree2", "degree3plus", "cycle_rank", "within_1m", "within_2m",
            "within_5m"]
    names = sys.argv[1:] or sorted(DATASETS, key=lambda k: k not in SMALL)
    table = []
    for name in names:
        source = gp.read_file(os.path.join(DATA, DATASETS[name])).to_crs(CRS)
        geometry = source["geometry"]
        old_wall = old_cpu = None
        if name in SMALL:
            _, old_wall, old_cpu = timed(legacy, geometry, {**BASE, "simplify": 0.0})
        new, new_wall, new_cpu = timed(sk.skeletonize_frame, geometry, dict(BASE))
        zero = sk.skeletonize_frame(geometry, {**BASE, "simplify": 0.0})
        new, zero = new["geometry"].values, zero["geometry"].values
        was = dict(baseline[name]["current_line"])
        for d in ("1", "2", "5"):
            was[f"within_{d}m"] = was.pop(f"within_{d}.0m")
        frozen = gp.read_file(os.path.join(REF, f"{name}_line.gpkg"))["geometry"]
        same = [LineString(sk.drop_collinear(get_coordinates(g))) for g in frozen]
        rows = [("baseline (frozen)", was),
                ("baseline collinear", row("same", same, geometry)),
                ("new simplify=0.0", row("zero", zero, geometry)),
                ("new default", row("default", new, geometry))]
        buffer_parts = len(get_geometry_buffer(geometry, radius=BASE["buffer"]))
        print(f"\n== {name}")
        print("".join(["".ljust(20)] + [k.rjust(13) for k in keys]))
        for label, r in rows:
            print("".join([label.ljust(20)] + [str(r[k]).rjust(13) for k in keys]))
        noded = baseline[name]["input_noded"]["components"]
        print(f"  components: input noded {noded}, buffered input {buffer_parts}")
        print(f"  verts/km reduction vs baseline: "
              f"{was['verts_per_km'] / rows[-1][1]['verts_per_km']:.1f}x")
        print(f"  simplification keeps end-point graph: {end_key(zero) == end_key(new)}")
        print(f"  non-simple lines: zero {int((~is_simple(zero)).sum())}, "
              f"default {int((~is_simple(new)).sum())}")
        print(f"  zero-length lines: default "
              f"{int((gp.GeoSeries(new).length == 0).sum())}")
        print(f"  noded pieces beyond line count (crossings): zero {crossing(zero)},"
              f" default {crossing(new)}")
        ref = openroads(name)
        if ref:
            print(f"  OS Open Roads reference: {ref['verts_per_km']} verts/km, "
                  f"{ref['km']} km, new default / reference "
                  f"{rows[-1][1]['verts_per_km'] / ref['verts_per_km']:.2f}")
        legacy_time = (f"legacy: wall {old_wall:.2f}s cpu {old_cpu:.2f}s, "
                       if old_wall is not None else "legacy: skipped, ")
        print(f"  time {legacy_time}new default: wall {new_wall:.2f}s cpu {new_cpu:.2f}s",
              flush=True)
        table.append((name, was, rows[-1][1], ref, new_wall, new_cpu))
    summary(table)


if __name__ == "__main__":
    main()
