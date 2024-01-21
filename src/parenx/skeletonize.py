#!/usr/bin/env python
"""skeletonize.py: simplify GeoJSON network to GeoPKG layers using image skeletonization"""

import argparse
import warnings
from functools import partial

import geopandas as gp
import networkx as nx
import numpy as np
import pandas as pd
import rasterio as rio
import rasterio.features as rif
from pyogrio import write_dataframe
from shapely import line_interpolate_point, set_precision, snap, unary_union
from shapely.affinity import affine_transform
from shapely.geometry import LineString, MultiPoint, Point
from shapely.ops import split
from skimage.morphology import remove_small_holes, skeletonize

from .shared import (
    combine_line,
    get_base_geojson,
    get_geometry_buffer,
    get_nx,
    get_source_target,
    log,
)

TRANSFORM_ONE = np.asarray([0.0, 1.0, -1.0, 0.0, 1.0, 1.0])
EMPTY = LineString([])

pd.set_option("display.max_columns", None)
CRS = "EPSG:27700"


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
    parser.add_argument("--scale", help="raster scale", type=float, default=1.0)
    parser.add_argument("--knot", help="keep image knots", action="store_true")
    parser.add_argument("--segment", help="segment", action="store_true")
    args = parser.parse_args()
    return {
        "inpath": args.inpath,
        "outpath": args.outpath,
        "tolerance": args.simplify,
        "buffer": args.buffer,
        "scale": args.scale,
        "knot": args.knot,
        "segment": args.segment,
    }


def get_pxsize(bound, scale=1.0):
    """get_dimension: calculates scaled image size in px

      bound: boundary corner points
      scale: scaling factor (default = 1.0)

    returns:
      size in px

    """
    r = np.diff(bound.reshape(-1, 2), axis=0)
    r = np.ceil(r.reshape(-1))
    return (r[[1, 0]] * scale).astype(int)


def get_affine_transform(this_gf, scale=1.0):
    """get_affine_transform: return affine transformations matrices, and scaled image size
    from GeoPandas boundary size

      this_gf: GeoPanda
      scale:  (default = 1.0)

    returns:
      rasterio and shapely affine tranformation matrices, and image size in px

    """
    bound = this_gf.total_bounds
    s = TRANSFORM_ONE / scale
    s[[4, 5]] = bound[[0, 3]]
    r = s[[1, 0, 4, 3, 2, 5]]
    r = rio.Affine(*r)
    return r, s, get_pxsize(bound, scale)


def get_raster_point(raster, value=1):
    """get_raster_point: return Point GeoSeries from raster array with values >= value

    args:
      raster: raster numpy array
      value: point threshold (default value = 1)
    returns:
      GeoSeries Point

    """
    r = np.stack(np.where(raster >= value))
    return gp.GeoSeries(map(Point, r.T), crs=CRS)


def sx_to_nx(this_gf, transform, simplify=0.0):
    """sx_to_nx: transform GeoPandas data from raster to projected coordinates

    args:
      this_gf: GeoDataFrame raster coordinates
      transform: affine transform

    returns:
      GeoDataFrame in projected coordinates

    """
    r = this_gf.copy()
    try:
        r = r.to_frame("geometry")
    except AttributeError:
        pass
    geometry = r["geometry"].map(transform).map(set_precision_pointone)
    if simplify > 0.0:
        geometry = geometry.simplify(simplify)
    r["geometry"] = geometry
    return r


def get_skeleton(geometry, transform, shape):
    """get_skeleton: return skeletonized raster buffer from Shapely geometry

    args:
      geometry: Shapely geometry to convert to raster buffer
      transform: rasterio affine transformation
      shape: output buffer px size

    returns:
      skeltonized numpy array raster buffer

    """
    r = rif.rasterize(geometry.values, transform=transform, out_shape=shape)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # parent, traverse = max_tree(invert(r))
        r = remove_small_holes(r, 4).astype(np.uint8)
    return skeletonize(r).astype(np.uint8)


def get_connected_class(edge_list):
    """get_connected_class: return labeled connected node pandas Series from edge list

    args:
      edge_list: source, target edge pandas DataFrame

    returns:
      labeled node pandas Series

    """
    nx_graph = nx.from_pandas_edgelist(edge_list)
    connected = nx.connected_components(nx_graph)
    r = {k: i for i, j in enumerate(connected) for k in j}
    return pd.Series(r, name="class")


def get_centre_edge(node):
    """get_centre_edge: return centroid Point from discrete node clusters

    args:
      node: discrete node cluster GeoDataSeries

    returns:
      GeoDataCentre node cluster centroid Point

    """
    centre = node[["geometry", "class"]].groupby("class").aggregate(tuple)
    centre = gp.GeoSeries(centre["geometry"].map(MultiPoint), crs=CRS).centroid
    centre = centre.rename("target")
    geometry = node[["class", "geometry"]].set_index("class").join(centre)
    geometry = geometry.apply(LineString, axis=1)
    r = node.rename(columns={"node": "source"}).copy()
    r["geometry"] = geometry.values
    return r


