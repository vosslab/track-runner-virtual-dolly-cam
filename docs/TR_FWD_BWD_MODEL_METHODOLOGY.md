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
`TR_MOTION_CUE_HEAT_MAP.md`.

Rules for preserving the dual-pass independence invariant in the
track-runner interval solver. For the motion-cue field, blob
extraction pipeline, ROI geometry, and `observe_blob_at` measurement
model, see `TR_MOTION_CUE_HEAT_MAP.md`. For the
shorter consumer-facing summary of the observation API, see
[RESIDUAL_MOTION_OBSERVATIONS.md](archive/RESIDUAL_MOTION_OBSERVATIONS.md).

## Terminology

These three terms are the canonical vocabulary for per-interval geometry.
Use them in prose and in new identifiers.

- **forward interval path** -- the per-pass solved trajectory the FWD
  propagator emits for one seed-to-seed interval. Pure Hermite on Stage-3 and
  on non-promoted intervals; the windowed walker (`track_runner/blob_walk/`)
  produces a blob-coupled variant by default on Stage-4-promoted intervals (the
  `blob_pass` seam is True for that path), with a pure-stall Hermite
  fallback. Local to that interval, local to that pass.
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

- **`raw_pred`** -- the pair-local Hermite-only fallback prediction inside one
  pass. The Stage-4 walker does not read it; it gathers image evidence from
  its endpoint seed and candidate lattice.
- **agreement-debug summary** -- optional per-interval FWD/BWD agreement
  measurements emitted to `<video>.track_runner.agreement_debug.json` by a
  debug solve. It supports investigation only and is never an input to solve,
  refine, target, or scoring.

Why "interval path" and not "track": "track" reads as whole-video and
collides with the track-and-field source material. Each of these three
objects is local to one seed-to-seed interval, so "interval path" is the
honest name.

## Overview

Every seed-to-seed interval is solved by TWO independent propagations: a
forward (FWD) pass that starts at the left seed and a backward (BWD) pass
that starts at the right seed. Each pass builds a pair-local Hermite fallback
from the same two endpoint boxes and keeps its own confidence anchor. By
default the propagator emits a pure-Hermite interval path on Stage 3 and on
non-promoted intervals. On Stage-4-promoted intervals (low/fair confidence,
reader present) the windowed Viterbi walker (`track_runner/blob_walk/`) runs by
default and produces a blob-coupled interval path; the `blob_pass` seam is True
for that path. A pass with `post_seed_accepted == 0` (no accepted frame beyond
the seed, covering both zero-accept stall and seed-only stall) falls back to
its Hermite path so default-on stays never-worse-than-Hermite; the gate reads
`WalkCoverage.post_seed_accepted` and the seed-frame stall root cause remains
open. The resulting
forward interval path and backward interval path are ONLY combined at two
clearly separated points: by
[interval_solver.py](../track_runner/interval_solver.py) to
produce the blended interval path for output (`blend_paths`), and by
[scoring.py](../track_runner/scoring.py) for a diagnostic
agreement metric computed on the two raw pass paths. Raw disagreement
between the passes is the system's primary uncertainty signal; anything
that narrows it without evidence is a regression.

## Why two passes

- Each Hermite fallback uses only its two endpoint seed boxes. The endpoint
  chord supplies both derivatives, which is linear for center position and
  log-linear for size. No neighboring seed supplies an inferred slope. See
  `fit_interval_curves` in [velocity_model.py](../track_runner/velocity_model.py).
- The independent passes use separate confidence anchors and, when promoted,
  separate image-derived walker paths. Walker disagreement under occlusion or
  identity ambiguity is the uncertainty signal.
- Without both passes there is no cheap per-interval uncertainty probe
  and the confidence tier in [scoring.py](../track_runner/scoring.py)
  collapses to a guess.

## Core invariants

- **Walker decisions use image evidence only.** The windowed walker does not
  consume the Hermite fallback or a selected-path position. A cost term that
  reads a previous accepted blob re-introduces cross-frame state and fails
  review.
- **No cross-pass blob decisions are stored anywhere.** The residual
  cache stores raw blobs only. For the concrete cache schema and the
  ROI-key mechanics see
  `TR_MOTION_CUE_HEAT_MAP.md`; this doc owns only
  the normative "no decisions in the cache" rule. Locked by
  `test_blob_accepted_at_frame_t_does_not_influence_frame_t_plus_one`.
