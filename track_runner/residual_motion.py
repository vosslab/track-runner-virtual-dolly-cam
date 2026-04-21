"""Per-frame blob observation from residual (camera-compensated) motion.

Provides a stateless API for extracting a single candidate blob observation
at one frame, given a predicted position and local tangent. The returned
observation is a raw measurement; all gating decisions (proximity, direction,
temporal smoothness) live in the caller (the propagator).

No cross-frame state lives in this module. The chained/global motion-cue
fusion that previously existed here was removed when the propagator took
over per-frame correction directly.

Public surface:
  - observe_blob_at(...): primary API; returns BlobObservation or None.
  - BlobObservation: dataclass carrying a single per-frame measurement.
  - BLOB_OBSERVER_VERSION: semantic version tag for fingerprint bumping.
  - compute_residual_for_frame(...): low-level residual + validity mask.
  - extract_frame_blobs(...): connected-component extraction from a residual.
  - filter_blobs_to_corridor(...): cross-track corridor filter.
  - compute_trajectory_tangent(...): local tangent from a solved trajectory.
  - compute_cue_confidence(...): scalar cue score per blob.
"""

# Standard Library
import warnings
import dataclasses

# PIP3 modules
import cv2
import numpy

# === Tunable constants ===

# Observer version tag. Bumped on every semantic change to the stateless
# per-frame observer API (new gate, reordered scoring, changed corridor
# geometry). Included in the interval-solver fingerprint so refine cache
# invalidates correctly when observer semantics change, even if numeric
# constants are unchanged.
BLOB_OBSERVER_VERSION = "v1"

# minimum blob area in pixels to suppress noise specks
MIN_BLOB_AREA = 25

# motion intensity threshold for blob extraction
DEFAULT_THRESHOLD = 10.0

# half-window for background estimation (2 = 5-frame window)
DEFAULT_HALF_WINDOW = 2

# ROI multiplier: crop region is this many times the torso box height
ROI_MULTIPLIER = 8.0

# ROI quantization step in pixels. The ROI bounds are snapped to a
# multiple of this value so that tiny FWD/BWD pred_center differences
# (a fraction of a pixel, a few pixels at most) resolve to the SAME ROI
# tuple and share a cache entry. Only meaningful divergence -- tens of
# pixels, which happens on tight curves and crowd scenes -- produces a
# distinct cache key and triggers a separate residual computation.
# 8 px is well below the typical ROI side length (240+ px at 60 px box)
# so quantization does not meaningfully change which pixels are covered.
ROI_QUANT = 8

# tangent estimation: minimum half-window
TANGENT_MIN_SPAN = 5

# tangent estimation: fallback half-window for low-confidence regions
TANGENT_FALLBACK_SPAN = 10

# tangent estimation: minimum confidence for primary window
TANGENT_CONFIDENCE_THRESHOLD = 0.5


#============================================
def build_warp_matrix(
	scene_transform: object,
	frame_n: int,
	frame_n1: int,
	scale_factor: float,
) -> numpy.ndarray:
	"""Build 2x3 affine matrix to warp frame N+1 into frame N's camera position.

	The SceneTransform stores cumulative motion. To warp N+1 into N's space,
	we need the delta transform: how did the camera move from N to N+1.

	Args:
		scene_transform: SceneTransform instance with cum_dx, cum_dy, cum_scale.
		frame_n: Source reference frame index.
		frame_n1: Frame to warp into frame_n's space.
		scale_factor: Downsample factor applied to frames.

	Returns:
		2x3 numpy float32 affine matrix for cv2.warpAffine.
	"""
	# cumulative values at each frame
	cum_dx_n = float(scene_transform.cum_dx[frame_n])
	cum_dy_n = float(scene_transform.cum_dy[frame_n])
	cum_scale_n = float(scene_transform.cum_scale[frame_n])

	cum_dx_n1 = float(scene_transform.cum_dx[frame_n1])
	cum_dy_n1 = float(scene_transform.cum_dy[frame_n1])
	cum_scale_n1 = float(scene_transform.cum_scale[frame_n1])

	# relative scale: how much to scale frame N+1 to match frame N
	rel_scale = cum_scale_n / cum_scale_n1

	# translation delta in frame N pixel space
	tx = (cum_dx_n - cum_dx_n1 * rel_scale) * scale_factor
	ty = (cum_dy_n - cum_dy_n1 * rel_scale) * scale_factor

	# build 2x3 affine matrix
	warp_matrix = numpy.array([
		[rel_scale, 0.0, tx],
		[0.0, rel_scale, ty],
	], dtype=numpy.float32)
	return warp_matrix


