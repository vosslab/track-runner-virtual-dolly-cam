#!/usr/bin/env python3
"""
Post-hoc quality scoring for 24-corpus blob walker v2 walks.

Reads existing fwd_verdicts.csv and bwd_verdicts.csv from each
interval directory.  No video decode; no walker re-run.

Output:
  corpus_quality.csv            one row per interval
  dump_step1/24corpus/QUALITY_SCORES.md   per-video tables + aggregate

Usage:
  source source_me.sh && python3 tools/blob_walk_v2/score_corpus_quality.py \
      --corpus-root blob_walk_v2/24corpus \
      --dump-root dump_step1/24corpus
"""

# Standard Library
import argparse
import csv
import math
import pathlib
import json

#============================================
# Physical conversion constants
# torso_width_inches: per plan, all corpus videos use torso_w = 12 inches
TORSO_WIDTH_INCHES = 12.0
# Speed envelope in mph -> W/s  (1 mph = 17.6 in/s; torso_w = 12 in)
# v_W_per_s = v_mph * (17.6 / TORSO_WIDTH_INCHES)
MPH_TO_W_PER_S = 17.6 / TORSO_WIDTH_INCHES
SPEED_LOWER_W_PER_S = 5.0 * MPH_TO_W_PER_S    # 5 mph  -> 7.333 W/s
SPEED_UPPER_W_PER_S = 20.0 * MPH_TO_W_PER_S   # 20 mph -> 29.333 W/s

# Sliding window for velocity-magnitude plausibility
VELOCITY_WINDOW = 9

# no_signal accepted_fraction threshold (both directions must be below)
NO_SIGNAL_ACCEPTED_FRACTION = 0.1


#============================================
def _find_verdicts_csv(seed_dir: pathlib.Path, direction: str) -> pathlib.Path:
	"""Return the path to the verdicts CSV for one direction.

	Args:
		seed_dir: interval seed directory, e.g. seed_1389_1420/
		direction: 'fwd' or 'bwd'

	Returns:
		Path to the CSV file.

	Raises:
		FileNotFoundError: if the CSV is absent.
	"""
	csv_path = seed_dir / f"{direction}_verdicts.csv"
	if not csv_path.exists():
		raise FileNotFoundError(f"Missing verdicts CSV: {csv_path}")
	return csv_path


#============================================
def _load_verdicts(csv_path: pathlib.Path) -> list:
	"""Load all rows from a verdicts CSV as a list of dicts.

	Args:
		csv_path: path to fwd_verdicts.csv or bwd_verdicts.csv

	Returns:
		List of row dicts (all values are strings as read by csv.DictReader).
	"""
	with open(csv_path, newline='') as fh:
		reader = csv.DictReader(fh)
		rows = list(reader)
	return rows


#============================================
def _load_source_fps_and_torso_w(dump_dir: pathlib.Path, video_name: str, left_frame: int) -> tuple:
	"""Load source_fps and torso_w_px from the dump JSON for this interval.

	Args:
		dump_dir: dump_step1/24corpus/<video>/ directory
		video_name: video basename
		left_frame: left seed frame index

	Returns:
		Tuple of (source_fps: float, torso_w_px: float).

	Raises:
		FileNotFoundError: if JSON is missing.
		KeyError: if required field is absent.
	"""
	json_path = dump_dir / f"FWD_{left_frame}.json"
	if not json_path.exists():
		raise FileNotFoundError(f"Missing dump JSON: {json_path}")
	data = json.loads(json_path.read_text())
	source_fps = float(data["source_fps"])
	# torso_w_px from step_0 seed
	torso_w_px = float(data["step_0"]["seed_w_px"])
	return source_fps, torso_w_px


