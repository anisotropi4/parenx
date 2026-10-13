# Implementation: issue #4 simplification (claude-opus-5-5)

Branch `4-speed-up-and-improve-simplification`, on top of `ec2f953`. Nothing is
committed. Python and numpy only, plus `scipy.sparse`. `scipy` is now declared in
`pyproject.toml`. It was imported directly but undeclared, relying on
`scikit-image` to pull it in. See "Review fix pass" below.

## Result

Second pass, extended to five datasets: the two small ones and the 1, 3 and 5 km
circles around central Leeds. With the default parameters the output has 10x to
39x fewer vertices per km than the frozen baseline. On the Leeds circles it is
1.08 to 1.22 times the vertex density of OS Open Roads on the same circle. The
end-point graph is unchanged by simplification on all five, with no non-simple
lines, no zero-length lines and no new crossings. The 2 m coverage of the input
is within 0.0025 of the baseline on all five.

`bench/simplify_check.py`, summary table (base is the frozen baseline, new is the
new default, OR is OS Open Roads):

| dataset | verts/km base | verts/km new | reduction | components | degree-3+ | within 2 m | OR verts/km | new / OR | wall s | CPU s |
|---|---|---|---|---|---|---|---|---|---|---|
| `princes_street` | 894.3 | 70.3 | 12.7x | 2 to 2 | 312 to 325 | 0.8216 to 0.8202 | - | - | 0.5 | 0.5 |
| `doncaster_rail` | 842.6 | 21.5 | 39.2x | 1 to 1 | 98 to 98 | 0.8690 to 0.8666 | - | - | 7.5 | 7.5 |
| `leeds_1km` | 918.1 | 89.2 | 10.3x | 29 to 29 | 969 to 983 | 0.7408 to 0.7453 | 82.3 | 1.08 | 1.4 | 1.4 |
| `leeds_3km` | 897.6 | 82.8 | 10.8x | 87 to 81 | 7,896 to 8,071 | 0.8287 to 0.8278 | 69.5 | 1.19 | 7.1 | 7.1 |
| `leeds_5km` | 893.7 | 77.7 | 11.5x | 142 to 134 | 16,147 to 16,441 | 0.8485 to 0.8481 | 63.8 | 1.22 | 17.0 | 17.0 |

Timing covers the whole `skeletonize_frame` at the default. The host is shared:
the load average was 17 to 20 on 32 cores during this run. The same Doncaster run
took 4.5 s in an earlier pass at load 5, so treat timings as plus or minus 50%.
CPU time tracks wall time here because the pipeline is single-threaded.

### Second pass changes

1. **Default tolerance is now one pixel, `1 / scale` metres**, not half a pixel
   diagonal. See "The `simplify` default" below for the numbers.
2. **Strip-wise rasterising** (`get_raster`, new, called by `get_skeleton`).
   `rasterio.features.rasterize` went from 0.10 s at 1 km to 4.7 to 5.5 s at
   5 km (three runs), about 50x for 25x the raster area. GDAL scans every polygon edge on every row of the
   polygon's bounding box. The buffered network is a few huge polygons, so the
   cost is rows x edges, which grows as the cube of the radius. The new code clips
   the polygons into strips of 256 rows with `clip_by_rect` and rasterises each
   strip. Strip edges lie on pixel edges, so the raster is almost identical.
   Correction: `bench/check_raster_parity.py` finds 0 differing pixels in 8 of 9
   comparisons, but 62 of 10.4 million pixels differ on `leeds_3km` at scale 1.
   My earlier claim of 0 on all five was wrong. Rasterise at 5 km: 5.5 s to 1.0 s. At 3 km: 1.4 s to 0.4 s.
3. **`bench/simplify_check.py`** runs the small datasets first, then the three
   circles, and ends with the summary table above. It reports the OS Open Roads
   verts/km and the ratio of the new output to it. It takes dataset names as
   optional arguments. The legacy stage is timed on the small datasets only. On
   the circles it would take most of the run, so it is skipped.

### Scaling, 1 km to 5 km

16.3x the network length, 24.7x the raster area. CPU per stage, measured before
the rasteriser fix at load about 18:

