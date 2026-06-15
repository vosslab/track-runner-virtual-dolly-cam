# Crop mode keeper decision (WS5-evidence)

Evidence for picking ONE keeper `crop_mode` for `track_runner/tr_crop.py`. This
record is evidence only. No `crop_mode` is deleted and no production behavior is
changed here; that is WS5-impl, gated on user confirmation below.

## Test setup

- Video: `IMG_3823.mkv` (real source, 1280x720, 30 fps, 4086 frames).
- Trajectory: real solved artifact `tr_config/IMG_3823.track_runner.torso_box_coords.npz`
  plus seeds, reconstructed exactly as encode/analyze mode does (stitch ->
  derive per-frame confidence -> anchor to seeds -> stamp seed confidence ->
  trajectory erasure). 718 seeds.
- Both modes run through `tr_crop.trajectory_to_crop_rects` with `crop_mode`
  overridden in memory only (config YAML unchanged). Sample frames are REAL
  cropped output produced by `cv2` decode + `tr_crop.apply_crop`, not plotted
  boxes.
- Harness: `_crop_mode_evidence.py` (scratch, repo root). Metrics dumped to
  `crop_mode_assets/metrics_dump.json`.

## analyze_crop_stability metrics, side by side

`encode_analysis.analyze_crop_stability` (lower is better for jerk/CV/chatter):

| metric | smooth | direct_center |
| --- | --- | --- |
| center_jerk_p50 | 1.118 | 1.000 |
| center_jerk_p95 | 2.693 | 2.121 |
| center_jerk_max | 37.03 | 260.377 |
| height_jerk_p50 | 0.000 | 0.000 |
| height_jerk_p95 | 1.000 | 1.000 |
| height_jerk_max | 2.000 | 31.000 |
| crop_size_cv | 0.3992 | 0.3540 |
| quantization_chatter_fraction | 0.2012 | 0.1211 |
| dominant_symptom | lateral_jitter_dominated | low_confidence_drift_dominated |

On the typical-frame axes (p50/p95 center jerk, crop_size_cv, chatter)
`direct_center` is calmer. On worst-case single-frame spikes (`center_jerk_max`,
`height_jerk_max`) `smooth` is far calmer.

## C5 zoom-bounce quantification

C5: crop zoom must not react to single-frame torso w/h jitter; position and size
are separable. Measured from the size-vs-frame plots. The raw per-frame desired
crop height (driven by noisy solved torso w/h) has step-std 2.289 px; the metric
of interest is how much of that jitter survives into crop height.

| C5 measure | smooth | direct_center |
| --- | --- | --- |
| crop_h peak-to-peak (px) | 282.0 | 137.0 |
| crop_h std (px) | 42.90 | 23.34 |
| crop_h per-frame step-std (px) | 0.7832 | 1.1419 |
| jitter transmission ratio (crop step-std / raw step-std, noisy frames) | 0.3245 | 0.4605 |
| worst single-frame crop-h step (px) | 7.0 (f1735) | 31.0 (f3758) |

Reading: `direct_center` holds a much tighter overall zoom envelope (half the
peak-to-peak and half the height std), but transmits slightly more frame-to-frame
torso jitter into the crop height and has a much larger worst-case single-frame
zoom step. Neither mode lets ordinary torso w/h jitter visibly breathe the
zoom -- both apply the same `CROP_POST_SMOOTH_SIZE_STRENGTH = 0.15` size EMA.

### Worst-case investigated, not a frame-edge artifact

`direct_center`'s worst height step (54 px -> 85 px at frame 3758) was traced
with `_inspect_hotspot.py`. It is NOT a fit-to-source frame-edge event (no edge
overshoot; runner is mid-frame). It is a real runner re-acquire right after a
trajectory-erasure / near-edge span (runner cx jumps 0 -> 690 over frames
3753-3758, solved torso height genuinely grows). `direct_center` follows the real
geometry change immediately; `smooth`'s CropController deadband + attack/release
damps the same event. This is the core C5-relevant separability tradeoff: the
spike is a real position+size change, not single-frame w/h jitter, but
`direct_center` couples to it harder.