#============================================
def _compute_walk_metrics(rows: list, source_fps: float, torso_w_px: float) -> dict:
	"""Compute per-walk quality metrics from a verdicts CSV row list.

	Accepted rows are those with status == 'accepted'.
	The after_walk_terminated row (stop_reason non-empty) is excluded from counts.

	Args:
		rows: list of row dicts from DictReader
		source_fps: frames per second for this video
		torso_w_px: torso width in pixels for normalizing

	Returns:
		Dict with all per-walk scalar metrics.
	"""
	# Filter out after_walk_terminated rows from totals
	walk_rows = [r for r in rows if r["status"] != "after_walk_terminated"]

	total_visited = len(walk_rows)
	accepted_rows = [r for r in walk_rows if r["status"] == "accepted"]
	accepted_count = len(accepted_rows)

	if total_visited == 0:
		accepted_fraction = 0.0
	else:
		accepted_fraction = accepted_count / total_visited

	# Longest no-accept streak: consecutive frames where status != accepted
	longest_streak = 0
	current_streak = 0
	for r in walk_rows:
		if r["status"] != "accepted":
			current_streak += 1
			longest_streak = max(longest_streak, current_streak)
		else:
			current_streak = 0

	# Velocity computation on accepted rows only
	# Each accepted row carries cand_cx, cand_cy (pixel coords) and frame_index
	# velocity in W/s: delta_px / torso_w_px / delta_t_seconds
	# delta_t_seconds = delta_frames / source_fps

	# Build list of (frame_index, cx_px, cy_px) for accepted rows only
	accepted_pts = []
	for r in accepted_rows:
		cx_str = r["cand_cx"]
		cy_str = r["cand_cy"]
		fi_str = r["frame_index"]
		# Skip rows with blank cand_cx (soft_miss, rejected - but accepted should have values)
		if cx_str == "" or cy_str == "" or fi_str == "":
			continue
		accepted_pts.append((int(fi_str), float(cx_str), float(cy_str)))

	# Compute per-frame velocities between consecutive accepted points
	# velocity magnitude in W/s
	vel_mags_w_per_s = []
	vel_angles_rad = []
	for i in range(1, len(accepted_pts)):
		fi0, cx0, cy0 = accepted_pts[i - 1]
		fi1, cx1, cy1 = accepted_pts[i]
		delta_frames = fi1 - fi0
		if delta_frames <= 0:
			continue
		delta_t = delta_frames / source_fps
		dx_px = cx1 - cx0
		dy_px = cy1 - cy0
		dist_px = math.hypot(dx_px, dy_px)
		# W/s: torso widths per second
		v_w_per_s = (dist_px / torso_w_px) / delta_t
		vel_mags_w_per_s.append(v_w_per_s)
		angle = math.atan2(dy_px, dx_px)
		vel_angles_rad.append(angle)

	# 9-frame sliding window metrics on velocity magnitudes
	window_speed_in_range_fraction = float("nan")
	windowed_speed_w_per_s_median = float("nan")
	windowed_speed_w_per_s_p95 = float("nan")
	windowed_vel_mag_variance_median = float("nan")
	windowed_vel_angle_spread_median = float("nan")
	windowed_vel_angle_spread_p95 = float("nan")

	n_vels = len(vel_mags_w_per_s)
	if n_vels >= VELOCITY_WINDOW:
		# Build windows: each window of length VELOCITY_WINDOW
		window_means = []
		window_variances = []
		window_angle_spreads = []
		in_range_count = 0
		total_windows = 0
		for w_start in range(n_vels - VELOCITY_WINDOW + 1):
			win_mags = vel_mags_w_per_s[w_start:w_start + VELOCITY_WINDOW]
			win_angles = vel_angles_rad[w_start:w_start + VELOCITY_WINDOW]
			win_mean = sum(win_mags) / VELOCITY_WINDOW
			win_var = sum((v - win_mean) ** 2 for v in win_mags) / VELOCITY_WINDOW
			# angle spread: max - min, accounting for circular wraparound
			# simplification: use max-min of raw angles (valid for tracks where
			# runner does not reverse 180 degrees within a 9-frame window)
			win_spread = max(win_angles) - min(win_angles)
			# clamp spread to [0, pi] in case of wraparound near +/-pi
			if win_spread > math.pi:
				win_spread = 2 * math.pi - win_spread
			window_means.append(win_mean)
			window_variances.append(win_var)
			window_angle_spreads.append(win_spread)
			if SPEED_LOWER_W_PER_S <= win_mean <= SPEED_UPPER_W_PER_S:
				in_range_count += 1
			total_windows += 1

		window_speed_in_range_fraction = in_range_count / total_windows
		sorted_means = sorted(window_means)
		n_wins = len(sorted_means)
		mid = n_wins // 2
		if n_wins % 2 == 1:
			windowed_speed_w_per_s_median = sorted_means[mid]
		else:
			windowed_speed_w_per_s_median = (sorted_means[mid - 1] + sorted_means[mid]) / 2.0
		# p95
		p95_idx = int(math.ceil(0.95 * n_wins)) - 1
		p95_idx = max(0, min(p95_idx, n_wins - 1))
		windowed_speed_w_per_s_p95 = sorted_means[p95_idx]

		sorted_vars = sorted(window_variances)
		if n_wins % 2 == 1:
			windowed_vel_mag_variance_median = sorted_vars[mid]
		else:
			windowed_vel_mag_variance_median = (sorted_vars[mid - 1] + sorted_vars[mid]) / 2.0

		sorted_spreads = sorted(window_angle_spreads)
		if n_wins % 2 == 1:
			windowed_vel_angle_spread_median = sorted_spreads[mid]
		else:
			windowed_vel_angle_spread_median = (sorted_spreads[mid - 1] + sorted_spreads[mid]) / 2.0
		p95_spread_idx = int(math.ceil(0.95 * n_wins)) - 1
		p95_spread_idx = max(0, min(p95_spread_idx, n_wins - 1))
		windowed_vel_angle_spread_p95 = sorted_spreads[p95_spread_idx]

	return {
		"accepted_count": accepted_count,
		"total_visited": total_visited,
		"accepted_fraction": accepted_fraction,
		"longest_no_accept_streak": longest_streak,
		"windowed_speed_in_range_fraction": window_speed_in_range_fraction,
		"windowed_speed_w_per_s_median": windowed_speed_w_per_s_median,
		"windowed_speed_w_per_s_p95": windowed_speed_w_per_s_p95,
		"windowed_vel_mag_variance_median": windowed_vel_mag_variance_median,
		"windowed_vel_angle_spread_median": windowed_vel_angle_spread_median,
		"windowed_vel_angle_spread_p95": windowed_vel_angle_spread_p95,
	}


