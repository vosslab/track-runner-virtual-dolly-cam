#!/usr/bin/env python3
"""CLI entry point for the track_runner tool v2.

Multi-pass orchestration: seed collection, interval solving, refinement,
crop trajectory computation, and video encoding.

Subcommands:
  prepare Create the fast-read working video beside the original
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
import math
import os
import sys
import shutil
import pathlib
import resource
import statistics
import subprocess
import time

# local repo modules
import cli_args
import tr_config
import state_io
import tr_paths
import tr_video_identity
import common_tools.frame_reader
import common_tools.probe_video
import seeding
import scoring
import seed_editor
import setup_mode
import interval_solver
import interval_fingerprint
import solve_queue
import review
import tr_crop
import key_input
import encode_analysis
import analyze_report
import regime_classifier
import camera_motion
import off_frame_geometry
import race_start
import scene_coords
import tr_schema
import fastread_video

# module-level video identity, set once in main() and treated as read-only
VIDEO_IDENTITY = None

# Per-process memory profile toggle. Flip to True locally when hunting a
# leak; main() prints peak RSS and open-FD count at end of run. Off in
# committed code per PYTHON_STYLE: configuration belongs in source, not
# in custom environment variables.
PROFILE_MEM = False


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
	warning messages but do not raise errors (policy: warn only, never
	reject on identity mismatch).

	Args:
		label: Human-readable name for the data file (e.g. "seeds").
		path: Path to the data file (`.json` or `.npz`).
	"""
	if VIDEO_IDENTITY is None:
		return
	if not os.path.isfile(path):
		return
	# NPZ files (torso_box_coords.npz) carry video_identity as
	# JSON-encoded bytes; JSON files carry it as a top-level key.
	if path.endswith(".npz"):
		# peek the schema first: an older SCHEMA_VERSION will be
		# overwritten when solve runs, so identity-mismatch checking is
		# moot (and load_torso_box_coords would raise). Policy of this
		# function is warn only, never reject (see docstring).
		schema = state_io.peek_torso_box_coords_schema(path)
		if schema is None or not tr_schema.is_supported_artifact_schema(
			"torso_box_coords", schema,
		):
			print(f"  warning: {label} file schema v{schema} stale; will be overwritten on solve")
			return
		coords_data = state_io.load_torso_box_coords(path)
		stored = coords_data.get("video_identity")
	else:
		with open(path, "r") as fh:
			data = json.load(fh)
		stored = data.get("video_identity")
	if stored is None:
		return
	result = tr_video_identity.compare_video_identity(stored, VIDEO_IDENTITY)
	if result["blocking"] or result["informational"]:
		print(f"  warning: {label} file video identity mismatch:")
		summary = tr_video_identity.summarize_mismatches(result)
		for line in summary.split("\n"):
			print(f"    {line}")


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
def _validate_diagnostics_confidence(diagnostics: dict, diag_path: str) -> None:
	"""Check that all intervals have confidence data.

	Raises RuntimeError if any interval is missing the confidence
	field, indicating stale diagnostics that need a fresh solve.

	Args:
		diagnostics: Dict with "intervals" key.
		diag_path: Path to the diagnostics file (for error messages).
	"""
	for iv in diagnostics.get("intervals", []):
		if "interval_score" not in iv:
			start, end = iv.get("start_frame"), iv.get("end_frame")
			raise RuntimeError(
				f"stale diagnostics for interval ({start}, {end}); "
				f"delete {diag_path} and re-solve"
			)
		score = iv["interval_score"]
		# accept legacy "confidence" or analytical "confidence_tier"
		if "confidence" not in score and "confidence_tier" not in score:
			raise RuntimeError(
				"diagnostics missing confidence data. "
				"Run 'solve' to regenerate."
			)


#============================================
def _partition_intervals_by_validity(intervals_list: list) -> tuple:
	"""Partition intervals into valid and stale based on score structure.

	Valid intervals have nested interval_score with confidence_tier.
	Stale intervals are missing interval_score or confidence_tier.

	Args:
		intervals_list: List of interval dicts.

	Returns:
		(valid_intervals, stale_count) where valid_intervals is a filtered list
		and stale_count is the number of intervals excluded.
	"""
	valid_intervals = []
	stale_count = 0
	for iv in intervals_list:
		if "interval_score" not in iv:
			stale_count += 1
			continue
		score = iv["interval_score"]
		if "confidence_tier" not in score and "confidence" not in score:
			stale_count += 1
			continue
		valid_intervals.append(iv)
	return (valid_intervals, stale_count)


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

	In target mode, stale intervals are skipped with a warning rather than
	requiring a full re-solve. In other modes (e.g. refine), stale entries
	trigger a re-solve via the fingerprint-miss path.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		seeds: Loaded seed list.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved-intervals JSON file.

	Returns:
		Diagnostics dict with interval confidence data, filtered for
		target mode to exclude stale intervals.
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
			# Partition intervals into valid and stale
			intervals_list = diag_data.get("intervals", [])
			valid_intervals, stale_count = _partition_intervals_by_validity(
				intervals_list
			)

			# In target mode, skip stale intervals with a warning
			if stale_count > 0:
				print(
					f"  warning: {stale_count} intervals had stale scores "
					f"and were skipped; run 'refine' or re-solve to refresh"
				)
				diag_data["intervals"] = valid_intervals

			# Validate the remaining intervals strictly
			try:
				_validate_diagnostics_confidence(diag_data, diag_path)
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

	# count by confidence tier (pre_race as separate class)
	from collections import Counter
	tier_counts = Counter(
		review.get_confidence_label(iv["interval_score"])
		for iv in intervals
	)
	need_seed = tier_counts.get("low", 0) + tier_counts.get("fair", 0)
	pre_race_count = tier_counts.get("pre_race", 0)
	analytic_count = total - pre_race_count

	print("")
	print(f"quality summary: "
		f"{tier_counts.get('high', 0)} high, "
		f"{tier_counts.get('good', 0)} good, "
		f"{tier_counts.get('fair', 0)} fair, "
		f"{tier_counts.get('low', 0)} low "
		f"({analytic_count} tracked"
		f"{f', {pre_race_count} pre-race' if pre_race_count > 0 else ''}"
		f")")
	if review.needs_refinement(diagnostics):
		print(f"  {need_seed} intervals need seeds (fair + low)")
		# compute severity breakdown for seed-needing intervals
		high_count = 0
		medium_count = 0
		low_count = 0
		for iv in intervals:
			score = iv["interval_score"]
			# Pre-race intervals are scene-anchored (C4) and not
			# severity-classified; classify_interval_severity returns
			# None for them. Skip alongside high/good so the breakdown
			# matches the "weak intervals" target predicate.
			if review.get_confidence_label(score) in ("high", "good", "pre_race"):
				continue
			sev = review.classify_interval_severity(iv, fps)
			if sev == "high":
				high_count += 1
			elif sev == "medium":
				medium_count += 1
			elif sev == "low":
				low_count += 1
		print(f"  severity breakdown: {high_count} high, "
			f"{medium_count} medium, {low_count} low")
		print(f"  hint: use --severity=high to focus on the "
			f"{high_count} worst intervals")
	else:
		print("  all intervals acceptable -- no seeds needed")
	print("")


#============================================
def _build_predictions_from_solved_intervals(solved_data: dict) -> dict:
	"""Build frame-indexed prediction dict with overlays and interval metadata.

	Each frame entry includes FWD/BWD/blended/consensus boxes plus interval-level
	quality metadata (severity, confidence, scores, failure reasons) so the GUI
	can display why an interval was flagged.

	Args:
		solved_data: Dict with "intervals" and "fps" keys, shaped like
			solve_all_intervals() output with per-interval geometry (forward_path,
			backward_path, blended_path) and scoring (interval_score) merged in.

	Returns:
		Dict mapping frame_index (int) to prediction dict with box data and
		an "interval_info" sub-dict for quality display.
	"""
	fps = float(solved_data.get("fps", 30.0))
	predictions = {}
	for interval_idx, iv in enumerate(solved_data.get("intervals", [])):
		fwd_track = iv.get("forward_path")
		bwd_track = iv.get("backward_path")
		blended_path = iv.get("blended_path")

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

		# case 1: FWD and BWD both present -> use them
		if fwd_track is not None and bwd_track is not None:
			n = min(len(fwd_track), len(bwd_track))
			for i in range(n):
				frame_index = start_frame + i
				frame_preds = {
					"forward": fwd_track[i],
					"backward": bwd_track[i],
					"interval_info": interval_info,
				}
				# add blended interval path (refined second-pass) if available
				if blended_path is not None and i < len(blended_path):
					frame_preds["blended"] = blended_path[i]
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
		# case 2: FWD/BWD missing but blended present -> render blended only
		elif blended_path is not None:
			for i in range(len(blended_path)):
				frame_index = start_frame + i
				frame_preds = {
					"blended": blended_path[i],
					"interval_info": interval_info,
				}
				predictions[frame_index] = frame_preds
		# case 3: all paths missing -> skip silently; caller falls back
		# to the torso_box_coords artifact which has populated paths

	return predictions


