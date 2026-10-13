# parenx next version: options for a faster, more aggressive simplifier

Author: Claude (Opus 5.5), 2026-10-13. Branch `4-speed-up-and-improve-simplification`, issue #4.

All timings below were measured on this machine, single run each, with another agent
working in the same repo. Treat them as indicative to about +/- 20 percent. Scripts are
in `/mnt/secondary/tmp/parenx-opus/` (`prof.py`, `rl.py`, `vor.py`, `london.py`).
Anything not measured is labelled **estimate**.

## 1. Diagnosis

### 1.1 What the code does

`skeletonize_frame` (the main path):

1. `get_geometry_buffer`: union all lines, buffer 8 m (mitre), union again.
2. `get_affine_transform`: one raster over the whole bounding box at `scale` px per metre.
3. `get_skeleton`: `rasterio.features.rasterize`, `remove_small_holes`, `skimage.skeletonize`.
4. `get_raster_point`: every skeleton pixel becomes a Shapely `Point` in a GeoSeries.
5. `get_raster_line`: build a 1 px square polygon per point, STRtree "covers" query to find
   8-neighbours, deduplicate pairs with pandas, make one 2-point `LineString` per pair,
   `line_merge`, `get_source_target` (STRtree nearest per endpoint), networkx connected
   components on short edges, collapse those clusters to a centroid ("knots"), merge again.
6. `sx_to_nx`: affine back to EPSG:27700, snap to 0.1 m, optional `simplify`.

`voronoi_frame`: buffer, take boundary, segmentize at `scale`, `voronoi_diagram`, explode,
keep Voronoi edges inside the buffer and far enough from the boundary, snap ends.

`skeletonize_tiles`: cut the extent into about 2 km Voronoi tiles, run `skeletonize_frame`
per tile in a serial Python loop, clip and gap-fill.

### 1.2 Measured stage costs (skeletonize, scale 1, buffer 8)

| stage | Princes St | Doncaster rail | Leeds OSM |
|---|---|---|---|
| input lines / vertices / km | 1,144 / 4,603 / 49 | 693 / 11,728 / 234 | 22,953 / 134,806 / 2,196 |
| raster shape (px) | 1,372 x 2,203 (3.0 Mpx) | 7,901 x 7,362 (58 Mpx) | 12,008 x 12,011 (144 Mpx) |
| foreground fill | 20 % | 2.5 % | 22 % (31 M px) |
| skeleton pixels | 32,471 | 48,569 | 1,726,793 |
| buffer + union | 0.15 s | 0.20 s | 8.3 s |
| rasterize | 0.10 s | 0.45 s | 14.9 s |
| remove_small_holes | 0.04 s | 1.41 s | 3.0 s |
| skeletonize | 0.12 s | 2.75 s | 8.3 s |
| pixels to Points | 0.21 s | 0.17 s | 5.6 s |
| Points to lines + graph (`get_raster_line`) | 0.99 s | 0.52 s | 30.9 s |
| peak RSS | 290 MB | 1,423 MB | 4,955 MB |
| output lines / vertices / km | 550 / 33,236 / 37 | 173 / 48,824 / 58 | 36,284 / 1,775,166 / 1,984 |

Wall time and CPU time agreed to within 1 percent in every row, so all stages are
single-threaded and CPU bound. Contention from the other agent was not visible.

Leeds end to end: 75 s wall (`/usr/bin/time`), 5.07 GB max RSS. Of the 72.6 s of
processing, the shares are:

- pixels to Points to lines to graph (`get_raster_point` + `get_raster_line`): 36.5 s, **50 %**
- `rasterize` of the unioned buffer polygon: 14.9 s, 21 %
- `skeletonize` + `remove_small_holes`: 11.3 s, 16 %
- buffer + union: 8.3 s, 11 %
- back-transform: 1.1 s, 2 %

So on the realistic input, half the time is the vector-ization of the skeleton, which is
pure overhead from building one Shapely object per pixel. `rasterize` is slow because it
burns a few huge, hole-riddled union polygons. Burning the buffered lines one by one, or
skipping the polygon union entirely, avoids it.

Inside `get_raster_line` on Princes St (32,471 pixels), measured:

