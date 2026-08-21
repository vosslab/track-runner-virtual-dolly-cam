# Track runner v3 specification

This document is subordinate to
[TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md). On conflict, the
contract wins and this document is corrected.

Status: v3, source-backed reference updated 2026-08-20

This document describes the architecture of track_runner v3, a seed-driven
interval solver for tracking a single runner in handheld video footage.

## Overview

Track runner v3 reframes a handheld video so that a chosen runner stays
centered, with adaptive zoom. The core philosophy is:

> Human establishes identity. Machine interpolates geometry.

The user draws torso rectangles on a sample of frames (seeds). The solver
propagates a bounding box forward and backward from each seed, then scores
each inter-seed interval by how well the two directions agree. Weak intervals
trigger a review pass that asks the user for more seeds. Refinement repeats
until all intervals reach acceptable confidence or the user accepts the result.

v3 adds support for approximate seeds with uncertain bounding boxes,
interval-length-aware confidence scoring, post-blend refinement with soft
spatial priors, multi-seed anchored interpolation, a PySide6-based annotation
UI, and a configurable encode filter pipeline.

See [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) for design
philosophy. See [TRACK_RUNNER_HISTORY.md](TRACK_RUNNER_HISTORY.md)
for evolution from v1 and v2.

## Module map

### Core engine

| Module | Lines | Purpose |
| --- | --- | --- |
| `track_runner.py` | ~8 | Thin entry point: `import cli; cli.main()` |
| `cli.py` | dispatch only | Parses arguments and calls the selected module in `modes/` |
| `modes/` | mode bodies | Implements prepare, setup, seed, edit, target, solve, refine, encode, and analyze |
| `tr_config.py` | config | YAML loading and validation |
| `state_io.py` | persistence | Seed and interval-score JSON I/O |
| `torso_box_coords_io.py` | persistence | Solved torso-coordinate NPZ I/O |
| `camera_motion_artifact.py` | persistence | Camera-motion NPZ I/O |
| `trajectory_confidence.py` | confidence owner | Derives confidence from independent raw FWD/BWD geometry |
| `blend_commitment.py` | commitment owner | Resolves a disagreement run from canonical residual/DoG evidence |
| `interval_solver.py` | solver | Per-interval Hermite solve, walker dispatch, stitching, and seed stamping |
| `tr_crop.py` | crop | Whole-path dolly crop and the smooth/direct-center baselines |
| `encoder.py` | encode | Applies crop rectangles and drives ffmpeg |

### UI package (`ui/`)

| Module | Purpose |
| --- | --- |
| `workspace.py` | Qt annotation workspace |
| `frame_view.py` | `FrameView(QGraphicsView)` with cursor-anchored zoom |
| `frame_source.py` | Worker-owned asynchronous frame reader and newest-only delivery |
| `session.py` | Annotation-session state and mode wiring |
| `keymap.py` | Declarative annotation keyboard bindings |
| `seed_controller.py` | `SeedController(QObject)` for seed collection |
| `target_controller.py` | `TargetController(SeedController)` for targeted refinement |
| `edit_controller.py` | `EditController(QObject)` for seed review and editing |
| `overlay_items.py` | `RectItem`, `PreviewBoxItem`, `ScaleBarItem` overlays |
| `status_presenter.py` | `StatusPresenter` QLabel for seed status display |
| `theme.py` | Dark/light/system theme support |
| `actions.py` | `make_action()` factory for toolbar actions |
| `app_shell.py` | `AppShell(QMainWindow)` base class with theme toggle |

### Shared utilities (`common_tools/`)

| Module | Purpose |
| --- | --- |
| `tools_common.py` | Video metadata, time formatting, shared helpers |
| `frame_reader.py` | OpenCV video frame reader with seek |
| `emwy_yaml_writer.py` | EMWY YAML output writer |
| `frame_filters.py` | Display-only and encode image filters |

### Dependency graph

