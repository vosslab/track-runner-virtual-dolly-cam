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

Run `make_walk_html_v2.py --help` for the full flag list.

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

Corpus quality metrics (classification, accepted_fraction rollups, FWD/BWD
agreement) render directly inside `walk.html`.

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