## Offline CROP_POST_SMOOTH layer effect (M6 note)

Measured with `_postsmooth_effect.py`, smooth path bare CropController output vs
the production post-smoothing EMA layer (`smooth_crop_trajectory` with the live
constants STRENGTH=0.0, SIZE_STRENGTH=0.15, MAX_VELOCITY=0.0):

- POSITION: near-no-op. STRENGTH=0.0 means no center EMA; max center-x delta is
  7.5 px and comes only from re-clamp rounding, not smoothing.
- SIZE: measurable and load-bearing. Height per-frame step-std drops from 1.182
  to 0.783 (-34%); max single-frame height delta 22 px. The SIZE_STRENGTH=0.15
  EMA is the main reason the smooth path's zoom is calmer at re-acquire events.

Conclusion for M6: the position/velocity legs of the stacked offline layer are
genuine no-ops and can be flattened safely, but the size EMA is doing real C5
work and must be preserved (or folded into whichever path is kept) when M6
flattens the stack. Both modes already call this same size EMA.

## Artifacts

- Per-mode crop-rect overlay plots (center x/y and w/h vs frame):
  - [crop_mode_assets/crop_plot_smooth.png](crop_mode_assets/crop_plot_smooth.png)
  - [crop_mode_assets/crop_plot_direct_center.png](crop_mode_assets/crop_plot_direct_center.png)
- Side-by-side crop-height comparison (C5 focus):
  - [crop_mode_assets/crop_height_compare.png](crop_mode_assets/crop_height_compare.png)
- Real cropped output sample frames (cv2 decode + apply_crop), frames
  612 / 1428 / 2245 / 3061 / 3673:
  - smooth: [f0612](crop_mode_assets/sample_smooth_f000612.jpg),
    [f1428](crop_mode_assets/sample_smooth_f001428.jpg),
    [f2245](crop_mode_assets/sample_smooth_f002245.jpg),
    [f3061](crop_mode_assets/sample_smooth_f003061.jpg),
    [f3673](crop_mode_assets/sample_smooth_f003673.jpg)
  - direct_center: [f0612](crop_mode_assets/sample_direct_center_f000612.jpg),
    [f1428](crop_mode_assets/sample_direct_center_f001428.jpg),
    [f2245](crop_mode_assets/sample_direct_center_f002245.jpg),
    [f3061](crop_mode_assets/sample_direct_center_f003061.jpg),
    [f3673](crop_mode_assets/sample_direct_center_f003673.jpg)
- Raw metrics: [crop_mode_assets/metrics_dump.json](crop_mode_assets/metrics_dump.json)

## Recommendation

Keep `direct_center`; retire `smooth`.

Deciding criteria, grounded in C5 + stability numbers:

1. C5 zoom envelope: `direct_center` halves the overall zoom envelope
   (peak-to-peak 137 vs 282 px; crop_h std 23.3 vs 42.9 px; crop_size_cv 0.354 vs
   0.399). A tighter, steadier zoom is the C5 intent for the common case.
2. Typical-frame stability: `direct_center` wins center_jerk_p50/p95 and
   quantization_chatter (0.121 vs 0.201). The smooth path's
   `lateral_jitter_dominated` symptom reflects its online deadband re-quantizing
   center motion into chatter.
3. Separability and inspectability: `direct_center` is offline
   position/size-separable signal processing with explicit, documented steps
   (forward-backward EMA, containment clamp, rolling-min fit-to-source). The
   smooth path's online CropController couples confidence into both position and
   size alpha, which is harder to reason about against C5.

The one axis where `smooth` wins is worst-case single-frame spikes
(center_jerk_max 37 vs 260; height_jerk_max 2 vs 31). These spikes are real
runner re-acquire / erasure-boundary events, not torso w/h jitter (verified at
frame 3758). They are a follow-up to harden in `direct_center` (e.g. clamp the
per-frame crop-h step across erasure boundaries) under WS5-impl/M6, NOT a reason
to keep the whole second mode. The shared SIZE_STRENGTH=0.15 size EMA already
present in `direct_center` is the right place to extend that hardening.

AWAITING USER CONFIRMATION
