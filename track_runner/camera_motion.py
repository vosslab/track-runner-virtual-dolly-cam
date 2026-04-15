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

# local repo modules
import tr_paths
import tr_video_identity


#============================================
@dataclass
class MotionTrack:
	"""Per-frame camera motion and quality metrics.

	All arrays have length total_frames (number of frames in the video).

	Attributes:
		dx: numpy array of per-frame x translations (pixels).
		dy: numpy array of per-frame y translations (pixels).
		scale: numpy array of per-frame scale factors (1.0 = no change).
		quality: numpy array of per-frame confidence (phase correlation response).
		event_flags: numpy array of int bitfield.
			bit 0: zoom_jump flag (large scale change detected).
			bit 1: low_quality flag (quality < 0.5).
	"""
	dx: numpy.ndarray
	dy: numpy.ndarray
	scale: numpy.ndarray
	quality: numpy.ndarray
	event_flags: numpy.ndarray


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
			reader: FrameReader instance with read_frame(frame_idx) method.
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
			reader: FrameReader with read_frame(frame_idx) method and
				.frame_count attribute.
			config: Configuration dict (unused for fixed zoom estimator).

		Returns:
			MotionTrack with translation and quality metrics.
		"""
		total_frames = reader.frame_count
		# allocate output arrays
		dx_arr = numpy.zeros(total_frames, dtype=numpy.float32)
		dy_arr = numpy.zeros(total_frames, dtype=numpy.float32)
		scale_arr = numpy.ones(total_frames, dtype=numpy.float32)
		quality_arr = numpy.zeros(total_frames, dtype=numpy.float32)
		event_flags_arr = numpy.zeros(total_frames, dtype=numpy.int32)

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
		for frame_idx in range(1, total_frames):
			curr_frame = reader.read_frame(frame_idx)
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

			dx_arr[frame_idx] = dx_val
			dy_arr[frame_idx] = dy_val
			quality_arr[frame_idx] = quality_val

			# set low_quality bit if confidence is below threshold
			if quality_val < 0.5:
				event_flags_arr[frame_idx] |= (1 << 1)

			# move to next frame
			prev_gray = curr_gray

		# apply 3-frame median filter to smooth dx, dy
		dx_arr = self._median_filter_1d(dx_arr, 3)
		dy_arr = self._median_filter_1d(dy_arr, 3)

		# construct motion track
		motion = MotionTrack(
			dx=dx_arr,
			dy=dy_arr,
			scale=scale_arr,
			quality=quality_arr,
			event_flags=event_flags_arr,
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
		dx_arr = numpy.zeros(total, dtype=numpy.float64)
		dy_arr = numpy.zeros(total, dtype=numpy.float64)
		# raw per-frame scale from log-polar correlation
		raw_scale = numpy.ones(total, dtype=numpy.float64)
		quality_arr = numpy.zeros(total, dtype=numpy.float64)
		event_flags_arr = numpy.zeros(total, dtype=numpy.int32)

		# read first frame
		frame0 = reader.read_frame(0)
		if frame0 is None:
			motion = MotionTrack(
				dx=dx_arr, dy=dy_arr,
				scale=numpy.ones(total, dtype=numpy.float64),
				quality=quality_arr, event_flags=event_flags_arr,
			)
			return motion
		prev_gray = cv2.cvtColor(frame0, cv2.COLOR_BGR2GRAY)
		h_frame, w_frame = prev_gray.shape
		# build hann window matching frame size
		hann = cv2.createHanningWindow((w_frame, h_frame), cv2.CV_64F)

		# estimate translation and raw scale for each frame pair
		for frame_idx in range(1, total):
			curr_frame = reader.read_frame(frame_idx)
			if curr_frame is None:
				continue
			curr_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)

			# translation via phase correlation
			prev_f = numpy.float64(prev_gray)
			curr_f = numpy.float64(curr_gray)
			shift, response = cv2.phaseCorrelate(prev_f, curr_f, hann)
			dx_arr[frame_idx] = shift[0]
			dy_arr[frame_idx] = shift[1]
			quality_arr[frame_idx] = response

			# scale via log-polar phase correlation
			scale_ratio = self._estimate_scale_logpolar(
				prev_gray, curr_gray, w_frame, h_frame,
			)
			raw_scale[frame_idx] = scale_ratio

			prev_gray = curr_gray

		# detect zoom jumps using 5-frame sliding window
		window_size = 5
		zoom_levels = config.get("camera", {}).get("zoom_levels", [1])
		scale_arr = numpy.ones(total, dtype=numpy.float64)
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
				# zoom transition detected
				event_flags_arr[i] |= (1 << 0)
				# snap to nearest zoom level ratio
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
			quality=quality_arr, event_flags=event_flags_arr,
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
			MotionTrack with per-frame dx, dy, scale, quality, event_flags.
		"""
		total = reader.frame_count
		dx_arr = numpy.zeros(total, dtype=numpy.float64)
		dy_arr = numpy.zeros(total, dtype=numpy.float64)
		scale_arr = numpy.ones(total, dtype=numpy.float64)
		quality_arr = numpy.zeros(total, dtype=numpy.float64)
		event_flags_arr = numpy.zeros(total, dtype=numpy.int32)

		frame0 = reader.read_frame(0)
		if frame0 is None:
			motion = MotionTrack(
				dx=dx_arr, dy=dy_arr, scale=scale_arr,
				quality=quality_arr, event_flags=event_flags_arr,
			)
			return motion
		prev_gray = cv2.cvtColor(frame0, cv2.COLOR_BGR2GRAY)
		h_frame, w_frame = prev_gray.shape
		hann = cv2.createHanningWindow((w_frame, h_frame), cv2.CV_64F)

		# reuse log-polar helper from discrete estimator
		discrete_est = DiscreteZoomEstimator()

		for frame_idx in range(1, total):
			curr_frame = reader.read_frame(frame_idx)
			if curr_frame is None:
				continue
			curr_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)

			# translation
			prev_f = numpy.float64(prev_gray)
			curr_f = numpy.float64(curr_gray)
			shift, response = cv2.phaseCorrelate(prev_f, curr_f, hann)
			dx_arr[frame_idx] = shift[0]
			dy_arr[frame_idx] = shift[1]
			quality_arr[frame_idx] = response

			# continuous scale via log-polar
			scale_ratio = discrete_est._estimate_scale_logpolar(
				prev_gray, curr_gray, w_frame, h_frame,
			)

			# stricter quality gating for continuous zoom
			if response < 0.3:
				scale_arr[frame_idx] = 1.0
				# set low quality flag
				event_flags_arr[frame_idx] |= (1 << 1)
			else:
				scale_arr[frame_idx] = scale_ratio

			prev_gray = curr_gray

		# apply 3-frame median filter to dx, dy, and scale
		fze = FixedZoomEstimator()
		dx_arr = fze._median_filter_1d(dx_arr, 3)
		dy_arr = fze._median_filter_1d(dy_arr, 3)
		scale_arr = fze._median_filter_1d(scale_arr, 3)

		motion = MotionTrack(
			dx=dx_arr, dy=dy_arr, scale=scale_arr,
			quality=quality_arr, event_flags=event_flags_arr,
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
def _compute_cache_key(
	video_identity: dict,
	estimator_type: str,
	config_fingerprint: str,
) -> str:
	"""Compute a unique cache key for motion data.

	Args:
		video_identity: Video identity dict from tr_video_identity.
		estimator_type: Name of the estimator class (e.g. "FixedZoomEstimator").
		config_fingerprint: Hash of the estimator config.

	Returns:
		String key suitable for naming cache files.
	"""
	# build a composite key from video identity and estimator info
	components = [
		video_identity.get("basename", "unknown"),
		str(video_identity.get("frame_count", 0)),
		estimator_type,
		config_fingerprint[:8],  # first 8 chars of hash
	]
	cache_key = "_".join(components)
	return cache_key


#============================================
def save_motion_cache(
	motion_track: MotionTrack,
	cache_path: str,
) -> None:
	"""Save motion track to an NPZ cache file.

	Args:
		motion_track: MotionTrack instance to save.
		cache_path: Path to the output .npz file.
	"""
	tr_paths.ensure_parent_dir(cache_path)
	numpy.savez(
		cache_path,
		dx=motion_track.dx,
		dy=motion_track.dy,
		scale=motion_track.scale,
		quality=motion_track.quality,
		event_flags=motion_track.event_flags,
	)


#============================================
def load_motion_cache(cache_path: str) -> MotionTrack | None:
	"""Load motion track from an NPZ cache file.

	Args:
		cache_path: Path to the .npz cache file.

	Returns:
		MotionTrack instance, or None if file does not exist.
	"""
	if not os.path.isfile(cache_path):
		return None
	# load arrays from NPZ
	data = numpy.load(cache_path)
	motion = MotionTrack(
		dx=numpy.array(data["dx"]),
		dy=numpy.array(data["dy"]),
		scale=numpy.array(data["scale"]),
		quality=numpy.array(data["quality"]),
		event_flags=numpy.array(data["event_flags"]),
	)
	return motion


#============================================
def precompute_camera_motion(
	reader,
	config: dict,
	input_file: str,
	video_info: dict,
	cache_dir: str,
) -> MotionTrack:
	"""Estimate camera motion, checking cache first.

	Args:
		reader: FrameReader instance with read_frame() and frame_count.
		config: Configuration dict with motion estimator settings.
		input_file: Path to the input video file (used for cache key).
		video_info: Video probe info dict (width, height, fps, etc.).
		cache_dir: Directory where cache files are stored.

	Returns:
		MotionTrack with per-frame motion data.
	"""
	# build video identity for cache validation
	video_identity = tr_video_identity.make_video_identity(
		input_file,
		video_info,
	)

	# get estimator config (default to FixedZoomEstimator if not specified)
	estimator_config = config.get("motion", {}).get("estimator", {})
	estimator_type = estimator_config.get("type", "FixedZoomEstimator")

	# compute cache key
	config_fp = _compute_config_fingerprint(estimator_config)
	cache_key = _compute_cache_key(video_identity, estimator_type, config_fp)
	cache_path = os.path.join(cache_dir, f"{cache_key}.npz")

	# try to load from cache
	cached_motion = load_motion_cache(cache_path)
	if cached_motion is not None:
		return cached_motion

	# select estimator by type or zoom_type config alias
	zoom_type = config.get("camera", {}).get("zoom_type", "fixed")
	if estimator_type == "FixedZoomEstimator" or zoom_type == "fixed":
		estimator = FixedZoomEstimator()
	elif estimator_type in ("DiscreteZoomEstimator", "iphone_discrete") or zoom_type in ("iphone_discrete", "discrete"):
		estimator = DiscreteZoomEstimator()
	elif estimator_type == "ContinuousZoomEstimator" or zoom_type == "continuous":
		estimator = ContinuousZoomEstimator()
	else:
		raise ValueError(f"unsupported estimator type: {estimator_type}")

	motion = estimator.estimate(reader, estimator_config)

	# save to cache
	save_motion_cache(motion, cache_path)

	return motion
