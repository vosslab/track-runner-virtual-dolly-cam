# Blob pipeline redesign: analysis and proposal

Draft report. Not a decision. Written after phase 3B/3C data landed on
the Conant clip and post-reseed IMG_3627 / IMG_3629.

## What the data says

### Conant, 162 post-race intervals (FWD+BWD)

**Finding 1. Hermite is not drifting.** No Conant interval has
`median_div_h > 0.3`; the raw_pred-to-blended divergence sits in
[0.00, 0.30] across the full sweep. Option C from the phase-4 menu
("improve raw_pred on short intervals") is not the weak link on this
clip.

**Finding 2. Divergence-accept correlation is inverted.** The tight
bucket has the worst accept rate:

| median div_h bucket   |   n | median accept |
| ---                   | --- | ---           |
| 0.00-0.10 (tight)     |  75 |          0.8% |
| 0.10-0.30             |  67 |         46.7% |
| 0.30-0.60             |   0 |           n/a |
| 0.60+ (drift)         |   0 |           n/a |

The tight bucket is dominated by short intervals where Hermite is
fine but blob-to-prediction distance is still large. Where raw_pred
is most accurate we accept the *fewest* blobs.

**Finding 3. Oracle rescue is modest.** Replacing the real blob with
a synthetic observation at the solver's own blended position (the
`--diagnostic-blended-reference` flag) nudges accept rate up but does
not saturate:

| duration   |   n | real % | oracle % | delta |
| ---        | --- | ---    | ---      | ---   |
| <0.5s      |  20 |   0.4% |    35.5% | +35.2 |
| 0.5-1.0s   |  42 |  12.0% |    28.8% | +16.8 |
| 1.0-2.0s   |  62 |  42.0% |    60.6% | +18.6 |
| 2.0s+      |  18 |  61.2% |    74.8% | +13.6 |

Even an observation that is by construction on the runner only
clears 35.5% on the shortest bucket.

**Finding 4. Gates reject correct observations.** Of 72 failing
intervals (real accept < 20%), the oracle-rescue distribution is:

| oracle bucket        |   n | interpretation                      |
| ---                  | --- | ---                                 |
| oracle >= 80%        |   0 | gates fine, real blob is wrong      |
| oracle rescues +30pp |  26 | gates fine, real blob noisy         |
| oracle stays low     |  46 | gate logic or raw_pred bad          |
| total failing        |  72 |                                     |

**46/72 stay low even with a perfect observer.** Gate logic itself --
not blob quality -- rejects the observation on those 46. This is the
"gate-by-gate audit" branch anticipated in phase 4.

### IMG_3627 / IMG_3629 after reseeding

Reseeding IMG_3629 dropped BWD p90 divergence from 3.69 to 2.04 (~45%
reduction), confirming that denser seeds do shrink Hermite
disagreement as the blend-tiebreak theory predicted:

| clip             | FWD median / p90   | BWD median / p90   |
| ---              | ---                | ---                |
| IMG_3629 v3 pre  | (prior run)        | median ~0.2 / 3.69 |
| IMG_3629 v4 post | 0.07 / 0.93        | 0.11 / 2.04        |
| IMG_3627 v4 post | 0.18 / 2.07        | 0.23 / 2.74        |

IMG_3627 still has 40 low-severity intervals. The two worst need a
mid-interval seed split more than they need a blob-pipeline redesign:

|   iv | frames       |   dur | FWD med/p90 | BWD med/p90 |
| ---  | ---          | ---   | ---         | ---         |
| [69] | 6279-6552    |  9.1s | 0.20 / 12.4 | 8.66 / 16.8 |
| [50] | 4368-4641    |  9.1s | 0.84 / 12.4 | 8.93 / 16.3 |
| [ 4] |  237- 471    |  7.8s | 0.00 /  8.9 | 1.63 /  9.5 |
| [54] | 4800-4914    |  3.8s | 0.11 /  7.3 | 0.62 /  9.0 |
| [ 3] |    3- 237    |  7.8s | 0.00 /  6.3 | 0.78 /  7.2 |
| [ 8] |  600- 709    |  3.6s | 0.17 /  5.0 | 0.24 /  6.0 |
| [10] |  819- 942    |  4.1s | 2.90 /  3.9 | 0.39 /  1.0 |
| [12] | 1006-1071    |  2.2s | 0.07 /  3.1 | 0.35 /  3.9 |

