# parenx next version: measured diagnosis and backend options

13 October 2026; source revision `ad5bf6f4c761865b46c174c45dc7de849abb2dc1`,
branch `4-speed-up-and-improve-simplification`, issue #4. This file is the sole
deliverable. Measurements below are real; proposed backend improvements are not
implemented.

## Decision in brief

Separate two operations which the current API conflates: recovering a centreline
from overlapping linework, and selecting a connected, hierarchy-aware road
network. Keep the existing entry points, replace their object-heavy backend, and
make aggressive selection an explicit policy rather than an accidental effect of
buffering or endpoint deduplication. **Recommend option 2, with option 1 as its
first replacement increment**, not as a second permanent backend. Keep raster
and Voronoi centreline operations available, but make an explicit graph/backbone
policy the route to a much smaller road network. Do not claim a 10x faithful
raster speed-up from eliminating a helper that takes 1.4% of runtime.

## Diagnosis and measurement

The bottleneck is **not universally the spatial index**. On Leeds, the largest
single measured stage is rasterization (10.561 s), closely followed by constructing
1.76 million two-point LineStrings (9.666 s). On Doncaster, thinning the mostly
empty image dominates (2.410 of 4.068 s). A Rust rewrite of only endpoint lookup
cannot provide a drastic end-to-end improvement.

### Method and environment

One foreground, timeout-bounded run per dataset, separate forked child processes;
Python 3.12.12, GeoPandas 1.2.0, Shapely 2.2.0 / GEOS 3.14.1, rasterio 1.5.2,
scikit-image 0.26.0, NumPy 2.5.4, pandas 3.0.6, NetworkX 3.7. Intel i9-14900K,
32 logical CPUs, approximately 123 GiB RAM. `OMP_NUM_THREADS=1` and
`OPENBLAS_NUM_THREADS=1`; imports precede measurement. Another exploration may
have competed for the host; these are single observations, not confidence
intervals or isolated-machine throughput guarantees.

The staged harness reproduces the current default skeleton pipeline with
`buffer=8, scale=1, simplify=0, knot=False, segment=False`, reading through
`get_base_geojson`. Each stage uses `perf_counter()` and `process_time()`.
Compute totals sum stages, excluding reads, diagnostics, spikes and `get_primal`.
RSS comes from `/proc/self/status` and `resource.getrusage`; it includes native
GEOS/GDAL/NumPy memory, unlike `tracemalloc`. Intermediate lifetimes differ from
an uninstrumented API call, so RSS is an observed instrumented working set, not
an exact production API peak. No files were emitted by the benchmark.

| Stage (seconds, wall / process CPU) | Princes Street | Doncaster rail | Leeds |
|---|---:|---:|---:|
| Read, project, 0.1 m snap (excluded) | 0.049 / 0.049 | 0.054 / 0.054 | 0.374 / 0.374 |
| Union, buffer, union | 0.093 / 0.093 | 0.148 / 0.148 | 7.042 / 7.042 |
| Rasterize | 0.066 / 0.066 | 0.337 / 0.336 | 10.561 / 10.561 |
| Remove small holes | 0.027 / 0.027 | 0.531 / 0.531 | 1.321 / 1.321 |
| Skeleton thinning | 0.053 / 0.053 | 2.410 / 2.409 | 6.666 / 6.666 |
| Construct pixel Points | 0.064 / 0.064 | 0.166 / 0.166 | 4.112 / 4.112 |
| Buffer Points into squares | 0.045 / 0.045 | 0.071 / 0.071 | 2.599 / 2.599 |
| STRtree covers query | 0.011 / 0.011 | 0.019 / 0.019 | 0.856 / 0.856 |
| Deduplicate pixel pairs | 0.006 / 0.006 | 0.013 / 0.013 | 0.740 / 0.740 |
| Construct pixel LineStrings | 0.168 / 0.168 | 0.276 / 0.276 | 9.666 / 9.661 |
| First line merge | 0.043 / 0.043 | 0.066 / 0.066 | 3.405 / 3.405 |
| Endpoint graph | 0.015 / 0.015 | 0.011 / 0.011 | 0.692 / 0.692 |
| Short-edge connected components | 0.002 / 0.002 | 0.002 / 0.002 | 0.075 / 0.075 |
| Cluster centroid spokes | 0.036 / 0.036 | 0.014 / 0.014 | 2.164 / 2.163 |
| Merge spokes with long edges | 0.003 / 0.003 | 0.002 / 0.002 | 0.276 / 0.276 |
| Affine transform and precision snap | 0.007 / 0.007 | 0.004 / 0.004 | 0.374 / 0.374 |
| **Compute total** | **0.637 / 0.637** | **4.068 / 4.067** | **50.550 / 50.543** |
| `get_primal` (excluded) | 0.007 / 0.007 | 0.004 / 0.004 | 0.484 / 0.484 |
| Instrumented peak RSS, MiB | 214.6 | 1,319.2 | 3,313.7 |

### What the raster and object graph actually contain

| Quantity | Princes Street | Doncaster rail | Leeds |
|---|---:|---:|---:|
| Input features | 1,144 | 693 | 22,953 |
| Input vertices | 4,603 | 11,728 | 134,806 |
| Input length, km (before snap) | 49.376 | 234.451 | 2,196.383 |
| Buffered raster rows x columns | 1,372 x 2,203 | 7,901 x 7,362 | 12,008 x 12,011 |
| Raster cells | 3,022,516 | 58,167,162 | 144,228,088 |
| Raster foreground before thinning | 604,548 (20.00%) | 1,456,108 (2.50%) | 31,139,018 (21.59%) |
| Skeleton pixels = Points = squares | 32,471 | 48,569 | 1,726,793 |
| Query pairs, directed including self | 98,555 | 146,021 | 5,249,227 |
| Unique undirected pixel LineStrings | 33,042 | 48,726 | 1,761,217 |
| Lines after first merge | 1,723 | 513 | 111,362 |
| Final lines | 550 | 173 | 36,284 |
| Final vertices | 33,236 | 48,824 | 1,775,166 |
| Final length / primal chord length, km | 37.150 / 33.567 | 57.947 / 53.087 | 1,984.374 / 1,799.488 |

