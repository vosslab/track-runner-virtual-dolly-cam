"""Frame-level residual computation and bounded image-cache primitives.

This module deliberately has no dependency on residual_motion.  The latter
remains the public facade and injects its patchable warp callable.
"""

# Standard Library
import collections
import collections.abc
import sys
import warnings

# PIP3 modules
import cv2
import numpy

# Reference fps anchor for the stride model.
REFERENCE_FPS = 60

# Default per-side neighbor count for residual computation.
DEFAULT_HALF_WINDOW = 4

# Shared BGR/gray cache hard limit.
MAX_GRAY_CACHE_FRAMES = 40

# Raw observation cache hard limit.
RESIDUAL_OBSERVATION_CACHE_MAX_BYTES = 512 * 1024 * 1024

WarpBuilder = collections.abc.Callable[[object, int, int, float], numpy.ndarray]
ValidityBuilder = collections.abc.Callable[[numpy.ndarray], numpy.ndarray]
FrameCacheEvicter = collections.abc.Callable[[dict], None]
GrayFrameReader = collections.abc.Callable[[object, int, dict], numpy.ndarray]
StrideResolver = collections.abc.Callable[[float], int]


#============================================
def _cache_value_bytes(value: object, seen: set[int] | None = None) -> int:
	"""Measure the Python and NumPy storage retained by one cache value."""
	if seen is None:
		seen = set()
	value_id = id(value)
	if value_id in seen:
		return 0
	seen.add(value_id)
	if isinstance(value, numpy.ndarray):
		return value.nbytes + sys.getsizeof(value)
	if isinstance(value, dict):
		return sys.getsizeof(value) + sum(
			_cache_value_bytes(key, seen) + _cache_value_bytes(item, seen)
			for key, item in value.items()
		)
	if isinstance(value, (list, tuple, set)):
		return sys.getsizeof(value) + sum(
			_cache_value_bytes(item, seen) for item in value
		)
	return sys.getsizeof(value)


#============================================
class ByteBoundedResidualCache(collections.OrderedDict):
	"""Per-walk raw residual/DoG cache with byte-accounted LRU eviction."""
	def __init__(self, max_bytes: int = RESIDUAL_OBSERVATION_CACHE_MAX_BYTES) -> None:
		super().__init__()
		if max_bytes < 1:
			raise ValueError(f"max_bytes must be >= 1, got {max_bytes}")
		self.max_bytes = max_bytes
		self.total_bytes = 0
		self.eviction_count = 0
		self.oversize_count = 0
		self._entry_bytes = {}
		self._frame_cache = {}

	def get(self, key: object, default: object = None) -> object:
		"""Return a raw entry and refresh its least-recently-used position."""
		if key == "_frames":
			return self._frame_cache
		if not super().__contains__(key):
			return default
		value = super().__getitem__(key)
		self.move_to_end(key)
		return value

	def setdefault(self, key: object, default: object = None) -> object:
		"""Keep the separately bounded frame cache out of raw-entry eviction."""
		if key == "_frames":
			return self._frame_cache
		if super().__contains__(key):
			return self.get(key)
		self[key] = default
		return self.get(key)

	def __setitem__(self, key: object, value: object) -> None:
		"""Store a raw entry only when it fits the declared byte cap."""
		if key == "_frames":
			if not isinstance(value, dict):
				raise TypeError("_frames residual cache entry must be a dict")
			self._frame_cache = value
			return
		entry_bytes = _cache_value_bytes(key) + _cache_value_bytes(value)
		if entry_bytes > self.max_bytes:
			self.oversize_count += 1
			return
		if super().__contains__(key):
			self.total_bytes -= self._entry_bytes.pop(key)
			super().__delitem__(key)
		while self and self.total_bytes + entry_bytes > self.max_bytes:
			old_key, _ = self.popitem(last=False)
			self.total_bytes -= self._entry_bytes.pop(old_key)
			self.eviction_count += 1
		super().__setitem__(key, value)
		self._entry_bytes[key] = entry_bytes
		self.total_bytes += entry_bytes

	def clear(self) -> None:
		"""Release all retained raw and frame-cache references at walk end."""
		super().clear()
		self._entry_bytes.clear()
		self._frame_cache.clear()
		self.total_bytes = 0


#============================================
def make_bounded_residual_cache() -> ByteBoundedResidualCache:
	"""Create the production per-walk image cache with its fixed byte cap."""
	return ByteBoundedResidualCache()


#============================================
def build_warp_matrix(scene_transform: object, frame_n: int, frame_n1: int,
		scale_factor: float) -> numpy.ndarray:
	"""Build the affine transform from frame_n1 into frame_n camera space."""
	cum_dx_n = float(scene_transform.cum_dx[frame_n])
	cum_dy_n = float(scene_transform.cum_dy[frame_n])
	cum_scale_n = float(scene_transform.cum_scale[frame_n])
	cum_dx_n1 = float(scene_transform.cum_dx[frame_n1])
	cum_dy_n1 = float(scene_transform.cum_dy[frame_n1])
	cum_scale_n1 = float(scene_transform.cum_scale[frame_n1])
	rel_scale = cum_scale_n / cum_scale_n1
	tx = (cum_dx_n - cum_dx_n1 * rel_scale) * scale_factor
	ty = (cum_dy_n - cum_dy_n1 * rel_scale) * scale_factor
	warp_matrix = numpy.array([
		[rel_scale, 0.0, tx],
		[0.0, rel_scale, ty],
	], dtype=numpy.float32)
	return warp_matrix


