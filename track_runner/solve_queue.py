"""Driver-side queue for interval solve work.

Owns the main-process orchestration that decides WHICH intervals get
queued and HOW completions are collected:

- `WorkPlan`: pure-data partition of seeds into cache-hits and pending
  intervals. Same shape whether consumed by solve mode or refine mode.
- `ExecutionContext`: immutable run configuration (reader, transforms,
  paths, worker count, debug). Keeps `execute_interval_work` callable
  with a small kwarg surface.
- `plan_interval_work`: pure function, no I/O, no printing. Consumed by
  `cli._mode_refine` for fast-exit / orphan prune and by
  `interval_solver.solve_all_intervals` for the dispatch loop.
- `execute_interval_work`: runs the in-process-vs-pool decision, the
  rich progress bar, the cache-hit fast-exit, the quit/drain path, and
  the result aggregation. Callbacks stay explicit kwargs; mutable state
  is contained in the local closure.

Import direction rule (see docs/TRACK_RUNNER_DESIGN.md and the approved
plan at ~/.claude/plans/snoopy-questing-neumann.md): this module does
NOT import `interval_solver` at module load. The only `interval_solver`
symbols the queue needs (`BlockBarColumn`, `solve_interval_analytical`)
are pulled in inside the function body of `execute_interval_work`. This
is an intentional tactical compromise; do not propagate it to new
symbols.
"""

# Standard Library
import dataclasses
import concurrent.futures

# PIP3 modules
import rich.progress

# local repo modules
import key_input
import solver_workers
import interval_fingerprint
import race_start
import race_start_contact_sheet
import tr_paths


#============================================
@dataclasses.dataclass(frozen=True)
class WorkPlan:
	"""Pure-data partition of seeds into cache-hits and pending intervals.

	Built by `plan_interval_work(seeds, prior_intervals)`. Immutable.

	Fields:
		usable_seeds_sorted: Seeds after filter + sort + dedup, the same
			list both solve and refine should iterate over.
		expected_fingerprints: Fingerprint for every interval in seed
			order. Length equals total_intervals.
		cached_results_by_idx: Per-pair_idx dict of cached result dicts
			for intervals whose fingerprint was found in prior_intervals.
			Keys are pair_idx integers; values are the result payload.
		pending_pair_indices: pair_idx values whose fingerprint was NOT
			in prior_intervals; these intervals need fresh solves.
		pruned_prior: Input prior_intervals filtered to only the keys
			present in expected_fingerprints. Callers in refine mode
			write this back to disk as the "orphan prune" operation.
			Empty input yields empty dict.
		total_intervals: len(expected_fingerprints); number of adjacent
			usable-seed pairs.
		reused_count: len(cached_results_by_idx).
		pending_count: len(pending_pair_indices).
		phase_by_idx: Dict mapping pair_idx to phase string:
			"pre_race" if end_frame <= interval_low,
			"interval" if (start_frame, end_frame) match interval endpoints,
			"post_race" otherwise. Empty when race_start_interval is None.
	"""
	usable_seeds_sorted: list
	expected_fingerprints: list
	cached_results_by_idx: dict
	pending_pair_indices: list
	pruned_prior: dict
	total_intervals: int
	reused_count: int
	pending_count: int
	phase_by_idx: dict = dataclasses.field(default_factory=dict)