Only skeleton pixels become Points, not all buffered foreground pixels. The
square radius is **one pixel**, hence side length two pixels (2 m at the default),
not a 1 m-sided square. `covers` includes the eight neighbouring pixels and self.
Leeds creates at least **5,214,803** Points, squares and pixel LineStrings across
these stages, before merged lines, graph endpoints and centroid spokes. Its
query index array alone occupies 83,987,632 bytes. Each uint8/bool full image is
137.5 MiB; morphology labels and rasterio copies cost much more than one byte
per cell. Leeds RSS was 1,525.8 MiB after rasterization, reached a high-water
mark of 3,313.7 MiB during hole removal, and was 2,599.1 MiB after pixel-line
merging. A Python-object-only profile would miss most of this.

Raster/vector setup through thinning is 25.591 s (50.6%) of Leeds compute;
pixel Points, squares, pair queries, deduplication, LineStrings and their first
merge together are 21.378 s (42.3%). Doncaster spends 84.2% in setup through
thinning despite having only 48,569 skeleton pixels. Work scales with bounding-box
area as well as line length.

### A measured, bounded hotspot spike

Replace Point buffers and spatial lookup with sorted row-major integer pixel IDs.
For each foreground pixel, test only `(0,1), (1,-1), (1,0), (1,1)` offsets,
explicitly masking row/column boundaries; `searchsorted` finds neighbour IDs.
On **all three actual skeletons**, sorted undirected pair arrays matched the
existing square/STRtree/dedup output exactly, not merely in edge count.

| Wall / CPU seconds | Princes Street | Doncaster | Leeds |
|---|---:|---:|---:|
| Old Points + squares + query + pair dedup | 0.125 / 0.125 | 0.269 / 0.269 | 8.307 / 8.307 |
| Integer adjacency including image scan | 0.00810 / 0.00810 | 0.10328 / 0.10328 | 0.67599 / 0.67598 |
| **Measured local speed ratio** | **15.5x** | **2.6x** | **12.3x** |

This spike does **not** emit equivalent final lines or remove pixel LineString
construction; it is not an end-to-end Rust speed claim. Replacing only this
stage predicts Leeds at 42.919 s, or **1.18x**, using measured stage arithmetic.
Numeric endpoint extraction with `np.unique(..., return_inverse=True)` took
0.00123 / 0.00123, 0.00051 / 0.00051 and 0.09047 / 0.09047 s respectively,
versus 0.01494 / 0.01494, 0.01136 / 0.01136 and 0.69170 / 0.69169 s for the
full old graph helper. Node and unique-edge counts agreed, but it did not
construct the full return frames or prove ID-to-geometry incidence equivalence;
these are **partial-work timings**, not a graph replacement benchmark.

An important correction to the issue's hypothesis: `get_source_target` already
uses **two batched** nearest queries, not a Python nearest-query call per
endpoint. The avoidable costs are extracting/exploding all interior coordinates,
allocating endpoint geometries, spatial lookup for exact-equal coordinates, and
reconstructing frames. On Leeds this entire helper is only 1.4% of compute.

### Other paths, and correctness debt that affects the design

`shared.get_geometry_buffer` first unions the lines, buffers the resulting
pieces with mitred joins, then unions their polygons. `combine_line` constructs
a MultiLineString and calls GEOS line-merge; it is not an all-intersections
network-noding operation. The raster branch builds an eight-neighbour graph,
merges degree-two paths, clusters edges of length at most two **pixels**, moves
cluster nodes to their centroid via spokes, merges again, removes short results,
then applies the raster-to-map affine transform and a 0.1 m precision snap.
`knot=True` bypasses the short-cluster collapse. `segment=True` has a separate
centre-trimming/buffering policy, including a hard-coded 0.612 m fallback; it
needs its own compatibility tests.

The actual raster `scale` is **pixels per metre**, not metres per pixel:
dimensions multiply by `scale`, and the affine divides by it. Thus doubling
`scale` approximately quadruples image cells. Voronoi `scale`, in contrast,
is a maximum boundary segment length in metres. Raster hole removal uses
`16 * scale`, not a physical area times `scale**2`; its physical meaning changes
with resolution. The two-pixel short-edge threshold also changes in metres.
Do not silently reverse the existing argument's meaning in a rewrite. Introduce
an explicit `pixel_size_m` alias with conflict checking, and retain legacy
`scale` conversion. Test non-unit scales and the existing pixel-origin convention.

The Voronoi path simplifies the buffered boundary by 0.5 m, segmentizes it at
`scale`, materializes segment endpoints, runs GEOS point-site Voronoi **edges**,
snaps to 0.1 m, explodes and clips to an envelope. It filters by distance to
the boundary and strict containment in the buffer; subsequent 1 m simplification
and centroid snapping remove detail. These are not generic road-ranking rules.
`voronoi_frame` ignores `parameter["simplify"]`; only its CLI applies that final
tolerance. Raster and tiled APIs return GeoDataFrames, whereas Voronoi and
`get_primal` return GeoSeries. Preserve these return contracts initially.

Additional single-run checks used the real entry points and `cProfile`;
profiling overhead is included, so their times are **not directly comparable**
to the low-overhead skeleton table:

| Algorithm / input | Wall / CPU, s | Peak RSS, MiB | Result |
|---|---:|---:|---|
| Voronoi / Princes Street | 4.802 / 4.802 | 255.7 | 466 lines, 1,822 vertices, 35.487 km |
| Voronoi / Doncaster | 7.172 / 7.172 | 329.1 | 238 lines, 1,218 vertices, 56.834 km |
| Tiled / Princes Street | 2.830 / 2.830 | 184.8 | **Failed**, after 7 tile skeleton calls |
| Tiled / Doncaster | 5.164 / 5.163 | 182.2 | **Failed**, after 46 tile skeleton calls |

Voronoi construction/cleanup accumulated 2.367/2.263 s on Princes Street and
4.369/2.539 s on Doncaster. There were about 4.0/4.9 million profiled calls;
Doncaster's actual GEOS `voronoi_diagram` call was 1.462 s. There is substantial
Python/frame construction outside the geometric kernel, but these inclusive
profile entries overlap and must not be added together.

Both tiled runs fail at `tile_skeletonize.py:get_gap_fill` ->
`shared.py:get_source_target`: a GeoDataFrame reaches
`line.map(get_coordinates).explode()` without the required DataFrame `column`
argument. Failed timings are not completed-network speed measurements.
`get_square(length=2000)` places centres 1,000 m apart, so interior Voronoi
tiles are approximately **1 km-sided**, despite the advertised 2 km side.
Each tile gets a `3 * buffer` halo, is processed serially, clipped, then
near-boundary endpoints within `buffer / 2` are joined. This proximity repair
can create links as well as close seams. A new tile scheduler must resolve
these semantics rather than merely parallelize the loop.

