# Blob walk v2 cost-model arc: complete synthesis

Status: COMPLETE -- cost-model rewrite (WP-COST-1) and P10 fallback fix shipped
as SCHEMA_VERSION 14 (2026-06-12). All source artifacts synthesized below.

Artifact path:
[blob_walk_v2_cost_model_synthesis.md](blob_walk_v2_cost_model_synthesis.md)

---

## Purpose and scope

This document tells the complete cost-model story for a future reader who was not
present during the arc. It covers the original acceptance failures that motivated
the audit, the seventeen audit findings, the nine-check validation campaign, the
diagnosis of each major bug, the shipped fix, the verification stack, and the
measured results. All numbers are cited to their source artifact. Where two
artifacts disagree the discrepancy is identified and the authoritative source is
named.

The story runs from roughly 2026-06-02 (extraction-audit / m4 A/B data) through
2026-06-12 (SCHEMA_VERSION 14 release). The starting baseline is the shipped
windowed-Viterbi walker introduced in the M4 absorption milestone; the ending
state is the pairwise velocity-delta cost model.

---

## 1. The problem

### 1.1 M0 acceptance failures

Before the windowed-Viterbi walker existed, per-frame argmax selection was the
production method: on each frame the blob with the highest `integrated_mag` was
chosen as the runner position. The M0 acceptance bar required an
`accepted_fraction` of at least 0.5 on a representative corpus of intervals.
On four of six audit videos, per-frame argmax failed to meet this bar. The
24-corpus post-M4 walker baseline settled at 19.7% FWD and 9.6% BWD
`accepted_fraction`.

Source: [blob_walk_v2_implementation_audit.md](../audits/blob_walk_v2_implementation_audit.md),
finding L3 reference note.

The H4 root-cause audit diagnosed the mechanism: on individual frames, leg blobs
and foot blobs routinely outscore torso blobs on `integrated_mag`, so per-frame
argmax oscillates between body parts even when the torso blob is present every
frame. The insight -- that only window-level trajectory consistency reliably
identifies the runner -- led to the windowed-Viterbi design.

### 1.2 What the windowed walker was supposed to do

The amendment spec (archived at
`docs/archive/windowed_path_selection_amendment.md`) prescribed a second-order
DP over a 9-frame rolling buffer. Key spec requirements:

- Trajectory-consistency terms penalizing velocity-magnitude variance and
  velocity-angle variance across the window, with weights in YAML.
- Evidence as a small bounded tie-breaker using normalized `confidence` (a
  `[0,1]` quantity from the DoG pipeline), not raw `integrated_mag`.
- Decisions emitted at window center, offset `(N-1)//2 = 4`.
- A five-value status enum: `accepted`, `interpolated`, `extrapolated`,
  `soft_miss_no_blob`, `soft_miss_no_path`.

After absorption the walker baseline improved substantially from the pre-walker
numbers, reaching 42.3% FWD / 41.0% BWD on the 24-corpus.

Source: [blob_walk_v2_implementation_audit.md](../audits/blob_walk_v2_implementation_audit.md),
finding L3.

However, the m4 A/B evaluation (held-out-seed error, 58 passes) found
6 rescued / 15 preserved / 35 regressed -- the walker was making things worse
on more passes than it was helping.

Source: [blob_walk_v2_implementation_audit.md](../audits/blob_walk_v2_implementation_audit.md),
finding L2.

---

## 2. The audit

Audit date: 2026-06-10. Source:
[blob_walk_v2_implementation_audit.md](../audits/blob_walk_v2_implementation_audit.md).

The audit was a read-only line-level examination of all six `track_runner/blob_walk/`
modules, cross-checked against the amendment spec, the stall-diagnosis doc, and the
m4 A/B report. It produced seventeen proven findings (P1-P17), four clean negative
findings (N1-N4), two likely findings (L1-L2), and a twelve-claim assumption table
(A-L) gating all behavior changes.

### 2.1 Viterbi scoring findings

**P1. Evidence term uses raw `integrated_mag`; spec requires normalized confidence.**

The spec called for a small bonus scaled by `confidence`, a `[0,1]` value defined
as `0.5*strength + 0.5*proximity` with a 10000.0 normalizer. The shipped code
used `WEIGHT_EVIDENCE(-0.05) * integrated_mag` where `integrated_mag` is the sum
of DoG magnitudes over a blob component -- raw pixel-sum values in the thousands.
Static scale: evidence approximately -135 to -500 per node versus displacement
cost at most ~0.8 and `SKIP_COST` 2.0. The "small tie-break" became the largest
term in the cost by a factor of 100-1000.

Code locations: `walk_viterbi.py` lines 38, 93-96, 113-114;
`residual_motion.py` line 288.

**P2. Specified trajectory-consistency terms never implemented.**

`WEIGHT_MAG_VAR = 0.5` and `WEIGHT_ANGLE_VAR = 0.3` were defined as module
constants at `walk_viterbi.py` lines 34-36 but had zero call sites in the repo
(grep-verified). The walker's stated reason to exist was "only window-level
trajectory consistency reliably identifies the runner." What remained in the
cost was a displacement cap, a linear displacement cost, and the oversized
evidence term. The DP answered "which blob is strongest per frame, within a loose
displacement check" rather than the spec's "which blob SEQUENCE forms the most
plausible runner path."

The audit noted that the realistic failure mode was within-body centroid jitter
(right object, unstable point on it) rather than identity switching, because the
DoG band-pass tends to merge limb motion at large scales. Whether the missing
terms would actually reduce jitter was flagged as claims D and E (unverified at
audit time).

**P3. Weights hardcoded; spec requires YAML residence.**

The amendment required all weights to live in `overlay_styles.yaml` or a sibling
YAML. All five constants were module literals at `walk_viterbi.py` lines 32-41.

**P4. Skip semantics deviate from spec in two ways.**

(a) Skip teleport hole: any transition touching a skip node cost flat `SKIP_COST`
with no geometric check, and the displacement cap did not scale across skipped
frames. A path could teleport across a skip. (`walk_viterbi.py` lines 191-192.)

(b) Double charge: a skip frame was charged `SKIP_COST` at both the node level
(`walk_viterbi.py` line 109) and the transition level (lines 191-192), yielding
approximately 4.0 per skipped frame against a real-node evidence bonus of -135
to -500. Combined with the oversized evidence term, skip was essentially never
chosen when any in-box candidate existed.

**P5. Bootstrap slack inflates the cap on every step.**

`max_jump_px = (MAX_RUNNER_SPEED_W_PER_S/fps + BOOTSTRAP_UNCERTAINTY_W) * torso_w`
at `walk_viterbi.py` lines 72-74. `BOOTSTRAP_UNCERTAINTY_W = 0.30` was documented
as bootstrap-only search slack but was applied to every transition. At 60 fps the
per-frame cap was 0.8 W instead of the physical 0.5 W -- a 60% inflation on every
step.

### 2.2 Emission timing and status findings

**P6. Steady-state emission makes `interpolated` and `extrapolated` unreachable.**

The walker emits the oldest window frame (offset 0), one per advance. The
`emit_status_from_path` function classifies a skip frame as `interpolated` or
`extrapolated` only relative to accepted frames within the current window; offset 0
can never have an in-window accept before it, so every emitted skip frame is
`soft_miss_no_path` (or `soft_miss_no_blob`). The spec's gap-interpolation behavior
effectively never ran during the walk; every steady-state miss froze position at the
stale anchor.

**P7. Emission at oldest frame; spec says center.**