```
track_runner.py -> cli
cli -> modes.*
modes.solve/refine -> solve_queue, interval_solver, trajectory_confidence
interval_solver -> velocity_model, blend_commitment, blob_walk
blend_commitment -> trajectory_confidence, residual/DoG evidence
modes.encode -> tr_crop, encoder, off_frame_geometry
tr_crop -> dolly_path, torso_size_stabilizer
tr_config -> yaml, tr_paths
state_io -> seed JSON, interval-score JSON
torso_box_coords_io -> torso-coordinate NPZ
camera_motion_artifact -> camera-motion NPZ
ui.workspace -> ui.frame_view, ui.app_shell, ui.actions
ui.session -> ui.frame_source, ui.keymap, ui.*_controller
ui.seed_controller -> ui.overlay_items, ui.status_presenter
ui.edit_controller -> ui.overlay_items, ui.status_presenter
```

## CLI subcommands

The CLI has no default mode. It requires one of these nine explicit modes:

| Mode | Purpose |
| --- | --- |
| `prepare` | Create an optional fast-read working video. |
| `setup` | Write camera and motion-estimator configuration for one video. |
| `seed` | Collect human seed torso boxes. |
| `edit` | Review and change existing seeds. |
| `target` | Add seeds around weak intervals. |
| `solve` | Build current camera motion, interval scores, and torso paths. |
| `refine` | Re-solve intervals changed by additional seeds. |
| `analyze` | Report crop-path and solver diagnostics without encoding. |
| `encode` | Produce the cropped output video. |

Global options include `-i/--input`, `-c/--config`, `-w/--workers`, and
`--time-range`; they precede the mode name. Each mode owns its own options.
Run `track_runner.py --help` or the mode-specific `--help` for the current
argument contract. [MODES.md](MODES.md) is the maintained workflow reference.

## Data flow

```
Pass 1: seeds
  User draws torso boxes at seed interval
  seeding.py -> seeds JSON

Pass 2: interval solve
  interval_solver: forward + backward propagation per interval
  scoring: agreement and related geometry-based terms per interval
  -> solved trajectory + interval diagnostics

Pass 3: review
  review.py identifies weak intervals (low confidence)
  -> seed targets for refinement pass

Refinement passes (`refine` mode)
  User seeds weak regions
  interval_solver re-runs on updated seeds
  -> repeat until acceptable or user stops

Pass N: crop
  tr_crop.py: crop rectangles from solved positions and owner confidence
  -> per-frame crop rectangles

Pass N+1: encode
  encoder.py: apply crop, resize, optional filters, ffmpeg encode
  audio mux from original
  -> final output video
```

## Drawing modes and seed statuses

The user annotates each seed frame using one of four drawing modes.

### The four drawing modes

- **Visible**: the runner is fully visible. The user draws a precise torso box.
  Exact torso position is known. Full tracking confidence. Jersey color is
  not stored or used as identity evidence per contract C6.
- **Partial**: the runner is partially hidden (another runner crossing, a pole,
  etc.) but the torso position is identifiable. Precise torso box drawn.
  Used as an interval endpoint.
- **Approximate** (`a` key): the runner is fully hidden behind an obstruction
  and the exact torso position cannot be determined. The user draws a larger
  area indicating the general region. Stored as `status: "approximate"` with
  `torso_box` holding the drawn area. No `jersey_hsv`. Used as a weak interval
  endpoint (conf=0.3). It preserves machine trajectory geometry with
  approximate confidence/status; it is not an erasure anchor or exact box.
- **Not in frame** (`n` key): the runner has physically left the camera frame.
  Confirmed off-screen past the edge. No position data. It is the
  authoritative runner-absence status.

### Approximate vs not_in_frame

These are distinct conditions. `not_in_frame` means the runner is confirmed
outside the frame boundary (off-screen). Approximate means the runner is within
the frame but fully hidden, and the user draws a general area. The approximate
area gives the solver a directional hint; `not_in_frame` has no location at all.

