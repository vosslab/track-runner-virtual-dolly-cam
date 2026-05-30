# blob_walk_v2

Blob walker v2 tools for track runner virtual dolly cam.

## Entry point

The single all-in-one driver discovers videos, optionally walks intervals,
renders walker tiles, and builds `walk.html`:

```
python3 tools/blob_walk_v2/make_walk_html_v2.py [flags]
```

### Key flags

| Flag | What it does |
| --- | --- |
| `-o`, `--output-root` | Root output directory (default: `blob_walk_v2`) |
| `-w`, `--walk` | Run the walker batch before rendering (default: skip walker) |
| `-j`, `--workers` | Number of parallel video workers for render (default: 1) |
| `--intervals-from-corpus` | Restrict the walk to the intervals named in a `dump_step1` corpus directory |
| `--skip-render` | Rebuild `walk.html` from existing CSVs and PNGs only |
| `-d`, `--dry-run` | Print selected videos and intended work, then exit |

| `--heat-movie` / `--no-heat-movie` | Encode per-direction heat movies alongside each interval's render output (default: off). Requires `--walk` and ffmpeg in PATH. See [docs/USAGE.md](../../docs/USAGE.md) for details. |

Run `make_walk_html_v2.py --help` for the full flag list.

### Heat movie output

When `--heat-movie` is set, two `.mkv` files are written beside each
interval's render tiles:

- `heat_fwd.mkv` -- FWD-direction heat movie.
- `heat_bwd.mkv` -- BWD-direction heat movie.

Each movie shows the residual-motion heat overlay at a fixed ROI size
derived from the larger bracketing seed, with the solved torso box and
in-box hot-mean text overlaid on every frame.

**ffmpeg is required.** A missing ffmpeg is diagnosed at startup before
any encode work begins. A normal `--walk` run without `--heat-movie`
does not need ffmpeg.

Raw `.bgr` frames are written to a per-interval scratch directory under
`/tmp`, encoded to `heat.mkv` there, then copied to the output
directory. The scratch directory is deleted after the copy is verified.
No scratch data persists between runs.

### Examples

```bash
# Get help
python3 tools/blob_walk_v2/make_walk_html_v2.py --help

# Walk and render walk.html (single worker)
python3 tools/blob_walk_v2/make_walk_html_v2.py --walk --workers 1

# Walk only the corpus intervals, then build walk.html
python3 tools/blob_walk_v2/make_walk_html_v2.py \
    --walk --intervals-from-corpus dump_step1/24corpus
```

## Default output paths

- Walk tiles and `walk.html`: under the `--output-root` directory (default `blob_walk_v2`).
- Solved torso boxes: the walker persists per-frame solved boxes (FWD, BWD,
  blended) to the track_runner `torso_box_coords.npz` (source full-frame pixels).
  Tiles render the human seed box on seed frames, the walker-solved box on every
  frame, and blob ellipses from the live trace.
- Render manifest: each `--walk` run writes `render_manifest.json` under each
  video output dir (one record per rendered tile). Check it with
  `check_render_manifest.py`, which fails if any non-seed frame lacks a solved
  box or any tile has `conversion_count != 1` (the "magenta + only" symptom)
  without a human opening `walk.html`:

```bash
source source_me.sh && python3 tools/blob_walk_v2/check_render_manifest.py \
    -i <output-root>
```

Corpus quality metrics (classification, accepted_fraction rollups, FWD/BWD
agreement) render directly inside `walk.html`.

### Render manifest heat fields

Each record in `render_manifest.json` carries five per-tile heat fields
written by `walk_driver.py` `_render_direction_tiles` after
`common_tools.in_box_heat.measure_in_box_heat` runs:

| Field | Type | Description |
| --- | --- | --- |
| `in_box_hot_mean` | float or null | Mean DoG value of above-threshold in-box pixels; null when none qualify. |
| `in_box_hot_count` | int | Count of above-threshold in-box pixels (0 when none qualify). |
| `in_box_heat_present` | bool | True when `in_box_hot_count > 0`. |
| `in_box_heat_computed` | bool | True only when a live trace with `residual_dog` AND a `solved_box` were both present (the primitive actually ran). |
| `heat_threshold_used` | float | The threshold passed to the primitive; always `residual_motion.DEFAULT_THRESHOLD` (10.0). |

`in_box_heat_computed=False` records a not-computed tile (stub trace from a
render-only npz, or no solved box). `in_box_heat_computed=True` with
`in_box_heat_present=False` records a computed-cold tile (the primitive ran
but found no above-threshold pixels inside the box).

The heat arrays are read in PROCESSED space. `measure_in_box_heat` subtracts
`roi_origin` from the box center exactly once before deriving pixel edges,
mirroring `walk_draw.processed_box_to_tile_local`. See
[docs/COORDINATE_SPACES.md](../../docs/COORDINATE_SPACES.md) for the
PROCESSED-space contract.

### Heat report in the manifest gate

`check_render_manifest.py` prints a heat-present fraction report after the
standard pass/fail gates. For each `(source, direction)` it prints:

```
  HEAT REPORT <manifest> [<direction>]: heat-present N/M eligible (X%); K not-computed (skipped); total T tiles; threshold=<value>
```

Eligibility is `in_box_heat_computed == true`. Computed-cold tiles stay in
the denominator; not-computed tiles are excluded (skipped). The report is
printed whether the gate passes or fails. The heat fraction does NOT affect
the exit code; the two existing gates (conversion_count == 1 and
non-seed-missing-solved-box) alone govern process exit status. Older manifests
without `in_box_heat_computed` on any record are silently skipped.

## Library modules

`make_walk_html_v2.py` is the only entry point. Everything below is a library
module imported by the driver, grouped by role. The modules are split across
two subdirs, `core/` and `render/`; the entry script, `walk_paths.py`, and
`walk_util.py` stay at the package root. `walk_paths.setup()` adds both subdirs
to `sys.path`, so every module imports its siblings by bare name regardless of
which subdir it lives in.

Package root:

- `make_walk_html_v2.py` -- the all-in-one entry point.
- `walk_paths.py` -- shared repo-root and `sys.path` setup (adds `core/` and
  `render/` to `sys.path`).
- `walk_util.py` -- small generic helpers (`_to_float`, `_evenly_spread`, ...).

`core/` -- walk orchestration, algorithm, I/O, and schema:

- `walk_driver.py` -- per-video FWD/BWD interval-walk orchestrator. Called as a
  library by `make_walk_html_v2.py` and by `tests/e2e/e2e_blob_walk_baseline.py`.
  It also has a standalone `main()`/argparse for direct single-video runs:
  `python3 tools/blob_walk_v2/core/walk_driver.py --help`.
- `walk_walker.py` -- `walk_one_direction` orchestration plus `resolve_audit_winner`.
- `walk_viterbi.py` -- windowed path selection (`select_path` + cost functions).
- `walk_status.py` -- per-frame status enum and interpolation/extrapolation.
- `walk_motion_gate.py` -- runner motion-physics constants and the jump gate.
- `walk_io.py` -- seed, reader, and scene-transform loading.
- `walk_debug_log.py` -- verdict CSV schema and writer.

`render/` -- per-frame tiles and HTML output:

- `walk_render.py` -- per-frame tile orchestration.
- `walk_draw.py` -- low-level cv2 draw primitives.
- `walk_palette.py` -- `overlay_styles.yaml` color lookup for walker overlays.
- `walk_html.py` -- builds `walk.html` (including the corpus quality summary).