The amendment required decisions emitted at window center, offset 4. Code emitted
offset 0. Each decision was made with zero past context inside the window (all 8
context frames were future), and the accepted-anchor update lagged further (see P8).

**P8. Acceptance-box anchor is stale by at least the window depth.**

`last_accepted` updated only when an emitted (oldest) frame was accepted.
In steady state the frame being observed was 9 frames ahead of the newest
possibly-accepted emitted frame, so the acceptance box was centered on geometry
at least 9 frames old. At `walk_walker.py` lines 1036-1039 and 377-379.

**P9. Extrapolated position is a hold; spec says linear extension.**

`walk_status.py` lines 114-116 held the last accepted position while the spec
required linear extension of the last two accepts. Reachable only in the end-of-walk
flush (P6). The `consec_extrap` counter also reset on an intervening
`soft_miss_no_blob`, allowing the `EXTRAP_MAX=2` demotion to be defeated.

**P10. Bootstrap acceptance can mask the Hermite fallback.**

Bootstrap status was `accepted` when the seed-frame observation was not None,
incrementing `WalkSummary.accepted_count`. The Stage-4 Hermite fallback gate
fired only on `accepted_count == 0`. A walk that found one blob at the seed
frame but then stalled for the whole interval would report `accepted_count=1`,
skip the fallback, and ship a path frozen at the seed position -- strictly worse
than Hermite.

The diagnosed stall intervals had bootstrap observations of None, so they did
fire the fallback. The masking variant was a latent production risk.

### 2.3 Gates and candidate supply findings

**P11. Candidate gate excludes raw blobs before Viterbi sees them.**

`corridor_blobs` was a misnomer: the corridor filter was removed in May 2026 and
the list was raw blobs filtered to the acceptance box (+-0.5W x +-0.75H around
the stale anchor). The Viterbi lattice only ever contained blobs within half a
torso of that anchor; window-level reasoning could not consider candidates the
per-frame box had already excluded. The extraction audit placed typical runner
blob centroids at 0.5-0.75 torso heights from the torso center, exactly at the
box's half-height boundary.

**P12. Stride > 1 breaks stepping and termination.**

The termination test `frame_f == neighbor_seed_frame` at `walk_walker.py` line 1027
misses whenever the interval span is not divisible by stride. For >= 90 fps sources
(stride = `max(1, round(fps/60))`), the walk would step past the neighbor seed and
continue until the `max_steps_guard` stopped it, observing frames in the adjacent
interval.

**P13. Dead superseded velocity-gate code.**

`walk_motion_gate.evaluate` implemented the superseded per-frame gate spec. It was
kept alive only by its tests.

**P14. `seed_w` frozen for the whole walk.**

The displacement cap and acceptance box used a scalar `seed_w`/`seed_h` throughout;
`size_at_frame` interpolation sized only the emitted boxes. On intervals with large
scale change, cap and box were wrong near the far seed.

### 2.4 Telemetry findings

**P15. `path_cost` column lied about its own meaning.**

The column header documented it as "Viterbi DP cost contribution at this frame"
but the writer stamped the same whole-window total on every emitted row. Any cost
analysis built on the existing CSV would have produced wrong conclusions. This was
the one pre-behavior-change code fix required to evaluate any other finding.

**P16. Pre-pass store built but not consumed by the walker.**

The per-interval residual cache was built in `interval_solver.py` but never passed
to the walker's observe calls. A performance opportunity deferred as out of scope.

**P17. Over-broad cache-bypass guard.**

The `overrides_in_use` flag bypassed residual-cache read and write whenever an
acceptance-box override was present, but the acceptance-box filter was applied after
the cache fetch -- the cache bypass gained no correctness benefit while forcing
full recomputation. The walker always passed both overrides, so every walker
observation recomputed residual + DoG + extraction.

### 2.5 Clean areas

**N1. Coordinate handling clean.** Blobs extracted ROI-local, ROI origin added back
once; single PROCESSED->SOURCE conversion at the observe exit; no space mixing
anywhere in the walker path.

**N2. No temporal-direction bias in blobs.** The residual was unsigned and
time-symmetric (absolute difference against a symmetric neighbor median), so FWD and
BWD passes saw the same evidence.

**N3. Integration clean.** FWD/BWD bundles symmetric; blob_pass threaded correctly;
no decision-shaped state crossed passes or intervals.

**N4. Centroid interpretation.** Blob centroids are geometric centroids of
thresholded DoG motion components -- centers of motion energy, not torso centers.
The documented 0.5-0.75 H offsets were inherent to the signal.

---

## 3. The validation campaign

Nine checks (Check 0 through Check 8, plus Check G) were run between 2026-06-10
and 2026-06-11. Each targeted one or more of the twelve assumptions (A-L) in the
audit's assumption table. Source:
[blob_walk_v2_validation_report.md](blob_walk_v2_validation_report.md).

The P15 telemetry fix (Check 1 / SCHEMA_VERSION 12) was the only code change
during the validation period: the `path_cost` column was corrected from a
whole-window total to a truthful per-frame `path_step_cost`, and
`window_head_frame` was added. A strict field-wise decision-equality gate was
applied before merging; all 8 baseline passes produced byte-exact decision columns
before and after.

### 3.1 Claims verdict table

| Claim | Summary | Verdict | Source |
| --- | --- | --- | --- |
| A | Rejected blobs on stall intervals are the runner's blobs | REFUTED | Check 2 |
| B | Evidence term dominates Viterbi path selection | MIXED | Check 6 |
| C | Regressed-interval root-cause split | RANKING-DOMINANT | Check 7 |
| D | Limbs merge into one broad runner-body blob | REFUTED (conditional) | Check 5 |
| E | Within-body vertical centroid jitter | OBSERVED | Check 5 |
| F | Oldest-frame emission contributes to quality loss | CONFIRMED (structural) | Check 4 |
| G | Linear extrapolation inferior to hold-last | UNDETERMINED | Check G |
| H | Gate redesign direction | INFORMED, NOT PROVEN | synthesis |
| I | Anchor staleness is the operative starvation path | CONDITIONALLY CONFIRMED | Check 4 |
| J | Bootstrap-accept masking of Hermite fallback | OBSERVED (1 of 26 passes, 3.8%) | Check 3 |
| K | Stride-2 overrun on 120-fps video | OBSERVED FAILURE | Check 0 |
| L | Identity jumps across skip-bridging Viterbi transitions | NOT EXERCISED in sample | Check 8 |

### 3.2 Claim A: rejected blobs are not the runner

Check 2 ran overlay analysis on the two diagnosed stall intervals. Jason 564-583
FWD: 195 rejected blobs across 20 frames; only 0.5% (1 blob) within 1.0 W of the
seed reference; median distance 5.97 W. The Jason acceptance box was 3.2 x 7.9
processed pixels (torso width = 3.25 proc px, bin=2 from 6.5 source px). Background
athletes produced strong blobs at 5-24 W. Runner signal was below the DoG detection
threshold inside the 3.2 px box.

Conant 1080-1111 FWD: only 2 blobs found in the tight ROI across 31 frames (both
at 2.37 W). A wide-ROI probe (pad=200) found blobs on all frames, but at 7-24 W
corresponding to other athletes.

Widening the acceptance box would have admitted background-athlete blobs, not
recovered runner signal. Acceptance-box widening was therefore contraindicated.

