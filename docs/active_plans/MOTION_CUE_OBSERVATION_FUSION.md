# Per-frame observation fusion in the post-solve refine pipeline

## Objective

Add a per-frame visual observation fusion pass to the solve pipeline that uses
temporal background subtraction blobs as center-position observations inside the
Hermite kinematic scaffold, reducing drift in long intervals and decreasing the
number of seeds users must place. Target: measurable reduction in center error
and number of intervals flagged for review, without regressing seed-frame accuracy.

## Design philosophy

> Hermite is the state prior. Residual motion is a per-frame center observation,
> fused continuously when unambiguous and vetoed aggressively when ambiguous.

The blob is a per-frame visual observation channel inside the Hermite scaffold.
In ordinary scenes it fires on most frames and provides strong, stable center
corrections. In ambiguous scenes (occlusion, competing runners) the system
vetoes the observation and keeps the Hermite prediction. Temporal continuity is the primary local identity gate inside an interval.
Global identity comes from human seeds.

Hermite owns: path shape, velocity continuity, box size, aspect ratio, scale,
physically plausible progression through the interval.

Blob cue owns: local center observation only. Frame-by-frame evidence of where
the runner center is, fused with the Hermite prior every frame.

Key properties:
- Continuous per-frame fusion, not occasional correction
- Anisotropic observation: cross-track tighter and trusted more, along-track
  looser and downweighted
- Two-tier acceptance: hard rejection for physical implausibility, soft
  confidence penalties for weaker signals, explicit ambiguity veto that sets
  observation weight to zero
- Temporal continuity as primary identity gate: previous accepted blob position
  carried forward, large jumps rejected as identity breaks
- "No observation" is a first-class state: no blob found or blob vetoed means
  keep Hermite prediction, log zero observation weight, continue
- Blob selection scored in Hermite-relative coordinates (along-track and
  cross-track decomposition before scoring)
- Tangent computed from original trajectory (minimum +/-5 frames), never from
  corrected positions
- Seeds protected by source check (`source == "seed"`), not confidence value

## Scope

- New module: `track_runner/residual_motion.py`
- Modified module: `track_runner/interval_solver.py` (one import, one call)
- Updated docs: `docs/CHANGELOG.md`, `docs/ROADMAP.md`

## Non-goals

- No changes to the initial FWD/BWD propagation or fusion (diagnostic scoring
  must remain honest)
- No changes to `anchor_to_seeds()` (it runs after and smooths any jitter)
- No blob-based size correction (blob area is amorphous)
- No auto-seeding or autonomous seed placement
- No UI changes
- No changes to crop, encode, or annotation systems
- No race-start/end detection (deferred)

## Current state summary

The solver uses Hermite interpolation between seeds with FWD/BWD propagation,
Dice-based fusion, and `anchor_to_seeds()` spline refinement. This is purely
geometric -- no visual observation after initial solve. Long intervals accumulate
drift because errors compound without correction. Users must add many seeds to
compensate.

`tools/diagnose_residual_motion.py` (2170 lines) proves the residual motion
technique works: temporal background subtraction via camera-aligned median
stacking reveals runner blobs. The diagnostic tool validates with a two-gate
experiment (seed frames as positive control, gap frames as target). The core
computation and blob extraction are proven; the integration into the solve
pipeline is new.

Experimental validation (2026-04-16) confirmed:
- Seed gate: 5/5 found, 5/5 trackable, median distance 14.8px
- Gap gate: 5/5 found, 5/5 trackable, median cross-track -4.6px, visual
  agreement 0.93, blob-track confidence 0.97
- Compensation helps: seed c/r 3.38, gap c/r 5.75
- One failure case (frame 11837): runner occluded by soccer goal, blob picked
  wrong runner. Signature: along-track 144px vs typical < 40px. This is an
  identity selection failure, not a correction failure. Along-track magnitude
  is the primary discriminator for this failure mode.

## Architecture boundaries and ownership

