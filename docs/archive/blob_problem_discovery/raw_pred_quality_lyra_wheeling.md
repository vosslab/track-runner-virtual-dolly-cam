# raw_pred -- what it is, how it is built, how it is used

User-prompted writeup. Triggered by the Lyra-Wheeling 5089-5278 visual where
the yellow ellipse appears to "move 5 torso boxes in 2 frames at 60 fps,"
which physically cannot happen. Truth: raw_pred drifts off the runner, a
stationary background blob sits where raw_pred is, the winner pick selects
that background blob, the gate rubber-stamps it. The yellow visual moves;
the runner did not.

This document explains raw_pred as a concept: purpose, construction, use,
and the precise way "if raw_pred drifts from truth then winner is not
truth" plays out.

## Purpose

raw_pred is the per-frame predicted runner center. One `(cx, cy, w, h)`
per frame, per pass (FWD and BWD independent under contract C9).

Its job is to give every other stage of the tracker a stable, smooth
geometric anchor that says "this is roughly where the runner is at frame
t." Stage 3 produces it. Stages 4 and 5 (blob-coupled refinement) consume
it as the center for their ROI crop, corridor filter, proximity gate,
direction gate, and snap output.

raw_pred is intentionally NOT a measurement of the current frame. It is a
prediction from anchor seeds. Per the design philosophy, identity (where
the runner is) is what the human establishes via seeds. raw_pred is the
machine's interpolation between human truth anchors.

Source files:
- [track_runner/velocity_model.py](../../../track_runner/velocity_model.py)
  -- raw_pred construction and the propagator that consumes it.
- [docs/TRACK_RUNNER_DESIGN.md](../../TRACK_RUNNER_DESIGN.md) -- five-stage
  pipeline and signal hierarchy.
- [docs/TRACK_RUNNER_CONTRACT.md](../../TRACK_RUNNER_CONTRACT.md) -- C5
  (boundary imprecision), C6 (interval independence), C9 (FWD/BWD
  independence).

## How raw_pred is calculated

### Inputs

For an interval `[start_frame, end_frame]` between two bracketing seeds:

- left seed: `(frame, cx, cy, w, h)` -- human-authored torso box at
  `start_frame`.
- right seed: `(frame, cx, cy, w, h)` -- human-authored torso box at
  `end_frame`.
- all seeds in the video, in scene coordinates, used only to estimate
  endpoint tangents (next bullet).
- scene_transform -- converts between pixel coordinates and a
  camera-motion-corrected scene frame so the Hermite curve operates on
  motion-stable coordinates.

### Tangents (the "velocity" at each endpoint)

`fit_interval_curves` in `velocity_model.py` builds two directionally
asymmetric Hermite curves per interval, one for FWD, one for BWD:

- FWD slope at the left seed is estimated from BACKWARD-looking neighbors
  (the seeds before `start_frame`). The FWD curve uses this slope as its
  starting tangent.
- BWD slope at the right seed is estimated from FORWARD-looking neighbors
  (the seeds after `end_frame`). The BWD curve uses this slope as its
  starting tangent (going backwards).
- The opposite-endpoint slope on each curve is set to the chord velocity
  between the two seeds: `(right - left) / interval_length`.

The slopes are estimated from neighbor seeds, not from image evidence.

### Position interpolation

For each frame `t` in `[start_frame, end_frame]`:

1. `t_param = (frame - start_frame) / interval_length` in `[0, 1]`.
2. `scene_cx = hermite_interpolate(t_param, left_sx, right_sx, m0_x * L, m1_x * L)`
   where `L = interval_length`. Same form for `scene_cy`.
3. `scene -> pixel` via `scene_transform.scene_to_pixel(frame, scene_cx, scene_cy)`.

`hermite_interpolate` is the standard cubic Hermite basis. The four basis
weights blend the two endpoint positions and the two endpoint tangents.
There is no per-frame image input.

### Size interpolation

