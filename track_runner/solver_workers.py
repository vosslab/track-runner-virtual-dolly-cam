"""Per-interval parallel solver execution.

The analytical solver in `interval_solver.solve_interval_analytical` is a
pure function of its inputs: two seeds, a scene transform, a motion track,
the seed list, and a video reader for ROI reads. Intervals have no
cross-talk (each interval is an independent per-process solve), so they
are safe to solve concurrently in worker processes.

This module owns the execution side:

- `WorkerContext`: a frozen dataclass holding the run-invariant objects
  (scene_transform, motion_track, full seed lists, VideoReader). One
  instance is built per worker process by `_worker_init` and reused for
  every interval that worker handles. The context is assigned once per
  process and treated as read-only thereafter, so no mutable state
  accumulates across interval solves (contract C3 conformance margin).
- `_solve_interval_worker`: the function `ProcessPoolExecutor` calls.
  Takes a tiny task tuple, returns (pair_idx, fingerprint, result).
- `make_pool`: builds a ProcessPoolExecutor with `_worker_init` wired up.

Architecture boundary: workers own their VideoReader and compute; the
main process owns cache lookup, dispatch, result aggregation, progress,
persistence, and quit handling. No worker writes to stdout or disk.
"""

# Standard Library
import atexit
import dataclasses
import concurrent.futures

# local repo modules
import interval_solver
import common_tools.frame_reader
import blob_walk.walk_viterbi as walk_viterbi
# residual_motion is imported lazily inside _worker_init and _worker_atexit
# to avoid a circular import (track_runner.py -> cli -> ... -> solver_workers
# -> track_runner.residual_motion -> track_runner -> circular). The lazy
# import is safe because those functions run in worker processes where the
# import chain is clean, or after the main process finishes initialization.


#============================================
@dataclasses.dataclass(frozen=True)
class WorkerContext:
	"""Run-invariant state for a single worker process.

	Constructed once per worker by `_worker_init` and thereafter treated
	as read-only. Frozen so accidental field mutation raises rather than
	silently accumulating across interval solves.
	"""
	reader: object
	scene_transform: object
	motion_track: object
	all_seeds_scene: list
	all_seeds: list
	fps: float
	debug: bool
	blob_pass: bool
	# Config-driven Viterbi cost weights for the Stage-4 walker pass. None means
	# "use the walk_viterbi module-constant defaults" (the Stage-3 pure-Hermite
	# path never runs the walker, so it ships None).
	walker_costs: dict | None = None


#============================================
# Holds the current worker's WorkerContext once `_worker_init` runs. A
# worker writes this exactly once at process startup and reads it on
# every subsequent task; the object itself is frozen, so interval solves
# cannot mutate it.
_WORKER_CONTEXT: WorkerContext | None = None


#============================================
def _worker_init(
	decode_video_path: str,
	scene_transform: object,
	motion_track: object,
	all_seeds_scene: list,
	all_seeds: list,
	fps: float,
	debug: bool,
	blob_pass: bool,
	bin_factor: int = 1,
	total_frames: int = 0,
	walker_costs: dict | None = None,
) -> None:
	"""Initialize per-process solver state for a pool worker.

	Runs exactly once per child process when the ProcessPoolExecutor
	starts the worker. Opens a dedicated VideoReader on the video path
	and builds the frozen WorkerContext consumed by every task.

	Args:
		decode_video_path: Path to the decode video reopened in this
			process (the resolved working-decode video: fast-read when
			valid, else original). The main process already validated the
			fast-read once; workers do not re-validate.
		scene_transform: SceneTransform instance.
		motion_track: Motion track object for scoring.
		all_seeds_scene: Precomputed list of all seeds in scene coords.
		all_seeds: Original seed list (pixel coords).
		fps: Video frame rate.
		debug: Debug flag; constant across all tasks in this run.
		blob_pass: Run-invariant blob-pass flag; False for Stage-3 (pure
			Hermite), True for the Stage-4 walker pass. Constant across all
			tasks in this run.
		walker_costs: Optional config-driven Viterbi cost weights. When
			provided, installed once here via walk_viterbi.set_cost_weights so
			every interval this worker solves uses the config weights. None
			keeps the walk_viterbi module-constant defaults.
	"""
	global _WORKER_CONTEXT
	# Install config cost weights once at process startup. Write-once is safe:
	# make_pool sets max_tasks_per_child=1, so every interval runs in a fresh
	# process (same lifecycle as the _WORKER_CONTEXT assignment below).
	if walker_costs is not None:
		walk_viterbi.set_cost_weights(walker_costs)
	# reopen the video in this process; the main process's reader cannot
	# cross the fork/spawn boundary. Always use FrameReader so every
	# worker exposes the same `.geometry` interface regardless of
	# bin_factor; bin_factor=1 short-circuits the resize and is
	# byte-identical to the legacy VideoReader path.
	reader = common_tools.frame_reader.FrameReader(
		video_path=decode_video_path,
		fps=fps,
		total_frames=total_frames,
		bin_factor=bin_factor,
	)
	_WORKER_CONTEXT = WorkerContext(
		reader=reader,
		scene_transform=scene_transform,
		motion_track=motion_track,
		all_seeds_scene=all_seeds_scene,
		all_seeds=all_seeds,
		fps=fps,
		debug=debug,
		blob_pass=blob_pass,
		walker_costs=walker_costs,
	)
	# close the reader when the worker shuts down so file handles do not
	# leak on the normal path. ProcessPoolExecutor also terminates
	# workers at pool shutdown.
	atexit.register(_worker_atexit)


