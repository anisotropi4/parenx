#!/usr/bin/env python3
"""component_provenance.py: did the new code lose topology, or fix a split?

The new implementation reports fewer connected components than the old one on the
larger Leeds circles (87 to 81 on 3 km, 142 to 134 on 5 km). Fewer is a regression
only if the merged pieces were genuinely separate.

Two pieces of an output are NOT genuinely separate if they lie within the buffer
merge distance of each other, because an 8 m buffer joins anything closer than
16 m: they are then inside a single buffered polygon, the skeleton of which should
be one connected component. So cluster the OLD output's components with a 16 m
proximity rule and compare that count with the new component count.

  old_clustered == new count -> the old output was splitting one buffered
                               component; the new merge is a fix
  new count < old_clustered  -> the new code merged pieces the 16 m rule keeps
                               apart, which needs explaining

The 16 m rule is a geometric proxy for the raster short-link filter, not a proof,
and the numbers should be read with that in mind.

Usage: .venv/bin/python bench/component_provenance.py
"""

import os
import warnings

import geopandas as gp
import networkx as nx
import numpy as np
from shapely import get_coordinates, unary_union

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REF = os.path.join(HERE, "reference")
BUFFER = 8.0
MERGE_DISTANCE = 2.0 * BUFFER

DATASETS = {
    "princes_street": "rnet_princes_street.geojson",
    "doncaster_rail": "rnet_doncaster_rail.geojson",
    "leeds_1km": "leeds_1km.geojson",
    "leeds_3km": "leeds_3km.geojson",
    "leeds_5km": "leeds_5km.geojson",
}
OLD = {
    "princes_street": 2,
    "doncaster_rail": 1,
    "leeds_1km": 29,
    "leeds_3km": 87,
    "leeds_5km": 142,
}
NEW = {
    "princes_street": 2,
    "doncaster_rail": 1,
    "leeds_1km": 29,
    "leeds_3km": 81,
    "leeds_5km": 134,
}


def snap_key(point, grid=0.1):
    return tuple(np.round(np.asarray(point) / grid).astype(np.int64))


def component_label(frame):
    """one label per line, by exact snapped endpoint hashing"""
    graph = nx.Graph()
    keys = []
    for geom in frame.geometry.values:
        if geom is None or geom.is_empty:
            continue
        coords = get_coordinates(geom)
        if len(coords) < 2:
            keys.append(None)
            continue
        start, end = snap_key(coords[0]), snap_key(coords[-1])
        graph.add_edge(start, end)
        keys.append(start)
    labels = {}
    for i, members in enumerate(nx.connected_components(graph)):
        for node in members:
            labels[node] = i
    return [labels.get(k) if k is not None else None for k in keys]


def component_geometries(frame):
    """dissolve the frame's lines into one geometry per component"""
    labels = component_label(frame)
    groups = {}
    for label, geometry in zip(labels, frame.geometry.values):
        if geometry is None or geometry.is_empty or label is None:
            continue
        groups.setdefault(label, []).append(geometry)
    return gp.GeoSeries(
        [unary_union(parts) for _, parts in sorted(groups.items())], crs=frame.crs
    )


def proximity_clusters(pieces, distance):
    """count groups of pieces whose geometries lie within `distance`"""
    graph = nx.Graph()
    graph.add_nodes_from(range(len(pieces)))
    left, right = pieces.sindex.query(
        pieces.buffer(distance / 2.0), predicate="intersects"
    )
    for i, j in zip(left, right):
        if i < j:
            graph.add_edge(int(i), int(j))
    return nx.number_connected_components(graph)


def main():
    from parenx.shared import get_geometry_buffer
    from parenx.skeletonize import skeletonize_frame

    print(
        f"{'dataset':16s} {'buffered':>8s} {'old':>5s} {'old@16m':>8s} {'new':>5s}  verdict"
    )
    for name, filename in DATASETS.items():
        source = gp.read_file(os.path.join(ROOT, "data", filename)).to_crs("EPSG:27700")
        old_path = os.path.join(REF, f"{name}_line.gpkg")
        old = gp.read_file(old_path).to_crs("EPSG:27700")
        parameter = {
            "simplify": 0.0,
            "buffer": BUFFER,
            "scale": 1.0,
            "knot": False,
            "segment": False,
        }
        new = skeletonize_frame(source["geometry"], parameter)
        new = new["geometry"] if isinstance(new, gp.GeoDataFrame) else new
        new = gp.GeoSeries(new, crs="EPSG:27700")
        new = new[~new.is_empty]

        old_clustered = proximity_clusters(
            component_geometries(old), MERGE_DISTANCE
        )
        buffered = len(get_geometry_buffer(source["geometry"], radius=BUFFER))
        if old_clustered == NEW[name] == OLD[name]:
            verdict = "counts agree, nothing to explain"
        elif old_clustered == NEW[name]:
            verdict = "old split pieces inside one buffered polygon; new merge is a fix"
        elif old_clustered == OLD[name]:
            verdict = "UNEXPLAINED: new merged pieces 16 m apart, old count stands"
        else:
            verdict = f"UNEXPLAINED: old@16m={old_clustered} vs old={OLD[name]} new={NEW[name]}"
        print(
            f"{name:16s} {buffered:8d} {OLD[name]:5d} {old_clustered:8d} {NEW[name]:5d}  {verdict}"
        )


if __name__ == "__main__":
    main()
