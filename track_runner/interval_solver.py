"""Compatibility facade for per-interval track solving."""

# Standard Library
import concurrent.futures
import math
import time

# local repo modules
import interval_analytical
import interval_fingerprint
import interval_progress
import interval_seed_anchoring
import off_frame_geometry
import race_start
import solve_queue
import scoring
import velocity_model

compute_interval_fingerprint = interval_fingerprint.compute_interval_fingerprint
filter_usable_seeds_sorted = interval_fingerprint.filter_usable_seeds_sorted
FrameETAColumn = interval_progress.FrameETAColumn
make_solve_progress = interval_progress.make_solve_progress
build_canonical_blend_heat_evaluator = (
	interval_analytical.build_canonical_blend_heat_evaluator
)
blend_paths = interval_analytical.blend_paths
reconstruct_trajectory_with_confidence = (
	interval_seed_anchoring.reconstruct_trajectory_with_confidence
)
restamp_cached_interval_seed_truth = (
	interval_seed_anchoring.restamp_cached_interval_seed_truth
)
anchor_to_seeds = interval_seed_anchoring.anchor_to_seeds

#============================================
def solve_interval_analytical(
	seed_start: dict,
	seed_end: dict,
	scene_transform: object,
	all_seeds_scene: list,
	fps: float,
	debug: bool = False,
	motion_track: object = None,
	all_seeds: list | None = None,
	reader: object = None,
	blob_pass: bool = True,
	telemetry: dict | None = None,
) -> dict:
	"""Preserve the public facade while delegating to the canonical owner."""
	result = interval_analytical.solve_interval_analytical(
		seed_start, seed_end, scene_transform, all_seeds_scene, fps,
		debug=debug,
		motion_track=motion_track,
		all_seeds=all_seeds,
		reader=reader,
		blob_pass=blob_pass,
		telemetry=telemetry,
	)
	return result


#============================================
def select_promoted_intervals(
	interval_results: list,
	usable_seeds_sorted: list,
	scene_transform: object,
	fps: float,
	video_frame_count: int | None,
	race_start_frame: int,
	candidate_indices: "list | None" = None,
) -> list:
	"""Select post-race Stage-3 intervals for the bounded Stage-4 walker.

	Risk uses the scoring owner's retained predicates plus the Branch-B chord
	predicate.  Allocation is first-fit-decreasing by risk, then start frame,
	and consumes whole inclusive intervals without exceeding the per-video
	walker budget.  Pre-race intervals are never promoted (C4).

	When `candidate_indices` is supplied, only those pair_idx values are
	considered. Refine mode passes `plan.pending_pair_indices` so cached
	(reused) intervals from prior runs are not re-promoted -- contract
	C6 says refine must not touch already-solved intervals.

	Args:
		interval_results: List of Stage-3 interval result dicts.
		usable_seeds_sorted: Exact seed sequence used by the solve work plan.
		scene_transform: Converts endpoint seed boxes to scene coordinates.
		fps: Video frame rate for the retained duration predicate.
		video_frame_count: Authoritative total video frame count.
		race_start_frame: First post-race frame; zero when no pre-race phase exists.
		candidate_indices: Optional list of pair_idx values to consider. When
			None, every interval in interval_results is a candidate (solve
			mode default).

	Returns:
		List of pair_idx integers (0-based position in interval_results) for
		intervals eligible for promotion.
	"""
	risk_by_pair = promotion_risk_by_pair(
		interval_results, usable_seeds_sorted, scene_transform, fps,
		video_frame_count, candidate_indices=candidate_indices,
	)
	candidates = []
	for pair_idx, result in enumerate(interval_results):
		if pair_idx not in risk_by_pair:
			continue
		candidates.append((risk_by_pair[pair_idx], int(result["start_frame"]), pair_idx, result))

	if not candidates:
		return []
	if video_frame_count is None:
		raise RuntimeError(
			"Stage-4 promotion requires authoritative video_frame_count; "
			"run solve through the video-artifact mode"
		)
	budget = stage4_walker_frame_budget(
		interval_results, video_frame_count, race_start_frame,
	)
	candidates.sort(key=lambda item: (-item[0], item[1]))
	remaining_budget = budget
	promoted = []
	for unused_risk, unused_start_frame, pair_idx, result in candidates:
		interval_frames = _inclusive_interval_frame_count(result)
		if interval_frames <= remaining_budget:
			promoted.append(pair_idx)
			remaining_budget -= interval_frames
	return promoted


