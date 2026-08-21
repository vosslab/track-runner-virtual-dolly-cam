# Stage 1: camera motion method

This doc describes how Stage 1 of the track-runner solve pipeline
estimates per-frame camera motion and turns it into the
`SceneTransform` used by every downstream stage. It is descriptive,
not normative; the non-negotiable rules live in
[TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md).

The Stage 1 implementation lives in
[camera_motion.py](../track_runner/camera_motion.py).
The scene-coordinate transform built from its output lives in
[scene_coords.py](../track_runner/scene_coords.py).

## Purpose

Stage 1 runs once per video before any interval is solved. It produces
a per-frame record of how the camera translated and zoomed between
consecutive frames, so all downstream geometry can be expressed in a
single scene coordinate system anchored at frame 0. Without this
prerequisite, seed positions and runner trajectories would be tangled
with camera pan and zoom.

Stage 1 coordinates per-pair measurement and builds one motion track before
the interval solver starts. Its output is then handed to every solver worker
through the process-pool initializer.

## Output: `MotionTrack`

`MotionTrack` is a small dataclass with four float32 arrays, each of
length `video_frame_count`. The dataclass is defined in
[camera_motion.py](../track_runner/camera_motion.py)
lines 47-66.

| Field | Meaning |
| --- | --- |
| `dx` | Per-frame x translation in pixels (frame N relative to frame N-1). `dx[0] = 0` by construction. |
| `dy` | Per-frame y translation in pixels. `dy[0] = 0`. |
| `scale` | Per-frame scale factor. `1.0` means no zoom change. For `fixed_zoom`, the array is all ones and is not persisted to disk. |
| `quality` | Phase correlation response in `[0, 1]` for each consecutive pair. Higher is more confident. Consumed by scoring as `motion_quality`. |

Sign convention: positive `dx` means the scene contents shifted to the
right between frame N-1 and frame N. Equivalently, the camera panned
left.

Invariants:

- `len(dx) == len(dy) == len(scale) == len(quality) == video_frame_count`.
- All four arrays are dtype `numpy.float32`.
- `quality[0] == 0` (no prior frame to correlate against).

## Algorithm overview

Each consecutive frame pair is registered with `cv2.phaseCorrelate` on
their grayscale conversions. Phase correlation finds the integer-plus-
sub-pixel translation that maximizes spectral alignment, returning both
the shift and a response value in `[0, 1]` that grades how peaky the
correlation surface was. For zoom-aware estimators, a log-polar warp is
applied first so that scale changes show up as translation along the
log-radius axis, and a second `phaseCorrelate` recovers the scale.

Three estimators implement this idea with different scale handling.

## Three estimator variants

