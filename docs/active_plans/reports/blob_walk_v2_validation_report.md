# Blob walk v2 validation report

Date: 2026-06-10 / 2026-06-11.

## Context

The implementation audit
([../audits/blob_walk_v2_implementation_audit.md](../audits/blob_walk_v2_implementation_audit.md))
identified twelve assumptions (A through L) about walker behavior that were
likely-but-unverified. The validation plan
([blob_walk_v2_validation_plan.md](../../archive/blob_walk_v2_validation_plan.md))
scheduled nine checks (Check 0 through Check 8) to move each claim to proven,
refuted, or still unknown. This report is the plan's deliverable: it records the
verdict for each claim, synthesizes cross-cutting findings, and provides an
evidence-ranked list of candidate first fixes.

The only code change during the validation period was the P15 telemetry fix
(Check 1): the walk debug CSV `path_cost` column was corrected from a
whole-window total mis-stamped on every row to a truthful per-frame column
(`path_step_cost`), and `window_head_frame` was added. A strict field-wise
decision-equality gate was applied before merging: all 8 baseline passes (2
stall FWD + 2 stall BWD + 4 healthy) produced byte-exact decision columns
before and after the fix (status, positions, accepted counts, fallback signal).
`SCHEMA_VERSION` advanced from 11 to 12 (metadata-only; not added to
`GEOMETRY_AFFECTING_SCHEMAS`). No selection logic, anchor logic, or fallback
condition was touched.

---

## Claims verdict table

| Claim | Summary | Verdict |
| --- | --- | --- |
| A | Rejected blobs on stall intervals are the runner's blobs | REFUTED |
| B | Evidence term dominates Viterbi path selection | MIXED |
| C | Regressed-interval root-cause split | RANKING-DOMINANT |
| D | Limbs merge into one broad runner-body blob | REFUTED (conditional) |
| E | Within-body vertical centroid jitter | OBSERVED |
| F | Oldest-frame emission contributes to quality loss | CONFIRMED (structural) |
| G | Linear extrapolation inferior to hold-last for stale anchor | UNDETERMINED |
| H | Gate redesign direction | INFORMED, NOT PROVEN |
| I | Anchor staleness is the operative starvation path beyond bootstrap | CONDITIONALLY CONFIRMED |
| J | Bootstrap-accept masking of Hermite fallback | OBSERVED (1 of 26 passes, 3.8%) |
| K | Stride-2 overrun on 120-fps video | OBSERVED FAILURE |
| L | Identity jumps across skip-bridging Viterbi transitions | NOT EXERCISED in sample |

---

## Verdict groups

### PROVEN (measured, evidence sufficient for a strong conclusion)

| Claim | Verdict label | One-line summary |
| --- | --- | --- |
| A | REFUTED | Rejected blobs are background athletes, not the runner; widening box would not recover runner signal. |
| C | RANKING-DOMINANT | 17/34 pure ranking + 5/10 selection-leaning mixed = 65% effective ranking share; 35% starvation share. |
| D | REFUTED (conditional) | Limb merge holds for large runners (~30 px); small runners (~11 px) resolve 4-6 distinct blobs. Size-dependence noted. |
| E | OBSERVED | Within-body vertical centroid jitter confirmed in Jason; 0.26 flips/step, |delta ncy| P95 0.211. |
| F | CONFIRMED (structural) | Anchor is always 9+ frames stale in steady state; quality impact conditional on image-space drift. |
| I | CONDITIONALLY CONFIRMED | Anchor staleness present at every rejection; two distinct sub-types confirmed (drift stall vs signal-absence stall). |
| J | OBSERVED | Bootstrap-accept masking confirmed in 1 of 26 passes (3.8%); code gap identified in interval_solver.py. |
| K | OBSERVED FAILURE | Stride-2 termination bug live on Lyra-Wheeling interval #164; walk overruns into adjacent intervals. |

### REFUTED (see individual claim sections for qualifiers on D)

Claims in this group: A, D (conditional).

### STILL UNKNOWN (insufficient data, path not exercised, or comparison indeterminate)