#============================================
def compute_validity_mask(warped: numpy.ndarray) -> numpy.ndarray:
	"""Return the eroded valid-pixel mask for a warped BGR frame."""
	gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
	_, mask = cv2.threshold(gray, 1, 255, cv2.THRESH_BINARY)
	kernel = numpy.ones((3, 3), numpy.uint8)
	mask = cv2.erode(mask, kernel, iterations=1)
	return mask


#============================================
def colorize_jet(mag: numpy.ndarray, fixed_max: float = 30.0) -> numpy.ndarray:
	"""Map residual magnitude to a fixed-scale JET BGR image."""
	normalized = numpy.clip(mag / fixed_max * 255, 0, 255).astype(numpy.uint8)
	colored = cv2.applyColorMap(normalized, cv2.COLORMAP_JET)
	return colored


#============================================
def _evict_frame_cache_to_limit(cache: dict) -> None:
	"""Evict oldest BGR/gray entries until their shared cache is bounded."""
	while len(cache) > MAX_GRAY_CACHE_FRAMES:
		oldest_key = next(iter(cache))
		del cache[oldest_key]


#============================================
def _read_gray_frame(
	reader: object,
	frame_index: int,
	cache: dict,
	evict_frame_cache: FrameCacheEvicter | None = None,
) -> numpy.ndarray:
	"""Read a frame as grayscale float32 using the shared LRU cache."""
	if evict_frame_cache is None:
		evict_frame_cache = _evict_frame_cache_to_limit
	if frame_index in cache:
		gray_float = cache.pop(frame_index)
		cache[frame_index] = gray_float
		return gray_float
	frame_bgr = reader.read_frame(frame_index)
	gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
	gray_float = gray.astype(numpy.float32)
	cache[frame_index] = gray_float
	evict_frame_cache(cache)
	return gray_float


#============================================
def resolve_stride(fps: float) -> int:
	"""Resolve the positive neighbor stride for the fps-invariant window."""
	if fps is None or not (fps > 0):
		raise ValueError(f"resolve_stride requires positive fps; got {fps!r}")
	stride = max(1, round(fps / REFERENCE_FPS))
	return int(stride)


#============================================
def compute_residual_for_frame(
	reader: object, frame_index: int, scene_transform: object,
	half_window: int = DEFAULT_HALF_WINDOW, cache: dict | None = None,
	roi: tuple | None = None, scale_factor: float = 1.0,
	return_extras: bool = False, fps: float | None = None,
	stride: int | None = None, warp_builder: WarpBuilder | None = None,
	validity_builder: ValidityBuilder | None = None,
	read_gray_frame: GrayFrameReader | None = None,
	evict_frame_cache: FrameCacheEvicter | None = None,
	stride_resolver: StrideResolver | None = None,
) -> tuple:
	"""Compute residual magnitude and validity mask for one frame."""
	if warp_builder is None:
		warp_builder = build_warp_matrix
	if validity_builder is None:
		validity_builder = compute_validity_mask
	if read_gray_frame is None:
		read_gray_frame = _read_gray_frame
	if evict_frame_cache is None:
		evict_frame_cache = _evict_frame_cache_to_limit
	if stride_resolver is None:
		stride_resolver = resolve_stride
	if stride is None:
		effective_fps = fps if fps is not None else getattr(reader, "fps", None)
		if effective_fps is None or not (effective_fps > 0):
			raise ValueError(
				"compute_residual_for_frame requires positive fps to resolve "
				"stride. Pass fps or stride explicitly, or use a reader with "
				f"a fps attribute. Got fps={effective_fps!r}."
			)
		stride = stride_resolver(effective_fps)
	stride = int(stride)
	if return_extras and roi is not None:
		raise ValueError("return_extras cannot be combined with roi")
	if scale_factor < 1.0 and roi is not None:
		raise ValueError("scale_factor<1.0 cannot be combined with roi")
	if return_extras or scale_factor < 1.0:
		return _compute_residual_with_extras(
			reader, frame_index, scene_transform, half_window, scale_factor,
			return_extras, stride=stride, warp_builder=warp_builder,
			validity_builder=validity_builder,
		)
	if cache is None:
		cache = {}
	center_full = read_gray_frame(reader, frame_index, cache)
	h_frame, w_frame = center_full.shape[:2]
	if roi is not None:
		rx1, ry1, rx2, ry2 = roi
		center_float = center_full[ry1:ry2, rx1:rx2]
		roi_h, roi_w = center_float.shape[:2]
	else:
		center_float = center_full
		roi_h, roi_w = h_frame, w_frame
		rx1, ry1 = 0, 0
	if roi_h <= 0 or roi_w <= 0:
		raise ValueError(
			f"degenerate ROI (h={roi_h}, w={roi_w}) at frame {frame_index};"
			f" prediction off-frame?"
		)
	aligned_stack = []
	for k in range(-half_window, half_window + 1):
		if k == 0:
			continue
		fi_other = frame_index + k * stride
		if fi_other < 0 or fi_other >= reader.frame_count:
			continue
		cache_key_bgr = ("bgr", fi_other)
		if cache_key_bgr in cache:
			other_bgr = cache[cache_key_bgr]
		else:
			other_bgr = reader.read_frame(fi_other)
			cache[cache_key_bgr] = other_bgr
			evict_frame_cache(cache)
		bin_factor = getattr(reader, "bin_factor", 1)
		warp_mat = warp_builder(scene_transform, frame_index, fi_other, 1.0 / bin_factor)
		roi_warp = warp_mat.copy()
		roi_warp[0, 2] -= rx1
		roi_warp[1, 2] -= ry1
		warped = cv2.warpAffine(other_bgr, roi_warp, (roi_w, roi_h))
		pair_validity = validity_builder(warped)
		gray_warped = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
		warped_float = gray_warped.astype(numpy.float32)
		warped_float[pair_validity == 0] = numpy.nan
		aligned_stack.append(warped_float)
	if len(aligned_stack) < 2:
		raise RuntimeError(
			f"insufficient neighbors at frame {frame_index}:"
			f" aligned_stack len={len(aligned_stack)}"
		)
	stack_array = numpy.stack(aligned_stack, axis=0)
	with warnings.catch_warnings():
		warnings.simplefilter("ignore", RuntimeWarning)
		median_bg = numpy.nanmedian(stack_array, axis=0).astype(numpy.float32)
	valid_count = numpy.sum(~numpy.isnan(stack_array), axis=0)
	validity_mask = (valid_count >= 2).astype(numpy.uint8) * 255
	residual = numpy.abs(center_float - median_bg)
	residual[validity_mask == 0] = 0.0
	return (residual, validity_mask)