#============================================
def stage4_walker_frame_budget(
	interval_results: list,
	video_frame_count: int,
	race_start_frame: int,
) -> int:
	"""Return the approved whole-frame Stage-4 walker budget for one video.

	The measured floor counts current low/fair, non-pre-race intervals in their
	inclusive frame spans. The policy preserves at least ten percent of the
	post-race video span when that measured amount is smaller.
	"""
	post_race_frames = max(0, int(video_frame_count) - int(race_start_frame))
	measured_walker_frames = 0
	for result in interval_results:
		if result is None or result.get("source") == "pre_race_reference":
			continue
		if result["interval_score"]["confidence_tier"] in interval_analytical.PROMOTION_TIERS:
			measured_walker_frames += _inclusive_interval_frame_count(result)
	budget = max(measured_walker_frames, math.ceil(0.10 * post_race_frames))
	return budget


#============================================
def promotion_risk_by_pair(
	interval_results: list,
	usable_seeds_sorted: list,
	scene_transform: object,
	fps: float,
	video_frame_count: int | None,
	candidate_indices: "list | None" = None,
) -> dict:
	"""Return positive Stage-4 promotion risk keyed by pair index.

	This is the public, non-allocating half of the Stage-4 policy.  Review and
	target use it to display the same risk that ``select_promoted_intervals``
	packs into the walker budget.
	"""
	candidate_set = None if candidate_indices is None else set(candidate_indices)
	risk_by_pair = {}
	for pair_idx, result in enumerate(interval_results):
		if result is None:
			continue
		if candidate_set is not None and pair_idx not in candidate_set:
			continue
		if result.get("source") == "pre_race_reference":
			continue
		if not scoring.promotion_risk_inputs_available(result["interval_score"]):
			if video_frame_count is None:
				continue
			raise RuntimeError(
				"Stage-3 interval score is missing promotion-risk inputs; "
				"run solve to regenerate the score"
			)
		chord_span_widths = _endpoint_chord_span_widths(
			usable_seeds_sorted[pair_idx], usable_seeds_sorted[pair_idx + 1],
			scene_transform,
		)
		risk = scoring.compute_promotion_risk(
			result["interval_score"], result["start_frame"], result["end_frame"],
			fps, chord_span_widths=chord_span_widths,
		)
		if risk > 0.0:
			risk_by_pair[pair_idx] = risk
	return risk_by_pair


#============================================
def _inclusive_interval_frame_count(interval_result: dict) -> int:
	"""Return the whole inclusive frame span consumed by one walker interval."""
	frame_count = int(interval_result["end_frame"]) - int(interval_result["start_frame"]) + 1
	return frame_count


#============================================
def _endpoint_chord_span_widths(
	seed_start: dict,
	seed_end: dict,
	scene_transform: object,
) -> float | None:
	"""Return endpoint chord length in canonical midpoint torso widths.

	Only the two work-plan endpoints contribute.  A nonphysical width leaves
	the Branch-B predicate unavailable instead of inventing a pixel threshold.
	"""
	start_frame = int(seed_start["frame_index"])
	end_frame = int(seed_end["frame_index"])
	start_sx, start_sy, start_sw, unused_start_sh = scene_transform.pixel_box_to_scene(
		start_frame, float(seed_start["cx"]), float(seed_start["cy"]),
		float(seed_start["w"]), float(seed_start["h"]),
	)
	end_sx, end_sy, end_sw, unused_end_sh = scene_transform.pixel_box_to_scene(
		end_frame, float(seed_end["cx"]), float(seed_end["cy"]),
		float(seed_end["w"]), float(seed_end["h"]),
	)
	midpoint_width = velocity_model.interpolate_size(
		float(start_sw), float(end_sw), 0.5, velocity_model.RAW_PRED_FORWARD,
	)
	if not math.isfinite(midpoint_width) or midpoint_width <= 0.0:
		return None
	dx = float(end_sx) - float(start_sx)
	dy = float(end_sy) - float(start_sy)
	if not math.isfinite(dx) or not math.isfinite(dy):
		return None
	chord_span_widths = math.hypot(dx, dy) / midpoint_width
	if not math.isfinite(chord_span_widths):
		return None
	return chord_span_widths


