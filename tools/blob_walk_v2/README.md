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

### Interval heat summary

In-box motion-cue heat is frame-derived but SPARSE: only the frames the walk
actually observed have a measured value (interpolated, extrapolated, soft-miss,
and render-only frames have none). Contract C13 routes dense frame data to the
`.npz` (integers) and interval data to JSON; sparse per-frame heat fits neither,
so it is NOT stored per frame. The walk does not put any heat keys on the
per-tile records in `render_manifest.json`.

Instead `walk_driver.py` aggregates heat to one record per `(interval,
direction)` and writes them to a sibling `render_heat_summary.json` (interval
data, JSON-approved by C13). Each summary record:

| Field | Type | Description |
| --- | --- | --- |
| `left_frame` | int | Interval left seed frame index. |
| `right_frame` | int | Interval right seed frame index. |
| `direction` | string | `fwd` or `bwd`. |
| `heat_eligible` | int | Frames the walk observed (heat was actually computed). |
| `heat_present` | int | Eligible frames with at least one above-threshold in-box pixel. |
| `heat_present_pct` | float | Fraction of eligible frames with heat, in [0, 1] (`heat_present / heat_eligible`; 0.0 when `heat_eligible == 0`). |
| `mean_heat` | float or null | Mean of per-frame hot-pixel means across heat-present frames only. Null when no heat-present frames. |
| `not_computed` | int | Frames with no walk observation (coverage, not hidden). |
| `seed_cold_frames` | list of int | Seed frames that were computed but came back cold. |
| `threshold` | float | `residual_motion.DEFAULT_THRESHOLD` used during the walk. |

`heat_eligible` is the denominator (frames the walk observed). `not_computed`
surfaces sparsity as coverage rather than hiding it. A seed frame is cold when
its heat was computed but no in-box pixel cleared the threshold -- a likely
stale or background-parked annotation. `heat_present_pct` is the fraction (not
a percentage integer) of eligible frames that are heat-present; `mean_heat` is
the mean over those same frames' per-frame hot-pixel means and is null when no
frame is heat-present. Both are C13-approved interval-level floats stored in
JSON (not per-frame, not in the npz). The per-frame float hot-pixel mean is
NOT persisted to `render_manifest.json`; it is threaded in-memory at walk time
and aggregated into `mean_heat` only.

The heat arrays are measured in PROCESSED space during the walk;
`common_tools.in_box_heat.measure_in_box_heat` subtracts `roi_origin` from the
box center exactly once. See
[docs/COORDINATE_SPACES.md](../../docs/COORDINATE_SPACES.md) for the
PROCESSED-space contract.

### Heat report in the manifest gate

`check_render_manifest.py` reads the sibling `render_heat_summary.json` (when
present) and prints, per interval-direction:

```
  HEAT REPORT <summary> [L,R] [<direction>]: heat-present N/M (X%); mean_heat=<float or n/a>; K not-computed; threshold=<value>
  SEED-COLD <summary> [L,R] [<direction>]: C seed tiles heat-cold; frames=[...]
```

These reports are report-only: they do NOT affect the exit code. The two gates
over `render_manifest.json` (conversion_count == 1 and
non-seed-missing-solved-box) alone govern process exit status. A run with no
`render_heat_summary.json` sibling (older run) skips the heat and seed-cold
reports cleanly.

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