| Claim | Verdict label | Why unknown |
| --- | --- | --- |
| B | MIXED | Evidence term dominates per-node cost; dynamic FWD selection dominated by spatial consistency (11.5% ev_match); BWD behaves differently (88.9% ev_match). Dominance depends on direction. |
| G | UNDETERMINED | `extrapolated` status never executed in 24 available CSVs; empirical comparison indeterminate; theoretical analysis supports linear but effect surface is negligible. |
| H | INFORMED, NOT PROVEN | Direction is design-aligned but no trial run; deprioritized by A + C evidence. |
| L | NOT EXERCISED in sample | Zero identity-jump events in 82 steps; structural Viterbi hole is real but corridor filter constrains candidates. |

---

## Per-claim sections

### Claim A: rejected blobs are the runner's blobs

Source: [../workstreams/blob_walk_v2_check2_rejected_overlays.md](../workstreams/blob_walk_v2_check2_rejected_overlays.md)

**Verdict: REFUTED**

The rejected blobs on both diagnosed stall intervals are background motion from
other athletes, not the target runner's blobs.

Jason 564-583 FWD: 195 rejected blobs across 20 frames. Only 0.5% (1 blob) fall
within 1.0 torso-width of the seed reference; 5.1% within 2.0 W; median distance
5.97 W. The Jason acceptance box is 3.2 x 7.9 processed px (torso width = 3.25
proc px, bin=2 from 6.5 source px). Background athletes produce strong blobs at
5-24 W; runner signal is below the DoG detection threshold inside the 3.2 px box.

Conant 1080-1111 FWD: only 2 blobs found in the tight ROI across all 31 frames
(both at 2.37 W). A wide-ROI probe (pad=200) found blobs on all frames, but at
7-24 W corresponding to other athletes.

Widening the acceptance box would not recover the runner's signal on these
intervals. The stall root causes are:
- Conant: runner is at the top edge of frame (cy_proc = 24 px); residual inside
  the tight ROI is at noise level on 30/31 frames.
- Jason: 3 proc-px torso; DoG diameter 2.3 px is at or below reliable detection
  range; background athletes produce stronger blobs far from the runner.

**Discrepancy note:** the earlier stall-diagnosis doc
([../audits/fwd_zero_coverage_diagnosis.md](../audits/fwd_zero_coverage_diagnosis.md))
states "24 of 31 Conant frames DO extract raw motion blobs (max integrated_mag
~2700)." Check 2 found only 2 blobs on 1 frame using the production walker's
exact tight ROI. The discrepancy indicates the diagnosis probe used a wider ROI
or a different extraction path. Check 2's measurements used the production
walker's own ROI formula exactly (accept box + `max(20, seed_w)` pad); blobs at
wider radii belong to background athletes at 8-24 W.

---

### Claim B: evidence term dominates Viterbi path selection

Source: [../workstreams/blob_walk_v2_check6_per_term_cost.md](../workstreams/blob_walk_v2_check6_per_term_cost.md)

**Verdict: MIXED**

Static: evidence cost (WEIGHT_EVIDENCE * integrated_mag) has median -558.52 on
accepted frames; displacement cost has median 0.000. The ratio is effectively
infinite on most frames; evidence magnitude exceeds displacement by 100-1000x
on Conant and 20-50x on Jason.

Dynamic: multi-candidate frames split by direction -- FWD 26 frames (ev_match
3/26 = 11.5%, disp_match 25/26 = 96.2%) vs BWD 18 frames (ev_match 16/18 =
88.9%, disp_match 16/18 = 88.9%). Pooled ev_match = 19/44 (43.2%), disp_match
= 41/44 (93.2%); the pooled ev_match figure obscures the strong direction
asymmetry (11.5% FWD vs 88.9% BWD).

The resolution: per-node evidence dominates the node's own Viterbi cost, but
accumulated transition costs from earlier window frames bias the DP path toward
spatial consistency. Jason FWD -- which has 4-6 competing blobs at limb-level
spacing -- illustrates this most clearly: the walker picks the spatially coherent
trajectory even when a higher-mag blob exists nearby. Evidence normalization as a
standalone fix therefore does not have a guaranteed effect on path selection.

---

### Claim C: regressed-interval root-cause split

