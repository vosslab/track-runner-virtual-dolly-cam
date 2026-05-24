"""Display-oriented residual-motion heat map facade for the annotation GUI.

Thin wrapper over the residual-motion compute primitive in
`track_runner.residual_motion`. Produces an ROI-scoped BGR composite
suitable for overlay in a Qt scene. No Qt imports live here; this
module is headless-testable.

Four conceptually separate layers:

  1. Residual calculation (`residual_motion.compute_residual_for_frame`
     delegated, ROI-scoped).
  2. DoG band-pass tuned to the torso width
     (`residual_motion.dog_filter_blob_scale`) so the overlay shows
     the same magnitude landscape the production observer sees.
  3. Threshold mask (`_build_threshold_mask`): binary per-pixel flag
     for "this pixel has enough motion to be interesting", computed
     against the DoG-filtered residual.
  4. Display composition (`_compose_overlay`): builds the final BGR
     image. Below-threshold pixels render as grayscale of the source
     frame (luminance preserved, color removed). Above-threshold
     pixels render as a JET-colorized residual blended with the color
     frame at `blend_alpha`.

The returned image is a full composite. The Qt caller must place it
at opacity 1.0; any overlay-level alpha would double-blend because the
composition already integrates the underlying frame.

ROI geometry matches the solver's own crop window (ROI_MULTIPLIER *
pred_h, via `residual_motion._compute_roi`). JET colorization is
delegated to `residual_motion.colorize_jet`, which is shared with
`tools/diagnose_residual_motion.py` and the blob-funnel diagnostic.

Public surface:
  - compute_heat_map_roi(...): returns (bgr_crop, origin) or None.
"""

# Standard Library
# (none)

# PIP3 modules
import cv2
import numpy

# local repo modules
import residual_motion


#============================================
def _build_threshold_mask(
	residual_mag: numpy.ndarray,
	threshold: float,
) -> numpy.ndarray:
	"""Build a binary uint8 mask of pixels that clear the noise threshold.

	Args:
		residual_mag: Residual magnitude array (float32, HxW).
		threshold: Magnitudes strictly below this count as noise.

	Returns:
		uint8 HxW mask: 1 where magnitude >= threshold, else 0.
	"""
	mask = (residual_mag >= float(threshold)).astype(numpy.uint8)
	return mask


#============================================
def _compose_overlay(
	frame_roi_bgr: numpy.ndarray,
	residual_mag: numpy.ndarray,
	above_mask: numpy.ndarray,
	fixed_max: float,
	blend_alpha: float,
) -> numpy.ndarray:
	"""Composite the display BGR image from frame, residual, and mask.

	Below-threshold pixels (where above_mask == 0) render as grayscale
	of the original frame: luminance preserved, color removed so the
	user reads "nothing interesting here". Above-threshold pixels
	render as a JET-colorized residual blended with the original color
	frame at `blend_alpha`, so the moving content still shows through
	under the heat color.

	Args:
		frame_roi_bgr: BGR uint8 crop of the current frame at the ROI.
		residual_mag: Residual magnitude array (float32, HxW matching
			frame_roi_bgr's HxW).
		above_mask: uint8 HxW mask from _build_threshold_mask.
		fixed_max: Passed through to residual_motion.colorize_jet.
		blend_alpha: Mix weight for JET vs color frame in
			above-threshold pixels. 0 = all frame, 1 = all JET.

	Returns:
		BGR uint8 composite image of shape (H, W, 3), opaque.
	"""
	# grayscale layer for sub-threshold regions: luminance preserved,
	# color removed. cvtColor round-trip BGR->GRAY->BGR replicates the
	# gray channel into all three BGR channels.
	gray = cv2.cvtColor(frame_roi_bgr, cv2.COLOR_BGR2GRAY)
	gray_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

	# JET layer: zero out sub-threshold magnitudes before colorization
	# so any mask leak lands at JET(0) dark blue, not at a warm color.
	mag_above = numpy.where(
		above_mask > 0, residual_mag, 0.0,
	).astype(numpy.float32)
	jet_bgr = residual_motion.colorize_jet(mag_above, float(fixed_max))

	# above-threshold composite: mix JET with the original color frame.
	alpha = float(blend_alpha)
	color_blend = cv2.addWeighted(
		frame_roi_bgr, 1.0 - alpha, jet_bgr, alpha, 0.0,
	)

	# pick per-pixel: above_mask -> color_blend, else gray_bgr.
	mask_3ch = numpy.stack(
		(above_mask, above_mask, above_mask), axis=-1,
	).astype(bool)
	composite = numpy.where(
		mask_3ch, color_blend, gray_bgr,
	).astype(numpy.uint8)
	return composite