**Discrepancy note.** The earlier stall-diagnosis doc reported "24 of 31 Conant
frames DO extract raw motion blobs (max integrated_mag ~2700)." Check 2 found
only 2 blobs on 1 frame using the production walker's exact tight ROI. The
discrepancy is plausibly explained by the earlier probe using a wider ROI. The
Check 2 result is authoritative because it used the production walker's exact ROI
formula; the earlier document's ROI methodology was not documented in sufficient
detail to confirm which is correct.

Source: [blob_walk_v2_check2_rejected_overlays.md](../workstreams/blob_walk_v2_check2_rejected_overlays.md).

### 3.3 Claim B: evidence term dominates selection (MIXED)

Check 6 measured per-term costs on 87 accepted frames across 5 passes (Conant and
Jason; stall passes and a zero-byte CSV excluded). Static: evidence cost had median
-558.52; displacement cost had median 0.000. The ratio was effectively infinite on
most frames; evidence dominated by 100-1000x.

Dynamic multi-candidate analysis (44 frames with 2+ distinct candidates): pooled
evidence-match fraction 43.2% (19/44), displacement-match fraction 93.2% (41/44).
But direction asymmetry was dramatic: FWD passes tracked minimum displacement (25/26
= 96.2%), not maximum evidence (3/26 = 11.5%); BWD passes tracked maximum evidence
(16/18 = 88.9%) and also minimum displacement (16/18 = 88.9%).

Resolution: per-node evidence dominated the node's own Viterbi cost, but accumulated
transition costs from earlier window frames biased the DP toward spatial consistency.
Jason FWD (4-6 competing blobs) illustrates: the walker picked the spatially coherent
trajectory even when a higher-mag blob was nearby.

Implication for fix priority: evidence normalization alone had no guaranteed effect
on path selection on the passes that mattered most (Jason FWD).

Source: [blob_walk_v2_check6_per_term_cost.md](../workstreams/blob_walk_v2_check6_per_term_cost.md).

### 3.4 Claim C: regressed-bucket split (RANKING-DOMINANT)

Check 7 re-walked all 35 m4 A/B regressed intervals and classified each by
empty-lattice fraction (mean of FWD and BWD `soft_miss_no_blob / total_frames_visited`).

Of 34 classified passes (1 Lyra-Wheeling pending due to decode timeout):

| Bucket | Count | Fraction of 34 |
| --- | --- | --- |
| Ranking-driven (mean empty < 0.2) | 17 | 50% |
| Mixed (0.2 to 0.5) | 10 | 29% |
| Starvation-driven (mean empty > 0.5) | 7 | 21% |
| Undetermined (Lyra-Wheeling) | 1 | -- |

The 35th pass (Lyra-Wheeling 754-981) timed out and could not flip the verdict:
even if starvation, ranking remains the largest bucket (17 vs 8).

Secondary finding: `soft_miss_no_path` was near zero everywhere (max 4 on any pass).
When candidates existed the walker accepted one. Ranking failures were wrong-blob-wins
events, not displacement-cap rejections.

A completion addendum (2026-06-10) re-walked the 10 mixed passes with per-frame status
capture. `accept_on_nonempty >= 0.88` across all 10 passes. Sub-classification:
starvation-leaning=5, selection-leaning=5. Effective ranking-driven share: 22 of 34
classified passes (65%); effective starvation share: 12 of 34 (35%).

The validation report's claim C section quotes 50% because it uses 34 classified
passes as the denominator. The completion addendum sharpens this to 65% including
the mixed-bucket diagnosis. Both numbers refer to the same 35-pass regressed set.

Source: [blob_walk_v2_check7_regressed_split.md](../workstreams/blob_walk_v2_check7_regressed_split.md).

### 3.5 Claim D: limb merge is scale-dependent (REFUTED conditional)

Check 5 examined blob counts near the seed reference. At Conant's torso height
(~30 px proc), 97-100% of candidate frames had exactly one blob near the reference;
the DoG merged the large runner. At Jason's scale (~11 px proc), every candidate
frame had 4-6 distinct blobs within one torso-width, covering the full vertical
extent. These were not background blobs; they were limb and trunk segments resolved
by the DoG at small scale.

Crossover is somewhere between 11 px and 30 px torso height. Limb merge is not a
universal pipeline property.

Source: [blob_walk_v2_check5_normalized_cy.md](../workstreams/blob_walk_v2_check5_normalized_cy.md).

### 3.6 Claim E: within-body vertical centroid jitter (OBSERVED)

From 88 accepted frames across 6 non-empty pass directions, global ncy statistics
(normalized centroid y-position within the torso box):

- ncy range: [-0.748, 0.298]
- |delta ncy| mean 0.066; P95 0.211; max 0.384 torso heights
- Alternation rate: 21 flips / 82 steps = 0.26 flips/step

The jitter concentrated in Jason. Jason/seed_602_629/FWD (4-6 competing blobs)
showed a 0.384-torso-height single-step jump and an alternation rate of 0.32
flips/step, with Viterbi switching between lower-runner blob cluster (ncy ~ -0.4)
and upper-runner cluster (ncy ~ +0.2) across windows.

Source: [blob_walk_v2_check5_normalized_cy.md](../workstreams/blob_walk_v2_check5_normalized_cy.md).

### 3.7 Claim F: anchor is structurally 9+ frames stale (CONFIRMED structural)

Check 4 measured anchor age (frame distance between observation time and the most
recently accepted emitted frame) across the 6 non-stall baseline passes.

Steady-state anchor age: median = 9.0, P90 = 9.0, max = 9 frames (matching the
theoretical minimum for oldest-frame emission with window depth WALKER_WINDOW_FRAMES
= 9). In passes with sparse accepts (BWD passes, partially stalled BWD), anchor age
rose to 10-22 frames as the anchor went un-updated.

For the two FWD stall cases: Conant 1080-1111 FWD accumulated 2.35 TW of anchor-to-
reference drift (box mispositioned from frame 1087 onward); Jason 564-583 FWD
stayed within 0.527 TW throughout (runner barely moved in image space), so anchor
age alone did not drive that stall.

ALL 72 rejections across 6 non-stall baseline passes occurred at anchor_age >= 7
frames at observation time. No rejections occurred with a fresh anchor.

Source: [blob_walk_v2_check4_anchor_lag.md](../workstreams/blob_walk_v2_check4_anchor_lag.md).

### 3.8 Claim G: extrapolation (UNDETERMINED)

Check G performed offline replay on 24 available walk debug CSVs. Zero affected
frames: `extrapolated` and `interpolated` statuses had zero occurrences across 366
`soft_miss_no_blob`, 282 `accepted`, and 24 `after_walk_terminated` frames. Both
statuses were structurally limited to the end-of-walk flush (P6 confirmed).

The P9 spec deviation was confirmed: `walk_status.py` lines 114-116 implemented
a hold while the spec required linear extension. Empirical comparison was
indeterminate (hold appeared better than linear in 10/10 replay scenarios, but
the reference line's curvature toward the neighbor seed contaminated the
comparison). Synthetic parametric analysis (uniform motion) showed linear better
(59/59 synthetic scenarios; hold accumulated 0.22-0.42 TW per frame). Effect
surface negligible at most 2 frames per pass per interval (EXTRAP_MAX=2).

Verdict: UNDETERMINED. Fix deferred until `extrapolated_count` becomes non-zero
in production.

Source: [blob_walk_v2_checkg_extrapolation_replay.md](../workstreams/blob_walk_v2_checkg_extrapolation_replay.md).

### 3.9 Claim I: two stall sub-types confirmed (CONDITIONALLY CONFIRMED)

Check 4 established two distinct causal sub-types:

