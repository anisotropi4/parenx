import pyogrio, time, shapely, os, numpy as np, geopandas as gp, networkx as nx, warnings; warnings.filterwarnings("ignore")
p="/mnt/secondary/home/robin/github/openroads/openroads/Greater_London.gpkg"
df=pyogrio.read_dataframe(p,columns=["road_classification","start_node","end_node"])
G=nx.from_pandas_edgelist(df,"start_node","end_node"); print("full comps",nx.number_connected_components(G))
for cls in [["Motorway","A Road"],["Motorway","A Road","B Road"]]:
    s=df[df.road_classification.isin(cls)]
    H=nx.from_pandas_edgelist(s,"start_node","end_node"); cc=sorted((len(c) for c in nx.connected_components(H)),reverse=True)
    w=time.perf_counter()
    m=shapely.line_merge(shapely.union_all(s.geometry.values))
    parts=list(m.geoms)
    for tol in [0,10,25]:
        g=gp.GeoDataFrame(geometry=shapely.simplify(np.array(parts),tol) if tol else np.array(parts),crs=27700)
        out=f"/mnt/secondary/tmp/parenx-opus/london_{len(cls)}_{tol}.gpkg"
        if os.path.exists(out): os.remove(out)
        pyogrio.write_dataframe(g,out)
        print(cls,"tol",tol,"edges",len(s),"merged",len(parts),"vertices",shapely.get_num_coordinates(g.geometry.values).sum(),"km",round(g.length.sum()/1000),"gpkg KB",os.path.getsize(out)//1024,"comps",len(cc),"largest share",round(cc[0]/sum(cc),3))
    print("merge+simplify s",round(time.perf_counter()-w,2))
print("orig gpkg KB", os.path.getsize(p)//1024)