| Component | Owner | Role |
| --- | --- | --- |
| `residual_motion.py` | new module | Residual computation, blob extraction, acceptance gate, observation fusion |
| `interval_solver.py` | existing | Orchestrator -- calls new module between stitch and anchor |
| `velocity_model.py` | unchanged | Hermite interpolation, propagation |
| `scoring.py` | unchanged | Interval confidence scoring |
| `anchor_to_seeds()` | unchanged | Post-correction spline smoothing |
| `diagnose_residual_motion.py` | unchanged | Standalone diagnostic tool |

### Mapping: milestones and workstreams to components and patches

| Milestone | Workstream | Component | Patches |
| --- | --- | --- | --- |
| M1: Core module | WS-1 extraction | `residual_motion.py` | Patch 1-2 |
| M1: Core module | WS-2 acceptance gate | `residual_motion.py` | Patch 3 |
| M1: Core module | WS-3 correction logic | `residual_motion.py` | Patch 4 |
| M2: Integration | WS-4 pipeline hookup | `interval_solver.py` | Patch 5 |
| M2: Integration | WS-5 verification | tests | Patch 6 |
| M3: Tuning | WS-6 parameter tuning | `residual_motion.py` | Patch 7 |
| M3: Tuning | WS-7 docs | docs | Patch 8 |

## Milestone plan

### Milestone 1: Core module

Build `track_runner/residual_motion.py` with all functions needed for per-frame
position correction.

- Depends on: none
- Entry criteria: none
- Exit criteria: module passes pyflakes, unit-level asserts on pure functions,
  `refine_with_motion_cues()` callable with trajectory + reader + scene_transform

#### Workstream breakdown

**WS-1: Residual computation extraction**
- Goal: Extract proven residual computation from diagnostic tool into production module
- Owner: coder
- Work packages: WP-1, WP-2
- Interfaces: needs VideoReader, SceneTransform; provides residual_mag + validity_mask
- Expected patches: 2 (extraction, frame cache)

**WS-2: Acceptance gate**
- Goal: Implement two-tier blob acceptance (hard reject + soft confidence penalties)
- Owner: coder
- Work packages: WP-3
- Interfaces: needs blob list, predicted position, tangent; provides accepted blob + effective confidence
- Expected patches: 1

**WS-3: Anisotropic correction**
- Goal: Implement Hermite-frame projection and bounded position correction
- Owner: coder
- Work packages: WP-4
- Interfaces: needs accepted blob, predicted state, tangent; provides corrected cx/cy
- Expected patches: 1

### Milestone 2: Pipeline integration

Wire the new module into `solve_all_intervals()` and verify end-to-end.

- Depends on: M1 exit criteria (module exists and is importable)
- Entry criteria: `residual_motion.py` passes pyflakes
- Exit criteria: solve pipeline runs on test video, seed frames unchanged,
  trajectory output includes motion-cue corrections, correction rate and
  convergence metrics printed

#### Workstream breakdown

**WS-4: Pipeline hookup**
- Goal: Add import and call site in interval_solver.py
- Owner: coder
- Work packages: WP-5
- Interfaces: needs trajectory list, reader, scene_transform, seeds from solve_all_intervals
- Expected patches: 1

**WS-5: Verification**
- Goal: Confirm correctness with tests, manual inspection, and convergence metrics
- Owner: tester
- Work packages: WP-6
- Interfaces: needs working pipeline from WS-4
- Expected patches: 1

### Milestone 3: Tuning and documentation

Tune gate thresholds on real videos. Update docs.

- Depends on: M2 exit criteria (pipeline runs end-to-end)
- Entry criteria: solve produces output on test video
- Exit criteria: changelog updated, roadmap updated, parameters validated on
  at least one video

#### Workstream breakdown

**WS-6: Parameter tuning**
- Goal: Validate and adjust gate thresholds, alpha ceiling, clamp values
- Owner: coder
- Work packages: WP-7
- Interfaces: needs working pipeline
- Expected patches: 1

