# Residual-motion observations

Consumer-facing summary of the observation API that the FWD/BWD
propagator calls once per non-endpoint frame. This is a bridge doc,
not a source of truth: measurement details live in
[TR_MOTION_CUE_HEAT_MAP.md](TR_MOTION_CUE_HEAT_MAP.md) and pass-local
consumption invariants live in
[FWD_BWD_MODEL_METHODOLOGY.md](FWD_BWD_MODEL_METHODOLOGY.md).

Owns: the one-page summary of what a propagator call receives back.
Does not own: heat-map construction, blob extraction, ROI geometry,
cue-confidence scoring, cache schema, dual-pass invariants.

Subordinate to [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md);
on conflict, the contract wins.

## API parameters

`observe_blob_at` is called with the following required parameters:

| Parameter | Type | Meaning |
| --- | --- | --- |
| `reader` | VideoReader | Source video reader with an `fps` property |
| `frame_index` | int | Frame to measure |
| `pred_center` | tuple | Predicted runner center (x, y) in full-frame pixels |
| `pred_box` | tuple | Predicted torso box (x1, y1, x2, y2) in full-frame pixels |
| `local_tangent` | tuple | Unit tangent vector (vx, vy) along motion |
| `scene_transform` | SceneTransform | Camera stabilization and warping data |
| `residual_cache` | dict | Per-interval cache (scoped to one seed-to-seed interval) |

Optional parameters for measurement control:

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `fps` | float or None | reader.fps | Frame rate; resolves `stride` when `stride` is None |
| `stride` | int or None | resolve_stride(fps) | Per-side neighbor stride (frames between samples). Default 1 at 60 fps, 2 at 119.94 fps, 4 at 240 fps; same time span across fps |
| `half_window` | int | DEFAULT_HALF_WINDOW (4) | Per-side neighbor count. Total samples = 2 * half_window (k=0 skipped). Fixed regardless of fps |
| `precomputed_store` | dict or None | None | Worker-local store from `track_runner.residual_pre_pass`. On a `(frame_index, roi)` hit, bypasses on-the-fly residual computation. On miss, falls through to the legacy reader path |

Production code paths in Stage 4 (`solve_interval_analytical`) construct a
`precomputed_store` once via `precompute_interval_residuals` and pass it
through `_apply_blob_snap` so every per-frame `observe_blob_at` call hits
the store and avoids scattered random-access reads on the source video.
Diagnostic tools (e.g., `tools/diagnose_residual_motion.py`) pass
`precomputed_store=None` and use the legacy reader path; that path still
works but pays the per-call decode cost.

The `stride` model replaces the older `window_seconds`/`resolve_half_window`
model (removed at SCHEMA_VERSION 11). Time span between center frame and
edge sample is fixed at ~133 ms regardless of camera fps; the stride
controls how many source frames separate consecutive samples. See
[TR_MOTION_CUE_HEAT_MAP.md](TR_MOTION_CUE_HEAT_MAP.md) for the rationale
and [TR_SCHEMA_VERSION_HISTORY.md](TR_SCHEMA_VERSION_HISTORY.md) v11.

## Pipeline in five steps

1. Scene-compensated residual is computed on an ROI around the
   caller's predicted center.
2. The residual magnitude image is the motion-cue heat map.
3. Connected-component blobs are extracted from the heat map,
   area-filtered, ranked by integrated magnitude, and truncated to
   top-K.
4. Blobs are filtered to a corridor around the caller's tangent.
5. The highest-confidence surviving blob is returned as a
   `BlobObservation` (or `None`).

## What `observe_blob_at` returns

On success, a `BlobObservation` with four fields:

| Field | Meaning |
| --- | --- |
| `center_pixel` | Best blob centroid in full-frame pixels |
| `cross_track` | Signed normal component of displacement from predicted center |
| `along_track` | Signed tangent component of displacement from predicted center |
| `confidence` | Cue confidence in `[0, 1]` |

On any failure (no neighbors, empty blob list, empty corridor,
degenerate tangent), it returns `None` and the caller falls through
to its raw Hermite prediction.

## Raw vs accepted

`observe_blob_at` output is a RAW measurement. It carries no accept
or reject decision; the caller's three local gates decide that.
Anything downstream of the gate (accepted blobs, `snap_pred` values,
gate outcomes, chained counters) is pass-local state that is
forbidden from being written back into the shared residual cache.
See [FWD_BWD_MODEL_METHODOLOGY.md](FWD_BWD_MODEL_METHODOLOGY.md) for
the full invariants and
[MOTION_CUE_HEAT_MAP.md](MOTION_CUE_HEAT_MAP.md) for the concrete
cache schema.

## Out of scope

YOLO / person detection plays no role in this pipeline; see
[TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md). Jersey color, HSV,
and runner-appearance template matching are banned as identity
evidence per contract clause C6.
