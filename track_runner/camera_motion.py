"""Camera motion estimation from video frames.

Estimates per-frame camera translation (dx, dy) and scale from consecutive
frame pairs using phase correlation. Stores results as durable artifacts for reuse across runs.
"""

# Standard Library
import concurrent.futures
import multiprocessing
import os
import time
from dataclasses import dataclass

# PIP3 modules
import cv2
import numpy
import rich.progress

# local repo modules
import tr_paths
import interval_solver
import tr_video_identity
import common_tools.frame_reader


#============================================
def _reader_geometry(reader: object) -> tuple[int, int, int]:
	"""Return (bin_factor, processed_width, processed_height) for any reader.

	FrameReader exposes a `.geometry` attribute via FrameGeometry; other
	readers (VideoReader, synthetic test stubs) report the source frame
	dims and bin_factor=1.
	"""
	geom = getattr(reader, "geometry", None)
	if geom is not None:
		return geom.bin_factor, geom.processed_width, geom.processed_height
	# fallback: assume bin_factor=1 and use width/height as processed dims
	w = int(getattr(reader, "width", 0))
	h = int(getattr(reader, "height", 0))
	return 1, w, h


#============================================
def _make_motion_progress() -> rich.progress.Progress:
	"""Build the camera-motion progress bar via the centralized helper.

	Delegates to `interval_solver.make_solve_progress` so Stage 1
	(camera motion), Stage 3 (Hermite pass), Stage 4 (blob promotion)
	all show the same columns: text, block bar, M/N, percent,
	`ETA M:SS  elapsed M:SS`.

	Returns:
		An unstarted `rich.progress.Progress` instance. Use as a context
		manager and call `add_task("  camera motion", total=...)`.
	"""
	return interval_solver.make_solve_progress()


#============================================
@dataclass
class MotionTrack:
	"""Per-frame camera motion and quality metrics.

	All arrays have length total_frames (number of frames in the video).

	Attributes:
		dx: numpy array of per-frame x translations (pixels).
		dy: numpy array of per-frame y translations (pixels).
		scale: numpy array of per-frame scale factors (1.0 = no change).
			For `fixed_zoom`, this is always 1.0 and is not persisted to
			disk, but the field is kept in memory so downstream
			SceneTransform code works uniformly across motion models.
		quality: numpy array of per-frame confidence (phase correlation
			response). Consumed by scoring.py for `motion_quality`.
	"""
	dx: numpy.ndarray
	dy: numpy.ndarray
	scale: numpy.ndarray
	quality: numpy.ndarray


#============================================
def _pair_scale_logpolar(
	prev_gray: numpy.ndarray,
	curr_gray: numpy.ndarray,
	w: int,
	h: int,
) -> float:
	"""Estimate scale ratio between two frames via log-polar correlation.

	Used by both DiscreteZoomEstimator and ContinuousZoomEstimator. A
	scale change between two frames shows up as an x-shift in log-polar
	coordinates, which a second phase-correlation recovers. Result is
	clamped to [0.5, 2.0] and returned as 1.0 if correlation confidence
	is below 0.1.

	Args:
		prev_gray: Previous grayscale frame.
		curr_gray: Current grayscale frame.
		w: Frame width.
		h: Frame height.

	Returns:
		Scale ratio (>1 means zoom in, <1 means zoom out).
	"""
	center = (w / 2.0, h / 2.0)
	max_radius = min(w, h) / 2.0
	# log-polar warp size
	lp_size = (256, 256)
	lp_prev = cv2.warpPolar(
		prev_gray, lp_size, center, max_radius,
		cv2.WARP_POLAR_LOG + cv2.INTER_LINEAR,
	)
	lp_curr = cv2.warpPolar(
		curr_gray, lp_size, center, max_radius,
		cv2.WARP_POLAR_LOG + cv2.INTER_LINEAR,
	)
	# phase correlate on log-polar images
	lp_prev_f = numpy.float64(lp_prev)
	lp_curr_f = numpy.float64(lp_curr)
	hann_lp = cv2.createHanningWindow(
		(lp_size[0], lp_size[1]), cv2.CV_64F,
	)
	shift, response = cv2.phaseCorrelate(lp_prev_f, lp_curr_f, hann_lp)
	# x-shift in log-polar corresponds to log(scale)
	log_base = max_radius / lp_size[0]
	if response < 0.1:
		scale_ratio = 1.0
	else:
		scale_ratio = numpy.exp(shift[0] * numpy.log(log_base))
		# clamp to reasonable range
		scale_ratio = max(0.5, min(2.0, scale_ratio))
	return scale_ratio


#============================================
# Motion model identifiers used by the persisted artifact and the chunked workers.
# Defined here (above the estimators) so they are picklable through the
# multiprocessing boundary alongside _measure_pairs_in_range without
# forward-reference gymnastics.
MOTION_MODEL_FIXED = "fixed_zoom"
MOTION_MODEL_DISCRETE = "discrete_zoom"
MOTION_MODEL_CONTINUOUS = "continuous_zoom"

VALID_MOTION_MODELS = frozenset({
	MOTION_MODEL_FIXED, MOTION_MODEL_DISCRETE, MOTION_MODEL_CONTINUOUS,
})

# Hann-window gate for fixed_zoom. Matches the long-standing rule
# in FixedZoomEstimator: build a Hann window only when the frame is
# square and at most this size; otherwise pass None to phaseCorrelate.
_FIXED_HANN_MAX = 64

# Cap on parallel workers. Beyond this the per-worker decode I/O
# saturates and additional pool members add scheduling overhead
# without measurable speedup.
_MAX_PARALLEL_CHUNKS = 8

# Below this many frames the serial path wins because pool startup
# (~hundreds of ms per process) dominates the actual work.
_MIN_FRAMES_FOR_PARALLEL = 200