#============================================
def _predictions_from_torso_box_coords(
	torso_box_coords_path: str,
	diag_path: str,
	fps: float,
) -> dict:
	"""Build predictions from unified torso_box_coords with scores merged in.

	Loads the unified torso_box_coords.npz artifact which contains all three
	interval paths (forward, backward, blended) pre-merged. Merges the
	interval_score sub-dict from interval_scores.json by (start_frame, end_frame),
	and hands the result to _build_predictions_from_solved_intervals so
	review.classify_interval_severity can read interval_score without a KeyError.

	Args:
		torso_box_coords_path: Path to the torso_box_coords.npz file.
		diag_path: Path to the interval_scores.json file (may be absent).
		fps: Video frame rate.

	Returns:
		Frame-indexed prediction dict, or empty dict when no intervals.
	"""
	intervals_file = state_io.load_torso_box_coords(torso_box_coords_path)
	solved_intervals = intervals_file.get("solved_intervals", {})
	if not solved_intervals:
		return {}
	intervals_list = list(solved_intervals.values())
	# build (start_frame, end_frame) -> interval_score map from scores file
	scored_by_key = {}
	if os.path.isfile(diag_path):
		score_data = state_io.load_diagnostics(diag_path)
		for scored_iv in score_data.get("intervals", []):
			key = (int(scored_iv["start_frame"]),
				int(scored_iv["end_frame"]))
			scored_by_key[key] = scored_iv["interval_score"]
	# Merge interval_score into every geometry interval. Per C10, torso
	# boxes are the source of truth; scores are advisory. If an interval
	# has geometry but no score (e.g. solve was interrupted before
	# diagnostics were rewritten), render with an empty score rather
	# than crash.
	missing_score_count = 0
	for iv in intervals_list:
		key = (int(iv["start_frame"]), int(iv["end_frame"]))
		if key not in scored_by_key:
			missing_score_count += 1
			iv["interval_score"] = {}
		else:
			iv["interval_score"] = scored_by_key[key]
	if missing_score_count > 0:
		print(
			f"  warning: {missing_score_count} intervals have torso boxes "
			f"but no score (likely interrupted solve); rendering anyway"
		)
	return _build_predictions_from_solved_intervals(
		{"intervals": intervals_list, "fps": float(fps)}
	)


