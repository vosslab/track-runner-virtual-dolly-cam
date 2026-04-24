# FWD/BWD model methodology

This document is subordinate to
[TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md). On conflict, the
contract wins and this document is corrected.

Source of truth for dual-pass independence and consumption
constraints.

Owns: what each pass may read, how the returned observation is
consumed, the normative raw-cache boundary, and the forbidden
coupling patterns.

Does not own: how the motion-cue field is computed, ROI geometry,
blob extraction, cue-confidence scoring, or the concrete cache
schema. Those live in
[MOTION_CUE_HEAT_MAP.md](MOTION_CUE_HEAT_MAP.md).

Rules for preserving the dual-pass independence invariant in the
track-runner interval solver. For the motion-cue field, blob
extraction pipeline, ROI geometry, and `observe_blob_at` measurement
model, see [MOTION_CUE_HEAT_MAP.md](MOTION_CUE_HEAT_MAP.md). For the
shorter consumer-facing summary of the observation API, see
[RESIDUAL_MOTION_OBSERVATIONS.md](RESIDUAL_MOTION_OBSERVATIONS.md).

## Terminology

These three terms are the canonical vocabulary for per-interval geometry.
Use them in prose and in new identifiers.

- **forward interval path** -- the per-pass solved trajectory the FWD
  propagator emits for one seed-to-seed interval, after optional blob
  snap. Local to that interval, local to that pass.
- **backward interval path** -- the per-pass solved trajectory the BWD
  propagator emits for the same interval. Independent of the forward
  interval path; pass-local.
- **blended interval path** -- the per-interval output trajectory formed
  by combining the forward interval path and the backward interval path
  after both passes have completed. Output artifact only: it feeds
  rendering, stitching, anchor correction, and crop. It is not a seed,
  not a raw pass prediction, not a scoring object, and not a legal input
  to either pass while that interval is being solved.

Supporting terms:

- **`raw_pred`** -- the frozen Hermite-only prediction inside one pass,
  before blob snap. Pass-local; the only trajectory a gate is allowed to
  read.
- **debug interval paths** -- the per-interval forward and backward
  interval paths persisted to the opt-in
  `<video>.track_runner.debug_tracks.npz` sidecar (legacy filename) when
  solve runs with `--debug-tracks`. Saved for overlay and inspection
  only; never read by solve, refine, or scoring. Governed by contract
  clause C8.

Why "interval path" and not "track": "track" reads as whole-video and
collides with the track-and-field source material. Each of these three
objects is local to one seed-to-seed interval, so "interval path" is the
honest name.

## Overview

Every seed-to-seed interval is solved by TWO independent propagations: a
forward (FWD) pass that starts at the left seed and a backward (BWD) pass
that starts at the right seed. Each pass builds its own directionally
asymmetric Hermite curve, produces its own `raw_pred` trajectory, and then
optionally snaps to a per-frame residual-motion blob observation. The
resulting forward interval path and backward interval path are ONLY
combined at two clearly separated points: by
[track_runner/interval_solver.py](../track_runner/interval_solver.py) to
produce the blended interval path for output (`blend_paths`), and by
[track_runner/scoring.py](../track_runner/scoring.py) for a diagnostic
agreement metric computed on the two raw pass paths. Raw disagreement
between the passes is the system's primary uncertainty signal; anything
that narrows it without evidence is a regression.

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
  cache stores raw blobs only. For the concrete cache schema and the
  ROI-key mechanics see
  [MOTION_CUE_HEAT_MAP.md](MOTION_CUE_HEAT_MAP.md); this doc owns only
  the normative "no decisions in the cache" rule. Locked by
  `test_blob_accepted_at_frame_t_does_not_influence_frame_t_plus_one`.
- **Residual cache holds image-derived data only.** Sharing IMAGES is
  not sharing DECISIONS. ROI quantization collapses sub-quantum FWD
  vs BWD jitter to one cache entry and leaves meaningful divergence
  with distinct entries; the concrete `ROI_QUANT` mechanics live in
  [MOTION_CUE_HEAT_MAP.md](MOTION_CUE_HEAT_MAP.md). Locked by
  `test_roi_quantization_collapses_subpixel_jitter`.
- **Seeds are hard anchors in both passes.** Endpoints are never moved
  by blob snap: `_apply_blob_snap` short-circuits on `i == 0` and
  `i == num - 1` (blob_gate = "skipped"). Locked by
  `test_seed_endpoints_never_moved_by_blob`.