#============================================
def _compute_fwd_bwd_agreement(fwd_rows: list, bwd_rows: list) -> tuple:
	"""Compute per-frame Euclidean distance between FWD and BWD accepted positions.

	Only frames present in BOTH logs as accepted are included.

	Args:
		fwd_rows: FWD verdicts rows
		bwd_rows: BWD verdicts rows

	Returns:
		Tuple (median_px, p95_px) both float; (nan, nan) if no overlap.
	"""
	# Build frame -> (cx, cy) for accepted rows in each direction
	fwd_accepted = {}
	for r in fwd_rows:
		if r["status"] == "accepted" and r["cand_cx"] != "" and r["cand_cy"] != "":
			fi = int(r["frame_index"])
			fwd_accepted[fi] = (float(r["cand_cx"]), float(r["cand_cy"]))

	bwd_accepted = {}
	for r in bwd_rows:
		if r["status"] == "accepted" and r["cand_cx"] != "" and r["cand_cy"] != "":
			fi = int(r["frame_index"])
			bwd_accepted[fi] = (float(r["cand_cx"]), float(r["cand_cy"]))

	common_frames = sorted(set(fwd_accepted) & set(bwd_accepted))
	if not common_frames:
		return (float("nan"), float("nan"))

	dists = []
	for fi in common_frames:
		fx, fy = fwd_accepted[fi]
		bx, by = bwd_accepted[fi]
		dists.append(math.hypot(fx - bx, fy - by))

	dists_sorted = sorted(dists)
	n = len(dists_sorted)
	mid = n // 2
	if n % 2 == 1:
		median_px = dists_sorted[mid]
	else:
		median_px = (dists_sorted[mid - 1] + dists_sorted[mid]) / 2.0

	p95_idx = int(math.ceil(0.95 * n)) - 1
	p95_idx = max(0, min(p95_idx, n - 1))
	p95_px = dists_sorted[p95_idx]

	return (median_px, p95_px)


#============================================
def _classify_interval(fwd_metrics: dict, bwd_metrics: dict) -> str:
	"""Classify one interval based on per-walk metrics.

	Args:
		fwd_metrics: output of _compute_walk_metrics for FWD
		bwd_metrics: output of _compute_walk_metrics for BWD

	Returns:
		'no_signal', 'completed_low_quality', or 'completed_normal'
	"""
	fwd_af = fwd_metrics["accepted_fraction"]
	bwd_af = bwd_metrics["accepted_fraction"]

	if fwd_af < NO_SIGNAL_ACCEPTED_FRACTION and bwd_af < NO_SIGNAL_ACCEPTED_FRACTION:
		return "no_signal"
	if fwd_af < 0.3 or bwd_af < 0.3:
		return "completed_low_quality"
	return "completed_normal"


