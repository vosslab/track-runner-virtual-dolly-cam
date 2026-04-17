"""Per-frame observation fusion using temporal background subtraction.

Provides center-position observations from residual motion blobs inside the
Hermite kinematic scaffold. The blob is a per-frame visual observation channel:
fused continuously when unambiguous, vetoed aggressively when ambiguous.

Hermite owns: path shape, velocity continuity, box size, aspect ratio, scale.
Blob cue owns: local center observation only.

Integration point: called between stitch_trajectories() and anchor_to_seeds()
in interval_solver.solve_all_intervals().

Tunable constants are module-level ALL_CAPS variables.

See docs/active_plans/MOTION_CUE_OBSERVATION_FUSION.md for design rationale.
"""

# Standard Library
import warnings

# PIP3 modules
import cv2
import numpy
import rich.progress

# === Tunable constants ===

# minimum blob area in pixels to suppress noise specks
MIN_BLOB_AREA = 25

# motion intensity threshold for blob extraction
DEFAULT_THRESHOLD = 10.0

# half-window for background estimation (2 = 5-frame window)
# diagnostic tool uses 4 (9-frame) for sparse sampling; pipeline uses 2
# because it processes every frame sequentially, so temporal coverage is dense
DEFAULT_HALF_WINDOW = 2

# ROI multiplier: crop region is this many times the torso box height
# matches the 8x used in tools/diagnose_residual_motion.py
ROI_MULTIPLIER = 8.0

# maximum blend weight toward blob observation
ALPHA_MAX = 0.6

# minimum cue confidence to pass tier-1 gate
MIN_CUE_CONFIDENCE = 0.25

# final veto threshold after tier-2 penalties
VETO_CONFIDENCE_THRESHOLD = 0.15

# temporal continuity: max distance from previous accepted blob
# expressed as fraction of max(pred_w, pred_h)
TEMPORAL_LINK_FRACTION = 0.75

# distance gate: max distance from predicted center
# expressed as fraction of max(pred_w, pred_h)
DISTANCE_GATE_FRACTION = 0.75

# along-track tier-2 penalty threshold (fraction of pred_h)
ALONG_TRACK_PENALTY_FRACTION = 2.0

# along-track scale factor (downweight relative to cross-track)
ALONG_TRACK_WEIGHT = 0.5

# cross-track clamp (fraction of pred_w)
CROSS_TRACK_CLAMP_FRACTION = 0.5

# along-track clamp (fraction of pred_h)
ALONG_TRACK_CLAMP_FRACTION = 0.75

# selection margin: best score must exceed second-best by this factor
SELECTION_MARGIN = 1.5

# short memory: keep prev_blob for this many missed frames
SHORT_MEMORY_FRAMES = 3

# tangent estimation: minimum half-window
TANGENT_MIN_SPAN = 5

# tangent estimation: fallback half-window for low-confidence regions
TANGENT_FALLBACK_SPAN = 10

# tangent estimation: minimum confidence for primary window
TANGENT_CONFIDENCE_THRESHOLD = 0.5

