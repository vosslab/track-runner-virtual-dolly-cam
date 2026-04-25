#!/usr/bin/env python3
"""Diagnose pre-race (Stage 1 + Stage 2) race-start detection.

Vocabulary:
	- interval: a single seed-to-seed range (two adjacent seeds, the
		frames strictly between them). Per contract C5.
	- window:   a sliding group of N consecutive seeds (= N-1 adjacent
		intervals). Production Stage 1 uses windows of
		PRE_RACE_MIN_WINDOW_SEEDS (= 3) seeds = 2 adjacent intervals to
		compute coherence.

Read-only evidence gatherer. Runs the same directional-coherence math
used in production (race_start.compute_window_metrics) and also
computes candidate signals that are NOT yet in the solver:
	- per-pair scene displacement stats (min, max, stdev)
	- per-pair heading angle variance
	- torso-size trend (mean width and linear slope)
	- camera pan velocity from motion_track
	- residual-motion energy at the window's center frame

Emits a per-window table (one row per sliding 3-seed window) preceded by
a column legend, an optional Stage 2 replay over the accepted window's
blended_path, and three PNG diagnostics:
	- <stem>.track_runner.pre_race_diag.seed_timeline.png
	- <stem>.track_runner.pre_race_diag.window_metrics.png
	- <stem>.track_runner.pre_race_diag.stage2_velocity.png (optional)

Does not modify any on-disk artifact. Does not change production
thresholds.
"""

# Standard Library
import os
import sys
import math
import glob
import argparse

# add track_runner directory to path so we can import its modules
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, "track_runner")
if _TRACK_RUNNER_DIR not in sys.path:
	sys.path.insert(0, _TRACK_RUNNER_DIR)

# PIP3 modules
import numpy
import tabulate
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# local repo modules
import camera_motion
import interval_fingerprint
import race_phases
import race_start
import residual_motion
import scene_coords
import state_io
import tr_paths
import video_io


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description="Diagnose pre-race (Stage 1 + Stage 2) detection signals",
	)
	parser.add_argument(
		"-i", "--input", dest="input_file", required=True,
		help="Path to input video file",
	)
	parser.add_argument(
		"-p", "--png-dir", dest="png_dir", default=None,
		help="Directory to write PNG diagnostics (default: alongside video)",
	)
	parser.add_argument(
		"-Q", "--quiet", dest="quiet", action="store_true",
		help="Suppress per-window console table (PNGs still written)",
	)
	args = parser.parse_args()
	return args


#============================================
def load_all_data(input_file: str) -> tuple:
	"""Load video, motion track, scene transform, seeds, and (optional)
	solved intervals geometry cache.

	Returns:
		Tuple (reader, motion_track, scene_transform, seeds_list,
			intervals_data_or_none).
	"""
	reader = video_io.VideoReader(input_file)
	print(f"  video: {reader.frame_count} frames, {reader.fps:.2f} fps, "
		f"{reader.width}x{reader.height}")

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
			f"Run 'setup' or 'solve' first.",
		)
	print(f"  motion cache: {cache_path}")

	scene_transform = scene_coords.SceneTransform(motion_track)

	seeds_path = tr_paths.default_seeds_path(input_file)
	seeds_data = state_io.load_seeds(seeds_path)
	seeds_list = seeds_data["seeds"]
	print(f"  seeds: {len(seeds_list)} loaded from {seeds_path}")

	# Geometry cache is optional; enables Stage 2 replay when present.
	intervals_data = None
	intervals_path = tr_paths.default_intervals_path(input_file)
	if os.path.exists(intervals_path):
		intervals_data = state_io.load_geometry_cache(intervals_path)
		n_solved = len(intervals_data["solved_intervals"])
		print(f"  solved intervals: {n_solved}")
	else:
		print(f"  solved intervals: (none; {intervals_path} missing)")

	result = (reader, motion_track, scene_transform, seeds_list,
		intervals_data)
	return result


