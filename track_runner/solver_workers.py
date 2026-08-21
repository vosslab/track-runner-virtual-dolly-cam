"""Per-interval parallel solver execution.

The analytical solver in `interval_solver.solve_interval_analytical` is a
pure function of its inputs: two seeds, a scene transform, a motion track,
the seed list, and a video reader for ROI reads. Intervals have no
cross-talk (each interval is an independent per-process solve), so they
are safe to solve concurrently in worker processes.

This module owns the execution side:

- `WorkerContext`: a frozen dataclass holding the run-invariant objects
  (scene_transform, motion_track, full seed lists, FrameReader). One
  instance is built per worker process by `_worker_init` and reused for
  every interval that worker handles. The context is assigned once per
  process and treated as read-only thereafter, so no mutable state
  accumulates across interval solves (contract C3 conformance margin).
- `_solve_interval_worker`: the function `ProcessPoolExecutor` calls.
  Takes a tiny task tuple and, when production telemetry is enabled,
  returns the result plus non-persistent RSS/cache/runtime measurements.
- `make_pool`: builds a ProcessPoolExecutor with `_worker_init` wired up.

Architecture boundary: workers own their FrameReader and compute; the
main process owns cache lookup, dispatch, result aggregation, progress,
persistence, and quit handling. No worker writes to stdout or disk.
"""

# Standard Library
import atexit
import dataclasses
import concurrent.futures
import os
import resource
import subprocess
import sys
import time

# local repo modules
import interval_solver
import common_tools.frame_reader
import residual_motion
import residual_pre_pass


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
	bin_factor: int = 1
	telemetry_enabled: bool = False


#============================================
@dataclasses.dataclass
class WorkerTelemetrySummary:
	"""Aggregate non-persistent measurements from one worker-pool dispatch."""
	driver_peak_before_pool_bytes: int
	interval_count: int = 0
	worker_pids: set[int] = dataclasses.field(default_factory=set)
	peak_worker_rss_bytes: int = 0
	worker_interval_seconds: float = 0.0
	prepass_lookup_count: int = 0
	prepass_miss_count: int = 0
	prepass_eviction_count: int = 0

	def add(self, telemetry: dict) -> None:
		"""Add one completed interval's worker measurements."""
		self.interval_count += 1
		self.worker_pids.add(int(telemetry["pid"]))
		self.peak_worker_rss_bytes = max(
			self.peak_worker_rss_bytes,
			int(telemetry["ru_maxrss_bytes"]),
		)
		self.worker_interval_seconds += float(telemetry["elapsed_s"])
		prepass = telemetry["prepass"]
		if prepass is not None:
			self.prepass_lookup_count += int(prepass["lookup_count"])
			self.prepass_miss_count += int(prepass["miss_count"])
			self.prepass_eviction_count += int(prepass["eviction_count"])


#============================================
# Holds the current worker's WorkerContext once `_worker_init` runs. A
# worker writes this exactly once at process startup and reads it on
# every subsequent task; the object itself is frozen, so interval solves
# cannot mutate it.
_WORKER_CONTEXT: WorkerContext | None = None


# One reserve worker leaves room for a second simultaneous controlled-allocation
# window while a pool starts or transitions work. It scales with the
# video-shaped worker budget instead of assuming a particular machine has a
# fixed amount of spare RAM. Decoder and Python-runtime memory are measured by
# normal telemetry; this reserve does not claim to bound them.
MEMORY_RESERVE_WORKERS = 1

#============================================
def _require_positive_frame_shape(width: int, height: int) -> None:
	"""Reject a non-image shape before using it in byte accounting."""
	if width <= 0 or height <= 0:
		raise ValueError(f"processed frame shape must be positive, got {width}x{height}")


