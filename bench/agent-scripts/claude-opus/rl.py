import sys,time,warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, geopandas as gp
from functools import partial
import parenx.skeletonize as sk
from parenx.shared import *
g=get_base_geojson(sys.argv[1]).geometry
buf=get_geometry_buffer(g,8.0); r_m,s_m,shape=sk.get_affine_transform(buf,1.0)
im=sk.get_skeleton(buf,r_m,shape,1.0); pt=sk.get_raster_point(im)
def t(n,f):
    w=time.perf_counter(); r=f(); print(f"{n:28s}{time.perf_counter()-w:7.3f}s"); return r
sq=t("square buffers",lambda: pt.buffer(1,cap_style="square",mitre_limit=1))
ix=t("sindex covers query",lambda: pt.sindex.query(sq,predicate="covers").T)
def mk():
    s=pd.DataFrame(np.sort(ix)).drop_duplicates().reset_index(drop=True); s=s.loc[np.where(s[0]!=s[1])]
    s=np.stack([pt[s[0].values],pt[s[1].values]]).T; return gp.GeoSeries(map(LineString,s),crs=CRS)
r=t("2-pt LineStrings",mk)
c=t("combine_line",lambda: combine_line(r))
e=t("get_source_target",lambda: get_source_target(c))
# numpy alternative: pixel adjacency via shifted array
def npadj():
    rr,cc=np.nonzero(im); idx=-np.ones(im.shape,np.int64); idx[rr,cc]=np.arange(len(rr))
    P=np.pad(idx,1,constant_values=-1); out=[]
    for dr,dc in [(0,1),(1,-1),(1,0),(1,1)]:
        nb=P[1+rr+dr,1+cc+dc]; m=nb>=0; out.append(np.c_[np.arange(len(rr))[m],nb[m]])
    return np.vstack(out)
a=t("numpy 8-neighbour edges",npadj)
print("pixels",len(pt),"shapely pairs",len(r),"numpy pairs",len(a))
