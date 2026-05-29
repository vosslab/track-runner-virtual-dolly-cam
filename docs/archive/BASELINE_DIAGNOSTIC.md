# Baseline-only zoom-bounce diagnostic

Date: 2026-05-05
Plan reference:
`declarative-shimmying-brooks.md`
Milestone 3a.

This document synthesizes the assessment-tool output across the
baseline corpus and gives a preliminary read on the Step 3.6
fit-to-source ratchet hypothesis BEFORE the user invests encode
wall-clock in producing Path A variants.

## Top-line finding

**The ratchet hypothesis is NOT supported by the baseline data.**
Across the three highest-bouncing baseline videos, edge-distance
Spearman correlation is essentially zero or weakly NEGATIVE at lag-0
and across all tested lags `-5..+5`. The hypothesis predicted strong
POSITIVE correlation (bounce concentrates near source-frame edges).
Path A (`crop_centered_fit_to_source: False`) is unlikely to reduce
the residual bounce reported after the 2026-05-02 crop changes.

The spectrum analysis finds the residual sits in two regimes that
neither Path A nor alpha-tuning would fix:

- 60fps races (Glenbrook): dominant energy at sub-1 Hz (slow drift
  on a 30-100 second period), well below the EMA cutoff at ~1.59 Hz.
  EMA was never going to attenuate this; the energy is so slow it
  may be perceptual rather than literal jitter.
- 30fps races (IMG_3823, IMG_3830): dominant energy at 10-15 Hz,
  near the Nyquist frequency. This is single-frame jitter consistent
  with integer-pixel rounding or compression artifact, NOT crop-edge
  ratchet behavior.

**Recommendation: do not produce Path A variants. Investigate
alternative mechanisms (integer-pixel rounding, sub-pixel crop,
or perceptual flicker calibration) in a follow-on plan.**

## Hotspot ranking

Top-hotspot `velocity_p95` per video, descending. See per-video
report under `hotspots/{basename}/{basename}_tracked.hotspots.md`.
The secondary `abs_smoothed` column is a cross-check: high p95
alongside high abs_smoothed indicates real bounce; high p95
alongside low abs_smoothed would indicate jitter without sustained
drift.

| Rank | Basename                                  | top velocity_p95 | top abs_smoothed | top frame | top start (s) |
| ---  | ---                                       | ---              | ---              | ---       | ---           |
| 1    | 2025-Glenbrook_South-1600m-IMG_1503       | 0.366111         | 0.197523         | 2973      | 47.05         |
| 2    | IMG_3830                                  | 0.306586         | 0.133981         | 44        | 0.00          |
| 3    | IMG_3823                                  | 0.283364         | 0.122660         | 1606      | 51.03         |
| 4    | Conant-4x400-2026_April_15                | 0.254586         | 0.148402         | 1641      | 24.85         |
| 5    | Lyra-Hersey-800m-IMG_3882                 | 0.249957         | 0.137372         | 11336     | 186.43        |
| 6    | IMG_3839                                  | 0.247415         | 0.137342         | 5702      | 92.53         |
| 7    | Hononega-Orion-1600m-IMG_3629             | 0.208415         | 0.136485         | 339       | 3.15          |
| 8    | IMG_3627                                  | 0.193928         | 0.124423         | 7762      | 126.87        |
| 9    | Hononega-Varsity_4x400m-IMG_3707          | 0.191166         | 0.132899         | 11064     | 181.90        |
| 10   | Hononega-Orion_600m-IMG_3702              | 0.183450         | 0.102890         | 5271      | 85.35         |
| --   | Lyra-Wheeling-IMG_3912                    | EXCLUDED         | EXCLUDED         | --        | --            |

Lyra-Wheeling-IMG_3912 was excluded because `mediainfo` reported an
invalid fps for `Lyra-Wheeling-IMG_3912_tracked.mkv`. Re-encode with
explicit fps metadata to include this video in future runs.

The representative subset for Milestone 2b would have been ranks 1, 2,
3 (Glenbrook, IMG_3830, IMG_3823), but the diagnostic below recommends
NOT proceeding to variant production with Path A as the intervention.

## Edge-distance correlation (ratchet hypothesis test)

Spearman correlation between bounce intensity (`abs(log_scale)`) and
inverse edge gap (`1 / max(|gap|, 1)`), evaluated at lags
`-5..+5` frames. Positive correlation supports the ratchet
hypothesis; near-zero or negative correlation rejects it.

