# TODO

Backlog scratchpad. Each topic gets its own section so entries can be
claimed, refined, or closed independently.

## Solver

### Stage 2 race-start refinement (currently deactivated)

Stage 2 was the velocity-onset detector in
[track_runner/race_phases.py](../track_runner/race_phases.py)
`detect_race_start`, intended to pick the exact race-start frame
**inside** Stage 1's seed-to-seed crossing interval. As of
2026-04-24 it is disabled in production:
[track_runner/solve_queue.py](../track_runner/solve_queue.py) now uses
`race_start.pick_race_start_frame_midpoint(low, high)` =
`ceil((low + high) / 2)` instead.

Why deactivated:

- The detector requires a 45-frame trailing baseline window
  (`PRE_WINDOW_S * fps` at 60 fps). When Stage 1's crossing interval is
  shorter than that (a common case with dense pre-race seeds), the scan
  loop body in
  [track_runner/race_phases.py:170-216](../track_runner/race_phases.py)
  never executes and returns `(None, 0.0)`.
- Even when the scan runs, on ambiguous velocity profiles it returns
  None and the wrapper used to crash solve. Diagnosed via
  [tools/diagnose_pre_race.py](../tools/diagnose_pre_race.py) on
  `Hononega-Orion_600m-IMG_3702.mkv` (interval 340-357, 18 frames,
  baseline window 45 frames -> no scan).

What is preserved (do not delete):

- `race_phases.detect_race_start` -- the velocity-onset detector itself.
- `race_start.detect_race_start_in_interval` -- the wrapper.
- The diagnostic tool's `stage2_velocity.png` plot which calls the
  detector for visualization.

Redesign brief for whoever picks this up:

- Goal: pick race_start_frame to sub-seed precision inside Stage 1's
  interval. Current midpoint is correct on average but wrong by up to
  ~half the interval width on individual clips.
- Constraint: must work on intervals as short as 2-3 frames (no
  baseline window assumption). The old detector demanded 45 frames of
  trailing baseline at 60 fps to start scanning, which is absurd
  given Stage 1's intervals can be a single frame and the rest of the
  pipeline already computes a useful per-frame motion signal from a
  9-frame window.
- **Recommended basis: the motion-cue heat map** in
  [track_runner/residual_motion.py](../track_runner/residual_motion.py).
  `compute_residual_for_frame` uses `DEFAULT_HALF_WINDOW = 4` -> a
  9-frame aligned-background-subtraction window and produces a
  per-frame residual magnitude map. Run it across the Stage 1 interval
  (plus a few neighbor frames so the 9-frame window stays populated
  even when the interval itself is short) and pick the first frame
  where residual energy at the runner's expected location crosses a
  torso-relative threshold. The diagnostic in
  [tools/diagnose_pre_race.py](../tools/diagnose_pre_race.py) already
  samples this signal in its `res_e` column; use it to calibrate the
  threshold before wiring the redesign into solve.
- Per-frame scene velocity (the old Stage 2 input) and camera pan
  velocity remain available as cross-checks, but neither needs a
  multi-second baseline.
- Acceptance: a reworked detector must not crash solve under any
  Stage 1 interval length. Falling back to the midpoint when
  uncertain is acceptable.

### Verify whether `MIN_BLOB_AREA` is a C2 violation or a denoising floor

`track_runner/residual_motion.py` defines `MIN_BLOB_AREA = 25` (pixels^2)
used by `extract_frame_blobs` to drop noise specks. The constant name
and its fixed pixel unit look like a runner-relative threshold, which
would violate
[docs/TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) clause C2
("target size thresholds ... must be expressed in torso units").
However, C2 explicitly permits raw pixels for "low-level raster-kernel
sizes that are about the image, not the runner" -- and a speckle
denoising floor falls in that bucket. The correct classification
depends on evidence, not on the name of the constant.

Two possibilities:

- **Denoising floor (C2-allowed):** 25 px^2 is below the smallest
  legitimate runner blob (at `torso_h = 15` the torso blob is roughly
  50-150 px^2), so the filter only kills scattered compression /
  sensor speckle. In that case the constant is fine but poorly named
  and commented.
- **Runner-size threshold (C2 violation):** at `torso_h` roughly 10-15
  the filter cuts into real runner-component blobs. Then the fix is
  a torso-relative threshold with a raw-pixel raster floor below it.

Evidence-gathering step (cheap, ~10 minutes of work) decides which
regime applies. Do this **before** any code fix:

1. Pick an interval with a small, distant runner (for example the
   IMG_3629 clip or the Hononega-Varsity 4x400 clip; both have
   stretches at `torso_h ~ 15 px`).