# tier-2 penalty multipliers
PENALTY_DIRECTION = 0.5
PENALTY_MOTION_DIRECTION = 0.5
PENALTY_LOW_MARGIN = 0.5
PENALTY_ALONG_TRACK = 0.3


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

	Forward-only assumption: frames are read in sequential order. Cache
	entries older than the current window are evicted by the caller.

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
	Clamps to frame boundaries. If ROI would cover most of the frame,
	returns the full frame bounds.

	Args:
		pred_cx: Predicted center x in pixels.
		pred_cy: Predicted center y in pixels.
		pred_h: Predicted box height in pixels.
		frame_w: Full frame width.
		frame_h: Full frame height.

	Returns:
		Tuple of (x1, y1, x2, y2) pixel bounds, clamped to frame.
	"""
	half_side = int(ROI_MULTIPLIER * pred_h / 2)
	# minimum ROI size of 100px
	half_side = max(half_side, 50)

	x1 = max(0, int(pred_cx) - half_side)
	y1 = max(0, int(pred_cy) - half_side)
	x2 = min(frame_w, int(pred_cx) + half_side)
	y2 = min(frame_h, int(pred_cy) + half_side)
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
		half_window: Frames on each side for background (default 4 = 9 frames).
		cache: Optional dict for frame caching. Modified in place.
		roi: Optional (x1, y1, x2, y2) bounds to restrict computation.
			Blob centroids are returned in full-frame coordinates.

	Returns:
		Tuple of (residual_mag, validity_mask) or (None, None).
		When roi is used, these are cropped to the ROI with blob
		centroids adjusted by roi offset.
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
	"""Compute tangent direction from the original trajectory at a frame.

	Uses minimum +/-TANGENT_MIN_SPAN frames. Falls back to wider window
	(+/-TANGENT_FALLBACK_SPAN) if confidence is low in the primary range.
	Returns (1, 0, 0, 1) if tangent cannot be computed (disables
	anisotropic decomposition for that frame).

	Args:
		trajectory: List of state dicts (original, uncorrected).
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

	Scores in Hermite-relative coordinates (along-track and cross-track
	decomposed before scoring).

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
		tangent: (tx, ty, nx, ny) unit vectors.

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

	# proximity: decompose into along-track and cross-track
	dx = blob["centroid_x"] - pred_cx
	dy = blob["centroid_y"] - pred_cy
	dist = (dx**2 + dy**2)**0.5
	diag = (pred_w**2 + pred_h**2)**0.5
	proximity = max(0.0, 1.0 - dist / diag) if diag > 0 else 0.0

	confidence = strength * 0.3 + size_score * 0.3 + proximity * 0.4
	result = max(0.0, min(1.0, confidence))
	return result


#============================================
def _apply_tier1_gate(
	blob: dict,
	pred_cx: float,
	pred_cy: float,
	pred_w: float,
	pred_h: float,
	cue_confidence: float,
	prev_accepted_blob: dict,
	tangent: tuple,
	corridor_radius: float,
) -> bool:
	"""Apply tier-1 hard rejection gate. All four must pass.

	1. Corridor containment
	2. Distance constraint
	3. Minimum confidence
	4. Temporal continuity (primary identity gate)

	Args:
		blob: Blob dict with centroid_x, centroid_y, cross_track, along_track.
		pred_cx: Predicted center x.
		pred_cy: Predicted center y.
		pred_w: Predicted box width.
		pred_h: Predicted box height.
		cue_confidence: Confidence score from compute_cue_confidence.
		prev_accepted_blob: Previous accepted blob dict, or None.
		tangent: (tx, ty, nx, ny) unit vectors.
		corridor_radius: Half-width of corridor.

	Returns:
		True if blob passes all tier-1 gates.
	"""
	# gate 1: corridor containment (already filtered, but verify)
	cross = blob.get("cross_track")
	if cross is not None and abs(cross) > corridor_radius:
		return False

	# gate 2: distance constraint
	dx = blob["centroid_x"] - pred_cx
	dy = blob["centroid_y"] - pred_cy
	dist = (dx**2 + dy**2)**0.5
	max_dist = DISTANCE_GATE_FRACTION * max(pred_w, pred_h)
	if dist > max_dist:
		return False

	# gate 3: minimum confidence
	if cue_confidence < MIN_CUE_CONFIDENCE:
		return False

	# gate 4: temporal continuity (primary identity gate)
	if prev_accepted_blob is not None:
		prev_dx = blob["centroid_x"] - prev_accepted_blob["centroid_x"]
		prev_dy = blob["centroid_y"] - prev_accepted_blob["centroid_y"]
		jump = (prev_dx**2 + prev_dy**2)**0.5
		max_link = TEMPORAL_LINK_FRACTION * max(pred_w, pred_h)
		if jump > max_link:
			return False

	return True