Width and height are interpolated in LOG space (multiplicative growth, not
linear), with their own endpoint slopes:

```
log_w = hermite_interpolate(t, log(left_w), log(right_w), m0_w * L, m1_w * L)
w     = exp(log_w)
```

The log space matters because runners visually grow and shrink as they
approach or recede from the camera; linear interpolation in pixel size
makes mid-interval boxes systematically wrong.

### Confidence

raw_pred carries a per-frame confidence value that decays linearly with
distance from the nearest seed: `start_conf = 1.0`, decay `0.97` per
frame, floor `0.1`. Confidence is descriptive, not gating: it is recorded
so downstream readers know how far they are from a truth anchor.

### Output

`_compute_raw_pred_forward` and `_compute_raw_pred_backward` each return a
list of `(frame_index, cx, cy, w, h, conf)` tuples in pixel coordinates,
one entry per frame from `start_frame` to `end_frame` inclusive. The two
lists are byte-frozen for the rest of the solve: downstream code is
documented to read raw_pred only, never to write it.

## How raw_pred is used

Once raw_pred exists for every frame in an interval, the propagator in
`_apply_blob_snap` consumes it as the geometric anchor at every blob
decision.

### What raw_pred drives, in order

1. **ROI cropping**. The per-frame ROI rectangle is centered on
   `raw_pred[t]` with a fixed multiple of the torso height. Residual
   extraction reads only inside that ROI.
2. **Residual / blob extraction**. Blob extraction itself is image-driven
   and does not read raw_pred, but it only runs on the ROI raw_pred chose.
3. **Corridor filter**. Of the raw blobs, only those within a radius
   `R * h` of raw_pred are kept as "corridor blobs."
4. **Cue confidence scoring**. The proximity term of the cue-confidence
   score is `dist(blob, raw_pred) / h`. Smaller distance -> higher score.
5. **Winner pick**. The blob with the highest cue-confidence score is
   tagged "the winner" and is the only candidate the three gates evaluate.
6. **Proximity gate**. Accept only if `dist(winner, raw_pred) / h <= 0.6`.
7. **Direction gate**. Accept only if
   `dot(winner - raw_pred, v_pred_dir) > 0` and large enough in magnitude.
   `v_pred_dir` itself comes from raw_pred differences.
8. **Path gate**. Bootstrap from prior accepts; reads recent raw_pred and
   recent accepts.
9. **Snap output**. On accept, snap_pred shifts raw_pred a bounded
   fraction toward the winner. On reject, snap_pred equals raw_pred
   unchanged.

Every per-frame quantity in `verdicts.csv` other than the raw extracted
blob centers themselves derives from raw_pred:

| verdicts column                                | reads raw_pred?  |
| ---------------------------------------------- | ---------------- |
| `winner_dist_px`, `winner_dist_h`              | YES              |
| `prox_ok`                                      | YES              |
| `dir_ok`, `dir_dot`, `v_pred_mag`              | YES              |
| `path_ok_prev`                                 | YES (via prior)  |
| `pre_dog_max_at_raw_pred`, `post_dog_max_at_raw_pred`, `validity_at_raw_pred` | YES |
| `roi_x1`, `roi_y1`, `roi_x2`, `roi_y2`         | YES              |
| `corridor_blobs_json` `cx`, `cy`               | NO (image)       |
| `n_corridor_blobs`                             | indirectly (via corridor radius) |

## Winner picking

"Winner" is a label inside the blob refinement pipeline. It is NOT a
statement that the chosen blob is the runner.

### Pick rule

For each frame, the corridor list (blobs within `R * h` of raw_pred) is
scored by cue-confidence:

```
size_score        = 1.0 if area in target band else < 1.0
proximity_score   = exp(-(dist / h) ** k)   # higher when closer to raw_pred
integrated_mag    = sum of residual magnitudes inside the blob mask
total_score       = 0.3 * integrated_mag + 0.3 * size + 0.4 * proximity  # convex blend
```

