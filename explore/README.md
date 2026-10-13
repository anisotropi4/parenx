# Exploration record: issue #4

Provenance of everything in this directory.

Two independent explorations of the backend ran on 2026-10-13 against revision
`ad5bf6f4c761865b46c174c45dc7de849abb2dc1` (branch
`4-speed-up-and-improve-simplification`), from the same brief:

| file | agent | model |
|---|---|---|
| `OPTIONS-claude-opus.md` | Claude Code (headless) | `claude-opus-5-5` |
| `OPTIONS-copilot-astra.md` | GitHub Copilot CLI | `gpt-6-astra` |

Neither agent modified anything outside this directory. The notes are the agents'
own work and are reproduced unedited, including their estimates; the numbers the
parent agent could check were re-measured independently (below). Read the two
files as proposals, not as verified results.

## Reproducing the measurements

- `../bench/profile_stages.py <geojson> <scale>` - stage-by-stage wall and CPU
  time of the current `skeletonize` pipeline, with raster cell counts, skeleton
  pixel counts and peak RSS.
- `../bench/london_selection.py` - OS Open Roads Greater London hierarchy
  selection, line merge and simplification, reported as bytes against the source
  GeoPackage.
- `../bench/agent-scripts/claude-opus/` - the scripts the Claude exploration used.

Run them with the repo virtualenv, for example:

```bash
.venv/bin/python bench/profile_stages.py data/rnet_doncaster_rail.geojson 1.0
```

## Independently re-measured

Checked by the parent agent on this machine, not taken from the notes:

- Leeds (`data/osm_leeds.geojson`, 22,953 lines, 2,196 km, 144.2 Mpx raster at
  scale 1): 45.7 s to 108.9 s inside `skeletonize_frame` across runs, peak RSS
  3.8 GB, CPU 102-108 percent. The spread is host contention, not the code: the
  box was running `netassign`, `ogr2ogr` and several `rustc` jobs (load average
  10-14) during the runs. The algorithm itself is single-threaded.
- The simplified layer is heavier than its input: Princes Street 4,603 input
  vertices to 33,236 output vertices, 93 verts/km to 895 verts/km, because the
  skeleton is kept at one vertex per pixel and `simplify` defaults to 0.0.
  Leeds output GeoPackage is 51 MB against a 16 MB input. `--simplify 5` cuts
  Princes Street output to 3,720 vertices, so part of this is a default, not an
  algorithm, problem.
- `tile_skeletonize.py` fails on both bundled small datasets
  (`DataFrame.explode() missing 1 required positional argument: 'column'` at
  `tile_skeletonize.py:get_gap_fill` to `shared.py:get_source_target`). The
  public `skeletonize_tiles` entry point is therefore dead on this revision.
- `get_source_target` really does drop parallel edges: three input links sharing
  both endpoints return two edges.
- Greater London (`openroads/Greater_London.gpkg`): 88,240,128 bytes, 210,997
  links, 1,047,730 vertices, 18,526 km, 84 endpoint-connected components,
  58,926 x 45,892 m bounding box (2,704 Mpx at 1 px per metre). Motorway plus A
  road selection merges to 6,368 links and, at 10 m simplification, 18,599
  vertices and 1,156 KB, which is 1.34 percent of the source bytes. Merge plus
  selection took 0.11 s of CPU. This confirms the agents' London figures.

## Growing-extent test circles, and what a good result looks like

`bench/make_leeds_buffers.py` generates both sets, centred on 430367, 433604
(EPSG:27700), the vertex centre of gravity of the Leeds network, about 170 m from
Briggate. It writes them into `data/`, and they are gitignored because they are
regenerable in about forty seconds:

```bash
.venv/bin/python bench/make_leeds_buffers.py
```

Inputs, clipped from `data/osm_leeds.geojson`:

| dataset | lines | vertices | km | verts/km |
|---|---|---|---|---|
| `data/leeds_1km.geojson` | 1,979 | 10,142 | 104.2 | 97.3 |
| `data/leeds_3km.geojson` | 9,672 | 50,679 | 764.7 | 66.3 |
| `data/leeds_5km.geojson` | 18,530 | 105,559 | 1,698.8 | 62.1 |

References, clipped from the same circles out of the OS Open Roads layer
`open_roads_leeds` in `/mnt/secondary/data/bronze/os/openroads/oproad_gb_2025-12.gpkg`.
Open Roads is the OS's own straightened junction-to-junction network, so its
density is the empirical target for "what a simplified network looks like":

| reference | lines | vertices | km | verts/km |
|---|---|---|---|---|
| `data/openroads_leeds_1km.geojson` | 1,007 | 5,288 | 64.2 | 82.34 |
| `data/openroads_leeds_3km.geojson` | 7,698 | 35,265 | 507.4 | 69.50 |
| `data/openroads_leeds_5km.geojson` | 15,397 | 72,056 | 1,130.0 | 63.76 |

Use it as a density target, not a length target: Open Roads is roads only, so it
is about 38 percent shorter inside the same circle than the OSM extract, which
also carries footways, paths and cycleways.

