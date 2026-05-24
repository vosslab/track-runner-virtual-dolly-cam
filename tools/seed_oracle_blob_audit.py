#!/usr/bin/env python3
"""Seed-oracle batch audit for blob observer quality across all videos.

Leave-one-out (LOO) seed-oracle audit: for each post-race seed, remove it from
the interpolator, compute raw_pred via neighbors, and compare observer behavior
at the hidden seed position to the oracle (seed as ground truth).

Outputs one CSV row per probed frame with measurements needed to test H1, H2, H3,
H6, H8 hypotheses.

Usage:
    tools/seed_oracle_blob_audit.py -c tr_config/ -v TRACK_VIDEOS/ -o output.csv
"""

# Standard Library
import os
import sys
import csv
import json
import argparse
import math
import multiprocessing as mp
import types
from pathlib import Path

# PIP3 modules
import numpy
import scipy.ndimage

# add track_runner directory to path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, "track_runner")
if _TRACK_RUNNER_DIR not in sys.path:
	sys.path.insert(0, _TRACK_RUNNER_DIR)

# add common_tools directory to path
_COMMON_TOOLS_DIR = os.path.join(_REPO_ROOT, "common_tools")
if _COMMON_TOOLS_DIR not in sys.path:
	sys.path.insert(0, _COMMON_TOOLS_DIR)

# local repo modules
import camera_motion
import frame_reader
import probe_video
import residual_motion
import scene_coords
import state_io
import velocity_model


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description="Leave-one-out seed-oracle batch audit for blob observer."
	)
	parser.add_argument(
		"-c", "--tr-config", dest="tr_config_dir", default="tr_config/",
		help="Path to tr_config directory (default: tr_config/)"
	)
	parser.add_argument(
		"-v", "--videos", dest="videos_dir", default="TRACK_VIDEOS/",
		help="Path to videos directory (default: TRACK_VIDEOS/)"
	)
	parser.add_argument(
		"-o", "--output", dest="output_path", default="output_smoke/seed_oracle/oracle.csv",
		help="Path to output CSV (default: output_smoke/seed_oracle/oracle.csv)"
	)
	parser.add_argument(
		"--workers", dest="num_workers", type=int, default=4,
		help="Number of worker processes (default: 4)"
	)
	parser.add_argument(
		"--limit-per-video", dest="limit_per_video", type=int, default=None,
		help="Optional cap on probed frames per video (for smoke runs)"
	)
	parser.add_argument(
		"--include-midpoints", dest="include_midpoints", action="store_true",
		default=False,
		help="Include midpoint frames between seeds (future support)"
	)
	parser.add_argument(
		"--reader-path", dest="reader_path", type=str, default="legacy",
		choices=["prepass", "legacy"],
		help="Residual reader: 'prepass' uses precomputed_interval_residuals, 'legacy' uses on-the-fly compute"
	)
	args = parser.parse_args()
	return args


#============================================
def find_seed_video_pairs(tr_config_dir: str, videos_path: str) -> list:
	"""Find all seed files and their corresponding videos.

	videos_path can be either a directory or a single video file.
	If a file, return pairs for all seeds files that match its basename.

	Returns list of (seeds_path, video_path) tuples.
	"""
	pairs = []
	tr_config_dir = Path(tr_config_dir)
	videos_path_obj = Path(videos_path)

	# if videos_path is a single file, extract its basename
	if videos_path_obj.is_file():
		video_basename = videos_path_obj.stem
		# find the corresponding seeds file
		for seeds_file in sorted(tr_config_dir.glob("*.seeds.json")):
			seeds_basename = seeds_file.stem.rsplit(".track_runner.seeds", 1)[0]
			if seeds_basename == video_basename:
				pairs.append((str(seeds_file), videos_path))
				return pairs
		print(f"Warning: no seeds file found matching {videos_path_obj.name}", file=sys.stderr)
		return []

	# videos_path is a directory
	videos_dir = videos_path_obj
	# find all seeds files
	for seeds_file in sorted(tr_config_dir.glob("*.seeds.json")):
		# extract basename (e.g. "Jason-3200m-sectionals-IMG_4005")
		basename = seeds_file.stem.rsplit(".track_runner.seeds", 1)[0]

		# find matching video file (try .mkv, .mp4, .mov, etc.)
		video_found = None
		for ext in [".mkv", ".mp4", ".mov", ".MOV"]:
			video_path = videos_dir / f"{basename}{ext}"
			if video_path.exists():
				video_found = str(video_path)
				break

		if video_found:
			pairs.append((str(seeds_file), video_found))
		else:
			print(f"Warning: no video found for seeds file {seeds_file.name}", file=sys.stderr)

	return pairs