1. Conant 1080-1111 FWD: positional-drift stall. Staleness + runner image-space
   drift = acceptance box mispositioned. 24/31 frames had anchor-to-reference
   drift > 0.5 TW; drift reached 2.35 TW at frame 1110. An anchor-advance fix
   would address this sub-type.

2. Jason 564-583 FWD: signal-absence stall. Anchor was correctly positioned
   (max drift 0.527 TW); blobs were absent because of centroid-offset geometry
   (L4 + P11). An anchor-advance fix alone would NOT cure this case.

A single fix cannot address both sub-types. The starvation stalls in the
regression set concentrated in IMG_3823 (6 intervals) + Conant (1), consistent
with a scene or runner-size dependency.

Source: [blob_walk_v2_check4_anchor_lag.md](../workstreams/blob_walk_v2_check4_anchor_lag.md).

### 3.10 Claim J: bootstrap masking observed in production (OBSERVED)

Check 3 examined 26 passes across 3 videos and 13 intervals. One pass exhibited
the masking pattern: Conant `seed_1126_1134` FWD, an 8-frame interval. Bootstrap
frame accepted (accepted_count = 1), all 7 post-bootstrap frames
`soft_miss_no_blob`, fallback gate (`accepted_count == 0`) not satisfied. Shipped
path frozen at seed position for 7 of 8 frames.

The BWD pass on the same interval found 3 accepted frames and returned normally.

Incidence: 1 of 26 passes = 3.8% of the sample. Short intervals most vulnerable:
a single bootstrap hit covers the entire "accepted" budget.

Source: [blob_walk_v2_check3_bootstrap_masking.md](../workstreams/blob_walk_v2_check3_bootstrap_masking.md).

### 3.11 Claim K: stride-2 overrun confirmed (OBSERVED FAILURE)

Check 0 analyzed Lyra-Wheeling-IMG_3912 (fps=119.94, stride=2). Of 148
post-race odd-span intervals, 147 were high-confidence; only 1 was promoted to
Stage 4: interval #164, frames 16588-16591, span=3, tier=fair.

With stride=2, the equality check `frame_f == neighbor_seed_frame` never fired
for a span-3 interval. FWD overshot to frames 16590, 16592, 16594, 16596 (3
frames into the next interval) before `max_steps_guard = 4` stopped it. BWD
overshot to frames 16589, 16587 (2 frames into the previous interval). Confirmed
by code analysis; no direct CSV telemetry was available.

Source: [blob_walk_v2_check0_stride_overrun.md](../workstreams/blob_walk_v2_check0_stride_overrun.md).

### 3.12 Claim L: skip-teleport not observed (NOT EXERCISED)

Check 8 measured 82 accepted-to-accepted steps across 8 passes (216 frame-rows).
Maximum observed displacement: 0.614 W. Identity-jump threshold (pooled P99 + 0.3
W = 0.578 + 0.30 = 0.878 W): 0 events exceeded it.

Skip-bridging steps (7 total, max 0.231 W) were smaller than between-accepts steps
on average. The corridor filter pre-constrained candidates to ~0.80 W of the last
anchor at 60 fps, acting as a soft outer bound. The structural Viterbi hole was
real but not exercised in the sample.

Source: [blob_walk_v2_check8_identity_jumps.md](../workstreams/blob_walk_v2_check8_identity_jumps.md).

---

## 4. Diagnosis

### 4.1 Min-displacement model enforced wrong physics

The first-order cost `WEIGHT_DISPLACEMENT * displacement` penalized motion.
A stationary distractor paid zero displacement cost per step; a moving runner paid
a positive cost proportional to its speed. With `WEIGHT_DISPLACEMENT = 1.0` the
DP effectively asked "which path moves the least?" -- a bias toward frozen
distractors rather than the moving runner.

Check 6's dynamic finding made this concrete: FWD multi-candidate frames selected
the minimum-displacement candidate 96.2% of the time. The DP had momentum from
prior accepted frames and was converging toward the nearest spatial position at
each step, which on frames with a moving runner meant tracking the wrong (less
mobile) blob.

Source: [blob_walk_v2_check6_per_term_cost.md](../workstreams/blob_walk_v2_check6_per_term_cost.md);
confirmed in [walk_viterbi.py](../../../track_runner/blob_walk/walk_viterbi.py)
docstring (shipped model, opening section).

### 4.2 Dead variance constants

`WEIGHT_MAG_VAR = 0.5` and `WEIGHT_ANGLE_VAR = 0.3` were defined but had zero
call sites. The DP structure provided no mechanism to compute these: window variance
is not separable across DP edges, so a naive inclusion would violate
optimal-substructure. The spec's terms were mathematically incompatible with the
first-order DP architecture.

The fix would require replacing them with pairwise delta terms (acceleration and
heading change between consecutive real nodes), which are additive and satisfy
Bellman optimality when the DP state is an ordered pair of real nodes.

Source: audit P2.

### 4.3 Evidence scale mismatch (100-1000x)

The raw `integrated_mag` of a blob was the sum of DoG filter response magnitudes
over the connected component -- values in the thousands. Multiplied by
`WEIGHT_EVIDENCE = -0.05` (a negative weight to reward stronger evidence), the
evidence term ranged from -135 to -500 per node. In comparison, the displacement
term was at most ~0.8 TW and `SKIP_COST` was 2.0. The "tie-breaker" dominated
the cost by two orders of magnitude.

The normalization fix was straightforward in principle: `ev = mag / max_mag` per
frame, bounded `[0, WEIGHT_EVIDENCE_NORM]`. But Check 6 showed the FWD direction
was already selecting by spatial consistency despite the evidence imbalance,
meaning normalization alone would not change the outcome on the passes that
mattered most.

Source: audit P1; validated as MIXED by
[blob_walk_v2_check6_per_term_cost.md](../workstreams/blob_walk_v2_check6_per_term_cost.md).

### 4.4 Skip double-charge and teleport hole

Skip was charged twice: once at the node level (initializing the DP cost at the
skip-frame position) and once at the transition level (adding `SKIP_COST` on the
edge into the skip node). Combined cost approximately 4.0 per skipped frame. With
real-node evidence bonuses of -135 to -500, skip was essentially never chosen when
any candidate existed -- the effective behavior was a hard veto of skip whenever
blobs were available.

The teleport hole (claim L) was structural: a transition from a skip node to a
real node carried no geometric cap, so in principle the walker could jump large
distances across a skip. In practice, the acceptance-box filter upstream
constrained candidates to ~0.80 W of the anchor, providing a soft outer bound.

Source: audit P4; validated (NOT EXERCISED) by
[blob_walk_v2_check8_identity_jumps.md](../workstreams/blob_walk_v2_check8_identity_jumps.md).

### 4.5 Bootstrap slack everywhere

`BOOTSTRAP_UNCERTAINTY_W = 0.30` added 60% slack to the per-frame displacement
cap on every step, not just bootstrap. At 60 fps the effective cap was 0.8 W
instead of the physical 0.5 W. This combined with the tight acceptance box
(P11) to produce contradictory gate geometry: the box constrained candidates to
within 0.75 H of the anchor while the cap allowed 0.8 W of motion per step.

Source: audit P5.

---

## 5. The fix

### 5.1 Scope and conceptual grouping

The fix was delivered as a single bundle (WP-COST-1 + P10, SCHEMA_VERSION 14,
2026-06-12). Changes 1-4 (cost-model) were one coupled fix: the soft displacement
cap is meaningless without consistency terms to distinguish runner from distractor;
normalized evidence only acts as a genuine tie-breaker once the cap stops selecting
by motion minimization. Changes 5-6 (P10 fallback, walk_io trust audit) were
separate fixes staged together.

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"Conceptual grouping" section.

