# Short-span interval frequency study

**Date:** 2026-06-12
**Status:** COMPLETE
**Question:** How common are 1-13-frame seed-to-seed intervals in real corpus data?
Are they a real bucket by count and frame share, or an edge case?

## Background

The new Viterbi cost model is degenerate on intervals where pairwise velocity
estimation needs >= 2 real frames. That threshold is spans <= 13 frames.
Six of 7 e2e regressions came from such spans. This study measures how
common short-span intervals are across all 12 corpus videos, by both
interval count and frame-weighted share.

## Method

- Source: `tr_config/*.track_runner.seeds.json` loaded via `state_io.load_seeds`.
- Spans computed as consecutive seed-pair differences on sorted frame indices.
- Frame-weighted share: frames in short intervals / total solved frames.
- Stage-4 promotion: fraction of short intervals with `confidence_tier` in
  `{low, fair}` from the matching `interval_scores.json`.
- 4 videos have minor seeds-vs-scores count mismatches (20 intervals total
  missing tier data); Stage-4 counts for those videos are slightly understated.

## Interval span distribution - counts

| Video | Seeds | Total | 1-2 | 3-8 | 9-13 | 14-30 | 31-100 | 101+ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2025-Glenbrook_South-1600m-IMG_1503 | 70 | 69 | 0 | 0 | 0 | 0 | 0 | 69 |
| Conant-4x400-2026_April_15 | 362 | 361 | 0 | 6 | 0 | 91 | 260 | 4 |
| Hononega-Orion-1600m-IMG_3629 | 427 | 426 | 54 | 18 | 34 | 110 | 166 | 44 |
| Hononega-Orion_600m-IMG_3702 | 563 | 562 | 70 | 278 | 93 | 93 | 28 | 0 |
| Hononega-Varsity_4x400m-IMG_3707 | 976 | 975 | 119 | 338 | 152 | 260 | 101 | 5 |
| IMG_3627 | 87 | 86 | 3 | 4 | 0 | 7 | 28 | 44 |
| IMG_3823 | 718 | 717 | 254 | 297 | 129 | 37 | 0 | 0 |
| IMG_3830 | 1580 | 1579 | 1131 | 358 | 54 | 33 | 3 | 0 |
| IMG_3839 | 23 | 22 | 0 | 0 | 0 | 0 | 0 | 22 |
| Jason-3200m-sectionals-IMG_4005 | 633 | 632 | 1 | 1 | 6 | 187 | 407 | 30 |
| Lyra-Hersey-800m-IMG_3882 | 537 | 536 | 88 | 39 | 53 | 143 | 210 | 3 |
| Lyra-Wheeling-IMG_3912 | 310 | 309 | 0 | 7 | 9 | 20 | 199 | 74 |

## Interval span distribution - percent of intervals

| Video | Total | 1-2 | 3-8 | 9-13 | 14-30 | 31-100 | 101+ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2025-Glenbrook_South-1600m-IMG_1503 | 69 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 100.0% |
| Conant-4x400-2026_April_15 | 361 | 0.0% | 1.7% | 0.0% | 25.2% | 72.0% | 1.1% |
| Hononega-Orion-1600m-IMG_3629 | 426 | 12.7% | 4.2% | 8.0% | 25.8% | 39.0% | 10.3% |
| Hononega-Orion_600m-IMG_3702 | 562 | 12.5% | 49.5% | 16.5% | 16.5% | 5.0% | 0.0% |
| Hononega-Varsity_4x400m-IMG_3707 | 975 | 12.2% | 34.7% | 15.6% | 26.7% | 10.4% | 0.5% |
| IMG_3627 | 86 | 3.5% | 4.7% | 0.0% | 8.1% | 32.6% | 51.2% |
| IMG_3823 | 717 | 35.4% | 41.4% | 18.0% | 5.2% | 0.0% | 0.0% |
| IMG_3830 | 1579 | 71.6% | 22.7% | 3.4% | 2.1% | 0.2% | 0.0% |
| IMG_3839 | 22 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 100.0% |
| Jason-3200m-sectionals-IMG_4005 | 632 | 0.2% | 0.2% | 0.9% | 29.6% | 64.4% | 4.7% |
| Lyra-Hersey-800m-IMG_3882 | 536 | 16.4% | 7.3% | 9.9% | 26.7% | 39.2% | 0.6% |
| Lyra-Wheeling-IMG_3912 | 309 | 0.0% | 2.3% | 2.9% | 6.5% | 64.4% | 23.9% |

## Frame-weighted span distribution - percent of frames