#============================================
def pair_angle_stdev_deg(pair_vectors: list) -> float:
	"""Return stdev of per-pair heading angles (degrees) from a list of
	(dx, dy) tuples.

	Uses circular-statistics-friendly math: stdev of the heading in
	degrees, computed from angles unwrapped relative to the mean. For
	small windows this is indistinguishable from proper circular stdev.
	Returns 0.0 when fewer than 2 vectors or all-zero vectors.
	"""
	if len(pair_vectors) < 2:
		return 0.0
	# Drop zero-length vectors; they have no heading.
	nonzero = [(dx, dy) for (dx, dy) in pair_vectors if (dx * dx + dy * dy) > 0.0]
	if len(nonzero) < 2:
		return 0.0
	angles = [math.degrees(math.atan2(dy, dx)) for (dx, dy) in nonzero]
	# reference the mean angle via sin/cos so wrap-around is handled
	mean_sin = sum(math.sin(math.radians(a)) for a in angles) / len(angles)
	mean_cos = sum(math.cos(math.radians(a)) for a in angles) / len(angles)
	mean_angle = math.degrees(math.atan2(mean_sin, mean_cos))
	# wrap each angle into [-180, 180] relative to the mean
	deltas = []
	for a in angles:
		d = a - mean_angle
		while d > 180.0:
			d -= 360.0
		while d < -180.0:
			d += 360.0
		deltas.append(d)
	mean_d = sum(deltas) / len(deltas)
	var = sum((d - mean_d) ** 2 for d in deltas) / len(deltas)
	return math.sqrt(var)


#============================================
def linear_slope(values: list) -> float:
	"""Return the least-squares slope of `values` against index 0..N-1."""
	n = len(values)
	if n < 2:
		return 0.0
	xs = list(range(n))
	mean_x = sum(xs) / n
	mean_y = sum(values) / n
	num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
	den = sum((x - mean_x) ** 2 for x in xs)
	if den == 0.0:
		return 0.0
	return num / den


#============================================
def camera_pan_over_frames(
	motion_track,
	start_frame: int,
	end_frame: int,
) -> tuple:
	"""Return (mean, max) of per-frame camera pan magnitude over
	[start_frame, end_frame] inclusive.
	"""
	dx = motion_track.dx
	dy = motion_track.dy
	n = dx.shape[0]
	lo = max(0, min(start_frame, n - 1))
	hi = max(0, min(end_frame, n - 1))
	if lo > hi:
		return (0.0, 0.0)
	segment_dx = dx[lo:hi + 1]
	segment_dy = dy[lo:hi + 1]
	mag = numpy.hypot(segment_dx, segment_dy)
	return (float(numpy.mean(mag)), float(numpy.max(mag)))


#============================================
def residual_energy_at_frame(
	reader,
	frame_index: int,
	scene_transform,
	cache: dict,
) -> tuple:
	"""Compute (mean residual magnitude over valid pixels, valid pixel
	count) at `frame_index`. Returns (nan, 0) when residual is not
	available (edge of video, etc.).

	Uses scale_factor=0.5 (half-resolution warp + median) for speed; this
	tool is a diagnostic, not a solver, and per-frame mean magnitude is
	stable across this downsample.
	"""
	residual_mag, _raw_single, validity_mask, _disp = (
		residual_motion.compute_residual_for_frame(
			reader, frame_index, scene_transform,
			half_window=residual_motion.DEFAULT_HALF_WINDOW,
			scale_factor=0.5,
			return_extras=True,
		)
	)
	_ = cache
	if residual_mag is None or validity_mask is None:
		return (float("nan"), 0)
	valid = validity_mask > 0
	if not numpy.any(valid):
		return (float("nan"), 0)
	mean_e = float(numpy.mean(residual_mag[valid]))
	valid_n = int(numpy.count_nonzero(valid))
	return (mean_e, valid_n)