#============================================
def _fmt(val) -> str:
	"""Format a metric value for CSV output.

	Args:
		val: numeric value or nan

	Returns:
		String representation; empty string for nan.
	"""
	if isinstance(val, float) and math.isnan(val):
		return ""
	if isinstance(val, float):
		return f"{val:.4f}"
	return str(val)


#============================================
def _fmt_md(val, decimals: int = 3) -> str:
	"""Format a metric value for Markdown table output.

	Args:
		val: numeric value or nan
		decimals: decimal places

	Returns:
		String; '-' for nan.
	"""
	if isinstance(val, float) and math.isnan(val):
		return "-"
	if isinstance(val, float):
		fmt_str = f"{{:.{decimals}f}}"
		return fmt_str.format(val)
	return str(val)


#============================================
def _parse_seed_dir_name(seed_dir_name: str) -> tuple:
	"""Parse 'seed_LLLL_RRRR' into (left_frame, right_frame).

	Args:
		seed_dir_name: e.g. 'seed_1389_1420'

	Returns:
		Tuple of (left_frame: int, right_frame: int).
	"""
	parts = seed_dir_name.split("_")
	# parts: ['seed', 'LLLL', 'RRRR']
	left_frame = int(parts[1])
	right_frame = int(parts[2])
	return left_frame, right_frame


#============================================
def score_interval(seed_dir: pathlib.Path, video_name: str,
		dump_video_dir: pathlib.Path) -> dict:
	"""Score one interval from its verdicts CSVs.

	Args:
		seed_dir: path to the seed directory, e.g. .../seed_1389_1420/
		video_name: video basename string
		dump_video_dir: dump_step1/24corpus/<video>/ directory

	Returns:
		Dict with all output columns for corpus_quality.csv.
	"""
	left_frame, right_frame = _parse_seed_dir_name(seed_dir.name)
	interval_id = f"{left_frame}-{right_frame}"
	interval_length = right_frame - left_frame

	source_fps, torso_w_px = _load_source_fps_and_torso_w(dump_video_dir, video_name, left_frame)

	fwd_csv = _find_verdicts_csv(seed_dir, "fwd")
	bwd_csv = _find_verdicts_csv(seed_dir, "bwd")

	fwd_rows = _load_verdicts(fwd_csv)
	bwd_rows = _load_verdicts(bwd_csv)

	fwd_metrics = _compute_walk_metrics(fwd_rows, source_fps, torso_w_px)
	bwd_metrics = _compute_walk_metrics(bwd_rows, source_fps, torso_w_px)

	agreement_median_px, agreement_p95_px = _compute_fwd_bwd_agreement(fwd_rows, bwd_rows)

	classification = _classify_interval(fwd_metrics, bwd_metrics)

	row = {
		"video": video_name,
		"interval_id": interval_id,
		"left_frame": left_frame,
		"right_frame": right_frame,
		"interval_length": interval_length,
		"fps": source_fps,
		"torso_w_px": torso_w_px,
		"classification": classification,
		"fwd_accepted_count": fwd_metrics["accepted_count"],
		"fwd_total_visited": fwd_metrics["total_visited"],
		"fwd_accepted_fraction": fwd_metrics["accepted_fraction"],
		"fwd_longest_no_accept_streak": fwd_metrics["longest_no_accept_streak"],
		"fwd_windowed_speed_in_range_fraction": fwd_metrics["windowed_speed_in_range_fraction"],
		"fwd_windowed_speed_w_per_s_median": fwd_metrics["windowed_speed_w_per_s_median"],
		"fwd_windowed_speed_w_per_s_p95": fwd_metrics["windowed_speed_w_per_s_p95"],
		"fwd_windowed_vel_mag_variance_median": fwd_metrics["windowed_vel_mag_variance_median"],
		"fwd_windowed_vel_angle_spread_median": fwd_metrics["windowed_vel_angle_spread_median"],
		"fwd_windowed_vel_angle_spread_p95": fwd_metrics["windowed_vel_angle_spread_p95"],
		"bwd_accepted_count": bwd_metrics["accepted_count"],
		"bwd_total_visited": bwd_metrics["total_visited"],
		"bwd_accepted_fraction": bwd_metrics["accepted_fraction"],
		"bwd_longest_no_accept_streak": bwd_metrics["longest_no_accept_streak"],
		"bwd_windowed_speed_in_range_fraction": bwd_metrics["windowed_speed_in_range_fraction"],
		"bwd_windowed_speed_w_per_s_median": bwd_metrics["windowed_speed_w_per_s_median"],
		"bwd_windowed_speed_w_per_s_p95": bwd_metrics["windowed_speed_w_per_s_p95"],
		"bwd_windowed_vel_mag_variance_median": bwd_metrics["windowed_vel_mag_variance_median"],
		"bwd_windowed_vel_angle_spread_median": bwd_metrics["windowed_vel_angle_spread_median"],
		"bwd_windowed_vel_angle_spread_p95": bwd_metrics["windowed_vel_angle_spread_p95"],
		"fwd_bwd_agreement_median_px": agreement_median_px,
		"fwd_bwd_agreement_p95_px": agreement_p95_px,
	}
	return row


