"""Shared implementation helpers for track_runner CLI modes."""

import argparse
import os
import time

import camera_motion
import common_tools.frame_reader
import interval_solver
import key_input
import off_frame_geometry
import race_start
import review
import scene_coords
import solver_workers
import state_io
import torso_box_coords_io
import modes.seed_validation as seed_validation


#============================================
def build_nif_crop_inputs(
	trajectory: list,
	seeds: list,
	diagnostics: dict,
	video_info: dict,
) -> tuple:
	"""Return crop-only NIF anchors without changing trajectory truth.

	``not_in_frame`` is an authoritative human statement that the runner
	is absent. The edge-anchored boxes needed to steer the output crop are
	therefore kept in a separate list. Callers use the returned list only
	with ``tr_crop.trajectory_to_crop_rects`` and retain ``trajectory`` for
	tracking, diagnostics, and overlays.

	Args:
		trajectory: Reconstructed, seed-erased tracking states.
		seeds: Human-authored seed records.
		diagnostics: Solve diagnostics, optionally with pre-race reference.
		video_info: Source video metadata with width and height.

	Returns:
		Tuple ``(crop_trajectory, nif_frames)``. ``crop_trajectory`` has
		edge-anchor states only where NIF output geometry is required;
		``nif_frames`` identifies those crop-only states to tr_crop. The
		input ``trajectory`` is changed in place to ``None`` over those same
		indices, so it remains authoritative runner truth.
	"""
	crop_trajectory = list(trajectory)
	nif_frames = set()
	if not seeds:
		return (crop_trajectory, nif_frames)

	pre_race_ref = diagnostics.get("pre_race_reference")
	race_start_frame = 0
	if pre_race_ref is not None:
		race_start_frame = int(pre_race_ref["race_start_frame"])
	frame_size = (video_info["width"], video_info["height"])
	solved_trajectory = {}
	for frame_index, state in enumerate(trajectory):
		if state is None:
			continue
		solved_trajectory[frame_index] = {
			"cx": int(state["cx"]),
			"cy": int(state["cy"]),
			"w": int(state["w"]),
			"h": int(state["h"]),
		}

	# This seam validates unsupported pre-race NIF and derives all edge
	# anchors from human NIF seeds plus visible solved brackets.
	nif_spans = off_frame_geometry.build_nif_spans(
		seeds, solved_trajectory, frame_size, race_start_frame,
		last_frame_index=len(trajectory) - 1,
	)
	nif_frames = off_frame_geometry.erase_nif_span_truth(
		trajectory, nif_spans,
	)
	off_frame_states = off_frame_geometry.build_off_frame_states(
		nif_spans, frame_size,
	)
	for frame_index, state in off_frame_states.items():
		if 0 <= frame_index < len(crop_trajectory):
			crop_trajectory[frame_index] = state
	result = (crop_trajectory, nif_frames)
	return result


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


def _ensure_target_diagnostics(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	seeds: list,
	diag_path: str,
	intervals_path: str,
) -> dict:
	"""Return complete diagnostics usable by target mode.

	Target mode depends on interval diagnostics to rank weak spans and
	build prediction overlays. If the diagnostics file is missing or
	empty, target mode reports that solve must regenerate it first.

	`state_io.load_interval_scores` admits only complete current records. Target
	therefore never filters or partially reuses stale interval scores.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		seeds: Loaded seed list.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved torso-coordinate NPZ file.

	Returns:
		Diagnostics dict with complete current interval confidence data.
	"""
	need_solve = False
	reason = None
	if not os.path.isfile(diag_path):
		need_solve = True
		reason = "missing diagnostics"
	else:
		diag_data = state_io.load_interval_scores(diag_path)
		if diag_data.get("intervals"):
			return diag_data
		need_solve = True
		reason = "diagnostics file has no intervals"

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
def _load_prior_results(
	intervals_path: str,
	diag_path: str,
	video_identity: dict,
) -> tuple:
	"""Load previously solved intervals and build a write-through callback.

	Geometry (forward_path, backward_path, blended_path) comes from
	`torso_box_coords.npz`. Scoring (interval_score) comes from
	`interval_scores.json`. This helper merges the two so every prior
	interval returned carries both its trajectory AND its score, matching
	the shape `write_solver_interval_scores` expects when it later rewrites
	the scoring file.

	Scores are matched to intervals by (start_frame, end_frame) because
	that pair is what `interval_scores.json` stores. A prior interval
	without a matching score entry is discarded so the next solve can
	regenerate both its geometry and score together.

	Args:
		intervals_path: Path to the torso_box_coords NPZ (unified artifact).
		diag_path: Path to the interval_scores JSON.

	Returns:
		Tuple (prior_results_dict, on_interval_solved_callback).
	"""
	intervals_file = torso_box_coords_io.load_torso_box_coords(intervals_path)
	intervals_file["video_identity"] = video_identity
	intervals_file["solve_complete"] = False
	solved = intervals_file.get("solved_intervals", {})
	# merge prior interval_score back onto each geometry entry.
	# key on (start_frame, end_frame) because interval_scores.json does
	# not carry fingerprints.
	prior_scores = {}
	if os.path.isfile(diag_path):
		diag = state_io.load_interval_scores(diag_path)
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
		intervals_file["video_identity"] = video_identity
		torso_box_coords_io.write_torso_box_coords(intervals_path, intervals_file)

	return (solved, _on_interval_solved)


