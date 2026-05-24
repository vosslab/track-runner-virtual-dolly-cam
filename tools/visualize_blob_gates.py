#!/usr/bin/env python3
"""Visualize blob-snap gate decisions for interval diagnostic.

CLI tool to replay the solver's per-frame blob-snap gating on a specified
interval or single frame, capture gate traces, and render diagnostic images.

This tool scaffolds the WP-1A CLI and orchestration; renderer is a stub
(WP-1B will replace with full three-panel implementation).
"""

# Standard Library
import csv
import json
import math
import os
import sys
import argparse

# PIP3 modules
import matplotlib
import matplotlib.patches
import matplotlib.pyplot
import numpy
from PIL import Image

# add track_runner directory to path so we can import its modules.
# Mirrors the pattern used by every other tool under tools/.
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
import residual_pre_pass
import scene_coords
import state_io
import tr_paths
import velocity_model


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description=(
			"Visualize blob-snap gate decisions for a single frame or interval. "
			"Captures gate traces from the FWD/BWD blob-snap pipeline and renders "
			"diagnostic images to show which frames accepted blob observations. "
			"Or build an HTML index of interval directories."
		)
	)
	parser.add_argument(
		"-v", "--video", dest="video_path", required=False,
		help="Path to input video file (required for frame/interval modes)."
	)

	# Single-frame vs interval mode vs html-index (mutually exclusive)
	mode_group = parser.add_mutually_exclusive_group(required=True)
	mode_group.add_argument(
		"-f", "--frame", dest="frame_index", type=int,
		help="Single frame mode: visualize blob gates at this frame index. "
		"Mutually exclusive with --interval."
	)
	mode_group.add_argument(
		"-i", "--interval", dest="interval_range", nargs=2, type=int,
		metavar=("START", "END"),
		help="Interval mode: START and END are 0-based seed frame indices "
		"(WP-2A implementation)."
	)
	mode_group.add_argument(
		"--build-html-index", dest="build_html_index", type=str,
		metavar="DIR",
		help="HTML index mode: scan DIR for interval directories and build "
		"BLOB_GATE_INDEX.html (WP-2B implementation)."
	)

	parser.add_argument(
		"-p", "--pass", dest="pass_name", default="both",
		choices=["fwd", "bwd", "both"],
		help="Limit to forward, backward, or both passes (default: both). "
		"Single-frame mode may render only one pass per frame."
	)

	parser.add_argument(
		"-o", "--output-dir", dest="output_dir", default=None,
		help="Output directory for PNG renders. "
		"Default: output_smoke/blob_refinement/{video_basename}/"
	)

	parser.add_argument(
		"--reader-path", dest="reader_path", default="prepass",
		choices=["prepass", "legacy"],
		help="Residual reading path: 'prepass' (Stage 4 canonical, via "
		"residual_pre_pass) or 'legacy' (on-the-fly via residual_motion). "
		"Default: prepass."
	)

	parser.add_argument(
		"--positive-control", dest="positive_control", action="store_true",
		help="Flag this interval as a positive control in the output PNG metadata."
	)

	parser.add_argument(
		"--write-corridor-blob-list", dest="write_corridor_blob_list", action="store_true",
		help="Write corridor_blobs_json column to verdicts.csv with full per-blob score set."
	)

	parser.add_argument(
		"--no-png", dest="no_png", action="store_true",
		help="Skip PNG rendering; emit CSV and classification only."
	)

	args = parser.parse_args()
	return args


