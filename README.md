# parenx

Simplify (or "[pare](https://dictionary.cambridge.org/dictionary/english/pare)") a GeoJSON network ("nx") using raster image skeletonization an Voronoi polygons

Provides functions that use image skeletonization or Voronoi polygons to simplify geographic networks composed of linestrings. The outputs are geographic layers representing simplified or 'primal' representations of the network. Primal networks only contains straight line segments

Sample datasets include:

- Princes Street in Edinburgh, in [data/rnet_princes_street.geojson](https://github.com/anisotropi4/parenx/blob/main/data/rnet_princes_street.geojson)
- Rail lines in Doncaster, in [data/rnet_doncaster_rail.geojson](https://github.com/anisotropi4/parenx/blob/main/data/rnet_doncaster_rail.geojson)
<!-- Todo: add more -->

## Installation

Install the package into an activated `python` virtual environment with the following command:

```bash
pip install parenx
```

Install the latest development version from GitHub with the following command:

```bash
pip install git+https://github.com/anisotropi4/parenx.git
```

This places the `skeletonization.py` and `voronoi.py` scripts into the executable search path.

Test to see if the package is installed with the following command:

```bash
python -c "import parenx; print(parenx.__version__)"
```

## Examples

A `bash` helper script `run.sh` and example data is available under the `sitepackage` project directory under `venv`. The exact path varies with module and `python` version

### Skeletonization
The following creates a simplified network by applying skeletonization to a buffered raster array in `output.gpkg`
<!--     
    (venv) $ ./skeletonize.py data/rnet_princes_street.geojson
    -->

```bash
# Download the data if not already present
if [ ! -f ./data/rnet_princes_street.geojson ]; then
    wget https://raw.githubusercontent.com/anisotropi4/parenx/main/data/rnet_princes_street.geojson
    # Create data folder if not already present
    if [ ! -d ./data ]; then
        mkdir ./data
    fi
    mv rnet_princes_street.geojson ./data
fi
```

```bash
skeletonize.py ./data/rnet_princes_street.geojson rnet_princes_street_skeletonized.gpkg
```


```bash
tile_skeletonize.py ./data/rnet_princes_street.geojson rnet_princes_street_skeletonized_tile.gpkg
```

### Voronoi
The following creates a simplified network by creating set of Voronoi polygons from points on the buffer in `output.gpkg`
<!--    
    (venv) $ ./voronoi.py data/rnet_princes_street.geojson -->

```bash
voronoi.py ./data/rnet_princes_street.geojson rnet_princes_street_voronoi.gpkg
```

### Simple operation
The `run.sh` script sets a python virtual environment and executes the script against a data file in the `data` directory

    $ ./run.sh

The `run.sh` script optionally takes a filename and file-extension. To simplify a file, say `somewhere.geojson` and output to `GeoPKG` files `sk-simple.gpkg` and `vr-simple.gpkg`
    
    $ ./run.sh somewhere.geojon simple

### Locating the `run.sh` script
To copy the `run.sh` script into your local directory the following could help

    $ find . -name run.sh -exec cp {} . \;


## Application Programming Interface (API) Example

The `skeletonize_frame`, `voronoi_frame`, `primal_frame` and `tile_skeletonize_frame` functions are exposed via a simple API.

```python
#!/usr/bin/env python3

import geopandas as gp
from parenx import skeletonize_frame, voronoi_frame, skeletonize_tiles, get_primal

CRS = "EPSG:27700"
filepath = "data/rnet_princes_street.geojson"
frame = gp.read_file(filepath).to_crs(CRS)

parameter = {"simplify": 0.0, "buffer": 8.0, "scale": 1.0, "knot": False, "segment": False}
r = skeletonize_frame(frame["geometry"], parameter)

parameter = {"simplify": 0.0, "scale": 5.0, "buffer": 8.0, "tolerance": 1.0}
r = voronoi_frame(frame["geometry"], parameter)

primal = get_primal(r)
```

## Notes
Both are the skeletonization and Voronoi approach are generic approaches, with the following known issues:

* This does not maintain a link between attributes and the simplified network
* This does not identify a subset of edges that need simplification
* The lines are a bit wobbly
* It is quite slow