#============================================
def build_window_rows(
	usable: list,
	scene_centers: list,
	fps: float,
	motion_track,
	reader,
	scene_transform,
) -> list:
	"""Build one metric dict per sliding window.

	Each row matches production Stage 1: window size
	race_start.PRE_RACE_MIN_WINDOW_SEEDS, provisional torso scale from
	visible/partial seeds strictly before the window (fallback to the
	window's first seed when none).
	"""
	window_size = race_start.PRE_RACE_MIN_WINDOW_SEEDS
	last_window_idx = len(usable) - window_size
	# residual-motion reader cache reused across windows
	res_cache: dict = {}
	rows = []
	for i in range(last_window_idx + 1):
		seed_lo = usable[i]
		seed_hi = usable[i + window_size - 1]
		frame_lo = seed_lo["frame_index"]
		frame_hi = seed_hi["frame_index"]
		window_dt_s = (frame_hi - frame_lo) / fps

		# provisional torso scale (same rule as locate_race_start_interval)
		pre_boundary_widths = [
			float(s["w"]) for s in usable[:i]
			if s["status"] in ("visible", "partial")
		]
		if pre_boundary_widths:
			torso_scale = sum(pre_boundary_widths) / len(pre_boundary_widths)
			torso_source = "pre_window_mean"
		elif seed_lo["status"] in ("visible", "partial"):
			torso_scale = float(seed_lo["w"])
			torso_source = "window_first"
		else:
			torso_scale = float("nan")
			torso_source = "missing"

		# production coherence metrics
		if not math.isnan(torso_scale):
			net_torso, coherence, pair_disps = race_start.compute_window_metrics(
				scene_centers, i, window_size, torso_scale,
			)
			path_torso = sum(pair_disps) / torso_scale
			triggers_now = race_start.window_triggers(net_torso, coherence)
		else:
			net_torso = float("nan")
			coherence = float("nan")
			pair_disps = []
			path_torso = float("nan")
			triggers_now = False

		# next-window trigger (None if no next window exists)
		if i < last_window_idx and not math.isnan(torso_scale):
			next_net, next_coh, _ = race_start.compute_window_metrics(
				scene_centers, i + 1, window_size, torso_scale,
			)
			triggers_next = race_start.window_triggers(next_net, next_coh)
		else:
			triggers_next = False

		# angle variance across per-pair vectors in scene space
		pair_vectors = []
		for j in range(i, i + window_size - 1):
			ux, uy = scene_centers[j]
			vx, vy = scene_centers[j + 1]
			pair_vectors.append((vx - ux, vy - uy))
		angle_std = pair_angle_stdev_deg(pair_vectors)

		# torso-size trend across the window (in pixel widths)
		widths = [float(s["w"]) for s in usable[i:i + window_size]]
		size_mean = sum(widths) / len(widths)
		size_slope = linear_slope(widths)

		# camera pan velocity across the window's video frame span
		pan_mean, pan_max = camera_pan_over_frames(
			motion_track, frame_lo, frame_hi,
		)

		# residual-motion energy at the window's center frame -- only
		# computed on windows that pass the dt floor; others are
		# dense-annotation jitter clusters that cannot trigger anyway,
		# and computing residuals there wastes time on long videos.
		if window_dt_s >= race_start.MIN_PRE_RACE_PAIR_DT_S:
			center_frame = (frame_lo + frame_hi) // 2
			res_mean, res_valid = residual_energy_at_frame(
				reader, center_frame, scene_transform, res_cache,
			)
		else:
			res_mean, res_valid = (float("nan"), 0)

		if (i + 1) % 25 == 0 or i == last_window_idx:
			print(f"    window {i + 1}/{last_window_idx + 1}", flush=True)

		row = {
			"win_idx": i,
			"seed_lo_idx": i,
			"seed_hi_idx": i + window_size - 1,
			"frame_lo": frame_lo,
			"frame_hi": frame_hi,
			"window_dt_s": window_dt_s,
			"torso_scale": torso_scale,
			"torso_source": torso_source,
			"net_torso": net_torso,
			"path_torso": path_torso,
			"coherence": coherence,
			"angle_std_deg": angle_std,
			"size_mean_w": size_mean,
			"size_slope_w": size_slope,
			"pan_mean": pan_mean,
			"pan_max": pan_max,
			"res_mean": res_mean,
			"res_valid_px": res_valid,
			"triggers_now": triggers_now,
			"triggers_next": triggers_next,
			"dt_below_min": window_dt_s < race_start.MIN_PRE_RACE_PAIR_DT_S,
		}
		rows.append(row)
	return rows


