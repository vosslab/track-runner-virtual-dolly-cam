#!/usr/bin/env python3
"""M4/M5 benchmark gate evaluation for track_runner analytical solver.

Re-solves each of 7 test videos from seeds using the analytical solver,
evaluates all gate thresholds from Plan 07 acceptance criteria, and writes
an audit artifact with measured values and pass/fail results.

Outputs:
  output_smoke/benchmark_gates.txt  -- audit artifact with gate results
  output_smoke/benchmark_encodes/   -- encoded videos (--encode only)
"""

# Standard Library
import os
import json
import statistics
import subprocess
import sys
import time

# determine repo root
REPO_ROOT = subprocess.run(
	["git", "rev-parse", "--show-toplevel"],
	capture_output=True, text=True, check=True,
).stdout.strip()

# add track_runner to sys.path so bare imports work (matches runtime behavior)
sys.path.insert(0, os.path.join(REPO_ROOT, "track_runner"))

# PIP3 modules
import numpy

# local repo modules (bare imports matching runtime behavior)
import camera_motion
import interval_solver
import scene_coords
import scoring
import state_io
import tr_config
import tr_paths
import video_io

# ============================================================
# Path constants
# ============================================================

DATA_DIR = os.path.join(REPO_ROOT, "tr_config")
VIDEO_DIR = os.path.join(REPO_ROOT, "TRACK_VIDEOS")
OUTPUT_DIR = os.path.join(REPO_ROOT, "output_smoke")

# ============================================================
# Video registry: all 7 benchmark videos
# ============================================================

VIDEO_REGISTRY = {
	"canon_60d_600m_zoom.MP4": "canon_60d (telephoto crowded)",
	"Hononega-Orion_600m-IMG_3702.mkv": "IMG_3702 (key failure case)",
	"Hononega-Varsity_4x400m-IMG_3707.mkv": "IMG_3707 (relay extreme)",
	"IMG_3627.MOV": "IMG_3627",
	"IMG_3629.mkv": "IMG_3629 (sparse long-gap)",
	"IMG_3823.MP4": "IMG_3823 (zoom variation)",
	"IMG_3830.MP4": "IMG_3830 (dense refined)",
}

# ============================================================
# Gate threshold constants -- Plan 07 lines 896-923
# ============================================================

# primary cross-video gates (meeting_point source)
GATE_NORMALIZED_CENTER_ERROR = 0.25
GATE_BOX_HEIGHT_ERROR = 0.10

# per-regime gates
GATE_IMG3702_CENTER_ERROR_PX = 20.0          # meeting_point
GATE_IMG3629_LOW_CONFIDENCE_FRACTION = 0.15  # interval_score
GATE_IMG3629_NORMALIZED_CENTER_ERROR = 0.25  # meeting_point
GATE_IMG3830_AGREEMENT_MEDIAN = 0.75         # interval_score
GATE_IMG3830_NORMALIZED_CENTER_ERROR = 0.10  # meeting_point
GATE_CANON60D_NORMALIZED_CENTER_ERROR = 0.25 # meeting_point
GATE_CANON60D_HIGH_CONFIDENCE_SHARE = 0.40   # interval_score
GATE_CANON60D_FAIR_LOW_FRACTION = 0.30       # interval_score
GATE_IMG3823_HEIGHT_JERK_P95 = 69.0          # trajectory_stability

# structural
GATE_MIN_VIDEOS_SOLVE = 3

# ============================================================
# Module-level solve cache
# ============================================================

_SOLVE_CACHE = {}


#============================================
def parse_args():
	"""Parse command-line arguments.

	Returns:
		argparse.Namespace with video, encode, verbose fields.
	"""
	import argparse
	parser = argparse.ArgumentParser(
		description="M4/M5 benchmark gate evaluation for track_runner analytical solver.",
	)
	parser.add_argument(
		'-v', '--video', dest='video', type=str, default=None,
		help="Run only this video filename (default: all 7)",
	)
	parser.add_argument(
		'-e', '--encode', dest='encode', action='store_true',
		help="Also encode videos for human visual review",
	)
	parser.add_argument(
		'-V', '--verbose', dest='verbose', action='store_true',
		help="Print per-frame metric details",
	)
	args = parser.parse_args()
	return args