#============================================
def _dispatch_blob_pass(
	stage_name: str,
	pair_indices: list,
	interval_results: list,
	seeds: list,
	context: object,
	num_workers: int,
	debug: bool,
	run_control: object,
	on_interval_solved: object,
	decode_video_path: str,
	blob_pass: bool = True,
) -> None:
	"""Dispatch a blob-coupled re-solve on selected post-race intervals.

	Shared by Stage 4 (promotion-selected) and Stage 5 (full pass) dispatch.
	Modifies interval_results in-place, overwriting each selected interval's
	Stage 3 result with a Stage 4/5 blob result. The analytical path is cheap;
	the expensive blob observer runs once per selected interval.

	Args:
		stage_name: Either "Stage 4" or "Stage 5" for log/banner purposes.
		pair_indices: List of pair_idx integers to re-solve.
		interval_results: List of interval result dicts from Stage 3, modified in-place.
		seeds: List of seed dicts sorted by frame_index.
		context: SolveContext with scene_transform, motion_track, reader, etc.
		num_workers: Number of parallel workers.
		debug: Debug flag.
		run_control: Run control for quit handling.
		on_interval_solved: Callback(fingerprint, result) for persistence.
		decode_video_path: Path to the decode video for the worker pool
			(resolved working-decode: fast-read when valid, else original).
		blob_pass: Default True. blob_pass flows to both the in-process and
			pool paths; promoted intervals run the windowed walker with a
			per-pass analytical stall fallback in either path. Passing False
			reverts a stage to the analytical re-solve.
	"""
	if not pair_indices:
		return

	stage_start = time.time()
	# context.bin_factor and context.video_frame_count are always populated
	# (set at ExecutionContext construction); direct access per do-not-hide-bugs.
	bin_factor = int(context.bin_factor)
	print(f"\n{stage_name}: blob pass ({len(pair_indices)} of "
		f"{len(interval_results)} intervals, bin={bin_factor})")

	# build work items: (pair_idx, seed_start, seed_end)
	tasks = []
	for pair_idx in pair_indices:
		result = interval_results[pair_idx]
		if result is None:
			continue
		# locate seed pair for this interval
		usable_seeds = interval_fingerprint.filter_usable_seeds_sorted(seeds)
		seed_start = usable_seeds[pair_idx]
		seed_end = usable_seeds[pair_idx + 1]
		tasks.append((pair_idx, seed_start, seed_end))

	# capture Stage 3 baseline scores before overwriting with Stage 4 results
	# so we can compute per-metric deltas in the summary line.
	baseline_by_pair = {}
	for pair_idx, _s, _e in tasks:
		stage3_result = interval_results[pair_idx]
		if stage3_result is not None and "interval_score" in stage3_result:
			baseline_by_pair[pair_idx] = stage3_result["interval_score"]
		else:
			baseline_by_pair[pair_idx] = None

	# dispatch: in-process if single worker or very few tasks,
	# pool if multiple workers and enough work to amortize pool setup.
	# The pool carries blob_pass as run-invariant worker context, so Stage-4
	# walker intervals use the pool exactly like Stage-3 analytical intervals.
	use_pool = num_workers >= 2 and len(tasks) >= 4
	worker_telemetry = None
	if use_pool:
		import solver_workers
		worker_telemetry = solver_workers.WorkerTelemetrySummary(
			driver_peak_before_pool_bytes=
				solver_workers.current_process_peak_rss_bytes(),
		)

	if len(tasks) > 0:
		# Frame-weighted ETA: blob cost is roughly proportional to interval
		# length, so summing frame spans across promoted intervals gives a
		# better remaining-time estimate than per-interval ticks. The
		# counter is a list wrapper (incremented in the dispatch loop) so
		# FrameETAColumn reads it lock-free in this single-process driver.
		total_frames_blob = sum(
			int(seed_end["frame_index"]) - int(seed_start["frame_index"])
			for _, seed_start, seed_end in tasks
		)
		blob_frame_counter = [0]
		with make_solve_progress(
			FrameETAColumn(blob_frame_counter, total_frames_blob),
		) as progress:
			task_id = progress.add_task(
				f"  {stage_name}: blob pass", total=len(tasks),
			)
			if not use_pool:
				# in-process blob pass
				for pair_idx, seed_start, seed_end in tasks:
					if run_control is not None and run_control.quit_requested:
						break
					result_blob = solve_interval_analytical(
						seed_start, seed_end, context.scene_transform,
						context.all_seeds_scene, context.fps,
						debug=debug,
						motion_track=context.motion_track,
						all_seeds=context.all_seeds,
						reader=context.reader,
						blob_pass=blob_pass,
					)
					# overwrite Stage 3 result with blob result
					fingerprint = compute_interval_fingerprint(
						seed_start, seed_end,
					)
					if on_interval_solved is not None:
						on_interval_solved(fingerprint, result_blob)
					interval_results[pair_idx] = result_blob
					progress.console.print(
						solve_queue._format_stage4_interval_result(
							result_blob, baseline_by_pair[pair_idx], context.fps,
						)
					)
					blob_frame_counter[0] += (
						int(seed_end["frame_index"])
						- int(seed_start["frame_index"])
					)
					progress.update(task_id, advance=1)
			else:
				# pool blob pass
				with solver_workers.make_pool(
					num_workers=num_workers,
					decode_video_path=decode_video_path,
					scene_transform=context.scene_transform,
					motion_track=context.motion_track,
					all_seeds_scene=context.all_seeds_scene,
					all_seeds=context.all_seeds,
					fps=context.fps,
					debug=debug,
					blob_pass=blob_pass,
					# Direct attribute access: both fields are always populated
					# at ExecutionContext construction (do-not-hide-bugs-with-defaults).
					bin_factor=context.bin_factor,
					total_frames=context.video_frame_count,
					telemetry_enabled=True,
				) as pool:
					# Frame span lookup keyed by pair_idx so the pool's
					# completion order doesn't matter for ETA accuracy.
					span_by_pair = {
						pair_idx: int(seed_end["frame_index"])
							- int(seed_start["frame_index"])
						for pair_idx, seed_start, seed_end in tasks
					}
					futures = {}
					for pair_idx, seed_start, seed_end in tasks:
						fut = pool.submit(
							solver_workers._solve_interval_worker,
							(pair_idx, seed_start, seed_end),
						)
						futures[fut] = pair_idx

					pending = set(futures.keys())
					while pending:
						if run_control is not None and run_control.quit_requested:
							break
						done, pending = concurrent.futures.wait(
							pending,
							timeout=0.25,
							return_when=concurrent.futures.FIRST_COMPLETED,
						)
						for fut in done:
							pair_idx, fingerprint, result_blob = (
								solver_workers.collect_worker_result(
									fut.result(), worker_telemetry,
								)
							)
							if on_interval_solved is not None:
								on_interval_solved(fingerprint, result_blob)
							interval_results[pair_idx] = result_blob
							progress.console.print(
								solve_queue._format_stage4_interval_result(
									result_blob, baseline_by_pair[pair_idx], context.fps,
								)
							)
							blob_frame_counter[0] += span_by_pair[pair_idx]
							progress.update(task_id, advance=1)

						# on quit, cancel pending and drain
						if run_control is not None and run_control.quit_requested:
							for fut in list(pending):
								fut.cancel()
							for fut in concurrent.futures.as_completed(pending):
								if fut.cancelled():
									continue
								pair_idx, fingerprint, result_blob = (
									solver_workers.collect_worker_result(
										fut.result(), worker_telemetry,
									)
								)
								interval_results[pair_idx] = result_blob

	if worker_telemetry is not None:
		stage_label = stage_name.split(":", 1)[0].lower().replace(" ", "_")
		print(solver_workers.format_worker_telemetry(
			stage_label, worker_telemetry,
		))

	stage_elapsed = time.time() - stage_start
	print(f"  {stage_name} complete: {len(pair_indices)} intervals, "
		f"elapsed {stage_elapsed:.1f} s")


