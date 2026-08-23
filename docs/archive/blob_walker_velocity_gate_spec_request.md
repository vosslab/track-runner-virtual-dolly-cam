## SUPERSEDED 2026-05-28

This spec was abandoned on 2026-05-28 after failing the 3-frame replay
validation. The proposed per-frame velocity gate plus y-axis flex did not
recover lock in 4 of 6 corpus videos (Jason, Lyra-Wheeling, IMG_3823, and
one additional clip failed the frame-1 acceptance bar). Widening the
slack term did not change the outcome, so the failure is structural to
the per-frame single-winner selection design, not a tuning problem. The
spec is archived for audit trail only and must not be implemented.

The replacement design is
[windowed_path_selection_amendment.md](windowed_path_selection_amendment.md),
which addresses the same lock-loss problem with a multi-frame windowed
path-selection model instead of a per-frame gate. Parts of the
replacement have already shipped via tasks #83 and #86. Consult the
amendment doc for the current active design.

# Blob walker velocity gate + y-axis flex: spec request

Status: SPEC REQUIRED. Do not start implementation until every numbered
question below has a written answer.

## Context

User flagged a single tile sequence (Glenbrook 1260-1680, FWD walk
from seed 1260). Visible behavior:

- +0, +1, +2, +4 frames: walker accepts the correct runner torso blob.
  Each accepted blob lands on the runner. Lock looks healthy.
- +8 frame: walker accepts a candidate that is far below and to the
  left of the +4 position. Tile is bordered red, status
  `rejected_max_jump`. Candidate ellipse is small and sits on a
  leg/foot blob, not the torso.
- +16 frame: past-stop (walker already stopped at +8).

Two failures stacked in one frame:

1. **Detector candidate is wrong.** The motion-cue heat map shows the
   torso AND the legs as separate hot blobs. At +8 the detector
   selected a leg blob. The acceptance box is too tight on y, so the
   thigh blob outscored a torso blob (or the torso blob was clipped
   out of the search region).
2. **Velocity sanity gate did not fire early enough.** The displacement
   from +4 to +8 is much larger than the displacement +0 -> +1 ->
   +2 -> +4 implied. The current `rejected_max_jump` gate caught the
   jump after the fact and stopped the walk; it did not steer the
   walker to a soft miss and try the next frame.

Source artifact: `blob_walk/visual/2025-Glenbrook_South-1600m-IMG_1503/seed_1260_FWD/frame_001268.png`
and surrounding tiles. Verdicts row at offset 8 has
`status=rejected_max_jump`. FWD lock_length=8, BWD lock_length=3.

Stale interval note: 1260-1680 no longer exists as an adjacent-seed
interval. The user added seeds between 1260 and 1680. Any rerun
should rediscover intervals from the current seeds.json, not from the
existing blob_walk/ output dirs.

## Core principle: the walker never gives up on the interval

User correction 2026-05-25: the walker stops when it reaches the
neighboring seed, not when it fails a single frame. There is no
"past stop" tile state because there is no walker stop in the
middle of an interval. A bad frame is a soft miss, not a terminal.

Consequence: hard-stop guardrails (`rejected_max_jump`,
`rejected_direction_reversal` as currently implemented) are wrong.
Both must demote to soft misses: reject the candidate, keep velocity,
advance the frame counter, retry on the next frame. The walk only
stops on:

- hit neighbor seed (success), or
- consec soft-miss counter reaches the variant's miss cap (and even
  then this is a per-interval stop, not a contract state).

This subsumes the "do not teleport" rule below and the "miss
handling" section of the spec.

## Design proposal (pending spec)

Two changes, both pre-spec:

1. **Y-axis tolerant acceptance region.**
   - Keep x tight around torso width.
   - Allow y from upper torso through thighs.
   - Score vertical displacement softly. Do not hard reject
     thigh-centered blobs; treat them as a slightly lower-confidence
     match.
