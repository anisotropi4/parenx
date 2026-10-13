#!/usr/bin/env python3
"""simplify_metrics.py: measure vertex reduction and topology preservation.

Two modes:

  freeze  run the CURRENT implementation on the bundled small datasets and save
          its outputs plus a metrics baseline under bench/reference/.
  check   measure the same datasets, or saved GeoPackages, and diff against the
          frozen baseline.

Usage:
  .venv/bin/python bench/simplify_metrics.py freeze
  .venv/bin/python bench/simplify_metrics.py check
  .venv/bin/python bench/simplify_metrics.py metrics FILE.gpkg [LAYER ...]
"""

import json
import os
import sys

import geopandas as gp
import networkx as nx
import numpy as np
from shapely import get_coordinates, line_merge, points as sh_points, unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(HERE, "reference")
DATA = os.path.join(os.path.dirname(HERE), "data")
CRS = "EPSG:27700"

DATASETS = {
    "princes_street": "rnet_princes_street.geojson",
    "doncaster_rail": "rnet_doncaster_rail.geojson",
    "leeds_1km": "leeds_1km.geojson",
    "leeds_3km": "leeds_3km.geojson",
    "leeds_5km": "leeds_5km.geojson",
}


def geoms_of(value):
    """the raster APIs return a GeoDataFrame; the others return a GeoSeries"""
    return value["geometry"] if isinstance(value, gp.GeoDataFrame) else value


def snap_key(point, grid=0.1):
    """integer key on a 0.1 m grid, so exact-hash topology is not float fragile"""
    return tuple(np.round(np.asarray(point) / grid).astype(np.int64))


def endpoint_graph(geoms):
    """simple graph from exact (snapped) endpoints of every line"""
    graph = nx.Graph()
    n = 0
    for geom in geoms:
        if geom is None or geom.is_empty:
            continue
        coords = get_coordinates(geom)
        if len(coords) < 2:
            continue
        graph.add_edge(snap_key(coords[0]), snap_key(coords[-1]))
        n += 1
    return graph, n


def metrics(geoms, name=""):
    """vertex count plus a topological fingerprint"""
    geoms = gp.GeoSeries([g for g in geoms if g is not None and not g.is_empty], crs=CRS)
    verts = int(sum(len(g.coords) for g in geoms))
    km = float(geoms.length.sum() / 1000)
    graph, n = endpoint_graph(geoms)
    components = nx.number_connected_components(graph) if len(graph) else 0
    degrees = dict(graph.degree())
    d1 = sum(1 for v in degrees.values() if v == 1)
    d2 = sum(1 for v in degrees.values() if v == 2)
    d3 = sum(1 for v in degrees.values() if v > 2)
    cycle_rank = n - len(graph) + components
    return {
        "name": name,
        "lines": int(len(geoms)),
        "vertices": verts,
        "km": round(km, 3),
        "verts_per_km": round(verts / km, 1) if km else None,
        "nodes": int(len(graph)),
        "components": int(components),
        "degree1": d1,
        "degree2": d2,
        "degree3plus": d3,
        "cycle_rank": int(cycle_rank),
    }


def coverage(geoms, other, distance, step=1.0):
    """fraction of geoms' length lying within `distance` of `other`

    Robust by construction: densify to at most `step`, then measure the distance
    from each densified vertex to the nearest part of `other`. This avoids the
    invalid polygons that buffering a mitred network produces, which makes a
    buffer-intersection version of this check throw TopologyException.
    """
    if len(geoms) == 0:
        return None
    total = float(geoms.length.sum())
    if total == 0:
        return None
    coords = get_coordinates(geoms.segmentize(step).values)
    if len(coords) == 0:
        return None
    other = other if hasattr(other, "sindex") else gp.GeoSeries(other, crs=CRS)
    _, distance_to = other.sindex.nearest(
        sh_points(coords[:, 0], coords[:, 1]), return_distance=True
    )
    return round(float((distance_to <= distance).mean()), 4)


