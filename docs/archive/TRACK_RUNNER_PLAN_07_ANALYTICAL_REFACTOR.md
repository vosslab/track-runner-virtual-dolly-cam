# Seed-anchored scene interpolation: solver rewrite plan

## Objective

Replace the track runner's visual-tracking solver with a seed-anchored analytical
solver that uses phase-correlation camera motion estimation and cubic Hermite
velocity interpolation. Two headline targets: (1) median center error < 25% of
box height across all videos (resolution- and zoom-safe), (2) median box-height
error < 10% across all videos. Box-size modeling is a top-level acceptance
criterion because historical size failure was catastrophic (torso area varies
344x across videos). Secondary local benchmark: IMG_3702 median center error
< 20px (video-specific, not the primary gate).

## Scope

- New components: camera motion estimator, scene coordinate transform, velocity
  model with directional FWD/BWD independence, setup questionnaire
- Modified components: interval solver, propagator, scoring, hypothesis, CLI, config
- Modified for schema: state_io (diagnostics v3), scoring, review, encode_analysis
- Unchanged: PySide6 UI, crop controller, encode pipeline, seeds JSON format,
  video_io, box_utils

## Non-goals

- No UI redesign (seed/edit/target controllers stay as-is)
- No crop controller changes (direct_center mode unchanged)
- No encode pipeline changes
- No full-interval optical-flow backbone
- No YOLO-led reacquisition as default solve path
- No visual tracker that overrides hard seed anchors
- Local visual correction and sanity-check warnings are deferred to after the
  analytical core proves out (see subordinate visual roles below)

## Current state summary

The current solver propagates runner position frame-by-frame using Lucas-Kanade
optical flow, patch correlation, and YOLOv8n person detection. Nine experiments
(documented in `docs/archive/TRACK_RUNNER_EXPERIMENT_HISTORY.md`) have established:

- Solver convergence error (not crop controller) drives the worst instability
- YOLO rarely selects the runner of interest in track footage
- Scale variation is huge (torso area varies 344x across videos)
- Dense seeding helps most; broad regime switching adds instability
- EMA and rate limiting are mutually exclusive (destructive interaction)
- Fill ratio 0.3 and torso_anchor 0.38 are validated composition fixes

The crop controller faithfully follows solver output. Fixing the solver is the
correct lever.

## Design philosophy

> Analytic model for geometry, visual cues for validation and short-range
> correction.

This rewrite changes three load-bearing design principles deliberately:

1. **From visual tracking to scene interpolation.** The solver no longer watches
   the runner frame-by-frame. It treats seeds as ground-truth anchors and
   interpolates between them analytically using camera motion estimation and
   smooth velocity models.

2. **Scene interpolation as primary occlusion geometry.** This rewrite does not
   newly introduce approximation through occlusion. The tool already uses
   approximate seeds as coarse positional guidance when the crop still needs a
   subject location. The change is that scene interpolation becomes the primary
   way to carry geometry through hidden spans, rather than a weak hint followed
   by local erasure. Confidence and review signals remain explicitly lower in
   hidden regions. `not_in_frame` seeds still erase trajectory (runner
   physically off-screen). This is a product-level behavior change requiring
   acceptance: the crop gains visual stability through hidden spans, but the
   solver must not overclaim certainty or let hidden-span interpolation corrupt
   confidence scoring and seed recommendations.

3. **From detection-first to seed-trust.** The old signal hierarchy makes YOLO
   the hero signal. The new design makes the analytical model the backbone and
   demotes visual tracking to three subordinate roles (see below). Seeds define
   identity; the tool trusts them. No per-frame identity check.

## Architecture boundaries and ownership

### Component map

| Component | Module path | Purpose |
| --- | --- | --- |
| motion-estimator | `track_runner/camera_motion.py` | Per-frame camera motion via phase correlation |
| scene-transform | `track_runner/scene_coords.py` | Pixel-to-scene coordinate conversion |
| velocity-model | `track_runner/velocity_model.py` | Directional Hermite interpolation + propagation |
| setup-questionnaire | `track_runner/setup_mode.py` | Per-video camera config CLI |
| interval-solver | `track_runner/interval_solver.py` | Orchestrates per-interval solve + fusion |
| propagator | `track_runner/propagator.py` | Thin wrapper calling velocity-model |
| scoring | `track_runner/scoring.py` | Dice agreement + velocity consistency |
| cli | `track_runner/cli.py` | Subcommand dispatch + pipeline orchestration |
| config | `track_runner/tr_config.py` | YAML config loading + camera section |

### Subordinate visual roles (bounded, not backbone)

Visual tracking is allowed only in narrow, bounded roles:

1. **Camera motion estimation (motion-estimator component).** Phase correlation on
   full frames. Scene stabilization, not identity tracking.
2. **Local correction near visible easy frames (deferred).** When the runner is
   large and isolated, a bounded detector nudge. Never source of truth. Deferred
   to after the analytical core proves out.
3. **Sanity-check warnings (deferred).** Flags disagreements without changing the
   solve: "analytic path disagrees with detection," "multiple plausible runners,"
   "recommend adding a seed." Deferred.

Explicitly excluded: no full-interval optical-flow backbone, no YOLO-led
reacquisition, no detector deciding identity during occlusion, no visual tracker
overriding seed anchors.

### Mapping: milestones to components and patches

| Milestone | Workstreams | Components touched | Expected patches |
| --- | --- | --- | --- |
| M1: Motion estimation | motion-api, fixed-estimator, scene-transform, tests | motion-estimator, scene-transform, config | 6-8 |
| M2: Discrete + continuous zoom | discrete-estimator, continuous-estimator, tests | motion-estimator, config | 4-6 |
| M3: Velocity model | directional-hermite, propagation, stationary-lock, tests | velocity-model, propagator | 6-8 |
| M4: Solver integration | solver-rewrite, scoring-update, occlusion-policy, schema-migration, tests | interval-solver, propagator, scoring, hypothesis, state_io, encode_analysis, review | 8-10 |
| M5: Setup mode + cleanup | setup-cli, config-camera, cleanup, docs | setup-questionnaire, cli, config | 4-6 |

