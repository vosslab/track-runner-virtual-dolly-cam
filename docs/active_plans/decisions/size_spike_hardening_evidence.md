# Size spike hardening evidence

Robust size stabilization is now wired into the size channel. It protects
against isolated torso-size spikes and preserves real scale ramps, but
current visible breathing is broadband near the rounding floor and remains
a known limitation.

Evidence-gated wiring of the robust torso-size stabilizer into the
production crop path. Output-feel change only: no schema bump, no stored
torso boxes / npz changed, no per-video config added, no crop_mode default
change. This is for the human keep/revert decision.

This is size-spike hardening, not a zoom-bounce fix. Reducing the broadband
breathing is a SEPARATE future evidence task (longer window / stronger EMA /
crop-height deadband), not done here.

AWAITING USER CONFIRMATION at the end.

## What changed

The robust size stabilizer (`track_runner/torso_size_stabilizer.py`,
median / hampel / mad_gated filters, already written and unit-tested) was
dead code with zero importers. It is now wired into the crop SIZE (w/h)
channel BEFORE the existing size-EMA, at a single insertion point that
covers BOTH crop modes.

- Insertion point: `track_runner/tr_crop.py`, function
  `trajectory_to_crop_rects`, immediately after the dense `full_trajectory`
  is assembled (gap-filled) and BEFORE the `crop_mode` dispatch. Both
  `direct_center` (consumes w and h) and `smooth` (consumes h via
  `CropController`) read this stabilized trajectory, so one wiring point
  covers both. This edits only the in-memory crop trajectory; the stored
  torso boxes / npz are untouched.
- Method/window chosen: `median`, window 7
  (`CROP_SIZE_STABILIZER_METHOD = "median"`,
  `CROP_SIZE_STABILIZER_WINDOW = 7`). Justification from the data is below.
- Position (cx, cy) is never touched by the stabilizer (C5 separability);
  only w and h are routed through the filter.
- The size-EMA (`CROP_POST_SMOOTH_SIZE_STRENGTH = 0.15`) is kept in place
  AFTER the robust stage for cosmetic smoothing, per the diagnostic.

## Method/window justification (changed from the diagnostic recommendation)

The diagnostic recommended `hampel`. The data does not support hampel on
these real trajectories. Measured per-frame torso-h behavior on the two
solved trajectories:

| video | size | torso-h median | per-frame step median | step p95 | step max |
| --- | --- | --- | --- | --- | --- |
| Hononega-Orion 600m | 2816x1584 | 158 px | 1.0 px | 7.0 px | 47 px |
| Lyra-Wheeling | 3840x2160 | 44 px | 0.0 px | 1.0 px | 10 px |

Fraction of frames each filter edits, and the residual per-frame step it
leaves on the torso-h series:

| video | filter | frames edited | residual step p95 |
| --- | --- | --- | --- |
| Hononega | hampel w7 | 1.8% | 6.0 px |
| Hononega | hampel w9 | 1.6% | 6.0 px |
| Hononega | median w5 | 8.5% | 5.0 px |
| Hononega | median w7 | 13.1% | 5.0 px |
| Hononega | median w9 | 17.4% | 5.0 px |
| Lyra | hampel w7 | 0.0% | 1.0 px |
| Lyra | median w7 | 0.0% | 1.0 px |

Reading: the residual torso jitter on these trajectories is BROADBAND
(every frame wiggles about +/-1 px), not isolated single-frame spikes.
A MAD-gated Hampel filter at k=3 therefore almost never fires (under 2% of
frames) because the local MAD is large when the whole neighborhood is
noisy, so the gate threshold is high. Hampel does not lower the per-frame
step (p95 stays at 6 vs the raw 7). A window-local `median` actually
reduces the torso-h step p95 from 7 px to 5 px on the 4K Hononega clip.

Ramp preservation (steepest sustained 30-frame torso growth span, percent
of the genuine growth retained after filtering):

| video | steepest 30-frame growth | median w7 retained | median w9 retained |
| --- | --- | --- | --- |
| Hononega | 150 px | 147 px (98%) | 143 px (95%) |
| Lyra | 18 px | 17 px (94%) | 17 px (94%) |

`median` window 7 preserves 98% of the steepest real scale ramp while
flattening the high-frequency wiggle: the local median tracks a monotone
ramp and only rejects the breathing. Window 7 matches the ~6-7 frame time
constant of the size-EMA. This is why `median` window 7 was chosen over
`hampel`. The median also still replaces true single-frame spikes with the
local median when they occur (proved by the synthetic C5 test).

## Before vs after through the full crop pipeline

`trajectory_to_crop_rects` was run on each real trajectory for both modes,
with the robust stage OFF (BEFORE = size-EMA only, the prior production
state) and ON (AFTER = median w7 then size-EMA, the new production state).

`mean_trans` is the mean jitter-transmission ratio (fraction of the
single-frame torso w/h jitter, scaled by the torso_height_multiple gain,
that reaches the crop height) on interior solved frames. `norm_jit` is the
RMS per-frame crop-height step divided by the median crop height (the
direct zoom-bounce metric). `p95` is the 95th-percentile crop-height step
in px. `ramp` is the crop-height growth over the steepest real scale span.