`not_in_frame` is literal absence in runner truth: it has no torso box and no
interpolated tracking geometry. Its derived `NifSpan` covers frames strictly
between bracketing visible or partial seeds (or through the known last frame
when open-ended), and that exact span is erased from runner truth. Encode may
derive a temporary edge-anchored crop target from those bracketing solved boxes
so the output camera follows the exit edge. That target is crop-output-only
intent, never a runner state, seed, or persisted tracking geometry.

### Properties by drawing mode

| Property | visible | partial | approximate | not_in_frame |
| --- | --- | --- | --- | --- |
| Status value in JSON | `visible` | `partial` | `approximate` | `not_in_frame` |
| Box type | precise torso | precise torso | larger approximate area | none |
| Has `torso_box` | YES | YES | YES | NO |
| Runner in frame | YES | YES | YES (hidden) | NO (off-screen) |
| Interval endpoint | YES | YES | YES (weak, conf=0.3) | NO |
| Trajectory erasure | NO | NO | NO | YES, exact derived `NifSpan` |
| Default confidence | 1.0 | 1.0 | 0.3 | n/a |

## Stored seed fields

Each current seed record contains only `frame_index`, `torso_box`, `status`,
and `pass`. The file does not preserve appearance descriptors, machine output,
or a seed-creation mode. The loader derives center and size convenience values
in memory from the human-authored torso box.

## Core algorithm: bounded interval solver

The interval solver treats each inter-seed span as an independent bounded
problem. Seeds are hard anchors. Within each interval the solver runs forward
propagation (from the left seed) and backward propagation (from the right
seed), then blends the two interval paths into a scored result.

### Forward and backward propagation

`velocity_model.py` builds independent FWD and BWD Hermite paths from human
seed anchors. Stage 4 may replace promoted spans with the blob walker, using
the residual-motion evidence pipeline. Appearance evidence is not identity
evidence. FWD and BWD remain independent until the confidence/commitment
boundary.

**Per-frame state**:

```
{"cx": float, "cy": float, "w": float, "h": float,
 "conf": float, "source": str}
```

`conf` is not a per-pass decay value. `trajectory_confidence.py` owns it and
derives it from the FWD/BWD center separation normalized by their mean torso
width. The same owner applies those values to fresh, cached, analyze, and
encode trajectories.

### Confidence-weighted blending

The baseline path is a confidence-weighted average of the independent raw
paths. Disagreement is determined by `trajectory_confidence.py`, not Dice
overlap. For each contiguous disagreement run, `blend_commitment.py` compares
the FWD/BWD trajectories against one canonical residual/DoG field and commits
the whole run to the stronger evidence direction, with a bounded transition
band. Missing evidence remains explicit and leaves the baseline path rather
than selecting a confidence winner.

```
blended_cx = (fwd_conf * fwd_cx + bwd_conf * bwd_cx) / (fwd_conf + bwd_conf)
```

The output confidence remains the trajectory-confidence owner's raw-pass
agreement. Commitment selects geometry; it does not invent a second confidence
or allow a committed result to feed back into FWD/BWD scoring.

### Post-blend refinement pass (historical / aspirational)

> **Status note:** This section describes a "soft-prior" refinement pass
> that re-propagated each interval using the blended interval path as a
> spatial prior. The current `refine` CLI mode in
> [refine.py](../track_runner/modes/refine.py) `run` does
> something different -- it re-solves only intervals whose fingerprint
> changed (cache-invalidation refinement, not post-blend soft-prior
> refinement). Treat the rest of this section as the historical design
> sketch; the methodology doc and the code in
> [interval_solver.py](../track_runner/interval_solver.py)
> are the truth for what currently runs. (For the canonical definitions
> of forward / backward / blended interval path, see
> `TR_FWD_BWD_MODEL_METHODOLOGY.md`.)

Pipeline order (as designed):

1. Independent FWD/BWD propagation (first pass)
2. Fuse (first pass) -- produces the blended interval path
3. **Refinement**: re-run FWD/BWD with the blended interval path as soft
   prior, re-blend
4. Anchor-to-seeds regularization
5. Stamp confidence + erasure
6. Crop

The refinement pass, where present, must not affect the first-pass
diagnostic signal, which drives confidence scoring and seed
recommendation.