| sub-step | time |
|---|---|
| 1 px square buffer per point | 0.048 s |
| STRtree covers query | 0.013 s |
| pandas dedupe + 2-point `LineString` per pair | 0.182 s |
| `line_merge` | 0.069 s |
| `get_source_target` | 0.020 s |
| **numpy 8-neighbour shift (replacement)** | **0.021 s** |

The numpy shift produced exactly the same 33,042 pixel pairs as the Shapely path. So the
whole "pixel to Shapely object to spatial index to pairs" detour can be replaced by four
array shifts at about 15 times less cost, with identical output.

`voronoi_frame` (buffer 8, scale 5, tolerance 1): Princes St 1.97 s, Doncaster 3.37 s.
Output is far lighter: 1,822 and 1,218 vertices, against 33,236 and 48,824 for skeletonize.

### 1.3 What the evidence says

1. **Time is split between two costs that grow differently.** Raster stages (rasterize,
   hole fill, skeletonize) grow with bounding-box area, not network length. Doncaster has
   5 times the length of Princes St but 19 times the pixels, because rail is long and thin.
   Its fill is only 2.5 percent, so 97.5 percent of the raster work is on empty pixels.
   Object stages grow with skeleton pixel count. Each skeleton pixel becomes a Python-level
   Shapely object several times over.
2. **Memory is the real wall.** 58 Mpx cost 1.4 GB peak. 144 Mpx (Leeds) cost 5.0 GB,
   about 35 bytes per pixel. Greater London's bounding box is
   58.9 km x 45.9 km, or 2,704 Mpx at 1 m (measured from the OS Open Roads extent). That is
   2.7 GB for a single `uint8` copy, and skimage makes several copies plus `bool` and
   `int64` label arrays in `remove_small_holes`. **Estimate** from the Leeds ratio of
   35 bytes per pixel: about 95 GB peak. This is why
   `skeletonize_tiles` exists, and its loop is serial.
3. **Output is heavier than input.** The skeleton output keeps one vertex per pixel.
   Princes St goes from 4,603 input vertices to 33,236 output vertices (7 times more).
   Doncaster goes from 11,728 to 48,824. Leeds goes from 134,806 to 1,775,166 (13 times
   more), and from 22,953 lines to 36,284. Only the optional `--simplify` reduces this, and it
   defaults to 0. The "wobbly lines" in the README are the 1 px staircase of the skeleton.
   This is the opposite of the 1 percent goal.
4. **Length loss is by design, not a bug.** Doncaster drops from 234 km to 58 km. The input
   union is also 234 km, so the loss is not duplicated input. It is parallel rail tracks
   within 16 m merging into one centreline. Any faithfulness check must therefore use
   coverage (distance-based), not total length.
5. **Technical debt seen while reading.**
   - `get_source_target` sorts `(source, target)` and then `drop_duplicates` on that pair.
     Two genuinely different lines between the same two nodes (a loop round a block, a
     slip road beside a main road) are silently dropped. Self-loops are kept.
   - `get_primal` computes a node table `r` and then throws it away.
   - `get_segment_buffer` uses `unary_union` (deprecated in shapely 2) and `try/except
     AttributeError` to tell Multi from single geometries. The same pattern recurs in
     `combine_line` and `get_geometry_buffer`.
   - CRS is hard-wired to EPSG:27700 in every function. Input is reprojected silently.
   - Parameters travel as a loose `dict` with no defaults, so the public API needs every
     key. `voronoi_frame` needs `tolerance` while `skeletonize_frame` needs `knot` and
     `segment`.
   - Attributes are dropped: the API takes a bare `GeoSeries`, which is the README's first
     weakness.
   - The raster is in (row, col) order and the transform swaps axes (`TRANSFORM_ONE`),
     which makes the code hard to follow and easy to break.
   - No tests.

## 2. Is the raster detour still the right primitive?

**What it buys.** Robustness. Skeletonizing a buffered union is indifferent to messy input:
duplicate lines, dual carriageways, roundabouts, slightly misaligned joins and overlapping
cycle routes all merge into one centreline with no topology preparation. It needs no
attributes and no clean graph. That is why parenx works on `rnet_*` route networks, which
are overlapping, untopological outputs of routing.

**What it costs.** Work and memory that scale with bounding-box area at the chosen pixel
size, not with the network. A 1 px staircase that has to be smoothed afterwards. A
quantisation error of up to half a pixel. A conversion step back to vectors that today is
the slowest part. Junction "knots" that need a separate repair (`get_centre_edge`).