Source: [../workstreams/blob_walk_v2_check7_regressed_split.md](../workstreams/blob_walk_v2_check7_regressed_split.md)

**Verdict: RANKING-DOMINANT**

Of the 35 m4 A/B regressed passes (34 complete, 1 Lyra-Wheeling pending):

| Bucket | Count (34 done) | Pct of 34 classified |
| --- | --- | --- |
| ranking-driven (mean empty-lattice fraction < 0.2) | 17 | 50% |
| mixed (0.2 to 0.5) | 10 | 29% |
| starvation-driven (mean empty-lattice fraction > 0.5) | 7 | 21% |
| pending (Lyra-Wheeling 754-981) | 1 | -- |

All percentages in this section are of the 34 classified passes (the 1 pending
Lyra-Wheeling pass is excluded from the denominator); the source artifact's
header line quotes 49% because it uses all 35 passes as the denominator.

The 35th (Lyra-Wheeling 754-981) is still pending decode; even if it is
starvation, ranking remains the largest single bucket (17 vs at most 8).

Secondary finding: `soft_miss_no_path` is near zero everywhere (max 4 on any
pass). When candidates are present the walker selects one. Ranking failures are
wrong-blob-wins events (limb or background blob outscores torso blob), not
displacement-cap rejections. The acceptance box and cap are not the primary
drivers of these regressions.

Starvation is concentrated in IMG_3823 (6 intervals) + Conant (1 interval),
suggesting a scene or runner-size dependency.

Mixed-bucket addendum (2026-06-10): the 10 mixed passes were re-walked with
per-frame status capture. `accept_on_nonempty >= 0.88` on all 10 passes --
when candidates are present the walker accepts one (wrong-blob-wins, not
path-rejection). Sub-class: starvation-leaning=5, selection-leaning=5.
Effective ranking-driven share: 22/34 classified passes (65%); effective
starvation share: 12/34 (35%). Position-verification caveat: wrong-blob-wins
diagnosis is inferred from accept_on_nonempty and sub-class thresholds, not
confirmed by per-frame position overlay.

See [../workstreams/blob_walk_v2_check7_regressed_split.md](../workstreams/blob_walk_v2_check7_regressed_split.md)
completion addendum for per-pass diagnosis table.

---

### Claim D: limbs merge into one broad runner-body blob

Source: [../workstreams/blob_walk_v2_check5_normalized_cy.md](../workstreams/blob_walk_v2_check5_normalized_cy.md)

**Verdict: REFUTED (conditional; size-dependent)**

The claim holds for Conant (torso height ~30 px, proc): 97-100% of frames with
candidates have exactly 1 blob near the reference. The DoG band-pass merges the
large runner into a single blob.

The claim does NOT hold for Jason (torso height ~11 px, proc): every frame with
candidates has 4-6 distinct blobs within 1 torso-width of the reference, covering
the full vertical extent of the runner. These are not background blobs; they are
limb and trunk segments resolved by the DoG at small scale.

Merge is not a universal pipeline property. The crossover scale is somewhere
between 11 px and 30 px torso height.

---

### Claim E: within-body vertical centroid jitter

Source: [../workstreams/blob_walk_v2_check5_normalized_cy.md](../workstreams/blob_walk_v2_check5_normalized_cy.md)

**Verdict: OBSERVED**

Global ncy statistics across 88 accepted frames (6 non-empty pass directions):

- ncy range: [-0.748, 0.298]
- `|delta ncy|` mean 0.066; P95 0.211; max 0.384 torso heights
- Alternation rate: 21 flips / 82 steps = 0.26 flips/step

The jitter is concentrated in Jason. Jason/seed_602_629/FWD (4-6 competing blobs
per frame) shows a 0.384-torso-height single-step jump at frames 608-614 and an
alternation rate of 0.32 flips/step. Viterbi switches between the lower-runner
blob cluster (ncy ~ -0.4) and upper-runner cluster (ncy ~ +0.2) across windows.

Conant shows milder behavior: ncy range 0.35-0.45 torso heights, alternation
0.10-0.21, step deltas mostly below 0.10. Consistent with stride-motion
displacement of a single merged blob, not between-blob switching.

