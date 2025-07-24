#!/usr/bin/env python3
"""tile_skeletonize: tile skeletonize geometry"""

import argparse
from functools import partial

import geopandas as gp
import numpy as np
import pandas as pd
from pyogrio import write_dataframe
from shapely import STRtree, box, clip_by_rect, disjoint, voronoi_polygons
from shapely.geometry import LineString, MultiPoint

from parenx.skeletonize import skeletonize_frame

from parenx.shared import (
    CRS,
    combine_line,
    get_base_geojson,
    get_primal,
    get_source_target,
    log,
)

pd.set_option("display.max_columns", None)


def get_args():
    """get_args: get command line parameters
    returns:
      parameter dict
    """

    parser = argparse.ArgumentParser(
        description="GeoJSON network raster simplification"
    )
    parser.add_argument("inpath", type=str, help="GeoJSON filepath to simplify")
    parser.add_argument(
        "outpath",
        nargs="?",
        type=str,
        help="GeoGPKG output path",
        default="output.gpkg",
    )
    parser.add_argument("--simplify", help="tolerance [m]", type=float, default=0.0)
    parser.add_argument("--buffer", help="line buffer [m]", type=float, default=8.0)
    parser.add_argument("--length", help="square size [m]", type=float, default=2000.0)
    parser.add_argument("--scale", help="raster scale", type=float, default=1.0)
    parser.add_argument("--knot", help="keep image knots", action="store_true")
    parser.add_argument("--segment", help="segment", action="store_true")
    return vars(parser.parse_args())


def get_square(this_gf, side_length=2000.0):
    """get_square: tile a geometry with squares default 4km² tiles
    returns:
      square tile GeoSeries
    """
    dimension = np.asarray(this_gf.union_all().bounds).reshape(-1, 2)
    outer_geometry = box(*dimension.reshape(-1))
    n, m = np.ceil(np.diff(dimension, axis=0).reshape(-1) / side_length + 2.0).astype(
        int
    )
    x_centre, y_centre = np.mean(dimension, axis=0)
    xn = x_centre + np.linspace(-n, n, 2 * n + 1) * side_length / 2.0
    yn = y_centre + np.linspace(-m, m, 2 * m + 1) * side_length / 2.0
    mesh = np.r_[np.meshgrid(xn, yn)].reshape(2, -1)
    point = MultiPoint(mesh.T)
    r = voronoi_polygons(point, extend_to=outer_geometry)
    ix = disjoint(r.geoms, outer_geometry)
    r = gp.GeoSeries(r.geoms, crs=CRS)
    return r[~ix]


def clip_geometry(*bound, geometry):
    """get_clip_geometry:"""
    s = np.asarray(bound).reshape(-1)
    return clip_by_rect(geometry, *s)


def get_tile_extent(this_nx, square, radius):
    """get_tile_extent:"""
    get_clip_geometry = partial(clip_geometry, geometry=this_nx.union_all())
    extent = square.buffer(3.0 * radius, join_style="mitre")
    r = gp.GeoSeries(extent.bounds.apply(get_clip_geometry, axis=1), crs=CRS)
    ix = r.is_empty
    r = r[~ix].to_frame("geometry")
    r["id"] = r.index
    r = r.explode(index_parts=True)
    return r


def get_gap_fill(this_gf, square, radius):
    """get_gap_fill: identify and fill gaps at"""
    nx_line = combine_line(this_gf["geometry"]).to_frame("geometry")
    _, node = get_source_target(nx_line)
    square_tree = STRtree(square.boundary)
    i, _ = square_tree.query(node["geometry"], predicate="within")
    ix = np.unique(i)
    end_node = node.loc[ix]
    node_tree = STRtree(end_node["geometry"])
    point = node_tree.geometries
    i, j = node_tree.query(point, predicate="dwithin", distance=radius / 2.0)
    ix = i != j
    ix = np.sort(np.stack([i[ix], j[ix]]).T, axis=1)
    i, j = np.unique(ix, axis=0).T
    r = gp.GeoSeries(map(LineString, zip(point[i], point[j])), crs=CRS)
    return r.to_frame("geometry")


def skeletonize_tiles(this_nx, parameter):
    """tile_skeletonize: use tiled image skeletonization to simplify network
    with parameters passed as dict key pairs

    args:
      parameter
        buffer:      line buffer distance [m]
        length:      square edge size [m]
        scale:       raster scale
        knot:        keep image knots
        segment:     segment lines

    returns:
      None

    """
    radius = parameter["buffer"]
    side_length = parameter["length"]
    square = get_square(this_nx, side_length)
    tile = get_tile_extent(this_nx, square, radius)
    tile = tile.reset_index(drop=True)
    r = []
    n = tile["id"].max()
    for i, j in tile.groupby("id"):
        print(f"{str(i).zfill(4)}\t{str(n).zfill(4)}")
        v = skeletonize_frame(j["geometry"], parameter)
        v = v[~v.is_empty]
        if v.empty:
            continue
        try:
            v = clip_geometry(square[i].bounds, geometry=v.union_all()).geoms
        except AttributeError:
            v = clip_geometry(square[i].bounds, geometry=v.union_all())
        v = gp.GeoSeries(v, crs=CRS).to_frame("geometry")
        v["id"] = i
        r.append(v)
    r = pd.concat(r)
    s = get_gap_fill(r, square, radius)
    r = pd.concat([r, s])
    r = combine_line(r["geometry"]).to_frame("geometry")
    tolerance = parameter["simplify"]
    r["geometry"] = r.simplify(tolerance)
    return r


def main():
    """main: load GeoJSON file, use tiled skeletonize buffer to simplify network,
    and output simplified and primal network as GeoPKG layers

    args:
      parameter
        inpath:      filepath to input GeoJSON file
        outpath:     filepath to output GeoPKG file
        simplify:    tolerance [m]
        buffer:      line buffer distance [m]
        length:      square edge size [m]
        scale:       raster scale
        knot:        keep image knots
        segment:     segment lines

    returns:
      None

    """
    log("start\t")
    parameter = get_args()
    base_nx = get_base_geojson(parameter["inpath"])
    log("read geojson")
    outpath = parameter["outpath"]
    write_dataframe(base_nx, outpath, layer="input")
    log("process\t")
    nx_line = skeletonize_tiles(base_nx, parameter)
    log("write simple")
    write_dataframe(nx_line, outpath, "line")
    log("write primal")
    mx_line = get_primal(nx_line["geometry"]).to_frame("geometry")
    write_dataframe(mx_line, outpath, "primal")
    log("stop\t")


if __name__ == "__main__":
    main()

# parameter = {
#     "inpath": "data/rnet_princes_street.geojson",
#     "outpath": "squarex.gpkg",
#     "side_length": 2000.0,
#     "tolerance": 0.0,
#     "buffer": 8.0,
#     "scale": 1.0,
#     "knot": False,
#     "segment": True,
# }
