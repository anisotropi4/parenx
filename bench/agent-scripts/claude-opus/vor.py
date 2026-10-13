import sys,time,warnings; warnings.filterwarnings("ignore")
from parenx import voronoi_frame
from parenx.shared import get_base_geojson
g=get_base_geojson(sys.argv[1]).geometry
p={"simplify":0.0,"buffer":8.0,"scale":5.0,"tolerance":1.0}
w=time.perf_counter();c=time.process_time()
r=voronoi_frame(g,p)
print("voronoi wall",round(time.perf_counter()-w,2),"cpu",round(time.process_time()-c,2),"out lines",len(r),"vertices",sum(len(x.coords) for x in r.geometry),"km",round(r.length.sum()/1000,1))