#============================================
def probe_video(video_path: str) -> dict:
	"""Probe video metadata via mediainfo.

	Args:
		video_path: Path to video file.

	Returns:
		Dict with width, height, fps, frame_count, duration_s.
	"""
	cmd = ["mediainfo", "--Output=JSON", video_path]
	result = subprocess.run(cmd, capture_output=True, text=True)
	if result.returncode != 0:
		raise RuntimeError(f"mediainfo failed: {result.stderr}")
	info_json = json.loads(result.stdout)
	tracks = info_json.get("media", {}).get("track", [])
	video_track = None
	general_track = None
	for t in tracks:
		if t.get("@type") == "Video":
			video_track = t
		elif t.get("@type") == "General":
			general_track = t
	if video_track is None:
		raise RuntimeError(f"no video track in {video_path}")
	width = int(video_track["Width"])
	height = int(video_track["Height"])
	fps = float(video_track.get("FrameRate", "30.0"))
	frame_count = int(
		video_track.get("FrameCount") or general_track.get("FrameCount", 0)
	)
	duration_s = float(
		video_track.get("Duration") or general_track.get("Duration", 0)
	)
	video_info = {
		"width": width, "height": height, "fps": fps,
		"frame_count": frame_count, "duration_s": duration_s,
	}
	return video_info


#============================================
def _video_path(video_name: str) -> str:
	"""Return full path to a video file."""
	return os.path.join(VIDEO_DIR, video_name)


#============================================
def _seeds_path(video_name: str) -> str:
	"""Return full path to seeds JSON for a video."""
	return os.path.join(DATA_DIR, video_name + ".track_runner.seeds.json")


#============================================
def _config_path(video_name: str) -> str:
	"""Return full path to per-video config YAML."""
	return os.path.join(DATA_DIR, video_name + ".track_runner.config.yaml")


#============================================
def check_video_ready(video_name: str) -> tuple:
	"""Check whether a video is ready for benchmarking.

	Args:
		video_name: Video filename from VIDEO_REGISTRY.

	Returns:
		Tuple of (ready: bool, reason: str or None).
		If ready is False and reason starts with "skip:", the video should
		be skipped. If reason starts with "ERROR:", it is a hard failure.
	"""
	vpath = _video_path(video_name)
	spath = _seeds_path(video_name)
	cpath = _config_path(video_name)

	# video file absent -> skip
	if not os.path.isfile(vpath):
		reason = f"skip: video not on this machine: {vpath}"
		return (False, reason)

	# video present but seeds missing -> hard error
	if not os.path.isfile(spath):
		reason = (
			f"ERROR: seeds missing for {video_name}\n"
			f"       Expected: {spath}"
		)
		return (False, reason)

	# video present but config missing -> hard error with setup command
	if not os.path.isfile(cpath):
		reason = (
			f"ERROR: camera config missing for {video_name}\n"
			f"       Expected: {cpath}\n"
			f"       Run: source source_me.sh && python3 "
			f"track_runner/track_runner.py -i TRACK_VIDEOS/{video_name} setup"
		)
		return (False, reason)

	# config present but missing camera.zoom_type
	cfg = tr_config.load_config(cpath)
	if "camera" not in cfg or "zoom_type" not in cfg.get("camera", {}):
		reason = (
			f"ERROR: camera.zoom_type missing in {cpath}\n"
			f"       Re-run: source source_me.sh && python3 "
			f"track_runner/track_runner.py -i TRACK_VIDEOS/{video_name} setup"
		)
		return (False, reason)

	return (True, None)


