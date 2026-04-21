#!/usr/bin/env python3
"""CLI entry point for the track_runner tool v2.

Multi-pass orchestration: seed collection, interval solving, refinement,
crop trajectory computation, and video encoding.

Subcommands:
  seed    Collect/add seeds, save, exit
  edit    Review/fix/delete existing seeds interactively
  target  Add seeds at weak interval frames with FWD/BWD overlays
  solve   Full re-solve: clears prior results and solves all intervals fresh
  refine  Re-solve only changed/new intervals, reuse prior results
  encode  Encode from existing trajectory, no solving
"""

# Standard Library
import argparse
import json
import os
import shutil
import statistics
import subprocess
import time

# local repo modules
import cli_args
import tr_config
import state_io
import tr_paths
import tr_video_identity
import encoder
import video_io
import seeding
import scoring
import seed_editor
import setup_mode
import interval_solver
import solve_queue
import review
import tr_crop
import key_input
import encode_analysis
import regime_classifier
import camera_motion
import scene_coords
import common_tools.frame_filters as frame_filters

# module-level video identity, set once in main() and treated as read-only
VIDEO_IDENTITY = None


#============================================
def _probe_video(input_file: str) -> dict:
	"""Probe video metadata using mediainfo JSON output.

	Extracts resolution, fps, frame count, and duration from the first
	video track. Falls back to General track for frame count and duration
	when the Video track lacks them.

	Args:
		input_file: Path to the input video file.

	Returns:
		Dict with keys: width, height, fps, frame_count, duration_s.

	Raises:
		RuntimeError: If mediainfo fails or returns no video track.
	"""
	mediainfo_path = shutil.which("mediainfo")
	if mediainfo_path is None:
		raise RuntimeError("mediainfo not found in PATH")
	cmd = [mediainfo_path, "--Output=JSON", input_file]
	result = subprocess.run(cmd, capture_output=True, text=True)
	if result.returncode != 0:
		raise RuntimeError(f"mediainfo failed: {result.stderr.strip()}")
	data = json.loads(result.stdout)
	media = data.get("media")
	if media is None:
		raise RuntimeError(f"mediainfo returned no media for: {input_file}")
	tracks = media.get("track", [])
	# find the first Video track and General track
	video_track = None
	general_track = None
	for track in tracks:
		track_type = track.get("@type", "")
		if track_type == "Video" and video_track is None:
			video_track = track
		elif track_type == "General" and general_track is None:
			general_track = track
	if video_track is None:
		raise RuntimeError(f"no video track found in: {input_file}")
	# extract resolution
	width = int(video_track["Width"])
	height = int(video_track["Height"])
	# extract fps (mediainfo provides FrameRate as a decimal string)
	fps = float(video_track.get("FrameRate", "0"))
	if fps <= 0:
		raise RuntimeError(f"invalid fps from mediainfo: {input_file}")
	# extract frame count; fall back to General track, then duration * fps
	frame_count_str = video_track.get("FrameCount")
	if frame_count_str is None and general_track is not None:
		frame_count_str = general_track.get("FrameCount")
	duration_str = video_track.get("Duration")
	if duration_str is None and general_track is not None:
		duration_str = general_track.get("Duration")
	if frame_count_str is not None:
		frame_count = int(frame_count_str)
		duration_s = frame_count / fps
	elif duration_str is not None:
		duration_s = float(duration_str)
		frame_count = int(duration_s * fps)
	else:
		raise RuntimeError(f"no frame count or duration from mediainfo: {input_file}")
	info = {
		"width": width,
		"height": height,
		"fps": fps,
		"frame_count": frame_count,
		"duration_s": duration_s,
	}
	return info


#============================================
def _check_identity_mismatch(label: str, path: str) -> None:
	"""Check a data file for video identity mismatch and print warnings.

	Reads the stored `video_identity` block from the file (JSON or
	NPZ) and compares it against VIDEO_IDENTITY. Mismatches produce
	warning messages but do not raise errors.

	Args:
		label: Human-readable name for the data file (e.g. "seeds").
		path: Path to the data file (`.json` or `.npz`).
	"""
	if VIDEO_IDENTITY is None:
		return
	if not os.path.isfile(path):
		return
	# NPZ files (geometry_cache.npz) carry video_identity as
	# JSON-encoded bytes; JSON files carry it as a top-level key.
	if path.endswith(".npz"):
		cache_data = state_io.load_geometry_cache(path)
		stored = cache_data.get("video_identity")
	else:
		with open(path, "r") as fh:
			data = json.load(fh)
		stored = data.get("video_identity")
	if stored is None:
		return
	mismatches = tr_video_identity.compare_video_identity(stored, VIDEO_IDENTITY)
	if mismatches:
		print(f"  warning: {label} file video identity mismatch:")
		for msg in mismatches:
			print(f"    {msg}")


#============================================
def _parse_time_range(time_range_str: str | None) -> tuple | None:
	"""Parse a 'START:END' time range string into a (start_s, end_s) tuple.

	Supports open-ended ranges: '200:' means from 200s to end,
	':500' means from start to 500s.

	Args:
		time_range_str: String like '30:120', '200:', ':500', or None.

	Returns:
		Tuple (start_s, end_s) where either may be None for open-ended
		ranges, or None if input is None.

	Raises:
		RuntimeError: If the string format is invalid.
	"""
	if time_range_str is None:
		return None
	parts = time_range_str.split(":")
	if len(parts) != 2:
		raise RuntimeError(
			f"Invalid --time-range format '{time_range_str}', expected 'START:END'"
		)
	# parse start, allowing empty string for open-ended start
	start_s = float(parts[0]) if parts[0].strip() else None
	# parse end, allowing empty string for open-ended end
	end_s = float(parts[1]) if parts[1].strip() else None
	return (start_s, end_s)


#============================================
def _validate_diagnostics_confidence(diagnostics: dict) -> None:
	"""Check that all intervals have confidence data.

	Raises RuntimeError if any interval is missing the confidence
	field, indicating stale diagnostics that need a fresh solve.

	Args:
		diagnostics: Dict with "intervals" key.
	"""
	for iv in diagnostics.get("intervals", []):
		score = iv["interval_score"]
		# accept legacy "confidence" or analytical "confidence_tier"
		if "confidence" not in score and "confidence_tier" not in score:
			raise RuntimeError(
				"diagnostics missing confidence data. "
				"Run 'solve' to regenerate."
			)


#============================================
def _ensure_target_diagnostics(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	seeds: list,
	diag_path: str,
	intervals_path: str,
) -> dict:
	"""Return diagnostics usable by target mode, solving when needed.

	Target mode depends on interval diagnostics to rank weak spans and
	build prediction overlays. If the diagnostics file is missing,
	empty, or stale, this helper runs a fresh solve so the interactive
	target workflow does not require a separate manual solve step.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		seeds: Loaded seed list.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved-intervals JSON file.

	Returns:
		Diagnostics dict with interval confidence data.
	"""
	need_solve = False
	reason = None
	if not os.path.isfile(diag_path):
		need_solve = True
		reason = "missing diagnostics"
	else:
		diag_data = state_io.load_diagnostics(diag_path)
		if not diag_data.get("intervals"):
			need_solve = True
			reason = "diagnostics file has no intervals"
		else:
			try:
				_validate_diagnostics_confidence(diag_data)
				return diag_data
			except RuntimeError:
				need_solve = True
				reason = "diagnostics missing confidence data"
	if need_solve:
		raise RuntimeError(
			f"target mode: {reason}. Run 'solve' first, then re-run target."
		)
	return diag_data