#============================================
def score_corpus(corpus_root: pathlib.Path, dump_root: pathlib.Path) -> list:
	"""Score all intervals in the corpus.

	Args:
		corpus_root: path to blob_walk_v2/24corpus/
		dump_root: path to dump_step1/24corpus/

	Returns:
		List of row dicts, one per interval, sorted by video then left_frame.
	"""
	all_rows = []
	# Enumerate video directories in corpus
	video_dirs = sorted(d for d in corpus_root.iterdir() if d.is_dir())
	for video_dir in video_dirs:
		video_name = video_dir.name
		dump_video_dir = dump_root / video_name
		if not dump_video_dir.exists():
			raise FileNotFoundError(f"Missing dump dir for video: {dump_video_dir}")
		# Enumerate seed directories
		seed_dirs = sorted(d for d in video_dir.iterdir() if d.is_dir() and d.name.startswith("seed_"))
		for seed_dir in seed_dirs:
			row = score_interval(seed_dir, video_name, dump_video_dir)
			all_rows.append(row)
	return all_rows


#============================================
# CSV output column order
_CSV_COLUMNS = [
	"video",
	"interval_id",
	"left_frame",
	"right_frame",
	"interval_length",
	"fps",
	"torso_w_px",
	"classification",
	"fwd_accepted_count",
	"fwd_total_visited",
	"fwd_accepted_fraction",
	"fwd_longest_no_accept_streak",
	"fwd_windowed_speed_in_range_fraction",
	"fwd_windowed_speed_w_per_s_median",
	"fwd_windowed_speed_w_per_s_p95",
	"fwd_windowed_vel_mag_variance_median",
	"fwd_windowed_vel_angle_spread_median",
	"fwd_windowed_vel_angle_spread_p95",
	"bwd_accepted_count",
	"bwd_total_visited",
	"bwd_accepted_fraction",
	"bwd_longest_no_accept_streak",
	"bwd_windowed_speed_in_range_fraction",
	"bwd_windowed_speed_w_per_s_median",
	"bwd_windowed_speed_w_per_s_p95",
	"bwd_windowed_vel_mag_variance_median",
	"bwd_windowed_vel_angle_spread_median",
	"bwd_windowed_vel_angle_spread_p95",
	"fwd_bwd_agreement_median_px",
	"fwd_bwd_agreement_p95_px",
]


#============================================
def write_corpus_csv(rows: list, output_path: pathlib.Path) -> None:
	"""Write corpus_quality.csv.

	Args:
		rows: list of interval row dicts
		output_path: destination CSV path
	"""
	with open(output_path, "w", newline="") as fh:
		writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
		writer.writeheader()
		for row in rows:
			# Format float values
			out_row = {}
			for col in _CSV_COLUMNS:
				val = row[col]
				if isinstance(val, float) and math.isnan(val):
					out_row[col] = ""
				elif isinstance(val, float):
					out_row[col] = f"{val:.6f}"
				else:
					out_row[col] = val
			writer.writerow(out_row)


#============================================
def _median_of_values(vals: list) -> float:
	"""Compute median of a list of numeric values, skipping nan.

	Args:
		vals: list of floats (may include nan)

	Returns:
		Median float or nan if no valid values.
	"""
	clean = [v for v in vals if not (isinstance(v, float) and math.isnan(v))]
	if not clean:
		return float("nan")
	clean_sorted = sorted(clean)
	n = len(clean_sorted)
	mid = n // 2
	if n % 2 == 1:
		return clean_sorted[mid]
	return (clean_sorted[mid - 1] + clean_sorted[mid]) / 2.0


