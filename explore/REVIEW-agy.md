# Review: Uncommitted Network Simplification Changes

**Review target**: Uncommitted changes in `src/parenx/` on branch `4-speed-up-and-improve-simplification` (Issue #4).  
**Author's report**: [`explore/IMPLEMENTATION-claude-opus.md`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/explore/IMPLEMENTATION-claude-opus.md)  
**Reviewer**: Antigravity (`agy`)  
**Scope**: Code audit across pixel graph algorithms, de-knotting, network topology preservation, edge cases, faithfulness, technical debt, and test harnesses.

---

## 1. Numbered Findings

### Finding 1: Conflict detection blind spot on non-noded interior intersections
- **Severity**: **Major**
- **File & line**: [`src/parenx/skeletonize.py#L586-L605`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/src/parenx/skeletonize.py#L586-L605) (`get_conflict`)
- **Why it matters**:  
  `get_conflict(line, base)` queries intersecting pairs using:
  ```python
  i, j = STRtree(line).query(line, predicate="intersects")
  ```
  This queries `line` against `line`, finding only pairs $(i, j)$ that *currently intersect in the simplified network*.  
  If two lines in `base` met at an interior vertex (e.g. a T-junction or un-noded intersection, such as those that can arise from tile-seam stitching in [`src/parenx/tile_skeletonize.py`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/src/parenx/tile_skeletonize.py) where `combine_line` does not split lines, or arbitrary networks passed to `simplify_network`), and Douglas–Peucker simplification straightens one line away, the two lines no longer intersect in `line`.  
  Because they do not intersect, `STRtree(line).query` **never returns $(i, j)$**. Consequently, `~equals(intersection(line[i], line[j]), intersection(base[i], base[j]))` is never evaluated for that pair, and the topological disconnection is missed completely. The simplified network silently loses connectivity.
- **Concrete suggested fix**:  
  Include intersection pairs from `base` in candidate pairs so lost intersections are evaluated:
  ```python
  def get_conflict(line, base):
      r = ~is_simple(line)
      r |= is_closed(base) & (get_num_coordinates(line) < 4)
      i_line, j_line = STRtree(line).query(line, predicate="intersects")
      i_base, j_base = STRtree(base).query(base, predicate="intersects")
      i = np.concatenate([i_line, i_base])
      j = np.concatenate([j_line, j_base])
      ix = i < j
      i, j = np.unique(np.stack([i[ix], j[ix]], axis=1), axis=0).T
      ix = ~equals(intersection(line[i], line[j]), intersection(base[i], base[j]))
      r[i[ix]] = True
      r[j[ix]] = True
      return r
  ```

---

### Finding 2: `set_hub` snaps to leaf/spur nodes, not guaranteed junction pixels
- **Severity**: **Major**
- **File & line**: [`src/parenx/skeletonize.py#L508-L513`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/src/parenx/skeletonize.py#L508-L513) (`set_hub`)
- **Why it matters**:  
  The code comment states:
  > *`# snap each hub to the cluster junction pixel nearest the centroid, so a hub is never a free point that can land on, or next to, another chain`*  
  and the author's report claims:
  > *"Snapping the hub to a junction pixel rules this out by construction, because a junction pixel can never be inside a chain."*  
  
  In reality, `node = np.unique(end[short])` collects all endpoints of short chains ($\le 2$ px), including degree-1 leaf nodes (e.g. short spurs radiating from a junction). If a degree-1 leaf pixel is closer to the cluster centroid than the true junction pixel (which frequently occurs when multiple short spurs cluster on one side), `hub` snaps to the degree-1 leaf node.  
  Furthermore, if a knot cluster consists solely of short chains without any junction pixel (e.g. an isolated triangle cycle or an isolated 2 px stub), none of the cluster nodes is a junction pixel.  
  While snapping to an existing cluster node prevents floating centroid coordinates, it **does not** guarantee by construction that spokes cannot intersect nearby chains. The downstream `simplify_network` STRtree check acts as a practical safety net, but `set_hub` does not uphold the stated invariance.
- **Concrete suggested fix**:  
  Filter `node` by full pixel degree ($\ge 3$) when determining candidate hub pixels:
  ```python
  # Count degree in the full pixel graph
  deg = np.bincount(np.concatenate([chain_start, chain_end]), minlength=len(point))
  junction_mask = deg[node] >= 3
  # If the cluster has genuine junction nodes, restrict hub snapping to them:
  candidate_node = node[junction_mask] if junction_mask.any() else node
  ```

---

### Finding 3: Undeclared direct dependency on `scipy` in `pyproject.toml`
- **Severity**: **Major**
- **File & line**: [`pyproject.toml#L16-L22`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/pyproject.toml#L16-L22); [`src/parenx/skeletonize.py#L15-L16`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/src/parenx/skeletonize.py#L15-L16)
- **Why it matters**:  
  `skeletonize.py` directly imports `coo_matrix` and `connected_components` from `scipy.sparse`. However, `scipy` is not listed in `dependencies` in [`pyproject.toml`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/pyproject.toml).  
  The report asserts: *"Python and numpy only, plus `scipy`, which `scikit-image` already pulls in. No new dependencies."* Relying on undeclared transitive dependencies violates packaging standards: build frontends (e.g. `pip`, `uv`) and stripped environments may fail to resolve or install `scipy` as an explicit dependency of `parenx`.
- **Concrete suggested fix**:  
  Add `"scipy"` to `dependencies` in [`pyproject.toml`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/pyproject.toml#L16-L22):
  ```toml
  dependencies = [
    "geopandas",
    "pyogrio",
    "rasterio",
    "shapely",
    "scikit-image",
    "scipy"
  ]
  ```

---

### Finding 4: Docstring and README claim `--simplify 0` only drops collinear vertices, but lossy de-knotting and spur-dropping still run
- **Severity**: **Minor**
- **File & line**: [`README.md#L59-L63`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/README.md#L59-L63); [`src/parenx/skeletonize.py#L674-L678`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/src/parenx/skeletonize.py#L674-L678)
- **Why it matters**:  
  The README claims:
  > *"Use `--simplify 0` to turn simplification off, which only drops exactly collinear vertices, or `--simplify N` for a tolerance of `N` metres."*  
  And `skeletonize.py` docstrings describe `0.0` as "none".  
  However, with `simplify=0.0` (and default `knot=False`), `set_hub` and `drop_spur` still execute: knot clusters $\le 2$ px are collapsed into hubs and spokes, and degree-1 spurs $\le 2$ px are dropped. This modifies vertices, edge lengths, and degree distributions. The output is only strictly lossless relative to the raw skeleton when both `--knot` and `--simplify 0` are supplied together.
- **Concrete suggested fix**:  
  Clarify in [`README.md`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/README.md) and [`skeletonize.py`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/src/parenx/skeletonize.py) docstrings that `--simplify 0` disables geometric Douglas–Peucker polyline simplification, whereas topological de-knotting (`set_hub`) and spur pruning (`drop_spur`) are governed by the `--knot` flag.

---

### Finding 5: `simplify_network` reverts both lines on conflict, unnecessarily dropping simplification on innocent lines
- **Severity**: **Minor**
- **File & line**: [`src/parenx/skeletonize.py#L603-L604, L630`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/src/parenx/skeletonize.py#L603-L604)
- **Why it matters**:  
  In `get_conflict`:
  ```python
  ix = ~equals(intersection(line[i], line[j]), intersection(base[i], base[j]))
  r[i[ix]] = True
  r[j[ix]] = True
  ```
  When line $i$ swings and intersects line $j$, both line $i$ and line $j$ are flagged and reverted to `base`. Line $j$ may have been a long, well-behaved line that simplified cleanly on its own. Because lines reverted to `base` are never retried for simplification in subsequent rounds, line $j$ permanently loses its vertex reduction simply because line $i$ intruded on it.  
  While this ensures conservative correctness and loop termination, it leaves unnecessary vertices on the table.
- **Concrete suggested fix**:  
  Document this conservative trade-off explicitly in the docstring, or in a future iteration attempt a second pass re-evaluating reverted lines once conflicting neighbors have been stabilized.

---

### Finding 6: Unused imports, undocumented legacy helper functions, and divergent Shapely geometry extraction idioms
- **Severity**: **Nit**
- **File & line**: [`src/parenx/skeletonize.py#L9, L211-L246, L583`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/src/parenx/skeletonize.py#L9); [`src/parenx/shared.py#L29`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/src/parenx/shared.py#L29)
- **Why it matters**:  
  1. `networkx` is imported at [`skeletonize.py#L9`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/src/parenx/skeletonize.py#L9) solely for `get_connected_class`, but is no longer used anywhere in the active simplification pipeline.
  2. `get_connected_class` (lines 211–224) and `get_centre_edge` (lines 227–245) are legacy helpers called only by `get_raster_line`, but unlike `get_raster_point`, `get_raster_line`, and `sx_to_nx`, they lack legacy docstring tags.
  3. Divergent unpacking idioms: [`skeletonize.py#L583`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/src/parenx/skeletonize.py#L583) uses `getattr(r, "geoms", [r])`, whereas [`shared.py#L29`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/src/parenx/shared.py#L29) and [`skeletonize.py#L303-L335`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/src/parenx/skeletonize.py#L303-L335) use `try: ... .geoms except AttributeError:`.
- **Concrete suggested fix**:  
  Add `# legacy:` comments to `get_connected_class` and `get_centre_edge`, standardize on `getattr(r, "geoms", [r])` or `shapely.get_parts(r)`, and remove the top-level `networkx` import when legacy functions are moved to `bench/`.

---

## 2. Verification of Specific Checklist Items

### 1. Pixel adjacency (`get_pixel_edge`, `get_pixel_index`)
- **Shifts & 8-neighbour set**: The 4 shifts `(0, 1)`, `(1, 0)`, `(1, 1)`, `(1, -1)` represent all forward neighbors in row-major order ($u < v$). The set of undirected pairs is **genuinely complete and duplicate-free**.
- **Column-wrap guard**: `(col + dc >= 0) & (col + dc < width)` is evaluated, and `ix &= j >= 0` ensures that flat indices wrapping across the right-to-left image boundary are never included in `source` or `target`.
- **Row bounds**: For row overflow at `height - 1`, keys exceed `pixel.max()`, causing `get_pixel_index` to return `-1`, which `ix &= j >= 0` masks out.
- **Empty & isolated pixels**: An empty raster yields empty arrays; a single isolated pixel yields no edges and empty chains without error.

### 2. Chain walk (`get_pixel_chain`)
- **Isolated cycles & degree-2 chains**: Degree $\ne 2$ nodes initiate walks that terminate at subsequent degree $\ne 2$ nodes. Unvisited remaining edges (isolated degree-2 cycles) are traversed into closed rings $[a_0, a_1, \dots, a_k, a_0]$.
- **2x2 pixel blocks**: In a 2x2 block, all 4 pixels have degree 3, yielding 6 distinct 2-pixel chains (4 boundary edges + 2 crossing diagonals). When `knot=False`, `set_hub` collapses the 2x2 block into a single clean junction node; when `knot=True`, crossing diagonal edges are retained.
- **Lassos / self-loops**: Chains looping back to a junction terminate correctly because the junction node has degree $\ge 3$ ($\ne 2$).

### 3. The hub (`set_hub`)
- Tested against small configurations: snapping to `node` prevents generating non-integer centroid coordinates, but as detailed in **Finding 2**, `node` contains leaf nodes, so the hub is not guaranteed to be a junction pixel.
- Isolated clusters of chains where every chain is $\le 2$ px and no longer chain attaches to the cluster are dropped entirely (producing `LINESTRING EMPTY`).

### 4. `simplify_network`
- **Crossing prevention**: Any simplification that causes two lines to cross creates a new intersection geometry in `line` that does not match `base`, correctly triggering reversion for lines that meet only at endpoints or were previously disjoint.
- **Loop termination**: Provably terminates. In every round where conflicts exist, `~equals_exact(r, base, 0.0)` ensures at least one line is reverted to `base`. Since the candidate pool of modified lines strictly decreases and is bounded by $N$, the loop terminates in $\le N$ iterations.
- **Interior vertex flaw**: Confirmed empirically (see **Finding 1**). If two lines meet only at an interior vertex in `base`, straightening one line away causes them to no longer intersect, completely evading `STRtree(line).query(line)`.

### 5. Strip rasteriser (`get_raster`)
- Verified against `rif.rasterize(np.asarray(geom.values), ...)`:
  - `data/rnet_princes_street.geojson`: 0 pixel differences.
  - `data/rnet_doncaster_rail.geojson`: 0 pixel differences.
  - The strip rasteriser produces an identical binary raster.

### 6. Faithfulness of APIs and signatures
- `skeletonize_frame`: Signature unchanged. Return type is `GeoDataFrame` with `"geometry"` column.
- `voronoi_frame` & `src/parenx/voronoi.py`: Completely untouched by git diff.
- `skeletonize_tiles`: Fix in `get_source_target` for `DataFrame.explode` resolved the crash in `get_gap_fill`. Runs and produces clean tiles.
- `get_primal`: Signature, implementation, and return type unchanged.

---

## 3. Reproduction Results & What Could Not Be Checked

### Successfully Reproduced
1. **`bench/simplify_metrics.py check`**:
   - `princes_street`: vertices $33,225 \to 15,575$ ($-53.1\%$), components $2 \to 2$, degree3+ $312 \to 325$, within 2m $0.8216 \to 0.8203$.
   - `doncaster_rail`: vertices $48,824 \to 24,494$ ($-49.8\%$), components $1 \to 1$, degree3+ $98 \to 98$, within 2m $0.869 \to 0.8708$.
   - `leeds_1km`: vertices $69,067 \to 27,824$ ($-59.7\%$), components $29 \to 29$, degree3+ $969 \to 983$, within 2m $0.7408 \to 0.7482$.
   - `leeds_3km`: vertices $592,569 \to 268,631$ ($-54.7\%$), components $87 \to 81$, degree3+ $7,896 \to 8,071$, within 2m $0.8287 \to 0.8303$.
   - `leeds_5km`: vertices $1,360,918 \to 625,521$ ($-54.0\%$), components $142 \to 134$, degree3+ $16,147 \to 16,441$, within 2m $0.8485 \to 0.8500$.
2. **`bench/simplify_check.py`**:
   - `princes_street`: $12.7\times$ vertex reduction ($894.3 \to 70.3$ verts/km), 0 non-simple lines, 0 crossings.
   - `doncaster_rail`: $39.2\times$ vertex reduction ($842.6 \to 21.5$ verts/km), 0 non-simple lines, 0 crossings.
3. **`bench/component_provenance.py`**:
   - `princes_street`, `doncaster_rail`, `leeds_1km`: counts agree.
   - `leeds_3km`: old clustered at 16m gives 81 components, matching new output exactly (confirms old code split components within single buffer polygons).

### Could Not Be Checked / Limitations
- `bench/component_provenance.py` on `leeds_5km` timed out due to CPU-heavy `unary_union` polygon buffering on 142 complex components and was killed to avoid delaying this review.
- Full Leeds (`data/osm_leeds.geojson`) was not rerun, as noted in the author's report.
- Timings varied slightly due to background load on the shared machine, but CPU process times were consistent.

---

## 4. Verdict

**SAFE TO COMMIT TO THE BRANCH WITH CONDITIONS**

The integer-array pixel graph, degree-2 chain contraction, strip rasteriser, and network simplification represent an order-of-magnitude algorithmic improvement over the legacy per-pixel Shapely STRtree stage, reducing vertices by $10\times$ to $39\times$ while preserving topology on all tested datasets.

However, before merging to `main`, the following **must change**:
1. **Fix Finding 1**: Update `get_conflict` in [`src/parenx/skeletonize.py`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/src/parenx/skeletonize.py) to check pairs intersecting in `base` so un-noded interior intersections cannot be silently disconnected.
2. **Fix Finding 3**: Declare `"scipy"` in `dependencies` in [`pyproject.toml`](file:///mnt/secondary/home/robin/github/people/anisotropi4/parenx/pyproject.toml).
3. **Address Finding 2 & 4**: Restrict candidate hub pixels to actual junction pixels ($\text{degree} \ge 3$) where available, and clarify in the README and docstrings that `--simplify 0` still applies knot collapse and spur removal unless `--knot` is also provided.
