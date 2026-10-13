#!/usr/bin/env python3
"""check_conflict_pairs.py: does the get_conflict pair set miss a lost intersection?

Reviewer finding 1: `get_conflict` queries STRtree(line).query(line), so it only
tests pairs that intersect AFTER simplification. If two lines met at an interior
vertex of one of them in `base` and simplification moves one away, the pair no
longer intersects, is never tested, and the lost connection goes unnoticed.

Two questions:
  1. Does the flaw reproduce on a synthetic pair built to trigger it?
  2. Does it fire on the real pipeline, or is it only defensive hardening?
     Answered by patching in the reviewer's candidate union and comparing the
     output on real datasets.

Usage: .venv/bin/python bench/check_conflict_pairs.py
"""

import os
import sys
import warnings

import geopandas as gp
import numpy as np

warnings.filterwarnings("ignore")

from shapely import (
    STRtree,
    equals,
    from_wkt,
    get_num_coordinates,
    intersection,
    is_closed,
    is_simple,
)
from shapely.geometry import LineString

import parenx.skeletonize as S
from parenx.shared import get_base_geojson

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def fixed_get_conflict(line, base):
    """get_conflict with the candidate set unioned over base as well as line"""
    r = ~is_simple(line)
    r |= is_closed(base) & (get_num_coordinates(line) < 4)
    i_line, j_line = STRtree(line).query(line, predicate="intersects")
    i_base, j_base = STRtree(base).query(base, predicate="intersects")
    i = np.concatenate([i_line, i_base])
    j = np.concatenate([j_line, j_base])
    ix = i < j
    i, j = np.unique(np.stack([i[ix], j[ix]], axis=1), axis=0).T
    ix = ~equals(intersection(line[i], line[j]), intersection(base[i], base[j]))
    r[i[ix]] = True
    r[j[ix]] = True
    return r


def synthetic():
    """B starts on an interior vertex of A; simplifying A moves the junction away"""
    print("== synthetic: interior vertex lost by simplification")
    a = LineString([(0.0, 0.0), (50.0, 5.0), (100.0, 0.0)])  # shallow bow
    b = LineString([(50.0, 5.0), (50.0, 20.0)])  # starts on A's interior vertex
    a_simple = LineString([(0.0, 0.0), (100.0, 0.0)])  # DP flattens the bow
    base = from_wkt(
        ["LINESTRING (0 0, 50 5, 100 0)", "LINESTRING (50 5, 50 20)"]
    )
    line = from_wkt(["LINESTRING (0 0, 100 0)", "LINESTRING (50 5, 50 20)"])
    print(f"  base intersection     : {a.intersection(b)}")
    print(f"  simplified intersection: {a_simple.intersection(b)}")
    print(f"  intersects in base     : {a.intersects(b)}")
    print(f"  intersects simplified  : {a_simple.intersects(b)}")
    current = S.get_conflict(line, base)
    patched = fixed_get_conflict(line, base)
    print(f"  get_conflict (current) : {list(map(bool, current))}")
    print(f"  get_conflict (patched) : {list(map(bool, patched))}")
    lost = bool(a_simple.intersects(b) is False and a.intersects(b))
    print(
        "  VERDICT: "
        + (
            "flaw reproduces: the lost intersection is invisible to the current test"
            if lost and not current.any() and patched.all()
            else "not reproduced as described"
        )
    )


def real_data():
    """does the pair-set difference change any output on real inputs?"""
    print("\n== real data: current vs patched pair set")
    datasets = {
        "princes_street": "rnet_princes_street.geojson",
        "doncaster_rail": "rnet_doncaster_rail.geojson",
        "leeds_1km": "leeds_1km.geojson",
    }
    parameter = {
        "simplify": None,
        "buffer": 8.0,
        "scale": 1.0,
        "knot": False,
        "segment": False,
    }
    for name, filename in datasets.items():
        source = get_base_geojson(os.path.join(ROOT, "data", filename))
        original = S.get_conflict
        S.get_conflict = original
        current = S.skeletonize_frame(source["geometry"], parameter)
        S.get_conflict = fixed_get_conflict
        patched = S.skeletonize_frame(source["geometry"], parameter)
        S.get_conflict = original
        trim = lambda r: r["geometry"].values if isinstance(r, gp.GeoDataFrame) else r
        cur, pat = trim(current), trim(patched)
        same_count = len(cur) == len(pat)
        differing = 0
        if same_count:
            cw = {g.wkb for g in cur}
            pw = {g.wkb for g in pat}
            differing = len(cw ^ pw)
        print(
            f"  {name:16s} lines {len(cur)} vs {len(pat)}  "
            f"geometries differing: {differing if same_count else 'n/a (counts differ)'}"
        )


if __name__ == "__main__":
    synthetic()
    real_data()