#============================================
def solve_video(video_name: str) -> dict:
	"""Solve a video using the analytical solver pipeline.

	Caches results in _SOLVE_CACHE so multiple gate checks share one solve.

	Args:
		video_name: Video filename from VIDEO_REGISTRY.

	Returns:
		Dict with keys: intervals, trajectory, seeds, video_info, config.
	"""
	# check cache
	if video_name in _SOLVE_CACHE:
		return _SOLVE_CACHE[video_name]

	vpath = _video_path(video_name)
	spath = _seeds_path(video_name)
	cpath = _config_path(video_name)

	# load seeds and config
	seeds_data = state_io.load_seeds(spath)
	seed_list = seeds_data.get("seeds", [])
	cfg = tr_config.load_config(cpath)

	# probe video metadata
	video_info = probe_video(vpath)

	# precompute camera motion
	cache_dir = tr_paths.ensure_data_dir()
	print(f"  precomputing camera motion for {video_name}...")
	t_start = time.time()
	with video_io.VideoReader(vpath) as reader:
		# camera_motion expects total_frames attribute on reader
		reader.total_frames = reader.frame_count
		motion_track = camera_motion.precompute_camera_motion(
			reader, cfg, vpath, video_info, cache_dir,
		)

	# create scene transform
	scene_transform = scene_coords.SceneTransform(motion_track)

	# filter to seeds that have spatial data (not_in_frame seeds lack cx/cy/w/h
	# and cause KeyError in solve_all_intervals scene coordinate conversion)
	spatial_seeds = [
		s for s in seed_list
		if "cx" in s and "cy" in s and "w" in s and "h" in s
	]

	# run analytical solver
	print(f"  solving intervals ({len(spatial_seeds)} spatial seeds "
		f"of {len(seed_list)} total)...")
	with video_io.VideoReader(vpath) as reader:
		# camera_motion expects total_frames attribute on reader
		reader.total_frames = reader.frame_count
		diagnostics = interval_solver.solve_all_intervals(
			reader, spatial_seeds,
			None,  # no detector for analytical solver
			cfg,
			scene_transform=scene_transform,
			motion_track=motion_track,
			num_workers=1,
		)
	elapsed = time.time() - t_start
	print(f"  solved in {elapsed:.1f}s")

	result = {
		"intervals": diagnostics.get("intervals", []),
		"trajectory": diagnostics.get("trajectory", []),
		"seeds": seed_list,
		"video_info": video_info,
		"config": cfg,
	}
	_SOLVE_CACHE[video_name] = result
	return result


# ============================================================
# Metric extraction helpers
# ============================================================

#============================================
def meeting_point_center_errors(intervals: list) -> list:
	"""Extract per-frame center_err_px from all intervals.

	Args:
		intervals: List of interval result dicts from solve_all_intervals().

	Returns:
		Flat list of center_err_px floats across all frames and intervals.
	"""
	all_errors = []
	for iv in intervals:
		fwd = iv.get("forward_track")
		bwd = iv.get("backward_track")
		if fwd is None or bwd is None:
			continue
		errors = scoring.compute_meeting_point_errors(fwd, bwd)
		for err in errors:
			all_errors.append(err["center_err_px"])
	return all_errors


#============================================
def meeting_point_normalized_errors(intervals: list) -> list:
	"""Extract normalized center errors (center_err_px / mean_box_h).

	Args:
		intervals: List of interval result dicts.

	Returns:
		Flat list of normalized error floats.
	"""
	all_errors = []
	for iv in intervals:
		fwd = iv.get("forward_track")
		bwd = iv.get("backward_track")
		if fwd is None or bwd is None:
			continue
		errors = scoring.compute_meeting_point_errors(fwd, bwd)
		num_frames = min(len(fwd), len(bwd))
		for i, err in enumerate(errors):
			if i >= num_frames:
				break
			# mean box height from fwd and bwd
			mean_h = (fwd[i]["h"] + bwd[i]["h"]) / 2.0
			if mean_h > 0:
				normalized = err["center_err_px"] / mean_h
			else:
				normalized = 1.0
			all_errors.append(normalized)
	return all_errors