# Worker-side progress counter shared with the driver. Set by the
# pool initializer; consumed by workers as a batched increment so
# the rich progress bar can show real per-pair motion without 8
# stepwise jumps when chunks finish near-simultaneously.
_WORKER_PROGRESS_COUNTER = None

# Workers flush their local pair-count into the shared counter
# whenever EITHER threshold is hit: 100 pairs accumulated locally
# (cheap on fast videos), or 10 seconds since the last flush
# (so slow-decode videos still tick visibly). Lock contention is
# negligible at this rate -- a few flushes per worker per second
# at most.
_PROGRESS_FLUSH_PAIRS = 100
_PROGRESS_FLUSH_SECONDS = 10.0


#============================================
def _worker_init_progress(counter) -> None:
	"""ProcessPool initializer: stash the shared progress counter.

	Called once per worker process at pool spawn. The counter is a
	`multiprocessing.Value('i', ...)` wrapping a shared ctypes int.
	`_estimate_chunk_pairs` reads it from the module global (cannot
	be passed per-task because the driver and workers share the
	value object, which is set up at pool-init time).
	"""
	global _WORKER_PROGRESS_COUNTER
	_WORKER_PROGRESS_COUNTER = counter


#============================================
def _pick_chunk_count(total_frames: int) -> int:
	"""Pick how many worker chunks to dispatch for this video.

	Returns 1 (serial fallback) when the video is short enough that
	pool startup outweighs the work. Otherwise returns
	`min(os.cpu_count(), _MAX_PARALLEL_CHUNKS)`.

	Args:
		total_frames: Number of frames in the video.

	Returns:
		Chunk count >= 1.
	"""
	if total_frames < _MIN_FRAMES_FOR_PARALLEL:
		return 1
	cpu_count = os.cpu_count() or 1
	return max(1, min(cpu_count, _MAX_PARALLEL_CHUNKS))


#============================================
def _build_hann_for_model(
	model_name: str,
	frame_w: int,
	frame_h: int,
) -> "numpy.ndarray | None":
	"""Construct the Hann window phaseCorrelate uses for this model.

	Mirrors the long-standing per-estimator window choice:

	- fixed_zoom: only build a window if the frame is square and at most
	  `_FIXED_HANN_MAX` (64) on a side; otherwise return None. Real-world
	  landscape video falls through to None, which is the historical
	  behavior.
	- discrete_zoom and continuous_zoom: build a CV_64F Hann window
	  matching the full frame size.

	Args:
		model_name: One of MOTION_MODEL_{FIXED,DISCRETE,CONTINUOUS}.
		frame_w: Frame width in pixels.
		frame_h: Frame height in pixels.

	Returns:
		A Hann window array, or None when the model and frame size do
		not warrant one.
	"""
	if model_name == MOTION_MODEL_FIXED:
		if frame_h == frame_w and frame_h <= _FIXED_HANN_MAX:
			return cv2.createHanningWindow(
				(frame_w, frame_h), cv2.CV_32F,
			)
		return None
	# discrete and continuous always use a CV_64F window
	return cv2.createHanningWindow((frame_w, frame_h), cv2.CV_64F)


#============================================
def _measure_pairs_in_range(
	reader,
	start_idx: int,
	end_idx: int,
	model_name: str,
	on_pair=None,
) -> tuple:
	"""Measure consecutive frame pairs over [start_idx, end_idx).

	Anchors on `start_idx - 1` (paying one seek), then walks frames
	sequentially using the reader's next-frame fast-path. Output index
	`k` corresponds to the pair `(start_idx - 1 + k, start_idx + k)`.

	Output dtypes and conventions match the existing estimators so the
	serial and parallel paths produce structurally identical arrays:

	- `dx`, `dy` are the float32 phaseCorrelate translations.
	- `quality` is the float32 phaseCorrelate response.
	- `scale` is the raw log-polar scale ratio for discrete and
	  continuous models (post-processing decides what to do with it),
	  and a constant 1.0 for fixed_zoom.

	Args:
		reader: Frame reader with `read_frame(idx)` returning a BGR
			numpy array (or None past end). Pickling is irrelevant
			here -- workers always pass a freshly-opened VideoReader.
		start_idx: First frame index whose pair to measure (>= 1).
		end_idx: One past the last frame index to measure.
		model_name: Selects translation/scale handling.
		on_pair: Optional callable invoked once per measured pair.
			Used by the serial path so the rich progress bar advances
			live during the long phaseCorrelate loop. Workers pass
			None so nothing crosses the process boundary.

	Returns:
		Tuple `(dx, dy, scale, quality)` of float32 numpy arrays of
		length `end_idx - start_idx`.

	Raises:
		ValueError: If `model_name` is not a known motion model.
		RuntimeError: If the anchor frame at `start_idx - 1` cannot be
			read. Fail loudly so callers cannot accidentally save a
			zero-filled artifact; this matches the historical FixedZoom
			behavior and tightens the looser Discrete/Continuous
			fall-through to all-zero motion.
	"""
	if model_name not in VALID_MOTION_MODELS:
		raise ValueError(f"unknown motion_model: {model_name}")
	n = max(0, end_idx - start_idx)
	dx_chunk = numpy.zeros(n, dtype=numpy.float32)
	dy_chunk = numpy.zeros(n, dtype=numpy.float32)
	scale_chunk = numpy.ones(n, dtype=numpy.float32)
	quality_chunk = numpy.zeros(n, dtype=numpy.float32)
	if n == 0:
		return dx_chunk, dy_chunk, scale_chunk, quality_chunk
	# anchor on the frame before the first pair
	prev_frame = reader.read_frame(start_idx - 1)
	if prev_frame is None:
		raise RuntimeError(
			f"cannot read anchor frame {start_idx - 1} from video"
		)
	prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
	frame_h, frame_w = prev_gray.shape
	hann_window = _build_hann_for_model(model_name, frame_w, frame_h)
	# walk forward; the reader's sequential fast-path (FrameReader
	# strategy 0 / VideoReader _next_frame) avoids cap.set() per
	# frame so consecutive read_frame calls only do one cv2 read.
	for k in range(n):
		frame_idx = start_idx + k
		curr_frame = reader.read_frame(frame_idx)
		if curr_frame is None:
			# matches the historical Discrete/Continuous behavior:
			# leave this slot at its initial value, keep prev_gray
			# pinned to the previous good frame, and try the next
			# index. (Original FixedZoom broke here; in practice
			# read failures only occur at end-of-stream where break
			# and continue are equivalent.)
			continue
		curr_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)
		# translation: fixed_zoom uses float32; the others float64
		if model_name == MOTION_MODEL_FIXED:
			shift, response = cv2.phaseCorrelate(
				prev_gray.astype(numpy.float32),
				curr_gray.astype(numpy.float32),
				hann_window,
			)
		else:
			shift, response = cv2.phaseCorrelate(
				numpy.float64(prev_gray),
				numpy.float64(curr_gray),
				hann_window,
			)
		dx_chunk[k] = float(shift[0])
		dy_chunk[k] = float(shift[1])
		quality_chunk[k] = float(response)
		# scale: log-polar correlation for the zoom-aware models
		if model_name != MOTION_MODEL_FIXED:
			scale_chunk[k] = _pair_scale_logpolar(
				prev_gray, curr_gray, frame_w, frame_h,
			)
		prev_gray = curr_gray
		# advance live progress (serial path passes a callback;
		# parallel workers pass None so nothing crosses the boundary)
		if on_pair is not None:
			on_pair()
	return dx_chunk, dy_chunk, scale_chunk, quality_chunk


