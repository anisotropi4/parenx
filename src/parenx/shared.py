"""share.py: common skeletonize and voronoi functions"""

import datetime as dt
from functools import partial

import geopandas as gp
import numpy as np
import pandas as pd
from shapely import get_coordinates, line_merge, set_precision, unary_union
from shapely.geometry import LineString, MultiLineString, Point

START = dt.datetime.now()
CRS = "EPSG:27700"

set_precision_pointone = partial(set_precision, grid_size=0.1)


def combine_line(line):
    """combine_line: return LineString GeoSeries combining lines with intersecting endpoints

    args:
      line: mixed LineString GeoSeries

    returns:
      join LineString GeoSeries

    """
    r = MultiLineString(line.values)
    try:
        return gp.GeoSeries(line_merge(r).geoms, crs=CRS)
    except AttributeError:
        return gp.GeoSeries(line_merge(r), crs=CRS)


def get_base_geojson(filepath):
    """get_base_nx: return GeoDataFrame at 0.1m precision from GeoJSON

    args:
      filepath: GeoJSON path

    returns:
      GeoDataFrame at 0.1m precision

    """
    r = gp.read_file(filepath).to_crs(CRS)
    r["geometry"] = r["geometry"].map(set_precision_pointone)
    return r


def get_end(geometry):
    """get_end: return numpy array of geometry LineString end-points

    args:
      geometry: geometry LineString

    returns:
      end-point numpy arrays

    """
    r = get_coordinates(geometry)
    return np.vstack((r[0, :], r[-1, :]))


def get_geometry_buffer(this_gf, radius=8.0):
    """get_geometry_buffer: return radius buffered GeoDataFrame

    args:
      this_gf: GeoDataFrame to
      radius: (default value = 8.0)

    returns:
      buffered GeoSeries geometry

    """
    try:
        r = gp.GeoSeries(unary_union(this_gf).geoms, crs=CRS)
    except AttributeError:
        r = gp.GeoSeries(this_gf, crs=CRS)
    r = gp.GeoSeries(r, crs=CRS).buffer(radius, join_style="mitre")
    union = unary_union(r)
    try:
        r = gp.GeoSeries(union.geoms, crs=CRS)
    except AttributeError:
        r = gp.GeoSeries(union, crs=CRS)
    return r


def get_primal(line):
    """get_primal: return primal edge network from LineString GeoDataFrame

    args:
      line: LineString GeoDataFrame

    returns:
      edge GeoDataFrames

    """
    r = line.map(get_end)
    edge = gp.GeoSeries(r.map(LineString), crs=CRS)
    r = np.vstack(r.to_numpy())
    r = gp.GeoSeries(map(Point, r)).to_frame("geometry")
    r = r.groupby(r.columns.to_list(), as_index=False).size()
    return edge


def get_source_target(line):
    """get_source_target: return edge and node GeoDataFrames from LineString with unique
    node Point and edge source and target

    args:
      line: LineString GeoDataFrame

    returns:
      edge, node: GeoDataFrames

    """
    r = line.map(get_coordinates).explode()
    ix = r.index.duplicated(keep="last") & r.index.duplicated(keep="first")
    r = gp.points_from_xy(*np.stack(r[~ix]).reshape(-1, 2).T)
    node = pd.Series(r).to_frame("geometry")
    node = node.groupby("geometry").size().rename("count").reset_index()
    node["node"] = node.index
    node = gp.GeoDataFrame(node, crs=CRS)

    edge = line.copy()
    try:
        edge = edge.to_frame("geometry")
    except AttributeError:
        pass
    edge = edge.rename_axis("edge").reset_index()

    r = np.asarray(r).reshape(-1, 2)
    i, j = node["geometry"].sindex.nearest(r[:, 0], return_all=False)
    edge["source"] = -1
    edge.iloc[i, -1] = j

    i, j = node["geometry"].sindex.nearest(r[:, 1], return_all=False)
    edge["target"] = -1
    edge.iloc[i, -1] = j

    column = ["source", "target"]
    edge[column] = np.sort(edge[column], axis=1)
    edge = edge.drop_duplicates(subset=column).reset_index(drop=True)
    edge["edge"] = edge.index
    return edge, node


def log(this_string):
    """log: print timestamp appended to 'this_string'

      this_string: text to print

    returns:
      None

    """
    now = dt.datetime.now() - START
    print(f"{this_string }\t{now}")