| stage | 1 km | 5 km | ratio | verdict |
|---|---|---|---|---|
| buffer and union (`shared.get_geometry_buffer`) | 0.44 s | 4.07 s | 9x | linear |
| rasterise | 0.10 s | 4.7 to 5.5 s | about 50x | superlinear, fixed (now 1.0 s) |
| `remove_small_holes` | 0.04 s | 1.4 s | 35x | about linear in area |
| `skimage` skeletonize | 0.15 s | 6.8 s | 45x | above area ratio, one run under load, not investigated |
| vectorise at simplify 0 | 0.27 s | 3.3 s | 12x | linear |
| `simplify_network` conflict check, per round | 0.06 s | 1.1 s | 18x | linear |

At 5 km the conflict check needs two rounds: 11 of 28,955 lines are put back
unsimplified, then the second round is clean. At 1 km it needs one round.

The other raster stages are roughly linear in raster area, which grows with the square of the
radius, faster than the network length. Avoiding that would mean skeletonizing
per polygon bounding box. It is not cheap, so I did not do it.

### Components on the Leeds circles

Components fall from 87 to 81 at 3 km and from 142 to 134 at 5 km. This is not a
loss. The new output matches the 8-connected components of the skeleton raster
exactly: 29, 81 and 137, where 3 of the 137 at 5 km are specks of 1 to 2 pixels.
Every buffered polygon still has at least one output line (checked at 3 km). The
baseline had more components because the old length filter dropped short links
between junctions and split components, as described below under "Change 2". The
buffered input has more parts (32, 92, 147) than the raster has components,
because buffer polygons less than a pixel apart touch once rasterised.

## Review fix pass (`explore/REVIEW-agy.md`)

All four checks were rerun on the final code. `/mnt/secondary/tmp/parenx_fix/`
holds the raw output.

1. **Finding 1, `get_conflict`.** Candidate pairs are now the union of the
   pairs that intersect in `line` and in `base`, deduplicated with `np.unique`.
   `line[i]` and `base[i]` are the same line, so the indices are shared. On the
   synthetic T-junction in `bench/check_conflict_pairs.py` the mask is now
   `[True, True]`, where it was `[False, False]`. That script now prints `not
   reproduced as described`, because its "current" row is the fixed code. On the
   real data it reports 0 differing geometries: `princes_street` 551 lines,
   `doncaster_rail` 173, `leeds_1km` 1,738. `simplify_network` on the synthetic
   pair now keeps the bowed line unsimplified. This is hardening for less noded
   input, such as tile seams or arbitrary input. It does not change any current
   output.
2. **Finding 3, `scipy`.** Added to `dependencies` in `pyproject.toml`.
3. **Finding 4, docs.** The README, the `--simplify` and `--knot` help text, and
   the `skeletonize_frame` and `skeletonize_tiles` docstrings now say that
   `--simplify` controls line simplification only. Knot collapse and spur
   dropping run unless `--knot` is passed. The lossless output is
   `--simplify 0 --knot`. The help text says this for `skeletonize.py` only.
   `tile_skeletonize.py` also clips and fills tile seams, so I did not call its
   output lossless.
4. **Finding 2, the hub.** I corrected the comment and the claim above. Then I
   measured the review's variant, which prefers degree 3+ pixels. It is one sort
   key in `set_hub`. A pixel's degree is its count of chain ends, because each
   pixel pair is in exactly one chain. Effect on hub choice, at the default:

   | dataset | clusters | no junction pixel | old hub not degree 3+ | hub moved |
   |---|---|---|---|---|
   | `princes_street` | 274 | 0 | 0 | 0 |
   | `doncaster_rail` | 88 | 0 | 0 | 0 |
   | `leeds_1km` | 835 | 0 | 1 | 1 |
   | `leeds_3km` | 6,915 | 0 | 3 | 3 |
   | `leeds_5km` | 14,247 | 1 | 7 | 6 |

   Both oracles give the same numbers with and without the variant, on all five
   datasets: vertices, components, degree-3+, degree-1 and coverage at 1, 2 and
   5 m, to the printed 4 decimal places. The few moved hubs shift no metric at
   this precision. It does not regress, and it matches the intent better, so I
   kept it. `bench/simplify_check.py` still finds no non-simple lines, no
   zero-length lines and no crossings on all five.