#============================================
@dataclasses.dataclass
class ExecutionContext:
	"""Run configuration for `execute_interval_work`.

	Mostly-immutable: all fields except `pre_race_reference` are set at
	construction and not written again. `pre_race_reference` is populated
	mid-execution by `execute_interval_work` once Stage 2 produces the
	final race_start_frame, then read by the pre-race synthesis loop.
	No callbacks, no cross-interval accumulators, no progress-bar handles.

	Fields:
		reader: VideoReader open on the input video. In-process path
			uses this reader directly; pool path hands it to worker-side
			reopen.
		scene_transform: SceneTransform instance for pixel<->scene coord
			conversion.
		motion_track: Camera motion track used for scoring (may be None).
		all_seeds: Original seed list in pixel coords.
		all_seeds_scene: Precomputed scene-coord tuples
			(frame_index, sx, sy, sw, sh), one per seed.
		fps: Video frame rate.
		num_workers: Requested worker count. Runtime may still fall back
			to in-process if pending_count is tiny.
		video_path: Input video path; workers use it to reopen their own
			readers across the process boundary.
		video_frame_count: Total frame count from mediainfo (for correct
			frame clamping in contact sheet rendering). Must be the
			authoritative value from cli._probe_video(), not OpenCV.
		debug: Debug flag; gates per-interval debug lines.
		race_start_interval: Optional tuple (low_frame, high_frame) for
			race-start interval classification. None means no pre-race
			classification (legacy refine path).
		pre_race_reference: Dict from compute_pre_race_reference or None.
			Initially None; populated by execute_interval_work after the
			interval is solved and Stage 2 fine detection runs.
	"""
	reader: object
	scene_transform: object
	motion_track: object
	all_seeds: list
	all_seeds_scene: list
	fps: float
	num_workers: int
	video_path: str
	video_frame_count: int
	debug: bool
	race_start_interval: tuple = None
	pre_race_reference: dict = None
	bin_factor: int = 1


#============================================
def plan_interval_work(
	seeds: list,
	prior_intervals: dict = None,
	race_start_interval: tuple = None,
) -> WorkPlan:
	"""Compute the WorkPlan for a seed list + optional cache.

	Pure function: no I/O, no printing, no side-effects. Called by both
	`interval_solver.solve_all_intervals` and `cli._mode_refine` so solve
	mode and refine mode see the same partition.

	When race_start_interval is provided, intervals are classified by phase:
	pre-race if end_frame <= interval_low (inclusive, so the interval ending
	exactly matching interval endpoints, post-race otherwise. Classification
	is stored in the plan for execute_interval_work to dispatch properly.

	Args:
		seeds: Raw seed list from the seeds file.
		prior_intervals: Optional fingerprint->result dict (the
			solved_intervals cache). None or empty means every interval
			is pending.
		race_start_interval: Optional tuple (low_frame, high_frame) for
			race-start interval. When None, no phase classification happens
			(legacy refine mode without interval).

	Returns:
		A frozen `WorkPlan`. When fewer than 2 usable seeds exist, all
		counts are zero and every list/dict is empty.
	"""
	# quiet seed filter -- refine mode relies on plan_interval_work
	# staying silent so the fast-exit path produces no stdout. Solver
	# mode prints its own banners at the call site.
	usable_sorted = interval_fingerprint.filter_usable_seeds_sorted(
		seeds, verbose=False,
	)
	# degenerate case: fewer than 2 usable seeds -> no intervals at all.
	if len(usable_sorted) < 2:
		empty_plan = WorkPlan(
			usable_seeds_sorted=usable_sorted,
			expected_fingerprints=[],
			cached_results_by_idx={},
			pending_pair_indices=[],
			pruned_prior={},
			total_intervals=0,
			reused_count=0,
			pending_count=0,
			phase_by_idx={},
		)
		return empty_plan
	# one fingerprint walk: feeds the cache partition AND the orphan
	# prune in one pass so refine and solve stay in lock-step.
	total_intervals = len(usable_sorted) - 1
	expected_fingerprints = []
	cached_results_by_idx = {}
	pending_pair_indices = []
	phase_by_idx = {}
	interval_low, interval_high = (race_start_interval if race_start_interval else
		(None, None))
	for pair_idx in range(total_intervals):
		seed_start = usable_sorted[pair_idx]
		seed_end = usable_sorted[pair_idx + 1]
		fingerprint = interval_fingerprint.compute_interval_fingerprint(
			seed_start, seed_end,
		)
		expected_fingerprints.append(fingerprint)
		# cache-hit branch: remember the cached payload so execute can
		# replay callbacks for it without touching the pool.
		if prior_intervals is not None and fingerprint in prior_intervals:
			cached_results_by_idx[pair_idx] = prior_intervals[fingerprint]
		else:
			pending_pair_indices.append(pair_idx)
		# phase classification: only when interval is present.
		# end_frame is inclusive (interval_solver convention). interval_low
		# is the last pre-race seed frame, so intervals ending AT interval_low
		# are fully pre-race (all frames < final race_start_frame which lies
		# inside the interval). Use <= to include that boundary interval.
		if interval_low is not None:
			start_frame = int(seed_start["frame_index"])
			end_frame = int(seed_end["frame_index"])
			if end_frame <= interval_low:
				phase_by_idx[pair_idx] = "pre_race"
			elif start_frame == interval_low and end_frame == interval_high:
				phase_by_idx[pair_idx] = "interval"
			else:
				phase_by_idx[pair_idx] = "post_race"
	# orphan prune: keep only keys that match a current fingerprint.
	# Empty input produces empty output; we never fabricate keys.
	if prior_intervals:
		expected_set = set(expected_fingerprints)
		pruned_prior = {
			fp: v for fp, v in prior_intervals.items() if fp in expected_set
		}
	else:
		pruned_prior = {}
	plan = WorkPlan(
		usable_seeds_sorted=usable_sorted,
		expected_fingerprints=expected_fingerprints,
		cached_results_by_idx=cached_results_by_idx,
		pending_pair_indices=pending_pair_indices,
		pruned_prior=pruned_prior,
		total_intervals=total_intervals,
		reused_count=len(cached_results_by_idx),
		pending_count=len(pending_pair_indices),
		phase_by_idx=phase_by_idx,
	)
	return plan