#============================================
def mark_accepted_window(rows: list) -> int:
	"""Return the index of the first row that matches production accept
	rules: window_dt_s >= MIN_PRE_RACE_PAIR_DT_S, triggers_now, and
	triggers_next. Returns -1 when no window qualifies.
	"""
	for k, row in enumerate(rows):
		if row["dt_below_min"]:
			continue
		if not row["triggers_now"]:
			continue
		if not row["triggers_next"]:
			continue
		return k
	return -1


#============================================
TABLE_LEGEND = """\
Vocabulary
  interval = one seed-to-seed range (two adjacent seeds).
  window   = sliding group of {nseeds} consecutive seeds = {nintervals} adjacent
             intervals. One row per window below.

Columns
  win       window index (= index of its first seed)
  seed_lo   first seed index in the window
  seed_hi   last seed index in the window  (= seed_lo + {nintervals})
  frame_lo  video frame of seed_lo
  frame_hi  video frame of seed_hi
  dt_s      (frame_hi - frame_lo) / fps; production skips windows below
            MIN_PRE_RACE_PAIR_DT_S = {dt_floor:.2f} s as dense-jitter clusters
  net       net scene displacement seed_lo -> seed_hi, in torso widths
            (production threshold: PRE_RACE_NET_DISP_THRESHOLD_TORSO_UNITS
            = {net_thr:.2f})
  path      sum of per-interval scene distances inside the window, torso widths
  coh       net / path; 1.0 = aligned motion, 0.0 = jitter (production
            threshold: PRE_RACE_COHERENCE_THRESHOLD = {coh_thr:.2f})
  ang_std   stdev of per-interval heading angles inside the window (deg)
  size_w    mean torso width (px) over seeds in the window
  size_sl   linear slope of torso width across the {nseeds} seeds (px/seed)
  pan_m     mean camera pan magnitude over [frame_lo, frame_hi] (px/frame)
  pan_x     max  camera pan magnitude over [frame_lo, frame_hi] (px/frame)
  res_e     residual-motion energy (mean magnitude over valid pixels) at
            window center frame; nan when window is dt-skipped
  valid_px  number of valid residual pixels at the center frame
  trig      Y if production trigger fires on THIS window
            (net >= {net_thr:.2f} AND coh >= {coh_thr:.2f})
  next      Y if production trigger fires on the NEXT window
            (production accept needs both trig and next)
  accept    *  production accepts at this window
            d  rejected: dt below MIN_PRE_RACE_PAIR_DT_S
            .  evaluated but did not satisfy trig + next
"""


def _legend_text() -> str:
	"""Render the legend block with the current production constants."""
	nseeds = race_start.PRE_RACE_MIN_WINDOW_SEEDS
	nintervals = nseeds - 1
	return TABLE_LEGEND.format(
		nseeds=nseeds,
		nintervals=nintervals,
		dt_floor=race_start.MIN_PRE_RACE_PAIR_DT_S,
		net_thr=race_start.PRE_RACE_NET_DISP_THRESHOLD_TORSO_UNITS,
		coh_thr=race_start.PRE_RACE_COHERENCE_THRESHOLD,
	)