5. **Finding 5, revert cost.** It is documented in the `simplify_network`
   docstring and measured at the default. Lines put back that simplification
   would have changed, with the vertices kept as a result: 0 on `princes_street`,
   `doncaster_rail` and `leeds_1km`. On `leeds_3km`, 2 lines and 2 vertices of
   52,254. On `leeds_5km`, 11 lines and 35 vertices of 113,043. This is an upper
   bound on what the conservative revert costs. Retrying is not worth it here.
6. **Finding 6, nits.** `get_connected_class` and `get_centre_edge` are marked
   legacy. `networkx` is now imported inside `get_connected_class`, the only
   user, so the current pipeline does not import it. `get_raster_line` still
   works: `bench/simplify_check.py` timed the legacy stage on the two small
   datasets. I left the `geoms` unpacking idioms as they are.

Evidence after the fix pass. These are unchanged from before it, number for number.

`bench/simplify_metrics.py check` (`simplify` 0.0, `knot` False):

| dataset | vertices | components | degree-3+ | degree-1 | within 2 m |
|---|---|---|---|---|---|
| `princes_street` | 33,225 to 15,575 | 2 to 2 | 312 to 325 | 93 to 92 | 0.8216 to 0.8203 |
| `doncaster_rail` | 48,824 to 24,494 | 1 to 1 | 98 to 98 | 39 to 39 | 0.8690 to 0.8708 |
| `leeds_1km` | 69,067 to 27,824 | 29 to 29 | 969 to 983 | 478 to 474 | 0.7408 to 0.7482 |
| `leeds_3km` | 592,569 to 268,631 | 87 to 81 | 7,896 to 8,071 | 3,204 to 3,192 | 0.8287 to 0.8303 |
| `leeds_5km` | 1,360,918 to 625,521 | 142 to 134 | 16,147 to 16,441 | 7,170 to 7,111 | 0.8485 to 0.8500 |

`bench/simplify_metrics.py check-default` (default 1 px tolerance):

| dataset | vertices | verts/km | components | degree-3+ | within 2 m |
|---|---|---|---|---|---|
| `princes_street` | 33,225 to 2,483 | 70.3 | 2 to 2 | 312 to 325 | 0.8216 to 0.8202 |
| `doncaster_rail` | 48,824 to 1,184 | 21.5 | 1 to 1 | 98 to 98 | 0.8690 to 0.8666 |
| `leeds_1km` | 69,067 to 6,460 | 89.2 | 29 to 29 | 969 to 983 | 0.7408 to 0.7453 |
| `leeds_3km` | 592,569 to 52,254 | 82.8 | 87 to 81 | 7,896 to 8,071 | 0.8287 to 0.8278 |
| `leeds_5km` | 1,360,918 to 113,043 | 77.7 | 142 to 134 | 16,147 to 16,441 | 0.8485 to 0.8481 |

`bench/check_raster_parity.py`: 8 of 9 comparisons are pixel-identical.
`leeds_3km` at scale 1 has 62 differing pixels of 10.4 million. This is the strip
rasteriser, which this pass does not touch, and it matches the parent agent's
finding in commit `d6a90a9`.

## What changed

All in `src/parenx/`.

### `skeletonize.py`

1. **Pixel graph from integer arrays** (`get_pixel_edge`, `get_pixel_index`).
   The foreground `(row, col)` indices become sorted flat ids. Four shifts, east,
   south, south-east and south-west, are looked up with `searchsorted`, with a
   column-wrap guard. This gives every undirected 8-neighbour pair, diagonals
   included. It replaces the Shapely `Point`, square buffer, STRtree `covers` and
   2-point `LineString` per pair.
2. **Degree-2 chain contraction** (`get_pixel_chain`). A CSR adjacency is built
   with `argsort`. A walk goes from every pixel whose degree is not 2 and stops at
   the next such pixel. Leftover unvisited edges are isolated pixel cycles and
   become closed rings. The walk is a plain Python loop over lists. It is linear
   in skeleton pixels and is not the bottleneck on these datasets.
