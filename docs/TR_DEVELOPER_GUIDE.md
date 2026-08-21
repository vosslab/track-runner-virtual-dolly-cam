# TR developer guide

For agents and devs changing track-runner code that touches tr_config
or the residual-motion pipeline. The track runner has well-designed
primitives that hide subtle invariants (torso geometry derivation,
camera-motion compensation, residual scoring, ROI extraction). This
guide names each primitive, what it guarantees, and the reinvention
trap a new caller falls into when they bypass it.

The traps below are real -- every one was reintroduced by a new tool at
least once. Use the production primitive instead.

## Always load seeds via state_io

Do this:

```python
import state_io
seeds_dict = state_io.load_seeds(seeds_path)
for seed in seeds_dict['seeds']:
    cx = seed['cx']   # derived center, float
    cy = seed['cy']
    w  = seed['w']
    h  = seed['h']
    # torso_box stays available as [x, y, w, h] for callers that want the rect
```

Do NOT do this:

```python
import json
with open(seeds_path) as f:
    raw = json.load(f)
for seed in raw['seeds']:
    box = seed['torso_box']        # box[0]/box[1] are TOP-LEFT, not center
    cx = box[0] + box[2] / 2.0     # manual derivation, easy to get wrong
```

`state_io.load_seeds` validates the current seed schema, rejects banned
stored fields, and calls `_derive_seed_geometry` on every seed so `cx/cy/w/h`
are attached in memory as floats. Source: `track_runner/state_io.py`
constants `CANONICAL_SEED_KEYS` and `DERIVED_SEED_KEYS`. Schema details
live in [TR_CONFIG_FILES.md](TR_CONFIG_FILES.md).

Why this matters: `torso_box[0]` is the top-left x, NOT the center.
Code that treats it as center silently offsets every ROI by (w/2, h/2),
which can be larger than the torso itself for small runners.

## Never derive center from torso_box subscripts in new code

If you have a seed dict that already went through `state_io.load_seeds`,
read `seed['cx']`/`seed['cy']` directly. If you receive a `torso_box`
list from somewhere else, compute the center as `box[0] + box[2]/2.0`
explicitly and add a comment explaining why the derived fields are
unavailable. Do not silently treat `box[0]`/`box[1]` as the center.

## Always read video frames via common_tools.frame_reader

Do this:

```python
import common_tools.frame_reader
import common_tools.probe_video

probe_info = common_tools.probe_video.probe_video(video_path)
reader = common_tools.frame_reader.FrameReader(
    video_path, probe_info['fps'], probe_info['frame_count'],
)
bgr = reader.read_frame(frame_index)  # returns BGR uint8 numpy array
fps = reader.fps                       # source video fps as a float
```

Do NOT do this:

```python
import cv2
cap = cv2.VideoCapture(video_path)
cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
ok, bgr = cap.read()  # reinvented; loses HEVC keyframe-aware caching,
                       # loses byte-identity with the solver, loses fps + frame_count
                       # consistency with probe_video
```

`common_tools.frame_reader.FrameReader` is the only reader the solver
uses. It owns HEVC keyframe-aware seek, sequential-read fast paths,
fps + frame_count fields, and consistent BGR uint8 output. Any new
tool that opens a video must go through this entry point.

Why this matters: cv2.VideoCapture with absolute-seek calls on HEVC
sources can take 2-4 seconds per call due to keyframe-relative decode
cost. The shared `FrameReader` caches recent reads and uses sequential
fast paths where available, so a forward walk over 100 frames runs in
~1 second instead of ~5 minutes. Equally important, every solver path
sees the same BGR bytes.

## Load SceneTransform from camera_motion.npz at runtime

Do this:

```python
import camera_motion
import scene_coords
motion_track = camera_motion.load_active_camera_motion_or_fail(
    input_file, config, expected_bin_factor=bin_factor, video_info=video_info,
)
scene_transform = scene_coords.SceneTransform(motion_track)
```

Do NOT do this:

```python
import pickle
with open('scene_transform.pkl', 'rb') as f:
    scene_transform = pickle.load(f)   # no such file exists
```