#============================================
def _load_prior_results(intervals_path: str, diag_path: str) -> tuple:
	"""Load previously solved intervals and build a write-through callback.

	Geometry (forward_path, backward_path, blended_path) comes from
	`torso_box_coords.npz`. Scoring (interval_score) comes from
	`interval_scores.json`. This helper merges the two so every prior
	interval returned carries both its trajectory AND its score, matching
	the shape `write_solver_diagnostics` expects when it later rewrites
	the scoring file.

	Scores are matched to intervals by (start_frame, end_frame) because
	that pair is what `interval_scores.json` stores. A prior interval
	without a matching score entry keeps an empty `interval_score` dict
	so downstream code still sees a real mapping; the next pass can
	re-score it if needed.

	Args:
		intervals_path: Path to the torso_box_coords NPZ (unified artifact).
		diag_path: Path to the interval_scores JSON.

	Returns:
		Tuple (prior_results_dict, on_interval_solved_callback).
	"""
	intervals_file = state_io.load_torso_box_coords(intervals_path)
	solved = intervals_file.get("solved_intervals", {})
	# One-time migration of geometry-compatible legacy fingerprint keys. This
	# rewrites on-disk fingerprints that predate the geometry/schema tag
	# split (e.g. tails carrying `/schema/5` or `/score_schema/4/prerace/4`)
	# into the current `GEOMETRY_TAG`, so schema bumps no longer force
	# full re-solves. If anything migrated, persist the rewrite so the
	# next load is free.
	solved, migrated_count = interval_fingerprint.migrate_legacy_fingerprints(solved)
	if migrated_count > 0:
		intervals_file["solved_intervals"] = solved
		state_io.write_torso_box_coords(intervals_path, intervals_file)
		print(f"  store: migrated {migrated_count} legacy fingerprint(s) "
			f"to current geometry tag")
	# merge prior interval_score back onto each geometry entry.
	# key on (start_frame, end_frame) because interval_scores.json does
	# not carry fingerprints.
	prior_scores = {}
	if os.path.isfile(diag_path):
		diag = state_io.load_diagnostics(diag_path)
		for iv in diag.get("intervals", []):
			key = (int(iv["start_frame"]), int(iv["end_frame"]))
			prior_scores[key] = iv.get("interval_score", {})
	# Geometry without a matching score is unusable downstream:
	# Stage 4 promotion reads interval_score["confidence_tier"] and
	# would KeyError on an empty score dict. Drop such entries so the
	# solver re-runs them and re-populates scores from scratch.
	stale_fingerprints = []
	for fingerprint, entry in solved.items():
		key = (int(entry["start_frame"]), int(entry["end_frame"]))
		score = prior_scores.get(key, {})
		if not score:
			stale_fingerprints.append(fingerprint)
			continue
		entry["interval_score"] = score
	for fingerprint in stale_fingerprints:
		del solved[fingerprint]
	if stale_fingerprints:
		print(f"  store: dropped {len(stale_fingerprints)} interval(s) "
			f"with missing scores; will re-solve")

	def _on_interval_solved(fingerprint: str, result: dict) -> None:
		"""Persist a newly solved interval to disk."""
		solved[fingerprint] = result
		intervals_file["solved_intervals"] = solved
		# embed video identity in each write
		if VIDEO_IDENTITY is not None:
			intervals_file["video_identity"] = VIDEO_IDENTITY
		state_io.write_torso_box_coords(intervals_path, intervals_file)

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
		intervals_path: Path to the torso_box_coords NPZ file.
		changed_frames: Set of frame_index ints that were modified.
	"""
	intervals_file = state_io.load_torso_box_coords(intervals_path)
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
	state_io.write_torso_box_coords(intervals_path, intervals_file)
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
def _resolve_solve_bin_factor(
	cli_bin: int | None,
	auto_target: int | None,
	source_width: int,
	source_height: int,
) -> tuple[int, str | None]:
	"""Resolve the solve bin_factor from CLI flags and source dims.

	Three mutually exclusive cases (--bin and --auto-bin are an argparse
	mutually-exclusive group):
	  - explicit --bin N (cli_bin is not None): use N exactly. --bin 1 is
	    the full-resolution escape hatch.
	  - explicit --auto-bin HEIGHT (auto_target is not None): keep the
	    existing HEIGHT-based meaning, bin = max(1, round(source_h / HEIGHT)).
	  - neither flag (both None): production default, route source WIDTH
	    through the shared floor selector at the project-wide default
	    target (TARGET_DEFAULT_WIDTH_PX; 1440p and below stay full-res).

	Compatibility note: the no-flag default keys on source WIDTH against
	the project-wide default target, while --auto-bin keys on source
	HEIGHT. The two paths use different axes on purpose and must not be
	conflated.

	Args:
		cli_bin: Value of --bin (None when the flag was not given).
		auto_target: Value of --auto-bin HEIGHT (None when not given).
		source_width: Raw source frame width in pixels.
		source_height: Raw source frame height in pixels.

	Returns:
		(bin_factor, info_message) where info_message is a one-line string
		to print describing the resolution, or None when nothing to print.

	Raises:
		ValueError: --auto-bin target < 1, or resolved bin_factor < 1.
	"""
	if cli_bin is not None:
		# explicit --bin N (includes the --bin 1 full-res escape hatch)
		if cli_bin < 1:
			raise ValueError(f"--bin must be >= 1, got {cli_bin}")
		return (cli_bin, None)
	if auto_target is not None:
		# explicit --auto-bin HEIGHT: unchanged height-based meaning
		if auto_target < 1:
			raise ValueError(
				f"--auto-bin target must be >= 1, got {auto_target}"
			)
		bin_factor = max(1, int(round(source_height / float(auto_target))))
		actual_height = source_height // bin_factor
		msg = (
			f"  --auto-bin {auto_target}: source height {source_height}"
			f" -> bin_factor={bin_factor} (actual height {actual_height})"
		)
		return (bin_factor, msg)
	# neither flag: production default keyed on source WIDTH, floored at
	# the project-wide TARGET_DEFAULT_WIDTH_PX (selector default arg).
	bin_factor = common_tools.frame_reader.select_default_bin_factor(
		source_width
	)
	actual_width = source_width // bin_factor
	msg = (
		f"  default bin: source width {source_width}"
		f" -> bin_factor={bin_factor} (actual width {actual_width})"
	)
	return (bin_factor, msg)


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
	is_refine: bool = False,
	decode_video_path: str = None,
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
		decode_video_path: Path the solver decodes frames from (the
			resolved working-decode video: fast-read when valid, else the
			original). State paths and identity continue to key off
			args.input_file (the original). Defaults to args.input_file
			when not supplied so non-routed callers behave as before.

	Returns:
		Diagnostics dict from solve_all_intervals().
	"""
	# decode path defaults to the original when a caller does not route a
	# resolved working-decode path through.
	if decode_video_path is None:
		decode_video_path = args.input_file
	fps = video_info["fps"]
	usable_seeds, _, _ = _validate_usable_seeds(seeds)

	print(f"solving... "
		f"({len(usable_seeds)} usable seeds, {num_workers} workers)")
	print("  (press Q to quit, P to pause)")
	t_solve_start = time.time()
	prior_ivs, on_solved_cb = _load_prior_results(intervals_path, diag_path)

	# Detect first-run cold-start condition: check if new hermite/blob
	# solved-result namespaces are populated. Non-blocking warning only.
	data_dir = tr_paths.ensure_data_dir()
	hermite_dir = os.path.join(data_dir, "intervals", "hermite")
	blob_dir = os.path.join(data_dir, "intervals", "blob")
	hermite_exists = os.path.isdir(hermite_dir) and any(
		os.scandir(hermite_dir)
	)
	blob_exists = os.path.isdir(blob_dir) and any(os.scandir(blob_dir))
	if not hermite_exists and not blob_exists:
		print("first run after solve restructure: full recompute expected")

	# build solver kwargs.  When neither --bin nor --auto-bin is given,
	# the no-flag default routes source WIDTH through the shared floor
	# selector at the project-wide TARGET_DEFAULT_WIDTH_PX.  --auto-bin
	# keeps its HEIGHT-based meaning; the two paths key on different axes
	# on purpose.
	cli_bin = getattr(args, "bin_factor", None)
	auto_target = getattr(args, "auto_bin_target", None)
	source_width = int(video_info["width"])
	source_height = int(video_info["height"])
	bin_factor, bin_info_msg = _resolve_solve_bin_factor(
		cli_bin, auto_target, source_width, source_height
	)
	if bin_info_msg is not None:
		print(bin_info_msg)
	if bin_factor > 1:
		print(
			f"  bin_factor={bin_factor}: camera-motion and residual"
			f" stages run on processed frames"
		)
	solve_kwargs = {
		"num_workers": num_workers,
		"debug": args.debug,
		"prior_solved_intervals": prior_ivs,
		"on_interval_solved": on_solved_cb,
		"hermite_only": args.hermite_only,
		"full_solve": args.full_solve,
		"upgrade": getattr(args, "upgrade", False),
		"bin_factor": bin_factor,
	}
	if on_interval_complete is not None:
		solve_kwargs["on_interval_complete"] = on_interval_complete

	# Stage 1: camera motion precompute
	print()
	print("Stage 1: camera motion")
	t_stage1_start = time.time()
	# Precompute camera motion for scene coordinate transformation
	if is_refine:
		# Refine never recomputes camera motion. Camera motion is a
		# property of the video, not of the seeds, so refine loads
		# the canonical camera_motion.npz solve produced. Missing
		# file or motion_model mismatch -> hard error pointing the
		# user at solve.
		print("loading camera motion (refine -- not recomputed)...")
		# Pass the refine run's bin_factor so a bin mismatch fails loudly
		# instead of reusing a stale-bin SOURCE camera track.
		motion_track = camera_motion.load_active_camera_motion_or_fail(
			args.input_file, cfg, expected_bin_factor=bin_factor,
		)
	else:
		print("precomputing camera motion...")
		# Stage 1 reader honors --bin via FrameReader. bin_factor=1
		# returns byte-identical frames. Decodes from the routed
		# decode_video_path; the artifact path/identity below stay keyed to
		# args.input_file (the original).
		stage1_reader = common_tools.frame_reader.FrameReader(
			video_path=decode_video_path,
			fps=float(video_info["fps"]),
			total_frames=int(video_info["frame_count"]),
			bin_factor=bin_factor,
		)
		try:
			motion_track = camera_motion.precompute_camera_motion(
				stage1_reader, cfg, args.input_file, video_info
			)
		finally:
			stage1_reader.close()
	motion_track_data = motion_track
	scene_transform = scene_coords.SceneTransform(motion_track)
	# pass the decode video path through so workers can reopen it in their
	# own process; VideoReader instances cannot cross the process boundary.
	# Workers receive the resolved decode path (no per-worker re-validation).
	solve_kwargs["decode_video_path"] = decode_video_path
	# Original video path threads through so every artifact-output-name and
	# identity decision in the solve queue keys off the original, never the
	# fast-read decode path (contract C13). decode_video_path is for frame
	# decoding only.
	solve_kwargs["original_video_path"] = args.input_file
	t_stage1_elapsed = time.time() - t_stage1_start
	print(f"  (Stage 1 complete, {t_stage1_elapsed:.1f}s)")

	# Stage 2: race-start interval identification. Locate the seed pair
	# spanning race_start_frame so Stage 3 can classify intervals as
	# pre-race vs post-race up front. Returns None when no pre-race phase
	# is identifiable; downstream code treats that as "skip pre-race
	# synthesis."
	print()
	print("Stage 2: race-start identification")
	t_stage2_start = time.time()
	race_start_interval = race_start.locate_race_start_interval(
		seeds, scene_transform, fps,
	)
	solve_kwargs["race_start_interval"] = race_start_interval
	t_stage2_elapsed = time.time() - t_stage2_start
	if race_start_interval is None:
		print(f"  (no pre-race phase detected, {t_stage2_elapsed:.1f}s)")
	else:
		rs_a, rs_b = race_start_interval
		print(
			f"  race-start interval: seeds {rs_a}-{rs_b} "
			f"({t_stage2_elapsed:.1f}s)"
		)

		# Compute race-start frame and pre-race reference immediately after Stage 2
		# (before Stage 3 dispatches). These use only Stage 2 output.
		final_race_start_frame = race_start.pick_race_start_frame_midpoint(
			rs_a, rs_b,
		)
		pre_race_reference = race_start.compute_pre_race_reference(
			seeds, final_race_start_frame, scene_transform,
			race_start_interval=race_start_interval
		)
		print()
		print("RACE-START DETECTION")
		print(f"  race_start_frame: {final_race_start_frame}")
		print(f"  pre-race reference: torso {pre_race_reference['torso_w']:.0f}x{pre_race_reference['torso_h']:.0f} "
			f"at scene ({pre_race_reference['scene_anchor_x']:.0f}, {pre_race_reference['scene_anchor_y']:.0f})")
		# Store in solve_kwargs so the early pre-race fast-path can reuse it
		solve_kwargs["pre_race_reference"] = pre_race_reference

	# Stage 3: Hermite-only analytical solve on all post-race intervals
	print()
	print("Stage 3: Hermite pass (analytical solver)")
	t_stage3_start = time.time()

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
		stage3_reader = common_tools.frame_reader.FrameReader(
			video_path=decode_video_path,
			fps=float(video_info["fps"]),
			total_frames=int(video_info["frame_count"]),
			bin_factor=bin_factor,
		)
		try:
			diagnostics = interval_solver.solve_all_intervals(
				stage3_reader, seeds,
				detector,
				cfg,
				scene_transform=scene_transform,
				motion_track=motion_track_data,
				video_frame_count=video_info["frame_count"],
				**solve_kwargs,
			)
		finally:
			stage3_reader.close()
	# restore default signal handler
	key_input.restore_default_sigint()
	t_stage3_elapsed = time.time() - t_stage3_start
	print()
	print(f"Stage 3 complete ({t_stage3_elapsed:.1f}s)")
	diagnostics["fps"] = fps
	# Record the solve mode: default (1-4), hermite_only (1-3), or full (1-5)
	hermite_only = solve_kwargs["hermite_only"]
	full_solve = solve_kwargs["full_solve"]
	if hermite_only:
		solve_mode = "hermite_only"
	elif full_solve:
		solve_mode = "full"
	else:
		solve_mode = "default"
	diagnostics["solve_mode"] = solve_mode
	# solve_stage reflects the final stage completed in this run.
	# hermite_only stops after Stage 3; default stops after Stage 4;
	# full continues through Stage 5. For consistency, record the stage
	# that completed, not hardcoded "hermite".
	if hermite_only:
		diagnostics["solve_stage"] = "stage_3_hermite"
	elif full_solve:
		diagnostics["solve_stage"] = "stage_5_blob_all"
	else:
		diagnostics["solve_stage"] = "stage_4_promoted"
	t_solve_elapsed = time.time() - t_solve_start
	# mark whether the solve completed or was interrupted
	# use the unified torso_box_coords artifact which includes all three paths
	intervals_path_unified = tr_paths.default_intervals_path(args.input_file)
	torso_box_coords_data = {"solved_intervals": prior_ivs}
	if rc.quit_requested:
		torso_box_coords_data["solve_complete"] = False
		print(f"  solve interrupted ({t_solve_elapsed:.1f}s)")
	else:
		torso_box_coords_data["solve_complete"] = True
		print(f"  solve complete ({t_solve_elapsed:.1f}s)")
	if VIDEO_IDENTITY is not None:
		torso_box_coords_data["video_identity"] = VIDEO_IDENTITY
	state_io.write_torso_box_coords(intervals_path_unified, torso_box_coords_data)
	print(f"  torso box coordinates written to {intervals_path_unified}")
	# write diagnostics to disk
	if VIDEO_IDENTITY is not None:
		diagnostics["video_identity"] = VIDEO_IDENTITY
	state_io.write_solver_diagnostics(diagnostics, diag_path, fps)
	print(f"  diagnostics written to {diag_path}")
	# optional debug sidecar: per-frame agreement data for investigation.
	# derive the sidecar path by stripping the canonical interval-scores
	# suffix (NOT via str.replace on a legacy name -- that became a no-op
	# after the diagnostics->interval_scores rename and silently wrote
	# agreement data on top of the scoring file).
	if args.debug:
		scores_suffix = ".track_runner.interval_scores.json"
		if diag_path.endswith(scores_suffix):
			debug_path = (
				diag_path[: -len(scores_suffix)]
				+ ".track_runner.agreement_debug.json"
			)
		else:
			# defensive fallback for unusual paths; unlikely in practice
			debug_path = diag_path + ".agreement_debug.json"
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
	video_context: fastread_video.VideoContext,
) -> None:
	"""Seed collection mode: collect seeds and save.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		seeds_path: Path to the seeds JSON file.
		video_context: Resolved per-run routing; the seeding UI decodes
			from video_context.working_decode.path while seed state keys
			off video_context.original_video_path.
	"""
	# show which physical video frames decode from for this run
	fastread_video.print_video_routing_banner(
		video_context.original_video_path,
		video_context.working_decode.path,
	)
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
	# load predictions from solved intervals with scores merged in
	predictions = None
	diag_path = tr_paths.default_diagnostics_path(args.input_file)
	intervals_path = tr_paths.default_intervals_path(args.input_file)
	if os.path.isfile(intervals_path):
		predictions = _predictions_from_torso_box_coords(
			intervals_path, diag_path, video_info["fps"],
		)
		if predictions:
			print(f"  loaded predictions for {len(predictions)} frames")

	# convert --start time to frame index
	start_frame = None
	if getattr(args, "start_time", None) is not None:
		start_frame = int(args.start_time * video_info["fps"])

	# seed collection
	print(f"launching seed collection (pass {pass_number})...")
	seeds = seeding.collect_seeds(
		video_context.working_decode.path,
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
	video_context: fastread_video.VideoContext,
) -> None:
	"""Seed editor mode: review/fix/delete existing seeds interactively.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		seeds_path: Path to the seeds JSON file.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved-intervals JSON file.
		video_context: Resolved per-run routing; the editor UI decodes
			from video_context.working_decode.path while seed state keys
			off video_context.original_video_path.
	"""
	# show which physical video frames decode from for this run
	fastread_video.print_video_routing_banner(
		video_context.original_video_path,
		video_context.working_decode.path,
	)
	seeds = _load_and_deduplicate_seeds(seeds_path)
	if not seeds:
		raise RuntimeError(f"no seeds to edit in {seeds_path}")
	print(f"loaded {len(seeds)} seeds from {seeds_path}")

	# back up seeds file before editing
	backup_path = seeds_path + ".bak"
	shutil.copy2(seeds_path, backup_path)
	print(f"  backup saved to {backup_path}")

	# compute seed confidence scores from diagnostics if available
	seed_confidences = None
	if os.path.isfile(diag_path):
		diag_data = state_io.load_diagnostics(diag_path)
		if diag_data.get("intervals"):
			# compute seed confidence scores from interval diagnostics
			seed_confidences = scoring.compute_seed_confidences(
				seeds, diag_data.get("intervals", []),
			)
			if seed_confidences:
				print(f"  computed confidence for {len(seed_confidences)} seeds")

	# load predictions from solved intervals (has per-frame tracks and merged scores)
	predictions = None
	if os.path.isfile(intervals_path):
		predictions = _predictions_from_torso_box_coords(
			intervals_path, diag_path, video_info["fps"],
		)
		if predictions:
			print(f"  loaded predictions for {len(predictions)} frames")

	# optionally filter by severity (show only seeds near weak intervals)
	# pre_race intervals are excluded from severity filtering
	frame_filter = None
	severity = getattr(args, "severity", None)
	if severity is not None and os.path.isfile(diag_path):
		fps = video_info["fps"]
		diag_data = state_io.load_diagnostics(diag_path)
		intervals = diag_data.get("intervals", [])
		# collect frame ranges from weak intervals at the severity threshold
		weak_frames = set()
		pre_race_excluded = 0
		for iv in intervals:
			score = iv["interval_score"]
			confidence = review.get_confidence_label(score)
			# pre_race intervals are synthesized and excluded from severity filtering
			if confidence == "pre_race":
				pre_race_excluded += 1
				continue
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
			msg = f"  severity filter: {len(weak_frames)} seeds near {severity}+ severity intervals"
			if pre_race_excluded > 0:
				msg += f" ({pre_race_excluded} pre-race intervals excluded)"
			print(msg)
		else:
			msg = f"  no seeds match severity={severity} filter, showing all"
			if pre_race_excluded > 0:
				msg += f" ({pre_race_excluded} pre-race intervals excluded)"
			print(msg)

	# convert --start time to frame index
	start_frame = None
	if getattr(args, "start_time", None) is not None:
		start_frame = int(args.start_time * video_info["fps"])

	# run the editor
	edited_seeds, summary = seed_editor.edit_seeds(
		video_context.working_decode.path, seeds, cfg,
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
def _generate_race_start_target_frames(
	diagnostics: dict,
	fps: float,
	frame_count: int,
) -> list:
	"""Generate target frame list for --race-start mode.

	Args:
		diagnostics: Loaded diagnostics dict with race_start_interval and
			race_start_frame in pre_race_reference.
		fps: Video frame rate.
		frame_count: Total frame count for clamping.

	Returns:
		list: Sorted, deduped, clamped frame indices.

	Raises:
		RuntimeError: If diagnostics lacks required fields.
	"""
	pre_race_reference = diagnostics.get("pre_race_reference")
	if pre_race_reference is None:
		raise RuntimeError(
			"diagnostics missing pre_race_reference; "
			"run 'solve' first to detect race_start_frame"
		)

	# Check schema version. race_start_interval was added in schema 5
	# (pre-unification); any version >= 5 is readable here per contract
	# C9 (older schemas remain readable when safe). The current
	# SCHEMA_VERSION may be higher but the field set we read is stable.
	if "track_runner_diagnostics" not in diagnostics:
		raise RuntimeError(
			"diagnostics file missing track_runner_diagnostics header; "
			"run 'solve' first to generate valid diagnostics"
		)
	header = diagnostics["track_runner_diagnostics"]
	min_required = 5
	if header < min_required:
		raise RuntimeError(
			f"diagnostics schema is version {header}, but target --race-start "
			f"requires at least version {min_required} (race_start_interval); "
			f"run 'solve' first to regenerate"
		)

	# Validate race_start_frame is present (fail loud on missing key);
	# the actual frame is not needed for selection -- the interval
	# bounds drive it.
	_ = int(pre_race_reference["race_start_frame"])
	race_start_interval = pre_race_reference["race_start_interval"]
	if len(race_start_interval) != 2:
		raise RuntimeError(
			"race_start_interval must have exactly 2 elements; "
			"run 'solve' first"
		)

	interval_low = int(race_start_interval[0])
	interval_high = int(race_start_interval[1])
	width = interval_high - interval_low
	if width < 1:
		raise RuntimeError(
			f"race_start_interval ({interval_low}, {interval_high}) has "
			f"non-positive width; run 'solve' first to regenerate"
		)

	# Frame selection scales with interval width so each refine pass
	# converges. Previously used fixed-second offsets (+/- 0.1, 0.25,
	# 0.5 s); on a 4-frame interval those span ~60x the interval and
	# the same far-away frames were proposed every pass. With width-
	# fraction offsets, every proposed frame lies inside
	# [interval_low, interval_high]:
	#   interval_low (last stationary seed)
	#   interval_low + 1/8 * width
	#   interval_low + 1/4 * width
	#   interval_low + 1/2 * width  (= race_start_frame midpoint)
	#   interval_low + 3/4 * width
	#   interval_low + 7/8 * width
	#   interval_high (first moving seed)
	# Dedupe + sort handles short intervals where fractions collapse.
	fractions = (0.0, 0.125, 0.25, 0.5, 0.75, 0.875, 1.0)
	frames = []
	for f in fractions:
		frames.append(interval_low + round(width * f))

	# Clamp to valid range
	clamped_frames = []
	for f in frames:
		clamped = max(0, min(f, frame_count - 1))
		clamped_frames.append(clamped)

	# Deduplicate and sort
	unique_frames = sorted(set(clamped_frames))

	return unique_frames


#============================================
def _mode_target(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	seeds_path: str,
	diag_path: str,
	intervals_path: str,
	video_context: fastread_video.VideoContext,
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
		video_context: Resolved per-run routing; the target UI decodes
			from video_context.working_decode.path while seed state keys
			off video_context.original_video_path.
	"""
	# show which physical video frames decode from for this run
	fastread_video.print_video_routing_banner(
		video_context.original_video_path,
		video_context.working_decode.path,
	)
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

	# Check for sub-modes
	use_race_start_mode = getattr(args, "target_race_start", False)
	use_from_analyze_mode = getattr(args, "target_from_analyze", False)

	if use_from_analyze_mode:
		analysis_path = tr_paths.default_encode_analysis_path(args.input_file)
		top_n = getattr(args, "top_n", None) or encode_analysis.ANALYZE_TOP_DEFAULT
		gap_top_n = getattr(args, "gap_top_n", None) or 0
		# pass existing seed frames so the loader skips regions already
		# seeded and can inject a midpoint for the largest seed gap.
		existing_seed_frames = [int(s["frame_index"]) for s in seeds]
		target_frames = encode_analysis.load_analyze_target_frames(
			analysis_path, video_info["frame_count"],
			top_n=top_n,
			existing_seed_frames=existing_seed_frames,
			gap_top_n=gap_top_n,
		)
		gaps_label = f", gaps {gap_top_n}" if gap_top_n else ""
		print(
			f"  loaded {len(target_frames)} target frames from "
			f"{analysis_path} (top {top_n}{gaps_label})"
		)
	elif use_race_start_mode:
		# Race-start target mode: fixed frame selection around detected race-start
		target_frames = _generate_race_start_target_frames(
			diag_data,
			fps,
			video_info["frame_count"],
		)
		# Print the contact sheet path
		contact_sheet_path = tr_paths.default_race_start_contact_sheet_path(
			args.input_file
		)
		print(f"  race-start confirmation: {contact_sheet_path}")
	else:
		# Standard target mode: generate refinement targets with optional severity filter
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

		# apply --top slicing if requested. UX: --top N means "give me N
		# frames, period" -- if the severity floor or confidence-based
		# filter produces fewer than N candidates, supplement with the
		# next-worst intervals so the user always gets the requested count
		# (capped at the number of non-pre-race intervals available).
		if top_n is not None:
			intervals = diag_data.get("intervals", [])
			# every non-pre-race interval is a potential candidate, ranked
			# worst-first by rank_key. Pre-race is excluded per C4.
			candidates = [
				iv for iv in intervals
				if review.get_confidence_label(iv["interval_score"]) != "pre_race"
			]
			candidates.sort(key=review.rank_key)
			top_intervals = candidates[:top_n]

			if top_intervals:
				top_diag = dict(diag_data)
				top_diag["intervals"] = top_intervals
				# severity=None for the regenerate step: --top has already
				# selected the worst-N regardless of severity floor, so
				# re-applying the severity filter would discard frames the
				# user explicitly asked for.
				target_frames = review.generate_refinement_targets(
					top_diag,
					mode="suggested",
					seed_interval=int(seed_interval * fps),
					severity=None,
				)
				# Ensure every top-N interval contributes at least one
				# target frame (midpoint), even high/good-confidence ones
				# that identify_weak_spans skips. With --top N the user
				# expects N frames.
				target_set = set(target_frames)
				for iv in top_intervals:
					start_frame = int(iv["start_frame"])
					end_frame = int(iv["end_frame"])
					if any(start_frame <= f <= end_frame for f in target_set):
						continue
					target_set.add((start_frame + end_frame) // 2)
				target_frames = sorted(target_set)
			else:
				target_frames = []

			if len(top_intervals) < top_n:
				print(
					f"  hint: only {len(top_intervals)} intervals available "
					f"(--top={top_n} requested)"
				)

	if not target_frames:
		if use_from_analyze_mode:
			print("  no target frames from analyze report (run 'analyze' first)")
		elif use_race_start_mode:
			print("  no race-start frames selected")
		else:
			sev_label = f" at {severity}+ severity" if severity else ""
			print(f"  no weak intervals found{sev_label}")
		return

	if use_from_analyze_mode:
		print(f"  {len(target_frames)} target frames from analyze report")
	elif use_race_start_mode:
		print(f"  {len(target_frames)} race-start target frames")
	else:
		sev_label = f" ({severity}+ severity)" if severity else ""
		top_label = f" (top {top_n})" if top_n is not None else ""
		print(f"  {len(target_frames)} target frames from weak intervals{sev_label}{top_label}")

	# build FWD/BWD predictions from solved intervals with scores merged in
	predictions = _predictions_from_torso_box_coords(
		intervals_path, diag_path, fps,
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
	frame_list = ", ".join(str(f) for f in target_frames)
	print(f"  target frame list: {frame_list}")
	print(f"  collecting seeds at {len(target_frames)} weak interval frames...")
	updated_seeds = seeding.collect_seeds_at_frames(
		video_context.working_decode.path,
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
def _clear_stale_torso_artifact(intervals_path: str) -> bool:
	"""Remove a torso_box_coords artifact stamped under an unsupported schema.

	Solve is the full re-solve path. A prior artifact stamped under a
	rolled-back schema (for example v11-v14) is no longer a supported solver
	artifact, and `load_torso_box_coords` raises on it before any clear runs.
	Peek the stamped schema without paying the rejection cost; if it is not a
	supported `torso_box_coords` schema, warn and delete the file so solve
	treats it as absent and regenerates it fresh at the current schema. This
	leniency is scoped to solve only; loaders and refine keep rejecting stale
	artifacts clearly.

	Args:
		intervals_path: Path to the torso_box_coords.npz artifact.

	Returns:
		True if a stale artifact was found and removed, otherwise False.
	"""
	if not os.path.isfile(intervals_path):
		return False
	prior_schema = state_io.peek_torso_box_coords_schema(intervals_path)
	if prior_schema is None:
		return False
	if tr_schema.is_supported_artifact_schema("torso_box_coords", prior_schema):
		return False
	print(f"  existing schema v{prior_schema} artifact is stale; "
		f"solve will regenerate it")
	os.remove(intervals_path)
	return True


#============================================
def _clear_stale_diagnostics_artifact(diag_path: str) -> bool:
	"""Remove an interval-scores artifact stamped under an unsupported schema.

	Solve regenerates interval scores from scratch, so a prior interval-scores
	JSON stamped under a rolled-back schema (for example v11-v14) is cleared
	rather than loaded. `_load_prior_results` calls `load_diagnostics`, which
	raises on an unsupported header before solve overwrites it; peek the header
	cheaply and delete the stale file so solve treats it as absent. This
	leniency is scoped to solve only, alongside the torso-artifact guard;
	loaders and refine keep rejecting stale artifacts clearly.

	Args:
		diag_path: Path to the interval_scores.json artifact.

	Returns:
		True if a stale artifact was found and removed, otherwise False.
	"""
	if not os.path.isfile(diag_path):
		return False
	prior_schema = state_io.peek_diagnostics_schema(diag_path)
	if prior_schema is None:
		return False
	if tr_schema.is_supported_artifact_schema("diagnostics", prior_schema):
		return False
	print(f"  existing schema v{prior_schema} interval-scores file is stale; "
		f"solve will regenerate it")
	os.remove(diag_path)
	return True


#============================================
def _mode_solve(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	seeds_path: str,
	diag_path: str,
	intervals_path: str,
	video_context: fastread_video.VideoContext,
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
		video_context: Resolved per-run routing; solve decodes from
			video_context.working_decode.path.
	"""
	fastread_video.print_video_routing_banner(
		video_context.original_video_path,
		video_context.working_decode.path,
	)
	seeds = _load_and_deduplicate_seeds(seeds_path)
	if not seeds:
		raise RuntimeError(f"no seeds found in {seeds_path}")
	print(f"loaded {len(seeds)} seeds from {seeds_path}")

	# Stale-schema guard, scoped to the solve operator path only. Solve is the
	# full re-solve path, so a prior artifact under a rolled-back schema is
	# cleared and regenerated fresh; loaders and refine keep rejecting stale
	# artifacts clearly.
	_clear_stale_torso_artifact(intervals_path)
	_clear_stale_diagnostics_artifact(diag_path)

	# only clear intervals if a prior solve completed successfully;
	# if the prior solve was interrupted, resume from saved intervals
	if os.path.isfile(intervals_path):
		intervals_file = state_io.load_torso_box_coords(intervals_path)
		prior_complete = intervals_file.get("solve_complete", False)
		prior_count = len(intervals_file.get("solved_intervals", {}))
		# --upgrade: keep store, run Stage 4 promotion only. Skip the
		# clear-and-re-solve prompt entirely; we do not want to wipe
		# anything. Falls through to _run_solve below with upgrade=True.
		if args.upgrade:
			if not prior_complete or prior_count == 0:
				print("  --upgrade requires a completed prior solve; "
					"run 'solve --hermite-only --keep' first")
				return
			print(f"  --upgrade: running Stage 4 on existing store "
				f"({prior_count} intervals)")
		elif prior_complete and prior_count > 0:
			print(f"  prior solve completed ({prior_count} intervals)")
			if args.assume_yes:
				answer = "y"
				print("  clear and re-solve from scratch? [y/N] y (-y)")
			elif args.keep_prior:
				answer = "n"
				print("  clear and re-solve from scratch? [y/N] n (--keep)")
			else:
				answer = input(
					"  clear and re-solve from scratch? [y/N] "
				).strip().lower()
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
		decode_video_path=video_context.working_decode.path,
	)


#============================================
def _mode_refine(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	seeds_path: str,
	diag_path: str,
	intervals_path: str,
	video_context: fastread_video.VideoContext,
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
		video_context: Resolved per-run routing; refine decodes from
			video_context.working_decode.path.
	"""
	fastread_video.print_video_routing_banner(
		video_context.original_video_path,
		video_context.working_decode.path,
	)
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
	# store validity is set membership of fingerprints, not cardinality,
	# and not the legacy global solve_complete flag. the queue module's
	# plan_interval_work is the single source of truth for the seed
	# filter, fingerprint walk, and orphan prune -- solve mode and
	# refine mode both call it so fingerprints match byte-for-byte.
	intervals_file = state_io.load_torso_box_coords(intervals_path)
	solved_intervals = intervals_file.get("solved_intervals", {})
	# Migrate geometry-compatible legacy fingerprint keys so refine can reuse
	# them. Write-back is gated on migrated_count > 0; if nothing
	# changed, we preserve the zero-write guarantee of unchanged-seed
	# refine.
	solved_intervals, migrated_count = interval_fingerprint.migrate_legacy_fingerprints(
		solved_intervals,
	)
	if migrated_count > 0:
		intervals_file["solved_intervals"] = solved_intervals
		state_io.write_torso_box_coords(intervals_path, intervals_file)
		print(f"  store: migrated {migrated_count} legacy fingerprint(s) "
			f"to current geometry tag")
	# Drop entries with geometry but no matching score (interrupted prior
	# solve left npz and interval_scores.json out of sync). These need to
	# be re-solved to make scoring consistent. Geometry-without-score is
	# treated as not-solved for refine partitioning.
	scored_keys = set()
	if os.path.isfile(diag_path):
		score_data = state_io.load_diagnostics(diag_path)
		for scored_iv in score_data.get("intervals", []):
			scored_keys.add((
				int(scored_iv["start_frame"]),
				int(scored_iv["end_frame"]),
			))
	unscored_fps = [
		fp for fp, iv in solved_intervals.items()
		if (int(iv["start_frame"]), int(iv["end_frame"])) not in scored_keys
	]
	if unscored_fps:
		for fp in unscored_fps:
			del solved_intervals[fp]
		# Persist the trimmed npz so _run_solve (which re-reads from
		# disk) sees the same partition the plan was built from.
		intervals_file["solved_intervals"] = solved_intervals
		state_io.write_torso_box_coords(intervals_path, intervals_file)
		print(
			f"  store: dropped {len(unscored_fps)} intervals with "
			f"geometry but no score (interrupted solve); will re-solve"
		)
	# Resolve the run's bin_factor with the same selector _run_solve uses so
	# the refine partition keys on bin identically to the downstream solve.
	# bin enters every interval fingerprint (cache-key bookkeeping only), so a
	# bin change between solve and refine forces recompute instead of reusing a
	# stale-bin interval store.
	refine_cli_bin = getattr(args, "bin_factor", None)
	refine_auto_target = getattr(args, "auto_bin_target", None)
	refine_bin_factor, _ = _resolve_solve_bin_factor(
		refine_cli_bin, refine_auto_target,
		int(video_info["width"]), int(video_info["height"]),
	)
	plan = solve_queue.plan_interval_work(
		seeds, solved_intervals, bin_factor=refine_bin_factor,
	)
	total_expected = plan.total_intervals

	# fast-exit (no write). enforces contract rule 1: unchanged seeds
	# produce no computation AND no disk write. pending_count == 0 is
	# the membership-complete signal; solve_complete on disk is advisory
	# only.
	if plan.pending_count == 0:
		# solve_complete is advisory, not a correctness gate. if
		# membership is complete but the legacy completion flag is
		# false, surface the discrepancy but trust the store.
		if intervals_file.get("solve_complete") is False:
			print("  warning: solve_complete=false on disk, "
				"store membership complete; trusting store")
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
		state_io.write_torso_box_coords(intervals_path, intervals_file)
		print(f"  store: pruned {pruned_count} stale intervals "
			f"({before} -> {len(plan.pruned_prior)})")

	# contract C6: refine must retain untouched intervals. If the prune
	# above emptied the prior store but intervals remain to solve, refine
	# would be doing a full solve under the refine label. Fail loud and
	# tell the user to run 'solve' with a reason.
	if (plan.reused_count == 0 and plan.total_intervals > 0
			and plan.pending_count == plan.total_intervals):
		raise RuntimeError(
			f"refine would re-solve all {plan.total_intervals} intervals "
			f"(no prior fingerprints matched); this is a full solve -- "
			f"run 'solve' instead. Likely cause: geometry tag changed "
			f"without a migration entry, or the store file is for a "
			f"different seed set."
		)

	print(f"  refine: {plan.pending_count} of {total_expected} intervals "
		f"need solving ({plan.reused_count} will be reused)")

	num_workers = _resolve_workers(args)
	_run_solve(
		args, cfg, seeds, video_info,
		intervals_path, diag_path, num_workers,
		is_refine=True,
		decode_video_path=video_context.working_decode.path,
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
	# Keep the OpenCV-backed filter registry out of CLI startup so
	# non-encode commands do not load cv2 or its FFmpeg bundle.

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

	Precedence (highest first):
	1. --no-filters wins over everything (returns []).
	2. -F none (case-insensitive, whitespace-tolerant) is an alias for
	   --no-filters and returns []. Mixed forms like 'none,blur' are
	   rejected with a targeted error.
	3. -F <list> overrides config processing.encode_filters.
	4. config processing.encode_filters is the fallback.

	Validates each filter name against the known filter list.

	Args:
		args: Parsed argparse namespace.
		proc_cfg: The processing section of the config dict.

	Returns:
		List of validated filter name strings, or empty list.
	"""
	# Lazy import for the same reason as _apply_encode_overrides():
	# only encode mode needs the OpenCV-backed filter registry.
	import common_tools.frame_filters as frame_filters

	# --no-filters short-circuit (parse-time validation already rejects
	# the --no-filters + -F combination, so reaching here means -F is
	# None whenever no_filters is True; defensive check kept for direct
	# callers).
	if getattr(args, "no_filters", False):
		if getattr(args, "encode_filters", None) is not None:
			raise RuntimeError(
				"--no-filters cannot be combined with -F/--encode-filters; "
				"pick one"
			)
		return []
	# -F none alias: case-insensitive, whitespace-tolerant
	cli_value = getattr(args, "encode_filters", None)
	if cli_value is not None:
		raw_tokens = [tok.strip() for tok in cli_value.split(",") if tok.strip()]
		lowered = [tok.lower() for tok in raw_tokens]
		if "none" in lowered:
			# 'none' must appear alone; reject mixed forms like 'none,blur'
			if len(lowered) > 1:
				raise RuntimeError(
					"'none' cannot be combined with other filters; use "
					"--no-filters or pass an explicit list"
				)
			return []
		filter_list = raw_tokens
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
	video_context: fastread_video.VideoContext | None = None,
) -> None:
	"""Analyze mode: compute crop-path stability metrics before encoding.

	Reconstructs the trajectory from solved intervals (same pipeline as
	encode), computes crop rects, then runs crop-path stability analysis
	and solver context analysis. Prints a formatted console report and
	writes a diagnostic YAML file.

	Reports name BOTH the canonical source (the original video) and the
	decode source (the fast-read working video basename, or the literal
	"original" when no fast-read is in use). Analyze reads solved-interval
	artifacts and does not decode frames itself, but it carries the routing
	labels so report consumers see the same provenance as decode modes.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved-intervals JSON file.
			If None, derived from input_file.
		video_context: Resolved per-run routing. Supplies canonical_source
			(original) and decode_source labels for the reports. When None
			(diagnostic/test callers), labels default to the input file as
			canonical and "original" as decode.
	"""
	# resolve the canonical source (original) and decode-source label. The
	# decode label is the fast-read basename when a valid fast-read is in
	# use, otherwise the literal "original".
	if video_context is None:
		canonical_source = os.path.basename(args.input_file)
		decode_source = "original"
	else:
		canonical_source = os.path.basename(video_context.original_video_path)
		if video_context.working_decode.using_fastread:
			decode_source = os.path.basename(video_context.working_decode.path)
		else:
			decode_source = "original"
		# show the routing banner; analyze itself does not decode frames,
		# so the decode line reflects what working modes would use.
		fastread_video.print_video_routing_banner(
			video_context.original_video_path,
			video_context.working_decode.path,
		)
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
	intervals_file = state_io.load_torso_box_coords(intervals_path)
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

	# reconstruct per-frame conf from FWD/BWD agreement (not persisted in
	# torso_box_coords.npz). See interval_solver.derive_per_frame_confidence.
	derived_confs = interval_solver.derive_per_frame_confidence(
		interval_results, len(trajectory),
	)
	for i, state in enumerate(trajectory):
		if state is not None:
			state["conf"] = derived_confs[i]

	# apply multi-seed anchored interpolation to reduce drift
	seeds_path = tr_paths.default_seeds_path(args.input_file)
	all_seeds = []
	if os.path.isfile(seeds_path):
		seeds_data = state_io.load_seeds(seeds_path)
		all_seeds = seeds_data.get("seeds", [])
		trajectory = interval_solver.anchor_to_seeds(trajectory, all_seeds)
		trajectory = interval_solver._stamp_seed_confidence(
			trajectory, all_seeds,
		)
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
		canonical_source=canonical_source,
		decode_source=decode_source,
	)

	# print console report
	report = encode_analysis.format_analysis_report(
		analysis, solver_context, analysis_path,
		regime_summary_line=regime_summary_line,
		canonical_source=canonical_source,
		decode_source=decode_source,
	)
	print(report)

	# write HTML diagnostic report when --plot is set. Camera motion is
	# loaded with the same graceful-degradation pattern as encode mode
	# (see _mode_encode draw_velocity branch around L2270): a missing
	# artifact is a warning, not an error.
	if args.write_plots:
		# Single message string used in both the HTML warnings section and
		# the stderr line below; defining it once prevents tense drift
		# between the two surfaces.
		camera_motion_missing_msg = (
			"Camera-motion data unavailable; "
			"camera and speed panels were skipped."
		)
		report_warnings = []
		motion_track = None
		scene_transform = None
		try:
			motion_track = camera_motion.load_active_camera_motion_or_fail(args.input_file, cfg)
		except RuntimeError:
			report_warnings.append(camera_motion_missing_msg)
		if motion_track is None:
			print(f"  warning: {camera_motion_missing_msg}", file=sys.stderr)
		else:
			scene_transform = scene_coords.SceneTransform(motion_track)

		# derive HTML output path: same stem as the YAML report, .html suffix
		html_path = pathlib.Path(analysis_path).with_suffix('.html')
		# strip the trailing .encode_analysis from the stem so the HTML's
		# References section links back to the YAML at <stem>.encode_analysis.yaml
		stem = html_path.stem
		if stem.endswith('.encode_analysis'):
			stem = stem[:-len('.encode_analysis')]

		# tr_crop.trajectory_to_crop_rects pads crop_rects to the full source
		# frame count via gap-filling, but `trajectory` here is the shorter
		# post-erasure list. Pad trajectory with None for the missing frames
		# so panel builders can index trajectory[i] alongside crop_rects[i];
		# None entries are treated as "no torso geometry" (gaps in the panel
		# series) per the documented degradation contract.
		total_frames = video_info["frame_count"]
		if len(trajectory) < total_frames:
			trajectory_aligned = list(trajectory) + [None] * (total_frames - len(trajectory))
		else:
			trajectory_aligned = trajectory[:total_frames]

		# Tag the frames `_apply_trajectory_erasure` wiped on purpose so the
		# HTML report can distinguish user-flagged dropouts from tracker gaps;
		# both still serialize as null in the panel JSON, but the renderer
		# can read `frames_erased` to color the two states differently.
		erased_frames = interval_solver.collect_erased_frames(
			all_seeds, fps, total_frames,
		)

		out = analyze_report.write_analyze_report(
			out_path=html_path,
			video_stem=stem,
			trajectory=trajectory_aligned,
			crop_rects=crop_rects,
			motion_track=motion_track,
			scene_transform=scene_transform,
			fps=fps,
			config=cfg,
			warnings=report_warnings,
			erased_frames=erased_frames,
			canonical_source=canonical_source,
			decode_source=decode_source,
		)
		print(f"wrote diagnostic report: {out}")


