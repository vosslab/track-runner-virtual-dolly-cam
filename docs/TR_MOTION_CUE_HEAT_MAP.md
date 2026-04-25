# Motion-cue heat map

This doc is subordinate to
[TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md). On conflict, the
contract wins and this document is corrected.

Technical source of truth for the image-derived motion-cue field and
the blob pipeline that turns it into candidate observations for the
FWD/BWD propagator.

Owns: the cue field definition, its computation, ROI geometry and
quantization, blob extraction and fields, corridor filter,
cue-confidence scoring, the concrete cache schema, the
`observe_blob_at` call flow, and measurement-level failure modes.

Does not own: dual-pass invariants, pass-local gating rules, allowed
signal flow, or the normative statement of which cache contents are
forbidden. Those live in
[FWD_BWD_MODEL_METHODOLOGY.md](FWD_BWD_MODEL_METHODOLOGY.md).

Related docs:

- [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) -- rules.
- [FWD_BWD_MODEL_METHODOLOGY.md](FWD_BWD_MODEL_METHODOLOGY.md) --
  dual-pass invariants, raw-cache boundary, allowed signal flow.
- [RESIDUAL_MOTION_OBSERVATIONS.md](RESIDUAL_MOTION_OBSERVATIONS.md)
  -- consumer-facing one-page summary of the observation API.
- [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) -- why motion cues
  matter.

Primary code:
[track_runner/residual_motion.py](../track_runner/residual_motion.py).
Display facade for the annotation UI:
[track_runner/residual_heat_map.py](../track_runner/residual_heat_map.py).
This doc refers to named constants (for example `ROI_MULTIPLIER`,
`ROI_QUANT`, `MIN_BLOB_AREA`, `DEFAULT_THRESHOLD`,
`DEFAULT_HALF_WINDOW`, `TANGENT_MIN_SPAN`) without repeating their
numeric values. Consult the code for current numbers; they are
expected to evolve.

## What the heat map is

A per-frame float32 image of motion-cue magnitude, with a matching
uint8 validity mask. Returned by
`compute_residual_for_frame(reader, frame_index, scene_transform,
half_window, cache, roi)` as a `(residual, validity_mask)` tuple.

The field has one magnitude value per ROI pixel. High magnitude means
that pixel moved relative to the scene-stabilized background; low
magnitude means it was well-explained by the median of aligned
neighbors. The validity mask is zero wherever the warped neighbor
coverage was insufficient (fewer than two contributing frames) and
255 elsewhere; residual magnitudes at invalid pixels are forced to
zero before return.

This is a literal floating-point image, not a thresholded binary map
and not a visualization. The JET colorization in
`residual_heat_map.py` is a display-only composite built on top of
the same field for the annotation UI.

## How the field is computed

For one frame at `frame_index`:

1. Read the target frame in grayscale float32 (`_read_gray_frame`,
   with an intra-call cache of prior reads).
2. Optionally crop the target to a square ROI centered on the
   caller's predicted position (see "ROI geometry" below).
3. For each neighbor `frame_index + k` with `k` in
   `[-half_window, +half_window] \ {0}` (the default is
   `DEFAULT_HALF_WINDOW = 4`, i.e. a 9-frame window spanning
   `[frame_index - 4, frame_index + 4]`):
   - Read the neighbor frame (BGR, cached separately).
   - Build a 2x3 affine warp matrix that transforms the neighbor
     into the target frame's camera position. The warp uses the
     cumulative `(cum_dx, cum_dy, cum_scale)` arrays in
     `SceneTransform` (`build_warp_matrix`). For ROI-scoped calls the
     warp translation is shifted by the ROI origin so the warp lands
     in ROI pixel coordinates directly.
   - Apply `cv2.warpAffine` to obtain the aligned neighbor crop.
   - Build a per-pair validity mask (`compute_validity_mask`): a
     gray-thresholded binary mask eroded by a 3x3 kernel. Pixels
     outside the neighbor's warped support are marked invalid.
   - Convert the warped neighbor to grayscale float32 and set
     invalid pixels to `NaN` so the median ignores them.
4. Stack the aligned neighbors and compute `numpy.nanmedian` along
   the stack axis. That median image is the scene-stabilized
   background estimate.
5. Build the combined validity mask: pixels where at least two
   neighbors contributed a non-NaN value are valid.
6. Compute `residual = abs(target - median)`. Zero-out invalid
   pixels. Return `(residual, validity_mask)`.

