# Auto-bin coord-stack audit

Date: 2026-05-28. Read-only audit of why #72 auto-bin (`bin_factor>1`) breaks the windowed walker end-to-end while passing the unit tests + #85 24-corpus run.

## Evidence

`blob_walk_v2/120corpus_windowed/Conant-4x400-2026_April_15/seed_3256_3272/fwd_verdicts.csv` (bin_factor=4): all 16 frames emit `soft_miss_no_blob`, `obs_corridor_n=0`, `pred_cx=931.5` (= 3726/4, source-frame centroid).

`blob_walk_v2/24corpus_windowed/Conant-4x400-2026_April_15/seed_3256_3272/fwd_verdicts.csv` (bin_factor=1, ran before #72): same interval had `FWD accepted_count > 0`.

## Named root cause: WARP_SCALE_MISMATCH

`track_runner/residual_motion.py:618` (production path of `compute_residual_for_frame`):

```python
warp_mat = build_warp_matrix(
    scene_transform, frame_index, fi_other, 1.0,
)
```

`scale_factor=1.0` is hard-coded. `motion_track.dx/dy` are stored in source-pixel units (camera_motion.py:720-723, 800-803 upscale by bin_factor before persist - contract upheld). `build_warp_matrix` multiplies `(cum_dx_n - cum_dx_n1 * rel_scale)` by `scale_factor`. Frames being warped are POST-bin (reader applied `_apply_bin`). At `bin_factor=4`, the translation applied to the post-bin frame is 4x too large -> catastrophic misalignment -> validity_mask rejects most pixels -> empty residual -> empty corridor_blobs.

## Secondary defect: DOG_DIAMETER / ROI_OVERRIDE source-scale

`walk_walker.walk_one_direction` lines 815-816, 903-904: `dog_diameter_override = 0.7 * seed_w` (source pixels). `observe_blob_at` lines 1178-1181 use the override as-is without converting via `geometry.source_to_processed_delta`. The `pred_w_p` fallback IS scaled correctly; only the override bypasses conversion. At bin_factor=4 the DoG kernel is 4x too large.

Same issue with `roi_override`: computed source-pixel in `walk_walker`, clamped to `reader.width/height` (post-bin) inside `observe_blob_at` lines 1157-1162 without conversion. Source-pixel bounds exceed post-bin frame; clamp produces effectively full-frame.

## Minimum fix

Two targeted changes within existing function boundaries.

### Fix 1 (primary)

In `residual_motion.compute_residual_for_frame` production path (line 617-619):

```python
bin_factor = getattr(reader, "bin_factor", 1)
warp_mat = build_warp_matrix(
    scene_transform, frame_index, fi_other, 1.0 / bin_factor,
)
```

### Fix 2 (secondary)

In `residual_motion.observe_blob_at`, convert override args from source to processed pixels via the already-available `geometry` object before use:

- Lines 1178-1181 (dog_diameter_override use site): `dog_diameter_actual = geometry.source_to_processed_delta(dog_diameter_override)` when override is set.
- Lines 1157-1162 (roi_override clamp site): convert the four corners via `geometry.source_to_processed` before clamping to `reader.width/height`.

The `geometry` object is assigned at line 1131; the `bin_factor != 1` guard already exists.

## Per-topic audit

| Topic | Status | Finding |
| --- | --- | --- |
| T1: seed coord conversion | VERIFIED | pred_cx=931.5 is source-frame centroid; no defect |
| T2: warp dx/dy scale | DEFECT | scale_factor=1.0 at residual_motion.py:618; must be 1/bin_factor |
| T3: validity_mask | VERIFIED | correct shape; consequence of T2 misalignment |
| T4: DoG diameter / roi_override scale | DEFECT | override args not divided by bin_factor in observe_blob_at |
| T5: source_to_processed functions | VERIFIED | functions correct; override args bypass conversion |
| T6: motion_track dx/dy units | VERIFIED | source-pixel contract upheld by both camera_motion estimators |
| T7: _compute_roi pred_h scale | VERIFIED (default) | correct when roi_override=None; T4 defect when set |

## Why #85 24-corpus passed despite this defect

The 24-corpus rerun (#85) ran BEFORE #72's `walk_io.py` edit landed. The reader was constructed with `bin_factor=1` so both fixes are no-ops. The defect surfaced only on the 120-corpus rerun (#95) after auto-bin took effect.

## Why unit tests passed

#72 added 7 tests for `select_bin_factor_for_analysis` in isolation (pure math). No integration test exercised the walker through `compute_residual_for_frame` with `bin_factor>1`. The walker tests stub out `observe_blob_at`. The Model B claim in the #72 handoff was correct for the entry/exit points of `observe_blob_at` but did not cover the `_override`-args paths or the warp-matrix scale_factor.

## Acceptance after fix

Both fixes land. Re-enable auto-bin in `tools/blob_walk_v2/walk_io.py` (remove the pinned `bin_factor=1`). Re-run 24-corpus to confirm 42.3%/41.0% reproduces under bin_factor=4. Then re-run 120-corpus.