2. **Velocity sanity gate (SOFT, not hard).**
   - Compare candidate displacement to last accepted position.
   - When the jump exceeds plausible frame-to-frame motion, REJECT
     THE CANDIDATE BUT NOT THE WALK. Record soft miss, keep velocity,
     advance the frame counter, try the next frame.
   - For skipped frames, scale the cap by `dt`, but still cap it.
   - +4 -> +8 must not be ACCEPTED unless `dt=4` makes the
     displacement physically plausible. The walker continues to
     +16 regardless.
3. **Gap handling.**
   - Do not teleport to the next blob.
   - If no plausible candidate appears, mark `soft_miss`.
   - Keep prior position and velocity.
   - Stop after miss cap; do not accept a distant blob to escape a
     gap.

## Spec required before implementation

Every question below must have a single concrete answer. "Use
existing defaults" is not an answer; cite the constant or pin the
value here.

### 1. Exact state vector

What variables are tracked per step? Candidate set:

- `cx`, `cy` (last accepted position in scene coords)
- `vx`, `vy` (velocity in scene units per frame, EMA or last-step or
  rolling N-frame)
- `last_accepted_frame_index`
- `consec_miss_no_blob`, `consec_miss_low_conf`
- anything else (size estimate, integrated direction, ...)

Confirm or revise.

### 2. Exact prediction equation

How is expected position at frame `k+1` computed?

- `cx_pred = cx_last + vx * dt`?
- `cy_pred = cy_last + vy * dt`?
- Constant velocity? Decayed velocity? Bounded by max speed?

State the formula verbatim with all symbols defined.

### 3. Exact jump gate formula

Formula for max allowed displacement. State the symbolic form.

How does the cap scale with:

- torso height `h` (multiples thereof, units = torso heights per frame)
- fps (per-frame scaling vs per-second scaling)
- skipped frames `dt` (linear in dt? sqrt? capped?)
- confidence (does a high-confidence candidate get more slack?)

### 4. Miss handling

After a soft miss:

- Is velocity frozen at the last-known value?
- Decayed (by what factor per missed frame)?
- Recomputed only on next accept?
- Does search radius expand by some factor per missed frame? By
  exactly how much?
- After how many consec misses is the walk stopped?

### 5. Coordinate space

Every formula above: pixel space or scene space (camera-motion
compensated)?

- Before or after bin-factor scaling?
- If pixel space: which frame's coords (frame `k` or frame `k+1`)?
- If scene space: is the gate evaluated in scene coords throughout,
  or converted back to pixel coords for the corridor / observer
  ROI?

### 6. Acceptance order

Does the motion gate run before cue-confidence ranking or after?

- If before: the gate excludes candidates first, then cue-conf
  ranks the survivors.
- If after: cue-conf picks a winner, then the gate accepts or rejects
  it.

Can a winner bypass motion rejection (e.g., very high cue
confidence)? If yes: state the bypass threshold and rationale.

### 7. Debug logging per frame

`verdicts.csv` must record at minimum:

- predicted `(cx_pred, cy_pred)` (the gate's expected position)
- chosen blob `(cx_blob, cy_blob)`
- displacement `dist(chosen, predicted)`
- allowed max displacement (the gate cap at this frame)
- `dt` (frames since last accept)
- velocity vector `(vx, vy)`
- reject reason (one of the named statuses; same vocabulary as
  current `status` column)

Columns to add to the existing verdicts schema (note: SCHEMA_VERSION
bump per contract C10 if added):

- `cx_pred`, `cy_pred`, `dt`, `vx`, `vy`, `displacement`,
  `max_displacement`

Existing columns `cx_raw`, `cy_raw`, `obs_cx`, `obs_cy`, `status`,
`corridor_cx`, `corridor_cy` stay.

## Acceptance criteria

Spec is accepted when:

- All 7 questions have a single written answer (no "TBD", no
  "configurable", no enum without a default).
- The answer set is internally consistent (e.g., section 5
  coordinate choice does not contradict section 3 unit choice).
- Section 7 column additions list every field with a stated
  dtype.
- A test plan names at least one synthetic interval where the new
  gate would behave differently from the current gate, plus the
  expected `verdicts.csv` row for that frame.

Until the spec lands, do not modify
[residual_motion.py](../../track_runner/residual_motion.py),
[velocity_model.py](../../track_runner/velocity_model.py),
or `walk.py`.

## Cross references

- [TRACK_RUNNER_CONTRACT.md](../TRACK_RUNNER_CONTRACT.md) C5
  (torso boxes are imprecise-boundary), C6 (intervals independent),
  C9 (FWD/BWD pass-local state), C10 (unified SCHEMA_VERSION).
- [TRACK_RUNNER_DESIGN.md](../TRACK_RUNNER_DESIGN.md)
  "Anti-pattern: chained blob state" -- per-frame velocity belongs
  to the per-walk state only; no global accumulator.
- Image evidence: `blob_walk/visual/2025-Glenbrook_South-1600m-IMG_1503/seed_1260_FWD/`.
- Related: `INTEGRATION_ASSESSMENT.md`
  Plan C (Stage 4 single-gate fix) -- still pending Plan B
  validation; this spec feeds the same downstream gate.

## Correction 2026-05-28 v3 (supersedes all prior corrections in this file)

Three prior bootstrap models have failed the 3-frame sequence test:

1. Single-frame radius (over-fit one lucky step).
2. Static zero-velocity bootstrap (no chance once the runner moves).
3. Endpoint-seed chord velocity prior (chord is an AVERAGE; runners are
   not chord-linear at frame scale -- they accelerate, brake into
   corners, and exhibit per-stride centroid oscillation).

The chord-prior failure pattern is documented in
`BOOTSTRAP_REDESIGN.md`.
The replacement design below collapses "bootstrap mode" to a single
permissive frame and uses real walker-measured velocity from there on.

## 1. State vector

Per-step bootstrap state:

- `seed_cx_px`, `seed_cy_px` -- this walker's anchor seed center, pixel
  coords.
- `seed_scene` -- anchor seed center in scene coordinates at the seed
  frame.
- `step_1_accept_scene` -- scene position of the winner blob accepted
  at step 1 (populated after step 1).
- `step_2_accept_scene` -- scene position of the winner blob accepted
  at step 2 (populated after step 2).
- `source_fps` -- frame rate of source video, REQUIRED (no default).
- `torso_w_px` -- seed-derived scale, pixel units.

There is NO endpoint-seed chord velocity prior in this design.

## 2. Prediction equation

```
pred_scene_step_1 = seed_scene
pred_scene_step_2 = step_1_accept_scene + (step_1_accept_scene - seed_scene)
pred_scene_step_3 = step_2_accept_scene
                    + 0.5 * (step_2_accept_scene - seed_scene)
```

Step 1 is geometry-only: the predicted center is the seed itself. Step
2 projects forward by the displacement just measured at step 1. Step 3
uses the rolling mean of the last 2 measured displacements (which under
the algebra simplifies to projecting the step-2 accept by half the total
displacement from the seed). From step 2 onward the walker is in
tracking mode with measured velocity.

Tracking mode (step >= 2) is identical in shape to the existing
post-bootstrap rolling-velocity recompute in
`walk_walker.py`,
seeded so the rolling window includes the seed entry from frame 0.

## 3. Jump gate formula

Per-step radius check in scene space; identical radius at every
bootstrap step.

```
search_radius_w     = (MAX_RUNNER_SPEED_W_PER_S / source_fps)
                      + BOOTSTRAP_UNCERTAINTY_W
allowed_radius_scene = search_radius_w * torso_w_scene
accepted            = dist(winner_scene, pred_scene_step_i)
                      <= allowed_radius_scene
```

The radius is constant across the bootstrap because each step's
prediction already absorbs the previously measured displacement; the
runner can move at most one per-frame envelope plus localization slack
from the predicted center.

Constants pinned in [walk_motion_gate.py](../../track_runner/blob_walk/walk_motion_gate.py):

- `MAX_RUNNER_SPEED_W_PER_S = 30.0` (W/s). 20 mph upper at 12 inch torso.
- `MIN_RUNNER_SPEED_W_PER_S = 7.3` (W/s). 5 mph lower at 12 inch torso.
- `BOOTSTRAP_UNCERTAINTY_W = 0.30` (W). Sub-torso localization slack.

Bootstrap N (frames before tracking mode) = 1. From step 2 onward the
walker uses the rolling walker-measured velocity vector for prediction.

## 4. Miss handling

Unchanged from the prior implementation. On a miss the walker holds
prev_scene at the last accept and continues; the rolling velocity stays
at its last computed value.

## 5. Coordinate space

Unchanged: jump gate evaluates in scene space; ROI extraction runs in
pixel space; overlays render in ROI-local pixel coordinates.

## 6. Acceptance order

Unchanged: cue-confidence selects winner; motion gate accepts or rejects.

## 7. Verdicts schema additions

`mode` distinguishes `bootstrap` (step 1 only) from `tracking` (step
>= 2). `vx_scene`, `vy_scene` are the WALKER-MEASURED velocity
components, computed from the rolling window of accepted scene
positions (seeded with the seed entry so step 2 has a valid
displacement from step 1's accept).

## 8. Cold-start velocity

The walker's cold-start velocity is ZERO. There is no chord prior. Step
1 fires with `pred = seed`, geometry-only. After step 1 accepts, the
rolling walker-measured velocity sets the prediction for step 2; it is
the seed -> step-1-winner scene displacement divided by one frame.

## Replay validation (3-frame sequence test, 2026-05-28 v3)

Per `REPLAY_REPORT.md`
under the corrected design (walker-measured velocity, constant radius,
BOOTSTRAP_UNCERTAINTY_W = 0.30):

| Video | Accepts / Testable | % | Bar (>51%) |
| --- | --- | --- | --- |
| Conant-4x400-2026_April_15 | 1 / 3 | 33 | FAIL |
| IMG_3823 | 2 / 4 | 50 | FAIL |
| IMG_3830 | 3 / 4 | 75 | PASS |
| Jason-3200m-sectionals-IMG_4005 | 0 / 3 | 0 | FAIL |
| Lyra-Hersey-800m-IMG_3882 | 6 / 7 | 86 | PASS |
| Lyra-Wheeling-IMG_3912 | 0 / 3 | 0 | FAIL |

4 of 6 videos FAIL. The slack sweep in
`RADIUS_SWEEP.md`
confirms widening slack to 0.75 W does not recover Jason, Lyra-Wheeling,
or IMG_3823 -- this is not a slack-tuning problem and not a velocity-
model problem. The next escalation suspects runner-extraction or the
ROI/transform stack; details in
`BOOTSTRAP_REDESIGN_v2.md`.

This spec is therefore NOT landed.

<!-- end of corrected spec; everything below this comment was the prior
stale "Corpus correction" + "Spec answers" block, removed 2026-05-28 per
coordinator direction (working artifact, no authority). -->

## Stale prior content removed 2026-05-28

The prior "Corpus correction 2026-05-28" and "Spec answers (filled
2026-05-28)" sections that followed this point pinned
`acceptance_radius_w = 1.25 (measured)` from a single-frame sweep. Both
sections are invalidated by the 3-frame sequence test (see "Correction
2026-05-28" and "Replay validation" above). The stale text was removed
per coordinator direction (working artifact, no authority). The
CHANGELOG bullet noting the removal is the durable audit trail.