#============================================
def _apply_tier2_penalties(
	blob: dict,
	cue_confidence: float,
	pred_cx: float,
	pred_cy: float,
	pred_w: float,
	pred_h: float,
	tangent: tuple,
	prev_accepted_blob: dict,
	second_best_confidence: float,
) -> float:
	"""Apply tier-2 soft confidence penalties.

	Reduces effective confidence without hard rejection.

	Args:
		blob: Blob dict with centroid_x, centroid_y.
		cue_confidence: Base confidence from compute_cue_confidence.
		pred_cx: Predicted center x.
		pred_cy: Predicted center y.
		pred_w: Predicted box width.
		pred_h: Predicted box height.
		tangent: (tx, ty, nx, ny) unit vectors.
		prev_accepted_blob: Previous accepted blob, or None.
		second_best_confidence: Confidence of second-best blob, or 0.0.

	Returns:
		Effective confidence after penalties.
	"""
	effective = cue_confidence
	tx, ty, nx, ny = tangent

	# penalty: direction disagreement (blob displacement opposes tangent)
	dx = blob["centroid_x"] - pred_cx
	dy = blob["centroid_y"] - pred_cy
	dot = dx * tx + dy * ty
	if dot < 0:
		effective *= PENALTY_DIRECTION

	# penalty: motion direction inconsistency (blob velocity opposes tangent)
	if prev_accepted_blob is not None:
		vx = blob["centroid_x"] - prev_accepted_blob["centroid_x"]
		vy = blob["centroid_y"] - prev_accepted_blob["centroid_y"]
		motion_dot = vx * tx + vy * ty
		if motion_dot < 0:
			effective *= PENALTY_MOTION_DIRECTION

	# penalty: low selection margin
	if second_best_confidence > 0.0:
		if cue_confidence < SELECTION_MARGIN * second_best_confidence:
			effective *= PENALTY_LOW_MARGIN

	# penalty: large along-track magnitude (secondary guard)
	along = blob.get("along_track")
	if along is not None and abs(along) > ALONG_TRACK_PENALTY_FRACTION * pred_h:
		effective *= PENALTY_ALONG_TRACK

	return effective


#============================================
def _compute_corrected_position(
	blob: dict,
	pred_cx: float,
	pred_cy: float,
	pred_w: float,
	pred_h: float,
	tangent: tuple,
	effective_confidence: float,
	traj_conf: float,
) -> tuple:
	"""Compute corrected center position using Hermite-frame projection.

	Decomposes blob offset into along-track and cross-track, applies
	anisotropic clamping and weighting, then blends with prediction.

	Cross-track: tighter clamp, full weight (runners stay in lane).
	Along-track: looser clamp, downweighted (timing imprecision).

	Args:
		blob: Blob dict with centroid_x, centroid_y.
		pred_cx: Predicted center x.
		pred_cy: Predicted center y.
		pred_w: Predicted box width.
		pred_h: Predicted box height.
		tangent: (tx, ty, nx, ny) unit vectors.
		effective_confidence: Confidence after tier-2 penalties.
		traj_conf: Trajectory confidence at this frame.

	Returns:
		Tuple of (new_cx, new_cy, alpha_used).
	"""
	tx, ty, nx, ny = tangent

	# compute raw offset from prediction to blob
	dx = blob["centroid_x"] - pred_cx
	dy = blob["centroid_y"] - pred_cy

	# decompose into along-track and cross-track (raw, for gating)
	along_raw = dx * tx + dy * ty
	cross_raw = dx * nx + dy * ny

	# clamp: cross-track tighter (more reliable signal)
	cross_clamp = CROSS_TRACK_CLAMP_FRACTION * pred_w
	cross = max(-cross_clamp, min(cross_clamp, cross_raw))

	# clamp: along-track looser (timing imprecision)
	along_clamp = ALONG_TRACK_CLAMP_FRACTION * pred_h
	along = max(-along_clamp, min(along_clamp, along_raw))

	# downweight along-track
	along *= ALONG_TRACK_WEIGHT

	# reconstruct corrected position in pixel space
	corrected_x = pred_cx + along * tx + cross * nx
	corrected_y = pred_cy + along * ty + cross * ny

	# compute blend alpha
	alpha = ALPHA_MAX * effective_confidence * (1.0 - traj_conf)

	# blend prediction and corrected observation
	new_cx = (1.0 - alpha) * pred_cx + alpha * corrected_x
	new_cy = (1.0 - alpha) * pred_cy + alpha * corrected_y

	return (new_cx, new_cy, alpha)


