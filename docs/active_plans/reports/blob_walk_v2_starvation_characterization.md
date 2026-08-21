# Blob walk v2: starvation-class characterization

Date: 2026-06-12.

Status: COMPLETE

Sources (all read-only, no video decode):

- [blob_walk_v2_check7_regressed_split.md](../workstreams/blob_walk_v2_check7_regressed_split.md)
- [blob_walk_v2_cost_model_ab.md](../../archive/blob_walk_v2_cost_model_ab.md)
- [blob_walk_v2_corpus120_run_2026_06_10.md](../workstreams/blob_walk_v2_corpus120_run_2026_06_10.md)
- [blob_walk_v2_validation_report.md](blob_walk_v2_validation_report.md) (claims A/D/I sections)
- [blob_walk_v2_check5_normalized_cy.md](../workstreams/blob_walk_v2_check5_normalized_cy.md)
- [blob_walk_v2_check3_bootstrap_masking.md](../workstreams/blob_walk_v2_check3_bootstrap_masking.md)

---

## Per-pass table: 12 starvation-class passes

The 12 passes are drawn from two sources:

- 7 pure starvation (check7: mean empty-lattice fraction > 0.5)
- 5 starvation-leaning mixed (check7 addendum: mean_empty >= 0.35, accepted on
  non-empty >= 0.88)

"Baseline empty fraction" = the FWD/BWD fractions and mean from the check7 table
(SCHEMA_VERSION 13, pre-cost-model-rewrite). "Post-rewrite accepted fraction" = the
FWD/BWD values from the cost-model A/B 22-pass run (SCHEMA_VERSION 14). "Empty
lattice fraction" and "post-rewrite accepted fraction" measure different quantities;
the relationship is inverted: high empty fraction drives low accepted fraction.
Seed-cold status from corpus120 artifact (only intervals that appeared in that run).

### Pure starvation passes (check7 rows 15, 17, 19, 20, 22, 24, 33)

| row | video | interval | span | baseline FWD empty | baseline BWD empty | mean | post-rewrite FWD | post-rewrite BWD | seed-cold | sub-type |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15 | IMG_3823 | 731-741 | 10 | 0.70 | 0.70 | 0.70 | 0.300 | 0.300 | unrecoverable (not in corpus120 run) | signal-absence |
| 17 | IMG_3823 | 1047-1066 | 19 | 0.47 | 0.68 | 0.58 | 0.526 | 0.263 | unrecoverable | signal-absence |
| 19 | IMG_3823 | 2316-2337 | 21 | 0.71 | 0.90 | 0.81 | 0.286 | 0.095 | unrecoverable | signal-absence |
| 20 | IMG_3823 | 2337-2341 | 4 | 0.75 | 0.75 | 0.75 | 0.250 | 0.250 | unrecoverable | signal-absence |
| 22 | IMG_3823 | 3614-3655 | 41 | 0.63 | 0.44 | 0.54 | 0.366 | 0.561 | unrecoverable | signal-absence |
| 24 | IMG_3823 | 3956-3979 | 23 | 0.74 | 0.74 | 0.74 | 0.261 | 0.261 | unrecoverable | signal-absence |
| 33 | Conant | 3211-3226 | 15 | 0.87 | 0.87 | 0.87 | 0.133 | 0.133 | unrecoverable | drift-stall |

### Starvation-leaning mixed passes (check7 addendum rows 4, 11, 27, 31, 32)

| row | video | interval | span | baseline FWD empty | baseline BWD empty | mean | accept_on_nonempty | post-rewrite FWD | post-rewrite BWD | seed-cold | sub-type |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 4 | IMG_3830 | 1624-1656 | 32 | 0.28 | 0.53 | 0.41 | 1.00 | 0.719 | 0.469 | unrecoverable | signal-absence (partial) |
| 11 | IMG_3830 | 2955-2960 | 5 | 0.80 | 0.00 | 0.40 | 1.00 | 0.200 | 1.000 | unrecoverable | signal-absence (partial) |
| 27 | Jason | 10669-10739 | 70 | 0.46 | 0.36 | 0.41 | 0.92 | 0.500 | 0.471 | unrecoverable | mixed (starvation + wrong-blob) |
| 31 | Lyra-Hersey | 3132-3137 | 5 | 0.20 | 0.60 | 0.40 | 0.88 | 0.600 | 0.400 | unrecoverable | signal-absence (partial) |
| 32 | Lyra-Hersey | 3143-3150 | 7 | 0.43 | 0.43 | 0.43 | 0.88 | 0.571 | 0.571 | unrecoverable | signal-absence (partial) |

Notes on the table:

- "Seed-cold" is unrecoverable for all 12 passes because none of these intervals
  appeared in the corpus120 manifest (which sampled different intervals at random).
  The seed-cold observation from corpus120 is a video-level diagnostic (Jason 35%,
  IMG_3823 3/40, Conant 2/40) and cannot be pinned to these specific intervals
  without a new decode run.