Other directly relevant debt:

- `get_source_target` sorts endpoint IDs and drops all but one edge for each
  endpoint pair. Different curved routes and parallel edges can disappear.
  Leeds's first merge produced 111,362 lines, but the helper kept 111,335.
  This is **not geometric deduplication**.
- `get_primal` replaces each entire line by its endpoint chord; it does not
  retain every original segment. It also builds/group-counts endpoint Points
  whose result is unused. A closed loop becomes a zero-length chord. Straight
  chords may cross other roads or leave the source corridor.
- The CLI reader reprojects/snaps input. Public functions generally assume
  projected metre coordinates and attach EPSG:27700; they do not consistently
  validate or transform the caller's CRS. A bare GeoSeries loses all attributes
  before any simplification starts.
- All CLI writers retain an `input` layer as well as `line` and `primal`.
  **A file retaining the original input cannot meet a 1% whole-file target.**
  Add an explicit compact-output mode; retain the current layer layout by
  default for compatibility.
- NetworkX is imported but not declared in `pyproject.toml`; the supplied
  environment masks that packaging debt. The replacement core can eliminate
  this runtime dependency rather than leave an undeclared fallback behind.

### Is the raster detour still the right primitive?

**For tangled, duplicated, nearby parallel linework whose desired result is a
single medial centreline: yes, sometimes. For selecting an already-topological
road graph: no.** Buffering plus thinning is an effective, fairly generic
geometric conflation policy, including rail yards without road attributes.
It trades off exact source geometry for a centreline and closes gaps smaller
than its buffer scale. It cannot tell a redundant lane from a separate bridge
deck, or a residential road from a motorway. Those distinctions require
attributes and topology before any 2D union.

Its dense cost is roughly bounding-box area divided by pixel area, multiplied
by morphology passes; graph cost then depends on skeleton pixels. It buys
robustness to small coordinate mismatches and shared corridors, but loses
provenance, grade separation, direction and fine shape unless handled explicitly.
At default scale Leeds's final vertex count is **13.2x the input**, not a
simplification in storage terms. Doncaster's length shrinks to about a quarter
of its input because parallel tracks are collapsed: that is useful centreline
abstraction, not faithful retention of every track.

Use a raster only on identified ambiguous corridors or bounded tiles; operate
directly on known road nodes elsewhere. For exact same-level road networks,
numeric endpoint IDs, degree-two contraction and constrained polyline
simplification are the right primitives. For sparse, geographically extensive
linework, component/local processing or a vector medial-axis method avoids
paying for vast empty rectangles. A coarser raster is a quality/speed trade-off,
not a free faithful acceleration; small links and gaps can disappear.

## The new contract: links, hierarchy, connectivity and bytes

### Deduplicated links and `keep=0.95`

Define a **canonical physical link** before selection, independently of
arbitrary file feature boundaries or individual raster pixel edges:

- Validate line geometry and CRS; work in an appropriate projected metre CRS.
  Preserve EPSG:27700 compatibility. Snap to the agreed 0.1 m grid without
  identifying separate grade levels. Use trusted source node IDs where present;
  otherwise split at genuine same-level junctions and overlap boundaries.
  Missing level information means a declared 2D assumption, not inferred bridges.
- Deduplicate coincident paths using full normalized geometry, allowing reversed
  direction and redundant collinear vertices, **plus compatible level and
  access semantics**. Keep a many-to-one source-ID map. Coincident opposite
  directed records can become one physical link with both travel arcs; do not
  discard the arcs. Same endpoints alone are insufficient. Parallel carriageways,
  alternate routes and distinct rail tracks are not duplicates by default.
- Contract maximal degree-two chains, but stop at junctions, protected terminals,
  and meaningful hierarchy/access/level changes. For isolated rings choose
  deterministic anchors and retain a closed-link representation; never erase
  them because there is no degree-one endpoint. Attribute change points are
  meaningful boundaries; arbitrary input row boundaries are not.
- Freeze this canonical link set and its source membership **before applying
  `keep`**. Tolerance-based corridor conflation is a separately declared operation,
  not exact deduplication; if requested, apply it before freezing the denominator
  and report both original and conflated counts.

The order matters. For the existing centreline entry points, their requested
raster/Voronoi extraction is an explicit conflation stage: recover centrelines,
canonicalize that representation, then select links. Report
`input_canonical_links`, `centreline_canonical_links` and which one is the
denominator; in centreline mode it is the latter. In backbone mode, which
preserves existing road centrelines, it is the former. Thus 95% of an extracted
rail-corridor graph is not a promise to retain 95% of the original tracks.
Changing a conflation buffer or raster resolution can change the abstract
network and its denominator; changing mere feature segmentation should not.
This avoids counting millions of pixel edges as road links or claiming that
pre-extraction connectivity guarantees survive an unconstrained raster pass.

For `N` canonical links, `keep=0.95` requests exactly
`M = ceil(0.95 * N)` retained link IDs. Validate finite `0 < keep <= 1`;
empty input returns a typed empty result with zero counts. The backward-compatible
default is `keep=1.0`, not an implicit 5% removal. Physical link count is **not**
length share, importance share, vertex count, or the number of output rows.
Later splitting, merging and constrained shape simplification may change output
row count, but must retain the selected-link provenance and report all these quantities.
Do not apply `keep` a second time in `get_primal`.

**Default selection connectivity policy: preserve all canonical vertices and
the connected-component partition of the chosen representation.** Do not join
its originally separate components. Report changes introduced by the preceding
centreline abstraction separately; legacy `keep=1` is not retroactively a claim
that buffering preserved the source network's topology. After selection, lock
anchors and reject shape operations that violate its graph contract.
Rank links by declared importance, form a maximum-importance spanning forest
per original component, then add the highest-ranked remaining links until `M`
is reached. Start the forest with explicitly protected links; they may contain
cycles. Stable canonical IDs break ties. A union-find implementation is
approximately `O(E log E)`, not repeated NetworkX bridge recomputation.
Every bridge is necessarily retained; removals come from redundancy, not an
unqualified shortest-link rule.