---

## Milestone plan

### M1: Motion estimation foundation

**Depends on:** none
**Entry criteria:** none
**Exit criteria:** `FixedZoomEstimator` produces stable per-frame dx/dy on all 7
test videos; scene-transform round-trips seed positions within 0.5px; NPZ caching
works with video identity validation.

**Goal:** Establish the `MotionTrack` API contract, implement the fixed-zoom
estimator, and build the scene coordinate transform. All downstream code consumes
`MotionTrack` and does not care which estimator produced it.

**Deliverables:**
- `camera_motion.py` with `MotionTrack` dataclass, `MotionEstimator` base,
  `FixedZoomEstimator`
- `scene_coords.py` with `SceneTransform` class
- `tr_config.py` updated for `camera` section
- NPZ caching with video identity check
- Unit tests for motion estimation and coordinate transform

#### Workstream breakdown

**Workstream: motion-api**
- Goal: Define `MotionTrack` contract and `MotionEstimator` interface
- Owner: coder
- Work packages: 2
- Interfaces: provides `MotionTrack` consumed by scene-transform and all downstream
- Expected patches: 2

**Workstream: fixed-estimator**
- Goal: Implement `FixedZoomEstimator` using `cv2.phaseCorrelate()`
- Owner: coder
- Work packages: 3
- Interfaces: needs motion-api; provides working estimator for M3
- Expected patches: 3

**Workstream: scene-transform**
- Goal: Build `SceneTransform` with cumulative motion and coordinate conversion
- Owner: coder
- Work packages: 2
- Interfaces: needs motion-api; provides transforms for velocity-model
- Expected patches: 2

**Workstream: M1-tests**
- Goal: Unit and smoke tests for all M1 components
- Owner: tester
- Work packages: 2
- Interfaces: needs all M1 workstreams
- Expected patches: 1-2

#### Work packages

**WP-1.1: Define MotionTrack dataclass and MotionEstimator interface**
- Owner: coder
- Touch points: `track_runner/camera_motion.py` (new)
- Acceptance criteria: `MotionTrack` holds dx, dy, scale, quality, event_flags arrays;
  `MotionEstimator` has `estimate(reader, config) -> MotionTrack` interface
- Verification: `source source_me.sh && python -m pytest tests/ -k camera_motion`
- Dependencies: none

**WP-1.2: Implement FixedZoomEstimator with phase correlation**
- Owner: coder
- Touch points: `track_runner/camera_motion.py`
- Acceptance criteria: `cv2.phaseCorrelate()` with Hann window on consecutive
  grayscale frame pairs; scale[t] = 1.0 for all t; quality from response value
- Verification: `source source_me.sh && python -m pytest tests/ -k fixed_zoom`
- Dependencies: WP-1.1

**WP-1.3: Add NPZ caching with video identity validation**
- Owner: coder
- Touch points: `track_runner/camera_motion.py`
- Acceptance criteria: cached MotionTrack loads in <0.1s; cache invalidates when
  video identity hash changes (uses existing `tr_video_identity.py`)
- Verification: run estimator twice, verify second run loads from cache
- Dependencies: WP-1.2

**WP-1.4: Add 3-frame median filter on motion estimates**
- Owner: coder
- Touch points: `track_runner/camera_motion.py`
- Acceptance criteria: motion blur and outlier frames produce smooth dx/dy after
  median filter; filter is applied before caching
- Verification: inject synthetic outlier frames, verify filtered output is smooth
- Dependencies: WP-1.2

**WP-1.5: Build SceneTransform class**
- Owner: coder
- Touch points: `track_runner/scene_coords.py` (new)
- Acceptance criteria: cumulative motion arrays; `pixel_to_scene()`,
  `scene_to_pixel()`, `pixel_box_to_scene()` methods; accepts scale arrays
  (even if all ones initially); zoom jump step discontinuities handled
- Verification: `source source_me.sh && python -m pytest tests/ -k scene_coords`
- Dependencies: WP-1.1

**WP-1.6: Add camera section to config YAML**
- Owner: coder
- Touch points: `track_runner/tr_config.py`, `track_runner/track_runner.config.yaml`
- Acceptance criteria: `camera.zoom_type` (fixed/iphone_discrete/continuous),
  `camera.zoom_levels`, `camera.camera_height`, `camera.camera_position`,
  `camera.track_size` validated and defaulted. M1 uses zoom_type and zoom_levels
  as known-failure labels (stamp the video source type, route benchmark
  expectations, warn when the selected estimator is not yet implemented). M2
  turns them into estimator behavior. camera_height, camera_position, and
  track_size are stored for later use only.
- Verification: `source source_me.sh && python -m pytest tests/ -k config`
- Dependencies: none

**WP-1.7: Write M1 unit and smoke tests**
- Owner: tester
- Touch points: `tests/test_camera_motion.py` (new), `tests/test_scene_coords.py` (new)
- Acceptance criteria: phase correlation produces consistent dx on panning video;
  scene-transform round-trips within 0.5px; caching works
- Verification: `source source_me.sh && python -m pytest tests/test_camera_motion.py tests/test_scene_coords.py`
- Dependencies: WP-1.2, WP-1.5

**WP-1.8: Run pyflakes lint on new modules**
- Owner: maintainer
- Touch points: `tests/test_pyflakes_code_lint.py`
- Acceptance criteria: all new .py files pass pyflakes
- Verification: `source source_me.sh && python -m pytest tests/test_pyflakes_code_lint.py`
- Dependencies: WP-1.7

---

### M2: Discrete and continuous zoom estimators

**Depends on:** M1 (motion-api and scene-transform must exist)
**Entry criteria:** M1 exit criteria met
**Exit criteria:** `DiscreteZoomEstimator` detects iPhone zoom jumps via
sliding-window on synthetic test sequences; `ContinuousZoomEstimator` produces
per-frame scale from log-polar correlation; all estimators produce valid
`MotionTrack` objects.

**Goal:** Complete the modular estimator family. Camera motion estimation is
source-adaptive: all modes emit common `MotionTrack`, but fixed-zoom,
discrete-zoom, and continuous-zoom footage use different estimators. The modular
`MotionEstimator` interface makes it easy to swap or add estimators (e.g., a
homography-based one) without touching the solver, scene transform, or velocity
model.