def _fmt(x, p):
	"""Tabulate-friendly formatter: pass NaN through as 'nan'."""
	if isinstance(x, float) and math.isnan(x):
		return "nan"
	return f"{x:.{p}f}"


def format_table(rows: list, accepted_idx: int) -> str:
	"""Return a printable per-window table built with tabulate."""
	headers = [
		"win", "seed_lo", "seed_hi", "frame_lo", "frame_hi", "dt_s",
		"net", "path", "coh", "ang_std", "size_w", "size_sl",
		"pan_m", "pan_x", "res_e", "valid_px", "trig", "next", "accept",
	]
	body = []
	for k, r in enumerate(rows):
		trig = "Y" if r["triggers_now"] else "."
		nxt = "Y" if r["triggers_next"] else "."
		if k == accepted_idx:
			acc = "*"
		elif r["dt_below_min"]:
			acc = "d"
		else:
			acc = "."
		body.append([
			r["win_idx"],
			r["seed_lo_idx"],
			r["seed_hi_idx"],
			r["frame_lo"],
			r["frame_hi"],
			_fmt(r["window_dt_s"], 3),
			_fmt(r["net_torso"], 2),
			_fmt(r["path_torso"], 2),
			_fmt(r["coherence"], 2),
			_fmt(r["angle_std_deg"], 1),
			_fmt(r["size_mean_w"], 1),
			_fmt(r["size_slope_w"], 2),
			_fmt(r["pan_mean"], 2),
			_fmt(r["pan_max"], 2),
			_fmt(r["res_mean"], 3),
			r["res_valid_px"],
			trig,
			nxt,
			acc,
		])
	return tabulate.tabulate(body, headers=headers, tablefmt="simple")


#============================================
def summarize_acceptance(rows: list, accepted_idx: int, usable: list) -> str:
	"""Return a one-line verdict (ACCEPTED / REJECTED + reason)."""
	if accepted_idx < 0:
		return (
			"REJECTED: no sliding window passes (dt>=min, triggers_now, "
			"triggers_next). Consider lowering PRE_RACE_* thresholds or "
			"adding more pre-race seeds."
		)
	row = rows[accepted_idx]
	window_size = race_start.PRE_RACE_MIN_WINDOW_SEEDS
	pair_disps_scene = []
	for j in range(row["win_idx"], row["win_idx"] + window_size - 1):
		ux, uy = _scene_center_cached(usable, j)
		vx, vy = _scene_center_cached(usable, j + 1)
		pair_disps_scene.append(math.hypot(vx - ux, vy - uy))
	transition_offset = max(
		range(len(pair_disps_scene)),
		key=lambda k: pair_disps_scene[k],
	)
	t_low_idx = row["win_idx"] + transition_offset
	t_hi_idx = t_low_idx + 1
	t_low_frame = usable[t_low_idx]["frame_index"]
	t_hi_frame = usable[t_hi_idx]["frame_index"]
	if t_low_idx == 0:
		return (
			f"ACCEPTED window={accepted_idx} but transition pair is at "
			f"seed index 0; production would return None (no pre-race "
			f"seed to anchor). race_start_interval = ({t_low_frame}, "
			f"{t_hi_frame})"
		)
	return (
		f"ACCEPTED window={accepted_idx} seed_lo=frame {t_low_frame} "
		f"seed_hi=frame {t_hi_frame}"
	)


_SCENE_CACHE: dict = {}
def _scene_center_cached(usable: list, idx: int) -> tuple:
	"""Lookup helper for summarize_acceptance; scene centers are
	precomputed in main() and attached to usable seeds under the
	`_scene` key so summarize doesn't need its own transform copy.
	"""
	return usable[idx]["_scene"]