#============================================
def _format_interval_result(result: dict, fps: float) -> str:
	"""Format a single interval result as a summary line.

	Private to the queue's per-completion output. Historical-location
	note: this helper used to live in interval_solver.py. Moved here in
	the 2026-04-17 solve_queue refactor because it is queue-dispatch
	output.

	Args:
		result: Interval result dict from `solve_interval_analytical`.
		fps: Video frame rate for duration calculation.

	Returns:
		One-line summary string.
	"""
	start_frame = result["start_frame"]
	end_frame = result["end_frame"]
	duration_s = (end_frame - start_frame) / fps
	score = result["interval_score"]
	reasons = score.get("failure_reasons", [])
	# detect v3 analytical vs v2 legacy format
	if "confidence_tier" in score:
		# v3 analytical format
		confidence = score["confidence_tier"]
		agree = score.get("agreement", 0.0)
		vel_cons = score.get("velocity_consistency", 0.0)
		size_cons = score.get("size_consistency", 0.0)
		# blob-snap acceptance fractions (FWD/BWD). None means no
		# candidate blobs were seen at all; format as n/a rather than 0%
		# so the user can tell "nothing to accept" apart from "nothing
		# was accepted".
		fwd_cov = score.get("blob_coverage_fwd")
		bwd_cov = score.get("blob_coverage_bwd")
		fwd_str = f"{fwd_cov * 100:.0f}%" if fwd_cov is not None else "n/a"
		bwd_str = f"{bwd_cov * 100:.0f}%" if bwd_cov is not None else "n/a"
		# display labels chosen to read honestly: overlap is a between-pass
		# metric (FWD vs BWD Dice), vel_smooth and size_smooth are
		# within-track smoothness metrics (not between passes), and
		# blob_accept clearly says this is blob-snap acceptance.
		metrics_str = (
			f"overlap={agree:.2f}  "
			f"vel_smooth={vel_cons:.2f}  "
			f"size_smooth={size_cons:.2f}  "
			f"blob_accept={fwd_str}/{bwd_str}"
		)
	else:
		# v2 legacy format
		confidence = score.get("confidence", "low")
		agree = score.get("agreement_score", 0.0)
		margin = score.get("competitor_margin", 0.0)
		identity = score.get("identity_score", 0.0)
		metrics_str = (
			f"agree={agree:.2f}  "
			f"margin={margin:.2f}  "
			f"identity={identity:.2f}"
		)
	_confidence_labels = {
		"high": "TRUST", "good": "GOOD", "fair": "FAIR", "low": "WEAK",
		"pre_race": "PRE-RACE",
	}
	tag = _confidence_labels.get(confidence, "WEAK")
	if confidence in ("high", "good"):
		label = f"[{tag}]"
	else:
		reason_str = ", ".join(reasons) if reasons else "low_confidence"
		label = f"[{tag}: {reason_str}]"
	line = (
		f"  interval {start_frame:5d}-{end_frame:5d} "
		f"({duration_s:.1f}s)  "
		f"{metrics_str}  {label}"
	)
	return line