r(ncy, integrated_mag) varies in sign per pass (0.380 Conant FWD; -0.491 Jason
FWD); no universal direction linking blob vertical position to motion strength.

---

### Claim F: oldest-frame emission contributes to quality loss

Source: [../workstreams/blob_walk_v2_check4_anchor_lag.md](../workstreams/blob_walk_v2_check4_anchor_lag.md)

**Verdict: CONFIRMED (structural)**

Steady-state anchor age at observation time is exactly 9 frames (median=9.0,
P90=9.0, max=9 on Conant_1296_1327_FWD). This matches the theoretical minimum
for the oldest-frame emission design (window depth = WALKER_WINDOW_FRAMES = 9).

In passes with sparse accepts (BWD passes, partially-stalled BWD) anchor age
rises to 10-22 frames as the anchor goes un-updated. For the two FWD stall cases
(Conant 1080-1111 FWD, Jason 564-583 FWD) anchor age grows to 17-30 frames.

The quality impact is conditional on image-space drift: Conant 1080-1111 FWD
accumulates 2.35 TW of position drift (anchor fully mispositioned from frame
1087 onward); Jason 564-583 FWD stays within 0.527 TW throughout (runner barely
moves in image space) so anchor age alone does not drive that stall.

---

### Claim G: linear extrapolation inferior to hold-last for stale anchor

**Verdict: UNDETERMINED**

Source: [../workstreams/blob_walk_v2_checkg_extrapolation_replay.md](../workstreams/blob_walk_v2_checkg_extrapolation_replay.md)

Offline replay performed on 24 available walk debug CSVs (8 unique
video/interval/direction combinations from check4, check5, and check8 runs).

Key findings:

1. **Zero affected frames in all logs.** Audit P6 is confirmed: `extrapolated`
   and `interpolated` statuses have zero occurrences across 366
   `soft_miss_no_blob`, 282 `accepted`, and 24 `after_walk_terminated` frames.
   Both statuses are structurally limited to the end-of-walk flush.

2. **P9 spec deviation confirmed.** `walk_status.py` lines 114-116 implement
   a HOLD for `extrapolated` ("Simple hold: use last accepted position")
   while the spec requires linear extension of the last two accepts. The
   `interpolated` case (lines 101-109) is already correctly implemented.

3. **Empirical comparison indeterminate.** With the seed-to-neighbor linear
   interpolation as reference, hold appears better than linear in 10 of 10
   hypothetical replay scenarios (hold mean 1.07 TW vs linear mean 1.19 TW
   at +1 frame, across 5 qualifying intervals). However, this result is
   an artifact of reference curvature: the reference converges toward the
   neighbor seed while linear extrapolation continues past it. The comparison
   does not cleanly measure position quality.

4. **Synthetic parametric analysis (uniform motion) shows linear better.**
   When the runner follows the reference line exactly, linear achieves
   zero error while hold accumulates 0.22-0.42 TW per frame (59 of 59
   synthetic scenarios: linear wins). This is the expected theoretical result.

5. **Effect surface is negligible at current accepted fractions.** EXTRAP_MAX=2
   caps the affected frames at 2 per pass per interval. At observed velocity
   magnitudes (0.5-2.6 px/frame) and torso widths (6.5-11.0 px), cumulative
   positional degradation from hold vs linear is under 0.8 TW total. The fix
   should be deferred until `extrapolated_count` becomes non-zero in production.

The verdict is UNDETERMINED rather than LINEAR BETTER because the available
logs contain no actual extrapolated frames, and the empirical reference is
contaminated. Theoretical analysis supports linear, but the practical impact
is zero at current accepted fractions.

---

### Claim H: gate redesign direction informed by A and C

**Verdict: DIRECTION INFORMED, NOT PROVEN**

Claim A (REFUTED) shows that widening the acceptance box would admit background-
athlete blobs, not runner signal. Claim C (RANKING-DOMINANT) shows that 50% of
regressions are wrong-blob-wins on non-empty frames, not starvation. Together
these argue against acceptance-box widening as the highest-leverage intervention.
The gate redesign hypothesis is not independently proven; it is deprioritized by
the A + C evidence.

---

### Claim I: anchor staleness is the operative starvation path beyond bootstrap