### 5.2 Pairwise velocity-delta scoring: full model

The DP runs over real observations only. A skip node is never a geometry node.
The DP state is an ordered pair of real nodes (prev_node, curr_node), so a
transition cost can depend on two consecutive velocities.

**Velocity computation.** Between two real nodes at frames t_a and t_b with pixel
centroids (cx_a, cy_a) and (cx_b, cy_b) and torso width W:

```
gap = t_b - t_a
vx = (cx_b - cx_a) / (gap * W)
vy = (cy_b - cy_a) / (gap * W)
speed = hypot(vx, vy)          # torso-widths per frame
```

Gap normalization bridges skipped frames: a two-frame skip produces a
half-magnitude per-frame velocity, not a doubled one. All spatial quantities are
in torso-width units (contract C2).

**Heading angle.** Defined as `acos(dot(v1, v2) / (|v1| * |v2|))` in `[0, pi]`.
Cosine clamped to `[-1, 1]` for numerical safety. Heading is defined as 0 when
either velocity magnitude is below `SPEED_EPSILON_W = 0.02` TW/frame.

**Hard prune.** A single gate: edges whose gap-normalized per-frame speed exceeds
`ABSOLUTE_MAX_JUMP_W = 1.5` TW/frame are pruned to +inf. This is the only hard
exclusion in the model.

**Edge cost** from real node a to real node b (no transition term):

```
cost = SKIP_COST * (gap - 1)                      # skip charge: once per bridged frame
cost += WEIGHT_DISPLACEMENT * speed               # soft displacement (linear)
if speed > MAX_RUNNER_SPEED_W_PER_S / fps:        # continuous overspeed penalty
    overspeed_frac = (speed - limit) / limit
    cost += WEIGHT_OVERSPEED * (overspeed_frac^2)
cost += evidence_b                                # evidence cost of node b
```

**Transition (delta) cost** between real nodes x->a and a->b:

```
delta = WEIGHT_SPEED_DELTA * |speed_ab - speed_xa|
delta += WEIGHT_HEADING_DELTA * angle(v_xa, v_ab)
```

This is added to the edge cost only when node a was itself reached as a pair;
first real nodes have no delta cost.

**Evidence normalization.** Per-frame, per-candidate:

```
ev = max(0, integrated_mag) / max_mag       # max_mag = max positive mag in this frame
cost = WEIGHT_EVIDENCE_NORM * (1.0 - ev)   # bounded [0, WEIGHT_EVIDENCE_NORM]
```

Zero-denominator frames receive neutral evidence (cost 0 for every candidate).
The term cannot dominate the path cost.

**Skip cost.** Each skipped frame between two real nodes costs `SKIP_COST` once.
This is a single charge, not a double charge (P4 fixed). Trailing and leading
skips are also charged at the same rate. The all-skip path costs `N * SKIP_COST`
and is the baseline every real path competes against.

**Shipped default weights.** Sourced from `track_runner/track_runner.config.yaml`:

```yaml
walker_costs:
  WEIGHT_DISPLACEMENT:  0.25
  WEIGHT_SPEED_DELTA:   1.0
  WEIGHT_HEADING_DELTA: 0.5
  WEIGHT_OVERSPEED:     4.0
  WEIGHT_EVIDENCE_NORM: 0.5
  SKIP_COST:            2.0
```

`WEIGHT_DISPLACEMENT = 0.25` was lowered from the plan's 1.0 per manager resolve
on 2026-06-12. At 1.0 the displacement term dominated evidence for slow-moving
runners; at 0.25 the velocity-delta terms and normalized evidence can compete on
equal footing.

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md);
[walk_viterbi.py](../../../track_runner/blob_walk/walk_viterbi.py).

### 5.3 Boundary rules

The DP handles the window boundaries as follows:

- First real node: no predecessor, no delta cost. Only evidence cost plus
  leading skip charge (SKIP_COST * number of frames skipped before it).
- Single-node path (only one real node in the window): no delta cost; trailing
  skip charge for frames after it.
- All-skip path: N * SKIP_COST; no real nodes.
- Trailing skip: each frame after the last real node costs SKIP_COST once.

Source: [walk_viterbi.py](../../../track_runner/blob_walk/walk_viterbi.py),
`select_path` function.

### 5.4 Tie rules

On exact cost ties the earlier state in iteration order wins, preserving the
incoming candidate order as the deterministic tie order.

Source: [walk_viterbi.py](../../../track_runner/blob_walk/walk_viterbi.py),
`select_path` docstring.

### 5.5 Design rationale: pairwise delta vs. window variance

The spec's trajectory-consistency terms (velocity-magnitude variance and heading
variance) were window-level statistics. Window variance is not separable across DP
edges: it depends on the window mean, which is not known until all nodes are chosen.
This violates optimal substructure and would require global rollback to re-evaluate.

Pairwise velocity-delta terms (speed-delta, heading-delta between consecutive pairs)
are additive over DP transitions: each transition cost depends only on two consecutive
velocities. The DP state is an ordered pair (prev_node, curr_node), and Bellman
optimality holds. The two approaches overlap but are not identical: variance penalizes
deviation from a mean, pairwise delta penalizes acceleration. The acceleration
interpretation is arguably more physically meaningful for a runner: a constant-velocity
runner accumulates zero delta cost regardless of its speed, while a runner that
accelerates or changes direction is penalized.

Source: [walk_viterbi.py](../../../track_runner/blob_walk/walk_viterbi.py),
module docstring "Cost model: pairwise velocity-delta scoring (audit P2)."

### 5.6 YAML wiring

All six weights now live in the `walker_costs` section of
`track_runner/track_runner.config.yaml`, resolving the P3 doc-code conflict.
Resolution uses the shared `tr_config.resolve_config` helper called from both
`cli.py` and `tools/blob_walk_v2/walk_driver.py`. Weights flow through
`WorkerContext.walker_costs` and `make_pool` initargs to `_worker_init ->
set_cost_weights` in worker processes.

A wiring gap was discovered and fixed during the A/B: `interval_solver._dispatch_blob_pass`
had omitted `walker_costs` from its `make_pool` call (spec-review F1 fix). The
34-pass config-1 baseline run (job buuqba7rd) predated this fix and used
module-constant defaults, which are numerically identical to the YAML defaults.

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"What changed" and "Provenance disclosure" sections;
[TR_SCHEMA_VERSION_HISTORY.md](../../TR_SCHEMA_VERSION_HISTORY.md), version 14.

### 5.7 P10 seed-only Hermite fallback fix

The `WalkCoverage` dataclass introduced two named fields:

- `accepted_count`: total accepted frames (unchanged meaning, preserved for
  telemetry and future consumers).
- `post_seed_accepted`: accepted frames excluding the pass's own seed frame.

Helper `count_post_seed_accepts(accepts, seed_frame)` computes the count. The
seed frame can appear in `accepts` at most once (only via bootstrap/seed
observation); windowed steps start at `seed_frame + sign*stride` and the neighbor
seed is never observed. The fallback gate now reads:

```python
walker_fallback_fwd = (fwd_coverage.post_seed_accepted == 0)
walker_fallback_bwd = (bwd_coverage.post_seed_accepted == 0)
```

Terminology note: "seed" replaces "bootstrap" in all new code and docs per user
decision 2026-06-12. The legacy `BOOTSTRAP_UNCERTAINTY_W` identifier remains in
existing code pending a follow-up rename.