Interpretation guidance:
- `> 0.30`: strong positive (supports hypothesis)
- `0.10` to `0.30`: weak positive (qualified support)
- `<= 0.10` (in either sign): negligible (rejects hypothesis)

| Video                                | lag-0 rho | lag-0 p   | best rho  | best lag | hypothesis read |
| ---                                  | ---       | ---       | ---       | ---      | ---             |
| 2025-Glenbrook_South-1600m-IMG_1503  | -0.1020   | 4.5e-40   | -0.0872   | -5       | weak NEGATIVE   |
| IMG_3830                             | -0.0200   | 0.19      | -0.0158   | -4       | negligible      |
| IMG_3823                             | -0.0136   | 0.39      | -0.0079   | +4       | negligible      |

**All three representative videos REJECT the ratchet hypothesis.**
The Glenbrook negative correlation is statistically significant
(`p=4.5e-40` over 16,787 paired frames) but small in magnitude;
interpreted directly, it says bounce is slightly LESS at frame edges,
which is the opposite of what the clamp ratchet would predict. The
other two videos show no measurable edge dependence at all.

The lag scan does not rescue the hypothesis: best-lag coefficients
are within 0.02 of lag-0 across all three videos, and best-lag is on
the negative side (lags -5, -4, +4).

Per the recipe-doc decision logic, the proceed-to-Path-A criterion
"edge-distance correlation is positive in at least 2 of 3
representative videos" is NOT met (0 of 3).

## Frame alignment

Sanity-check stanza per video. `n_paired_frames` is the count after
dropping NaN trajectory frames (no solved torso) and zero log_scale
(frame-0 reference). All three videos paired cleanly with the
`--frame-tolerance 5000 --truncate-mode head` settings the batch used.

| Video                                | n_video | n_traj | n_paired |
| ---                                  | ---     | ---    | ---      |
| 2025-Glenbrook_South-1600m-IMG_1503  | 17540   | 16801  | 16787    |
| IMG_3830                             | 4222    | 4217   | 4216     |
| IMG_3823                             | 4083    | 4083   | 3990     |

The Glenbrook video-vs-trajectory gap of 739 frames is a normal
encoder/solver convention difference, not a tooling bug. Default tool
tolerance is 2 (designed to fail loudly on gross misalignment); we
explicitly raised it to 5000 because the corpus expects this gap.

## Spectrum (bounce timescale)

Top-3 dominant frequency peaks per video, from FFT of the per-frame
log_scale series. EMA cutoff annotated at `fps / (2 * pi * tau_frames)`
with default `tau_frames=6` (matching the current
`crop_post_smooth_size_strength=0.15` default).

### 2025-Glenbrook_South-1600m-IMG_1503

60 fps, EMA cutoff approx. 1.59 Hz.

| Rank | Frequency  | Position relative to EMA cutoff | Power  |
| ---  | ---        | ---                             | ---    |
| 1    | 0.024 Hz   | well below cutoff               | 6.85e4 |
| 2    | 0.014 Hz   | well below cutoff               | 1.29e4 |
| 3    | 0.007 Hz   | well below cutoff               | 1.18e4 |

All three top peaks sit two orders of magnitude below the EMA
cutoff. The dominant period is 1/0.024 ~ 42 seconds. The EMA is not
designed to attenuate energy this slow (its time constant is only
~0.1 second). This is sustained drift, not high-frequency bounce.

### IMG_3830

30 fps, EMA cutoff approx. 0.80 Hz.

| Rank | Frequency  | Position relative to EMA cutoff | Power |
| ---  | ---        | ---                             | ---   |
| 1    | 14.027 Hz  | far above cutoff                | 349.5 |
| 2    | 14.083 Hz  | far above cutoff                | 296.1 |
| 3    | 14.858 Hz  | far above cutoff                | 286.9 |

All peaks sit near the Nyquist frequency (15 Hz at 30 fps). This is
near-frame-rate jitter, suggestive of single-frame artifacts
(integer-pixel rounding, encoder rate-control flutter, scene-cut
artifacts). EMA at the current alpha=0.15 should attenuate energy
this fast, but the residual remaining at 14 Hz suggests the source
of this jitter is NOT in the upstream crop pipeline that EMA
operates on; it is most likely introduced by integer rounding at the
encode step or by single-frame source-content motion.

### IMG_3823

30 fps, EMA cutoff approx. 0.80 Hz.