#============================================
def find_interval_blended_path(
	intervals_data: dict,
	t_low_frame: int,
	t_hi_frame: int,
) -> list:
	"""Locate the solved interval whose start_frame == t_low_frame and
	end_frame == t_hi_frame and return its blended_path. Returns None
	when no match exists (cache may predate the Stage 1 result or the
	fingerprint differs).
	"""
	if intervals_data is None:
		return None
	for _fp, entry in intervals_data["solved_intervals"].items():
		if entry["start_frame"] == t_low_frame and entry["end_frame"] == t_hi_frame:
			return entry["blended_path"]
	return None


#============================================
def run_stage2_replay(
	blended_path: list,
	scene_transform,
	fps: float,
	t_low_frame: int,
) -> dict:
	"""Run the Stage 2 velocity-onset detector over a crossing interval's
	blended_path and return the detector's full result dict.
	"""
	result = race_phases.detect_race_start(blended_path, scene_transform, fps)
	return result


#============================================
def per_frame_scene_velocity(
	blended_path: list,
	start_frame: int,
	scene_transform,
	fps: float,
) -> tuple:
	"""Return (frames_list, scene_velocity_list) for plotting.

	Velocity is per-frame scene displacement magnitude in scene units *
	fps (units per second). blended_path entries have shape
	{"cx", "cy", "w", "h"} (no frame_index); position k corresponds to
	video frame `start_frame + k`.
	"""
	frames = []
	vels = []
	prev_scene = None
	for k, entry in enumerate(blended_path):
		if entry is None:
			frames.append(None)
			vels.append(float("nan"))
			prev_scene = None
			continue
		fi = start_frame + k
		cx = entry["cx"]
		cy = entry["cy"]
		w = entry["w"]
		h = entry["h"]
		sx, sy, _sw, _sh = scene_transform.pixel_box_to_scene(fi, cx, cy, w, h)
		if prev_scene is None:
			vels.append(float("nan"))
		else:
			d = math.hypot(sx - prev_scene[0], sy - prev_scene[1])
			vels.append(d * fps)
		frames.append(fi)
		prev_scene = (sx, sy)
	return (frames, vels)


#============================================
def png_path(input_file: str, png_dir: str, suffix: str) -> str:
	"""Build a diagnostic PNG path."""
	if png_dir:
		base_dir = png_dir
		os.makedirs(base_dir, exist_ok=True)
	else:
		base_dir = os.path.dirname(os.path.abspath(input_file))
	stem = os.path.basename(input_file)
	filename = f"{stem}.track_runner.pre_race_diag.{suffix}.png"
	return os.path.join(base_dir, filename)


#============================================
def plot_seed_timeline(
	usable: list,
	accepted_idx: int,
	output_path: str,
) -> None:
	"""Plot seed scene x/y vs frame with window boundaries highlighted."""
	frames = [s["frame_index"] for s in usable]
	xs = [s["_scene"][0] for s in usable]
	ys = [s["_scene"][1] for s in usable]

	fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
	axes[0].plot(frames, xs, marker="o", linewidth=1, color="tab:blue")
	axes[0].set_ylabel("scene x")
	axes[0].grid(True, alpha=0.3)
	axes[1].plot(frames, ys, marker="o", linewidth=1, color="tab:orange")
	axes[1].set_ylabel("scene y")
	axes[1].set_xlabel("frame index")
	axes[1].grid(True, alpha=0.3)

	if accepted_idx >= 0:
		lo = frames[accepted_idx]
		hi = frames[accepted_idx + race_start.PRE_RACE_MIN_WINDOW_SEEDS - 1]
		for ax in axes:
			ax.axvspan(lo, hi, alpha=0.2, color="tab:green",
				label="accepted window")
			ax.legend(loc="upper right")

	fig.suptitle(os.path.basename(output_path))
	fig.tight_layout()
	fig.savefig(output_path, dpi=120)
	plt.close(fig)
	print(f"  wrote {output_path}")