#============================================
def render_gate_trace_png(
	trace: object,
	source_bgr_frame: numpy.ndarray,
	output_path: str,
) -> None:
	"""Three-panel renderer for gate trace PNG.

	Renders three rows:
	  1. Post-DoG residual (JET colormap) with overlays
	  2. Pre-DoG residual (JET colormap) with overlays
	  3. Source frame BGR with identical overlays
	  + Bottom annotation strip with gate details

	Overlays (all panels):
	  - ROI bounds (cyan rect)
	  - corridor polygon (magenta)
	  - validity_mask invalid pixels (semitransparent red wash)
	  - raw_blobs (yellow filled circles, radius proportional to area)
	  - corridor_blobs (orange-bordered)
	  - winner (red filled)
	  - raw_pred[t] (white cross)
	  - raw_pred[t-1] / raw_pred[t+1] (small white dots)
	  - v_pred arrow
	  - proximity ring (white circle, radius 0.6 * h)

	Args:
		trace: BlobGateTrace object from blob_snap.
		source_bgr_frame: BGR numpy array for the frame.
		output_path: Filesystem path where PNG is written.
	"""
	# Extract dimensions from source frame
	frame_height, frame_width = source_bgr_frame.shape[:2]

	# Create figure with 3 columns and annotation strip below
	fig = matplotlib.pyplot.figure(figsize=(15, 6), dpi=120)

	# Create axes for the three panels
	ax_post_dog = fig.add_subplot(131)
	ax_pre_dog = fig.add_subplot(132)
	ax_source = fig.add_subplot(133)

	# Extract data from trace
	observer_trace = trace.observer_trace
	has_observer = observer_trace is not None

	# Panel 1: Post-DoG residual
	if has_observer and observer_trace.residual_dog is not None:
		residual_post = observer_trace.residual_dog
		im1 = ax_post_dog.imshow(residual_post, cmap='jet', origin='upper')
		matplotlib.pyplot.colorbar(im1, ax=ax_post_dog, label='Post-DoG')
	else:
		ax_post_dog.text(0.5, 0.5, 'No observation', ha='center', va='center',
			transform=ax_post_dog.transAxes, fontsize=10, color='red')
		ax_post_dog.set_xlim(0, frame_width)
		ax_post_dog.set_ylim(frame_height, 0)

	ax_post_dog.set_title('Post-DoG Residual')
	ax_post_dog.set_aspect('equal')

	# Panel 2: Pre-DoG residual
	if has_observer and observer_trace.residual_pre_dog is not None:
		residual_pre = observer_trace.residual_pre_dog
		im2 = ax_pre_dog.imshow(residual_pre, cmap='jet', origin='upper')
		matplotlib.pyplot.colorbar(im2, ax=ax_pre_dog, label='Pre-DoG')
	else:
		if has_observer:
			ax_pre_dog.text(0.5, 0.5, 'Pre-DoG not available\n(cache hit)', ha='center',
				va='center', transform=ax_pre_dog.transAxes, fontsize=10, color='red')
		else:
			ax_pre_dog.text(0.5, 0.5, 'No observation', ha='center', va='center',
				transform=ax_pre_dog.transAxes, fontsize=10, color='red')
		ax_pre_dog.set_xlim(0, frame_width)
		ax_pre_dog.set_ylim(frame_height, 0)

	ax_pre_dog.set_title('Pre-DoG Residual')
	ax_pre_dog.set_aspect('equal')

	# Panel 3: Source frame BGR (convert to RGB for display)
	source_rgb = source_bgr_frame[..., ::-1]
	ax_source.imshow(source_rgb, origin='upper')
	ax_source.set_title('Source Frame')
	ax_source.set_aspect('equal')

	# Helper function to draw overlays on an axis
	def draw_overlays(ax, frame_width, frame_height):
		if has_observer:
			# ROI bounds (cyan rect)
			roi_x1, roi_y1, roi_x2, roi_y2 = observer_trace.roi_bounds
			roi_width = roi_x2 - roi_x1
			roi_height = roi_y2 - roi_y1
			rect = matplotlib.patches.Rectangle(
				(roi_x1, roi_y1), roi_width, roi_height,
				linewidth=1, edgecolor='cyan', facecolor='none'
			)
			ax.add_patch(rect)

			# Validity mask invalid pixels (semitransparent red wash)
			if observer_trace.validity_mask is not None:
				validity = observer_trace.validity_mask
				invalid = ~validity
				if invalid.any():
					invalid_y, invalid_x = numpy.where(invalid)
					ax.scatter(invalid_x, invalid_y, c='red', alpha=0.1, s=1)

			# Corridor polygon (magenta)
			if hasattr(observer_trace, 'corridor_polygon') and observer_trace.corridor_polygon is not None:
				polygon = observer_trace.corridor_polygon
				ax.plot(polygon[:, 0], polygon[:, 1], 'magenta', linewidth=1)

			# Raw blobs (yellow filled circles)
			for blob in observer_trace.raw_blobs:
				cx = blob.get('centroid_x', blob.get('cx'))
				cy = blob.get('centroid_y', blob.get('cy'))
				area = blob['area']
				radius = (area / math.pi) ** 0.5
				circle = matplotlib.patches.Circle(
					(cx, cy), radius, fill=True, alpha=0.4,
					facecolor='yellow', edgecolor='yellow', linewidth=0.5
				)
				ax.add_patch(circle)

			# Corridor blobs (orange-bordered)
			for blob in observer_trace.corridor_blobs:
				cx = blob.get('centroid_x', blob.get('cx'))
				cy = blob.get('centroid_y', blob.get('cy'))
				area = blob['area']
				radius = (area / math.pi) ** 0.5
				circle = matplotlib.patches.Circle(
					(cx, cy), radius, fill=False,
					edgecolor='orange', linewidth=1.0
				)
				ax.add_patch(circle)

			# Winner blob (red filled)
			if observer_trace.winner_blob is not None:
				winner = observer_trace.winner_blob
				cx = winner.get('centroid_x', winner.get('cx'))
				cy = winner.get('centroid_y', winner.get('cy'))
				area = winner['area']
				radius = (area / math.pi) ** 0.5
				circle = matplotlib.patches.Circle(
					(cx, cy), radius, fill=True, alpha=0.6,
					facecolor='red', edgecolor='red', linewidth=1.5
				)
				ax.add_patch(circle)

			# Proximity ring (white circle, radius 0.6 * h)
			if trace.raw_curr is not None and len(trace.raw_curr) >= 5 and trace.observer_trace is not None:
				curr_cx = trace.raw_curr[1]
				curr_cy = trace.raw_curr[2]
				curr_h = trace.raw_curr[4]
				prox_radius = 0.6 * curr_h
				circle = matplotlib.patches.Circle(
					(curr_cx, curr_cy), prox_radius, fill=False,
					edgecolor='white', linewidth=1, linestyle='--'
				)
				ax.add_patch(circle)

		# raw_pred[t-1] and raw_pred[t+1] (small white dots)
		if trace.raw_prev is not None and len(trace.raw_prev) >= 3:
			prev_cx = trace.raw_prev[1]
			prev_cy = trace.raw_prev[2]
			ax.plot(prev_cx, prev_cy, 'w.', markersize=5)

		if trace.raw_next is not None and len(trace.raw_next) >= 3:
			next_cx = trace.raw_next[1]
			next_cy = trace.raw_next[2]
			ax.plot(next_cx, next_cy, 'w.', markersize=5)

		# raw_pred[t] (white cross)
		if trace.raw_curr is not None and len(trace.raw_curr) >= 3:
			curr_cx = trace.raw_curr[1]
			curr_cy = trace.raw_curr[2]
			cross_size = 10
			ax.plot([curr_cx - cross_size, curr_cx + cross_size], [curr_cy, curr_cy],
				'w-', linewidth=1)
			ax.plot([curr_cx, curr_cx], [curr_cy - cross_size, curr_cy + cross_size],
				'w-', linewidth=1)

		# v_pred arrow
		if trace.v_pred is not None and len(trace.v_pred) >= 2 and trace.raw_curr is not None and len(trace.raw_curr) >= 3:
			v_x, v_y = trace.v_pred
			curr_cx = trace.raw_curr[1]
			curr_cy = trace.raw_curr[2]
			v_scale = 5.0
			ax.arrow(curr_cx, curr_cy, v_x * v_scale, v_y * v_scale,
				head_width=3, head_length=2, fc='white', ec='white', alpha=0.8)

	# Draw overlays on all panels
	for ax in [ax_post_dog, ax_pre_dog, ax_source]:
		draw_overlays(ax, frame_width, frame_height)
		ax.set_xlim(0, frame_width)
		ax.set_ylim(frame_height, 0)

	# Compute annotation strip text
	frame_idx = trace.frame_index
	pass_name = trace.pass_name
	blob_gate = trace.blob_gate
	pass_str = "FWD" if pass_name == "FWD" else "BWD"
	line1 = f"frame={frame_idx}  pass={pass_str}  blob_gate={blob_gate}"

	n_raw = len(observer_trace.raw_blobs) if has_observer else 0
	n_corridor = len(observer_trace.corridor_blobs) if has_observer else 0
	line2 = f"n_raw_blobs={n_raw}  n_corridor_blobs={n_corridor}"

	line3 = ""
	if trace.winner_dist_px is not None and trace.winner_dist_h is not None:
		rank_info = ""
		if has_observer and observer_trace.winner_blob is not None:
			total_blobs = max(1, len(observer_trace.corridor_blobs))
			rank_by_dist = 1
			winner_cx = observer_trace.winner_blob.get('centroid_x', observer_trace.winner_blob.get('cx'))
			winner_cy = observer_trace.winner_blob.get('centroid_y', observer_trace.winner_blob.get('cy'))
			for blob in observer_trace.corridor_blobs:
				blob_cx = blob.get('centroid_x', blob.get('cx'))
				blob_cy = blob.get('centroid_y', blob.get('cy'))
				if blob_cx == winner_cx and blob_cy == winner_cy:
					break
				rank_by_dist += 1
			rank_info = f"  rank_by_dist={rank_by_dist}/{total_blobs}"
		winner_conf = observer_trace.winner_score if has_observer else None
		conf_str = f"{winner_conf:.2f}" if winner_conf is not None else "?"
		line3 = (f"winner: dist={trace.winner_dist_px:.1f}px ({trace.winner_dist_h:.2f}h)  "
			f"conf={conf_str}{rank_info}")
	else:
		line3 = "winner: none"

	line4 = ""
	if trace.proximity_ok is not None:
		prox_threshold = trace.proximity_threshold
		prox_dist = trace.winner_dist_px if trace.winner_dist_px is not None else 0.0
		line4 = f"proximity: dist={prox_dist:.1f}  threshold={prox_threshold:.1f}  prox_ok={trace.proximity_ok}"
	else:
		line4 = "proximity: N/A"

	line5 = ""
	if trace.direction_ok is not None:
		dot = trace.direction_dot if trace.direction_dot is not None else 0.0
		line5 = f"direction: v_pred_mag={trace.v_pred_mag:.1f}  dot={dot:.1f}  dir_ok={trace.direction_ok}"
	else:
		line5 = "direction: N/A"

	# Determine outline color for annotation strip
	if trace.blob_gate == "accepted":
		outline_color = 'green'
	elif trace.blob_gate == "rejected":
		outline_color = 'red'
	elif trace.blob_gate == "absent":
		outline_color = 'gray'
	elif trace.blob_gate == "skipped":
		outline_color = 'darkgray'
	else:
		outline_color = 'black'

	# Add annotation strip below panels
	fig.text(0.05, 0.08, line1, fontfamily='monospace', fontsize=9)
	fig.text(0.05, 0.06, line2, fontfamily='monospace', fontsize=9)
	fig.text(0.05, 0.04, line3, fontfamily='monospace', fontsize=9)
	fig.text(0.05, 0.02, line4, fontfamily='monospace', fontsize=9)
	fig.text(0.05, 0.00, line5, fontfamily='monospace', fontsize=9)

	# Add frame border with verdict color
	border = matplotlib.patches.Rectangle(
		(0.01, 0.0), 0.98, 1.0, transform=fig.transFigure,
		fill=False, edgecolor=outline_color, linewidth=3
	)
	fig.patches.append(border)

	matplotlib.pyplot.subplots_adjust(left=0.05, right=0.95, top=0.95, bottom=0.12)
	matplotlib.pyplot.savefig(output_path, dpi=120, bbox_inches='tight')
	matplotlib.pyplot.close(fig)