#============================================
def _process_one_frame(
	frame_index: int,
	entry: dict,
	original_trajectory: list,
	reader: object,
	scene_transform: object,
	prev_accepted_blob: dict,
	half_window: int,
	threshold: float,
	cache: dict,
) -> tuple:
	"""Process a single frame for motion-cue observation fusion.

	Computes residual, extracts blobs, applies two-tier gate, and
	returns the correction result.

	Args:
		frame_index: Current frame index.
		entry: Trajectory state dict for this frame.
		original_trajectory: Snapshot of uncorrected trajectory (for tangent).
		reader: VideoReader instance.
		scene_transform: SceneTransform instance.
		prev_accepted_blob: Previous accepted blob dict, or None.
		half_window: Background subtraction window.
		threshold: Motion threshold.
		cache: Frame cache dict, modified in place.

	Returns:
		Tuple of (outcome, blob, alpha_used) where outcome is one of:
		  "accepted" - blob passed, alpha_used > 0, entry updated
		  "vetoed" - blob found but ambiguous
		  "rejected" - blob failed tier-1
		  "no_data" - no residual or no blobs found
		  "no_tangent" - tangent unstable, skipped
	"""
	# get predicted position and size
	pred_cx = float(entry["cx"])
	pred_cy = float(entry["cy"])
	pred_w = float(entry["w"])
	pred_h = float(entry["h"])
	traj_conf = float(entry.get("conf", 0.0) or 0.0)

	# compute tangent from ORIGINAL trajectory (never corrected)
	tangent = compute_trajectory_tangent(original_trajectory, frame_index)
	tx, ty, nx, ny = tangent
	tangent_mag = (tx**2 + ty**2)**0.5
	if tangent_mag < 0.001:
		return ("no_tangent", None, 0.0)

	# compute ROI around predicted position (8x torso box)
	frame_w = reader.width if hasattr(reader, "width") else 1920
	frame_h = reader.height if hasattr(reader, "height") else 1080
	roi = _compute_roi(pred_cx, pred_cy, pred_h, frame_w, frame_h)
	roi_x1, roi_y1 = roi[0], roi[1]

	# compute residual within ROI only
	residual, validity_mask = compute_residual_for_frame(
		reader, frame_index, scene_transform, half_window, cache, roi,
	)
	if residual is None:
		return ("no_data", None, 0.0)

	# extract blobs (centroids are in ROI coordinates)
	blobs = extract_frame_blobs(residual, validity_mask, threshold)
	# convert blob centroids from ROI to full-frame coordinates
	for blob in blobs:
		blob["centroid_x"] += roi_x1
		blob["centroid_y"] += roi_y1
	if not blobs:
		return ("no_data", None, 0.0)

	# filter blobs to corridor
	corridor_radius = max(1.5 * pred_w, 0.75 * pred_h)
	corridor_blobs = filter_blobs_to_corridor(
		blobs, pred_cx, pred_cy, tangent, corridor_radius,
	)
	if not corridor_blobs:
		return ("no_data", None, 0.0)

	# score all corridor blobs
	blob_scores = []
	for blob in corridor_blobs:
		score = compute_cue_confidence(
			blob, pred_cx, pred_cy, pred_w, pred_h, tangent,
		)
		blob_scores.append((blob, score))
	blob_scores.sort(key=lambda x: x[1], reverse=True)

	best_blob, best_confidence = blob_scores[0]
	second_best_confidence = blob_scores[1][1] if len(blob_scores) > 1 else 0.0

	# tier-1 hard gate
	passes_tier1 = _apply_tier1_gate(
		best_blob, pred_cx, pred_cy, pred_w, pred_h,
		best_confidence, prev_accepted_blob, tangent, corridor_radius,
	)
	if not passes_tier1:
		return ("rejected", None, 0.0)

	# tier-2 soft penalties
	effective_confidence = _apply_tier2_penalties(
		best_blob, best_confidence, pred_cx, pred_cy, pred_w, pred_h,
		tangent, prev_accepted_blob, second_best_confidence,
	)

	# ambiguity veto
	if effective_confidence < VETO_CONFIDENCE_THRESHOLD:
		return ("vetoed", None, 0.0)

	# accepted: compute corrected position
	new_cx, new_cy, alpha_used = _compute_corrected_position(
		best_blob, pred_cx, pred_cy, pred_w, pred_h,
		tangent, effective_confidence, traj_conf,
	)

	# update trajectory entry (position only, not size)
	entry["cx"] = new_cx
	entry["cy"] = new_cy

	return ("accepted", best_blob, alpha_used)