**WS-7: Documentation**
- Goal: Update changelog and roadmap
- Owner: planner
- Work packages: WP-8
- Interfaces: needs final parameter values
- Expected patches: 1

## Work package specifications

### WP-1: Extract residual computation functions

- Title: Extract residual computation from diagnostic tool
- Owner: coder
- Touch points: `track_runner/residual_motion.py` (new), `tools/diagnose_residual_motion.py` (read only)
- Acceptance criteria:
  - `build_warp_matrix()` extracted unchanged
  - `compute_validity_mask()` extracted unchanged
  - `extract_frame_blobs()` extracted unchanged (with MIN_BLOB_AREA=25)
  - `compute_residual_for_frame()` implemented with half_window=4 default (9-frame
    window, matching the validated diagnostic configuration; tunable parameter)
  - Module passes pyflakes
- Verification commands:
  - `source source_me.sh && python3 -m pytest tests/test_pyflakes_code_lint.py -k residual_motion`
- Dependencies: none

### WP-2: Implement sequential frame cache

- Title: Add sliding frame cache for sequential processing
- Owner: coder
- Touch points: `track_runner/residual_motion.py`
- Acceptance criteria:
  - Cache maps frame_index to grayscale float32 array
  - Evicts entries older than `frame_index - half_window - 3` (extra buffer of 2
    frames beyond minimum to avoid edge-case cache misses)
  - Memory bounded to ~12 frames at any time
  - Sequential frame reads share cached frames (overlap optimization)
  - Strict forward-only assumption documented in docstring
- Verification commands:
  - `source source_me.sh && python3 -m pytest tests/test_pyflakes_code_lint.py -k residual_motion`
- Dependencies: WP-1

### WP-3: Implement two-tier blob acceptance gate

- Title: Build two-tier acceptance filter for blob candidates
- Owner: coder
- Touch points: `track_runner/residual_motion.py`
- Acceptance criteria:
  - `filter_blobs_to_corridor()` extracted from diagnostic tool
  - `compute_cue_confidence()` scores blobs in Hermite-relative coordinates
    (along-track and cross-track decomposed before scoring): strength (0.3),
    size plausibility (0.3), proximity (0.4). This scalar is the selection and
    margin comparator.
  - **Tier 1 (hard rejection)** -- blob must pass ALL four to be considered:
    1. Corridor containment (cross-track distance within corridor radius)
    2. Distance constraint: reject if dist > 0.75 * max(pred_w, pred_h)
    3. Minimum cue confidence >= 0.25
    4. Temporal continuity (primary identity gate): reject if previous accepted
       blob exists and distance from it exceeds max_link_dist (0.75 *
       max(pred_w, pred_h)). This is the main defense against wrong-runner
       selection in occlusion -- experimentally validated on frame 11837 where
       wrong runner shows large jump from previous accepted position.
  - **Three distinct per-frame outcomes** (must be explicitly distinct in code):
    1. **Accepted**: blob passes both tiers, observation fused, state updated
    2. **Vetoed**: blob found but ambiguous (effective confidence < 0.15),
       observation weight set to zero, state NOT updated (veto preserves
       temporal memory)
    3. **Rejected**: blob fails tier 1 hard gate, chain break rules apply
  - **Temporal continuity state management with short memory**:
    - Initialize `prev_accepted_blob = None`, `miss_count = 0` at interval start
    - On acceptance: set `prev_accepted_blob = current_blob`, `miss_count = 0`
    - On rejection or no blob found: increment `miss_count`; if `miss_count <= 3`,
      keep `prev_accepted_blob` (survives brief occlusions); if `miss_count > 3`,
      set `prev_accepted_blob = None` (clean break for long gaps)
    - On veto: do NOT update `prev_accepted_blob` or `miss_count` (ambiguous
      frame preserves temporal memory without advancing or breaking it)
  - **Tier 2 (soft confidence penalties)** -- reduce effective confidence, do not reject:
    - Direction disagreement (blob displacement dot tangent < 0): multiply score by 0.5
    - Motion direction inconsistency (blob velocity opposes tangent): multiply score by 0.5
    - Low selection margin (best blob score < 1.5x second-best): multiply score by 0.5
    - Along-track magnitude > 2.0 * pred_h: multiply score by 0.3 (secondary
      guard, demoted from tier 1 since temporal continuity handles this case
      more robustly)
  - **Ambiguity veto**: after tier 2 penalties, if effective confidence < 0.15,
    set observation weight to zero for this frame (explicit veto, not silent skip)
  - Selection margin uses the same `compute_cue_confidence()` scalar for both
    best and second-best blob comparison