#============================================
def load_scene_transform(
	video_path: str,
) -> object:
	"""Load scene_transform from camera motion cache.

	Args:
		video_path: Path to video file.

	Returns:
		SceneTransform object.

	Raises:
		RuntimeError: If camera motion cache is missing or invalid.
	"""
	motion_path = tr_paths.default_camera_motion_path(video_path)

	if not os.path.exists(motion_path):
		raise RuntimeError(
			f"Camera motion cache not found: {motion_path}. "
			f"Run solve mode first to generate camera motion."
		)

	motion_track = camera_motion.load_motion_cache(motion_path, None)
	if motion_track is None:
		raise RuntimeError(
			f"Failed to load camera motion from {motion_path}. "
			f"File may be corrupt."
		)

	scene_transform = scene_coords.SceneTransform(motion_track)
	return scene_transform


#============================================
def load_seeds_and_find_bracketing_pair(
	video_path: str,
	frame_index: int,
	seeds_path: str = None,
) -> tuple:
	"""Load seeds and find the bracketing seed pair for a single frame.

	Args:
		video_path: Path to video.
		frame_index: Frame to find seeds for.
		seeds_path: Optional override for seeds file path.

	Returns:
		(start_seed_dict, end_seed_dict, seeds_list) or raises on error.

	Raises:
		RuntimeError: If seeds file is missing or frame has no bracketing pair.
	"""
	if seeds_path is None:
		seeds_path = tr_paths.default_seeds_path(video_path)

	if not os.path.exists(seeds_path):
		raise RuntimeError(
			f"Seeds file not found: {seeds_path}. "
			f"Run seed mode first to annotate frames."
		)

	seeds_data = state_io.load_seeds(seeds_path)
	seeds_list = seeds_data["seeds"]

	if not seeds_list:
		raise RuntimeError("Seeds file is empty or has no 'seeds' array.")

	# Find the bracketing pair: largest seed frame <= frame_index and
	# smallest seed frame >= frame_index.
	start_seed = None
	end_seed = None

	for seed in seeds_list:
		frame = seed["frame_index"]
		if frame <= frame_index and (start_seed is None or frame > start_seed["frame_index"]):
			start_seed = seed
		if frame >= frame_index and (end_seed is None or frame < end_seed["frame_index"]):
			end_seed = seed

	if start_seed is None or end_seed is None:
		raise RuntimeError(
			f"No bracketing seed pair found for frame {frame_index}. "
			f"Available seeds at frames: "
			f"{[s['frame_index'] for s in seeds_list]}"
		)

	return (start_seed, end_seed, seeds_list)


#============================================
def ensure_output_directory(output_dir: str) -> None:
	"""Create output directory if it doesn't exist.

	Args:
		output_dir: Directory path to create.
	"""
	os.makedirs(output_dir, exist_ok=True)


#============================================
def classify_lost_at_stage(trace: object) -> str:
	"""Classify where the blob was lost in the funnel.

	Uses the taxonomy from plan WP-3A classify_funnel.

	Args:
		trace: BlobGateTrace object.

	Returns:
		One of: none, observer, raw_blobs, corridor, gate_prox, gate_dir,
		gate_path, accepting, or coverage_bug.
	"""
	if trace.blob_gate == "skipped":
		return "none"

	if trace.blob_gate == "accepted":
		return "accepting"

	if trace.blob_gate == "absent":
		if trace.observer_trace is None or not trace.observer_trace.has_residual:
			return "observer"
		if trace.observer_trace.validity_mask is not None:
			if (trace.observer_trace.validity_mask == 0).all():
				return "observer"
		if not trace.observer_trace.raw_blobs:
			return "raw_blobs"
		if not trace.observer_trace.corridor_blobs:
			return "corridor"
		return "coverage_bug"

	if trace.blob_gate == "rejected":
		if trace.proximity_ok is False:
			return "gate_prox"
		if trace.direction_ok is False:
			return "gate_dir"
		if trace.path_ok_prev is False:
			return "gate_path"
		return "coverage_bug"

	return "coverage_bug"


#============================================
def encode_corridor_blobs_json(
	trace: object,
	write_flag: bool,
) -> str | None:
	"""Encode corridor_blobs with full per-blob score set to compact JSON.

	Args:
		trace: BlobGateTrace object.
		write_flag: Whether to write the column (--write-corridor-blob-list).

	Returns:
		JSON string with list of blob dicts, or None if flag is off or no blobs.
	"""
	if not write_flag:
		return None

	if trace.observer_trace is None or not trace.observer_trace.corridor_blobs:
		return None

	blob_list = []
	for blob in trace.observer_trace.corridor_blobs:
		blob_dict = {
			"cx": float(blob.get("centroid_x", blob.get("cx", 0.0))),
			"cy": float(blob.get("centroid_y", blob.get("cy", 0.0))),
			"area": float(blob.get("area", 0.0)),
			"dist_h": float(blob.get("dist_h", 0.0)),
			"integrated_mag": float(blob.get("integrated_mag", 0.0)),
			"size_score": float(blob.get("size_score", 0.0)),
			"proximity_score": float(blob.get("proximity_score", 0.0)),
			"total_score": float(blob.get("total_score", 0.0)),
		}
		blob_list.append(blob_dict)

	if not blob_list:
		return None

	return json.dumps(blob_list, separators=(",", ":"))