Source: [../workstreams/blob_walk_v2_check4_anchor_lag.md](../workstreams/blob_walk_v2_check4_anchor_lag.md)

**Verdict: CONDITIONALLY CONFIRMED**

ALL 72 rejections across the 6 non-stall baseline passes occur at anchor_age >= 7
frames at observation time. No rejections occurred with a fresh anchor.

Two causal sub-types are confirmed:

1. Conant 1080-1111 FWD: staleness + runner image-space drift = acceptance box
   mispositioned. 24/31 frames have anchor-to-reference drift > 0.5 TW; drift
   reaches 2.35 TW at frame 1110. Anchor-advance fix would address this sub-type.

2. Jason 564-583 FWD: anchor is staleness-present but runner is near-stationary
   in image space (max drift 0.527 TW). Box is correctly positioned; blobs are
   absent because of centroid-offset geometry (L4 + P11). Anchor-advance fix
   alone would NOT cure this case.

All non-stall misses in the baseline 4-interval sample are `soft_miss_no_blob`
(extraction-level absent), not `soft_miss_no_path` (box-excluded). Zero empty-
lattice rejections observed in the non-stall baseline passes.

---

### Claim J: bootstrap-accept masking of Hermite fallback

Source: [../workstreams/blob_walk_v2_check3_bootstrap_masking.md](../workstreams/blob_walk_v2_check3_bootstrap_masking.md)

**Verdict: OBSERVED (1 of 26 passes, 3.8%)**

Conant `seed_1126_1134` FWD (8-frame interval): bootstrap frame accepted
(accepted_count = 1), all 7 post-bootstrap frames `soft_miss_no_blob`, fallback
gate (`accepted_count == 0`) not satisfied. Shipped path is frozen at seed
position for 7 of 8 frames, strictly worse than pure Hermite. The BWD pass on
the same interval found 3 accepted frames and returned normally.

The code gap: `interval_solver.py` uses `accepted_count == 0` as the fallback
condition; a bootstrap-only walk (accepted_count = 1, post_bootstrap_accepted
= 0) is not caught. Short intervals (8 frames) are more vulnerable: one
bootstrap hit covers the entire "accepted" budget.

26 passes examined across 3 videos and 13 intervals; 4 (15.4%) are true
zero-accept (fallback fired); 21 (80.8%) are normal (accepted >= 2, no fallback).

---

### Claim K: stride-2 overrun on 120-fps video

Source: [../workstreams/blob_walk_v2_check0_stride_overrun.md](../workstreams/blob_walk_v2_check0_stride_overrun.md)

**Verdict: OBSERVED FAILURE**

Lyra-Wheeling-IMG_3912.mkv has fps=119.94, stride=2. Of 148 post-race odd-span
intervals, 147 are high-confidence (not promoted to Stage 4); only 1 is promoted:
interval #164, frames 16588-16591, span=3, tier=fair.

With stride=2, the equality check `frame_f == neighbor_seed_frame` at
`walk_walker.py` line 1027 never fires for a span-3 interval. The FWD pass
overshoots to frames 16590, 16592, 16594, 16596 (3 frames into the next interval)
before the `max_steps_guard = 4` stops it. The BWD pass overshoots to frames
16589, 16587 (2 frames into the previous interval). Confirmed by code analysis;
no direct CSV telemetry was available (debug CSVs absent for this video).

Practical impact is bounded: interval #164 spans only 3 frames (~25 ms at 120
fps); overrun affects at most 4 frames in adjacent high-confidence intervals.

---

### Claim L: identity jumps across skip-bridging Viterbi transitions

Source: [../workstreams/blob_walk_v2_check8_identity_jumps.md](../workstreams/blob_walk_v2_check8_identity_jumps.md)

**Verdict: NOT EXERCISED in sample**

82 accepted-to-accepted steps across 8 passes (216 frame-rows). Maximum observed
displacement: 0.614 W (a between-accepts step). Identity-jump threshold (pooled
P99 + 0.3 W = 0.578 + 0.30 = 0.878 W): 0 events exceed it.

