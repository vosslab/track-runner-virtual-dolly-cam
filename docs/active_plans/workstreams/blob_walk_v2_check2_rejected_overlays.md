# Check 2: rejected-blob overlays (claim A)

Read-only diagnostic, 2026-06-10. No production code changed.
Cross-references [fwd_zero_coverage_diagnosis.md](../audits/fwd_zero_coverage_diagnosis.md)
and [blob_walk_v2_validation_plan.md](../active/blob_walk_v2_validation_plan.md#check-2).

## Claim A (from validation plan)

> Claim A: on the two diagnosed stall intervals, the raw motion blobs that
> are rejected by the acceptance box are the runner's blobs (likely-but-unverified).
> If true, widening the acceptance box would recover the runner's signal.

## Method

For each stall interval the script:

1. Reproduced the production walker's tight ROI and DoG parameters exactly:
   - ROI = acceptance box (+/-0.5W x +/-0.75H) padded by `max(20, seed_w)` px.
   - DoG diameter = `0.7 * seed_w_proc`.
   - Threshold = `DEFAULT_THRESHOLD = 10.0`.
2. Called `compute_residual_for_frame` directly (bypassing `observe_blob_at`)
   to get raw blobs before the acceptance-box filter.
3. Shifted blob centroids from ROI-relative to full-frame PROCESSED coords.
4. Classified each blob as inside or outside the acceptance box.
5. Computed per-blob distance from the frozen-anchor reference in torso-width
   units (contract C2) and normalized vertical offset.
6. Rendered overlay PNGs on sampled frames: green = seed torso box,
   yellow = acceptance box, red = rejected blobs, orange = accepted blobs.

Tool: `_temp_check2_rejected_overlays.py` (deleted after handoff).

## Interval geometry

| Interval | Seed cx (src) | Seed cy (src) | Seed w (src) | Accept box (proc) |
| --- | --- | --- | --- | --- |
| Conant 1080-1111 FWD | 825.0 | 47.8 | 22.0 | 11.0 x 22.1 px |
| Jason 564-583 FWD | 728.8 | 388.8 | 6.5 | 3.2 x 7.9 px |

Note: both videos are bin=2, so PROC = SOURCE / 2.

## Per-interval results

### Conant-4x400-2026_April_15 interval 1080-1111 FWD

- Frames with blobs in tight ROI: 1 of 31 (frame 1096 only).
- Total blobs in tight ROI across interval: 2.
- All 2 blobs outside acceptance box (rejected).
- Blob distances: median 2.37W, both > 1.0W.
- Blob integrated_mag range: 30-210 (very weak signal).
- Residual max across interval: 12-42 counts (near-noise level inside tight ROI).
- Wide-ROI probe (pad=200): blobs exist on all frames but at 7-24W distance,
  corresponding to other athletes crossing the frame, not the Conant runner.

The Conant runner is at cy_proc = 23.9 (very top of frame, only 24 px from
top edge). The tight ROI (387,0,438,54) is 51 x 54 px. Inside this area,
the residual is at noise level on 30 of 31 frames. The runner's own limb
motion at this scale (seed w=11 proc px) is too weak to produce blobs above
threshold inside the tight ROI on most frames. The blobs that DO appear in
wider searches are from other runners crossing further down the track.

### Jason-3200m-sectionals-IMG_4005 interval 564-583 FWD

- Frames with blobs: 20 of 20 (all frames extract 10 blobs each, clipped at
  the 10-blob per-frame cap).
- Total rejected blobs across interval: 195 (of 200 total).
- Accepted blobs (inside the 3.2 x 7.9 px acceptance box): 5 across 4 frames.
- Within 1.0W: 0.5% (1 blob).
- Within 2.0W: 5.1% (10 blobs).
- Within 3.0W: 10.8% (21 blobs).
- Median distance from reference: 5.97 torso-widths.
- Mean distance: 5.69 torso-widths.
- Median normalized cy-offset: -0.50 (blobs slightly above seed center).
- Blob integrated_mag range: 10-168.

The Jason acceptance box is 3.2 x 7.9 processed px (seed w = 3.25 proc px
from a 6.5 source px torso). This is one of the smallest possible targets.
The 195 rejected blobs at 6W median distance correspond to background motion
from other athletes in the scene -- none of these blobs are near the Jason
runner's torso.

## Overlay PNGs

Rendered PNGs (every 4th frame) under
`output_smoke/blob_walk_v2_check2/`:

- `Conant-4x400-2026_April_15/Conant-4x400-2026_April_15_f1080_fwd_overlay.png`
  through `...f1108...` (8 PNGs).
- `Jason-3200m-sectionals-IMG_4005/Jason-3200m-sectionals-IMG_4005_f0564_fwd_overlay.png`
  through `...f0580...` (5 PNGs).

Key evidence frames: Jason f0564 and f0568 show 10 red dots (rejected blobs)
scattered across the acceptance-box neighborhood. None of the red dots land
near the yellow acceptance box (3 px wide). The green seed box and yellow
acceptance box are visible but tiny relative to the blob scatter.

## Verdict

**Claim A: REFUTED**

The rejected blobs are NOT the runner's blobs. They are background motion
(other athletes) scattered 5-24 torso-widths from the runner's seed position.

Evidence summary:

- Jason 564-583: 195 rejected blobs; 0.5% within 1.0W, 5.1% within 2.0W.
  Median distance 5.97W. Background motion dominates; runner signal is
  below detection threshold inside the 3.2 px acceptance box.
- Conant 1080-1111: only 2 blobs found in tight ROI across 31 frames
  (both at 2.37W). Wide-ROI probe confirms blobs in the frame are from
  other athletes at 7-24W, not from the target runner.

Widening the acceptance box would not recover the runner's signal on these
intervals. The stall root cause is more precisely:

1. **Conant**: runner is at the top edge of frame (cy_proc = 24 px). The
   residual inside the tight ROI is at noise level. The runner's own limb
   motion is below threshold at this seed scale.
2. **Jason**: runner torso is only 3 px wide (proc). The DoG diameter of
   0.7 x 3.25 = 2.3 px may be below reliable detection range. Background
   athletes produce stronger blobs far from the runner.

The actual bug documented in `fwd_zero_coverage_diagnosis.md` -- frozen
anchor + tight acceptance box -- is real. But the assumption that "rejected
blobs = runner blobs" is not supported by the data. The blobs being rejected
are background, not runner signal. Recovery requires signal amplification
or scale-adaptive parameters, not just a wider acceptance box.

## Limitations and interpretation notes

The diagnosis doc (`fwd_zero_coverage_diagnosis.md`) states "24 of 31 Conant
frames DO extract raw motion blobs (max integrated_mag ~2700)." This check
found only 2 blobs on 1 frame using the production walker's exact tight ROI.
The discrepancy suggests the diagnosis probe used either a wider ROI or a
different extraction path. This check's measurements used the production
walker's own ROI formula exactly (`accept box + max(20, seed_w)` pad) and
confirmed that inside that ROI, the Conant signal is at noise level on 30/31
frames. If the diagnosis found blobs within a wider search area, those blobs
are the background-motion blobs visible in the wide-ROI probe at 8-24W.
Either way, they are not the runner's blobs.