2. Run `tools/check_interval_blob_funnel.py` on that interval with a
   small `--debug-print-blob-areas` addition (or instrument
   `extract_frame_blobs` ad hoc to log raw blob areas before the
   `MIN_BLOB_AREA` filter).
3. Count blobs in the 10-50 px^2 band and inspect them. Real runner
   components -> filter is biting -> C2 violation. Scattered speckles
   unrelated to the runner -> filter is denoising -> C2-allowed.

Follow-up actions depend on the evidence:

- **Denoising outcome:** no behavior change. Rename the constant (for
  example `SPECKLE_FLOOR_PX_SQ`) and rewrite the docstring to label it
  explicitly as a raster-kernel floor under C2's allowed bucket. No
  cache invalidation needed.
- **Runner-relative outcome:** implement a torso-aware threshold, for
  example:

  ```python
  def min_blob_area(torso_h: float) -> float:
  	# C2: area scales with torso_h**2 so the threshold must too
  	runner_relative = AREA_FRACTION_OF_TORSO_SQ * torso_h * torso_h
  	# raster-level speckle floor, permitted as raw pixels under C2
  	speckle_floor = MIN_BLOB_AREA_FLOOR_PX
  	return max(speckle_floor, runner_relative)
  ```

  Calibrate `AREA_FRACTION_OF_TORSO_SQ` so current behavior is
  recovered at a typical corpus torso height (avoids surprise
  regressions). This alters blob-extraction output and invalidates
  geometry caches, so per contract C9 and
  [docs/TR_SCHEMA_VERSION_HISTORY.md](TR_SCHEMA_VERSION_HISTORY.md)
  bump `SCHEMA_VERSION` and log the rationale.

Do **not** jump straight to the formula-based fix without the evidence
step. If the constant is already in the denoising regime, shipping a
formula is a behavioral change for no gain and invalidates the
geometry cache needlessly.

### Dedicated worker module for interval-job queueing

Interval-job queueing logic is currently duplicated across several
call sites. Extract it into one worker module so additions and fixes
only have to land in one place.

### Unify per-frame and per-interval scoring across the pipeline

Per-frame `conf` is currently produced and consumed in at least four
mostly-disconnected places, each with its own conventions:

- [track_runner/velocity_model.py](../track_runner/velocity_model.py)
  `blend_paths` emits `merged_conf` per blended-path frame during
  solve; it decays from FWD/BWD propagator state.
- [track_runner/interval_solver.py](../track_runner/interval_solver.py)
  `_stamp_seed_confidence` overwrites seed-frame conf with `1.0` for
  visible/partial and `0.3` for approximate.
- [track_runner/interval_solver.py](../track_runner/interval_solver.py)
  `derive_per_frame_confidence` (added 2026-04-27) reconstructs conf
  from `||fwd-bwd||/torso_h` mapped through `exp(-d/scale)` for
  analyze and encode (because the npz schema does not persist
  per-frame conf).
- [track_runner/scoring.py](../track_runner/scoring.py) computes
  per-interval agreement / `confidence_tier` from the FWD/BWD pair as
  a separate quantity that lives in `interval_scores.json`.

`anchor_to_seeds` reads `state.get("conf", 0.5)` at
`interval_solver.py:1136`; `regime_classifier._per_frame_features`
expects a real `state["conf"]`; `encode_analysis.analyze_crop_stability`
treats it as a 0..1 weight. Different sources, different scales,
different defaults.

Goal: one `scoring` API that owns confidence semantics end-to-end --
how raw FWD/BWD agreement becomes a per-frame score, how seeds
override it, and how per-frame scores roll up to per-interval tiers.
Solve, refine, analyze, and encode should all call into the same
helpers instead of each rolling their own. Bonus: re-derive the npz
schema choice from this -- if scoring is fully reconstructable from
geometry plus seeds, the `conf` field stays out of the cache by
design rather than by accident.

## Seeding UI

### Combine YOLO-assist with the motion-cue residual map

Each signal alone is weak:

- YOLO misses small or occluded runners and picks up spectators.
- Motion cues highlight anything moving (other runners, crowd,
  camera shake).

Their intersection -- "person-shaped AND moving in a way consistent
with the predicted trajectory" -- should filter out most false
positives and surface the runner even when either signal alone would
fail.

Useful in the seed UI (to rank candidate boxes) and possibly as a
gated per-frame observation during propagation.

## GUI overlays

### Show motion heat map in the annotation window

Offer a toggle/overlay for the motion heat map inside the GUI so the
user can see moving objects directly while seeding or reviewing,
without having to run a separate diagnostic tool.