#### Workstream breakdown

**Workstream: discrete-estimator**
- Goal: Sliding-window zoom jump detection with piecewise-constant scale segments
- Owner: coder
- Work packages: 3
- Interfaces: needs motion-api from M1; provides discrete zoom handling
- Expected patches: 3

**Workstream: continuous-estimator**
- Goal: Log-polar transform + phase correlation for per-frame scale
- Owner: coder
- Work packages: 2
- Interfaces: needs motion-api from M1
- Expected patches: 2

**Workstream: M2-tests**
- Owner: tester
- Work packages: 2
- Expected patches: 1-2

#### Work packages

**WP-2.1: Implement DiscreteZoomEstimator with sliding-window detection**
- Owner: coder
- Touch points: `track_runner/camera_motion.py`
- Acceptance criteria: 5-frame sliding window, not single-frame threshold; detects
  zoom transitions that unfold over 2-5 frames at 60fps; snaps detected ratios to
  known zoom levels from config; piecewise-constant scale segments with short
  transition windows
- Verification: `source source_me.sh && python -m pytest tests/ -k discrete_zoom`
- Dependencies: WP-1.1, WP-1.2

**WP-2.2: Add zoom level snapping from config**
- Owner: coder
- Touch points: `track_runner/camera_motion.py`
- Acceptance criteria: when `config.camera.zoom_levels = [1, 2, 5]`, detected
  ratios snap to nearest known level; event_flags marks zoom_jump frames
- Verification: synthetic test with known zoom ratios
- Dependencies: WP-2.1, WP-1.6

**WP-2.3: Handle piecewise scale in SceneTransform**
- Owner: coder
- Touch points: `track_runner/scene_coords.py`
- Acceptance criteria: cumulative scale has step discontinuities at zoom jumps;
  pixel_to_scene/scene_to_pixel remain continuous across jumps
- Verification: round-trip test across a synthetic zoom jump
- Dependencies: WP-1.5, WP-2.1

**WP-2.4: Implement ContinuousZoomEstimator**
- Owner: coder
- Touch points: `track_runner/camera_motion.py`
- Acceptance criteria: log-polar transform + phase correlation per frame; quality
  gating rejects low-confidence scale estimates (fall back to scale=1.0); stronger
  quality threshold than fixed-zoom
- Verification: `source source_me.sh && python -m pytest tests/ -k continuous_zoom`
- Dependencies: WP-1.1, WP-1.2

**WP-2.5: Write M2 tests**
- Owner: tester
- Touch points: `tests/test_camera_motion.py`
- Acceptance criteria: discrete zoom detection on synthetic sequences; continuous
  zoom on gradual-scale sequences; all estimators produce valid MotionTrack
- Verification: `source source_me.sh && python -m pytest tests/test_camera_motion.py`
- Dependencies: WP-2.1, WP-2.4

---

### M3: Velocity model with directional FWD/BWD independence

**Depends on:** M1 (scene-transform required for coordinate conversion)
**Entry criteria:** M1 exit criteria met
**Exit criteria:** FWD and BWD Hermite curves produce measurably different
mid-interval positions on test data; seed round-trip error < 0.1px; stationary
lock activates on synthetic stationary sequences.

**Goal:** Build the velocity-model component using cubic Hermite interpolation
with directional boundary velocities, preserving real first-pass FWD/BWD
independence.

**Key design decision:** For each interval [seed_i, seed_{i+1}]:
- Positions hard-anchored at both bracketing seeds (in scene coordinates)
- FWD endpoint slopes from backward-looking local regression (2-4 seeds left of seed_i)
- BWD endpoint slopes from forward-looking local regression (2-4 seeds right of seed_{i+1})
- Sparse fallback: one-sided finite differences or linear velocity
- Both curves pass through the same endpoints but enter with different slopes
- Mid-interval disagreement is meaningful (directionally asymmetric first-pass models)
- Dice coefficient on FWD/BWD boxes measures this real asymmetry
- Note: FWD/BWD share endpoint positions, so they are directionally asymmetric
  and diagnostically useful, not fully informationally independent

#### Workstream breakdown

**Workstream: directional-hermite**
- Goal: Cubic Hermite fitting with directional boundary slopes
- Owner: coder
- Work packages: 4
- Interfaces: needs scene-transform from M1; provides interpolation for propagation
- Expected patches: 4

**Workstream: propagation**
- Goal: FWD/BWD propagation functions using Hermite model
- Owner: coder
- Work packages: 2
- Interfaces: needs directional-hermite; provides propagation for interval-solver
- Expected patches: 2

**Workstream: M3-tests**
- Owner: tester
- Work packages: 2
- Expected patches: 2

#### Work packages

**WP-3.1: Implement directional slope estimation from seed neighborhoods**
- Owner: coder
- Touch points: `track_runner/velocity_model.py` (new)
- Acceptance criteria: `estimate_directional_slope(seeds, anchor_idx, direction,
  scene_transform)` uses 2-4 nearest seeds in specified direction; falls back to
  finite differences when sparse; returns (dx/dt, dy/dt) slope at anchor
- Verification: `source source_me.sh && python -m pytest tests/ -k directional_slope`
- Dependencies: WP-1.5

**WP-3.2: Implement cubic Hermite interpolation for position**
- Owner: coder
- Touch points: `track_runner/velocity_model.py`
- Acceptance criteria: `fit_directional_hermite(left_seed, right_seed,
  left_slope, right_slope, scene_transform)` returns callable interpolator;
  hard-anchored at endpoints; smooth between them
- Verification: seed round-trip error < 0.1px
- Dependencies: WP-3.1

**WP-3.3: Implement PCHIP log-space interpolation for box size**
- Owner: coder
- Touch points: `track_runner/velocity_model.py`
- Acceptance criteria: w/h interpolated using PCHIP in log-space (reuses pattern
  from existing `_build_local_fit()` in `interval_solver.py`); same directional
  slope estimation for size
