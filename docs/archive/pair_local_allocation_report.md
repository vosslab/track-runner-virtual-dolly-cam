# Pair-local allocation report

## Pre-resolve baseline

This report preserves the allocation baseline before M13 replaces the corpus's
schema-10 solve artifacts. It does not reinterpret a legacy selection as a
schema-15 risk decision: **before selected** means the existing non-pre-race
diagnostic intervals whose `confidence_tier` is `low` or `fair`. Their inclusive
frame spans are the legacy walker frames reported by M1 and used by M2.

The twelve stems come from the root `tr_config/*.track_runner.seeds.json` files.
The count and frames below come from each matching
`*.track_runner.interval_scores.json`; every frame total matches the M1 budget
table. `race_start_frame` and the post-race frame count come from the diagnostic
`pre_race_reference` and the current camera-motion `dx` length, except Jason,
whose M1 record supplies the documented motion-decode exclusion and frame count.
The floor is `ceil(0.10 * post_race_frames)` and the budget is M2's
`max(legacy walker frames, floor)`.

| Stem | Before selected | Before walker frames | Post-race frames | Floor | Budget | After selected | After walker frames | After wall time |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| 2025-Glenbrook_South-1600m-IMG_1503 | 56 | 14126 | 17541 | 1755 | 14126 | 2 | 1682 | 1606.1 s |
| Conant-4x400-2026_April_15 | 2 | 33 | 13392 | 1340 | 1340 | 28 | 1339 | 1563.1 s |
| Hononega-Orion-1600m-IMG_3629 | 153 | 10895 | 17207 | 1721 | 10895 | 33 | 1720 | 5030.3 s |
| Hononega-Orion_600m-IMG_3702 | 11 | 239 | 5187 | 519 | 519 | 31 | 519 | 596.5 s |
| Hononega-Varsity_4x400m-IMG_3707 | 36 | 1369 | 14663 | 1467 | 1467 | 76 | 1466 | 2293.5 s |
| IMG_3627 | 51 | 5681 | 8110 | 811 | 5681 | 11 | 801 | 1653.4 s |
| IMG_3823 | 23 | 209 | 3999 | 400 | 400 | 44 | 396 | 96.8 s |
| IMG_3830 | 54 | 279 | 4004 | 401 | 401 | 81 | 401 | 163.1 s |
| IMG_3839 | 20 | 7560 | 7607 | 761 | 7560 | 2 | 756 | 1580.3 s |
| Jason-3200m-sectionals-IMG_4005 | 35 | 3922 | 35816 | 3582 | 3922 | `pre_existing` decode tail | unavailable | failed before solve |
| Lyra-Hersey-800m-IMG_3882 | 5 | 93 | 13425 | 1343 | 1343 | 84 | 1343 | 971.7 s |
| Lyra-Wheeling-IMG_3912 | 2 | 29 | 26735 | 2674 | 2674 | 16 | 2623 | 1141.6 s |

No existing solve log establishes a historical wall time. It is therefore
unavailable rather than inferred; M13 will record only the new run's wall time.

## Branch-B chord baseline

M2 selected Branch B with `chord_span_widths >= 10`. For the legacy selected
intervals, the following summary applies the current endpoint-chord owner to
the pre-resolve seeds and camera motion: endpoints are converted to scene space,
distance is divided by their midpoint torso width. It is not a new signal or a
retroactive promotion decision. It supplies the comparison baseline for the
M14 after rows: did schema-15 allocation move walker frames toward high-chord
intervals?

| Stem | Selected with chord >= 10 | Selected with usable chord | p50 chord widths | p90 chord widths |
| --- | ---: | ---: | ---: | ---: |
| 2025-Glenbrook_South-1600m-IMG_1503 | 55 | 56 | 72.534 | 526.659 |
| Conant-4x400-2026_April_15 | 0 | 2 | 3.120 | 3.415 |
| Hononega-Orion-1600m-IMG_3629 | 87 | 153 | 13.566 | 1194.960 |
| Hononega-Orion_600m-IMG_3702 | 3 | 11 | 2.866 | 221.699 |
| Hononega-Varsity_4x400m-IMG_3707 | 21 | 36 | 15.942 | 2746.681 |
| IMG_3627 | 37 | 51 | 31.077 | 961.395 |
| IMG_3823 | 4 | 23 | 2.598 | 21.565 |
| IMG_3830 | 0 | 54 | 0.543 | 1.298 |
| IMG_3839 | 20 | 20 | 90.761 | 115.019 |
| Jason-3200m-sectionals-IMG_4005 | unavailable | unavailable | unavailable | unavailable |
| Lyra-Hersey-800m-IMG_3882 | 0 | 5 | 2.524 | 4.557 |
| Lyra-Wheeling-IMG_3912 | 0 | 2 | 2.063 | 3.650 |

Jason has no usable pre-resolve camera-motion artifact because M1's motion
computation stopped at the known decode failure. Its legacy walker frame count
and budget remain valid M1/M2 evidence, but there is no honest scene-space
endpoint-chord value to report before M13.

For context, the M1 all-triple pooled Branch-B evidence was p90 error `1.047`
in `[10,20)` chord widths and `1031.882` in `[20,inf)`. That is the decision
record's basis for the threshold, separate from the selected-interval summary
above.

## After-resolve completion

Eleven corpus solves completed at schema 15 and respect the approved one-sided
budget. Under-spend is expected when the next whole interval does not fit or no
more eligible intervals remain; it is not a percentage failure.

| Stem | Positive-risk intervals | Promoted intervals | Promoted chord >= 10 | Promoted chord range |
| --- | ---: | ---: | ---: | ---: |
| 2025-Glenbrook_South-1600m-IMG_1503 | 68 | 2 | 2 | 113.324-276.301 |
| Conant-4x400-2026_April_15 | 129 | 28 | 24 | 2.751-44.853 |
| Hononega-Orion-1600m-IMG_3629 | 174 | 33 | 27 | 0.471-1640.303 |
| Hononega-Orion_600m-IMG_3702 | 238 | 31 | 30 | 0.622-1045.833 |
| Hononega-Varsity_4x400m-IMG_3707 | 428 | 76 | 75 | 0.387-5755.330 |
| IMG_3627 | 62 | 11 | 11 | 10.288-2088.587 |
| IMG_3823 | 46 | 44 | 11 | 0.150-35.618 |
| IMG_3830 | 97 | 81 | 2 | 0.091-12.646 |
| IMG_3839 | 20 | 2 | 2 | 93.245-112.522 |
| Lyra-Hersey-800m-IMG_3882 | 182 | 84 | 26 | 0.036-215.484 |
| Lyra-Wheeling-IMG_3912 | 50 | 16 | 14 | 0.080-37.335 |

These rows establish that Branch B moves much of Orion's automatic allocation
toward high-chord intervals. The real fresh-solve encode also found 104
unpromoted post-race intervals whose reloaded analytical centers leave the
source frame. That is target/refine evidence, not a requirement that Stage 4
solve every interval automatically: the five sampled failures are already in
the existing target ordering, and direct real-video walker replays solve all
five when selected. The lifecycle evidence is in
[pair_local_encode_evaluation.md](pair_local_encode_evaluation.md).

The eleven successful stems are all at schema 15 and remain at or below their
approved budgets. Jason has no after-allocation row because video validation
fails before solve; its schema-10 artifact and documented terminal-frame failure
are preserved and classified `pre_existing` in
`extended_corpus_outcomes.md`. No chord or runtime
value is invented for a solve that did not run.
