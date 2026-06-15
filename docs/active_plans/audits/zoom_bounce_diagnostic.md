# Zoom bounce diagnostic

Read-only audit of crop zoom bounce in the production crop path. No
production code or `docs/CHANGELOG.md` was changed. Measured on two real
solved trajectories from `tr_config/`.

## Verdict (one line per mode)

- `direct_center` (production default): zoom bounce is PARTIALLY FIXED.
  Single-frame torso w/h jitter is attenuated, not separated. The size-EMA
  passes roughly 40% of unsmoothed jitter through to crop height.
- `smooth` (legacy `CropController`): zoom bounce is PARTIALLY FIXED, same
  mechanism, similar transmission. Online deadband plus the same size-EMA.
- Robust size stabilizer (`torso_size_stabilizer.py`) is NOT wired into the
  crop path. It is dead code. This is the primary residual root cause.

Definition used (per contract C5): zoom bounce is the crop HEIGHT changing
frame-to-frame in reaction to single-frame torso w/h jitter while the
runner's true scale is stable. Legitimate zoom is gradual size change
tracking the runner getting closer or farther.

## Production size to crop-zoom path (traced)

Encode entry: `track_runner/cli.py:2521`
`_mode_encode` -> `tr_crop.trajectory_to_crop_rects(...)`.

`trajectory_to_crop_rects` (`track_runner/tr_crop.py:1099`) reads
`crop_mode` from config (`track_runner/tr_crop.py:1179`). Both production
configs set `crop_mode: direct_center`
(`tr_config/track_runner.config.yaml:6`, and per-video
`tr_config/IMG_3823.track_runner.config.yaml:6`).

### direct_center (production)

`direct_center_crop_trajectory` (`track_runner/tr_crop.py:712`):

- Crop height is derived from BOTH torso dimensions and averaged
  (`tr_crop.py:797-799`):
  `desired_crop_h = 0.5 * (raw_h*mult + (raw_w*mult)/aspect)`.
  This averaging is a per-frame de-noiser: if `w` or `h` is noisy on one
  frame, the average dampens it. It does NOT remove temporal jitter.
- The ONLY temporal size stabilization is a forward-backward EMA with
  `alpha_size = CROP_POST_SMOOTH_SIZE_STRENGTH = 0.15`
  (`tr_crop.py:36`, applied at `tr_crop.py:809-810`, and re-applied after
  the fit-to-source ceiling at `tr_crop.py:983-984`).
- The rate-limiter / biased-monotonicity block (`tr_crop.py:823-906`) is
  explicitly SKIPPED whenever `alpha_size > 0` (`tr_crop.py:831-834`), so in
  production it never runs. The doc comment there says the EMA alone handles
  zoom stability.
- A rolling-min fit-to-source ceiling (`_rolling_min_ceiling_per_frame`,
  `tr_crop.py:308`) can FORCE the crop smaller near a frame edge. This is a
  separate, geometry-driven zoom source unrelated to torso jitter.

So in production, crop height jitter is controlled by exactly one
mechanism: the size-EMA at alpha 0.15.

### smooth (legacy)

`compute_crop_trajectory` -> `CropController.update`
(`tr_crop.py:462`). Crop height comes from `th / fill`
(`tr_crop.py:491`) using ONLY torso height (no w/h average). An online EMA
with attack/release alpha and a deadband smooths `smooth_size`
(`tr_crop.py:562-568`). Then `trajectory_to_crop_rects` applies the SAME
offline size-EMA at alpha 0.15 via `smooth_crop_trajectory`
(`tr_crop.py:1204-1213`, `tr_crop.py:1307-1309`).

## Is torso_size_stabilizer wired in? NO

`grep` across `track_runner/` and `tools/` finds zero importers of
`track_runner/torso_size_stabilizer.py`. Its functions
`stabilize_torso_size` and `stabilize_trajectory`
(median / hampel / mad_gated robust size filters) are referenced only
inside their own module docstring. `cli.py`, `tr_crop.py`, and the
encoder do not import it.