#============================================
def meeting_point_scale_errors(intervals: list) -> list:
	"""Extract per-frame scale_err_pct from all intervals.

	Args:
		intervals: List of interval result dicts.

	Returns:
		Flat list of scale_err_pct floats.
	"""
	all_errors = []
	for iv in intervals:
		fwd = iv.get("forward_track")
		bwd = iv.get("backward_track")
		if fwd is None or bwd is None:
			continue
		errors = scoring.compute_meeting_point_errors(fwd, bwd)
		for err in errors:
			all_errors.append(err["scale_err_pct"])
	return all_errors


#============================================
def confidence_tier_counts(intervals: list) -> dict:
	"""Count confidence tiers across all intervals.

	Args:
		intervals: List of interval result dicts.

	Returns:
		Dict mapping tier name to count, e.g. {"high": 3, "good": 2, ...}.
	"""
	counts = {}
	for iv in intervals:
		score = iv.get("interval_score", {})
		tier = score.get("confidence_tier", "unknown")
		counts[tier] = counts.get(tier, 0) + 1
	return counts


#============================================
def agreement_scores(intervals: list) -> list:
	"""Extract agreement floats from all intervals.

	Args:
		intervals: List of interval result dicts.

	Returns:
		List of agreement floats.
	"""
	values = []
	for iv in intervals:
		score = iv.get("interval_score", {})
		agreement = score.get("agreement")
		if agreement is not None:
			values.append(float(agreement))
	return values


#============================================
def height_jerk_p95(trajectory: list) -> float:
	"""Compute 95th percentile of frame-to-frame box height changes.

	Args:
		trajectory: Frame-indexed list where each entry is a tracking
			state dict or None (for gaps).

	Returns:
		95th percentile of abs(h[t] - h[t-1]) in pixels.
		Returns 0.0 if not enough data.
	"""
	jerks = []
	prev_h = None
	for frame in trajectory:
		if frame is None:
			prev_h = None
			continue
		h = frame.get("h", 0)
		if prev_h is not None and h > 0 and prev_h > 0:
			jerk = abs(h - prev_h)
			jerks.append(jerk)
		prev_h = h
	if not jerks:
		return 0.0
	p95 = float(numpy.percentile(jerks, 95))
	return p95


# ============================================================
# Gate evaluation
# ============================================================

#============================================
def _gate_result(
	source: str,
	gate_name: str,
	video: str,
	measured: float,
	threshold: float,
	comparison: str,
	passed: bool,
) -> dict:
	"""Build a gate result dict.

	Args:
		source: Metric source label (meeting_point, interval_score, etc.).
		gate_name: Human-readable gate name.
		video: Video key or "all".
		measured: Measured metric value.
		threshold: Gate threshold value.
		comparison: Comparison operator string (< , <= , >= , etc.).
		passed: Whether the gate passed.

	Returns:
		Dict with all fields plus a "result" string (PASS/FAIL).
	"""
	result_str = "PASS" if passed else "FAIL"
	gate = {
		"source": source,
		"gate_name": gate_name,
		"video": video,
		"measured": measured,
		"threshold": threshold,
		"comparison": comparison,
		"passed": passed,
		"result": result_str,
	}
	return gate


