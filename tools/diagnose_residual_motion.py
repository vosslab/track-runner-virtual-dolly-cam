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
import residual_motion

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
	parser.add_argument(
		"-k", "--k-factor", dest="k_factor", type=float,
		default=residual_motion.DOG_K_FACTOR_DEFAULT,
		help="DoG k-factor (sigma_2 / sigma_1). 1.1=paper tight, 1.6=SIFT, "
			"3-5 works well on this project's residuals (default: 3.0)"
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
	cache_pattern = os.path.join(tr_paths.DATA_DIR, f"{basename}.*.npz")
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

	# load solved intervals (has blended_path per interval)
	intervals_path = tr_paths.default_intervals_path(input_file)
	intervals_data = state_io.load_geometry_cache(intervals_path)
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
	"""Find the blended interval path box for a given frame from solved intervals.

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
			blended = interval.get("blended_path", [])
			# blended_path index maps to frame offset from start
			offset = frame_index - start
			if offset < len(blended):
				box = blended[offset]
				return box
	return None


#============================================
def find_blended_path_for_frame(
	frame_index: int,
	intervals_data: dict,
) -> tuple:
	"""Find the blended interval path and offset for a frame.

	Args:
		frame_index: Frame to look up.
		intervals_data: Solved intervals data.

	Returns:
		Tuple of (blended_path_list, frame_offset_in_track, start_frame)
		or (None, -1, -1) if not found.
	"""
	solved = intervals_data.get("solved_intervals", {})
	for key, interval in solved.items():
		start = interval["start_frame"]
		end = interval["end_frame"]
		if start <= frame_index <= end:
			blended = interval.get("blended_path", [])
			offset = frame_index - start
			if offset < len(blended):
				return (blended, offset, start)
	return (None, -1, -1)


#============================================
def _gap_has_motion(
	gap_start: int,
	gap_end: int,
	intervals_data: dict,
) -> bool:
	"""Check whether a gap between seeds contains actual runner motion.

	A gap is stationary if the blended interval path shows negligible displacement
	between start and end (pre-race, standing still, etc.).

	Args:
		gap_start: Start frame of the gap.
		gap_end: End frame of the gap.
		intervals_data: Solved intervals data.

	Returns:
		True if the runner moves significantly in this gap.
	"""
	# check displacement across the gap in the blended interval path
	solved = intervals_data.get("solved_intervals", {})
	for key, interval in solved.items():
		iv_start = interval["start_frame"]
		iv_end = interval["end_frame"]
		# find an interval that covers this gap
		if iv_start <= gap_start and iv_end >= gap_end:
			blended = interval.get("blended_path", [])
			offset_a = gap_start - iv_start
			offset_b = gap_end - iv_start
			if offset_a < len(blended) and offset_b < len(blended):
				box_a = blended[offset_a]
				box_b = blended[offset_b]
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
	# skip anything before race_start_frame when detection is available.
	# pre-race frames have a stationary runner and fixed camera, so
	# background subtraction produces no useful residual-motion signal.
	pre_race = diagnostics.get("pre_race_reference")
	if pre_race is not None and pre_race.get("race_start_frame") is not None:
		race_start_frame = int(pre_race["race_start_frame"])
	else:
		race_start_frame = 0

	# only "visible" seeds are reliable positive controls. "partial" seeds
	# mark that the torso box is clipped by the frame edge or an occluder,
	# which makes them poor anchors for diagnostic evaluation.
	usable_statuses = {"visible"}
	usable_seeds = []
	pre_race_seed_skips = 0
	for seed in seeds_list:
		if seed.get("status") not in usable_statuses:
			continue
		if int(seed["frame_index"]) < race_start_frame:
			pre_race_seed_skips += 1
			continue
		usable_seeds.append(seed)
	if pre_race_seed_skips > 0:
		print(f"  frame sampler: skipped {pre_race_seed_skips} "
			f"pre-race seed(s) (frame_index < race_start_frame={race_start_frame})")

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

	# gap pool: place gap frames at a random offset from selected seed frames
	# this tests whether the Hermite correction holds just outside known truth
	gap_offset_min = 10
	gap_offset_max = 20
	seed_frame_list = sorted(int(s["frame_index"]) for s in usable_seeds)
	# build list of moving gap intervals for boundary checking
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
		# collect selected seed frame indices
		selected_seed_frames = sorted(
			f["frame_index"] for f in frames if f["pool"] == "seed"
		)
		# for each selected seed, try +offset and -offset into adjacent gaps
		gap_candidates = []
		for sf in selected_seed_frames:
			for direction in (+1, -1):
				# random offset between 10-20 frames from seed
				offset = random.randint(gap_offset_min, gap_offset_max)
				candidate = sf + direction * offset
				# check that candidate falls inside a moving gap
				for gap_start, gap_end, gap_len in moving_gaps:
					# must be inside gap and away from the seed boundaries
					if gap_start < candidate < gap_end:
						gap_candidates.append(candidate)
						break

		# deduplicate and clamp; also exclude any pre-race candidate
		# (can happen when a seed near race_start_frame emits a -offset
		# candidate that falls before the race starts)
		gap_candidates = sorted(set(gap_candidates))
		gap_candidates = [
			f for f in gap_candidates
			if 1 <= f <= frame_count - 2 and f >= race_start_frame
		]

		# if we have more candidates than budget, sample; otherwise use all
		if len(gap_candidates) > gap_budget:
			gap_frames_selected = random.sample(gap_candidates, gap_budget)
		else:
			gap_frames_selected = gap_candidates

		for f in gap_frames_selected:
			frames.append({
				"frame_index": f,
				"pool": "gap",
				"truth_box": None,
			})
		print(f"  frame sampler: gap frames at +/-{gap_offset_min}-"
			f"{gap_offset_max} from selected seeds")

	# sort by frame index
	frames.sort(key=lambda x: x["frame_index"])

	# print summary
	seed_count = sum(1 for f in frames if f["pool"] == "seed")
	gap_count = sum(1 for f in frames if f["pool"] == "gap")
	print(f"  frame sampler: {seed_count} seed frames + {gap_count} gap frames "
		f"= {len(frames)} total")
	return frames


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
	# thickness reduced by 1 px from the caller-supplied value for a
	# lighter overlay; floor at 1
	thin = max(1, thickness - 1)
	# blend the rectangle over the frame so underlying pixels still read
	# through; 0.7 alpha keeps the outline crisp but slightly translucent
	overlay = frame.copy()
	cv2.rectangle(overlay, (x1, y1), (x2, y2), color, thin)
	alpha = 0.7
	cv2.addWeighted(overlay, alpha, frame, 1.0 - alpha, 0.0, frame)
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
	blended_path: list,
	frame_offset: int,
	span: int = 5,
) -> tuple:
	"""Compute local tangent and normal vectors from blended interval path.

	Uses positions at +/-span frames to estimate direction of motion.

	Args:
		blended_path: List of dicts with cx, cy keys.
		frame_offset: Index into blended_path for the target frame.
		span: Number of frames on each side for tangent estimation.

	Returns:
		Tuple of (tangent_x, tangent_y, normal_x, normal_y) as unit vectors.
		Returns (1, 0, 0, 1) if tangent cannot be computed.
	"""
	# clamp to available range
	lo = max(0, frame_offset - span)
	hi = min(len(blended_path) - 1, frame_offset + span)
	if lo >= hi:
		# cannot compute tangent
		return (1.0, 0.0, 0.0, 1.0)

	dx = float(blended_path[hi]["cx"]) - float(blended_path[lo]["cx"])
	dy = float(blended_path[hi]["cy"]) - float(blended_path[lo]["cy"])
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
# along-track clamp is looser than cross-track because a runner can be
# ahead or behind the Hermite point and still be correct
ALONG_TRACK_CLAMP_FACTOR = 2.0


#============================================
def apply_hermite_correction(
	blob_x: float,
	blob_y: float,
	hermite_cx: float,
	hermite_cy: float,
	tangent: tuple,
	corridor_radius: float,
) -> dict:
	"""Correct blob center using Hermite scaffold as coordinate system.

	Decomposes the blob-to-Hermite offset into along-track and cross-track
	components, clamps them with anisotropic limits, and reconstructs the
	corrected position. Cross-track is clamped tightly (corridor_radius)
	because lateral error is suspicious. Along-track is clamped looser
	(ALONG_TRACK_CLAMP_FACTOR * corridor_radius) because the runner can
	be ahead or behind the Hermite prediction and still be correct.

	Args:
		blob_x: Raw blob centroid x.
		blob_y: Raw blob centroid y.
		hermite_cx: Hermite blended interval path center x.
		hermite_cy: Hermite blended interval path center y.
		tangent: (tx, ty, nx, ny) unit vectors from compute_local_tangent.
		corridor_radius: Cross-track clamp limit in pixels.

	Returns:
		Dict with keys: corrected_x, corrected_y, raw_along, raw_cross,
		clamped_along, clamped_cross, correction_mag.
	"""
	tx, ty, nx, ny = tangent
	# offset from Hermite center to blob
	dx = blob_x - hermite_cx
	dy = blob_y - hermite_cy
	# decompose into along-track and cross-track
	raw_along = dx * tx + dy * ty
	raw_cross = dx * nx + dy * ny
	# anisotropic clamp: cross-track tight, along-track looser
	cross_limit = corridor_radius
	along_limit = ALONG_TRACK_CLAMP_FACTOR * corridor_radius
	clamped_along = max(-along_limit, min(along_limit, raw_along))
	clamped_cross = max(-cross_limit, min(cross_limit, raw_cross))
	# reconstruct corrected position in pixel space
	corrected_x = hermite_cx + clamped_along * tx + clamped_cross * nx
	corrected_y = hermite_cy + clamped_along * ty + clamped_cross * ny
	# correction magnitude: how far the corrected point moved from raw blob
	corr_dx = corrected_x - blob_x
	corr_dy = corrected_y - blob_y
	correction_mag = (corr_dx**2 + corr_dy**2)**0.5
	result = {
		"corrected_x": corrected_x,
		"corrected_y": corrected_y,
		"raw_along": raw_along,
		"raw_cross": raw_cross,
		"clamped_along": clamped_along,
		"clamped_cross": clamped_cross,
		"correction_mag": correction_mag,
	}
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

	Thin back-compat shim over residual_motion.compute_residual_for_frame
	with return_extras=True. Preserves the historical 4-tuple return order
	so diagnostic PNG panels keep working unchanged.

	Args:
		reader: VideoReader instance.
		frame_index: Center frame index N.
		scene_transform: SceneTransform for camera compensation.
		scale_factor: Downsample factor (<1.0 downsamples before warp).
		half_window: Offsets on each side (default 4 = 9-frame window).

	Returns:
		Tuple of (residual_mag, raw_mag_single, validity_mask, display_frame).
		display_frame is frame N (BGR, possibly resized) for PNG overlay.
	"""
	return residual_motion.compute_residual_for_frame(
		reader=reader,
		frame_index=frame_index,
		scene_transform=scene_transform,
		half_window=half_window,
		scale_factor=scale_factor,
		return_extras=True,
	)


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
	k_factor: float,
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

	# get blended interval path and reference box
	blended_path, blended_offset, blended_start = find_blended_path_for_frame(
		frame_index, intervals_data
	)
	ref_box = find_trajectory_box(frame_index, seeds_list, intervals_data)

	# compute tangent from blended interval path
	if blended_path is not None:
		tangent = compute_local_tangent(blended_path, blended_offset)
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
		# gap frame: use blended interval path position with corridor
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

	# DoG band-pass tuned to the per-frame torso width as the target
	# blob diameter. ref_w is already in scaled-image pixel space
	# (multiplied by scale_factor at capture), matching the residual map.
	# Both raw and DoG maps flow through so PNG panels can show
	# before/after and stats report both blob counts; blob extraction and
	# downstream temporal tracking run on the DoG-filtered map.
	comp_mag_dog = residual_motion.dog_filter_blob_scale(
		comp_mag, ref_w, k=k_factor,
	)
	comp_mag_dog[validity_mask == 0] = 0.0

	stats["display_frame"] = display_frame
	stats["comp_mag"] = comp_mag
	stats["comp_mag_dog"] = comp_mag_dog
	stats["raw_mag"] = raw_mag
	stats["validity_mask"] = validity_mask

	# before/after blob counts at the same threshold
	raw_blobs = residual_motion.extract_frame_blobs(
		comp_mag, validity_mask, threshold,
	)
	stats["num_blobs_raw"] = len(raw_blobs)

	# extract blobs from center frame on the DoG-filtered map
	center_blobs = residual_motion.extract_frame_blobs(
		comp_mag_dog, validity_mask, threshold,
	)
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
				filtered = residual_motion.filter_blobs_to_corridor(
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

		# get reference point for this frame (may shift slightly).
		# Use each neighbor's own torso width for DoG sigma sizing so the
		# filter stays C2-compliant as the runner scales across the 5-frame
		# window. Fall back to the center frame's torso width only when
		# the blended path doesn't cover the neighbor.
		neighbor_box = find_trajectory_box(fi, seeds_list, intervals_data)
		if neighbor_box is not None:
			nb_cx = float(neighbor_box["cx"]) * scale_factor
			nb_cy = float(neighbor_box["cy"]) * scale_factor
			nb_w = float(neighbor_box["w"]) * scale_factor
		else:
			nb_cx = ref_cx
			nb_cy = ref_cy
			nb_w = ref_w

		# DoG-filter the neighbor's residual using its own torso width
		# as the target diameter (per-frame C2-compliant scaling).
		n_mag_dog = residual_motion.dog_filter_blob_scale(
			n_mag, nb_w, k=k_factor,
		)
		n_mag_dog[n_validity == 0] = 0.0
		n_blobs = residual_motion.extract_frame_blobs(n_mag_dog, n_validity, threshold)
		if pool == "seed" and truth_box is not None:
			filtered = filter_blobs_near_seed(
				n_blobs, nb_cx, nb_cy, search_radius
			)
		else:
			filtered = residual_motion.filter_blobs_to_corridor(
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
		# store raw blob centroid before correction
		raw_bx = candidate_blob["centroid_x"]
		raw_by = candidate_blob["centroid_y"]
		stats["raw_blob_x"] = raw_bx
		stats["raw_blob_y"] = raw_by
		raw_dx = raw_bx - ref_cx
		raw_dy = raw_by - ref_cy
		stats["raw_blob_dist"] = (raw_dx**2 + raw_dy**2)**0.5

		# apply Hermite scaffold correction: clamp blob offset within corridor
		correction = apply_hermite_correction(
			raw_bx, raw_by, ref_cx, ref_cy, tangent,
			stats["corridor_radius"],
		)
		stats["candidate_x"] = correction["corrected_x"]
		stats["candidate_y"] = correction["corrected_y"]
		# corrected distance from Hermite center
		cdx = correction["corrected_x"] - ref_cx
		cdy = correction["corrected_y"] - ref_cy
		stats["blob_dist"] = (cdx**2 + cdy**2)**0.5
		stats["candidate_strength"] = candidate_blob["integrated_mag"]
		stats["correction_mag"] = correction["correction_mag"]

		# cross-track and along-track from clamped correction
		stats["cross_track"] = correction["clamped_cross"]
		stats["along_track"] = correction["clamped_along"]

		# comp vs raw at corrected candidate location
		cand_ix = int(correction["corrected_x"])
		cand_iy = int(correction["corrected_y"])
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

		# check seed-box overlap for seed frames (use corrected position)
		if pool == "seed" and truth_box is not None:
			# does corrected centroid fall within expanded seed box (1.5x)?
			expand = 1.5
			in_box = (abs(cdx) <= ref_w * expand / 2
				and abs(cdy) <= ref_h * expand / 2)
			stats["overlaps_seed"] = in_box
	else:
		# no candidate found
		stats["candidate_x"] = None
		stats["candidate_y"] = None
		stats["raw_blob_x"] = None
		stats["raw_blob_y"] = None
		stats["raw_blob_dist"] = None
		stats["blob_dist"] = None
		stats["candidate_strength"] = None
		stats["correction_mag"] = None
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

	# review metrics layer: per-frame scores for seed recommendation
	# visual_agreement_score: 0-1, how well blob agrees with solver position
	# combines cross-track tightness, track persistence, and blob strength
	va_score = 0.0
	if candidate_blob is not None and ref_h > 0:
		# cross-track component: 1.0 at zero, decays to 0 at corridor edge
		cross_abs = abs(cross_val) if cross_val is not None else 999.0
		corridor_r = stats.get("corridor_radius", ref_h)
		cross_agreement = max(0.0, 1.0 - cross_abs / corridor_r)
		# persistence component: track_length / window_size
		persist = min(1.0, trk_len / tracking_window)
		# strength component: normalized by reference box area
		ref_area = ref_w * ref_h
		strength_val = stats.get("candidate_strength", 0.0) or 0.0
		# cap at 1.0, typical runner fills ~half the box area
		strength_norm = min(1.0, strength_val / max(ref_area * 0.5, 1.0))
		# weighted combination
		va_score = 0.5 * cross_agreement + 0.3 * persist + 0.2 * strength_norm
	stats["visual_agreement"] = va_score

	# blob_track_confidence: 0-1, how reliable the tracking itself is
	# high when track is long, strong, and smooth
	bt_conf = 0.0
	if best_track is not None and len(best_track) >= 2:
		persist_c = min(1.0, len(best_track) / tracking_window)
		# smoothness: low displacement variance = high confidence
		positions = [(b["centroid_x"], b["centroid_y"]) for _, b in best_track]
		if len(positions) >= 2:
			displacements = []
			for j in range(1, len(positions)):
				dx_t = positions[j][0] - positions[j - 1][0]
				dy_t = positions[j][1] - positions[j - 1][1]
				displacements.append((dx_t**2 + dy_t**2)**0.5)
			disp_var = numpy.var(displacements) if displacements else 0.0
			# normalize: variance of 100 px^2 -> 0 confidence
			smooth_c = max(0.0, 1.0 - disp_var / 100.0)
		else:
			smooth_c = 0.5
		bt_conf = 0.6 * persist_c + 0.4 * smooth_c
	stats["blob_track_confidence"] = bt_conf

	# cross_track_disagreement: signed cross-track distance
	# positive = blob is to one side, negative = other side
	# large absolute value = visual cue disagrees with solver
	stats["cross_track_disagreement"] = cross_val if cross_val is not None else None

	return stats


#============================================
def get_blended_path_positions(
	blended_path: list,
	blended_offset: int,
	scale_factor: float,
	num_points: int = 25,
) -> list:
	"""Get blended interval path positions around a frame for curve drawing.

	Args:
		blended_path: List of dicts with cx, cy keys.
		blended_offset: Index of the target frame in blended_path.
		scale_factor: Scale factor for coordinates.
		num_points: Number of points on each side.

	Returns:
		List of (x, y) tuples in scaled pixel coords.
	"""
	points = []
	lo = max(0, blended_offset - num_points)
	hi = min(len(blended_path), blended_offset + num_points + 1)
	for i in range(lo, hi):
		px = float(blended_path[i]["cx"]) * scale_factor
		py = float(blended_path[i]["cy"]) * scale_factor
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
	comp_mag_dog = stats.get("comp_mag_dog")

	if display_frame is None or comp_mag is None or comp_mag_dog is None:
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
		blended_path, blended_offset, blended_start = find_blended_path_for_frame(
			frame_index, intervals_data
		)
		if blended_path is not None:
			curve_pts = get_blended_path_positions(
				blended_path, blended_offset, scale_factor
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

	# draw raw blob centroid (dim dot) and corrected candidate (crosshair)
	cand_x = stats.get("candidate_x")
	cand_y = stats.get("candidate_y")
	raw_bx = stats.get("raw_blob_x")
	raw_by = stats.get("raw_blob_y")
	if raw_bx is not None:
		# dim dot for raw blob position
		cv2.circle(panel_tl, (int(raw_bx), int(raw_by)), 5, (180, 100, 180), -1)
	if cand_x is not None:
		# bright crosshair for corrected position
		draw_crosshair(panel_tl, cand_x, cand_y)
		# line from raw blob to corrected position showing correction
		if raw_bx is not None:
			cv2.line(panel_tl, (int(raw_bx), int(raw_by)),
				(int(cand_x), int(cand_y)), (255, 0, 255), 1)
		# line from Hermite reference to corrected position
		if ref_cx is not None:
			cv2.line(panel_tl, (int(ref_cx), int(ref_cy)),
				(int(cand_x), int(cand_y)), (0, 255, 0), 1)

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

	raw_d = stats.get("raw_blob_dist")
	corr_mag = stats.get("correction_mag")

	if cand_x is not None:
		dist_str = f"dist={blob_d:.0f}px" if blob_d is not None else "dist=?"
		raw_str = f"raw={raw_d:.0f}" if raw_d is not None else ""
		corr_str = f"corr={corr_mag:.0f}" if corr_mag is not None else ""
		cross_str = f"cross={cross:.0f}" if cross is not None else ""
		along_str = f"along={along:.0f}" if along is not None else ""
		cv2.putText(panel_tl,
			f"candidate: {dist_str}  {raw_str}  {corr_str}",
			(10, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 200, 255), 1)
		text_y += 18
		cv2.putText(panel_tl,
			f"  {cross_str}  {along_str}",
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
	panel_tr = residual_motion.colorize_jet(comp_mag)
	num_bg_frames = 2 * half_window
	cv2.putText(panel_tr, f"compensated residual ({num_bg_frames} frames)", (10, 25),
		cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
	# draw corridor/boxes on residual panel
	if pool == "gap":
		blended_path, blended_offset, blended_start = find_blended_path_for_frame(
			frame_index, intervals_data
		)
		if blended_path is not None:
			curve_pts = get_blended_path_positions(
				blended_path, blended_offset, scale_factor
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
	# draw raw blob (dim) and corrected candidate (bright) on residual panel
	if raw_bx is not None:
		cv2.circle(panel_tr, (int(raw_bx), int(raw_by)), 5, (180, 100, 180), -1)
	if cand_x is not None:
		draw_crosshair(panel_tr, cand_x, cand_y, color=(0, 255, 0))
		if raw_bx is not None:
			cv2.line(panel_tr, (int(raw_bx), int(raw_by)),
				(int(cand_x), int(cand_y)), (255, 0, 255), 1)
		if blob_d is not None:
			cv2.putText(panel_tr, f"{blob_d:.0f}px",
				(int(cand_x) + 15, int(cand_y) - 5),
				cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
	# track info at bottom
	trk_info = f"track len={trk_len}/5  score={score:.2f}  cand={n_cand}"
	cv2.putText(panel_tr, trk_info, (10, panel_tr.shape[0] - 10),
		cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

	# top-far-right: DoG-filtered residual heatmap (full frame).
	# Same JET colormap and fixed_max as the raw panel so intensities are
	# visually comparable. Light overlays only: corridor/box, no track clutter.
	panel_tr_dog = residual_motion.colorize_jet(comp_mag_dog)
	num_blobs_raw = stats.get("num_blobs_raw", 0)
	num_blobs_dog = stats.get("num_blobs", 0)
	cv2.putText(panel_tr_dog,
		f"DoG-filtered residual  raw_blobs={num_blobs_raw}  dog_blobs={num_blobs_dog}",
		(10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
	if pool == "gap":
		blended_path_d, blended_offset_d, _ = find_blended_path_for_frame(
			frame_index, intervals_data
		)
		if blended_path_d is not None:
			curve_pts_d = get_blended_path_positions(
				blended_path_d, blended_offset_d, scale_factor
			)
			tangent_d = stats.get("tangent", (1, 0, 0, 1))
			c_radius_d = stats.get("corridor_radius", 50)
			draw_corridor_on_frame(panel_tr_dog, curve_pts_d, tangent_d, c_radius_d,
				(200, 200, 200))
	elif ref_cx is not None:
		ref_w_val = stats.get("ref_w", 0)
		ref_h_val_draw = stats.get("ref_h", 0)
		draw_box_on_frame(panel_tr_dog, ref_cx, ref_cy, ref_w_val, ref_h_val_draw,
			(255, 255, 255), 2)

	# bottom-left: thresholded motion mask (full frame)
	motion_mask = (comp_mag > threshold).astype(numpy.uint8) * 255
	panel_bl = cv2.cvtColor(motion_mask, cv2.COLOR_GRAY2BGR)
	cv2.putText(panel_bl, f"motion mask (t={threshold:.1f})", (10, 25),
		cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
	if pool == "gap":
		blended_path, blended_offset, blended_start = find_blended_path_for_frame(
			frame_index, intervals_data
		)
		if blended_path is not None:
			curve_pts = get_blended_path_positions(
				blended_path, blended_offset, scale_factor
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

	# bottom-far-right: DoG thresholded motion mask (full frame).
	# Same threshold as raw mask so the difference in components is
	# attributable to the DoG filter alone.
	motion_mask_dog = (comp_mag_dog > threshold).astype(numpy.uint8) * 255
	panel_bl_dog = cv2.cvtColor(motion_mask_dog, cv2.COLOR_GRAY2BGR)
	cv2.putText(panel_bl_dog, f"DoG motion mask (t={threshold:.1f})", (10, 25),
		cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
	if pool == "gap":
		blended_path_m, blended_offset_m, _ = find_blended_path_for_frame(
			frame_index, intervals_data
		)
		if blended_path_m is not None:
			curve_pts_m = get_blended_path_positions(
				blended_path_m, blended_offset_m, scale_factor
			)
			tangent_m = stats.get("tangent", (1, 0, 0, 1))
			c_radius_m = stats.get("corridor_radius", 50)
			draw_corridor_on_frame(panel_bl_dog, curve_pts_m, tangent_m, c_radius_m,
				(0, 200, 200))
	elif ref_cx is not None:
		ref_w_val2 = stats.get("ref_w", 0)
		ref_h_val2 = stats.get("ref_h", 0)
		draw_box_on_frame(panel_bl_dog, ref_cx, ref_cy, ref_w_val2, ref_h_val2,
			(0, 255, 0), 2)
	if cand_x is not None:
		draw_crosshair(panel_bl_dog, cand_x, cand_y)

	# bottom-right: original frame without annotations (clean reference)
	panel_br = display_frame.copy()
	cv2.putText(panel_br, f"original frame {frame_index}", (10, 25),
		cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

	# assemble full-frame 2x3 grid:
	#   top:    original-with-overlays | raw residual JET | DoG residual JET
	#   bottom: clean original         | raw motion mask  | DoG motion mask
	top_row = numpy.hstack([panel_tl, panel_tr, panel_tr_dog])
	bottom_row = numpy.hstack([panel_br, panel_bl, panel_bl_dog])
	grid_full = numpy.vstack([top_row, bottom_row])

	# save full-frame PNG
	full_path = os.path.join(output_dir, f"{pool}_frame_{frame_index:06d}.png")
	cv2.imwrite(full_path, grid_full)

	# build cropped 2x2 grid centered on reference point (8x torso box, square)
	ref_h_val = stats.get("ref_h", 50)
	# center crop midway between Hermite reference and corrected candidate
	# so both points are visible when they disagree
	if cand_x is not None and ref_cx is not None:
		crop_cx = (ref_cx + cand_x) / 2.0
		crop_cy = (ref_cy + cand_y) / 2.0
	elif ref_cx is not None:
		crop_cx = ref_cx
		crop_cy = ref_cy
	else:
		crop_cx = None
		crop_cy = None
	if crop_cx is not None and ref_h_val > 0:
		crop_tl = crop_square_around_point(panel_tl, crop_cx, crop_cy, ref_h_val)
		crop_tr = crop_square_around_point(panel_tr, crop_cx, crop_cy, ref_h_val)
		crop_tr_dog = crop_square_around_point(panel_tr_dog, crop_cx, crop_cy, ref_h_val)
		crop_bl = crop_square_around_point(panel_bl, crop_cx, crop_cy, ref_h_val)
		crop_bl_dog = crop_square_around_point(panel_bl_dog, crop_cx, crop_cy, ref_h_val)
		crop_br = crop_square_around_point(panel_br, crop_cx, crop_cy, ref_h_val)
		# resize all crops to the same size for grid assembly
		crop_size = crop_tl.shape[0]
		crop_tr = cv2.resize(crop_tr, (crop_size, crop_size))
		crop_tr_dog = cv2.resize(crop_tr_dog, (crop_size, crop_size))
		crop_bl = cv2.resize(crop_bl, (crop_size, crop_size))
		crop_bl_dog = cv2.resize(crop_bl_dog, (crop_size, crop_size))
		crop_br = cv2.resize(crop_br, (crop_size, crop_size))
		crop_top = numpy.hstack([crop_tl, crop_tr, crop_tr_dog])
		crop_bot = numpy.hstack([crop_br, crop_bl, crop_bl_dog])
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
	# only "visible" seeds are reliable positive controls. "partial" seeds
	# mark that the torso box is clipped by the frame edge or an occluder,
	# which makes them poor anchors for diagnostic evaluation.
	usable_statuses = {"visible"}
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

		colored = residual_motion.colorize_jet(mag)

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
	line_w = 168
	print("=" * line_w)
	print(f"{'frame':>7} {'pool':>5} {'tier':>6} "
		f"{'trk_ln':>6} {'raw_d':>7} {'blob_d':>7} {'corr':>6} "
		f"{'cross':>7} {'along':>7} "
		f"{'strnth':>7} {'c/r':>5} {'#cand':>5} {'raw/dog':>9} "
		f"{'score':>6} {'v_agr':>5} {'b_con':>5} {'revw?':>5}")
	print("-" * line_w)

	for stats in all_stats:
		frame_idx = stats["frame_index"]
		pool = stats["pool"]
		tier = stats.get("tier", "?")

		trk_len = stats.get("track_length", 0)
		raw_d = stats.get("raw_blob_dist")
		blob_d = stats.get("blob_dist")
		corr_m = stats.get("correction_mag")
		cross = stats.get("cross_track")
		along = stats.get("along_track")
		strength = stats.get("candidate_strength")
		cvr = stats.get("comp_vs_raw")
		n_cand = stats.get("num_corridor_candidates", 0)
		n_blob = stats.get("num_blobs", 0)
		n_blob_raw = stats.get("num_blobs_raw", 0)
		blob_pair = f"{n_blob_raw:>4}/{n_blob:<4}"
		score = stats.get("track_score", 0.0)
		va = stats.get("visual_agreement", 0.0)
		bc = stats.get("blob_track_confidence", 0.0)
		eligible = stats.get("review_candidate", False)

		# format nullable values
		rd_s = f"{raw_d:>7.1f}" if raw_d is not None else "   none"
		bd_s = f"{blob_d:>7.1f}" if blob_d is not None else "   none"
		cm_s = f"{corr_m:>6.1f}" if corr_m is not None else "  none"
		cr_s = f"{cross:>7.1f}" if cross is not None else "   none"
		al_s = f"{along:>7.1f}" if along is not None else "   none"
		st_s = f"{strength:>7.0f}" if strength is not None else "   none"
		cv_s = f"{cvr:>5.2f}" if cvr is not None else " none"
		el_s = "  YES" if eligible else "   NO"

		print(f"{frame_idx:>7} {pool:>5} {tier:>6} "
			f"{trk_len:>6} {rd_s} {bd_s} {cm_s} "
			f"{cr_s} {al_s} "
			f"{st_s} {cv_s} {n_cand:>5} {blob_pair:>9} "
			f"{score:>6.2f} {va:>5.2f} {bc:>5.2f} {el_s}")

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
		# review candidate count
		gap_eligible = sum(1 for s in gap_stats if s.get("review_candidate", False))
		print(f"  review candidates: {gap_eligible}/{len(gap_stats)} "
			f"(trk>={REVIEW_MIN_TRACK_LENGTH}, "
			f"|cross|<={REVIEW_MAX_CROSS_TRACK:.0f}, "
			f"|along|<={REVIEW_MAX_ALONG_TRACK:.0f}, "
			f"score>={REVIEW_MIN_SCORE})")

	# review metrics summary
	gap_va = [s["visual_agreement"] for s in gap_stats] if gap_stats else []
	gap_bc = [s["blob_track_confidence"] for s in gap_stats] if gap_stats else []
	if gap_va:
		print("\nReview metrics (gap frames):")
		print(f"  median visual agreement: {float(numpy.median(gap_va)):.2f}")
		print(f"  median blob track confidence: {float(numpy.median(gap_bc)):.2f}")

	# interval-level needs_review: flag intervals with low visual agreement
	if all_stats:
		# group gap frames by interval
		interval_scores = {}
		for s in all_stats:
			if s["pool"] != "gap":
				continue
			iv = s.get("interval", -1)
			if iv < 0:
				continue
			if iv not in interval_scores:
				interval_scores[iv] = []
			interval_scores[iv].append(s)

		# flag intervals where median visual agreement is low
		review_threshold = 0.3
		needs_review = []
		for iv_idx, frames in sorted(interval_scores.items()):
			va_vals = [f["visual_agreement"] for f in frames]
			med_va = float(numpy.median(va_vals))
			# compute reasons for flagging
			reasons = []
			if med_va < review_threshold:
				reasons.append(f"low visual agreement ({med_va:.2f})")
			# check for high cross-track disagreement
			cross_vals = [f["cross_track_disagreement"]
				for f in frames if f.get("cross_track_disagreement") is not None]
			if cross_vals:
				max_cross = max(abs(c) for c in cross_vals)
				if max_cross > REVIEW_MAX_CROSS_TRACK:
					reasons.append(f"cross-track disagreement ({max_cross:.0f}px)")
			# check for missing candidates
			no_cand = sum(1 for f in frames if f.get("track_length", 0) == 0)
			if no_cand > 0:
				reasons.append(f"{no_cand} frames with no candidate")
			if reasons:
				needs_review.append((iv_idx, reasons, frames))

		if needs_review:
			print(f"\nIntervals needing review ({len(needs_review)}):")
			for iv_idx, reasons, frames in needs_review:
				frame_nums = [f["frame_index"] for f in frames]
				reason_str = "; ".join(reasons)
				print(f"  interval {iv_idx} (frames {frame_nums}): {reason_str}")
		else:
			print("\n  No intervals flagged for review.")

		# top-N seed recommendations: gap frames ranked by visual agreement
		if gap_stats:
			# frames with low agreement are where the user should look
			low_va_frames = sorted(gap_stats, key=lambda s: s["visual_agreement"])
			n_suggest = min(3, len(low_va_frames))
			print(f"\nSeed recommendation: top {n_suggest} frames to inspect:")
			for s in low_va_frames[:n_suggest]:
				va_val = s["visual_agreement"]
				fi = s["frame_index"]
				reason_parts = []
				if va_val < review_threshold:
					reason_parts.append(f"low agreement ({va_val:.2f})")
				ct = s.get("cross_track_disagreement")
				if ct is not None and abs(ct) > 50:
					reason_parts.append(f"cross-track {ct:.0f}px")
				tl = s.get("track_length", 0)
				if tl < REVIEW_MIN_TRACK_LENGTH:
					reason_parts.append(f"short track ({tl})")
				reason_str = "; ".join(reason_parts) if reason_parts else "weakest agreement"
				print(f"  frame {fi}: {reason_str}")

	# interpretation guide
	print("\nInterpretation:")
	print("  works on seeds, fails in gaps = signal exists, "
		"alignment degrades in hard regions")
	print("  fails on seeds = method too weak for this video")
	print("  works on both = seed recommendations are reliable")
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
	print(f"  dog_filter: torso-scale band-pass active (k={args.k_factor:.2f}); "
		f"blob-extraction threshold -t applies to DoG output")

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
			args.half_window, args.k_factor,
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