Source: [blob_walk_v2_p10_fix_plan.md](../../archive/blob_walk_v2_p10_fix_plan.md);
[TR_SCHEMA_VERSION_HISTORY.md](../../TR_SCHEMA_VERSION_HISTORY.md), version 14.

---

## 6. The verification stack

### 6.1 Contract tests

`tests/test_walk_cost_model.py` contains 17 contract tests covering:

- Model-flip: ranking-class intervals where the old min-displacement model would
  select a stationary distractor over a moving runner.
- Limb-oscillation: intervals where the old evidence-dominated model would
  select a stronger-magnitude limb blob over the torso.
- Skip-bridge: transitions across skipped frames; single-charge semantics.
- Boundary: first-node and last-node window edge cases.
- Start-bias: paths that differ only in which direction they begin.
- Evidence tie-break: normalized evidence selecting the stronger candidate when
  all other costs are equal.
- Neutral-zero: zero-denominator frames receive zero evidence cost.

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"Evidence by change" table.

### 6.2 Brute-force optimality check

`tests/test_walk_viterbi_brute_force.py` enumerated all possible paths over a
test lattice and verified the DP result matched the brute-force minimum in 39/39
cases.

Mutation M1 was also caught: a sign-bug injected into the DP caused the
brute-force test to fail as required. This confirmed the test was exercising the
DP logic rather than testing a trivially correct path.

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"Evidence by change" table.

### 6.3 YAML wiring tests and mutation checks

`tests/test_walker_costs_config.py` contains 9 config tests including a `make_pool`
boundary capture test. Mutation M2 (deleting a wiring call site) was caught by
this suite.

### 6.4 P10 fallback tests

`tests/test_walk_coverage.py`: 8 coverage unit tests with case shapes drawn from
the Check 3 per-pass table: empty accepts, bootstrap-only, bootstrap + windowed,
bootstrap-miss windowed-accept, BWD seed at right endpoint, duplicate-frame case.

`tests/test_walker_stall_fallback.py`: Conant `seed_1126_1134` gate reproduction.
Mutation M3 was caught.

The primary proof was the targeted Stage-4 re-solve of Conant `seed_1126_1134`:
`walker_fallback_fwd = True` with Hermite FWD geometry shipped; BWD unchanged.

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"Evidence by change" table.

### 6.5 walk_io parity tests

`tests/test_walk_io_parity.py`: 20 parity tests covering reader geometry, path
parity against `tr_paths`, loud-failure asserts on missing artifact, and
race-phase labels. This addressed the `walk_io.load_race_start_frame` trust issue
(change 6 in the bundle).

### 6.6 Full suite baseline

2985 tests passed, 0 failed, at the time of the release review.

Pure-Hermite path byte-identity was argued structurally: Stage-3 `blob_pass=False`
never reaches the walker; the cost-model change only affects Stage-4-promoted
intervals. No regression on any pure-Hermite pass.

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"General safety" rows.

---

## 7. Results

### 7.1 22-pass ranking bucket

All 34 classified regressed passes from Check 7 were re-solved under the new model.
There was no pre-change `accepted_fraction` baseline for this set (0/34 intervals
overlapped with the corpus_walk data).

Per-video subtotals (accepted / non-seed frames):

| Video | FWD | BWD |
| --- | --- | --- |
| IMG_3830 (13 intervals) | 68/90 = 75.6% | 72/90 = 80.0% |
| IMG_3823 (11 intervals) | 65/153 = 42.5% | 61/153 = 39.9% |
| Jason (3 intervals) | 109/162 = 67.3% | 110/162 = 67.9% |
| Lyra-Hersey (5 intervals) | 115/131 = 87.8% | 115/131 = 87.8% |
| Conant (2 intervals) | 52/78 = 66.7% | 51/78 = 65.4% |
| Overall 34-pass total | 409/614 = 66.6% | 409/614 = 66.6% |

Ranking-class passes: FWD range 0.692-1.000, mostly >= 0.833. Starvation-class
passes: FWD range 0.133-0.526 (expected; sparse blobs). The dominant ranking-failure
bucket now showed 66.7-100% accepted on ranking-class passes, mostly >= 83% FWD.

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"22-pass set summary" section.

### 7.2 Corpus controls: 5-video subset

All 5 corpus videos were re-solved against the frozen interval manifest from the
Jun 10 corpus run (SCHEMA_VERSION 13 baselines from
[blob_walk_v2_corpus120_run_2026_06_10.md](../workstreams/blob_walk_v2_corpus120_run_2026_06_10.md)).

| Video | Before FWD | After FWD | Delta | Before BWD | After BWD | Delta |
| --- | --- | --- | --- | --- | --- | --- |
| IMG_3830 | 83.6% | 82.0% | -1.6 pp | 88.5% | 88.5% | 0.0 pp |
| IMG_3823 | 55.8% | 54.5% | -1.3 pp | 54.5% | 53.2% | -1.3 pp |
| Conant | 67.9% | 65.8% | -2.1 pp | 76.2% | 74.1% | -2.1 pp |
| Lyra-Hersey | 82.7% | 81.6% | -1.1 pp | 78.0% | 76.4% | -1.6 pp |
| Jason | 40.2% | 38.4% | -1.4 pp | 39.0% | 37.5% | -1.5 pp |

No regression on any video. Maximum delta: -2.1 pp (Conant FWD/BWD). The before
baselines were SCHEMA_VERSION 13 (post-P12, pre-cost-model). The after values are
SCHEMA_VERSION 14. Denominator differences between before and after reflect
schema 14 counting changes (seed frame included consistently); absolute accepted
counts were stable or near-stable.

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"5-video corpus summary" table.

### 7.3 Held-out-seed error (quality authority)

The quality authority metric is held-out-seed error: the distance between the
predicted torso center at the middle seed of a three-seed window and the actual
seed position, measured in torso-width units.

`e2e_walker_ab` was run with random_seed=12345, n=5 per video, per_video_budget_s=1800.0
(25 passes across 5 videos, 1 skipped by budget, 24 evaluated):

| Video | Preserved | Regressed | Rescued | Needs review | Skipped |
| --- | --- | --- | --- | --- | --- |
| IMG_3830 | 2 | 3 | 0 | 0 | 0 |
| IMG_3823 | 2 | 3 | 0 | 0 | 0 |
| Jason | 2 | 0 | 1 | 1 | 1 |
| Lyra-Hersey | 4 | 1 | 0 | 0 | 0 |
| Conant | 2 | 0 | 3 | 0 | 0 |
| Total | 12 | 7 | 4 | 1 | 1 |

Rescues (strongest improvement signal):

- Conant [1157,1173,1235]: `hermite_err` 1.175 -> `walker_err` 0.242 (delta -0.933)
- Conant [5310,5372,5434]: `hermite_err` 1.487 -> `walker_err` 0.238 (delta -1.249)
- Conant [7780,7811,7842]: `hermite_err` 0.916 -> `walker_err` 0.428 (delta -0.489)
- Jason [23406,23500,23594]: `hermite_err` 1.378 -> `walker_err` 0.524 (delta -0.854)

Regressions: 6 of 7 were on very short spans (1-13 frames), where the pairwise
velocity-delta cost requires at least 2 frames of real-node history to compute
-- degenerate by construction. The one normal-length regression was Lyra-Hersey
[840,892,945] (span=105 frames, `hermite_err` 0.206 -> `walker_err` 0.778),
unexplained, flagged for WS-2B overlay review.