#============================================
def evaluate_cross_video_gates(
	solved: dict,
	verbose: bool = False,
) -> list:
	"""Evaluate cross-video closure gates.

	Args:
		solved: Dict mapping video_name -> solve result for each solved video.
		verbose: Print per-frame details.

	Returns:
		List of gate result dicts.
	"""
	results = []
	for video_name in sorted(solved.keys()):
		data = solved[video_name]
		intervals = data["intervals"]
		# short label for display
		label = video_name.split(".")[0]

		# gate: normalized center error median < 0.25
		norm_errors = meeting_point_normalized_errors(intervals)
		if norm_errors:
			median_val = statistics.median(norm_errors)
			passed = median_val < GATE_NORMALIZED_CENTER_ERROR
			gate = _gate_result(
				"meeting_point", "normalized_center_error (median)",
				label, median_val, GATE_NORMALIZED_CENTER_ERROR, "<", passed,
			)
			results.append(gate)
			_print_gate(gate)
			if verbose:
				print(f"    values: min={min(norm_errors):.4f} "
					f"max={max(norm_errors):.4f} n={len(norm_errors)}")

		# gate: box height error (scale_err_pct) median < 0.10
		scale_errors = meeting_point_scale_errors(intervals)
		if scale_errors:
			median_val = statistics.median(scale_errors)
			passed = median_val < GATE_BOX_HEIGHT_ERROR
			gate = _gate_result(
				"meeting_point", "box_height_error (median)",
				label, median_val, GATE_BOX_HEIGHT_ERROR, "<", passed,
			)
			results.append(gate)
			_print_gate(gate)

		# gate: dice variation -- agreement values not all identical
		agreements = agreement_scores(intervals)
		if len(agreements) >= 2:
			unique_count = len(set(round(a, 6) for a in agreements))
			passed = unique_count > 1
			# report unique count as measured value
			gate = _gate_result(
				"interval_score", "dice_variation (unique values)",
				label, float(unique_count), 2.0, ">=", passed,
			)
			results.append(gate)
			_print_gate(gate)

	return results


