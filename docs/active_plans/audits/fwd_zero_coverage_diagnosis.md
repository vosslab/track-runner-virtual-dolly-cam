# Fwd zero-coverage diagnosis

Read-only root-cause audit, 2026-06-08, gating WP-6. No production code was
changed. Cross-references the M4 A/B report
[m4_walker_ab_report.md](../reports/m4_walker_ab_report.md) and its appended
independent-verification note, and the standalone walk manifests under
`corpus_walk/`.

## Summary verdict

Forward zero-coverage on the two named intervals is a **bug**, not an expected
absence of forward motion signal. The residual-motion pipeline extracts real,
strong blobs on nearly every forward frame, but every blob is rejected as
`acceptance_box_empty`: the candidates fall outside the tight acceptance box
that the walker anchors on the (frozen) seed center. Because the windowed
walker has no velocity model, the prediction anchor stays pinned at the start
seed until a frame is accepted, so the very first failure becomes permanent and
the whole forward span collapses to interpolation. The forward/backward
asymmetry is incidental: the backward pass happens to accept at its own seed
frame and therefore reports a real (low) coverage fraction instead of `None`.

The single most likely cause: the acceptance box geometry and anchor in
[walk_walker.py](../../../track_runner/blob_walk/walk_walker.py)
`_compute_roi_and_observe` (half-width `0.5*seed_w`, half-height `0.75*seed_h`,
centered on a never-advancing seed anchor), interacting with the no-velocity
frozen-anchor step.

## What `cov FWD = None` actually means

`blob_coverage_fwd` comes from `_coverage_from_track`
([interval_solver.py](../../../track_runner/interval_solver.py) lines 100-122).
It returns `None` exactly when `candidate_count == 0`, where `candidate_count`
counts only frames whose `blob_gate` is `accepted` or `rejected`. The walker
status -> blob_gate map
([walker_bundle.py](../../../track_runner/walker_bundle.py) lines 50-56) sends
`accepted` -> accepted, `soft_miss_no_path` -> rejected, and
`interpolated` / `extrapolated` / `soft_miss_no_blob` -> absent (endpoints ->
skipped). So `cov FWD = None` means the forward pass produced zero `accepted`
frames AND zero `soft_miss_no_path` frames: every interior frame was
`soft_miss_no_blob` (empty candidate list). It is a coverage failure, not a
mis-placement: the scored "forward path" is then back-filled entirely by
`_build_full_span_path` interpolation (walker_bundle.py lines 241-354), and an
interpolated forward path scored against a blob-tracked backward path under the
Dice agreement metric is what collapses the M4 `agreement` number.

## Per-interval evidence

Source of truth: a read-only probe that walks each interval forward via the
production library path
([walk_walker.walk_one_direction](../../../track_runner/blob_walk/walk_walker.py)),
with `residual_motion.observe_blob_at` and `extract_frame_blobs` wrapped in
memory to record per-frame reject reasons and raw-blob counts. The two named
intervals are not on disk under `corpus_walk/` (that set is an older standalone
`walk_driver` sample with a different per-frame CSV schema), so the per-frame
detail had to be regenerated from the live videos in `TRACK_VIDEOS/`. The
result reproduces the M4 `accepted_fraction 0.0` exactly.

### Conant interval seed 1080-1111

- Forward start seed (frame 1080): cx 825.0, cy 47.8, w 22.0, h 29.5. The
  runner is high in the frame near the top edge (cy 47.8 with h 29.5).
- Image-plane motion across the interval: 23 px x, 43 px y over 31 frames
  = 0.74 px/frame x, about 0.03 torso-widths per frame. This is a near-
  stationary interval (camera holds), so a motion-overrun explanation is ruled
  out.
- Forward result: `accepted_fraction = 0.000`, accepted_count 0, all 31 emitted
  frames `soft_miss_no_blob`, stop `hit_neighbor_seed`.
- Reject-reason distribution over the forward span: `no_raw_blobs` x7,
  `acceptance_box_empty` x24. Raw extraction wrapper shows 24 of 31 frames DO
  extract raw motion blobs (max integrated_mag rising to ~2700), yet all are
  outside the acceptance box.
- Backward start seed (frame 1111): cx 802.0, cy 90.8, w 20.0, h 25.5.
  Backward result: accepted_count 4 of 31 (fraction 0.129) -- low, but nonzero,
  so `candidate_count > 0` and coverage is a real fraction, not `None`.

### Jason-3200m interval seed 564-583

- Forward start seed (frame 564): cx 728.8, cy 388.8, w 6.5, h 10.5. This is a
  very small torso (6.5 px wide), so the acceptance box half-width is only
  ~3.2 px and half-height ~7.9 px.
- Image-plane motion: 3.8 px x, 1.0 px y over 19 frames = 0.20 px/frame,
  about 0.03 torso-widths per frame. Again near-stationary.
- Forward result: `accepted_fraction = 0.000`, accepted_count 0, all 19 frames
  `soft_miss_no_blob`.
- Reject-reason distribution: `acceptance_box_empty` x19 (all frames). Raw
  extraction shows all 19 frames extract 5-10 strong raw blobs each (max
  integrated_mag ~3000-3700), every one outside the ~3 px acceptance box.