The one `needs_review` case was Jason [12408,12502,12596] (`walker_err` 2.918 vs
`hermite_err` 1.312, delta +1.606), span=188 frames, high-motion interval.

Acceptance frame (user directive): the target is most intervals working better, not
all. Rescues on the hardest intervals (Conant drift-stall, Jason long-run) are the
primary signal. The short-span regressions are unrepresentative of normal tracking
intervals.

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"E2E smoke outcomes: Part 5 results."

### 7.4 Identity check

39 intervals were checked across all 5 corpus videos (18 initial batch, 21
completion batch). Each check examined the last accepted frame in a FWD pass
(furthest from seed, most likely to drift) for cross-athlete or background-blob
capture.

Verdict: PASS -- no identity jumps observed across all 39 checked intervals.

Conant cx values (457-945) spanned the full frame width; cy values (349-445) were
consistent with track-level runner positions. IMG_3823 cx range (220-798): runner
traversing the scene; positions consistent with the same runner. Jason seeds_23312
and 23406 both showed cx~1231-1235, cy~367-368, tw~9-10 (far-end runner, adjacent
intervals, matching position).

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"Identity spot check: Part 3 results."

### 7.5 Tuning sensitivity

Four configs were tested on the fast 24-pass subset (IMG_3830 + IMG_3823):

| Config | Changed | FWD | Delta |
| --- | --- | --- | --- |
| 1 defaults | -- | 56.1% | baseline |
| 2 evidence-forward | WEIGHT_EVIDENCE_NORM 0.5->1.0 | 56.1% | 0.0 pp |
| 3 strong geometry | WEIGHT_SPEED_DELTA 1.0->2.0, HEADING_DELTA 0.5->1.0 | 56.1% | 0.0 pp |
| 4 low skip cost | SKIP_COST 2.0->1.0 | 56.1% | 0.0 pp |

All configs produced identical results on the fast subset. Short high-confidence
passes with a majority blob converge to the same path regardless of cost weights.
The cost terms only differentiate when multiple blobs compete with comparable scores.

Conclusion: default weights are the winning config. For future tuning sensitivity,
longer and more contested intervals (Jason, Lyra-Hersey) are the primary tuning
surface.

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"Tuning table: Part 4 results."

### 7.6 Corpus-120 baseline (pre-cost-model)

The Jun 10 corpus run (post-P12, pre-cost-model, SCHEMA_VERSION 13) across 120
intervals (6 videos x 20 random intervals each) produced 58.5% FWD / 61.1% BWD.

This figure is substantially higher than the L3 pre-P12 reference (38.7% FWD /
39.1% BWD). The corpus run used a fresh random sample (rng_seed=None), and
high-performing intervals may be over-represented relative to the fixed-seed L3
sample. The elevation is plausible but not directly attributable to P12. Held-out-
seed distance measurements are the quality authority; `accepted_fraction` is
diagnostic only.

Source: [blob_walk_v2_corpus120_run_2026_06_10.md](../workstreams/blob_walk_v2_corpus120_run_2026_06_10.md).

---

## 8. Residuals

Items that are known and measured, listed with frequency estimate and impact.
Post-rewrite ranking of open work: signal-absence stall first, then the
seed-frame stall and the bounded ranking residual, short-span degeneracy last.

### 8.1 Ranking-failure class (largely addressed; residual bounded)

Pre-fix this was the dominant bucket: 22 of 34 classified regressed passes (65%)
were wrong-blob-wins events where a limb or body-segment blob outscored the torso
blob. The pairwise velocity-delta model addressed it: re-solved under the new
model, the 22-pass set now shows 66.7-100% accepted on ranking-class passes
(mostly >= 83% FWD), with the full 34-pass set at 66.6% overall (Section 7.1).
The remaining ranking residual is bounded to misranking within otherwise-improved
passes plus one unexplained normal-length regression: Lyra-Hersey [840,892,945],
`hermite_err` 0.206 -> `walker_err` 0.778, under overlay review (Section 8.4).
The deeper fix -- scale-adaptive DoG diameter, or a post-extraction blob merge at
runner scale -- requires M3 evidence from the fix-phase roadmap; its primary
motivation is now the signal-absence class (8.2), not ranking.

Source: [blob_walk_v2_fix_phase_roadmap.md](../active/blob_walk_v2_fix_phase_roadmap.md),
milestone M3 section.

### 8.2 Signal-absence stall class (largest open bucket, high impact)

The largest open bucket post-rewrite. Measured frequency: 10.8% of corpus
interval-directions show seed-cold symptoms; Jason alone reaches 35%. Cost-model
weights provably cannot move it -- all 4 tuning configs were identical on
starvation passes, so the failure is structurally upstream of ranking.

The Jason signal-absence stall sub-type: at ~3 proc-px torso height, no blobs
extract in the corridor regardless of box size or walker parameters. Three corpus
Jason intervals ([16826,16920], [17014,17108], [30456,30550]) showed FWD=0 accepted.
This is a structural extraction-scale problem; the DoG diameter is too large
relative to the runner. Addressed by M3 decision-rule branch 2 (scale-adaptive DoG)
if the WS-2C census confirms fragmentation at small scales.

