#!/usr/bin/env python3
"""Independent verification of the London-scale claims in the exploration notes."""
import os
import time

import geopandas as gp
import numpy as np
from shapely import line_merge, unary_union

GPKG = "/mnt/secondary/home/robin/github/openroads/openroads/Greater_London.gpkg"
OUT = "/home/robin/.hermes/cache/scratch/parenx-bench"
os.makedirs(OUT, exist_ok=True)

src_bytes = os.path.getsize(GPKG)
print(f"source gpkg = {src_bytes/1048576:.1f} MB")
g = gp.read_file(GPKG, layer=None)
print("columns:", list(g.columns))
print("layers available:", end=" ")
import pyogrio

print([x[0] for x in pyogrio.list_layers(GPKG)])
print("n =", len(g), "crs =", g.crs, "types =", sorted(set(g.geom_type)))

g = g.to_crs(27700)
cls = [c for c in g.columns if "class" in c.lower()]
print("class-like columns:", cls)
if cls:
    print(g[cls[0]].value_counts().head(12).to_string())

verts = int(sum(len(x.coords) for x in g.geometry if x is not None))
km = g.length.sum() / 1000
b = g.total_bounds
print(f"links={len(g)} vertices={verts} length={km:,.0f} km")
print(f"bbox {b[2]-b[0]:.0f} x {b[3]-b[1]:.0f} m = {(b[2]-b[0])*(b[3]-b[1])/1e6:.0f} km2")
print(f"raster at 1 px/m = {(b[2]-b[0])*(b[3]-b[1])/1e6:.0f} Mpx")

# connected components via exact endpoint hashing
from shapely import get_coordinates

import networkx as nx

ends = []
for geom in g.geometry:
    if geom is None or geom.is_empty:
        continue
    c = get_coordinates(geom)
    ends.append((tuple(np.round(c[0], 1)), tuple(np.round(c[-1], 1))))
G = nx.Graph()
G.add_edges_from(ends)
print("components =", nx.number_connected_components(G))

if cls:
    col = cls[0]
    sel = g[g[col].astype(str).str.contains("Motorway|^A Road", case=False, na=False)]
    if sel.empty:
        sel = g[g[col].astype(str).str.contains("Motorway|A Road", case=False, na=False)]
    print(f"\nselected M+A: n={len(sel)} km={sel.length.sum()/1000:,.0f}")
    t0 = time.process_time()
    m = line_merge(unary_union(sel.geometry.values))
    m = gp.GeoSeries(list(getattr(m, "geoms", [m])), crs=27700)
    t1 = time.process_time()
    print(f"line_merge+union: n={len(m)} km={m.length.sum()/1000:,.0f} cpu={t1-t0:.2f}s")
    for tol in (0.0, 10.0, 25.0):
        s = m.simplify(tol) if tol > 0 else m
        v = int(sum(len(x.coords) for x in s if x is not None))
        p = f"{OUT}/lon_ma_s{tol:.0f}.gpkg"
        if os.path.exists(p):
            os.remove(p)
        gp.GeoDataFrame({"geometry": s}).to_file(p, driver="GPKG", layer="line")
        sz = os.path.getsize(p)
        print(
            f"  M+A simplify={tol:5.1f}m: links={len(s):6d} vertices={v:8d} "
            f"km={s.length.sum()/1000:7.0f} gpkg={sz/1024:8.0f} KB "
            f"({100*sz/src_bytes:5.2f}% of source) verts={100*v/verts:5.2f}%"
        )