#============================================
def compute_validity_mask(
	warped: numpy.ndarray,
) -> numpy.ndarray:
	"""Create a mask of valid (non-black) pixels after warping.

	Pixels that land outside the source frame after warpAffine are black.
	These must be excluded from residual computation.

	Args:
		warped: Warped BGR frame.

	Returns:
		Binary mask (uint8, 255=valid, 0=invalid).
	"""
	gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
	_, mask = cv2.threshold(gray, 1, 255, cv2.THRESH_BINARY)
	kernel = numpy.ones((3, 3), numpy.uint8)
	mask = cv2.erode(mask, kernel, iterations=1)
	return mask


#============================================
def extract_frame_blobs(
	mag: numpy.ndarray,
	validity_mask: numpy.ndarray,
	threshold: float,
	top_k: int = 10,
) -> list:
	"""Extract top-K motion blobs from a residual magnitude image.

	Args:
		mag: Residual magnitude array.
		validity_mask: Binary validity mask (255=valid).
		threshold: Motion threshold.
		top_k: Maximum number of blobs to return.

	Returns:
		List of dicts with keys: centroid_x, centroid_y, area,
		integrated_mag, label_id.
	"""
	# threshold and mask
	thresh_mask = (mag > threshold).astype(numpy.uint8)
	thresh_mask = thresh_mask & (validity_mask > 0).astype(numpy.uint8)
	num_labels, labels, label_stats, centroids = cv2.connectedComponentsWithStats(
		thresh_mask, connectivity=8
	)

	blobs = []
	for label_id in range(1, num_labels):
		area = int(label_stats[label_id, cv2.CC_STAT_AREA])
		# skip tiny noise specks
		if area < MIN_BLOB_AREA:
			continue
		component_pixels = labels == label_id
		integrated = float(numpy.sum(mag[component_pixels]))
		cx = float(centroids[label_id][0])
		cy = float(centroids[label_id][1])
		blobs.append({
			"centroid_x": cx,
			"centroid_y": cy,
			"area": area,
			"integrated_mag": integrated,
			"label_id": label_id,
		})

	# sort by integrated magnitude descending, keep top K
	blobs.sort(key=lambda b: b["integrated_mag"], reverse=True)
	result = blobs[:top_k]
	return result


#============================================
def filter_blobs_to_corridor(
	blobs: list,
	ref_x: float,
	ref_y: float,
	tangent: tuple,
	corridor_radius: float,
) -> list:
	"""Filter blobs to those within a corridor around a reference point.

	The corridor is defined by a center point, a tangent direction, and
	a half-width. Blobs are kept if their cross-track distance from the
	reference is within the corridor radius.

	Args:
		blobs: List of blob dicts from extract_frame_blobs.
		ref_x: Corridor center x.
		ref_y: Corridor center y.
		tangent: Tuple of (tx, ty, nx, ny) from compute_trajectory_tangent.
		corridor_radius: Half-width of the corridor.

	Returns:
		Filtered list of blob dicts (with cross_track and along_track added).
	"""
	tx, ty, nx, ny = tangent
	result = []
	for blob in blobs:
		dx = blob["centroid_x"] - ref_x
		dy = blob["centroid_y"] - ref_y
		# decompose into along-track and cross-track
		along = dx * tx + dy * ty
		cross = dx * nx + dy * ny
		if abs(cross) <= corridor_radius:
			blob_copy = dict(blob)
			blob_copy["cross_track"] = cross
			blob_copy["along_track"] = along
			result.append(blob_copy)
	return result


#============================================
def _read_gray_frame(
	reader: object,
	frame_index: int,
	cache: dict,
) -> numpy.ndarray:
	"""Read a frame as grayscale float32, using cache when available.

	Args:
		reader: VideoReader instance.
		frame_index: Frame to read.
		cache: Dict mapping frame_index -> grayscale float32 array.

	Returns:
		Grayscale float32 array, or None if read fails.
	"""
	if frame_index in cache:
		return cache[frame_index]
	frame_bgr = reader.read_frame(frame_index)
	if frame_bgr is None:
		return None
	gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
	gray_float = gray.astype(numpy.float32)
	cache[frame_index] = gray_float
	return gray_float


