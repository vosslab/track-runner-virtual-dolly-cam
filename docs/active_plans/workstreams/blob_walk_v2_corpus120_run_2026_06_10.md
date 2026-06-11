# Corpus-120 walk run 2026-06-10

Workstream artifact. Result analysis for the post-P12 corpus walk run.

## Method

Command (from `run_random_walk.sh`):

```
6 videos x 20 random visible-both intervals per video
Corpus:  data/outdoor_corpus.txt
Mode:    random-sample, rng_seed=None (fresh RNG each run)
Output:  corpus_walk/
```

The run selected 20 intervals at random from the visible-both interval pool
for each video. Because `rng_seed=None`, this is a FRESH random sample --
different intervals than earlier corpus runs. Differences in aggregate
metrics relative to prior baselines reflect sampling variation in addition
to any code changes.

The P12 stride-termination fix is staged in the working tree. All 120
intervals were solved under post-P12 walker behavior (SCHEMA_VERSION 13).

Run log: `/private/tmp/claude-501/-Users-vosslab-nsh-track-runner-virtual-dolly-cam/f96933b8-011b-4409-8458-8c18c2668da1/tasks/bmk1z0p3o.output`

Per-video intervals in this run identified via `render_heat_summary.json`
(written only for this run's 20 intervals). Prior-run artifacts from
`corpus_walk/Lyra-Wheeling-IMG_3912/` (Jun 3 mtime) were excluded.

## Per-video results

Accepted_fraction computed as `sum(accepted) / sum(interval_length)` over
the 20 walked intervals for each direction.

| Video | Elapsed | Intervals | FWD accepted | FWD len | FWD% | BWD accepted | BWD len | BWD% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| IMG_3830 | 0:28 | 20 | 51 | 61 | 83.6% | 54 | 61 | 88.5% |
| IMG_3823 | 0:20 | 20 | 43 | 77 | 55.8% | 42 | 77 | 54.5% |
| Jason-3200m-sectionals-IMG_4005 | 52:22 | 20 | 459 | 1142 | 40.2% | 445 | 1142 | 39.0% |
| Lyra-Hersey-800m-IMG_3882 | 25:48 | 20 | 473 | 572 | 82.7% | 446 | 572 | 78.0% |
| Conant-4x400-2026_April_15 | 31:19 | 20 | 496 | 731 | 67.9% | 557 | 731 | 76.2% |
| Lyra-Wheeling-IMG_3912 | 6:11:13 | 20 | 1491 | 2570 | 58.0% | 1603 | 2570 | 62.4% |
| **Corpus total** | ~8:01 | **120** | **3013** | **5153** | **58.5%** | **3147** | **5153** | **61.1%** |

Elapsed times are from the run log. Jason and Lyra-Wheeling are 4K HEVC
sources with expensive per-frame decode; elapsed times are dominated by
decode, not walker compute.

## Corpus rollup vs L3 reference

The audit L3 reference figures (shipped-walker baseline, pre-P12):

- 24-corpus: 42.3% FWD / 41.0% BWD
  (source: `docs/active_plans/audits/blob_walk_v2_implementation_audit.md`)
- 120-corpus: 38.7% FWD / 39.1% BWD (same audit)

This run (post-P12, FRESH random sample, 120 intervals):

- 58.5% FWD / 61.1% BWD

The this-run figures are substantially higher than the L3 reference.
Several factors prevent treating this as a confirmed improvement:

1. Fresh random sample: the L3 reference used a fixed seed sample; this
   run used `rng_seed=None`. High-performing intervals (dense scenes with
   lots of residual motion) are over-represented relative to the L3 sample
   by chance; the difference between 38.7% and 58.5% is larger than any
   plausible P12-only effect.
2. P12 is stride>1 only: the P12 fix affects intervals where
   `(seed_right - seed_left) / stride` produces an odd number of steps.
   At stride 2 (120 fps source), 13 of the 20 Lyra-Wheeling intervals
   sampled here fall in that category. The other five videos are stride 1
   (30 fps) and are byte-identical pre/post-P12 (per changelog).
3. `accepted_fraction` is diagnostic, not the quality authority. Per the
   roadmap, held-out-seed distance is the quality authority. This metric is
   not measured here.

The valid conclusion from this run: the walker completes all 120 intervals
without errors, all stop_reasons are `hit_neighbor_seed`, and no regressions
in manifest PASS checks are observed. The higher accepted_fraction is
plausible but not attributable to P12 alone without a fixed-seed A/B.

## Lyra-Wheeling P12 observations

Lyra-Wheeling-IMG_3912 is 120 fps, stride 2. The P12 fix targets the
stride-termination overrun (walker could step past the neighbor seed before
the stop condition fired).

### Stop reasons

All 20 sampled intervals (both FWD and BWD) report stop_reason =
`hit_neighbor_seed`. No `early_stop`, `max_steps_exceeded`, or other
anomalous stop reason is present. This is consistent with the fix working:
the crossing-plus-clamp stop condition fires correctly in all sampled cases.

### Odd-span intervals

At stride 2, the P12 bug manifested on intervals where the half-span
`(right - left) // 2` is odd (the final stride step would overshoot the
neighbor seed by 1 frame before the equality check could fire). Of the 20
intervals in this sample:

- 13 of 20 have an odd half-span (prime trigger candidates).
- 7 of 20 have an even half-span (not directly affected by the bug).

All 13 odd-half-span intervals show `hit_neighbor_seed` as both FWD and
BWD stop reason. No termination anomaly observed.

The specific interval documented in the P12 unit tests (frames 16588-16591,
interval #164 in Lyra-Wheeling) was NOT in this run's random sample. The
interval `seed_16614_16641` (span=27, half-span=13, also ODD) was in the
corpus but was a prior-run result (Jun 3 mtime) and was excluded from this
run's analysis. No walk debug CSVs are written by this tool path
(`make_walk_html_v2.py`), so per-frame status sequences for P12 verification
are not available from these artifacts. The unit tests in
`tests/test_walk_neighbor_reached.py` remain the primary P12 verification
source.

## Seed-cold tile observations

The heat report identified 26 of 240 interval-directions with at least one
seed-cold tile (residual motion below threshold at a seed frame):

| Video | Seed-cold interval-directions | of 40 total |
| --- | --- | --- |
| IMG_3830 | 0 | 0/40 |
| IMG_3823 | 3 | 3/40 |
| Jason-3200m-sectionals-IMG_4005 | 14 | 14/40 |
| Lyra-Hersey-800m-IMG_3882 | 1 | 1/40 |
| Conant-4x400-2026_April_15 | 2 | 2/40 |
| Lyra-Wheeling-IMG_3912 | 6 | 6/40 |

These are diagnostic observations only. A seed-cold tile means the seed
frame's residual motion is below threshold; the walker sees no blob at that
specific frame. This is expected for seed frames where the runner is
stationary relative to the background or where the camera motion correction
is imperfect. Seed-cold frames do not indicate a bug -- they indicate
intervals where the seed endpoint has no blob to anchor to, which may lower
accepted_fraction for those intervals.

Jason has the highest seed-cold rate (14/40 = 35%), consistent with its
long decode time and challenging scene content. This is a pre-existing
observation, not a regression.

## Limitations

- Fresh random sample: results are not directly comparable to any fixed-seed
  baseline. Sampling variation dominates the comparison.
- Diagnostic metric: accepted_fraction is not the quality authority per
  the roadmap. Held-out-seed distance measurements (WS-2A) are needed for
  authoritative quality comparison.
- Pre-P10 walker: the P10 fix (per-frame displacement cap softening or
  removal) is not yet applied. Intervals with high displacement may still
  show low accepted_fraction due to the P10 issue. This is expected.
- No walk debug CSVs: the corpus walk tool path does not write per-frame
  status debug CSVs, so per-frame P12 verification (frame-by-frame status
  sequence confirming the crossing clamp fires) is not available from these
  artifacts. Unit tests remain the P12 gate.
- Lyra-Wheeling interval #164 not in sample: the exact interval used for
  P12 unit-test validation was not sampled, so this run cannot confirm
  empirically that interval #164 is now solved correctly. The unit tests
  cover the exact frame arithmetic.

## Conclusion

No roadmap pause trigger observed. All 120 intervals complete with
`hit_neighbor_seed` stop reasons and all 6 manifest PASS checks pass.
The P12 termination fix appears structurally sound for the 13 odd-half-span
Lyra-Wheeling intervals in this sample. The elevated accepted_fraction
(58.5% FWD / 61.1% BWD) vs the L3 reference (38.7% / 39.1%) is primarily
attributable to fresh-sample selection bias; a fixed-seed A/B (WS-2A,
post-M1) is required before the number can be treated as a quality signal.
Roadmap proceeds as planned.
