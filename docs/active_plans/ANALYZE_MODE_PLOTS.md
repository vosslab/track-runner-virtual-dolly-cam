# Plan: Analyze-mode diagnostic plots

## Goal

Add an opt-in `--plot` flag to the `analyze` subcommand that emits
PNG diagnostic plots alongside the existing `tr_config/<stem>.encode_analysis.yaml`
report. Three per-frame signals plus a stacked dashboard:

1. Zoom level (per-frame torso height and crop height in source-frame pixels)
2. Camera motion magnitude (per-frame `hypot(dx, dy)` from MotionTrack, with `scale` on a twin axis)
3. Runner speed (per-frame land-relative motion magnitude derived from the torso center via `SceneTransform.pixel_to_scene` and first difference)

Plus a combined `dashboard.png` with all three panels stacked sharing one x-axis.

## Why this is worth doing

- Encode-mode crop instability ("zoom pumping," "drift," "bouncing") is hard to diagnose from a final encoded video. The crop trajectory's input signals (torso geometry, camera motion, runner ground speed) are computed during analyze but never visualized.
- The user's "I set torso_multiple to 5 and got a multiple of 10" debugging session would have been a 5-second look at a zoom plot.
- Plots are output-only artifacts; no algorithm changes, no risk of regressions in the solver or encoder paths.
- All required data is either already computed during analyze or available via the existing `MotionTrack` and `SceneTransform` interfaces.

## Non-goals

- Interactive plots. Output is static PNG only.
- Per-interval confidence overlays beyond what the existing report dumps. Future work can layer interval boundaries on the x-axis as a thin band, but not in this round.
- Web dashboard / live update. Out of scope.
- Replacing the YAML report. Plots supplement, not supplant.
- Plotting solver-internal scores (FWD/BWD agreement, blob coverage). Those are tracking-quality concerns; this plan is encode-quality.

## CLI surface

Single binary toggle on the `analyze` subparser (no path argument; outputs are auto-derived).

```python
parser.add_argument(
    "-p", "--plot",
    dest="write_plots", action="store_true",
    help="write diagnostic PNG plots (zoom, camera motion, runner speed) "
         "alongside the encode_analysis.yaml report",
)
```

Per ARGPARSE MINIMALISM in `docs/PYTHON_STYLE.md`: a binary toggle is the minimal surface. No DPI flag, no figsize flag, no per-panel selection. Users who want more control can post-process the YAML.

`-p` is the short form; matches the alphabetical pattern of existing analyze flags (`-s`, `-t`, `-g`, `-S`).

## Output paths

Co-located with the existing analyze report. Reuse the basename helper that produces `tr_config/<stem>.encode_analysis.yaml`:

| Path | Content |
| --- | --- |
| `tr_config/<stem>.encode_analysis.zoom.png`     | Zoom panel only |
| `tr_config/<stem>.encode_analysis.camera.png`   | Camera-motion panel only |
| `tr_config/<stem>.encode_analysis.speed.png`    | Runner-speed panel only |
| `tr_config/<stem>.encode_analysis.dashboard.png` | All three stacked, shared x-axis |

Reasons for individual files plus dashboard: individual files are easier to share in a chat / paste into a bug report; the dashboard is the at-a-glance view for the user's local triage.

## Data sources (no duplicate extraction)

Each plot pulls from existing structures so the plot path does not become a parallel computation pipeline.

| Plot | Data source | Where it is loaded today |
| --- | --- | --- |
| Zoom: per-frame torso_h | `state["h"]` from the dense solved trajectory | `_mode_analyze` already loads this via `state_io.load_torso_box_coords` |
| Zoom: per-frame crop_h | output of `tr_crop.direct_center_crop_trajectory` | `encode_analysis._compute_crop_metrics` runs this; cache the rect list and pass through |
| Camera motion: per-frame dx, dy, scale, quality | `MotionTrack` arrays | load via `camera_motion.load_active_camera_motion_or_fail(input_file)` |
| Runner speed: per-frame ground velocity | `SceneTransform.pixel_to_scene(frame_index, cx, cy)` then frame-to-frame diff | requires `SceneTransform`, which `_mode_analyze` already builds for regime-classifier work |

The runner-speed panel is the only signal that is not already produced as a NumPy array somewhere in analyze. The projection helper belongs in `analyze_plots.py` because it is plot-specific (stride-cycle smoothing or noise rejection are plot-readability choices, not algorithm choices).

## New module: `track_runner/analyze_plots.py`

Public API: one entry point.

```python
def write_analyze_plots(
    *,
    out_dir: pathlib.Path,
    video_stem: str,
    trajectory: list,                    # dense per-frame state dicts
    crop_rects: list,                    # output of direct_center_crop_trajectory
    motion_track: object | None,         # MotionTrack or None if cache absent
    scene_transform: object | None,      # SceneTransform or None
    fps: float | None,                   # for seconds twin-axis; None -> frame index only
) -> list:                               # list of pathlib.Path actually written
    """Write diagnostic plots; return list of paths actually written."""
```