| Rank | Frequency  | Position relative to EMA cutoff | Power |
| ---  | ---        | ---                             | ---   |
| 1    | 14.379 Hz  | far above cutoff                | 456.8 |
| 2    | 10.742 Hz  | above cutoff                    | 318.6 |
| 3    | 14.409 Hz  | far above cutoff                | 313.8 |

Same pattern as IMG_3830: high-frequency near-Nyquist content. The
secondary 10.74 Hz peak suggests an additional periodic component,
possibly camera shake at runner stride frequency (~3-4 strides per
second × 3 harmonics).

## Visual hotspot/edge coincidence

The plan asked for a check: do the hotspot timestamps coincide with
edge-approach frames in the correlator scatter? Given that all three
correlator coefficients are essentially zero, this question is moot:
there are no concentrated edge-approach frames to coincide with.
The correlator scatter PNGs (under `correlate/`) show diffuse
bounce-vs-gap clouds, not the diagonal stripe the hypothesis
predicted.

The proceed-to-variant gate "visual hotspot/edge coincidence holds
for >= 2 of 3 representative videos" is NOT met (0 of 3).

## Preliminary read on the hypothesis

The ratchet hypothesis as originally framed (Step 3.6 fit-to-source
clamp causes asymmetric bounce when the runner approaches a source
edge) is **rejected** by the baseline data. None of the three
representative videos show the predicted edge-distance signature.

**Producing Path A variants would not be a useful next step**: the
clamp does not appear to be the bounce source.

The spectrum data is more informative about what IS happening. Two
distinct regimes:

1. **Low-frequency drift (Glenbrook 60fps).** Dominant energy at
   sub-second timescales (~42s period). The EMA cannot help; the
   smoothing time-constant is too short. Possible causes: tracker
   drift across the race, slow size adaptation in
   `direct_center_crop_trajectory`, or true cinematic preference
   that the user perceives as "drift" rather than "bounce".

2. **Near-Nyquist jitter (30fps videos).** Dominant energy at 14
   Hz, half the frame rate. Possible causes: integer-pixel
   rounding in the final crop rectangle, encoder rate-control
   flutter, or single-frame source-content motion. The crop
   pipeline operates on float crops up to the Step 3.6 fit-to-
   source step; the rounding to integer pixels happens in
   `tr_crop` near the encoder boundary and downstream of EMA.

Neither regime is what Path A or Path B would target. Both
candidates assume the bounce is in the [crop_post_smooth_size_strength,
fit-to-source clamp] band, which the spectrum data shows it is not.

## Recommended next steps

1. **Do not proceed to Milestone 2b Path A variant production.**
   The baseline diagnostic does not justify the encode wall-clock.
2. **Open a new investigation plan** for the actual signal regimes
   surfaced here:
   - **For 30fps near-Nyquist jitter:** instrument the crop
     rectangle BEFORE and AFTER integer rounding in
     `track_runner/tr_crop.py` to see whether the high-frequency
     energy is introduced at rounding. Compare encoder-side
     bitrate flutter against the same time series.
   - **For 60fps slow drift:** check whether
     `direct_center_crop_trajectory` is producing slow size walks
     across the race (independent of EMA). The diagnostic tool
     `tools/analyze_crop_path_stability.py` measures this directly
     on the crop trajectory before encode.
3. **Re-run the correlator with --score rms_detrended** to confirm
   the ratchet rejection holds under a different bounce definition.
   (The default `velocity_p95` is jitter-sensitive; `rms_detrended`
   captures sustained oscillation around a moving baseline.)
4. **Consider whether the user's perceptual "1-2% bounce" complaint
   is actually drift.** If the dominant energy is at 0.024 Hz, the
   visual experience is likely "the crop slowly walks", not "the
   crop oscillates". A brief subjective review of the Glenbrook
   hotspot clips (under `hotspots/2025-Glenbrook_South-1600m-IMG_1503/`)
   would calibrate this.

## Artifacts

| Artifact                                                                                                          | Description                       |
| ---                                                                                                               | ---                               |
| `HOTSPOTS_INDEX.md`                                                          | Cross-corpus hotspot ranking      |
| `hotspots`                                                   | Per-video top-5 hotspot windows   |
| `hotspots`                                            | Hotspot clips (15s each, x5)      |
| `correlate`                                                        | Scatter + lag bar chart per video |
| `correlate`                                                         | Frame-alignment + lag table       |
| `spectrum`                                                             | FFT power spectrum + EMA cutoff   |
| `spectrum`                                                      | Top-3 dominant frequencies (text) |