For an undirected graph without protected cycles, `M >= V - C` is necessary
and sufficient. With protected links, contract their connected components:
their edge count plus the further edges required to connect those components
is the exact undirected minimum under those protections. If the budget is
infeasible, raise a clear **KeepBudgetError** containing requested count,
minimum feasible count and the blocking protection policy. Do not quietly keep
more than 95%, drop a bridge, or report success after islanding a village.
A tree cannot lose 5% of its links while retaining all its vertices.

Connectivity alone does not guarantee useful routes. Optionally protect key
cycles and strategic river crossings; test stretch between protected terminals.
For directed/access-restricted routing, physical connectivity is only weak
connectivity: retain allowed-direction arcs and validate the requested
reachability/SCC contract. An undirected spanning forest is **not** a proof
that one-way routes survive. Initially label the default output a physical
cartographic network; reject an unsupported directed-preservation request
rather than pretending to be a turn-restriction-aware routing simplifier.

For the London **backbone** policy, explicitly permit dropping low-priority
vertices and dead-end branches. Protect major-road links, boundary gateways and
chosen terminals, then connect the surviving anchors **within each original
component** through lower-class roads when needed. A practical heuristic is
multi-source shortest paths between protected components followed by a weighted
component-graph spanning forest; rank hierarchy before distance, union the
chosen paths, and retain useful redundant routes within the remaining budget.
This is a constrained backbone/Steiner-style heuristic, not an exact globally
optimal solver. Connectors count against both link and byte budgets. If they
do not fit, report infeasibility and the achievable result size; never silently
relax the protected-road requirement.

Keep the strict policy for ordinary `keep=0.95`. Require an explicit
`connectivity="backbone"`/backbone mode for dropping terminals. This makes
aggressiveness a visible product choice rather than a side effect of geometry.

### Where road hierarchy comes from

Accept a GeoDataFrame as an additive API extension, or an explicitly aligned
importance/source-ID series for existing GeoSeries calls. Preserve old
`(this_gs, parameter)` calls. For OSM, take `highway` plus `*_link`, access,
oneway, bridge/tunnel/layer, ref and route membership; use an explicit
mode-dependent ranking, e.g. motorway/trunk/primary/secondary/tertiary ahead of
local/service roads for a strategic motor-road map. Slip roads must inherit
their connecting role; walking/cycling policies must not promote motorways.
Tags may live in `other_tags`; decode them once in the input adapter.

The actual Leeds file has usable `highway`: 7,894 service and 6,524 residential
features, but also cycleways, paths and links. The actual London GeoPackage
has `road_classification`, `road_function`, `primary_route`, `trunk_road`,
`road_structure`, and non-null `start_node`/`end_node` IDs. It does not need
an OSM spatial join just to rank roads.

**Bare GeoSeries fallback:** keep geometry-only centreline functionality. For
`keep < 1`, use equal semantic priority with a documented structural score
(backbone role, length as a tie-breaker, stable ID), and emit a visible
`hierarchy_unavailable` diagnostic. This is not "most important roads".
If the caller requests a hierarchy-based London/strategic-road policy without
attributes or explicit scores, fail with an actionable error. Do not download
OSM implicitly or invent road classes from width or length.

Track provenance inside the graph rather than transferring it by nearest-point
after everything has been dissolved. For unavoidable geometric conflation, use
distance-and-angle-gated shared length to allocate source contributions and
retain ambiguity/unmatched flags. Extensive values need apportioned sums;
intensive values need weighted means; categories need a declared rule.
The local `netweld` design is useful prior art, but its overlap kernel does not
by itself decide that a parallel road may safely be discarded.

Support an explicit source-ID-aligned eligibility/protection mask so a caller
can identify the subset needing simplification. Outside that mask, retain links
and lock their geometry/attachment nodes. If a canonical link contains both
eligible and protected sources, protection wins unless the caller explicitly
splits that policy boundary. Include protected links in N and in the feasibility
calculation; do not interpret `keep=0.95` as removing 5% of an unspecified
eligible subset. Automatic ambiguous-corridor detection can use nearby,
same-level, similarly oriented overlaps, but should expose its candidate mask
rather than make unreviewable conflation decisions.

### What the 1% London target means, with a measured feasibility check

Measure **physical bytes** of a specified delivered artifact against a named
input artifact. Separately report features, canonical/retained links, elementary
segments (`sum(vertices - 1)` for open line parts), vertices, geometry payload,
attribute payload, sidecars and container/index overhead. A 99% vertex reduction
is not a 99% link reduction. Removing repeated text attributes or compressing
the same data is not evidence of aggressive network selection.

Use two ledgers: (a) actual shipped bytes, including all required provenance
files and metadata; (b) normalized same-format bytes with matching coordinate
precision, attribute schema and compression, to isolate algorithmic reduction.
Do not compare gzipped output to uncompressed input and call that all
simplification. RFC 7946 GeoJSON output should be longitude/latitude with declared
rounding; a projected-coordinate JSON size probe is only a probe.

The local London GeoPackage is **88,240,128 bytes**, so 1% is **882,401 bytes**.
A single read took **1.345 s wall / 1.344 s CPU**. It contains 210,997 features,
1,047,730 vertices and 18,525.736 km. Its source-node multigraph took
**0.564 / 0.564 s** to construct in Python: 168,603 vertices, 210,997 edges,
84 components, 42,724 degree-one nodes. These are input features, **not yet
canonical deduplicated links**. Keeping all those nodes needs at least 168,519
edges (79.9% of the raw edge count), demonstrating why a tiny backbone needs
an explicit different terminal policy.

| London hierarchy subset | Raw links | Vertices | Length, km | Subset components / original components represented |
|---|---:|---:|---:|---:|
| Motorway + A roads | 25,721 | 128,379 | 2,003.500 | 4 / 2 |
| Motorway + A + B roads | 32,580 | 163,065 | 2,521.301 | 6 / 3 |

Thus filtering major roads alone introduces disconnection **within originally
connected components**. Never join the genuinely separate original components
just to make the result look connected.

An in-memory size-only experiment used compact projected GeoJSON, empty
properties, no IDs, and no connectivity repair. Simplifying every selected
input feature to 20 m still left Motorway+A at 3,253,031 bytes and A+B at
4,115,965 bytes. Most features were already near two vertices; simplification
alone could not remove their row overhead. Next, GEOS line-merge followed by
20 m simplification gave:

| **Size proxy, not a deliverable** | Motorway + A | Motorway + A + B |
|---|---:|---:|
| Merged features | 5,849 | 7,454 |
| Vertices | 15,147 | 19,211 |
| Geometry-only 2D WKB payload, bytes | 294,993 | 374,462 |
| Compact projected GeoJSON, bytes | 812,475 | 1,032,773 |
| Relative to original 88.24 MB GeoPackage | 0.92% | 1.17% |
| Relative to same-serializer full geometry-only network | 2.03% | 2.58% |

The normalized full geometry-only JSON was **39,964,742 bytes**; its separate
accounting pass took **1.299 / 1.299 s**, peak RSS 223.6 MiB. The hierarchy
selection and multiple size probes together took **1.272 / 1.272 s** for
Motorway+A and **1.468 / 1.468 s** for A+B, peak process RSS 586.3 MiB.
The merge experiment deliberately ignored attribute and grade boundaries; a
20 m per-line simplifier is not a topology validator. These are optimistic
size proxies and cannot be used as proof of a finished connected output.

**Conclusion:** approximately 1% of this attribute-rich input is plausible for
a compact strategic map with chain contraction and a small schema; it has
not been demonstrated with required attributes, level separation, connectors,
safe geometry and provenance included. Even the optimistic A+B GeoJSON proxy
exceeds the budget. Binary coordinate encoding may leave room for essential
attributes, but a GeoPackage's tables/indexes also have fixed overhead.
Report a Pareto curve of bytes versus protected-road coverage, route stretch
and hierarchy threshold instead of promising the same 1% for every format.
`keep=0.95` is a separate request: it generally cannot also mean a 1%-link
backbone, although attributes/chain contraction may greatly reduce bytes.

The London bounds are about 58.926 x 45.892 km. Adding an approximate 8 m buffer
would give a **45,909 x 58,942**, **2.706 billion-cell** default raster:
2.52 GiB for just one uint8 array. Scaling the observed Leeds high-water mark
by cell count gives about **61 GiB**; that is a rough memory extrapolation,
not a London benchmark or an exact predictor of polygon/object costs. No
London raster run was attempted. The local 106,898,554-byte OSM PBF was located
but not parsed: it also contains non-road data and is already compressed, so
1% of that file is a different and misleading road-simplification denominator.

## Options

Effort below is estimated engineer-days for one experienced geospatial/Rust
developer, including focused tests and documentation; production wheels and
platform troubleshooting can add time. Ranges are not measured delivery dates.
Only the explicitly marked microbenchmarks above establish measured speed-ups.

1. **Replace pixel geometry with array graph tracing; keep existing raster kernels.**
   **Mechanism:** identify integer foreground coordinates once; enumerate
   neighbours without Shapely; trace maximal degree-two chains, preserving
   diagonals, junctions and isolated cycles. Allocate geometries only for traced
   chains. Use numeric endpoint IDs, union-find for short clusters and numeric
   centroid accumulation. Retain rasterio, scikit-image and GEOS where they
   already run compiled code. Delete the old point/square/pixel-line graph
   implementation when parity passes.

   **Speed:** measured adjacency-only ratios are 2.6-15.5x, but the Leeds
   end-to-end prediction is only 1.18x for that change alone. Eliminating the
   full 21.378 s pixel-geometry block for free would give 1.73x; retaining
   numeric tracing costs lowers that. Also removing other graph/frame costs
   leaves 25.591 s of setup/thinning, an absolute **1.98x ceiling** if those
   stages are untouched. Estimate **1.4-1.9x Leeds overall**, and only
   **1.1-1.2x Doncaster**, depending on tracing cost. This is a valuable first
   increment, not the sponsor's drastic-speed solution.

   **Fidelity/risk:** low-to-medium if the exact pixel adjacency, short-cluster
   policy and grid are retained; tracing rings, diagonal triangles and centroid
   spokes needs explicit tests. Matching pair arrays is not final-network
   parity. **Effort:** 4-7 days. **Debt:** removes most object/Pandas graph
   machinery without introducing another backend; leaves global raster and
   buffer costs. **Keep/1%:** can host the common graph selector, but by itself
   has neither hierarchy nor a byte-budget policy. Must not prune pixel edges
   to implement `keep`. Does not solve London size alone.

2. **One Rust graph/provenance core, with explicit centreline and backbone policies
   (recommended).**
   **Mechanism:** thin Python adapters normalize CRS and pass contiguous
   coordinates/offsets, attributes and source IDs in batches through PyO3.
   Rust owns canonical links, noding/deduplication, stable IDs, union-find,
   adjacency, selection, chain contraction and provenance. Start with NumPy
   arrays/WKB at the boundary; add Arrow only if bulk attribute throughput
   warrants it. No Python callback per vertex and no subprocess/JSON round trip.
   GEOS can remain the robust buffer/Voronoi implementation rather than being
   reimplemented merely to make every dependency Rust.

   Existing calls retain their named centreline algorithm by default. An
   explicit **backbone/network mode** preserves already-good centrelines,
   selects on the graph and uses endpoint-locked simplification; it does not
   rasterize a whole county. Local ambiguous corridors can request the same
   raster/Voronoi kernels used by compatibility mode. Treat bridge/tunnel
   partitions separately. This is one core with several real algorithms,
   not a legacy Python engine plus an unrelated fast engine.

   **Speed:** estimate **10-50x versus the 50.55 s Leeds raster baseline for
   the graph/backbone workload** after replacing global image/object work,
   subject to correct noding and connector costs. Reasoning: the raw Leeds
   geometry has 134,806 vertices versus 144 million raster cells and 5.2 million
   temporary geometries; even Python constructed London's much larger
   source-ID graph in 0.564 s. That graph timing excludes canonicalization,
   geometric conflation, routing repair and serialization and is **not** a
   full simplifier benchmark. This speed range is an architectural estimate,
   not Rust magic and **not output-equivalent raster acceleration**.
   Compatibility mode initially inherits option 1's smaller gains; faster
   rasterization/tiling is separate work. End-to-end I/O limits short jobs.

   **Fidelity/risk:** medium for compatibility adapters, high if geometric
   noding or aggressive policy is silently substituted. Gate the latter
   explicitly and preserve reference outputs for the former. Unknown OSM
   levels/turn restrictions require declared limits. **Effort:** 18-30 days
   total, including option 1, initial hierarchy selection, bindings and tests;
   full turn-restriction-aware routing preservation is outside this estimate.
   **Debt:** replaces graph ownership and attribute handling centrally; remove
   duplicated Python implementations and NetworkX rather than keeping fallback
   dispatch forever. **Keep/1%:** implements the exact canonical-link and
   connectivity contracts above; best fit for hierarchy-aware London reduction
   and compact, auditable output. A 1% target can still be infeasible.