Internal helpers (one per panel) plus a `_render_dashboard` that calls the three panel functions on subplots.

```python
def _render_zoom_panel(ax, trajectory, crop_rects, fps): ...
def _render_camera_panel(ax_motion, ax_scale, motion_track, fps): ...
def _render_speed_panel(ax, trajectory, scene_transform, fps): ...
def _render_dashboard(out_path, ...): ...
```

Use matplotlib `Agg` backend (no GUI). Default figure: 10 inches wide x 3 inches per panel, 100 dpi. Save as PNG with `bbox_inches="tight"`.

X-axis: frame index primary, optional seconds twin axis when `fps` is known. The twin axis just adds a top tick layer; data is plotted against frame index.

Y-axis: every panel labels its units explicitly (px, px/frame, m/s if scene units permit). Speed panel uses scene-coord units per frame as the primary, with a per-second secondary axis when fps is available.

Gaps: `numpy.nan` for missing frames so matplotlib breaks the line. No interpolation across not-in-frame gaps.

## Wiring into the analyze flow

`track_runner/cli.py` `_mode_analyze`:

1. Parse `args.write_plots`.
2. After the existing analyze report is written, if `args.write_plots`:
   - Already-loaded data: `trajectory`, `scene_transform`, `crop_rects`, `fps`.
   - Try `motion_track = camera_motion.load_active_camera_motion_or_fail(args.input_file)` inside try/RuntimeError; on failure, pass `motion_track=None` and emit a one-line warning that the camera and speed panels will be skipped (graceful degradation, see "Failure modes" below).
   - Call `analyze_plots.write_analyze_plots(...)`.
   - Print one line per written PNG so the user knows where the artifacts landed.

`track_runner/encode_analysis.py`:

- No new logic. If `_compute_crop_metrics` already returns the crop rect list internally, expose it (or have `_mode_analyze` rerun the pure crop computation; it is cheap).

Net change to existing modules: ~30 lines split across `cli.py` and `cli_args.py`.

## Failure modes (graceful)

| Condition | Behavior |
| --- | --- |
| matplotlib import fails | Hard error: `--plot` was explicitly requested |
| `load_active_camera_motion_or_fail` raises | Warn once; skip camera and speed panels; still write zoom panel and a degraded dashboard (zoom only) |
| Trajectory empty | Warn once; return empty list; exit 0 |
| Trajectory has no scene_transform projection (e.g., scene_transform is None) | Skip speed panel; still write zoom and (if motion_track loaded) camera panel |
| `fps` is None or non-positive | Omit the seconds twin-axis; primary frame-index axis still works |
| Output directory does not exist | `pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)` |

The "graceful skip" path means a user running `analyze --plot` on a freshly-solved video that has no camera-motion cache yet still gets the zoom plot, with a clear warning that speed and camera plots require running `solve` first.

## Tests: `tests/test_tr_analyze_plots.py`

Behavioral assertions per `docs/PYTHON_STYLE.md` PYTEST section. Stable across reasonable refactors.

| Test | Property |
| --- | --- |
| `test_writes_four_pngs_when_all_data_present` | Returns list of 4 paths; each ends in the documented suffix; each file is non-empty |
| `test_skips_camera_and_speed_when_motion_absent` | Returns list of 2 paths (zoom + degraded dashboard); the camera and speed panel files are absent |
| `test_skips_speed_when_scene_transform_absent_but_motion_present` | Returns list of 3 paths (zoom, camera, dashboard); speed panel file is absent |
| `test_constant_velocity_input_produces_constant_speed` | With a synthetic trajectory of constant scene velocity, the computed per-frame speed array is approximately constant (RMS deviation < 5% of mean). Confirms projection wiring without locking in numerical values. |
| `test_paths_under_supplied_out_dir` | Every returned path's `parent` equals the supplied `out_dir` |
| `test_paths_contain_video_stem` | Every returned path's basename starts with the supplied stem |

What we do NOT test (would be fragile):

- Pixel counts, image dimensions, dpi.
- Color values, line widths, font sizes.
- Exact numerical y-axis values (locks in tunable plot constants).
- matplotlib internal state.

## Documentation

| File | Update |
| --- | --- |
| `docs/modes/ANALYZE.md` | Re-run `tools/refresh_mode_docs.py` after the CLI flag lands so the auto-generated help block reflects `--plot`; add a hand-written paragraph above the auto block describing the four output artifacts and graceful-degradation policy |
| `docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md` | Add a "Diagnostic plots" section describing each panel, what it shows, and what visible patterns mean (e.g., "step changes in zoom panel = the crop trajectory is hitting fit-to-source clamp boundaries; flat camera-motion panel = either the camera was static or the camera-motion cache is unhealthy") |
| `docs/CHANGELOG.md` | Entry under today's date in "Additions and New Features" |
| `docs/ENCODE_DESIGN.md` | One-line cross-link to the analyze plot output as the canonical diagnostic for encode-quality issues |