The corridor list is then sorted by `total_score` descending; index 0 in
`corridor_blobs_json` is the winner.

### What this rule actually selects

The winner is the corridor blob that is brightest AND most centered on
raw_pred AND closest to expected torso size. None of those criteria check
"is this the runner."

If the runner is in the corridor, two cases:
1. Runner blob is the brightest + closest + right size -> winner = runner.
2. Some OTHER corridor blob beats the runner on the blended score ->
   winner = the other blob, not the runner.

The "other blob" is most commonly a high-residual background feature near
raw_pred: a bleacher edge that moved slightly under camera jitter, a
distant runner crossing the corridor, a fence post that gets edge-flicker
from the camera-motion compensation.

## Why "winner != truth" when raw_pred drifts

The proximity term dominates winner selection when raw_pred sits on or
very near a non-runner blob:

```
score(non-runner-at-raw_pred) ~ 0.4 * 1.0 + size + integrated_mag
score(real-runner-far-from-raw_pred) ~ 0.4 * exp(-large) + size + integrated_mag
```

The proximity exponential collapses fast: a runner blob `0.7 h` from
raw_pred contributes proximity `exp(-0.7^2) ~ 0.61` while a non-runner
blob at `0.01 h` contributes `exp(-0.01^2) ~ 1.00`. The non-runner blob
wins as long as size and integrated_mag are not dramatically lower.

After winner pick the three gates only see the winner, not the runner. The
proximity gate checks `dist(winner, raw_pred) <= 0.6 h`. For a non-runner
blob sitting at raw_pred that condition trivially holds. The direction
gate checks `dot(winner - raw_pred, v_pred_dir)`; with `dist ~ 0` the
vector is nearly zero and `dot` is nearly zero, which counts as "fine,
not contradicting motion." The path gate compares against prior accepts.

Net effect: the gate ACCEPTS the non-runner blob. The verdicts row reads
`gate=accepted, lost_at_stage=accepting, winner_dist_h=0.01`. The
trajectory snaps to the wrong location. From the gate's perspective every
check passed.

The runner blob, meanwhile, sits in the corridor as a non-winner
alternative. It gets rendered cyan in the diagnostic PNG. It contributes
nothing to the accepted observation. There is no way for the runner blob
to "outvote" the non-runner once the proximity term has decided the order.

This is the precise mechanism by which "if raw_pred drifts from truth,
then winner is not truth": the winner is whatever blob sits closest to
raw_pred, and the gates exist to confirm proximity to raw_pred, not
proximity to the runner.

## Why raw_pred can drift from truth

Three plausible mechanisms, in priority order, all interval-local:

1. **Two seeds cannot represent multi-second runner motion.** A 189-frame
   interval at 60 fps is 3.15 seconds. Real gait, occlusion, pace change,
   and direction change cannot be captured by a single Hermite curve
   anchored at two endpoints. Mid-interval positions drift away from
   truth and toward the smooth interpolation.
2. **Seed boundary imprecision (contract C5).** Both endpoint seed centers
   carry sub-pixel imprecision. With only two anchors across the interval
   that imprecision projects into the middle as a multi-pixel offset.
3. **Endpoint tangent estimate is wrong.** The slope at each endpoint is
   derived from neighbor seeds, not image evidence. A misplaced neighbor
   seed or a noisy local velocity makes the Hermite curve bow away from
   truth.

A 0.28 h offset is small in absolute terms. The damage happens at the
gate threshold: the proximity gate is 0.6 h, the corridor radius is
larger. An offset that places raw_pred next to a stationary high-residual
background feature means that background feature wins proximity over the
real runner.

## Case study: Lyra-Wheeling 5089-5278

Direct measurement from
`output_smoke/blob_refine_fix/2026-05-23/m2_sweep/Lyra-Wheeling-IMG_3912/interval_5089_5278/interval_5089_5278/verdicts.csv`,
FWD pass, frames 5089-5095:

| frame | raw_pred (ROI center) | winner blob   | dist_h | gate     | reason     |
| ----- | --------------------- | ------------- | ------ | -------- | ---------- |
| 5089  | n/a (skipped)         | -             | -      | skipped  | endpoint   |
| 5090  | (2616, 1056)          | (2615, 1043)  | 0.74   | REJECTED | gate_prox  |
| 5091  | (2616, 1056)          | (2614, 1043)  | 0.73   | REJECTED | gate_prox  |
| 5092  | (2616, 1056)          | (2616, 1059)  | 0.01   | ACCEPTED | accepting  |
| 5093  | (2616, 1056)          | (2614, 1059)  | 0.01   | ACCEPTED | accepting  |
| 5094  | (2616, 1056)          | (2612, 1057)  | 0.01   | ACCEPTED | accepting  |
| 5095  | (2616, 1056)          | (2609, 1042)  | 0.76   | REJECTED | gate_prox  |

raw_pred barely moves over 5 frames (Hermite velocity magnitudes are 1.5
px/frame). The ROI is fixed at (2429, 869)-(2803, 1243). raw_pred sits
permanently at the centroid of a stationary background feature near
(2616, 1059).

Real runner is at (~2614, ~1043) on 5090, 5091, 5095. The real runner
blob has y ~ 1043; the background feature blob has y ~ 1058. The two are
about 0.32 h apart.

On 5092-5094 the real runner blob momentarily drops out of the corridor
(residual signal weakens for a few frames, perhaps occlusion or stride
pose). The background feature blob is now the closest blob to raw_pred.
Cue-confidence proximity term picks it as winner. Gate accepts.

When the runner blob returns at 5095, the runner is once again the
closest-to-raw_pred blob in the corridor (cue-conf picks the runner
again), but it is still 0.76 h away because raw_pred is offset from
truth. Gate rejects.

The visual artifact the user observed is the yellow ellipse "jumping" 5
torso heights between frame 5090 and 5092. The runner did not move. The
WINNER-PICK switched between two different blobs in the corridor. The
switch is allowed and expected by the spec; the spec presumes raw_pred is
right.

## Why blob-side policies cannot fix this

The blob refinement plan at
[/Users/vosslab/.claude/plans/kind-exploring-cray.md](/Users/vosslab/.claude/plans/kind-exploring-cray.md)
enumerated policy set `{A, B, C, F, G}` plus triple-conditional E. All
five operate inside the blob pipeline.

For Lyra-Wheeling 5092 with raw_pred offset 0.32 h from real runner:

| policy | what it changes        | accept on 5092?            | runner-correct? |
| ------ | ---------------------- | -------------------------- | --------------- |
| A      | baseline               | background blob            | NO              |
| B      | prox cutoff 0.6 -> 1.0 | background AND runner now in; cue-conf still picks background | NO              |
| C      | closest-blob wins      | background blob (closer)   | NO              |
| F      | weighted snap          | output drifts toward background | NO       |
| G      | prox squared in score  | background even more dominant | NO              |
| E      | shift centroid         | not applicable; centroid already at raw_pred | NO |

None help, because every blob policy still anchors on raw_pred and the
underlying error is that raw_pred is offset.

The plan named this exact risk:

> H_RAW_PRED_CORPUS (raw_pred wrong corpus-wide): the full-corpus oracle
> (WP-1A) shows H2 SUPPORTED at the 30% threshold corpus-wide AND in >=
> 6 of 12 videos. Policy set `{A, B, C, E, F, G}` operates on the blob
> side of the gate. If raw_pred itself is wrong corpus-wide, the blob
> side is the wrong layer to fix; a raw_pred follow-up plan is needed.
> THIS IS THE ONLY SUSPENSION HYPOTHESIS.