3. **Full compiled raster backend with bounded-memory component/tile execution.**
   **Mechanism:** Rust handles buffer-to-mask conversion, sparse/blocked storage,
   thinning and integer graph tracing; process occupied regions only, run a
   bounded number of independent tiles concurrently, and stitch by global
   grid/node IDs. Avoid materializing a global union when an equivalent
   per-shape mask union can be demonstrated. Preserve morphology semantics
   before switching thinning algorithms or drawing direct line strokes.

   **Speed:** estimate **2-8x overall**, highly dependent on rasterization,
   occupancy, tile overlap and worker count. There is no measured compiled
   prototype here. Doncaster's 2.5% filled mask offers substantial empty-area
   avoidance; Leeds still needs most tiles. Rasterio/scikit-image/GEOS are
   already compiled, so changing the wrapper language alone might yield almost
   nothing. Memory becomes bounded by active tile area rather than London
   bbox area, at the cost of halos and seam state.

   **Fidelity/risk:** medium-to-high. A fixed `3 * buffer` halo is not a proof
   of equivalent global skeletonization for wide junctions or long connected
   cavities. Require cross-tile state/reconciliation or whole-component
   fallback with an explicit resource limit. Parallelism must not change the
   grid origin, tie-breaking or component joins. **Effort:** 20-35 days.
   **Debt:** removes raster object debt but creates ownership of a specialised
   image engine; larger maintenance surface than reusing tested kernels.
   **Keep/1%:** still needs the graph/hierarchy selector from option 2. Good
   for generic rail/centreline fidelity, not a substitute for selecting major
   roads. It keeps the area-cost primitive the London policy should avoid.

4. **SedonaDB SQL as the primary engine.**
   **Mechanism:** read a columnar network, project/filter/normalize geometry,
   retain attributes and perform spatial joins in SQL; group duplicates and
   line-merge where appropriate. A custom batch graph operator is still needed
   for canonical topology, connectivity-constrained pruning and centreline
   extraction. Do not materialize every pixel as a SQL point and spatially
   self-join it; that reproduces the current algorithmic problem.

   **Speed:** **unmeasured**. A planning estimate of **1-3x for vector
   preprocessing** is plausible from batching and parallel execution, but
   startup/conversion overhead may make small jobs slower. This is not a
   total-network prediction. Generic spatial-join benchmark wins do not imply
   gains in thinning, sequential graph selection or GEOS union. If a custom
   operator avoids the raster entirely, its gain comes from option 2's
   algorithm, not SQL syntax.

   **Fidelity/risk:** high for a claimed drop-in, lower for relational attribute
   processing. The reviewed SQL reference provides `ST_Union_Agg`,
   `ST_LineMerge`, buffer and spatial predicates; it does not establish a
   compatible thinning/Voronoi/link-budget graph operator. SedonaDB is not
   SedonaSpark, and the latter's function inventory cannot be assumed here.
   **Effort:** 5-8 days for an honest preprocessing/parity spike; 25-45 days
   for a full replacement with missing graph kernels. **Debt:** SQL is clear
   for hierarchy/attribute rules, but engine views, geometry conversion and
   custom graph state can create more layers than they remove. The local
   `netlos` is useful parity prior art, not a current performance endorsement.
   **Keep/1%:** supports ranking/filtering and output projection well; row
   `ORDER BY importance LIMIT ...` alone fails connectivity and canonical-link
   semantics. It needs the same connector/budget core as option 2.

5. **Rust core embedding selected SedonaDB crates.**
   **Mechanism:** share useful geometry traits/WKB/Arrow plumbing and expose
   the graph core either as a direct library or a SedonaDB batch operator.
   Audit dependencies first: at the reviewed upstream revision,
   `sedona-geometry` has a relatively small traits/WKB dependency set, but
   `sedona-geo` depends on Arrow, DataFusion expression/common crates and
   multiple Sedona crates. Importing the latter is not a lightweight way to
   obtain a general-purpose road simplifier.

   **Speed:** **no extra speed-up justified over option 2** for the same graph
   algorithm. Estimate similar kernel throughput; Arrow may reduce bulk
   conversion, whereas query-engine adapters may add overhead. No Rust or
   SedonaDB build was performed in this exploration. **Fidelity/risk:** same
   geometric risks as option 2 plus upstream API/version churn and dependency
   compatibility. Do not fork Sedona just to get a missing network operator.
   **Effort:** 3-5 days dependency/API spike, roughly 23-40 days total if this
   becomes the implementation route. **Debt:** good only if it removes code
   we would otherwise maintain; poor if it makes a 1,100-line package carry a
   query engine for a few primitives. **Keep/1%:** identical to option 2;
   those semantics are custom domain logic, not provided by a geometry crate.
   Prefer standalone `geo`/`geo-traits`/`rstar` or GEOS where sufficient.

6. **Vector medial-axis/Voronoi rewrite instead of a raster-centred rewrite.**
   **Mechanism:** retain the existing Voronoi function name and buffer-boundary
   policy, but generate sites in arrays, call GEOS in bulk and trace/prune its
   output in the common graph core. A later segment-site Voronoi/medial-axis
   kernel could avoid dense boundary sampling, but is a different algorithm.
   Cost tracks boundary complexity/site count rather than empty bbox area.

   **Speed:** estimate **2-5x versus the current Voronoi path** after reducing
   millions of profiled Python/frame calls. No equivalent-output prototype was
   timed. The 1.462 s GEOS diagram inside the 7.172 s profiled Doncaster run
   is not removed by Rust bindings; exact gain requires unprofiled measurement.
   This is not a claim that Voronoi beats raster on dense Leeds.
   **Fidelity/risk:** medium for batched existing GEOS semantics; high for a
   new segment medial axis, degeneracies and changed clipping/snap behaviour.
   **Effort:** 8-15 days to batch the existing method, 20-35 for a new robust
   geometric kernel. **Debt:** removes object/frame work and shares graph
   infrastructure, but do not keep two competing Voronoi implementations.
   **Keep/1%:** uses the same selector/provenance pipeline; contour geometry
   alone has no road hierarchy. Attractive for sparse rail corridors, not
   the first London investment.

## Recommendation and the first three concrete steps