#============================================
def compute_heat_map_roi(
	reader: object,
	frame_index: int,
	scene_transform: object,
	pred_center: tuple,
	pred_box: tuple,
	fps: float = float(residual_motion.REFERENCE_FPS),
	threshold: float = 10.0,
	fixed_max: float = 30.0,
	blend_alpha: float = 0.40,
	out_arrays: dict | None = None,
) -> tuple | None:
	"""Compose the motion heat-map overlay for one frame's ROI.

	Orchestrates three stages: residual calculation, threshold mask,
	display composition. Returns the finished BGR composite plus its
	top-left pixel origin so a caller can place it inside a full-frame
	Qt scene at opacity 1.0.

	Args:
		reader: VideoReader-compatible reader with .width, .height,
			.frame_count, and .read_frame(index) -> BGR numpy array.
		frame_index: Index of the frame to compute heat for.
		scene_transform: SceneTransform used by the residual primitive
			to warp neighboring frames into the center frame's camera
			space. Pass the solver's real transform; an identity one
			will let camera pan ghost into the residual.
		pred_center: (cx, cy) predicted torso center in full-frame pixels.
		pred_box: (w, h) predicted torso box size in pixels. Only h is
			used (matches solver's ROI rule).
		fps: Frame rate in frames per second. Used to resolve the neighbor
			stride via residual_motion.resolve_stride. Default is
			REFERENCE_FPS (60). Pass reader.fps for correct stride at
			non-60-fps sources.
		threshold: Magnitude boundary. Pixels strictly below render as
			grayscale of the source frame; pixels at or above render
			as JET over the color frame.
		fixed_max: Magnitude mapped to JET hot-red. Default 30.0
			matches the diagnostic tool.
		blend_alpha: Mix weight for JET vs color frame in
			above-threshold pixels. This is NOT an overlay-level
			opacity: the final pixmap is opaque. 0.40 default keeps
			the frame visible under the heat tint.
		out_arrays: Optional dict to receive intermediate arrays for
			testing. When non-None, populates with keys:
			  "residual_dog": DoG-filtered residual (post-validity).
			  "validity_mask": Validity mask (uint8).
			  "roi_bounds": ROI tuple (x1, y1, x2, y2).
			  "dog_k": DoG k-factor used.
			  "threshold": Threshold value used.
			When None, behavior is unchanged (byte-identical).

	Returns:
		Tuple (bgr: numpy.ndarray shape (roi_h, roi_w, 3) uint8,
		origin: (x1, y1)) when the residual is available, else None.
		Origin is the top-left corner of the ROI in full-frame pixels.
	"""
	# require a reader capable of reporting frame geometry
	if reader is None:
		return None
	# require a valid prediction
	if pred_center is None or pred_box is None:
		return None

	pred_cx, pred_cy = float(pred_center[0]), float(pred_center[1])
	pred_w = float(pred_box[0])
	pred_h = float(pred_box[1])

	# stage 0: build the ROI using the solver's exact crop rule
	roi = residual_motion._compute_roi(
		pred_cx, pred_cy, pred_h, int(reader.width), int(reader.height),
	)
	x1, y1, x2, y2 = int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3])

	# stage 1a: read the center frame ROI for compositing. The residual
	# primitive reads its own copies internally; we pay one extra I/O
	# here to keep the facade self-contained.
	center_bgr = reader.read_frame(frame_index)
	if center_bgr is None:
		return None
	frame_roi = center_bgr[y1:y2, x1:x2].copy()

	# stage 1b: residual calculation. stride derived from fps via M2 model.
	stride = residual_motion.resolve_stride(fps)
	residual_result = residual_motion.compute_residual_for_frame(
		reader, frame_index, scene_transform,
		fps=fps, stride=stride, cache=None, roi=roi,
	)
	residual_mag, validity_mask = residual_result
	if residual_mag is None:
		return None

	# stage 1c: DoG band-pass tuned to the torso width so the overlay
	# shows the same magnitude landscape the production observer sees.
	# Sub-torso speckle is suppressed; torso-scale blobs are enhanced.
	dog_k = residual_motion.DOG_K_FACTOR_DEFAULT
	residual_mag = residual_motion.dog_filter_blob_scale(
		residual_mag, pred_w,
		k=dog_k,
	)
	if validity_mask is not None:
		residual_mag[validity_mask == 0] = 0.0

	# Capture intermediate arrays for testing if requested (side-channel,
	# trace-sink pattern; byte-identical when out_arrays is None).
	if out_arrays is not None:
		out_arrays["residual_dog"] = residual_mag.copy()
		out_arrays["validity_mask"] = validity_mask.copy() if validity_mask is not None else None
		out_arrays["roi_bounds"] = (x1, y1, x2, y2)
		out_arrays["dog_k"] = dog_k
		out_arrays["threshold"] = threshold

	# stage 2: threshold mask.
	above_mask = _build_threshold_mask(residual_mag, threshold)

	# stage 3: display composition.
	composite = _compose_overlay(
		frame_roi_bgr=frame_roi,
		residual_mag=residual_mag,
		above_mask=above_mask,
		fixed_max=float(fixed_max),
		blend_alpha=float(blend_alpha),
	)

	origin = (x1, y1)
	result = (composite, origin)
	return result
