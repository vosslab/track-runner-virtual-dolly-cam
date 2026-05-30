# Walker npz coordinate contract (WS1-C)

Proven contract for the blob_walk_v2 walker writing per-frame solved torso boxes
into track_runner's `torso_box_coords.npz`. This is the hard gate for WS1-B
(npz persist) and WS2-B1 (render boxes). Every claim is backed by a code site
(file + function); line numbers are pointers only and drift on edit.

Plan: `~/.claude/plans/twinkling-splashing-lemon.md` Milestone M1, WS1-C
(outside the repo; not linked).

## Summary verdict

- `state_io` does NOT transform coordinates. It is preserve-only (round +
  clip to uint16). The walker owns the processed->source projection.
- The projection reuses existing `FrameGeometry` helpers; do not hand-roll
  crop/bin/ROI math.
- There is NO separate crop origin. `FrameGeometry` is origin-preserving
  (top-left fixed at (0,0)); goodbox/bin crop only removes right/bottom
  pixels. The canonical source-to-tile chain therefore has `crop = 0`.
- Schema is additive: one unified `SCHEMA_VERSION`; no new constant, no bump.

## (a) Does state_io transform or only store? PRESERVE-ONLY (proven)

`state_io.write_torso_box_coords` reads `s["cx"], s["cy"], s["w"], s["h"]`
from each path dict, casts to float32, then `_round_clip_uint16` (round to
nearest int, clip to [0, 65535], cast uint16). No bin, ROI, or crop term is
applied. `state_io.load_torso_box_coords` rebuilds the dicts with `int(arr[i])`.
It is a pure serialization round-trip.

- Code: `track_runner/state_io.py` `write_torso_box_coords`,
  `_round_clip_uint16`, `load_torso_box_coords`.
- Consequence: **the walker (WS1-B) owns the processed->source transform.**
  Whatever box WS1-B hands `write_torso_box_coords` is stored verbatim
  (within integer rounding). If WS1-B writes processed-space boxes, the npz
  is processed-space and wrong. WS1-B MUST project to source first.

## (b) Reuse existing geometry helpers (proven; do not hand-roll)

`SeedsView` maps source->processed via
`geometry.source_to_processed` + `geometry.source_to_processed_delta`
(`track_runner/state_io.py` `SeedsView._build_processed`). The walker's
projection is the exact inverse and MUST use the existing helpers:

| Quantity | Helper WS1-B calls |
| --- | --- |
| center (cx, cy) | `geometry.processed_to_source(cx_p, cy_p)` |
| width delta (w) | `geometry.processed_to_source_delta(w_p, 0.0)[0]` |
| height delta (h) | `geometry.processed_to_source_delta(0.0, h_p)[1]` |

Helper signatures (verbatim, `common_tools/frame_reader.py`, class
`FrameGeometry`):

```
def source_to_processed(self, x: float, y: float) -> tuple[float, float]:
    return (x / self.bin_factor, y / self.bin_factor)

def processed_to_source(self, x: float, y: float) -> tuple[float, float]:
    return (x * self.bin_factor, y * self.bin_factor)

def source_to_processed_delta(self, dx: float, dy: float) -> tuple[float, float]:
    return (dx / self.bin_factor, dy / self.bin_factor)

def processed_to_source_delta(self, dx: float, dy: float) -> tuple[float, float]:
    return (dx * self.bin_factor, dy * self.bin_factor)
```

The geometry to use is `reader.geometry` from the walker's open reader
(`tools/blob_walk_v2/core/walk_io.py` `open_walker_reader` constructs a
`FrameReader` at `select_bin_factor_for_analysis(source_width)`; the
`SeedsView` is already built against this same geometry in
`walk_driver.run_interval_walk` via `load_walker_seeds_view` +
`assert_geometry_match`). Reuse that geometry; do not build a new one.

Round-trip proof (probe output, bin=4, source 3840x2178, asymmetric box):