**When it is wrong.**

- Large, sparse extents (rail, motorways, regional networks). Fill under 5 percent.
- Inputs that are already clean, topological graphs with a hierarchy (OS Open Roads,
  OSM after `osmnx`/`osm2streets`). There the job is "select and merge", not "find the
  centreline", and a vector graph method is exact and orders of magnitude cheaper.
- Any extent that does not fit in memory at the chosen scale (London at 1 m).

**Verdict.** Keep the raster primitive for the "collapse parallel strands into one" step,
but (a) only rasterise where it is needed, in tiles that are processed in parallel,
(b) never turn pixels into Shapely objects, and (c) put a graph-first fast path in front of
it for the aggressive, hierarchy-driven case.

## 3. Definitions the options depend on

### 3.1 "Deduplicated network links"

A link is an edge of the simplified graph *after* parallel strands are merged and after
degree-2 chains are merged (`line_merge`). So:

- Two carriageways of one road, or three overlapping cycle routes, count as **one** link.
- A road split into 12 OSM ways with no junction between them counts as **one** link.
- A link runs junction to junction (node degree not 2) or to a dead end.

This is the unit the user sees on a map, so it is the right unit to count. It also fixes
the current `drop_duplicates` bug, because parallel strands are merged by geometry first,
and remaining genuine parallel edges keep separate ids (a multigraph).

### 3.2 `keep=0.95` and connectivity

`keep` is a fraction of **total deduplicated link length** (not link count, since counts
are dominated by tiny service stubs). It ranks links by importance and keeps the top ones
until 95 percent of length is retained.

Dropping by rank alone can island parts of the network. Proposed rule:

1. Score each link: `score = class_weight(highway) * f(length) * g(betweenness)` where
   betweenness is optional and sampled (k-source Brandes) for large graphs.
2. Mark the **backbone**: the links that keep each connected component of the input
   connected. Compute a maximum spanning forest on `score` (Kruskal, `O(E log E)`). The
   backbone is never dropped.
3. Drop non-backbone links in ascending score until the kept length falls to `keep`.
4. If the backbone alone is longer than `keep` allows, stop at the backbone and **warn**,
   reporting the actual fraction achieved. Do not silently island.
5. Optionally prune dead-end spurs shorter than `buffer` after dropping, then repeat.
6. Report: requested `keep`, achieved `keep`, component count before and after (must be
   equal), and dropped length by class.

A `connect="strict" | "largest" | "none"` flag covers the other reasonable choices:
strict (default) as above, keep only the largest component, or rank only.

### 3.3 Road hierarchy

Sources, in order:

1. Caller passes a `GeoDataFrame` with a column named by `rank_by=` (default tries
   `highway`, then `road_classification`, then `road_function`, then `class`).
2. Built-in lookup tables:
   - OSM `highway`: motorway > trunk > primary > secondary > tertiary > unclassified >
     residential > living_street > service > track/path/cycleway/footway. `*_link` takes
     its parent class minus a half step.
   - OS Open Roads `road_classification`: Motorway > A Road > B Road > Classified
     Unnumbered > Not Classified > Unclassified > Unknown.
   - Route networks (`rnet_*`): a numeric flow column, e.g. `commute_fastest_bicycle_go_dutch`.
     Rank by flow directly.
3. Caller passes a numeric column or a callable.