#============================================
def load_race_start(tr_config_dir: str, basename: str) -> int:
	"""Load race_start_frame from interval_scores.json or seeds.json."""
	scores_path = Path(tr_config_dir) / f"{basename}.track_runner.interval_scores.json"
	if not scores_path.exists():
		return 0

	with open(scores_path) as f:
		data = json.load(f)

	# Try to get race_start_frame from the top level
	if "race_start_frame" in data:
		return int(data["race_start_frame"])

	# Fallback: get from pre_race_reference if available
	if "pre_race_reference" in data and isinstance(data["pre_race_reference"], dict):
		if "end_frame" in data["pre_race_reference"]:
			return int(data["pre_race_reference"]["end_frame"]) + 1

	# Final fallback: return 0 (all frames treated as post-race)
	return 0


#============================================
def build_hermite_with_exclusion(
	all_seeds_post_race: list,
	all_seeds_scene: list,
	exclude_idx: int,
	scene_transform: object,
) -> dict:
	"""Build Hermite interval curves excluding one seed at exclude_idx.

	all_seeds_post_race: list of seed dicts (post-race only)
	all_seeds_scene: list of (frame, sx, sy, sw, sh) tuples (post-race only)
	exclude_idx: index of seed to exclude (in post-race lists)

	Returns interval curves or None if not enough seeds remain.
	"""
	# filter out the excluded seed
	filtered_seeds_post_race = [
		s for i, s in enumerate(all_seeds_post_race) if i != exclude_idx
	]
	filtered_seeds_scene = [
		s for i, s in enumerate(all_seeds_scene) if i != exclude_idx
	]

	# need at least 2 seeds to define an interval
	if len(filtered_seeds_post_race) < 2:
		return None

	# find the interval that would contain the excluded seed
	excluded_frame = all_seeds_post_race[exclude_idx]["frame_index"]
	left_seed = None
	right_seed = None

	for i, s in enumerate(filtered_seeds_post_race):
		frame = s["frame_index"]
		if frame < excluded_frame and (left_seed is None or frame > left_seed["frame_index"]):
			left_seed = s
		if frame > excluded_frame and (right_seed is None or frame < right_seed["frame_index"]):
			right_seed = s

	# if no bracketing interval, return None
	if left_seed is None or right_seed is None:
		return None

	# fit curves
	curves = velocity_model.fit_interval_curves(
		left_seed, right_seed, filtered_seeds_scene,
		scene_transform=scene_transform
	)
	return curves


#============================================
def compute_raw_pred_for_frame(
	frame_index: int,
	curves: dict,
	scene_transform: object,
) -> tuple:
	"""Compute raw_pred at one frame from Hermite curves.

	Returns (cx, cy, w, h) in pixel coordinates, or None on error.
	Uses the production _compute_raw_pred_forward delegated to velocity_model.
	"""
	if curves is None:
		return None

	start_frame = curves["start_frame"]
	end_frame = curves["end_frame"]

	# guard: frame outside interval
	if frame_index < start_frame or frame_index > end_frame:
		return None

	# Use the production forward-pass raw_pred computation
	raw_pred_list = velocity_model._compute_raw_pred_forward(curves, scene_transform)
	if not raw_pred_list:
		return None

	# raw_pred_list is indexed by (frame_index - start_frame)
	idx = frame_index - start_frame
	if idx < 0 or idx >= len(raw_pred_list):
		return None

	frame_idx, cx, cy, w, h, conf = raw_pred_list[idx]
	return (float(cx), float(cy), float(w), float(h))