#============================================
def residual_scratch_bytes(processed_width: int, processed_height: int) -> int:
	"""Return a source-level ledger for one residual/DoG calculation.

	``compute_residual_for_frame`` retains one float32 aligned frame per
	neighbor and ``numpy.stack`` makes one second float32 stack. One additional
	stack accounts for ``numpy.nanmedian`` reduction workspace. It then holds
	the median source/result, int64 valid-count image, float32 residual, and
	uint8 validity. The extraction seam adds one BGR warp, gray warp, pair
	validity, every DoG temporary, and connected-component masks/labels. Every
	term below maps to a named array in those production functions; no
	unexplained frame multiplier is used.
	"""
	_require_positive_frame_shape(processed_width, processed_height)
	pixels = processed_width * processed_height
	neighbor_count = residual_motion.DEFAULT_HALF_WINDOW * 2
	aligned_and_stacked = neighbor_count * 4 * 2
	median_reduction_workspace = neighbor_count * 4
	median_and_residual = 4 + 4 + 4
	validity_and_count = 1 + 8
	current_warp_arrays = 3 + 1 + 1
	# GaussianBlur outputs, subtraction, clip, astype, and residual.copy.
	dog_working_arrays = 4 + 4 + 4 + 4 + 4 + 4
	# threshold masks, connected-component labels, and one component mask.
	blob_extraction_arrays = 1 + 1 + 4 + 1
	bytes_per_pixel = (
		aligned_and_stacked
		+ median_reduction_workspace
		+ median_and_residual
		+ validity_and_count
		+ current_warp_arrays
		+ dog_working_arrays
		+ blob_extraction_arrays
	)
	return pixels * bytes_per_pixel


#============================================
def controlled_worker_bytes(processed_width: int, processed_height: int) -> int:
	"""Return the declared maximum of solver-owned retained working bytes.

	The fixed pre-pass result store is bounded by byte-count eviction. The
	shape-dependent terms are derived from existing frame-count caps and a
	source-level residual allocation ledger. This is deliberately conservative:
	it treats the Stage-3 canonical residual cache and Stage-4 pre-pass terms as
	concurrent so one automatic count is safe for either pool dispatch.

	Args:
		processed_width: Analysis-frame width after binning.
		processed_height: Analysis-frame height after binning.

	Returns:
		Declared byte budget for one solver worker's controlled image memory.
	"""
	_require_positive_frame_shape(processed_width, processed_height)
	pixels = processed_width * processed_height
	# BGR is three uint8 channels and gray is one uint8 channel. Both rolling
	# pre-pass dictionaries independently enforce MAX_PREPASS_BUFFER_FRAMES.
	prepass_rolling_bytes = (
		residual_pre_pass.MAX_PREPASS_BUFFER_FRAMES * pixels * 4
	)
	# The combined `_frames` LRU can retain either BGR uint8 or float32 gray
	# entries. Gray is the larger entry, so use four bytes/pixel, not three.
	frame_cache_bytes = residual_motion.MAX_GRAY_CACHE_FRAMES * pixels * 4
	# Each walker direction has a raw observation cache containing residual,
	# DoG, validity, and blob data. Its own byte cap is independent of the
	# pre-pass store and both can be live during a promoted solve.
	raw_observation_cache_bytes = residual_motion.RESIDUAL_OBSERVATION_CACHE_MAX_BYTES
	residual_scratch = residual_scratch_bytes(processed_width, processed_height)
	result = (
		residual_pre_pass.PREPASS_RESULT_STORE_MAX_BYTES
		+ prepass_rolling_bytes
		+ frame_cache_bytes
		+ raw_observation_cache_bytes
		+ residual_scratch
	)
	return result