- Verification: monotonic size change between seeds with monotonic true sizes
- Dependencies: WP-3.1

**WP-3.4: Add stationary lock**
- Owner: coder
- Touch points: `track_runner/velocity_model.py`
- Acceptance criteria: when consecutive seeds show <3% displacement in scene
  coordinates relative to box dimension, hold position constant; reuses existing
  5-frame stationary threshold concept; prevents spline fitting through noise
  during pre-race stationary phase
- Verification: synthetic stationary sequence produces zero-displacement output
- Dependencies: WP-3.2

**WP-3.5: Implement propagate_forward and propagate_backward**
- Owner: coder
- Touch points: `track_runner/velocity_model.py`
- Acceptance criteria: `propagate_forward(interval, scene_transform)` returns
  per-frame tracking state dicts; confidence decays 0.97/frame from start seed
  (floor 0.1); uses FWD Hermite curve; `propagate_backward` same from opposite
  end using BWD Hermite curve
- Verification: FWD and BWD produce different mid-interval positions
- Dependencies: WP-3.2, WP-3.3

**WP-3.6: Rewrite propagator.py as thin velocity-model wrapper**
- Owner: coder
- Touch points: `track_runner/propagator.py`
- Acceptance criteria: `propagate_forward()` and `propagate_backward()` delegate
  to velocity-model; no optical flow, no patch correlation, no feature detection;
  same output format (list of tracking state dicts) for backward compatibility
  with interval_solver
- Verification: existing interval_solver can call new propagator without changes
- Dependencies: WP-3.5

**WP-3.7: Write M3 unit tests**
- Owner: tester
- Touch points: `tests/test_velocity_model.py` (new)
- Acceptance criteria: FWD/BWD asymmetry verified; seed round-trip < 0.1px;
  stationary lock activates; sparse fallback works
- Verification: `source source_me.sh && python -m pytest tests/test_velocity_model.py`
- Dependencies: WP-3.5, WP-3.6

**WP-3.8: Run pyflakes on velocity-model**
- Owner: maintainer
- Touch points: `tests/test_pyflakes_code_lint.py`
- Acceptance criteria: all new .py files pass pyflakes
- Verification: `source source_me.sh && python -m pytest tests/test_pyflakes_code_lint.py`
- Dependencies: WP-3.7

---

### M4: Solver integration

**Depends on:** M1 (motion-estimator, scene-transform), M3 (velocity-model,
propagator). M2 is NOT required -- M4 works with FixedZoomEstimator; discrete
and continuous zoom can land later.
**Entry criteria:** M1 and M3 exit criteria met
**Exit criteria:** Full solve + encode on IMG_3702 produces output with
convergence error < 20px median; FWD/BWD Dice scores show real variation (not
uniformly high); pyflakes passes on all modified files.

**Goal:** Wire the new analytical components into the interval solver, update
scoring, implement the new occlusion policy, and validate on all 7 test videos.

#### Workstream breakdown

**Workstream: solver-rewrite**
- Goal: Update interval_solver to use velocity-model via new propagator
- Owner: coder
- Work packages: 3
- Interfaces: needs velocity-model, scene-transform; provides solved trajectories
- Expected patches: 3

**Workstream: scoring-update**
- Goal: Replace competitor margin with velocity consistency
- Owner: coder
- Work packages: 2
- Interfaces: needs solver-rewrite; provides updated scoring for review/target UI
- Expected patches: 2

**Workstream: occlusion-policy**
- Goal: Implement new interpolation-through-occlusion policy
- Owner: coder
- Work packages: 2
- Interfaces: modifies trajectory erasure in interval_solver
- Expected patches: 2

**Workstream: M4-tests**
- Owner: tester
- Work packages: 3
- Expected patches: 2-3

#### Work packages

**WP-4.1: Update solve_interval to accept SceneTransform**
- Owner: coder
- Touch points: `track_runner/interval_solver.py`
- Acceptance criteria: `solve_interval()` receives `SceneTransform` and uses
  new propagator; no YOLO detector call in solve path; keeps `fuse_tracks()`,
  `stitch_trajectories()`, parallel solve shell
- Verification: solve completes without error on one test video
- Dependencies: WP-3.6

**WP-4.2: Pre-compute camera motion in solve pipeline**
- Owner: coder
- Touch points: `track_runner/cli.py`, `track_runner/interval_solver.py`
- Acceptance criteria: `solve_all_intervals()` pre-computes camera motion once,
  passes `SceneTransform` to each interval solve; selects estimator from
  `config.camera.zoom_type`
- Verification: NPZ cache created on first run; second run loads from cache
- Dependencies: WP-4.1, WP-1.3

**WP-4.3: Remove YOLO from solve path**
- Owner: coder
- Touch points: `track_runner/interval_solver.py`, `track_runner/cli.py`
- Acceptance criteria: YOLO detection no longer called during solve; detection
  module kept for future subordinate roles but not imported in solve path
- Verification: solve runs without YOLO model downloaded
- Dependencies: WP-4.1

**WP-4.4: Replace competitor margin with velocity consistency**
- Owner: coder
- Touch points: `track_runner/scoring.py`
- Acceptance criteria: keep `_compute_dice_coefficient()`, `compute_agreement()`,
  `score_interval()`; replace competitor margin with velocity consistency
  (leave-one-out prediction error on directional support seeds -- for each
  support seed, fit the Hermite curve without it and measure prediction error
  at the left-out seed); update `classify_confidence()` to use velocity
  consistency and size consistency instead of competitor margin
- Verification: scoring produces non-uniform confidence across intervals
- Dependencies: WP-4.1

**WP-4.5: Update hypothesis.py**
- Owner: coder
- Touch points: `track_runner/hypothesis.py`
- Acceptance criteria: remove YOLO-based competitor tracking; stub module for
  future visual validation warnings (subordinate role 3); no competitor paths
  generated during solve
- Verification: solve completes without hypothesis module active
- Dependencies: WP-4.3

**WP-4.6: Implement new occlusion policy**
- Owner: coder
- Touch points: `track_runner/interval_solver.py`
- Acceptance criteria: approximate seeds no longer trigger trajectory erasure
  (interpolation through occlusion when bracketed by seeds); `not_in_frame`
  seeds still erase (runner off-screen); update `_apply_trajectory_erasure()`