Production never persists a `SceneTransform.pkl`. It always builds one
at runtime from `camera_motion.npz`. Schema and reuse semantics live in
[TR_CONFIG_FILES.md](TR_CONFIG_FILES.md) under "Camera motion NPZ".

## Pass fps explicitly to residual-motion helpers

The residual pipeline samples neighbor frames at a stride derived from
fps via `residual_motion.resolve_stride(fps)`. A wrong fps silently
samples wrong neighbors and the residual map degrades.

Required-argument primitives (no default):

- `residual_heat_map.compute_heat_map_roi(... , fps, ...)` -- fps is
  required.
- `residual_motion.compute_residual_for_frame(reader, frame_index,
  scene_transform, fps=..., stride=..., cache=..., roi=...)` -- fps
  required.
- `residual_motion.observe_blob_at(... , fps=..., stride=..., ...)` --
  fps required.

Source video fps is on the reader instance as `reader.fps`. For the
common case:

```python
fps = reader.fps
stride = residual_motion.resolve_stride(fps)
```

Why this matters: the source corpus mixes 60 fps and 119.94 fps clips.
Defaulting to 60 on a 119.94 source halves the neighbor temporal
spacing, contaminating every blob with non-runner motion.

## Use compute_heat_map_roi for ROI heat-map composition

```python
import residual_heat_map
result = residual_heat_map.compute_heat_map_roi(
    reader=reader,
    frame_index=frame_index,
    scene_transform=scene_transform,
    pred_center=(cx, cy),
    pred_box=(w, h),
    fps=reader.fps,
)
if result is not None:
    bgr_crop, (x_origin, y_origin) = result
    cv2.imwrite(out_path, bgr_crop)
```

`compute_heat_map_roi` returns the BGR composite at ROI scope (size
~8h x 8h) plus the top-left pixel origin. It runs the same residual +
DoG + threshold + JET-composite pipeline the UI overlay uses. The
returned crop is for display. Do not write the whole frame, do not
reimplement the residual stack, and do not apply a colormap a second
time.

The four-stage rationale (residual -> DoG -> threshold -> JET) is
documented in [TR_MOTION_CUE_HEAT_MAP.md](TR_MOTION_CUE_HEAT_MAP.md).

## Use compute_heat_map_overlay_roi for transparent heat-map overlays

When you need a transparent heat-map layer for compositing on top of
the source frame (instead of an opaque composite), use
`compute_heat_map_overlay_roi`:

```python
import residual_heat_map
result = residual_heat_map.compute_heat_map_overlay_roi(
    reader=reader,
    frame_index=frame_index,
    scene_transform=scene_transform,
    pred_center=(cx, cy),
    pred_box=(w, h),
    fps=reader.fps,
    blend_alpha=0.40,  # Optional: opacity control
)
if result is not None:
    bgra_crop, (x_origin, y_origin) = result
    # bgra_crop has shape (roi_h, roi_w, 4) uint8
    # Alpha channel: 0 (transparent) below threshold, int(blend_alpha*255) above
```

`compute_heat_map_overlay_roi` returns the same JET-colorized motion
field as `compute_heat_map_roi`, but with a proper alpha channel instead
of an opaque composite. Below-threshold pixels are fully transparent
(alpha=0); above-threshold pixels have alpha=`int(blend_alpha * 255)` so
a downstream alpha-composite shows the JET layer at the requested opacity
while keeping the underlying frame visible.

This sibling avoids reimplenting the residual + DoG pipeline. The caller
is responsible for alpha-blending at the ROI location; the returned BGRA
crop encodes transparency so the blending is correct on the first try.

## Use observe_blob_at for blob extraction with gates