- **The blended interval path is output-only.** It feeds rendering and
  anchor correction. It MUST NOT feed the agreement metric. See
  `compute_agreement(forward_path, backward_path)` at
  [scoring.py line 192 / 473](../track_runner/scoring.py) which takes the
  raw pass paths, not `blended_path`.
- **Agreement metrics come from the raw forward and backward interval
  paths, never from the blended one.** `blended_path` is accepted as an
  optional argument in `score_interval_analytical` but used only for
  `velocity_consistency` (trajectory smoothness), never for `agreement`.
  Do not refactor the agreement call to take `blended_path`.

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
  |    forward_path (snap_pred)
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
       backward_path (snap_pred)

forward_path, backward_path
  |           |
  |           +--> scoring.compute_agreement()          [DIAGNOSTIC]
  |           |    (raw-pass agreement drives confidence_tier,
  |           |     review severity, seed recommendations)
  |           |
  v           v
blend_paths(forward_path, backward_path)                [OUTPUT]
  |
  v
stitch_trajectories -> anchor_to_seeds -> _apply_trajectory_erasure
  |
  v
encoder / crop
```

Agreement lives on the LEFT branch (raw forward/backward interval paths).
Everything downstream of `blend_paths` -- the blended interval path -- is
the RIGHT branch and is strictly an output concern.

## Blob snap layer

Per-frame blob observation is a LOCAL measurement channel. At every
non-endpoint, non-stationary frame the propagator queries
`residual_motion.observe_blob_at` and applies three independent gates
against its own `raw_pred`. This doc covers only pass-local
consumption and gating; the measurement pipeline,
cue-confidence scoring, and blob extraction live in
[MOTION_CUE_HEAT_MAP.md](MOTION_CUE_HEAT_MAP.md). The three gates
are:

- proximity: `dist(blob, raw[t]) <= ALPHA * h`
- direction: `dot(blob - raw[t], v_pred) >= 0` (skipped below velocity
  floor)
- motion path: capsule check against `raw[i-1]` only (asymmetric: prev-
  side noise is rare because each pass just came from a seed anchor)

The gate resolves to one of three outcomes per frame: `accepted` (blend
with displacement clamp), `rejected` (fall through to raw), `absent` (no
observation). Design details live in
`~/.claude/plans/happy-forging-valiant.md`.

**ROI coupling.** FWD and BWD share the same `residual_cache` and
may share raw blobs when their ROIs coincide; they compute
independently when the ROIs diverge. The detailed ROI-key mechanics
live in [MOTION_CUE_HEAT_MAP.md](MOTION_CUE_HEAT_MAP.md). The rule
this doc owns is simpler: sharing IMAGES is allowed, sharing
DECISIONS is not.

## What breaks the invariant

Reject these at code review:

- Reading `snap_pred[...]` inside any gate or slope computation.
- Writing accepted-blob positions, filtered blobs, or gate outcomes into
  `residual_cache`. The cache is IMAGE DATA ONLY.
- Passing `blended_path` (the blended interval path) or the stitched
  trajectory into `compute_agreement`,
  `compute_meeting_point_errors`, or any severity computation in
  [review.py](../track_runner/review.py).
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

- [docs/TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) -- primary
  truth document; non-negotiable rules.
- [docs/MOTION_CUE_HEAT_MAP.md](MOTION_CUE_HEAT_MAP.md) --
  mechanism-level technical doc: heat-map construction, ROI
  geometry, blob extraction, corridor filter, cue-confidence scoring,
  concrete cache schema.
- [docs/TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) -- signal
  hierarchy, dual scoring philosophy, separation of concerns.
- [docs/TRACK_RUNNER_V3_SPEC.md](TRACK_RUNNER_V3_SPEC.md) -- interval
  scoring schema, severity rules, blob coverage fields.
- [docs/RESIDUAL_MOTION_OBSERVATIONS.md](RESIDUAL_MOTION_OBSERVATIONS.md)
  -- thin consumer-facing summary of the observation API.
- [docs/CHANGELOG.md](CHANGELOG.md) -- 2026-04-17 motion-cue removal
  and blob-snap landing entries.
- `~/.claude/plans/happy-forging-valiant.md` -- design plan for the
  per-frame blob snap inside the propagator.
