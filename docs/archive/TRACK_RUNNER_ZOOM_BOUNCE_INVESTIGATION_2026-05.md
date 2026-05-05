# Track-runner zoom-bounce investigation (2026-05)

Archive of the May 2026 investigation into residual zoom-bounce after
the 2026-05-02 crop changes. The active live evidence
(`HOTSPOTS_INDEX.md`, `BASELINE_DIAGNOSTIC.md`) lives at
[output_smoke/zoom_bounce/EVIDENCE/](../../output_smoke/zoom_bounce/EVIDENCE/)
and is NOT moved here; this archive records the plan, decision, and
hand-off only.

## Plan reference

The original investigation plan was
`/Users/vosslab/.claude/plans/declarative-shimmying-brooks.md`.
Title: *Encode zoom-bounce: build assessment tools while user produces
the corpus*.

## Trigger

Three synergistic crop changes shipped on 2026-05-02 targeted the
long-standing zoom-bounce complaint: removed the `crop_min_size`
floor; averaged width-driven and height-driven crop-height estimates
in `direct_center_crop_trajectory`; bumped the
`crop_post_smooth_size_strength` default from 0.0 to 0.15. The user
reported residual jitter in the 1-2% range was still
human-noticeable. An independent code-tracing pass identified Step
3.6 fit-to-source as a one-sided clamp ratchet hypothesis: the
asymmetric clamp at
[track_runner/tr_crop.py:855-885](../../track_runner/tr_crop.py)
should fire when the centered crop extends past a source edge and
should be the dominant source of residual bounce. The plan was
written to test that hypothesis.

## Original plan structure

| Milestone | Purpose | Status |
| --- | --- | --- |
| 1 | Build four assessment tools (hotspot finder, edge correlator, spectrum analyzer, variant ranker) | DONE |
| 1b | Tighten measurements after reviewer feedback (detrended hotspot score, lagged Spearman, metric directionality, black-bar meter) | DONE |
| 2a | User produces baseline corpus (10 `*_tracked.mkv` files) | DONE |
| 2b | User produces Path A (clamp-disabled) variants | CANCELLED |
| 3a | Baseline-only diagnostic to validate the ratchet hypothesis on real footage before any variant production | DONE |
| 3b | Variant comparison + DECISION.md + follow-on plan | BLOCKED (no variants) |
| 4 | Optional cleanup of dead `crop_zoom_stabilization` branch | OPTIONAL (deferred) |

## Decision

The Step 3.6 fit-to-source ratchet hypothesis is **REJECTED** by the
data.

Three representative videos (Glenbrook-1600m, IMG_3830, IMG_3823)
produced edge-distance Spearman correlation coefficients of `-0.102`,
`-0.020`, and `-0.014` at lag-0. Best-lag scans over `-5..+5` frames
did not rescue the hypothesis. The reviewer's proceed-to-Path-A
criterion (positive correlation in >= 2 of 3 representative videos)
is met `0 of 3`. Path A (flipping `crop_centered_fit_to_source` to
`False`) would not fix the residual bounce because the clamp is not
the bounce source.

Decision: do NOT produce Path A variants; do NOT run Milestone 3b;
investigate the actual signal regimes the spectrum analysis surfaced
instead.

## Spectrum signature

Two regimes, fps-dependent:

| Regime | Videos | Dominant frequency | Period | EMA cutoff | Plausible mechanism |
| --- | --- | --- | --- | --- | --- |
| Low-frequency drift | Glenbrook (60 fps) | 0.024 Hz | 42 s | 1.59 Hz | camera-motion drift, crop controller lag, scene-transform integration, torso-box size walk |
| Near-Nyquist jitter | IMG_3830, IMG_3823 (30 fps) | ~14 Hz | 0.07 s | 0.80 Hz | integer-pixel crop rounding, bbox rounding, encoder rate-control flutter, source single-frame motion |

Neither regime sits in the band the EMA at
`crop_post_smooth_size_strength=0.15` is designed to attenuate, so
neither is fixable by tuning that knob.

