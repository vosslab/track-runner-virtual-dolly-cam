# Pair-local promotion gate

This M2 record applies the pre-registered rules in
[pair_local_sagitta_audit.md](pair_local_sagitta_audit.md). It records
the policy inputs selected from that audit; it does not alter the solve model.

## Selected branch

- Eligible triples use `visible` and `partial` seeds.
- All five bins are decision-bearing: each has at least 30 triples.
- Branch C does not apply: the `[0,2)` p90 is `0.286`, below `0.5`.
- Branch B applies: p90 increases across the decision-bearing bins:
  `0.286`, `0.392`, `0.490`, `1.047`, and `1031.882`; the last is at least `1.0`.
- The Branch B threshold is `10` torso widths: `[10,20)` is the lowest
  decision-bearing bin with p90 at least `1.0`.
- `chord_span_widths >= 10` joins the promotion terms under Branch B.

## Walker budgets

The budget for each video is `max(measured, ceil(0.10 * post_race_frames))`.
`measured` is the current low-or-fair walker-frame count reported by M1.

| Video | Measured | Post-race frames | Floor | Budget |
| --- | ---: | ---: | ---: | ---: |
| 2025-Glenbrook_South-1600m-IMG_1503 | 14126 | 17541 | 1755 | 14126 |
| Conant-4x400-2026_April_15 | 33 | 13392 | 1340 | 1340 |
| Hononega-Orion-1600m-IMG_3629 | 10895 | 17207 | 1721 | 10895 |
| Hononega-Orion_600m-IMG_3702 | 239 | 5187 | 519 | 519 |
| Hononega-Varsity_4x400m-IMG_3707 | 1369 | 14663 | 1467 | 1467 |
| IMG_3627 | 5681 | 8110 | 811 | 5681 |
| IMG_3823 | 209 | 3999 | 400 | 400 |
| IMG_3830 | 279 | 4004 | 401 | 401 |
| IMG_3839 | 7560 | 7607 | 761 | 7560 |
| Jason-3200m-sectionals-IMG_4005 | 3922 | 35816 | 3582 | 3922 |
| Lyra-Hersey-800m-IMG_3882 | 93 | 13425 | 1343 | 1343 |
| Lyra-Wheeling-IMG_3912 | 29 | 26735 | 2674 | 2674 |

Jason's motion-computation exclusion affects the pooled sagitta statistics only.
Its reported current walker frames and post-race frames still determine its budget.

## Packing rule

- Risk is the float count of triggered retained predicates. Each interval receives
  one point for each of `motion_quality < 0.5`, `occlusion_fraction > 0.3`,
  `size_consistency < 0.5`, and `interval_duration > 10 * fps`. Under Branch B,
  `chord_span_widths >= 10` adds one point.
- An interval is eligible for promotion when `risk > 0`.
- The count adds no weights or signal thresholds beyond the retained predicates
  and the selected Branch B threshold.
- Sort post-race intervals by risk descending; break ties with lower `start_frame`.
- Promote an interval only when its full frame count fits the remaining budget.
- Skip a non-fitting interval and continue through the sorted intervals.
- Stop when no remaining interval fits.
- Promoted walker frames must be at most the stated budget. Under-spend is valid.
- The promote floor is `0`: an interval exceeds it exactly when `risk > 0`.
  When a post-race interval exceeds that floor and fits the budget, promote at
  least one interval.

## Outlier classification

The M1 audit retains all outliers in branch selection. It classifies the ten
largest-error triples mechanically as follows.

| Video | Frames L/M/R | Classification |
| --- | --- | --- |
| Hononega-Varsity_4x400m-IMG_3707 | 14323/14364/14400 | geometry_suspect |
| Hononega-Varsity_4x400m-IMG_3707 | 13140/13170/13200 | geometry_suspect |
| Hononega-Orion-1600m-IMG_3629 | 16710/16842/16974 | annotation_suspect |
| Hononega-Varsity_4x400m-IMG_3707 | 11341/11355/11370 | geometry_suspect |
| Hononega-Varsity_4x400m-IMG_3707 | 9634/9650/9720 | geometry_suspect |
| Hononega-Varsity_4x400m-IMG_3707 | 12961/12996/13020 | geometry_suspect |
| Hononega-Varsity_4x400m-IMG_3707 | 9480/9540/9576 | geometry_suspect |
| Hononega-Varsity_4x400m-IMG_3707 | 11761/11832/11868 | geometry_suspect |
| Hononega-Orion_600m-IMG_3702 | 3935/3944/3948 | geometry_suspect |
| Hononega-Varsity_4x400m-IMG_3707 | 13723/13764/13800 | geometry_suspect |

## M6 instruction

M6 uses the risk count, `risk > 0` eligibility rule, Branch B threshold,
per-video budgets, and packing rule above. This supplies the machine-executable
promotion instruction without an additional model, weight, or signal threshold.