Sources:
[blob_walk_v2_starvation_characterization.md](blob_walk_v2_starvation_characterization.md);
corpus results in
[blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
note on Jason starvation intervals.

### 8.3 Short-span degeneracy (high count, negligible impact)

6 of 7 held-out-seed regressions were on spans of 1-13 frames. Pairwise velocity-
delta cost requires at least 2 frames of real-node history. At span=1 or span=2
the DP degenerates. Measured weight: 57% of intervals by count, 8.8% of frames by
weight, ~0.3% of corpus frames after Stage-4 promotion filtering. Not a quality
regression for normal-length tracking intervals; these are seed-placement
artifacts rather than structural failures.

Sources:
[blob_walk_v2_short_span_frequency.md](blob_walk_v2_short_span_frequency.md);
[blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"Key findings" in the e2e_walker_ab section.

### 8.4 Lyra-Hersey [840,945] regression (low frequency, medium impact)

A span-105 regression (`hermite_err` 0.206 -> `walker_err` 0.778). Unexplained at
release time. Flagged for WS-2B overlay review in the M2 roadmap milestone.

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"Known gaps" section.

### 8.5 Jason [12408,12596] needs_review (low frequency, medium impact)

Span=188 frames, high-motion interval. `walker_err` 2.918 vs `hermite_err` 1.312
(delta +1.606). Flagged for WS-2B overlay review.

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"Known gaps" section.

### 8.6 Anchor staleness floor (structural, impact conditional)

The oldest-frame emission design places a structural floor of 9 frames on anchor
age (P8, F CONFIRMED). Every anchor rejection in the baseline sample occurred at
anchor age >= 7. Impact is conditional on image-space drift: Conant drift-stall
(2.35 TW drift) was directly caused by staleness; Jason signal-absence stall was
not (drift under 0.53 TW). Anchor-advance is the M4 fix-phase milestone.

Source: [blob_walk_v2_check4_anchor_lag.md](../workstreams/blob_walk_v2_check4_anchor_lag.md).

### 8.7 Bootstrap stall root cause (low frequency)

P10 fallback now catches the worst symptom (frozen-at-seed path), but the root
cause of the seed-frame stall itself is still open. Historical incidence: 3.8% of
sampled passes in the Check 3 sample.

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"Known gaps" item 6.

### 8.8 walk_motion_gate.evaluate() dead code (low impact)

The old three-cap-min gate now has zero call sites in the production path (P13).
Kept alive by its tests. Cleanup deferred until primary behavior work (M3, M4)
is stable.

Source: audit P13;
[blob_walk_v2_fix_phase_roadmap.md](../active/blob_walk_v2_fix_phase_roadmap.md),
deferred items.

### 8.9 Wiring call sites without guard tests (low impact)

Two wiring call sites have no dedicated test: `solver_workers._worker_init`'s
`set_cost_weights` call, and `make_walk_html_v2.process_video`'s
`apply_walker_costs_for_video` call. Deleting either would be invisible to the
current suite. Recommended cheap pre-commit hardening; not yet done.

Source: [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md),
"Known gaps" item 1.

---

## 9. Lessons

### 9.1 Spec-vs-implementation drift: weight constants vs. call sites

The two most impactful bugs (P1 and P2) shared a common pattern: the spec described
a behavior, the code defined the constants needed for that behavior as module
literals, and then neither the spec nor the tests detected that the constants had
zero call sites. `WEIGHT_MAG_VAR` and `WEIGHT_ANGLE_VAR` were never wired into the
DP; `WEIGHT_EVIDENCE` was wired but used raw `integrated_mag` instead of the
`confidence` the spec defined.

The lesson: defining a constant is not the same as implementing the behavior. A
check that each weight constant appears in at least one non-test DP evaluation path
would have caught P2 immediately.

### 9.2 Wiring no-ops and provenance gaps

The YAML wiring gap (weights flowing from config but not reaching the Stage-4 worker
pool) was discovered during the A/B, not during development. The fix was behavior-
neutral because the YAML defaults matched the module constants; but the gap meant
any future config change would have been silently ignored on promoted intervals.

The pattern: a component can appear "wired" at one level of the call stack (config
loaded, field populated in WorkerContext) while the actual execution path uses
module-level defaults. End-to-end wiring tests that inject a non-default value and
verify it arrives at the DP would have caught this class of bug.

### 9.3 Metric pitfalls: static scale vs. dynamic selection

The static analysis of P1 (evidence magnitude 100-1000x displacement) correctly
identified a scale imbalance but overpredicted the behavioral effect. Dynamic
analysis (Check 6) showed FWD selection was dominated by spatial consistency
(96.2% min-displacement), not max-evidence (11.5%). The DP's accumulated path
momentum overrode per-node evidence on the passes that mattered most.

The lesson: a cost-term magnitude analysis is a necessary but not sufficient
condition for understanding DP path selection. The interplay between per-node
costs and multi-step path momentum requires dynamic measurement on multi-candidate
frames.

### 9.4 Two root causes, one symptom

The two stall sub-types (Conant drift stall, Jason signal-absence stall) presented
identically in user-visible behavior: the walker rejected all candidates and froze
at the seed. But their mechanisms were completely different, and each required a
different fix direction. Claim I's conditional confirmation and the two-sub-type
story (Check 4) established that diagnosing the symptom was not sufficient; the
root cause required separate characterization for each video.

### 9.5 Refuted hypothesis, not just evidence gap

Claim A is a rare case of a hypothesis being cleanly refuted (not just lacking
evidence). The rejected blobs on the stall intervals were other athletes at 5-24
torso-widths, not the target runner. Widening the acceptance box would have
admitted noise, not recovered signal. The refutation was established by direct
overlay measurement (Check 2), not inference. Future proposals to widen the
acceptance box require new evidence that runner blobs exist at wider radii.

### 9.6 Tuning invariance at short spans

The four tuning configs produced identical results on the fast 24-pass subset.
This is a design property, not a test failure: when one blob dominates a frame by
a large margin, weight values within reasonable ranges all select the same path.
The cost terms only differentiate on contested frames with multiple comparably
scored blobs at limb-level spacing. Tuning sensitivity requires longer contested
intervals (Jason long-run, Lyra-Hersey lap segments) as the tuning surface.

### 9.7 Claim resolution takes longer than implementation

The audit-to-ship arc ran from 2026-06-10 (audit complete) to 2026-06-12 (schema
14 shipped) -- two days. But the twelve claims required nine check workstreams,
each involving video-level analysis at 4K HEVC decode costs. Lyra-Wheeling
timed out entirely (Claim C, 35th interval: UNDETERMINED). The measurement
infrastructure -- baseline harness, overlay tooling, per-term telemetry -- was
built in parallel with the validation checks and was not present at the start of
the arc.

---

## 10. Schema version context

The cost-model arc spans SCHEMA_VERSION 11 through 14:

| Version | Change | Geometry-affecting |
| --- | --- | --- |
| 11 | (pre-arc baseline) | -- |
| 12 | P15 telemetry fix (path_step_cost; window_head_frame) | No |
| 13 | P12 stride-termination fix (crossing test + clamp) | Yes (stride > 1 only) |
| 14 | WP-COST-1 cost-model rewrite + P10 seed-only fallback | Yes (Stage-4 promoted only) |

Source: [TR_SCHEMA_VERSION_HISTORY.md](../../TR_SCHEMA_VERSION_HISTORY.md).

---

## Source artifact index

| Artifact | Role in this report |
| --- | --- |
| [blob_walk_v2_implementation_audit.md](../audits/blob_walk_v2_implementation_audit.md) | Primary audit: findings P1-P17, N1-N4, L1-L2, claims A-L |
| [blob_walk_v2_validation_report.md](blob_walk_v2_validation_report.md) | Validation verdicts and synthesis |
| [blob_walk_v2_check0_stride_overrun.md](../workstreams/blob_walk_v2_check0_stride_overrun.md) | Claim K: stride-2 overrun |
| [blob_walk_v2_check3_bootstrap_masking.md](../workstreams/blob_walk_v2_check3_bootstrap_masking.md) | Claim J: P10 masking |
| [blob_walk_v2_check4_anchor_lag.md](../workstreams/blob_walk_v2_check4_anchor_lag.md) | Claims F, I: anchor staleness |
| [blob_walk_v2_check5_normalized_cy.md](../workstreams/blob_walk_v2_check5_normalized_cy.md) | Claims D, E: blob merge and jitter |
| [blob_walk_v2_check6_per_term_cost.md](../workstreams/blob_walk_v2_check6_per_term_cost.md) | Claim B: evidence dominance |
| [blob_walk_v2_check7_regressed_split.md](../workstreams/blob_walk_v2_check7_regressed_split.md) | Claim C: ranking vs starvation split |
| [blob_walk_v2_check8_identity_jumps.md](../workstreams/blob_walk_v2_check8_identity_jumps.md) | Claim L: identity jumps |
| [blob_walk_v2_checkg_extrapolation_replay.md](../workstreams/blob_walk_v2_checkg_extrapolation_replay.md) | Claim G: extrapolation hold vs linear |
| [blob_walk_v2_corpus120_run_2026_06_10.md](../workstreams/blob_walk_v2_corpus120_run_2026_06_10.md) | Post-P12 corpus baseline |
| [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md) | Release review: fix, verification, results |
| [blob_walk_v2_fix_phase_roadmap.md](../active/blob_walk_v2_fix_phase_roadmap.md) | Fix-phase sequencing; milestone M1-M5 |
| [blob_walk_v2_p10_fix_plan.md](../../archive/blob_walk_v2_p10_fix_plan.md) | M1 detailed plan (archived) |
| [TR_SCHEMA_VERSION_HISTORY.md](../../TR_SCHEMA_VERSION_HISTORY.md) | Schema version history v12-v14 |
| [walk_viterbi.py](../../../track_runner/blob_walk/walk_viterbi.py) | Shipped cost model |
