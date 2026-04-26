"""Per-interval parallel solver execution.

The analytical solver in `interval_solver.solve_interval_analytical` is a
pure function of its inputs: two seeds, a scene transform, a motion track,
the seed list, and a video reader for ROI reads. Intervals have no
cross-talk (per 2026-04-17 blob-snap rework), so they are safe to solve
concurrently in worker processes.

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
import video_io
import interval_solver


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


#============================================
# Holds the current worker's WorkerContext once `_worker_init` runs. A
# worker writes this exactly once at process startup and reads it on
# every subsequent task; the object itself is frozen, so interval solves
# cannot mutate it.
_WORKER_CONTEXT: WorkerContext | None = None


#============================================
def _worker_init(
	video_path: str,
	scene_transform: object,
	motion_track: object,
	all_seeds_scene: list,
	all_seeds: list,
	fps: float,
	debug: bool,
) -> None:
	"""Initialize per-process solver state for a pool worker.

	Runs exactly once per child process when the ProcessPoolExecutor
	starts the worker. Opens a dedicated VideoReader on the video path
	and builds the frozen WorkerContext consumed by every task.

	Args:
		video_path: Path to the video file (reopened in this process).
		scene_transform: SceneTransform instance.
		motion_track: Motion track object for scoring.
		all_seeds_scene: Precomputed list of all seeds in scene coords.
		all_seeds: Original seed list (pixel coords).
		fps: Video frame rate.
		debug: Debug flag; constant across all tasks in this run.
	"""
	global _WORKER_CONTEXT
	# reopen the video in this process; the main process's reader cannot
	# cross the fork/spawn boundary.
	reader = video_io.VideoReader(video_path)
	reader.__enter__()
	_WORKER_CONTEXT = WorkerContext(
		reader=reader,
		scene_transform=scene_transform,
		motion_track=motion_track,
		all_seeds_scene=all_seeds_scene,
		all_seeds=all_seeds,
		fps=fps,
		debug=debug,
	)
	# close the reader when the worker shuts down so file handles do not
	# leak on the normal path. ProcessPoolExecutor also terminates
	# workers at pool shutdown.
	atexit.register(_worker_atexit)


#============================================
def _worker_atexit() -> None:
	"""Close the worker's VideoReader on process exit."""
	global _WORKER_CONTEXT
	ctx = _WORKER_CONTEXT
	if ctx is not None and ctx.reader is not None:
		ctx.reader.__exit__(None, None, None)
		_WORKER_CONTEXT = None


#============================================
def _solve_interval_worker(task: tuple) -> tuple:
	"""Solve one interval inside a pool worker.

	Takes a tiny pickleable task tuple and returns the fingerprint plus
	result dict. All heavy inputs come from the frozen WorkerContext
	built once per process by `_worker_init`.

	Args:
		task: Tuple of (pair_idx, seed_start, seed_end, blob_snap_enabled).

	Returns:
		Tuple of (pair_idx, fingerprint, result_dict).
	"""
	pair_idx, seed_start, seed_end, blob_snap_enabled = task
	ctx = _WORKER_CONTEXT
	fingerprint = interval_solver.compute_interval_fingerprint(
		seed_start, seed_end,
	)
	result = interval_solver.solve_interval_analytical(
		seed_start, seed_end,
		ctx.scene_transform,
		ctx.all_seeds_scene,
		ctx.fps,
		blob_snap_enabled,
		debug=ctx.debug,
		motion_track=ctx.motion_track,
		all_seeds=ctx.all_seeds,
		reader=ctx.reader,
	)
	return (pair_idx, fingerprint, result)


#============================================
def make_pool(
	num_workers: int,
	video_path: str,
	scene_transform: object,
	motion_track: object,
	all_seeds_scene: list,
	all_seeds: list,
	fps: float,
	debug: bool,
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
		video_path: Path to the video file.
		scene_transform: SceneTransform instance.
		motion_track: Motion track object.
		all_seeds_scene: Seeds in scene coordinates.
		all_seeds: Seeds in pixel coordinates.
		fps: Video frame rate.
		debug: Debug flag.

	Returns:
		A started ProcessPoolExecutor. Caller is responsible for using
		it as a context manager so workers shut down cleanly.
	"""
	return concurrent.futures.ProcessPoolExecutor(
		max_workers=num_workers,
		initializer=_worker_init,
		initargs=(
			video_path, scene_transform, motion_track,
			all_seeds_scene, all_seeds, fps, debug,
		),
		max_tasks_per_child=1,
	)