## Hand-off

Investigation continues in **one** focused follow-up plan at
`/Users/vosslab/.claude/plans/`:

**[noisy-jittering-tendril.md](../../../.claude/plans/noisy-jittering-tendril.md)** -- trajectory torso-box noise as the cause of zoom bounce.
The working hypothesis: zoom bounce is caused by frame-to-frame
noise in the solved torso box, especially h/w. The crop algorithm
faithfully tracks the noisy h, so crop_h inherits the variance.
The encoded log_scale is a downstream symptom. Primary cases:
Glenbrook-1600m, IMG_3830, IMG_3823. Builds
`tools/analyze_torso_box_noise.py` to emit per-frame
`crop_zoom_trace.csv` (torso h/w, derived crop sizes pre/post EMA,
post-fit, edge-clamped flag, deltas, jerk) and per-hotspot 4-series
plots. Mechanism-first; no torso-h smoothing tuning, no clamp
disabling, no EMA changes until trajectory-noise hypothesis is
quantified.

Two earlier follow-up plans were drafted but immediately superseded
because they targeted the wrong layer (video-frequency / encoder
behavior, not trajectory geometry):

- `meandering-drifting-tide.md` (60fps low-freq drift; SUPERSEDED)
- `staccato-flickering-pixel.md` (30fps near-Nyquist jitter;
  SUPERSEDED)

Both kept on disk with SUPERSEDED banners pointing at
`noisy-jittering-tendril.md`.

## Tools shipped during this investigation

| Tool | Purpose |
| --- | --- |
| [tools/find_zoom_hotspots.py](../../tools/find_zoom_hotspots.py) | Top-N worst-bounce windows; velocity_p95 / rms_detrended / abs_smoothed scoring; clip extraction |
| [tools/correlate_bounce_with_edge.py](../../tools/correlate_bounce_with_edge.py) | Edge-distance Spearman with lagged scan; frame-alignment diagnostics |
| [tools/spectrum_zoom_bounce.py](../../tools/spectrum_zoom_bounce.py) | FFT power spectrum with EMA cutoff annotation |
| [tools/rank_zoom_variants.py](../../tools/rank_zoom_variants.py) | Multi-variant ranking with directionality enforcement |
| [tools/measure_black_bars.py](../../tools/measure_black_bars.py) | Letterbox exposure measurement (Path A artifact-cost meter; not used after rejection) |
| [tests/test_tr_zoom_bounce_tools.py](../../tests/test_tr_zoom_bounce_tools.py) | 12 behavioral tests for all five tools |
| [docs/ZOOM_BOUNCE_REVIEW_RECIPE.md](../ZOOM_BOUNCE_REVIEW_RECIPE.md) | User-facing workflow guide |

## Key implementation notes (forensic, for future reference)

- The default Tool 1 hotspot score `velocity_p95` saturates at
  `2 * 0.223 = 0.446` because the underlying Fourier-Mellin scale is
  clamped to `[0.80, 1.25]` in `assess_pixel_zoom`. The implementation
  filters samples with `|log_scale| >= 0.20` before percentile to
  avoid saturating ties on noisy windows. See
  `LOG_SCALE_CLAMP_THRESHOLD` in
  [tools/find_zoom_hotspots.py](../../tools/find_zoom_hotspots.py).
- `common_tools.probe_video.probe_video` returns a dict, not a tuple.
  Tools 1, 2, 5, plus the existing assess_pixel_zoom, were updated to
  match.
- `correlate_bounce_with_edge.py` default `--frame-tolerance` is 2.
  Real-corpus video-vs-trajectory frame counts routinely differ by
  hundreds of frames; pass `-T 5000 -M head` for production use.
- Lyra-Wheeling-IMG_3912 was excluded from the diagnostic because
  `mediainfo` reported invalid fps for the tracked output. Re-encode
  with explicit fps metadata to include this video in future runs.
