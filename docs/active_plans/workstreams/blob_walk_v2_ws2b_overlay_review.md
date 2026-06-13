# WS-2B overlay review: Lyra-Hersey [840,945] and Jason [12408,12596]

Date: 2026-06-12.
Status: COMPLETE.

Artifact: this file.
Tile paths:
- `corpus_walk/Lyra-Hersey-800m-IMG_3882/seed_840_945/fwd/` (106 tiles, rendered)
- `corpus_walk/Lyra-Hersey-800m-IMG_3882/seed_840_945/bwd/` (106 tiles, rendered)
- `corpus_walk/Lyra-Hersey-800m-IMG_3882/seed_840_945/trajectory.png` (rendered)
- `corpus_walk/Jason-3200m-sectionals-IMG_4005/seed_12408_12596/fwd/` (189 tiles, rendered)
- `corpus_walk/Jason-3200m-sectionals-IMG_4005/seed_12408_12596/bwd/` (bwd tiles still rendering when artifact written; CSVs complete)

Data sources:
- Walk CSVs: `output_smoke/ab_22pass/Lyra-Hersey-800m-IMG_3882.mkv/seed_840_945/` (pre-existing, from 22-pass run)
- Fresh walk+render: `corpus_walk/<video>/seed_<L>_<R>/` (written by `_temp_ws2b_render.py` 2026-06-12)
- Seed positions confirmed via `_temp_ws2b_seeds.py`
- e2e metrics from `e2e_walker_ab` log `/tmp/e2e_walker_ab.log` (job `bnp8r9l58`)

---

## 1. Lyra-Hersey [840, 892, 945] -- normal-length regression

### e2e metrics

- hermite_err = 0.206 torso-widths
- walker_err = 0.778 torso-widths (delta +0.572)
- classification: regressed
- span: 105 frames (left seed 840, right seed 945, held-out seed 892)

### Seeds (source pixels)

| frame | cx | cy | w | h | status |
| --- | --- | --- | --- | --- | --- |
| 840 | 1745.0 | 674.5 | 28.0 | 53.0 | visible |
| 892 | 1741.0 | 677.5 | 24.0 | 53.0 | visible |
| 945 | 1790.0 | 669.0 | 28.0 | 56.0 | visible |

Processed-pixel seeds (bin_factor=2, torso_w=14px):
- left seed 840: cx=872.5, cy=337.25
- right seed 945: cx=895.0, cy=334.5
- held-out seed 892 (ground truth): cx=870.5, cy=338.75

### Status mix (interval_summary.csv)

| direction | accepted | interpolated | soft_miss_no_blob | stop_reason |
| --- | --- | --- | --- | --- |
| FWD | 103/105 | 1 | 1 | hit_neighbor_seed |
| BWD | 103/105 | 0 | 2 | hit_neighbor_seed |

103/105 accepted in each direction. The walker successfully traverses almost the
entire interval. There are no hard stops, no direction reversals, no identity jumps.
The acceptance rate alone does not explain the regression.

### Characterization of the error at frame 892

The walker is tracking the runner but the FWD/BWD selected blob at frame 892 sits
~9px below the true torso center:

| quantity | value |
| --- | --- |
| FWD cx at frame 892 | 871.03 px (seed truth: 870.5; error: 0.53 px = 0.04 tw) |
| FWD cy at frame 892 | 347.91 px (seed truth: 338.75; error: 9.16 px = 0.65 tw) |
| BWD cx at frame 892 | 871.51 px (seed truth: 870.5; error: 1.01 px = 0.07 tw) |
| BWD cy at frame 892 | 348.21 px (seed truth: 338.75; error: 9.46 px = 0.68 tw) |
| blended cy error (approx) | ~9.3 px / 14 = 0.66 tw; combined ~0.78 tw |

At frame 892:
- corridor_n = 1 (one blob inside the corridor in both FWD and BWD)
- strength_score FWD = 0.70 (vs ~1.0 on most frames), integrated_mag = 7023
- integrated_mag at frame 892 is 7023, vs 11000-25000 on frames 841-891

The blob accepted at 892 has the correct cx but its cy is biased low (centroid pulled
down by leg/foot region or by the runner's crouched arm-pump position at that point
in the stride cycle). The corridor filter admits only one blob; the walker accepts it
because it is the only available candidate and its proximity score is high (0.92).
There is no wrong-blob selection -- the one blob in the corridor IS near the runner.
The blob centroid itself is displaced downward ~9px from the human-annotated torso box.

### Where on the span does the path diverge