#============================================
def evaluate_regime_gates(solved: dict) -> list:
	"""Evaluate per-regime closure gates.

	Args:
		solved: Dict mapping video_name -> solve result.

	Returns:
		List of gate result dicts.
	"""
	results = []

	# helper to find video by substring match
	def _find(prefix: str) -> tuple:
		for vname in solved:
			if prefix in vname:
				return (vname, solved[vname])
		return (None, None)

	# IMG_3702: center_error_px median < 20
	vname, data = _find("IMG_3702")
	if data is not None:
		errors = meeting_point_center_errors(data["intervals"])
		if errors:
			median_val = statistics.median(errors)
			passed = median_val < GATE_IMG3702_CENTER_ERROR_PX
			gate = _gate_result(
				"meeting_point", "center_error_px (median)",
				"IMG_3702", median_val, GATE_IMG3702_CENTER_ERROR_PX, "<", passed,
			)
			results.append(gate)
			_print_gate(gate)

	# IMG_3629: low_confidence_fraction <= 0.15
	vname, data = _find("IMG_3629")
	if data is not None:
		tiers = confidence_tier_counts(data["intervals"])
		total = sum(tiers.values())
		low_count = tiers.get("low", 0)
		if total > 0:
			fraction = low_count / total
			passed = fraction <= GATE_IMG3629_LOW_CONFIDENCE_FRACTION
			gate = _gate_result(
				"interval_score", "low_confidence_fraction",
				"IMG_3629", fraction, GATE_IMG3629_LOW_CONFIDENCE_FRACTION,
				"<=", passed,
			)
			results.append(gate)
			_print_gate(gate)

		# IMG_3629: normalized_center_error < 0.25
		norm_errors = meeting_point_normalized_errors(data["intervals"])
		if norm_errors:
			median_val = statistics.median(norm_errors)
			passed = median_val < GATE_IMG3629_NORMALIZED_CENTER_ERROR
			gate = _gate_result(
				"meeting_point", "normalized_center_error (median)",
				"IMG_3629", median_val, GATE_IMG3629_NORMALIZED_CENTER_ERROR,
				"<", passed,
			)
			results.append(gate)
			_print_gate(gate)

	# IMG_3830: agreement_median >= 0.75
	vname, data = _find("IMG_3830")
	if data is not None:
		agreements = agreement_scores(data["intervals"])
		if agreements:
			median_val = statistics.median(agreements)
			passed = median_val >= GATE_IMG3830_AGREEMENT_MEDIAN
			gate = _gate_result(
				"interval_score", "agreement_median",
				"IMG_3830", median_val, GATE_IMG3830_AGREEMENT_MEDIAN,
				">=", passed,
			)
			results.append(gate)
			_print_gate(gate)

		# IMG_3830: normalized_center_error < 0.10
		norm_errors = meeting_point_normalized_errors(data["intervals"])
		if norm_errors:
			median_val = statistics.median(norm_errors)
			passed = median_val < GATE_IMG3830_NORMALIZED_CENTER_ERROR
			gate = _gate_result(
				"meeting_point", "normalized_center_error (median)",
				"IMG_3830", median_val, GATE_IMG3830_NORMALIZED_CENTER_ERROR,
				"<", passed,
			)
			results.append(gate)
			_print_gate(gate)

	# canon_60d: normalized_center_error < 0.25
	vname, data = _find("canon_60d")
	if data is not None:
		norm_errors = meeting_point_normalized_errors(data["intervals"])
		if norm_errors:
			median_val = statistics.median(norm_errors)
			passed = median_val < GATE_CANON60D_NORMALIZED_CENTER_ERROR
			gate = _gate_result(
				"meeting_point", "normalized_center_error (median)",
				"canon_60d", median_val, GATE_CANON60D_NORMALIZED_CENTER_ERROR,
				"<", passed,
			)
			results.append(gate)
			_print_gate(gate)

		# canon_60d: high_confidence_share >= 0.40
		tiers = confidence_tier_counts(data["intervals"])
		total = sum(tiers.values())
		high_count = tiers.get("high", 0)
		if total > 0:
			share = high_count / total
			passed = share >= GATE_CANON60D_HIGH_CONFIDENCE_SHARE
			gate = _gate_result(
				"interval_score", "high_confidence_share",
				"canon_60d", share, GATE_CANON60D_HIGH_CONFIDENCE_SHARE,
				">=", passed,
			)
			results.append(gate)
			_print_gate(gate)

		# canon_60d: fair+low fraction <= 0.30
		fair_count = tiers.get("fair", 0)
		low_count = tiers.get("low", 0)
		if total > 0:
			fraction = (fair_count + low_count) / total
			passed = fraction <= GATE_CANON60D_FAIR_LOW_FRACTION
			gate = _gate_result(
				"interval_score", "fair_low_fraction",
				"canon_60d", fraction, GATE_CANON60D_FAIR_LOW_FRACTION,
				"<=", passed,
			)
			results.append(gate)
			_print_gate(gate)

	# IMG_3823: height_jerk_p95 < 69
	vname, data = _find("IMG_3823")
	if data is not None:
		trajectory = data["trajectory"]
		jerk_p95 = height_jerk_p95(trajectory)
		passed = jerk_p95 < GATE_IMG3823_HEIGHT_JERK_P95
		gate = _gate_result(
			"trajectory_stability", "height_jerk_p95",
			"IMG_3823", jerk_p95, GATE_IMG3823_HEIGHT_JERK_P95, "<", passed,
		)
		results.append(gate)
		_print_gate(gate)

	return results


#============================================
def evaluate_structural_gate(solved: dict) -> list:
	"""Evaluate structural gate: at least N videos solved.

	Args:
		solved: Dict mapping video_name -> solve result.

	Returns:
		List with one gate result dict.
	"""
	count = len(solved)
	passed = count >= GATE_MIN_VIDEOS_SOLVE
	gate = _gate_result(
		"structural", "min_videos_solved",
		"all", float(count), float(GATE_MIN_VIDEOS_SOLVE), ">=", passed,
	)
	_print_gate(gate)
	return [gate]


