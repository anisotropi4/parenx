#!/usr/bin/env python
"""skeletonize.py: simplify GeoJSON network to GeoPKG layers using image skeletonization"""

import argparse
import warnings
from functools import partial

import geopandas as gp
import numpy as np
import pandas as pd
import rasterio as rio
import rasterio.features as rif
from pyogrio import write_dataframe
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from shapely import (
    clip_by_rect,
    STRtree,
    equals,
    equals_exact,
    get_coordinates,
    get_num_coordinates,
    intersection,
    is_closed,
    is_empty,
    is_simple,
    line_interpolate_point,
    line_merge,
    simplify,
    snap,
)
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point
from shapely.ops import split
from skimage.morphology import remove_small_holes, skeletonize

from parenx.shared import (
    CRS,
    combine_line,
    get_base_geojson,
    get_geometry_buffer,
    get_primal,
    get_source_target,
    log,
    set_precision_pointone,
)

TRANSFORM_ONE = np.asarray([0.0, 1.0, -1.0, 0.0, 1.0, 1.0])
EMPTY = LineString([])

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
    parser.add_argument(
        "--simplify",
        help="line simplify tolerance [m], default one pixel 1 / scale, 0 for none."
        " Knots and short spurs are still removed unless --knot is set",
        type=float,
        default=None,
    )
    parser.add_argument("--buffer", help="line buffer [m]", type=float, default=8.0)
    parser.add_argument("--scale", help="raster scale", type=float, default=1.0)
    parser.add_argument(
        "--knot",
        help="keep image knots and short spurs, with --simplify 0 the output is lossless",
        action="store_true",
    )
    parser.add_argument("--segment", help="segment", action="store_true")
    return vars(parser.parse_args())


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

    legacy: no longer used by skeletonize_frame, kept so bench/simplify_check.py
    can time and compare the previous pixel-to-vector stage

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

    legacy: no longer used by skeletonize_frame, kept so bench/simplify_check.py
    can time and compare the previous pixel-to-vector stage

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


def get_raster(geometry, transform, shape, height=256):
    """get_raster: return raster buffer of polygons, rasterised in strips of rows.
    GDAL scans every polygon edge on every row of the polygon bounding box, so one
    large network polygon costs rows x edges. Strip edges lie on pixel edges, so the
    result is the same as rasterising the whole polygon at once

    args:
      geometry: Shapely polygon array
      transform: rasterio affine transformation
      shape: output buffer px size
      height: strip height [px] (default = 256)

    returns:
      numpy array raster buffer

    """
    r = np.zeros(shape, dtype=np.uint8)
    xmin = transform.c - abs(transform.a)
    xmax = transform.c + (shape[1] + 1) * abs(transform.a)
    for i in range(0, shape[0], height):
        j = min(i + height, shape[0])
        y0, y1 = sorted([transform.f + i * transform.e, transform.f + j * transform.e])
        part = clip_by_rect(geometry, xmin, y0, xmax, y1)
        part = part[~is_empty(part)]
        if len(part) == 0:
            continue
        offset = transform * rio.Affine.translation(0, i)
        r[i:j] = rif.rasterize(part, transform=offset, out_shape=(j - i, shape[1]))
    return r


def get_skeleton(geometry, transform, shape, scale):
    """get_skeleton: return skeletonized raster buffer from Shapely geometry

    args:
      geometry: Shapely geometry to convert to raster buffer
      transform: rasterio affine transformation
      shape: output buffer px size

    returns:
      skeltonized numpy array raster buffer

    """
    r = get_raster(np.asarray(geometry.values), transform, shape)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # parent, traverse = max_tree(invert(r))
        r = remove_small_holes(r, 16 * scale).astype(np.uint8)
    return skeletonize(r).astype(np.uint8)