#============================================
def _print_quality_summary(diagnostics: dict, fps: float) -> None:
	"""Print a human-readable quality summary from diagnostics.

	Args:
		diagnostics: Dict from interval_solver.solve_all_intervals().
		fps: Video frame rate for time calculations.
	"""
	intervals = diagnostics.get("intervals", [])
	total = len(intervals)

	# count by confidence tier
	from collections import Counter
	tier_counts = Counter(
		review.get_confidence_label(iv["interval_score"])
		for iv in intervals
	)
	need_seed = tier_counts.get("low", 0) + tier_counts.get("fair", 0)

	print("")
	print(f"quality summary: "
		f"{tier_counts.get('high', 0)} high, "
		f"{tier_counts.get('good', 0)} good, "
		f"{tier_counts.get('fair', 0)} fair, "
		f"{tier_counts.get('low', 0)} low "
		f"({total} intervals)")
	if review.needs_refinement(diagnostics):
		print(f"  {need_seed} intervals need seeds (fair + low)")
		# compute severity breakdown for seed-needing intervals
		high_count = 0
		medium_count = 0
		low_count = 0
		for iv in intervals:
			score = iv["interval_score"]
			if review.get_confidence_label(score) in ("high", "good"):
				continue
			sev = review.classify_interval_severity(iv, fps)
			if sev == "high":
				high_count += 1
			elif sev == "medium":
				medium_count += 1
			else:
				low_count += 1
		print(f"  severity breakdown: {high_count} high, "
			f"{medium_count} medium, {low_count} low")
		print(f"  hint: use --severity=high to focus on the "
			f"{high_count} worst intervals")
	else:
		print("  all intervals acceptable -- no seeds needed")
	print("")


#============================================
def _build_predictions_from_diagnostics(diagnostics: dict) -> dict:
	"""Build frame-indexed prediction dict with overlays and interval metadata.

	Each frame entry includes FWD/BWD/fused/consensus boxes plus interval-level
	quality metadata (severity, confidence, scores, failure reasons) so the GUI
	can display why an interval was flagged.

	Args:
		diagnostics: Dict from solve_all_intervals() with in-memory interval data.

	Returns:
		Dict mapping frame_index (int) to prediction dict with box data and
		an "interval_info" sub-dict for quality display.
	"""
	fps = float(diagnostics.get("fps", 30.0))
	predictions = {}
	for iv in diagnostics.get("intervals", []):
		fwd_track = iv.get("forward_track")
		bwd_track = iv.get("backward_track")
		fused_track = iv.get("fused_track")
		if fwd_track is None or bwd_track is None:
			# stored intervals may lack per-direction tracks
			continue

		# build interval quality metadata once per interval
		score = iv.get("interval_score", {})
		severity = review.classify_interval_severity(iv, fps)
		# extract metrics from v3 analytical or v2 legacy score
		if "confidence_tier" in score:
			conf_label = score.get("confidence_tier", "unknown")
			agree_val = float(score.get("agreement", 0.0))
			secondary_val = float(score.get("velocity_consistency", 0.0))
		else:
			conf_label = score.get("confidence", "unknown")
			agree_val = float(score.get("agreement_score", 0.0))
			secondary_val = float(score.get("competitor_margin", 0.0))
		interval_info = {
			"severity": severity,
			"confidence": conf_label,
			"agreement": agree_val,
			"margin": secondary_val,
			"reasons": score.get("failure_reasons", []),
		}

		start_frame = int(iv["start_frame"])
		n = min(len(fwd_track), len(bwd_track))
		for i in range(n):
			frame_index = start_frame + i
			frame_preds = {
				"forward": fwd_track[i],
				"backward": bwd_track[i],
				"interval_info": interval_info,
			}
			# add fused (refined second-pass) if available
			if fused_track is not None and i < len(fused_track):
				frame_preds["fused"] = fused_track[i]
			# compute consensus as average of FWD and BWD
			fwd_box = fwd_track[i]
			bwd_box = bwd_track[i]
			consensus = {
				"cx": (float(fwd_box["cx"]) + float(bwd_box["cx"])) / 2.0,
				"cy": (float(fwd_box["cy"]) + float(bwd_box["cy"])) / 2.0,
				"w": (float(fwd_box["w"]) + float(bwd_box["w"])) / 2.0,
				"h": (float(fwd_box["h"]) + float(bwd_box["h"])) / 2.0,
			}
			frame_preds["consensus"] = consensus
			predictions[frame_index] = frame_preds
	return predictions


#============================================
# tracks whether we already warned the user about a missing debug
# sidecar in the current process; keeps the log output quiet across
# multiple debug-overlay consumers in the same invocation
_WARNED_MISSING_DEBUG_SIDECAR = False


def _merge_debug_tracks(solved_intervals: dict, sidecar_path: str) -> None:
	"""Merge forward/backward tracks from a debug sidecar in place.

	If the sidecar exists, every interval whose fingerprint matches an
	entry in both the geometry cache and the sidecar gains
	`forward_track` and `backward_track` keys on its in-memory dict so
	downstream debug-overlay code paths (which use `iv.get(
	"forward_track")`) render correctly. Fingerprints that appear in
	only one side are left alone; sidecar entries that do not match
	the current geometry cache are counted and reported but not
	applied.

	If the sidecar is absent, prints a single "run with
	--debug-tracks to regenerate" message per process and returns.

	Args:
		solved_intervals: Dict of fingerprint -> in-memory interval
			dict (mutated in place).
		sidecar_path: Path to the debug_tracks.npz sidecar.
	"""
	global _WARNED_MISSING_DEBUG_SIDECAR
	if not os.path.isfile(sidecar_path):
		if not _WARNED_MISSING_DEBUG_SIDECAR:
			_WARNED_MISSING_DEBUG_SIDECAR = True
			print(
				"  fwd/bwd debug overlay unavailable "
				"(run solve with --debug-tracks to regenerate)"
			)
		return
	debug_data = state_io.load_debug_tracks(sidecar_path)
	if not debug_data:
		return
	matched = 0
	stale = 0
	for fingerprint, tracks in debug_data.items():
		if fingerprint in solved_intervals:
			solved_intervals[fingerprint]["forward_track"] = tracks[
				"forward_track"
			]
			solved_intervals[fingerprint]["backward_track"] = tracks[
				"backward_track"
			]
			matched += 1
		else:
			stale += 1
	if stale > 0:
		print(
			f"  debug tracks: {matched} intervals matched, "
			f"{stale} sidecar entries stale (re-run --debug-tracks "
			f"to refresh)"
		)


#============================================
def _load_prior_results(intervals_path: str) -> tuple:
	"""Load previously solved intervals and build a write-through callback.

	Returns the solved-intervals dict and a callback that persists new
	entries to disk immediately after each interval is solved.

	Args:
		intervals_path: Path to the solved-intervals JSON file.

	Returns:
		Tuple (prior_results_dict, on_interval_solved_callback).
	"""
	intervals_file = state_io.load_geometry_cache(intervals_path)
	solved = intervals_file.get("solved_intervals", {})

	def _on_interval_solved(fingerprint: str, result: dict) -> None:
		"""Persist a newly solved interval to disk."""
		solved[fingerprint] = result
		intervals_file["solved_intervals"] = solved
		# embed video identity in each write
		if VIDEO_IDENTITY is not None:
			intervals_file["video_identity"] = VIDEO_IDENTITY
		state_io.write_geometry_cache(intervals_path, intervals_file)

	return (solved, _on_interval_solved)


#============================================
def _invalidate_intervals_for_frames(
	intervals_path: str,
	changed_frames: set,
) -> None:
	"""Remove solved intervals that touch any of the changed seed frames.

	Each fingerprint key encodes two seed frame indices separated by pipe
	characters. An interval is invalidated if either its start or end
	frame index appears in changed_frames.

	Args:
		intervals_path: Path to the solved-intervals JSON file.
		changed_frames: Set of frame_index ints that were modified.
	"""
	intervals_file = state_io.load_geometry_cache(intervals_path)
	solved = intervals_file.get("solved_intervals", {})
	if not solved:
		return
	# extract frame indices from each fingerprint and check for overlap
	keys_to_remove = []
	for fp in solved:
		# fingerprint format: "frame|cx|cy|w|h|frame|cx|cy|w|h"
		parts = fp.split("|")
		# first frame index is parts[0], second is parts[5]
		start_fi = int(parts[0])
		end_fi = int(parts[5])
		if start_fi in changed_frames or end_fi in changed_frames:
			keys_to_remove.append(fp)
	if not keys_to_remove:
		print(f"  no solved intervals affected by {len(changed_frames)} changed seeds")
		return
	for key in keys_to_remove:
		del solved[key]
	intervals_file["solved_intervals"] = solved
	if VIDEO_IDENTITY is not None:
		intervals_file["video_identity"] = VIDEO_IDENTITY
	state_io.write_geometry_cache(intervals_path, intervals_file)
	remaining = len(solved)
	print(f"  invalidated {len(keys_to_remove)} solved intervals "
		f"({remaining} remaining)")