Skip-bridging steps (7 total, max 0.231 W) are smaller than between-accepts steps
on average, not larger. The structural hole is real -- Viterbi's skip-to-blob
transition bypasses the displacement cap -- but the corridor filter
(`walk_motion_gate.py`) pre-constrains candidates to ~0.80 W of the last anchor
at 60 fps, acting as a soft outer bound that prevents large jumps even without
the cap.

Caveat: the bootstrap-stall intervals contribute zero accepted-frame steps; the
worst-case jump scenario (stale anchor + long skip run + recovering blob) is
unrepresented in this sample.

---

## Cross-cutting synthesis

### The starvation story is extraction-level, not gate-level

Claim A (REFUTED) and claim C (RANKING-DOMINANT) converge on a consistent
picture: the Viterbi gate (acceptance box + displacement cap) is not the primary
failure mode. Starvation intervals are concentrated in IMG_3823 (6) and Conant (1)
and are driven by DoG extraction failing to find blobs at small runner scale --
not by the acceptance box excluding runner blobs that exist. Widening the box
would admit background athletes (A refuted); it would not supply runner signal
where extraction already fails.

### Ranking failures are wrong-blob-wins on non-empty frames

The dominant regression class (17 of 34 classified intervals, ranking-driven) is
the walker accepting a limb, foot, or background blob on frames where candidates
are present. The Viterbi cost structure (evidence term 100-1000x larger than
displacement in magnitude, but dynamic selection dominated by spatial consistency)
means the walker is selecting the spatially consistent trajectory at the cost of
occasionally locking onto the wrong body part.

### Honest tension between Check 4 and Check 2

Check 4 (claim I) shows that Conant 1080-1111 FWD stalls because runner
image-space drift accumulates past the acceptance box as the anchor goes stale
(2.35 TW drift by the end of the interval). An anchor-advance fix would address
this sub-type. However, Check 2 (claim A) shows that the blobs outside the box
in that neighborhood are background athletes at 7-24 W -- so widening the box to
capture more candidates would not help, and is separately contraindicated. The
correct intervention for the Conant drift case is advancing the anchor (so the
box follows the runner), not widening the box (which would admit wrong blobs).
The Jason stall is different: anchor is correctly positioned but runner signal is
below detection threshold regardless of box size.

### Two distinct stall sub-types warrant distinct fixes

The two diagnosed FWD stall passes represent fundamentally different failure modes:
Conant is a positional-drift stall (box migrates away from runner), while Jason is
a signal-absence stall (runner too small for reliable DoG extraction). A single
fix cannot address both.

---

## Evidence-ranked candidate first fixes

These are listed by strength of evidence, with one-line rationale each. Fix
directions recorded verbatim from the workstream artifacts are included where the
artifact stated one; they are candidate directions, not approved changes. Every
fix requires its own user-approved plan per the validation plan stop rule before
any behavior change is made.

**Design orientation (user direction 2026-06-11):** the goal is better tracking
quality with LESS gating, not more. Prefer fixes that remove or soften wrong
guards (stride termination equality, bootstrap fallback masking, over-broad cache
bypass) over fixes that add new gates or penalty terms. The claim-A refutation
cautions against naive acceptance-box widening at current extraction quality; it
does not endorse keeping hard exclusion long-term -- replacing hard exclusion with
soft scoring (claim H direction) once candidate supply is understood remains
design-aligned. Check 6's finding that the window DP already favors spatially
coherent paths supports relying on window-level consistency over hard per-frame
gates.

### Tier 1: observed concrete bugs (code-confirmed failure in production data)

1. **P12: stride termination fix (`walk_walker.py` line 1027)**
   Replace the equality check `frame_f == neighbor_seed_frame` with an
   overshoot-safe comparison (`>= 0` in direction of travel). Lyra-Wheeling
   interval #164 confirms the bug is live (OBSERVED FAILURE, Check 0).

2. **P10: bootstrap-accept fallback fix (`interval_solver.py`)**
   Extend the Hermite fallback condition from `accepted_count == 0` to
   `post_bootstrap_accepted == 0` (or equivalent). Conant `seed_1126_1134` FWD
   confirms masking occurs in real data (OBSERVED, Check 3, 3.8% of passes in
   sample).

### Tier 2: evidence-supported improvements