- Verification commands:
  - `source source_me.sh && python3 -m pytest tests/test_pyflakes_code_lint.py -k residual_motion`
- Dependencies: WP-1

### WP-4: Implement anisotropic position correction

- Title: Build Hermite-frame projection and bounded center correction
- Owner: coder
- Touch points: `track_runner/residual_motion.py`
- Acceptance criteria:
  - `compute_trajectory_tangent()` computes tangent from original (uncorrected)
    trajectory using minimum +/-5 frame window; falls back to wider window (+/-10)
    if trajectory confidence is below 0.5 in the +/-5 range; if still unstable
    (magnitude < 0.001), skip correction for that frame entirely (do not apply
    corrections in exactly the regions where tangent is unreliable)
  - Raw along-track and cross-track computed first (used for gating in WP-3),
    then clamped values used for correction
  - Correction vector decomposed into along-track and cross-track components
    using tangent and normal unit vectors
  - Cross-track clamped to +/- 0.5 * pred_w (tighter -- cross-track is the
    more reliable and important signal, runners stay in lane)
  - Along-track clamped to +/- 0.75 * pred_h (looser -- along-track can drift
    because Hermite timing is imperfect)
  - Along-track additionally scaled by 0.5 (downweighted relative to cross-track
    because along-track blob error is larger due to motion blur and timing)
  - Corrected position reconstructed: `pred + along*tangent + across*normal`
  - Alpha formula: `ALPHA_MAX = 0.6` (constant, tunable in WP-7);
    `alpha = ALPHA_MAX * effective_confidence * (1.0 - traj_conf)`
  - Final: `new_cx = (1-alpha)*pred_cx + alpha*corrected_x` (position only, not size)
  - Tangent always computed from original trajectory snapshot taken before
    correction loop begins, never from corrected positions
  - Seed frames protected by `if entry["source"] == "seed": continue` (not by
    checking confidence == 1.0)
- Verification commands:
  - `source source_me.sh && python3 -m pytest tests/test_pyflakes_code_lint.py -k residual_motion`
- Dependencies: WP-3

### WP-5: Wire into solve pipeline

- Title: Add motion-cue refine call to solve_all_intervals
- Owner: coder
- Touch points: `track_runner/interval_solver.py`
- Acceptance criteria:
  - `import residual_motion` added to local repo modules section
  - `refine_with_motion_cues()` called between `stitch_trajectories()` and
    `anchor_to_seeds()` at line 1244
  - Reader and scene_transform passed through (already available as parameters)
  - Seeds passed for seed-frame protection
  - Progress bar shown during per-frame processing (rich.progress)
  - Summary printed: frames processed, observation usage rate (accepted/non-seed),
    veto rate, mean alpha, mean chain length, number of chain breaks
- Verification commands:
  - `source source_me.sh && python3 -m pytest tests/test_pyflakes_code_lint.py -k interval_solver`
  - `source source_me.sh && python3 -m pytest tests/ -x`
- Dependencies: WP-4

### WP-6: End-to-end verification