With a bare `GeoSeries` (today's API), there are no attributes. Then the score falls back
to geometry only: link length, and optional sampled edge betweenness on the simplified
graph (which measures "through route" importance well). This must be stated in the docs
and logged at run time, because a bare-geometry 1 percent cut is much weaker than a
hierarchy-driven one.

Attributes reach the output by a **source-to-output join**: every input line records which
output link it collapsed onto (nearest link within `buffer`, weighted by shared length).
Output links then carry `max(class)`, `sum(flow)` or a caller-supplied aggregation. This
fixes README weakness 1 and is what makes ranking possible after the merge. `netweld`
already implements an exact shared-length join in Rust and is the template.

### 3.4 File size accounting and the 1 percent London target

Three sizes, all reported by the tool:

- **Vertices**: total coordinate count. Drives GeoJSON and GPKG size almost linearly.
- **Segments (links)**: row count. Drives attribute size.
- **Bytes**: written GPKG or GeoParquet size, with the attributes the caller asked to keep.

Measured on OS Open Roads Greater London (`Greater_London.gpkg`, 86,172 KB, 210,997
links, 1,047,730 vertices, 18,526 km, 84 connected components):

| selection | links after `line_merge` | vertices | km | GPKG (geometry only) | components |
|---|---|---|---|---|---|
| Motorway + A, no simplify | 6,368 | 109,022 (10.4 %) | 2,003 | 2,852 KB (3.3 %) | 4 |
| Motorway + A, simplify 10 m | 6,368 | 18,599 (1.8 %) | 1,996 | 1,156 KB (1.3 %) | 4 |
| Motorway + A, simplify 25 m | 6,368 | 15,480 (1.5 %) | 1,987 | 1,104 KB (1.3 %) | 4 |
| Motorway + A + B, simplify 10 m | 8,035 | 23,473 (2.2 %) | 2,512 | 1,432 KB (1.7 %) | 6 |

Selection plus merge plus simplify took 0.3 s for all of London. Motorway and A roads are
11 percent of length. So:

- 1 percent of **bytes** is not quite reached by selection plus Douglas-Peucker alone. It is
  1.3 percent.
- The remaining factor of about 1.5 to 2 comes from collapsing dual carriageways and
  slip roads into one centreline (the parenx step) and from fewer, longer links. London A
  roads are heavily dual. **Estimate**: collapsing them gives 35 to 50 percent fewer
  vertices and links, which lands at 0.7 to 0.9 percent of bytes.
- Connectivity: the M + A subset is 4 components, and the largest holds essentially all
  nodes (share 1.0 to 3 decimal places). The 3 small ones are tiny isolated fragments.
  Keeping the important roads connected at 1 percent is therefore achievable. The
  backbone rule in 3.2 would add the few B or minor links that join them.
- So "1 percent" should be specified as **bytes of the written file with geometry plus a
  small fixed attribute set** (id, class, length, source count). In vertex terms that is
  about 10,000 vertices for London, which is a sensible target for a strategic map.
  A `target_size=0.01` argument can drive `keep` and `simplify` by bisection.

## 4. Options

Speed-ups are relative to today's `skeletonize_frame` on the same input, end to end.

### Option 1. Vectorise the Python core (no new language)

**Mechanism.** Keep the algorithm. Replace pixels to `Point` to square to STRtree to pandas
pairs with numpy 8-neighbour shifts on the skeleton array (measured: identical pairs). Build
the graph directly as arrays (pixel id to node id), trace degree-2 chains with a small
vectorised walker or `scipy.sparse.csgraph`, collapse knots with `connected_components` on
a sparse matrix instead of networkx. Replace `get_source_target` with exact coordinate
hashing (`np.unique` on snapped coordinates), which also removes the `drop_duplicates`
bug. Add Douglas-Peucker on output by default (`simplify = scale`). Crop the raster to
per-component bounding boxes so empty space is not processed. Make `skeletonize_tiles`
use a process pool.

**Expected speed-up.** Object stages: about 10 to 15 times (measured 0.18+0.05+0.01 s
to 0.02 s for pair finding). Raster stages unchanged on dense inputs. End to end on Princes
St: 1.6 s to about 0.6 s (**estimate** from the stage table). On Doncaster per-component
cropping cuts the 58 Mpx raster to the 2.5 percent that is near the network: **estimate**
3 to 5 times. On Leeds, the 36.5 s object stage drops to about 2.5 s if the 15 times
ratio holds, giving about 38 s instead of 73 s (**estimate**, 2 times). Burning lines
instead of the union polygon may cut `rasterize` and the union from 23 s to a few seconds
(**estimate**, not measured). With a pool of N cores on tiles: close to N times on large
inputs. So Option 1 alone gives 2 to 4 times on one core and roughly 10 to 30 times on a
workstation. That is "faster", but not "much, much faster".

**Fidelity risk.** Low. Same algorithm. Output can be checked pixel pair by pixel pair.

**Effort.** 3 to 5 days.

**Technical debt.** Removes the worst of it (object explosion, `try/except` dispatch,
duplicate drop bug). Still pure Python, still heavy on memory per tile.

**`keep` and 1 percent.** Not addressed by itself. Needs Option 4 on top.

### Option 2. Rust core with Python bindings (PyO3 + maturin), raster algorithm kept

**Mechanism.** One compiled crate `parenx-core` with: rasterise lines-with-buffer directly
(distance-transform style, no polygon union needed), Zhang-Suen or Guo-Hall thinning,
skeleton graph extraction, knot collapse, Douglas-Peucker, tile scheduler with `rayon`.
Python keeps the public functions and CLIs as thin wrappers that pass Arrow/WKB buffers
in and out. Optional distance-field thinning avoids the separate buffer-union step, which is
8.3 s on Leeds.

**Expected speed-up.** **Estimate** 20 to 50 times end to end on one core for Leeds-sized
inputs, from: no Shapely objects per pixel, no polygon buffer-union (the buffer becomes
"pixels within r of a line", which is a bounded scan per segment), thinning in a tight loop
that only visits foreground pixels, and tile parallelism. junctions-rs shows the same
pattern (Rust core validated against a Python reference). Memory falls to tile size x
threads, so London at 1 m becomes feasible.

**Fidelity risk.** Medium. Different thinning implementation gives slightly different
skeletons at junctions. Must be validated against Option 1 output, not bit for bit.

**Effort.** 10 to 15 days including bindings, wheels in CI and validation.

**Technical debt.** Replaces the backend, rather than layering beside it. Adds a build
toolchain (maturin, cibuildwheel).

**`keep` and 1 percent.** Provides the fast collapse step. Ranking still needed (Option 4).

### Option 3. SedonaDB / SQL pipeline

**Mechanism.** Express selection, buffer, union, line merge, simplify and the
attribute join in SQL (SedonaDB or DuckDB spatial), as in `netlos`.

**Expected speed-up.** Good for the vector parts (selection, merge, simplify, join).
SQL has no skeletonization or thinning primitive, so the core centreline step either stays
in Python or becomes a UDF. **Estimate**: no gain on the raster-bound parts.

**Fidelity risk.** Medium to high. Line merging and topology in SQL engines differ (GEOS
versions, precision handling).

**Effort.** 5 to 8 days for the vector path. The centreline step would remain outside.

**Technical debt.** Adds a second runtime with its own geometry semantics. Splits the
algorithm between SQL and Python. I do not recommend it as the backend. It is a good
*front end* for very large inputs (pre-select by hierarchy from a PBF or parquet before
parenx sees them).

**`keep` and 1 percent.** The selection half is natural in SQL (`WHERE rank <= k`).
Connectivity-preserving drop (spanning forest) is awkward in SQL.

### Option 4. Hierarchy-first graph simplifier ("pare" mode)

**Mechanism.** A new mode that works on the graph, not on pixels:

1. Build a planar-ish graph by exact endpoint hashing (snap 0.1 m, as today).
2. Score links by hierarchy (3.3) and optional sampled betweenness.
3. Backbone spanning forest plus ranked drop to `keep` (3.2).
4. Run the centreline collapse (Option 1 or 2) **only on the kept links**, and only where
   parallel strands exist (tiles with more than one strand within `2*buffer`).
5. `line_merge`, Douglas-Peucker, attribute join back.

**Expected speed-up.** Measured: London M + A selection, merge and simplify in 0.3 s.
Graph build for 211 k links is **estimate** under 2 s in numpy. Because only 10 to 20
percent of length goes into the raster step, and that step is tiled, the London run at
1 percent is **estimate** under 30 s in Python plus Option 1, under 5 s with Option 2.
Today's code cannot run London untiled at all (memory, section 1.3).

**Fidelity risk.** Low for `keep=1.0` (it reduces to the existing algorithm). The
aggressive path is new behaviour, so it needs its own checks (section 6).

**Effort.** 4 to 6 days on top of Option 1.

**Technical debt.** Neutral to positive. It forces a proper graph type (node table, edge
table with stable ids) that the current code fakes with `get_source_target`.

**`keep` and 1 percent.** This is the option that delivers them.

### Option 5. Replace the raster with a vector medial axis (Voronoi done well)

**Mechanism.** The existing `voronoi_frame` idea, but on a segment Voronoi diagram
(boost::polygon style, or `centerline` crate) of the buffer boundary, pruned by distance
to boundary. No pixels.

**Expected speed-up.** Measured today: voronoi is slower than skeletonize on Princes St
(1.97 s vs 1.6 s) because it uses point Voronoi on a densely segmentized boundary. A
segment Voronoi in Rust would be **estimate** 10 to 30 times faster than today's voronoi.
Output is already 18 to 40 times lighter in vertices than skeletonize.

**Fidelity risk.** Medium. Medial axes have spurs at every buffer corner. Pruning is the
hard part. That is what the current "dewhisker" code is doing.

**Effort.** 10+ days in Rust. Segment Voronoi is hard to make robust.

**Technical debt.** Would let us drop one of the two pipelines eventually. Not now.

**`keep` and 1 percent.** Same as Option 2. Needs Option 4.

## 5. Recommendation

**Staged: Option 1, then Option 4, then Option 2 for the hot loop.**

- Stage A (Option 1, 1 week). Vectorise the Python core, fix the duplicate drop bug, add
  output simplification and a process pool. Add the test harness (section 6) first so
  everything after is measured against today's output.
- Stage B (Option 4, 1 week). Add `keep`, `rank_by`, `connect` and the attribute join.
  Change the public functions to accept a `GeoDataFrame` or `GeoSeries`, and keep the old
  positional `parameter` dict working with defaults. This delivers the 1 percent London
  target in Python.
- Stage C (Option 2, 2 to 3 weeks, only if Stage A plus B is still too slow). Move
  rasterise, thin, trace, knot-collapse and tile scheduling into a Rust crate called from
  the same Python functions. Delete the replaced Python, so there is one backend.

Option 3 (SQL) stays as an optional pre-filter for PBF-scale inputs. Option 5 is parked.

### First three concrete next steps

1. **Freeze a reference.** Run today's `skeletonize_frame`, `voronoi_frame` and
   `skeletonize_tiles` on `rnet_princes_street` and `rnet_doncaster_rail` with default
   parameters. Save outputs as `tests/reference/*.gpkg`. Write `tests/test_faithful.py`
   with the checks in section 6. Pin the run time per stage as a benchmark.
2. **Replace `get_raster_point` + `get_raster_line` pair finding** with the numpy
   8-neighbour shift (already shown to produce identical pairs on Princes St), and
   `get_source_target` with coordinate hashing. Run the tests. Expected result: identical
   output except for parallel edges that the old code dropped.
3. **Prototype `keep` on OS Open Roads London** using `road_classification` and the
   backbone rule. Report kept length, vertex count, bytes and component count for
   `keep` in {0.95, 0.5, 0.2, 0.11} and for `target_size=0.01`.

## 6. Verification: proving faithfulness

Run on both small in-repo datasets, against the frozen reference output.

1. **Coverage, both ways.** Fraction of reference length within `d` metres of new output,
   and fraction of new output within `d` of reference, for `d` = 1, 2, 5 m. Target: at
   least 99 percent at 2 m for `keep=1.0`. Plus Hausdorff distance per connected
   component.
2. **Input coverage.** Fraction of input line length within `buffer` of output. Must be
   100 percent for `keep=1.0`. This catches the parallel-edge drop bug.
3. **Topology.** Node degree histogram and number of connected components equal to the
   reference (ties allowed at knots). No zero-length or self-intersecting links. Every
   link endpoint is a node of degree not 2 or a dead end (proves `line_merge` ran).
4. **Length.** Total output length within 2 percent of reference. Report length per
   input class when attributes exist.
5. **`keep` checks.** Achieved length fraction within 0.5 percentage points of `keep`, or
   a warning when the backbone limits it. Component count equal to input component count
   under `connect="strict"`. Kept links are monotone in `keep` (a link kept at 0.5 is also
   kept at 0.95).
6. **Attribute join.** Every input line maps to exactly one output link or is flagged
   as dropped. Sum of flow is conserved for route networks.
7. **API.** Smoke tests that `skeletonize_frame`, `voronoi_frame`, `skeletonize_tiles`,
   `get_primal`, the three console scripts and the `parenx` wrapper still run with the old
   arguments.
8. **Performance.** Stage timer and peak RSS logged per run, compared to the table in
   section 1.2, on Princes St, Doncaster and Leeds.
