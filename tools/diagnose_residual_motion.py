#!/usr/bin/env python3
"""Diagnose residual motion signal using aligned temporal background subtraction.

Warps neighboring frames into the center frame's camera position, builds a
robust median background from the aligned stack, and subtracts it from the
center frame to reveal moving objects. The runner appears as a residual
because it occupies a different position in each contributing frame.

Scoring uses constrained temporal data association:
  - Seed frames: measure blob distance to known annotated box (gate 1)
  - Gap frames: find blobs within a corridor along the Hermite curve,
    track them across 5 consecutive frames, pick the most temporally
    consistent candidate (gate 2)

Gate 1 must pass before trusting gate 2 results. If the method fails on
seed frames where truth is known, it is too weak for this video.

Outputs:
  - Per-frame 2x2 diagnostic PNGs (original, raw diff, bg subtraction, motion mask)
  - Summary montage of all motion masks
  - Short residual video (~100 frames from weakest solver gap)
  - Per-frame statistics table and two-gate experiment summary to stdout
"""

# Standard Library
import os
import sys
import glob
import random
import warnings
import argparse

# add track_runner directory to path so we can import its modules
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, "track_runner")
if _TRACK_RUNNER_DIR not in sys.path:
	sys.path.insert(0, _TRACK_RUNNER_DIR)

# PIP3 modules
import cv2
import numpy

# local repo modules
import camera_motion
import scene_coords
import state_io
import tr_paths
import video_io

# track scoring weights (cross-track penalty dominates)
WEIGHT_PERSISTENCE = 2.0
WEIGHT_STRENGTH = 1.0
WEIGHT_CROSS_TRACK = 3.0
WEIGHT_ALONG_TRACK = 0.5
WEIGHT_SMOOTHNESS = 0.5

# review candidate thresholds (frames worth re-seeding)
REVIEW_MIN_TRACK_LENGTH = 3
REVIEW_MAX_CROSS_TRACK = 100.0
REVIEW_MAX_ALONG_TRACK = 300.0
REVIEW_MIN_SCORE = 1.5

# minimum blob area in pixels to suppress noise specks
MIN_BLOB_AREA = 25


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description="Diagnose residual motion signal after camera compensation"
	)
	parser.add_argument(
		"-i", "--input", dest="input_file", required=True,
		help="Path to input video file"
	)
	parser.add_argument(
		"-n", "--num-samples", dest="num_samples", type=int, default=10,
		help="Number of frames to sample (default: 10)"
	)
	parser.add_argument(
		"-t", "--threshold", dest="threshold", type=float, default=10.0,
		help="Residual intensity threshold for motion mask (default: 10.0)"
	)
	parser.add_argument(
		"-s", "--scale", dest="scale", type=float, default=1.0,
		help="Downsample factor, e.g. 0.5 for half resolution (default: 1.0)"
	)
	parser.add_argument(
		"-w", "--window", dest="half_window", type=int, default=4,
		help="Half-window for background estimation (default: 4 = 9 frames)"
	)
	args = parser.parse_args()
	return args


#============================================
def load_all_data(input_file: str) -> tuple:
	"""Load video, motion track, seeds, diagnostics, and intervals.

	Args:
		input_file: Path to the input video file.

	Returns:
		Tuple of (reader, motion_track, scene_transform, seeds_list,
		diagnostics, intervals_data).
	"""
	# load video
	reader = video_io.VideoReader(input_file)
	print(f"  video: {reader.frame_count} frames, {reader.fps:.1f} fps, "
		f"{reader.width}x{reader.height}")

	# load camera motion cache
	# cache files use a computed key, not a fixed path; glob for matching npz
	basename = os.path.basename(input_file)
	cache_pattern = os.path.join(tr_paths.DATA_DIR, f"{basename}_*.npz")
	cache_files = sorted(glob.glob(cache_pattern))
	motion_track = None
	cache_path = None
	for candidate in cache_files:
		motion_track = camera_motion.load_motion_cache(candidate)
		if motion_track is not None:
			cache_path = candidate
			break
	if motion_track is None:
		raise RuntimeError(
			f"No camera motion cache found matching {cache_pattern}. "
			f"Run 'setup' or 'solve' first."
		)
	print(f"  motion cache: {cache_path}")

	# build scene transform
	scene_transform = scene_coords.SceneTransform(motion_track)

	# load seeds
	seeds_path = tr_paths.default_seeds_path(input_file)
	seeds_data = state_io.load_seeds(seeds_path)
	seeds_list = seeds_data.get("seeds", [])
	print(f"  seeds: {len(seeds_list)} loaded from {seeds_path}")

	# load diagnostics (has interval confidence info)
	diag_path = tr_paths.default_diagnostics_path(input_file)
	diagnostics = state_io.load_diagnostics(diag_path)
	diag_intervals = diagnostics.get("intervals", [])
	print(f"  diagnostics: {len(diag_intervals)} intervals")

	# load solved intervals (has fused_track per interval)
	intervals_path = tr_paths.default_intervals_path(input_file)
	intervals_data = state_io.load_intervals(intervals_path)
	solved_count = len(intervals_data.get("solved_intervals", {}))
	print(f"  solved intervals: {solved_count}")

	result = (reader, motion_track, scene_transform, seeds_list,
		diagnostics, intervals_data)
	return result


#============================================
def find_trajectory_box(
	frame_index: int,
	seeds_list: list,
	intervals_data: dict,
) -> dict | None:
	"""Find the fused-track box for a given frame from solved intervals.

	Args:
		frame_index: Frame to look up.
		seeds_list: Sorted list of seed dicts.
		intervals_data: Solved intervals data from state_io.

	Returns:
		Dict with cx, cy, w, h keys, or None if frame is not covered.
	"""
	solved = intervals_data.get("solved_intervals", {})
	for key, interval in solved.items():
		start = interval["start_frame"]
		end = interval["end_frame"]
		if start <= frame_index <= end:
			fused = interval.get("fused_track", [])
			# fused_track index maps to frame offset from start
			offset = frame_index - start
			if offset < len(fused):
				box = fused[offset]
				return box
	return None


#============================================
def find_fused_track_for_frame(
	frame_index: int,
	intervals_data: dict,
) -> tuple:
	"""Find the fused track and offset for a frame.

	Args:
		frame_index: Frame to look up.
		intervals_data: Solved intervals data.

	Returns:
		Tuple of (fused_track_list, frame_offset_in_track, start_frame)
		or (None, -1, -1) if not found.
	"""
	solved = intervals_data.get("solved_intervals", {})
	for key, interval in solved.items():
		start = interval["start_frame"]
		end = interval["end_frame"]
		if start <= frame_index <= end:
			fused = interval.get("fused_track", [])
			offset = frame_index - start
			if offset < len(fused):
				return (fused, offset, start)
	return (None, -1, -1)


#============================================
def _gap_has_motion(
	gap_start: int,
	gap_end: int,
	intervals_data: dict,
) -> bool:
	"""Check whether a gap between seeds contains actual runner motion.

	A gap is stationary if the fused track shows negligible displacement
	between start and end (pre-race, standing still, etc.).

	Args:
		gap_start: Start frame of the gap.
		gap_end: End frame of the gap.
		intervals_data: Solved intervals data.

	Returns:
		True if the runner moves significantly in this gap.
	"""
	# check displacement across the gap in the fused track
	solved = intervals_data.get("solved_intervals", {})
	for key, interval in solved.items():
		iv_start = interval["start_frame"]
		iv_end = interval["end_frame"]
		# find an interval that covers this gap
		if iv_start <= gap_start and iv_end >= gap_end:
			fused = interval.get("fused_track", [])
			offset_a = gap_start - iv_start
			offset_b = gap_end - iv_start
			if offset_a < len(fused) and offset_b < len(fused):
				box_a = fused[offset_a]
				box_b = fused[offset_b]
				# compute displacement in pixels
				dx = float(box_b["cx"]) - float(box_a["cx"])
				dy = float(box_b["cy"]) - float(box_a["cy"])
				displacement = (dx**2 + dy**2)**0.5
				# compare to box height as a scale reference
				box_h = float(box_a.get("h", 50))
				# significant motion = displacement > 1x box height
				is_moving = displacement > box_h
				return is_moving
	# if no interval covers the gap, likely has motion
	return True