#============================================
def build_verdict_row(
	trace: object,
	frame_index: int,
	pass_name: str,
	h_value: float,
	w_value: float = None,
	write_corridor_blob_list: bool = False,
) -> dict:
	"""Build one verdict row for a traced frame.

	Args:
		trace: BlobGateTrace object.
		frame_index: Absolute frame index.
		pass_name: "FWD" or "BWD".
		h_value: Torso height in pixels (raw_curr[4]).
		w_value: Torso width in pixels (raw_curr[3]). If None, uses 0.5*h as fallback.

	Returns:
		Dictionary with all verdict columns.
	"""
	has_observer = trace.observer_trace is not None
	n_raw_blobs = len(trace.observer_trace.raw_blobs) if has_observer else 0
	n_corridor_blobs = len(trace.observer_trace.corridor_blobs) if has_observer else 0

	winner_dist_px = trace.winner_dist_px if trace.winner_dist_px is not None else 0.0
	winner_dist_h = trace.winner_dist_h if trace.winner_dist_h is not None else 0.0

	# Preserve None as "vacuous" for gate columns; convert only for numeric operations.
	proximity_ok_raw = trace.proximity_ok
	dir_ok_raw = trace.direction_ok
	path_ok_prev_raw = trace.path_ok_prev

	# For numeric operations, use 0.0 as default
	v_pred_mag = trace.v_pred_mag if trace.v_pred_mag is not None else 0.0
	dir_dot = trace.direction_dot if trace.direction_dot is not None else 0.0

	# Detect if direction gate was vacuous (velocity below floor).
	# When v_pred_mag <= BLOB_SNAP_VELOCITY_FLOOR, the direction gate
	# is set to True without evaluating (vacuous set).
	dir_gate_vacuous = (
		trace.observer_trace is not None and
		v_pred_mag <= velocity_model.BLOB_SNAP_VELOCITY_FLOOR
	)

	closest_corridor_blob_dist_h = None
	winner_rank_by_distance = None
	winner_rank_by_confidence = None
	best_alt_dist_h = None
	best_alt_conf = None

	if has_observer and n_corridor_blobs >= 2:
		winner_blob = trace.observer_trace.winner_blob
		if winner_blob is not None:
			corridor_blobs = trace.observer_trace.corridor_blobs
			distances = []
			winner_cx = winner_blob.get('centroid_x', winner_blob.get('cx'))
			winner_cy = winner_blob.get('centroid_y', winner_blob.get('cy'))
			for blob in corridor_blobs:
				blob_cx = blob.get('centroid_x', blob.get('cx'))
				blob_cy = blob.get('centroid_y', blob.get('cy'))
				dx = blob_cx - winner_cx
				dy = blob_cy - winner_cy
				dist = (dx * dx + dy * dy) ** 0.5
				distances.append((dist, blob))

			distances.sort(key=lambda x: x[0])
			closest_corridor_blob_dist_h = distances[0][0] / h_value if h_value > 0 else 0.0

			winner_rank_by_distance = 1
			winner_rank_by_confidence = 1
			best_alt_dist_h = None
			best_alt_conf = None

			for rank, (dist, blob) in enumerate(distances, start=1):
				blob_cx = blob.get('centroid_x', blob.get('cx'))
				blob_cy = blob.get('centroid_y', blob.get('cy'))
				if blob_cx == winner_cx and blob_cy == winner_cy:
					winner_rank_by_distance = rank
					conf = blob.get('confidence', 0.0)
					if conf >= trace.observer_trace.winner_score:
						winner_rank_by_confidence = rank
					break

			conf_sorted = sorted(corridor_blobs, key=lambda b: b.get('confidence', 0.0), reverse=True)
			for rank, blob in enumerate(conf_sorted, start=1):
				blob_cx = blob.get('centroid_x', blob.get('cx'))
				blob_cy = blob.get('centroid_y', blob.get('cy'))
				if blob_cx != winner_cx or blob_cy != winner_cy:
					best_alt_conf = blob.get('confidence', 0.0)
					dx = blob_cx - winner_cx
					dy = blob_cy - winner_cy
					best_alt_dist_h = ((dx * dx + dy * dy) ** 0.5) / h_value if h_value > 0 else 0.0
					winner_rank_by_confidence = rank
					break

	pre_dog_max_at_raw_pred = None
	post_dog_max_at_raw_pred = None
	validity_at_raw_pred = None
	roi_x1 = None
	roi_y1 = None
	roi_x2 = None
	roi_y2 = None
	raw_pred_in_roi = None

	if has_observer and trace.raw_curr is not None and len(trace.raw_curr) >= 3:
		roi_x1, roi_y1, roi_x2, roi_y2 = trace.observer_trace.roi_bounds
		raw_pred_x = trace.raw_curr[1]
		raw_pred_y = trace.raw_curr[2]
		raw_pred_in_roi = (roi_x1 <= raw_pred_x <= roi_x2 and roi_y1 <= raw_pred_y <= roi_y2)

		if trace.observer_trace.residual_dog is not None:
			residual_dog = trace.observer_trace.residual_dog
			dy = int(round(raw_pred_y - roi_y1))
			dx = int(round(raw_pred_x - roi_x1))
			roi_h = roi_y2 - roi_y1
			roi_w = roi_x2 - roi_x1
			circle_mask = numpy.zeros(residual_dog.shape, dtype=bool)
			yy, xx = numpy.ogrid[:roi_h, :roi_w]
			circle_mask[(yy - dy) ** 2 + (xx - dx) ** 2 <= 30 ** 2] = True
			if circle_mask.any():
				post_dog_max_at_raw_pred = float(residual_dog[circle_mask].max())

		if trace.observer_trace.residual_pre_dog is not None:
			residual_pre_dog = trace.observer_trace.residual_pre_dog
			dy = int(round(raw_pred_y - roi_y1))
			dx = int(round(raw_pred_x - roi_x1))
			roi_h = roi_y2 - roi_y1
			roi_w = roi_x2 - roi_x1
			circle_mask = numpy.zeros(residual_pre_dog.shape, dtype=bool)
			yy, xx = numpy.ogrid[:roi_h, :roi_w]
			circle_mask[(yy - dy) ** 2 + (xx - dx) ** 2 <= 30 ** 2] = True
			if circle_mask.any():
				pre_dog_max_at_raw_pred = float(residual_pre_dog[circle_mask].max())

		if trace.observer_trace.validity_mask is not None:
			dy = int(round(raw_pred_y - roi_y1))
			dx = int(round(raw_pred_x - roi_x1))
			if 0 <= dy < trace.observer_trace.validity_mask.shape[0] and 0 <= dx < trace.observer_trace.validity_mask.shape[1]:
				validity_at_raw_pred = int(trace.observer_trace.validity_mask[dy, dx])
	elif has_observer:
		roi_x1, roi_y1, roi_x2, roi_y2 = trace.observer_trace.roi_bounds

	lost_at = classify_lost_at_stage(trace)

	# Build corridor_blobs_json column if requested
	corridor_blobs_json = encode_corridor_blobs_json(trace, write_corridor_blob_list)

	# Compute torso width if not provided.
	# If w_value is None, fallback to 0.5*h (typical runner aspect ratio).
	if w_value is None:
		w_value = 0.5 * h_value if h_value > 0 else 0.0

	# Convert gate bool/None to string representation for CSV.
	# None -> "vacuous", True -> "True", False -> "False"
	prox_ok_str = "vacuous" if proximity_ok_raw is None else str(proximity_ok_raw)
	dir_ok_str = "vacuous" if dir_ok_raw is None else str(dir_ok_raw)
	path_ok_prev_str = "vacuous" if path_ok_prev_raw is None else str(path_ok_prev_raw)
	dir_gate_vacuous_str = "True" if dir_gate_vacuous else "False"

	verdict_row = {
		"frame_index": frame_index,
		"pass": pass_name,
		"gate": trace.blob_gate,
		"has_residual": has_observer,
		"n_raw_blobs": n_raw_blobs,
		"n_corridor_blobs": n_corridor_blobs,
		"winner_dist_px": winner_dist_px,
		"winner_dist_h": winner_dist_h,
		"winner_conf": trace.observer_trace.winner_score if has_observer and trace.observer_trace.winner_score is not None else None,
		"prox_ok": prox_ok_str,
		"dir_ok": dir_ok_str,
		"path_ok_prev": path_ok_prev_str,
		"dir_gate_vacuous": dir_gate_vacuous_str,
		"v_pred_mag": v_pred_mag,
		"dir_dot": dir_dot,
		"closest_corridor_blob_dist_h": closest_corridor_blob_dist_h,
		"winner_rank_by_distance": winner_rank_by_distance,
		"winner_rank_by_confidence": winner_rank_by_confidence,
		"best_alt_dist_h": best_alt_dist_h,
		"best_alt_conf": best_alt_conf,
		"pre_dog_max_at_raw_pred": pre_dog_max_at_raw_pred,
		"post_dog_max_at_raw_pred": post_dog_max_at_raw_pred,
		"validity_at_raw_pred": validity_at_raw_pred,
		"roi_x1": roi_x1,
		"roi_y1": roi_y1,
		"roi_x2": roi_x2,
		"roi_y2": roi_y2,
		"raw_pred_in_roi": raw_pred_in_roi,
		"lost_at_stage": lost_at,
		"corridor_blobs_json": corridor_blobs_json,
		"torso_w": w_value,
		"torso_h": h_value,
	}

	return verdict_row