| video | mode | mean_trans before | mean_trans after | norm_jit before | norm_jit after | p95 step before/after | ramp before/after |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Hononega | direct_center | 0.410 | 0.407 | 0.00875 | 0.00877 | 6.0 / 6.0 | 387 / 386 |
| Hononega | smooth | 0.547 | 0.529 | 0.00710 | 0.00706 | 9.0 / 9.0 | 623 / 621 |
| Lyra | direct_center | 0.111 | 0.111 | 0.00381 | 0.00380 | 2.0 / 2.0 | 107 / 107 |
| Lyra | smooth | 0.137 | 0.137 | 0.00299 | 0.00299 | 2.0 / 2.0 | 167 / 167 |

## Honest verdict: transmission is NOT meaningfully reduced on these clips

The crop-height jitter and transmission ratios are essentially UNCHANGED
before vs after (the largest move is mean_trans 0.547 -> 0.529 on Hononega
smooth, about a 3% relative reduction; norm_jit barely moves; p95 is
identical). The fix does NOT distort real scale tracking (the ramp column
is preserved to within 1-2 px in every row), which is the one thing it had
to not break. But it also does not materially reduce zoom bounce on these
two trajectories.

Root cause of the null result, and why it is not a wiring bug:

1. The stored production trajectories do not contain the single-frame
   outliers the robust filter targets. The residual bounce is broadband
   +/-1 px breathing already near the integer-rounding floor.
2. The robust median reduces the torso-h p95 step from 7 to 5 px, but that
   reduction sits UPSTREAM of an already-effective size-EMA (alpha 0.15)
   plus the direct_center W+H averaging and integer rounding, all of which
   already absorb most of the 1-2 px difference before it reaches crop
   height. Two stages chasing the same broadband 1 px wiggle cannot stack.

The wiring IS the right design (separates robust size TRACKING from
cosmetic size SMOOTHING, reuses existing code, leaves position untouched,
preserves real ramps) and it WILL reject genuine single-frame spikes when
they occur (the synthetic C5 test proves a 60% single-frame spike yields a
stable crop height after the fix). It simply has near-zero effect on the
two trajectories measured because they are already spike-free.

## Real cropped sample frames

Skipped on purpose. The before/after crop rectangles differ by at most 1 px
in height on every frame of both clips (see the p95 and ramp columns:
identical or off by 1). A 1 px crop-height difference is below the visual
threshold, so side-by-side cropped frames would be pixel-identical and
would misrepresent the change. The crop-h-vs-frame plots below are the
substantive evidence. Source videos ARE available
(`Hononega-Orion_600m-IMG_3702.mkv`, `Lyra-Wheeling-IMG_3912.mkv` under the
external TRACK_VIDEOS drive); frames were omitted because they would not
distinguish before from after, not because the video was missing.

## Plots

Full-clip crop height before (EMA only) vs after (median w7 + EMA):

- `zoom_bounce_fix_Hononega-Orion_600m-IMG_3702_direct_center.png`
- `zoom_bounce_fix_Hononega-Orion_600m-IMG_3702_smooth.png`
- `zoom_bounce_fix_Lyra-Wheeling-IMG_3912_direct_center.png`
- `zoom_bounce_fix_Lyra-Wheeling-IMG_3912_smooth.png`

Real-scale-ramp zoom-in (proves genuine scale change is still tracked):

- `zoom_bounce_fix_ramp_Hononega-Orion_600m-IMG_3702_direct_center.png`
- `zoom_bounce_fix_ramp_Hononega-Orion_600m-IMG_3702_smooth.png`
- `zoom_bounce_fix_ramp_Lyra-Wheeling-IMG_3912_direct_center.png`
- `zoom_bounce_fix_ramp_Lyra-Wheeling-IMG_3912_smooth.png`

## Recommendation

Honest recommendation: KEEP the wiring as a design correction, but do NOT
expect it to fix the residual zoom bounce on already-spike-free
trajectories. It is a correct, safe, ramp-preserving separation of robust
size tracking from cosmetic smoothing, and it reuses existing tested code
with zero risk to position or stored artifacts. Its value is insurance:
when a future trajectory does carry single-frame torso spikes (the failure
mode the diagnostic feared), the median stage will reject them before they
reach crop height. On the two clips measured the effect is negligible
because the spikes the diagnostic worried about are not actually present in
the stored trajectories.

## Known limitation: broadband sub-pixel breathing remains

The visible residual breathing on current clips is BROADBAND ~1 px wiggle
near the integer-rounding floor, not isolated spikes. This change does NOT
measurably reduce it (see the null-effect before/after table above). That
broadband breathing is an OPEN known limitation.

Reducing it is a SEPARATE future evidence task, not done here. Candidate
future levers (each trades ramp responsiveness and needs its own evidence):

- a longer-window median on the size channel;
- a stronger size-EMA (lower alpha) after the robust stage;
- a crop-height deadband that suppresses sub-threshold height changes.

This change deliberately does not pursue any of those tradeoffs.

AWAITING USER CONFIRMATION