Single-interval evidence does not satisfy the 30% / 6-of-12 threshold. It
does falsify the simpler arrow: "blob refinement can fix this kind of
failure by re-weighting corridor blobs." That arrow is refuted on
Lyra-Wheeling 5089-5278 by construction.

## Recommended next experiments

Three, in order, none of which touch production gate code.

1. **Corpus oracle re-analysis.** Add a small analyzer that reads the
   existing `oracle/oracle.csv` and reports the per-interval distribution
   of `dist(raw_pred, seed-interpolated truth) / h_seed`. Report median,
   p90, fraction-of-frames over 0.6 h, per video and corpus-wide. If the
   fraction is >= 30% corpus-wide AND in >= 6 of 12 videos, H_RAW_PRED_CORPUS
   is SUPPORTED.
2. **Suspension decision.** If experiment 1 SUPPORTS the hypothesis,
   write `oracle/SUSPENSION_CHECK.md` with verdict PLAN_SUSPENDED and
   open a follow-up plan targeting Hermite construction or seed density.
3. **Operator-level remedy for this interval.** Independently, the
   189-frame Lyra-Wheeling interval is too wide for two-seed Hermite to
   represent. Adding a manual seed near frame 5092 would split the
   interval, shorten the Hermite span, and pull raw_pred onto the real
   runner without any code change. This is the user-facing remedy
   regardless of the corpus verdict.

## What "fixing raw_pred" would look like (out of scope here)

raw_pred quality is a Stage 3 concern, not a Stage 4 concern. Plausible
follow-up plans, none in scope of this document:

- **Denser seeding.** Reduce average interval length so two-seed Hermite
  drift stays under 0.3 h.
- **Better tangent estimation.** Replace neighbor-seed slope estimates
  with image-evidence-aware tangents.
- **Different motion model.** Replace Hermite with a model that handles
  acceleration, gait periodicity, or lap-cyclic priors.
- **Seed quality scoring.** Score seeds by how much they drag raw_pred
  away from neighbor truth; surface low-quality seeds for re-annotation
  per contract C3 (seeds are truth at solve time, ranked for the user).

All four are Stage 1-3 changes. None are Stage 4 blob policies.

## Artifacts

- PNG visual evidence (real torso aspect, doubled strokes, legend,
  index-0 winner color rule):
  `output_smoke/blob_refine_fix/2026-05-23/microscope/visual_verify_v2/Lyra-Wheeling-IMG_3912_5089_5278/`
- HTML index:
  `output_smoke/blob_refine_fix/2026-05-23/microscope/visual_real_aspect.html`
- Source verdicts:
  `output_smoke/blob_refine_fix/2026-05-23/m2_sweep/Lyra-Wheeling-IMG_3912/interval_5089_5278/interval_5089_5278/verdicts.csv`
- raw_pred construction code:
  [track_runner/velocity_model.py](../../../track_runner/velocity_model.py)
  `_compute_raw_pred_forward` (line 349),
  `_compute_raw_pred_backward` (line 434),
  `hermite_interpolate` (line 217),
  `fit_interval_curves` (line 250).

## Cross references

- [docs/TRACK_RUNNER_CONTRACT.md](../../TRACK_RUNNER_CONTRACT.md) C5
  (boundary imprecision), C6 (interval independence), C9 (FWD/BWD
  independence).
- [docs/TRACK_RUNNER_DESIGN.md](../../TRACK_RUNNER_DESIGN.md) five-stage
  pipeline; anti-pattern chained blob state; signal hierarchy.
- [docs/active_plans/active/blob_refine_microscope_phase.md](../active/blob_refine_microscope_phase.md)
  microscope phase Findings (E1 dir-overshoot, E2 path bootstrap, E3
  mixed-bucket).
- [/Users/vosslab/.claude/plans/kind-exploring-cray.md](/Users/vosslab/.claude/plans/kind-exploring-cray.md)
  blob refinement plan; H_RAW_PRED_CORPUS suspension hypothesis; M3
  decision tree.