3. **De-knotting kept, with two deliberate changes** (`set_hub`, `drop_spur`).
   As before, chains of 2 px or less are clustered by connected components, and
   each cluster becomes one hub. Each cluster pixel that a longer chain ends at is
   joined to the hub by one straight spoke. Lines of 2 px or less are then dropped.
   - Change 1: the hub is a cluster pixel, not the free centroid. My first
     version used the free centroid. On Princes Street a centroid landed exactly
     on a pixel of another chain, which made 4 lines non-simple. The hub is now
     the cluster junction pixel (degree 3 or more) nearest the centroid, or the
     nearest cluster end pixel if the cluster has no junction pixel. Correction:
     I first wrote that this rules out the problem by construction. It does not.
     Before the review fix the hub was the nearest cluster end pixel of any
     degree, which can be a degree-1 leaf, and even a junction hub does not stop a
     spoke from crossing another line. The guarantee is only that the hub is a
     pixel of its own cluster, not a free point. The oracles find no non-simple
     lines and no crossings on the five datasets.
   - Change 2: the 2 px length filter now only drops short lines that end at a
     degree-1 node, or short self-loops. The old filter dropped any merged line of
     2 px or less. That includes a short link between two junctions, which can
     split a component.
   - I also tried dropping diagonal pairs that are already joined through a shared
     4-neighbour. It gave the same coverage but moved Princes Street degree-3+ to
     334, against 325 without it. I removed it, so the full 8-neighbour graph is
     kept, as the brief asks.
4. **Lossless vertex removal** (`drop_collinear`). Interior vertices that lie
   exactly on the straight line between their neighbours are dropped. Pixel runs
   become single segments. The point set is unchanged. This always runs, even
   with `simplify` 0.0.
5. **Topology-safe simplification** (`simplify_network`, `get_conflict`). After
   merging through degree-2 nodes, every line ends at a junction or an end. Each
   line is simplified on its own with vectorised Douglas-Peucker
   (`shapely.simplify(..., preserve_topology=False)`). This always keeps both
   end-points exact, so the junction anchors cannot move. Then a check runs. A
   line is a conflict if it is no longer simple, if it is a ring with fewer than
   4 coordinates, or if it meets another line other than where the unsimplified
   lines meet (STRtree pairs, then `equals` on the pairwise intersections).
   Conflicting lines are put back unsimplified, and the check repeats until it is
   clean. Both lines of a pair are put back and not retried, even if only one
   moved. Candidate pairs are those that intersect in either the simplified or
   the unsimplified network, so a meeting that simplification removes is also
   found (review Finding 1). It always ends, because each round reverts at least one more line and
   the unsimplified network passes. So no crossing or self-intersection can come
   from simplification, by construction.
   - My first version used GEOS `TopologyPreservingSimplifier` on the whole
     network as one `MultiLineString`. It gave the same small-dataset results, but
     took 70 s CPU on Leeds. The check-and-revert version takes 2.5 s there.
6. The pixel-to-metre affine transform is applied to numpy coordinate arrays, then
   `set_precision(0.1)` as before.
7. `skeletonize_frame` keeps its signature. It still accepts a bare GeoSeries and
   returns a GeoDataFrame with a `geometry` column, as before. `knot=True` returns
   the contracted chains without hub collapse, as before.
8. The old `get_raster_point`, `get_raster_line` and `sx_to_nx` stay unchanged and
   are marked legacy. `skeletonize_frame` no longer calls them.
   `bench/simplify_check.py` and `bench/profile_stages.py` use them to time and
   compare the old stage.

### The `simplify` default (behaviour change)

- A missing `"simplify"` key, or `None`, now means **one pixel, 1 / scale
  metres** (1 m at scale 1). The CLI default for `skeletonize.py` and
  `tile_skeletonize.py` changed from `0.0` to `None`. `get_default_simplify` and
  `get_tolerance` implement it.
- An explicit `0.0` turns off line simplification. Correction: this is not
  lossless on its own. Unless `knot` is True, knots are still collapsed to a hub
  and short spurs are still dropped. The lossless output, the pixel skeleton with
  only exactly collinear vertices dropped, is `simplify` 0.0 with `knot` True
  (`--simplify 0 --knot`). The oracle `check` mode runs `simplify` 0.0 with
  `knot` False, so it measures "no line simplification", not lossless.
