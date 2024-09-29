#!/usr/bin/env bash

if [ ! -d venv ]; then
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install --upgrade wheel
    pip install parenx
fi

source venv/bin/activate

LIBPATH=$(find . -name data | fgrep parenx | head -1)
INPATH=${1:-"${LIBPATH}/rnet_princes_street.geojson"}
OUTPUT=${2:-"output"}

echo simplify ${INPATH}

if [ ! -d archive ]; then
    mkdir archive
fi

for k in sk sk-tile vr
do
    if [ -s ${k}-${OUTPUT}.gpkg ]; then
        mv ${k}-${OUTPUT}.gpkg archive
    fi
    if [ -s ${k}-${OUTPUT}.geojson ]; then
        mv ${k}-${OUTPUT}.geojson archive
    fi
done

echo skeletonize ${INPATH}
skeletonize.py ${INPATH} sk-${OUTPUT}.gpkg
skeletonize.py ${INPATH} sk-${OUTPUT}-simple.gpkg --simplify 1.0
skeletonize.py ${INPATH} sk-${OUTPUT}-segment.gpkg --segment
echo tile skeletonize ${INPATH}
tile_skeletonize.py ${INPATH} sk-tile-${OUTPUT}.gpkg --buffer 8.0
tile_skeletonize.py ${INPATH} sk-tile-${OUTPUT}-simple.gpkg --buffer 8.0 --simplify 1.0
tile_skeletonize.py ${INPATH} sk-tile-${OUTPUT}-segment.gpkg --buffer 8.0 --segment

echo voronoi ${INPATH}
voronoi.py ${INPATH} vr-${OUTPUT}.gpkg
voronoi.py ${INPATH} vr-${OUTPUT}-simple.gpkg --simplify 1.0
OGR2OGR=$(which ogr2ogr)

if [ x"${OGR2OGR}" != x ]; then
    for k in sk sk-tile vr
    do
        rm -f ${k}-${OUTPUT}.geojson
        ogr2ogr -f GeoJSON ${k}-${OUTPUT}.geojson ${k}-${OUTPUT}.gpkg line
        sed -i 's/00000[0-9]*//g' ${k}-${OUTPUT}.geojson
    done
    for k in sk sk-tile vr
    do
        rm -f ${k}-${OUTPUT}-simple.geojson
        ogr2ogr -f GeoJSON ${k}-${OUTPUT}-simple.geojson ${k}-${OUTPUT}-simple.gpkg line
        sed -i 's/00000[0-9]*//g' ${k}-${OUTPUT}-simple.geojson
    done
fi
