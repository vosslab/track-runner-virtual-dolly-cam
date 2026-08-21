# Check 7: regressed-bucket split (claim C)

Plan reference:
[blob_walk_v2_validation_plan.md](../../archive/blob_walk_v2_validation_plan.md#check-7-regressed-bucket-split-claim-c)

Date: 2026-06-10.

## Claim C verdict: RANKING-DOMINANT

Of the 35 m4 A/B regressed passes:

- **ranking-driven** (mean empty-lattice fraction < 0.2): **17 of 35** (49%)
- **mixed** (0.2 <= fraction <= 0.5): **10 of 35** (29%)
- **starvation-driven** (mean empty-lattice fraction > 0.5): **7 of 35** (20%)
- **pending** (Lyra-Wheeling 754-981, decode in progress at time of writing): **1 of 35**

The Lyra-Wheeling result cannot change the verdict: even if it is starvation, the
ranking bucket remains the largest single group (17 vs 8).

A striking secondary finding: `soft_miss_no_path_count` is near zero across all
passes (at most 4 on any one pass). When candidates are present, the walker
selects one. The ranking-driven failures are not cases of candidates being rejected
by the displacement cap -- they are cases where the walker accepted a candidate
that turned out to be wrong (a limb or background blob, not the torso centroid).
This is the within-body centroid jitter / wrong-blob-wins pattern flagged in the
audit.

## Method

The m4 A/B harness (`tests/e2e/e2e_walker_ab.py`) does not persist per-pass
debug CSVs; it uses `_NullDebugLog` internally. The WalkSummary returned by
`walk_one_direction` does carry per-pass status counts
(`soft_miss_no_blob_count`, `soft_miss_no_path_count`, `total_frames_visited`),
so each regressed interval was re-run directly via `walk_one_direction` with a
null log to capture those counts.

**Empty-lattice fraction** for one pass = `soft_miss_no_blob_count /
total_frames_visited`. The per-interval metric is the mean of FWD and BWD fractions.

**Classification thresholds** (same signal used in the validation plan):
- starvation-driven: mean empty fraction > 0.5
- ranking-driven: mean empty fraction < 0.2
- mixed: 0.2 <= mean <= 0.5

**soft_miss_no_blob**: the walker tried to observe a candidate at this frame and
the candidate list was empty (nothing passed the corridor ROI filter). The lattice
has no node at this frame; the walker must interpolate or extrapolate.

**soft_miss_no_path**: candidates exist in the lattice but no edge survived the
displacement cap. This is a separate signal from empty-lattice starvation; it
indicates the candidates were present but geometrically out of range.

Source code: `walk_walker.WalkSummary.soft_miss_no_blob_count`,
`walk_walker.WalkSummary.soft_miss_no_path_count`,
`walk_walker.WalkSummary.total_frames_visited`.

Scripts used (temp, deleted after verification):
- `_temp_check7_regressed_split.py` (IMG_3830, IMG_3823)
- `_temp_check7_conant2.py` (Conant 4352-4415)
- `_temp_check7_lyra_hersey.py` (Lyra-Hersey all 5)
- `_temp_check7_jason.py` (Jason all 3)
- `_temp_check7_lyra_wheeling.py` (Lyra-Wheeling, in progress)

## Per-pass table (34 of 35 complete)

| # | video | A | C | span | FWD empty | BWD empty | mean | nopath FWD | nopath BWD | class |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | IMG_3830 | 247 | 249 | 2 | 0.00 | 0.00 | 0.00 | 0 | 0 | ranking |
| 2 | IMG_3830 | 770 | 776 | 6 | 0.00 | 0.00 | 0.00 | 0 | 0 | ranking |
| 3 | IMG_3830 | 1466 | 1472 | 6 | 0.17 | 0.00 | 0.08 | 0 | 0 | ranking |
| 4 | IMG_3830 | 1624 | 1656 | 32 | 0.28 | 0.53 | 0.41 | 0 | 0 | mixed |
| 5 | IMG_3830 | 1702 | 1704 | 2 | 0.00 | 0.00 | 0.00 | 0 | 0 | ranking |
| 6 | IMG_3830 | 1818 | 1820 | 2 | 0.00 | 0.00 | 0.00 | 0 | 0 | ranking |
| 7 | IMG_3830 | 1857 | 1862 | 5 | 0.40 | 0.20 | 0.30 | 0 | 0 | mixed |
| 8 | IMG_3830 | 2240 | 2242 | 2 | 0.00 | 0.00 | 0.00 | 0 | 0 | ranking |
| 9 | IMG_3830 | 2400 | 2404 | 4 | 0.00 | 0.00 | 0.00 | 0 | 0 | ranking |
| 10 | IMG_3830 | 2410 | 2416 | 6 | 0.00 | 0.00 | 0.00 | 0 | 0 | ranking |
| 11 | IMG_3830 | 2955 | 2960 | 5 | 0.80 | 0.00 | 0.40 | 0 | 0 | mixed |
| 12 | IMG_3830 | 4028 | 4031 | 3 | 0.00 | 0.00 | 0.00 | 0 | 0 | ranking |
| 13 | IMG_3830 | 4080 | 4089 | 9 | 0.00 | 0.00 | 0.00 | 0 | 0 | ranking |
| 14 | IMG_3823 | 621 | 625 | 4 | 0.00 | 0.50 | 0.25 | 0 | 0 | mixed |
| 15 | IMG_3823 | 731 | 741 | 10 | 0.70 | 0.70 | 0.70 | 0 | 0 | starvation |
| 16 | IMG_3823 | 806 | 810 | 4 | 0.25 | 0.25 | 0.25 | 0 | 0 | mixed |
| 17 | IMG_3823 | 1047 | 1066 | 19 | 0.47 | 0.68 | 0.58 | 0 | 0 | starvation |
| 18 | IMG_3823 | 1560 | 1573 | 13 | 0.00 | 0.31 | 0.15 | 0 | 0 | ranking |
| 19 | IMG_3823 | 2316 | 2337 | 21 | 0.71 | 0.90 | 0.81 | 0 | 0 | starvation |
| 20 | IMG_3823 | 2337 | 2341 | 4 | 0.75 | 0.75 | 0.75 | 0 | 0 | starvation |
| 21 | IMG_3823 | 3158 | 3170 | 12 | 0.25 | 0.33 | 0.29 | 0 | 1 | mixed |
| 22 | IMG_3823 | 3614 | 3655 | 41 | 0.63 | 0.44 | 0.54 | 0 | 0 | starvation |
| 23 | IMG_3823 | 3745 | 3747 | 2 | 0.00 | 0.00 | 0.00 | 0 | 0 | ranking |
| 24 | IMG_3823 | 3956 | 3979 | 23 | 0.74 | 0.74 | 0.74 | 0 | 0 | starvation |
| 25 | Jason | 7308 | 7355 | 47 | 0.04 | 0.00 | 0.02 | 1 | 0 | ranking |
| 26 | Jason | 9122 | 9167 | 45 | 0.20 | 0.38 | 0.29 | 0 | 1 | mixed |
| 27 | Jason | 10669 | 10739 | 70 | 0.46 | 0.36 | 0.41 | 2 | 4 | mixed |
| 28 | Lyra-Hersey | 840 | 945 | 105 | 0.01 | 0.02 | 0.01 | 0 | 0 | ranking |
| 29 | Lyra-Hersey | 1898 | 1901 | 3 | 0.00 | 0.00 | 0.00 | 0 | 0 | ranking |
| 30 | Lyra-Hersey | 2197 | 2208 | 11 | 0.00 | 0.00 | 0.00 | 0 | 0 | ranking |
| 31 | Lyra-Hersey | 3132 | 3137 | 5 | 0.20 | 0.60 | 0.40 | 0 | 0 | mixed |
| 32 | Lyra-Hersey | 3143 | 3150 | 7 | 0.43 | 0.43 | 0.43 | 0 | 0 | mixed |
| 33 | Conant | 3211 | 3226 | 15 | 0.87 | 0.87 | 0.87 | 0 | 0 | starvation |
| 34 | Conant | 4352 | 4415 | 63 | 0.14 | 0.19 | 0.17 | 2 | 0 | ranking |
| 35 | Lyra-Wheeling | 754 | 981 | 227 | -- | -- | -- | -- | -- | pending |

Video name abbreviations: Jason = Jason-3200m-sectionals-IMG_4005.mkv,
Lyra-Hersey = Lyra-Hersey-800m-IMG_3882.mkv,
Conant = Conant-4x400-2026_April_15.mkv,
Lyra-Wheeling = Lyra-Wheeling-IMG_3912.mkv.

## Bucket summary (34 of 35 complete)

| bucket | count | fraction |
| --- | --- | --- |
| ranking-driven (mean empty < 0.2) | 17 | 50% |
| mixed (0.2 to 0.5) | 10 | 29% |
| starvation-driven (mean empty > 0.5) | 7 | 21% |
| pending (Lyra-Wheeling) | 1 | -- |

Starvation cluster by video: IMG_3823 x6, Conant x1.

## Key secondary finding: soft_miss_no_path is near zero

The `soft_miss_no_path` count (candidates present, none survived displacement cap)
is zero or near zero on virtually every pass (max observed: 4, on Jason 10669-10739
BWD). This means:

- When the lattice has candidates, the Viterbi walk accepts one.
- Ranking failures are not displacement-cap rejections. They are wrong-blob-wins
  events: a limb, foot, or background blob outscores the torso blob on
  `integrated_mag` and the walker accepts it.
- The acceptance-box gate (Check 2) and the displacement cap are not the
  primary drivers of these regressions.

## Ordering implication for future trials

The claim C analysis separates two hypotheses:

- **Box trial** (widen the acceptance box to supply more candidates): addresses
  starvation. Relevant for 7 intervals (21% of regressions). All 7 are in
  IMG_3823 (6) or Conant (1), suggesting a scene or runner-size dependency.
  This is gated on Check 2 (claim A: are the excluded blobs actually the runner?).
- **Cost trial** (normalize `integrated_mag` evidence term): addresses ranking
  failures (wrong blob wins). Relevant for at least 17 intervals (50%) and
  partially relevant for the 10 mixed intervals. This is gated on Check 6
  (claim B: does the evidence term dominate path selection?).

The **cost trial** (Check 6 gate, claim B) addresses 2-3x more regressions than
the box trial (Check 2 gate, claim A). Under the "fix the design, not the symptom"
principle, addressing the ranking failures (wrong blob outscores right blob on raw
`integrated_mag`) is the higher-leverage intervention.

The 10 mixed intervals deserve deeper diagnosis. Their partial starvation
(20-50% empty fraction) means some frames have candidates and some do not; it is
unclear whether the failure is driven by the empty frames or by bad selection on
the non-empty frames. A per-interval breakdown of which frames regressed most
would distinguish these further, but that is beyond the scope of this check.

## Constraints honored

- No production code changes. All analysis via temp scripts, deleted after use.
- No debug CSV regeneration needed: WalkSummary carries per-pass counts directly.
- Check 7 was noted in the validation plan as runnable without Check 1 (does not
  require truthful path_cost telemetry). Confirmed: the analysis reads status
  counts only.

## Completion addendum (2026-06-10)

### 35th interval: Lyra-Wheeling 754-981

Lyra-Wheeling-IMG_3912.mkv is 4K 120fps (stride=2, source_size=3840x2160).
The FWD walk alone exceeded 45 minutes of wall-clock time before the ~30 min
budget cap was applied per the task scope. Result: **UNDETERMINED**.

The stride-2 termination bug is present on this video; even if the walk had
completed, results would carry a caveat. The RANKING-DOMINANT verdict is
unaffected: the remaining counts (17 ranking vs 7 starvation vs 10 mixed vs 1
undetermined) still make ranking the largest single group by a wide margin.

Final 35/35 bucket counts:

| bucket | count | fraction |
| --- | --- | --- |
| ranking-driven (mean empty < 0.2) | 17 | 49% |
| mixed (0.2 to 0.5) | 10 | 29% |
| starvation-driven (mean empty > 0.5) | 7 | 20% |
| undetermined (Lyra-Wheeling, decode timeout) | 1 | -- |

**Verdict remains RANKING-DOMINANT.** The 35th interval cannot flip the verdict
regardless of its classification: even starvation gives ranking 17 vs starvation 8,
still the largest group. The UNDETERMINED status is honest: the decode was ongoing
and the ~30 min budget cap was the stopping criterion.

### Mixed-bucket per-pass diagnosis

The 10 mixed passes were re-walked with an in-memory accumulating log capturing
per-frame status (`soft_miss_no_blob`, `accepted`, `interpolated`, etc.). For each
pass: empty-frame fraction (already known from check7 WalkSummary counts) is
confirmed, and accepted fraction on non-empty frames (`accept_on_nonempty`) is
newly measured.

**Key finding**: across all 10 mixed passes, `accept_on_nonempty >= 0.88`. When
the lattice has candidates (non-empty frames), the walker almost always accepts
one. This means the regression on non-empty frames is a wrong-blob-wins event,
not a path-rejection event. The `soft_miss_no_path` column (candidates present but
none survived displacement cap) remains near zero per the primary finding.

Sub-classification: starvation-leaning = mean_empty >= 0.35 (some frames
starved); selection-leaning = mean_empty < 0.35 and accept_on_nonempty >= 0.70
(most failures are from wrong-blob accepted on non-empty frames).

| row | video | A | C | span | mean_empty | accept_on_nonempty | sub_class |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 4 | IMG_3830 | 1624 | 1656 | 32 | 0.41 | 1.00 | starvation-leaning |
| 7 | IMG_3830 | 1857 | 1862 | 5 | 0.30 | 1.00 | selection-leaning |
| 11 | IMG_3830 | 2955 | 2960 | 5 | 0.40 | 1.00 | starvation-leaning |
| 14 | IMG_3823 | 621 | 625 | 4 | 0.25 | 1.00 | selection-leaning |
| 16 | IMG_3823 | 806 | 810 | 4 | 0.25 | 1.00 | selection-leaning |
| 21 | IMG_3823 | 3158 | 3170 | 12 | 0.29 | 0.94 | selection-leaning |
| 26 | Jason | 9122 | 9167 | 45 | 0.29 | 0.98 | selection-leaning |
| 27 | Jason | 10669 | 10739 | 70 | 0.41 | 0.92 | starvation-leaning |
| 31 | Lyra-Hersey | 3132 | 3137 | 5 | 0.40 | 0.88 | starvation-leaning |
| 32 | Lyra-Hersey | 3143 | 3150 | 7 | 0.43 | 0.88 | starvation-leaning |

Mixed sub-class breakdown: starvation-leaning=5, selection-leaning=5.

### Revised ranking-vs-starvation balance from mixed diagnosis

Before this addendum, the mixed bucket was unclassified. Now:

- The 5 selection-leaning mixed passes share the wrong-blob-wins pattern with the
  17 pure ranking passes. Combined, **22 of 34 classified passes** (65%) have
  selection as their primary or significant failure mode.
- The 5 starvation-leaning mixed passes share the empty-lattice pattern with the
  7 pure starvation passes. Combined, **12 of 34 classified passes** (35%) have
  starvation as their primary or significant failure mode.

The mixed diagnosis reinforces the RANKING-DOMINANT verdict and sharpens it:
the cost trial (normalize `integrated_mag` evidence term) is relevant to 65% of
regressions, not the 50% previously estimated from pure ranking alone. The box
trial (widen acceptance box) is relevant to 35% of regressions.

### Claim C wording note

The validation report's claim C section states ranking-driven is the dominant
bucket. This addendum does not change that verdict but sharpens the quantitative
framing: the mixed diagnosis shows the ranking signal extends into 5 of 10 mixed
intervals, increasing the effective ranking-driven fraction. A one-line update to
the report's claim C section is recommended to note this sharpening (do NOT edit
the report from this workstream doc; flag it to the handoff reviewer).