- First pass: I set sqrt(0.5) px, half a pixel diagonal. On the Leeds circles that
  gave 135, 126 and 121 verts/km, 1.6 to 1.9 times Open Roads. So I moved it to
  1 px. Justification: the skeleton of an even-width band can sit on either of two
  centre pixels, so its position is only known to one pixel. A 1 px tolerance
  removes exactly the one-pixel staircase jogs. It is not a cliff: verts/km falls
  smoothly from 0.9 px to 1.1 px (Leeds 1 km: 96.7, 90.5 at 0.99, 89.2, 89.1 at
  1.01, 86.1), so float rounding at exactly 1.0 does not matter.
- Sensitivity, from the saved skeletons. Topology (components, degree-3+, cycle
  rank) is identical at every tolerance in this table, on every dataset.

  | tolerance [px] | 0.7071 | 1.0 | 1.5 | 2.0 |
  |---|---|---|---|---|
  | Princes Street verts/km | 123.6 | 70.3 | 58.4 | 50.8 |
  | Doncaster rail verts/km | 81.2 | 21.5 | 17.5 | 15.6 |
  | Leeds 1 km verts/km | 136.9 | 89.2 | 76.3 | 68.4 |
  | Leeds 3 km verts/km | 127.7 | 82.8 | 70.6 | 63.4 |
  | Leeds 5 km verts/km | 122.4 | 77.7 | 66.1 | - |
  | Leeds 1 km within 1 m / 2 m | 0.532 / 0.749 | 0.516 / 0.745 | 0.495 / 0.742 | 0.465 / 0.727 |
  | Leeds 3 km within 1 m / 2 m | 0.595 / 0.829 | 0.585 / 0.828 | 0.564 / 0.822 | 0.541 / 0.805 |
  | Leeds 5 km within 1 m / 2 m | 0.662 / 0.849 | 0.645 / 0.848 | 0.617 / 0.844 | - |

- Judgement. At 1 px, 3 km and 5 km land in the 60 to 85 band. 1 km lands just
  above it at 89.2, 1.08 times its own Open Roads figure of 82.3. 1.25 px would
  bring 1 km to 81.6. I did not chase that. 1 px has a physical reason and 1.25 px
  does not. 2 m coverage costs at most 0.004 at 1 px. 1 m coverage costs 0.015 to
  0.02, which is the expected price of moving vertices by up to 1 m. At 2 px, 2 m
  coverage starts to fall (3 km: 0.829 to 0.805), so the band is reachable without
  distortion, but not much beyond it.
- The ratio to Open Roads rises with extent, 1.08 to 1.22. That is because Open
  Roads gets sparser with extent (82 to 64), faster than this output (89 to 78).
  The outer circles add long suburban and rural roads that Open Roads draws as long
  straight links. The skeleton keeps more curvature on them.
- Doncaster rail at 21.5 verts/km is far below the band. It is a rail network of
  long, gentle curves, not a road network. Its 2 m coverage moves 0.8690 to 0.8666.
- The README "Skeletonization" section and API example document the default.
  The API example now passes `"simplify": None`. Explicit `0.0` would give the
  dense lossless output.
- `voronoi.py` is unchanged and keeps its own `--simplify` default of 0.

### `tile_skeletonize.py` and `shared.py`

- `get_source_target` now accepts a GeoDataFrame and uses its `geometry` column.
  This fixes the `DataFrame.explode() missing 1 required positional argument`
  crash in `get_gap_fill`. `skeletonize_tiles` runs again: Princes Street gives
  556 lines and 2 components at the default.
- The final per-line `r.simplify(tolerance)` in `skeletonize_tiles` is replaced by
  `simplify_network` with the same default rule. Per-line simplification could
  let lines cross, but end-points were already kept.

## Explaining the near-misses precisely

- **Doncaster components: 1 against 3 noded input components.** The 8 m buffer
  joins input parts closer than 16 m. The buffered input that is skeletonized is
  one polygon (`buffered input 1` in the check output), and the baseline output
  was also 1 component. A buffer-and-skeleton method cannot keep components that
  its buffer has merged. Princes Street: 2 noded, 2 buffered, 2 output.
