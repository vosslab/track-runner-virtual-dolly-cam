# Blob walk v2 check 8 identity jumps

Workstream artifact for validation plan check 8 (claim L).
Plan reference: [blob_walk_v2_validation_plan.md](../active/blob_walk_v2_validation_plan.md).
Audit reference: [blob_walk_v2_implementation_audit.md](../audits/blob_walk_v2_implementation_audit.md).

Date: 2026-06-10.

## Claim L (from audit)

Viterbi transitions involving a skip node cost flat `SKIP_COST = 2.0` with no
geometric check (`walk_viterbi.py` lines ~191-192). The displacement cap
(`max_jump_px`) is only applied when both endpoints are real blobs. A
skip-to-blob or blob-to-skip transition bypasses the cap entirely. This creates
a structural hole: a path can accumulate unbounded position jumps across skip
frames, potentially "teleporting" to a distant blob after a gap.

Claim L asks: is this hole exercised in practice?

## Method

Source: 4 seed-to-seed intervals walked under the baseline gate
(`tests/e2e/e2e_blob_walk_baseline.py`), 2 FWD + 2 BWD passes per interval,
8 passes total. Videos: Conant-4x400 (2 intervals), Jason-3200m-sectionals
(2 intervals). Lyra-Wheeling excluded per task boundary (live stride bug).

Command to produce the walk output:

```
source source_me.sh && python3 tests/e2e/e2e_blob_walk_baseline.py walk \
    --output-dir /tmp/check8_walk_output
```

Analysis method: for each pass, extract accepted-frame positions (blob centroid
from `cand_cx`/`cand_cy`). Compute displacement in torso-width units between
consecutive accepted-frame pairs. Classify each step as skip-bridging (one or
more intervening frames have a skip status: `soft_miss_no_blob`,
`soft_miss_no_path`, `interpolated`, `extrapolated`) vs between-accepts (no
skip frames between the two accepted frames).

Pool all steps across 8 passes; compute P50/P90/P95/P99 and max.
Identity-jump threshold: pooled P99 + 0.3 W (per validation plan check 8).
Count events exceeding the threshold; classify as skip-bridging or not.

Note: the debug CSV records `cand_cx`/`cand_cy` (blob centroid) only for
accepted frames; miss frames have blank position columns. The interpolated
position the walker emits for skip frames (stored in `direction_path`) is not
recoverable from the verdict CSV alone. The analysis therefore measures
accepted-to-accepted steps only, which is the correct scope for the Viterbi
geometry hole: the hole is in the skip-to-blob Viterbi transition, and the
skip-bridging steps here measure the resulting displacement after that
uncapped transition.

## Corpus summary

| Pass | rows | accepted | skip | steps |
| --- | --- | --- | --- | --- |
| Conant seed_1080_1111 FWD | 31 | 0 | 31 | 0 |
| Conant seed_1080_1111 BWD | 31 | 4 | 27 | 2 |
| Conant seed_1296_1327 FWD | 31 | 30 | 1 | 28 |
| Conant seed_1296_1327 BWD | 31 | 23 | 8 | 21 |
| Jason seed_564_583 FWD | 19 | 0 | 19 | 0 |
| Jason seed_564_583 BWD | 19 | 2 | 17 | 0 |
| Jason seed_602_629 FWD | 27 | 24 | 3 | 22 |
| Jason seed_602_629 BWD | 27 | 11 | 16 | 9 |
| TOTAL | 216 | 94 | 122 | 82 |

Skip rate across all real frame-rows: 56.5% (122 / 216). High skip prevalence
in bootstrap intervals (seed_1080_1111 FWD: 100% skip; seed_564_583 FWD: 100%
skip) is consistent with the known bootstrap-stall bug documented in
`TRACK_RUNNER_DESIGN.md`.

## Displacement distribution

Pooled across all 82 accepted-to-accepted steps:

| Metric | Value (torso widths W) |
| --- | --- |
| N steps | 82 |
| mean | 0.125 W |
| P50 | 0.089 W |
| P90 | 0.231 W |
| P95 | 0.374 W |
| P99 | 0.578 W |
| max | 0.614 W |

Histogram:

```
     0-0.1 W:   49  (59.8%)
   0.1-0.2 W:   20  (24.4%)
   0.2-0.3 W:    7  ( 8.5%)
   0.3-0.5 W:    3  ( 3.7%)
   0.5-1.0 W:    3  ( 3.7%)
   1.0-2.0 W:    0  ( 0.0%)
   2.0-5.0 W:    0  ( 0.0%)
      5.0+ W:    0  ( 0.0%)
```

### Skip-bridging steps (7 total)

Steps where one or more intervening frames have a skip status -- i.e., the
accepted-to-accepted pair crosses at least one Viterbi-skip frame.

| Metric | Value (torso widths W) |
| --- | --- |
| N | 7 |
| mean | 0.193 W |
| P50 | 0.220 W |
| P90 | 0.231 W |
| P99 | 0.231 W |
| max | 0.231 W |

### Between-accepts steps (75 total)

Steps where the two accepted frames are directly adjacent (no skip in between).

| Metric | Value (torso widths W) |
| --- | --- |
| N | 75 |
| mean | 0.119 W |
| P50 | 0.086 W |
| P90 | 0.255 W |
| P99 | 0.581 W |
| max | 0.614 W |

## Identity-jump threshold

Pooled P99 + 0.3 W = 0.578 + 0.30 = **0.878 W**.

## Identity-jump events

Events with displacement > 0.878 W: **0** (zero).

No step in the 8-pass corpus exceeds the threshold. The maximum observed
displacement is 0.614 W (a between-accepts step), well below the 0.878 W
threshold.

## Claim L verdict

**NOT EXERCISED in sample.**

Zero identity-jump events in this 8-pass, 216-frame corpus sample. The
skip-bridging steps that do exist (7 steps, max 0.231 W) are small -- far
below both the identity-jump threshold and the single-frame Viterbi
displacement cap of ~0.80 W at 60 fps.

## Why the hole does not manifest in this sample

The Viterbi corridor filter (`walk_motion_gate.py`) pre-filters blob candidates
to a per-frame acceptance box centered on the last-accepted position. At 60 fps,
the corridor radius is `MAX_RUNNER_SPEED_W_PER_S / fps + BOOTSTRAP_UNCERTAINTY_W
= 30.0/60 + 0.30 = 0.80 W`. Corridor blobs are already constrained to within
~0.80 W of the last anchor. A skip-to-blob transition therefore cannot place
the next accepted blob more than ~0.80 W from the pre-skip anchor, even without
a Viterbi displacement cap -- the corridor acts as a soft outer bound.

The structural hole in the Viterbi transition cost is real: the cap is not
applied, and a sufficiently stale anchor (see audit claim F / anchor-staleness)
could allow a corridor that has drifted far from the runner. But in this 8-pass
sample, with anchor staleness bounded by the window depth and the bootstrap-stall
intervals producing zero accepted frames, no such event occurred.

## Confidence and caveats

- Sample size: 82 steps across 4 intervals and 2 videos. Small. Intervals were
  chosen by the baseline gate (fast, short intervals), not by skip prevalence.
- The bootstrap-stall passes (Conant FWD bootstrap, Jason FWD early) produced
  zero accepted frames and zero steps -- the worst-case skip scenario is not
  represented in step-displacement terms (no next accepted frame to measure).
- The verdict is for this specific sample. A longer-skip-run interval on a
  video with higher scene motion could produce a stale anchor and a larger
  post-skip blob jump. The structural hole remains open.
- No behavior change is implied by this verdict. Per the validation plan stop
  rule, no walker trial starts without the gating claim proven and user approval.

## Artifacts

- Walk output: `/tmp/check8_walk_output` (scratch, not committed).
- Analysis: `_temp_check8_analysis.py` (deleted before handoff per task scope).