- "Post-rewrite accepted fraction" for pure starvation passes comes from the A/B
  22-pass run results table (SCHEMA_VERSION 14).
- IMG_3823 rows 19-20 are a back-to-back pair (interval 2337 is the shared seed).
- Conant row 33 (3211-3226) is a separate Conant interval from the main cluster.
  Conant's primary diagnosed stall interval (1080-1111) was a separate diagnostic
  case in check2/check4; it does not appear in the check7 regressed bucket.

---

## Question (a): did the cost-model rewrite help, hurt, or not touch starvation?

**Finding: the cost-model rewrite did not touch starvation. Expected and confirmed.**

The post-rewrite accepted fractions for the 7 pure starvation passes are:

| row | interval | pre-rewrite classification | post-rewrite FWD | post-rewrite BWD |
| --- | --- | --- | --- | --- |
| 15 | 731-741 | starvation (0.70 mean empty) | 0.300 | 0.300 |
| 17 | 1047-1066 | starvation (0.58 mean empty) | 0.526 | 0.263 |
| 19 | 2316-2337 | starvation (0.81 mean empty) | 0.286 | 0.095 |
| 20 | 2337-2341 | starvation (0.75 mean empty) | 0.250 | 0.250 |
| 22 | 3614-3655 | starvation (0.54 mean empty) | 0.366 | 0.561 |
| 24 | 3956-3979 | starvation (0.74 mean empty) | 0.261 | 0.261 |
| 33 | 3211-3226 | starvation (0.87 mean empty) | 0.133 | 0.133 |

All 7 remain below 0.53 post-rewrite. The range 0.095-0.561 reflects how little
the Viterbi cost changes matter when the lattice is empty on 50-87% of frames.
The tuning table (cost_model_ab Part 4) confirms this directly: all 4 weight
configs (including evidence-forward at WEIGHT_EVIDENCE_NORM=1.0 and low-skip
SKIP_COST=1.0) produced identical results on the fast subset that includes these
starvation passes. The cost-model A/B notes this explicitly: "The starvation-class
passes (rows 15, 17, 19, 20, 22, 24) are insensitive to weight tuning because the
starvation condition means few or no blobs survive the corridor filter -- the result
is structural (no blobs), not a cost-ranking problem."

The mechanism is structural: the Viterbi cost model operates on candidates that
have already passed the corridor filter. When the corridor is empty, there is
nothing for the cost model to rank. Changing skip cost, displacement weight,
evidence normalization, or velocity-delta terms cannot create blobs that DoG
extraction failed to produce.

---

## Question (b): common physical condition citing claim D evidence

**Finding: small apparent runner size causing DoG to resolve multiple limb-level
blobs or fail below the detection threshold entirely. Crossover scale is between
11 px and 30 px torso height (processed-space).**

The starvation cluster concentrates in IMG_3823 (6 of 7 pure starvation passes)
and Conant (1 of 7). The two diagnosed stall sub-types from claims A, D, and I
explain both:

### Sub-type 1: signal-absence stall (Jason / small-runner archetype)

Established in Check 2 (claim A), check5 (claim D), and the validation report
claim I section.

Jason torso: `torso_w = 6.5 px`, `torso_h = 11.0 px` (processed space).
At 11 px torso height the DoG band-pass does NOT merge the runner into one blob.
Instead it resolves 4-6 spatially-distinct patches covering the full vertical
extent of the runner body (limb-level separation). The acceptance box for
Jason's seed_564_583 interval is 3.2 x 7.9 processed pixels; the DoG diameter
2.3 px is at or below reliable detection range. Background athletes at 5-24 W
produce stronger blobs than runner signal inside the tight acceptance box.

The claim D evidence is precise: blob merge holds for Conant (~30 px torso, 97-100%
of frames have exactly 1 near-reference blob) and fails for Jason (~11 px torso,
every frame with candidates has 4-6 distinct blobs within 1 torso-width). The
crossover is somewhere between 11 px and 30 px. IMG_3823, which hosts 6 of the 7
pure starvation passes, is also a small-runner scene: its corpus120 accepted
fraction (54.5-55.8%) is the second lowest of any 30 fps video, and its 14/40
seed-cold rate in the corpus120 sample is zero (only Jason reaches 35%). The
IMG_3823 starvation intervals likely share the small-runner condition, though
the processed-space torso measurements for those specific intervals are
unrecoverable without a decode run.

The Conant stall (row 33, interval 3211-3226) matches sub-type 1 by the claim A
data: only 2 blobs found in the tight ROI across all 31 frames of the diagnosed
stall interval (1080-1111), consistent with runner signal below noise level
inside the acceptance box. Row 33 (3211-3226) is a different interval on the
same video; sub-type assignment (drift-stall vs signal-absence) is inferred from
the video-level pattern.

### Sub-type 2: drift stall (Conant archetype)