This is the most actionable finding: a purpose-built robust size
stabilizer exists but the production crop path does not use it. Crop zoom
stability rests entirely on a single low-alpha EMA.

## C5 separability check: center vs size

Center and size ARE computed independently in both modes. In
`direct_center`, `smoothed_cx/cy` (`tr_crop.py:801-807`, position EMA
alpha 0.0 = glued to runner) and `smoothed_h` (`tr_crop.py:809-810`) are
separate signals; the containment clamp moves center only. In `smooth`,
center and size are independent EMA channels in `CropController.update`.

Consequence: a stable center with noisy w/h produces a stable crop POSITION
but a BOUNCING crop HEIGHT, because the height channel still tracks the
noisy w/h through the EMA. C5 separability of position-vs-size is satisfied;
C5's "noisy w/h must not create zoom bounce" is only partially satisfied
because the size channel has no robust (median/hampel) stage, only an EMA.

## Measured jitter transmission

Method: assemble the dense blended trajectory from
`*.torso_box_coords.npz` (loader shape per
`track_runner/state_io.py:669`), run `trajectory_to_crop_rects` for each
mode, and measure per-frame `|crop_h[t] - crop_h[t-1]|` against the
per-frame torso w/h single-frame step. Transmission ratio = actual crop-h
step divided by the crop-h step that an unsmoothed 1:1 pass-through of
torso jitter would produce (gain `mult*0.5` for h, `mult*0.5/aspect` for
w). A near-zero ratio means jitter is suppressed; a high ratio means
jitter reaches the crop height.

Measured on interior solved frames only (leading/trailing
fallback frames excluded).

| video | mode | median transmission | mean transmission | frozen frac (<1px) | norm torso jitter | norm crop-h jitter |
| --- | --- | --- | --- | --- | --- | --- |
| Hononega-Orion 4K (2816x1584) | direct_center | 0.384 | 0.535 | 0.361 | 0.0063 | 0.0029 |
| Hononega-Orion 4K | smooth | 0.384 | 0.670 | 0.330 | 0.0063 | 0.0019 |
| IMG_3823 (1280x720, tiny torso) | direct_center | 0.000 | 0.246 | 0.748 | 0.0000 | 0.0000 |
| IMG_3823 | smooth | 0.000 | 0.288 | 0.669 | 0.0000 | 0.0000 |

Reading:

- On the 4K clip the size-EMA cuts normalized crop-h jitter to roughly
  HALF the torso jitter (0.0029 vs 0.0063 for direct_center), and freezes
  the zoom on ~36% of frames. That is real attenuation, not separation:
  the median transmission ratio is 0.38, so about 40% of single-frame
  torso jitter still reaches crop height.
- On IMG_3823 the torso is only 10-17 px tall, so crop_h is ~50-90 px and
  integer rounding dominates: median crop-h step is 0 px and 67-75% of
  frames are frozen. Zoom bounce is invisible there because the crop is
  small, not because the stabilization is strong.

### EMA alpha sweep (isolates the only size-stabilization stage)

Same trajectory, same fit-to-source pipeline, `direct_center`, sweeping the
production constant `CROP_POST_SMOOTH_SIZE_STRENGTH`:

| video | alpha_size | median crop-h step | p95 step | norm jitter | frozen frac |
| --- | --- | --- | --- | --- | --- |
| Hononega 4K | 0.00 (no EMA) | 2.0 px | 3.0 px | 0.0057 | 0.153 |
| Hononega 4K | 0.15 (production) | 1.0 px | 5.0 px | 0.0029 | 0.361 |
| Hononega 4K | 0.30 | 1.0 px | 7.0 px | 0.0029 | 0.261 |
| Hononega 4K | 0.50 | 1.0 px | 9.0 px | 0.0029 | 0.234 |

This is the decisive evidence:

- The EMA at 0.15 halves median crop-h jitter versus no smoothing
  (2 px -> 1 px). It works.