#============================================
def _resolve_workers(args: argparse.Namespace) -> int:
	"""Resolve worker count from args or auto-detect.

	Args:
		args: Parsed argparse namespace.

	Returns:
		Number of workers to use.
	"""
	cpu_count = os.cpu_count()
	num_workers = getattr(args, "workers", None)
	if num_workers is None:
		num_workers = max(1, cpu_count // 2)
	print(f"  workers: {num_workers} (of {cpu_count} CPUs)")
	return num_workers


#============================================
def _load_and_deduplicate_seeds(seeds_path: str) -> list:
	"""Load seeds from disk and deduplicate by frame_index.

	Keeps latest pass when duplicates exist at the same frame.

	Args:
		seeds_path: Path to the seeds JSON file.

	Returns:
		Deduplicated list of seed dicts sorted by frame_index.
	"""
	seeds_data = state_io.load_seeds(seeds_path)
	seeds = seeds_data.get("seeds", [])
	if not seeds:
		return seeds
	# deduplicate: keep latest pass per frame
	seen_frames = {}
	for seed in seeds:
		fi = int(seed["frame_index"])
		if fi in seen_frames:
			existing = seen_frames[fi]
			if int(seed["pass"]) >= int(existing["pass"]):
				seen_frames[fi] = seed
		else:
			seen_frames[fi] = seed
	if len(seen_frames) < len(seeds):
		dropped = len(seeds) - len(seen_frames)
		print(f"  removed {dropped} duplicate seeds")
		seeds = sorted(seen_frames.values(), key=lambda s: int(s["frame_index"]))
		# write cleaned seeds back to disk
		seeds_data_out = {
			state_io.SEEDS_HEADER_KEY: state_io.SEEDS_HEADER_VALUE,
			"seeds": seeds,
		}
		state_io.write_seeds(seeds_path, seeds_data_out)
		print(f"  saved {len(seeds)} deduplicated seeds")
	return seeds


#============================================
def _save_seeds_to_disk(seeds: list, seeds_path: str) -> None:
	"""Write seeds list to disk with proper header and video identity.

	Args:
		seeds: List of seed dicts.
		seeds_path: Output file path.
	"""
	seeds_data = {
		state_io.SEEDS_HEADER_KEY: state_io.SEEDS_HEADER_VALUE,
		"seeds": seeds,
	}
	if VIDEO_IDENTITY is not None:
		seeds_data["video_identity"] = VIDEO_IDENTITY
	state_io.write_seeds(seeds_path, seeds_data)


#============================================
def _make_save_callback(seeds_path: str) -> object:
	"""Build an incremental save callback for crash-safe seed saving.

	Args:
		seeds_path: Path to the seeds JSON file.

	Returns:
		Callable that accepts a seeds list and writes it to disk.
	"""
	def _save(seeds_list: list) -> None:
		"""Write seeds to disk after each new seed is collected."""
		_save_seeds_to_disk(seeds_list, seeds_path)
	return _save


#============================================
def _validate_usable_seeds(seeds: list) -> tuple:
	"""Validate that enough usable seeds exist for solving.

	Args:
		seeds: List of seed dicts.

	Returns:
		Tuple of (usable_seeds, visible_count, partial_count).

	Raises:
		RuntimeError: If fewer than 2 usable seeds exist.
	"""
	usable_seeds = [
		s for s in seeds
		if s["status"] in ("visible", "partial")
	]
	visible_count = sum(
		1 for s in usable_seeds
		if s["status"] == "visible"
	)
	partial_count = sum(
		1 for s in usable_seeds if s.get("status") == "partial"
	)
	if len(usable_seeds) < 2:
		raise RuntimeError(
			f"need at least 2 usable seeds (visible or partial); "
			f"got {len(usable_seeds)} ({len(seeds)} total)"
		)
	# log absence seed counts if any exist
	not_in_frame_count = sum(
		1 for s in seeds if s.get("status") == "not_in_frame"
	)
	approx_count = sum(
		1 for s in seeds if s.get("status") in ("approximate", "obstructed")
	)
	if not_in_frame_count > 0 or approx_count > 0 or partial_count > 0:
		print(f"  seed status breakdown: "
			f"{visible_count} visible, {partial_count} partial, "
			f"{approx_count} approx, {not_in_frame_count} not_in_frame")
	return (usable_seeds, visible_count, partial_count)


#============================================
def _run_solve(
	args: argparse.Namespace,
	cfg: dict,
	seeds: list,
	video_info: dict,
	intervals_path: str,
	diag_path: str,
	num_workers: int,
	on_interval_complete: object = None,
) -> dict:
	"""Run the interval solver and write diagnostics.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		seeds: List of seed dicts for solving.
		video_info: Video metadata dict.
		intervals_path: Path to solved-intervals file.
		diag_path: Path to write diagnostics.
		num_workers: Number of parallel workers.
		on_interval_complete: Optional callback for each solved interval.

	Returns:
		Diagnostics dict from solve_all_intervals().
	"""
	fps = video_info["fps"]
	usable_seeds, _, _ = _validate_usable_seeds(seeds)

	print(f"solving... "
		f"({len(usable_seeds)} usable seeds, {num_workers} workers)")
	print("  (press Q to quit, P to pause)")
	t_solve_start = time.time()
	prior_ivs, on_solved_cb = _load_prior_results(intervals_path)
	# build solver kwargs
	solve_kwargs = {
		"num_workers": num_workers,
		"debug": args.debug,
		"prior_intervals": prior_ivs,
		"on_interval_solved": on_solved_cb,
	}
	if on_interval_complete is not None:
		solve_kwargs["on_interval_complete"] = on_interval_complete

	# Precompute camera motion for scene coordinate transformation
	print("precomputing camera motion...")
	cache_dir = tr_paths.ensure_data_dir()
	with video_io.VideoReader(args.input_file) as reader:
		motion_track = camera_motion.precompute_camera_motion(
			reader, cfg, args.input_file, video_info, cache_dir
		)
	motion_track_data = motion_track
	scene_transform = scene_coords.SceneTransform(motion_track)
	# pass the video path through so workers can reopen it in their own
	# process; VideoReader instances cannot cross the process boundary.
	solve_kwargs["video_path"] = args.input_file

	# set up keyboard controls and signal handler
	rc = key_input.RunControl()
	key_input.install_sigint_handler(rc)
	# enable quit-chain tracing when debug flag is set
	if args.debug:
		key_input.QUIT_TRACE = True
	with key_input.KeyInputReader() as kreader:
		solve_kwargs["run_control"] = rc
		solve_kwargs["key_reader"] = kreader
		# analytical solver does not use YOLO detector
		detector = None
		with video_io.VideoReader(args.input_file) as reader:
			diagnostics = interval_solver.solve_all_intervals(
				reader, seeds,
				detector,
				cfg,
				scene_transform=scene_transform,
				motion_track=motion_track_data,
				**solve_kwargs,
			)
	# restore default signal handler
	key_input.restore_default_sigint()
	diagnostics["fps"] = fps
	t_solve_elapsed = time.time() - t_solve_start
	# mark whether the solve completed or was interrupted
	intervals_file = state_io.load_geometry_cache(intervals_path)
	if rc.quit_requested:
		intervals_file["solve_complete"] = False
		print(f"  solve interrupted ({t_solve_elapsed:.1f}s)")
	else:
		intervals_file["solve_complete"] = True
		print(f"  solve complete ({t_solve_elapsed:.1f}s)")
	if VIDEO_IDENTITY is not None:
		intervals_file["video_identity"] = VIDEO_IDENTITY
	state_io.write_geometry_cache(intervals_path, intervals_file)
	# opt-in debug-tracks sidecar: uses the in-memory prior_ivs dict,
	# which still carries forward_track/backward_track for every
	# newly-solved interval because _on_interval_solved stored the full
	# result before write_geometry_cache stripped them. Skipped when
	# --debug-tracks was not passed; no file produced in that case.
	if getattr(args, "debug_tracks", False):
		debug_tracks_path = tr_paths.default_debug_tracks_path(args.input_file)
		debug_cache_shape = {"solved_intervals": prior_ivs}
		state_io.write_debug_tracks(debug_tracks_path, debug_cache_shape)
		print(f"  debug tracks sidecar written to {debug_tracks_path}")
	# write diagnostics to disk
	if VIDEO_IDENTITY is not None:
		diagnostics["video_identity"] = VIDEO_IDENTITY
	state_io.write_solver_diagnostics(diagnostics, diag_path, fps)
	print(f"  diagnostics written to {diag_path}")
	# optional debug sidecar: per-frame agreement data for investigation
	if args.debug:
		debug_path = diag_path.replace(
			".diagnostics.json", ".agreement_debug.json",
		)
		n_written = state_io.write_agreement_debug_sidecar(
			diagnostics, debug_path,
		)
		if n_written > 0:
			print(
				f"  agreement-debug sidecar: {n_written} intervals "
				f"-> {debug_path}"
			)
	_print_quality_summary(diagnostics, fps)
	if rc.quit_requested:
		print(f"  quit to exit: {rc.quit_elapsed():.1f}s")
	return diagnostics


#============================================
def _mode_seed(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	seeds_path: str,
) -> None:
	"""Seed collection mode: collect seeds and save.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		seeds_path: Path to the seeds JSON file.
	"""
	# parse optional time range
	time_range = _parse_time_range(args.time_range)
	# load existing seeds
	seeds_data = state_io.load_seeds(seeds_path)
	existing_seeds = seeds_data.get("seeds", [])
	# determine pass number
	if existing_seeds:
		print(f"loaded {len(existing_seeds)} existing seeds from {seeds_path}")
		existing_passes = [s["pass"] for s in existing_seeds]
		pass_number = max(existing_passes) + 1
	else:
		pass_number = 1
	# load predictions from diagnostics or solved intervals if available
	predictions = None
	diag_path = tr_paths.default_diagnostics_path(args.input_file)
	intervals_path = tr_paths.default_intervals_path(args.input_file)
	if os.path.isfile(diag_path):
		diag_data = state_io.load_diagnostics(diag_path)
		if diag_data.get("intervals"):
			predictions = _build_predictions_from_diagnostics(diag_data)
			if predictions:
				print(f"  loaded predictions for {len(predictions)} frames")
	if not predictions and os.path.isfile(intervals_path):
		intervals_file = state_io.load_geometry_cache(intervals_path)
		solved_intervals = intervals_file.get("solved_intervals", {})
		_merge_debug_tracks(
			solved_intervals,
			tr_paths.default_debug_tracks_path(args.input_file),
		)
		if solved_intervals:
			intervals_list = list(solved_intervals.values())
			predictions = _build_predictions_from_diagnostics(
				{"intervals": intervals_list}
			)
			if predictions:
				print(f"  loaded predictions for {len(predictions)} frames "
					"(from solved intervals)")

	# convert --start time to frame index
	start_frame = None
	if getattr(args, "start_time", None) is not None:
		start_frame = int(args.start_time * video_info["fps"])

	# seed collection
	print(f"launching seed collection (pass {pass_number})...")
	seeds = seeding.collect_seeds(
		args.input_file,
		args.seed_interval,
		cfg,
		pass_number=pass_number,
		existing_seeds=existing_seeds if existing_seeds else None,
		frame_count_override=video_info["frame_count"],
		debug=args.debug,
		save_callback=_make_save_callback(seeds_path),
		time_range=time_range,
		predictions=predictions,
		start_frame=start_frame,
	)
	if not seeds:
		raise RuntimeError("no seeds collected")
	_save_seeds_to_disk(seeds, seeds_path)
	print(f"saved {len(seeds)} seeds to {seeds_path}")


#============================================
def _mode_edit(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	seeds_path: str,
	diag_path: str,
	intervals_path: str,
) -> None:
	"""Seed editor mode: review/fix/delete existing seeds interactively.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		seeds_path: Path to the seeds JSON file.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved-intervals JSON file.
	"""
	seeds = _load_and_deduplicate_seeds(seeds_path)
	if not seeds:
		raise RuntimeError(f"no seeds to edit in {seeds_path}")
	print(f"loaded {len(seeds)} seeds from {seeds_path}")

	# back up seeds file before editing
	backup_path = seeds_path + ".bak"
	shutil.copy2(seeds_path, backup_path)
	print(f"  backup saved to {backup_path}")

	# build predictions and seed confidences from diagnostics if available
	predictions = None
	seed_confidences = None
	if os.path.isfile(diag_path):
		diag_data = state_io.load_diagnostics(diag_path)
		# try diagnostics file first (may lack per-frame tracks)
		if diag_data.get("intervals"):
			predictions = _build_predictions_from_diagnostics(diag_data)
			if predictions:
				print(f"  loaded predictions for {len(predictions)} frames")
			# compute seed confidence scores from interval diagnostics
			seed_confidences = scoring.compute_seed_confidences(
				seeds, diag_data.get("intervals", []),
			)
			if seed_confidences:
				print(f"  computed confidence for {len(seed_confidences)} seeds")

	# fallback: load predictions from solved intervals (has per-frame tracks)
	if not predictions and os.path.isfile(intervals_path):
		intervals_file = state_io.load_geometry_cache(intervals_path)
		solved_intervals = intervals_file.get("solved_intervals", {})
		_merge_debug_tracks(
			solved_intervals,
			tr_paths.default_debug_tracks_path(args.input_file),
		)
		if solved_intervals:
			intervals_list = list(solved_intervals.values())
			predictions = _build_predictions_from_diagnostics(
				{"intervals": intervals_list}
			)
			if predictions:
				print(f"  loaded predictions for {len(predictions)} frames (from solved intervals)")

	# optionally filter by severity (show only seeds near weak intervals)
	frame_filter = None
	severity = getattr(args, "severity", None)
	if severity is not None and os.path.isfile(diag_path):
		fps = video_info["fps"]
		diag_data = state_io.load_diagnostics(diag_path)
		intervals = diag_data.get("intervals", [])
		# collect frame ranges from weak intervals at the severity threshold
		weak_frames = set()
		for iv in intervals:
			score = iv["interval_score"]
			confidence = review.get_confidence_label(score)
			if confidence in ("high", "good"):
				continue
			sev = review.classify_interval_severity(iv, fps)
			# include if severity meets threshold
			include = False
			if severity == "low":
				include = True
			elif severity == "medium" and sev in ("medium", "high"):
				include = True
			elif severity == "high" and sev == "high":
				include = True
			if include:
				start_f = int(iv["start_frame"])
				end_f = int(iv["end_frame"])
				# include seeds within the weak interval range
				for seed in seeds:
					fi = int(seed.get("frame_index", -1))
					if start_f <= fi <= end_f:
						weak_frames.add(fi)
		if weak_frames:
			frame_filter = weak_frames
			print(f"  severity filter: {len(weak_frames)} seeds near "
				f"{severity}+ severity intervals")
		else:
			print(f"  no seeds match severity={severity} filter, showing all")

	# convert --start time to frame index
	start_frame = None
	if getattr(args, "start_time", None) is not None:
		start_frame = int(args.start_time * video_info["fps"])

	# run the editor
	edited_seeds, summary = seed_editor.edit_seeds(
		args.input_file, seeds, cfg,
		predictions=predictions,
		frame_filter=frame_filter,
		seed_confidences=seed_confidences,
		debug=args.debug,
		start_frame=start_frame,
	)

	# save if changes were made
	changes = summary["redrawn"] + summary["deleted"] + summary["status_changed"]
	if changes > 0:
		_save_seeds_to_disk(edited_seeds, seeds_path)
		print(f"saved {len(edited_seeds)} seeds to {seeds_path}")
		# invalidate only solved intervals that touch changed seeds
		changed_frames = summary.get("changed_frames", set())
		if changed_frames and os.path.isfile(intervals_path):
			_invalidate_intervals_for_frames(intervals_path, changed_frames)
	else:
		print("no changes made")


#============================================
def _mode_target(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	seeds_path: str,
	diag_path: str,
	intervals_path: str,
) -> None:
	"""Target mode: add seeds at weak interval frames with FWD/BWD overlays.

	Loads solved intervals and diagnostics, generates refinement targets
	filtered by severity, builds FWD/BWD predictions, and launches the
	interactive seed collection UI at those frames.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		seeds_path: Path to the seeds JSON file.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved-intervals JSON file.
	"""
	# load seeds
	seeds = _load_and_deduplicate_seeds(seeds_path)
	if not seeds:
		raise RuntimeError(f"no seeds found in {seeds_path}")
	print(f"loaded {len(seeds)} seeds from {seeds_path}")
	fps = video_info["fps"]
	diag_data = _ensure_target_diagnostics(
		args, cfg, video_info, seeds, diag_path, intervals_path,
	)

	# back up seeds before modifying
	backup_path = seeds_path + ".bak"
	shutil.copy2(seeds_path, backup_path)
	print(f"  backup saved to {backup_path}")

	# generate refinement targets with optional severity filter
	severity = getattr(args, "severity", None)
	seed_interval = getattr(args, "seed_interval", 10.0)
	top_n = getattr(args, "top_n", None)

	# generate target frames
	target_frames = review.generate_refinement_targets(
		diag_data,
		mode="suggested",
		seed_interval=int(seed_interval * fps),
		severity=severity,
	)

	# apply --top slicing if requested
	if top_n is not None:
		# extract intervals and filter by severity to match generate_refinement_targets logic
		intervals = diag_data.get("intervals", [])
		filtered_intervals = []
		if severity is not None:
			# only include intervals matching severity threshold
			min_rank = review._SEVERITY_RANK.get(severity, 0)
			for iv in intervals:
				score = iv["interval_score"]
				if review.get_confidence_label(score) in ("high", "good"):
					continue
				iv_severity = review.classify_interval_severity(iv, fps)
				if review._SEVERITY_RANK.get(iv_severity, 0) >= min_rank:
					filtered_intervals.append(iv)
		else:
			# no severity filter: include all intervals
			for iv in intervals:
				score = iv["interval_score"]
				if review.get_confidence_label(score) not in ("high", "good"):
					filtered_intervals.append(iv)

		# sort worst-first and slice
		filtered_intervals.sort(key=review.rank_key)
		filtered_intervals = filtered_intervals[:top_n]

		# re-generate target frames from the top-N intervals only
		if filtered_intervals:
			# build a minimal diagnostics dict with just the top intervals
			top_diag = dict(diag_data)
			top_diag["intervals"] = filtered_intervals
			target_frames = review.generate_refinement_targets(
				top_diag,
				mode="suggested",
				seed_interval=int(seed_interval * fps),
				severity=severity,
			)
		else:
			target_frames = []

		if len(filtered_intervals) < top_n:
			print(f"  hint: only {len(filtered_intervals)} intervals matched (--top={top_n} requested)")

	if not target_frames:
		sev_label = f" at {severity}+ severity" if severity else ""
		print(f"  no weak intervals found{sev_label}")
		return

	sev_label = f" ({severity}+ severity)" if severity else ""
	top_label = f" (top {top_n})" if top_n is not None else ""
	print(f"  {len(target_frames)} target frames from weak intervals{sev_label}{top_label}")

	# build FWD/BWD predictions from diagnostics
	predictions = _build_predictions_from_diagnostics(diag_data)
	# fallback to solved intervals if diagnostics lack per-frame tracks
	if not predictions and os.path.isfile(intervals_path):
		intervals_file = state_io.load_geometry_cache(intervals_path)
		solved_intervals = intervals_file.get("solved_intervals", {})
		_merge_debug_tracks(
			solved_intervals,
			tr_paths.default_debug_tracks_path(args.input_file),
		)
		if solved_intervals:
			intervals_list = list(solved_intervals.values())
			predictions = _build_predictions_from_diagnostics(
				{"intervals": intervals_list}
			)
	if predictions:
		print(f"  loaded predictions for {len(predictions)} frames")

	# determine pass number
	existing_passes = [s["pass"] for s in seeds]
	next_pass = max(existing_passes) + 1 if existing_passes else 2

	# convert --start time to frame index
	start_frame = None
	if getattr(args, "start_time", None) is not None:
		start_frame = int(args.start_time * video_info["fps"])

	# collect seeds at target frames with predictions overlay
	print(f"  collecting seeds at {len(target_frames)} weak interval frames...")
	updated_seeds = seeding.collect_seeds_at_frames(
		args.input_file,
		target_frames,
		cfg,
		pass_number=next_pass,
		mode="target_refine",
		existing_seeds=seeds,
		predictions=predictions,
		debug=args.debug,
		save_callback=_make_save_callback(seeds_path),
		start_frame=start_frame,
	)
	# save updated seeds
	new_count = len(updated_seeds) - len(seeds)
	_save_seeds_to_disk(updated_seeds, seeds_path)
	print(f"saved {len(updated_seeds)} seeds to {seeds_path} "
		f"({new_count} new)")


#============================================
def _mode_solve(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	seeds_path: str,
	diag_path: str,
	intervals_path: str,
) -> None:
	"""Solve mode: run interval solver, write diagnostics, exit.

	Non-interactive: solves, writes diagnostics, prints quality summary.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		seeds_path: Path to the seeds JSON file.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved-intervals JSON file.
	"""
	seeds = _load_and_deduplicate_seeds(seeds_path)
	if not seeds:
		raise RuntimeError(f"no seeds found in {seeds_path}")
	print(f"loaded {len(seeds)} seeds from {seeds_path}")

	# only clear intervals if a prior solve completed successfully;
	# if the prior solve was interrupted, resume from saved intervals
	if os.path.isfile(intervals_path):
		intervals_file = state_io.load_geometry_cache(intervals_path)
		prior_complete = intervals_file.get("solve_complete", False)
		prior_count = len(intervals_file.get("solved_intervals", {}))
		if prior_complete and prior_count > 0:
			print(f"  prior solve completed ({prior_count} intervals)")
			answer = input("  clear and re-solve from scratch? [y/N] ").strip().lower()
			if answer in ("y", "yes"):
				os.remove(intervals_path)
				print("  cleared solved intervals (full re-solve)")
			else:
				print("  keeping prior results (use 'refine' for incremental updates)")
				return
		elif prior_count > 0:
			print(f"  resuming interrupted solve ({prior_count} intervals saved)")
		else:
			os.remove(intervals_path)

	num_workers = _resolve_workers(args)
	_run_solve(
		args, cfg, seeds, video_info,
		intervals_path, diag_path, num_workers,
	)


#============================================
def _mode_refine(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	seeds_path: str,
	diag_path: str,
	intervals_path: str,
) -> None:
	"""Refine mode: re-solve only changed intervals, reuse prior results.

	Requires existing solved intervals from a prior solve. Only
	intervals whose fingerprint changed (due to edited seeds) are
	re-solved; prior results are reused for unchanged intervals.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		seeds_path: Path to the seeds JSON file.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved-intervals JSON file.
	"""
	seeds = _load_and_deduplicate_seeds(seeds_path)
	if not seeds:
		raise RuntimeError(f"no seeds found in {seeds_path}")
	print(f"loaded {len(seeds)} seeds from {seeds_path}")
	if not os.path.isfile(intervals_path):
		raise RuntimeError(
			f"no solved intervals at {intervals_path}; run 'solve' first"
		)

	# refine-mode contract (see plan):
	#  1. unchanged seeds -> no computation, no write.
	#  2. add one seed -> solve exactly 2 intervals.
	#  3. remove one seed -> solve exactly 1 interval.
	# cache validity is set membership of fingerprints, not cardinality,
	# and not the legacy global solve_complete flag. the queue module's
	# plan_interval_work is the single source of truth for the seed
	# filter, fingerprint walk, and orphan prune -- solve mode and
	# refine mode both call it so cache keys match byte-for-byte.
	intervals_file = state_io.load_geometry_cache(intervals_path)
	solved_intervals = intervals_file.get("solved_intervals", {})
	plan = solve_queue.plan_interval_work(seeds, solved_intervals)
	total_expected = plan.total_intervals

	# fast-exit (no write). enforces contract rule 1: unchanged seeds
	# produce no computation AND no disk write. pending_count == 0 is
	# the membership-complete signal; solve_complete on disk is advisory
	# only.
	if plan.pending_count == 0:
		# solve_complete is advisory, not a correctness gate. if
		# membership is complete but the legacy completion flag is
		# false, surface the discrepancy but trust the cache.
		if intervals_file.get("solve_complete") is False:
			print("  warning: solve_complete=false on disk, "
				"cache membership complete; trusting cache")
		print(f"  refine: all {total_expected} intervals already solved, "
			f"nothing to do")
		return

	# fall-through path: work is needed, so writes become allowed. the
	# plan's pruned_prior already has orphan fingerprints filtered out,
	# so persisting it is the prune operation. only write when the
	# prune actually changed the dict, per the zero-write contract rule.
	before = len(solved_intervals)
	pruned_count = before - len(plan.pruned_prior)
	if pruned_count > 0:
		intervals_file["solved_intervals"] = dict(plan.pruned_prior)
		state_io.write_geometry_cache(intervals_path, intervals_file)
		print(f"  cache: pruned {pruned_count} stale intervals "
			f"({before} -> {len(plan.pruned_prior)})")

	print(f"  refine: {plan.pending_count} of {total_expected} intervals "
		f"need solving ({plan.reused_count} will be reused)")

	num_workers = _resolve_workers(args)
	_run_solve(
		args, cfg, seeds, video_info,
		intervals_path, diag_path, num_workers,
	)


#============================================
def _parse_resolution(value: str) -> list:
	"""Parse a 'WxH' string into a [width, height] integer list.

	Args:
		value: Resolution string like '1920x1080'.

	Returns:
		[width, height] with both values as ints.

	Raises:
		RuntimeError: If the string is not in WxH form or either dim
			is not a positive integer.
	"""
	# split on the literal 'x' separator; anything else is an error
	parts = value.lower().split("x")
	if len(parts) != 2 or not parts[0] or not parts[1]:
		raise RuntimeError(
			f"invalid --output-resolution '{value}', expected WxH "
			"(e.g. '1920x1080')"
		)
	width = int(parts[0])
	height = int(parts[1])
	if width <= 0 or height <= 0:
		raise RuntimeError(
			f"invalid --output-resolution '{value}', width and height "
			"must be positive"
		)
	return [width, height]


#============================================
def _apply_encode_overrides(args: argparse.Namespace, cfg: dict) -> None:
	"""Merge CLI encode-override flags into cfg['processing'] in place.

	Handles --aspect plus the phase-4 flags (--torso-multiple,
	-r/--output-resolution, --crf, --video-codec). Each flag is applied
	only when the user set it; unset flags leave the config untouched.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict, mutated in place.
	"""
	cfg.setdefault("processing", {})
	processing = cfg["processing"]
	# aspect is the legacy override flag; still honored here
	if getattr(args, "aspect", None) is not None:
		processing["crop_aspect"] = args.aspect
	# torso_height_multiple: crop zoom knob
	multiple = getattr(args, "torso_multiple", None)
	if multiple is not None:
		processing["torso_height_multiple"] = float(multiple)
	# explicit output resolution, parsed as WxH
	resolution_str = getattr(args, "output_resolution", None)
	if resolution_str is not None:
		processing["output_resolution"] = _parse_resolution(resolution_str)
	# CRF quality
	crf_override = getattr(args, "crf", None)
	if crf_override is not None:
		processing["crf"] = int(crf_override)
	# ffmpeg video codec
	codec_override = getattr(args, "video_codec", None)
	if codec_override is not None:
		processing["video_codec"] = codec_override


#============================================
def _resolve_encode_filters(args: argparse.Namespace, proc_cfg: dict) -> list:
	"""Resolve the encode filter list from CLI and tr_config.

	CLI --encode-filters overrides config processing.encode_filters.
	Validates each filter name against the known filter list.

	Args:
		args: Parsed argparse namespace.
		proc_cfg: The processing section of the config dict.

	Returns:
		List of validated filter name strings, or empty list.
	"""
	# CLI override takes priority
	cli_value = getattr(args, "encode_filters", None)
	if cli_value is not None:
		# split comma-separated string into list
		filter_list = [f.strip() for f in cli_value.split(",") if f.strip()]
	else:
		# fall back to config value
		filter_list = list(proc_cfg.get("encode_filters", []))
	# validate each filter name
	for name in filter_list:
		if name not in frame_filters.ALL_ENCODE_FILTERS:
			raise RuntimeError(
				f"unknown encode filter: '{name}'. "
				f"Valid filters: {frame_filters.ALL_ENCODE_FILTERS}"
			)
	return filter_list


#============================================
def _mode_analyze(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	diag_path: str,
	intervals_path: str | None = None,
) -> None:
	"""Analyze mode: compute crop-path stability metrics before encoding.

	Reconstructs the trajectory from solved intervals (same pipeline as
	encode), computes crop rects, then runs crop-path stability analysis
	and solver context analysis. Prints a formatted console report and
	writes a diagnostic YAML file.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved-intervals JSON file.
			If None, derived from input_file.
	"""
	# apply aspect override
	if getattr(args, "aspect", None) is not None:
		cfg.setdefault("processing", {})
		cfg["processing"]["crop_aspect"] = args.aspect

	# load diagnostics (for fps and interval metadata)
	if not os.path.isfile(diag_path):
		raise RuntimeError(
			f"no diagnostics found at {diag_path}; run 'solve' first"
		)
	diag_data = state_io.load_diagnostics(diag_path)

	# reconstruct trajectory from solved intervals
	if intervals_path is None:
		intervals_path = tr_paths.default_intervals_path(args.input_file)
	if not os.path.isfile(intervals_path):
		raise RuntimeError(
			f"no solved intervals found at {intervals_path}; "
			f"run 'solve' first"
		)
	intervals_file = state_io.load_geometry_cache(intervals_path)
	solved = intervals_file.get("solved_intervals", {})
	if not solved:
		raise RuntimeError(
			"solved intervals file contains no interval data"
		)
	# sort interval results by start_frame for stitching
	interval_results = sorted(
		solved.values(), key=lambda r: int(r["start_frame"]),
	)
	trajectory = interval_solver.stitch_trajectories(interval_results)

	# apply multi-seed anchored interpolation to reduce drift
	seeds_path = tr_paths.default_seeds_path(args.input_file)
	all_seeds = []
	if os.path.isfile(seeds_path):
		seeds_data = state_io.load_seeds(seeds_path)
		all_seeds = seeds_data.get("seeds", [])
		trajectory = interval_solver.anchor_to_seeds(trajectory, all_seeds)
		# apply trajectory erasure from seeds
		fps = float(diag_data.get("fps", video_info["fps"]))
		trajectory = interval_solver._apply_trajectory_erasure(
			trajectory, all_seeds, fps,
		)

	if not trajectory:
		raise RuntimeError(
			"could not reconstruct trajectory from solved intervals"
		)

	# compute crop trajectory
	print("computing crop trajectory...")
	crop_rects = tr_crop.trajectory_to_crop_rects(trajectory, video_info, cfg)

	# compute output dimensions (same logic as encode)
	proc_cfg = cfg.get("processing", {})
	user_resolution = proc_cfg.get("output_resolution")
	if user_resolution is not None:
		crop_w = int(user_resolution[0])
		crop_h = int(user_resolution[1])
	elif crop_rects:
		all_widths = [r[2] for r in crop_rects]
		all_heights = [r[3] for r in crop_rects]
		crop_w = int(statistics.median(all_widths))
		crop_h = int(statistics.median(all_heights))
	else:
		crop_h = video_info["height"] // 2
		crop_w = crop_h
	# ensure even dimensions
	crop_w = crop_w - (crop_w % 2)
	crop_h = crop_h - (crop_h % 2)

	fps = float(diag_data.get("fps", video_info["fps"]))

	# run crop-path stability analysis
	print("analyzing crop path stability...")
	analysis = encode_analysis.analyze_crop_stability(
		crop_rects, trajectory, crop_w, crop_h, fps,
	)

	# run solver context analysis
	solver_context = encode_analysis.analyze_solver_context(
		interval_results, all_seeds, fps,
	)

	# run regime classification for smart mode diagnostics
	regime_spans = regime_classifier.classify_regimes(
		trajectory, video_info,
	)
	regime_summary_line = regime_classifier.format_regime_summary(
		regime_spans, video_info["frame_count"],
	)

	# write YAML report
	analysis_path = tr_paths.default_encode_analysis_path(args.input_file)
	encode_analysis.write_analysis_yaml(
		analysis, solver_context, analysis_path,
		regime_spans=regime_spans,
	)

	# print console report
	report = encode_analysis.format_analysis_report(
		analysis, solver_context, analysis_path,
		regime_summary_line=regime_summary_line,
	)
	print(report)


#============================================
def _mode_encode(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	diag_path: str,
	intervals_path: str | None = None,
) -> None:
	"""Encode mode: encode cropped video from existing diagnostics.

	Reconstructs the per-frame trajectory from the solved intervals
	file, since the diagnostics file stores only interval summaries
	(no per-frame trajectory data).

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved-intervals JSON file.
			If None, derived from input_file.
	"""
	# apply CLI overrides (aspect + phase-4 encode-only flags)
	_apply_encode_overrides(args, cfg)

	# load diagnostics (for fps and interval metadata)
	if not os.path.isfile(diag_path):
		raise RuntimeError(
			f"no diagnostics found at {diag_path}; run 'solve' first"
		)
	diag_data = state_io.load_diagnostics(diag_path)

	# reconstruct trajectory from solved intervals
	if intervals_path is None:
		intervals_path = tr_paths.default_intervals_path(args.input_file)
	if not os.path.isfile(intervals_path):
		raise RuntimeError(
			f"no solved intervals found at {intervals_path}; "
			f"run 'solve' first"
		)
	intervals_file = state_io.load_geometry_cache(intervals_path)
	solved = intervals_file.get("solved_intervals", {})
	if not solved:
		raise RuntimeError(
			"solved intervals file contains no interval data"
		)
	# merge optional debug sidecar (fwd/bwd tracks) when debug is on
	if args.debug:
		_merge_debug_tracks(
			solved, tr_paths.default_debug_tracks_path(args.input_file),
		)
	# sort interval results by start_frame for stitching
	interval_results = sorted(
		solved.values(), key=lambda r: int(r["start_frame"]),
	)
	trajectory = interval_solver.stitch_trajectories(interval_results)

	# save raw trajectory and FWD/BWD tracks before anchoring for debug overlay
	raw_trajectory_for_debug = None
	fwd_trajectory_for_debug = None
	bwd_trajectory_for_debug = None
	if args.debug:
		raw_trajectory_for_debug = [
			dict(s) if s is not None else None
			for s in trajectory
		]
		# stitch forward and backward tracks for FWD/BWD overlay boxes
		n_frames = len(trajectory)
		fwd_trajectory_for_debug = [None] * n_frames
		bwd_trajectory_for_debug = [None] * n_frames
		for result in interval_results:
			start = int(result["start_frame"])
			fwd_track = result.get("forward_track", [])
			bwd_track = result.get("backward_track", [])
			for i, fwd_state in enumerate(fwd_track):
				fi = start + i
				if 0 <= fi < n_frames and fwd_state is not None:
					fwd_trajectory_for_debug[fi] = fwd_state
			for i, bwd_state in enumerate(bwd_track):
				fi = start + i
				if 0 <= fi < n_frames and bwd_state is not None:
					bwd_trajectory_for_debug[fi] = bwd_state

	# apply multi-seed anchored interpolation to reduce drift
	seeds_path = tr_paths.default_seeds_path(args.input_file)
	if os.path.isfile(seeds_path):
		seeds_data = state_io.load_seeds(seeds_path)
		all_seeds = seeds_data.get("seeds", [])
		trajectory = interval_solver.anchor_to_seeds(trajectory, all_seeds)
		# apply trajectory erasure from seeds (function decides which seeds to erase)
		fps = float(diag_data.get("fps", video_info["fps"]))
		trajectory = interval_solver._apply_trajectory_erasure(
			trajectory, all_seeds, fps,
		)

	if not trajectory:
		raise RuntimeError(
			"could not reconstruct trajectory from solved intervals"
		)

	num_workers = _resolve_workers(args)

	# compute crop trajectory
	print("computing crop trajectory...")
	crop_rects = tr_crop.trajectory_to_crop_rects(trajectory, video_info, cfg)

	# resolve output path (encoded output stays next to input video)
	output_file = getattr(args, "output_file", None)
	if output_file is not None:
		output_path = output_file
	else:
		output_path = tr_paths.default_output_path(args.input_file)

	# compute output dimensions: explicit config > median of crop rects > fallback
	proc_cfg = cfg.get("processing", {})
	user_resolution = proc_cfg.get("output_resolution")
	if user_resolution is not None:
		# user-specified output resolution
		crop_w = int(user_resolution[0])
		crop_h = int(user_resolution[1])
	elif crop_rects:
		# derive from median of all crop rectangles for stability
		all_widths = [r[2] for r in crop_rects]
		all_heights = [r[3] for r in crop_rects]
		crop_w = int(statistics.median(all_widths))
		crop_h = int(statistics.median(all_heights))
	else:
		crop_h = video_info["height"] // 2
		crop_w = crop_h
	# ensure even dimensions for codec compatibility
	crop_w = crop_w - (crop_w % 2)
	crop_h = crop_h - (crop_h % 2)
	video_codec = proc_cfg.get("video_codec", "libx264")
	crf_value = int(proc_cfg.get("crf", 18))

	# resolve encode filters: CLI overrides config, config overrides default
	encode_filters = _resolve_encode_filters(args, proc_cfg)

	# encode
	temp_video = output_path + ".tmp.mp4"
	workers_enc_label = f" ({num_workers} workers)" if num_workers > 1 else ""
	filters_label = f" filters={encode_filters}" if encode_filters else ""
	print(f"encoding cropped video: {crop_w}x{crop_h}{workers_enc_label}{filters_label}")
	print("  (press Q to quit)")
	t_encode_start = time.time()

	# set up keyboard controls for encoding
	enc_rc = key_input.RunControl()
	key_input.install_sigint_handler(enc_rc)
	# enable quit-chain tracing when debug flag is set
	if args.debug:
		key_input.QUIT_TRACE = True

	# build frame_states for debug overlay
	frame_states_for_debug = None
	if args.debug:
		frame_states_for_debug = []
		for i, state in enumerate(trajectory):
			if state is not None:
				debug_state = {
					"cx": state["cx"],
					"cy": state["cy"],
					"w": state["w"],
					"h": state["h"],
					"conf": state["conf"],
					"source": state.get("source", "propagated"),
					"seed_status": state.get("seed_status", ""),
					"frame_index": i,
					"bbox": (state["cx"], state["cy"], state["w"], state["h"]),
				}
				# attach raw (pre-anchor) position for drift comparison overlay
				if raw_trajectory_for_debug is not None and i < len(raw_trajectory_for_debug):
					raw = raw_trajectory_for_debug[i]
					if raw is not None:
						debug_state["raw_box"] = [raw["cx"], raw["cy"], raw["w"], raw["h"]]
				# attach FWD/BWD projection boxes for debug overlay
				if fwd_trajectory_for_debug is not None and i < len(fwd_trajectory_for_debug):
					fwd = fwd_trajectory_for_debug[i]
					if fwd is not None:
						debug_state["forward_box"] = [fwd["cx"], fwd["cy"], fwd["w"], fwd["h"]]
				if bwd_trajectory_for_debug is not None and i < len(bwd_trajectory_for_debug):
					bwd = bwd_trajectory_for_debug[i]
					if bwd is not None:
						debug_state["backward_box"] = [bwd["cx"], bwd["cy"], bwd["w"], bwd["h"]]
			else:
				debug_state = None
			frame_states_for_debug.append(debug_state)

	with key_input.KeyInputReader() as enc_kreader:
		if num_workers > 1:
			encoder.encode_cropped_video_parallel(
				args.input_file, crop_rects, temp_video,
				crop_w, crop_h,
				codec=video_codec, crf=crf_value,
				frame_states=frame_states_for_debug,
				debug=args.debug,
				workers=num_workers,
				encode_filters=encode_filters,
				run_control=enc_rc,
				key_reader_obj=enc_kreader,
			)
		else:
			with video_io.VideoReader(args.input_file) as reader:
				encoder.encode_cropped_video(
					reader, crop_rects, temp_video,
					crop_w, crop_h,
					codec=video_codec, crf=crf_value,
					frame_states=frame_states_for_debug,
					debug=args.debug,
					encode_filters=encode_filters,
					run_control=enc_rc,
					key_reader_obj=enc_kreader,
				)
	# restore default signal handler
	key_input.restore_default_sigint()
	t_encode_elapsed = time.time() - t_encode_start
	if enc_rc.quit_requested:
		print(f"  encode interrupted ({t_encode_elapsed:.1f}s)")
		print("  skipping mux and finalize (quit requested)")
		return
	print(f"  encode complete ({t_encode_elapsed:.1f}s)")

	# mux audio
	print("muxing audio...")
	t_mux_start = time.time()
	encoder.copy_audio(args.input_file, temp_video, output_path)
	t_mux_elapsed = time.time() - t_mux_start
	print(f"  mux complete ({t_mux_elapsed:.1f}s)")

	# clean up temp file
	keep_temp = getattr(args, "keep_temp", False)
	if not keep_temp and os.path.isfile(temp_video) and os.path.isfile(output_path):
		os.remove(temp_video)
	print(f"\noutput: {output_path}")


#============================================
def _mode_setup(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	config_path: str,
) -> None:
	"""Setup mode: interactive questionnaire for camera configuration.

	Launches an interactive CLI questionnaire to collect per-video camera
	settings (zoom type, camera height, position, track size) and stores
	them in the configuration file.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict (may be modified).
		video_info: Video metadata dict.
		config_path: Path to the configuration file.
	"""
	setup_mode.run_setup(config_path, cfg)


#============================================
def main() -> None:
	"""Main entry point for the track_runner v2 CLI."""
	t_total_start = time.time()
	args = cli_args.parse_args()

	# validate input file exists
	if not os.path.isfile(args.input_file):
		raise RuntimeError(f"input file not found: {args.input_file}")

	# verify required external tools are available
	for tool in ("mediainfo", "ffprobe", "ffmpeg"):
		if shutil.which(tool) is None:
			raise RuntimeError(f"{tool} not found in PATH")

	# ensure tr_config/ data directory exists
	tr_paths.ensure_data_dir()

	# resolve config path
	config_path = args.config_file
	if config_path is None:
		config_path = tr_paths.default_config_path(args.input_file)

	# paths for seeds, diagnostics, and solved intervals
	seeds_path = tr_paths.default_seeds_path(args.input_file)
	diag_path = tr_paths.default_diagnostics_path(args.input_file)
	intervals_path = tr_paths.default_intervals_path(args.input_file)

	# print all config and data file paths
	print(f"config:      {os.path.abspath(config_path)}")
	print(f"seeds:       {os.path.abspath(seeds_path)}")
	print(f"diagnostics: {os.path.abspath(diag_path)}")
	print(f"intervals:   {os.path.abspath(intervals_path)}")
	# show analysis file path when it exists (diagnostic awareness)
	analysis_path = tr_paths.default_encode_analysis_path(args.input_file)
	if os.path.isfile(analysis_path):
		print(f"analysis:    {os.path.abspath(analysis_path)}")

	# load config: per-video file if it exists, otherwise defaults.
	# track whether a per-video config already existed so we can gate
	# modes that need `setup` to have been run first (solve/refine/target).
	had_config_file = os.path.isfile(config_path)
	if had_config_file:
		# per-video config: auto-save legacy-key migrations so the
		# deprecation notice fires exactly once per file
		cfg = tr_config.load_config(config_path, auto_save_migration=True)
	else:
		cfg = tr_config.read_default_config()
	tr_config.validate_config(cfg)

	# probe video metadata
	print(f"probing video: {args.input_file}")
	video_info = _probe_video(args.input_file)
	fps = video_info["fps"]
	print(f"  resolution: {video_info['width']}x{video_info['height']}")
	print(f"  fps:        {fps:.4f}")
	print(f"  frames:     {video_info['frame_count']}")
	print(f"  duration:   {video_info['duration_s']:.2f}s")

	# build video identity fingerprint for data file tagging
	global VIDEO_IDENTITY
	VIDEO_IDENTITY = tr_video_identity.make_video_identity(
		args.input_file, video_info,
	)

	# check existing data files for video identity mismatches
	_check_identity_mismatch("seeds", seeds_path)
	_check_identity_mismatch("diagnostics", diag_path)
	_check_identity_mismatch("intervals", intervals_path)

	# dispatch to mode function
	mode = args.mode

	# gate modes that consume setup-only config (zoom_type, camera.*,
	# track_size): require a per-video config file to exist. `seed`,
	# `edit`, `encode`, `analyze`, and `setup` itself are intentionally
	# exempt so users can collect seeds before configuring the camera.
	if mode in ("solve", "refine", "target") and not had_config_file:
		raise RuntimeError(
			f"no per-video config at {config_path} -- "
			f"run 'setup' mode first: "
			f"./track_runner/track_runner.py -i {args.input_file} setup"
		)

	# gate `refine` on `solve` having been run at least once: refine
	# reads per-interval diagnostics to decide which intervals to redo.
	# `target` is not gated on solve because target-mode auto-runs a
	# fresh solve when diagnostics are missing (see cli.py:_mode_target).
	if mode == "refine" and not os.path.isfile(diag_path):
		raise RuntimeError(
			f"no diagnostics at {diag_path} -- "
			f"run 'solve' mode first: "
			f"./track_runner/track_runner.py -i {args.input_file} solve"
		)

	if mode == "seed":
		_mode_seed(args, cfg, video_info, seeds_path)
	elif mode == "edit":
		_mode_edit(args, cfg, video_info, seeds_path, diag_path, intervals_path)
	elif mode == "target":
		_mode_target(args, cfg, video_info, seeds_path, diag_path, intervals_path)
	elif mode == "solve":
		_mode_solve(args, cfg, video_info, seeds_path, diag_path, intervals_path)
	elif mode == "refine":
		_mode_refine(args, cfg, video_info, seeds_path, diag_path, intervals_path)
	elif mode == "setup":
		_mode_setup(args, cfg, video_info, config_path)
	elif mode == "encode":
		_mode_encode(args, cfg, video_info, diag_path, intervals_path)
	elif mode == "analyze":
		_mode_analyze(args, cfg, video_info, diag_path, intervals_path)
	else:
		raise RuntimeError(f"unknown mode: {mode}")

	# print total elapsed time
	t_total_elapsed = time.time() - t_total_start
	print(f"total time: {t_total_elapsed:.1f}s")


#============================================
if __name__ == "__main__":
	main()
