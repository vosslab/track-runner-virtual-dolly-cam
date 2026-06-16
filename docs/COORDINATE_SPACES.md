# Coordinate spaces

The single contract for pixel coordinate spaces in the binning pipeline.
If any code comment or other doc contradicts this file, this file wins and
the other thing is the bug to fix.

This contract was settled on 2026-05-29 ("Option A"). It replaces the older
"model B" comment (all public coords are SOURCE) that lived in
[common_tools/frame_reader.py](../common_tools/frame_reader.py); that comment
is now deleted.

## The two spaces

There are exactly two pixel spaces in the analysis pipeline:

- SOURCE -- full-frame pixels at the video's native resolution. This is the
  storage and consumption space: the per-frame torso-box npz (written and read
  via `track_runner/state_io.py`) is SOURCE, and the encoder consumes SOURCE.
- PROCESSED -- post-bin plus goodbox-snap pixels. This is the analysis space:
  the walker decodes frames here, steps here, and draws here. `reader.width`
  and `reader.height` are PROCESSED dimensions.

`source == processed` only at `bin_factor == 1` (and only when goodbox snap is
also a no-op, which it is at `bin_factor == 1`). At `bin_factor > 1` the two
spaces differ and must not be mixed.

## Binned-by-default and the single storage boundary

Solve and walker analyze in PROCESSED space by default. The default bin factor
is computed by `common_tools/frame_reader.select_default_bin_factor`:

```
bin_factor = max(1, floor(source_width / TARGET_DEFAULT_WIDTH_PX))
```

`TARGET_DEFAULT_WIDTH_PX = 1440` is a project-wide constant in
`common_tools/frame_reader.py` (not config).

| Source width | bin_factor | Processed width |
| --- | --- | --- |
| 3840 (4K) | 2 | 1920 |
| 2880 (2.8K) | 2 | 1440 |
| 2560 (1440p) | 1 | 2560 (full-res) |
| 1920 (1080p) | 1 | 1920 (full-res) |

Use `--bin N` for an exact override or `--bin 1` as an escape hatch. Use
`--auto-bin HEIGHT` for a height-based target (different formula; see SOLVE.md).

The entire solve runs in ONE coordinate space (PROCESSED at bin > 1). Conversion
to SOURCE happens exactly once, at the storage boundary, immediately before
`state_io.write_torso_box_coords`, via `geometry.processed_to_source` (centers)
and `geometry.processed_to_source_delta` (width/height). Hermite produces SOURCE
coordinates directly (its `MotionTrack.dx/dy` are stored in SOURCE pixels). The
walker produces PROCESSED coordinates and must always cross the boundary before
persist. At `bin_factor = 1` the conversion is an identity no-op.

The camera-motion artifact keys on `bin_factor`. The phase-correlation estimator
runs on PROCESSED frames, so the stored SOURCE dx/dy depend on the analysis bin.
A bin change invalidates the camera-motion artifact and forces a recompute. No
SCHEMA_VERSION bump is involved; this is artifact-identity bookkeeping only.

The interval fingerprint does NOT key on `bin_factor`. Stored torso boxes are
always unbinned SOURCE-frame coordinates, so bin is a runtime performance
setting that does not change what the artifact stores. A solve at one bin and a
refine at another reuse all unchanged intervals. See
[TR_SCHEMA_VERSION_HISTORY.md](TR_SCHEMA_VERSION_HISTORY.md) for the interval
reuse identity rule and the full fingerprint allow-list.

## Conversions are pure scale

`FrameGeometry` (held by the reader as `reader.geometry`) provides the
conversions:

- `source_to_processed` / `processed_to_source` for centers (cx, cy).
- `source_to_processed_delta` / `processed_to_source_delta` for sizes (w, h).

These conversions are PURE SCALE. They divide or multiply by `bin_factor`.
They do NOT clamp to frame bounds. This is the agreed design, not an
oversight.

### The goodbox subtlety

Goodbox snaps the scaled dimensions DOWN to FFT-friendly sizes. As a result
`processed_width` can be strictly less than `source_width // bin_factor`.

Because `source_to_processed` is pure scale (`source / bin_factor`), a SOURCE
coordinate near the right or bottom edge can map to a PROCESSED coordinate that
lands OUTSIDE the processed frame (its value exceeds `processed_width` or
`processed_height`). This is expected and correct: the conversion reports where
the point scales to, not whether it is in frame.