#============================================
def _estimate_chunk_pairs(args: tuple) -> tuple:
	"""ProcessPool worker: open a VideoReader and measure one chunk.

	Designed for `concurrent.futures.ProcessPoolExecutor.map` /
	`submit`. All arguments are simple picklable values; the reader is
	freshly opened per worker process and not passed across the
	boundary.

	Args:
		args: Tuple `(video_path, start_idx, end_idx, model_name)`.

	Returns:
		Tuple `(start_idx, end_idx, dx, dy, scale, quality)`. The
		index pair is echoed back so the driver can write the chunk
		into the right slice without depending on dispatch order.
	"""
	(
		video_path,
		start_idx,
		end_idx,
		model_name,
		bin_factor,
		fps,
		total_frames,
	) = args
	# batched flush into shared counter on either pair-count or
	# wall-clock thresholds, whichever fires first. Keeps the bar
	# moving on slow videos without spamming the lock on fast ones.
	local = [0]
	last_flush = [time.monotonic()]
	counter = _WORKER_PROGRESS_COUNTER

	def _on_pair() -> None:
		local[0] += 1
		now = time.monotonic()
		hit_pair_threshold = local[0] >= _PROGRESS_FLUSH_PAIRS
		hit_time_threshold = (
			now - last_flush[0]
		) >= _PROGRESS_FLUSH_SECONDS
		if hit_pair_threshold or hit_time_threshold:
			with counter.get_lock():
				counter.value += local[0]
			local[0] = 0
			last_flush[0] = now

	on_pair = _on_pair if counter is not None else None
	# Open a FrameReader so workers honor the run's bin_factor.
	# FrameReader returns processed frames; phase correlation runs at
	# processed dims and the chunk's dx/dy come back in processed-frame
	# pixels. The driver upscales at the boundary before persisting.
	reader = common_tools.frame_reader.FrameReader(
		video_path=video_path,
		fps=fps,
		total_frames=total_frames,
		bin_factor=bin_factor,
	)
	try:
		dx_c, dy_c, scale_c, quality_c = _measure_pairs_in_range(
			reader, start_idx, end_idx, model_name,
			on_pair=on_pair,
		)
	finally:
		reader.close()
	# flush whatever did not hit a batch boundary
	if counter is not None and local[0] > 0:
		with counter.get_lock():
			counter.value += local[0]
	return start_idx, end_idx, dx_c, dy_c, scale_c, quality_c


#============================================
def _split_pair_range(
	total_frames: int,
	n_chunks: int,
) -> list:
	"""Partition pair indices [1, total_frames) into n_chunks ranges.

	Each chunk is a `(start_idx, end_idx)` half-open range. Earlier
	chunks absorb the remainder when the range does not divide evenly.
	Empty chunks are dropped, so the returned list may be shorter than
	`n_chunks` for very small ranges.

	Args:
		total_frames: Number of frames in the video.
		n_chunks: Desired chunk count (>= 1).

	Returns:
		List of `(start_idx, end_idx)` tuples covering `[1, total_frames)`
		without overlap.
	"""
	if total_frames <= 1 or n_chunks < 1:
		return []
	pair_count = total_frames - 1
	# ceiling division gives the larger chunk size; later chunks shrink
	chunk_size = (pair_count + n_chunks - 1) // n_chunks
	ranges = []
	cursor = 1
	while cursor < total_frames:
		end = min(cursor + chunk_size, total_frames)
		ranges.append((cursor, end))
		cursor = end
	return ranges


