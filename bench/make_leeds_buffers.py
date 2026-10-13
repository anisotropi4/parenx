#!/usr/bin/env python3
"""make_leeds_buffers.py: extract 1, 3 and 5 km circular subsets of central Leeds.

Cheap, growing-extent test cases, so the simplifier can be exercised on a
realistic urban network without paying for the whole 144 Mpx Leeds raster.

The centre is the length-independent centre of gravity of the network's vertices,
which sits about 170 m from Briggate (the historic city centre), 220 m from the
bounding-box centre and 630 m from Leeds railway station. All the plausible
centres agree to within 650 m, which is well inside the smallest radius here.

Writes data/leeds_1km.geojson, data/leeds_3km.geojson and data/leeds_5km.geojson.

Usage: .venv/bin/python bench/make_leeds_buffers.py
"""

import os

import geopandas as gp
import numpy as np
from shapely import get_coordinates
from shapely.geometry import Point

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOURCE = os.path.join(ROOT, "data", "osm_leeds.geojson")
RADII = (1000.0, 3000.0, 5000.0)
# OS Open Roads, clipped to the same circles: the reference for what a simplified
# network looks like (straightened, junction to junction, ~60 to 82 vertices/km).
OPENROADS = "/mnt/secondary/data/bronze/os/openroads/oproad_gb_2025-12.gpkg"
OPENROADS_LAYER = "open_roads_leeds"


def nearest_openroads():
    """the openroads path if it is present on this host, else None"""
    return OPENROADS if os.path.exists(OPENROADS) else None


def centre_of_network(frame):
    """centre of gravity of the vertices, in the frame's own CRS"""
    coords = get_coordinates(frame.geometry.values)
    return float(coords[:, 0].mean()), float(coords[:, 1].mean())


def main():
    network = gp.read_file(SOURCE).to_crs("EPSG:27700")
    cx, cy = centre_of_network(network)
    print(f"centre {cx:.0f},{cy:.0f}")
    for radius in RADII:
        circle = Point(cx, cy).buffer(radius, quad_segs=256)
        clip = gp.clip(network, circle, keep_geom_type=True)
        clip = clip.explode(index_parts=False).reset_index(drop=True)
        clip = gp.GeoDataFrame({"geometry": clip["geometry"].values}, crs="EPSG:27700")
        path = os.path.join(ROOT, "data", f"leeds_{int(radius / 1000)}km.geojson")
        if os.path.exists(path):
            os.remove(path)
        clip.to_file(path, driver="GeoJSON")
        verts = int(sum(len(g.coords) for g in clip.geometry))
        km = clip.length.sum() / 1000
        size = os.path.getsize(path) / 1024
        print(
            f"  {int(radius/1000)} km: lines={len(clip):6d} vertices={verts:7d} "
            f"km={km:8.1f} verts/km={verts/km:7.1f} file={size:8.0f} KB"
        )

    source = nearest_openroads()
    if source is None:
        print(f"no openroads at {OPENROADS}, skipping the reference networks")
        return
    reference = gp.read_file(source, layer=OPENROADS_LAYER).to_crs("EPSG:27700")
    print(f"openroads reference source: {source} layer {OPENROADS_LAYER}")
    for radius in RADII:
        circle = Point(cx, cy).buffer(radius, quad_segs=256)
        clip = gp.clip(reference, circle, keep_geom_type=True)
        clip = clip.explode(index_parts=False).reset_index(drop=True)
        clip = gp.GeoDataFrame({"geometry": clip["geometry"].values}, crs="EPSG:27700")
        path = os.path.join(ROOT, "data", f"openroads_leeds_{int(radius/1000)}km.geojson")
        if os.path.exists(path):
            os.remove(path)
        clip.to_file(path, driver="GeoJSON")
        verts = int(sum(len(g.coords) for g in clip.geometry))
        km = clip.length.sum() / 1000
        print(
            f"  reference {int(radius/1000)} km: lines={len(clip):6d} vertices={verts:7d} "
            f"km={km:8.1f} verts/km={verts/km:6.2f} (the density target)"
        )


if __name__ == "__main__":
    main()
