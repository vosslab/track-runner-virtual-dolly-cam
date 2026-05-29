# Degenerate ROI investigation - 3rd auto-bin bug

Date: 2026-05-29. Read-only audit of "degenerate ROI (h=X, w=0)" errors that knocked out 59 of 120 intervals in #95 v2 (windowed walker, auto-bin re-enabled post-#100).

## Verdict

**COORD-SYSTEM BUG, not real off-frame seeds.** Confidence: high.

Named root cause: **ROI_CLAMP_SPACE_MISMATCH** - distinct second bin-related defect that survived #100. WARP_SCALE_MISMATCH (#99) was Fix 1; DoG/roi_override conversion (#100) was Fix 2; this is Fix 3.

## Raise site

`track_runner/residual_motion.py` lines 584-592 inside `compute_residual_for_frame`. Fires when `roi_h <= 0 or roi_w <= 0` after slicing `center_full[ry1:ry2, rx1:rx2]`.

Error consistently fires at the seed frame (step=0, bootstrap), confirmed by log lines:
```
degenerate ROI (h=63, w=0) at frame 1389   <- left seed frame
degenerate ROI (h=50, w=0) at frame 1482   <- right seed frame (BWD)
```

Nonzero h with w=0 -> y-clamp valid, x-clamp collapses to a point.

## The defect

File: `tools/blob_walk_v2/walk_walker.py` lines 812-813 (bootstrap) and 900-901 (per-step loop). Both blocks identical:

```python
roi_pad = max(20, seed_w)                        # seed_w in SOURCE pixels (e.g. ~200 at 4K)
roi_x1 = max(0, int(acceptance_box[0] - roi_pad))     # source coord, correct
roi_y1 = max(0, int(acceptance_box[1] - roi_pad))
roi_x2 = min(reader.width, int(acceptance_box[2] + roi_pad))    # WRONG: reader.width is POST-bin (960)
roi_y2 = min(reader.height, int(acceptance_box[3] + roi_pad))
roi_override = (roi_x1, roi_y1, roi_x2, roi_y2)
```

`seed_cx`, `seed_cy`, `seed_w`, `seed_h` come from `state_io.load_seeds` in **source-frame coordinates**. `reader.width` and `reader.height` are **post-bin dimensions** per `frame_reader.py` lines 21-22.

## Traced arithmetic - Lyra-Wheeling FWD_1196

| Variable | Value | Space |
| --- | --- | --- |
| seed_cx | 2346.5 | source |
| seed_w | ~200 | source |
| acceptance_box[0] | ~2246 | source |
| acceptance_box[2] | ~2446 | source |
| roi_pad | ~200 | source |
| roi_x1 raw | 2046 | source |
| roi_x1 clamped max(0,_) | 2046 | source |
| roi_x2 raw | 2646 | source |
| **roi_x2 clamped min(reader.width=960, _)** | **960** | post-bin (WRONG) |

`roi_override` exits walker as `(2046, ..., 960, ...)`. In observe_blob_at at bin=4:

| Step | Expression | Result |
| --- | --- | --- |
| source_to_processed(2046) | 2046/4 | roi_ox1_p = 511 |
| source_to_processed(960) | 960/4 | roi_ox2_p = 240 |
| ox1 = max(0, min(960, 511)) | | 511 |
| ox2 = max(511, min(960, 240)) | | max(511, 240) = 511 |
| roi_w = ox2 - ox1 | | **0** |

Zero-width ROI. Raise fires at seed frame.

## Why 59 of 120 fail

All seeds where `cx > 960` (source) hit the inverted-x2 path. On 4K source (3840 wide), that's runner positions in the right ¾ of the frame:
- Lyra-Wheeling (4K): 20/20 fail (runner consistently right-of-center after camera pan).
- Conant (4K): 14/20 fail.
- Jason (4K): 10/20 fail.
- Lyra-Hersey (4K): 15/20 fail.
- IMG_3823 / IMG_3830 (likely 1080p, bin_factor=1 or 2): 0 fail; reader.width >= source_width / bin_factor matches the seed range.

## Minimum fix

File: `tools/blob_walk_v2/walk_walker.py` lines 812-813 and 900-901.

Replace:
```python
roi_x2 = min(reader.width, int(acceptance_box[2] + roi_pad))
roi_y2 = min(reader.height, int(acceptance_box[3] + roi_pad))
```

With:
```python
src_w = reader.geometry.source_width
src_h = reader.geometry.source_height
roi_x2 = min(src_w, int(acceptance_box[2] + roi_pad))
roi_y2 = min(src_h, int(acceptance_box[3] + roi_pad))
```

At bin_factor=1, `source_width == reader.width`, so no-op. Safe both cases.

`roi_x1`/`roi_y1` lower clamps (`max(0, ...)`) are correct - 0 is 0 in both coord spaces.

## Falsified contract

`residual_motion.py` line 1161 docstring: "roi_override arrives in source-pixel coords (walk_walker contract)." Walker violates this by clamping x/y upper bounds against POST-bin dimensions. The observe_blob_at conversion (#100) cannot recover an already-inverted pair.

## Why this is the THIRD auto-bin bug

| # | Defect | Site | Status |
| --- | --- | --- | --- |
| 1 | WARP_SCALE_MISMATCH | residual_motion.py:618 (scale_factor=1.0) | Fixed in #100 |
| 2 | DoG / roi_override source-scale | observe_blob_at:1157, 1178 | Fixed in #100 |
| 3 | ROI_CLAMP_SPACE_MISMATCH | walk_walker.py:812-813, 900-901 | **OPEN - this audit** |

The pattern: auto-bin introduces a source-vs-processed coord ambiguity at every interface where the seed/walker pipeline meets the reader/observer pipeline. Each "fix" plugs one leak; the next leak is at a different boundary. The user's instinct that the design is fragile (per #103 design discussion) is correct.

## Acceptance after fix

Apply minimum fix to walk_walker.py. Re-render 120-corpus. Expect: errors -> 0 (or close), Lyra-Wheeling joins the corpus, both bars hold or improve.