#============================================
def _worker_atexit() -> None:
	"""Close the worker's reader on process exit."""
	global _WORKER_CONTEXT
	ctx = _WORKER_CONTEXT
	if ctx is not None and ctx.reader is not None:
		close = getattr(ctx.reader, "close", None)
		if close is not None:
			close()
		else:
			ctx.reader.__exit__(None, None, None)

	_WORKER_CONTEXT = None


#============================================
def _solve_interval_worker(task: tuple) -> tuple:
	"""Solve one interval inside a pool worker.

	Takes a tiny pickleable task tuple and returns the fingerprint plus
	result dict. All heavy inputs come from the frozen WorkerContext
	built once per process by `_worker_init`.

	Args:
		task: Tuple of (pair_idx, seed_start, seed_end).

	Returns:
		Tuple of (pair_idx, fingerprint, result_dict).
	"""
	pair_idx, seed_start, seed_end = task
	ctx = _WORKER_CONTEXT
	fingerprint = interval_solver.compute_interval_fingerprint(
		seed_start, seed_end,
	)
	# The worker solves with the dispatch's blob_pass: False for Stage-3 (pure
	# Hermite on every interval) and True for the Stage-4 walker pass. The flag
	# is run-invariant per dispatch, carried on the frozen WorkerContext.
	result = interval_solver.solve_interval_analytical(
		seed_start, seed_end,
		ctx.scene_transform,
		ctx.all_seeds_scene,
		ctx.fps,
		debug=ctx.debug,
		motion_track=ctx.motion_track,
		all_seeds=ctx.all_seeds,
		reader=ctx.reader,
		blob_pass=ctx.blob_pass,
	)
	return (pair_idx, fingerprint, result)


#============================================
def make_pool(
	num_workers: int,
	decode_video_path: str,
	scene_transform: object,
	motion_track: object,
	all_seeds_scene: list,
	all_seeds: list,
	fps: float,
	debug: bool,
	blob_pass: bool,
	bin_factor: int = 1,
	total_frames: int = 0,
	walker_costs: dict | None = None,
) -> concurrent.futures.ProcessPoolExecutor:
	"""Create a ProcessPoolExecutor configured with `_worker_init`.

	The heavy run-invariant objects ship once per worker through
	`initargs`, so per-task payload stays small.

	Workers are configured with `max_tasks_per_child=1`: every interval
	is solved in a fresh process that exits and is replaced after the
	task returns. This is a hard upper bound on per-worker memory
	growth -- when a process exits, the OS reclaims its entire heap,
	pymalloc arenas, numpy buffers, and OpenCV decode state. There is
	no shared mutable state across intervals to amortize, and the
	per-interval solve dwarfs the ~1-3 s spawn + import + WorkerContext
	pickle cost, so reuse buys nothing but a memory leak surface.

	Args:
		num_workers: Number of worker processes.
		decode_video_path: Path to the decode video reopened per worker (the
			resolved working-decode video: fast-read when valid, else
			original). Forwarded to `_worker_init` as `decode_video_path`.
		scene_transform: SceneTransform instance.
		motion_track: Motion track object.
		all_seeds_scene: Seeds in scene coordinates.
		all_seeds: Seeds in pixel coordinates.
		fps: Video frame rate.
		debug: Debug flag.
		blob_pass: Run-invariant blob-pass flag; False for Stage-3 (pure
			Hermite), True for the Stage-4 walker pass.
		walker_costs: Optional config-driven Viterbi cost weights shipped to
			each worker; None keeps the walk_viterbi module-constant defaults.

	Returns:
		A started ProcessPoolExecutor. Caller is responsible for using
		it as a context manager so workers shut down cleanly.
	"""
	return concurrent.futures.ProcessPoolExecutor(
		max_workers=num_workers,
		initializer=_worker_init,
		initargs=(
			decode_video_path, scene_transform, motion_track,
			all_seeds_scene, all_seeds, fps, debug, blob_pass,
			bin_factor, total_frames, walker_costs,
		),
		max_tasks_per_child=1,
	)