```
processed walker box (cx,cy,w,h) = (213.5, 401.25, 46.0, 73.0)
projected source box (cx,cy,w,h) = (854.0, 1605.0, 184.0, 292.0)
round-trip back to processed = (213.5, 401.25, 46.0, 73.0)
round-trip exact (processed->source->processed) = True
npz reloaded blended box (cx,cy,w,h) = (854, 1605, 184, 292)
npz preserve-only (reload == round(source)) = True
```

## (c) Written npz path + write-mode contract

- Write path: `tr_paths.default_intervals_path(input_file)` (alias
  `default_torso_box_coords_path`), which returns
  `tr_config/{stem}.track_runner.torso_box_coords.npz` where `{stem}` is the
  video basename with the extension stripped. Code:
  `track_runner/tr_paths.py` `default_intervals_path` -> `_data_file_path`.
  - Run root: repo-root-relative `tr_config/` directory (`tr_paths.DATA_DIR`).
  - Video/corpus subdir: none; flat under `tr_config/`, namespaced by `{stem}`.
  - Filename: `{stem}.track_runner.torso_box_coords.npz`.
  - Extension stripped on purpose so a MOV->MKV remux does not orphan the
    artifact (C13 fragile-value avoidance).
  - The walker addresses videos by basename
    (`tools/blob_walk_v2/core/walk_io.py` `open_walker_reader` resolves
    `TRACK_VIDEOS/{video_basename}`); WS1-B passes that same basename/stem to
    `tr_paths.default_intervals_path` so the walker and the main solver write
    the SAME canonical file.
- One npz contains: ALL intervals for ONE video (shared file, multiple
  interval keys), not per-interval files. Keyed by
  `interval_fingerprint.compute_interval_fingerprint(left_seed, right_seed)`.
  Pre-race intervals store blended only; post-race store fwd + bwd + blended.
- Reader site: `track_runner/cli.py` `_predictions_from_torso_box_coords`
  (`state_io.load_torso_box_coords`, ~:508) and `_load_prior_results`
  (~:568). Consumers iterate `solved_intervals.values()`; the fingerprint key
  is for merge identity only, never a renderer/encoder lookup key.
- Write mode: load-existing -> merge interval key -> write-back ONCE. Pattern
  already in `cli.py` `_load_prior_results` / `solve_video`
  (`load_torso_box_coords`, mutate `intervals_file["solved_intervals"]`,
  `write_torso_box_coords`). Writing a walked interval intentionally REPLACES
  any existing solved interval for the same fingerprint key (overwrite at the
  interval-key level is intended per plan Resolved decisions).
- Full-corpus / multi-interval: AGGREGATE-BEFORE-WRITE. The driver collects
  every walked interval into one `solved_intervals` dict and calls
  `write_torso_box_coords` ONCE per video at the end. No per-worker
  read-modify-write of the npz (that loses updates because
  `write_torso_box_coords` rewrites the whole file atomically via
  `os.replace`).

Executable projection example (asymmetric sentinels; see probe in (b)):
source 3840x2178, bin_factor=4, ROI origin (37,91), box center not divisible
by bin, w != h. Processed walker box (213.5, 401.25, 46.0, 73.0) projects to
source (854.0, 1605.0, 184.0, 292.0); npz reload gives integer
(854, 1605, 184, 292). All within rounding.

## (d) ROI origin space + crop origin + source-to-tile formula

- `roi_origin_xy` on `BlobObserverTrace` is in PROCESSED-frame coords. Code:
  `track_runner/residual_motion.py` `observe_blob_at` sets
  `roi_origin_xy = (roi[0], roi[1])` from `_compute_roi(pred_cx_p, ...)`
  where `pred_cx_p`/`pred_cy_p` are the processed-frame prediction center the
  walker passes (walker callers feed processed coords via
  `load_walker_seeds_view`). The trace `corridor_blobs` / `raw_blobs`
  centroids are also processed full-frame (ROI origin added back at
  residual_motion.py ~:1249).