Choose **option 2, starting with option 1**. Keep proven compiled geometry/image
libraries at first. A single Rust graph core is the durable investment;
reimplementing every geometric primitive or introducing SQL as the obligatory
runtime is not. Target drastic performance in an explicitly different
graph/backbone policy, and prove centreline compatibility independently.
If strict pixel-equivalent output must also be 10x faster, the present evidence
does not justify that promise: fund option 3's rasterization/memory spike before
committing to it.

1. **Freeze contracts and build the oracle (2-3 days, included above).**
   Pin this revision and inputs; record actual API types, CLI layers, scale,
   snapping and `knot`/`segment` behaviour. Turn the tiled failure and endpoint
   pair deduplication issue into explicit regression cases. Specify canonical
   link IDs, strict/backbone connectivity and `KeepBudgetError`; add golden
   reference comparisons before replacing internals. Fix known backend defects
   in a separately identified reference patch, never by silently changing the
   oracle. Establish same-output and backbone benchmark tracks.
2. **Replace, do not wrap, the pixel/graph backend (4-7 days).**
   Implement integer adjacency and chain tracing in the core, including rings
   and short-cluster centroids; batch geometry creation and endpoint assignment.
   Remove the old point/square/pixel-LineString path after parity passes.
   Measure whole pipeline plus RSS, not just an attractive helper. The first
   target is at least 1.5x on Leeds with no topology regression; the Amdahl
   limit explains why this is not the final speed milestone. Profile raster
   allocation/shape ingestion next if compatibility throughput is the priority.
3. **Deliver an attributed, connected London backbone (remaining core work).**
   Start from GeoPackage source node IDs and road classes, not a 2.7-billion-cell
   raster. Add the common selector, provenance and endpoint-locked simplifier;
   repair major-road connections within original components; measure a
   threshold/tolerance/byte-budget frontier. Gate release on exact `keep`
   accounting, protection/reachability checks and honest actual artifact bytes.
   Use Leeds to target at least 10x compute improvement in backbone mode, while
   reporting its changed output contract; then measure complete London I/O +
   processing + serialization. Do not claim that target is already achieved.

### Public surfaces and migration

Retain `skeletonize_frame`, `voronoi_frame`, `skeletonize_tiles`, `get_primal`,
the three console-script names and `parenx`'s existing algorithm dispatch.
Keep old positional arguments and parameter dictionaries working. Add a
keyword `keep=0.95` to the three simplifiers, with absent value resolving to
1.0; accept `parameter["keep"]` for dictionary callers and reject conflicting
explicit settings. The three parsers gain `--keep`, hierarchy/backbone controls
and explicit compact-output selection; the wrapper must forward quoted
arguments unchanged. `get_primal` remains the chord conversion, not another
selection stage. Document the distinction between its potentially unsafe
straight-line representation and a routing-preserving polyline network.

Default calls continue to execute the named centreline operation. A clearly
documented `parameter["mode"] = "backbone"` opt-in uses the shared graph policy,
not a fake faster skeletonizer; all adapters dispatch to the same core.
GeoDataFrame input retains source attributes through a documented mapping;
geometry-only callers keep their existing result type. Put stable lineage and
counts in an explicit report/mapping rather than relying solely on fragile
`GeoDataFrame.attrs`. Any report required to interpret the exported network
counts toward the delivered byte budget.

Delete superseded graph helpers and duplicated adapter logic once reference
coverage is sufficient. Do not ship permanent `python` versus `rust` algorithm
forks with divergent defaults. Keep the old implementation only as a pinned
development oracle, not a silent runtime fallback. Packaging must explicitly
decide the supported Python range (currently advertised as >=3.8), build/test
wheels, and surface native import failures rather than rerouting to an
unmaintained slow path.

## Verification: how to prove faithfulness

There are two different obligations: **preserve existing centreline behaviour**
where requested, and **meet the new backbone's declared invariants**. Matching
an old bug is not the same as preserving intended functionality.

| Check | Concrete acceptance/evidence |
|---|---|
| Reference compatibility | On both small bundled inputs, compare old/new skeleton and Voronoi, `get_primal`, and tiled output after the isolated reference bug fix. Canonicalize direction/order, then compare normalized coordinates/WKB where exact parity is intended. Cover default and non-unit scale, nonzero simplify, `knot` and `segment`. |
| Raster intermediate parity | Identical masks and foreground pixel sets on the fixed grid; identical sorted eight-neighbour edge pairs; equivalent traced paths, junction classes and centroid coordinates. Include diagonal contacts, 2x2 knots, rings, tiny components and boundary pixels. |
| Geometry tolerance | For a faithful compiled substitution, require <=0.1 m endpoint displacement after snapping, equal graph incidence, and total length within 0.1% unless an approved bug fix explains the difference. Prefer exact results; these are proposed gates, not evidence already obtained. |
| Topology | Compare component membership, node degree distribution, endpoints, bridges, parallel links, cycle rank and loops on a level-aware multigraph. No new cross-level connection; no deleted protected bridge. Do not equate feature count or a simple-graph component count with topology parity. |
| `keep` | Count selected canonical IDs, not output rows; exactly `ceil(keep*N)` or a typed infeasibility error. Test keep=1, 0.95, tiny positive values, invalid/NaN input, a tree, cycle, disconnected network, protected cycles and two distinct routes sharing endpoints. |
| Determinism and dedup | Input row permutation/reversed geometry must not change physical output or selected IDs. Equivalent resegmentation with the same attributes must not change N. Preserve conflicting attributes with diagnostics; never silently collapse levels or alternate paths. |
| Length and coverage | Report total length ratio and per-class retained length; compute both input length within an output buffer and output length within an input buffer, at 1, 2, 8 and 16 m. Include length-weighted 50th/95th/max lateral distance, not just point samples or nearest endpoints. |
| Backbone fidelity | Retain 100% of explicitly protected links/terminals; no new disconnection between originally connected protected anchors. Measure shortest-path stretch on all small-fixture terminal pairs and a seeded, class/region-stratified London sample. Proposed initial stretch gate: 95th percentile <=1.10, maximum <=1.25, with infeasible cases reported rather than hidden. |
| Simplified shape | Lock junction/terminal coordinates, preserve level-aware incidence, check for introduced same-level crossings and corridor excursions; reject/relax a simplification that fails. A per-LineString Douglas-Peucker call alone does not guarantee network topology. |
| Tiles | Compare one tile versus multiple tile origins/sizes and worker counts; test roads parallel to seams, junctions on seams, large roundabouts and buffer cavities. Require stable global endpoint IDs and no seam-only links absent from the intended graph. |
| Attributes/provenance | Every output link has selected-source membership; every retained canonical link has output representation. Check transfer totals/rules, nulls and ambiguity. Road hierarchy joins must not match the nearest unrelated parallel street. |
| API/CLI | Assert actual GeoSeries/GeoDataFrame types, CRS and empty-output schema; exercise all console names and the wrapper, filenames with spaces, parameter conflicts, expected legacy layers and the compact export layout. |
| Performance/bytes | On a quiet follow-up host, record wall and CPU per stage, total including conversion/I/O, peak native RSS, counts and actual output artifact bytes. Compare the same input and declared policy. Use repeated small runs and one full run per large configuration; do not mix profiled/unprofiled results or report a microbenchmark as end-to-end throughput. |