- Verification: interval with approximate seed mid-interval has continuous
  trajectory through the seed frame
- Dependencies: WP-4.1

**WP-4.7: Update confidence classification for new signals**
- Owner: coder
- Touch points: `track_runner/scoring.py`, `track_runner/review.py`
- Acceptance criteria: confidence grid uses agreement + velocity consistency
  instead of agreement + competitor margin; severity classification updated;
  seed recommendation logic updated
- Verification: review output on test video produces reasonable seed suggestions
- Dependencies: WP-4.4

**WP-4.8: Migrate state_io.py to diagnostics v3 schema**
- Owner: coder
- Touch points: `track_runner/state_io.py`
- Acceptance criteria: persist all `interval_score_v2` fields; bump diagnostics
  header to `track_runner_diagnostics: 3`; read v2 diagnostics without error
  (map old score keys to legacy display); seeds schema unchanged
- Verification: write v3 diagnostics, read back, verify all fields; read old v2 file
- Dependencies: WP-4.4

**WP-4.9: Migrate encode_analysis.py to new metrics**
- Owner: coder
- Touch points: `track_runner/encode_analysis.py`
- Acceptance criteria: report velocity_consistency, size_consistency,
  motion_quality, occlusion_fraction instead of identity/competitor stats;
  remove references to identity_score and competitor_margin
- Verification: run analyze on solved video, verify report has new metrics
- Dependencies: WP-4.4, WP-4.8

**WP-4.10: Smoke test on all 7 test videos**
- Owner: tester
- Touch points: `tests/test_solver_smoke.py` (new or existing)
- Acceptance criteria: full solve completes on all 7 videos without error;
  convergence error on IMG_3702 < 20px median; FWD/BWD Dice scores show real
  variation; multi-regime benchmark gates pass (see regression gate table)
- Verification: `source source_me.sh && python -m pytest tests/test_solver_smoke.py`
- Dependencies: WP-4.6, WP-4.7, WP-4.8, WP-4.9

**WP-4.11: Encode and visual review**
- Owner: tester
- Touch points: encode pipeline (no code changes, just validation)
- Acceptance criteria: encode on IMG_3702 produces watchable output; no seasick
  sensation; dolly-cam feel subjectively improved
- Verification: `source source_me.sh && python track_runner/track_runner.py -i IMG_3702.mkv encode`
- Dependencies: WP-4.10

**WP-4.12: Run pyflakes on all modified files**
- Owner: maintainer
- Touch points: `tests/test_pyflakes_code_lint.py`
- Acceptance criteria: all modified .py files pass pyflakes
- Verification: `source source_me.sh && python -m pytest tests/test_pyflakes_code_lint.py`
- Dependencies: WP-4.11

---

### M5: Setup mode and cleanup

**Depends on:** M1 (config camera section must exist). Can run in parallel with
M3 and M4 for the setup questionnaire work. Cleanup work packages depend on M4.
**Entry criteria:** M1 exit criteria met (for setup); M4 exit criteria met (for cleanup)
**Exit criteria:** `setup` subcommand works end-to-end; `solver_backend` config
key functional; changelog updated; pyflakes clean.

**Goal:** Add the setup questionnaire CLI subcommand and perform final cleanup,
documentation, and lint.

#### Workstream breakdown

**Workstream: setup-cli**
- Goal: Interactive CLI questionnaire for per-video camera config
- Owner: coder
- Work packages: 3
- Expected patches: 3

**Workstream: cleanup-docs**
- Goal: Remove dead code, update changelog, documentation
- Owner: planner (docs) + maintainer (lint)
- Work packages: 3
- Expected patches: 2-3

#### Work packages

**WP-5.1: Create setup_mode.py with questionnaire flow**
- Owner: coder
- Touch points: `track_runner/setup_mode.py` (new)
- Acceptance criteria: interactive CLI questions for (1) zoom type (iPhone
  discrete/fixed/continuous), (2) zoom levels if iPhone, (3) camera height
  (elevated/track level), (4) camera position (center-short/side-long),
  (5) track size (160/200/400m); writes to config YAML camera section.
  zoom_type and zoom_levels are active in current solver; camera_height,
  camera_position, and track_size are stored for later use only (do not affect
  current solver behavior)
- Verification: `source source_me.sh && python track_runner/track_runner.py -i test.mkv setup`
- Dependencies: WP-1.6

**WP-5.2: Add setup subparser to CLI**
- Owner: coder
- Touch points: `track_runner/cli_args.py`, `track_runner/cli.py`
- Acceptance criteria: `setup` subcommand registered; `_mode_setup()` handler
  calls `setup_mode.py`; runs before seed/solve/encode in `run` mode if no
  camera config exists
- Verification: `track_runner.py -i VIDEO setup` completes successfully
- Dependencies: WP-5.1

**WP-5.3: Add estimator selection from config**
- Owner: coder
- Touch points: `track_runner/cli.py`
- Acceptance criteria: `config.camera.zoom_type` selects
  FixedZoom/DiscreteZoom/ContinuousZoom estimator automatically; default to
  FixedZoom when no camera config
- Verification: config with `zoom_type: iphone_discrete` uses DiscreteZoomEstimator
- Dependencies: WP-5.1, WP-4.2

**WP-5.4: Add solver_backend config key for rollback**
- Owner: coder
- Touch points: `track_runner/tr_config.py`, `track_runner/cli.py`,
  `track_runner/track_runner.config.yaml`
- Acceptance criteria: `solver_backend: scene_interp` (default) uses new solver;
  `solver_backend: legacy_interval` uses old optical-flow propagator; per-video
  config override works
- Verification: set `solver_backend: legacy_interval` in config, verify old solver runs
- Dependencies: WP-4.12

**WP-5.5: Update docs/CHANGELOG.md**
- Owner: planner
- Touch points: `docs/CHANGELOG.md`
- Acceptance criteria: all milestones documented with philosophy changes noted
- Verification: visual review
- Dependencies: WP-4.12