Prior weight formula:

```
prior_weight = min(0.3, blended_conf * 0.3)
```

The prior only affects `cx`/`cy`, not `w`/`h`. Low-confidence blended frames
produce near-zero prior weight, preventing error reinforcement.

At each propagated frame:

```
new_cx = (1 - prior_weight) * flow_cx + prior_weight * prior_cx
new_cy = (1 - prior_weight) * flow_cy + prior_weight * prior_cy
```

The prior is keyed by absolute frame index to eliminate alignment bugs
between forward and backward passes.

### Multi-seed anchored interpolation

After stitching intervals, a post-stitch correction pass fits a local
windowed curve through nearby seeds and nudges low-confidence frames toward
the fit. This exploits the weak kinematic prior that distance runners exhibit
locally smooth image-plane motion.

`_collect_anchor_knots()` and any seed-to-trajectory conversion must use
top-level `cx`/`cy` or compute center from `torso_box` top-left coordinates;
`torso_box[0:2]` are never center coordinates.

**Seed window**: the nearest 4 seeds on each side of the current frame.
`CubicSpline` with natural boundary conditions fits `cx`/`cy`; `PCHIP` in
log-space fits `w`/`h` to avoid overshoot on scale changes. With only 2 knots,
the fit degenerates to linear interpolation.

**Blend gains** scale with uncertainty:

- `cx`/`cy` blend: `0.5 * (1 - conf)^2`
- `w`/`h` blend: `0.3 * (1 - conf)^2`

Visible seeds are hard-pinned. Partial seeds guide the fit but are not pinned.

**Displacement caps**:

- `dx` capped at 25% of `w`
- `dy` capped at 25% of `h`
- `dw` capped at 15% of `w`
- `dh` capped at 15% of `h`

**Proximity skip**: frames within 7 of any seed (~0.23 s at 30 fps) are not
corrected. No extrapolation past the first and last seed.

**Deduplication**: when multiple seeds fall on the same frame, visible seeds
are preferred over partial. Among same-status seeds, the larger `torso_box`
area wins.

### Hypothesis generation

Competitor-based hypothesis generation was a prior-design mechanism
and is **removed as a normative scoring pillar** in the current
design. In observed footage it produced many more false-positive
competitors than true ones, and the identity-scoring it fed into
relied on HSV / appearance cues that are now banned per contract C6.

Code in `hypothesis.py` may still generate competitor candidates for
debugging or future research, but its output must not be treated as a
trusted scoring pillar. Any competitor-min-height constant in code is
a raw-pixel threshold that does not satisfy C1; the target value,
expressed in torso units, is a TODO and must come from current code
or a follow-up decision rather than a guess in this document.

Appearance-based competitor identity scoring (HSV, template
correlation, jersey color) is banned as identity evidence per C6.

### Trajectory erasure

When the runner is marked `not_in_frame`, the solver derives one absent span
from the human-authored visible/partial brackets and erases only that span.

| Drawing mode | Runner-truth policy | Endpoint | Reason |
| --- | --- | --- | --- |
| visible | retained | yes (accurate) | precise torso box |
| partial | retained | yes (accurate) | precise torso box |
| approximate | retained | yes (weak) | uncertain position |
| not_in_frame | erase strict-between NifSpan frames | no | runner off-screen |

`off_frame_geometry.build_nif_spans()` derives the absence set and
`erase_nif_span_truth()` is its only runner-truth erasure owner. The visible
and partial bracket frames remain intact. Edge anchors belong only to the
separate crop-output trajectory.

### Cyclical prior detection

`_detect_cyclical_prior()` looks for repeating patterns in the trajectory
(minimum 900 frames, period 25-40 s). When a cyclical period is detected, it
can inform seed placement and gap analysis.

## Confidence scoring

`scoring.py` scores each interval using three aggregate metrics.

### Confidence decision grid

| Agreement | Separation (margin) | Confidence | Notes |
| --- | --- | --- | --- |
| > 0.5 | > 0.5 | `high` | Trusted |
| > 0.5 | > 0.2 | `good` | Acceptable |
| > 0.2 | > 0.1 | `fair` | Borderline |
| else | | `low` | Needs seed |