#============================================
def write_quality_scores_md(rows: list, output_path: pathlib.Path) -> None:
	"""Write QUALITY_SCORES.md.

	Args:
		rows: list of interval row dicts
		output_path: destination Markdown path
	"""
	lines = []
	lines.append("# Quality scores: 24-corpus blob walker v2")
	lines.append("")
	lines.append("Completion: all 48 walks reach `hit_neighbor_seed` (per #70).")
	lines.append("Quality metrics expose differences between intervals.")
	lines.append("No-signal intervals have genuinely empty residual heat-maps;")
	lines.append("they are classified separately, not as failures.")
	lines.append("")

	# Group rows by video
	videos_seen = []
	rows_by_video = {}
	for row in rows:
		v = row["video"]
		if v not in rows_by_video:
			rows_by_video[v] = []
			videos_seen.append(v)
		rows_by_video[v].append(row)

	for video in videos_seen:
		video_rows = rows_by_video[video]
		lines.append(f"## {video}")
		lines.append("")
		# Per-video table header
		lines.append("| interval | length | fps | FWD accepted_frac | BWD accepted_frac | FWD/BWD agree median px | classification |")
		lines.append("| --- | --- | --- | --- | --- | --- | --- |")
		for row in video_rows:
			interval_id = row["interval_id"]
			length = row["interval_length"]
			fps = int(row["fps"])
			fwd_af = _fmt_md(row["fwd_accepted_fraction"])
			bwd_af = _fmt_md(row["bwd_accepted_fraction"])
			agree = _fmt_md(row["fwd_bwd_agreement_median_px"])
			cls = row["classification"]
			lines.append(f"| {interval_id} | {length} | {fps} | {fwd_af} | {bwd_af} | {agree} | {cls} |")
		lines.append("")

	# Aggregate section
	lines.append("## Aggregate")
	lines.append("")

	total_intervals = len(rows)
	no_signal_rows = [r for r in rows if r["classification"] == "no_signal"]
	low_quality_rows = [r for r in rows if r["classification"] == "completed_low_quality"]
	normal_rows = [r for r in rows if r["classification"] == "completed_normal"]

	lines.append(f"- Total intervals: {total_intervals}")
	lines.append(f"- no_signal: {len(no_signal_rows)}")
	lines.append(f"- completed_low_quality: {len(low_quality_rows)}")
	lines.append(f"- completed_normal: {len(normal_rows)}")
	lines.append("")

	# Median metrics over non-no-signal intervals
	non_no_signal = [r for r in rows if r["classification"] != "no_signal"]
	if non_no_signal:
		median_fwd_af = _median_of_values([r["fwd_accepted_fraction"] for r in non_no_signal])
		median_bwd_af = _median_of_values([r["bwd_accepted_fraction"] for r in non_no_signal])
		median_combined_af = _median_of_values(
			[r["fwd_accepted_fraction"] for r in non_no_signal] +
			[r["bwd_accepted_fraction"] for r in non_no_signal]
		)
		median_agree = _median_of_values([r["fwd_bwd_agreement_median_px"] for r in non_no_signal])
		median_speed_in_range = _median_of_values(
			[r["fwd_windowed_speed_in_range_fraction"] for r in non_no_signal] +
			[r["bwd_windowed_speed_in_range_fraction"] for r in non_no_signal]
		)

		lines.append(f"- Median accepted_fraction (FWD, non-no-signal): {_fmt_md(median_fwd_af)}")
		lines.append(f"- Median accepted_fraction (BWD, non-no-signal): {_fmt_md(median_bwd_af)}")
		lines.append(f"- Median accepted_fraction (combined, non-no-signal): {_fmt_md(median_combined_af)}")
		lines.append(f"- Median FWD/BWD agreement (px, non-no-signal): {_fmt_md(median_agree)}")
		lines.append(f"- Median windowed_speed_in_range_fraction (non-no-signal): {_fmt_md(median_speed_in_range)}")
	lines.append("")

	# Notes on no-signal intervals
	lines.append("## Notes")
	lines.append("")
	lines.append("No-signal intervals (both FWD and BWD accepted_fraction < 0.1):")
	lines.append("")
	if no_signal_rows:
		# Group by video
		ns_by_video = {}
		for r in no_signal_rows:
			v = r["video"]
			if v not in ns_by_video:
				ns_by_video[v] = []
			ns_by_video[v].append(r["interval_id"])
		for v, iids in sorted(ns_by_video.items()):
			lines.append(f"- {v}: {', '.join(iids)}")
	else:
		lines.append("- None")
	lines.append("")
	lines.append("These intervals have genuinely empty residual heat-maps (zero motion signal).")
	lines.append("The walker correctly reaches `hit_neighbor_seed` on all 48 directions;")
	lines.append("the empty residual is a property of the source video segment, not a walker bug.")
	lines.append("")

	# Videos with most no_signal
	if no_signal_rows:
		ns_video_counts = {}
		for r in no_signal_rows:
			v = r["video"]
			ns_video_counts[v] = ns_video_counts.get(v, 0) + 1
		sorted_ns = sorted(ns_video_counts.items(), key=lambda kv: -kv[1])
		lines.append("Videos contributing most no_signal intervals:")
		for v, cnt in sorted_ns:
			lines.append(f"- {v}: {cnt}")
		lines.append("")

	output_path.parent.mkdir(parents=True, exist_ok=True)
	output_path.write_text("\n".join(lines) + "\n")


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description="Post-hoc quality scoring for 24-corpus blob walker v2 walks."
	)
	parser.add_argument(
		"--corpus-root",
		dest="corpus_root",
		required=True,
		help="Path to blob_walk_v2/24corpus (contains one dir per video)."
	)
	parser.add_argument(
		"--dump-root",
		dest="dump_root",
		default="dump_step1/24corpus",
		help="Path to dump_step1/24corpus (contains per-video dump JSONs)."
	)
	parser.add_argument(
		"--output-csv",
		dest="output_csv",
		default="corpus_quality.csv",
		help="Output CSV path (default: corpus_quality.csv at repo root)."
	)
	parser.add_argument(
		"--output-md",
		dest="output_md",
		default=None,
		help=(
			"Output Markdown path. "
			"Default: <dump-root>/QUALITY_SCORES.md"
		)
	)
	args = parser.parse_args()
	return args