#============================================
def _compute_roi(
	pred_cx: float,
	pred_cy: float,
	pred_h: float,
	frame_w: int,
	frame_h: int,
) -> tuple:
	"""Compute ROI bounds for residual computation around predicted position.

	Uses ROI_MULTIPLIER * pred_h as the square crop side length.
	Clamps to frame boundaries. Bounds are snapped to multiples of
	ROI_QUANT so that sub-quantum jitter in pred_cx / pred_cy (e.g.
	the few-pixel FWD vs BWD disagreement common on symmetric motion)
	produces IDENTICAL ROI tuples and therefore shares the cache
	entry. Larger divergence (tight curves, crowds) yields distinct
	ROIs and correctly triggers a per-pass residual computation.

	Args:
		pred_cx: Predicted center x in pixels.
		pred_cy: Predicted center y in pixels.
		pred_h: Predicted box height in pixels.
		frame_w: Full frame width.
		frame_h: Full frame height.

	Returns:
		Tuple of (x1, y1, x2, y2) pixel bounds, quantized to
		ROI_QUANT and clamped to frame.
	"""
	half_side = int(ROI_MULTIPLIER * pred_h / 2)
	# minimum ROI size of 100px
	half_side = max(half_side, 50)

	# quantize the center so small jitter collapses to the same bucket
	quant_cx = int(round(pred_cx / ROI_QUANT)) * ROI_QUANT
	quant_cy = int(round(pred_cy / ROI_QUANT)) * ROI_QUANT

	# clamp BOTH bounds to frame extents; without an upper clamp on x1/y1,
	# an off-frame prediction (pred_cx > frame_w) produced x1 > x2 and a
	# zero-width slice that downstream code mis-handled.
	x1 = max(0, min(frame_w, quant_cx - half_side))
	y1 = max(0, min(frame_h, quant_cy - half_side))
	x2 = max(x1, min(frame_w, quant_cx + half_side))
	y2 = max(y1, min(frame_h, quant_cy + half_side))
	return (x1, y1, x2, y2)