- Raising alpha does NOT reduce median jitter further (stuck at 1 px from
  integer rounding) and RAISES p95 (5 -> 9 px). An EMA cannot remove
  single-frame reaction; it passes `alpha * jitter` through every frame.
  That is the structural ceiling of an EMA-only design.

### Worst-frame nature

The largest crop-height steps on the 4K clip are smooth multi-frame ramps
that track real torso growth during re-acquire and approach (for example
the frame-347 region: torso_h rises 88 -> 92 px over five frames while
crop_h ramps 113 -> 159 px monotonically). Those are LEGITIMATE scale
changes, not bounce. The residual bounce is the steady-state +/-1 px
per-frame breathing on stable-scale frames captured by the norm-jitter
numbers above, plus occasional EMA-passed single-frame spikes at p95.

## Plots

- Hononega-Orion direct_center: `zoom_bounce_Hononega-Orion_600m-IMG_3702_direct_center.png`
- Hononega-Orion smooth: `zoom_bounce_Hononega-Orion_600m-IMG_3702_smooth.png`
- IMG_3823 direct_center: `zoom_bounce_IMG_3823_direct_center.png`
- IMG_3823 smooth: `zoom_bounce_IMG_3823_smooth.png`

Each plot shows raw torso w/h (top, the input jitter) against output crop
height (bottom) over a representative 600-frame window. The crop-height
trace is visibly smoother than the torso traces but still tracks their
short-term wiggle on the 4K clip.

## Root cause of residual bounce

The size channel has exactly ONE stabilization stage: a forward-backward
EMA at alpha 0.15. An EMA is a low-pass filter, not an outlier rejector:
on every frame it injects `alpha * (desired_h - smoothed_h)`, so a
single-frame torso spike always moves the crop height by ~15% of the
spike. With a stable center and noisy w/h, the crop height therefore still
breathes. The robust median/hampel size filter that would actually reject
single-frame outliers (`torso_size_stabilizer.py`) exists but is not
called anywhere in the crop path.

## Recommended fix (NOT implemented)

Preferred (durable, "fix the design"): wire
`track_runner/torso_size_stabilizer.py` into the size channel of
`direct_center_crop_trajectory` BEFORE the size-EMA. Apply a robust
median or hampel/mad_gated filter to the per-frame `desired_crop_h`
series so single-frame torso w/h outliers are rejected outright, then let
the existing EMA do final cosmetic smoothing. This separates size tracking
(robust, trend-following) from size smoothing (cosmetic), which is exactly
the C5 separability the contract asks for. It reuses existing, already
written code.

Lesser alternatives (treat the symptom, do not adopt alone):

- Raise `CROP_POST_SMOOTH_SIZE_STRENGTH`. Rejected: the alpha sweep shows
  this does not lower median jitter and worsens p95.
- Make crop height strictly trend-following (e.g. clamp `desired_crop_h`
  to a long-window median trend and forbid per-frame deviation beyond a
  small band). This would remove the residual breathing but risks lagging
  genuine fast scale changes during re-acquire; the robust-filter-then-EMA
  approach is gentler and keeps fast legitimate zooms.

Any size-channel change is an output-feel change only (it does not touch
solve geometry or stored artifacts), so it does not require a schema bump.
It does require human approval before implementation.

## Status update and open known limitation

The robust stabilizer was wired in (median window 7), see
[size_spike_hardening_evidence.md](../decisions/size_spike_hardening_evidence.md).
The honest outcome: this is size-spike hardening, not a zoom-bounce fix. It
rejects isolated single-frame torso w/h outliers and preserves real
multi-frame scale ramps, but the measured effect on the current clips is
near-zero.

OPEN KNOWN LIMITATION: the visible residual breathing on current clips is
BROADBAND ~1 px wiggle near the integer-rounding floor, not isolated
spikes, so the spike-rejecting stabilizer does not measurably reduce it.
Reducing the broadband breathing is a SEPARATE future evidence task, with
these candidate future levers (each trades ramp responsiveness):

- a longer-window median on the size channel;
- a stronger size-EMA (lower alpha) after the robust stage;
- a crop-height deadband that suppresses sub-threshold height changes.