- Title: Verify pipeline correctness on test video
- Owner: tester
- Touch points: tests/, output_smoke/
- Acceptance criteria:
  - Solve runs to completion on a test video without errors
  - Seed frames in output trajectory have unchanged cx, cy (verified by
    `entry["source"] == "seed"` check, positions bit-identical)
  - Non-seed frames show corrections (print summary: observation usage rate,
    veto rate, mean alpha, mean chain length, chain break count)
  - **Observation usage rate**: accepted / non-seed frames (how often the blob
    is used); **veto rate**: vetoed / non-seed frames (how often ambiguity fires)
  - **Seed-adjacent stability**: verify no visible discontinuity in the 3 frames
    immediately before and after each seed frame (correction should blend
    smoothly into hard-pinned seed positions)
  - **Convergence metric**: compare FWD/BWD agreement before and after motion-cue
    correction (should not degrade)
  - **Center jerk metric**: compute mean frame-to-frame center displacement
    variance before and after (should decrease or stay similar)
  - **Mean |along| of accepted corrections**: if this stays small, gate is
    working; if it grows, wrong blobs are being accepted
  - **User pain metrics**: number of intervals flagged for review before vs
    after; number of extra seeds needed on same test video before vs after
    (the real quality-of-life measure)
  - Existing tests pass: `pytest tests/ -x`
  - No regression in pyflakes: `pytest tests/test_pyflakes_code_lint.py`
- Verification commands:
  - `source source_me.sh && python3 -m pytest tests/ -x`
  - Manual: run solve on test video, inspect trajectory output and metrics
- Dependencies: WP-5

### WP-7: Tune gate parameters on real video

- Title: Validate acceptance thresholds on real footage
- Owner: coder
- Touch points: `track_runner/residual_motion.py`
- Acceptance criteria:
  - Run on at least one real video with known problem intervals
  - Verify that corrections fire in drift-prone regions
  - Verify that corrections do NOT fire near other runners or moving objects
  - Adjust thresholds if gate is too permissive or too strict
  - Adjust ALPHA_MAX if corrections are too aggressive or too weak
  - Document final parameter values in module-level constants with comments
- Verification commands:
  - Manual: run solve, compare before/after trajectory and convergence metrics
- Dependencies: WP-6

### WP-8: Update documentation

- Title: Update changelog and roadmap
- Owner: planner
- Touch points: `docs/CHANGELOG.md`, `docs/ROADMAP.md`
- Acceptance criteria:
  - Changelog entry under today's date with description of new module and behavior
  - Roadmap "Motion-cue seed recommendation" item updated to reflect that per-frame
    correction is now implemented (move to "completed" or update scope)
- Verification commands: none (documentation only)
- Dependencies: WP-7

## Acceptance criteria and gates

### Unit gate
- `residual_motion.py` passes pyflakes
- Pure functions have assert-level tests where practical

### Integration gate
- `solve_all_intervals()` runs to completion with motion-cue refine enabled
- Seed frames are unchanged in output (checked by source field, not confidence)
- Convergence metrics do not degrade

### Regression gate
- All existing tests in `tests/` pass
- Pyflakes passes on all modified files

### Release gate
- At least one real video solved with improved trajectory in problem intervals
- No new failures introduced in previously-good intervals
- Changelog updated

## Test and verification strategy

- **Pyflakes**: run on new and modified files after each patch
- **Existing tests**: full `pytest tests/ -x` after integration (WP-5)
- **Manual verification**: solve a test video, compare trajectory quality before/after
- **Seed preservation check**: verify seed-frame positions are bit-identical in output
  (check by source field: `entry["source"] == "seed"`)
- **Failure mode check**: verify gate rejects blobs from other runners or background motion
- **Convergence metrics**: FWD/BWD agreement before vs after, center jerk reduction

## Migration and compatibility policy

- **Additive architecture**: new module, one new call in pipeline. Existing data
  schema unchanged. Solve behavior changes only by inserting the new fusion pass.
- **No schema changes**: trajectory format unchanged (same state dict keys)
- **No config changes**: parameters are hardcoded constants in the module (tunable
  later via config if proven)
- **Backward compatible**: if motion cache is missing, `refine_with_motion_cues()`
  returns trajectory unchanged
- **Deletion criteria**: none (no legacy code removed)
- **Rollback**: remove the one call in `interval_solver.py` line 1244 to disable

## Risk register

