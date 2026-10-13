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