## Milestone breakdown

Numbers are labels; ordering is dependency-driven.

### M1: Module skeleton (no panel content)

- Files: `track_runner/analyze_plots.py` (new).
- Public `write_analyze_plots` function with the documented signature.
- All three internal panel helpers stubbed with `pass`.
- matplotlib Agg backend imported.
- The function actually creates the four PNGs (empty figures with axis labels), so callers can wire it up before panels are populated.
- Test: paths-and-existence tests pass.

### M2: Zoom panel

- `_render_zoom_panel` plots `state["h"]` and `crop_h` traces vs frame index.
- Twin y-axis: torso height (left, source-px), crop height (right, source-px).
- Legend: "torso height", "crop height".
- Annotations: dashed horizontal line at median crop height; user-provided `torso_multiple` in title.
- Test: constant-trajectory input produces flat lines; varying-trajectory input produces non-flat lines (RMS > 0).

### M3: Camera-motion panel

- `_render_camera_panel` plots `hypot(dx, dy)` per frame on left axis; `scale` on right axis.
- Color: motion magnitude in default blue; scale in light orange.
- Legend: "camera dxy", "scale".
- Test: synthetic MotionTrack with constant dx produces flat magnitude line at the expected value.

### M4: Runner-speed panel

- New helper `_runner_speed_per_frame(trajectory, scene_transform)` returns NumPy array of land-relative speed in scene-units per frame, with `nan` on missing frames.
- `_render_speed_panel` plots that array; secondary y-axis converts to scene-units per second when fps is available.
- Optionally apply a 5-frame rolling median to suppress stride-phase ripple. Document the choice in the docstring; do not bury it.
- Test: constant-velocity synthetic input -> approximately constant output (RMS deviation bounded).

### M5: Dashboard composite

- `_render_dashboard` calls the three panel functions on a shared-x figure with `gridspec` layout.
- 10 x 9 inches total (3 inches per row).
- Test: dashboard PNG exists and is non-empty.

### M6: CLI wiring + graceful degradation

- `--plot` flag added to analyze subparser.
- `_mode_analyze` calls `write_analyze_plots` with the right args; degrades gracefully when motion or scene_transform is absent.
- Test: integration test (mock `_mode_analyze` inputs) confirms the right number of PNGs are written under each degradation case.

### M7: Docs and changelog

- Update the four documentation files listed above.
- Re-run `tools/refresh_mode_docs.py` to refresh `docs/modes/ANALYZE.md`'s auto-generated `--help` block.
- Add changelog entry.

## Estimate

Roughly one focused day of work.

- M1+M2+M5: ~2 hours (skeleton and zoom panel, dashboard wiring).
- M3+M4: ~3 hours (camera and speed panels with their projection helper).
- M6: ~1 hour (CLI wiring, graceful degradation paths, integration test).
- M7: ~1 hour (docs and refresh_mode_docs run).
- Buffer for matplotlib quirks: ~1 hour.

About 150 LOC in `analyze_plots.py`, ~30 LOC of CLI/wiring, ~80 LOC of tests, ~50 lines of doc updates.

## Risk register

| Risk | Mitigation |
| --- | --- |
| matplotlib renders slightly differently across versions | Tests assert path existence and behavioral properties only; never lock in pixel layout |
| Plots become huge for long videos (e.g., 30k frames) | Sub-sample to ~5000 points for display when frame count exceeds that; document the cutoff. Do NOT save raw arrays into the PNG |
| Speed panel jitter from stride-phase looks alarming when it is normal | Apply a 5-frame median filter; document explicitly; offer raw-vs-smoothed in panel title |
| Users add `--plot` to `solve` or `refine` thinking it works there | The flag is on `analyze` only; `cli_args.py` parser tests should confirm it is rejected on other subcommands |
| Camera-motion cache age mismatch | The active-marker resolver already validates this; falling-back to "skip camera panel" is the right default |

## Out of scope (file as separate plans if wanted)

- Interactive HTML dashboard (plotly).
- Plotly + matplotlib parity.
- Per-interval shading on the x-axis showing solved interval boundaries and confidence tier.
- Plotting fwd/bwd disagreement, blob coverage, or other tracker diagnostics; that belongs in a separate "track quality plot" feature, not here.
- Reading EXIF / file metadata for plot titles beyond the video stem.
- Multi-video comparison plots (cross-clip overlay).

## Next step after approval

If approved, implement in the milestone order above. Each milestone is a separate commit so reviewers can step through. M1 is a no-op skeleton (paths exist, panels empty) - a useful starting point because it lets the CLI wiring land independently of any rendering choices.