The established way to clip Open Roads in this ecosystem is the `openroadsget`
Rust binary used by `csis` (`openroadsget links|junctions -g <gpkg> --bbox <bng
box>` from `~/github/robinlovelace/csis/_targets.R`), with a Python `gpkg_bbox`
helper for other layers. `bench/make_leeds_buffers.py` does the geopandas
equivalent, which is enough for circles this size.

## The frozen baseline on all five datasets

`bench/reference/baseline.json` records the original implementation's output on
all five. On the circles it is 12 to 14 times denser in vertices than its input:

| dataset | output lines | vertices | verts/km | components | degree-3+ | within 2 m |
|---|---|---|---|---|---|---|
| `princes_street` | 547 | 33,225 | 894.3 | 2 | 312 | 0.8216 |
| `doncaster_rail` | 173 | 48,824 | 842.6 | 1 | 98 | 0.8690 |
| `leeds_1km` | 1,740 | 69,067 | 918.1 | 29 | 969 | 0.7408 |
| `leeds_3km` | 13,983 | 592,569 | 897.6 | 87 | 7,896 | 0.8287 |
| `leeds_5km` | 28,916 | 1,360,918 | 893.7 | 142 | 16,147 | 0.8485 |

The large reference GeoPackages under `bench/reference/` (1.6, 12.7 and 28.9 MB)
are gitignored for the same reason; the JSON carries the numbers. Refreezing
reproduces the two small datasets' metrics exactly, though the GeoPackage bytes
themselves are not stable between runs (container metadata), so compare metrics,
not hashes.

## The component counts: the new code merges, and the old code was splitting

The first implementation pass reduced the component count on the two larger
circles (87 to 81 on 3 km, 142 to 134 on 5 km). That is a fix, not a regression,
and `bench/component_provenance.py` shows it: cluster the *old* output's
components with the buffer merge distance of 16 m, because an 8 m buffer joins
anything closer than that, so two pieces inside 16 m of each other sit in one
buffered polygon and its skeleton should be one component.

| dataset | buffered polygons | old components | old clustered at 16 m | new components |
|---|---|---|---|---|
| princes_street | 2 | 2 | 2 | 2 |
| doncaster_rail | 1 | 1 | 1 | 1 |
| leeds_1km | 32 | 29 | 29 | 29 |
| leeds_3km | 92 | 87 | **81** | **81** |
| leeds_5km | 147 | 142 | **134** | **134** |

The old output had 6 and 8 pieces that were within 16 m of another component,
which is to say inside one buffered polygon, emitted as separate components: the
old `<= 2 px` length filter dropped short connector links and split the
component. The new code keeps short junction links and merges them through
degree-2 chains, so it lands exactly on the 16 m clustering.

Caveats, stated plainly: the buffered polygon count is an upper bound, not the
answer, because a buffered island whose skeleton is shorter than 2 m produces no
output at all, and both the old and the new code lose those. And the 16 m rule is
a geometric proxy for the raster short-link filter rather than a proof of what the
raster did. The agreement is exact on both circles, 81 of 81 and 134 of 134, which
is why it counts as an explanation rather than a coincidence.

## Verified: the strip-wise rasteriser, with one honest exception

The second pass replaced the single `rasterio.features.rasterize` call with
strip-wise rasterising, 256 rows at a time, because the single call was scaling at
roughly the cube of the radius (0.10 s at 1 km, 5.5 s at 5 km: GDAL scans every
polygon edge on every row of the polygon's bounding box). The agent claimed the
raster was identical on all five datasets. `bench/check_raster_parity.py` compares
the two calls directly, and it is 8 of 9 comparisons identical, not 9 of 9:

| dataset | scale | differing pixels | foreground old / new |
|---|---|---|---|
| princes_street | 1.0 | 0 | 604,548 / 604,548 |
| doncaster_rail | 1.0 | 0 | 1,456,108 / 1,456,108 |
| leeds_1km | 1.0 | 0 | 1,294,748 / 1,294,748 |
| **leeds_3km** | **1.0** | **62** | **10,399,537 / 10,399,599** |
| leeds_5km | 1.0 | 0 | 23,900,051 / 23,900,051 |
| princes_street | 2.0 | 0 | 2,418,246 / 2,418,246 |
| doncaster_rail | 2.0 | 0 | 5,824,672 / 5,824,672 |
| leeds_1km | 2.0 | 0 | 5,179,643 / 5,179,643 |

So "the raster is unchanged" is not exactly true: 62 pixels of 10.4 million differ
on one dataset, which means a polygon edge that crosses a strip boundary can land
on the other side of a pixel centre. The claim about the mechanism, that strip
edges lie on pixel edges, is therefore incomplete.

Its effect was measured rather than argued. Running `leeds_3km` at the default with
a strip height of 256 against a strip height of a million, which degenerates to the
old single call:

| strip height | lines | vertices | km | components | degree-3+ |
|---|---|---|---|---|---|
| 256 (new default) | 14,047 | 52,258 | 631.512 | 82 | 8,076 |
| 1,000,000 (old behaviour) | 14,047 | 52,257 | 631.512 | 82 | 8,076 |

One vertex. No change in line count, length, components or junctions. So the
parity claim should be softened to "one vertex difference across these inputs
because of a strip-boundary pixel rule", not "identical", and the speed win stands
on its own: the 5 km rasterise went from 5.5 s to 1.0 s.
