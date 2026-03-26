# M4/M5 benchmark gate findings

Run date: 2026-03-25

## Summary

All 7 benchmark videos solved successfully. 17 of 31 closure gates passed, 14 failed.
The failures fall into three categories: under-seeded videos, a broken motion estimator,
and borderline threshold misses.

## Gate results by video

| Video | Norm center err | Box height err | Per-regime | Diagnosis |
| --- | --- | --- | --- | --- |
| IMG_3702 | 0.138 PASS | 0.046 PASS | 21.1px FAIL (< 20) | Borderline per-regime miss |
| IMG_3707 | 0.226 PASS | 0.056 PASS | (no regime gates) | Clean pass |
| IMG_3627 | 2.663 FAIL | 0.150 FAIL | (no regime gates) | Under-seeded |
| IMG_3629 | 1.347 FAIL | 0.109 FAIL | low_conf 0.37 FAIL, norm 1.35 FAIL | Under-seeded + large gaps |
| IMG_3823 | 0.263 FAIL | 0.041 PASS | jerk_p95 2.5 PASS | Borderline miss |
| IMG_3830 | 0.109 PASS | 0.054 PASS | agree 0.81 PASS, norm 0.109 FAIL (< 0.10) | Borderline per-regime miss |
| canon_60d | 0.947 FAIL | 0.107 FAIL | norm 0.95 FAIL, high_conf 0.25% FAIL, fair_low 69% FAIL | Motion estimator broken |

## Seed density analysis

| Video | Visible seeds | Frame span | Avg gap | Max gap | Seed density |
| --- | --- | --- | --- | --- | --- |
| IMG_3830 | 1056 | 4216 | 4.0 | 94 | Very dense |
| IMG_3823 | 583 | 4082 | 7.0 | 58 | Dense |
| canon_60d | 351 | 2856 | 8.0 | 84 | Dense |
| IMG_3702 | 449 | 5509 | 12.2 | 77 | Moderate |
| IMG_3707 | 695 | 15122 | 21.8 | 188 | Moderate |
| IMG_3629 | 278 | 17204 | 62.1 | 638 | Sparse |
| IMG_3627 | 44 | 8007 | 186.2 | 600 | Very sparse |

## Error vs interval length

The analytical solver handles short occlusions (runner behind a crowd member for 1-2
frames) with near-zero error because the velocity model interpolates smoothly between
adjacent seeds. Failures are driven entirely by long unseeded stretches.

Measured on IMG_3629 (failing, sparse seeds):

| Interval length | Intervals | Frames | Median norm err | Max norm err |
| --- | --- | --- | --- | --- |
| 1 frame (adjacent seeds) | 49 | 98 | 0.061 | 3.281 |
| 2-5 frames | 8 | 37 | 0.140 | 0.227 |
| 6-20 frames | 106 | 1698 | 0.279 | 3.968 |
| 21-50 frames | 142 | 4739 | 0.483 | 4.507 |
| 51-200 frames | 39 | 3961 | 1.527 | 5.619 |
| 200+ frames | 22 | 7037 | 3.729 | 16.436 |

The 22 intervals with gaps over 200 frames contribute 7037 frames (41% of all frames)
and dominate the overall median. Single-frame occlusions where the runner is known
+/- 1 frame resolve at 0.06 normalized error -- well within all gate thresholds.

Confirmed on IMG_3830 (passing, dense seeds):

| Interval length | Intervals | Median norm err |
| --- | --- | --- |
| 1 frame | 852 | 0.076 |
| 2-3 frames | 431 | 0.124 |
| 4-10 frames | 240 | 0.169 |
| 11-30 frames | 53 | 0.383 |
| 31-100 frames | 3 | 0.400 |

## Root cause analysis

### Category 1: Under-seeded videos

**IMG_3627** and **IMG_3629** have catastrophic normalized center errors (2.66 and 1.35)
because the solver has too few anchor points. The analytical solver interpolates
between seeds, and when gaps exceed hundreds of frames, FWD/BWD tracks diverge badly.

- **IMG_3627**: 44 visible seeds across 8007 frames (avg gap 186, max gap 600). Needs
  substantially more seeds -- at least 5-10x current density.
- **IMG_3629**: 278 visible seeds across 17204 frames (avg gap 62, max gap 638). Overall
  density is moderate but the large gaps (up to 638 frames) create pockets of total
  failure. Needs seeds in the worst gaps.

**Fix**: Run additional seeding passes on these videos to fill gaps. The solver itself
is working correctly -- it just has insufficient input.

### Category 2: Broken motion estimator (canon_60d)

canon_60d has **dense seeds** (avg gap 8) yet catastrophic errors (norm 0.95). The seed
density is comparable to IMG_3823 (avg gap 7) which nearly passes. The problem is not
seeds -- it is the **continuous zoom estimator** producing poor motion compensation.

Evidence:
- 68.6% of intervals are fair or low confidence
- Only 0.25% are high confidence (gate requires >= 40%)
- The `ContinuousZoomEstimator` uses log-polar phase correlation which was already noted
  as "fragile" in the changelog

**Fix**: This video likely needs a different estimator type. The per-video config says
`zoom_type: continuous` but the camera may actually be better modeled as fixed or discrete
zoom. Alternatively, the `ContinuousZoomEstimator` algorithm needs improvement for
telephoto footage.

### Category 3: Borderline threshold misses

Three videos are within ~10% of their thresholds:

- **IMG_3702** per-regime: 21.1px vs 20.0px threshold (5% over)
- **IMG_3830** per-regime: 0.109 vs 0.10 threshold (9% over)
- **IMG_3823** cross-video: 0.263 vs 0.25 threshold (5% over)

These could be addressed by either:
1. Adding a few seeds in the weakest intervals to improve convergence
2. Relaxing the thresholds slightly (requires explicit approval per plan policy)

## Production code fix applied

Fixed estimator dispatch in [track_runner/camera_motion.py](track_runner/camera_motion.py):
`precompute_camera_motion()` now recognizes `estimator_type == "iphone_discrete"` and
`zoom_type == "discrete"` as aliases for `DiscreteZoomEstimator`. Without this fix,
videos configured by the setup wizard with discrete zoom would crash with
`ValueError: unsupported estimator type`.

## Recommended next steps

1. **Seed IMG_3627**: Run 2-3 additional seeding passes to bring visible seed count from
   44 to at least 200-400. This is the highest-impact fix.
2. **Seed IMG_3629 gaps**: Add seeds specifically in the intervals with gaps > 200 frames.
3. **Investigate canon_60d estimator**: Try re-running setup with `zoom_type: fixed`
   instead of `continuous`. If that passes, the continuous estimator is the problem.
4. **Re-evaluate thresholds**: After steps 1-3, re-run the benchmark. The borderline
   videos (IMG_3702, IMG_3823, IMG_3830) may pass once the catastrophic cases are fixed,
   or their thresholds may need minor adjustment.

## Benchmark script

Benchmark gate evaluation script:
[tools/benchmark_solver_gates.py](tools/benchmark_solver_gates.py)

Audit artifact (regenerated on each run):
`output_smoke/benchmark_gates.txt`

```bash
# run all 7 videos
source source_me.sh && python3 tools/benchmark_solver_gates.py

# run single video
source source_me.sh && python3 tools/benchmark_solver_gates.py --video IMG_3830.MP4

# verbose mode (per-frame details)
source source_me.sh && python3 tools/benchmark_solver_gates.py --verbose
```