The top two at 9.1s are clearly under-sampled; everything beyond the
top tier (p90 ~ 2-3h) is genuinely hard content, not low-hanging
fruit.

## Diagnosis

The current `_apply_blob_snap` layer treats three gates as hard
filters:

- **proximity** -- blob center within `alpha` torso heights of raw_pred
- **direction** -- blob displacement vector agrees with raw_pred tangent
- **motion path** -- blob not too far perpendicular to the expected
  motion line

A blob that fails any one gate is discarded entirely; a blob that
passes all three is fully snapped. The three-bucket funnel summary
from `check_interval_blob_funnel.py` shows the path gate dropping the
most frames on short intervals, but the phase-3c oracle result makes
clear that the path gate is not actually the problem. When a perfect
observation fails the gate, the issue is that the gate's *reference*
is untrustworthy on short intervals:

- The raw_pred tangent on a short interval is dominated by seed-to-
  seed chord noise. Direction comparisons against it reject many
  observations that are geometrically fine.
- The proximity threshold (`alpha * torso_h`) is a per-frame distance
  in torso units, not a per-second displacement. On a short interval
  where the runner moves several torso heights in one frame, 0.6h is
  a tight miss; on a long interval where the runner moves 0.1h per
  frame, 0.6h is enormous.
- The path gate's corridor is built from raw_pred, so it inherits the
  same tangent noise.

The design assumed raw_pred is truth and blob is noisy. Phase-3 shows
the relationship is more symmetric than that: both estimators are
noisy, and their disagreement on failing intervals is structural
(residual-motion blobs drift off-centroid due to leg motion; raw_pred
tangents are chord-dominated on short spans) rather than due to wrong-
runner identification.

## Proposal: replace binary gates with continuous weighting

Core change: the per-frame output is not "snap to blob if all three
gates pass, else pure Hermite." It is

    output[t] = (1 - w[t]) * raw_pred[t] + w[t] * blob_center[t]

where `w[t] in [0, 1]` is a smooth function of the same three gate
signals, and `w[t] = 0` on frames with no corridor blob (identical to
current behavior on those frames).

### Shape of `w`

Each gate produces a soft score in [0, 1]:

    s_prox = softstep(blob_dist_h,     lo=0.3, hi=1.5)   # closer is better
    s_dir  = (1 + cos(angle_diff)) / 2                   # aligned is better
    s_path = softstep(perp_dist_h,     lo=0.3, hi=1.5)   # on-line is better

where `softstep(x, lo, hi) = clamp((hi - x) / (hi - lo), 0, 1)`.

Final weight:

    w[t] = min(s_prox, s_dir, s_path) * w_max

with `w_max <= 0.5` as the maximum pull per frame (half a torso
toward the blob when all three gates are saturated). Current design
is the limit `w_max in {0, 1}` with hard thresholds; the proposal is
a bounded intermediate regime.

### Why this is on-contract

- Contract C5 (pass independence, scoring from raw pass trajectories):
  gates still read only the frozen pass-local `raw_pred`. No cross-
  frame accepted-blob memory. No cross-pass reads. Agreement/scoring
  still compares FWD vs BWD raw pass trajectories, not blended
  output. A post-blob-snap pass trajectory already exists in the
  current design; this proposal changes how that trajectory is
  computed, not what scoring sees.
- Contract C3 (interval independence): per-frame weight from per-
  frame gate signals; no accumulators across intervals.
- Contract C4 (seeds are truth): endpoints untouched. `w[0] = w[N] =
  0` enforced. Blend-to-seed tail remains.
- Contract C6 (no appearance cues): `w` is computed from geometry
  alone (position, velocity, torso scale).
- The three gates' architectural role is unchanged; only their
  binary-vs-continuous shape changes.

### Why this addresses the phase-3 failures

- The 46 "oracle stays low" intervals: a blob that is on the runner
  but outside the current threshold ring now contributes at a small
  but nonzero weight. The track improves marginally each frame
  instead of staying at pure Hermite.