Competitor-margin and appearance-based identity scoring are removed under C6.
Current diagnostics use only the documented geometry and motion fields.

### Interval-length-aware scoring

For intervals of 5 frames or fewer, the confidence tier is promoted by one
level (low to fair, fair to good). The promotion never reaches high. This
compensates for inherent noise in FWD/BWD agreement on very short intervals.

### Interval severity classification

`review.py` classifies intervals for refinement prioritization.
Severity is driven by FWD/BWD agreement and current geometry-based terms.

- **high**: very low agreement
- **medium**: low agreement
- **low**: everything else

Exact thresholds come from code; do not treat the numeric values that
previously appeared in this section as normative.

Short-interval demotion: intervals under 10 frames are unconditionally
demoted from high to medium.

Duration-based promotion: intervals longer than 10 s are promoted one level
(low to medium, medium to high).

## Refinement modes

The `--refine` flag controls which intervals trigger a new seeding round.

### suggested

Default mode. `review.py` identifies the worst intervals by confidence.
Seed targets are placed at midpoints of low-confidence intervals.

### interval

Re-seeds every inter-seed interval regardless of confidence.

### gap

Re-seeds only the largest seedless gaps (controlled by `--gap-threshold`,
default 15 s).

## Crop controller

`tr_crop.py` operates as a virtual camera operator, producing smooth crop
trajectories from the confidence-weighted tracker output.

### Parameters

| Parameter | Default | Description |
| --- | --- | --- |
| `crop_mode` | `dolly` | Crop algorithm: `dolly` (offline whole-path), `smooth`, or `direct_center` |
| `torso_height_multiple` | config value | User-facing zoom control for all crop modes |
| `crop_smoothing_attack` | 0.15 | Smooth-mode alpha for large position errors |
| `crop_smoothing_release` | 0.05 | Smooth-mode alpha for small position errors |
| `crop_max_velocity` | 30.0 px/frame | Smooth-mode maximum crop movement |
| `crop_dolly_smoothness` | 20.0 | Dolly whole-path smoothness penalty |

`target_fill_ratio` is an internal smooth-mode conversion from
`torso_height_multiple`, not a user configuration key. There is no
user-facing minimum crop-size floor; the crop paths retain only a 1-pixel
sanity floor for degenerate geometry.

### Direct-center crop mode

`crop_mode: direct_center` replaces the online `CropController` with a pure
offline signal-processing baseline. It centers the crop directly on the dense
solved trajectory, applies forward-backward EMA to crop size only, and has no
deadband, attack/release alpha, center post-smoothing, or center velocity cap.

This mode treats telephoto crop generation as a reframing problem: the operator
already kept the runner roughly centered, and the system refines that framing.
Recommended for telephoto footage where the runner fills most of the frame.

Pipeline:

1. Extract `cx`, `cy`, `w`, and `h` from the solved trajectory
2. Compute crop height by averaging the height-driven and width-driven
   `torso_height_multiple` estimates, then derive width from the aspect ratio
3. Apply forward-backward EMA to crop size only (constant in `tr_crop.py`)
4. Guard only the 1-pixel degenerate-size sanity floor
5. Reconstruct and clamp rectangles to frame bounds
6. Re-clamp to frame bounds
7. Convert to integer tuples using `round()` for crop stability

`crop_mode: dolly` is the current default. It solves the full known path and
uses the existing `smooth` controller only as its explicit non-convergence
fallback. Set `crop_mode: direct_center` or `smooth` to retain either baseline.

### Crop smoothing boundary

The current crop paths do not apply an additional generic offline center
smoother. `smooth` owns its online controller behavior. `direct_center` keeps
the center glued to the solved trajectory and smooths crop size only. `dolly`
solves the whole path and falls back to `smooth` only on non-convergence.

### Telephoto preset

For tight-zoom footage (e.g., 600mm lens), add these values to the
`processing` section:

```yaml
processing:
  crop_mode: direct_center
  crop_max_velocity: 12.0
```

The forward-backward EMA position/size smoothing and the final velocity cap
are fixed constants in `tr_crop.py` and are no longer user-configurable.
`crop_max_velocity` (smooth mode only) may still be set per-video.

**Output resolution**: defaults to the median of all crop rectangle dimensions.
Can be overridden with `output_resolution: [width, height]` in config.

## Detection

YOLOv8n via ONNX runtime. No HOG fallback.

| Parameter | Value |
| --- | --- |
| Model | `yolov8n.onnx` |
| Input size | 640 px |
| Confidence threshold | 0.25 (fixed constant in `tr_detection.py`, not per-video config) |
| NMS threshold | 0.45 (fixed constant in `tr_detection.py`, not per-video config) |
| Class | person (COCO class 0) |
| ROI padding | 3.0x bbox size |
| Min ROI crop | 320 px |

## Encode pipeline

`encoder.py` decodes frames with OpenCV, applies crop rectangles, optionally
runs filters, and encodes with ffmpeg.

### Encode filters

Two filter engines, applied in order: OpenCV per-frame filters run first,
then ffmpeg temporal filters.

**OpenCV filters** (per-frame):

| Filter | Parameters |
| --- | --- |
| `bilateral` | d=9, sigmaColor=75, sigmaSpace=75 |
| `clahe` | clipLimit=2.0, tileGridSize=(8, 8) |
| `sharpen` | gain=1.5 |
| `denoise` | h=10, hColor=10, template=7, search=21 |
| `auto_levels` | 1st-99th percentile per channel |

**FFmpeg filters** (temporal):

| Filter | Description |
| --- | --- |
| `hqdn3d` | High-quality 3D denoiser |
| `nlmeans` | Non-local means denoiser |

When any encode filter is active, resize uses `cv2.INTER_LANCZOS4` instead
of `cv2.INTER_LINEAR`.

Configure via `--encode-filters bilateral,hqdn3d` on CLI or
`processing.encode_filters` in config YAML.

### Display-only filters

`common_tools/frame_filters.py` provides display-only filters for the
annotation UI. These do not affect detection or color extraction.

Presets: `none`, `bilateral`, `clahe`, `bilateral+clahe`, `sharpen`,
`edge_enhance`.

### Debug overlay

When `-d` is set, the encoder draws tracking data on output frames. Colors and
styles are loaded from [overlay_styles.yaml](../track_runner/overlay_styles.yaml)
via `overlay_config`. Semantic roles:

- **Accepted box**: solid, colored by tracking source (seed status on seed frames)
- **Forward track**: dashed, prediction color
- **Backward track**: dashed, prediction color
- **Lost/no data**: lost color for status text

All elements scale with output resolution and box size. Transparency values
(box blending, text blending) are configured in `encoder_overlay` section of
the YAML.

### Parallel encoding

`encode_cropped_video_parallel()` splits the video into segments and encodes
with 4 worker processes, then concatenates.

### Video output

| Parameter | Default |
| --- | --- |
| Codec | libx264 |
| CRF | 18 |
| Container | inferred from extension |

## Annotation UI

PySide6-based annotation window replacing OpenCV popup loops.

### Window structure

`AnnotationWindow(AppShell)` contains `FrameView(QGraphicsView)` as central
widget, with a mode toolbar and status bar. Mode toolbar has mutually-exclusive
Seed/Target/Edit actions.

### Mode accent colors

| Mode | Color |
| --- | --- |
| Seed | `#0D9488` (teal) |
| Target | `#3B82F6` (blue) |
| Edit | `#8B5CF6` (purple) |

### Status colors

| Status | Color |
| --- | --- |
| visible | `#22C55E` (green) |
| partial | `#F59E0B` (amber) |
| approximate | `#F97316` (orange) |
| not_in_frame | `#94A3B8` (slate) |

### Zoom

Cursor-anchored zoom: 1.25x per wheel tick, clamped 0.5x to 10x. Scale bar
appears in top-right corner when zoom > 1.05x.