**WP-5.6: Remove dead imports and unused YOLO references from solve path**
- Owner: maintainer
- Touch points: all modified .py files
- Acceptance criteria: no unused imports; pyflakes clean
- Verification: `source source_me.sh && python -m pytest tests/test_pyflakes_code_lint.py`
- Dependencies: WP-4.12

**WP-5.7: Run full regression on all 7 test videos**
- Owner: tester
- Touch points: none (validation only)
- Acceptance criteria: solve + encode on all 7 videos; no regressions from M4
  smoke test results
- Verification: `source source_me.sh && python -m pytest tests/ -x`
- Dependencies: WP-5.5

---

## Post-rewrite diagnostics contract

The current `interval_score` dict contains `identity_score` and
`competitor_margin`. These are removed. The new contract must be defined
before implementation begins because diagnostics are a shared interface,
not a local solver detail.

**New interval_score_v2 schema:**

```yaml
interval_score_v2:
  agreement: float           # Dice coefficient FWD/BWD (unchanged meaning)
  velocity_consistency: float # Leave-one-out prediction error on support seeds
  size_consistency: float     # Box-size interpolation residual (headline metric)
  motion_quality: float       # Camera motion estimation confidence in this interval
  occlusion_fraction: float   # Fraction of interval frames in approximate spans
  confidence_tier: high|good|fair|low
  severity: high|medium|low
  failure_reasons: []         # Primary scoring reasons (see below)
  warning_flags: []           # Advisory flags (do not affect confidence tier)
```

**Metric definitions:**
- `velocity_consistency`: leave-one-out prediction error. For each support seed
  in the directional neighborhood, fit the Hermite curve without that seed and
  measure the prediction error at the left-out seed's position. Average over
  support seeds. Low error = smooth consistent motion; high error = seeds
  disagree about velocity. This is operational because support seeds are distinct
  from the hard-anchored interval endpoints.
- `size_consistency`: same leave-one-out approach on box-size (w, h) PCHIP fit.
  High residual flags abrupt or erratic scale changes.
- `motion_quality`: mean phase-correlation response value over the interval's
  frames. Low quality indicates texture-poor or motion-blurred frames where
  camera motion estimation is unreliable.
- `occlusion_fraction`: fraction of interval frames inside approximate-supported
  hidden spans. A hidden span is bounded by: start = the approximate seed frame,
  end = the next visible or partial seed frame (or interval endpoint). Frames
  within hidden spans are interpolated, not erased. The fraction is
  hidden_frame_count / total_interval_frames.

**Primary failure reasons (drive confidence and seed recommendation):**
- `low_agreement` -- Dice < 0.2 (unchanged threshold and meaning)
- `weak_motion_model` -- velocity_consistency < 0.5 (leave-one-out error high)
- `long_occlusion` -- occlusion_fraction > 0.3 (>30% of interval is hidden span)
- `low_motion_quality` -- motion_quality < 0.5 (phase correlation response low)
- `sparse_support` -- fewer than 2 directional support seeds available for slope
  estimation on either side; triggers finite-difference fallback

**Confidence classification rules (deterministic):**

```
if agreement > 0.5 and velocity_consistency > 0.5 and size_consistency > 0.5:
    confidence_tier = "high"
elif agreement > 0.5 and velocity_consistency > 0.3:
    confidence_tier = "good"
elif agreement > 0.2 and velocity_consistency > 0.2:
    confidence_tier = "fair"
else:
    confidence_tier = "low"

# Modifiers (same pattern as current system):
# Short intervals (<= 5 frames): promote one tier (never to high)
# Long intervals (> 10s): demote one tier
# Low motion quality (< 0.5): demote one tier
# High occlusion fraction (> 0.3): cap at "fair"
```

**Severity classification (derived from confidence_tier + failure_reasons):**
- high: confidence_tier == "low", or any primary failure reason present
- medium: confidence_tier == "fair"
- low: confidence_tier in ("good", "high")
- Short-interval demotion: intervals < 10 frames demote from high to medium

**Warning flags (advisory, do not change solve):**
- `approximate_span` -- interval contains approximate seed(s)
- `no_directional_support` -- no neighboring seeds for slope estimation on one side
- `scale_unstable` -- box-size interpolation has high residual
- `visual_conflict` -- optional post-solve check found disagreement (deferred to future)

**Explicitly removed (YOLO-dependent):**
- `identity_score` removed
- `competitor_margin` removed
- `likely_identity_swap`, `low_separation`, `weak_appearance` removed as primary reasons
- Optional warning-only visual checks may emit `visual_conflict` later, not in
  initial milestones

**Consumers that must be updated:**

| Consumer | File:line | Current dependency | Required change |
| --- | --- | --- | --- |
| `classify_confidence()` | `scoring.py:144` | `identity_score`, `competitor_margin` | Use `velocity_consistency`, `size_consistency` |
| `identify_weak_spans()` | `review.py:14` | `likely_identity_swap`, `low_separation` | Use only primary failure reasons (`low_agreement`, `weak_motion_model`, `long_occlusion`, `sparse_support`); do not depend on deferred `visual_conflict` |
| Encode analysis report | `encode_analysis.py:760` | Identity/competitor stats | Report velocity/size consistency stats |
| Diagnostics persistence | `state_io.py:210` | Current score keys | Persist new score fields |

**Warning-only visual separation check (subordinate role 3, deferred):**
As partial replacement for lost competitor-margin value, a future milestone may
add a lightweight post-solve warning when the analytical path passes near other
detected persons or when scene-coordinate velocity deviates from prediction.
This would emit `visual_conflict` as a warning flag. **Not implemented in M1-M5.**
review.py uses only the primary failure reasons (`low_agreement`,
`weak_motion_model`, `long_occlusion`, `sparse_support`) for seed suggestions
in the initial landing. `visual_conflict` is reserved in the schema but not
populated or consumed until a future milestone.

## Occlusion policy migration

The erasure policy change is a first-class migration with one canonical rule.

**Canonical rule (new):** Scene interpolation carries geometry through hidden
spans. Approximate seeds lower confidence but do not erase trajectory.
`not_in_frame` seeds erase trajectory (runner off-screen). Confidence and review
signals are explicitly lower in interpolated hidden regions.