#============================================
def _compute_residual_with_extras(
	reader: object, frame_index: int, scene_transform: object, half_window: int,
	scale_factor: float, return_extras: bool, stride: int = 1,
	warp_builder: WarpBuilder | None = None,
	validity_builder: ValidityBuilder | None = None,
) -> tuple:
	"""Compute the diagnose-compatible residual path and optional extras."""
	if warp_builder is None:
		warp_builder = build_warp_matrix
	if validity_builder is None:
		validity_builder = compute_validity_mask
	center_frame = reader.read_frame(frame_index)
	h_orig, w_orig = center_frame.shape[:2]
	if scale_factor < 1.0:
		new_w = int(w_orig * scale_factor)
		new_h = int(h_orig * scale_factor)
		center_resized = cv2.resize(center_frame, (new_w, new_h))
	else:
		new_w = w_orig
		new_h = h_orig
		center_resized = center_frame.copy()
	gray_center = cv2.cvtColor(center_resized, cv2.COLOR_BGR2GRAY)
	center_float = gray_center.astype(numpy.float32)
	aligned_stack = []
	for k in range(-half_window, half_window + 1):
		if k == 0:
			continue
		fi_other = frame_index + k * stride
		if fi_other < 0 or fi_other >= reader.frame_count:
			continue
		other_frame = reader.read_frame(fi_other)
		if scale_factor < 1.0:
			other_frame = cv2.resize(other_frame, (new_w, new_h))
		warp_mat = warp_builder(scene_transform, frame_index, fi_other, scale_factor)
		warped = cv2.warpAffine(other_frame, warp_mat, (new_w, new_h))
		pair_validity = validity_builder(warped)
		gray_warped = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
		warped_float = gray_warped.astype(numpy.float32)
		warped_float[pair_validity == 0] = numpy.nan
		aligned_stack.append(warped_float)
	if len(aligned_stack) < 2:
		raise RuntimeError(
			f"insufficient neighbors at frame {frame_index} (extras path):"
			f" aligned_stack len={len(aligned_stack)}"
		)
	stack_array = numpy.stack(aligned_stack, axis=0)
	with warnings.catch_warnings():
		warnings.simplefilter("ignore", RuntimeWarning)
		median_background = numpy.nanmedian(stack_array, axis=0).astype(numpy.float32)
	valid_count = numpy.sum(~numpy.isnan(stack_array), axis=0)
	validity_mask = (valid_count >= 2).astype(numpy.uint8) * 255
	residual = numpy.abs(center_float - median_background)
	residual[validity_mask == 0] = 0.0
	if not return_extras:
		return (residual, validity_mask)
	if frame_index + 1 < reader.frame_count:
		frame_n1 = reader.read_frame(frame_index + 1)
		if scale_factor < 1.0:
			frame_n1 = cv2.resize(frame_n1, (new_w, new_h))
		gray_n1 = cv2.cvtColor(frame_n1, cv2.COLOR_BGR2GRAY)
		raw_mag = numpy.abs(center_float - gray_n1.astype(numpy.float32))
	else:
		raw_mag = numpy.zeros((new_h, new_w), dtype=numpy.float32)
	return (residual, raw_mag, validity_mask, center_resized)