3. **Ranking-quality improvement (wrong-blob-wins)**
   17 of 34 classified regressions are ranking-driven: a limb or background
   blob outscores the torso blob on raw `integrated_mag`. Mixed-bucket diagnosis
   (Check 7 addendum) sharpens this to 22/34 effective ranking share (65%),
   making ranking quality the leading behavior-trial area after the observed bugs
   (P12 + P10) are fixed. Claim B (MIXED) shows dynamic selection is already
   dominated by spatial consistency for FWD passes; a targeted approach
   (blob-merging pass at runner scale for small runners, or scale-adaptive
   proximity term) may reduce the wrong-blob-wins rate. This is the
   highest-count regression class (Claims C + D + E).

4. **Small-runner extraction scale (DoG diameter)**
   Claims D and E show that at Jason's torso scale (11 px proc), the DoG
   resolves 4-6 distinct limb blobs instead of one runner blob. A scale-adaptive
   DoG diameter (or a post-extraction merge step at runner scale) would reduce
   the competing-candidate count before Viterbi, attacking the wrong-blob-wins
   problem at its source.

### Tier 3: explicitly deprioritized (evidence against or insufficient return)

5. **Acceptance-box widening (deprioritized by Claim A)**
   Widening the box would admit background-athlete blobs at 5-24 W, not runner
   signal. Claim A (REFUTED) directly contraindicated this approach. Do not
   implement without new evidence that runner signal exists at wider radii.

6. **Skip-cap (Viterbi) change (deprioritized by Claim L)**
   Claim L (NOT EXERCISED) found zero identity-jump events in 82 steps. The
   structural hole is real but the corridor filter acts as a soft outer bound.
   Not a first-fix candidate.

7. **Evidence normalization as standalone fix (deprioritized by Claim B mixed)**
   Static evidence magnitude dominates, but dynamic FWD pass selection already
   tracks min-displacement (not max-evidence) at 96.2% of multi-candidate frames.
   Normalization alone is unlikely to change path selection in the cases that
   matter most (Jason FWD). Requires its own scoped trial if pursued.

---

## Open items

1. **Claim G: UNDETERMINED (offline replay completed, insufficient data for
   strong verdict).** The `extrapolated` status has zero occurrences in all 24
   available walk debug CSVs; the code path is never executed at current accepted
   fractions. Theoretical analysis (uniform motion) supports linear extrapolation
   over hold, but the empirical comparison is contaminated by reference-line
   curvature. Effect surface is negligible (at most 2 frames per pass per
   interval, EXTRAP_MAX=2). Spec deviation (P9) confirmed but deferred; fix
   when `extrapolated_count` becomes non-zero. See
   [../workstreams/blob_walk_v2_checkg_extrapolation_replay.md](../workstreams/blob_walk_v2_checkg_extrapolation_replay.md).

2. **Mixed bucket diagnosis complete with position-verification caveat**: the 10
   mixed passes were re-walked with per-frame status capture (Check 7 addendum,
   2026-06-10). `accept_on_nonempty >= 0.88` across all passes; sub-class
   breakdown is starvation-leaning=5, selection-leaning=5. Effective ranking share
   is 65%, effective starvation share is 35%. The wrong-blob-wins diagnosis is
   inferred from acceptance rates and sub-class thresholds; per-frame position
   overlay would confirm which blob was accepted on each non-empty frame.

3. **Lyra-Wheeling regressed interval #35 UNDETERMINED**: Lyra-Wheeling 754-981
   FWD walk exceeded the 45-minute budget cap and did not complete (4K 120fps,
   stride 2). Results would also carry the stride-2 termination bug caveat (P12).
   Verdict: UNDETERMINED. The bucket cannot flip the RANKING-DOMINANT overall
   verdict (17 ranking vs max 8 starvation).

4. **Check 2 vs stall-diagnosis ROI discrepancy**: the stall-diagnosis doc
   reports "24 of 31 Conant frames DO extract raw motion blobs" while Check 2
   found 2 blobs on 1 frame under the production tight ROI. The discrepancy
   is plausibly explained by a wider ROI in the earlier probe, but the probe
   methodology was not documented in sufficient detail to confirm. A targeted
   re-check specifying both ROI formulas would resolve this.