A goodbox-aware clamp inside the conversion was rejected. Clamping inside the
conversion hides the off-frame condition and silently relocates the point.
Instead, frame bounds are an EXPLICIT predicate:
`coord_space.ProcessedPoint.in_bounds(geometry)`. Callers that need to know
whether a processed point is on-frame ask `in_bounds` after converting; an
off-frame point becomes an honest in-bounds soft-miss decision at the caller,
not a degenerate ROI deep inside the observer.

## Who lives in which space

| Thing | Space |
| --- | --- |
| Driver seeds fed to the walker | PROCESSED |
| Walker stepping / ROI construction | PROCESSED |
| `residual_motion.observe_blob_at` INPUTS (`pred_center`, `pred_box`, `roi_override`, `dog_diameter_override`, `acceptance_box`) | PROCESSED |
| `residual_motion.observe_blob_at` RETURN centroid | SOURCE |
| `observe_blob_at` trace `corridor_blobs` centroids (`centroid_x`, `centroid_y`) | PROCESSED (full-frame, ROI origin already added back) |
| `common_tools.in_box_heat.measure_in_box_heat` inputs (`residual_dog`, `validity_mask`, `roi_origin`, `box`) | PROCESSED |
| `SeedsView.source` | SOURCE (original seed dict, unchanged) |
| `SeedsView.seeds` | PROCESSED (lazy, via held `FrameGeometry`) |
| Torso-box npz (`state_io` write/load) | SOURCE |
| `reader.width` / `reader.height` | PROCESSED |
| Encoder trajectory / crop geometry | SOURCE |

### In-box heat reads PROCESSED arrays with one ROI subtraction

`common_tools.in_box_heat.measure_in_box_heat` reads the `residual_dog` and
`validity_mask` ROI arrays in PROCESSED space. The box is a typed
`ProcessedBox`. A single subtraction of `roi_origin` maps the box center into
array-local coordinates before deriving pixel edges, matching the identical
pattern in `walk_draw.processed_box_to_tile_local`. No second subtraction
occurs. Passing a `SourceBox` raises `ValueError` immediately via
`coord_space.require_processed_box`.

### The observe_blob_at asymmetry

`observe_blob_at` is deliberately asymmetric and you must remember this:

- Its INPUTS are PROCESSED. The caller converts to PROCESSED before calling.
- Its RETURN centroid is SOURCE.

This asymmetry is the most error-prone seam in the pipeline. The return
centroid is currently an untyped tuple; M2 / WS2-B makes the return a typed
SOURCE primitive so the seam fails loud instead of silently feeding a SOURCE
centroid back into a PROCESSED stepping loop.

## Enforcement: types are spaces

The enforcement mechanism is typed primitives in
[common_tools/coord_space.py](../common_tools/coord_space.py)
(`SourcePoint`, `ProcessedPoint`, `SourceBox`, `ProcessedBox`). The rule is:

- The TYPE encodes the SPACE. A `ProcessedPoint` is a processed-space point;
  there is no ambiguity to track by convention.
- Converting twice is a method-not-found error: a `ProcessedPoint` has no
  `source_to_processed` method, so a double conversion does not type-check.
- Passing the wrong space into a boundary fails loud via `require_*` guards
  rather than silently producing wrong geometry.

Bounds checking is the explicit `ProcessedPoint.in_bounds(geometry)` predicate
described above, kept separate from conversion so off-frame is a visible
decision, not a hidden clamp.

## The #101 lesson, recorded for posterity

Bug #101 (degenerate ROI, `w=0`, at `bin_factor > 1`) came from two walker
callers feeding the boundary DIFFERENT spaces:

- `make_walk_html_v2.process_video` passed SOURCE-pixel seeds.
- the former `walk_driver.main` (removed 2026-06-02) passed PROCESSED-pixel
  seeds (from `SeedsView.seeds`).

At `bin_factor > 1` the SOURCE seed, treated as PROCESSED, built a degenerate
ROI: a source `cx` near the right edge, clamped against the PROCESSED width,
collapsed to zero width and raised inside `observe_blob_at`. An isolated
processed-seed run looked clean, which masked the defect.

The typed boundary makes this a compile-time / boundary error: the SOURCE seed
is a `SourcePoint`, the boundary requires a `ProcessedPoint`, and the mismatch
fails immediately at the call site instead of deep in the observer.

## Related contract clauses

- [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) clause C2: runner-relative
  thresholds are in torso-box units, not raw pixels. Coordinate-space scale and
  torso-unit scale are orthogonal concerns; both apply.
- [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) clause C13: frame-based
  data is minimal, stored in .npz, integers preferred. The torso-box npz is
  SOURCE per the table above.
- [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md): design philosophy and the
  separation-of-concerns boundaries that cross these spaces.
