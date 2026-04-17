"""Per-interval parallel solver execution.

The analytical solver in `interval_solver.solve_interval_analytical` is a
pure function of its inputs: two seeds, a scene transform, a motion track,
the seed list, and a video reader for ROI reads. Intervals have no
cross-talk (per 2026-04-17 blob-snap rework), so they are safe to solve
concurrently in worker processes.

This module owns the execution side:

- `_WORKER_STATE`: per-process cache populated once by `_worker_init`.
  Heavy immutable objects (scene_transform, motion_track, full seed
  lists, VideoReader) are shipped via the pool initializer instead of
  pickled per task, so per-task payload stays tiny.
- `_solve_interval_worker`: the function `ProcessPoolExecutor` calls.
  Takes a tiny task tuple, returns (pair_idx, fingerprint, result).
- `run_solve_parallel` / `run_solve_inprocess`: the two execution modes.
  The driver in `interval_solver.solve_all_intervals` picks one based on
  worker count and interval count.

Architecture boundary: workers own their VideoReader and compute; the
main process owns cache lookup, dispatch, result aggregation, progress,
persistence, and quit handling. No worker writes to stdout or disk.
"""

# Standard Library
import atexit
import concurrent.futures

# local repo modules
import video_io
import interval_solver


#============================================
# Per-worker state cache. Populated once per child process by
# `_worker_init`, reused across every `_solve_interval_worker` call in
# that process.
_WORKER_STATE: dict = {}


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
	and caches the run-invariant objects in module-level state.

	Args:
		video_path: Path to the video file (reopened in this process).
		scene_transform: SceneTransform instance.
		motion_track: Motion track object for scoring.
		all_seeds_scene: Precomputed list of all seeds in scene coords.
		all_seeds: Original seed list (pixel coords).
		fps: Video frame rate.
		debug: Debug flag; constant across all tasks in this run.
	"""
	# reopen the video in this process; the main process's reader cannot
	# cross the fork/spawn boundary.
	reader = video_io.VideoReader(video_path)
	reader.__enter__()
	_WORKER_STATE["reader"] = reader
	_WORKER_STATE["scene_transform"] = scene_transform
	_WORKER_STATE["motion_track"] = motion_track
	_WORKER_STATE["all_seeds_scene"] = all_seeds_scene
	_WORKER_STATE["all_seeds"] = all_seeds
	_WORKER_STATE["fps"] = fps
	_WORKER_STATE["debug"] = debug
	# close the reader when the worker shuts down so file handles do not
	# leak on the normal path. ProcessPoolExecutor also terminates
	# workers at pool shutdown.
	atexit.register(_worker_atexit)


#============================================
def _worker_atexit() -> None:
	"""Close the worker's VideoReader on process exit."""
	reader = _WORKER_STATE.get("reader")
	if reader is not None:
		reader.__exit__(None, None, None)
		_WORKER_STATE["reader"] = None


#============================================
def _solve_interval_worker(task: tuple) -> tuple:
	"""Solve one interval inside a pool worker.

	Takes a tiny pickleable task tuple and returns the fingerprint plus
	result dict. All heavy inputs come from `_WORKER_STATE`, populated
	by `_worker_init` once per process.

	Args:
		task: Tuple of (pair_idx, seed_start, seed_end).

	Returns:
		Tuple of (pair_idx, fingerprint, result_dict).
	"""
	pair_idx, seed_start, seed_end = task
	fingerprint = interval_solver.compute_interval_fingerprint(
		seed_start, seed_end,
	)
	result = interval_solver.solve_interval_analytical(
		seed_start, seed_end,
		_WORKER_STATE["scene_transform"],
		_WORKER_STATE["all_seeds_scene"],
		_WORKER_STATE["fps"],
		debug=_WORKER_STATE["debug"],
		motion_track=_WORKER_STATE["motion_track"],
		all_seeds=_WORKER_STATE["all_seeds"],
		reader=_WORKER_STATE["reader"],
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
	)