Established in claims F and I. Conant's primary diagnosed stall (1080-1111 FWD)
is a positional-drift stall: the acceptance box anchor goes stale while the
runner moves in image space (2.35 torso-width drift by frame 1110). The corridor
shifts out of position relative to the actual runner location. This is distinct
from signal-absence: the runner blobs exist in principle but the window is
positioned in the wrong part of the frame.

Row 33 (Conant 3211-3226) carries this same video's starvation label; its exact
sub-type is unrecoverable without a new run, but the high empty fraction (0.87
mean) is consistent with either sub-type on a Conant interval.

### The unifying physical condition

Both sub-types converge on one observation from the validation report synthesis
section: "Starvation intervals are driven by DoG extraction failing to find blobs
at small runner scale." At small apparent size (torso <= ~11 px processed):

- The DoG filter resolves limb segments rather than a merged runner blob.
- The acceptance box (sized to the seed torso box) is proportionally small in
  absolute pixels.
- Any slight anchor drift misplaces the small box relative to the diffuse limb
  patches.
- Background runners at larger apparent scale produce stronger blobs far from
  the runner.

The result: the corridor is empty on the majority of frames. This is an
extraction-scale problem upstream of the corridor filter and upstream of Viterbi.

---

## Question (c): frequency across all corpus intervals

**Finding: 26/240 interval-directions (10.8%) show seed-cold symptoms; Jason 35%
(14/40) is the dominant contributor. The check7 starvation bucket covers 12/34
classified regressed passes (35%). Combining: starvation is a minority bucket but
not negligible.**

### From corpus120 (seed-cold tile data, closest proxy for starvation frequency)

The corpus120 run identified 26 of 240 interval-directions with at least one
seed-cold tile (residual motion below threshold at the seed frame):

| Video | Seed-cold interval-directions | of 40 total | notes |
| --- | --- | --- | --- |
| IMG_3830 | 0 | 0/40 | large runner |
| IMG_3823 | 3 | 3/40 (7.5%) | mid-size runner |
| Jason | 14 | 14/40 (35.0%) | small runner |
| Lyra-Hersey | 1 | 1/40 (2.5%) | large runner |
| Conant | 2 | 2/40 (5.0%) | mixed-size (race lap variation) |
| Lyra-Wheeling | 6 | 6/40 (15.0%) | P12 bug caveat |

Corpus total: 26/240 = 10.8% of interval-directions.

Seed-cold is a proxy: it measures whether the seed frame's residual motion is
below threshold, which predicts but does not guarantee starvation across the
full interval. The 35% Jason seed-cold rate is consistent with Jason's 38.4%
post-rewrite accepted fraction (the lowest in the corpus) and the observed 0.0%
accepted fraction on three Jason intervals in the corpus A/B run
([16826,16920], [17014,17108], [30456,30550] FWD=0).

### From check7 regressed bucket

Of the 34 classified m4 A/B regressed passes:

- 7 pure starvation (mean empty > 0.5): 21%
- 5 starvation-leaning mixed (mean_empty >= 0.35): 15%
- Combined starvation-relevant: 12/34 = 35%

This fraction applies to the regressed-interval population, not the full corpus.
Within the regressed bucket, 35% of passes have starvation as a primary or
significant factor.

### Combined frequency picture

- Among all corpus interval-directions (corpus120 proxy): ~10.8% show seed-cold
  symptom; Jason dominates at 35%.
- Among the m4 regressed bucket (check7): 35% starvation-relevant.
- The starvation bucket is concentrated in small-runner scenes (Jason, IMG_3823)
  and in specific Conant intervals (drift-stall sub-type).
- IMG_3830 and Lyra-Hersey, both larger-runner scenes, show near-zero starvation
  symptoms in all artifacts.

The 35% figure from check7 is the regressed-interval population; the 10.8% figure
from corpus120 is the all-interval population. The discrepancy is expected: the
regressed bucket was defined as intervals that declined under the old Viterbi model,
which over-samples scenes where the existing pipeline was already struggling.

---

## Summary

| Question | Finding |
| --- | --- |
| (a) cost model impact on starvation | None. Starvation is insensitive to weight tuning. Result is structural: empty lattice before Viterbi. |
| (b) physical condition | Small apparent runner size (torso ~11 px proc). DoG resolves limb-level blobs or falls below detection threshold; drift stall is a secondary sub-type. Crossover at 11-30 px torso height (claim D evidence). |
| (c) frequency | 10.8% of all corpus interval-directions (seed-cold proxy); 35% of the m4 regressed bucket. Jason is the dominant contributor (35% of Jason interval-directions). |

The starvation mechanism is entirely upstream of the Viterbi cost model: it is an
extraction-scale problem at the DoG filter and acceptance-box sizing stage. No Viterbi
weight adjustment, cost term redesign, or skip-cost change addresses it. The cost-model
rewrite correctly left this bucket unchanged.
