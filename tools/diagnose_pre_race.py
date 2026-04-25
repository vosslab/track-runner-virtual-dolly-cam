#!/usr/bin/env python3
"""Diagnose pre-race race-start detection.

Vocabulary:
	- interval: a single seed-to-seed range (two adjacent seeds, the
		frames strictly between them). Per contract C5.
	- window:   a sliding group of N consecutive seeds (= N-1 adjacent
		intervals). Production Stage 1 uses windows of
		PRE_RACE_MIN_WINDOW_SEEDS (= 3) seeds = 2 adjacent intervals to
		compute coherence.

Read-only evidence gatherer. Computes a diagnostic race_start_frame
using the production Stage 1 coherence math
(race_start.locate_race_start_interval + pick_race_start_frame_midpoint),
then concentrates its output around that frame so the user can see
which signals are actually informative.

Per-window candidate signals (NOT yet in the solver):
	- per-pair heading angle variance
	- torso-size trend (mean width and linear slope)
	- camera pan velocity from motion_track
	- residual-motion energy at the window's center frame

PNG diagnostics:
	- <stem>.track_runner.pre_race_diag.seed_timeline.png
	      Scene x/y of every seed; race_start_frame marked.
	- <stem>.track_runner.pre_race_diag.window_metrics.png
	      Per-window metric curves zoomed to the windows immediately
	      around race_start_frame (so the eye can pick out the signal).
	- <stem>.track_runner.pre_race_diag.torso_residual.png
	      Per-frame median residual-motion magnitude inside the
	      projected pre-race torso box, around race_start_frame.

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
		description="Diagnose pre-race race-start detection signals",
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

	# Geometry cache is no longer consumed by this tool. Returning None
	# keeps the load_all_data signature stable for any future caller.
	intervals_data = None

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
def pick_diagnostic_race_start_frame(rows: list, usable: list, fps: float) -> tuple:
	"""Pick race_start_frame the same way production does.

	Production: find Stage 1 interval (locate_race_start_interval),
	then take ceil((interval_low + interval_high) / 2) via
	pick_race_start_frame_midpoint.

	Returns:
		Tuple (race_start_frame, source, accepted_window_idx,
			interval_low_frame, interval_high_frame). race_start_frame
			is None when no production-accepted window exists; in that
			case `source` says why.
	"""
	accepted_idx = mark_accepted_window(rows)
	if accepted_idx < 0:
		return (None, "no production-accepted window", -1, None, None)

	# Find the transition pair (largest per-interval scene displacement)
	# inside the accepted window, same logic as production.
	row = rows[accepted_idx]
	window_size = race_start.PRE_RACE_MIN_WINDOW_SEEDS
	pair_disps_scene = []
	for j in range(row["win_idx"], row["win_idx"] + window_size - 1):
		ux, uy = usable[j]["_scene"]
		vx, vy = usable[j + 1]["_scene"]
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
			None,
			"transition pair at seed index 0 (no pre-race seed)",
			accepted_idx, t_low_frame, t_hi_frame,
		)

	# ceil((low + high) / 2) -- same as race_start.pick_race_start_frame_midpoint
	rsf = race_start.pick_race_start_frame_midpoint(t_low_frame, t_hi_frame)
	source = (
		f"Stage 1 interval ({t_low_frame}, {t_hi_frame}); "
		f"midpoint = ceil(({t_low_frame}+{t_hi_frame})/2)"
	)
	_ = fps
	return (rsf, source, accepted_idx, t_low_frame, t_hi_frame)


#============================================
def compute_pre_race_anchor(
	usable: list,
	race_start_frame: int,
	scene_transform,
) -> dict:
	"""Mean scene-anchored center and torso size from seeds before
	race_start_frame. Same math as race_start.compute_pre_race_reference
	but operates on the diagnostic's `usable` list (which already has
	`_scene` cached) and is pure -- no warnings, no schema fields.

	Returns dict with scene_anchor_x, scene_anchor_y, torso_w, torso_h.
	"""
	qualifying = [
		s for s in usable
		if s["status"] in ("visible", "partial")
		and s["frame_index"] < race_start_frame
	]
	if not qualifying:
		raise RuntimeError(
			"no visible/partial pre-race seeds; cannot anchor torso box",
		)
	torso_w = sum(s["w"] for s in qualifying) / len(qualifying)
	torso_h = sum(s["h"] for s in qualifying) / len(qualifying)
	sxs = []
	sys_ = []
	for s in qualifying:
		sx, sy, _sw, _sh = scene_transform.pixel_box_to_scene(
			s["frame_index"], s["cx"], s["cy"], s["w"], s["h"],
		)
		sxs.append(sx)
		sys_.append(sy)
	return {
		"scene_anchor_x": sum(sxs) / len(sxs),
		"scene_anchor_y": sum(sys_) / len(sys_),
		"torso_w": torso_w,
		"torso_h": torso_h,
		"source_count": len(qualifying),
	}


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
	race_start_frame: int,
	output_path: str,
) -> None:
	"""Plot seed scene x/y vs frame, with the accepted window shaded
	and the chosen race_start_frame marked.
	"""
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
	if race_start_frame is not None:
		for ax in axes:
			ax.axvline(race_start_frame, color="tab:red", linestyle="--",
				label=f"race_start_frame={race_start_frame}")
	for ax in axes:
		ax.legend(loc="upper right")

	fig.suptitle(os.path.basename(output_path))
	fig.tight_layout()
	fig.savefig(output_path, dpi=120)
	plt.close(fig)
	print(f"  wrote {output_path}")


#============================================
def plot_window_metrics(
	rows: list,
	zoom_lo: int,
	zoom_hi: int,
	race_start_frame: int,
	output_path: str,
) -> None:
	"""Plot per-window metric curves over windows [zoom_lo, zoom_hi]
	(inclusive) with production thresholds marked. The zoom range is
	chosen to surround race_start_frame so the eye can isolate which
	signals discriminate motion from jitter; on long videos the full
	curves are visually flat outside this band.
	"""
	zoom_rows = [r for r in rows if zoom_lo <= r["win_idx"] <= zoom_hi]
	if not zoom_rows:
		zoom_rows = rows
	xs = [r["win_idx"] for r in zoom_rows]

	def col(name):
		return [r[name] for r in zoom_rows]

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

	# Mark race_start_frame on every panel by mapping it to the
	# nearest window index visible in the zoom range.
	if race_start_frame is not None:
		nearest = min(
			zoom_rows,
			key=lambda r: abs(r["frame_lo"] - race_start_frame),
		)
		for ax in axes:
			ax.axvline(nearest["win_idx"], color="tab:red", linestyle="--",
				alpha=0.6,
				label=f"race_start near win={nearest['win_idx']}")
		axes[0].legend(loc="upper left")

	fig.suptitle(os.path.basename(output_path))
	fig.tight_layout()
	fig.savefig(output_path, dpi=120)
	plt.close(fig)
	print(f"  wrote {output_path}")


#============================================
def torso_residual_at_frame(
	reader,
	scene_transform,
	anchor: dict,
	frame_index: int,
	scale_factor: float,
) -> float:
	"""Compute the median residual-motion magnitude inside the projected
	pre-race torso box at `frame_index`.

	The pre-race scene anchor (scene_anchor_x, scene_anchor_y, torso_w,
	torso_h) is the contract-C2 reference: where the runner WAS before
	race start. Projecting it back to pixel space at each frame using
	scene_transform.scene_box_to_pixel gives the rectangle the runner
	would still occupy if the race had not started. When the race
	starts, the runner moves out of this box, the residual map shows
	high foreground there (the runner's old silhouette is now exposed
	background, and the runner appears nearby), and the median magnitude
	rises sharply.

	Returns NaN when the residual map cannot be computed (edge of video)
	or when the projected box is fully off-frame.
	"""
	residual_mag, _raw_single, validity_mask, _disp = (
		residual_motion.compute_residual_for_frame(
			reader, frame_index, scene_transform,
			half_window=residual_motion.DEFAULT_HALF_WINDOW,
			scale_factor=scale_factor,
			return_extras=True,
		)
	)
	if residual_mag is None or validity_mask is None:
		return float("nan")

	# project the scene-anchored torso box to pixel coordinates at this frame
	pcx, pcy, pw, ph = scene_transform.scene_box_to_pixel(
		frame_index,
		anchor["scene_anchor_x"], anchor["scene_anchor_y"],
		anchor["torso_w"], anchor["torso_h"],
	)
	# residual_mag is at scale_factor relative to native pixel resolution
	pcx *= scale_factor
	pcy *= scale_factor
	pw *= scale_factor
	ph *= scale_factor
	x1 = int(round(pcx - pw / 2.0))
	x2 = int(round(pcx + pw / 2.0))
	y1 = int(round(pcy - ph / 2.0))
	y2 = int(round(pcy + ph / 2.0))
	rh, rw = residual_mag.shape[:2]
	x1 = max(0, x1)
	y1 = max(0, y1)
	x2 = min(rw, x2)
	y2 = min(rh, y2)
	if x2 <= x1 or y2 <= y1:
		return float("nan")
	box = residual_mag[y1:y2, x1:x2]
	box_valid = validity_mask[y1:y2, x1:x2] > 0
	if not numpy.any(box_valid):
		return float("nan")
	return float(numpy.median(box[box_valid]))


#============================================
def plot_torso_residual_intensity(
	reader,
	scene_transform,
	anchor: dict,
	race_start_frame: int,
	frame_lo: int,
	frame_hi: int,
	output_path: str,
) -> None:
	"""Per-frame median residual-motion magnitude inside the projected
	pre-race torso box, plotted across [frame_lo, frame_hi]. The
	expected signal: low and flat before race_start_frame (runner still
	occupying the box, no residual), then rising as the runner exits
	the box and is replaced by background.
	"""
	frames = list(range(frame_lo, frame_hi + 1))
	medians = []
	scale_factor = 0.5
	for f in frames:
		medians.append(
			torso_residual_at_frame(
				reader, scene_transform, anchor, f, scale_factor,
			),
		)
		if (len(medians) % 20 == 0) or (len(medians) == len(frames)):
			print(f"    residual {len(medians)}/{len(frames)}", flush=True)

	fig, ax = plt.subplots(1, 1, figsize=(10, 4))
	ax.plot(frames, medians, marker="o", color="tab:brown", linewidth=1)
	ax.axvline(race_start_frame, color="tab:green", linestyle="--",
		label=f"race_start_frame={race_start_frame}")
	ax.set_xlabel("frame index")
	ax.set_ylabel("median residual magnitude\nin projected torso box")
	ax.grid(True, alpha=0.3)
	ax.legend(loc="upper left")
	fig.suptitle(os.path.basename(output_path))
	fig.tight_layout()
	fig.savefig(output_path, dpi=120)
	plt.close(fig)
	print(f"  wrote {output_path}")


#============================================
ZOOM_WINDOWS_PER_SIDE = 12
RESIDUAL_FRAMES_PER_SIDE_S = 1.0


def main() -> None:
	args = parse_args()

	(reader, motion_track, scene_transform, seeds_list,
		_intervals_data) = load_all_data(args.input_file)
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

	# Pick race_start_frame the production way (Stage 1 + midpoint).
	race_start_frame, source, accepted_idx, t_low_frame, t_hi_frame = (
		pick_diagnostic_race_start_frame(rows, usable, fps)
	)

	if not args.quiet:
		print()
		print(_legend_text())
		print(format_table(rows, accepted_idx))
		print()

	print()
	print("=" * 60)
	if race_start_frame is not None:
		print(f"RACE_START_FRAME = {race_start_frame}")
		print(f"  source: {source}")
		print(f"  Stage 1 interval: ({t_low_frame}, {t_hi_frame})")
	else:
		print("RACE_START_FRAME = (not picked)")
		print(f"  reason: {source}")
	print("=" * 60)

	# PNGs
	print()
	print("writing diagnostic PNGs...")
	timeline_path = png_path(args.input_file, args.png_dir, "seed_timeline")
	plot_seed_timeline(usable, accepted_idx, race_start_frame, timeline_path)

	# Zoomed window metrics: ZOOM_WINDOWS_PER_SIDE on each side of the
	# accepted window. When no acceptance, fall back to first 50 windows.
	if accepted_idx >= 0:
		zoom_lo = max(0, accepted_idx - ZOOM_WINDOWS_PER_SIDE)
		zoom_hi = min(len(rows) - 1, accepted_idx + ZOOM_WINDOWS_PER_SIDE)
	else:
		zoom_lo = 0
		zoom_hi = min(len(rows) - 1, 50)
	metrics_path = png_path(args.input_file, args.png_dir, "window_metrics")
	plot_window_metrics(
		rows, zoom_lo, zoom_hi, race_start_frame, metrics_path,
	)

	# Torso-residual intensity around race_start_frame.
	if race_start_frame is not None:
		anchor = compute_pre_race_anchor(
			usable, race_start_frame, scene_transform,
		)
		print(f"  pre-race anchor: scene=({anchor['scene_anchor_x']:.1f}, "
			f"{anchor['scene_anchor_y']:.1f}) "
			f"torso=({anchor['torso_w']:.1f}, {anchor['torso_h']:.1f}) "
			f"from {anchor['source_count']} pre-race seeds")
		span = max(2, int(round(RESIDUAL_FRAMES_PER_SIDE_S * fps)))
		f_lo = max(0, race_start_frame - span)
		f_hi = min(reader.frame_count - 1, race_start_frame + span)
		residual_path = png_path(
			args.input_file, args.png_dir, "torso_residual",
		)
		plot_torso_residual_intensity(
			reader, scene_transform, anchor,
			race_start_frame, f_lo, f_hi, residual_path,
		)
	else:
		print("  (torso_residual.png not written; no race_start_frame)")


#============================================
if __name__ == "__main__":
	main()