```python
import common_tools.coord_space as coord_space
import residual_motion
obs = residual_motion.observe_blob_at(
    frame_index=frame_index,
    pred_center=coord_space.ProcessedPoint(cx=cx, cy=cy),
    pred_box=coord_space.ProcessedBox(cx=cx, cy=cy, w=w, h=h),
    scene_transform=scene_transform,
    reader=reader,
    residual_cache={},
    threshold=residual_motion.DEFAULT_THRESHOLD,
    half_window=residual_motion.DEFAULT_HALF_WINDOW,
    fps=reader.fps,
    stride=residual_motion.resolve_stride(reader.fps),
    precomputed_store=None,
    trace_sink=None,
)
if obs is not None:
    bcx, bcy = obs.center_pixel.cx, obs.center_pixel.cy
    conf = obs.confidence
```

`observe_blob_at` runs the production pipeline: ROI crop -> residual ->
DoG band-pass -> extract_frame_blobs -> optional acceptance box -> strongest
eligible image component. It returns a `BlobObservation` or None. The API
does not infer a track direction or modify trajectory geometry.

Do not bypass it by calling `compute_residual_for_frame` +
`extract_frame_blobs` directly. The pipeline composition matters: a
naive extract on the raw residual returns blobs from the whole frame
including the crowd and other runners.

The mechanism is documented in [TR_MOTION_CUE_HEAT_MAP.md](TR_MOTION_CUE_HEAT_MAP.md);
the per-frame consumer contract is in [RESIDUAL_MOTION_OBSERVATIONS.md](archive/RESIDUAL_MOTION_OBSERVATIONS.md).

## Common reinvention traps

- Opening source video with `cv2.VideoCapture` + absolute-seek. Fix:
  `common_tools.frame_reader.FrameReader`, composed with
  `common_tools.probe_video.probe_video`.
- Bootstrap classification using `torso_box[N]` as center. Fix:
  `state_io.load_seeds` + `seed['cx']`/`seed['cy']`.
- Heat-map PNGs sized to full frame. Fix: write the
  `compute_heat_map_roi` BGR crop without resizing.
- Heat-map default fps masking wrong-stride bugs. Fix: pass
  `fps=reader.fps` everywhere.
- Reimplementing residual + DoG + extract chain. Fix: call
  `observe_blob_at` or `compute_heat_map_roi`.
- Persisting `SceneTransform.pkl`. Fix: rebuild from
  `camera_motion.load_motion_cache` at runtime.
- Treating partial-status seeds as visible. Fix: filter
  `status == 'visible'` for blob-detection experiments; partial means
  the runner is occluded and the blob signal is compromised.
  See [TRACK_RUNNER_V3_SPEC.md](TRACK_RUNNER_V3_SPEC.md) status
  definitions.

## When you must reach past the primitives

If a production primitive does not fit (you need an intermediate result
the function does not expose, or you need a slightly different gate),
prefer one of these in order:

1. Pass `out_arrays` or `trace_sink` if the primitive accepts one.
   `compute_heat_map_roi` accepts `out_arrays` for the four intermediate
   stages; `observe_blob_at` accepts `trace_sink` for residual + raw
   blobs + candidate blobs + winner + DoG + validity mask.
2. Extend the primitive in `track_runner/` with a tested patch.
3. Last resort: copy the primitive into a `_temp_*` experiment file
   with a comment naming the source function and the divergence reason.
   Do not silently reinvent in production-adjacent paths.

## Related docs

- [TR_CONFIG_FILES.md](TR_CONFIG_FILES.md) -- on-disk schema for every
  tr_config file.
- [TR_MOTION_CUE_HEAT_MAP.md](TR_MOTION_CUE_HEAT_MAP.md) -- residual
  pipeline mechanism (four stages, threshold semantics, JET map).
- [RESIDUAL_MOTION_OBSERVATIONS.md](archive/RESIDUAL_MOTION_OBSERVATIONS.md) --
  per-frame consumer contract.
- [TR_CAMERA_MOTION_METHOD.md](TR_CAMERA_MOTION_METHOD.md) -- how
  `camera_motion.npz` is built.
- [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) -- hard
  invariants (C1-C12). C5 (intervals independent) and C6 (no appearance
  cues) are the most common ones a new tool violates.
- [TRACK_RUNNER_V3_SPEC.md](TRACK_RUNNER_V3_SPEC.md) -- seed status
  definitions, propagation contract, scoring details.
