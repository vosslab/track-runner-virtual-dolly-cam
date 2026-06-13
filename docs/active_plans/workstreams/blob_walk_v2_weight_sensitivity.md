# Blob walk v2 Viterbi weight sensitivity

SIDE QUEST: maps which cost terms are load-bearing on the 24-pass subset.

Date: 2026-06-12.

## Purpose

The tuning A/B in `blob_walk_v2_cost_model_ab.md` ran 4 configs on the
fast 24-pass subset (IMG_3830 rows 1-13, IMG_3823 rows 14-24) and got
identical results. This report resolves whether that flatness reflects
genuine insensitivity or an under-tuned range, using extreme configs and
selected-path divergence tracking to isolate load-bearing terms.

## Subset definition

24 passes, IMG_3830 and IMG_3823 only.

IMG_3830 (13 passes):
(247,249), (770,776), (1466,1472), (1624,1656), (1702,1704), (1818,1820),
(1857,1862), (2240,2242), (2400,2404), (2410,2416), (2955,2960), (4028,4031),
(4080,4089)

IMG_3823 (11 passes):
(621,625), (731,741), (806,810), (1047,1066), (1560,1573), (2316,2337),
(2337,2341), (3158,3170), (3614,3655), (3745,3747), (3956,3979)

Total non-seed frames: 237 FWD (237 BWD).

## Method

Runner: `_temp_weight_sensitivity.py` (deleted after run).
Weight injection: `walk_viterbi.set_cost_weights(weights)` called in-process
before each config, `reset_cost_weights_for_tests(None)` between configs.
No config file touched. Each config ran `run_interval_walk` (render_tiles=False)
on all 24 intervals and captured accepted_fraction + per-pass accepted (cx, cy)
positions from the written verdict CSVs.

Divergence: frames where BOTH configs accepted a candidate but the selected
position differed by > 0.5 px in either x or y. Measures path-level
difference, not just aggregate fraction.

Multi-candidate fraction: fraction of FWD accepted frames with >= 2 corridor
candidates. This is the load-bearing denominator: the cost ranking can only
arbitrate when >= 2 candidates compete. Single-candidate frames are
deterministic regardless of weights.

## Config table

| config | DISPLACEMENT | SPEED_DELTA | HEADING_DELTA | OVERSPEED | EVIDENCE_NORM | SKIP_COST |
| --- | --- | --- | --- | --- | --- | --- |
| A defaults | 0.25 | 1.0 | 0.5 | 4.0 | 0.5 | 2.0 |
| B deltas zeroed | 0.25 | 0.0 | 0.0 | 4.0 | 0.5 | 2.0 |
| C evidence zeroed | 0.25 | 1.0 | 0.5 | 4.0 | 0.0 | 2.0 |
| D displacement 1.0 | 1.0 | 1.0 | 0.5 | 4.0 | 0.5 | 2.0 |
| E skip doubled | 0.25 | 1.0 | 0.5 | 4.0 | 0.5 | 4.0 |
| F skip halved | 0.25 | 1.0 | 0.5 | 4.0 | 0.5 | 1.0 |

## Results

### Per-config aggregate (24 passes, FWD direction)

| config | FWD accepted/total | FWD fraction | diverged/common | div fraction |
| --- | --- | --- | --- | --- |
| A defaults | 133/237 | 0.561 | 0/109 | 0.000 |
| B deltas zeroed | 140/237 | 0.591 | 0/109 | 0.000 |
| C evidence zeroed | 134/237 | 0.565 | 0/109 | 0.000 |
| D displacement 1.0 | 132/237 | 0.557 | 0/108 | 0.000 |
| E skip doubled | 140/237 | 0.591 | 0/109 | 0.000 |
| F skip halved | 120/237 | 0.506 | 1/95 | 0.011 |

### Per-interval accepted fraction (all 6 configs, FWD)