#============================================
def probe_seed_frame(
	frame_index: int,
	seed_box: tuple,
	h_seed: float,
	w_seed: float,
	reader: object,
	scene_transform: object,
	residual_cache: dict,
	precomputed_store: "dict | None",
	video_name: str,
	post_race_seeds: list,
	all_seeds_scene: list,
) -> list:
	"""Probe one frame at a seed position.

	Calls observe_blob_at with both oracle (seed as raw_pred) and LOO
	(from interpolation) raw_pred values. Returns a list of CSV row dicts.
	"""
	rows = []

	# seed torso center (oracle ground truth)
	seed_torso_cx, seed_torso_cy = seed_box[0], seed_box[1]

	# Find the seed index in post_race_seeds for LOO
	seed_idx = None
	for idx, seed in enumerate(post_race_seeds):
		if seed["frame_index"] == frame_index and seed["cx"] == seed_torso_cx and seed["cy"] == seed_torso_cy:
			seed_idx = idx
			break

	# Call observe_blob_at with oracle prediction (seed as raw_pred)
	trace_oracle = types.SimpleNamespace(observer_trace=None)
	blob_oracle = residual_motion.observe_blob_at(
		frame_index=frame_index,
		pred_center=(seed_torso_cx, seed_torso_cy),
		pred_box=(w_seed, h_seed),
		local_tangent=(1.0, 0.0, 0.0, 1.0),
		scene_transform=scene_transform,
		reader=reader,
		residual_cache=residual_cache,
		precomputed_store=precomputed_store,
		trace_sink=trace_oracle,
	)

	observer_trace_oracle = trace_oracle.observer_trace

	# Extract oracle measurements
	oracle_winner_cx = None
	oracle_winner_cy = None
	oracle_winner_dist_h = None
	oracle_mask_overlap_frac = 0.0
	oracle_n_raw_blobs = 0
	oracle_n_corridor_blobs = 0
	oracle_best_alt_dist_h = None

	if blob_oracle is not None and observer_trace_oracle is not None:
		oracle_winner_cx, oracle_winner_cy = blob_oracle.center_pixel
		if h_seed > 0:
			oracle_winner_dist_h = math.sqrt(
				(oracle_winner_cx - seed_torso_cx)**2 + (oracle_winner_cy - seed_torso_cy)**2
			) / h_seed
		else:
			oracle_winner_dist_h = 0.0

		oracle_n_raw_blobs = len(observer_trace_oracle.raw_blobs) if observer_trace_oracle.raw_blobs else 0
		oracle_n_corridor_blobs = len(observer_trace_oracle.corridor_blobs) if observer_trace_oracle.corridor_blobs else 0

		# Compute distance to second-nearest corridor blob from seed_torso_center
		if observer_trace_oracle.corridor_blobs and observer_trace_oracle.winner_blob is not None and h_seed > 0:
			winner_id = id(observer_trace_oracle.winner_blob)
			alt_dists = []
			for blob in observer_trace_oracle.corridor_blobs:
				if id(blob) == winner_id:
					continue
				bx = float(blob["centroid_x"])
				by = float(blob["centroid_y"])
				d = math.sqrt((bx - seed_torso_cx)**2 + (by - seed_torso_cy)**2) / h_seed
				alt_dists.append(d)
			if alt_dists:
				oracle_best_alt_dist_h = min(alt_dists)

		# Compute H1 mask overlap from the winner blob's connected component
		if observer_trace_oracle.residual_dog is not None and observer_trace_oracle.winner_blob is not None:
			# Label connected components in the residual DoG
			labeled, num_components = scipy.ndimage.label(observer_trace_oracle.residual_dog > 0)
			if num_components > 0:
				# Get ROI bounds from observer trace (roi_bounds in full-frame coords)
				roi_x1, roi_y1, roi_x2, roi_y2 = observer_trace_oracle.roi_bounds
				# winner_blob has centroid_x, centroid_y in full-frame (processed) coordinates
				winner_cx_frame = int(observer_trace_oracle.winner_blob["centroid_x"])
				winner_cy_frame = int(observer_trace_oracle.winner_blob["centroid_y"])
				# Convert to ROI-relative coordinates
				winner_x = winner_cx_frame - roi_x1
				winner_y = winner_cy_frame - roi_y1
				if 0 <= winner_y < labeled.shape[0] and 0 <= winner_x < labeled.shape[1]:
					winner_component = labeled[winner_y, winner_x]
					if winner_component > 0:
						blob_mask = (labeled == winner_component)
						# seed_torso is in frame coords; convert to ROI-relative
						seed_roi_x = int(seed_torso_cx - roi_x1)
						seed_roi_y = int(seed_torso_cy - roi_y1)
						# Dilate seed torso by 0.5 * h
						dilation_px = int(0.5 * h_seed)
						y_min = max(0, seed_roi_y - dilation_px)
						y_max = min(labeled.shape[0], seed_roi_y + dilation_px)
						x_min = max(0, seed_roi_x - dilation_px)
						x_max = min(labeled.shape[1], seed_roi_x + dilation_px)
						if y_max > y_min and x_max > x_min:
							seed_box_mask = numpy.zeros_like(blob_mask, dtype=bool)
							seed_box_mask[y_min:y_max, x_min:x_max] = True
							intersection = numpy.sum(blob_mask & seed_box_mask)
							blob_area = numpy.sum(blob_mask)
							if blob_area > 0:
								oracle_mask_overlap_frac = float(intersection) / float(blob_area)

	# Compute pre/post DoG max at seed torso
	pre_dog_max = None
	post_dog_max = None
	validity_at_seed = 0

	if observer_trace_oracle is not None:
		# Get ROI bounds to convert frame coords to ROI-relative
		roi_x1, roi_y1, roi_x2, roi_y2 = observer_trace_oracle.roi_bounds
		# Convert seed position from frame coords to ROI-relative
		roi_cx = int(seed_torso_cx - roi_x1)
		roi_cy = int(seed_torso_cy - roi_y1)

		if observer_trace_oracle.residual_pre_dog is not None:
			radius_px = 30
			y_min = max(0, roi_cy - radius_px)
			y_max = min(observer_trace_oracle.residual_pre_dog.shape[0], roi_cy + radius_px)
			x_min = max(0, roi_cx - radius_px)
			x_max = min(observer_trace_oracle.residual_pre_dog.shape[1], roi_cx + radius_px)
			if y_max > y_min and x_max > x_min:
				patch = observer_trace_oracle.residual_pre_dog[y_min:y_max, x_min:x_max]
				pre_dog_max = float(numpy.max(patch)) if patch.size > 0 else None

		if observer_trace_oracle.residual_dog is not None:
			radius_px = 30
			y_min = max(0, roi_cy - radius_px)
			y_max = min(observer_trace_oracle.residual_dog.shape[0], roi_cy + radius_px)
			x_min = max(0, roi_cx - radius_px)
			x_max = min(observer_trace_oracle.residual_dog.shape[1], roi_cx + radius_px)
			if y_max > y_min and x_max > x_min:
				patch = observer_trace_oracle.residual_dog[y_min:y_max, x_min:x_max]
				post_dog_max = float(numpy.max(patch)) if patch.size > 0 else None

		if observer_trace_oracle.validity_mask is not None:
			if 0 <= roi_cy < observer_trace_oracle.validity_mask.shape[0] and 0 <= roi_cx < observer_trace_oracle.validity_mask.shape[1]:
				validity_at_seed = int(observer_trace_oracle.validity_mask[roi_cy, roi_cx])

	# LOO call: if we have enough seeds, build Hermite without this one
	loo_winner_cx = None
	loo_winner_cy = None
	loo_winner_dist_h = None
	loo_n_raw_blobs = 0
	loo_n_corridor_blobs = 0
	loo_mask_overlap_frac = 0.0
	loo_best_alt_dist_h = None
	raw_pred_loo_cx = None
	raw_pred_loo_cy = None

	if seed_idx is not None and len(post_race_seeds) > 2:
		# Build LOO Hermite curves
		loo_curves = build_hermite_with_exclusion(post_race_seeds, all_seeds_scene, seed_idx, scene_transform)
		if loo_curves is not None:
			# Compute raw_pred at this frame using LOO curves
			raw_pred_loo = compute_raw_pred_for_frame(frame_index, loo_curves, scene_transform)
			if raw_pred_loo is not None:
				loo_cx, loo_cy, loo_w, loo_h = raw_pred_loo
				raw_pred_loo_cx = loo_cx
				raw_pred_loo_cy = loo_cy

				# Call observe_blob_at with LOO raw_pred
				trace_loo = types.SimpleNamespace(observer_trace=None)
				blob_loo = residual_motion.observe_blob_at(
					frame_index=frame_index,
					pred_center=(loo_cx, loo_cy),
					pred_box=(loo_w, loo_h),
					local_tangent=(1.0, 0.0, 0.0, 1.0),
					scene_transform=scene_transform,
					reader=reader,
					residual_cache=residual_cache,
					precomputed_store=precomputed_store,
					trace_sink=trace_loo,
				)

				observer_trace_loo = trace_loo.observer_trace

				if blob_loo is not None and observer_trace_loo is not None:
					loo_winner_cx, loo_winner_cy = blob_loo.center_pixel
					if loo_h > 0:
						loo_winner_dist_h = math.sqrt(
							(loo_winner_cx - seed_torso_cx)**2 + (loo_winner_cy - seed_torso_cy)**2
						) / loo_h
					else:
						loo_winner_dist_h = 0.0

					loo_n_raw_blobs = len(observer_trace_loo.raw_blobs) if observer_trace_loo.raw_blobs else 0
					loo_n_corridor_blobs = len(observer_trace_loo.corridor_blobs) if observer_trace_loo.corridor_blobs else 0

					# Compute H1 mask overlap from LOO winner blob's connected component
					if observer_trace_loo.residual_dog is not None and observer_trace_loo.winner_blob is not None:
						labeled_loo, num_components_loo = scipy.ndimage.label(observer_trace_loo.residual_dog > 0)
						if num_components_loo > 0:
							roi_x1_l, roi_y1_l, roi_x2_l, roi_y2_l = observer_trace_loo.roi_bounds
							winner_cx_frame_l = int(observer_trace_loo.winner_blob["centroid_x"])
							winner_cy_frame_l = int(observer_trace_loo.winner_blob["centroid_y"])
							winner_x_l = winner_cx_frame_l - roi_x1_l
							winner_y_l = winner_cy_frame_l - roi_y1_l
							if 0 <= winner_y_l < labeled_loo.shape[0] and 0 <= winner_x_l < labeled_loo.shape[1]:
								winner_component_l = labeled_loo[winner_y_l, winner_x_l]
								if winner_component_l > 0:
									blob_mask_l = (labeled_loo == winner_component_l)
									# seed_torso_center remains the reference (per H1 plan)
									seed_roi_x_l = int(seed_torso_cx - roi_x1_l)
									seed_roi_y_l = int(seed_torso_cy - roi_y1_l)
									dilation_px_l = int(0.5 * h_seed)
									y_min_l = max(0, seed_roi_y_l - dilation_px_l)
									y_max_l = min(labeled_loo.shape[0], seed_roi_y_l + dilation_px_l)
									x_min_l = max(0, seed_roi_x_l - dilation_px_l)
									x_max_l = min(labeled_loo.shape[1], seed_roi_x_l + dilation_px_l)
									if y_max_l > y_min_l and x_max_l > x_min_l:
										seed_box_mask_l = numpy.zeros_like(blob_mask_l, dtype=bool)
										seed_box_mask_l[y_min_l:y_max_l, x_min_l:x_max_l] = True
										intersection_l = numpy.sum(blob_mask_l & seed_box_mask_l)
										blob_area_l = numpy.sum(blob_mask_l)
										if blob_area_l > 0:
											loo_mask_overlap_frac = float(intersection_l) / float(blob_area_l)

					# Compute distance to second-nearest corridor blob from seed_torso_center
					if observer_trace_loo.corridor_blobs and observer_trace_loo.winner_blob is not None and h_seed > 0:
						winner_id_l = id(observer_trace_loo.winner_blob)
						alt_dists_l = []
						for blob in observer_trace_loo.corridor_blobs:
							if id(blob) == winner_id_l:
								continue
							bx = float(blob["centroid_x"])
							by = float(blob["centroid_y"])
							d = math.sqrt((bx - seed_torso_cx)**2 + (by - seed_torso_cy)**2) / h_seed
							alt_dists_l.append(d)
						if alt_dists_l:
							loo_best_alt_dist_h = min(alt_dists_l)

	# Compute distance to nearest other seed
	dist_to_nearest_other = float('inf')
	if seed_idx is not None:
		for i, other_seed in enumerate(post_race_seeds):
			if i == seed_idx:
				continue
			frame_dist = abs(other_seed["frame_index"] - frame_index)
			dist_to_nearest_other = min(dist_to_nearest_other, frame_dist)
	if dist_to_nearest_other == float('inf'):
		dist_to_nearest_other = 0.0

	# Create base row
	row = {
		"video": video_name,
		"frame": frame_index,
		"h_seed": h_seed,
		"w_seed": w_seed,
		"seed_torso_cx": seed_torso_cx,
		"seed_torso_cy": seed_torso_cy,
		"race_phase": "post_race",
		"dist_to_nearest_other_seed": dist_to_nearest_other,
		"dist_from_nearest_seed": 0.0,
		"is_nif": False,
		"raw_pred_loo_cx": raw_pred_loo_cx,
		"raw_pred_loo_cy": raw_pred_loo_cy,
		"raw_pred_oracle_cx": seed_torso_cx,
		"raw_pred_oracle_cy": seed_torso_cy,
		"loo_winner_cx": loo_winner_cx,
		"loo_winner_cy": loo_winner_cy,
		"loo_winner_dist_h": loo_winner_dist_h,
		"loo_winner_mask_overlap_frac": loo_mask_overlap_frac,
		"loo_n_raw_blobs": loo_n_raw_blobs,
		"loo_n_corridor_blobs": loo_n_corridor_blobs,
		"loo_best_alt_dist_h": loo_best_alt_dist_h,
		"oracle_winner_cx": oracle_winner_cx,
		"oracle_winner_cy": oracle_winner_cy,
		"oracle_winner_dist_h": oracle_winner_dist_h,
		"oracle_winner_mask_overlap_frac": oracle_mask_overlap_frac,
		"oracle_n_raw_blobs": oracle_n_raw_blobs,
		"oracle_n_corridor_blobs": oracle_n_corridor_blobs,
		"oracle_best_alt_dist_h": oracle_best_alt_dist_h,
		"pre_dog_max_at_seed_torso": pre_dog_max,
		"post_dog_max_at_seed_torso": post_dog_max,
		"validity_at_seed_torso": validity_at_seed,
		"oracle_blob_gate": "absent" if blob_oracle is None else "observed",
		"loo_blob_gate": "absent" if loo_winner_cx is None else "observed",
	}

	rows.append(row)
	return rows


