# Check 5: normalized-cy trace (claims D and E)

Part of the blob_walk_v2 validation plan at
[blob_walk_v2_validation_plan.md](../active/blob_walk_v2_validation_plan.md).

Date: 2026-06-10.

## What was measured

Per-frame normalized vertical blob position for walker-selected blobs, across
4 seed-to-seed intervals (2 videos x 2 intervals each; Lyra-Wheeling-120fps
excluded per standing P12 flag). Data source: fresh walker pass via
`tests/e2e/e2e_blob_walk_baseline.py walk --output-dir output_smoke/check5_walks`,
using the current v14 walker (post-P15 fix, SCHEMA_VERSION 12).

Videos and intervals:

| Video | Interval | Role | FWD accepted | BWD accepted |
| --- | --- | --- | --- | --- |
| Conant-4x400-2026_April_15.mkv | seed_1080_1111 | bootstrap | 0 | 4 (3 with cand_cy) |
| Conant-4x400-2026_April_15.mkv | seed_1296_1327 | steady_state | 30 (29 ncy) | 23 (22 ncy) |
| Jason-3200m-sectionals-IMG_4005.mkv | seed_564_583 | early | 0 | 2 (1 ncy) |
| Jason-3200m-sectionals-IMG_4005.mkv | seed_602_629 | steady_state | 24 (23 ncy) | 11 (10 ncy) |

Total accepted frames with ncy: 88 (across 6 non-empty pass directions).

### Reference geometry