#============================================
def solve_all_intervals(
	reader: object,
	seeds: list,
	num_workers: int = 1,
	debug: bool = False,
	on_interval_complete: object = None,
	prior_solved_intervals: dict = None,
	on_interval_solved: object = None,
	run_control: object = None,
	key_reader: object = None,
	scene_transform: object = None,
	motion_track: object = None,
	decode_video_path: str = None,
	original_video_path: str = None,
	video_frame_count: int = None,
	hermite_only: bool = False,
	full_solve: bool = False,
	upgrade: bool = False,
	race_start_interval: tuple = None,
	pre_race_reference: dict = None,
	bin_factor: int = 1,
	blob_pass: bool = True,
) -> dict:
	"""Solve all seed-to-seed intervals and stitch into a full trajectory.

	Splits the seed list into consecutive pairs, solves each interval using
	analytical velocity models in scene coordinates, stitches results, and
	returns a diagnostics-format dict with per-interval scoring and the
	full trajectory.

	Args:
		reader: FrameReader-compatible object with read_frame() and fps.
		seeds: List of seed dicts sorted by frame_index. Each seed must have
			cx, cy, w, h, frame_index keys. Non-visible seeds are skipped.
		num_workers: Number of parallel solver workers. 1 runs in-process.
			>= 2 opens a ProcessPoolExecutor and dispatches pending
			intervals across workers; completions are aggregated into
			seed order by the main process.
		decode_video_path: Path to the decode video (resolved working-decode:
			fast-read when valid, else original). Required when
			num_workers >= 2 so each worker can open its own FrameReader.
			State paths and identity continue to key off the original video.
		original_video_path: Path to the ORIGINAL video (args.input_file).
			Threaded into ExecutionContext.original_video_path so every
			artifact-output-name decision in the solve queue keys off the
			original, never the fast-read decode path (contract C13).
			Defaults to decode_video_path when omitted so non-routed
			diagnostic callers that pass a single path behave as before.
		debug: If True, show per-frame debug output and progress bars.
		on_interval_complete: Optional callback called with each interval result
			dict as intervals finish. Used for interactive seed requesting.
		prior_solved_intervals: Optional dict of fingerprint->result for reusing
			previously solved intervals. Keys are from state_io.interval_fingerprint().
		on_interval_solved: Optional callback(fingerprint, result) called when
			a new interval is solved, for persisting to the solved-intervals file.
		scene_transform: SceneTransform instance for coordinate conversion.
		motion_track: Optional motion track for scoring.
		run_control: Optional run control object for quit handling.
		key_reader: Optional key reader for interactive quit.
		video_frame_count: Total frame count from mediainfo (required for
			contact sheet rendering and frame clamping). Must be the
			authoritative value from modes.video_artifacts.probe_video(), not OpenCV.
		hermite_only: If True, stop after the linear/log-linear Stage 3; skip Stage 4
			and Stage 5. Used for fast diagnostics.
		full_solve: If True, run Stage 5 (blob pass on every post-race interval).
			Default False runs Stages 1-4 (blob only on promoted intervals).
		blob_pass: Default True (on). The Stage 4 promotion pass (and the
			Stage 5 full pass under --full) runs the windowed walker on its
			selected intervals, with a per-pass analytical fallback when a walk
			stalls (zero accepted frames -> that pass uses its analytical path).
			Forces in-process dispatch for the affected stage (the blob_pass
			flag is not carried across the worker-pool boundary). Stage 3 stays
			analytical on every interval. Passing False reverts the blob pass
			to the analytical re-solve.
	Returns:
		Dict with keys:
			- "intervals": list of interval result dicts
			- "trajectory": full frame-by-frame tracking state list
	"""
	# Read fps from the reader protocol. FrameReader and test readers provide it.
	fps = float(getattr(reader, "fps", 30.0))

	# Stage 2 race-start identification now happens in modes.shared._run_solve before
	# this call. The result is passed in via the race_start_interval kwarg.
	# Refine mode and any other caller that does not pre-stage the lookup
	# may pass None here; we fall back to running the locator inline so the
	# function remains usable standalone.
	if race_start_interval is None:
		race_start_interval = race_start.locate_race_start_interval(
			seeds, scene_transform, fps
		)

	# plan_interval_work is the single source of truth for seed filter +
	# fingerprint computation + prior-solved partition. refine mode also calls
	# it, so solve and refine agree on every fingerprint byte-for-byte.
	# Pass race_start_interval for classification.
	# The unified fingerprint encodes seed geometry plus the current schema;
	# how an interval was solved (analytical vs blob) is metadata
	# on the result, not part of the fingerprint.
	# A cache hit can predate the C3 endpoint stamp even though its fingerprint
	# still matches the current human seeds. Repair those paths before the queue
	# replays them to callbacks or stitches them into the production trajectory.
	if prior_solved_intervals:
		restamp_cached_interval_seed_truth(prior_solved_intervals, seeds)
	plan = solve_queue.plan_interval_work(
		seeds, prior_solved_intervals, race_start_interval=race_start_interval,
	)
	if plan.total_intervals == 0:
		print("  interval_solver: need at least 2 usable seeds to solve intervals")
		return {"intervals": [], "trajectory": []}

	# Derive scene boxes from the exact filter/sort/dedup seed set that the
	# WorkPlan dispatches.  In particular, duplicate frame indices retain the
	# planner's latest-pass winner rather than ambiguous seed geometry.
	all_seeds_scene = []
	for seed in plan.usable_seeds_sorted:
		frame_index = int(seed["frame_index"])
		cx = float(seed["cx"])
		cy = float(seed["cy"])
		w = float(seed["w"])
		h = float(seed["h"])
		sx, sy, sw, sh = scene_transform.pixel_box_to_scene(
			frame_index, cx, cy, w, h,
		)
		all_seeds_scene.append((frame_index, sx, sy, sw, sh))

	# execute_interval_work owns the in-process-vs-pool decision, the
	# progress bar, the per-completion callback fire-order, and the
	# quit/drain path. It returns a seed-ordered list with quit holes
	# already dropped on the interrupted path.
	# Artifact-name path keys off the original video. Diagnostic callers that
	# omit original_video_path get the decode path, preserving single-path
	# behavior; routed production callers (modes.shared._run_solve) pass the original.
	if original_video_path is None:
		original_video_path = decode_video_path
	context = solve_queue.ExecutionContext(
		reader=reader,
		scene_transform=scene_transform,
		motion_track=motion_track,
		all_seeds=seeds,
		all_seeds_scene=all_seeds_scene,
		fps=fps,
		num_workers=num_workers,
		decode_video_path=decode_video_path,
		original_video_path=original_video_path,
		video_frame_count=video_frame_count,
		debug=debug,
		race_start_interval=race_start_interval,
		pre_race_reference=pre_race_reference,
		bin_factor=bin_factor,
	)
	interval_results = solve_queue.execute_interval_work(
		plan, context,
		on_interval_complete=on_interval_complete,
		on_interval_solved=on_interval_solved,
		run_control=run_control,
		key_reader=key_reader,
	)

	# Stage 4 and Stage 5: re-solve pass over selected intervals.
	# Stage 4 (default): promotion pass on low/fair confidence intervals.
	# Stage 5 (--full): pass on every post-race interval.
	# By default these run the windowed walker (blob_pass=True); passing
	# blob_pass=False reverts a stage to the analytical re-solve.
	# Results overwrite the Stage 3 entries in interval_results.

	stage_4_promoted_count = 0
	if not hermite_only:
		if full_solve:
			# Stage 5: blob pass on every post-race interval, restricted
			# to freshly solved intervals per contract C6 (refine must
			# not re-touch already-solved intervals). In a clean solve,
			# plan.pending_pair_indices covers every post-race interval,
			# so behavior is unchanged. In refine, it is the small set
			# refine actually solved. `--upgrade` bypasses the filter on
			# purpose -- the point of upgrade is to re-blob the entire
			# cached set after an analytical-only batch.
			candidate_indices = (
				None if upgrade else set(plan.pending_pair_indices)
			)
			stage_5_indices = []
			for pair_idx, result in enumerate(interval_results):
				if result is None:
					continue
				# Skip pre-race intervals per contract C4
				if result.get("source") == "pre_race_reference":
					continue
				if candidate_indices is not None and pair_idx not in candidate_indices:
					continue
				stage_5_indices.append(pair_idx)
			_dispatch_blob_pass(
				"Stage 5: full blob pass",
				stage_5_indices,
				interval_results,
				seeds,
				context,
				num_workers,
				debug,
				run_control,
				on_interval_solved,
				decode_video_path,
				blob_pass=blob_pass,
			)
		else:
			# Stage 4 (default): blob promotion pass on promoted intervals only.
			# Restrict to intervals that were freshly solved this run (per
			# contract C6: refine must not re-touch already-solved intervals).
			# In a clean solve, plan.pending_pair_indices == every interval,
			# so behavior is unchanged for the no-cache path.
			# `--upgrade` mode: bypass the freshly-solved restriction and
			# consider every cached interval -- the whole point of upgrade
			# is to promote weak intervals from an analytical-only batch.
			race_start_frame = 0
			if context.pre_race_reference is not None:
				race_start_frame = int(context.pre_race_reference["race_start_frame"])
			candidate_indices = None if upgrade else plan.pending_pair_indices
			promoted_indices = select_promoted_intervals(
				interval_results,
				plan.usable_seeds_sorted,
				context.scene_transform,
				context.fps,
				context.video_frame_count,
				race_start_frame,
				candidate_indices=candidate_indices,
			)
			stage_4_promoted_count = len(promoted_indices)
			_dispatch_blob_pass(
				"Stage 4: blob promotion pass",
				promoted_indices,
				interval_results,
				seeds,
				context,
				num_workers,
				debug,
				run_control,
				on_interval_solved,
				decode_video_path,
				blob_pass=blob_pass,
			)

	# stitch and finalize
	trajectory = reconstruct_trajectory_with_confidence(interval_results)
	trajectory = anchor_to_seeds(trajectory, seeds)
	trajectory = interval_seed_anchoring.stamp_seed_truth(trajectory, seeds)
	# NIF span membership is the authoritative absence boundary for the
	# returned solver trajectory.
	race_start_frame = 0
	if context.pre_race_reference is not None:
		race_start_frame = int(context.pre_race_reference["race_start_frame"])
	# Span membership does not depend on exit side. Standalone solver tests and
	# diagnostic readers may not expose dimensions, so use a nominal size there;
	# Analyze/Encode later derive their crop anchors from authoritative metadata.
	if hasattr(reader, "width") and hasattr(reader, "height"):
		frame_size = (int(reader.width) * bin_factor, int(reader.height) * bin_factor)
	else:
		frame_size = (1, 1)
	solved_trajectory = {}
	for frame_index, state in enumerate(trajectory):
		if state is None:
			continue
		solved_trajectory[frame_index] = {
			"cx": int(state["cx"]), "cy": int(state["cy"]),
			"w": int(state["w"]), "h": int(state["h"]),
		}
	nif_spans = off_frame_geometry.build_nif_spans(
		seeds, solved_trajectory, frame_size, race_start_frame,
		last_frame_index=len(trajectory) - 1,
	)
	off_frame_geometry.erase_nif_span_truth(trajectory, nif_spans)

	# Surface race-start detection so the user can sanity-check the
	# pre-race reference (contract C2) without opening diagnostics files.
	pre_race_reference = context.pre_race_reference
	race_start.print_race_phase_summary(pre_race_reference, fps=fps, seeds=seeds)

	output = {
		"intervals": interval_results,
		"trajectory": trajectory,
		"pre_race_reference": pre_race_reference,
		"stage_4_promoted_count": stage_4_promoted_count,
	}
	return output