#============================================
def process_one_video(
	seeds_path: str,
	video_path: str,
	tr_config_dir: str,
	limit_per_video: "int | None",
) -> list:
	"""Process one video: load seeds, probe each post-race seed.

	Returns list of CSV row dicts.
	"""
	rows = []

	# Ensure sys.path is set up for worker process (multiprocessing spawn mode on macOS)
	_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, "track_runner")
	if _TRACK_RUNNER_DIR not in sys.path:
		sys.path.insert(0, _TRACK_RUNNER_DIR)
	_COMMON_TOOLS_DIR = os.path.join(_REPO_ROOT, "common_tools")
	if _COMMON_TOOLS_DIR not in sys.path:
		sys.path.insert(0, _COMMON_TOOLS_DIR)

	# extract basename from seeds path
	basename = Path(seeds_path).stem.rsplit(".track_runner.seeds", 1)[0]
	video_name = Path(video_path).stem

	# load seeds
	seeds_data = state_io.load_seeds(seeds_path)
	all_seeds = seeds_data["seeds"]

	# load race_start
	race_start_frame = load_race_start(tr_config_dir, basename)

	# filter post-race seeds
	post_race_seeds = [s for s in all_seeds if s["frame_index"] >= race_start_frame]
	post_race_seeds = [s for s in post_race_seeds if "cx" in s and "cy" in s and "w" in s and "h" in s]

	if not post_race_seeds:
		print(f"No post-race seeds in {basename}", file=sys.stderr)
		return []

	# load scene_transform
	motion_track = camera_motion.load_motion_cache(
		os.path.join(tr_config_dir, f"{basename}.track_runner.camera_motion.npz")
	)
	if motion_track is None:
		print(f"Warning: motion_cache not found for {basename}", file=sys.stderr)
		return []
	scene_transform = scene_coords.SceneTransform(motion_track)

	# open video
	probe_info = probe_video.probe_video(video_path)
	reader = frame_reader.FrameReader(
		video_path, probe_info["fps"], probe_info["frame_count"]
	)

	# convert seeds to scene coordinates for Hermite fitting
	all_seeds_scene = []
	for seed in post_race_seeds:
		frame = seed["frame_index"]
		cx, cy = seed["cx"], seed["cy"]
		w, h = seed["w"], seed["h"]
		sx, sy, sw, sh = scene_transform.pixel_box_to_scene(frame, cx, cy, w, h)
		all_seeds_scene.append((frame, sx, sy, sw, sh))

	# load precomputed residual store if available (for Stage 4)
	precomputed_store = None

	# probe each post-race seed
	residual_cache = {}
	limit_count = 0

	for seed_idx, seed in enumerate(post_race_seeds):
		if limit_per_video is not None and limit_count >= limit_per_video:
			break

		frame_index = seed["frame_index"]
		cx, cy = seed["cx"], seed["cy"]
		w, h = seed["w"], seed["h"]

		# probe at seed position
		seed_rows = probe_seed_frame(
			frame_index, (cx, cy), h, w,
			reader, scene_transform, residual_cache,
			precomputed_store, video_name,
			post_race_seeds, all_seeds_scene,
		)
		rows.extend(seed_rows)
		limit_count += 1

	reader.close()

	return rows


