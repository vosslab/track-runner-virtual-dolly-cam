"""Camera motion estimation from video frames.

Estimates per-frame camera translation (dx, dy) and scale from consecutive
frame pairs using phase correlation. Caches results for reuse across runs.
"""

# Standard Library
import hashlib
import json
import os
from dataclasses import dataclass

# PIP3 modules
import cv2
import numpy
import rich.progress

# local repo modules
import tr_paths
import interval_solver
import tr_video_identity


#============================================
def _make_motion_progress() -> rich.progress.Progress:
	"""Build a rich Progress bar configured for motion-estimation loops.

	Matches the column layout used in `encoder.encode_cropped_video` so the
	UI feels consistent across long-running passes. Refresh is capped at
	1 Hz to avoid flicker on fast consecutive frame pairs.

	Returns:
		An unstarted `rich.progress.Progress` instance. Use it as a context
		manager and call `add_task("  camera motion", total=...)`.
	"""
	progress = rich.progress.Progress(
		rich.progress.TextColumn("{task.description}"),
		interval_solver.BlockBarColumn(),
		rich.progress.TaskProgressColumn(),
		rich.progress.TimeRemainingColumn(),
		refresh_per_second=1,
	)
	return progress


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
	) -> MotionTrack:
		"""Estimate motion using phase correlation on consecutive frames.

		Args:
			reader: FrameReader with read_frame(frame_index) method and
				.frame_count attribute.
			config: Configuration dict (unused for fixed zoom estimator).

		Returns:
			MotionTrack with translation and quality metrics.
		"""
		total_frames = reader.frame_count
		# allocate output arrays (float32; event_flags not tracked)
		dx_arr = numpy.zeros(total_frames, dtype=numpy.float32)
		dy_arr = numpy.zeros(total_frames, dtype=numpy.float32)
		scale_arr = numpy.ones(total_frames, dtype=numpy.float32)
		quality_arr = numpy.zeros(total_frames, dtype=numpy.float32)

		# read first frame (frame 0)
		prev_frame = reader.read_frame(0)
		if prev_frame is None:
			raise RuntimeError("cannot read first frame from video")
		prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)

		# create Hann window for phase correlation
		# window size must exactly match frame dimensions for phaseCorrelate
		frame_h, frame_w = prev_gray.shape
		if frame_h == frame_w and frame_h <= self.window_size:
			# only use window if frame is square and smaller than window_size
			hann_window = cv2.createHanningWindow(
				(frame_w, frame_h),
				cv2.CV_32F
			)
		else:
			# use None (no window) if frame dimensions don't match
			hann_window = None

		# process each consecutive pair
		with _make_motion_progress() as progress:
			task = progress.add_task(
				"  camera motion", total=total_frames - 1,
			)
			for frame_index in range(1, total_frames):
				curr_frame = reader.read_frame(frame_index)
				if curr_frame is None:
					break
				curr_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)

				# compute phase correlation between frames
				shift, response = cv2.phaseCorrelate(
					prev_gray.astype(numpy.float32),
					curr_gray.astype(numpy.float32),
					hann_window
				)

				# extract translation and quality
				dx_val = float(shift[0])
				dy_val = float(shift[1])
				quality_val = float(response)

				dx_arr[frame_index] = dx_val
				dy_arr[frame_index] = dy_val
				quality_arr[frame_index] = quality_val

				# move to next frame
				prev_gray = curr_gray
				progress.update(task, advance=1)

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
	def estimate(self, reader: object, config: dict) -> MotionTrack:
		"""Estimate per-frame motion with discrete zoom jump detection.

		Args:
			reader: FrameReader with read_frame(idx), frame_count, fps.
			config: Config dict with camera.zoom_levels list.

		Returns:
			MotionTrack with per-frame dx, dy, scale, quality, event_flags.
		"""
		total = reader.frame_count
		dx_arr = numpy.zeros(total, dtype=numpy.float32)
		dy_arr = numpy.zeros(total, dtype=numpy.float32)
		# raw per-frame scale from log-polar correlation
		raw_scale = numpy.ones(total, dtype=numpy.float32)
		quality_arr = numpy.zeros(total, dtype=numpy.float32)

		# read first frame
		frame0 = reader.read_frame(0)
		if frame0 is None:
			motion = MotionTrack(
				dx=dx_arr, dy=dy_arr,
				scale=numpy.ones(total, dtype=numpy.float32),
				quality=quality_arr,
			)
			return motion
		prev_gray = cv2.cvtColor(frame0, cv2.COLOR_BGR2GRAY)
		h_frame, w_frame = prev_gray.shape
		# build hann window matching frame size
		hann = cv2.createHanningWindow((w_frame, h_frame), cv2.CV_64F)

		# estimate translation and raw scale for each frame pair
		with _make_motion_progress() as progress:
			task = progress.add_task("  camera motion", total=total - 1)
			for frame_index in range(1, total):
				curr_frame = reader.read_frame(frame_index)
				if curr_frame is None:
					progress.update(task, advance=1)
					continue
				curr_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)

				# translation via phase correlation
				prev_f = numpy.float64(prev_gray)
				curr_f = numpy.float64(curr_gray)
				shift, response = cv2.phaseCorrelate(prev_f, curr_f, hann)
				dx_arr[frame_index] = shift[0]
				dy_arr[frame_index] = shift[1]
				quality_arr[frame_index] = response

				# scale via log-polar phase correlation
				scale_ratio = self._estimate_scale_logpolar(
					prev_gray, curr_gray, w_frame, h_frame,
				)
				raw_scale[frame_index] = scale_ratio

				prev_gray = curr_gray
				progress.update(task, advance=1)

		# detect zoom jumps using 5-frame sliding window
		window_size = 5
		zoom_levels = config.get("camera", {}).get("zoom_levels", [1])
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
	def _estimate_scale_logpolar(
		self,
		prev_gray: numpy.ndarray,
		curr_gray: numpy.ndarray,
		w: int,
		h: int,
	) -> float:
		"""Estimate scale ratio between two frames via log-polar correlation.

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
	def estimate(self, reader: object, config: dict) -> MotionTrack:
		"""Estimate per-frame motion with continuous scale tracking.

		Args:
			reader: FrameReader with read_frame(idx), frame_count, fps.
			config: Config dict.

		Returns:
			MotionTrack with per-frame dx, dy, scale, quality.
		"""
		total = reader.frame_count
		dx_arr = numpy.zeros(total, dtype=numpy.float32)
		dy_arr = numpy.zeros(total, dtype=numpy.float32)
		scale_arr = numpy.ones(total, dtype=numpy.float32)
		quality_arr = numpy.zeros(total, dtype=numpy.float32)

		frame0 = reader.read_frame(0)
		if frame0 is None:
			motion = MotionTrack(
				dx=dx_arr, dy=dy_arr, scale=scale_arr,
				quality=quality_arr,
			)
			return motion
		prev_gray = cv2.cvtColor(frame0, cv2.COLOR_BGR2GRAY)
		h_frame, w_frame = prev_gray.shape
		hann = cv2.createHanningWindow((w_frame, h_frame), cv2.CV_64F)

		# reuse log-polar helper from discrete estimator
		discrete_est = DiscreteZoomEstimator()

		with _make_motion_progress() as progress:
			task = progress.add_task("  camera motion", total=total - 1)
			for frame_index in range(1, total):
				curr_frame = reader.read_frame(frame_index)
				if curr_frame is None:
					progress.update(task, advance=1)
					continue
				curr_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)

				# translation
				prev_f = numpy.float64(prev_gray)
				curr_f = numpy.float64(curr_gray)
				shift, response = cv2.phaseCorrelate(prev_f, curr_f, hann)
				dx_arr[frame_index] = shift[0]
				dy_arr[frame_index] = shift[1]
				quality_arr[frame_index] = response

				# continuous scale via log-polar
				scale_ratio = discrete_est._estimate_scale_logpolar(
					prev_gray, curr_gray, w_frame, h_frame,
				)

				# stricter quality gating for continuous zoom
				if response < 0.3:
					scale_arr[frame_index] = 1.0
				else:
					scale_arr[frame_index] = scale_ratio

				prev_gray = curr_gray
				progress.update(task, advance=1)

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
def _compute_config_fingerprint(config: dict) -> str:
	"""Compute a hash fingerprint of the motion estimation config.

	Args:
		config: Configuration dict (typically motion.estimator settings).

	Returns:
		Hex string representing the config state.
	"""
	config_json = json.dumps(config, sort_keys=True, default=str)
	# md5 used for cache fingerprinting, not security
	fingerprint = hashlib.md5(
		config_json.encode(), usedforsecurity=False,
	).hexdigest()
	return fingerprint


#============================================

# Motion model identifiers written inside the NPZ as `motion_model`.
MOTION_MODEL_FIXED = "fixed_zoom"
MOTION_MODEL_DISCRETE = "discrete_zoom"
MOTION_MODEL_CONTINUOUS = "continuous_zoom"

# Valid model values; loader rejects anything else.
VALID_MOTION_MODELS = frozenset({
	MOTION_MODEL_FIXED, MOTION_MODEL_DISCRETE, MOTION_MODEL_CONTINUOUS,
})

# Per-model required array sets. Fixed zoom carries no scale because
# it is constant 1.0 by construction; writing it would be pure ballast.
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


#============================================
def save_motion_cache(
	motion_track: MotionTrack,
	cache_path: str,
	motion_model: str,
	video_identity: dict,
	config_hash: str,
) -> None:
	"""Save motion track to the canonical camera_motion.npz file.

	Writes per-model arrays (fixed_zoom omits `scale`; discrete and
	continuous include it) plus `motion_model`, `video_identity_basename`,
	`frame_count`, and `config_hash` as cache-identity metadata. All
	per-frame arrays are stored as float32. No `event_flags`.

	Args:
		motion_track: MotionTrack instance to save.
		cache_path: Target NPZ file path
			(`<video>.track_runner.camera_motion.npz`).
		motion_model: One of MOTION_MODEL_{FIXED,DISCRETE,CONTINUOUS}.
		video_identity: dict carrying at least `basename` and
			`frame_count`; persisted so a stale cache can be detected
			without re-probing the video.
		config_hash: MD5-8 of the estimator config dict. Loader
			compares to the current config; mismatch triggers recompute.
	"""
	if motion_model not in VALID_MOTION_MODELS:
		raise ValueError(f"unknown motion_model: {motion_model}")
	tr_paths.ensure_parent_dir(cache_path)
	arrays = {
		"motion_model": numpy.frombuffer(
			motion_model.encode("utf-8"), dtype=numpy.uint8
		),
		"video_identity_basename": numpy.frombuffer(
			str(video_identity.get("basename", "unknown")).encode("utf-8"),
			dtype=numpy.uint8,
		),
		"frame_count": numpy.asarray(
			int(video_identity.get("frame_count", 0)), dtype=numpy.int64,
		),
		"config_hash": numpy.frombuffer(
			config_hash.encode("utf-8"), dtype=numpy.uint8
		),
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
	expected_config_hash: str | None = None,
) -> MotionTrack | None:
	"""Load motion track from camera_motion.npz.

	Returns None if the file does not exist OR the persisted
	`config_hash` differs from `expected_config_hash`. A stale cache
	(mismatched hash) is treated as absent so the caller recomputes
	and overwrites atomically. No merge, no partial reuse.

	For `fixed_zoom`, the on-disk file carries no `scale` array; the
	loader synthesizes an all-ones scale array so downstream
	SceneTransform code sees the same shape regardless of model.

	Args:
		cache_path: Path to `<video>.track_runner.camera_motion.npz`.
		expected_config_hash: Current config's md5-8 hash; if provided
			and disagreeing with the stored value, the cache is
			treated as stale and None is returned.

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
		stored_hash = bytes(npz["config_hash"]).decode("utf-8")
		# stale cache: behave as if file is absent so caller recomputes
		if expected_config_hash is not None and stored_hash != expected_config_hash:
			return None
		required = _REQUIRED_ARRAYS[motion_model]
		for key in required:
			if key not in npz.files:
				raise RuntimeError(
					f"motion cache missing required array {key!r} "
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
	motion = MotionTrack(dx=dx, dy=dy, scale=scale, quality=quality)
	return motion


#============================================
def precompute_camera_motion(
	reader,
	config: dict,
	input_file: str,
	video_info: dict,
	cache_dir: str,
) -> MotionTrack:
	"""Estimate camera motion, checking the single-file cache first.

	The cache lives at `<video>.track_runner.camera_motion.npz`. If
	present and the stored `config_hash` matches the current
	estimator-config hash, returns the cached track. Otherwise the
	estimator runs and the cache is atomically overwritten.

	Args:
		reader: FrameReader instance with read_frame() and frame_count.
		config: Configuration dict with motion estimator settings.
		input_file: Path to the input video file (used for cache path).
		video_info: Video probe info dict (width, height, fps, etc.).
		cache_dir: Directory where cache files are stored. Ignored for
			path selection (tr_paths resolves to tr_config/); kept in
			the signature for callsite compatibility.

	Returns:
		MotionTrack with per-frame motion data.
	"""
	del cache_dir  # retained for callsite compatibility; path from tr_paths
	# build video identity for cache validation
	video_identity = tr_video_identity.make_video_identity(
		input_file, video_info,
	)
	# get estimator config (default to FixedZoomEstimator if not specified)
	estimator_config = config.get("motion", {}).get("estimator", {})
	estimator_type = estimator_config.get("type")
	# fall back to camera.zoom_type alias when motion.estimator.type absent
	if estimator_type is None:
		zoom_type = config.get("camera", {}).get("zoom_type", "fixed")
		estimator_type = zoom_type
	motion_model = _estimator_type_to_model(estimator_type)
	config_fp = _compute_config_fingerprint(estimator_config)
	config_hash = config_fp[:8]
	cache_path = tr_paths.default_motion_cache_path(input_file)
	# try to load from cache; stale hash returns None and triggers recompute
	cached_motion = load_motion_cache(cache_path, config_hash)
	if cached_motion is not None:
		return cached_motion
	# select the matching estimator implementation
	if motion_model == MOTION_MODEL_FIXED:
		estimator = FixedZoomEstimator()
	elif motion_model == MOTION_MODEL_DISCRETE:
		estimator = DiscreteZoomEstimator()
	else:
		estimator = ContinuousZoomEstimator()
	motion = estimator.estimate(reader, estimator_config)
	# save to cache atomically (numpy.savez overwrites in place)
	save_motion_cache(
		motion, cache_path, motion_model, video_identity, config_hash,
	)
	return motion