### Scrub step sizes

`[` and `]` keys cycle through presets: 0.1s, 0.2s, 0.5s, 1.0s, 2.0s, 5.0s.
Hold Alt for 5x multiplier, Shift for 2x multiplier.

### Keyboard shortcuts (seed controller)

| Key | Action |
| --- | --- |
| ESC, q | Quit |
| SPACE | Skip frame |
| LEFT/RIGHT | Navigate frames |
| `[`, `]` | Decrease/increase step size |
| n | Mark not_in_frame |
| p | Mark partial |
| a | Draw approximate area |
| f | Use FWD/BWD average position |
| mouse drag | Draw torso box |

### Theme

`apply_theme(app, mode)` supports dark, light, and system modes. Dark palette:
bg `#0F0F23`, text `#F8FAFC`, accent `#E11D48`.

## File formats

All companion files derive from the input filename stem.

### Config YAML

Path: `tr_config/<video-stem>.track_runner.config.yaml`

Header key `track_runner` must equal `3`.

```yaml
track_runner: 3
processing:
  crop_aspect: "1:1"
  torso_height_multiple: 3.33
  video_codec: "libx264"
  crf: 18
  encode_filters: []
  output_resolution: [1280, 720]  # optional
```

### Overlay styles YAML

Path: `emwy_tools/track_runner/overlay_styles.yaml`

Centralized visual palette for all UI and encoder overlays. Loaded once per
process by `overlay_config.py`. Semantic layers:

- `seed_status`: annotation state colors (visible, partial, approximate, not_in_frame)
- `predictions`: algorithm output colors (forward, backward)
- `tracking_source`: debug overlay source colors (detection, propagated, merged, etc.)
- `workspace_mode`: editing mode accent colors (seed, target, edit)
- `draw_mode_badge`: drawing sub-mode badge colors (approximate, partial)
- `preview_box`: user-drawn confirmation box color and opacity
- `encoder_overlay`: debug overlay blending (box_opacity, text_opacity)
- `defaults`: inherited fill_opacity, line_style, thickness_tier
- `thickness_tiers`: named scale multipliers (normal=1.0, heavy=2.0)

Visual encoding model:

- **Color** = semantic state (what the annotation means)
- **Line style** (solid/dashed) = object certainty (confirmed vs inferred)
- **Opacity/fill** = spatial extent without blocking the frame
- **Thickness** = emphasis tier (confirmed/authored vs inferred/predicted)

### Seeds JSON

Path: `tr_config/<video-stem>.track_runner.seeds.json`

Header key `track_runner_seeds` must equal `3`.

**Coordinate convention**: `torso_box` stores `[x, y, w, h]` where `x, y` is
the **top-left corner** of the bounding rectangle. `load_seeds` derives `cx`,
`cy`, `w`, and `h` in memory for consumers; these values are not stored.

### Coordinate spaces and storage boundary

Three coordinate spaces exist; the full definitions and the per-callsite space
table are in [COORDINATE_SPACES.md](COORDINATE_SPACES.md), which is the source
of truth on conflict.

- SOURCE: full-frame pixels at the video's native resolution. This is the
  storage and consumption space. The seed JSON above is SOURCE, the per-frame
  torso-box NPZ (written and read via `torso_box_coords_io.py`) is SOURCE, and the encoder
  consumes SOURCE.
- PROCESSED: post-bin analysis pixels. `reader.width`/`reader.height` are
  PROCESSED. The blob walker decodes, steps, and selects candidates here. At
  `bin_factor = 1`, PROCESSED equals SOURCE.
- SCENE: a frame-0-anchored internal space used only inside the Hermite leg,
  built from `MotionTrack.dx/dy/scale`. Because `MotionTrack.dx/dy` are stored
  in SOURCE pixels (camera motion upscales by `bin_factor` before persist), the
  `SceneTransform` pixel side is SOURCE.

