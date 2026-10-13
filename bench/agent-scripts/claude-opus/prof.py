import sys, time, resource
from functools import partial
import numpy as np, geopandas as gp
from shapely.affinity import affine_transform
import parenx.skeletonize as sk
from parenx.shared import get_base_geojson, get_geometry_buffer, get_source_target, combine_line
T={}
def t(name, f, *a, **k):
    w=time.perf_counter(); c=time.process_time(); r=f(*a, **k)
    T[name]=(time.perf_counter()-w, time.process_time()-c); print(f"{name:20s} wall {T[name][0]:7.2f}s cpu {T[name][1]:7.2f}s", flush=True); return r
path=sys.argv[1]; scale=float(sys.argv[2]) if len(sys.argv)>2 else 1.0
gf=t("read", get_base_geojson, path)
g=gf.geometry
nv=sum(len(x.coords) for x in g)
print("lines",len(g),"vertices",nv,"length_km",round(g.length.sum()/1000,1))
buf=t("buffer_union", get_geometry_buffer, g, radius=8.0)
r_m, s_m, shape = sk.get_affine_transform(buf, scale)
print("raster shape", shape, "Mpx", shape[0]*shape[1]/1e6)
import rasterio.features as rif, warnings
from skimage.morphology import remove_small_holes, skeletonize
im=t("rasterize", rif.rasterize, buf.values, transform=r_m, out_shape=shape)
print("fg px", int(im.sum()), "fill", round(im.mean(),4))
im2=t("remove_holes", lambda: remove_small_holes(im.astype(bool), 16*scale).astype(np.uint8))
sk_im=t("skeletonize", lambda: skeletonize(im2).astype(np.uint8))
print("skeleton px", int(sk_im.sum()))
pt=t("raster_point", sk.get_raster_point, sk_im)
line=t("raster_line", sk.get_raster_line, pt, False)
out=t("sx_to_nx", sk.sx_to_nx, line, partial(affine_transform, matrix=s_m))
print("out lines", len(out), "out vertices", sum(len(x.coords) for x in out.geometry), "out km", round(out.length.sum()/1000,1))
print("maxrss MB", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024)