#============================================
def _mode_encode(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	diag_path: str,
	intervals_path: str | None = None,
	video_context: fastread_video.VideoContext | None = None,
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
		video_context: Resolved per-run routing. Encode always reads
			video_context.final_encode.path (the original) for final
			quality. None falls back to args.input_file.
	"""
	# Encode always decodes the original for final quality. final_encode.path
	# equals the original by construction; fall back to args.input_file when
	# no context is threaded (non-routed callers).
	if video_context is None:
		encode_video_path = args.input_file
	else:
		encode_video_path = video_context.final_encode.path
		fastread_video.print_video_routing_banner(
			video_context.original_video_path, encode_video_path,
		)
	# Import encoder lazily so non-encode commands do not load OpenCV.
	import encoder

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
	intervals_file = state_io.load_torso_box_coords(intervals_path)
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

	# reconstruct per-frame conf from FWD/BWD agreement (not persisted in
	# torso_box_coords.npz). See interval_solver.derive_per_frame_confidence.
	derived_confs = interval_solver.derive_per_frame_confidence(
		interval_results, len(trajectory),
	)
	for i, state in enumerate(trajectory):
		if state is not None:
			state["conf"] = derived_confs[i]

	# derive overlay-tier flags from CLI: developer overlay implies the
	# review overlay; velocity is independent. Global args.debug is left
	# alone (it controls diagnostic output, not rendered overlays).
	draw_tracking = args.draw_tracking_overlay or args.draw_debug_overlay
	draw_debug = args.draw_debug_overlay
	draw_velocity = args.draw_velocity_arrow

	# save raw trajectory and FWD/BWD tracks before anchoring; only the
	# developer overlay needs them (FWD/BWD/raw boxes are debug-tier)
	raw_trajectory_for_debug = None
	fwd_trajectory_for_debug = None
	bwd_trajectory_for_debug = None
	if draw_debug:
		raw_trajectory_for_debug = [
			dict(s) if s is not None else None
			for s in trajectory
		]
		# stitch forward and backward interval paths for FWD/BWD overlay boxes
		n_frames = len(trajectory)
		fwd_trajectory_for_debug = [None] * n_frames
		bwd_trajectory_for_debug = [None] * n_frames
		for result in interval_results:
			start = int(result["start_frame"])
			fwd_track = result.get("forward_path", [])
			bwd_track = result.get("backward_path", [])
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
	all_seeds = []
	if os.path.isfile(seeds_path):
		seeds_data = state_io.load_seeds(seeds_path)
		all_seeds = seeds_data.get("seeds", [])
		trajectory = interval_solver.anchor_to_seeds(trajectory, all_seeds)
		trajectory = interval_solver._stamp_seed_confidence(
			trajectory, all_seeds,
		)
		# apply trajectory erasure from seeds (function decides which seeds to erase)
		fps = float(diag_data.get("fps", video_info["fps"]))
		trajectory = interval_solver._apply_trajectory_erasure(
			trajectory, all_seeds, fps,
		)

	if not trajectory:
		raise RuntimeError(
			"could not reconstruct trajectory from solved intervals"
		)

	# Encode-time NIF synthesis: fill NIF spans with edge-anchored geometry.
	# Requires race_start_frame from diagnostics and the solved trajectory.
	# Build an in-memory NifSpan list and fill NIF-span frames before crop.
	nif_frames = set()
	if all_seeds:
		pre_race_ref = diag_data.get("pre_race_reference")
		race_start_frame = 0
		if pre_race_ref is not None:
			race_start_frame = int(pre_race_ref["race_start_frame"])
		# Convert trajectory to a dict keyed by frame index for build_nif_spans
		frame_size = (video_info["width"], video_info["height"])
		# Build in-memory solved trajectory dict for NIF span construction
		solved_trajectory_dict = {}
		for i, state in enumerate(trajectory):
			if state is not None:
				solved_trajectory_dict[i] = {
					"cx": int(state["cx"]),
					"cy": int(state["cy"]),
					"w": int(state["w"]),
					"h": int(state["h"]),
				}
		# Build NIF spans. Raises ValueError loudly on pre-race NIF or
		# missing before-bracket per WP-A1; we do not catch it.
		nif_spans = off_frame_geometry.build_nif_spans(
			all_seeds, solved_trajectory_dict, frame_size, race_start_frame,
		)
		# Fill trajectory with edge-anchored NIF geometry
		if nif_spans:
			off_frame_states = off_frame_geometry.build_off_frame_states(
				nif_spans, frame_size,
			)
			# Merge filled states into trajectory in-memory copy
			for frame_idx, state in off_frame_states.items():
				if 0 <= frame_idx < len(trajectory):
					trajectory[frame_idx] = state
					nif_frames.add(frame_idx)

	num_workers = _resolve_workers(args)

	# compute crop trajectory
	print("computing crop trajectory...")
	crop_rects = tr_crop.trajectory_to_crop_rects(trajectory, video_info, cfg, nif_frames=nif_frames)

	# resolve output path (encoded output stays next to input video).
	# Default container is .mkv (mkvmerge concat output); --mp4 or -o
	# foo.mp4 produces a .mp4 final via the existing audio-mux step
	# (ffmpeg -c copy honors the destination extension's container).
	# The parser already rejects: -o foo.mov; --mp4 -o foo.mkv; etc.
	output_file = getattr(args, "output_file", None)
	if output_file is not None:
		_, ext = os.path.splitext(output_file)
		ext_lower = ext.lower()
		if ext_lower not in (".mkv", ".mp4"):
			raise RuntimeError(
				f"output extension {ext!r} not supported; use .mkv "
				f"(default) or .mp4 (with --mp4)"
			)
		output_path = output_file
	elif getattr(args, "mp4", False):
		# swap the default .mkv extension for .mp4
		default_mkv = tr_paths.default_output_path(args.input_file)
		stem, _ = os.path.splitext(default_mkv)
		output_path = f"{stem}.mp4"
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

	# pre-encode validation: refuse when the runner is sustained outside
	# the safe central window of the output frame. Black-fill from a
	# slightly off-source crop is fine, but a runner pinned to one side
	# for a sustained run is almost always a configuration bug; better
	# to fail in a second than to burn a multi-minute encode.
	if not args.allow_offcenter_crop:
		aspect_ratio = tr_crop.parse_aspect_ratio(
			proc_cfg.get("crop_aspect", "1:1")
		)
		torso_multiple = float(
			proc_cfg.get("torso_height_multiple", 3.33)
		)
		tr_crop.validate_torso_within_central_window(
			trajectory, crop_rects,
			crop_w, crop_h,
			video_info["width"], video_info["height"],
			torso_multiple=torso_multiple,
			aspect_ratio=aspect_ratio,
		)

	# resolve encode filters: CLI overrides config, config overrides default
	encode_filters = _resolve_encode_filters(args, proc_cfg)

	# encode. The intermediate is fixed at {final_stem}.tmp.mkv -- the
	# encoder writes Matroska bytes via mkvmerge, so a .mkv-suffixed
	# temp name is truthful. Using `.tmp.mkv` (rather than `.mkv` alone)
	# avoids accidentally overwriting an existing real `{stem}.mkv` if
	# the user has both encodes side by side.
	final_stem, _ = os.path.splitext(output_path)
	temp_video = f"{final_stem}.tmp.mkv"
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

	# build frame_states for any overlay tier; debug-only fields (raw,
	# FWD/BWD boxes) are only attached when draw_debug is set.
	# prev_center for the velocity arrow is precomputed here on the
	# driver side so parallel workers see correct lookback across chunk
	# boundaries (a chunk-local lookback would miss valid priors from
	# the preceding chunk and silently suppress the arrow at every
	# segment seam).
	any_overlay = draw_tracking or draw_debug or draw_velocity
	frame_states_for_debug = None
	if any_overlay:
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
		# precompute prev_center for the velocity arrow on the driver
		# side; the encoder will read state["prev_center"] directly so
		# parallel workers do not need access to other workers' chunks.
		#
		# By default the prev_center is land-relative (i.e., the runner's
		# motion relative to the ground), not camera-relative: when the
		# camera pans faster than the runner, a camera-relative arrow
		# would point backwards. We project the prior frame's pixel
		# position into scene (land-anchored) coords, then back into the
		# CURRENT frame's pixel coords. The arrow drawn from the current
		# crosshair to the difference is then runner-vs-ground motion
		# expressed in the current camera's pixel space.
		if draw_velocity:
			scene_transform = None
			# Load the canonical camera-motion artifact. Encode is
			# read-only and degrades gracefully when nothing is
			# available -- it does not hard-error like refine does.
			motion_track = None
			try:
				motion_track = camera_motion.load_active_camera_motion_or_fail(
					args.input_file, cfg
				)
			except RuntimeError:
				motion_track = None
			if motion_track is not None:
				scene_transform = scene_coords.SceneTransform(motion_track)
			else:
				print(
					"  warning: no camera_motion artifact available; "
					"velocity arrow will use camera-relative motion "
					"(run 'solve' to populate the artifact)"
				)
			interval_solver_lookback = encoder._VELOCITY_LOOKBACK_FRAMES
			for i, st in enumerate(frame_states_for_debug):
				if st is None:
					continue
				start = i - 1
				stop = max(-1, i - 1 - interval_solver_lookback)
				prev_center = None
				prev_idx = -1
				for k in range(start, stop, -1):
					if k < 0:
						break
					prior = frame_states_for_debug[k]
					if prior is None:
						continue
					pcx = prior.get("cx")
					pcy = prior.get("cy")
					if pcx is None or pcy is None:
						continue
					if not (math.isfinite(pcx) and math.isfinite(pcy)):
						continue
					prev_center = (float(pcx), float(pcy))
					prev_idx = k
					break
				# Reproject the prior pixel position through scene
				# coordinates so the arrow direction reflects ground
				# motion. Falls back to camera-relative when the artifact
				# is unavailable or the frame index is out of range.
				if prev_center is not None and scene_transform is not None:
					try:
						prev_scene = scene_transform.pixel_to_scene(
							prev_idx, prev_center[0], prev_center[1],
						)
						prev_in_current = scene_transform.scene_to_pixel(
							i, prev_scene[0], prev_scene[1],
						)
						prev_center = (
							float(prev_in_current[0]),
							float(prev_in_current[1]),
						)
					except (IndexError, ValueError):
						pass
				st["prev_center"] = prev_center

	with key_input.KeyInputReader() as enc_kreader:
		if num_workers > 1:
			encoder.encode_cropped_video_parallel(
				encode_video_path, crop_rects, temp_video,
				crop_w, crop_h,
				codec=video_codec, crf=crf_value,
				frame_states=frame_states_for_debug,
				workers=num_workers,
				encode_filters=encode_filters,
				run_control=enc_rc,
				key_reader_obj=enc_kreader,
				draw_tracking=draw_tracking,
				draw_debug=draw_debug,
				draw_velocity=draw_velocity,
				nif_frames=nif_frames,
			)
		else:
			probe_info = common_tools.probe_video.probe_video(encode_video_path)
			with common_tools.frame_reader.FrameReader(
				encode_video_path, probe_info["fps"], probe_info["frame_count"],
			) as reader:
				encoder.encode_cropped_video(
					reader, crop_rects, temp_video,
					crop_w, crop_h,
					codec=video_codec, crf=crf_value,
					frame_states=frame_states_for_debug,
					encode_filters=encode_filters,
					run_control=enc_rc,
					key_reader_obj=enc_kreader,
					draw_tracking=draw_tracking,
					draw_debug=draw_debug,
					draw_velocity=draw_velocity,
					nif_frames=nif_frames,
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
	video_context: fastread_video.VideoContext,
) -> None:
	"""Setup mode: interactive questionnaire for camera configuration.

	Launches an interactive CLI questionnaire to collect per-video camera
	settings (zoom type, camera height, position, track size) and stores
	them in the configuration file.

	Setup may decode the fast-read working video for any display, but the
	configuration it writes always keys off the original video: config_path
	is derived from the original and video_context.metadata_identity (the
	original) is the recorded source. The fast-read video must never be
	recorded as the configured source.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict (may be modified).
		video_info: Video metadata dict.
		config_path: Path to the configuration file (keyed off the original).
		video_context: Resolved per-run routing. Config/state writes use
			video_context.original_video_path; only frame decode for display
			may use video_context.working_decode.path.
	"""
	# show which physical video frames decode from for this run; the config
	# written by run_setup still keys off the original (config_path).
	fastread_video.print_video_routing_banner(
		video_context.original_video_path,
		video_context.working_decode.path,
	)
	setup_mode.run_setup(config_path, cfg)


#============================================
def _mode_prepare(args: argparse.Namespace, video_info: dict) -> None:
	"""Prepare mode: create the fast-read working video.

	Always rebuilds the fast-read from scratch: any existing fast-read is
	deleted and the transcode runs unconditionally.

	prepare uses the ORIGINAL video only (it is the creator). No config,
	seeds, or diagnostics are read. This mode does not require setup to have
	been run first.

	Args:
		args: Parsed argparse namespace (expects verbose).
		video_info: Video metadata dict from _probe_video (not used directly;
			kept for signature consistency with other mode functions).
	"""
	fastread_path = tr_paths.fastread_video_path(args.input_file)
	verbose = args.verbose
	fastread_video.create_fastread_video(
		args.input_file,
		fastread_path,
		verbose=verbose,
	)


#============================================
def main() -> None:
	"""Main entry point for the track_runner v2 CLI."""
	t_total_start = time.time()
	args = cli_args.parse_args()

	# validate input file exists
	if not os.path.isfile(args.input_file):
		raise RuntimeError(f"input file not found: {args.input_file}")

	# require .mkv source: random-access seek on .mov/.mp4 is too slow
	# for the Stage 4 pre-pass and FrameReader strategy-1 path. Remux is
	# lossless and one-time; transcoding is never done by this pipeline.
	ext_lower = os.path.splitext(args.input_file)[1].lower()
	if ext_lower != ".mkv":
		stem = os.path.splitext(args.input_file)[0]
		raise RuntimeError(
			f"input video must be .mkv, got {args.input_file!r}. "
			f".mov/.mp4 are no longer supported because random-access "
			f"seek is too slow on those containers. Remux losslessly: "
			f"mkvmerge -o {stem}.mkv {args.input_file}"
		)

	# verify required external tools are available
	for tool in ("mediainfo", "ffprobe", "ffmpeg"):
		if shutil.which(tool) is None:
			raise RuntimeError(f"{tool} not found in PATH")

	# prepare mode: dispatch early before config/data-path setup; it uses
	# only the original video and ffmpeg and does not need config, seeds,
	# diagnostics, or data directory.
	if args.mode == "prepare":
		# probe to provide the status summary geometry; video_info is the
		# only context prepare needs beyond the input path.
		print(f"probing video: {args.input_file}")
		video_info = _probe_video(args.input_file)
		fps = video_info["fps"]
		print(f"  resolution: {video_info['width']}x{video_info['height']}")
		print(f"  fps:        {fps:.4f}")
		print(f"  frames:     {video_info['frame_count']}")
		print(f"  duration:   {video_info['duration_s']:.2f}s")
		_mode_prepare(args, video_info)
		t_total_elapsed = time.time() - t_total_start
		print(f"total time: {t_total_elapsed:.1f}s")
		return

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
	# resolve_config centralizes the per-video/default branch (and the
	# legacy-key auto-migration) shared with the blob_walk_v2 walk driver;
	# config_path honors any --config-file override resolved above.
	cfg, had_config_file = tr_config.resolve_config(
		args.input_file, config_path=config_path,
	)
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

	# Resolve original-vs-fast-read routing ONCE for this run (prepare
	# already early-returned above; it is its own creator). A valid context
	# is the authorization for working-mode FrameReaders to decode from
	# working_decode.path. A present-but-invalid fast-read raises here with
	# the remedy. Modes receive this context and never re-run discovery.
	video_context = fastread_video.resolve_video_context(args.input_file)

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
		_mode_seed(args, cfg, video_info, seeds_path, video_context)
	elif mode == "edit":
		_mode_edit(
			args, cfg, video_info, seeds_path, diag_path, intervals_path,
			video_context,
		)
	elif mode == "target":
		_mode_target(
			args, cfg, video_info, seeds_path, diag_path, intervals_path,
			video_context,
		)
	elif mode == "solve":
		_mode_solve(
			args, cfg, video_info, seeds_path, diag_path, intervals_path,
			video_context,
		)
	elif mode == "refine":
		_mode_refine(
			args, cfg, video_info, seeds_path, diag_path, intervals_path,
			video_context,
		)
	elif mode == "setup":
		_mode_setup(args, cfg, video_info, config_path, video_context)
	elif mode == "encode":
		_mode_encode(
			args, cfg, video_info, diag_path, intervals_path, video_context,
		)
	elif mode == "analyze":
		_mode_analyze(
			args, cfg, video_info, diag_path, intervals_path, video_context,
		)
		# --seed shortcut: hand off to target's --from-analyze path so
		# the user goes from analyze report -> seeding UI in one command.
		# `-t N` and `-g N` also trigger this path -- supplying either
		# is a clear signal the user wants to act on the targets.
		seed_requested = (
			getattr(args, "analyze_seed", False)
			or getattr(args, "top_n", None) is not None
			or getattr(args, "gap_top_n", None) is not None
		)
		if seed_requested:
			if not had_config_file:
				raise RuntimeError(
					f"--seed requires a per-video config at {config_path}; "
					f"run 'setup' mode first"
				)
			args.target_from_analyze = True
			args.target_race_start = False
			# target mode reads optional fields the analyze parser does not
			# define; supply safe defaults so getattr() lookups inside
			# _mode_target succeed.
			for field, default in (
				("severity", None),
				("seed_interval", 10.0),
				("top_n", None),
				("start_time", None),
			):
				if not hasattr(args, field):
					setattr(args, field, default)
			_mode_target(
				args, cfg, video_info, seeds_path, diag_path, intervals_path,
				video_context,
			)
	else:
		raise RuntimeError(f"unknown mode: {mode}")

	# print total elapsed time
	t_total_elapsed = time.time() - t_total_start
	print(f"total time: {t_total_elapsed:.1f}s")

	# per-process memory profile -- gated by the module-level PROFILE_MEM
	# constant (off by default; flip in source when diagnosing leaks)
	if PROFILE_MEM:
		# ru_maxrss is bytes on macOS, KB on Linux
		ru = resource.getrusage(resource.RUSAGE_SELF)
		open_fds = "n/a"
		# /dev/fd is the macOS equivalent of /proc/self/fd; counting
		# entries gives the current open-FD count without needing psutil
		fd_dir = "/dev/fd"
		if os.path.isdir(fd_dir):
			open_fds = str(len(os.listdir(fd_dir)))
		print(
			f"profile: ru_maxrss={ru.ru_maxrss} (bytes on macOS) "
			f"open_fds={open_fds}"
		)


#============================================
if __name__ == "__main__":
	main()