- Backward start seed (frame 583): cx 725.0, cy 389.8, w 7.0, h 10.5. Backward
  result: accepted_count 2 of 19 (fraction 0.105) -- again low but nonzero.

## Extracted vs filtered: the decisive distinction

The candidates are EXTRACTED, then FILTERED OUT. On both forward passes the
residual is computed, blobs are found above threshold, and the failure is at the
geometric acceptance-box test in
[residual_motion.observe_blob_at](../../../track_runner/residual_motion.py)
lines 1324-1339: keep a blob only if its centroid lies inside
`[cx +/- 0.5*w] x [cy - 0.75*h, cy + 0.75*h]` around the anchor. With the anchor
frozen at the seed center and the moving residual blobs (legs, arms, swinging
limbs) systematically offset from the torso-box center, every blob lands
outside the box, the list comes back empty, `observe_blob_at` returns `None`,
and the frame is `soft_miss_no_blob`.

This is a self-reinforcing stall, by design of the no-velocity walker:

- The prediction anchor is `last_accepted_cx/cy` and only updates on an accept
  (walk_walker.py lines 1040-1041, 377-380; there is no velocity model).
- The bootstrap frame observes at the exact seed center
  (`_run_bootstrap_step`); on both intervals the bootstrap itself returns
  `None` (`no_raw_blobs` for Conant frame 1080, `acceptance_box_empty` for
  Jason frame 564). So `last_accepted` never leaves the seed center.
- With a frozen anchor, every subsequent frame's blobs are measured against the
  same stale box and keep missing. One early miss cascades into a whole-span
  miss.

## Why forward fails but backward does not (the asymmetry)

The asymmetry is not a directional code path difference. FWD and BWD run the
same `walk_one_direction` with opposite `sign` and opposite anchor seeds
([walker_bundle.build_walker_bundles_for_interval](../../../track_runner/walker_bundle.py)
lines 156-188): FWD anchors on the left seed, BWD on the right seed, each using
its own seed box as the torso-unit scale. On these two intervals the backward
seed frame happens to have a residual blob inside its acceptance box at the
bootstrap (so BWD accepts a few frames and reports a real fraction), while the
forward seed frame does not (so FWD stays frozen and reports `None`). Which end
"catches" is a coin-flip of where the limb residual sits relative to the torso
box at that particular seed frame. The standalone `corpus_walk/` manifests
corroborate that one-sided / strongly asymmetric coverage is common: of 120
intervals on disk, many show large FWD/BWD accepted-count splits in BOTH
directions (for example Conant 2284-2346 fwd 10 / bwd 51; Jason 6110-6204 fwd
88 / bwd 14; Jason 22230-22372 fwd 9 / bwd 81), which is the same box-anchoring
fragility surfacing as asymmetry rather than as a hard zero.

## Verdict: bug, not expected

Bug. The forward passes have abundant forward motion signal (raw blobs every
frame), so "those forward starts genuinely have no motion signal" is falsified
by direct measurement. The zero coverage is produced by a fixable
acceptance-box anchoring/sizing interaction with the no-velocity frozen-anchor
step, not by an honest absence of evidence. This is a corridor/ROI/bootstrap
asymmetry, exactly the failure mode the M4 independent-verification note
suspected.

## Recommended next step for WP-6 gating

WP-6 should not treat the walker as "worse than Hermite" on the strength of
these intervals, and it should not ship the walker as default-on until the
acceptance-box stall is fixed. Two concrete, separable actions:

1. Primary (code fix, highest value): fix the frozen-anchor / acceptance-box
   stall in
   [walk_walker.py](../../../track_runner/blob_walk/walk_walker.py). The fix
   belongs in `_compute_roi_and_observe` and the bootstrap/step anchor logic,
   not in the metric. Candidate directions (for the fix owner to choose and
   validate, all expressible in torso units per contract C2):
   - Widen the acceptance box from `0.5*w` half-width toward the runner-relative
     search radius already defined for the walker
     (`walk_motion_gate.bootstrap_search_radius_w`), so a near-stationary
     runner's limb blobs are admitted.
   - Recover the anchor when the bootstrap fails: if the seed-frame observation
     returns `None`, the walker must not pin the anchor to the seed center for
     the entire span; allow the window/Viterbi to acquire from the strongest
     in-ROI blob rather than requiring a centroid inside a torso-centered box.
   - Verify against the empirical gate below.

2. Secondary (metric hardening, do regardless): one-sided coverage intervals
   (`blob_coverage_fwd is None` xor `blob_coverage_bwd is None`) must be bucketed
   separately in the A/B classification in
   the former `e2e_walker_ab.py` `_classify`, because
   an interpolated empty pass scored against a tracked pass under the Dice
   self-consistency `agreement` metric is an apples-to-oranges comparison and
   cannot license a "regressed" label. This matches the independent-verification
   note's priority-1 recommendation. Do not weaken the metric for two-sided
   intervals.

### Settling instrumentation (already run; reproduce for the fix gate)

The verdict here is empirical, not provisional: the per-frame reject reasons and
raw-blob counts above were measured directly from the live videos via read-only
library calls. To gate the WP-6 fix, re-run the same forward walk on these two
intervals and confirm the forward span flips from all `soft_miss_no_blob`
(reject reason `acceptance_box_empty` with raw_blobs present) to a nonzero
`accepted_count`, then re-read the M4 A/B deltas with both passes two-sided.
