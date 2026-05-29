# Off-frame geometry

Edge-anchored crop geometry for `not_in_frame` (NIF) spans. This document
captures the storage and interpretation contract that lets the encoder
fill NIF gaps with deliberate edge-anchored crop intent instead of holding
the runner's last interior position.

## Framing

NIF crop geometry is an encode-time derived view, not a new stored
trajectory schema.

The user-authored `not_in_frame` seed remains the durable record. The
solved `torso_box_coords` artifact is unchanged. Edge-anchored geometry
is computed on demand at encode time from those existing inputs.

## Storage policy

- No schema change.
- No `SCHEMA_VERSION` bump.
- No new per-frame fields (no `is_off_frame`, no `exit_side`, no
  `frame_state`).
- No dtype change on `cx, cy` (they remain `uint16`).

The existing `cx, cy, w, h` fields carry NIF state by interpretation only
inside the encode path. NIF-filled values are never written back to the
solved trajectory artifact; they exist solely as encode-time crop targets
in an in-memory derived view.

## Edge-anchored geometry rules

Four cases, by inferred exit side:

- right exit: `cx = frame_width`, `cy = interpolated cy`.
- left exit: `cx = 0`, `cy = interpolated cy`.
- bottom exit: `cy = frame_height`, `cx = interpolated cx`.
- top exit: `cy = 0`, `cx = interpolated cx`.

Left and right are the primary cases for track-runner footage. Top and
bottom are supported for completeness but rare.

Size (`w`, `h`) across the NIF span is the integer mean of the last
visible calculated torso box before the span and the first visible
calculated torso box after. If only one bracket exists, that bracket's
size is held flat.

Interpolation along the non-pinned axis is linear across the span
between the bracketing visible coordinates. With one bracket, the
non-pinned coordinate is held flat.

## Interpretation contract

During NIF spans, `cx, cy` are an edge anchor for crop intent, not a
literal torso centroid. Outside NIF spans, `cx, cy` retain the normal
centroid meaning.

Edge-pinned values such as `cx = 0` or `cx = frame_width` are not
sentinels. Outside an active NIF span they have their normal meaning.
Code that reads the encode trajectory must consult NIF context to know
whether a given frame is edge-anchored; it must not infer NIF state from
edge-pinned coordinates alone.

## Confidence semantics

NIF crop geometry is calculated crop geometry, not weak fallback. Crop
consumes NIF frames with full authority:

- `conf = 1.0`
- `source = "nif_edge_anchor"`

This is crop confidence, not seed confidence. Seeds remain truth anchors
under contract C1. NIF crop geometry is downstream of solve and reflects
deliberate camera intent for an off-frame runner.

## NIF-context provenance

NIF context is the in-memory `NifSpan` list, the semantic object of
record. Each `NifSpan` carries `start_frame`, `end_frame`, `exit_side`,
`before_frame`, `after_frame`, `stable_w`, `stable_h`, `anchor_start`,
and `anchor_end`. The list is derived at fill time from:

- `tr_config/{basename}.track_runner.seeds.json` (NIF seeds and
  `race_start_frame`).
- `tr_config/{basename}.track_runner.torso_box_coords.npz` (bracketing
  solved torso boxes).
- Source video metadata (`frame_width`, `frame_height`, `frame_count`).

The `NifSpan` list is not persisted. A transient `set[int]` of NIF frame
indices may be derived from the list for fast per-frame lookup; that set
is a convenience cache, not the API of record.

## Pre-race incompatibility

`not_in_frame` seeds in `[TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) C1: seeds are
  human-authored truth. NIF seeds are the durable record; edge-anchored
  geometry is downstream and not a seed.
- [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) C2: torso box
  is the unit of scale for runner-relative decisions; size across an NIF
  span is bracketing-torso-mean, not a raw pixel constant.
- [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) C4: pre-race
  reference is fixed; NIF in `[TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) C5: torso
  box position and size are independent. Width and height are stable
  across an NIF span; only the non-pinned coordinate moves.
- [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) C12:
  per-frame data is limited to what is needed; NIF context is not stored
  per frame.
- [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) C13: cache is
  temporary and never depended on; the NIF frame-index set is a
  per-encode-run lookup, not a persisted cache.
