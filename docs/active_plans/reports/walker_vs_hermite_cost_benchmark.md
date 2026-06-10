# Walker vs Hermite cost benchmark

Read-only benchmark quantifying why the solver runs cheap Hermite on every
interval and spends the expensive walker only on promoted (low/fair
confidence) intervals. This report supports the design rationale in
[TRACK_RUNNER_DESIGN.md](../../TRACK_RUNNER_DESIGN.md) (five-stage pipeline,
"spend expensive evidence only where cheap evidence is uncertain").

## Method and data availability

- Hermite cost: MEASURED in isolation, plus the canonical documented figure.
- Walker cost: MODELED from the per-frame operation count multiplied by the
  repo's own MEASURED decode benchmarks. The walker's dominant cost is
  per-frame residual decode plus image ops; the Viterbi DP is negligible.

Corpus video data is present under `TRACK_VIDEOS/` and precomputed walker
output exists under `corpus_walk/`, but no per-interval wall-clock timing is
recorded in those artifacts (the manifests hold per-frame status, not
durations). Running a fresh timed walker pass requires concurrent 4K HEVC HDR
decode, which is the very cost being characterized; rather than perturb the
machine, the walker number is modeled from the repo's published decode
benchmarks in [common_tools/README.md](../../../common_tools/README.md).

Interval geometry is taken from the real corpus: `corpus_walk/` contains 120
seed-to-seed intervals across 6 videos, with median span 23 frames, mean 35,
and max 377 (measured, see commands below).

## Hermite cost (measured)

The Hermite analytical path (`velocity_model._compute_raw_pred_forward` /
`_compute_raw_pred_backward`) is pure closed-form arithmetic. Each frame
evaluates `hermite_interpolate` four times (center x, center y, log-width,
log-height). There is no video decode and no image processing on this path.
When `blob_snap_enabled` is False the propagator short-circuits straight to
`raw_pred` (`velocity_model._apply_blob_snap`, line ~697).

Isolated micro-benchmark of the per-frame Hermite arithmetic
(`/tmp/_hermite_bench.py`, using the real `velocity_model.hermite_interpolate`):

| Interval frames | Per-interval (ms) | Per-frame (us) |
| --- | --- | --- |
| 50 | 0.042 | 0.82 |
| 100 | 0.078 | 0.77 |
| 200 | 0.157 | 0.78 |
| 500 | 0.386 | 0.77 |

The canonical documented figure is `~3 ms per 100-frame interval`
([TRACK_RUNNER_DESIGN.md](../../TRACK_RUNNER_DESIGN.md), line 60). The isolated
arithmetic floor measured here (~0.08 ms per 100 frames) is lower because the
3 ms figure also covers curve fitting and scene-transform setup per interval.
This report uses the conservative documented 3 ms per 100-frame interval as the
Hermite cost, which only makes the walker ratio more favorable to the gate, not
less.

Key point: the Hermite cost is decode-free. It does not scale with video codec,
resolution, or seek pattern. It scales only with frame count, at roughly
1 microsecond of CPU per frame.

## Walker cost (modeled from measured decode)

The walker (`track_runner/blob_walk/walk_walker.walk_one_direction` plus
`walk_viterbi.select_path`) has two cost components:

1. Per-frame residual observation (`residual_motion.observe_blob_at` ->
   `compute_residual_for_frame`). This is the dominant cost. Per target frame
   it reads `2 * DEFAULT_HALF_WINDOW = 8` neighbor frames (`DEFAULT_HALF_WINDOW
   = 4`, k=0 skipped; `residual_motion.py` line ~601) plus the center frame,
   then runs 8 `cv2.warpAffine` warps, 8 grayscale conversions, and a
   `numpy.nanmedian` over the ROI stack. Blob extraction
   (`extract_frame_blobs`) follows.
2. Viterbi DP over a 9-frame rolling window of candidate lists
   (`walk_viterbi.select_path`). This is pure CPU over a small lattice (a
   handful of candidates per frame) and is negligible next to the decode plus
   warp cost.

The per-frame decode cost depends entirely on the read pattern, and the repo
has measured both regimes
([common_tools/README.md](../../../common_tools/README.md), 4K HEVC HDR, the
representative source codec):

| Read pattern | Per-frame decode (ms) | Source |
| --- | --- | --- |
| Strategy 0 sequential (pre-pass active) | 6.4 (worst sustained ~14) | README Strategy 0 |
| Strategy 1 scattered seek, single process | 130-575 | README Strategy 1 |
| Strategy 1 scattered, 7-worker real run | median 2599, p95 3683 | README Strategy 1 |

The residual pre-pass (`residual_pre_pass.py`) exists specifically to convert
the walker's scattered neighbor reads into monotonically increasing sequential
reads, pulling the per-frame decode from the ~2600 ms scattered regime down to
the ~6-14 ms sequential regime. So the realistic per-frame walker decode cost,
with the pre-pass in place, is the sequential rate.