- **Princes Street within 2 m: 0.8199 against 0.8216.** This is an oracle sampling
  effect, not a loss of fidelity. The oracle densifies to 1 m and averages over
  vertices, so the sample positions depend on where the vertices are. Evidence:
  - Every densified point of the new `simplify=0` output is within 1 m of the
    frozen baseline output. Only 2 baseline points, at one spot, are more than 1 m
    from the new output.
  - The frozen baseline with only exactly collinear vertices removed has the same
    point set, and scores 0.8196 (row `baseline collinear`). The new default
    scores 0.8199, so it beats the baseline on the same footing. On Doncaster,
    0.8705 against 0.8690 is better outright.
- **Princes Street cycle rank 121 to 129, degree-3+ 312 to 325.** The old
  `get_source_target` drops parallel edges between the same two nodes (see
  `explore/README.md`), and the old length filter could drop short junction
  links. The new code keeps both. 3 of the extra lines on Princes Street are
  parallel to another line with the same ends. I have not traced every one of the
  8 extra cycles back to a specific old drop. Doncaster is unchanged (33, 98).
- **Degree-2 nodes 23 to 4 on Princes Street.** Lines are now merged through every
  degree-2 node. The 4 left are closed rings, which the oracle counts as a
  degree-2 node at the ring start.

## Measured output, first pass

These are first-pass figures, kept for the record. The first-pass default was
sqrt(0.5) px. The oracle rows (lossless mode) are unchanged by the second pass.


`bench/simplify_metrics.py check` (the oracle, which passes `simplify` 0.0, so this
is the lossless mode, not the default):

```
== princes_street
princes_street:current_line  547  33225  894.3  37.15   2  93  23  312  121
princes_street:now           548  15575  418.7  37.197  2  92   4  325  129
  current_line: within_1m 0.6379, 2m 0.8216, 5m 0.9512
  now:          within_1m 0.6353, 2m 0.8203, 5m 0.9506
  vertices 33225 -> 15575 (-53.1%)
  components 2 -> 2, degree3plus 312 -> 325, degree1 93 -> 92
== doncaster_rail
doncaster_rail:current_line  173  48824  842.6  57.947  1  39  4  98  33
doncaster_rail:now           173  24494  422.6  57.956  1  39  4  98  33
  current_line: within_1m 0.6792, 2m 0.869, 5m 0.9812
  now:          within_1m 0.6782, 2m 0.8708, 5m 0.9813
  vertices 48824 -> 24494 (-49.8%)
  components 1 -> 1, degree3plus 98 -> 98, degree1 39 -> 39
```

(columns: lines, vertices, verts/km, km, components, degree1, degree2, degree3+,
cycle rank)

`bench/simplify_check.py` (default parameters and the extra checks):

```
== princes_street
                    lines vertices verts/km comp deg1 deg2 deg3+ cycles  1m     2m     5m
baseline (frozen)     547    33225    894.3    2   93   23   312   121 0.6379 0.8216 0.9512
baseline collinear    547    15971    429.9    2   93   23   312   121 0.6334 0.8196 0.9507
new simplify=0.0      548    15575    418.7    2   92    4   325   129 0.6353 0.8203 0.9506
new default           548     4346    122.3    2   92    4   325   129 0.6322 0.8199 0.9512
  components: input noded 2, buffered input 2
  verts/km reduction vs baseline: 7.3x
  simplification keeps end-point graph: True
  non-simple lines: zero 0, default 0
  zero-length lines: default 0
  noded pieces beyond line count (crossings): zero 0, default 0
  time legacy: wall 0.59s cpu 0.59s, new default: wall 0.29s cpu 0.29s

== doncaster_rail
baseline (frozen)     173    48824    842.6    1   39    4    98    33 0.6792 0.8690 0.9812
baseline collinear    173    24590    424.4    1   39    4    98    33 0.6776 0.8701 0.9810
new simplify=0.0      173    24494    422.6    1   39    4    98    33 0.6782 0.8708 0.9813
new default           173     4460     80.4    1   39    4    98    33 0.6789 0.8705 0.9809
  components: input noded 3, buffered input 1
  verts/km reduction vs baseline: 10.5x
  simplification keeps end-point graph: True
  non-simple lines: zero 0, default 0
  zero-length lines: default 0
  noded pieces beyond line count (crossings): zero 0, default 0
  time legacy: wall 4.73s cpu 4.72s, new default: wall 3.63s cpu 3.63s
```