#============================================
def _estimate_parallel(
	video_path: str,
	total_frames: int,
	model_name: str,
	n_chunks: int,
	bin_factor: int = 1,
	fps: float = 0.0,
) -> tuple:
	"""Drive the chunked, multi-process measurement pass.

	Splits `[1, total_frames)` into chunks, dispatches each to its own
	worker process, and assembles full-length per-frame arrays. Workers
	exit after returning their chunk (`max_tasks_per_child=1`) so each
	worker's heap, OpenCV decode state, and pymalloc arenas are
	reclaimed by the OS.

	Worker exceptions abort the dispatch and propagate to the caller.
	No silent fallback to the serial path -- that would mask real
	decode, pickling, or chunk-boundary bugs.

	Output arrays follow the same shape as the serial path
	(length `total_frames`, index 0 is always zero/one for dx/dy and
	scale respectively, quality[0] is zero).

	Args:
		video_path: Path to the input video file.
		total_frames: Number of frames in the video.
		model_name: One of MOTION_MODEL_{FIXED,DISCRETE,CONTINUOUS}.
		n_chunks: Worker chunk count (>= 1). The actual number of
			chunks dispatched may be smaller for very short videos.

	Returns:
		Tuple `(dx, dy, scale, quality)` of float32 numpy arrays of
		length `total_frames`.
	"""
	if model_name not in VALID_MOTION_MODELS:
		raise ValueError(f"unknown motion_model: {model_name}")
	dx_arr = numpy.zeros(total_frames, dtype=numpy.float32)
	dy_arr = numpy.zeros(total_frames, dtype=numpy.float32)
	scale_arr = numpy.ones(total_frames, dtype=numpy.float32)
	quality_arr = numpy.zeros(total_frames, dtype=numpy.float32)
	chunk_ranges = _split_pair_range(total_frames, n_chunks)
	if not chunk_ranges:
		return dx_arr, dy_arr, scale_arr, quality_arr
	tasks = [
		(
			video_path, start, end, model_name,
			bin_factor, fps, total_frames,
		)
		for (start, end) in chunk_ranges
	]
	# fresh process per chunk for memory hygiene; matches solver pool.
	# Use ProcessPoolExecutor as a context manager so shutdown happens
	# automatically on exception even if a future raises mid-iteration.
	# Shared int counter gives workers a way to report per-pair
	# progress to the driver continuously, instead of only on chunk
	# completion (which would otherwise jump 0 -> 100% for evenly
	# sized chunks that finish near-simultaneously).
	progress_counter = multiprocessing.Value("i", 0)
	with _make_motion_progress() as progress:
		task_id = progress.add_task(
			"  camera motion", total=total_frames - 1,
		)
		with concurrent.futures.ProcessPoolExecutor(
			max_workers=len(tasks),
			max_tasks_per_child=1,
			initializer=_worker_init_progress,
			initargs=(progress_counter,),
		) as executor:
			futures = [
				executor.submit(_estimate_chunk_pairs, args)
				for args in tasks
			]
			pending = set(futures)
			# poll-and-wait loop: every iteration we either consume a
			# completed chunk or refresh the progress bar from the
			# shared worker counter. The 0.25 s timeout is short
			# enough to feel live but long enough to avoid burning
			# the driver core on a busy-loop.
			while pending:
				done, pending = concurrent.futures.wait(
					pending, timeout=0.25,
					return_when=concurrent.futures.FIRST_COMPLETED,
				)
				progress.update(
					task_id, completed=int(progress_counter.value),
				)
				for future in done:
					(
						start_idx, end_idx,
						dx_c, dy_c, scale_c, quality_c,
					) = future.result()
					dx_arr[start_idx:end_idx] = dx_c
					dy_arr[start_idx:end_idx] = dy_c
					scale_arr[start_idx:end_idx] = scale_c
					quality_arr[start_idx:end_idx] = quality_c
		# all chunks have flushed; force the bar to exactly 100% in
		# case the last batch fell short of _PROGRESS_FLUSH_BATCH
		progress.update(task_id, completed=total_frames - 1)
	return dx_arr, dy_arr, scale_arr, quality_arr


#============================================
def _run_measurement(
	reader,
	total_frames: int,
	model_name: str,
	n_chunks: int,
) -> tuple:
	"""Dispatch raw-pair measurement to the serial or parallel path.

	Both paths produce full-length float32 arrays
	`(dx, dy, scale, quality)` of length `total_frames`, with index 0
	left at its initial value (zero for `dx`/`dy`/`quality`, one for
	`scale`). This is the single point where serial vs parallel is
	decided, so estimators feed the same post-processing block in
	either case.

	When `n_chunks > 1` the reader must expose a `video_path`
	attribute so workers can reopen the file in their own processes.
	`video_io.VideoReader` provides this; synthetic test readers do
	not, so they must use `n_chunks=1`.

	Args:
		reader: Frame reader; used directly when `n_chunks == 1`,
			only for `video_path` and `frame_count` when `n_chunks > 1`.
		total_frames: Number of frames in the video.
		model_name: One of MOTION_MODEL_{FIXED,DISCRETE,CONTINUOUS}.
		n_chunks: Worker count (>= 1). `1` forces the serial path.

	Returns:
		Tuple `(dx, dy, scale, quality)` of float32 numpy arrays of
		length `total_frames`.
	"""
	if n_chunks <= 1:
		dx_arr = numpy.zeros(total_frames, dtype=numpy.float32)
		dy_arr = numpy.zeros(total_frames, dtype=numpy.float32)
		scale_arr = numpy.ones(total_frames, dtype=numpy.float32)
		quality_arr = numpy.zeros(total_frames, dtype=numpy.float32)
		# show a live progress bar that advances per measured pair.
		# _measure_pairs_in_range raises if the anchor frame cannot
		# be read, so we no longer pre-validate frame 0 separately.
		with _make_motion_progress() as progress:
			task_id = progress.add_task(
				"  camera motion", total=total_frames - 1,
			)
			def _advance() -> None:
				progress.update(task_id, advance=1)
			dx_c, dy_c, scale_c, quality_c = _measure_pairs_in_range(
				reader, 1, total_frames, model_name,
				on_pair=_advance,
			)
			# write chunk into full-length arrays
			dx_arr[1:total_frames] = dx_c
			dy_arr[1:total_frames] = dy_c
			scale_arr[1:total_frames] = scale_c
			quality_arr[1:total_frames] = quality_c
		return dx_arr, dy_arr, scale_arr, quality_arr
	# parallel path: workers reopen the video themselves
	video_path = getattr(reader, "video_path", None)
	if video_path is None:
		raise RuntimeError(
			"n_chunks > 1 requires a reader with a video_path attribute"
		)
	# threading bin/fps through so workers can construct a FrameReader
	# with the same processed-frame contract as the driver.
	bin_factor, _, _ = _reader_geometry(reader)
	fps = float(getattr(reader, "fps", 0.0))
	return _estimate_parallel(
		video_path, total_frames, model_name, n_chunks,
		bin_factor=bin_factor, fps=fps,
	)