| Video | Total frames | 1-2 | 3-8 | 9-13 | 14-30 | 31-100 | 101+ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2025-Glenbrook_South-1600m-IMG_1503 | 16800 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 100.0% |
| Conant-4x400-2026_April_15 | 14326 | 0.0% | 0.3% | 0.0% | 13.3% | 83.1% | 3.4% |
| Hononega-Orion-1600m-IMG_3629 | 17204 | 0.3% | 0.6% | 2.0% | 12.0% | 40.8% | 44.3% |
| Hononega-Orion_600m-IMG_3702 | 5509 | 2.0% | 27.2% | 17.3% | 32.2% | 21.3% | 0.0% |
| Hononega-Varsity_4x400m-IMG_3707 | 15122 | 0.8% | 14.7% | 10.6% | 38.4% | 31.8% | 3.7% |
| IMG_3627 | 8007 | 0.0% | 0.3% | 0.0% | 1.9% | 23.7% | 74.0% |
| IMG_3823 | 4082 | 7.4% | 39.2% | 35.9% | 17.5% | 0.0% | 0.0% |
| IMG_3830 | 4216 | 33.4% | 36.8% | 13.2% | 14.0% | 2.6% | 0.0% |
| IMG_3839 | 8294 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 100.0% |
| Jason-3200m-sectionals-IMG_4005 | 35908 | 0.0% | 0.0% | 0.2% | 12.2% | 73.8% | 13.7% |
| Lyra-Hersey-800m-IMG_3882 | 13760 | 0.8% | 1.5% | 4.0% | 23.1% | 68.2% | 2.4% |
| Lyra-Wheeling-IMG_3912 | 27144 | 0.0% | 0.1% | 0.4% | 1.8% | 50.9% | 46.7% |

## Short-span detail (spans 1-13) per video

| Video | Short iv | Total iv | % iv | Short fr | Total fr | % frames | S4-short | % S4 of short |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2025-Glenbrook_South-1600m-IMG_1503 | 0 | 69 | 0.0% | 0 | 16800 | 0.0% | 0 | N/A |
| Conant-4x400-2026_April_15 | 6 | 361 | 1.7% | 46 | 14326 | 0.3% | 0 | 0.0% |
| Hononega-Orion-1600m-IMG_3629 | 106 | 426 | 24.9% | 505 | 17204 | 2.9% | 2 | 1.9% |
| Hononega-Orion_600m-IMG_3702 | 441 | 562 | 78.5% | 2563 | 5509 | 46.5% | 5 | 1.1% |
| Hononega-Varsity_4x400m-IMG_3707 | 609 | 975 | 62.5% | 3951 | 15122 | 26.1% | 30 | 4.9% |
| IMG_3627 | 7 | 86 | 8.1% | 24 | 8007 | 0.3% | 0 | 0.0% |
| IMG_3823 | 680 | 717 | 94.8% | 3368 | 4082 | 82.5% | 28 | 4.1% |
| IMG_3830 | 1543 | 1579 | 97.7% | 3518 | 4216 | 83.4% | 53 | 3.4% |
| IMG_3839 | 0 | 22 | 0.0% | 0 | 8294 | 0.0% | 0 | N/A |
| Jason-3200m-sectionals-IMG_4005 | 8 | 632 | 1.3% | 77 | 35908 | 0.2% | 0 | 0.0% |
| Lyra-Hersey-800m-IMG_3882 | 180 | 536 | 33.6% | 870 | 13760 | 6.3% | 3 | 1.7% |
| Lyra-Wheeling-IMG_3912 | 16 | 309 | 5.2% | 145 | 27144 | 0.5% | 1 | 6.2% |

## Corpus rollup

- Videos: 12
- Total seeds: 6286
- Total intervals: 6274
- Total frames in intervals: 170,372

### Bucket distribution

| Bucket | Intervals | % of intervals | Frames | % of frames |
| --- | --- | --- | --- | --- |
| 1-2 | 1720 | 27.4% | 2108 | 1.2% |
| 3-8 | 1346 | 21.5% | 7301 | 4.3% |
| 9-13 | 530 | 8.4% | 5658 | 3.3% |
| 14-30 | 981 | 15.6% | 21073 | 12.4% |
| 31-100 | 1402 | 22.3% | 76616 | 45.0% |
| 101+ | 295 | 4.7% | 57616 | 33.8% |

### Short-span summary (1-13 frames)

| Metric | Value |
| --- | --- |
| Short interval count | 3596 / 6274 = 57.3% |
| Short frame share | 15067 / 170372 = 8.8% |
| Stage-4-promoted among short | 122 / 3596 = 3.4% |

## Notes on data coverage

- All 12 videos have both seeds.json and interval_scores.json files.
- 4 videos have minor seeds-vs-scores count mismatches (20 intervals total):
  Hononega-Orion-1600m-IMG_3629 (13), IMG_3823 (4), IMG_3627 (2),
  Hononega-Orion_600m-IMG_3702 (1). These intervals are counted in span
  distributions but have no tier in the Stage-4 column.
- Confidence tiers in corpus: high 76.4%, good 9.3%, pre_race 6.0%,
  fair 4.5%, low 3.8%. Stage-4 promotes low + fair = 8.3% of scored intervals.

## Interpretation

Short-span intervals (1-13 frames) account for 57% of all intervals by count
but only 8.8% of total frames -- the frame-weighted impact is small. The
split is highly bimodal: four videos (IMG_3830, IMG_3823, Hononega-Orion_600m,
Hononega-Varsity_4x400m) were annotated at near-every-frame density with most
intervals <= 8 frames, while five videos (Glenbrook South, Conant, IMG_3827,
IMG_3839, Jason) have almost no short-span intervals. This means short-span is a
real bucket in densely-seeded videos and nearly absent in coarsely-seeded ones;
whether it matters depends on which annotation style a solver is being validated
against. Among short intervals, only 3.4% are Stage-4-promoted (low/fair
confidence), meaning nearly all short-span intervals are pre-race or high/good
confidence and the Viterbi cost model degeneracy affects fewer than 4% of the
already-small (8.8% frame-share) short-span bucket -- approximately 0.3% of all
corpus frames lie in short-span Stage-4-promoted intervals.