#============================================
def plot_window_metrics(
	rows: list,
	output_path: str,
) -> None:
	"""Plot per-window metric curves with production thresholds marked."""
	xs = [r["win_idx"] for r in rows]

	def col(name):
		return [r[name] for r in rows]

	fig, axes = plt.subplots(5, 1, figsize=(10, 11), sharex=True)

	axes[0].plot(xs, col("net_torso"), marker="o", color="tab:blue",
		label="net_torso")
	axes[0].axhline(
		race_start.PRE_RACE_NET_DISP_THRESHOLD_TORSO_UNITS,
		color="black", linestyle="--",
		label=f"threshold={race_start.PRE_RACE_NET_DISP_THRESHOLD_TORSO_UNITS}",
	)
	axes[0].set_ylabel("net disp\n(torso)")
	axes[0].legend(loc="upper left")
	axes[0].grid(True, alpha=0.3)

	axes[1].plot(xs, col("coherence"), marker="o", color="tab:green",
		label="coherence")
	axes[1].axhline(
		race_start.PRE_RACE_COHERENCE_THRESHOLD,
		color="black", linestyle="--",
		label=f"threshold={race_start.PRE_RACE_COHERENCE_THRESHOLD}",
	)
	axes[1].set_ylim(-0.05, 1.05)
	axes[1].set_ylabel("coherence")
	axes[1].legend(loc="lower right")
	axes[1].grid(True, alpha=0.3)

	axes[2].plot(xs, col("angle_std_deg"), marker="o", color="tab:purple")
	axes[2].set_ylabel("angle stdev\n(deg)")
	axes[2].grid(True, alpha=0.3)

	axes[3].plot(xs, col("pan_mean"), marker="o", color="tab:red",
		label="mean")
	axes[3].plot(xs, col("pan_max"), marker="s", color="tab:orange",
		label="max", markersize=4)
	axes[3].set_ylabel("camera pan\n(px/frame)")
	axes[3].legend(loc="upper left")
	axes[3].grid(True, alpha=0.3)

	axes[4].plot(xs, col("res_mean"), marker="o", color="tab:brown")
	axes[4].set_ylabel("residual\nenergy")
	axes[4].set_xlabel("window index")
	axes[4].grid(True, alpha=0.3)

	fig.suptitle(os.path.basename(output_path))
	fig.tight_layout()
	fig.savefig(output_path, dpi=120)
	plt.close(fig)
	print(f"  wrote {output_path}")


#============================================
def plot_stage2_velocity(
	frames: list,
	vels: list,
	stage2_result: dict,
	t_low_frame: int,
	t_hi_frame: int,
	output_path: str,
) -> None:
	"""Plot per-frame scene velocity over the crossing interval with the
	detector's chosen race_start_frame marked.
	"""
	valid_frames = [f for f in frames if f is not None]
	valid_vels = [v for v, f in zip(vels, frames) if f is not None]

	fig, ax = plt.subplots(1, 1, figsize=(10, 4))
	ax.plot(valid_frames, valid_vels, marker="o", color="tab:blue",
		linewidth=1)

	rs_frame = stage2_result.get("race_start_frame")
	if rs_frame is not None:
		ax.axvline(rs_frame, color="tab:green", linestyle="--",
			label=f"race_start_frame={rs_frame}")
	ax.axvline(t_low_frame, color="gray", linestyle=":",
		label=f"interval lo={t_low_frame}")
	ax.axvline(t_hi_frame, color="gray", linestyle=":",
		label=f"interval hi={t_hi_frame}")

	thr = stage2_result.get("threshold_used")
	if thr is not None:
		ax.axhline(thr, color="black", linestyle="--", alpha=0.5,
			label=f"threshold={thr:.2f}")

	ax.set_xlabel("frame index")
	ax.set_ylabel("scene velocity (units/s)")
	ax.grid(True, alpha=0.3)
	ax.legend(loc="upper left")
	fig.suptitle(os.path.basename(output_path))
	fig.tight_layout()
	fig.savefig(output_path, dpi=120)
	plt.close(fig)
	print(f"  wrote {output_path}")