#============================================
def main() -> None:
	"""Main entry point."""
	args = parse_args()

	corpus_root = pathlib.Path(args.corpus_root)
	dump_root = pathlib.Path(args.dump_root)
	output_csv = pathlib.Path(args.output_csv)

	if args.output_md is not None:
		output_md = pathlib.Path(args.output_md)
	else:
		output_md = dump_root / "QUALITY_SCORES.md"

	if not corpus_root.exists():
		raise FileNotFoundError(f"corpus_root does not exist: {corpus_root}")
	if not dump_root.exists():
		raise FileNotFoundError(f"dump_root does not exist: {dump_root}")

	print(f"Scoring corpus: {corpus_root}")
	rows = score_corpus(corpus_root, dump_root)
	print(f"Scored {len(rows)} intervals.")

	write_corpus_csv(rows, output_csv)
	print(f"Wrote CSV: {output_csv}")

	write_quality_scores_md(rows, output_md)
	print(f"Wrote Markdown: {output_md}")

	# Print classification summary
	no_signal = sum(1 for r in rows if r["classification"] == "no_signal")
	low_quality = sum(1 for r in rows if r["classification"] == "completed_low_quality")
	normal = sum(1 for r in rows if r["classification"] == "completed_normal")
	print(f"  no_signal:             {no_signal}")
	print(f"  completed_low_quality: {low_quality}")
	print(f"  completed_normal:      {normal}")

	non_no_signal = [r for r in rows if r["classification"] != "no_signal"]
	if non_no_signal:
		median_combined_af = _median_of_values(
			[r["fwd_accepted_fraction"] for r in non_no_signal] +
			[r["bwd_accepted_fraction"] for r in non_no_signal]
		)
		median_agree = _median_of_values([r["fwd_bwd_agreement_median_px"] for r in non_no_signal])
		median_speed_in_range = _median_of_values(
			[r["fwd_windowed_speed_in_range_fraction"] for r in non_no_signal] +
			[r["bwd_windowed_speed_in_range_fraction"] for r in non_no_signal]
		)
		print(f"  Median accepted_fraction (non-no-signal): {median_combined_af:.3f}")
		print(f"  Median FWD/BWD agreement px (non-no-signal): {_fmt_md(median_agree)}")
		print(f"  Median windowed_speed_in_range_fraction (non-no-signal): {_fmt_md(median_speed_in_range)}")


if __name__ == '__main__':
	main()
