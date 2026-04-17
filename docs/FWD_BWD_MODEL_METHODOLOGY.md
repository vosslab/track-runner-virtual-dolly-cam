# FWD/BWD model methodology

Rules for preserving the dual-pass independence invariant in the track-runner
interval solver.

## Overview

Every seed-to-seed interval is solved by TWO independent propagations: a
forward (FWD) pass that starts at the left seed and a backward (BWD) pass
that starts at the right seed. Each pass builds its own directionally
asymmetric Hermite curve, produces its own `raw_pred` trajectory, and then
optionally snaps to a per-frame residual-motion blob observation. The two
resulting tracks are ONLY combined at two clearly separated points: by
[track_runner/interval_solver.py](../track_runner/interval_solver.py) for
output (`fuse_tracks`) and by
[track_runner/scoring.py](../track_runner/scoring.py) for a diagnostic
agreement metric. Raw disagreement between the passes is the system's
primary uncertainty signal; anything that narrows it without evidence is a
regression.

## Why two passes

- FWD fits a Hermite curve whose left-endpoint slope comes from a BACKWARD
  linear regression through the left seed and its earlier neighbors; the
  right-endpoint slope is the interval chord. BWD does the mirror: right
  slope from a FORWARD regression, left slope from the chord. See
  `estimate_directional_slope` and `fit_interval_curves` in
  [track_runner/velocity_model.py](../track_runner/velocity_model.py).
- The two curves are DIFFERENT interpolants of the same two endpoints. On
  straight motion they agree almost exactly; on curvature, occlusion, or
  identity ambiguity they disagree, and the disagreement is the signal.
- Without both passes there is no cheap per-interval uncertainty probe
  and the confidence tier in [scoring.py](../track_runner/scoring.py)
  collapses to a guess.

## Core invariants

- **Gates in each pass read only that pass's `raw_pred`.** Locked by
  `test_observer_inputs_depend_only_on_raw_pred` and
  `test_raw_pred_is_never_mutated_by_snap` in
  [tests/test_blob_snap.py](../tests/test_blob_snap.py). Rationale: any
  gate that reads `snap_pred[i-1]` re-introduces cross-frame state.
- **No cross-pass blob decisions are stored anywhere.** The residual
  cache stores raw blobs only (see the cache content boundary docstring
  in [residual_motion.py](../track_runner/residual_motion.py)
  `observe_blob_at`). Locked by
  `test_blob_accepted_at_frame_t_does_not_influence_frame_t_plus_one`.
- **Residual cache holds image-derived data only.** Keyed by
  `(frame_index, roi)` where `roi` is quantized to `ROI_QUANT = 8` px.
  Identical ROIs share raw blobs (image data); different ROIs trigger
  an independent residual computation. Rationale: sharing IMAGES is
  not sharing DECISIONS. Locked by
  `test_roi_quantization_collapses_subpixel_jitter`.
- **Seeds are hard anchors in both passes.** Endpoints are never moved
  by blob snap: `_apply_blob_snap` short-circuits on `i == 0` and
  `i == num - 1` (blob_gate = "skipped"). Locked by
  `test_seed_endpoints_never_moved_by_blob`.
- **The fused track is output-only.** It feeds rendering and anchor
  correction. It MUST NOT feed the agreement metric. See
  `compute_agreement(forward_track, backward_track)` at
  [scoring.py line 192 / 473](../track_runner/scoring.py) which takes the
  raw pass tracks, not `fused_track`.
- **Agreement metrics come from raw FWD and BWD, never from fused.**
  `fused_track` is accepted as an optional argument in
  `score_interval_analytical` but used only for `velocity_consistency`
  (trajectory smoothness), never for `agreement`. Do not refactor the
  agreement call to take `fused_track`.

## Signal flow

```
seeds
  |
  v
fit_interval_curves  (velocity_model.py)
  |
  +---- FWD Hermite slopes (backward regression at left seed)
  |       |
  |       v
  |    _compute_raw_pred_forward  -> raw[] (frozen, tuple-valued)
  |       |
  |       v
  |    _apply_blob_snap  (reads raw[] only; shared residual_cache)
  |       |
  |       v
  |    forward_track (snap_pred)
  |
  +---- BWD Hermite slopes (forward regression at right seed)
          |
          v
       _compute_raw_pred_backward -> raw[] (frozen)
          |
          v
       _apply_blob_snap  (reads raw[] only)
          |
          v
       backward_track (snap_pred)

forward_track, backward_track
  |           |
  |           +--> scoring.compute_agreement()          [DIAGNOSTIC]
  |           |    (raw-pass agreement drives confidence_tier,
  |           |     review severity, seed recommendations)
  |           |
  v           v
fuse_tracks(forward_track, backward_track)              [OUTPUT]
  |
  v
stitch_trajectories -> anchor_to_seeds -> _apply_trajectory_erasure
  |
  v
encoder / crop
```