def get_raster_line(point, knot=False):
    """get_raster_line: return LineString GeoSeries from 1px line raster eliminating knots

    args:
      point: 1px raster array with knots

    returns:
      1px line LineString GeoSeries with knots removed

    """
    square = point.buffer(1, cap_style="square", mitre_limit=1)
    ix = point.sindex.query(square, predicate="covers").T
    ix = np.sort(ix)
    s = pd.DataFrame(ix).drop_duplicates().reset_index(drop=True)
    s = s.loc[np.where(s[0] != s[1])]
    s = np.stack([point[s[0].values], point[s[1].values]]).T
    r = gp.GeoSeries(map(LineString, s), crs=CRS)
    if r.empty:
        return gp.GeoSeries(LineString([]))
    edge, node = get_source_target(combine_line(r).to_frame("geometry"))
    if knot:
        return combine_line(edge["geometry"])
    ix = edge.length > 2.0
    connected = get_connected_class(edge.loc[~ix, ["source", "target"]])
    if connected.empty:
        return edge.loc[ix, "geometry"]
    node = node.loc[connected.index].join(connected).sort_index()
    connected_edge = get_centre_edge(node)
    r = combine_line(pd.concat([connected_edge["geometry"], edge.loc[ix, "geometry"]]))
    return r[r.length > 2.0]


def get_split(line, point, separation=1.0e-6):
    """get_split:"""
    return list(split(snap(line, point, separation), point).geoms)


def split_centres(line, offset):
    """split_centres:"""
    if line.length <= 2.0 * offset:
        return EMPTY
    p = line_interpolate_point(line, offset)
    _, centre = get_split(line, p)
    p = line_interpolate_point(centre, -offset)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        centre, _ = get_split(centre, p)
    return centre


def get_segment_buffer(this_gf, radius):
    """get_segment:"""
    r = this_gf.to_frame("geometry")
    r = gp.GeoSeries(unary_union(r).geoms, crs=CRS).to_frame("geometry")
    split_centre = partial(split_centres, offset=np.sqrt(1.5) * radius)
    s = gp.GeoSeries(this_gf.map(split_centre), crs=CRS)
    s = s.buffer(radius, 0, join_style="round", cap_style="round")
    s = gp.GeoSeries(unary_union(s.values).geoms, crs=CRS)
    i, j = this_gf.sindex.query(s, predicate="intersects")
    r["class"] = -1
    r.loc[j, "class"] = s.index[i]
    count = r.groupby("class").count()
    r = r.join(count["geometry"].rename("count"), on="class")
    ix = r["class"] == -1
    r.loc[ix, "count"] = 0
    ix = r["count"].isin([0, 1])
    p = this_gf[~ix]
    p = gp.GeoSeries(unary_union(p).geoms, crs=CRS)
    p = p.buffer(radius, join_style="round", cap_style="round")
    try:
        p = gp.GeoSeries(unary_union(p.values).geoms, crs=CRS)
    except AttributeError:
        p = gp.GeoSeries(unary_union(p.values), crs=CRS)
    q = this_gf[ix].buffer(0.612, 64, join_style="mitre", cap_style="round")
    r = pd.concat([p, q])
    return r


def skeletonize_frame(this_gs, parameter):
    """skeltonize_frame:"""
    radius = parameter["buffer"]
    scale = parameter["scale"]
    if parameter["segment"]:
        nx_geometry = get_segment_buffer(this_gs, radius=radius)
    else:
        nx_geometry = get_geometry_buffer(this_gs, radius=radius)
    r_matrix, s_matrix, out_shape = get_affine_transform(nx_geometry, scale)
    shapely_transform = partial(affine_transform, matrix=s_matrix)
    skeleton_im = get_skeleton(nx_geometry, r_matrix, out_shape)
    nx_point = get_raster_point(skeleton_im)
    sx_line = get_raster_line(nx_point, parameter["knot"])
    tolerance = parameter["tolerance"]
    return sx_to_nx(sx_line, shapely_transform, simplify=tolerance)


set_precision_pointone = partial(set_precision, grid_size=0.1)


def main():
    """main: load GeoJSON file, use skeletonize buffer to simplify network, and output
    input, simplified and primal network as GeoPKG layers

    args:
       path: GeoJSON filepath

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
    nx_line = skeletonize_frame(base_nx["geometry"], parameter)
    log("write simple")
    write_dataframe(nx_line, outpath, "line")
    log("write primal")
    mx_line = get_nx(nx_line["geometry"]).to_frame("geometry")
    write_dataframe(mx_line, outpath, "primal")
    log("stop\t")


if __name__ == "__main__":
    main()