#============================================
def select_diagnostic_frames(
	seeds_list: list,
	diagnostics: dict,
	num_samples: int,
	frame_count: int,
	intervals_data: dict,
) -> list:
	"""Select frames in two pools: seed frames (truth) and gap frames.

	Seed pool (~half of budget): randomly selected seed frames with known
	annotated boxes. These are positive controls for gate 1.

	Gap pool (~half of budget): midpoints of long moving inter-seed gaps.
	These test whether the signal persists where the solver is weakest.

	Args:
		seeds_list: Sorted list of seed dicts.
		diagnostics: Diagnostics data with interval info.
		num_samples: Total number of frames to select.
		frame_count: Total frames in video.
		intervals_data: Solved intervals data for motion check.

	Returns:
		List of dicts with keys: frame_index, pool ("seed" or "gap"),
		truth_box (dict with cx/cy/w/h for seed frames, None for gap).
	"""
	usable_statuses = {"visible", "partial"}
	usable_seeds = []
	for seed in seeds_list:
		if seed.get("status") in usable_statuses:
			usable_seeds.append(seed)

	# split budget: half for seeds, half for gaps
	seed_budget = num_samples // 2
	gap_budget = num_samples - seed_budget

	frames = []

	# seed pool: randomly select from usable seeds in moving regions
	# skip seeds where the runner is stationary (pre-race, etc.)
	# because background subtraction detects motion, not presence
	if usable_seeds and seed_budget > 0:
		margin = 10
		seed_frame_list = sorted(int(s["frame_index"]) for s in usable_seeds)
		# build set of seeds adjacent to at least one moving gap
		moving_seed_frames = set()
		for i in range(len(seed_frame_list) - 1):
			gap_start = seed_frame_list[i]
			gap_end = seed_frame_list[i + 1]
			if _gap_has_motion(gap_start, gap_end, intervals_data):
				moving_seed_frames.add(gap_start)
				moving_seed_frames.add(gap_end)

		eligible_seeds = [
			s for s in usable_seeds
			if (margin < int(s["frame_index"]) < frame_count - margin
				and int(s["frame_index"]) in moving_seed_frames)
		]
		stationary_seeds = len(usable_seeds) - len(eligible_seeds)
		if stationary_seeds > 0:
			print(f"  frame sampler: skipped {stationary_seeds} "
				f"stationary seed(s) (pre-race, etc.)")

		if len(eligible_seeds) > seed_budget:
			selected_seeds = random.sample(eligible_seeds, seed_budget)
		else:
			selected_seeds = eligible_seeds

		for seed in selected_seeds:
			truth_box = {
				"cx": float(seed["cx"]),
				"cy": float(seed["cy"]),
				"w": float(seed["w"]),
				"h": float(seed["h"]),
			}
			frames.append({
				"frame_index": int(seed["frame_index"]),
				"pool": "seed",
				"truth_box": truth_box,
			})
		# if we got fewer seeds than budget, give extra to gaps
		gap_budget += seed_budget - len(selected_seeds)

	# gap pool: find moving gaps and select midpoints
	seed_frame_list = sorted(int(s["frame_index"]) for s in usable_seeds)
	moving_gaps = []
	stationary_count = 0
	if len(seed_frame_list) >= 2:
		for i in range(len(seed_frame_list) - 1):
			gap_start = seed_frame_list[i]
			gap_end = seed_frame_list[i + 1]
			gap_len = gap_end - gap_start
			if _gap_has_motion(gap_start, gap_end, intervals_data):
				moving_gaps.append((gap_start, gap_end, gap_len))
			else:
				stationary_count += 1

	if stationary_count > 0:
		print(f"  frame sampler: skipped {stationary_count} stationary gap(s)")

	if moving_gaps and gap_budget > 0:
		# distribute across active range with random jitter
		# so repeat runs sample different frames
		earliest = min(g[0] for g in moving_gaps)
		latest = max(g[1] for g in moving_gaps)
		active_range = latest - earliest
		step = max(1, active_range // (gap_budget + 1))
		# jitter up to +/- 25% of step size
		jitter_range = max(1, step // 4)
		gap_frames_selected = []
		for i in range(gap_budget):
			base = earliest + step * (i + 1)
			jitter = random.randint(-jitter_range, jitter_range)
			f = base + jitter
			# clamp to valid range
			f = max(1, min(f, frame_count - 2))
			gap_frames_selected.append(f)

		for f in gap_frames_selected:
			frames.append({
				"frame_index": f,
				"pool": "gap",
				"truth_box": None,
			})

	# sort by frame index
	frames.sort(key=lambda x: x["frame_index"])

	# print summary
	seed_count = sum(1 for f in frames if f["pool"] == "seed")
	gap_count = sum(1 for f in frames if f["pool"] == "gap")
	print(f"  frame sampler: {seed_count} seed frames + {gap_count} gap frames "
		f"= {len(frames)} total")
	return frames


#============================================
def build_warp_matrix(
	scene_transform: scene_coords.SceneTransform,
	frame_n: int,
	frame_n1: int,
	scale_factor: float,
) -> numpy.ndarray:
	"""Build 2x3 affine matrix to warp frame N+1 into frame N's camera position.

	The SceneTransform stores cumulative motion. To warp N+1 into N's space,
	we need the delta transform: how did the camera move from N to N+1.

	The forward transform is: pixel = scene * cum_scale + (cum_dx, cum_dy)
	To warp frame N+1 pixels into frame N pixel space:
	  pixel_N = (pixel_N1 - cum_translation_N1) / cum_scale_N1 * cum_scale_N + cum_translation_N

	This simplifies to an affine: pixel_N = pixel_N1 * (scale_N/scale_N1) + delta_translation

	Args:
		scene_transform: SceneTransform instance.
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
	These must be excluded from flow computation and statistics.

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
def colorize_residual(
	mag: numpy.ndarray,
	fixed_max: float = 30.0,
) -> numpy.ndarray:
	"""Convert residual magnitude to a JET colormap with fixed scale.

	Uses a fixed maximum so background noise stays dark and only genuine
	motion residuals light up. No per-frame normalization.

	Args:
		mag: Residual magnitude array (absolute intensity difference).
		fixed_max: Maximum value mapped to full red (default 30 intensity).

	Returns:
		BGR colorized image (uint8).
	"""
	normalized = numpy.clip(mag / fixed_max * 255, 0, 255).astype(numpy.uint8)
	colored = cv2.applyColorMap(normalized, cv2.COLORMAP_JET)
	return colored


#============================================
def draw_box_on_frame(
	frame: numpy.ndarray,
	cx: float,
	cy: float,
	w: float,
	h: float,
	color: tuple,
	thickness: int = 2,
	label: str = "",
) -> None:
	"""Draw a bounding box rectangle on a frame (in-place).

	Args:
		frame: BGR image to draw on.
		cx: Box center x in pixels.
		cy: Box center y in pixels.
		w: Box width in pixels.
		h: Box height in pixels.
		color: BGR color tuple.
		thickness: Line thickness.
		label: Optional text label above the box.
	"""
	x1 = int(cx - w / 2)
	y1 = int(cy - h / 2)
	x2 = int(cx + w / 2)
	y2 = int(cy + h / 2)
	cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
	if label:
		cv2.putText(frame, label, (x1, y1 - 5),
			cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)


#============================================
def draw_crosshair(
	frame: numpy.ndarray,
	cx: float,
	cy: float,
	size: int = 12,
	color: tuple = (255, 0, 255),
	thickness: int = 2,
) -> None:
	"""Draw a crosshair marker on a frame (in-place).

	Args:
		frame: BGR image to draw on.
		cx: Center x in pixels.
		cy: Center y in pixels.
		size: Half-length of crosshair arms.
		color: BGR color tuple.
		thickness: Line thickness.
	"""
	ix = int(cx)
	iy = int(cy)
	cv2.line(frame, (ix - size, iy), (ix + size, iy), color, thickness)
	cv2.line(frame, (ix, iy - size), (ix, iy + size), color, thickness)


#============================================
def compute_local_tangent(
	fused_track: list,
	frame_offset: int,
	span: int = 5,
) -> tuple:
	"""Compute local tangent and normal vectors from fused track.

	Uses positions at +/-span frames to estimate direction of motion.

	Args:
		fused_track: List of dicts with cx, cy keys.
		frame_offset: Index into fused_track for the target frame.
		span: Number of frames on each side for tangent estimation.

	Returns:
		Tuple of (tangent_x, tangent_y, normal_x, normal_y) as unit vectors.
		Returns (1, 0, 0, 1) if tangent cannot be computed.
	"""
	# clamp to available range
	lo = max(0, frame_offset - span)
	hi = min(len(fused_track) - 1, frame_offset + span)
	if lo >= hi:
		# cannot compute tangent
		return (1.0, 0.0, 0.0, 1.0)

	dx = float(fused_track[hi]["cx"]) - float(fused_track[lo]["cx"])
	dy = float(fused_track[hi]["cy"]) - float(fused_track[lo]["cy"])
	magnitude = (dx**2 + dy**2)**0.5
	if magnitude < 0.001:
		return (1.0, 0.0, 0.0, 1.0)

	# normalize to unit vector
	tx = dx / magnitude
	ty = dy / magnitude
	# normal is perpendicular (rotate 90 degrees)
	nx = -ty
	ny = tx
	return (tx, ty, nx, ny)


#============================================
def corridor_half_width(
	box: dict,
	tier: str,
) -> float:
	"""Compute corridor half-width based on box size and confidence tier.

	Wider corridor for lower confidence (solver is less trustworthy).

	Args:
		box: Dict with w, h keys.
		tier: Confidence tier string ("high", "fair", "low").

	Returns:
		Corridor half-width in pixels.
	"""
	bw = float(box["w"])
	bh = float(box["h"])
	# base radius: whichever is larger of 1.5*width or 0.75*height
	base_radius = max(1.5 * bw, 0.75 * bh)
	# scale by confidence tier
	tier_scale = {"high": 1.0, "fair": 1.5, "low": 2.0}
	multiplier = tier_scale.get(tier, 2.0)
	result = base_radius * multiplier
	return result


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
		tangent: Tuple of (tx, ty, nx, ny) from compute_local_tangent.
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
def filter_blobs_near_seed(
	blobs: list,
	seed_cx: float,
	seed_cy: float,
	max_dist: float,
) -> list:
	"""Filter blobs to those near a seed box center.

	For seed frames, we use a simple distance radius rather than
	a corridor, since we have known truth.

	Args:
		blobs: List of blob dicts.
		seed_cx: Seed center x.
		seed_cy: Seed center y.
		max_dist: Maximum distance from seed center.

	Returns:
		Filtered list with dist_to_seed added.
	"""
	result = []
	for blob in blobs:
		dx = blob["centroid_x"] - seed_cx
		dy = blob["centroid_y"] - seed_cy
		dist = (dx**2 + dy**2)**0.5
		if dist <= max_dist:
			blob_copy = dict(blob)
			blob_copy["dist_to_seed"] = dist
			blob_copy["offset_x"] = dx
			blob_copy["offset_y"] = dy
			result.append(blob_copy)
	return result


#============================================
def track_blobs_across_frames(
	frame_blobs: list,
	max_link_dist: float,
	tangent: tuple = None,
	max_cross_jump: float = None,
) -> list:
	"""Link blobs across consecutive frames into short tracks.

	Simple nearest-neighbor linking with optional cross-track consistency.

	Args:
		frame_blobs: List of lists, one per frame. Each is a list of blob dicts.
		max_link_dist: Maximum centroid distance for linking.
		tangent: Optional (tx, ty, nx, ny) for cross-track consistency check.
		max_cross_jump: Max cross-track change between linked frames.

	Returns:
		List of tracks. Each track is a list of (frame_offset, blob) tuples.
	"""
	if not frame_blobs:
		return []

	# initialize tracks from first frame's blobs
	active_tracks = []
	for blob in frame_blobs[0]:
		active_tracks.append([(0, blob)])

	# link across subsequent frames
	for fi in range(1, len(frame_blobs)):
		current_blobs = frame_blobs[fi]
		used_blobs = set()
		new_tracks = []

		for track in active_tracks:
			last_offset, last_blob = track[-1]
			best_blob = None
			best_dist = max_link_dist

			for bi, blob in enumerate(current_blobs):
				if bi in used_blobs:
					continue
				dx = blob["centroid_x"] - last_blob["centroid_x"]
				dy = blob["centroid_y"] - last_blob["centroid_y"]
				dist = (dx**2 + dy**2)**0.5
				if dist >= best_dist:
					continue
				# optional cross-track consistency check
				if tangent is not None and max_cross_jump is not None:
					_, _, nx, ny = tangent
					cross_prev = last_blob.get("cross_track", 0.0)
					cross_curr = dx * nx + dy * ny + cross_prev
					if abs(cross_curr - cross_prev) > max_cross_jump:
						continue
				best_dist = dist
				best_blob = bi

			if best_blob is not None:
				track.append((fi, current_blobs[best_blob]))
				used_blobs.add(best_blob)
			new_tracks.append(track)

		# start new tracks from unlinked blobs
		for bi, blob in enumerate(current_blobs):
			if bi not in used_blobs:
				new_tracks.append([(fi, blob)])

		active_tracks = new_tracks

	return active_tracks


#============================================
def score_track(
	track: list,
	num_frames: int,
	tangent: tuple,
	ref_x: float,
	ref_y: float,
) -> float:
	"""Score a blob track for candidate selection.

	Cross-track penalty dominates. Along-track offset is weakly penalized.
	Persistence and strength are rewarded.

	Args:
		track: List of (frame_offset, blob) tuples.
		num_frames: Total frames in the window.
		tangent: (tx, ty, nx, ny) unit vectors.
		ref_x: Reference point x (curve or seed center).
		ref_y: Reference point y.

	Returns:
		Score (higher is better).
	"""
	_, _, nx, ny = tangent
	track_len = len(track)
	persistence = track_len / num_frames

	# mean integrated magnitude (normalize by typical blob)
	magnitudes = [b["integrated_mag"] for _, b in track]
	mean_mag = sum(magnitudes) / len(magnitudes) if magnitudes else 0.0
	# normalize: 10000 is a typical strong blob
	norm_strength = min(mean_mag / 10000.0, 1.0)

	# mean absolute cross-track distance
	cross_dists = []
	for _, blob in track:
		dx = blob["centroid_x"] - ref_x
		dy = blob["centroid_y"] - ref_y
		cross = abs(dx * nx + dy * ny)
		cross_dists.append(cross)
	mean_cross = sum(cross_dists) / len(cross_dists) if cross_dists else 0.0
	# normalize: corridor width is typically 50-200px
	norm_cross = min(mean_cross / 200.0, 1.0)

	# mean absolute along-track distance (soft penalty for far-away blobs)
	tx, ty, _, _ = tangent
	along_dists = []
	for _, blob in track:
		dx = blob["centroid_x"] - ref_x
		dy = blob["centroid_y"] - ref_y
		along = abs(dx * tx + dy * ty)
		along_dists.append(along)
	mean_along = sum(along_dists) / len(along_dists) if along_dists else 0.0
	# normalize: 500px along-track is clearly wrong
	norm_along = min(mean_along / 500.0, 1.0)

	# displacement variance (smoothness)
	if track_len >= 2:
		displacements = []
		for i in range(1, track_len):
			_, b_prev = track[i - 1]
			_, b_curr = track[i]
			dx = b_curr["centroid_x"] - b_prev["centroid_x"]
			dy = b_curr["centroid_y"] - b_prev["centroid_y"]
			displacements.append((dx**2 + dy**2)**0.5)
		mean_disp = sum(displacements) / len(displacements)
		var_disp = sum((d - mean_disp)**2 for d in displacements) / len(displacements)
		# normalize: variance of 100 is bad
		norm_smooth = min(var_disp / 100.0, 1.0)
	else:
		norm_smooth = 1.0

	score = (WEIGHT_PERSISTENCE * persistence
		+ WEIGHT_STRENGTH * norm_strength
		- WEIGHT_CROSS_TRACK * norm_cross
		- WEIGHT_ALONG_TRACK * norm_along
		- WEIGHT_SMOOTHNESS * norm_smooth)
	return score


#============================================
def select_best_track(
	tracks: list,
	num_frames: int,
	tangent: tuple,
	ref_x: float,
	ref_y: float,
) -> tuple:
	"""Select the best blob track from candidates.

	Args:
		tracks: List of tracks from track_blobs_across_frames.
		num_frames: Total frames in the window.
		tangent: (tx, ty, nx, ny) unit vectors.
		ref_x: Reference point x.
		ref_y: Reference point y.

	Returns:
		Tuple of (best_track, best_score) or (None, 0.0) if no candidates.
	"""
	if not tracks:
		return (None, 0.0)

	best_track = None
	best_score = -999.0
	for track in tracks:
		s = score_track(track, num_frames, tangent, ref_x, ref_y)
		if s > best_score:
			best_score = s
			best_track = track

	return (best_track, best_score)


#============================================
def find_interval_info(
	frame_index: int,
	diagnostics: dict,
) -> tuple:
	"""Find interval ID and confidence tier for a frame.

	Args:
		frame_index: Frame to look up.
		diagnostics: Diagnostics data.

	Returns:
		Tuple of (interval_index, confidence_tier) or (-1, "unknown").
	"""
	for i, iv in enumerate(diagnostics.get("intervals", [])):
		if not isinstance(iv, dict):
			continue
		start = iv.get("start_frame", 0)
		end = iv.get("end_frame", 0)
		if start <= frame_index <= end:
			score = iv.get("interval_score", {})
			tier = score.get("confidence_tier", "unknown")
			return (i, tier)
	return (-1, "unknown")


#============================================
def compute_multiframe_flow(
	reader: video_io.VideoReader,
	frame_index: int,
	scene_transform: scene_coords.SceneTransform,
	scale_factor: float,
	half_window: int = 4,
) -> tuple:
	"""Detect motion at frame N using aligned temporal background subtraction.

	Warps neighboring frames (N-k through N+k, k != 0) into frame N's
	camera position, builds a robust median background from the aligned
	stack, then subtracts the background from frame N to reveal moving
	objects.

	Also computes a raw (uncompensated) single-pair residual as null baseline.

	Args:
		reader: VideoReader instance.
		frame_index: Center frame index N.
		scene_transform: SceneTransform for camera compensation.
		scale_factor: Downsample factor.
		half_window: Offsets on each side (default 4 = 9-frame window).

	Returns:
		Tuple of (residual_mag, raw_mag_single, validity_mask, display_frame).
		display_frame is frame N (for PNG overlay).
	"""
	# read center frame
	center_frame = reader.read_frame(frame_index)
	if center_frame is None:
		return (None, None, None, None)

	h_orig, w_orig = center_frame.shape[:2]
	if scale_factor < 1.0:
		new_w = int(w_orig * scale_factor)
		new_h = int(h_orig * scale_factor)
		center_resized = cv2.resize(center_frame, (new_w, new_h))
	else:
		new_w = w_orig
		new_h = h_orig
		center_resized = center_frame.copy()

	# convert center frame to grayscale float for residual computation
	gray_center = cv2.cvtColor(center_resized, cv2.COLOR_BGR2GRAY)
	center_float = gray_center.astype(numpy.float32)

	# collect aligned neighbor frames into a stack for median computation
	aligned_stack = []
	for k in range(-half_window, half_window + 1):
		if k == 0:
			continue
		fi_other = frame_index + k
		if fi_other < 0 or fi_other >= reader.frame_count:
			continue

		other_frame = reader.read_frame(fi_other)
		if other_frame is None:
			continue

		if scale_factor < 1.0:
			other_frame = cv2.resize(other_frame, (new_w, new_h))

		# warp into frame N's camera position
		warp_mat = build_warp_matrix(
			scene_transform, frame_index, fi_other, scale_factor,
		)
		warped = cv2.warpAffine(other_frame, warp_mat, (new_w, new_h))

		# validity mask for warped regions
		pair_validity = compute_validity_mask(warped)

		# convert to grayscale float
		gray_warped = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
		warped_float = gray_warped.astype(numpy.float32)

		# set invalid pixels to NaN so median ignores them
		warped_float[pair_validity == 0] = numpy.nan

		aligned_stack.append(warped_float)

	if len(aligned_stack) < 2:
		return (None, None, None, None)

	# build median background from aligned stack
	stack_array = numpy.stack(aligned_stack, axis=0)
	# suppress All-NaN slice warning; edge pixels may have no valid frames
	with warnings.catch_warnings():
		warnings.simplefilter("ignore", RuntimeWarning)
		median_background = numpy.nanmedian(stack_array, axis=0).astype(numpy.float32)

	# combined validity mask: valid where at least 2 frames contributed
	valid_count = numpy.sum(~numpy.isnan(stack_array), axis=0)
	validity_mask = (valid_count >= 2).astype(numpy.uint8) * 255

	# compute residual: absolute difference between frame N and median background
	residual = numpy.abs(center_float - median_background)
	residual[validity_mask == 0] = 0.0

	# compute raw single-pair residual as null baseline
	frame_n1 = reader.read_frame(frame_index + 1)
	raw_mag = numpy.zeros((new_h, new_w), dtype=numpy.float32)
	if frame_n1 is not None:
		if scale_factor < 1.0:
			frame_n1 = cv2.resize(frame_n1, (new_w, new_h))
		gray_n1 = cv2.cvtColor(frame_n1, cv2.COLOR_BGR2GRAY)
		raw_mag = numpy.abs(
			center_float - gray_n1.astype(numpy.float32)
		)

	return (residual, raw_mag, validity_mask, center_resized)


#============================================
def compute_frame_statistics(
	frame_info: dict,
	reader: video_io.VideoReader,
	scene_transform: scene_coords.SceneTransform,
	intervals_data: dict,
	diagnostics: dict,
	seeds_list: list,
	threshold: float,
	scale_factor: float,
	half_window: int,
) -> dict:
	"""Compute per-frame motion statistics using temporal blob tracking.

	For seed frames: finds blobs near annotated truth box.
	For gap frames: finds blobs within curve corridor, tracks across 5 frames.

	Args:
		frame_info: Dict with frame_index, pool, truth_box.
		reader: VideoReader instance.
		scene_transform: SceneTransform.
		intervals_data: Solved intervals data.
		diagnostics: Diagnostics data.
		seeds_list: List of seed dicts.
		threshold: Motion threshold.
		scale_factor: Downsample factor.
		half_window: Half-window for background estimation.

	Returns:
		Dict of statistics for this frame.
	"""
	frame_index = frame_info["frame_index"]
	pool = frame_info["pool"]
	truth_box = frame_info["truth_box"]
	stats = {"frame_index": frame_index, "pool": pool}

	# find interval info
	iv_idx, tier = find_interval_info(frame_index, diagnostics)
	stats["interval"] = iv_idx
	stats["tier"] = tier

	# get fused track and reference box
	fused_track, fused_offset, fused_start = find_fused_track_for_frame(
		frame_index, intervals_data
	)
	ref_box = find_trajectory_box(frame_index, seeds_list, intervals_data)

	# compute tangent from fused track
	if fused_track is not None:
		tangent = compute_local_tangent(fused_track, fused_offset)
	else:
		tangent = (1.0, 0.0, 0.0, 1.0)
	stats["tangent"] = tangent

	# determine reference point and corridor
	if pool == "seed" and truth_box is not None:
		# seed frame: use annotated box as truth
		ref_cx = truth_box["cx"] * scale_factor
		ref_cy = truth_box["cy"] * scale_factor
		ref_w = truth_box["w"] * scale_factor
		ref_h = truth_box["h"] * scale_factor
		# search radius: 2x box height
		search_radius = 2.0 * ref_h
		stats["ref_cx"] = ref_cx
		stats["ref_cy"] = ref_cy
		stats["ref_w"] = ref_w
		stats["ref_h"] = ref_h
		stats["corridor_radius"] = search_radius
	elif ref_box is not None:
		# gap frame: use fused track position with corridor
		ref_cx = float(ref_box["cx"]) * scale_factor
		ref_cy = float(ref_box["cy"]) * scale_factor
		ref_w = float(ref_box["w"]) * scale_factor
		ref_h = float(ref_box["h"]) * scale_factor
		c_radius = corridor_half_width(ref_box, tier) * scale_factor
		stats["ref_cx"] = ref_cx
		stats["ref_cy"] = ref_cy
		stats["ref_w"] = ref_w
		stats["ref_h"] = ref_h
		stats["corridor_radius"] = c_radius
	else:
		stats["no_reference"] = True
		return stats

	# compute residual for center frame
	result = compute_multiframe_flow(
		reader, frame_index, scene_transform, scale_factor, half_window,
	)
	comp_mag, raw_mag, validity_mask, display_frame = result
	if comp_mag is None:
		stats["no_data"] = True
		return stats

	stats["display_frame"] = display_frame
	stats["comp_mag"] = comp_mag
	stats["raw_mag"] = raw_mag
	stats["validity_mask"] = validity_mask

	# extract blobs from center frame
	center_blobs = extract_frame_blobs(comp_mag, validity_mask, threshold)
	stats["num_blobs"] = len(center_blobs)

	# compute residuals for neighboring frames (for temporal tracking)
	# use 5-frame window: N-2 through N+2
	tracking_window = 5
	tracking_half = tracking_window // 2
	frame_blobs_list = []

	for k in range(-tracking_half, tracking_half + 1):
		fi = frame_index + k
		if fi < 0 or fi >= reader.frame_count:
			frame_blobs_list.append([])
			continue
		if k == 0:
			# use already-computed center frame blobs
			if pool == "seed" and truth_box is not None:
				filtered = filter_blobs_near_seed(
					center_blobs, ref_cx, ref_cy, search_radius
				)
			else:
				filtered = filter_blobs_to_corridor(
					center_blobs, ref_cx, ref_cy, tangent, stats["corridor_radius"]
				)
			frame_blobs_list.append(filtered)
			continue

		# compute residual for neighbor frame
		neighbor_result = compute_multiframe_flow(
			reader, fi, scene_transform, scale_factor, half_window,
		)
		n_mag, _, n_validity, _ = neighbor_result
		if n_mag is None:
			frame_blobs_list.append([])
			continue

		# get reference point for this frame (may shift slightly)
		neighbor_box = find_trajectory_box(fi, seeds_list, intervals_data)
		if neighbor_box is not None:
			nb_cx = float(neighbor_box["cx"]) * scale_factor
			nb_cy = float(neighbor_box["cy"]) * scale_factor
		else:
			nb_cx = ref_cx
			nb_cy = ref_cy

		n_blobs = extract_frame_blobs(n_mag, n_validity, threshold)
		if pool == "seed" and truth_box is not None:
			filtered = filter_blobs_near_seed(
				n_blobs, nb_cx, nb_cy, search_radius
			)
		else:
			filtered = filter_blobs_to_corridor(
				n_blobs, nb_cx, nb_cy, tangent, stats["corridor_radius"]
			)
		frame_blobs_list.append(filtered)

	# track blobs across frames
	max_link = ref_h if ref_h > 0 else 50.0
	cross_jump = stats["corridor_radius"] * 0.5 if pool == "gap" else None
	tracks = track_blobs_across_frames(
		frame_blobs_list, max_link, tangent if pool == "gap" else None, cross_jump
	)

	# select best track
	best_track, best_score = select_best_track(
		tracks, tracking_window, tangent, ref_cx, ref_cy
	)

	stats["best_track"] = best_track
	stats["track_score"] = best_score
	stats["track_length"] = len(best_track) if best_track else 0

	# extract candidate position at center frame (offset 2 in 5-frame window)
	center_offset = tracking_half
	candidate_blob = None
	if best_track is not None:
		for fo, blob in best_track:
			if fo == center_offset:
				candidate_blob = blob
				break
		# fall back to closest frame if center not in track
		if candidate_blob is None and best_track:
			closest = min(best_track, key=lambda x: abs(x[0] - center_offset))
			candidate_blob = closest[1]

	if candidate_blob is not None:
		stats["candidate_x"] = candidate_blob["centroid_x"]
		stats["candidate_y"] = candidate_blob["centroid_y"]
		dx = candidate_blob["centroid_x"] - ref_cx
		dy = candidate_blob["centroid_y"] - ref_cy
		stats["blob_dist"] = (dx**2 + dy**2)**0.5
		stats["candidate_strength"] = candidate_blob["integrated_mag"]

		# cross-track and along-track decomposition
		tx, ty, nx, ny = tangent
		stats["cross_track"] = dx * nx + dy * ny
		stats["along_track"] = dx * tx + dy * ty

		# comp vs raw at candidate location
		cand_ix = int(candidate_blob["centroid_x"])
		cand_iy = int(candidate_blob["centroid_y"])
		h, w = comp_mag.shape[:2]
		# sample a small region around candidate
		r = 10
		cy1 = max(0, cand_iy - r)
		cy2 = min(h, cand_iy + r)
		cx1 = max(0, cand_ix - r)
		cx2 = min(w, cand_ix + r)
		comp_patch = comp_mag[cy1:cy2, cx1:cx2]
		raw_patch = raw_mag[cy1:cy2, cx1:cx2]
		comp_val = float(numpy.median(comp_patch)) if comp_patch.size > 0 else 0.0
		raw_val = float(numpy.median(raw_patch)) if raw_patch.size > 0 else 0.0
		if raw_val > 0.01:
			stats["comp_vs_raw"] = comp_val / raw_val
		else:
			stats["comp_vs_raw"] = 0.0

		# check seed-box overlap for seed frames
		if pool == "seed" and truth_box is not None:
			# does blob centroid fall within expanded seed box (1.5x)?
			expand = 1.5
			in_box = (abs(dx) <= ref_w * expand / 2 and abs(dy) <= ref_h * expand / 2)
			stats["overlaps_seed"] = in_box
	else:
		# no candidate found
		stats["candidate_x"] = None
		stats["candidate_y"] = None
		stats["blob_dist"] = None
		stats["candidate_strength"] = None
		stats["cross_track"] = None
		stats["along_track"] = None
		stats["comp_vs_raw"] = None
		if pool == "seed":
			stats["overlaps_seed"] = False

	# count corridor candidates at center frame
	stats["num_corridor_candidates"] = len(frame_blobs_list[center_offset])

	# review candidate: frames where motion cue is strong enough to suggest re-seeding
	trk_len = stats.get("track_length", 0)
	cross_val = stats.get("cross_track")
	along_val = stats.get("along_track")
	eligible = (
		trk_len >= REVIEW_MIN_TRACK_LENGTH
		and cross_val is not None
		and abs(cross_val) <= REVIEW_MAX_CROSS_TRACK
		and along_val is not None
		and abs(along_val) <= REVIEW_MAX_ALONG_TRACK
		and best_score >= REVIEW_MIN_SCORE
	)
	stats["review_candidate"] = eligible

	return stats


#============================================
def get_fused_track_positions(
	fused_track: list,
	fused_offset: int,
	scale_factor: float,
	num_points: int = 25,
) -> list:
	"""Get fused track positions around a frame for curve drawing.

	Args:
		fused_track: List of dicts with cx, cy keys.
		fused_offset: Index of the target frame in fused_track.
		scale_factor: Scale factor for coordinates.
		num_points: Number of points on each side.

	Returns:
		List of (x, y) tuples in scaled pixel coords.
	"""
	points = []
	lo = max(0, fused_offset - num_points)
	hi = min(len(fused_track), fused_offset + num_points + 1)
	for i in range(lo, hi):
		px = float(fused_track[i]["cx"]) * scale_factor
		py = float(fused_track[i]["cy"]) * scale_factor
		points.append((px, py))
	return points


#============================================
def draw_corridor_on_frame(
	frame: numpy.ndarray,
	curve_points: list,
	tangent: tuple,
	corridor_radius: float,
	color: tuple = (0, 200, 200),
) -> None:
	"""Draw a curve segment with corridor band on a frame (in-place).

	Args:
		frame: BGR image to draw on.
		curve_points: List of (x, y) along the track.
		tangent: (tx, ty, nx, ny) unit vectors.
		corridor_radius: Half-width of corridor.
		color: BGR color for the curve line.
	"""
	_, _, nx, ny = tangent
	# draw curve line
	if len(curve_points) >= 2:
		pts = numpy.array([(int(x), int(y)) for x, y in curve_points], dtype=numpy.int32)
		cv2.polylines(frame, [pts], False, color, 2)

	# draw corridor edges as thin lines offset from curve
	for sign in (-1, 1):
		offset_x = nx * corridor_radius * sign
		offset_y = ny * corridor_radius * sign
		offset_pts = [
			(int(x + offset_x), int(y + offset_y))
			for x, y in curve_points
		]
		if len(offset_pts) >= 2:
			pts = numpy.array(offset_pts, dtype=numpy.int32)
			cv2.polylines(frame, [pts], False, color, 1)


#============================================
def draw_track_on_frame(
	frame: numpy.ndarray,
	track: list,
	color: tuple = (255, 0, 255),
) -> None:
	"""Draw a blob track as connected dots on a frame (in-place).

	Args:
		frame: BGR image to draw on.
		track: List of (frame_offset, blob) tuples.
		color: BGR color.
	"""
	# draw dots at each blob centroid
	points = []
	for _, blob in track:
		ix = int(blob["centroid_x"])
		iy = int(blob["centroid_y"])
		cv2.circle(frame, (ix, iy), 4, color, -1)
		points.append((ix, iy))

	# connect with lines
	for i in range(1, len(points)):
		cv2.line(frame, points[i - 1], points[i], color, 1)


#============================================
def crop_square_around_point(
	image: numpy.ndarray,
	cx: float,
	cy: float,
	box_h: float,
	multiplier: float = 8.0,
) -> numpy.ndarray:
	"""Crop a square region around a point, sized relative to the box.

	The crop side length is multiplier * box_h, centered on (cx, cy).
	Pads with black if the crop extends beyond image boundaries.

	Args:
		image: Source image (BGR or grayscale).
		cx: Center x of crop.
		cy: Center y of crop.
		box_h: Reference box height for sizing.
		multiplier: Crop side = multiplier * box_h.

	Returns:
		Square cropped image.
	"""
	half_side = int(multiplier * box_h / 2)
	if half_side < 20:
		half_side = 20

	h, w = image.shape[:2]
	# compute crop bounds (may extend outside image)
	x1 = int(cx) - half_side
	y1 = int(cy) - half_side
	x2 = int(cx) + half_side
	y2 = int(cy) + half_side

	# clamp to image bounds and compute padding
	src_x1 = max(0, x1)
	src_y1 = max(0, y1)
	src_x2 = min(w, x2)
	src_y2 = min(h, y2)

	# extract the valid region
	crop = image[src_y1:src_y2, src_x1:src_x2]

	# pad if needed
	pad_left = src_x1 - x1
	pad_top = src_y1 - y1
	pad_right = x2 - src_x2
	pad_bottom = y2 - src_y2
	if pad_left > 0 or pad_top > 0 or pad_right > 0 or pad_bottom > 0:
		crop = cv2.copyMakeBorder(crop, pad_top, pad_bottom, pad_left, pad_right,
			cv2.BORDER_CONSTANT, value=0)

	return crop


#============================================
def render_diagnostic_png(
	stats: dict,
	seeds_list: list,
	intervals_data: dict,
	threshold: float,
	scale_factor: float,
	half_window: int,
	output_dir: str,
) -> None:
	"""Render a 2x2 diagnostic PNG for one frame.

	Args:
		stats: Statistics dict from compute_frame_statistics.
		seeds_list: List of seed dicts.
		intervals_data: Solved intervals data.
		threshold: Motion threshold.
		scale_factor: Downsample factor.
		half_window: Half-window size.
		output_dir: Output directory.
	"""
	frame_index = stats["frame_index"]
	pool = stats["pool"]
	display_frame = stats.get("display_frame")
	comp_mag = stats.get("comp_mag")

	if display_frame is None or comp_mag is None:
		return

	# top-left: original frame with annotations
	panel_tl = display_frame.copy()
	tier = stats.get("tier", "?")
	iv_idx = stats.get("interval", -1)
	label = f"frame {frame_index}  iv:{iv_idx}  {tier}  [{pool}]"
	cv2.putText(panel_tl, label, (10, 25),
		cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

	ref_cx = stats.get("ref_cx")
	ref_cy = stats.get("ref_cy")

	if pool == "seed":
		# draw seed truth box in cyan
		ref_w = stats.get("ref_w", 0)
		ref_h = stats.get("ref_h", 0)
		if ref_cx is not None:
			draw_box_on_frame(panel_tl, ref_cx, ref_cy, ref_w, ref_h,
				(255, 255, 0), 2, "seed truth")
	else:
		# draw curve segment and corridor for gap frames
		fused_track, fused_offset, fused_start = find_fused_track_for_frame(
			frame_index, intervals_data
		)
		if fused_track is not None:
			curve_pts = get_fused_track_positions(
				fused_track, fused_offset, scale_factor
			)
			tangent = stats.get("tangent", (1, 0, 0, 1))
			c_radius = stats.get("corridor_radius", 50)
			draw_corridor_on_frame(panel_tl, curve_pts, tangent, c_radius)
		# draw predicted torso box in green so user can see solver error
		ref_w = stats.get("ref_w", 0)
		ref_h = stats.get("ref_h", 0)
		if ref_cx is not None:
			draw_box_on_frame(panel_tl, ref_cx, ref_cy, ref_w, ref_h,
				(0, 255, 0), 2, "predicted")

	# draw candidate crosshair and line from reference to candidate
	cand_x = stats.get("candidate_x")
	cand_y = stats.get("candidate_y")
	if cand_x is not None:
		draw_crosshair(panel_tl, cand_x, cand_y)
		# draw line from reference point to candidate to show offset
		if ref_cx is not None:
			cv2.line(panel_tl, (int(ref_cx), int(ref_cy)),
				(int(cand_x), int(cand_y)), (255, 0, 255), 1)

	# diagnostic text overlay on top-left panel
	text_y = 50
	blob_d = stats.get("blob_dist")
	trk_len = stats.get("track_length", 0)
	cross = stats.get("cross_track")
	along = stats.get("along_track")
	cvr = stats.get("comp_vs_raw")
	strength = stats.get("candidate_strength")
	n_cand = stats.get("num_corridor_candidates", 0)
	score = stats.get("track_score", 0.0)

	if cand_x is not None:
		dist_str = f"dist={blob_d:.0f}px" if blob_d is not None else "dist=?"
		cross_str = f"cross={cross:.0f}" if cross is not None else ""
		along_str = f"along={along:.0f}" if along is not None else ""
		cv2.putText(panel_tl, f"candidate: {dist_str}  {cross_str}  {along_str}",
			(10, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 200, 255), 1)
		text_y += 18
		cvr_str = f"c/r={cvr:.1f}" if cvr is not None else "c/r=?"
		str_str = f"mag={strength:.0f}" if strength is not None else ""
		cv2.putText(panel_tl,
			f"track={trk_len}/5  {cvr_str}  {str_str}  score={score:.2f}",
			(10, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 200, 255), 1)
		text_y += 18
		cv2.putText(panel_tl, f"corridor candidates={n_cand}  total blobs={stats.get('num_blobs', 0)}",
			(10, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 200, 255), 1)
	else:
		cv2.putText(panel_tl, "NO CANDIDATE FOUND",
			(10, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
		text_y += 20
		cv2.putText(panel_tl, f"corridor candidates={n_cand}  total blobs={stats.get('num_blobs', 0)}",
			(10, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
	if pool == "seed":
		overlaps = stats.get("overlaps_seed", False)
		overlap_str = "YES" if overlaps else "NO"
		text_y += 18
		overlap_color = (0, 255, 0) if overlaps else (0, 0, 255)
		cv2.putText(panel_tl, f"overlaps seed box: {overlap_str}",
			(10, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, overlap_color, 1)

	# top-right: compensated residual heatmap (full frame)
	panel_tr = colorize_residual(comp_mag)
	num_bg_frames = 2 * half_window
	cv2.putText(panel_tr, f"compensated residual ({num_bg_frames} frames)", (10, 25),
		cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
	# draw corridor/boxes on residual panel
	if pool == "gap":
		fused_track, fused_offset, fused_start = find_fused_track_for_frame(
			frame_index, intervals_data
		)
		if fused_track is not None:
			curve_pts = get_fused_track_positions(
				fused_track, fused_offset, scale_factor
			)
			tangent = stats.get("tangent", (1, 0, 0, 1))
			c_radius = stats.get("corridor_radius", 50)
			draw_corridor_on_frame(panel_tr, curve_pts, tangent, c_radius,
				(200, 200, 200))
	elif ref_cx is not None:
		ref_w = stats.get("ref_w", 0)
		ref_h = stats.get("ref_h", 0)
		draw_box_on_frame(panel_tr, ref_cx, ref_cy, ref_w, ref_h,
			(255, 255, 255), 2)
	# draw best track on residual
	best_track = stats.get("best_track")
	if best_track is not None:
		draw_track_on_frame(panel_tr, best_track)
	if cand_x is not None:
		draw_crosshair(panel_tr, cand_x, cand_y, color=(0, 255, 0))
		if blob_d is not None:
			cv2.putText(panel_tr, f"{blob_d:.0f}px",
				(int(cand_x) + 15, int(cand_y) - 5),
				cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
	# track info at bottom
	trk_info = f"track len={trk_len}/5  score={score:.2f}  cand={n_cand}"
	cv2.putText(panel_tr, trk_info, (10, panel_tr.shape[0] - 10),
		cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

	# bottom-left: thresholded motion mask (full frame)
	motion_mask = (comp_mag > threshold).astype(numpy.uint8) * 255
	panel_bl = cv2.cvtColor(motion_mask, cv2.COLOR_GRAY2BGR)
	cv2.putText(panel_bl, f"motion mask (t={threshold:.1f})", (10, 25),
		cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
	if pool == "gap":
		fused_track, fused_offset, fused_start = find_fused_track_for_frame(
			frame_index, intervals_data
		)
		if fused_track is not None:
			curve_pts = get_fused_track_positions(
				fused_track, fused_offset, scale_factor
			)
			tangent = stats.get("tangent", (1, 0, 0, 1))
			c_radius = stats.get("corridor_radius", 50)
			draw_corridor_on_frame(panel_bl, curve_pts, tangent, c_radius,
				(0, 200, 200))
	elif ref_cx is not None:
		ref_w = stats.get("ref_w", 0)
		ref_h = stats.get("ref_h", 0)
		draw_box_on_frame(panel_bl, ref_cx, ref_cy, ref_w, ref_h,
			(0, 255, 0), 2)
	if cand_x is not None:
		draw_crosshair(panel_bl, cand_x, cand_y)

	# bottom-right: original frame without annotations (clean reference)
	panel_br = display_frame.copy()
	cv2.putText(panel_br, f"original frame {frame_index}", (10, 25),
		cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

	# assemble full-frame 2x2 grid
	top_row = numpy.hstack([panel_tl, panel_tr])
	bottom_row = numpy.hstack([panel_bl, panel_br])
	grid_full = numpy.vstack([top_row, bottom_row])

	# save full-frame PNG
	full_path = os.path.join(output_dir, f"{pool}_frame_{frame_index:06d}.png")
	cv2.imwrite(full_path, grid_full)

	# build cropped 2x2 grid centered on reference point (8x torso box, square)
	ref_h_val = stats.get("ref_h", 50)
	# use candidate position as crop center if available, else reference
	crop_cx = cand_x if cand_x is not None else ref_cx
	crop_cy = cand_y if cand_y is not None else ref_cy
	if crop_cx is not None and ref_h_val > 0:
		crop_tl = crop_square_around_point(panel_tl, crop_cx, crop_cy, ref_h_val)
		crop_tr = crop_square_around_point(panel_tr, crop_cx, crop_cy, ref_h_val)
		crop_bl = crop_square_around_point(panel_bl, crop_cx, crop_cy, ref_h_val)
		crop_br = crop_square_around_point(panel_br, crop_cx, crop_cy, ref_h_val)
		# resize all crops to the same size for grid assembly
		crop_size = crop_tl.shape[0]
		crop_tr = cv2.resize(crop_tr, (crop_size, crop_size))
		crop_bl = cv2.resize(crop_bl, (crop_size, crop_size))
		crop_br = cv2.resize(crop_br, (crop_size, crop_size))
		crop_top = numpy.hstack([crop_tl, crop_tr])
		crop_bot = numpy.hstack([crop_bl, crop_br])
		grid_crop = numpy.vstack([crop_top, crop_bot])
		crop_path = os.path.join(output_dir,
			f"{pool}_frame_{frame_index:06d}_crop.png")
		cv2.imwrite(crop_path, grid_crop)

	# save motion mask for montage
	stats["motion_mask"] = motion_mask
	stats["scaled_box"] = {"cx": ref_cx, "cy": ref_cy,
		"w": stats.get("ref_w", 0), "h": stats.get("ref_h", 0)}


#============================================
def build_montage(
	all_stats: list,
	output_dir: str,
) -> None:
	"""Build a summary montage of all motion masks.

	Args:
		all_stats: List of per-frame stats dicts (with motion_mask key).
		output_dir: Output directory.
	"""
	panels = []
	for stats in all_stats:
		mask = stats.get("motion_mask")
		if mask is None:
			continue
		# resize to a consistent height for the montage
		target_h = 200
		ratio = target_h / mask.shape[0]
		target_w = int(mask.shape[1] * ratio)
		resized = cv2.resize(mask, (target_w, target_h))
		panel = cv2.cvtColor(resized, cv2.COLOR_GRAY2BGR)
		# label
		frame_idx = stats.get("frame_index", 0)
		pool = stats.get("pool", "?")
		tier = stats.get("tier", "?")
		label = f"f{frame_idx} {pool} {tier}"
		cv2.putText(panel, label, (3, 15),
			cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 0), 1)
		# draw candidate crosshair if available
		cand_x = stats.get("candidate_x")
		cand_y = stats.get("candidate_y")
		if cand_x is not None:
			draw_crosshair(panel, cand_x * ratio, cand_y * ratio,
				size=6, color=(255, 0, 255))
		panels.append(panel)

	if not panels:
		print("  no panels for montage")
		return

	montage = numpy.hstack(panels)
	out_path = os.path.join(output_dir, "montage.png")
	cv2.imwrite(out_path, montage)
	print(f"  montage saved: {out_path}")


#============================================
def find_weakest_gap_midpoint(
	seeds_list: list,
	diagnostics: dict,
	frame_count: int,
) -> int:
	"""Find the midpoint of the longest low-confidence inter-seed gap.

	Args:
		seeds_list: Sorted list of seed dicts.
		diagnostics: Diagnostics data.
		frame_count: Total frames in video.

	Returns:
		Frame index at the midpoint of the weakest gap.
	"""
	usable_statuses = {"visible", "partial"}
	seed_frames = []
	for seed in seeds_list:
		if seed.get("status") in usable_statuses:
			seed_frames.append(int(seed["frame_index"]))
	seed_frames.sort()

	if len(seed_frames) < 2:
		return frame_count // 2

	longest_gap = 0
	longest_mid = frame_count // 2
	for i in range(len(seed_frames) - 1):
		gap = seed_frames[i + 1] - seed_frames[i]
		if gap > longest_gap:
			longest_gap = gap
			longest_mid = (seed_frames[i] + seed_frames[i + 1]) // 2

	return longest_mid


#============================================
def render_flow_video(
	reader: video_io.VideoReader,
	scene_transform: scene_coords.SceneTransform,
	seeds_list: list,
	intervals_data: dict,
	diagnostics: dict,
	midpoint: int,
	threshold: float,
	scale_factor: float,
	output_dir: str,
	half_window: int = 2,
	num_video_frames: int = 100,
) -> None:
	"""Render a short video of consecutive residual flow heatmaps.

	Args:
		reader: VideoReader instance.
		scene_transform: SceneTransform for camera compensation.
		seeds_list: List of seed dicts.
		intervals_data: Solved intervals data.
		diagnostics: Diagnostics data.
		midpoint: Center frame for the video segment.
		threshold: Motion threshold.
		scale_factor: Downsample factor.
		output_dir: Output directory.
		half_window: Frames on each side for averaging.
		num_video_frames: Number of frames to render.
	"""
	half = num_video_frames // 2
	start = max(half_window, midpoint - half)
	end = min(reader.frame_count - half_window - 1, midpoint + half)

	out_w = int(reader.width * scale_factor)
	out_h = int(reader.height * scale_factor)

	out_path = os.path.join(output_dir, "residual_flow.mp4")
	fourcc = cv2.VideoWriter_fourcc(*"mp4v")
	writer = cv2.VideoWriter(out_path, fourcc, reader.fps, (out_w, out_h))

	print(f"  rendering flow video: frames {start}-{end} "
		f"(window={2*half_window+1}) -> {out_path}")
	for fi in range(start, end):
		result = compute_multiframe_flow(
			reader, fi, scene_transform, scale_factor, half_window,
		)
		mag, raw_mag, validity_mask, display_frame = result
		if mag is None:
			continue

		colored = colorize_residual(mag)

		# overlay predicted box
		box = find_trajectory_box(fi, seeds_list, intervals_data)
		if box is not None:
			bx = float(box["cx"]) * scale_factor
			by = float(box["cy"]) * scale_factor
			bw = float(box["w"]) * scale_factor
			bh = float(box["h"]) * scale_factor
			conf = box.get("conf", 0.0)
			if conf is None:
				conf = 0.0
			draw_box_on_frame(colored, bx, by, bw, bh, (255, 255, 255), 2,
				f"c={conf:.2f}")

		cv2.putText(colored, f"frame {fi}", (10, 25),
			cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

		writer.write(colored)

	writer.release()
	print(f"  flow video saved: {out_path}")


#============================================
def print_statistics_table(all_stats: list) -> None:
	"""Print per-frame statistics and two-gate summary to stdout.

	Args:
		all_stats: List of per-frame stats dicts.
	"""
	print()
	line_w = 130
	print("=" * line_w)
	print(f"{'frame':>7} {'pool':>5} {'tier':>6} "
		f"{'trk_ln':>6} {'blob_d':>7} {'cross':>7} {'along':>7} "
		f"{'strnth':>7} {'c/r':>5} {'#cand':>5} {'#blob':>5} "
		f"{'score':>6} {'revw?':>5}")
	print("-" * line_w)

	for stats in all_stats:
		frame_idx = stats["frame_index"]
		pool = stats["pool"]
		tier = stats.get("tier", "?")

		trk_len = stats.get("track_length", 0)
		blob_d = stats.get("blob_dist")
		cross = stats.get("cross_track")
		along = stats.get("along_track")
		strength = stats.get("candidate_strength")
		cvr = stats.get("comp_vs_raw")
		n_cand = stats.get("num_corridor_candidates", 0)
		n_blob = stats.get("num_blobs", 0)
		score = stats.get("track_score", 0.0)
		eligible = stats.get("review_candidate", False)

		# format nullable values
		bd_s = f"{blob_d:>7.1f}" if blob_d is not None else "   none"
		cr_s = f"{cross:>7.1f}" if cross is not None else "   none"
		al_s = f"{along:>7.1f}" if along is not None else "   none"
		st_s = f"{strength:>7.0f}" if strength is not None else "   none"
		cv_s = f"{cvr:>5.2f}" if cvr is not None else " none"
		el_s = "  YES" if eligible else "   NO"

		print(f"{frame_idx:>7} {pool:>5} {tier:>6} "
			f"{trk_len:>6} {bd_s} {cr_s} {al_s} "
			f"{st_s} {cv_s} {n_cand:>5} {n_blob:>5} "
			f"{score:>6.2f} {el_s}")

	print("=" * line_w)

	# gate 1: seed frames
	seed_stats = [s for s in all_stats if s["pool"] == "seed"]
	gap_stats = [s for s in all_stats if s["pool"] == "gap"]

	if seed_stats:
		print(f"\nGate 1 -- Seed frames ({len(seed_stats)} tested):")
		# count frames with trackable candidate near seed
		seed_dists = [s["blob_dist"] for s in seed_stats if s.get("blob_dist") is not None]
		seed_overlaps = sum(1 for s in seed_stats if s.get("overlaps_seed", False))
		seed_tracked = sum(1 for s in seed_stats if s.get("track_length", 0) >= 3)
		seed_cvr = [s["comp_vs_raw"] for s in seed_stats if s.get("comp_vs_raw") is not None]
		seed_trklen = [s["track_length"] for s in seed_stats if s.get("track_length", 0) > 0]

		found = len(seed_dists)
		print(f"  candidate found: {found}/{len(seed_stats)}")
		if seed_dists:
			med_dist = float(numpy.median(seed_dists))
			# count within 1x typical box height
			ref_hs = [s.get("ref_h", 50) for s in seed_stats if s.get("blob_dist") is not None]
			within_box = sum(1 for d, h in zip(seed_dists, ref_hs) if d <= h)
			print(f"  within 1x box height: {within_box}/{found}")
			print(f"  median distance to seed: {med_dist:.1f} px")
		print(f"  overlaps expanded seed box: {seed_overlaps}/{len(seed_stats)}")
		if seed_tracked:
			print(f"  trackable (len >= 3): {seed_tracked}/{len(seed_stats)}")
		if seed_trklen:
			print(f"  median track length: {float(numpy.median(seed_trklen)):.1f}")
		if seed_cvr:
			med_cvr = float(numpy.median(seed_cvr))
			helps = "compensation helps" if med_cvr > 1.0 else "compensation does NOT help"
			print(f"  median comp/raw: {med_cvr:.2f} ({helps})")

		# gate 1 pass/fail
		if found > 0 and seed_overlaps >= len(seed_stats) * 0.5:
			print("  --> GATE 1 PASS: method finds runner on seed frames")
		else:
			print("  --> GATE 1 FAIL: method too weak on known seed frames")

	if gap_stats:
		print(f"\nGate 2 -- Gap frames ({len(gap_stats)} tested):")
		gap_dists = [s["blob_dist"] for s in gap_stats if s.get("blob_dist") is not None]
		gap_cross = [s["cross_track"] for s in gap_stats if s.get("cross_track") is not None]
		gap_along = [s["along_track"] for s in gap_stats if s.get("along_track") is not None]
		gap_tracked = sum(1 for s in gap_stats if s.get("track_length", 0) >= 3)
		gap_cvr = [s["comp_vs_raw"] for s in gap_stats if s.get("comp_vs_raw") is not None]
		gap_scores = [s["track_score"] for s in gap_stats if s.get("track_score", 0) > 0]

		found = len(gap_dists)
		print(f"  candidate found: {found}/{len(gap_stats)}")
		if gap_cross:
			print(f"  median cross-track: {float(numpy.median(gap_cross)):.1f} px")
		if gap_along:
			print(f"  median along-track: {float(numpy.median(gap_along)):.1f} px")
		print(f"  trackable (len >= 3): {gap_tracked}/{len(gap_stats)}")
		if gap_cvr:
			med_cvr = float(numpy.median(gap_cvr))
			helps = "compensation helps" if med_cvr > 1.0 else "compensation does NOT help"
			print(f"  median comp/raw: {med_cvr:.2f} ({helps})")
		if gap_scores:
			print(f"  median track score: {float(numpy.median(gap_scores)):.2f}")
		# auto-seed eligibility
		gap_eligible = sum(1 for s in gap_stats if s.get("review_candidate", False))
		print(f"  auto-seed eligible: {gap_eligible}/{len(gap_stats)} "
			f"(trk>={REVIEW_MIN_TRACK_LENGTH}, "
			f"|cross|<={REVIEW_MAX_CROSS_TRACK:.0f}, "
			f"|along|<={REVIEW_MAX_ALONG_TRACK:.0f}, "
			f"score>={REVIEW_MIN_SCORE})")

	# interpretation guide
	print("\nInterpretation:")
	print("  works on seeds, fails in gaps = signal exists, "
		"alignment degrades in hard regions")
	print("  fails on seeds = method too weak for this video")
	print("  works on both = auto-seed generation is plausible")
	print()


#============================================
def main() -> None:
	"""Run the residual motion diagnostic."""
	args = parse_args()

	if not os.path.isfile(args.input_file):
		raise RuntimeError(f"Input file not found: {args.input_file}")

	# set up output directory
	output_dir = os.path.join("output_smoke", "residual_motion")
	os.makedirs(output_dir, exist_ok=True)
	num_frames_in_window = 2 * args.half_window + 1
	print("\nResidual Motion Diagnostic")
	print("=" * 40)
	print(f"  settings: threshold={args.threshold}px, "
		f"scale={args.scale}, "
		f"window={num_frames_in_window} frames (half={args.half_window})")

	# load all data
	result = load_all_data(args.input_file)
	reader, motion_track, scene_transform, seeds_list, diagnostics, intervals_data = result

	# select diagnostic frames (two pools)
	diagnostic_frames = select_diagnostic_frames(
		seeds_list, diagnostics, args.num_samples, reader.frame_count,
		intervals_data,
	)
	seed_indices = [f["frame_index"] for f in diagnostic_frames if f["pool"] == "seed"]
	gap_indices = [f["frame_index"] for f in diagnostic_frames if f["pool"] == "gap"]
	print(f"  seed frames: {seed_indices}")
	print(f"  gap frames: {gap_indices}")

	# process each frame
	all_stats = []
	for i, frame_info in enumerate(diagnostic_frames):
		frame_index = frame_info["frame_index"]
		pool = frame_info["pool"]
		print(f"\n  processing frame {frame_index} [{pool}] "
			f"({i + 1}/{len(diagnostic_frames)})...")

		stats = compute_frame_statistics(
			frame_info, reader, scene_transform, intervals_data,
			diagnostics, seeds_list, args.threshold, args.scale,
			args.half_window,
		)
		if stats.get("no_data") or stats.get("no_reference"):
			print(f"  warning: no data/reference for frame {frame_index}")
			continue

		# render diagnostic PNG
		render_diagnostic_png(
			stats, seeds_list, intervals_data,
			args.threshold, args.scale, args.half_window, output_dir,
		)
		all_stats.append(stats)

	# print statistics table
	print_statistics_table(all_stats)

	# build montage
	build_montage(all_stats, output_dir)

	# render flow video centered on weakest gap
	midpoint = find_weakest_gap_midpoint(seeds_list, diagnostics, reader.frame_count)
	render_flow_video(
		reader, scene_transform, seeds_list, intervals_data, diagnostics,
		midpoint, args.threshold, args.scale, output_dir,
		args.half_window,
	)

	print(f"\nAll outputs in: {output_dir}/")


#============================================
if __name__ == "__main__":
	main()