#============================================
def main() -> None:
	args = parse_args()

	(reader, motion_track, scene_transform, seeds_list,
		intervals_data) = load_all_data(args.input_file)
	fps = reader.fps

	usable = interval_fingerprint.filter_usable_seeds_sorted(
		seeds_list, verbose=False,
	)
	print(f"  usable seeds: {len(usable)}")

	if len(usable) < race_start.PRE_RACE_MIN_WINDOW_SEEDS:
		raise RuntimeError(
			f"need at least {race_start.PRE_RACE_MIN_WINDOW_SEEDS} "
			f"usable seeds; have {len(usable)}",
		)

	# Precompute scene centers and attach to usable seeds for plotting.
	scene_centers = []
	for s in usable:
		sx, sy, _sw, _sh = scene_transform.pixel_box_to_scene(
			s["frame_index"], s["cx"], s["cy"], s["w"], s["h"],
		)
		scene_centers.append((sx, sy))
		s["_scene"] = (sx, sy)

	print("  computing per-window signals...")
	rows = build_window_rows(
		usable, scene_centers, fps, motion_track, reader, scene_transform,
	)

	accepted_idx = mark_accepted_window(rows)

	if not args.quiet:
		print()
		print(_legend_text())
		print(format_table(rows, accepted_idx))
		print()
	print(summarize_acceptance(rows, accepted_idx, usable))

	# Stage 2 replay if we can find a blended_path for the accepted pair.
	stage2_done = False
	if accepted_idx >= 0 and intervals_data is not None:
		# locate transition pair inside accepted window (largest per-pair disp)
		pair_disps_scene = []
		win_size = race_start.PRE_RACE_MIN_WINDOW_SEEDS
		for j in range(accepted_idx, accepted_idx + win_size - 1):
			ux, uy = scene_centers[j]
			vx, vy = scene_centers[j + 1]
			pair_disps_scene.append(math.hypot(vx - ux, vy - uy))
		transition_offset = max(
			range(len(pair_disps_scene)),
			key=lambda k: pair_disps_scene[k],
		)
		t_low_idx = accepted_idx + transition_offset
		t_hi_idx = t_low_idx + 1
		t_low_frame = usable[t_low_idx]["frame_index"]
		t_hi_frame = usable[t_hi_idx]["frame_index"]

		blended_path = find_interval_blended_path(
			intervals_data, t_low_frame, t_hi_frame,
		)
		if blended_path is not None:
			print()
			print(f"Stage 2 replay over interval "
				f"({t_low_frame}, {t_hi_frame}):")
			stage2_result = run_stage2_replay(
				blended_path, scene_transform, fps, t_low_frame,
			)
			for k, v in stage2_result.items():
				print(f"  {k}: {v}")

			pf_frames, pf_vels = per_frame_scene_velocity(
				blended_path, t_low_frame, scene_transform, fps,
			)
			vel_path = png_path(
				args.input_file, args.png_dir, "stage2_velocity",
			)
			plot_stage2_velocity(
				pf_frames, pf_vels, stage2_result,
				t_low_frame, t_hi_frame, vel_path,
			)
			stage2_done = True
		else:
			print(
				f"  (no solved interval matches "
				f"({t_low_frame},{t_hi_frame}); Stage 2 replay skipped)",
			)

	# PNGs
	print()
	print("writing diagnostic PNGs...")
	timeline_path = png_path(args.input_file, args.png_dir, "seed_timeline")
	plot_seed_timeline(usable, accepted_idx, timeline_path)

	metrics_path = png_path(args.input_file, args.png_dir, "window_metrics")
	plot_window_metrics(rows, metrics_path)

	if not stage2_done:
		print("  (stage2_velocity.png not written; no solved interval or "
			"no accepted window)")


#============================================
if __name__ == "__main__":
	main()