- **Residual cache holds image-derived data only.** Sharing IMAGES is
  not sharing DECISIONS. ROI quantization collapses sub-quantum FWD
  vs BWD jitter to one cache entry and leaves meaningful divergence
  with distinct entries; the concrete `ROI_QUANT` mechanics live in
  `TR_MOTION_CUE_HEAT_MAP.md`. Locked by
  `test_roi_quantization_collapses_subpixel_jitter`.
- **Worker-local pre-pass store is image-derived data, not state.**
  Inside `solve_interval_analytical`, before either pass runs, the
  worker sequentially walks its interval's frames and builds a local
  `precomputed_store` dict keyed by `(frame_index, roi)` -> `(residual
  float32, validity uint8)`. Both FWD and BWD's `observe_blob_at` calls
  read from this dict via the `precomputed_store` parameter; on a hit
  the per-frame residual computation is skipped, on a miss the call
  falls through to the direct reader path. The store contains pure
  image-derived residuals (same data the residual_cache holds, just
  pre-computed); no FWD/BWD decisions, no per-pass state, no
  cross-interval information. Per contract clause C5 the store lives
  only for the duration of one `solve_interval_analytical` call. See
  [residual_pre_pass.py](../track_runner/residual_pre_pass.py).
- **Seeds are hard anchors in both passes.** Endpoints are never moved
  by any blob-coupled stage. The walker produces no output at the
  seed endpoints; the blended interval path is anchored to seeds
  after blending. Locked by `test_seed_endpoints_never_moved_by_blob`.
- **The blended interval path is output-only.** It feeds rendering and
  anchor correction. It MUST NOT feed the agreement metric. See
  `compute_agreement(forward_path, backward_path)` at
  [scoring.py](../track_runner/scoring.py) which takes the
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
  +---- precompute_interval_residuals (residual_pre_pass.py)
  |       |  sequential walk pad_lo..pad_hi; populates
  |       |  worker-local precomputed_store keyed by (fi, roi)
  |       v
  |     precomputed_store -> consumed by both passes below
  |
  +---- FWD Hermite slopes (backward regression at left seed)
  |       |
  |       v
  |    _compute_raw_pred_forward  -> raw[] (frozen, tuple-valued)
  |       |
  |       v
  |    forward_path (pure Hermite by default)
  |       |  [blob_pass=True: Stage-4/5 promoted intervals]
  |       v
  |    windowed Viterbi walker (track_runner/blob_walk/)
  |       reads raw[] + residual_motion.observe_blob_at
  |       |
  |       v
  |    forward_path (blob-coupled, default on promoted intervals)
  |
  +---- BWD Hermite slopes (forward regression at right seed)
          |
          v
       _compute_raw_pred_backward -> raw[] (frozen)
          |
          v
       backward_path (pure Hermite by default)
          |  [blob_pass=True: Stage-4/5 promoted intervals]
          v
       windowed Viterbi walker (track_runner/blob_walk/)
          reads raw[] + residual_motion.observe_blob_at
          |
          v
       backward_path (blob-coupled, default on promoted intervals)

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
stitch_trajectories -> anchor_to_seeds -> NifSpan runner-truth erasure
  |
  v
