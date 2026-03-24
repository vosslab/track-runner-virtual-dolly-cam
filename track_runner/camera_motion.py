#!/usr/bin/env python3
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
				.total_frames attribute.
			config: Configuration dict (unused for fixed zoom estimator).

		Returns:
			MotionTrack with translation and quality metrics.
		"""
		total_frames = reader.total_frames
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
def _compute_config_fingerprint(config: dict) -> str:
	"""Compute a hash fingerprint of the motion estimation config.

	Args:
		config: Configuration dict (typically motion.estimator settings).

	Returns:
		Hex string representing the config state.
	"""
	config_json = json.dumps(config, sort_keys=True, default=str)
	fingerprint = hashlib.md5(config_json.encode()).hexdigest()
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
	track_runner.tr_paths.ensure_parent_dir(cache_path)
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
		reader: FrameReader instance with read_frame() and total_frames.
		config: Configuration dict with motion estimator settings.
		input_file: Path to the input video file (used for cache key).
		video_info: Video probe info dict (width, height, fps, etc.).
		cache_dir: Directory where cache files are stored.

	Returns:
		MotionTrack with per-frame motion data.
	"""
	# build video identity for cache validation
	video_identity = track_runner.tr_video_identity.make_video_identity(
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

	# estimate motion (currently only FixedZoomEstimator supported)
	if estimator_type == "FixedZoomEstimator":
		estimator = FixedZoomEstimator()
	else:
		raise ValueError(f"unsupported estimator type: {estimator_type}")

	motion = estimator.estimate(reader, estimator_config)

	# save to cache
	save_motion_cache(motion, cache_path)

	return motion