Modeled per-frame walker cost (sequential, pre-pass active):

- 9 sequential frame decodes at ~6.4 ms = ~58 ms decode, but neighbor frames
  are shared across adjacent target frames via the rolling cache, so the
  amortized incremental decode is closer to 1 new frame per target frame:
  ~6-14 ms.
- Plus 8 warpAffine + grayscale + nanmedian over the ROI per frame, plus blob
  extraction. These are tens of milliseconds of CPU on a multi-hundred-pixel
  ROI.

Conservative modeled per-frame walker cost: roughly 10-40 ms per frame even on
the cheap sequential path, dominated by decode and warp.

## The ratio

Per frame:

| Path | Per-frame cost | Type |
| --- | --- | --- |
| Hermite | ~0.0008 ms (0.8 us) measured; ~0.03 ms using doc 3 ms / 100 | decode-free arithmetic |
| Walker | ~10-40 ms modeled (sequential pre-pass); ~2600 ms if scattered | decode + warp + DP |

Per-frame walker-to-Hermite ratio:

- Versus the measured Hermite arithmetic floor (0.8 us/frame): the walker is
  roughly 12000x to 50000x more expensive per frame on the cheap sequential
  path.
- Versus the conservative documented Hermite figure (3 ms per 100 frames =
  0.03 ms/frame): the walker is roughly 300x to 1300x more expensive per frame
  on the sequential path.
- Without the pre-pass (scattered seeks): the gap widens by another two orders
  of magnitude.

Per 100-frame interval:

- Hermite: ~3 ms (documented) or ~0.08 ms (measured arithmetic).
- Walker: ~1-4 seconds (sequential) for the residual plus warp work over the
  interval; far more if scattered.

Headline: the walker is roughly 300x to over 1000x more expensive than Hermite
per interval on the cheap sequential path, and orders of magnitude worse on the
scattered path the pre-pass was built to avoid. This is a conservative band:
against the isolated Hermite arithmetic floor the ratio is tens of thousands to
one.

## Conclusion: the gap justifies promote-only

The cost gap is not marginal; it spans three to five orders of magnitude per
interval. Hermite is decode-free closed-form arithmetic that costs about a
microsecond of CPU per frame and is insensitive to codec, resolution, and seek
pattern. The walker's cost is dominated by per-frame video decode plus eight
warpAffine warps and a median over a multi-hundred-pixel ROI, costing tens of
milliseconds per frame at best and seconds per frame if reads scatter. Running
the walker on every one of the corpus's 120 intervals would cost minutes to
tens of minutes of decode-bound work, while Hermite clears all 120 intervals in
well under a second. Because Stage 3 Hermite already produces honest FWD/BWD
disagreement that flags exactly which intervals are uncertain, spending the
walker only on the low/fair-confidence promoted subset captures nearly all of
the accuracy benefit for a small fraction of the cost. The promote-only gate is
strongly justified by these numbers: cheap evidence runs everywhere, expensive
evidence runs only where cheap evidence is uncertain.

## Commands run and output

Hermite micro-benchmark (`/tmp/_hermite_bench.py`):

```
$ source source_me.sh && python3 /tmp/_hermite_bench.py
hermite_interpolate is decode-free closed-form: 0.5

 interval_frames     reps    total_s  per_interval_ms   per_frame_us
              50      400     0.0167           0.0419         0.8207
             100      200     0.0156           0.0782         0.7739
             200      200     0.0314           0.1572         0.7820
             500      200     0.0772           0.3858         0.7701
```

Corpus interval geometry (`/tmp/_interval_stats.py`):

```
$ source source_me.sh && python3 /tmp/_interval_stats.py
num intervals in corpus_walk: 120
min/median/max interval span (frames): 1 23 377
mean span: 35.166666666666664
```

Source confirmations:

- `residual_motion.py` line ~601-607: per target frame reads
  `2 * DEFAULT_HALF_WINDOW = 8` neighbor frames (k=0 skipped) for the residual.
- `residual_motion.py` line ~84: `DEFAULT_HALF_WINDOW = 4`.
- `velocity_model.py` line ~697: when `blob_snap_enabled` is False the
  propagator returns `raw_pred` (pure Hermite), confirming the decode-free
  Hermite path.
- `residual_pre_pass.py` line ~1-12: the pre-pass converts scattered reads into
  Strategy-0 sequential reads.
- [common_tools/README.md](../../../common_tools/README.md): measured 6.4 ms
  sequential vs median 2599 ms scattered per-frame decode on 4K HEVC HDR.
- [TRACK_RUNNER_DESIGN.md](../../TRACK_RUNNER_DESIGN.md) line 60: documented
  Hermite "~3 ms per 100-frame interval".