- The 26 "oracle rescues" intervals: a clean blob now contributes at
  a weight near `w_max` every frame, so the per-frame pull integrates
  over the interval instead of relying on every frame passing the
  gate.
- Short-interval failures: the blob signal is never fully rejected by
  a single noisy gate. The worst short-interval case degenerates to
  "blob contributes at w ~ 0.1-0.3" rather than "blob contributes at
  w = 0".

### Failure modes introduced

1. A consistently wrong-runner blob now pulls output every frame
   instead of being discarded. Mitigations:
   - Cap per-frame pull at `w_max * torso_h` (e.g., 0.5h). A wrong-
     runner blob at 2h away saturates the proximity soft-step at 0
     anyway, so pull stays bounded.
   - Direction gate in soft form still penalizes a blob moving
     opposite to raw_pred tangent (cosine near -1 -> `s_dir` near 0
     -> `w` near 0).
2. The `w_max` parameter becomes a new knob to tune. The current
   design has an implicit `w_max = 1` for accepted frames and 0 for
   rejected frames; a calibration run on known-good intervals is
   needed to pick `w_max` that doesn't oversmooth.
3. Torso-h thresholds still need per-interval-duration scaling to
   handle the "0.6h on a 30-fps long interval vs 0.6h on a short
   interval" asymmetry noted above. This is orthogonal to the
   binary-vs-continuous change and can be rolled in at the same time
   by letting `lo, hi` in `softstep` scale with per-frame expected
   displacement.

## Alternative / complementary changes

These are not substitutes for the weighting change but could combine
with it:

- **Scale gate thresholds with per-frame displacement.** Define
  `d_frame = |raw_pred[t] - raw_pred[t-1]|` and set proximity `hi`
  to `max(0.8h, 2 * d_frame)`. A fast-moving runner gets a wider
  ring; a nearly-stationary runner gets the current ring.
- **Use chord direction for the direction gate on short intervals.**
  When `end_frame - start_frame < N` (say 30), replace raw_pred
  tangent with seed-to-seed chord direction for the `s_dir` term.
  Chord is coarser but far less noisy than tangent on short spans.
- **Blob-to-torso calibration (option D).** Phase-3 data does not
  cleanly identify a centroid bias, but if one shows up in a later
  per-frame comparison of blob center vs blended position on
  healthy intervals, subtract the mean offset at observation time.

## Prototype plan

All of this should be prototyped outside the solver first, per phase
4's "do not replace solve" rule.

1. Add a `--weighting-mode` flag to `tools/check_interval_blob_funnel.py`
   that computes the continuous weight per frame but does not feed it
   back; just records `w[t]` and the counterfactual output.
2. Extend `check_prediction_divergence.py` to also report the
   divergence of `(1-w)*raw_pred + w*blob` against blended, so we can
   compare the weighted-output trajectory to the current blended
   output on the same intervals.
3. Run both on the Conant clip; expected outcomes:
   - The 46 "oracle stays low" intervals should show the weighted
     trajectory move measurably toward blended without any gate logic
     change.
   - The 26 "oracle rescues" intervals should show weighted accept
     rate tracking oracle accept rate more tightly.
   - Healthy intervals (currently passing) should show no regression
     in trajectory quality.
4. Only after (3) holds on the Conant clip and at least one other
   clip, propose a solver edit.

## Open questions

- Is there a `w_max` value that is stable across the clips we have, or
  does it want to be scene-adaptive?
- Does the weighting change interact poorly with refine (which
  re-runs blob snap on updated intervals)? Refine is idempotent per
  contract; the weight formula is deterministic from frozen raw_pred
  and per-frame blob geometry, so re-running should converge.
- Should `w_max` scale with scoring tier? High-confidence intervals
  don't need aggressive pulling; low-confidence intervals probably do.

## Summary

The phase-3 data shows the blob pipeline's bottleneck is not bad
prediction or bad observation, but a binary gating design that
rejects most of the usable signal. A continuous weighting formulation
keeps every contract invariant, addresses both the "blob is noisy"
and "gate rejects correct observation" failure modes surfaced by the
oracle test, and has a clean prototype path via the existing funnel
and divergence tools. The main risk -- a consistently wrong-runner
blob pulling output continuously -- is bounded by `w_max` and by the
same gate signals that currently reject such blobs.