#============================================
def compute_residual_for_frame(
	reader: object,
	frame_index: int,
	scene_transform: object,
	half_window: int = DEFAULT_HALF_WINDOW,
	cache: dict = None,
	roi: tuple = None,
) -> tuple:
	"""Compute residual magnitude and validity mask for one frame.

	Warps neighboring frames into frame_index's camera position, builds
	a median background from the aligned stack, and subtracts it to
	reveal moving objects.

	When roi is provided, only processes that region of each frame,
	reducing compute cost proportional to the area ratio.

	Uses cache dict to avoid re-reading full frames in sequential processing.

	Args:
		reader: VideoReader instance.
		frame_index: Center frame index.
		scene_transform: SceneTransform instance.
		half_window: Frames on each side for background (default 2 = 5 frames).
		cache: Optional dict for frame caching. Modified in place.
		roi: Optional (x1, y1, x2, y2) bounds to restrict computation.
			Blob centroids are returned in ROI coordinates; caller must
			add roi offsets to restore full-frame coordinates.

	Returns:
		Tuple of (residual_mag, validity_mask) or (None, None).
	"""
	if cache is None:
		cache = {}

	# read center frame as grayscale float (full frame, cached)
	center_full = _read_gray_frame(reader, frame_index, cache)
	if center_full is None:
		return (None, None)

	h_frame, w_frame = center_full.shape[:2]

	# crop to ROI if specified
	if roi is not None:
		rx1, ry1, rx2, ry2 = roi
		center_float = center_full[ry1:ry2, rx1:rx2]
		roi_h, roi_w = center_float.shape[:2]
	else:
		center_float = center_full
		roi_h, roi_w = h_frame, w_frame
		rx1, ry1 = 0, 0

	# degenerate ROI (prediction off-frame, or clamp produced zero area):
	# downstream cv2.warpAffine calls with a zero-size dsize can yield
	# an unexpected full-frame-shaped result in this code path, which
	# then mis-broadcasts against the empty center_float slice. Treat
	# as "no residual available" for this frame and return.
	if roi_h <= 0 or roi_w <= 0:
		return (None, None)

	scale_factor = 1.0

	# collect aligned neighbor frames into a stack for median computation
	aligned_stack = []
	for k in range(-half_window, half_window + 1):
		if k == 0:
			continue
		fi_other = frame_index + k
		if fi_other < 0 or fi_other >= reader.frame_count:
			continue

		# read neighbor frame as BGR for warping (use cache to avoid re-reads)
		cache_key_bgr = ("bgr", fi_other)
		if cache_key_bgr in cache:
			other_bgr = cache[cache_key_bgr]
		else:
			other_bgr = reader.read_frame(fi_other)
			if other_bgr is None:
				continue
			cache[cache_key_bgr] = other_bgr
		if other_bgr is None:
			continue

		# warp full frame into center frame's camera position
		warp_mat = build_warp_matrix(
			scene_transform, frame_index, fi_other, scale_factor,
		)
		# warp only the ROI region by adjusting output size and offset
		# translate warp matrix to ROI origin
		roi_warp = warp_mat.copy()
		roi_warp[0, 2] -= rx1
		roi_warp[1, 2] -= ry1
		warped = cv2.warpAffine(other_bgr, roi_warp, (roi_w, roi_h))

		# validity mask for warped regions
		pair_validity = compute_validity_mask(warped)

		# convert to grayscale float
		gray_warped = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
		warped_float = gray_warped.astype(numpy.float32)

		# set invalid pixels to NaN so median ignores them
		warped_float[pair_validity == 0] = numpy.nan
		aligned_stack.append(warped_float)

	if len(aligned_stack) < 2:
		return (None, None)

	# build median background from aligned stack
	stack_array = numpy.stack(aligned_stack, axis=0)
	# suppress All-NaN slice warning; edge pixels may have no valid frames
	with warnings.catch_warnings():
		warnings.simplefilter("ignore", RuntimeWarning)
		median_bg = numpy.nanmedian(stack_array, axis=0).astype(numpy.float32)

	# combined validity mask: valid where at least 2 frames contributed
	valid_count = numpy.sum(~numpy.isnan(stack_array), axis=0)
	validity_mask = (valid_count >= 2).astype(numpy.uint8) * 255

	# compute residual: absolute difference between center and median background
	residual = numpy.abs(center_float - median_bg)
	residual[validity_mask == 0] = 0.0

	return (residual, validity_mask)


#============================================
def compute_trajectory_tangent(
	trajectory: list,
	frame_index: int,
) -> tuple:
	"""Compute tangent direction from a solved trajectory at a frame.

	Uses minimum +/-TANGENT_MIN_SPAN frames. Falls back to wider window
	(+/-TANGENT_FALLBACK_SPAN) if confidence is low in the primary range.
	Returns (1, 0, 0, 1) if tangent cannot be computed (disables
	anisotropic decomposition for that frame).

	Args:
		trajectory: List of state dicts.
		frame_index: Frame to compute tangent at.

	Returns:
		Tuple of (tx, ty, nx, ny) as unit vectors.
	"""
	num_frames = len(trajectory)

	# try primary window first, then fallback
	for span in (TANGENT_MIN_SPAN, TANGENT_FALLBACK_SPAN):
		lo = max(0, frame_index - span)
		hi = min(num_frames - 1, frame_index + span)
		if lo >= hi:
			continue

		# check that endpoints have valid trajectory entries
		entry_lo = trajectory[lo]
		entry_hi = trajectory[hi]
		if entry_lo is None or entry_hi is None:
			continue

		# for primary span, check confidence threshold
		if span == TANGENT_MIN_SPAN:
			conf_lo = float(entry_lo.get("conf", 0.0) or 0.0)
			conf_hi = float(entry_hi.get("conf", 0.0) or 0.0)
			if conf_lo < TANGENT_CONFIDENCE_THRESHOLD or conf_hi < TANGENT_CONFIDENCE_THRESHOLD:
				# try fallback span
				continue

		dx = float(entry_hi["cx"]) - float(entry_lo["cx"])
		dy = float(entry_hi["cy"]) - float(entry_lo["cy"])
		magnitude = (dx**2 + dy**2)**0.5

		# tangent too short to be meaningful
		if magnitude < 0.001:
			continue

		# normalize to unit vector
		t_x = dx / magnitude
		t_y = dy / magnitude
		# normal is perpendicular (rotate 90 degrees)
		n_x = -t_y
		n_y = t_x
		return (t_x, t_y, n_x, n_y)

	# cannot compute tangent -- disables anisotropic decomposition
	return (1.0, 0.0, 0.0, 1.0)