The estimator is selected once per video from config (see
[Configuration](#configuration)). All three subclass
`MotionEstimator` and return a `MotionTrack`.

### FixedZoomEstimator (default)

Implementation: lines 100-230 of
[camera_motion.py](../track_runner/camera_motion.py).

Use this when zoom is constant for the whole video. It is the default
and the right choice for most footage.

Method:

- Convert each frame to grayscale (`cv2.cvtColor`, `BGR2GRAY`).
- Compute `cv2.phaseCorrelate(prev_gray, curr_gray, hann_window)` for
  every consecutive pair. The Hann window is built only when the frame
  is square and no larger than `window_size = 64` (lines 152-160);
  otherwise no window is applied. This is a quirk of the current code:
  for typical landscape video the Hann window is effectively disabled.
  See [Known limitations](#known-limitations).
- Store `shift[0]` -> `dx[i]`, `shift[1]` -> `dy[i]`, `response` -> `quality[i]`.
- After the loop, apply a 3-frame median filter to `dx` and `dy` to
  suppress single-frame outliers (`_median_filter_1d`, lines 207-230).
- `scale` is left as the all-ones initial array.

### DiscreteZoomEstimator

Implementation: lines 234-409 of
[camera_motion.py](../track_runner/camera_motion.py).

Use this for cameras with discrete zoom steps, for example iPhone
1x / 2x / 5x lens switches. Zoom transitions appear as one or two
frames of large scale change, between long stretches of unit scale.

Method:

- Translation: same `cv2.phaseCorrelate` pass as fixed zoom, but with
  the Hann window unconditionally built at frame size and `CV_64F`
  precision (line 271).
- Raw scale per pair: `_estimate_scale_logpolar` (lines 341-386). The
  two grayscale frames are warped with `cv2.warpPolar` into a
  256 x 256 log-polar image centered on the frame, and a second
  `cv2.phaseCorrelate` recovers a horizontal shift. Scale is
  `exp(shift_x * log(log_base))` where
  `log_base = max_radius / lp_size[0]`. Result clamped to
  `[0.5, 2.0]`. If the log-polar response falls below `0.1`, scale
  is forced to `1.0` (line 380-381).
- Zoom-jump detection (lines 300-327): a 5-frame sliding window walks
  the raw scale array. Whenever
  `max(window) / max(min(window), 1e-6) > 1.40`, the cumulative scale
  is updated by the current raw value and snapped to the nearest
  ratio in `camera.zoom_levels` (e.g. `[1, 2, 5]`). Outside a jump
  window, per-frame `scale` is set to `1.0`. The result is a `scale`
  array that stays at 1.0 except on the one or two frames where a
  zoom transition was localized.
- Median filter (3-frame) is applied to `dx` and `dy`. The `scale`
  array is left as-is so the snap step is preserved.

### ContinuousZoomEstimator

Implementation: lines 413-492 of
[camera_motion.py](../track_runner/camera_motion.py).

Use this for cameras with smoothly varying zoom (e.g. powered zoom
lenses). Scale changes gradually rather than in discrete steps.

Method:

- Translation pass identical to the discrete estimator.
- Per-frame scale comes directly from the same
  `_estimate_scale_logpolar` helper, with no jump detection and no
  snapping.
- Stricter quality gate: if the translation response is below `0.3`,
  `scale[i]` is forced to `1.0` for that frame (line 474). This
  prevents the noisier log-polar estimate from corrupting frames
  where even the simpler translation correlation was unreliable.
- 3-frame median filter is applied to `dx`, `dy`, and `scale`.

## From `MotionTrack` to `SceneTransform`

`SceneTransform` (defined in
[scene_coords.py](../track_runner/scene_coords.py)) wraps a
`MotionTrack` and precomputes three cumulative arrays once at
construction (lines 36-40):

```
cum_dx    = numpy.cumsum(motion_track.dx)
cum_dy    = numpy.cumsum(motion_track.dy)
cum_scale = numpy.cumprod(motion_track.scale)
```

These represent the camera's total accumulated motion since frame 0.
Pixel-to-scene and scene-to-pixel conversions are direct affine
transforms (lines 42-97):

```
# pixel -> scene
scene_x = (px - cum_dx[frame]) / cum_scale[frame]
scene_y = (py - cum_dy[frame]) / cum_scale[frame]

# scene -> pixel
px = sx * cum_scale[frame] + cum_dx[frame]
py = sy * cum_scale[frame] + cum_dy[frame]
```

Box-shaped quantities use `pixel_box_to_scene` /
`scene_box_to_pixel` (lines 100-163). Center coordinates transform
like points; widths and heights divide by `cum_scale[frame]` going
into scene space and multiply going back out. This means a torso box
that looks 80 px tall in a frame zoomed in by 2x is roughly 40 scene
units tall, which is the same number of scene units it had at the
1x baseline. That property is what lets the rest of the solver reason
about runner geometry without re-deriving the camera state.

## Cache contract

The single-file cache lives at the path returned by
`tr_paths.default_camera_motion_path(input_file)` (see
[tr_paths.py](../track_runner/tr_paths.py) lines 180-190),
which resolves to:

```
tr_config/<basename>.track_runner.camera_motion.npz
```

`save_motion_cache` writes the following arrays:

- `motion_model` (utf-8 bytes): one of `fixed_zoom`, `discrete_zoom`,
  `continuous_zoom`.
- `video_identity` (utf-8 JSON): complete source-video identity, used to
  reject cache reuse for a different source video.
- `frame_count` (int64): expected length of the per-frame arrays.
- `bin_factor` (int64): processed-frame bin used for measurement and required
  for reuse.
- `dx`, `dy`, `quality`: float32, length `frame_count`.
- `scale`: float32, length `frame_count`. Omitted for `fixed_zoom`,
  since it is constant 1.0 by construction.

`load_motion_cache` returns `None` if the file is missing or its persisted
model, analysis bin, or complete source-video identity disagrees with the
current run. A stale artifact is treated as absent so `solve` recomputes and
replaces it; other consuming modes direct the user to run `solve`. There is
no merge or partial reuse. For `fixed_zoom`, the loader synthesizes a constant
ones `scale` array so downstream `SceneTransform` code sees the same shape
regardless of model.

Note: `camera_motion.npz` is **not** tracked in
`tr_schema.SUPPORTED_ARTIFACT_SCHEMAS`; it does not participate in
the unified `SCHEMA_VERSION` (see
[TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) C9).
Staleness is determined by comparing the persisted motion model, bin factor,
and source-video identity with the current run. Camera-motion method changes
retain this artifact layout and refresh through `solve`; they do not create a
separate schema-version track.

## Downstream use

`SceneTransform` is what the rest of the solver actually consumes;
the raw `MotionTrack` is plumbed through only because a few callers
need direct access to `quality`.

- `interval_solver.solve_all_intervals` receives both objects from
  `cli._run_solve` and forwards them through the pool initializer in
  [solver_workers.py](../track_runner/solver_workers.py)
  so each worker process holds the same scene transform.
- [velocity_model.py](../track_runner/velocity_model.py)
  uses `SceneTransform` to convert seed pixel positions into scene
  coordinates for Hermite curve fitting. It never touches
  `MotionTrack` directly.
- [residual_motion.py](../track_runner/residual_motion.py)
  `build_warp_matrix` (lines 100-141) derives a per-frame-pair 2x3
  affine warp from the cumulative arrays:
  `rel_scale = cum_scale[N] / cum_scale[N+1]` and the translation
  delta in frame-N pixel space. This warp lets residual-motion
  observation cancel out camera pan and zoom before extracting
  motion blobs (see
  `TR_MOTION_CUE_HEAT_MAP.md`).
- The `quality` array feeds confidence scoring as `motion_quality`,
  surfacing intervals where the camera-motion estimate itself was
  shaky.

## Configuration

Stage 1 reads its estimator selection from `motion.estimator.type` in the run
config. Accepted values map to the three estimators:

| Config value | Estimator |
| --- | --- |
| `FixedZoomEstimator`, `fixed` | `FixedZoomEstimator` |
| `DiscreteZoomEstimator`, `discrete`, `iphone_discrete` | `DiscreteZoomEstimator` |
| `ContinuousZoomEstimator`, `continuous` | `ContinuousZoomEstimator` |

The repo default is `fixed`. A configuration that selects a non-fixed model
records that choice explicitly:

```yaml
motion:
  estimator:
    type: discrete
camera:
  zoom_levels: [2, 5]
```

`camera.zoom_levels` is consumed only by the discrete estimator; it
defines the snap targets for the zoom-jump detector.

## Performance and progress

- Stage 1 dispatches per-pair measurement to a chunked
  `concurrent.futures.ProcessPoolExecutor`. The frame-pair range
  `[test_tr_camera_motion.py](../tests/source/test_tr_camera_motion.py)
  covers estimator behavior on synthetic frames, the per-model cache
  contract (fixed omits `scale`, discrete preserves it), source-identity
  staleness handling, and median-filter outlier suppression.
- [test_tr_scene_coords.py](../tests/geometry/test_tr_scene_coords.py)
  covers `SceneTransform` round-trip invariants and zoom-jump
  composition.

## Related docs

- [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md): C4
  pre-race anchor (uses scene coordinates), C9 unified
  `SCHEMA_VERSION` (note that `camera_motion.npz` is intentionally
  outside this scheme).
- [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md): five-stage
  pipeline overview and the role of Stage 1 within it.
- `TR_MOTION_CUE_HEAT_MAP.md`: residual
  motion pipeline that consumes the per-pair warp matrix derived
  from the scene transform.