For context, this exploration's **endpoint-only simple-graph diagnostics** were:

| Input | Input components / degree-one nodes / cycle rank | Skeleton components / degree-one nodes / cycle rank |
|---|---:|---:|
| Princes Street | 4 / 66 / 310 | 2 / 95 / 120 |
| Doncaster | 23 / 190 / 119 | 1 / 39 / 31 |
| Leeds | 12,804 / 27,044 / 1,113 | 212 / 9,297 / 6,129 |

These diagnostics are not fully noded routing graphs: source features can meet
at interior vertices, parallel edges were collapsed by `nx.Graph`, and no
grade data was applied. In particular Leeds's 12,804 is **not** a claim that
its real road system has that many disconnected components. The table shows
why raw endpoint counts cannot establish fidelity, and why the oracle needs
canonical noding and multigraph checks. Coverage and path-stretch statistics
were not computed in this exploration; they are explicit next-version gates,
not claimed passes.

## Evidence sources and reproduction notes

Primary local sources: `src/parenx/shared.py` (`get_geometry_buffer`,
`get_source_target`, `get_primal`); `skeletonize.py` (`get_affine_transform`,
`get_skeleton`, `get_raster_line`); `voronoi.py`; `tile_skeletonize.py`;
`__init__.py`, the `parenx` shell wrapper and `pyproject.toml`. The fourth bundled
input, `rnet_3km_buffer.geojson`, was inventoried only: 20,478,671 bytes, 18,477
features, 59,819 vertices, 715.127 km, and bicycle-flow/quietness attributes.
It was not subjected to another large raster benchmark.

All Python commands used `.venv/bin/python -B` with
`PYTHONDONTWRITEBYTECODE=1`; the heavy harness used a foreground `timeout 540`
and one child per input. No dependency installation or Rust build was needed.
The stage sequence is the table above, calling the existing helpers except
that `get_raster_line` was expanded into its identical constituent operations
to time them. The integer-adjacency spike, expressed independently for
reproduction, was:

```python
row, col = np.nonzero(image)
width = image.shape[1]
flat = row.astype(np.int64) * width + col
parts = []
for dr, dc in [(0, 1), (1, -1), (1, 0), (1, 1)]:
    valid = ((row + dr < image.shape[0])
             & (col + dc >= 0) & (col + dc < width))
    source = np.flatnonzero(valid)
    dest = flat[source] + dr * width + dc
    target = np.searchsorted(flat, dest)
    inside = target < len(flat)
    source, dest, target = source[inside], dest[inside], target[inside]
    hit = flat[target] == dest
    parts.append(np.column_stack((source[hit], target[hit])))
edges = np.concatenate(parts)
```

Compare sorted `u * number_of_pixels + v` arrays with the existing undirected
non-self pairs, using int64 for these inputs. This tests the exact primitive,
not a visual approximation. An implementation should reuse foreground indices
and avoid the spike's second full-image scan.

Input SHA-256 fingerprints:

```text
rnet_princes_street.geojson  f6f1a8241bfccb6a157d63e44b106bff69f983d1c8137ae4851f2fdd787b4418
rnet_doncaster_rail.geojson  e72a7e0ed9a60ba9f4292a5efce621454b93eb6c61023e8b3d2d7a47cf4de2ea
osm_leeds.geojson           303a18326a64cf4a9bb08ebbd404af6ea32ad094967f283083cf8629505627fd
rnet_3km_buffer.geojson     f73a843648fc5c2732736aaf546c5b3e84e9447fefc526c4b413c046d9d1e214
```

Prior art inspected read-only at the user-specified local paths:

- `~/github/robinlovelace/junctions-rs/README.md` and `docs/benchmarks.md`:
  host-independent Rust domain core, thin PyO3 adapters and explicit CRS/level
  semantics. Its published 24.7x synthetic Rust/DuckDB result is **not**
  evidence for parenx; its documented real-data policy mismatches reinforce
  the need to distinguish geometry parity from candidate-policy parity.
- `~/github/atfutures/netweld/README.md`: exact distance/angle-gated shared-length
  measurement, an indexed kernel checked against brute force, and explicit
  extensive/intensive transfer. Useful model for provenance and testing;
  no netweld speed is attributed to this proposal.
- `~/github/atfutures/netlos/netlos/core.py`, `README.md` and `notes/PARITY.md`:
  SQL is effective for declarative classification, but the README says the
  project is superseded. Its parity report documents two point-selection
  distance mismatches despite identical downstream labels; no timings are
  reported there. This is a caution against claiming semantic equivalence
  solely from similar function names.

Upstream SedonaDB sources checked on 13 October 2026:
[README and architecture](https://github.com/apache/sedona-db/tree/ec16de6eadefea8aeefc4624c8e279d882542975),
[SQL function reference](https://sedona.apache.org/sedonadb/latest/reference/sql/),
[`sedona-geo` dependencies](https://github.com/apache/sedona-db/blob/ec16de6eadefea8aeefc4624c8e279d882542975/rust/sedona-geo/Cargo.toml),
and [`sedona-geometry` dependencies](https://github.com/apache/sedona-db/blob/ec16de6eadefea8aeefc4624c8e279d882542975/rust/sedona-geometry/Cargo.toml).
The source tree contains raster crates, while parts of its README still describe
raster functionality as coming soon. Neither observation proves that the
required skeletonization operation exists or matches parenx. Pin and test an
actual released API before choosing it; this exploration did not install or
benchmark SedonaDB. DuckDB availability alone is not evidence of SedonaDB
capability or performance.