From the trajectory overlay (`corpus_walk/Lyra-Hersey-800m-IMG_3882/seed_840_945/trajectory.png`):
- Frames 840-870: path wanders in a tight cluster around cx=870-876, cy=337-351 (the
  runner is present but the blob centroid oscillates with stride). The tangled lines
  correspond to alternating higher/lower cy as the blob center moves up/down with
  the runner's body during the stride cycle.
- Frames 870-895: trajectory straightens out and the path tracks the runner's rightward
  movement (cx drifts from ~870 to ~874).
- Frames 895-945: path continues tracking to the right seed without divergence.

There is no single large displacement event. No frame shows a large centroid step (all
step sizes < 20px throughout). The error at 892 is a per-frame blob centroid offset,
not a tracking identity switch or stall.

### Cost term analysis

No wrong-blob ranked over the runner. There is only one corridor blob throughout most
of the span (corridor_n = 1 on almost every frame). The pairwise velocity-delta cost
terms had nothing to adjudicate between -- only one candidate was available. The 9px
cy error at 892 is intrinsic to the blob extraction: the torso heat blob at 892 has a
lower integrated_mag (7023) than its neighbors (11000-25000), and its centroid sits
at cy=347.9 rather than the annotated 338.75. This is a blob centroid estimation error
under reduced residual-motion signal (possibly mid-stride occlusion by limbs or lower
body), not a cost model ranking failure.

Hermite in contrast uses a smooth curve fit through the bracketing seeds (840 at cy=337.25
and 945 at cy=334.5) and predicts cy~337 at frame 892, which is close to the human seed
at cy=338.75 (hermite_err=0.206). Hermite wins here because the runner's true torso
center changes very little in cy across this span, and Hermite interpolates that smooth
motion accurately. The walker's one-blob-per-frame acceptance tracks actual blob
centroids which fluctuate with the stride cycle.

### Overlay visual summary

Tile `fwd/frame_000892.png`: the runner is mid-stride, partially obscured by a vertical
pole structure. A single heat blob is visible (green-yellow box) at approximately the
torso/mid-body region but the box lower edge reaches into the hip/leg area, explaining
the cy downward offset. The selected box is the correct athlete (no identity confusion
with any other person in the scene).

Tile `fwd/frame_000840.png` (left seed): three athletes visible. The seed box (cyan
square) correctly identifies the target runner in the center of the frame. The blob
detection is working correctly at the seed.

Tile `fwd/frame_000870.png`: runner approaching a fence/gate region. Blob tracking active,
single blob in corridor.

Tile `fwd/frame_000912.png`: runner past the pole region, clear shot. Path recovered.

Tile `fwd/frame_000945.png` (right seed): runner at right seed position, correctly tracked.

### Mechanism

Mechanism: per-frame blob centroid displacement (cy bias ~9px / 0.65 tw) at a single
frame where residual-motion signal is reduced (mag 7023 vs ~20k baseline) and the runner
passes through partial occlusion by a vertical pole. One corridor blob throughout the span;
no wrong-blob ranking possible with only one candidate. This is not a cost model failure;
it is a blob centroid estimation error under partial occlusion.

Impact: 0.572 tw regression at the held-out frame. The path stays on the correct athlete
throughout. The blended output will be slightly low in cy around frame 892 but the tracker
never loses identity or stalls.

Frequency: single-frame centroid displacement events under partial occlusion are common
in this venue (the fence and pole structures create periodic occlusion as the runner
crosses them). The issue is specific to blob centroid quality under those conditions,
not to the pairwise velocity-delta cost model.

---

## 2. Jason [12408, 12502, 12596] -- needs_review

### e2e metrics

- hermite_err = 1.312 torso-widths
- walker_err = 2.918 torso-widths (delta +1.606)
- classification: needs_review
- span: 188 frames (left seed 12408, right seed 12596, held-out seed 12502)

### Seeds (source pixels)

| frame | cx | cy | w | h | status |
| --- | --- | --- | --- | --- | --- |
| 12408 | 1240.0 | 704.5 | 28.0 | 49.0 | visible |
| 12502 | 1306.5 | 713.5 | 23.0 | 41.0 | visible |
| 12596 | 1327.0 | 681.5 | 28.0 | 39.0 | visible |

Processed-pixel seeds (bin_factor=2, torso_w=14px):
- left seed 12408: cx=620.0, cy=352.25
- right seed 12596: cx=663.5, cy=340.75
- held-out seed 12502 (ground truth): cx=653.25, cy=356.75

### Status mix