Agreement lives on the LEFT branch (raw tracks). Everything downstream of
`fuse_tracks` is the RIGHT branch and is strictly an output concern.

## Blob snap layer

Per-frame blob observation is a LOCAL measurement channel. At every
non-endpoint, non-stationary frame the propagator queries
`residual_motion.observe_blob_at` and applies three independent gates
against its own `raw_pred`:

- proximity: `dist(blob, raw[t]) <= ALPHA * h`
- direction: `dot(blob - raw[t], v_pred) >= 0` (skipped below velocity
  floor)
- motion path: capsule check against `raw[i-1]` only (asymmetric: prev-
  side noise is rare because each pass just came from a seed anchor)

The gate resolves to one of three outcomes per frame: `accepted` (blend
with displacement clamp), `rejected` (fall through to raw), `absent` (no
observation). Design details live in
`~/.claude/plans/happy-forging-valiant.md`.

**ROI coupling.** Each pass's call to `observe_blob_at` quantizes its
ROI center to multiples of `ROI_QUANT = 8` px. On the typical case
(straight motion, sub-quantum FWD/BWD divergence) both passes land in
the same ROI bucket and reuse the same raw blob list. On the interesting
case (tight curvature, crowd scenes, occlusion edges) the ROIs differ
and each pass computes its own residual. The quantization buys cache
reuse on easy intervals without contaminating hard intervals where
independence matters most.

## What breaks the invariant

Reject these at code review:

- Reading `snap_pred[...]` inside any gate or slope computation.
- Writing accepted-blob positions, filtered blobs, or gate outcomes into
  `residual_cache`. The cache is IMAGE DATA ONLY.
- Passing `fused_track` (or the stitched trajectory) into
  `compute_agreement`, `compute_meeting_point_errors`, or any severity
  computation in [review.py](../track_runner/review.py).
- Reintroducing per-pass running state: any variable named `last_*`,
  `prev_accepted_*`, `miss_count`, `chain_*`, or any list that grows as
  the propagator iterates and is read by a gate.
- Computing `path_ok_next` and combining it with `path_ok_prev` via `or`
  or `and` in a way that hides the asymmetric design (see the explicit
  comment at
  [velocity_model.py `_apply_blob_snap`](../track_runner/velocity_model.py)).
- Averaging `blob_coverage_fwd` and `blob_coverage_bwd` into a single
  number inside `_stamp_blob_coverage`. Asymmetric coverage is signal.
- Running BWD's propagation with any knowledge of FWD's `snap_pred` (or
  vice versa). The shared `residual_cache` is the ONLY permitted
  cross-pass object and it holds images only.

## Testing strategy

Tests live in [tests/test_blob_snap.py](../tests/test_blob_snap.py):

- `test_delete_test_no_observer_equals_pure_hermite` -- with blob
  observation disabled (`reader=None`), the output equals the pre-patch
  pure-Hermite propagator exactly. This is the kill switch: if the snap
  layer ever cannot be cleanly removed, the invariant is broken.
- `test_blob_accepted_at_frame_t_does_not_influence_frame_t_plus_one`
  -- locks the no-cross-frame-state rule.
- `test_raw_pred_is_never_mutated_by_snap` -- locks the frozen-raw rule.
- `test_observer_inputs_depend_only_on_raw_pred` -- locks the gate-input
  rule.
- `test_seed_endpoints_never_moved_by_blob` -- locks hard anchors.
- `test_roi_quantization_collapses_subpixel_jitter` -- locks the shared-
  image / independent-decision trade in the cache.
- `test_coverage_split_reports_per_pass` -- locks the diagnostic-purity
  rule for coverage.
- `test_severity_is_independent_of_blob_coverage` -- locks the scoring
  separation: coverage is an additive diagnostic, not a confidence input.

## Historical context

Through early 2026 the solver ran motion-cue fusion as a separate stage
after propagation; the propagator itself used pure Hermite. That stage
carried chained, cross-frame state and proved impossible to reason
about. On 2026-04-17 motion-cue fusion was removed (see
[docs/CHANGELOG.md](CHANGELOG.md)) and replaced with the stateless
per-frame blob snap inside each propagator pass. The refactor preserved
the dual-pass diagnostic property by construction: each pass's snap
decisions depend only on its own `raw_pred`.

## Related docs

- [docs/TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) -- signal
  hierarchy, dual scoring philosophy, separation of concerns.
- [docs/TRACK_RUNNER_V3_SPEC.md](TRACK_RUNNER_V3_SPEC.md) -- interval
  scoring schema, severity rules, blob coverage fields.
- [docs/CHANGELOG.md](CHANGELOG.md) -- 2026-04-17 motion-cue removal
  and blob-snap landing entries.
- `~/.claude/plans/happy-forging-valiant.md` -- design plan for the
  per-frame blob snap inside the propagator.