- NO separate crop origin exists. `FrameGeometry` is origin-preserving: the
  bin/goodbox crop removes only right/bottom pixels; the top-left origin is
  fixed at (0,0) with no offset term (`common_tools/frame_reader.py`
  `FrameGeometry` docstring; `_apply_bin` crops `[0:processed_height,
  0:processed_width]`). Therefore `crop_x = crop_y = 0` in the canonical
  formula.
- Canonical source-to-tile formula:
  `tile_x = source_x - crop_x - roi_x` (analogously for y), `crop = 0` here.
  Exactly one subtraction chain; `conversion_count` must equal 1.

  IMPORTANT render-space note (see Concern below): the WALKER RENDER TILE is
  PROCESSED-frame, not source-frame, because the walker reader bins at
  bin>1 and `reader.read_frame` returns the binned image
  (`common_tools/frame_reader.py` `_apply_bin` / `read_frame`), and the ROI
  crop in `walk_render.render_walk_tile` is applied to that binned frame with
  a processed-space `roi_origin`
  (`track_runner/residual_heat_map.py` `compute_heat_map_overlay_roi` builds
  the ROI from `reader.width/height`, which are POST-BIN). So at draw, WS2-B1
  must convert PROCESSED box -> tile:
  `tile = processed_coord - roi_processed` (crop=0). The npz is source-frame;
  WS2-B1 reads source from the npz (render-only) or the in-memory source
  projected path, and must DOWN-project source->processed
  (`geometry.source_to_processed`) before subtracting the processed ROI
  origin -- OR read the in-memory processed walker path directly. Either way
  it is exactly ONE net conversion into tile space.

Rounding policy: keep cx,cy,w,h and the edge derivation in float until the
final draw call. Derive edges from the float center first
(`cx - w/2`, etc.) BEFORE rounding; never round center and half-width
separately then add. The npz layer rounds once at write (uint16); the draw
layer rounds once at the cv2 call. Do not round in between.

## (e) C10 unified SCHEMA_VERSION: additive (proven)

`track_runner/tr_schema.py` defines ONE `SCHEMA_VERSION = 11`.
`write_torso_box_coords` always stamps `tr_schema.SCHEMA_VERSION`.
`SUPPORTED_ARTIFACT_SCHEMAS["torso_box_coords"] = {10, 11}`. The walker
writes the EXISTING artifact shape with the EXISTING writer, so it is
additive: no new version constant, no bump, no
`docs/TR_SCHEMA_VERSION_HISTORY.md` entry required. Do not introduce a
walker-specific or observer-specific version constant (C10 forbids it).

## Concern: plan's "canonical render space = source-frame" is wrong for tiles

The plan's Coordinate ledger states the canonical render box space is
source-frame. Proof shows the WALKER RENDER TILE is PROCESSED-frame at
bin>1 (the tile is a crop of the binned `reader.read_frame` output). The npz
IS source-frame (proven), but the rendered tile is processed-frame. WS2-B1
must therefore do source->processed before the single ROI subtraction, or
draw from the in-memory processed walker path. This does not change the npz
contract; it changes which space the single source-to-tile conversion lands
in. Flagged for WS2-B1 / WS2-A; WS2-A is the trace-coordinate escalation
owner per the plan.

## Handoff checklist for WS1-B

1. Open the walker reader's geometry (`reader.geometry`); do not build a new
   `FrameGeometry`.
2. For each emitted processed box, project to source with
   `geometry.processed_to_source` (center) and
   `geometry.processed_to_source_delta` (w, h). Keep float.
3. Build `solved_intervals[compute_interval_fingerprint(left, right)] =
   {start_frame, end_frame, forward_path, backward_path, blended_path}` with
   source-frame dicts; `blended_path = interval_solver.blend_paths(fwd, bwd)`.
4. Aggregate all intervals for the video in the driver, then call
   `state_io.write_torso_box_coords(tr_paths.default_intervals_path(stem), ...)`
   ONCE (load-existing first, merge keys, write back).
5. Round-trip via `state_io.load_torso_box_coords`; endpoint solved box must
   ~= human seed box (`SeedsView.source`) within rounding (C3).