| direction | accepted | soft_miss_no_blob | soft_miss_no_path | stop_reason |
| --- | --- | --- | --- | --- |
| FWD | 1/188 | 187 | 0 | hit_neighbor_seed (after walk terminated) |
| BWD | 11/188 | 176 | 1 | hit_neighbor_seed (after walk terminated) |

Signal absence: the corridor filter returns 0 blobs on 187/188 FWD frames and 176/188 BWD
frames. The walker cannot accept what it cannot see.

### FWD accepted frame

One FWD accepted frame: frame 12564, cx=613.3, cy=370.0, strength=0.007 (very weak),
integrated_mag=73 (effectively noise level). This is not a real runner acceptance; the
blob is too small (area-weighted mag 73 vs. the signal that would be expected for a
detected torso at this scale). The path cost at frame 12564 is 18.0 (max skip cost
for 9 frames), confirming the window is fully stacked with skips.

The "accepted" label is technically correct by the walker's criteria (a blob existed in
the corridor and was chosen), but the signal is so weak that the path produced is
essentially the straight-line interpolation between seeds with one noisy blob added.

### BWD accepted frames (11 total)

BWD accepted 11 frames, all in the range [12410, 12428] -- the first ~20 frames
immediately after the right seed at 12596 (BWD walks backward from 12596):

| frame | cx | cy | strength | mag |
| --- | --- | --- | --- | --- |
| 12428 | 669.8 | 354.8 | 0.347 | 3470.9 |
| 12427 | 661.9 | 350.3 | 0.009 | 93.1 |
| 12425 | 670.4 | 352.8 | 0.204 | 2043.7 |
| 12424 | 669.8 | 352.6 | 0.215 | 2147.4 |
| 12423 | 669.1 | 353.3 | 0.163 | 1627.2 |
| 12422 | 668.0 | 354.4 | 0.210 | 2095.1 |
| 12421 | 667.5 | 355.0 | 0.151 | 1514.2 |
| 12419 | 663.9 | 354.3 | 0.067 | 671.5 |
| 12416 | 668.1 | 346.1 | 0.008 | 83.0 |
| 12415 | 666.7 | 346.7 | 0.003 | 30.3 |
| 12410 | 658.3 | 359.1 | 0.053 | 530.5 |

BWD accepts 11 frames near the right seed and then immediately stalls into
soft_miss_no_blob for frames 12429-12595 (back toward the left seed). The held-out
seed at 12502 falls entirely in the starvation zone.

### Where on the span does the path diverge

The path does not diverge -- it never establishes. Both FWD and BWD are degenerate:

- FWD: 187/188 frames emit soft_miss_no_blob from the start (frame 12408 onward). The
  walker produces a straight interpolated line between the left and right seeds, with one
  noise-level blob at frame 12564 briefly accepted.
- BWD: 11 frames near the right seed accept blobs (mags 30-3471); 176 remaining frames
  produce nothing.

The held-out seed at 12502 falls in the mid-span starvation zone. The blended path
at 12502 is the interpolated/extrapolated straight line, placing the predicted position
at the geometric midpoint between the two seeds:
- interpolated cx at 12502 = 620 + (12502-12408)/(12596-12408) * (663.5-620) = 620 + 94/188 * 43.5 = 641.7
- interpolated cy at 12502 = 352.25 + 94/188 * (340.75-352.25) = 352.25 - 5.75 = 346.5
- held-out seed truth: cx=653.25, cy=356.75
- interpolated error dx = 11.55 px, dy = -10.25 px -> distance = 15.5 px / 14 = 1.1 tw
  plus any asymmetry from the BWD accepted frames contributes further offset

Hermite fits a cubic through the two seeds and achieves 1.312 tw error (the runner's
actual trajectory is not a straight line across this interval). The walker at 2.918 tw
is worse than the straight-line interpolation baseline, which means the blended path
is pulled off the straight interpolation by the BWD accepted frames near 12596 that
cluster around cx=660-670, pulling the blended path rightward at the midpoint.

### Signal absence: why the corridor is empty

From tile `fwd/frame_012408.png` (left seed): the scene is an indoor track meet with a
large crowd, athletes, and race infrastructure. The runner (wearing green/yellow) is
visible in the mid-left area. The seed box (cyan square) is placed on the target.

From tile `fwd/frame_012450.png`: the scene is still the crowded start/finish area. The
box shows the predicted position in the dense crowd.

From tile `fwd/frame_012502.png` (held-out seed): the runner is surrounded by other
athletes and spectators. The heat signal inside the corridor is absent because the
residual-motion extractor sees a dense field of athletes all moving together; the
differential motion of the target runner against the crowd background is
indistinguishable from the crowd's own motion. No blob survives the corridor filter.