#============================================
def compute_cue_confidence(
	blob: dict,
	pred_cx: float,
	pred_cy: float,
	pred_w: float,
	pred_h: float,
	tangent: tuple,
) -> float:
	"""Compute confidence of a motion blob as a tracking cue.

	Factors:
	  - integrated_mag normalized (blob strength) -- weight 0.3
	  - area relative to predicted box area (size plausibility) -- weight 0.3
	  - distance from prediction normalized by box diagonal (proximity) -- weight 0.4

	Args:
		blob: Blob dict with centroid_x, centroid_y, area, integrated_mag.
		pred_cx: Predicted center x.
		pred_cy: Predicted center y.
		pred_w: Predicted box width.
		pred_h: Predicted box height.
		tangent: (tx, ty, nx, ny) unit vectors. Kept for API symmetry.

	Returns:
		Float in [0, 1]. Higher = more trustworthy blob.
	"""
	# strength: integrated magnitude normalized
	strength = min(float(blob["integrated_mag"]) / 10000.0, 1.0)

	# size plausibility: blob area vs predicted box area
	pred_area = pred_w * pred_h
	area_ratio = float(blob["area"]) / pred_area if pred_area > 0 else 0.0
	# ideal ratio ~0.3-0.8 (blob is part of runner, not whole box)
	size_score = 1.0 - abs(area_ratio - 0.5) * 2.0
	size_score = max(0.0, size_score)

	# proximity: isotropic distance normalized by box diagonal
	dx = blob["centroid_x"] - pred_cx
	dy = blob["centroid_y"] - pred_cy
	dist = (dx**2 + dy**2)**0.5
	diag = (pred_w**2 + pred_h**2)**0.5
	proximity = max(0.0, 1.0 - dist / diag) if diag > 0 else 0.0

	confidence = strength * 0.3 + size_score * 0.3 + proximity * 0.4
	result = max(0.0, min(1.0, confidence))
	return result


#============================================
@dataclasses.dataclass
class BlobObservation:
	"""A single per-frame blob measurement. Stateless, carries no history.

	Returned by observe_blob_at when a candidate blob exists in the
	corridor around a predicted position. Gating decisions live in the
	caller, not in this dataclass.

	Attributes:
		center_pixel: (cx, cy) in full-frame pixel coordinates.
		cross_track: Signed cross-track distance from the predicted
			center, in pixels. Positive along normal, negative opposite.
		along_track: Signed along-track distance from the predicted
			center, in pixels.
		confidence: Cue confidence in [0, 1] from compute_cue_confidence.
	"""
	center_pixel: tuple
	cross_track: float
	along_track: float
	confidence: float