#============================================
def _invalidate_intervals_for_frames(
	intervals_path: str,
	changed_frames: set,
	video_identity: dict,
) -> None:
	"""Remove solved intervals that touch any of the changed seed frames.

	Each fingerprint key encodes two seed frame indices separated by pipe
	characters. An interval is invalidated if either its start or end
	frame index appears in changed_frames.

	Args:
		intervals_path: Path to the torso_box_coords NPZ file.
		changed_frames: Set of frame_index ints that were modified.
		video_identity: Identity of the source video that owns this artifact.
	"""
	intervals_file = torso_box_coords_io.load_torso_box_coords(intervals_path)
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
	intervals_file["video_identity"] = video_identity
	torso_box_coords_io.write_torso_box_coords(intervals_path, intervals_file)
	remaining = len(solved)
	print(f"  invalidated {len(keys_to_remove)} solved intervals "
		f"({remaining} remaining)")


#============================================
def _resolve_workers(
	args: argparse.Namespace, video_info: dict | None = None,
) -> int:
	"""Resolve worker count from an override or the solve memory budget.

	Args:
		args: Parsed argparse namespace.
		video_info: Source width and height from the already-completed probe.
			Solve supplies this and receives budget-based automatic selection.
			Other established pool callers retain their CPU-only default until
			they supply equivalent processed-frame geometry.

	Returns:
		Number of workers to use.
	"""
	cpu_count = os.cpu_count() or 1
	requested_workers = getattr(args, "workers", None)
	if requested_workers is not None:
		num_workers = solver_workers.select_budgeted_worker_count(
			available_bytes=0,
			parent_bytes=0,
			worker_bytes=1,
			reserve_bytes=0,
			cpu_count=cpu_count,
			requested_workers=requested_workers,
		)
		print(f"  workers: {num_workers} (explicit override; {cpu_count} CPUs)")
		return num_workers
	if video_info is None:
		num_workers = max(1, cpu_count // 2)
		print(f"  workers: {num_workers} (of {cpu_count} CPUs)")
		return num_workers
	bin_factor, _ = _resolve_solve_bin_factor(
		getattr(args, "bin_factor", None),
		getattr(args, "auto_bin_target", None),
		int(video_info["width"]), int(video_info["height"]),
	)
	# The FrameReader goodbox crop can only reduce these dimensions. Accounting
	# with post-bin source dimensions is therefore a safe upper bound before a
	# worker opens its own reader.
	processed_width = int(video_info["width"]) // bin_factor
	processed_height = int(video_info["height"]) // bin_factor
	worker_bytes = solver_workers.controlled_worker_bytes(
		processed_width, processed_height,
	)
	reserve_bytes = worker_bytes * solver_workers.MEMORY_RESERVE_WORKERS
	# MemAvailable/vm_stat already describe bytes free for new allocations while
	# this driver is resident. Add the measured driver baseline only to express
	# the policy's explicit capacity - parent - reserve arithmetic; subtracting
	# it again inside the selector returns exactly the OS-reported availability
	# and therefore does not double-count the parent on Linux.
	available_after_parent_bytes = solver_workers.available_memory_bytes()
	parent_bytes = solver_workers.current_process_rss_bytes()
	available_bytes = available_after_parent_bytes + parent_bytes
	num_workers = solver_workers.select_budgeted_worker_count(
		available_bytes=available_bytes,
		parent_bytes=parent_bytes,
		worker_bytes=worker_bytes,
		reserve_bytes=reserve_bytes,
		cpu_count=cpu_count,
	)
	print(
		f"  workers: {num_workers} (of {cpu_count} CPUs; "
		f"available_after_parent={available_after_parent_bytes} parent={parent_bytes} "
		f"worker_budget={worker_bytes} reserve={reserve_bytes})"
	)
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
		if "video_identity" in seeds_data:
			seeds_data_out["video_identity"] = seeds_data["video_identity"]
		state_io.write_seeds(seeds_path, seeds_data_out)
		print(f"  saved {len(seeds)} deduplicated seeds")
	return seeds


#============================================
def _save_seeds_to_disk(
	seeds: list,
	seeds_path: str,
	video_identity: dict,
) -> None:
	"""Write seeds list to disk with proper header and video identity.

	Args:
		seeds: List of seed dicts.
		seeds_path: Output file path.
		video_identity: Identity of the source video that owns these seeds.
	"""
	seeds_data = {
		state_io.SEEDS_HEADER_KEY: state_io.SEEDS_HEADER_VALUE,
		"seeds": seeds,
	}
	seeds_data["video_identity"] = video_identity
	state_io.write_seeds(seeds_path, seeds_data)


#============================================
def _make_save_callback(seeds_path: str, video_identity: dict) -> object:
	"""Build an incremental save callback for crash-safe seed saving.

	Args:
		seeds_path: Path to the seeds JSON file.
		video_identity: Identity of the source video that owns these seeds.

	Returns:
		Callable that accepts a seeds list and writes it to disk.
	"""
	def _save(seeds_list: list) -> None:
		"""Write seeds to disk after each new seed is collected."""
		_save_seeds_to_disk(seeds_list, seeds_path, video_identity)
	return _save
_validate_usable_seeds = seed_validation.validate_usable_seeds

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
	  - bare --auto-bin (auto_target == -1, the sentinel set by argparse
	    const=-1): route through the same width-floor selector as the
	    no-flag default so that re-solve.sh and interactive refine agree.
	  - explicit --auto-bin HEIGHT (auto_target is not None and != -1):
	    keep the existing HEIGHT-based meaning: bin = max(1, round(source_h / HEIGHT)).
	  - neither flag (both None): production default, route source WIDTH
	    through the shared floor selector at the project-wide default
	    target (TARGET_DEFAULT_WIDTH_PX; 1440p and below stay full-res).

	The bare-flag and no-flag paths both call select_default_bin_factor on
	source_width, so they always resolve the same bin. --auto-bin HEIGHT
	keys on source HEIGHT intentionally and must not be conflated with
	either default path.

	Args:
		cli_bin: Value of --bin (None when the flag was not given).
		auto_target: Value of --auto-bin HEIGHT or sentinel -1 for bare flag
			(None when not given at all).
		source_width: Raw source frame width in pixels.
		source_height: Raw source frame height in pixels.

	Returns:
		(bin_factor, info_message) where info_message is a one-line string
		to print describing the resolution, or None when nothing to print.

	Raises:
		ValueError: --bin < 1, or --auto-bin HEIGHT with HEIGHT < 1.
	"""
	if cli_bin is not None:
		# explicit --bin N (includes the --bin 1 full-res escape hatch)
		if cli_bin < 1:
			raise ValueError(f"--bin must be >= 1, got {cli_bin}")
		return (cli_bin, None)
	if auto_target is not None:
		# bare --auto-bin (sentinel -1): route through the same width-floor
		# selector so batch solve and no-flag default always agree.
		if auto_target == -1:
			bin_factor = common_tools.frame_reader.select_default_bin_factor(
				source_width
			)
			actual_width = source_width // bin_factor
			msg = (
				f"  --auto-bin (width-floor): source width {source_width}"
				f" -> bin_factor={bin_factor} (actual width {actual_width})"
			)
			return (bin_factor, msg)
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
	video_identity: dict,
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
		video_identity: Identity of the source video that owns output artifacts.
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
	prior_ivs, on_solved_cb = _load_prior_results(
		intervals_path, diag_path, video_identity,
	)

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
			video_info=video_info,
		)
	else:
		print("precomputing camera motion...")
		# Stage 1 reader honors --bin via FrameReader. bin_factor=1
		# uses the original full-resolution frames. Decodes from the routed
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
	# own process; FrameReader instances cannot cross the process boundary.
	# Workers receive the resolved decode path (no per-worker re-validation).
	solve_kwargs["decode_video_path"] = decode_video_path
	# Original video path threads through so every artifact-output-name and
	# identity decision in the solve queue keys off the original, never the
	# fast-read decode path. decode_video_path is for frame
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
		stage3_reader = common_tools.frame_reader.FrameReader(
			video_path=decode_video_path,
			fps=float(video_info["fps"]),
			total_frames=int(video_info["frame_count"]),
			bin_factor=bin_factor,
		)
		try:
			diagnostics = interval_solver.solve_all_intervals(
				stage3_reader, seeds,
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
	torso_box_coords_data = {"solved_intervals": prior_ivs}
	if rc.quit_requested:
		torso_box_coords_data["solve_complete"] = False
		print(f"  solve interrupted ({t_solve_elapsed:.1f}s)")
	else:
		torso_box_coords_data["solve_complete"] = True
		print(f"  solve complete ({t_solve_elapsed:.1f}s)")
	torso_box_coords_data["video_identity"] = video_identity
	torso_box_coords_io.write_torso_box_coords(intervals_path, torso_box_coords_data)
	print(f"  torso box coordinates written to {intervals_path}")
	# write diagnostics to disk
	diagnostics["video_identity"] = video_identity
	state_io.write_solver_interval_scores(diagnostics, diag_path, fps)
	print(f"  interval scores written to {diag_path}")
	# optional debug sidecar: per-frame agreement data for investigation.
	# derive the sidecar path by stripping the canonical interval-scores
	# suffix with an exact terminal replacement so agreement data cannot
	# overwrite the scoring file.
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