#============================================
def _handle_miss(state: dict) -> None:
	"""Update temporal state after a missed frame (no blob, rejected, no data).

	Increments miss_count. If miss_count exceeds SHORT_MEMORY_FRAMES,
	breaks the chain and clears prev_accepted_blob.

	Args:
		state: Mutable dict with prev_accepted_blob, miss_count,
			current_chain_length, chain_lengths, chain_break_count.
	"""
	state["miss_count"] += 1
	if state["miss_count"] > SHORT_MEMORY_FRAMES:
		if state["current_chain_length"] > 0:
			state["chain_lengths"].append(state["current_chain_length"])
			state["current_chain_length"] = 0
			state["chain_break_count"] += 1
		state["prev_accepted_blob"] = None


#============================================
def refine_with_motion_cues(
	trajectory: list,
	reader: object,
	scene_transform: object,
	seeds: list,
	half_window: int = DEFAULT_HALF_WINDOW,
	threshold: float = DEFAULT_THRESHOLD,
	race_start_frame: int = None,
) -> tuple:
	"""Apply per-frame motion-cue observation fusion to a fused trajectory.

	For each non-seed frame: compute residual, find best blob, apply
	two-tier gate, blend accepted observations with Hermite prediction.
	Seeds are never modified. Tangent from original trajectory snapshot.

	Args:
		trajectory: List of state dicts from stitch_trajectories().
		reader: VideoReader instance.
		scene_transform: SceneTransform instance.
		seeds: All seed dicts (for seed-frame protection).
		half_window: Background subtraction window (default 4 = 9 frames).
		threshold: Motion intensity threshold.
		race_start_frame: If not None, frames before this index are
			skipped entirely (pre-race veto). The runner is stationary,
			so motion-cue has no valid signal and should not be fused.

	Returns:
		Tuple of (modified_trajectory, per_interval_stats) where
		per_interval_stats is a list of dicts, one per interval, with keys:
		{"processed": int, "accepted": int, "acceptance_rate": float}.
		List index corresponds to zero-based interval index.
	"""
	num_frames = len(trajectory)
	if num_frames == 0:
		return trajectory, []

	# guard: reader must support frame_count and read_frame
	if not hasattr(reader, "frame_count") or not hasattr(reader, "read_frame"):
		print("  motion-cue fusion: skipped (reader does not support frame access)")
		# return zero-filled stats for all intervals
		num_intervals = max(1, len(seeds) - 1) if seeds else 1
		return trajectory, [{"processed": 0, "accepted": 0, "acceptance_rate": 0.0}
			for _ in range(num_intervals)]

	# build sorted seed frame list for interval boundary detection
	seed_frame_list = sorted(int(s["frame_index"]) for s in seeds
		if s.get("status") in ("visible", "partial", "approximate"))

	# snapshot original trajectory for tangent computation
	original_trajectory = []
	for entry in trajectory:
		if entry is None:
			original_trajectory.append(None)
		else:
			original_trajectory.append(dict(entry))

	# frame cache for sequential processing
	cache = {}

	# temporal continuity and statistics state
	state = {
		"prev_accepted_blob": None,
		"miss_count": 0,
		"current_chain_length": 0,
		"chain_lengths": [],
		"chain_break_count": 0,
	}
	frames_processed = 0
	frames_accepted = 0
	frames_vetoed = 0
	frames_rejected = 0
	frames_pre_race = 0
	alpha_sum = 0.0

	# find first and last valid frame indices
	first_valid = None
	last_valid = None
	for i in range(num_frames):
		if trajectory[i] is not None:
			if first_valid is None:
				first_valid = i
			last_valid = i
	if first_valid is None:
		# return zero-filled stats for all intervals
		num_intervals = max(1, len(seed_frame_list) - 1) if seed_frame_list else 1
		return trajectory, [{"processed": 0, "accepted": 0, "acceptance_rate": 0.0}
			for _ in range(num_intervals)]

	# interval tracking: find which interval each frame belongs to
	# intervals are defined by consecutive seed pairs
	current_interval_idx = 0
	interval_start = seed_frame_list[0] if seed_frame_list else first_valid
	interval_end = seed_frame_list[1] if len(seed_frame_list) > 1 else last_valid
	iv_accepted = 0
	iv_processed = 0
	total_intervals = max(1, len(seed_frame_list) - 1)
	per_interval_stats = []

	total_frames = last_valid - first_valid + 1
	# "global, not scoped" advertises that fusion ignores the refine-mode
	# locality invariant: the entire trajectory is fused regardless of how
	# many intervals actually changed. see plan "Deferred: fusion scoping".
	print(f"  motion-cue fusion: {total_frames} frames, "
		f"{total_intervals} intervals, "
		f"window={2 * half_window + 1} frames "
		f"(global, not scoped)")
	print(f"  fusion: starting interval 1/{total_intervals} "
		f"(frames {interval_start}-{interval_end})")
	progress = rich.progress.Progress(
		rich.progress.TextColumn("  motion-cue fusion"),
		rich.progress.BarColumn(),
		rich.progress.TaskProgressColumn(),
		rich.progress.TimeRemainingColumn(),
		rich.progress.TimeElapsedColumn(),
		refresh_per_second=1,
	)
	with progress:
		ptask = progress.add_task("fusion", total=total_frames)
		for frame_index in range(first_valid, last_valid + 1):
			progress.update(ptask, advance=1)

			# detect interval boundary and print summary of previous interval
			if (len(seed_frame_list) > 1
				and current_interval_idx < total_intervals
				and frame_index >= interval_end):
				# print one-line summary for the completed interval
				if iv_processed > 0:
					iv_rate = iv_accepted / iv_processed
					progress.console.print(
						f"  fusion: interval {current_interval_idx + 1}/{total_intervals} "
						f"(frames {interval_start}-{interval_end}): "
						f"{iv_accepted}/{iv_processed} accepted ({iv_rate:.0%})"
					)
					# append stats for this interval
					per_interval_stats.append({
						"processed": iv_processed,
						"accepted": iv_accepted,
						"acceptance_rate": iv_rate,
					})
				else:
					# zero-processed interval still gets a stats entry
					per_interval_stats.append({
						"processed": 0,
						"accepted": 0,
						"acceptance_rate": 0.0,
					})
				# advance to next interval
				current_interval_idx += 1
				iv_accepted = 0
				iv_processed = 0
				interval_start = interval_end
				if current_interval_idx < total_intervals:
					interval_end = seed_frame_list[current_interval_idx + 1]
				else:
					interval_end = last_valid
				# announce start of new interval
				progress.console.print(
					f"  fusion: starting interval {current_interval_idx + 1}/{total_intervals} "
					f"(frames {interval_start}-{interval_end})"
				)

			entry = trajectory[frame_index]

			# pre-race veto: runner is stationary, motion-cue has no valid
			# signal here. break the temporal chain so stale pre-race blob
			# state cannot bleed across the race-start boundary.
			if race_start_frame is not None and frame_index < race_start_frame:
				state["prev_accepted_blob"] = None
				state["miss_count"] = 0
				if state["current_chain_length"] > 0:
					state["chain_lengths"].append(state["current_chain_length"])
					state["current_chain_length"] = 0
				frames_pre_race += 1
				continue

			# skip empty frames
			if entry is None:
				_handle_miss(state)
				continue

			# protect seed frames
			if entry.get("source") == "seed":
				continue

			# skip frames near video boundaries
			if frame_index < half_window or frame_index >= reader.frame_count - half_window:
				continue

			frames_processed += 1
			iv_processed += 1

			# process this frame
			outcome, blob, alpha_used = _process_one_frame(
				frame_index, entry, original_trajectory, reader,
				scene_transform, state["prev_accepted_blob"],
				half_window, threshold, cache,
			)

			if outcome == "accepted":
				state["prev_accepted_blob"] = blob
				state["miss_count"] = 0
				state["current_chain_length"] += 1
				frames_accepted += 1
				iv_accepted += 1
				alpha_sum += alpha_used
			elif outcome == "vetoed":
				# veto preserves temporal memory
				frames_vetoed += 1
			elif outcome == "rejected":
				frames_rejected += 1
				_handle_miss(state)
			else:
				# no_data or no_tangent
				_handle_miss(state)

			# evict old cache entries (forward-only, +2 buffer)
			evict_before = frame_index - half_window - 3
			keys_to_evict = []
			for k in cache:
				# cache keys are either int (grayscale) or ("bgr", int)
				if isinstance(k, int) and k < evict_before:
					keys_to_evict.append(k)
				elif isinstance(k, tuple) and k[1] < evict_before:
					keys_to_evict.append(k)
			for k in keys_to_evict:
				del cache[k]

	# print final interval summary and append final interval stats
	if iv_processed > 0:
		iv_rate = iv_accepted / iv_processed
		print(f"  fusion: interval {current_interval_idx + 1}/{total_intervals} "
			f"(frames {interval_start}-{interval_end}): "
			f"{iv_accepted}/{iv_processed} accepted ({iv_rate:.0%})")
		# append stats for final interval
		per_interval_stats.append({
			"processed": iv_processed,
			"accepted": iv_accepted,
			"acceptance_rate": iv_rate,
		})
	else:
		# zero-processed final interval still gets a stats entry
		per_interval_stats.append({
			"processed": 0,
			"accepted": 0,
			"acceptance_rate": 0.0,
		})

	# close final chain
	if state["current_chain_length"] > 0:
		state["chain_lengths"].append(state["current_chain_length"])

	# print summary
	chain_lengths = state["chain_lengths"]
	chain_break_count = state["chain_break_count"]
	if frames_processed > 0:
		usage_rate = frames_accepted / frames_processed
		veto_rate = frames_vetoed / frames_processed
		mean_alpha = alpha_sum / frames_accepted if frames_accepted > 0 else 0.0
		mean_chain = sum(chain_lengths) / len(chain_lengths) if chain_lengths else 0.0
	else:
		usage_rate = 0.0
		veto_rate = 0.0
		mean_alpha = 0.0
		mean_chain = 0.0

	print(f"  motion-cue fusion: {frames_processed} frames processed")
	if frames_pre_race > 0:
		print(f"    pre-race skipped: {frames_pre_race} "
			f"(race_start_frame={race_start_frame})")
	print(f"    observation usage: {frames_accepted}/{frames_processed} "
		f"({usage_rate:.1%})")
	print(f"    vetoed: {frames_vetoed} ({veto_rate:.1%}), "
		f"rejected: {frames_rejected}")
	print(f"    mean alpha: {mean_alpha:.3f}, "
		f"mean chain: {mean_chain:.1f}, "
		f"chain breaks: {chain_break_count}")

	return trajectory, per_interval_stats
