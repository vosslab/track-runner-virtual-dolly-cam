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

# === Tunable constants ===

# minimum blob area in pixels to suppress noise specks
MIN_BLOB_AREA = 25

# motion intensity threshold for blob extraction
DEFAULT_THRESHOLD = 10.0

# half-window for background estimation (4 = 9-frame window)
# validated on diagnostic runs; provisional default, may reduce for speed
DEFAULT_HALF_WINDOW = 4

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
def compute_residual_for_frame(
	reader: object,
	frame_index: int,
	scene_transform: object,
	half_window: int = DEFAULT_HALF_WINDOW,
	cache: dict = None,
) -> tuple:
	"""Compute residual magnitude and validity mask for one frame.

	Warps neighboring frames into frame_index's camera position, builds
	a median background from the aligned stack, and subtracts it to
	reveal moving objects.

	Uses cache dict to avoid re-reading frames in sequential processing.
	Scale factor is always 1.0 (full resolution).

	Args:
		reader: VideoReader instance.
		frame_index: Center frame index.
		scene_transform: SceneTransform instance.
		half_window: Frames on each side for background (default 4 = 9 frames).
		cache: Optional dict for frame caching. Modified in place.

	Returns:
		Tuple of (residual_mag, validity_mask) or (None, None).
	"""
	if cache is None:
		cache = {}

	# read center frame as grayscale float
	center_float = _read_gray_frame(reader, frame_index, cache)
	if center_float is None:
		return (None, None)

	h_frame, w_frame = center_float.shape[:2]
	scale_factor = 1.0

	# collect aligned neighbor frames into a stack for median computation
	aligned_stack = []
	for k in range(-half_window, half_window + 1):
		if k == 0:
			continue
		fi_other = frame_index + k
		if fi_other < 0 or fi_other >= reader.frame_count:
			continue

		# read neighbor frame as BGR for warping
		other_bgr = reader.read_frame(fi_other)
		if other_bgr is None:
			continue

		# warp into center frame's camera position
		warp_mat = build_warp_matrix(
			scene_transform, frame_index, fi_other, scale_factor,
		)
		warped = cv2.warpAffine(other_bgr, warp_mat, (w_frame, h_frame))

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
def refine_with_motion_cues(
	trajectory: list,
	reader: object,
	scene_transform: object,
	seeds: list,
	half_window: int = DEFAULT_HALF_WINDOW,
	threshold: float = DEFAULT_THRESHOLD,
) -> list:
	"""Apply per-frame motion-cue observation fusion to a fused trajectory.

	For each non-seed frame with a valid trajectory state:
	  1. Predict position from current trajectory
	  2. Compute residual motion (background subtraction)
	  3. Find best corridor blob near predicted position
	  4. Apply two-tier acceptance gate
	  5. If accepted: compute anisotropic correction, blend with prediction
	  6. If vetoed or rejected: keep Hermite prediction

	Tangent is always computed from the original (uncorrected) trajectory
	snapshot. Seeds are never modified. Processes frames sequentially with
	a sliding cache for efficiency. Forward-only processing assumed.

	Three per-frame outcomes:
	  - Accepted: blob passes both tiers, observation fused, state updated
	  - Vetoed: blob found but ambiguous, observation weight = 0
	  - Rejected: blob fails tier 1, chain break rules apply

	Args:
		trajectory: List of state dicts from stitch_trajectories().
		reader: VideoReader instance.
		scene_transform: SceneTransform instance.
		seeds: All seed dicts (for seed-frame protection).
		half_window: Background subtraction window (default 4 = 9 frames).
		threshold: Motion intensity threshold.

	Returns:
		Modified trajectory list (same object, modified in place).
	"""
	num_frames = len(trajectory)
	if num_frames == 0:
		return trajectory

	# guard: reader must support frame_count and read_frame
	if not hasattr(reader, "frame_count") or not hasattr(reader, "read_frame"):
		print("  motion-cue fusion: skipped (reader does not support frame access)")
		return trajectory

	# build set of seed frame indices for protection
	seed_frames = set()
	for seed in seeds:
		seed_frames.add(int(seed["frame_index"]))

	# snapshot original trajectory for tangent computation
	# (tangent must never use corrected positions)
	original_trajectory = []
	for entry in trajectory:
		if entry is None:
			original_trajectory.append(None)
		else:
			original_trajectory.append(dict(entry))

	# frame cache for sequential processing
	cache = {}

	# temporal continuity state
	prev_accepted_blob = None
	miss_count = 0

	# statistics
	frames_processed = 0
	frames_accepted = 0
	frames_vetoed = 0
	frames_rejected = 0
	alpha_sum = 0.0
	chain_lengths = []
	current_chain_length = 0
	chain_break_count = 0

	# find first and last valid frame indices
	first_valid = None
	last_valid = None
	for i in range(num_frames):
		if trajectory[i] is not None:
			if first_valid is None:
				first_valid = i
			last_valid = i
	if first_valid is None:
		return trajectory

	# process each frame sequentially
	for frame_index in range(first_valid, last_valid + 1):
		entry = trajectory[frame_index]
		if entry is None:
			# no trajectory state at this frame
			miss_count += 1
			if miss_count > SHORT_MEMORY_FRAMES:
				if current_chain_length > 0:
					chain_lengths.append(current_chain_length)
					current_chain_length = 0
					chain_break_count += 1
				prev_accepted_blob = None
			continue

		# protect seed frames
		if entry.get("source") == "seed":
			continue

		# skip frames near video boundaries
		if frame_index < half_window or frame_index >= reader.frame_count - half_window:
			continue

		frames_processed += 1

		# get predicted position and size from trajectory
		pred_cx = float(entry["cx"])
		pred_cy = float(entry["cy"])
		pred_w = float(entry["w"])
		pred_h = float(entry["h"])
		traj_conf = float(entry.get("conf", 0.0) or 0.0)

		# compute tangent from ORIGINAL trajectory (never corrected)
		tangent = compute_trajectory_tangent(original_trajectory, frame_index)
		# if tangent is unstable, skip correction for this frame
		tx, ty, nx, ny = tangent
		tangent_mag = (tx**2 + ty**2)**0.5
		if tangent_mag < 0.001:
			miss_count += 1
			if miss_count > SHORT_MEMORY_FRAMES:
				if current_chain_length > 0:
					chain_lengths.append(current_chain_length)
					current_chain_length = 0
					chain_break_count += 1
				prev_accepted_blob = None
			continue

		# compute residual for this frame
		residual, validity_mask = compute_residual_for_frame(
			reader, frame_index, scene_transform, half_window, cache,
		)
		if residual is None:
			miss_count += 1
			if miss_count > SHORT_MEMORY_FRAMES:
				if current_chain_length > 0:
					chain_lengths.append(current_chain_length)
					current_chain_length = 0
					chain_break_count += 1
				prev_accepted_blob = None
			continue

		# extract blobs
		blobs = extract_frame_blobs(residual, validity_mask, threshold)
		if not blobs:
			# no observation: keep Hermite, manage chain
			miss_count += 1
			if miss_count > SHORT_MEMORY_FRAMES:
				if current_chain_length > 0:
					chain_lengths.append(current_chain_length)
					current_chain_length = 0
					chain_break_count += 1
				prev_accepted_blob = None
			continue

		# filter blobs to corridor
		corridor_radius = max(1.5 * pred_w, 0.75 * pred_h)
		corridor_blobs = filter_blobs_to_corridor(
			blobs, pred_cx, pred_cy, tangent, corridor_radius,
		)
		if not corridor_blobs:
			miss_count += 1
			if miss_count > SHORT_MEMORY_FRAMES:
				if current_chain_length > 0:
					chain_lengths.append(current_chain_length)
					current_chain_length = 0
					chain_break_count += 1
				prev_accepted_blob = None
			continue

		# score all corridor blobs
		blob_scores = []
		for blob in corridor_blobs:
			score = compute_cue_confidence(
				blob, pred_cx, pred_cy, pred_w, pred_h, tangent,
			)
			blob_scores.append((blob, score))
		# sort by score descending
		blob_scores.sort(key=lambda x: x[1], reverse=True)

		best_blob, best_confidence = blob_scores[0]
		second_best_confidence = blob_scores[1][1] if len(blob_scores) > 1 else 0.0

		# tier-1 hard gate
		passes_tier1 = _apply_tier1_gate(
			best_blob, pred_cx, pred_cy, pred_w, pred_h,
			best_confidence, prev_accepted_blob, tangent, corridor_radius,
		)

		if not passes_tier1:
			# rejected: chain break rules apply
			frames_rejected += 1
			miss_count += 1
			if miss_count > SHORT_MEMORY_FRAMES:
				if current_chain_length > 0:
					chain_lengths.append(current_chain_length)
					current_chain_length = 0
					chain_break_count += 1
				prev_accepted_blob = None
			continue

		# tier-2 soft penalties
		effective_confidence = _apply_tier2_penalties(
			best_blob, best_confidence, pred_cx, pred_cy, pred_w, pred_h,
			tangent, prev_accepted_blob, second_best_confidence,
		)

		# ambiguity veto
		if effective_confidence < VETO_CONFIDENCE_THRESHOLD:
			# vetoed: preserve temporal memory, do NOT update state
			frames_vetoed += 1
			continue

		# accepted: compute corrected position
		new_cx, new_cy, alpha_used = _compute_corrected_position(
			best_blob, pred_cx, pred_cy, pred_w, pred_h,
			tangent, effective_confidence, traj_conf,
		)

		# update trajectory entry (position only, not size)
		entry["cx"] = new_cx
		entry["cy"] = new_cy

		# update temporal state
		prev_accepted_blob = best_blob
		miss_count = 0
		current_chain_length += 1
		frames_accepted += 1
		alpha_sum += alpha_used

		# evict old cache entries (forward-only, +2 buffer)
		evict_before = frame_index - half_window - 3
		keys_to_evict = [k for k in cache if k < evict_before]
		for k in keys_to_evict:
			del cache[k]

	# close final chain
	if current_chain_length > 0:
		chain_lengths.append(current_chain_length)

	# print summary
	non_seed_frames = frames_processed
	if non_seed_frames > 0:
		usage_rate = frames_accepted / non_seed_frames
		veto_rate = frames_vetoed / non_seed_frames
		mean_alpha = alpha_sum / frames_accepted if frames_accepted > 0 else 0.0
		mean_chain = sum(chain_lengths) / len(chain_lengths) if chain_lengths else 0.0
	else:
		usage_rate = 0.0
		veto_rate = 0.0
		mean_alpha = 0.0
		mean_chain = 0.0

	print(f"  motion-cue fusion: {frames_processed} frames processed")
	print(f"    observation usage: {frames_accepted}/{non_seed_frames} "
		f"({usage_rate:.1%})")
	print(f"    vetoed: {frames_vetoed} ({veto_rate:.1%}), "
		f"rejected: {frames_rejected}")
	print(f"    mean alpha: {mean_alpha:.3f}, "
		f"mean chain: {mean_chain:.1f}, "
		f"chain breaks: {chain_break_count}")

	return trajectory