**Centralized policy owner:** `_apply_trajectory_erasure()` in
`interval_solver.py` (line ~913). This single function governs solve, analyze,
and encode paths.

**Callers that must switch to new policy:**

| Caller | File:line | Current behavior | Required change |
| --- | --- | --- | --- |
| `_apply_trajectory_erasure()` | `interval_solver.py:913` | Erases near approximate seeds (0.5s radius) | Remove approximate erasure; keep not_in_frame erasure |
| Analyze reconstruction | `cli.py:1083` | Calls shared erasure | Automatically uses new policy (shared function) |
| Encode reconstruction | `encoder.py` (via cli) | Calls shared erasure | Automatically uses new policy |
| Confidence stamping | `interval_solver.py` | Approximate seeds get conf=0.3 | Keep conf=0.3 for approximate (lower but not erased) |

**Scope:** This policy change affects solving, rendered output, and analysis
reports. Approximate seeds produce interpolated positions at lower confidence
in all three paths.

## Persistence and compatibility

**Persistence hierarchy:**
- **Seeds JSON:** canonical user work. Preserve, load, migrate forward on read.
  Never treat as disposable cache. Never require users to re-annotate old videos.
- **Intervals JSON:** derived geometry (trajectory only, no scoring). Disposable,
  safe to delete and rebuild by re-solve.
- **Diagnostics JSON:** derived scoring. Disposable, safe to delete and rebuild.
  Schema changes from v2 to v3.
- **Motion cache NPZ:** derived cache. Disposable, safe to delete and rebuild.

**What changed:**

**Motion cache (new):**
- Location: alongside existing per-video files, `{video}.camera_motion.npz`
- Managed by `tr_paths.py` (add new path function)
- Keyed by: video identity hash + estimator type + config fingerprint
  (changing zoom_type or zoom_levels invalidates cache)
- Invalidated by video identity hash change (existing `tr_video_identity.py`)
- Not versioned in git (generated artifact)

**Diagnostics JSON schema change:**
- Header version bumps: `track_runner_diagnostics: 3` (from 2)
- New fields: all `interval_score_v2` fields
- Removed fields: `identity_score`, `competitor_margin`, `identity_swap` reasons
- Backward compatibility: old v2 diagnostics files are still readable for display
  but old failure reasons are mapped to legacy display only, not mixed with new
  score semantics; re-analysis regenerates derived summaries using v2 schema
- Old v2 diagnostics do NOT trigger automatic re-solve (user must explicitly re-solve)

**Intervals JSON:** Contains fused FWD/BWD trajectory geometry plus per-interval
scores. The schema changes because `interval_score_v2` replaces old score fields.
Disposable -- may be deleted and regenerated by re-solve. No user work stored.
Old intervals files trigger re-solve when score fields do not match v2 schema.

**Seeds JSON:** **Fully preserved, no migration needed.** This contains user work
(hand-drawn torso boxes). Format unchanged, all existing seeds are valid. The new
solver reads the same seed fields (frame, cx, cy, w, h, status, torso_box).

**Diagnostics JSON:** May be deleted and regenerated by re-solve. No user work stored.
Old v2 diagnostics are readable for display but will be regenerated with v3 schema
on next solve.

**Config YAML:** Additive change only (new `camera` section). Old configs without
`camera` section default to `zoom_type: fixed`.

## Acceptance criteria and gates

### Unit/verification gate (per-milestone)
- All new modules pass pyflakes
- All unit tests pass
- No regressions in existing tests

### Integration gate (M4)
- Full solve pipeline works end-to-end on at least 3 test videos
- Camera motion estimation + scene transform + velocity model + interval solver
  produce valid trajectories

### Regression gate (M4 + M5)
- All 7 test videos solve and encode without error
- FWD/BWD Dice scores show real variation (not uniformly high)
- Box-size accuracy: median box height error < 10% across all videos (headline metric)

**Multi-regime benchmark gates (numeric, from v3 findings baselines):**

Primary cross-video gates (resolution- and zoom-safe):

| Metric | Scope | Pass threshold |
| --- | --- | --- |
| Normalized center error (median center error / box height) | All 7 videos | < 0.25 |
| Box height error median | All 7 videos | < 10% |

Per-regime gates:

| Regime | Video | Metric | Current baseline | Pass threshold |
| --- | --- | --- | --- | --- |
| Key failure | IMG_3702 | Center error median (px, local benchmark) | 99.5px | < 20px |
| Sparse long-gap | IMG_3629 | Low-confidence fraction | 11.1% | <= 15% |
| Sparse long-gap | IMG_3629 | Normalized center error | n/a | < 0.25 |
| Dense refined | IMG_3830 | Agreement score median | 0.80 | >= 0.75 |
| Dense refined | IMG_3830 | Normalized center error | n/a | < 0.10 |
| Telephoto crowded | canon_60d | Normalized center error | n/a | < 0.25 |
| Telephoto crowded | canon_60d | High-confidence share | 38.6% (legacy) | >= 40% (see note) |
| Telephoto crowded | canon_60d | Fair+low interval fraction | n/a | <= 30% |

**Telephoto gate rationale:** The archived telephoto difficulty was dominated by
weak appearance and low detector separation under the old scoring stack, not
geometric disagreement. If most non-target people are stationary while the runner
is the dominant moving subject, the motion-centered rewrite may improve this
regime despite poor legacy identity scores. The high-confidence gate is set near
the legacy baseline (38.6%) because the new scoring system measures different
things (velocity consistency, not identity). This should be validated explicitly
in the telephoto benchmark -- any miss here is serious even if center error is
fine.
| Zoom variation | IMG_3823 | height_jerk_p95 | 69.0 px/frame | < 69 px/frame |

### Release gate (M5)
- Setup mode works end-to-end
- Changelog updated
- Pyflakes clean on entire repo

## Test and verification strategy

