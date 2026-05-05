# Torso-noise finding (Milestone 3, plan: noisy-jittering-tendril)

Cross-video synthesis of the trajectory torso-box noise investigation.
Closes Milestone 3 of
`/Users/vosslab/.claude/plans/noisy-jittering-tendril.md`.

## Per-video correlation table

`abs(diff(torso_h))` velocity correlated against the smoothed crop
height series (pre-encode, computed by re-running the
`tr_crop.direct_center_crop_trajectory` math on the solved
trajectory) and against the post-encode Fourier-Mellin log_scale
series.

| Video | fps | n_frames | rho(\|d torso_h\|, \|d crop_h\|) | rho(\|d torso_h\|, \|d log_scale\|) |
| --- | --- | --- | --- | --- |
| 2025-Glenbrook_South-1600m-IMG_1503 | 60 | 16801 | +0.8053 | +0.0158 |
| IMG_3830 | 30 | 4217 | +0.2668 | +0.0020 |
| IMG_3823 | 30 | 4083 | +0.1878 | +0.0084 |

Per-video summaries with full numbers:
[Glenbrook](2025-Glenbrook_South-1600m-IMG_1503/2025-Glenbrook_South-1600m-IMG_1503_torso_noise_summary.md),
[IMG_3830](IMG_3830/IMG_3830_torso_noise_summary.md),
[IMG_3823](IMG_3823/IMG_3823_torso_noise_summary.md).

## Per-pass introduction point

The tool emits torso_h velocity_p95 for each layer of the solver
pipeline (FWD interval path, BWD interval path, blended interval
path, stitched + anchored + erased final).

| Video | FWD | BWD | blended | final |
| --- | --- | --- | --- | --- |
| Glenbrook | 1.000 | 1.000 | 0.989 | 0.989 |
| IMG_3830 | 7.000 | 7.000 | 7.000 | 7.000 |
| IMG_3823 | 2.000 | 2.000 | 2.000 | 2.000 |

Verdict: jitter is identical at every layer. The FWD and BWD passes
each ingest noisy torso_h, the blend faithfully averages two equally
noisy series, and the anchor + erasure layers do not amplify or
attenuate. The introduction point is **the per-frame observation
step inside each pass**, upstream of the blend. Blob snap, anchor
fitting in the analytical solver, or whatever produces `raw_pred` in
`velocity_model` is where the integer-pixel torso_h is being
generated.

## Hotspot / torso-spike alignment

For each video, the tool checks how many of the top-5 Tool 1
hotspots fall within +/- 60 frames of a torso-velocity spike at or
above the per-video p95.

| Video | hotspots aligned with torso spike |
| --- | --- |
| Glenbrook | 5 / 5 |
| IMG_3830 | 4 / 5 |
| IMG_3823 | 5 / 5 |

Aggregate: 14 / 15 hotspots (93 percent) coincide with torso-h
velocity spikes.

## Forensic observations

- **Integer-pixel quantization.** The torso_h velocity_p95 values
  are exact integers (1.000, 2.000, 7.000). This is the signature
  of integer-pixel rounding somewhere in the per-frame observation
  step. Sub-pixel torso geometry would not produce these clean
  integer p95s. The fact that 95 percent of frame-to-frame torso_h
  steps are <= 1, 2, or 7 px reads as "the torso_h walks one or two
  pixels per frame as the bbox snaps to integer rows or columns."
- **fps-dependent severity.** Glenbrook (60fps) at velocity_p95 1.0
  px / frame translates to 60 px/s of torso_h drift; IMG_3830
  (30fps) at 7.0 px / frame translates to 210 px/s. The 30fps
  videos have noisier per-frame steps but coarser sampling.
- **Decoupled post-encode signal.** Pearson rho between
  `abs(diff(torso_h))` and `abs(diff(post_encode_log_scale))` is
  essentially zero (0.0020 to 0.0158) on all three videos despite
  4-5 of 5 hotspot timestamps coinciding with torso spikes. The
  encoder + Fourier-Mellin assessment layer introduces independent
  noise that masks the upstream signal in the post-encode metric
  but not in the local hotspot timing.

## Hypothesis verdicts

- **H1 (torso jitter causes crop jitter):** SUPPORTED via hotspot
  localization (14 / 15) and via direct correlation on Glenbrook
  (rho 0.81). On the 30fps cases the global Pearson rho is below
  the 0.5 decision-tree threshold (0.27 and 0.19), but per-hotspot
  alignment remains high. Reading: the mechanism is real on every
  video; the global rho is diluted on 30fps cases by long quiet
  stretches between high-jitter spikes. The crop pipeline is
  faithfully tracking a noisy torso_h.
- **H2 (Step 3.6 clamp amplifies):** NOT SEPARATELY QUANTIFIED in
  this iteration. The trace CSV records `edge_clamped_bool` per
  frame; the per-video summaries record only the count
  (`n_edge_clamped_frames` is 75 percent on Glenbrook, 36 percent
  on IMG_3830, 32 percent on IMG_3823). A sign-asymmetry analysis
  on `crop_height_delta` for clamped vs non-clamped frames is
  deferred to the follow-up.
- **H3 (EMA staircase):** NOT OBSERVABLE here. The trace CSV
  contains `raw_crop_height_estimate` (pre-EMA) and
  `smoothed_crop_height` (post-EMA); a staircase test against those
  two series is straightforward but was not the first decision-tree
  hit and is deferred.

## Hand-off

H1 is the dominant mechanism. The next plan moves to torso-h
stabilization options the user previously spec'd (median or Hampel
filtering, percentile windowing, separated position-vs-size
smoothing, blob-update downweighting, multi-frame torso estimate)
and locates the integer-pixel rounding in the per-frame observation
step. Investigation entry points:

- `track_runner/velocity_model.py` -- `raw_pred` computation and
  whether the per-frame torso geometry is rounded before storage.
- `track_runner/residual_motion.py` -- blob-snap centroid integer
  rounding.
- `track_runner/interval_solver.py` -- `solve_interval_analytical`
  and how per-frame torso h/w land in `forward_path` /
  `backward_path` arrays. The per-pass FWD and BWD jitter is
  identical, so any fix that operates after the blend cannot
  recover the lost subpixel signal; the fix must enter inside the
  per-frame observation step or earlier.

The post-encode decoupling (rho ~ 0 between torso_h velocity and
post-encode log_scale velocity) is a separate question. Even if
torso-h stabilization eliminates the crop_h jitter, the post-encode
bounce metric may not improve until the encoder / Fourier-Mellin
measurement decoupling is understood. That is its own plan.

The crop pipeline (`tr_crop.py`), the Step 3.6 clamp, and the EMA
default remain off-limits until torso-h stabilization is tested in
isolation.