#============================================
def observe_blob_at(
	frame_index: int,
	pred_center: tuple,
	pred_box: tuple,
	local_tangent: tuple,
	scene_transform: object,
	reader: object,
	residual_cache: dict,
	threshold: float = DEFAULT_THRESHOLD,
	half_window: int = DEFAULT_HALF_WINDOW,
) -> BlobObservation:
	"""Return the best blob observation at one frame, or None.

	Stateless. Reads raw image evidence and returns a single optional
	measurement. The caller owns all gating logic (proximity, direction,
	temporal smoothness). This function owns image extraction and
	corridor selection only.

	Cache content boundary (strict). residual_cache may hold raw image
	data only:
	  - extracted raw-blob lists (pre-gate)
	  - a sub-dict of cached frame reads (grayscale and BGR)
	It MUST NOT hold any per-frame decision: accepted blobs, filtered
	or chained blob lists, gate outcomes, or any value derived from a
	gating decision. Anything decision-shaped in the cache is back-door
	state and fails review.

	The cache is scoped to a single interval by the caller and cleared
	at interval end. FWD and BWD within the same interval legitimately
	share raw residuals and raw blobs; they each apply independent gates
	in the propagator, so sharing image data does not leak decisions.

	Cache key is `(frame_index, roi)`. FWD and BWD at the same frame
	usually produce near-identical ROIs (both pass's raw_pred converges
	at endpoints and differs only by Hermite slope asymmetry in between);
	when they DO produce identical ROIs the second caller reuses the
	first caller's raw blobs. When they produce DIFFERENT ROIs (tight
	curves, occlusion edges, crowd scenes) each pass computes its own
	residual against its own ROI. This preserves FWD/BWD independence
	in exactly the regimes where divergence matters most, at the cost
	of up to 2x residual computation on intervals where the two passes
	disagree. Raw frame reads (the nested `_frames` sub-cache) are keyed
	by `frame_index` alone and stay shared -- they don't depend on ROI.

	Args:
		frame_index: Frame to observe.
		pred_center: (cx, cy) raw kinematic prediction in pixels. Used
			only to seed the ROI and to decompose blob displacement into
			along-track / cross-track; never written into the cache.
		pred_box: (w, h) predicted box size in pixels. Used for ROI
			size, corridor radius, and confidence scoring.
		local_tangent: (tx, ty, nx, ny) unit vectors describing the
			pass's local motion direction. Pass (1, 0, 0, 1) to fall
			back to axis-aligned decomposition.
		scene_transform: SceneTransform instance used by
			compute_residual_for_frame to align neighbor frames.
		reader: Video reader providing width, height, frame_count, and
			read_frame(index).
		residual_cache: Mutable dict keyed by frame_index. The caller
			creates this empty per interval and drops it after both
			passes complete.
		threshold: Motion intensity threshold for blob extraction.
		half_window: Half-window count for background subtraction.

	Returns:
		BlobObservation for the best in-corridor candidate, or None
		when no residual was computable, no blobs were extracted, or
		no extracted blob fell inside the corridor.
	"""
	pred_cx, pred_cy = pred_center
	pred_w, pred_h = pred_box

	# compute ROI from caller's prediction and use it as part of the
	# cache key so FWD and BWD with divergent raw_pred each get their
	# own residual. _compute_roi returns a 4-tuple, which is hashable.
	frame_w = getattr(reader, "width", 1920)
	frame_h = getattr(reader, "height", 1080)
	roi = _compute_roi(pred_cx, pred_cy, pred_h, frame_w, frame_h)
	cache_key = (frame_index, roi)

	# fetch or compute cached frame data (raw image-derived only)
	cached = residual_cache.get(cache_key)
	if cached is None:
		# nested cache for raw frame reads (keyed by frame_index alone;
		# frame bytes are ROI-independent).
		frame_read_cache = residual_cache.setdefault("_frames", {})
		residual, validity_mask = compute_residual_for_frame(
			reader, frame_index, scene_transform,
			half_window, frame_read_cache, roi,
		)
		if residual is None:
			# negative-result entry avoids re-attempts; holds no decisions
			residual_cache[cache_key] = {"raw_blobs": []}
			return None
		raw_blobs = extract_frame_blobs(residual, validity_mask, threshold)
		# restore full-frame coords so downstream math is in pixel space
		roi_x1 = roi[0]
		roi_y1 = roi[1]
		for blob in raw_blobs:
			blob["centroid_x"] += roi_x1
			blob["centroid_y"] += roi_y1
		residual_cache[cache_key] = {"raw_blobs": raw_blobs}
		cached = residual_cache[cache_key]

	raw_blobs = cached["raw_blobs"]
	if not raw_blobs:
		return None

	# apply corridor filter (uses caller's tangent; NOT stored in cache)
	tangent = local_tangent if local_tangent is not None else (1.0, 0.0, 0.0, 1.0)
	corridor_radius = max(1.5 * pred_w, 0.75 * pred_h)
	corridor_blobs = filter_blobs_to_corridor(
		raw_blobs, pred_cx, pred_cy, tangent, corridor_radius,
	)
	if not corridor_blobs:
		return None

	# pick the highest-confidence blob in the corridor
	best_blob = None
	best_score = -1.0
	for blob in corridor_blobs:
		score = compute_cue_confidence(
			blob, pred_cx, pred_cy, pred_w, pred_h, tangent,
		)
		if score > best_score:
			best_score = score
			best_blob = blob

	if best_blob is None:
		return None

	observation = BlobObservation(
		center_pixel=(float(best_blob["centroid_x"]), float(best_blob["centroid_y"])),
		cross_track=float(best_blob.get("cross_track", 0.0)),
		along_track=float(best_blob.get("along_track", 0.0)),
		confidence=float(best_score),
	)
	return observation
