# Motion-cue heat map

This document is subordinate to
[TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md). The contract wins on
any conflict.

The motion-cue heat map is the image-derived evidence input to the windowed
blob walker. It identifies moving image components after camera compensation;
it does not identify a runner, define a track direction, or change a human
seed.

Primary implementation: [residual_motion.py](../track_runner/residual_motion.py).
Image mechanics live in [residual_frame.py](../track_runner/residual_frame.py).

## Field definition

`compute_residual_for_frame()` returns a float32 residual image and a uint8
validity mask for one full frame or one ROI. For a target frame, the reader
loads neighboring frames, camera-aligns them into the target coordinate space,
and computes a NaN-masked median background. The residual is the absolute
difference between the target and that background. Pixels with insufficient
warped support are invalid and have zero residual.

The default neighbor count is fixed and the stride is derived from FPS. This
keeps the temporal span similar across common video frame rates without
turning the heat map into a timing or performance gate.

The field is motion evidence only:

- It is not appearance or identity evidence.
- It is not a runner probability.
- It does not carry an inferred track direction.
- It does not contain a selected path or any cross-frame state.

## How the map is calculated

For target frame `t`, `compute_residual_for_frame()` performs these steps:

1. Read the target frame in grayscale float32.
2. Choose neighboring frames on both sides of `t`; the center frame itself is
   not a neighbor.
3. Use `SceneTransform` to build an affine warp from each neighbor into the
   target camera position. For an ROI, the translation is shifted into ROI
   coordinates before warping.
4. Warp each neighbor, derive its valid support mask, and represent invalid
   pixels as `NaN`.
5. Stack the aligned neighbor images and calculate the per-pixel
   `numpy.nanmedian`. That is the camera-compensated background estimate.
6. Take `abs(target - median)` and zero pixels with fewer than two valid
   neighbor contributions.

The result is a magnitude image, not a binary mask or a rendered overlay. The
UI colorizes it only after this calculation.

## FPS-aware stride

The code keeps a fixed number of neighbors and derives the frame stride from
the source FPS:

```
stride = max(1, round(fps / REFERENCE_FPS))
```

At 60 fps the neighboring offsets are contiguous; at higher frame rates the
offset grows so the temporal span remains similar while the number of decoded
frames stays bounded. The current constant values live in code rather than in
this document, preventing the documentation from becoming a second tuning
surface.

## DoG preparation

Before connected-component extraction, the observer applies a
Difference-of-Gaussians filter sized from the predicted torso width. The DoG
suppresses broad background changes and fine speckle while retaining a
torso-scale motion component. It is applied both to the production observer
and the GUI overlay so they describe the same residual landscape.

## ROI and cache boundary

`observe_blob_at()` receives a typed PROCESSED-space predicted center and
box. It creates a quantized, frame-clamped ROI from the prediction and uses
that ROI with the frame index as the image-evidence cache key. Tiny FWD/BWD
prediction differences can therefore reuse raw image work, while materially
different ROIs compute their own evidence.

The interval-scoped cache may hold only raw image products:

- `(frame_index, roi)` residual, validity, and raw blob data
- `"_frames"` grayscale and BGR frame reads

It never holds selected blobs, path positions, gate results, or temporal
history. Reusing image evidence does not couple FWD and BWD decisions.

## Blob extraction

The observer applies a Difference-of-Gaussians filter to the residual before
connected-component extraction. `extract_frame_blobs()` returns at most the
strongest image components by integrated magnitude. It preserves small
components as evidence; `small_blob` is descriptive metadata rather than a
hard discard.

Each raw blob contains these image-derived fields:

| Field | Meaning |
| --- | --- |
| `centroid_x`, `centroid_y` | Component centroid in ROI pixels before restoration to full-frame pixels. |
| `area`, `bbox`, `label_id` | Connected-component geometry. |
| `integrated_mag` | Sum of residual magnitude over the component. |
| `small_blob` | Whether the component is below the named noise-size threshold. |

The observer restores centroids to full-frame coordinates exactly once when it
returns the selected `BlobObservation` as a typed SOURCE-space point.

## Observation contract

`observe_blob_at()` is stateless. It accepts the current frame, a predicted
center and box, the scene transform, a reader, and the image-evidence cache.
It can also accept a seed-local ROI, DoG diameter, and acceptance box for the
walker bootstrap path.

The production sequence is:

1. Build or reuse the ROI residual and validity mask.
2. Apply the DoG filter and extract raw blobs.
3. Apply an optional acceptance box.
4. Select the eligible blob with the greatest `integrated_mag`.
5. Return its raw centroid and descriptive confidence metadata.

There is no corridor filter, tangent projection, directional decomposition,
or trajectory correction in this API. `compute_cue_confidence()` records
strength and isotropic proximity metadata; it does not reject a blob or choose
the winner.

FWD and BWD call the observer independently with their own predictions.
Stage-3 analytical propagation never calls it. The Stage-4 walker decides how to use the
optional observation after this image-only boundary.

## Coordinate spaces

All geometry supplied to `observe_blob_at()` is typed PROCESSED space. Its
returned `BlobObservation.center_pixel` is typed SOURCE space. Callers must
make the conversion explicit; the observer rejects an incorrect input type at
its boundary.

## Failure behavior

The observer returns `None` when it cannot produce an image observation:

- camera-aligned neighbors are unavailable;
- no blob survives the image threshold;
- no blob lies inside an optional acceptance box; or
- the predicted center is outside the processed frame.

The optional trace labels these outcomes as `no_residual`, `no_raw_blobs`,
`acceptance_box_empty`, or `off_frame`. A `None` observation is a soft image
miss; it does not alter a seed or assert that the runner is absent.

## UI overlay

The annotation UI calls the same residual and DoG pipeline through
`residual_heat_map.py`. Its colorized image is display-only. It does not feed
back into tracking state or change the cached raw evidence.