| interval | span | A defaults | B deltas | C evidence | D disp | E skip2x | F skip0.5 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [247,249] | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| [770,776] | 6 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| [1466,1472] | 6 | 0.833 | 0.833 | 0.833 | 0.833 | 0.833 | 0.667 |
| [1624,1656] | 32 | 0.719 | 0.719 | 0.719 | 0.719 | 0.719 | 0.656 |
| [1702,1704] | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| [1818,1820] | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| [1857,1862] | 5 | 0.600 | 0.600 | 0.600 | 0.600 | 0.600 | 0.600 |
| [2240,2242] | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| [2400,2404] | 4 | 0.750 | 1.000 | 0.750 | 0.750 | 1.000 | 0.750 |
| [2410,2416] | 6 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.667 |
| [2955,2960] | 5 | 0.200 | 0.200 | 0.200 | 0.200 | 0.200 | 0.200 |
| [4028,4031] | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| [4080,4089] | 9 | 0.889 | 1.000 | 1.000 | 0.889 | 1.000 | 0.444 |
| [621,625] | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| [731,741] | 10 | 0.300 | 0.300 | 0.300 | 0.300 | 0.300 | 0.300 |
| [806,810] | 4 | 0.750 | 0.750 | 0.750 | 0.750 | 0.750 | 0.750 |
| [1047,1066] | 19 | 0.526 | 0.526 | 0.526 | 0.474 | 0.526 | 0.474 |
| [1560,1573] | 13 | 0.692 | 1.000 | 0.692 | 0.692 | 1.000 | 0.692 |
| [2316,2337] | 21 | 0.286 | 0.286 | 0.286 | 0.286 | 0.286 | 0.286 |
| [2337,2341] | 4 | 0.250 | 0.250 | 0.250 | 0.250 | 0.250 | 0.250 |
| [3158,3170] | 12 | 0.667 | 0.750 | 0.667 | 0.667 | 0.750 | 0.500 |
| [3614,3655] | 41 | 0.366 | 0.366 | 0.366 | 0.366 | 0.366 | 0.366 |
| [3745,3747] | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| [3956,3979] | 23 | 0.261 | 0.261 | 0.261 | 0.261 | 0.261 | 0.217 |

### Multi-candidate fraction

| video | total accepted | >=2 corridor cands | fraction |
| --- | --- | --- | --- |
| IMG_3830 | 66 | 53 | 0.803 |
| IMG_3823 | 67 | 56 | 0.836 |
| OVERALL | 133 | 109 | 0.820 |

82.0% of accepted frames had >= 2 corridor candidates. The cost ranking
arbitrated among real competing candidates on the majority of accepted frames.

Single-candidate accepted frames: 24/133 = 18.0% were deterministic regardless
of weights.

## Interpretation

The validation flatness (configs 1-4 in the original A/B) is NOT explained by
single-candidate dominance. At 82% multi-candidate density, the cost ranking
is actively arbitrating on most frames, yet four configs produced identical
accepted_fraction. The explanation is that the original configs 2-4 only
varied one term at a time by 2x (evidence 0.5->1.0, speed_delta 1.0->2.0,
skip 2.0->1.0); the terms compete and the 2x nudges were insufficient to
shift rankings at candidate densities typical of this subset.

SKIP_COST is the only load-bearing term on this subset: halving it (F,
1.0) drops accepted_fraction from 0.561 to 0.506 (-9.8%) and produces 1
diverged frame. Doubling it (E, 4.0) raises accepted_fraction to 0.591
(+5.1%) with zero path divergence (different accept/skip decisions, same
candidate choices when accepting). A lower skip cost makes the all-skip
path relatively cheaper, which decreases accepted frames; a higher skip
cost makes skipping relatively expensive, which forces more accepts.

Zeroing the delta terms (B) raised accepted_fraction by +5.1% with zero
path divergence -- exactly matching skip-doubled. This suggests that on
this subset, the delta terms slightly discourage acceptance by adding
transition cost on multi-candidate contested frames, and removing them is
equivalent in outcome to raising skip pressure. Evidence zeroed (C) changed
almost nothing (+0.4%), confirming the normalized evidence term is a
near-neutral tie-breaker on this subset. Displacement 4x (D) changed
almost nothing either (-0.4%).

The primary conclusion: SKIP_COST drives accept/skip decisions on this
subset; SPEED_DELTA and HEADING_DELTA provide marginal trajectory-consistency
pressure that weakly discourages some accepts. EVIDENCE_NORM and
WEIGHT_DISPLACEMENT are near-inert at realistic candidate densities in
short, high-confidence intervals. For future tuning, Jason and Lyra-Hersey
(long intervals, contested low-confidence tracking) are the correct primary
surface; the IMG_3830/IMG_3823 fast subset is structurally insensitive to
ranking terms because most of its intervals are short and high-confidence.