#============================================
class MotionEstimator:
	"""Base class for motion estimation algorithms.

	Subclasses implement estimate() to provide motion tracks from video frames.
	"""

	#============================================
	def estimate(
		self,
		reader,
		config: dict,
	) -> MotionTrack:
		"""Estimate per-frame camera motion from video frames.

		Args:
			reader: FrameReader instance with read_frame(frame_index) method.
			config: Configuration dict (structure depends on estimator type).

		Returns:
			MotionTrack with dx, dy, scale, quality, event_flags arrays.

		Raises:
			NotImplementedError: Must be overridden by subclass.
		"""
		raise NotImplementedError(
			"estimate() must be implemented by subclass"
		)


#============================================
class FixedZoomEstimator(MotionEstimator):
	"""Estimate translation using phase correlation with fixed zoom.

	Uses cv2.phaseCorrelate() on consecutive grayscale frame pairs with a
	Hann window for smoothing. Scale is fixed at 1.0 (no zoom estimation).
	Results are median-filtered to smooth outliers.

	Args:
		window_size: Size of the Hann window for smoothing (default 64).
	"""

	#============================================
	def __init__(self, window_size: int = 64):
		"""Initialize FixedZoomEstimator.

		Args:
			window_size: Size of the Hann window for phase correlation.
		"""
		self.window_size = window_size

	#============================================
	def estimate(
		self,
		reader,
		config: dict,
		n_chunks: int = 1,
	) -> MotionTrack:
		"""Estimate motion using phase correlation on consecutive frames.

		Args:
			reader: FrameReader with read_frame(frame_index) method and
				.frame_count attribute.
			config: Configuration dict (unused for fixed zoom estimator).
			n_chunks: Internal knob. `1` -> existing serial measurement
				path. `>1` -> dispatch chunk workers via
				`_estimate_parallel`; the reader must expose a
				`video_path` attribute (any `video_io.VideoReader` does).

		Returns:
			MotionTrack with translation and quality metrics.
		"""
		total_frames = reader.frame_count
		# raw measurement: serial or chunked. Both call
		# _measure_pairs_in_range under the hood, so output is
		# structurally identical and feeds the same post-processing.
		dx_arr, dy_arr, scale_arr, quality_arr = _run_measurement(
			reader, total_frames, MOTION_MODEL_FIXED, n_chunks,
		)
		# at this point dx/dy are in processed-frame pixels. upscale
		# back to source-frame so MotionTrack persistence keeps its
		# existing source-frame contract.
		bin_factor, _, _ = _reader_geometry(reader)
		if bin_factor != 1:
			dx_arr = dx_arr * float(bin_factor)
			dy_arr = dy_arr * float(bin_factor)

		# apply 3-frame median filter to smooth dx, dy
		dx_arr = self._median_filter_1d(dx_arr, 3)
		dy_arr = self._median_filter_1d(dy_arr, 3)

		# construct motion track
		motion = MotionTrack(
			dx=dx_arr,
			dy=dy_arr,
			scale=scale_arr,
			quality=quality_arr,
		)
		return motion

	#============================================
	def _median_filter_1d(
		self,
		arr: numpy.ndarray,
		kernel_size: int,
	) -> numpy.ndarray:
		"""Apply 1D median filter with given kernel size.

		Args:
			arr: 1D numpy array to filter.
			kernel_size: Size of the median filter kernel (must be odd).

		Returns:
			Filtered array of the same shape.
		"""
		if kernel_size % 2 == 0:
			kernel_size += 1
		half = kernel_size // 2
		result = arr.copy()
		for i in range(half, len(arr) - half):
			# collect values in the window
			window = arr[i - half:i + half + 1]
			# compute median
			result[i] = numpy.median(window)
		return result


