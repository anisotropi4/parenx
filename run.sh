#!/usr/bin/env bash

INPATH=${1:-"data/rnet_princes_street.geojson"}
OUTPUT=${2:-"output"}

echo simplify ${INPATH}

if [ ! -d venv ]; then
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install --upgrade wheel
    if [ -s requirements.txt ]; then
        pip install --upgrade -r requirements.txt | tee setup.txt
    fi
fi

if [ ! -d archive ]; then
    mkdir archive
fi
source venv/bin/activate

for k in sk vr
do
    if [ -s ${k}-${OUTPUT}.gpkg ]; then
        mv ${k}-${OUTPUT}.gpkg archive
    fi
    if [ -s ${k}-line.geojsone ]; then
        mv ${k}-line.geojson archive
    fi
done

echo skeletonize ${INPATH}
./skeletonize.py ${INPATH} sk-${OUTPUT}.gpkg
./skeletonize.py ${INPATH} sk-${OUTPUT}-simple.gpkg --simplify 1.0
./skeletonize.py ${INPATH} sk-${OUTPUT}-segment.gpkg --segment
echo voronoi ${INPATH}
./voronoi.py ${INPATH} vr-${OUTPUT}.gpkg
./voronoi.py ${INPATH} vr-${OUTPUT}-simple.gpkg --simplify 1.0
OGR2OGR=$(which ogr2ogr)

if [ x"${OGR2OGR}" != x ]; then
    for k in sk vr
    do
        rm -f ${k}-line.geojson
        ogr2ogr -f GeoJSON ${k}-line.geojson ${k}-output.gpkg line
        sed -i 's/00000[0-9]*//g' ${k}-line.geojson
    done
    for k in sk vr
    do
        rm -f ${k}-line-simple.geojson
        ogr2ogr -f GeoJSON ${k}-line-simple.geojson ${k}-output-simple.gpkg line
        sed -i 's/00000[0-9]*//g' ${k}-line-simple.geojson
    done

fi