#============================================
def _print_interval_result_rich(
	result: dict,
	fps: float,
	progress: rich.progress.Progress,
) -> None:
	"""Print an interval result line through a rich.progress console.

	Going through `progress.console.print` keeps the output from
	clobbering the live progress bar.
	"""
	progress.console.print(_format_interval_result(result, fps))


#============================================
def _solve_pre_race_interval(
	seed_start: dict,
	seed_end: dict,
	pre_race_reference: dict,
	scene_transform,
	fps: float,
) -> dict:
	"""Synthesize a pre-race interval result from the reference.

	Computes frames [seed_start.frame_index, seed_end.frame_index] using
	the averaged torso dimensions and scene-anchored center from
	pre_race_reference. No FWD/BWD propagation runs.

	Args:
		seed_start: Start seed dict.
		seed_end: End seed dict.
		pre_race_reference: Dict from compute_pre_race_reference.
		scene_transform: SceneTransform for pixel-space back-projection.
		fps: Video frame rate.

	Returns:
		Interval result dict matching the analytical shape per plan §5b:
		start_frame, end_frame, blended_path, interval_score, fingerprint, source.
	"""
	import interval_fingerprint as ifp

	start_frame = int(seed_start["frame_index"])
	end_frame = int(seed_end["frame_index"])
	scene_anchor_x = pre_race_reference["scene_anchor_x"]
	scene_anchor_y = pre_race_reference["scene_anchor_y"]
	torso_w = pre_race_reference["torso_w"]
	torso_h = pre_race_reference["torso_h"]

	# Build blended_path: one frame_state per frame in [start_frame, end_frame]
	blended_path = []
	for t in range(start_frame, end_frame + 1):
		# Back-project scene-anchored position to pixel space at this frame
		cx_t, cy_t = scene_transform.scene_to_pixel(
			t, scene_anchor_x, scene_anchor_y
		)
		frame_state = {
			"cx": cx_t,
			"cy": cy_t,
			"w": torso_w,
			"h": torso_h,
			"conf": 1.0,
			"source": "pre_race",
		}
		blended_path.append(frame_state)

	# Build interval_score with pre_race tier and consistency=1.0
	interval_score = {
		"agreement": 1.0,
		"velocity_consistency": 1.0,
		"size_consistency": 1.0,
		"motion_quality": 1.0,
		"occlusion_fraction": 0.0,
		"confidence_tier": "pre_race",
		"failure_reasons": [],
		"warning_flags": list(pre_race_reference["warnings"]),
	}

	# Compute fingerprint for cache consistency
	fingerprint = ifp.compute_interval_fingerprint(seed_start, seed_end)

	result = {
		"start_frame": start_frame,
		"end_frame": end_frame,
		"blended_path": blended_path,
		"interval_score": interval_score,
		"fingerprint": fingerprint,
		"source": "pre_race_reference",
	}
	return result