#============================================
def write_verdicts_csv(output_path: str, verdicts: list) -> None:
	"""Write verdicts to CSV file.

	Args:
		output_path: Path to output CSV.
		verdicts: List of verdict dicts.
	"""
	if not verdicts:
		return

	fieldnames = list(verdicts[0].keys())
	with open(output_path, 'w', newline='') as f:
		writer = csv.DictWriter(f, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(verdicts)


#============================================
def write_verdicts_json(output_path: str, verdicts: list) -> None:
	"""Write verdicts to JSON file.

	Args:
		output_path: Path to output JSON.
		verdicts: List of verdict dicts.
	"""
	with open(output_path, 'w') as f:
		json.dump(verdicts, f, indent=2)


#============================================
def build_contact_sheet(
	png_dir: str,
	output_path: str,
	frame_indices: list,
	verdicts_by_frame: dict,
) -> None:
	"""Build 8-column contact sheet from per-frame PNGs.

	Each thumbnail is downsampled from source-native resolution to 320x180.
	Outline color: green=accepted, red=rejected, gray=absent, dimgray=skipped.

	Args:
		png_dir: Directory containing per-frame PNG files.
		output_path: Path where contact sheet PNG is written.
		frame_indices: Sorted list of frame indices to include.
		verdicts_by_frame: Dict mapping frame_index -> verdict dict.
	"""
	if not frame_indices:
		return

	cols = 8
	thumb_w = 320
	thumb_h = 180
	border_width = 3

	rows = (len(frame_indices) + cols - 1) // cols
	total_w = cols * (thumb_w + 2 * border_width)
	total_h = rows * (thumb_h + 2 * border_width)

	contact = Image.new('RGB', (total_w, total_h), color=(20, 20, 20))

	for idx, frame_num in enumerate(frame_indices):
		row = idx // cols
		col = idx % cols

		verdict = verdicts_by_frame.get(frame_num)
		gate = verdict['gate'] if verdict else 'unknown'

		if gate == "accepted":
			outline_color = (0, 255, 0)
		elif gate == "rejected":
			outline_color = (255, 0, 0)
		elif gate == "absent":
			outline_color = (128, 128, 128)
		elif gate == "skipped":
			outline_color = (64, 64, 64)
		else:
			outline_color = (255, 255, 255)

		# render_gate_trace_png writes files as f"{pass_name}_{frame_idx:06d}.png".
		# Recover pass_name from the per-frame directory basename (FWD or BWD).
		pass_name = os.path.basename(os.path.normpath(png_dir))
		png_name = f"{pass_name}_{frame_num:06d}.png"
		png_path = os.path.join(png_dir, png_name)

		if os.path.isfile(png_path):
			thumb_img = Image.open(png_path).convert('RGB')
			thumb_img.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
		else:
			thumb_img = Image.new('RGB', (thumb_w, thumb_h), color=(80, 80, 80))

		x = col * (thumb_w + 2 * border_width) + border_width
		y = row * (thumb_h + 2 * border_width) + border_width
		contact.paste(thumb_img, (x, y))

		pixels = contact.load()
		for bx in range(x - border_width, x + thumb_w + border_width):
			for by in range(y - border_width, y + thumb_h + border_width):
				if bx < 0 or bx >= total_w or by < 0 or by >= total_h:
					continue
				if bx < x or bx >= x + thumb_w or by < y or by >= y + thumb_h:
					pixels[bx, by] = outline_color

	contact.save(output_path)


#============================================
def load_interval_scores(interval_dir: str) -> dict:
	"""Load interval_scores.json if present.

	Args:
		interval_dir: Path to interval directory.

	Returns:
		Dict with scores data, or empty dict if file missing.
	"""
	scores_path = os.path.join(interval_dir, "interval_scores.json")
	if not os.path.exists(scores_path):
		return {}

	with open(scores_path, 'r') as f:
		scores_data = json.load(f)
	return scores_data


#============================================
def load_classification_tag(interval_dir: str) -> str:
	"""Load classification tag from CLASSIFICATION.md if present.

	Args:
		interval_dir: Path to interval directory.

	Returns:
		Classification tag string, or empty string if file missing.
	"""
	class_path = os.path.join(interval_dir, "CLASSIFICATION.md")
	if not os.path.exists(class_path):
		return ""

	with open(class_path, 'r') as f:
		content = f.read()

	lines = content.strip().split('\n')
	for line in lines:
		line = line.strip()
		if line and not line.startswith('#'):
			return line

	return ""


#============================================
def load_control_marker(interval_dir: str) -> str:
	"""Load control marker from .control_marker sentinel file if present.

	Args:
		interval_dir: Path to interval directory.

	Returns:
		Control marker string (e.g. 'positive', 'negative'), or empty string.
	"""
	marker_path = os.path.join(interval_dir, ".control_marker")
	if not os.path.exists(marker_path):
		return ""

	with open(marker_path, 'r') as f:
		content = f.read().strip()
	return content


#============================================
def find_first_contact_sheet(interval_dir: str) -> str:
	"""Find the first contact sheet PNG in interval directory.

	Args:
		interval_dir: Path to interval directory.

	Returns:
		Relative path to contact sheet (relative to interval dir root).
	"""
	for filename in os.listdir(interval_dir):
		if filename.startswith("contact_sheet") and filename.endswith(".png"):
			return filename

	return ""


#============================================
def find_first_per_frame_png(interval_dir: str, pass_name: str) -> str:
	"""Find the first per-frame PNG for a given pass.

	Args:
		interval_dir: Path to interval directory.
		pass_name: "FWD" or "BWD".

	Returns:
		Relative path to PNG (relative to interval dir root), or empty string.
	"""
	per_frame_dir = os.path.join(interval_dir, "per_frame", pass_name)
	if not os.path.isdir(per_frame_dir):
		return ""

	png_files = [f for f in os.listdir(per_frame_dir) if f.endswith(".png")]
	if png_files:
		png_files.sort()
		return os.path.join("per_frame", pass_name, png_files[0])

	return ""


#============================================
def escape_html(text: str) -> str:
	"""Escape HTML special characters.

	Args:
		text: String to escape.

	Returns:
		Escaped string safe for HTML.
	"""
	text = text.replace("&", "&amp;")
	text = text.replace("<", "&lt;")
	text = text.replace(">", "&gt;")
	text = text.replace('"', "&quot;")
	return text


#============================================
def build_html_index(root_dir: str, output_path: str) -> None:
	"""Build HTML index for all interval directories under root_dir.

	Args:
		root_dir: Root directory to scan for interval_* subdirs.
		output_path: Path where BLOB_GATE_INDEX.html is written.
	"""
	intervals = []
	for item in os.listdir(root_dir):
		item_path = os.path.join(root_dir, item)
		if not os.path.isdir(item_path):
			continue
		if not item.startswith("interval_"):
			continue

		parts = item.split("_")
		if len(parts) >= 3:
			start_frame_str = parts[1]
			end_frame_str = parts[2]
			label = f"{start_frame_str}-{end_frame_str}"
		else:
			label = item

		scores = load_interval_scores(item_path)
		blob_accept_pct = scores.get("blob_accept_percentage", "")
		tier = scores.get("confidence_tier", "")

		classification = load_classification_tag(item_path)
		control_marker = load_control_marker(item_path)
		contact_sheet = find_first_contact_sheet(item_path)
		png_fwd = find_first_per_frame_png(item_path, "FWD")
		png_bwd = find_first_per_frame_png(item_path, "BWD")

		intervals.append({
			"label": label,
			"item": item,
			"blob_accept_pct": blob_accept_pct,
			"tier": tier,
			"classification": classification,
			"control_marker": control_marker,
			"contact_sheet": contact_sheet,
			"png_fwd": png_fwd,
			"png_bwd": png_bwd,
		})

	intervals.sort(key=lambda x: x["label"])

	html_lines = []
	html_lines.append("<!DOCTYPE html>")
	html_lines.append("<html>")
	html_lines.append("<head>")
	html_lines.append("<meta charset='UTF-8'>")
	html_lines.append("<title>Blob Gate Interval Index</title>")
	html_lines.append("<style>")
	html_lines.append("body { font-family: sans-serif; margin: 20px; }")
	html_lines.append("h1 { color: #333; }")
	html_lines.append("table { border-collapse: collapse; width: 100%; }")
	html_lines.append("th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }")
	html_lines.append("th { background-color: #f0f0f0; font-weight: bold; }")
	html_lines.append("tr:nth-child(even) { background-color: #f9f9f9; }")
	html_lines.append("a { color: #0066cc; text-decoration: none; }")
	html_lines.append("a:hover { text-decoration: underline; }")
	html_lines.append("img { max-width: 200px; height: auto; }")
	html_lines.append("</style>")
	html_lines.append("</head>")
	html_lines.append("<body>")
	html_lines.append("<h1>Blob Gate Interval Index</h1>")
	html_lines.append("<p>Index of interval directories with blob-snap diagnostic data.</p>")
	html_lines.append("<table>")

	html_lines.append("<tr>")
	html_lines.append("<th>Thumbnail</th>")
	html_lines.append("<th>Interval</th>")
	html_lines.append("<th>Blob Accept %</th>")
	html_lines.append("<th>Tier</th>")
	html_lines.append("<th>Classification</th>")
	html_lines.append("<th>Control Marker</th>")
	html_lines.append("<th>Links</th>")
	html_lines.append("</tr>")

	for interval in intervals:
		html_lines.append("<tr>")

		thumbnail_html = ""
		if interval["contact_sheet"]:
			contact_path = os.path.join(interval["item"], interval["contact_sheet"])
			contact_path = escape_html(contact_path)
			thumbnail_html = (
				f"<img src='{contact_path}' alt='contact sheet' "
				f"style='max-width: 120px; height: auto;'>"
			)
		html_lines.append(f"<td>{thumbnail_html}</td>")

		interval_label = escape_html(interval["label"])
		html_lines.append(f"<td>{interval_label}</td>")

		blob_pct_text = ""
		if interval["blob_accept_pct"]:
			blob_pct_text = f"{interval['blob_accept_pct']:.1f}%" if isinstance(interval["blob_accept_pct"], (int, float)) else str(interval["blob_accept_pct"])
		html_lines.append(f"<td>{blob_pct_text}</td>")

		tier_text = escape_html(interval["tier"]) if interval["tier"] else ""
		html_lines.append(f"<td>{tier_text}</td>")

		classification_text = escape_html(interval["classification"]) if interval["classification"] else ""
		html_lines.append(f"<td>{classification_text}</td>")

		control_text = escape_html(interval["control_marker"]) if interval["control_marker"] else ""
		html_lines.append(f"<td>{control_text}</td>")

		links_html = ""
		link_parts = []
		if interval["png_fwd"]:
			png_path = os.path.join(interval["item"], interval["png_fwd"])
			png_path = escape_html(png_path)
			link_parts.append(f"<a href='{png_path}'>FWD</a>")
		if interval["png_bwd"]:
			png_path = os.path.join(interval["item"], interval["png_bwd"])
			png_path = escape_html(png_path)
			link_parts.append(f"<a href='{png_path}'>BWD</a>")

		links_html = " | ".join(link_parts)
		html_lines.append(f"<td>{links_html}</td>")

		html_lines.append("</tr>")

	html_lines.append("</table>")
	html_lines.append("</body>")
	html_lines.append("</html>")

	html_content = "\n".join(html_lines)

	with open(output_path, 'w') as f:
		f.write(html_content)


#============================================
def main() -> None:
	"""Main entry point."""
	args = parse_args()

	# Validate that video_path is provided for non-index modes
	if args.build_html_index is None and args.video_path is None:
		raise RuntimeError(
			"--video is required for frame or interval modes. "
			"Use --build-html-index instead for HTML index mode."
		)

	# Handle HTML index mode (WP-2B)
	if args.build_html_index is not None:
		root_dir = args.build_html_index
		if not os.path.isdir(root_dir):
			raise RuntimeError(f"Directory not found: {root_dir}")

		print(f"Building HTML index for {root_dir}...")
		output_path = os.path.join(root_dir, "BLOB_GATE_INDEX.html")
		build_html_index(root_dir, output_path)
		print(f"  Wrote: {output_path}")
		return

	# Determine output directory
	if args.output_dir is None:
		video_basename = os.path.basename(args.video_path)
		video_basename = os.path.splitext(video_basename)[0]
		output_dir = os.path.join(
			_REPO_ROOT, "output_smoke", "blob_refinement", video_basename
		)
	else:
		output_dir = args.output_dir

	ensure_output_directory(output_dir)

	# Handle interval mode (WP-2A)
	if args.interval_range is not None:
		start_seed_frame = args.interval_range[0]
		end_seed_frame = args.interval_range[1]
		print(f"Visualizing blob gates for interval [{start_seed_frame}, {end_seed_frame}]...")

		# Load video and probe
		print(f"Loading video: {args.video_path}")
		if not os.path.exists(args.video_path):
			raise RuntimeError(f"Video file not found: {args.video_path}")

		probe_info = probe_video.probe_video(args.video_path)
		width = probe_info.get("width", "?")
		height = probe_info.get("height", "?")
		frame_count = probe_info["frame_count"]
		print(f"  Video: {width}x{height}, {frame_count} frames")

		# Load scene transform
		print("Loading scene transform...")
		scene_transform = load_scene_transform(args.video_path)

		# Load seeds and find the target interval
		print("Loading seeds...")
		seeds_path = tr_paths.default_seeds_path(args.video_path)
		if not os.path.exists(seeds_path):
			raise RuntimeError(f"Seeds file not found: {seeds_path}")

		seeds_data = state_io.load_seeds(seeds_path)
		seeds_list = seeds_data["seeds"]

		start_seed = None
		end_seed = None
		for seed in seeds_list:
			if seed["frame_index"] == start_seed_frame:
				start_seed = seed
			if seed["frame_index"] == end_seed_frame:
				end_seed = seed

		if start_seed is None or end_seed is None:
			raise RuntimeError(
				f"Seeds not found for interval [{start_seed_frame}, {end_seed_frame}]. "
				f"Available seeds at frames: {[s['frame_index'] for s in seeds_list]}"
			)

		print(f"  Interval: seeds at frames {start_seed['frame_index']} -> {end_seed['frame_index']}")

		# Open video reader
		print("Opening video reader...")
		fps = float(probe_info["fps"])
		frame_count = probe_info["frame_count"]
		reader = frame_reader.FrameReader(args.video_path, fps, frame_count)
		print(f"  Reader opened: {reader.frame_count} frames, {reader.width}x{reader.height}")

		# Build interval curves
		start_frame = start_seed["frame_index"]
		end_frame = end_seed["frame_index"]

		start_cx = float(start_seed["cx"])
		start_cy = float(start_seed["cy"])
		start_w = float(start_seed["w"])
		start_h = float(start_seed["h"])

		end_cx = float(end_seed["cx"])
		end_cy = float(end_seed["cy"])
		end_w = float(end_seed["w"])
		end_h = float(end_seed["h"])

		start_sx, start_sy, start_sw, start_sh = scene_transform.pixel_box_to_scene(
			start_frame, start_cx, start_cy, start_w, start_h
		)
		end_sx, end_sy, end_sw, end_sh = scene_transform.pixel_box_to_scene(
			end_frame, end_cx, end_cy, end_w, end_h
		)

		frame_diff = float(end_frame - start_frame)
		if frame_diff > 0:
			fwd_slope_x = (end_sx - start_sx) / frame_diff
			fwd_slope_y = (end_sy - start_sy) / frame_diff
			bwd_slope_x = (end_sx - start_sx) / frame_diff
			bwd_slope_y = (end_sy - start_sy) / frame_diff
			fwd_size_slope_w = (math.log(end_sw) - math.log(start_sw)) / frame_diff if start_sw > 1e-6 and end_sw > 1e-6 else 0.0
			fwd_size_slope_h = (math.log(end_sh) - math.log(start_sh)) / frame_diff if start_sh > 1e-6 and end_sh > 1e-6 else 0.0
			bwd_size_slope_w = fwd_size_slope_w
			bwd_size_slope_h = fwd_size_slope_h
		else:
			fwd_slope_x = fwd_slope_y = bwd_slope_x = bwd_slope_y = 0.0
			fwd_size_slope_w = fwd_size_slope_h = bwd_size_slope_w = bwd_size_slope_h = 0.0

		interval_curves_fwd = {
			"start_frame": start_frame,
			"end_frame": end_frame,
			"left_pos": (start_sx, start_sy),
			"right_pos": (end_sx, end_sy),
			"left_size": (start_sw, start_sh),
			"right_size": (end_sw, end_sh),
			"fwd_slopes": (fwd_slope_x, fwd_slope_y),
			"fwd_size_slopes": (fwd_size_slope_w, fwd_size_slope_h),
			"bwd_slopes": (bwd_slope_x, bwd_slope_y),
			"bwd_size_slopes": (bwd_size_slope_w, bwd_size_slope_h),
		}

		interval_curves_bwd = dict(interval_curves_fwd)

		# Compute raw predictions
		print("Computing raw predictions...")
		raw_pred_fwd = velocity_model._compute_raw_pred_forward(
			interval_curves_fwd, scene_transform
		)
		raw_pred_bwd = velocity_model._compute_raw_pred_backward(
			interval_curves_bwd, scene_transform
		)

		print(f"  Forward pass: {len(raw_pred_fwd)} frames")
		print(f"  Backward pass: {len(raw_pred_bwd)} frames")

		# Build precomputed store if requested
		residual_cache = {}
		precomputed_store = None
		if args.reader_path == "prepass":
			print("Pre-computing residuals...")
			half_window = 8
			fps = probe_info["fps"]
			stride = 1

			precomputed_store = residual_pre_pass.precompute_interval_residuals(
				reader, scene_transform,
				start_seed, end_seed,
				raw_pred_fwd, raw_pred_bwd,
				half_window=half_window,
				fps=fps,
				stride=stride,
			)
			print(f"  Store keys: {len(precomputed_store) if precomputed_store else 0}")

		# Create interval output directory
		interval_output_dir = os.path.join(
			output_dir, f"interval_{start_frame}_{end_frame}"
		)
		ensure_output_directory(interval_output_dir)

		# Per-frame output directories (skip if --no-png)
		if not args.no_png:
			per_frame_dir_fwd = os.path.join(interval_output_dir, "per_frame", "FWD")
			per_frame_dir_bwd = os.path.join(interval_output_dir, "per_frame", "BWD")
			ensure_output_directory(per_frame_dir_fwd)
			ensure_output_directory(per_frame_dir_bwd)
		else:
			per_frame_dir_fwd = None
			per_frame_dir_bwd = None

		# Apply blob snap and collect traces, build verdicts
		print("Running blob-snap gating...")
		all_verdicts = []
		all_traces_fwd = []
		all_traces_bwd = []
		verdicts_by_frame_pass = {}

		# Forward pass
		if args.pass_name in ["fwd", "both"]:
			print("  FWD pass...")
			velocity_model._apply_blob_snap(
				raw_pred_fwd,
				reader,
				scene_transform,
				residual_cache,
				blob_snap_enabled=True,
				precomputed_store=precomputed_store,
				trace_sink=all_traces_fwd,
			)
			for trace in all_traces_fwd:
				trace.pass_name = "FWD"
			print(f"    Captured {len(all_traces_fwd)} traces")

		# Backward pass
		if args.pass_name in ["bwd", "both"]:
			print("  BWD pass...")
			velocity_model._apply_blob_snap(
				raw_pred_bwd,
				reader,
				scene_transform,
				residual_cache,
				blob_snap_enabled=True,
				precomputed_store=precomputed_store,
				trace_sink=all_traces_bwd,
			)
			for trace in all_traces_bwd:
				trace.pass_name = "BWD"
			print(f"    Captured {len(all_traces_bwd)} traces")

		# Build verdicts and render per-frame PNGs
		print("Building verdicts and rendering...")
		png_count = 0

		# Process FWD traces
		for trace in all_traces_fwd:
			frame_idx = trace.frame_index
			h_value = trace.raw_curr[4] if trace.raw_curr and len(trace.raw_curr) > 4 else 1.0
			w_value = trace.raw_curr[3] if trace.raw_curr and len(trace.raw_curr) > 3 else None
			verdict = build_verdict_row(
				trace, frame_idx, "FWD", h_value, w_value=w_value,
				write_corridor_blob_list=args.write_corridor_blob_list
			)
			all_verdicts.append(verdict)
			verdicts_by_frame_pass[(frame_idx, "FWD")] = verdict

			if not args.no_png:
				source_bgr_frame = reader.read_frame(frame_idx)
				output_filename = f"FWD_{frame_idx:06d}.png"
				output_path = os.path.join(per_frame_dir_fwd, output_filename)
				render_gate_trace_png(trace, source_bgr_frame, output_path)
				png_count += 1

		# Process BWD traces
		for trace in all_traces_bwd:
			frame_idx = trace.frame_index
			h_value = trace.raw_curr[4] if trace.raw_curr and len(trace.raw_curr) > 4 else 1.0
			w_value = trace.raw_curr[3] if trace.raw_curr and len(trace.raw_curr) > 3 else None
			verdict = build_verdict_row(
				trace, frame_idx, "BWD", h_value, w_value=w_value,
				write_corridor_blob_list=args.write_corridor_blob_list
			)
			all_verdicts.append(verdict)
			verdicts_by_frame_pass[(frame_idx, "BWD")] = verdict

			if not args.no_png:
				source_bgr_frame = reader.read_frame(frame_idx)
				output_filename = f"BWD_{frame_idx:06d}.png"
				output_path = os.path.join(per_frame_dir_bwd, output_filename)
				render_gate_trace_png(trace, source_bgr_frame, output_path)
				png_count += 1

		if png_count > 0:
			print(f"  Rendered {png_count} per-frame PNGs")
		else:
			print("  PNG rendering skipped (--no-png)")

		# Write verdicts
		verdicts_csv_path = os.path.join(interval_output_dir, "verdicts.csv")
		verdicts_json_path = os.path.join(interval_output_dir, "verdicts.json")
		write_verdicts_csv(verdicts_csv_path, all_verdicts)
		write_verdicts_json(verdicts_json_path, all_verdicts)
		print(f"  Wrote verdicts to {verdicts_csv_path}")
		print(f"  Wrote verdicts to {verdicts_json_path}")

		# Build contact sheets for each pass (skip if --no-png)
		if not args.no_png:
			if args.pass_name in ["fwd", "both"]:
				frame_indices_fwd = sorted(set(t.frame_index for t in all_traces_fwd))
				verdicts_fwd = {f: verdicts_by_frame_pass.get((f, "FWD"), {}) for f in frame_indices_fwd}
				contact_path_fwd = os.path.join(interval_output_dir, "contact_sheet_FWD.png")
				build_contact_sheet(per_frame_dir_fwd, contact_path_fwd, frame_indices_fwd, verdicts_fwd)
				print(f"  Wrote FWD contact sheet to {contact_path_fwd}")

			if args.pass_name in ["bwd", "both"]:
				frame_indices_bwd = sorted(set(t.frame_index for t in all_traces_bwd))
				verdicts_bwd = {f: verdicts_by_frame_pass.get((f, "BWD"), {}) for f in frame_indices_bwd}
				contact_path_bwd = os.path.join(interval_output_dir, "contact_sheet_BWD.png")
				build_contact_sheet(per_frame_dir_bwd, contact_path_bwd, frame_indices_bwd, verdicts_bwd)
				print(f"  Wrote BWD contact sheet to {contact_path_bwd}")

		print(f"Done. Output at {interval_output_dir}")
		reader.close()
		return

	# Single-frame mode
	frame_index = args.frame_index
	print(f"Visualizing blob gates for frame {frame_index}...")

	# Load video and probe
	print(f"Loading video: {args.video_path}")
	if not os.path.exists(args.video_path):
		raise RuntimeError(f"Video file not found: {args.video_path}")

	probe_info = probe_video.probe_video(args.video_path)
	width = probe_info.get("width", "?")
	height = probe_info.get("height", "?")
	frame_count = probe_info["frame_count"]
	print(f"  Video: {width}x{height}, {frame_count} frames")

	# Load scene transform
	print("Loading scene transform...")
	scene_transform = load_scene_transform(args.video_path)

	# Load seeds and find bracketing pair
	print("Loading seeds...")
	start_seed, end_seed, seeds_list = load_seeds_and_find_bracketing_pair(
		args.video_path, frame_index
	)

	print(f"  Frame {frame_index} is bracketed by seeds at frames "
		f"{start_seed['frame_index']} and {end_seed['frame_index']}")

	# Open video reader
	print("Opening video reader...")
	fps = float(probe_info["fps"])
	frame_count = probe_info["frame_count"]
	reader = frame_reader.FrameReader(args.video_path, fps, frame_count)
	print(f"  Reader opened: {reader.frame_count} frames, "
		f"{reader.width}x{reader.height}")

	# Build interval curves for both passes
	# Extract required fields from seeds and convert to scene coords
	start_frame = start_seed["frame_index"]
	end_frame = end_seed["frame_index"]

	# Derive geometry from torso_box (cx/cy/w/h are already derived by state_io)
	start_cx = float(start_seed["cx"])
	start_cy = float(start_seed["cy"])
	start_w = float(start_seed["w"])
	start_h = float(start_seed["h"])

	end_cx = float(end_seed["cx"])
	end_cy = float(end_seed["cy"])
	end_w = float(end_seed["w"])
	end_h = float(end_seed["h"])

	start_sx, start_sy, start_sw, start_sh = scene_transform.pixel_box_to_scene(
		start_frame, start_cx, start_cy, start_w, start_h
	)
	end_sx, end_sy, end_sw, end_sh = scene_transform.pixel_box_to_scene(
		end_frame, end_cx, end_cy, end_w, end_h
	)

	# Estimate directional slopes
	# For now, use simple finite difference for single-pair bracketing
	frame_diff = float(end_frame - start_frame)
	if frame_diff > 0:
		fwd_slope_x = (end_sx - start_sx) / frame_diff
		fwd_slope_y = (end_sy - start_sy) / frame_diff
		bwd_slope_x = (end_sx - start_sx) / frame_diff
		bwd_slope_y = (end_sy - start_sy) / frame_diff
		fwd_size_slope_w = (math.log(end_sw) - math.log(start_sw)) / frame_diff if start_sw > 1e-6 and end_sw > 1e-6 else 0.0
		fwd_size_slope_h = (math.log(end_sh) - math.log(start_sh)) / frame_diff if start_sh > 1e-6 and end_sh > 1e-6 else 0.0
		bwd_size_slope_w = fwd_size_slope_w
		bwd_size_slope_h = fwd_size_slope_h
	else:
		fwd_slope_x = fwd_slope_y = bwd_slope_x = bwd_slope_y = 0.0
		fwd_size_slope_w = fwd_size_slope_h = bwd_size_slope_w = bwd_size_slope_h = 0.0

	# Build interval curve dicts
	interval_curves_fwd = {
		"start_frame": start_frame,
		"end_frame": end_frame,
		"left_pos": (start_sx, start_sy),
		"right_pos": (end_sx, end_sy),
		"left_size": (start_sw, start_sh),
		"right_size": (end_sw, end_sh),
		"fwd_slopes": (fwd_slope_x, fwd_slope_y),
		"fwd_size_slopes": (fwd_size_slope_w, fwd_size_slope_h),
		"bwd_slopes": (bwd_slope_x, bwd_slope_y),
		"bwd_size_slopes": (bwd_size_slope_w, bwd_size_slope_h),
	}

	interval_curves_bwd = dict(interval_curves_fwd)

	# Compute raw predictions
	print("Computing raw predictions...")
	raw_pred_fwd = velocity_model._compute_raw_pred_forward(
		interval_curves_fwd, scene_transform
	)
	raw_pred_bwd = velocity_model._compute_raw_pred_backward(
		interval_curves_bwd, scene_transform
	)

	print(f"  Forward pass: {len(raw_pred_fwd)} frames")
	print(f"  Backward pass: {len(raw_pred_bwd)} frames")

	# Build precomputed store if requested
	residual_cache = {}
	precomputed_store = None
	if args.reader_path == "prepass":
		print("Pre-computing residuals...")
		# Extract blob-snap configuration from velocity_model
		half_window = 8
		fps = probe_info["fps"]
		stride = 1

		precomputed_store = residual_pre_pass.precompute_interval_residuals(
			reader, scene_transform,
			start_seed, end_seed,
			raw_pred_fwd, raw_pred_bwd,
			half_window=half_window,
			fps=fps,
			stride=stride,
		)
		print(f"  Store keys: {len(precomputed_store) if precomputed_store else 0}")

	# Apply blob snap and collect traces
	print("Running blob-snap gating...")
	fwd_traces = []
	bwd_traces = []

	# Forward pass
	if args.pass_name in ["fwd", "both"]:
		print("  FWD pass...")
		velocity_model._apply_blob_snap(
			raw_pred_fwd,
			reader,
			scene_transform,
			residual_cache,
			blob_snap_enabled=True,
			precomputed_store=precomputed_store,
			trace_sink=fwd_traces,
		)
		# Annotate traces with pass name
		for trace in fwd_traces:
			trace.pass_name = "FWD"
		print(f"    Captured {len(fwd_traces)} traces")

	# Backward pass
	if args.pass_name in ["bwd", "both"]:
		print("  BWD pass...")
		velocity_model._apply_blob_snap(
			raw_pred_bwd,
			reader,
			scene_transform,
			residual_cache,
			blob_snap_enabled=True,
			precomputed_store=precomputed_store,
			trace_sink=bwd_traces,
		)
		# Annotate traces with pass name
		for trace in bwd_traces:
			trace.pass_name = "BWD"
		print(f"    Captured {len(bwd_traces)} traces")

	# Render traces
	print("Rendering...")
	all_traces = []
	if args.pass_name in ["fwd", "both"]:
		all_traces.extend(fwd_traces)
	if args.pass_name in ["bwd", "both"]:
		all_traces.extend(bwd_traces)

	png_count = 0
	for trace in all_traces:
		# Only render for the requested frame in single-frame mode
		if trace.frame_index != frame_index:
			continue

		output_filename = (
			f"frame_{trace.frame_index:05d}_{trace.pass_name}_"
			f"gate_{trace.blob_gate}.png"
		)
		output_path = os.path.join(output_dir, output_filename)

		# Read the source frame for context
		source_bgr_frame = reader.read_frame(trace.frame_index)

		# Render (stub; WP-1B replaces)
		render_gate_trace_png(trace, source_bgr_frame, output_path)
		print(f"  Wrote: {output_filename}")
		png_count += 1

	print(f"Done. Rendered {png_count} PNGs to {output_dir}")

	if png_count == 0:
		raise RuntimeError(
			"No frames were rendered. Check frame index and bracketing seeds."
		)

	reader.close()


#============================================
if __name__ == "__main__":
	main()
