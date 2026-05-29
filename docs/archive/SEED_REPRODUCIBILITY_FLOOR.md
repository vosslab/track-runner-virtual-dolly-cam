# Seed reproducibility floor: corpus measurement

Per contract C4, the pre-race-start frames are stationary: camera fixed,
runner has not moved. Variance across pre-race seeds is human seed
reproducibility noise (the "ask a human to circle the same object 15
times" effect; see contract C5), not real motion or scale change.

Tool: `measure_seed_reproducibility.py`.
Reads only `tr_config/{basename}.track_runner.seeds.json` plus the
co-located `interval_scores.json` for `race_start_frame`. No video file
needed.

## Per-video corpus table

Sorted by `n_pre_race_visible_seeds` descending. Per contract C2,
fractional columns are the cross-video-comparable measure; pixel
columns are video-internal only.

| video | n_pre | w_mean (px) | h_mean (px) | w stdev (px) | h stdev (px) | w stdev / mean | h stdev / mean |
| --- | --- | --- | --- | --- | --- | --- | --- |
| IMG_3830 | 162 | 26.78 | 40.95 | 8.17 | 9.52 | **30.5%** | **23.2%** |
| Hononega-Orion_600m-IMG_3702 | 55 | 68.47 | 97.02 | 12.14 | 15.05 | 17.7% | 15.5% |
| Hononega-Orion-1600m-IMG_3629 | 26 | 35.15 | 50.12 | 3.70 | 7.56 | 10.5% | 15.1% |
| Hononega-Varsity_4x400m-IMG_3707 | 23 | 53.65 | 97.78 | 6.75 | 10.66 | 12.6% | 10.9% |
| Conant-4x400-2026_April_15 | 22 | 42.91 | 60.05 | 5.49 | 6.46 | 12.8% | 10.8% |
| Lyra-Hersey-800m-IMG_3882 | 21 | 26.71 | 43.52 | 2.26 | 4.94 | 8.5% | 11.3% |
| IMG_3823 | 16 | 11.25 | 16.81 | 1.00 | 1.11 | 8.9% | 6.6% |
| Lyra-Wheeling-IMG_3912 | 9 | 56.78 | 106.44 | 4.55 | 4.25 | 8.0% | 4.0% |
| IMG_3627 | 4 | 35.00 | 68.50 | 4.24 | 3.11 | 12.1% | 4.5% |
| IMG_3839 | 3 | 56.67 | 79.33 | 25.54 | 39.02 | 45.1% | 49.2% |
| 2025-Glenbrook_South-1600m-IMG_1503 | 2 | 22.00 | 38.00 | 7.07 | 7.07 | 32.1% | 18.6% |

## Reading

- The earlier "3 percent" floor estimate (used in the parent plan
  hypothesis) is **too optimistic by an order of magnitude**. Across the
  well-sampled videos (n_pre >= 20), the fractional w stdev sits in
  8 - 30 percent and fractional h stdev sits in 6 - 23 percent. Single
  pre-race seed boxes carry roughly 10 - 30 percent boundary
  imprecision, not 3 percent.
- IMG_3830 (n=162) is the most reliable corpus point and shows the
  **largest** fractional stdev (30.5 / 23.2 percent). This is consistent
  with its small absolute torso (~27 x 41 px) -- a 1-pixel boundary
  ambiguity is a 3-4 percent fractional error, and 8-9 px stdev across
  162 seeds compounds to the ~30 percent number observed.
- IMG_3823 (n=16, very small torso ~11 x 17 px) bucks the trend with
  the lowest fractional w stdev (8.9 percent). On a tiny torso the
  human seems to commit to fewer plausible boundaries because the box
  has so few choices, even though each pixel is a larger fraction.
- Pixel and fractional do not move together. Lyra-Wheeling has the
  largest h_mean (106 px) and the smallest fractional h stdev (4 percent);
  the absolute pixel std is 4.25 px which is small because each pixel
  is a small fraction of the box.
- Adjacent per-frame solved torso h/w (inside one interval, bracketed
  by the same two seeds) inherit only a fraction of the per-seed
  variance because Hermite interpolation is smooth between the same
  boundary values. The per-frame `|d torso_h|/torso_h` velocity is a
  much smaller scale than the seed-level fractional stdev. The two
  measurements answer different questions: per-seed stdev sets the
  noise floor of the underlying truth signal; per-frame velocity p95
  measures how much that noise leaks into adjacent-frame deltas.

## Implication for the stabilizer plan

The acceptance-gate "torso_h fractional p95 at or below 3 percent" was
calibrated to a wrong floor. Replace it with:

- **Relative reduction:** stabilized fractional p95 drops by at least
  50 percent vs unstabilized on at least 2 of 3 representative videos.
- **No floor gate:** do not assert an absolute level; the empirical
  floor varies by video and is set by the underlying seed quality, not
  by the stabilizer.