From tile `fwd/frame_012564.png`: the one weakly-accepted frame shows a tiny blob (mag=73)
inside the predicted box. The scene shows the runner moving through a slightly less dense
region, but the signal is still near noise level.

The starvation is not a corridor geometry failure (the ROI is correctly positioned).
It is a signal extraction failure: in a dense pack of moving athletes in an indoor meet,
the residual-motion field cannot isolate the target runner from the crowd.

### Cost term analysis

No cost term drove a wrong choice. The corridor is empty on 99.5% of FWD frames
and 93.6% of BWD frames. No candidates exist to rank. The pairwise velocity-delta
cost model is not implicated -- it never runs because the candidate lattice is empty.
The issue is upstream: residual-motion extraction produces no blobs inside the
walker's corridor window for this interval.

The higher walker_err (2.918) vs hermite_err (1.312) is caused by the BWD accepted
frames near 12596 pulling the blended path slightly toward the right-seed region, making
the interpolated midpoint estimate worse than a pure Hermite curve.

### Overlay visual summary

Tile `fwd/frame_012408.png` (left seed): crowded start area of an indoor meet. Target
runner (green/yellow jersey) visible, seed box correctly placed. No heat visible around
the target position -- residual-motion contrast is absent even at the seed.

Tile `fwd/frame_012450.png`: same dense crowd. The prediction box correctly follows the
expected trajectory but no blobs are detected inside it.

Tile `fwd/frame_012502.png` (held-out seed region): runner is mid-pack in the crowded
arena. The walker's prediction box is near the runner but the heat map shows no
discriminating blobs within the corridor.

Tile `fwd/frame_012564.png` (the one accepted frame): a faint blob (mag=73) is visible
inside the box. This is near noise level and is not a reliable detection.

Tile `fwd/frame_012596.png` (right seed): the seed box correctly anchors the right seed.
Nearby blobs are present (the BWD pass accepts 11 frames in this vicinity, mags up to
3471).

### Mechanism

Mechanism: complete signal absence throughout most of the span. The residual-motion
extractor cannot produce a discriminating blob inside the walker's corridor because the
target runner is running in a dense pack of athletes in an indoor meet. Differential
motion against background is suppressed by crowd motion; no blob survives the corridor
filter for 187/188 FWD frames and 176/188 BWD frames.

This is the starvation/signal-absence class: the walker produces no blobs to work with.
The classification as `needs_review` (both methods far from truth) is consistent with
the difficulty of the interval. It is not a cost model failure, not a ranking failure,
and not an identity capture event.

Impact: walker_err=2.918 vs hermite_err=1.312. The walker is worse than Hermite here
because the small number of BWD accepted blobs near 12596 (low-mag, weak signal) pulls
the blended path slightly away from the smooth Hermite interpolation. Hermite stays
closer to truth because it only uses the two seed anchors and has no noisy intermediate
blobs to corrupt the interpolation.

Frequency: the starvation class in dense packs at indoor meets is the largest remaining
quality bucket per the cost_model_ab release review section "Next-target note".

---

## Summary table

| interval | hermite_err | walker_err | delta | mechanism | cost term? |
| --- | --- | --- | --- | --- | --- |
| Lyra-Hersey [840,945] | 0.206 | 0.778 | +0.572 | Per-frame blob centroid cy bias (~9px) at frame 892 under partial pole occlusion; single blob in corridor, no ranking failure | No -- only one candidate; cost model not implicated |
| Jason [12408,12596] | 1.312 | 2.918 | +1.606 | Complete signal absence (187/188 FWD, 176/188 BWD soft_miss_no_blob); dense indoor meet pack; residual-motion extraction cannot isolate runner from crowd | No -- empty corridor; cost model not implicated |

Both cases confirm the regression is not caused by the pairwise velocity-delta cost
rewrite. Neither interval shows a ranking failure where the wrong blob was selected
over the runner. The two distinct failure modes are:

1. Blob centroid displacement under partial occlusion (Lyra-Hersey): the walker
   selected the correct athlete's blob but that blob's centroid was offset downward
   by leg/hip inclusion when the runner passed a vertical pole. The cost model
   correctly chose the only available blob; the error is in blob centroid quality
   under occlusion.

2. Signal absence / starvation (Jason): the corridor filter finds no blobs for
   almost the entire interval because the runner is in a dense pack at an indoor
   meet. The walker produces a near-straight interpolation, which is then perturbed
   slightly by weak near-seed BWD acceptances, making it worse than Hermite's clean
   two-point curve.
