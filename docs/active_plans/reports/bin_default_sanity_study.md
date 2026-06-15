# Bin default sanity study (M2 / WS2-verify)

Empirical safety check that the new binned walker default does not break
tracking. This is a bounded sanity study, NOT a full A/B baseline. Read-only on
production code: the study reuses the shared `open_analysis_reader`,
`load_seeds_view`, `load_scene_transform`, and `run_interval_walk` paths and
walks a fixed, pre-recorded interval set.

The constant under test is `TARGET_DEFAULT_WIDTH_PX = 1440` in
[common_tools/frame_reader.py](../../../common_tools/frame_reader.py); the
default bin is `select_default_bin_factor(source_width) =
max(1, floor(source_width / 1440))`.

## Chosen video and resolved default bin

| Field | Value |
| --- | --- |
| Video | `Lyra-Wheeling-IMG_3912` |
| Source dims | 3840 x 2160 (true 4K) |
| Default bin (floor 3840/1440 = 2) | bin_factor = 2 -> processed 1920 x 1080 |
| Decode file | `Lyra-Wheeling-IMG_3912.fastread.mkv` (full-res sibling; binning is post-decode so the comparison is unaffected by which sibling decodes) |

This is the only repo source whose default bin is >= 2. The four candidate
videos named in the brief (Conant, Hononega-Orion_600m, IMG_3823, IMG_3830) are
all 2816-wide or smaller, so they resolve to default bin = 1 and would exercise
no binning. Lyra-Wheeling is the genuine 4K case that actually triggers the
new default (4K -> bin2), so it was chosen instead. It has an
`interval_scores.json` (promoted-tier intervals) and a `.fastread.mkv`.

## Pre-recorded interval set (recorded BEFORE running)

Promoted (low / fair confidence tier) intervals in
`Lyra-Wheeling-IMG_3912.track_runner.interval_scores.json`: 16588-16591 (fair,
3 frames, approximate/partial seeds; excluded as too short and noisy),
19981-20169 (fair), 27120-27144 (fair). The two intervals below were fixed
before any solve was run.

| Interval (seed frames) | Span | Left seed box (src px x,y,w,h) | Right seed box | Case |
| --- | --- | --- | --- | --- |
| 27120-27144 | 24 | 2043,972,59,108 | 2410,817,62,107 | normal torso (w~59-62) |
| 19981-20169 | 188 | 2415,617,22,33 | 2306,626,17,38 | small torso (w~17-22) |

## Pass criteria (set BEFORE the run)

1. No coordinate shift: solved-box centers at sampled interior frames agree
   between bin 1 and the binned default within tolerance. Small per-frame
   pixel differences from resolution are EXPECTED; a systematic offset of
   multiple torso-widths, or a bin-scale factor (e.g. a ~2x multiplier), is a
   BUG. (primary)
2. No all-miss walker pass: neither bin produces a pass with accepted
   fraction 0 where the other does not. (primary)
3. Accepted-fraction drop no worse than 25% relative vs bin 1; a larger drop
   is flagged for human review rather than silently accepted. (secondary)

## Per-interval results

Accepted fraction = accepted_count / interval_length, reported per direction
(FWD and BWD independent, C9). Center-shift max is the largest Euclidean
distance (SOURCE pixels) between the bin-1 and binned blended-path centers
across three sampled interior frames (~25/50/75 percent offsets).

| Interval | seed w/h (src) | dir | bin1 acc-frac | binned acc-frac | rel change | center-shift max (px) | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 27120-27144 (normal) | 59-62 / 107-108 | FWD | 0.125 | 0.125 | 0.0% | 0.66 | PASS |
| 27120-27144 (normal) | 59-62 / 107-108 | BWD | 0.000 | 0.000 | n/a (0 both) | 0.66 | PASS |
| 19981-20169 (small) | 17-22 / 33-38 | FWD | 0.910 | 0.910 | 0.0% | 6.12 | PASS |
| 19981-20169 (small) | 17-22 / 33-38 | BWD | 0.085 | 0.090 | +6.3% (improved) | 6.12 | PASS |

Both walks hit `stop_reason = hit_neighbor_seed` in every direction at both
bins (no early termination, no degenerate stall).

## Criterion analysis

1. No coordinate shift (PASS). Normal interval: centers identical to ~0.66 px
   at both bins. Small interval: max shift 6.12 px. Small torso width is ~20
   src px, so 6.12 px is ~0.31 torso-widths -- sub-torso, well within
   resolution-quantization tolerance. No systematic offset: the per-frame
   shifts (0.82, 4.09, 6.12 px) are small and non-monotone-in-magnitude, not a
   constant bias. No bin-scale bug: bin1 cx 2355.75 vs bin2 cx 2349.81 is a
   ratio of 1.0025, not the ~2x that a missing PROCESSED->SOURCE rescale would
   produce. The M1 storage-boundary fix is confirmed intact at bin 2.
2. No all-miss walker pass (PASS). Every non-zero pass stays non-zero across
   bins. The only zero is BWD on the normal interval, and it is zero at BOTH
   bins (a pre-existing walker BWD-stall property, not bin-induced): the
   binned run never zeroes a pass that bin 1 accepted.
3. Accepted-fraction drop <= 25% relative (PASS). FWD fractions are
   bit-identical across bins on both intervals (0.125 and 0.910). The small
   interval's BWD fraction rose from 0.085 to 0.090 (one extra accepted frame:
   16 -> 17), an improvement, not a drop. No interval shows any drop, let alone
   a >25% drop. Criterion (3) held; nothing flagged for review.

## Overall conclusion

PASS on all three criteria. The new binned default (4K -> bin2,
processed 1920x1080) tracks the same as full-resolution bin 1 on both a
normal-torso and a small-torso promoted interval: no coordinate shift beyond
sub-torso resolution quantization, no new all-miss pass, and no accepted-
fraction drop (FWD identical, small-interval BWD slightly improved). No bin-
scale coordinate bug is present, confirming the M1 PROCESSED->SOURCE storage
boundary holds at bin 2.

Scope caveats: one 4K video, two promoted intervals, single held-bin
comparison. The small-torso interval has a structurally low BWD accepted
fraction (~0.09) at both bins; that is a pre-existing walker BWD property
independent of binning and out of scope for this bin-safety check. Per the
standing held-out-seed caveat, accepted fraction here is bin-safety evidence
(criterion-3 secondary), not a walker-vs-Hermite quality ranking.