#============================================
def execute_interval_work(
	plan: WorkPlan,
	context: ExecutionContext,
	*,
	on_interval_complete: object = None,
	on_interval_solved: object = None,
	run_control: object = None,
	key_reader: object = None,
) -> list:
	"""Execute the plan: replay cache hits, dispatch pending intervals.

	Runs either in-process or through `solver_workers.make_pool` based
	on worker count and pending interval count. Fires
	`on_interval_complete` for every interval (cached + solved);
	`on_interval_solved` only for fresh solves. Returns the seed-ordered
	result list even when the pool delivers completions out of order.

	Args:
		plan: WorkPlan from `plan_interval_work`.
		context: ExecutionContext with run-invariant state.
		on_interval_complete: Optional callback(result) fired for every
			interval, in completion order for solves and in seed order
			for cache hits.
		on_interval_solved: Optional callback(fingerprint, result) fired
			exactly once per fresh solve. Never fires for cache hits.
		run_control: Optional run control object for quit handling.
		key_reader: Optional key reader for interactive quit input.

	Returns:
		List of interval result dicts in seed order. Length equals
		plan.total_intervals on the normal-completion path; may be
		shorter (gaps dropped) when the user quit mid-solve.
	"""
	# function-level import: tactical compromise to keep this module
	# free of a load-time dependency on interval_solver. BlockBarColumn
	# and solve_interval_analytical live there and cannot move in this
	# refactor. See design-philosophy scope-lock in
	# ~/.claude/plans/snoopy-questing-neumann.md.
	import interval_solver

	total_intervals = plan.total_intervals
	usable_seeds_sorted = plan.usable_seeds_sorted
	expected_fingerprints = plan.expected_fingerprints
	pending_pair_indices = plan.pending_pair_indices
	reused_count = plan.reused_count
	phase_by_idx = plan.phase_by_idx

	# pre-allocate so workers fill by index while the main process
	# preserves seed order.
	interval_results = [None] * total_intervals
	seq_frame_counter = [0]
	solved_count = 0

	# Find the race-start interval (phase="interval") -- this is the
	# seed pair that spans race_start_frame. Once its result lands,
	# fire pre-race synthesis early rather than waiting for the entire
	# pool to complete.
	race_start_pair_idx = None
	for idx, phase in phase_by_idx.items():
		if phase == "interval":
			race_start_pair_idx = idx
			break
	pre_race_fired = False

	# separate pending indices by phase. Pre-race intervals are solved
	# after the race-start interval completes, using the computed
	# pre_race_reference. Note: pending_pre_race are handled inline during
	# the result-collection loop once race_start_pair_idx fires.
	pending_normal = [idx for idx in pending_pair_indices
		if phase_by_idx.get(idx) != "pre_race"]
	pending_pre_race = [idx for idx in pending_pair_indices
		if phase_by_idx.get(idx) == "pre_race"]

	# replay cache hits: the pre-parallel serial loop fired
	# on_interval_complete uniformly after every append, and
	# test_on_interval_complete_fires_for_every_interval locks that.
	for pair_idx, cached in plan.cached_results_by_idx.items():
		interval_results[pair_idx] = cached
		if on_interval_complete is not None:
			on_interval_complete(cached)

	# decide execution mode. in-process when the user asked for one
	# worker, or when there is very little work to amortize pool setup
	# cost (spawn + imports + initializer payload copy). Note: pending_total
	# includes both normal and pre-race, but pre-race are fast and solved
	# after normal, so decision is based on normal work only.
	use_pool = context.num_workers >= 2 and len(pending_normal) >= 4

	# announce work scope after cache partition so the number reflects
	# actual solves, not total intervals. Note: pending_normal does not
	# include pre-race; those are synthesized fast after Stage 2.
	pending_normal_total = len(pending_normal)
	if pending_normal_total == 0 and len(pending_pre_race) == 0:
		print(f"  all {total_intervals} intervals cached; nothing to solve "
			f"(analytical mode)")
	elif pending_normal_total == 0 and len(pending_pre_race) > 0:
		print(f"  all normal intervals cached; {len(pending_pre_race)} pre-race "
			f"intervals will be synthesized")
	elif reused_count > 0:
		print(f"  solving {pending_normal_total} new intervals, "
			f"{reused_count} cached (analytical mode)...")
	else:
		print(f"  solving {total_intervals} intervals (analytical mode)...")

	# per-interval column legend: printed once per solve so the shorthand
	# in each subsequent result line is self-explanatory. Distinguishes
	# between-pass metrics (overlap) from within-track smoothness metrics
	# (vel_smooth, size_smooth), which the shorter labels cannot convey
	# on their own.
	if pending_normal_total > 0:
		print("  columns (all in [0,1], higher=better):")
		print("    overlap      = FWD/BWD Dice overlap (agreement between passes)")
		print("    vel_smooth   = within-track velocity smoothness")
		print("    size_smooth  = within-track box-size smoothness")
		print("    blob_accept  = fraction of frames with accepted blob snap (FWD/BWD)")

	# pure cache-hit fast-exit: no bar, no pool. Post-loop summary and
	# accounting assertion still run below.
	# Hoist progress and _accept so pre-race synthesis can use them
	# whether or not pending_normal_total > 0.
	progress = None
	task = None

	def _synthesize_pre_race() -> None:
		"""Synthesize all pending pre-race intervals.

		Fires once per execute_interval_work call. Called from two sites:
		1. _accept callback when race-start interval completes (newly solved).
		2. Cached-race-start detection at top of pool dispatch (cached hit).

		The nonlocal pre_race_fired flag prevents double-execution.
		"""
		nonlocal pre_race_fired
		if (pre_race_fired or race_start_pair_idx is None or
			len(pending_pre_race) == 0):
			return
		pre_race_fired = True

		interval_low, interval_high = context.race_start_interval
		final_race_start_frame = race_start.pick_race_start_frame_midpoint(
			interval_low, interval_high,
		)

		# Reuse precomputed pre_race_reference if available (from F2 pass);
		# otherwise compute it now.
		if context.pre_race_reference is not None:
			pre_race_reference = context.pre_race_reference
		else:
			pre_race_reference = race_start.compute_pre_race_reference(
				context.all_seeds, final_race_start_frame, context.scene_transform,
				race_start_interval=context.race_start_interval
			)
			context.pre_race_reference = pre_race_reference

		# Render the race-start confirmation contact sheet -- a required
		# artifact whenever pre-race intervals are synthesized. It is the
		# user-facing visual check that the chosen race_start_frame is
		# correct, and it is the artifact `target --race-start` points
		# the user at.
		tiles = race_start.choose_race_start_confirmation_frames(
			final_race_start_frame, context.fps, context.video_frame_count
		)
		contact_sheet_path = tr_paths.default_race_start_contact_sheet_path(
			context.video_path
		)
		race_start_contact_sheet.render_race_start_contact_sheet(
			context.video_path,
			context.fps,
			context.video_frame_count,
			tiles,
			pre_race_reference,
			context.scene_transform,
			contact_sheet_path,
		)
		print(f"  race-start confirmation: {contact_sheet_path}")

		# Synthesize pre-race intervals using the reference. These run
		# in the driver process while the pool continues with remaining
		# post-race intervals.
		for pre_race_pair_idx in pending_pre_race:
			if run_control is not None and run_control.quit_requested:
				break
			if key_reader is not None and run_control is not None:
				ch = key_reader.poll()
				key_input.handle_key(ch, run_control, key_reader, progress if progress is not None else None)
			if run_control is not None and run_control.quit_requested:
				break

			seed_start = usable_seeds_sorted[pre_race_pair_idx]
			seed_end = usable_seeds_sorted[pre_race_pair_idx + 1]
			start_frame = int(seed_start["frame_index"])
			end_frame = int(seed_end["frame_index"])

			if context.debug:
				console = progress.console if progress is not None else None
				if console is not None:
					console.print(f"  synthesizing pre-race interval {pre_race_pair_idx + 1}/{total_intervals} "
						f"(frames {start_frame}-{end_frame})")
				else:
					print(f"  synthesizing pre-race interval {pre_race_pair_idx + 1}/{total_intervals} "
						f"(frames {start_frame}-{end_frame})")

			result = _solve_pre_race_interval(
				seed_start, seed_end, pre_race_reference,
				context.scene_transform, context.fps
			)
			_accept(pre_race_pair_idx, expected_fingerprints[pre_race_pair_idx], result)

	def _accept(pair_idx: int, fingerprint: str, result: dict) -> None:
		nonlocal solved_count
		interval_results[pair_idx] = result
		solved_count += 1
		if on_interval_solved is not None:
			on_interval_solved(fingerprint, result)
		# always print per-completion summary so long runs have
		# visible progress beyond the bar. goes through the
		# rich console so the bar rendering is preserved.
		if progress is not None:
			_print_interval_result_rich(result, context.fps, progress)
		# on_interval_complete fires in completion order, not
		# seed order. Consumers needing seed order read from
		# interval_results after the call returns.
		if on_interval_complete is not None:
			on_interval_complete(result)
		n_frames = result["end_frame"] - result["start_frame"]
		seq_frame_counter[0] += n_frames
		if progress is not None and task is not None:
			progress.update(task, advance=1)

		# M4 early pre-race fast-path: fire pre-race synthesis as soon as
		# the race-start interval result lands (phase="interval"), not after
		# the entire pool completes. This runs in the driver process, avoiding
		# worker-pool race conditions (per R4 risk mitigation).
		if race_start_pair_idx is not None and pair_idx == race_start_pair_idx:
			_synthesize_pre_race()

	# Edge case: race-start interval was cached. If we have pending pre-race
	# intervals to synthesize, fire the fast-path now before the pool starts.
	# The cached result is already in interval_results via the cache replay
	# loop above, but _accept was never called, so _synthesize_pre_race must
	# be triggered manually here. The nonlocal pre_race_fired flag ensures
	# we do not synthesize twice if somehow the race-start is both cached
	# and (incorrectly) dispatched to the pool.
	if (race_start_pair_idx is not None and
		race_start_pair_idx in plan.cached_results_by_idx):
		_synthesize_pre_race()

	if pending_normal_total > 0:
		# frame-weighted ETA: sum span across cache-miss normal intervals.
		# pre-race intervals are fast and do not contribute significantly to ETA.
		total_frames = 0
		for pair_idx in pending_normal:
			seed_start = usable_seeds_sorted[pair_idx]
			seed_end = usable_seeds_sorted[pair_idx + 1]
			total_frames += (
				int(seed_end["frame_index"]) - int(seed_start["frame_index"])
			)
		if total_frames > 0:
			eta_column = interval_solver.FrameETAColumn(
				seq_frame_counter, total_frames,
			)
		else:
			eta_column = None
		with interval_solver.make_solve_progress(eta_column) as progress:
			# bar is sized to cache-miss work only. the cache-hit count
			# was already printed by cli (refine: N of M need solving).
			if reused_count > 0:
				desc = f"  solving {pending_normal_total} new intervals"
			else:
				desc = "  solving intervals"
			task = progress.add_task(desc, total=pending_normal_total)

			if not use_pool:
				# in-process path: identical to the pre-parallel loop.
				# Used for num_workers == 1 or for tiny runs where pool
				# setup would dominate. Solves only normal (non-pre-race)
				# intervals here; pre-race is handled after bracket completes.
				for pair_idx in pending_normal:
					if run_control is not None and run_control.quit_requested:
						break
					if key_reader is not None and run_control is not None:
						ch = key_reader.poll()
						key_input.handle_key(ch, run_control, key_reader, progress)
					if run_control is not None and run_control.quit_requested:
						break
					seed_start = usable_seeds_sorted[pair_idx]
					seed_end = usable_seeds_sorted[pair_idx + 1]
					start_frame = int(seed_start["frame_index"])
					end_frame = int(seed_end["frame_index"])
					if context.debug:
						progress.console.print(
							f"  solving interval {pair_idx + 1}/{total_intervals} "
							f"(frames {start_frame}-{end_frame})"
						)
					result = interval_solver.solve_interval_analytical(
						seed_start, seed_end, context.scene_transform,
						context.all_seeds_scene, context.fps,
						blob_snap_enabled=False,
						debug=context.debug,
						motion_track=context.motion_track,
						all_seeds=context.all_seeds,
						reader=context.reader,
					)
					_accept(pair_idx, expected_fingerprints[pair_idx], result)
			else:
				# pool path: dispatch cache-miss normal intervals to workers,
				# poll the key reader between completions, aggregate
				# results by pair_idx (completion order != seed order).
				# Pre-race intervals are handled separately after this completes.
				with solver_workers.make_pool(
					num_workers=context.num_workers,
					video_path=context.video_path,
					scene_transform=context.scene_transform,
					motion_track=context.motion_track,
					all_seeds_scene=context.all_seeds_scene,
					all_seeds=context.all_seeds,
					fps=context.fps,
					debug=context.debug,
					bin_factor=context.bin_factor,
					total_frames=context.video_frame_count,
				) as pool:
					future_to_idx = {}
					for pair_idx in pending_normal:
						seed_start = usable_seeds_sorted[pair_idx]
						seed_end = usable_seeds_sorted[pair_idx + 1]
						fut = pool.submit(
							solver_workers._solve_interval_worker,
							(pair_idx, seed_start, seed_end, False),
						)
						future_to_idx[fut] = pair_idx

					pending_futures = set(future_to_idx.keys())
					while pending_futures:
						if run_control is not None and run_control.quit_requested:
							break
						if key_reader is not None and run_control is not None:
							ch = key_reader.poll()
							key_input.handle_key(
								ch, run_control, key_reader, progress,
							)
						if run_control is not None and run_control.quit_requested:
							break
						# short wait lets us poll the key reader at ~4Hz
						done, pending_futures = concurrent.futures.wait(
							pending_futures,
							timeout=0.25,
							return_when=concurrent.futures.FIRST_COMPLETED,
						)
						for fut in done:
							pair_idx, fingerprint, result = fut.result()
							_accept(pair_idx, fingerprint, result)

					# on quit, cancel anything not yet running and drain
					# in-flight work so the main process still sees
					# completions (and on_interval_solved fires).
					if run_control is not None and run_control.quit_requested:
						for fut in list(pending_futures):
							fut.cancel()
						for fut in concurrent.futures.as_completed(pending_futures):
							if fut.cancelled():
								continue
							pair_idx, fingerprint, result = fut.result()
							_accept(pair_idx, fingerprint, result)

	# ============ Pre-race synthesis was moved earlier ============
	# M4 change: pre-race synthesis now fires as soon as the race-start
	# interval result lands (in _accept, phase="interval"), not after the
	# entire pool completes. The old post-pool code path has been removed.
	# This runs in the driver process, avoiding worker-pool race conditions.

	# Verify accounting only when not interrupted by quit
	if run_control is None or not run_control.quit_requested:
		assert reused_count + solved_count == total_intervals, (
			f"reuse/solve accounting drifted: "
			f"reused={reused_count} solved={solved_count} "
			f"total={total_intervals}"
		)

	# post-loop summary: make cache behavior visible on stdout
	print(f"  solver: reused {reused_count}, solved {solved_count} "
		f"of {total_intervals}")
	# visibility warning if we had a non-empty cache but matched
	# nothing -- likely a fingerprint drift from seed filter or
	# rounding changes. Only surfaces when pruned_prior is non-empty
	# (we actually had something to reuse) yet nothing was reused.
	if plan.pruned_prior and reused_count == 0 and solved_count > 0:
		print(f"  solver: WARNING 0 of {len(plan.pruned_prior)} prior intervals "
			f"reused -- possible fingerprint mismatch "
			f"(check seed filter / rounding)")

	if run_control is not None and run_control.quit_requested:
		done = len([r for r in interval_results if r is not None])
		key_input._quit_trace(
			"FUNCTION_RETURN", context="solve_all_intervals_analytical",
			quit_requested=True, intervals_done=done,
			intervals_total=total_intervals,
		)
		print(f"  quit requested: {done}/{total_intervals} intervals completed")
		print("  hint: re-run 'solve' to resume")

	# drop any None holes left by a quit so the downstream stitcher
	# gets only real results. in the normal-completion path the
	# accounting assertion above already proved there are no holes.
	filled_results = [r for r in interval_results if r is not None]
	return filled_results