def get_connected_class(edge_list):
    """get_connected_class: return labeled connected node pandas Series from edge list

    legacy: only called by get_raster_line. networkx is imported here so the
    current pipeline does not need it

    args:
      edge_list: source, target edge pandas DataFrame

    returns:
      labeled node pandas Series

    """
    import networkx as nx

    nx_graph = nx.from_pandas_edgelist(edge_list)
    connected = nx.connected_components(nx_graph)
    r = {k: i for i, j in enumerate(connected) for k in j}
    return pd.Series(r, name="class")


def get_centre_edge(node):
    """get_centre_edge: return centroid Point from discrete node clusters

    legacy: only called by get_raster_line

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

    legacy: no longer used by skeletonize_frame, kept so bench/simplify_check.py
    can time and compare the previous pixel-to-vector stage

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
        return gp.GeoSeries(EMPTY, crs=CRS)
    edge, node = get_source_target(combine_line(r))
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


def get_segment_buffer(this_gs, radius):
    """get_segment:"""
    r = this_gs.to_frame("geometry")
    try:
        r = gp.GeoSeries(this_gs.unary_union.geoms, crs=CRS)
    except AttributeError:
        r = gp.GeoSeries(this_gs, crs=CRS)
    r = r.to_frame("geometry").reset_index(drop=True)
    split_centre = partial(split_centres, offset=np.sqrt(1.5) * radius)
    s = gp.GeoSeries(this_gs.map(split_centre), crs=CRS)
    if s.is_empty.all():
        return r.buffer(0.612, 64, join_style="mitre", cap_style="round")
    s = s.buffer(radius, 0, join_style="round", cap_style="round")
    ix = s.is_empty.values
    s = s[~ix].reset_index(drop=True)
    try:
        s = gp.GeoSeries(s.unary_union.geoms, crs=CRS)
    except AttributeError:
        s = gp.GeoSeries(s.unary_union, crs=CRS)
    i, j = r.sindex.query(s, predicate="intersects")
    r["class"] = -1
    r.loc[j, "class"] = s.index[i]
    count = r.groupby("class").count()
    r = r.join(count["geometry"].rename("count"), on="class")
    ix = r["class"] == -1
    r.loc[ix, "count"] = 0
    ix = r["count"].isin([0, 1]).values
    p = r[~ix]
    q = r[ix].buffer(0.612, 64, join_style="mitre", cap_style="round")
    if p.is_empty.all():
        return q
    p = gp.GeoSeries(p.unary_union.geoms, crs=CRS)
    p = p.buffer(radius, join_style="round", cap_style="round")
    try:
        p = gp.GeoSeries(p.unary_union.geoms, crs=CRS)
    except AttributeError:
        p = gp.GeoSeries(p.unary_union, crs=CRS)
    r = pd.concat([p, q])
    return r


NEIGHBOUR = ((0, 1), (1, 0), (1, 1), (1, -1))


def get_default_simplify(scale=1.0):
    """get_default_simplify: return the default simplify tolerance [m], one raster
    pixel, 1 / scale. The skeleton of an even-width band can sit on either of two
    centre pixels, so its position is only known to one pixel. This removes the
    one-pixel staircase jogs and gives a vertex density close to OS Open Roads

    args:
      scale: raster scale (default = 1.0)

    returns:
      tolerance [m]

    """
    return 1.0 / scale


def get_tolerance(parameter):
    """get_tolerance: return simplify tolerance [m] from parameter dict, where a
    missing or None "simplify" value means one pixel and 0.0 means none

    args:
      parameter: parameter dict

    returns:
      tolerance [m]

    """
    r = parameter.get("simplify")
    if r is None:
        return get_default_simplify(parameter.get("scale", 1.0))
    return float(r)


def get_pixel_index(pixel, key):
    """get_pixel_index: return index of key in sorted flat pixel id array, or -1

    args:
      pixel: sorted flat pixel id numpy array
      key: flat pixel ids to find

    returns:
      index numpy array, -1 where key is not a pixel

    """
    if len(pixel) == 0:
        return np.full(len(key), -1)
    r = np.minimum(np.searchsorted(pixel, key), len(pixel) - 1)
    return np.where(pixel[r] == key, r, -1)


def get_pixel_edge(raster):
    """get_pixel_edge: return foreground pixel coordinates and undirected 8-neighbour
    pixel pairs, found by four array shifts over the sorted flat pixel ids

    args:
      raster: 1px line raster numpy array

    returns:
      (row, col) pixel numpy array, source and target pixel index numpy arrays

    """
    row, col = np.nonzero(raster)
    width = raster.shape[1]
    pixel = row.astype(np.int64) * width + col
    source, target = [], []
    for dr, dc in NEIGHBOUR:
        ix = (col + dc >= 0) & (col + dc < width)
        j = get_pixel_index(pixel, pixel + dr * width + dc)
        ix &= j >= 0
        source.append(np.flatnonzero(ix))
        target.append(j[ix])
    return np.stack([row, col], axis=1), np.concatenate(source), np.concatenate(target)


def get_pixel_chain(n, source, target):
    """get_pixel_chain: contract degree-2 pixel chains into pixel index paths that
    start and end at junction or end pixels, with closed paths for isolated cycles

    args:
      n: pixel count
      source, target: undirected pixel pair index numpy arrays

    returns:
      list of pixel index lists

    """
    m = len(source)
    u = np.concatenate([source, target])
    v = np.concatenate([target, source])
    edge = np.concatenate([np.arange(m), np.arange(m)])
    order = np.argsort(u, kind="stable")
    start = np.searchsorted(u[order], np.arange(n + 1)).tolist()
    v = v[order].tolist()
    edge = edge[order].tolist()
    degree = np.diff(start)
    two = (degree == 2).tolist()
    visited = [False] * m

    def walk(a, k):
        path = [a]
        e = edge[k]
        while True:
            visited[e] = True
            a = v[k]
            path.append(a)
            if not two[a]:
                return path
            k = start[a]
            if edge[k] == e:
                k += 1
            e = edge[k]
            if visited[e]:
                return path

    r = []
    for a in np.flatnonzero(degree != 2).tolist():
        for k in range(start[a], start[a + 1]):
            if not visited[edge[k]]:
                r.append(walk(a, k))
    for e in range(m):
        if visited[e]:
            continue
        a = int(source[e])
        k = start[a] if edge[start[a]] == e else start[a] + 1
        r.append(walk(a, k))
    return r


def get_line_length(line):
    """get_line_length: return length of coordinate array polyline"""
    return np.hypot(*np.diff(line, axis=0).T).sum()


def set_hub(chain, point, threshold=2.0):
    """set_hub: collapse each cluster of chains shorter than threshold px into a hub
    at the cluster junction pixel nearest the cluster centroid, or the nearest end
    pixel if it has no junction pixel, join each cluster pixel that a longer chain
    ends at to the hub with one straight spoke, and drop the short chains

    args:
      chain: list of pixel index lists
      point: (row, col) pixel numpy array
      threshold: knot chain length [px] (default = 2.0)

    returns:
      list of float (row, col) coordinate arrays

    """
    line = [point[i].astype(float) for i in chain]
    length = np.asarray([get_line_length(i) for i in line])
    short = length <= threshold
    end = np.asarray([[i[0], i[-1]] for i in chain]).reshape(-1, 2)
    if not short.any():
        return line
    node, ix = np.unique(end[short], return_inverse=True)
    ix = ix.reshape(-1, 2)
    graph = coo_matrix(
        (np.ones(len(ix)), (ix[:, 0], ix[:, 1])), shape=(len(node), len(node))
    )
    _, label = connected_components(graph, directed=False)
    count = np.bincount(label)
    centre = np.stack(
        [np.bincount(label, point[node, k]) / count for k in range(2)], axis=1
    )
    # snap each hub to the cluster junction pixel, degree 3 or more, nearest the
    # centroid, or to the nearest cluster end pixel if the cluster has no junction
    # pixel. The hub is then a pixel of the cluster, not a free point, but in the
    # second case it may be a degree-1 leaf pixel. A spoke is not ruled out from
    # crossing another line by construction, and simplify_network will not catch
    # that, as it only compares against its input, which already holds the spokes
    distance = np.hypot(*(point[node] - centre[label]).T)
    # each pixel pair is in exactly one chain, so chain end counts are pixel degrees
    degree = np.bincount(end.reshape(-1), minlength=len(point))
    order = np.lexsort((distance, degree[node] < 3, label))
    first = np.concatenate([[True], np.diff(label[order]) != 0])
    hub = point[node[order[first]]].astype(float)
    r = [line[i] for i in np.flatnonzero(~short)]
    ix = get_pixel_index(node, end[~short].reshape(-1))
    ix = np.unique(ix[ix >= 0])
    for i in ix:
        spoke = np.vstack([point[node[i]], hub[label[i]]])
        if (spoke[0] != spoke[1]).any():
            r.append(spoke)
    return r


def drop_spur(line, threshold=2.0):
    """drop_spur: drop lines no longer than threshold px that end at a degree-1 node,
    or start and end at the same node, as these are skeleton artefacts that do not
    change connectivity

    args:
      line: list of coordinate arrays
      threshold: spur length [px] (default = 2.0)

    returns:
      list of coordinate arrays

    """
    if not line:
        return line
    end = np.vstack([np.vstack([i[0], i[-1]]) for i in line])
    _, ix, count = np.unique(end, axis=0, return_inverse=True, return_counts=True)
    degree = count[ix.reshape(-1)].reshape(-1, 2)
    r = []
    for i, p in enumerate(line):
        if get_line_length(p) <= threshold:
            if (degree[i] == 1).any() or (p[0] == p[-1]).all():
                continue
        r.append(p)
    return r


def drop_collinear(line):
    """drop_collinear: drop interior vertices exactly on the straight line between
    their neighbours, which is lossless

    args:
      line: coordinate array

    returns:
      coordinate array

    """
    if len(line) < 3:
        return line
    d = np.diff(line, axis=0)
    cross = d[:-1, 0] * d[1:, 1] - d[:-1, 1] * d[1:, 0]
    dot = (d[:-1] * d[1:]).sum(axis=1)
    ix = np.concatenate([[True], (cross != 0) | (dot <= 0), [True]])
    return line[ix]


def merge_line(line):
    """merge_line: merge coordinate array lines through degree-2 nodes

    args:
      line: list of coordinate arrays

    returns:
      LineString numpy array
    """
    if not line:
        return np.asarray([], dtype=object)
    r = line_merge(MultiLineString([LineString(i) for i in line]))
    return np.asarray(getattr(r, "geoms", [r]))


def get_conflict(line, base):
    """get_conflict: return mask of lines that are not simple, are degenerate rings, or
    meet another line other than where their base lines meet. Candidate pairs are
    those that intersect in either line or base, so a meeting that simplification
    removes, say at an interior vertex, is found as well as a new one

    args:
      line: simplified LineString numpy array
      base: unsimplified LineString numpy array, base[i] is line[i] unsimplified

    returns:
      boolean numpy array
    """
    r = ~is_simple(line)
    r |= is_closed(base) & (get_num_coordinates(line) < 4)
    i, j = np.concatenate(
        [STRtree(k).query(k, predicate="intersects") for k in (line, base)], axis=1
    )
    ix = i < j
    i, j = np.unique(np.stack([i[ix], j[ix]], axis=1), axis=0).reshape(-1, 2).T
    ix = ~equals(intersection(line[i], line[j]), intersection(base[i], base[j]))
    r[i[ix]] = True
    r[j[ix]] = True
    return r


def simplify_network(geometry, tolerance):
    """simplify_network: simplify the interior of each line while keeping every line
    end-point exact, so junctions cannot move. Each line is simplified on its own,
    then any line that is no longer simple, or meets another line other than where
    the unsimplified lines meet, is put back unsimplified until none are left.
    Both lines of a conflicting pair are put back and are not simplified again, even
    if only one of them moved. This is conservative and keeps vertices, but few: on
    the Leeds 5 km test circle 11 of 28,955 lines are put back, which keeps 35 of
    113,043 vertices, and none are put back on the smaller test inputs

    args:
      geometry: LineString array-like
      tolerance: tolerance [m], 0.0 for none

    returns:
      LineString numpy array

    """
    base = np.asarray(geometry)
    if tolerance <= 0.0 or len(base) == 0:
        return base
    r = simplify(base, tolerance, preserve_topology=False)
    while True:
        ix = get_conflict(r, base) & ~equals_exact(r, base, 0.0)
        if not ix.any():
            return r
        r[ix] = base[ix]


def get_skeleton_line(skeleton_im, s_matrix, knot=False, tolerance=0.0):
    """get_skeleton_line: return simplified LineString network in projected coordinates
    from 1px line raster, using integer pixel arrays

    args:
      skeleton_im: 1px line raster numpy array
      s_matrix: shapely affine transformation matrix
      knot: keep image knots (default = False)
      tolerance: simplify tolerance [m] (default = 0.0)

    returns:
      LineString GeoSeries
    """
    point, source, target = get_pixel_edge(skeleton_im)
    chain = get_pixel_chain(len(point), source, target)
    if knot:
        line = [point[i].astype(float) for i in chain]
    else:
        line = set_hub(chain, point)
        line = drop_spur([get_coordinates(i) for i in merge_line(line)])
    line = [drop_collinear(get_coordinates(i)) for i in merge_line(line)]
    if not line:
        return gp.GeoSeries(EMPTY, crs=CRS)
    a, b, d, e, xoff, yoff = s_matrix
    line = [
        np.stack([a * i[:, 0] + b * i[:, 1] + xoff, d * i[:, 0] + e * i[:, 1] + yoff], 1)
        for i in line
    ]
    r = set_precision_pointone(np.asarray([LineString(i) for i in line]))
    r = r[~is_empty(r)]
    r = simplify_network(r, tolerance)
    return gp.GeoSeries(r, crs=CRS)


def skeletonize_frame(this_gs, parameter):
    """skeltonize_frame: use image skeletonization to simplify network with
    parameters passed as dict key pairs

    args:
      this_gs:     GeoSeries network to simplify
      parameter
        simplify:    line simplify tolerance [m], missing or None for the
                     default of one pixel, 1 / scale, and 0.0 for none. Line
                     end-points are kept exact and lines cannot cross, so the
                     network topology is unchanged by simplification. This
                     controls polyline simplification only
        buffer:      line buffer distance [m]
        scale:       raster scale
        knot:        keep image knots. If False, knots of short chains are
                     collapsed to a hub and short spurs are dropped whatever the
                     simplify value. simplify 0.0 with knot True is lossless
        segment:     segment lines

    returns:
      simplified network GeoDataFrame with a "geometry" column

    """
    radius = parameter["buffer"]
    scale = parameter["scale"]
    if parameter["segment"]:
        nx_geometry = get_segment_buffer(this_gs, radius=radius)
    else:
        nx_geometry = get_geometry_buffer(this_gs, radius=radius)
    r_matrix, s_matrix, out_shape = get_affine_transform(nx_geometry, scale)
    skeleton_im = get_skeleton(nx_geometry, r_matrix, out_shape, scale)
    tolerance = get_tolerance(parameter)
    r = get_skeleton_line(skeleton_im, s_matrix, parameter["knot"], tolerance)
    return r.to_frame("geometry")


def main():
    """main: load GeoJSON file, use skeletonize buffer to simplify network, and output
    simplified and primal network as GeoPKG layers

    args:
      parameter
        inpath:      filepath to input GeoJSON file
        outpath:     filepath to output GeoPKG file
        simplify:    tolerance [m]
        buffer:      line buffer distance [m]
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
    nx_line = skeletonize_frame(base_nx["geometry"], parameter)
    log("write simple")
    write_dataframe(nx_line, outpath, "line")
    log("write primal")
    mx_line = get_primal(nx_line["geometry"]).to_frame("geometry")
    write_dataframe(mx_line, outpath, "primal")
    log("stop\t")


if __name__ == "__main__":
    main()