#============================================
class DiscreteZoomEstimator(MotionEstimator):
	"""Estimator for cameras with discrete zoom steps (e.g. iPhone 1x/2x/5x).

	Detects zoom transitions using a sliding window and snaps detected
	scale ratios to known zoom levels from config.
	"""

	#============================================
	def estimate(
		self,
		reader: object,
		config: dict,
		n_chunks: int = 1,
	) -> MotionTrack:
		"""Estimate per-frame motion with discrete zoom jump detection.

		Args:
			reader: FrameReader with read_frame(idx), frame_count, fps.
			config: Config dict with camera.zoom_levels list.
			n_chunks: Internal knob; see FixedZoomEstimator.estimate.
				Only the raw-pair measurement parallelizes; the
				cross-frame jump detector below is unconditionally
				serial.

		Returns:
			MotionTrack with per-frame dx, dy, scale, quality, event_flags.
		"""
		total = reader.frame_count
		# raw measurement: log-polar scale per pair, no jump detection
		dx_arr, dy_arr, raw_scale, quality_arr = _run_measurement(
			reader, total, MOTION_MODEL_DISCRETE, n_chunks,
		)
		# upscale dx/dy from processed-frame to source-frame pixels.
		# scale (log-polar) is dimensionless and unchanged.
		bin_factor, _, _ = _reader_geometry(reader)
		if bin_factor != 1:
			dx_arr = dx_arr * float(bin_factor)
			dy_arr = dy_arr * float(bin_factor)

		# detect zoom jumps using 5-frame sliding window. zoom_levels
		# is required for the discrete estimator -- silently defaulting
		# to [1] would make every transition snap back to unit scale,
		# which looks like a working run but is wrong. Fail loudly
		# when the caller forgot to configure it.
		window_size = 5
		camera_cfg = config["camera"]
		zoom_levels = camera_cfg["zoom_levels"]
		scale_arr = numpy.ones(total, dtype=numpy.float32)
		cumulative_scale = 1.0

		for i in range(1, total):
			# sliding window around current frame
			win_start = max(1, i - window_size // 2)
			win_end = min(total, i + window_size // 2 + 1)
			window_scales = raw_scale[win_start:win_end]
			if len(window_scales) < 2:
				continue
			# check for large variation in window
			max_s = numpy.max(window_scales)
			min_s = numpy.min(window_scales)
			ratio = max_s / max(min_s, 1e-6)
			if ratio > 1.40:
				# zoom transition detected; snap to nearest zoom level
				cumulative_scale *= raw_scale[i]
				snapped = self._snap_to_zoom_level(
					cumulative_scale, zoom_levels,
				)
				scale_arr[i] = snapped / (cumulative_scale / raw_scale[i])
				cumulative_scale = snapped
			else:
				# stable -- no zoom change
				scale_arr[i] = 1.0

		# apply 3-frame median filter to dx, dy
		fze = FixedZoomEstimator()
		dx_arr = fze._median_filter_1d(dx_arr, 3)
		dy_arr = fze._median_filter_1d(dy_arr, 3)

		motion = MotionTrack(
			dx=dx_arr, dy=dy_arr, scale=scale_arr,
			quality=quality_arr,
		)
		return motion

	#============================================
	def _snap_to_zoom_level(
		self,
		cumulative: float,
		zoom_levels: list,
	) -> float:
		"""Snap a cumulative scale value to the nearest zoom level ratio.

		Args:
			cumulative: Current cumulative scale factor.
			zoom_levels: List of zoom level integers (e.g. [1, 2, 5]).

		Returns:
			Nearest zoom level ratio relative to base level.
		"""
		if not zoom_levels:
			return cumulative
		base = zoom_levels[0]
		ratios = [z / base for z in zoom_levels]
		# find closest ratio
		best = min(ratios, key=lambda r: abs(cumulative - r))
		return best


#============================================
class ContinuousZoomEstimator(MotionEstimator):
	"""Estimator for cameras with smooth continuous zoom.

	Uses log-polar phase correlation for per-frame scale estimation
	with stricter quality gating than discrete zoom.
	"""

	#============================================
	def estimate(
		self,
		reader: object,
		config: dict,
		n_chunks: int = 1,
	) -> MotionTrack:
		"""Estimate per-frame motion with continuous scale tracking.

		Args:
			reader: FrameReader with read_frame(idx), frame_count, fps.
			config: Config dict.
			n_chunks: Internal knob; see FixedZoomEstimator.estimate.

		Returns:
			MotionTrack with per-frame dx, dy, scale, quality.
		"""
		total = reader.frame_count
		# raw measurement: yields raw log-polar scale per pair without
		# applying the response<0.3 gate. Gate runs in post-processing
		# below so serial and parallel paths are equivalent.
		dx_arr, dy_arr, raw_scale, quality_arr = _run_measurement(
			reader, total, MOTION_MODEL_CONTINUOUS, n_chunks,
		)
		# upscale dx/dy from processed-frame to source-frame pixels.
		# scale (log-polar) is dimensionless and unchanged.
		bin_factor, _, _ = _reader_geometry(reader)
		if bin_factor != 1:
			dx_arr = dx_arr * float(bin_factor)
			dy_arr = dy_arr * float(bin_factor)

		# continuous-zoom quality gate: per-frame, no cross-frame
		# state; safe to apply to the merged arrays.
		scale_arr = numpy.where(
			quality_arr < 0.3,
			numpy.float32(1.0),
			raw_scale,
		).astype(numpy.float32)

		# apply 3-frame median filter to dx, dy, and scale
		fze = FixedZoomEstimator()
		dx_arr = fze._median_filter_1d(dx_arr, 3)
		dy_arr = fze._median_filter_1d(dy_arr, 3)
		scale_arr = fze._median_filter_1d(scale_arr, 3)

		motion = MotionTrack(
			dx=dx_arr, dy=dy_arr, scale=scale_arr,
			quality=quality_arr,
		)
		return motion


#============================================

# Per-model required array sets. Fixed zoom carries no scale because
# it is constant 1.0 by construction; writing it would be pure ballast.
# (Motion-model identifiers MOTION_MODEL_FIXED / DISCRETE / CONTINUOUS
# and VALID_MOTION_MODELS are defined near the top of the module so
# they are available to the chunked-worker code path.)
_REQUIRED_ARRAYS = {
	MOTION_MODEL_FIXED: ("dx", "dy", "quality"),
	MOTION_MODEL_DISCRETE: ("dx", "dy", "scale", "quality"),
	MOTION_MODEL_CONTINUOUS: ("dx", "dy", "scale", "quality"),
}


def _estimator_type_to_model(estimator_type: str) -> str:
	"""Map a config-level estimator type string to a motion_model label."""
	if estimator_type in ("FixedZoomEstimator", "fixed"):
		return MOTION_MODEL_FIXED
	if estimator_type in (
		"DiscreteZoomEstimator", "discrete", "iphone_discrete",
	):
		return MOTION_MODEL_DISCRETE
	if estimator_type in ("ContinuousZoomEstimator", "continuous"):
		return MOTION_MODEL_CONTINUOUS
	raise ValueError(f"unsupported estimator type: {estimator_type}")


def _motion_model_from_config(config: dict) -> str:
	"""Resolve the motion_model label implied by the current config.

	Reads `motion.estimator.type` if present; otherwise falls back to the
	`camera.zoom_type` alias (`fixed`/`discrete`/`continuous`); otherwise
	defaults to `fixed`. Both keys are optional in the YAML schema, so
	chained-default `.get(...)` is intentional here.
	"""
	estimator_config = config.get("motion", {}).get("estimator", {})
	estimator_type = estimator_config.get("type")
	if estimator_type is None:
		estimator_type = config.get("camera", {}).get("zoom_type", "fixed")
	motion_model = _estimator_type_to_model(estimator_type)
	return motion_model


#============================================
def save_motion_cache(
	motion_track: MotionTrack,
	cache_path: str,
	motion_model: str,
	video_identity: dict,
	bin_factor: int = 1,
) -> None:
	"""Save motion track to the canonical camera_motion.npz file.

	Writes per-model arrays (fixed_zoom omits `scale`; discrete and
	continuous include it) plus `motion_model`, `video_identity_basename`,
	`frame_count`, and `bin_factor` as artifact-identity metadata. All
	per-frame arrays are stored as float32. No `event_flags`. This is a
	durable solved-result artifact, not cache.

	bin_factor is persisted as identity metadata (cache-key bookkeeping,
	NOT an on-disk schema change). The phase-correlation estimator runs on
	PROCESSED frames and upscales dx/dy by bin_factor to SOURCE before
	storage, so the stored SOURCE track depends on the analysis bin even
	though its units are SOURCE. A bin change must recompute camera motion;
	`load_motion_cache` treats a bin mismatch as a stale artifact.

	Args:
		motion_track: MotionTrack instance to save.
		cache_path: Target NPZ file path at
			`<video>.track_runner.camera_motion.npz`. This is the
			canonical single-file solved artifact per video. If motion_model
			differs from the stored value on load, the stored version is
			treated as stale and recomputed.
		motion_model: One of MOTION_MODEL_{FIXED,DISCRETE,CONTINUOUS}.
		video_identity: dict carrying at least `basename` and
			`frame_count`; persisted so a stale artifact can be detected
			without re-probing the video.
	"""
	if motion_model not in VALID_MOTION_MODELS:
		raise ValueError(f"unknown motion_model: {motion_model}")
	tr_paths.ensure_parent_dir(cache_path)
	arrays = {
		"motion_model": numpy.frombuffer(
			motion_model.encode("utf-8"), dtype=numpy.uint8
		),
		"video_identity_basename": numpy.frombuffer(
			str(video_identity["basename"]).encode("utf-8"),
			dtype=numpy.uint8,
		),
		"frame_count": numpy.asarray(
			int(video_identity["frame_count"]), dtype=numpy.int64,
		),
		"bin_factor": numpy.asarray(int(bin_factor), dtype=numpy.int64),
		"dx": numpy.asarray(motion_track.dx, dtype=numpy.float32),
		"dy": numpy.asarray(motion_track.dy, dtype=numpy.float32),
		"quality": numpy.asarray(motion_track.quality, dtype=numpy.float32),
	}
	# fixed zoom omits scale; other models include it
	if motion_model != MOTION_MODEL_FIXED:
		arrays["scale"] = numpy.asarray(
			motion_track.scale, dtype=numpy.float32,
		)
	numpy.savez(cache_path, **arrays)


#============================================
def load_motion_cache(
	cache_path: str,
	expected_motion_model: str | None = None,
	expected_bin_factor: int | None = None,
) -> MotionTrack | None:
	"""Load motion track from camera_motion.npz.

	Returns None if the file does not exist OR the persisted
	`motion_model` differs from `expected_motion_model` OR the persisted
	`bin_factor` differs from `expected_bin_factor`. A stale artifact
	(mismatched motion_model or bin_factor) is treated as absent so the
	caller recomputes and overwrites atomically. No merge, no partial reuse.

	The phase-correlation estimator runs on PROCESSED frames, so the stored
	SOURCE dx/dy depend on the analysis bin even though their units are
	SOURCE. A bin change must recompute camera motion. A legacy artifact
	written before bin_factor was persisted has no `bin_factor` key; it is
	treated as a bin_factor=1 solve.

	For `fixed_zoom`, the on-disk file carries no `scale` array; the
	loader synthesizes an all-ones scale array so downstream
	SceneTransform code sees the same shape regardless of model.

	Args:
		cache_path: Path to `<video>.track_runner.camera_motion.npz`.
		expected_motion_model: Current motion_model derived from config
			(one of MOTION_MODEL_*); if provided and disagreeing with
			the stored value, the artifact is treated as stale and None
			is returned.

	Returns:
		MotionTrack instance, or None if missing / stale / unknown
		motion_model.

	Raises:
		RuntimeError: If the file exists but a required per-model
			array is missing.
	"""
	if not os.path.isfile(cache_path):
		return None
	with numpy.load(cache_path, allow_pickle=False) as npz:
		motion_model = bytes(npz["motion_model"]).decode("utf-8")
		if motion_model not in VALID_MOTION_MODELS:
			raise RuntimeError(
				f"unknown motion_model {motion_model!r} in {cache_path}; "
				f"run tools/_migrate_tr_config.py to archive and regenerate"
			)
		# stale artifact: behave as if file is absent so caller recomputes
		if expected_motion_model is not None and motion_model != expected_motion_model:
			return None
		# bin staleness: a legacy artifact (no bin_factor key) is a bin=1
		# solve. A bin mismatch means the stored SOURCE track was computed at
		# a different analysis resolution, so it is stale and must recompute.
		if expected_bin_factor is not None:
			if "bin_factor" in npz.files:
				stored_bin_factor = int(npz["bin_factor"])
			else:
				stored_bin_factor = 1
			if stored_bin_factor != int(expected_bin_factor):
				return None
		required = _REQUIRED_ARRAYS[motion_model]
		for key in required:
			if key not in npz.files:
				raise RuntimeError(
					f"motion artifact missing required array {key!r} "
					f"for model {motion_model} in {cache_path}"
				)
		dx = numpy.asarray(npz["dx"], dtype=numpy.float32)
		dy = numpy.asarray(npz["dy"], dtype=numpy.float32)
		quality = numpy.asarray(npz["quality"], dtype=numpy.float32)
		if motion_model == MOTION_MODEL_FIXED:
			# synthesize a constant-1.0 scale so downstream code sees
			# a uniform MotionTrack shape regardless of model
			scale = numpy.ones(len(dx), dtype=numpy.float32)
		else:
			scale = numpy.asarray(npz["scale"], dtype=numpy.float32)
		# Internal consistency check: if frame_count is persisted, verify
		# it agrees with the length of the dx array. Mismatches indicate
		# file corruption or a partially-written file.
		if "frame_count" in npz.files:
			persisted_frame_count = int(npz["frame_count"])
			actual_length = len(dx)
			if persisted_frame_count != actual_length:
				raise RuntimeError(
					f"camera_motion.npz frame_count={persisted_frame_count} "
					f"but dx array has {actual_length} entries; file is corrupt"
				)
	motion = MotionTrack(dx=dx, dy=dy, scale=scale, quality=quality)
	return motion


#============================================
def load_active_camera_motion_or_fail(
	input_file: str,
	config: dict,
	expected_bin_factor: int | None = None,
) -> MotionTrack:
	"""Load the canonical camera-motion artifact from disk.

	Refine entry point. Loads the canonical solved artifact file
	`<video>.track_runner.camera_motion.npz`. Refine never recomputes
	Stage 1, so a missing file, a stored motion_model that disagrees with
	the current config, or a stored bin_factor that disagrees with the
	refine run's bin raises with a "run solve first" message.

	Args:
		input_file: Path to the input video.
		config: Configuration dict with motion estimator settings.
		expected_bin_factor: The refine run's bin_factor. When provided and
			it disagrees with the stored bin, the artifact is treated as
			stale (the stored SOURCE track was computed at a different
			analysis resolution) and the load fails. None skips the check.

	Returns:
		MotionTrack from the canonical solved artifact file.

	Raises:
		RuntimeError: If the artifact file is missing or its stored
			motion_model / bin_factor does not match. Tells the caller to
			run solve first.
	"""
	expected_motion_model = _motion_model_from_config(config)
	cache_path = tr_paths.default_camera_motion_path(input_file)
	if not os.path.isfile(cache_path):
		raise RuntimeError(
			"Camera-motion artifact for this solve is missing."
			" Run solve first."
		)
	cached = load_motion_cache(
		cache_path, expected_motion_model, expected_bin_factor,
	)
	if cached is None:
		raise RuntimeError(
			"Camera-motion artifact for this solve is missing."
			" Run solve first."
		)
	return cached


#============================================
def precompute_camera_motion(
	reader,
	config: dict,
	input_file: str,
	video_info: dict,
) -> MotionTrack:
	"""Estimate camera motion, checking the single-file store first.

	The solved artifact lives at `<video>.track_runner.camera_motion.npz`. If
	present and the stored motion_model matches the current config's
	motion_model, returns the stored track. Otherwise the estimator runs
	and the artifact is atomically overwritten.

	Args:
		reader: FrameReader instance with read_frame() and frame_count.
		config: Configuration dict with motion estimator settings.
		input_file: Path to the input video file (used for artifact path).
		video_info: Video probe info dict (width, height, fps, etc.).

	Returns:
		MotionTrack with per-frame motion data.
	"""
	# build video identity for artifact validation
	video_identity = tr_video_identity.make_video_identity(
		input_file, video_info,
	)
	motion_model = _motion_model_from_config(config)
	# The estimator runs phase correlation on PROCESSED frames at this
	# reader's bin_factor, then upscales dx/dy to SOURCE. The stored SOURCE
	# track therefore depends on bin even though its units are SOURCE, so the
	# cache identity must key on bin: a bin change forces recompute.
	bin_factor, _, _ = _reader_geometry(reader)
	# Check canonical artifact file and return if motion_model AND bin match
	canonical_path = tr_paths.default_camera_motion_path(input_file)
	cached_motion = load_motion_cache(
		canonical_path,
		expected_motion_model=motion_model,
		expected_bin_factor=bin_factor,
	)
	if cached_motion is not None:
		return cached_motion
	# select the matching estimator implementation
	if motion_model == MOTION_MODEL_FIXED:
		estimator = FixedZoomEstimator()
	elif motion_model == MOTION_MODEL_DISCRETE:
		estimator = DiscreteZoomEstimator()
	else:
		estimator = ContinuousZoomEstimator()
	# pick chunk count: cap at 8 cores, fall back to serial on short
	# videos so pool startup does not eat the wall-clock budget
	n_chunks = _pick_chunk_count(reader.frame_count)
	# Pass the full config so estimators can reach top-level keys
	# they need (DiscreteZoomEstimator reads camera.zoom_levels).
	motion = estimator.estimate(
		reader, config, n_chunks=n_chunks,
	)
	# save to canonical artifact path atomically (numpy.savez overwrites
	# in place).
	save_motion_cache(
		motion, canonical_path, motion_model, video_identity,
		bin_factor=bin_factor,
	)
	return motion