#============================================
def select_budgeted_worker_count(
	available_bytes: int,
	parent_bytes: int,
	worker_bytes: int,
	reserve_bytes: int,
	cpu_count: int,
	requested_workers: int | None = None,
) -> int:
	"""Select an automatic pool size from explicit byte terms.

	An explicit ``--workers`` value is an operator override and therefore wins
	exactly. Automatic selection preserves the historical half-CPU target only
	when its full parent + workers + reserve expression fits available memory.

	Args:
		available_bytes: Currently available host memory in bytes.
		parent_bytes: Conservative driver-process memory already occupied.
		worker_bytes: Declared controlled memory budget for one worker.
		reserve_bytes: Extra headroom retained outside the selected pool.
		cpu_count: Number of CPUs available to the process.
		requested_workers: Explicit CLI override, if supplied.

	Returns:
		The explicit override or a positive memory-safe automatic count.

	Raises:
		ValueError: A count or byte term is invalid.
		RuntimeError: No automatic worker fits the declared budget.
	"""
	if cpu_count < 1:
		raise ValueError(f"cpu_count must be >= 1, got {cpu_count}")
	for name, value in (
		("available_bytes", available_bytes),
		("parent_bytes", parent_bytes),
		("worker_bytes", worker_bytes),
		("reserve_bytes", reserve_bytes),
	):
		if value < 0:
			raise ValueError(f"{name} must be >= 0, got {value}")
	if worker_bytes < 1:
		raise ValueError("worker_bytes must be >= 1")
	if requested_workers is not None:
		if requested_workers < 1:
			raise ValueError(f"--workers must be >= 1, got {requested_workers}")
		return requested_workers
	remaining_bytes = available_bytes - parent_bytes - reserve_bytes
	max_by_memory = remaining_bytes // worker_bytes
	if max_by_memory < 1:
		raise RuntimeError(
			"no solver worker fits memory budget: "
			f"available={available_bytes} parent={parent_bytes} "
			f"reserve={reserve_bytes} worker={worker_bytes}"
		)
	cpu_target = max(1, cpu_count // 2)
	return min(cpu_target, max_by_memory)


#============================================
def _parse_vm_stat_available_bytes(output: str) -> int:
	"""Return macOS reclaimable-page bytes from ``vm_stat`` output."""
	page_size = None
	page_counts = {}
	for line in output.splitlines():
		if "page size of" in line:
			page_size = int(line.split("page size of ")[1].split(" bytes")[0])
		elif ":" in line:
			label, value = line.split(":", 1)
			page_counts[label.strip()] = int(value.strip().rstrip("."))
	if page_size is None:
		raise RuntimeError("vm_stat did not report a page size")
	available_pages = sum(
		page_counts.get(label, 0)
		for label in ("Pages free", "Pages inactive", "Pages speculative")
	)
	if available_pages < 1:
		raise RuntimeError("vm_stat did not report reclaimable pages")
	return page_size * available_pages


#============================================
def available_memory_bytes(platform: str = sys.platform) -> int:
	"""Return current host memory available for a new worker pool.

	Linux exposes this directly as ``MemAvailable``. macOS exposes its
	reclaimable page counts through ``vm_stat``. The fail-loud fallback avoids
	pretending installed physical RAM is currently available memory.
	"""
	if platform.startswith("linux"):
		with open("/proc/meminfo") as fh:
			for line in fh:
				if line.startswith("MemAvailable:"):
					return int(line.split()[1]) * 1024
		raise RuntimeError("/proc/meminfo did not report MemAvailable")
	if platform == "darwin":
		result = subprocess.run(
			["vm_stat"], capture_output=True, check=True, text=True,
		)
		return _parse_vm_stat_available_bytes(result.stdout)
	raise RuntimeError(
		f"cannot determine available memory on {platform}; use a supported host"
	)


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
	telemetry_enabled: bool = False,
) -> None:
	"""Initialize per-process solver state for a pool worker.

	Runs exactly once per child process when the ProcessPoolExecutor
	starts the worker. Opens a dedicated FrameReader on the video path
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
	"""
	global _WORKER_CONTEXT
	# reopen the video in this process; the main process's reader cannot
	# cross the fork/spawn boundary. Always use FrameReader so every
	# worker exposes the same `.geometry` interface regardless of
	# bin_factor; bin_factor=1 short-circuits the resize and is
	# direct FrameReader behavior at full resolution.
	# Construct via the shared opener so all callers route reader construction
	# through one code path.  Production passes the explicit resolved
	# bin_factor (decided by modes.shared._resolve_solve_bin_factor), so the opener
	# uses it as-is and makes no default-bin selector call -- behavior is
	# unchanged from the prior direct FrameReader construction.
	reader = common_tools.frame_reader.open_analysis_reader(
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
		bin_factor=bin_factor,
		telemetry_enabled=telemetry_enabled,
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
		Tuple of (pair_idx, fingerprint, result_dict), with a fourth telemetry
		mapping when the worker context enables measurement.
	"""
	pair_idx, seed_start, seed_end = task
	ctx = _WORKER_CONTEXT
	fingerprint = interval_solver.compute_interval_fingerprint(
		seed_start, seed_end,
	)
	# The worker solves with the dispatch's blob_pass: False for Stage-3 (pure
	# Hermite on every interval) and True for the Stage-4 walker pass. The flag
	# is run-invariant per dispatch, carried on the frozen WorkerContext.
	started = time.monotonic()
	telemetry = {} if ctx.telemetry_enabled else None
	solve_kwargs = {
		"debug": ctx.debug,
		"motion_track": ctx.motion_track,
		"all_seeds": ctx.all_seeds,
		"reader": ctx.reader,
		"blob_pass": ctx.blob_pass,
	}
	if telemetry is not None:
		solve_kwargs["telemetry"] = telemetry
	result = interval_solver.solve_interval_analytical(
		seed_start, seed_end,
		ctx.scene_transform,
		ctx.all_seeds_scene,
		ctx.fps,
		**solve_kwargs,
	)
	if ctx.telemetry_enabled:
		telemetry["elapsed_s"] = time.monotonic() - started
		telemetry["pid"] = os.getpid()
		telemetry["ru_maxrss_bytes"] = ru_maxrss_bytes(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
		return (pair_idx, fingerprint, result, telemetry)
	return (pair_idx, fingerprint, result)


#============================================
def ru_maxrss_bytes(raw_value: int | float, platform: str = sys.platform) -> int:
	"""Normalize ``resource.getrusage().ru_maxrss`` to bytes.

	macOS reports bytes while Linux/BSD reports KiB.  Keeping the conversion
	here gives every telemetry consumer one portable authoritative peak value.
	"""
	value = int(raw_value)
	if platform == "darwin":
		return value
	return value * 1024


#============================================
def current_process_peak_rss_bytes() -> int:
	"""Return the driver process peak RSS in bytes before a pool starts."""
	usage = resource.getrusage(resource.RUSAGE_SELF)
	peak_bytes = ru_maxrss_bytes(usage.ru_maxrss)
	return peak_bytes


#============================================
def current_process_rss_bytes(platform: str = sys.platform) -> int:
	"""Return a measured driver baseline for pool budgeting.

	Linux exposes current resident pages without launching another process.
	macOS does not provide that value through the Python standard library, so it
	uses the established measured peak-RSS value. The conservative macOS path is
	intentional: it keeps pool sizing inside the process/runtime boundary.
	"""
	if platform.startswith("linux"):
		with open("/proc/self/statm") as fh:
			resident_pages = int(fh.read().split()[1])
		return resident_pages * os.sysconf("SC_PAGE_SIZE")
	return current_process_peak_rss_bytes()


#============================================
def collect_worker_result(
	worker_result: tuple,
	summary: WorkerTelemetrySummary,
) -> tuple:
	"""Collect telemetry and return the historical three-item solve result."""
	pair_idx, fingerprint, result, telemetry = worker_result
	summary.add(telemetry)
	collected = (pair_idx, fingerprint, result)
	return collected


#============================================
def format_worker_telemetry(stage_name: str, summary: WorkerTelemetrySummary) -> str:
	"""Format one parseable pool measurement line for the solve log."""
	if summary.prepass_lookup_count > 0:
		miss_rate = summary.prepass_miss_count / summary.prepass_lookup_count
	else:
		miss_rate = 0.0
	line = (
		f"  telemetry stage={stage_name} intervals={summary.interval_count} "
		f"worker_processes={len(summary.worker_pids)} "
		f"driver_peak_before_pool_bytes={summary.driver_peak_before_pool_bytes} "
		f"peak_worker_rss_bytes={summary.peak_worker_rss_bytes} "
		f"worker_interval_seconds={summary.worker_interval_seconds:.3f} "
		f"prepass_lookups={summary.prepass_lookup_count} "
		f"prepass_misses={summary.prepass_miss_count} "
		f"prepass_miss_rate={miss_rate:.6f} "
		f"prepass_evictions={summary.prepass_eviction_count}"
	)
	return line


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
	telemetry_enabled: bool = False,
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
			bin_factor, total_frames, telemetry_enabled,
		),
		max_tasks_per_child=1,
	)
