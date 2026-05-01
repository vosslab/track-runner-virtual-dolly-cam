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
| `window_seconds` | float | 8.0/60.0 | Background window in seconds |
| `fps` | float or None | reader.fps | Frame rate used to resolve `half_window` |
| `half_window` | int or None | None | Expert override: skip resolution and use this half-window directly |

Production code paths should pass `window_seconds` and let `fps` be derived
from the reader. The `half_window` parameter is retained for diagnose tool
and tests as an expert override; production must not pass it.

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