`pred_cy` (the walker's last-accepted anchor center-y) is used as the torso-center
reference. This is the geometry the walker itself sees when deciding gate placement.
`torso_h_px` is the linearly-interpolated seed-to-seed torso height at each frame.
The seed frame itself has blank `cand_cy` (bootstrap accepted); those frames are
excluded from the ncy calculation.

### Claim D measurement

For every frame that has a non-empty `candidates_json` (regardless of accepted/miss
status), count distinct blobs within 1 `torso_w_px` of `pred_cy`. Duplicates in
`candidates_json` (same (cx, cy) to 3dp) are collapsed before counting.

### Claim E measurement

`ncy = (cand_cy - pred_cy) / torso_h_px` per accepted frame with a real `cand_cy`.
Statistics: distribution, frame-to-frame `|delta ncy|`, top/bottom alternation rate
(sign flips of ncy about per-interval median), and Pearson r(ncy, integrated_mag).

## Claim D results: limb blob count near reference

### Per-interval near-reference blob counts

| Interval | Direction | Frames w/ candidates | Blob counts (n: pct%) |
| --- | --- | --- | --- |
| Conant/seed_1080_1111 | bwd | 3 | 1: 33%  2: 67% |
| Conant/seed_1296_1327 | fwd | 29 | 1: 97%  2: 3% |
| Conant/seed_1296_1327 | bwd | 22 | 1: 100% |
| Jason/seed_564_583 | bwd | 1 | 1: 100% |
| Jason/seed_602_629 | fwd | 23 | 4: 48%  5: 26%  6: 26% |
| Jason/seed_602_629 | bwd | 10 | 1: 60%  2: 30%  3: 10% |

### Global counts across all 88 frames

| Blobs near ref | Frames | Pct |
| --- | --- | --- |
| 1 | 58 | 66% |
| 2 | 6 | 7% |
| 3 | 1 | 1% |
| 4 | 11 | 12% |
| 5 | 6 | 7% |
| 6 | 6 | 7% |

Exactly-1-blob frames: 66%.  Multi-blob frames: 34%.

### Key observation: video-specific behavior

The split is not uniform. Conant is almost entirely 1 blob near reference (97-100%
of frames). Jason/seed_602_629/FWD is the inverse: 4-6 distinct blobs near reference
on every single frame with candidates (0 frames with exactly 1 blob).

Jason torso scale: `torso_w = 6.5 px`, `torso_h = 11.0 px`. At 11 px tall the runner
subtends sub-frame territory; the DoG band-pass at this scale does NOT merge the runner
into one blob. Instead it resolves 4-6 spatially-distinct residual-motion patches
within the runner body region, likely corresponding to individual limbs and trunk
segments. These blobs are all within 1 torso-width of the reference; they are NOT
distant background blobs or other runners.

A sample per-frame normalized-cy listing for Jason/seed_602_629/FWD (all blobs near
reference, sorted by cy, each value is ncy = (cy - pred_cy) / torso_h):

```
frame 603: [-0.57, -0.57, -0.36, -0.35, -0.23, 0.58, ...]  (6 near ref)
frame 607: [-0.42, -0.40, -0.37, -0.20, -0.16, 0.45, ...]  (6 near ref)
frame 614: [0.09, 0.17, 0.32, 0.39, 0.46, ...]             (5 near ref)
frame 621: [-0.96, 0.00, 0.03, 0.04, 0.29, 0.29, 0.31, ...] (6 near ref)
```

The cluster spans roughly ncy -0.5 to +0.5 (top to bottom of the torso region),
with no clear single dominant blob. Viterbi selects one per frame; the selected
blob's cy is the `cand_cy` used in Claim E.

## Claim E results: within-body centroid jitter

### Per-interval ncy distributions

| Interval | Dir | n | ncy range | mean | median | |delta ncy| mean | p95 | Alternations |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Conant/seed_1296_1327 | fwd | 29 | [-0.291, 0.156] | -0.098 | -0.134 | 0.051 | 0.098 | 6/28 = 0.21 |
| Conant/seed_1296_1327 | bwd | 22 | [-0.089, 0.264] | 0.084 | 0.122 | 0.046 | 0.114 | 2/21 = 0.10 |
| Conant/seed_1080_1111 | bwd | 3 | [-0.688, -0.513] | -0.598 | -0.592 | 0.087 | 0.096 | 1/2 = 0.50 |
| Jason/seed_602_629 | fwd | 23 | [-0.566, 0.298] | -0.082 | 0.013 | 0.085 | 0.358 | 7/22 = 0.32 |
| Jason/seed_602_629 | bwd | 10 | [-0.061, 0.187] | 0.074 | 0.073 | 0.109 | 0.223 | 5/9 = 0.56 |
| Jason/seed_564_583 | bwd | 1 | n/a | -0.748 | -0.748 | n/a | n/a | n/a |

### Global ncy statistics (88 accepted frames)

- Range: [-0.748, 0.298]
- Mean: -0.053   Median: 0.006
- `|delta ncy|`: mean 0.066, p50 0.050, p95 0.211, max 0.384
- Alternation: 21 flips / 82 steps = 0.26 flips/step

### |delta ncy| distribution

| Bucket | Count | Pct |
| --- | --- | --- |
| < 0.05 | 42 | 51% |
| 0.05-0.10 | 28 | 34% |
| 0.10-0.20 | 7 | 9% |
| 0.20-0.50 | 5 | 6% |
| > 0.50 | 0 | 0% |

85% of consecutive-frame steps stay within 0.10 torso heights. The tail is
dominated by Jason/seed_602_629/FWD, where per-frame ncy swings reach 0.384.

### Correlation of ncy with integrated_mag

Per-interval r(ncy, integrated_mag) values: 0.380, 0.679, 0.014, -0.491, -0.557.

No consistent direction: Conant FWD shows a positive correlation (higher blob =
stronger motion), Jason FWD and BWD show a negative correlation. The sign depends
on whether the selected blob happens to be in the upper or lower part of the runner
in the stronger-motion frames. There is no universal pattern linking blob vertical
position to motion strength.

### ncy drift pattern: slow drift vs. jitter

Conant/seed_1296_1327/FWD shows slow monotonic drift over 29 frames (ncy trace:
-0.007 -> +0.156 -> -0.291 -> -0.073), consistent with the selected blob following
the runner's center-of-mass displacement during a running stride rather than
switching between body parts. Alternation rate is 0.21.

Jason/seed_602_629/FWD shows a different character: a large monotonic drop for the
first 8 frames (ncy -0.566 to -0.295), then a rapid recovery and oscillation
(ncy 0.089 to 0.298, then back). The shift at frame 608->614 (ncy -0.295 to +0.089,
delta = 0.384) is the largest single step seen in the corpus and corresponds
exactly to the region where the walker's accepted blob switches. With 4-6 competing
blobs at limb-level spacing on this interval, Viterbi is making a between-blob
choice on nearly every window, and its selection can jump between the top-of-runner
cluster (ncy ~ +0.2) and the bottom-of-runner cluster (ncy ~ -0.4).

## Verdicts

### Claim D: limbs merged into one broad runner-body blob

**REFUTED (conditional)**

The claim holds for Conant (30px tall runner): 97-100% of frames have exactly 1 blob
near reference. The DoG band-pass merges the Conant runner into a single large blob.

The claim does NOT hold for Jason (11px tall runner): every frame with candidates
has 4-6 distinct blobs within 1 torso-width, covering the full vertical extent of the
runner. Limb-level separation is observed on small runners. "Limbs merged" is not a
universal property of this pipeline; it depends on runner apparent size in the frame.

The claim is **REFUTED for small runners** and **SUPPORTED for large runners**.
The crossover scale is somewhere between 11px and 30px torso height.

### Claim E: within-body vertical centroid jitter

**OBSERVED**

Evidence is clearest in Jason/seed_602_629/FWD. With 4-6 blobs across the torso
region, Viterbi selects a different blob per window, and the selected blob's cy
jumps between the lower and upper runner clusters. A 0.384-torso-height single-step
jump (frame 608 to 614) is present. The alternation rate for this pass is 0.32
flips/step; the global rate is 0.26 flips/step.

Conant shows milder jitter: ncy range ~0.35-0.45 torso heights, alternation 0.10-0.21,
step deltas mostly below 0.10. This is consistent with normal stride-motion displacement
of a single merged blob rather than between-blob switching.

The within-body jitter hypothesis is **confirmed for small runners** (Jason, 11px).
For large runners (Conant, 30px) the dominant pattern is slow drift, not sharp jitter.

## Implications for consistency term design

The pre-P2 speculation was that the missing velocity-variance and angle-variance
terms would help by penalizing jerky paths. This measurement suggests the actual
failure mode is more specific:

1. For small runners, the jitter arises because 4-6 distinct blobs compete at
   limb-level spacing. Adding a variance term penalizes the jitter but does not
   fix its cause: too many equally-plausible candidates at fine spatial scales.
   The right fix is either (a) a blob-merging pass at the runner scale before
   Viterbi, or (b) a scale-adaptive proximity term that rewards staying in the
   same sub-cluster across the window.

2. For large runners (Conant), jitter is not the primary issue; the blob supply
   is either empty (bootstrap interval, soft_miss_no_blob) or a single dominant
   blob (steady-state). Variance terms would do nothing here.

No consistency-term change is recommended at this time. The measurement gates
further design discussion per the validation plan constraint.

## Limitations

- 4 intervals across 2 videos is a small sample. The Conant and Jason results
  may not generalize to other videos in the corpus.
- The two stall intervals (Conant 1080-1111 FWD and Jason 564-583 FWD) have
  zero accepted frames with cand_cy and contribute nothing to Claim E.
- pred_cy drifts as the anchor goes stale across miss runs; the ncy reference
  is not a pure seed-interpolated torso center. This inflates apparent jitter
  slightly on miss-heavy passes.
- Lyra-Wheeling (120fps, stride=2, P12 live bug) excluded from all measurements.

## Commands used

```bash
# Fresh walk
source source_me.sh && python3 _temp_check5_fresh_walk.py
# Full analysis
source source_me.sh && python3 _temp_check5_analyze_fresh.py
# Detailed Jason per-frame table
source source_me.sh && python3 _temp_check5_detail.py
```

Output dir: `output_smoke/check5_walks/`
Walker schema: v14 (SCHEMA_VERSION 12), columns include `path_step_cost` and
`window_head_frame`.