#============================================
def evaluate_supplemental(
	solved: dict,
	verbose: bool = False,
) -> list:
	"""Report supplemental seed-residual diagnostics (info only).

	Args:
		solved: Dict mapping video_name -> solve result.
		verbose: Print per-seed details.

	Returns:
		List of gate result dicts with result="INFO".
	"""
	results = []
	for video_name in sorted(solved.keys()):
		data = solved[video_name]
		seeds = data["seeds"]
		trajectory = data["trajectory"]
		label = video_name.split(".")[0]
		if not seeds or not trajectory:
			continue

		# compute residuals: predicted position vs seed ground truth
		residuals = []
		for seed in seeds:
			# skip seeds without spatial data (e.g. not_in_frame)
			if "cx" not in seed or "cy" not in seed:
				continue
			frame_idx = int(seed["frame_index"])
			if frame_idx >= len(trajectory) or trajectory[frame_idx] is None:
				continue
			pred = trajectory[frame_idx]
			dx = pred["cx"] - seed["cx"]
			dy = pred["cy"] - seed["cy"]
			dist = float(numpy.sqrt(dx * dx + dy * dy))
			residuals.append(dist)

		if residuals:
			median_val = statistics.median(residuals)
			gate = {
				"source": "seed_residual",
				"gate_name": "anchor_fidelity (median)",
				"video": label,
				"measured": median_val,
				"threshold": 0.0,
				"comparison": "(info)",
				"passed": True,
				"result": "INFO",
			}
			results.append(gate)
			print(f"  INFO  seed_residual  anchor_fidelity  "
				f"{label}  median={median_val:.1f}px  n={len(residuals)}")
			if verbose:
				print(f"    residuals: min={min(residuals):.1f} "
					f"max={max(residuals):.1f}")

	return results


#============================================
def _print_gate(gate: dict) -> None:
	"""Print a single gate result to console.

	Args:
		gate: Gate result dict.
	"""
	status = gate["result"]
	tag = "PASS " if status == "PASS" else "FAIL "
	print(f"  {tag} {gate['source']:22s} {gate['gate_name']:35s} "
		f"{gate['video']:12s} "
		f"measured={gate['measured']:.4f}  threshold {gate['comparison']} "
		f"{gate['threshold']:.4f}")


# ============================================================
# Audit artifact
# ============================================================

#============================================
def write_audit_artifact(
	all_results: list,
	skipped_videos: list,
	solved_count: int,
	output_path: str,
) -> None:
	"""Write the audit artifact text file.

	Args:
		all_results: List of all gate result dicts (closure + supplemental).
		skipped_videos: List of skipped video names.
		solved_count: Number of videos that were actually solved.
		output_path: Path to write the artifact.
	"""
	# count results
	closure_results = [r for r in all_results if r["result"] != "INFO"]
	supplemental_results = [r for r in all_results if r["result"] == "INFO"]
	passed = sum(1 for r in closure_results if r["result"] == "PASS")
	failed = sum(1 for r in closure_results if r["result"] == "FAIL")
	skipped = len(skipped_videos)
	total_closure = len(closure_results)

	# build timestamp
	timestamp = time.strftime("%Y-%m-%d %H:%M")

	# write artifact
	os.makedirs(os.path.dirname(output_path), exist_ok=True)
	lines = []
	lines.append(f"M4/M5 Benchmark Gate Results -- {timestamp}")
	lines.append("=" * 60)
	# header row
	header = (f"{'SOURCE':22s} {'GATE':35s} {'VIDEO':12s} "
		f"{'MEASURED':>10s}  {'THRESHOLD':>12s}  {'RESULT':>6s}")
	lines.append(header)

	for r in all_results:
		threshold_str = f"{r['comparison']} {r['threshold']:.4f}"
		if r["result"] == "INFO":
			threshold_str = "(info)"
		row = (f"{r['source']:22s} {r['gate_name']:35s} {r['video']:12s} "
			f"{r['measured']:10.4f}  {threshold_str:>12s}  {r['result']:>6s}")
		lines.append(row)

	lines.append("=" * 60)
	lines.append(f"CLOSURE GATES:  PASSED {passed}/{total_closure}   "
		f"FAILED {failed}   SKIPPED {skipped}")
	lines.append(f"SUPPLEMENTAL:   {len(supplemental_results)} "
		f"seed_residual checks reported")

	# authority line
	total_videos = len(VIDEO_REGISTRY)
	if solved_count == total_videos and skipped == 0 and failed == 0:
		authority = (f"AUTHORITATIVE (all {total_videos} videos, "
			f"0 skipped, 0 failed)")
	else:
		authority = (f"NOT AUTHORITATIVE ({solved_count} of "
			f"{total_videos} videos solved, {skipped} skipped, "
			f"{failed} failed)")
	lines.append(f"AUTHORITY:      {authority}")

	text = "\n".join(lines) + "\n"
	with open(output_path, "w") as f:
		f.write(text)
	print(f"\naudit artifact written to {output_path}")