encoder / crop
```

Agreement lives on the LEFT branch (raw forward/backward interval paths).
Everything downstream of `blend_paths` -- the blended interval path -- is
the RIGHT branch and is strictly an output concern.

## Blob observation and the windowed walker

Per-frame blob observation is provided by `residual_motion.observe_blob_at`.
The measurement pipeline (heat-map construction, ROI geometry, blob
extraction, optional acceptance-box filtering, and descriptive confidence)
is documented in
`TR_MOTION_CUE_HEAT_MAP.md`.

On Stage 3 and on non-promoted intervals, the propagator does NOT call
`observe_blob_at`. It produces a pure-Hermite `raw_pred` trajectory and returns
it as the interval path.

On Stage-4-promoted intervals the windowed Viterbi walker
(`track_runner/blob_walk/`) runs by default (`blob_pass=True` for that path).
The walker calls `observe_blob_at` at each
non-endpoint frame, retrieves raw image candidates from the trace, and
runs a window-level Viterbi DP to select a globally consistent path. A pass with
`post_seed_accepted == 0` (no accepted frame beyond the seed, covering both
zero-accept stall and seed-only stall) falls back to its Hermite path, keeping
default-on never-worse-than-Hermite; the gate reads `WalkCoverage.post_seed_accepted`.
Viterbi weight tuning and the promoted-only A/B shipped 2026-06-12 (pairwise
velocity-delta cost model); only the seed-frame stall root cause
remains open. Full
walker mechanics are in the Windowed path-selection
walker section of [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) and in
[windowed_path_selection_amendment.md](archive/windowed_path_selection_amendment.md).

**ROI coupling.** FWD and BWD share the same `residual_cache` and
may share raw blobs when their ROIs coincide; they compute
independently when the ROIs diverge. The detailed ROI-key mechanics
live in `TR_MOTION_CUE_HEAT_MAP.md`. The rule
this doc owns is simpler: sharing IMAGES is allowed, sharing
DECISIONS is not.

## What breaks the invariant

Reject these at code review:

- Reading a selected walker path position inside any gate or cost term.
  Gates must read only `raw_pred`.
- Writing accepted-blob positions, filtered blobs, or walker decisions into
  `residual_cache`. The cache is IMAGE DATA ONLY.
- Passing `blended_path` (the blended interval path) or the stitched
  trajectory into `compute_agreement`,
  `compute_meeting_point_errors`, or any severity computation in
  [review.py](../track_runner/review.py).
- Reintroducing per-pass running state: any variable named `last_*`,
  `prev_accepted_*`, `miss_count`, `chain_*`, or any list that grows as
  the walker iterates frames and is read by a gate or cost term.
- Averaging `blob_coverage_fwd` and `blob_coverage_bwd` into a single
  number inside any coverage diagnostic. Asymmetric coverage is signal.
- Running BWD's walker with any knowledge of FWD's selected path (or
  vice versa). The shared `residual_cache` is the ONLY permitted
  cross-pass object and it holds images only.

## Testing strategy

Core propagator tests live in `tests/test_tr_velocity_model.py`. These
lock the pure-Hermite path and verify that `raw_pred` is frozen and that
propagation is independent across FWD and BWD.

The `observe_blob_at` / `BlobObservation` contract tests
(`test_blob_observation_contract.py`, `test_observe_blob_at_processed_contract.py`,
`test_tr_residual_motion_bin.py`, `test_tr_residual_motion_window.py`) exercise
the PRESERVED measurement API.

Walker-specific tests live under `tests/` prefixed with `test_tr_walker_*` and
cover the windowed Viterbi path, status enum, and FWD/BWD independence of the
walker buffer. These tests apply to Stage-4/5 dispatches (`blob_pass=True`).

## Historical context

Through early 2026 the solver ran motion-cue fusion as a separate stage
after propagation; the propagator itself used pure Hermite. That stage
carried chained, cross-frame state and proved impossible to reason
about. On 2026-04-17 that stage was removed (see [CHANGELOG.md](CHANGELOG.md)).
A subsequent per-frame blob snap layer inside the propagator was introduced
and later removed in favor of the windowed Viterbi walker
(`track_runner/blob_walk/`). The propagator is pure Hermite on Stage 3 and on
non-promoted intervals; the walker is the designated blob consumer and now runs
by default on Stage-4-promoted intervals (`blob_pass=True` for that path),
with a pure-stall Hermite fallback.

## Related docs

- [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) -- primary
  truth document; non-negotiable rules.
- `TR_MOTION_CUE_HEAT_MAP.md` --
  mechanism-level technical doc: heat-map construction, ROI
  geometry, blob extraction, optional acceptance-box filtering, and
  descriptive confidence,
  concrete cache schema.
- [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) -- signal
  hierarchy, dual scoring philosophy, separation of concerns.
- [TRACK_RUNNER_V3_SPEC.md](TRACK_RUNNER_V3_SPEC.md) -- interval
  scoring schema, severity rules, blob coverage fields.
- [RESIDUAL_MOTION_OBSERVATIONS.md](archive/RESIDUAL_MOTION_OBSERVATIONS.md)
  -- thin consumer-facing summary of the observation API.
- [CHANGELOG.md](CHANGELOG.md) -- 2026-04-17 motion-cue removal
  and blob-snap landing entries.