| Risk | Impact | Trigger | Owner | Mitigation |
| --- | --- | --- | --- | --- |
| Wrong blob accepted causes trajectory jump | High | Multi-runner scene, occlusion | coder | Temporal continuity hard gate (primary); along-track magnitude as tier 2 soft penalty (secondary) |
| Gate too strict, never fires | Medium | Conservative thresholds | coder | Tier 2 uses soft penalties instead of hard rejection; tuning pass in M3; summary prints correction rate |
| Residual computation too slow for long videos | Medium | 5000+ frame intervals | coder | Half_window=4 default (9 frames) + sequential cache with overlap; can reduce to 2 if needed |
| Corrected positions create velocity discontinuities | Medium | Large alpha corrections | coder | ALPHA_MAX starts at 0.6 (conservative); anchor_to_seeds() smooths after; tangent from original trajectory |
| Tangent instability in weak regions | Medium | Short window + low confidence trajectory | coder | Minimum +/-5 frame window; fall back to +/-10 if confidence low |
| Blob not found in most frames (sparse signal) | Low | Low-contrast video, small runner | coder | Design handles gracefully -- no blob = no correction |
| Plan vs implementation drift | Low | Implementation diverges from design | reviewer | Review after M1 and M2 |

## Rollout and release checklist

1. Merge M1 patches (new module, no pipeline change)
2. Merge M2 patches (pipeline hookup + verification)
3. Run solve on test video, confirm improvement via convergence metrics
4. Merge M3 patches (tuning + docs)
5. User commits

## Patch plan and reporting format

- Patch 1: `residual_motion.py` -- extract residual computation functions
- Patch 2: `residual_motion.py` -- add sequential frame cache
- Patch 3: `residual_motion.py` -- implement two-tier blob acceptance gate
- Patch 4: `residual_motion.py` -- implement anisotropic Hermite-frame position correction
- Patch 5: `interval_solver.py` -- wire refine_with_motion_cues into pipeline
- Patch 6: tests -- end-to-end verification with convergence metrics
- Patch 7: `residual_motion.py` -- tune parameters on real video
- Patch 8: docs -- update changelog and roadmap

## Documentation close-out

- `docs/CHANGELOG.md`: entry describing new module and behavior change
- `docs/ROADMAP.md`: update motion-cue item status

## Open questions and decisions needed

1. **Half-window size**: Plan uses 4 (9-frame window, matching validated
   diagnostic -- provisional default). Performance may force a 5-frame fallback
   on long videos. Tunable parameter, not a fixed theoretical choice.
2. **Tier 2 penalty multipliers**: Direction (0.5), motion direction (0.5), low
   margin (0.5), along-track magnitude (0.3). These are initial guesses. If the
   gate fires too rarely, increase these; if too many false positives, decrease.
3. **ALPHA_MAX**: Plan starts at 0.6. If false positives still cause visible jumps,
   lower to 0.4. If corrections are too weak, raise to 0.8. Exposed as a
   module-level constant for easy tuning.
4. **Along-track scale factor**: Plan uses 0.5 (half weight). Along-track blob
   error is larger than cross-track due to motion blur and timing.
5. **Tangent fallback window**: Plan uses +/-5 default, +/-10 fallback. If still
   unstable, skip correction for that frame entirely.
6. **Selection margin multiplier**: Plan uses 1.5x. If multi-runner scenes are
   common and blobs are similar in score, this may need to increase.
7. **Temporal continuity max_link_dist**: Plan uses 0.75 * max(pred_w, pred_h).
   May need tuning -- too tight rejects valid blobs during fast motion, too
   loose lets wrong-runner blobs through.
8. **Short memory duration**: Plan uses miss_count <= 3 (keep prev_blob for up
   to 3 missed frames). May need to increase for videos with longer brief
   occlusions. Chain length logging will inform this.
9. **Veto vs rejection distinction**: Plan treats veto (ambiguous) differently
   from rejection (implausible). If this adds implementation complexity without
   measurable benefit, can simplify to two outcomes. Start with three, observe.