The solve and walker run in PROCESSED space by default. The default bin factor
is `floor(source_width / 1440)` (`TARGET_DEFAULT_WIDTH_PX` constant in
`common_tools/frame_reader.py`): 4K bins at 2, 2.8K bins at 2, 1440p and 1080p
stay at bin=1 (full-res). Override with `--bin N` or `--auto-bin HEIGHT`.

Storage-space rule: every value written to the torso-box npz must be SOURCE.
The Hermite leg produces SOURCE pixels directly (SOURCE seed -> SCENE -> SOURCE
pixel). The walker leg produces PROCESSED pixels, so the walker path must be
projected PROCESSED -> SOURCE exactly once, at the storage boundary,
immediately before `torso_box_coords_io.write_torso_box_coords`, via
`geometry.processed_to_source` (centers) and `geometry.processed_to_source_delta`
(width/height). At `bin_factor = 1` this projection is an identity no-op. At
`bin_factor > 1` it is mandatory for the walker path; omitting it stores
PROCESSED pixels mislabeled as SOURCE.

```json
{
	"track_runner_seeds": 3,
  "seeds": [
    {
	  "frame_index": 150,
      "torso_box": [620, 330, 40, 60],
	  "pass": 1,
	  "status": "visible"
    },
    {
	  "frame_index": 300,
	  "status": "not_in_frame",
	  "pass": 1
    },
    {
	  "frame_index": 450,
      "status": "approximate",
      "torso_box": [460, 240, 80, 120],
	  "pass": 2
    }
  ]
}
```

Valid `status` values: `visible`, `partial`, `approximate`, `not_in_frame`.

Only these four stored fields are accepted for each seed. Other seed headers,
statuses, or fields fail loudly; this single-user repository has no v2 seed
compatibility reader.

### Interval scores JSON

Path: `tr_config/<video-stem>.track_runner.interval_scores.json`.

Sole owner of per-interval scoring: interval scores, failure reasons,
pre-race summary. Reader `state_io.load_interval_scores`, writer
`state_io.write_interval_scores`.

This file does NOT carry the forward, backward, or blended interval
paths. Per-frame geometry lives elsewhere; see "Geometry cache NPZ"
below and the canonical reference in
[TR_CONFIG_FILES.md](TR_CONFIG_FILES.md).

### Torso-box-coords NPZ

Path: `tr_config/<video-stem>.track_runner.torso_box_coords.npz`.

Persists pixel-snapped uint16 `cx`, `cy`, `w`, and `h` arrays plus a JSON
manifest for each interval. Every interval persists blended SOURCE-frame
geometry only; forward and backward paths are transient solve-time inputs to
scoring and commitment. There is no debug-track sidecar. Readers reject
incomplete array groups or a path whose length disagrees with its manifest
interval. Full schema is in [TR_CONFIG_FILES.md](TR_CONFIG_FILES.md).

## Key constants

| Component | Parameter | Value |
| --- | --- | --- |
| Trajectory confidence | Raw-pass agreement | exp(-center separation / mean torso width) |
| Blend commitment | Disagreement decision | One canonical residual/DoG evidence run |
| Scoring | Agreement threshold (high) | > 0.5 |
| Scoring | Short interval promotion | <= 5 frames |
| Crop | Target fill ratio | 0.30 |
| Crop | Smoothing attack | 0.15 |
| Crop | Smoothing release | 0.05 |
| Crop | Max velocity | 30.0 px/frame |
| Crop | Alpha floor | 0.02 |
| Anchor | Proximity skip | 7 frames |
| Anchor | Blend scale (cx/cy) | 0.5 |
| Anchor | Blend scale (w/h) | 0.3 |
| Anchor | Max displacement (cx/cy) | 25% of dimension |
| Anchor | Max displacement (w/h) | 15% of dimension |
| Anchor | Window seeds | 4 per side |
| Erasure | Approx radius | 0.5 s |
| Erasure | Not-in-frame radius | 1.0 s |
| Review | Short interval demotion | < 10 frames |
| Review | Duration promotion threshold | 10.0 s |
| Detection | Confidence threshold | 0.25 |
| Detection | NMS threshold | 0.45 |
| Encoder | Default CRF | 18 |