# ============================================================
# Main
# ============================================================

#============================================
def main() -> None:
	"""Run M4/M5 benchmark gate evaluation."""
	args = parse_args()

	print("M4/M5 Benchmark Gate Evaluation")
	print("=" * 60)

	# determine which videos to run
	if args.video:
		if args.video not in VIDEO_REGISTRY:
			print(f"ERROR: unknown video '{args.video}'")
			print(f"known videos: {', '.join(sorted(VIDEO_REGISTRY.keys()))}")
			sys.exit(2)
		video_list = [args.video]
	else:
		video_list = list(VIDEO_REGISTRY.keys())

	# preflight: check which videos are ready
	ready_videos = []
	skipped_videos = []
	for video_name in video_list:
		label = VIDEO_REGISTRY[video_name]
		ready, reason = check_video_ready(video_name)
		if ready:
			print(f"  ready: {video_name} ({label})")
			ready_videos.append(video_name)
		elif reason.startswith("skip:"):
			print(f"  skip:  {video_name} -- {reason}")
			skipped_videos.append(video_name)
		else:
			# hard error
			print(f"\n{reason}")
			sys.exit(2)

	print(f"\n{len(ready_videos)} videos ready, "
		f"{len(skipped_videos)} skipped\n")

	# solve all ready videos
	solved = {}
	for video_name in ready_videos:
		label = VIDEO_REGISTRY[video_name]
		print(f"solving {video_name} ({label})...")
		result = solve_video(video_name)
		n_intervals = len(result["intervals"])
		n_traj = sum(1 for f in result["trajectory"] if f is not None)
		print(f"  {n_intervals} intervals, {n_traj} trajectory frames\n")
		solved[video_name] = result

	# evaluate gates
	all_results = []
	print("=" * 60)
	print("CLOSURE GATES")
	print("=" * 60)

	# structural gate
	results = evaluate_structural_gate(solved)
	all_results.extend(results)

	# cross-video gates
	print("\ncross-video gates:")
	results = evaluate_cross_video_gates(solved, verbose=args.verbose)
	all_results.extend(results)

	# per-regime gates
	print("\nper-regime gates:")
	results = evaluate_regime_gates(solved)
	all_results.extend(results)

	# supplemental
	print("\nsupplemental diagnostics:")
	results = evaluate_supplemental(solved, verbose=args.verbose)
	all_results.extend(results)

	# write audit artifact
	artifact_path = os.path.join(OUTPUT_DIR, "benchmark_gates.txt")
	write_audit_artifact(all_results, skipped_videos, len(solved), artifact_path)

	# summary
	closure_results = [r for r in all_results if r["result"] != "INFO"]
	passed = sum(1 for r in closure_results if r["result"] == "PASS")
	failed = sum(1 for r in closure_results if r["result"] == "FAIL")
	total = len(closure_results)

	print(f"\nSUMMARY: {passed}/{total} closure gates passed, "
		f"{failed} failed, {len(skipped_videos)} videos skipped")

	# exit code
	if failed > 0:
		sys.exit(1)


#============================================
if __name__ == '__main__':
	main()