#============================================
def main():
	"""Main entry point."""
	args = parse_args()

	# ensure output directory exists
	output_path = Path(args.output_path)
	output_path.parent.mkdir(parents=True, exist_ok=True)

	# find all seed-video pairs
	pairs = find_seed_video_pairs(args.tr_config_dir, args.videos_dir)
	if not pairs:
		raise RuntimeError("No seed-video pairs found")

	print(f"Found {len(pairs)} seed-video pairs", file=sys.stderr)

	# process videos in parallel
	all_rows = []
	if args.num_workers == 1:
		# single-threaded for debugging
		for seeds_path, video_path in pairs:
			print(f"Processing {Path(video_path).stem}...", file=sys.stderr)
			rows = process_one_video(
				seeds_path, video_path, args.tr_config_dir, args.limit_per_video
			)
			all_rows.extend(rows)
	else:
		# multiprocessing
		with mp.Pool(args.num_workers) as pool:
			results = []
			for seeds_path, video_path in pairs:
				result = pool.apply_async(
					process_one_video,
					(seeds_path, video_path, args.tr_config_dir, args.limit_per_video)
				)
				results.append(result)

			for i, result in enumerate(results):
				rows = result.get()
				all_rows.extend(rows)
				print(f"Completed {i+1}/{len(results)} videos", file=sys.stderr)

	# write CSV
	if not all_rows:
		raise RuntimeError("No rows to write")

	csv_columns = [
		"video", "frame", "h_seed", "w_seed", "seed_torso_cx", "seed_torso_cy",
		"race_phase", "dist_to_nearest_other_seed", "dist_from_nearest_seed", "is_nif",
		"raw_pred_loo_cx", "raw_pred_loo_cy", "raw_pred_oracle_cx", "raw_pred_oracle_cy",
		"loo_winner_cx", "loo_winner_cy", "loo_winner_dist_h", "loo_winner_mask_overlap_frac",
		"loo_n_raw_blobs", "loo_n_corridor_blobs", "loo_best_alt_dist_h",
		"oracle_winner_cx", "oracle_winner_cy", "oracle_winner_dist_h", "oracle_winner_mask_overlap_frac",
		"oracle_n_raw_blobs", "oracle_n_corridor_blobs", "oracle_best_alt_dist_h",
		"pre_dog_max_at_seed_torso", "post_dog_max_at_seed_torso", "validity_at_seed_torso",
		"oracle_blob_gate", "loo_blob_gate",
	]

	with open(output_path, "w", newline="") as f:
		writer = csv.DictWriter(f, fieldnames=csv_columns)
		writer.writeheader()
		writer.writerows(all_rows)

	print(f"Wrote {len(all_rows)} rows to {output_path}", file=sys.stderr)
	print("First 3 rows:", file=sys.stderr)
	for i, row in enumerate(all_rows[:3]):
		print(f"  {row}", file=sys.stderr)


#============================================
if __name__ == "__main__":
	main()
