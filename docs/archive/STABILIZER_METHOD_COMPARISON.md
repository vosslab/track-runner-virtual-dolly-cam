# Stabilizer method/window comparison

Per Patch 5 / M4 of `declarative-shimmying-brooks.md`,
gate revised per Patch 5b feedback.

39 runs of `tools/analyze_torso_box_noise.py` against the 3
representative videos: 1 baseline (`-S none`) plus 12 method/window
pairs (`{median, hampel, mad_gated} x {5, 7, 9, 15}`) each.

## Revised gate definition

The original gate measured `crop_h_fractional_velocity_p95`. That
metric was height-only and used a first-order fractional definition.
The revised gate matches `tools/assess_pixel_zoom.py`'s
`zoom_velocity_log_p95` definition but is computed pre-encode on the
simulated final crop rectangle (post W+H averaging, post forward-
backward EMA, post Step 3.6 first fit, post second-pass EMA, post
second-pass re-fit; only integer rounding is missing relative to the
encoder's actual crop rectangle):

```
final_crop_log_size = log(sqrt(final_crop_w * final_crop_h))
final_crop_log_velocity = abs(diff(final_crop_log_size))
final_crop_log_velocity_p95 = p95(final_crop_log_velocity)
```

This captures fractional zoom change from both width and height in a
single signal and matches the canonical definition used by the post-
encode assessor.

The simulated hotspot severity is the sum of the top-5 peak
intensities of a window-local p95 on the same `final_crop_log_velocity`
series (window = 5 seconds at the video's fps, greedy non-max
suppression with min-gap = window).

## Per-video results: revised gate

Reduction percentages are relative to the same video's `none`
baseline. Negative values mean the stabilized run had a smaller
metric (better).

### 2025-Glenbrook_South-1600m-IMG_1503 (60fps; large torso)

Baseline: `final_crop_log_velocity_p95 = 0.063148`,
`simulated_hotspot_severity = 11.246009`.

| method | window | log_v_p95 | log_v reduction | severity | severity reduction |
| --- | --- | --- | --- | --- | --- |
| median | 5 | 0.063333 | +0.3% | 11.245930 | 0.0% |
| median | 7 | 0.063331 | +0.3% | 11.245750 | 0.0% |
| median | 9 | 0.063151 | 0.0% | 11.245728 | 0.0% |
| median | 15 | 0.063219 | +0.1% | 11.186880 | -0.5% |
| hampel | 5 | 0.063333 | +0.3% | 11.246009 | 0.0% |
| hampel | 7 | 0.063333 | +0.3% | 11.246009 | 0.0% |
| hampel | 9 | 0.063148 | 0.0% | 11.246009 | 0.0% |
| hampel | 15 | 0.063172 | 0.0% | 11.187105 | -0.5% |
| mad_gated | 5 | 0.063333 | +0.3% | 11.246009 | 0.0% |
| mad_gated | 7 | 0.063333 | +0.3% | 11.246009 | 0.0% |
| mad_gated | 9 | 0.063148 | 0.0% | 11.246009 | 0.0% |
| mad_gated | 15 | 0.063172 | 0.0% | 11.187105 | -0.5% |

### IMG_3823 (30fps; small torso)

Baseline: `final_crop_log_velocity_p95 = 0.026464`,
`simulated_hotspot_severity = 1.797963`.

| method | window | log_v_p95 | log_v reduction | severity | severity reduction |
| --- | --- | --- | --- | --- | --- |
| median | 5 | 0.026466 | 0.0% | 1.797641 | 0.0% |
| median | 7 | 0.026462 | 0.0% | 1.796675 | -0.1% |
| median | 9 | 0.026454 | 0.0% | 1.795443 | -0.1% |
| median | 15 | 0.025943 | -2.0% | 1.790962 | -0.4% |
| hampel | 5 | 0.026463 | 0.0% | 1.798137 | 0.0% |
| hampel | 7 | 0.026464 | 0.0% | 1.798186 | 0.0% |
| hampel | 9 | 0.026467 | 0.0% | 1.797765 | 0.0% |
| hampel | 15 | 0.026465 | 0.0% | 1.795371 | -0.1% |
| mad_gated | 5 | 0.026463 | 0.0% | 1.798137 | 0.0% |
| mad_gated | 7 | 0.026464 | 0.0% | 1.798186 | 0.0% |
| mad_gated | 9 | 0.026467 | 0.0% | 1.797765 | 0.0% |
| mad_gated | 15 | 0.026465 | 0.0% | 1.795371 | -0.1% |

### IMG_3830 (30fps; small torso)

Baseline: `final_crop_log_velocity_p95 = 0.017571`,
`simulated_hotspot_severity = 0.275985`.

| method | window | log_v_p95 | log_v reduction | severity | severity reduction |
| --- | --- | --- | --- | --- | --- |
| median | 5 | 0.017605 | +0.2% | 0.274635 | -0.5% |
| median | 7 | 0.017647 | +0.4% | 0.274897 | -0.4% |
| median | 9 | 0.017391 | -1.0% | 0.274658 | -0.5% |
| median | 15 | 0.016967 | -3.4% | 0.274805 | -0.4% |
| hampel | 5 | 0.017570 | 0.0% | 0.274613 | -0.5% |
| hampel | 7 | 0.017608 | +0.2% | 0.274724 | -0.5% |
| hampel | 9 | 0.017611 | +0.2% | 0.274724 | -0.5% |
| hampel | 15 | 0.017627 | +0.3% | 0.275073 | -0.3% |
| mad_gated | 5 | 0.017570 | 0.0% | 0.274613 | -0.5% |
| mad_gated | 7 | 0.017608 | +0.2% | 0.274724 | -0.5% |
| mad_gated | 9 | 0.017611 | +0.2% | 0.274724 | -0.5% |
| mad_gated | 15 | 0.017627 | +0.3% | 0.275073 | -0.3% |

## Acceptance gate evaluation (revised)

### Primary gate

> `final_crop_log_velocity_p95` drops by >= 30% on at least 2 of 3
> videos.

Best (method, window) per video on `log_v_p95`:

| video | best (method, window) | log_v reduction |
| --- | --- | --- |
| Glenbrook | median w9 / hampel w9 / mad_gated w9 | 0.0% |
| IMG_3823 | median w15 | -2.0% |
| IMG_3830 | median w15 | -3.4% |

Maximum reduction across the entire grid is **-3.4%** (median w=15
on IMG_3830). The 30% threshold is not approached on any video.

**Primary gate: FAIL (by an order of magnitude).**

### Secondary gate

> Simulated hotspot severity drops on at least 2 of 3 videos.

For median w=15 (the strongest log_v reducer):

| video | severity reduction |
| --- | --- |
| Glenbrook | -0.5% |
| IMG_3823 | -0.4% |
| IMG_3830 | -0.4% |

All three videos do show a small hotspot-severity drop, so the
literal Secondary gate (drop on >= 2 of 3) technically passes. But
the magnitude of the drop is < 0.5 percent, which is well below the
noise floor of the metric and not a meaningful zoom-bounce
improvement. Reporting this as "passes" would be misleading.

**Secondary gate: TECHNICAL PASS, EFFECTIVELY FAIL.**

## Why the gates fail

The torso-h stabilizer reduces torso-h jitter as designed (median
w=15: -25.2% Glenbrook, -46.2% IMG_3823, -69.2% IMG_3830 measured by
fractional p95 of raw torso_h). But that reduction does not survive
the crop pipeline:

```
torso_h, torso_w
  -> stabilized (this plan)
  -> W+H average: desired_crop_h = 0.5*(h*M + w*M/aspect)
  -> forward-backward EMA (alpha = 0.15)
  -> Step 3.6 fit-to-source pass 1
  -> forward-backward EMA pass 2
  -> Step 3.6 fit-to-source pass 2
  -> final crop rectangle (what we measure)
```

The two EMA passes plus the Step 3.6 fit-to-source clamp absorb the
upstream torso-h variance to the point where the final crop rectangle
is essentially decoupled from the stabilizer's effect. The crop
pipeline already smooths heavily; adding an upstream median filter
on torso w/h does not change what the encoder sees.

This matches the parent finding's structural prediction
([TORSO_NOISE_FINDING.md](TORSO_NOISE_FINDING.md)):
H1 (torso noise correlates with crop noise) was supported in the
correlation sense, but the magnitude of crop-h velocity is not
dominated by torso-h velocity. The dominant residual is downstream
of where this plan intervenes.

## Recommendation

**REJECT the stabilizer for production integration. M5 is BLOCKED.**

The empirical evidence is unambiguous on the revised gate: torso-h
stabilization does not move the zoom-bounce metric the user actually
sees. Maximum reduction on the simulated final crop log-velocity
p95 across all 39 runs is -3.4 percent, against a 30 percent target.

Keep the analyze-tool flags (`-S`, `-W`), the
`torso_size_stabilizer.py` module, the 4 new tests, and the
`final_crop_log_velocity_p95` / `simulated_hotspot_severity` metrics
as diagnostic infrastructure. The library and tests are correct;
they are simply not the right intervention point for the bounce
problem.

The next mechanism to investigate (separate plan) is the crop
pipeline itself: Step 3.6 fit-to-source clamp interactions, the
W+H averaging, or the EMA staircase. These were explicitly deferred
to follow-up by the parent investigation
([TORSO_NOISE_FINDING.md](TORSO_NOISE_FINDING.md)
"H2" and "H3"). The empirical result here is a strong signal that
those follow-ups are required: torso stabilization does not visibly
help, so the residual must come from downstream of where it
intervenes.

## Diagnostic-only legacy metrics (not gates)

For continuity with the original M4 numbers, the height-only
fractional metric still appears in each per-video summary. The
strongest reductions on `torso_h_fractional_velocity_p95` (now
diagnostic-only):

| video | best | torso_h reduction |
| --- | --- | --- |
| Glenbrook | median w15 | -25.2% |
| IMG_3823 | median w15 | -46.2% |
| IMG_3830 | median w15 | -69.2% |

These confirm the stabilizer works on the signal it directly targets,
but per the gate evaluation above, that signal does not propagate to
the simulated zoom-bounce signal at any meaningful magnitude.