def report(label, frame, other=None):
    row = metrics(frame, label)
    if other is not None:
        for d in (1.0, 2.0, 5.0, 16.0):
            row[f"within_{d}m"] = coverage(frame, other, d)
    return row


def print_table(rows):
    keys = [
        "name",
        "lines",
        "vertices",
        "verts_per_km",
        "km",
        "components",
        "degree1",
        "degree2",
        "degree3plus",
        "cycle_rank",
    ]
    width = max(len(str(r.get("name", ""))) for r in rows) + 2
    print("".join(k.ljust(width) for k in keys))
    for row in rows:
        print("".join(str(row.get(k, "")).ljust(width) for k in keys))
    for row in rows:
        cov = {k: v for k, v in row.items() if k.startswith("within_")}
        if cov:
            print(f"  {row['name']}: {cov}")


def noded_input(frame):
    """input noded into true topology: union, line_merge, explode"""
    merged = line_merge(unary_union(frame["geometry"].values))
    return gp.GeoSeries(list(getattr(merged, "geoms", [merged])), crs=CRS)


def freeze():
    """run the current implementation and save outputs plus a baseline"""
    from parenx.skeletonize import skeletonize_frame

    os.makedirs(REF, exist_ok=True)
    baseline = {}
    for name, filename in DATASETS.items():
        source = gp.read_file(os.path.join(DATA, filename)).to_crs(CRS)
        parameter = {
            "simplify": 0.0,
            "buffer": 8.0,
            "scale": 1.0,
            "knot": False,
            "segment": False,
        }
        line = skeletonize_frame(source["geometry"], parameter)
        path = os.path.join(REF, f"{name}_line.gpkg")
        if os.path.exists(path):
            os.remove(path)
        gp.GeoDataFrame({"geometry": geoms_of(line).values}).to_file(
            path, driver="GPKG", layer="line"
        )
        noded = noded_input(source)
        baseline[name] = {
            "input": report(f"{name}:input", source["geometry"]),
            "input_noded": report(f"{name}:input_noded", noded),
            "current_line": report(f"{name}:current_line", geoms_of(line), other=source["geometry"]),
        }
        print(f"frozen {path}")
    with open(os.path.join(REF, "baseline.json"), "w") as handle:
        json.dump(baseline, handle, indent=2, sort_keys=True)
    print(f"wrote {os.path.join(REF, 'baseline.json')}")
    for name, rows in baseline.items():
        print(f"\n== {name}")
        print_table(list(rows.values()))
    return baseline


def check(simplify=0.0):
    """measure the current tree and diff against the frozen baseline"""
    from parenx.skeletonize import skeletonize_frame

    with open(os.path.join(REF, "baseline.json")) as handle:
        baseline = json.load(handle)
    for name, filename in DATASETS.items():
        source = gp.read_file(os.path.join(DATA, filename)).to_crs(CRS)
        parameter = {
            "simplify": simplify,
            "buffer": 8.0,
            "scale": 1.0,
            "knot": False,
            "segment": False,
        }
        line = geoms_of(skeletonize_frame(source["geometry"], parameter))
        now = report(f"{name}:now", line, other=source["geometry"])
        was = baseline[name]["current_line"]
        print(f"\n== {name}")
        print_table([baseline[name]["input"], was, now])
        print(
            f"  vertices {was['vertices']} -> {now['vertices']}"
            f" ({100 * (now['vertices'] - was['vertices']) / max(was['vertices'], 1):+.1f}%)"
        )
        print(
            f"  components {was['components']} -> {now['components']},"
            f" degree3plus {was['degree3plus']} -> {now['degree3plus']},"
            f" degree1 {was['degree1']} -> {now['degree1']}"
        )


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode == "freeze":
        freeze()
    elif mode == "check":
        check()
    elif mode == "check-default":
        check(simplify=None)
    elif mode == "metrics":
        rows = []
        for path in sys.argv[2:]:
            frame = gp.read_file(path)
            geoms = geoms_of(frame)
            rows.append(report(os.path.basename(path), geoms))
        print_table(rows)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