| Level | Scope | Gate |
| --- | --- | --- |
| Unit | Per-function tests in `tests/test_camera_motion.py`, `tests/test_scene_coords.py`, `tests/test_velocity_model.py` | Blocks next work package |
| Smoke | Full solve on one video per milestone | Blocks milestone exit |
| Integration | Solve + encode on IMG_3702 | Blocks M4 exit |
| Regression | Solve + encode on all 7 videos | Blocks M5 exit |
| Lint | `tests/test_pyflakes_code_lint.py` | Blocks every milestone exit |

## Migration and compatibility policy

- **Additive rollout:** New modules added alongside existing code. Old propagator,
  hypothesis, and detection modules remain in the repo but are no longer called
  from the solve path.
- **Backward compatibility:** Seeds JSON format unchanged. Intervals JSON schema
  changes (interval_score_v2 replaces old score fields); old intervals trigger
  re-solve. Diagnostics JSON bumps to v3. Config YAML gains new `camera` and
  `solver_backend` sections (additive).
- **Legacy deletion criteria:** Old optical-flow propagator, YOLO-based hypothesis,
  and detection imports in solve path may be removed after M5 regression gate passes
  and at least 2 weeks of user testing. Until then, old code stays in version
  control.
- **Rollback:** Add explicit `solver_backend` config key with values
  `legacy_interval` (old optical-flow solver) and `scene_interp` (new analytical
  solver). Default to `scene_interp` after M4 exits. Dispatch lives in `cli.py`
  before `interval_solver` is entered: when `legacy_interval` is set, the old
  propagator, hypothesis, and scoring modules are used unchanged. The legacy
  solve path remains runnable through M5 and beyond. If the new solver regresses
  on any test video, switch that video's config to `legacy_interval`. The old
  propagator code stays in the repo until the legacy path is proven unnecessary.

## Risk register

| Risk | Impact | Trigger | Owner | Mitigation |
| --- | --- | --- | --- | --- |
| Phase correlation fails on low-texture frames | Camera motion estimates degrade | Sky or empty track fills most of frame | coder | Check quality from `cv2.phaseCorrelate()` response; fall back to zero motion |
| Cumulative drift over long seed gaps | Scene coordinates drift >100px | Seed gaps >20s (observed in sparse data) | coder | Hermite is anchored at both interval endpoints; drift bounded within interval |
| Translation + isotropic scale insufficient | Residual tilt/rotation/parallax | Handheld footage with significant rotation | architect | Treat as first-implementation boundary; extend model if residuals too large |
| FWD/BWD Dice uniformly high | Diagnostic signal weakened | Directional slopes too similar | tester | Verify FWD/BWD asymmetry in M3 tests; adjust slope window if needed |
| Smooth but wrong trajectories | Runner identity swapped | Crowded scenes, user seeded wrong runner | user | Accepted tradeoff; dense seeding through crowded sections mitigates |
| Sparse seeds degrade Hermite quality | Linear fallback in long intervals | Few seeds placed by user | coder | Finite-difference fallback documented; still better than optical flow over 20s |

## Patch plan and reporting format

- Patch 1: motion-estimator MotionTrack API and FixedZoomEstimator
- Patch 2: motion-estimator NPZ caching and median filter
- Patch 3: scene-transform SceneTransform class
- Patch 4: config camera section support
- Patch 5: tests M1 unit and smoke tests
- Patch 6: motion-estimator DiscreteZoomEstimator
- Patch 7: motion-estimator ContinuousZoomEstimator
- Patch 8: scene-transform piecewise scale handling
- Patch 9: tests M2 zoom estimator tests
- Patch 10: velocity-model directional slope estimation
- Patch 11: velocity-model Hermite interpolation + PCHIP size
- Patch 12: velocity-model stationary lock
- Patch 13: velocity-model FWD/BWD propagation functions
- Patch 14: propagator rewrite as velocity-model wrapper
- Patch 15: tests M3 velocity model tests
- Patch 16: interval-solver integration with SceneTransform
- Patch 17: interval-solver YOLO removal from solve path
- Patch 18: scoring velocity consistency metric
- Patch 19: interval-solver occlusion policy change
- Patch 20: state_io diagnostics v3 schema migration
- Patch 21: encode_analysis new metrics reporting
- Patch 22: tests M4 smoke test on 7 videos (multi-regime gates)
- Patch 23: setup-questionnaire CLI subcommand
- Patch 24: cli setup subparser and estimator selection
- Patch 25: config solver_backend rollback key
- Patch 26: docs changelog and cleanup
- Patch 27: tests M5 full regression

## Rollout and release checklist

- [ ] All M1-M5 exit criteria met
- [ ] All 7 test videos solve and encode without error
- [ ] IMG_3702 convergence error < 20px median
- [ ] FWD/BWD Dice shows real variation
- [ ] Setup mode works for all 3 zoom types
- [ ] Pyflakes clean
- [ ] Changelog updated
- [ ] Old code retained in version control (not deleted)

## Documentation close-out

- [ ] `docs/CHANGELOG.md` updated with all milestones
- [ ] Philosophy changes documented (visual tracking to scene interpolation,
      erasure to interpolation, detection-first to seed-trust)
- [ ] `docs/archive/` receives old plan docs as needed
- [ ] `docs/CODE_ARCHITECTURE.md` updated for new component map

## Open questions and decisions needed

All major design questions are resolved.

**Resolved decisions:**
- **Metric framing:** Primary gates use normalized center error (% of box height),
  not raw pixels. IMG_3702 < 20px is a secondary local benchmark only.
- **M2 timing:** M4 proceeds with FixedZoomEstimator; M2 does not block it.
- **Old diagnostics:** Load legacy v2 diagnostics read-only for display; require
  re-solve for new scoring semantics.
- **Setup scope:** Collects all camera fields once (zoom_type, zoom_levels,
  camera_height, camera_position, track_size). zoom_type and zoom_levels are
  active in solver; remaining fields stored for later use only.
- **Diagnostics contract:** Defined as `interval_score_v2` with explicit consumer list.
- **Occlusion policy:** First-class migration in canonical erasure owner.
- **Benchmark gates:** Multi-regime matrix with normalized metrics.
- **Seeds preservation:** Seeds JSON is canonical user work; never disposable.
- **Rollback:** Real solver_backend config key (legacy_interval vs scene_interp).