Timing note: the host is shared, and the load average was 16 to 17 during these runs.
CPU time (`time.process_time`) is the figure to trust. Both timings cover the
whole `skeletonize_frame` (buffer, rasterise, skeletonize, vectorise), in the same
process. On Doncaster most of the remaining 3.6 s is the buffer union and
`skimage` skeletonize, which this change does not touch.

## Whole Leeds (`data/osm_leeds.geojson`, scale 1), first pass only

Not rerun in the second pass. The default figure below is at sqrt(0.5) px.

Skeleton of 1,726,882 pixels. Buffer plus rasterise plus skeletonize took 23.9 s
CPU, and those stages are unchanged. Stages after the skeleton, timed on a saved
skeleton:

| mode | CPU | lines | vertices | verts/km | components | degree-1 | degree-3+ | cycle rank |
|---|---|---|---|---|---|---|---|---|
| simplify 0.0 | 3.5 s | 36,319 | 810,789 | 407.8 | 200 | 9,213 | 20,510 | 6,476 |
| default | 6.0 s | 36,319 | 228,972 | 119.9 | 200 | 9,213 | 20,510 | 6,476 |

The end-point graph is identical in both modes, with no non-simple lines. The
legacy vectorise stage dominated the 45 s to 109 s whole-run figure in
`explore/README.md`. The new whole run is about 30 s CPU, and the buffer and
`skimage` skeletonize are now most of it. Peak RSS was 3.4 GB, from the raster
stages.

## Commands run

```bash
.venv/bin/python bench/simplify_metrics.py check      # oracle, lossless mode
.venv/bin/python bench/simplify_check.py              # reviewer one-liner: five datasets, default mode, checks, timing, Open Roads
.venv/bin/python bench/simplify_check.py leeds_5km    # one dataset
.venv/bin/python bench/simplify_metrics.py check-default  # oracle, default mode (fix pass)
.venv/bin/python bench/check_conflict_pairs.py         # Finding 1 (fix pass)
.venv/bin/python bench/check_raster_parity.py          # strip rasteriser parity (fix pass)
# CLI and wrapper smoke test (outputs in /mnt/secondary/tmp/parenx_cli/)
PATH=$PWD/.venv/bin:$PATH sh src/parenx/parenx skeletonize      data/rnet_princes_street.geojson OUT/sk.gpkg
PATH=$PWD/.venv/bin:$PATH sh src/parenx/parenx tile_skeletonize data/rnet_princes_street.geojson OUT/tile.gpkg --simplify 0
PATH=$PWD/.venv/bin:$PATH sh src/parenx/parenx voronoi          data/rnet_princes_street.geojson OUT/vr.gpkg
```

CLI `line` layers (Princes Street), second pass at the default: `skeletonize`
551 lines, 70.5 verts/km. `tile_skeletonize` 556 lines, 71.0 verts/km. First
pass: `tile_skeletonize --simplify 0` gave 556 lines, 420.1 verts/km and
2 components. `voronoi` 466 lines, unchanged code. All `primal` layers were
written. `skeletonize_frame(..., knot=True, segment=True)` and
`get_primal(r["geometry"])` also run.

Note: `bench/simplify_metrics.py metrics FILE.gpkg` reads the first layer of a
GeoPackage. For CLI outputs that is the `input` layer, not `line`. I did not
modify the oracle.

## Not verified

- Second pass: the `skimage` skeletonize ratio of 45x for 25x the area was timed
  once under load. I did not repeat it or look into it.
- Second pass: the Leeds circles were checked through `skeletonize_frame` only.
  `tile_skeletonize` and the CLI were smoke-tested on Princes Street only.
- Second pass: whole Leeds was not rerun with the 1 px default or the new
  rasteriser.

- The Leeds run below covers only the stages after the skeleton, plus one
  buffer-and-skeleton timing. I did not run the legacy Leeds pipeline again. The
  45 s to 109 s figure comes from `explore/README.md`. Greater London was not run.
- The `segment=True` path is only smoke-tested (it runs and returns lines). No
  metrics.
- `tile_skeletonize` output was only checked for running, line count and
  components. Its tile-seam gap fill is unchanged.
- The Python chain walk is linear but interpreted. It costs about 0.7 s for Leeds'
  1.7 M skeleton pixels, so it is not a bottleneck yet.
- There is no unit test suite in the repo. `bench/simplify_check.py` is the
  regression check.