The returned residual is the input to a downstream DoG band-pass step
inside both `observe_blob_at` (production observer) and
`compute_heat_map_roi` (GUI overlay). See [the DoG band-pass pre-filter
subsection below](#dog-band-pass-pre-filter).

If fewer than two aligned neighbors could be collected, the function
returns `(None, None)`.

There is no per-frame smoothing, normalization, or contrast
stretching on the stored field. The only preprocessing is the warp,
the median, and the absolute difference. Visualization-time scaling
happens in `residual_heat_map.py` and does not mutate the cached
field.

## ROI geometry and ROI quantization

ROIs are square, centered on a quantized version of the caller's
predicted center, with side length
`ROI_MULTIPLIER * pred_h` (floored to a minimum size). The ROI bounds
are clamped to frame bounds and snapped to multiples of `ROI_QUANT`.
`_compute_roi` returns a 4-tuple `(x1, y1, x2, y2)` used as part of
the cache key.

Quantization design: sub-quantum differences between FWD's and BWD's
predicted centers (a fraction of a pixel, a few pixels at most on
straight motion) resolve to the same ROI tuple, so the two passes
share one residual computation and one raw-blob list. Larger
divergence -- tens of pixels, typical on tight curves, crowd edges,
or occlusion boundaries -- produces distinct ROI tuples and the two
passes each compute their own residual. This keeps the two passes
independent in exactly the regimes where divergence matters, at a
bounded cache-miss cost.

## DoG band-pass pre-filter

Before connected-component extraction, the residual magnitude image is
band-pass filtered by `dog_filter_blob_scale(mag, diameter, k=3.0)`
(in [track_runner/residual_motion.py](../track_runner/residual_motion.py)).
This step is applied unconditionally inside both `observe_blob_at`
(production observer) and `compute_heat_map_roi` (GUI overlay) so the
solver and the user-visible overlay see the same magnitude landscape.

The target blob diameter is the predicted torso width. Sigma sizing
follows the Laplacian-of-Gaussian peak-radius relation
(Yoshioka et al. 2009, DoG Picker, eq. 4):

- `r = diameter / 2`
- `sigma_1 = r / sqrt(2)`
- `sigma_2 = k * sigma_1`
- `dog = blur(mag, sigma_1) - blur(mag, sigma_2)`
- negative-lobe response is clipped to zero

Effect: blobs whose size matches the runner torso are enhanced; both
sub-torso speckle (mis-registered background pixels) and larger
structures (ground texture, crowd silhouettes) are suppressed. The
threshold downstream is then `threshold` per `extract_frame_blobs`,
applied to the DoG-filtered map.

The k-factor default of 3.0 was chosen empirically on this project's
residuals (k=1.1 is too tight; k=5 is also strong; the diagnose tool's
`-k` flag exposes the knob for sweeps but production does not).

## Blob extraction from the heat map

`extract_frame_blobs(mag, validity_mask, threshold, top_k=10)`:

1. Threshold the magnitude image with `mag > threshold` and AND
   with the validity mask to exclude invalid pixels.
2. Run `cv2.connectedComponentsWithStats` with 8-connectivity.
3. For each non-background component, read the area from the stats
   array; drop components with `area < MIN_BLOB_AREA`.
4. Compute `integrated_mag = sum(mag[component_pixels])` over the
   component's pixels.
5. Sort components by `integrated_mag` descending.
6. Return the top `top_k`.

Each blob is a dict with the following fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `centroid_x` | float | Component centroid x (ROI pixels on return; restored to full-frame pixels by the caller in `observe_blob_at`) |
| `centroid_y` | float | Component centroid y, same convention |
| `area` | int | Component pixel count |
| `integrated_mag` | float | Sum of magnitude over component pixels |
| `label_id` | int | Connected-component label id |

Two fields are added later by the corridor filter (see below):
`cross_track` and `along_track`.

Centroids live in ROI coordinates on return from `extract_frame_blobs`;
`observe_blob_at` adds `(roi_x1, roi_y1)` to restore full-frame pixel
coordinates before any downstream gate sees them.

## Corridor filter

`filter_blobs_to_corridor(blobs, ref_x, ref_y, tangent,
corridor_radius)`:

Given a caller-supplied reference point `(ref_x, ref_y)`, a unit
tangent/normal tuple `(tx, ty, nx, ny)`, and a corridor half-width,
keep blobs whose cross-track distance from the reference is within
the corridor. Cross-track and along-track are the blob displacement
`(dx, dy)` projected onto the normal and tangent respectively. Each
surviving blob gets `cross_track` and `along_track` added to its
dict.

Reference point and tangent come from the caller (the propagator's
pass-local state). The corridor filter itself stores nothing.

Tangent construction: `compute_trajectory_tangent(trajectory,
frame_index)` first tries a symmetric `+/- TANGENT_MIN_SPAN` window;
if either endpoint's confidence is below
`TANGENT_CONFIDENCE_THRESHOLD` it widens to
`+/- TANGENT_FALLBACK_SPAN`. On a zero-magnitude chord or a missing
endpoint it returns the axis-aligned identity `(1, 0, 0, 1)`, which
effectively disables anisotropic decomposition for that frame.

## Cue-confidence scoring

`compute_cue_confidence(blob, pred_cx, pred_cy, pred_w, pred_h,
tangent)` returns a scalar in `[0, 1]` blending three factors. The
current weights (0.3 strength, 0.3 size, 0.4 proximity) live in
the function body; consult code for current values:

- **Strength**: `integrated_mag` clamped against a normalization
  constant.
- **Size plausibility**: blob area relative to predicted box area,
  scored by distance from an ideal ratio (the runner is normally a
  part of the corridor, not the whole ROI).
- **Proximity**: isotropic distance between blob centroid and
  predicted center, normalized by the predicted-box diagonal.

The `tangent` argument is accepted for API symmetry but currently
unused in the scoring body. The scoring is post-corridor; the
corridor has already constrained cross-track geometry.

## Per-frame observation: `observe_blob_at`

The single entry point used by the propagator.
`observe_blob_at(frame_index, pred_center, pred_box, local_tangent,
scene_transform, reader, residual_cache, threshold=...,
half_window=...)` returns a `BlobObservation` or `None`.

`BlobObservation` fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `center_pixel` | `(float, float)` | Best blob centroid in full-frame pixels |
| `cross_track` | float | Signed normal component of displacement from predicted center |
| `along_track` | float | Signed tangent component of displacement from predicted center |
| `confidence` | float | Cue confidence in `[0, 1]` |

Call flow inside `observe_blob_at`:

1. Compute ROI via `_compute_roi` using the caller's predicted
   center and height. Form `cache_key = (frame_index, roi)`.
2. Look up `residual_cache[cache_key]`. On hit, reuse the stored
   raw-blob list. On miss, call `compute_residual_for_frame` on
   that ROI, call `extract_frame_blobs`, translate centroids back
   to full-frame coordinates, and store `{"raw_blobs": [...]}` in
   the cache. A negative result (residual unavailable) stores an
   empty `raw_blobs` list so the next caller does not retry.
3. Filter the raw blobs to the corridor around the caller's
   predicted center using the caller's tangent and a
   corridor radius derived from `pred_box` (currently
   `max(1.5 * w, 0.75 * h)`; see code for the live formula).
4. Score surviving blobs via `compute_cue_confidence`; pick the
   highest-scoring.
5. Return the `BlobObservation` for the winner, or `None` if any
   stage produced no candidate.

This function is stateless. All state (the residual cache, the
predicted center, the tangent, the box) is supplied by the caller;
no module-level variable holds progress across frames.

## Cache schema

`residual_cache` is a mutable dict supplied by the caller, scoped to
one interval. Two subkey patterns are legal:

- `(frame_index, roi)` -> `{"raw_blobs": [...]}`: raw image-derived
  output. The blob dicts here are the pre-corridor, pre-gate top-K.
- `"_frames"` -> `{frame_index -> grayscale_float32, ("bgr",
  frame_index) -> bgr_ndarray}`: per-frame byte-level read cache,
  keyed by frame index alone (reads are ROI-independent).

The normative rules for what the cache MUST NOT hold (accepted
blobs, gate outcomes, `snap_pred` values, chained counters, etc.)
live in
[FWD_BWD_MODEL_METHODOLOGY.md](FWD_BWD_MODEL_METHODOLOGY.md) under
the raw-cache boundary. This doc owns the concrete schema above.

## Consumption

Both FWD and BWD call `observe_blob_at` independently per
non-endpoint frame, each using its own `pred_center`, `pred_box`,
and `local_tangent`. Everything downstream of the return value --
the three local gates, accept/reject/absent resolution,
`snap_pred[t]` blending, seed-endpoint skipping -- is pass-local
state owned by
[FWD_BWD_MODEL_METHODOLOGY.md](FWD_BWD_MODEL_METHODOLOGY.md).

## What the heat map is not

- Not an appearance cue. Residual magnitude is a geometric property
  of the scene-stabilized image, not a color / jersey / template
  signal. Appearance cues are banned per C6.
- Not a detection probability. Magnitudes are not normalized to a
  probability distribution and do not encode "runner vs not".
- Not identity evidence. A high-confidence blob in the corridor
  says "something moved here relative to the scene", not "this is
  the tracked runner". The gates and the corridor constrain the
  answer; the heat map on its own does not.
- Not pre-computed for the whole frame. The field is always
  ROI-scoped. Attempting to run the propagator against a full-frame
  heat map is outside the current design.
- Not YOLO-derived. Person detection plays no role in the heat map
  or in blob extraction.

## Failure modes

- **No neighbors available**: near sequence boundaries (first /
  last `half_window` frames) the aligned stack may collapse to one
  or zero frames. `compute_residual_for_frame` returns
  `(None, None)` and `observe_blob_at` returns `None`.
- **All blobs below threshold or below `MIN_BLOB_AREA`**:
  `extract_frame_blobs` returns an empty list. `observe_blob_at`
  returns `None` and the propagator falls through to raw Hermite.
- **Corridor empty**: blobs exist but none within the cross-track
  corridor. `observe_blob_at` returns `None`.
- **Degenerate tangent**: `compute_trajectory_tangent` returns the
  axis-aligned identity when the chord magnitude is too small or
  endpoints are missing. The corridor still works, but anisotropic
  decomposition is disabled for that frame.
- **Heavy camera motion + partial warp coverage**: validity_mask
  shrinks near ROI edges; blobs in low-validity regions are
  suppressed. Interval-level `blob_coverage_fraction` in the
  diagnostics carries `no_candidate_blobs: true` when the whole
  interval had zero candidates.

## Why the default window is 9 frames (half_window = 4)

The background estimator is a `nanmedian` over aligned neighbors. Median
robustness needs enough valid samples per pixel to ignore a single
moving object passing through, and in practice that means a 9-frame
window (`+/- 4`): the runner occupies a small fraction of those eight
neighbors at any given pixel, so the median collapses to the stationary
background almost everywhere. A 5-frame window (`+/- 2`) leaves the
median with only four neighbors, which is too few for the median to
reject the runner from its own path: the runner's pixels enter the
background estimate and the residual under-reports real motion.

The 9-frame window has always been the value used by
`tools/diagnose_residual_motion.py`, which is the most-exercised
reference implementation in this repo. The production path briefly
shipped with `half_window = 2` (5-frame window) but that was an
oversight, not a deliberate choice; the production `DEFAULT_HALF_WINDOW`
was raised to `4` to match diagnose. The 9-frame window should be
treated as the canonical default for all callers.

Trade-offs at the default:

- Compute cost scales linearly with window size. A 9-frame window
  performs four frame reads + warps per target frame (plus the target
  itself), versus two for a 5-frame window.
- Near sequence boundaries the available stack shrinks; the fallback
  condition `len(aligned_stack) < 2` in the library returns
  `(None, None)` and the propagator falls through to pure Hermite.
  This happens on up to four frames on each end instead of two; the
  behavioral impact is negligible because those regions are already
  close to seeds.
- Very fast cross-frame camera motion widens the per-pair warp residual
  near ROI edges (more "invalid" pixels), but the median already masks
  NaNs and the wider window improves the odds that at least two
  neighbors contribute a valid value at each pixel.

If you see evidence that the default is the wrong regime for a
particular scene, prefer re-running `tools/diagnose_residual_motion.py`
with an explicit `--half-window` override before editing
`DEFAULT_HALF_WINDOW`. Changing the constant affects every production
caller and invalidates geometry caches.

## Version tag

Per contract C9 there is exactly one schema-version authority:
`tr_schema.SCHEMA_VERSION` in
[track_runner/tr_schema.py](../track_runner/tr_schema.py). Cache
invalidation for observer-behavior changes (new gate, changed corridor
geometry, changed scoring terms, the DoG band-pass step) flows through
two coordinated structures inside `tr_schema`:

- `SCHEMA_VERSION` -- bumped whenever any artifact written to disk
  records a new schema (geometry-affecting or metadata-only).
- `GEOMETRY_AFFECTING_SCHEMAS` -- the set of schema versions that
  altered solved-geometry semantics. The interval-solver fingerprint
  (`interval_fingerprint.build_geometry_tag`) embeds the highest
  member of this set <= `SCHEMA_VERSION` as `geometry_schema_v<N>`.
  Metadata-only bumps slide through unchanged; geometry-affecting
  bumps invalidate cached entries naturally.
- `SUPPORTED_ARTIFACT_SCHEMAS` -- per-artifact compatibility windows
  used by loaders so older readable artifacts are not rejected on a
  schema bump.

There is intentionally no separate `BLOB_OBSERVER_VERSION` constant or
similar parallel authority. To bump observer behavior in a way that
invalidates geometry caches, increment `SCHEMA_VERSION` and add the
new version to `GEOMETRY_AFFECTING_SCHEMAS`. The legacy `blob_snap/v1`
fingerprint format produced before this unification is migrated to
`geometry_schema_v3` at load time by
`interval_fingerprint.migrate_legacy_fingerprints` (see
[docs/TR_SCHEMA_VERSION_HISTORY.md](TR_SCHEMA_VERSION_HISTORY.md)).
