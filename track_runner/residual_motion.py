"""Per-frame blob observation from residual (camera-compensated) motion.

Provides a stateless API for extracting a single candidate blob observation
at one frame, given a predicted position and local tangent. The returned
observation is a raw measurement; all gating decisions (proximity, direction,
temporal smoothness) live in the caller (the propagator).

No cross-frame state lives in this module. The chained/global motion-cue
fusion that previously existed here was removed when the propagator took
over per-frame correction directly.

Residual observer contract (M6): the residual pre-pass observer and the
on-the-fly compute_residual_for_frame path produce byte-identical results
for any given (frame_index, roi) pair when both use the same backend
(PyAV or cv2). The PyAV backend is the canonical reference for M6+.

Public surface:
  - observe_blob_at(...): primary API; returns BlobObservation or None.
  - BlobObservation: dataclass carrying a single per-frame measurement.
  - compute_residual_for_frame(...): low-level residual + validity mask.
  - dog_filter_blob_scale(...): DoG band-pass tuned to a target blob diameter.
  - extract_frame_blobs(...): connected-component extraction from a residual.
  - filter_blobs_to_corridor(...): cross-track corridor filter.
  - compute_trajectory_tangent(...): local tangent from a solved trajectory.
  - compute_cue_confidence(...): scalar cue score per blob.

Cache invalidation for observe_blob_at semantic changes is handled by
the unified SCHEMA_VERSION model in tr_schema.py (see contract C9).
There is intentionally no per-module BLOB_OBSERVER_VERSION constant.
"""

# Standard Library
import warnings
import dataclasses

# PIP3 modules
import cv2
import numpy

# local repo modules
import blob_trace
import common_tools.coord_space as coord_space

# === Tunable constants ===

# minimum blob area in pixels to suppress noise specks.
# TODO(C2): May be a runner-size threshold (C2 violation) or a denoising
# floor (C2-allowed raster-kernel territory). Empirically verify on a
# distant-runner interval (torso_h ~15) before deciding: count blobs in
# the 10-50 px^2 band and inspect whether they are real runner components
# or scattered speckle. See docs/TODO.md for the evidence-gathering plan.
MIN_BLOB_AREA = 25

# motion intensity threshold for blob extraction
DEFAULT_THRESHOLD = 10.0

# DoG k-factor default (sigma_2 / sigma_1) for the band-pass filter.
# Paper default is 1.1 (DoG Picker, Yoshioka et al. 2009); 1.6 is the
# SIFT/blob-detection classic. Empirically, on residual-motion heat
# maps from this project's running-subject footage, values in 2-5 give
# much stronger response than 1.1-1.6 -- the tight-band paper settings
# leave the torso blob too dim relative to sub-torso speckle. The
# torso blob is also long-aspect elliptical (not perfectly round), so
# a wider DoG band better captures the elongated component shape than
# the tighter k=1.1-1.6 settings. Empirically k=5 gives noticeably
# better response than k=3 on this project's footage.
DOG_K_FACTOR_DEFAULT = 5.0

# Minimum target diameter (pixels) below which the DoG kernel collapses.
# Callers passing smaller diameters get the input array unchanged.
DOG_MIN_DIAMETER = 4.0

# Reference fps anchor for the stride model. Neighbor stride is
# round(fps / REFERENCE_FPS), so 60 fps -> stride 1 (byte-identical
# to legacy 60 fps behavior), 120 fps -> stride 2, 240 fps -> stride 4.
# Total neighbor count stays 8 (4 each side). Time span is fixed at
# ~133 ms regardless of fps. See M2 of plan memoized-percolating-moler.md.
REFERENCE_FPS = 60

# Default per-side neighbor count for residual computation. The window
# is 2 * DEFAULT_HALF_WINDOW + 1 = 9 samples total, of which 8 are
# actually used (k=0 is skipped). Fixed regardless of fps; the time
# span is controlled by stride instead.
DEFAULT_HALF_WINDOW = 4

# upper bound on the per-interval grayscale frame cache. Each cached
# frame at 2816x1584 float32 is ~17.8 MB; an unbounded cache filled the
# entire interval (hundreds of frames per worker, tens of GB across the
# pool) and was the cause of the 2026-04-25 batch-solve OOM crashes.
# With the M2 stride model, the worst-case neighbor offset is
# DEFAULT_HALF_WINDOW * stride. At 240 fps stride=4, so the outermost
# neighbor lands at frame +/-16; a cache cap of 40 entries covers the
# rolling window (33 frames) plus FWD/BWD overlap at any fps.
MAX_GRAY_CACHE_FRAMES = 40

# ROI multiplier: crop region is this many times the torso box height
ROI_MULTIPLIER = 8.0

# ROI quantization step in pixels. The ROI bounds are snapped to a
# multiple of this value so that tiny FWD/BWD pred_center differences
# (a fraction of a pixel, a few pixels at most) resolve to the SAME ROI
# tuple and share a cache entry. Only meaningful divergence -- tens of
# pixels, which happens on tight curves and crowd scenes -- produces a
# distinct cache key and triggers a separate residual computation.
# 8 px is well below the typical ROI side length (240+ px at 60 px box)
# so quantization does not meaningfully change which pixels are covered.
ROI_QUANT = 8

# tangent estimation: minimum half-window
TANGENT_MIN_SPAN = 5

# tangent estimation: fallback half-window for low-confidence regions
TANGENT_FALLBACK_SPAN = 10

# tangent estimation: minimum confidence for primary window
TANGENT_CONFIDENCE_THRESHOLD = 0.5

#============================================
def build_warp_matrix(
	scene_transform: object,
	frame_n: int,
	frame_n1: int,
	scale_factor: float,
) -> numpy.ndarray:
	"""Build 2x3 affine matrix to warp frame N+1 into frame N's camera position.

	The SceneTransform stores cumulative motion. To warp N+1 into N's space,
	we need the delta transform: how did the camera move from N to N+1.

	Args:
		scene_transform: SceneTransform instance with cum_dx, cum_dy, cum_scale.
		frame_n: Source reference frame index.
		frame_n1: Frame to warp into frame_n's space.
		scale_factor: Downsample factor applied to frames.

	Returns:
		2x3 numpy float32 affine matrix for cv2.warpAffine.
	"""
	# cumulative values at each frame
	cum_dx_n = float(scene_transform.cum_dx[frame_n])
	cum_dy_n = float(scene_transform.cum_dy[frame_n])
	cum_scale_n = float(scene_transform.cum_scale[frame_n])

	cum_dx_n1 = float(scene_transform.cum_dx[frame_n1])
	cum_dy_n1 = float(scene_transform.cum_dy[frame_n1])
	cum_scale_n1 = float(scene_transform.cum_scale[frame_n1])

	# relative scale: how much to scale frame N+1 to match frame N
	rel_scale = cum_scale_n / cum_scale_n1

	# translation delta in frame N pixel space
	tx = (cum_dx_n - cum_dx_n1 * rel_scale) * scale_factor
	ty = (cum_dy_n - cum_dy_n1 * rel_scale) * scale_factor

	# build 2x3 affine matrix
	warp_matrix = numpy.array([
		[rel_scale, 0.0, tx],
		[0.0, rel_scale, ty],
	], dtype=numpy.float32)
	return warp_matrix


#============================================
def compute_validity_mask(
	warped: numpy.ndarray,
) -> numpy.ndarray:
	"""Create a mask of valid (non-black) pixels after warping.

	Pixels that land outside the source frame after warpAffine are black.
	These must be excluded from residual computation.

	Args:
		warped: Warped BGR frame.

	Returns:
		Binary mask (uint8, 255=valid, 0=invalid).
	"""
	gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
	_, mask = cv2.threshold(gray, 1, 255, cv2.THRESH_BINARY)
	kernel = numpy.ones((3, 3), numpy.uint8)
	mask = cv2.erode(mask, kernel, iterations=1)
	return mask


#============================================
def colorize_jet(
	mag: numpy.ndarray,
	fixed_max: float = 30.0,
) -> numpy.ndarray:
	"""Map residual magnitude to a JET BGR image with fixed scale.

	Shared primitive used by the heat-map overlay, the diagnose tool, and
	the interval-blob funnel diagnostic. Fixed-scale normalization keeps
	colors comparable across frames and across callers. Default
	`fixed_max=30.0` matches the long-standing diagnose-tool behavior.

	Args:
		mag: Residual magnitude array (float32, HxW).
		fixed_max: Magnitude value mapped to full JET red. Default 30.0.

	Returns:
		BGR uint8 image of shape (H, W, 3).
	"""
	# clip and normalize magnitudes into the 0-255 uint8 range, then apply
	# OpenCV's JET colormap. Values >= fixed_max saturate to full red; zero
	# maps to dark blue.
	normalized = numpy.clip(mag / fixed_max * 255, 0, 255).astype(numpy.uint8)
	colored = cv2.applyColorMap(normalized, cv2.COLORMAP_JET)
	return colored


#============================================
def dog_filter_blob_scale(
	mag: numpy.ndarray,
	diameter: float,
	k: float = DOG_K_FACTOR_DEFAULT,
) -> numpy.ndarray:
	"""Difference-of-Gaussians band-pass tuned to a target blob diameter.

	Enhances image-plane blobs of the given diameter and suppresses both
	smaller speckle and larger structures. The sigma selection follows
	the Laplacian-of-Gaussian peak-radius relation r = sqrt(2) * sigma
	(DoG Picker, Yoshioka et al. 2009, eq. 4).

	Args:
		mag: Input image (float32, HxW). Not modified.
		diameter: Target blob diameter in pixels of `mag`.
		k: DoG k-factor (sigma_2 / sigma_1). 1.1 is the paper default
			(tight band, most LoG-faithful); 1.6 is SIFT classic (wider
			band, stronger response); ~5 is human-visual-system wide.

	Returns:
		Float32 band-pass filtered array of the same shape as `mag`,
		with negative-lobe response clipped to 0. If `diameter` is
		below `DOG_MIN_DIAMETER`, returns `mag` unchanged.
	"""
	# guard against tiny target sizes where the kernel would collapse
	if diameter < DOG_MIN_DIAMETER:
		return mag
	# LoG peaks at r = sqrt(2) * sigma; set sigma_1 so the filter's
	# zero-crossing radius matches the target blob radius
	radius = diameter / 2.0
	sigma_1 = radius / numpy.sqrt(2.0)
	sigma_2 = k * sigma_1
	# real-space Gaussian blurs; OpenCV derives ksize from sigma when 0,0
	blur_1 = cv2.GaussianBlur(mag, (0, 0), sigmaX=sigma_1, sigmaY=sigma_1)
	blur_2 = cv2.GaussianBlur(mag, (0, 0), sigmaX=sigma_2, sigmaY=sigma_2)
	# positive-center DoG; negative lobes are not useful for blob picking
	dog = blur_1 - blur_2
	dog = numpy.clip(dog, 0.0, None).astype(numpy.float32)
	return dog


#============================================
def extract_frame_blobs(
	mag: numpy.ndarray,
	validity_mask: numpy.ndarray,
	threshold: float,
	top_k: int = 10,
) -> list:
	"""Extract top-K motion blobs from a residual magnitude image.

	Args:
		mag: Residual magnitude array.
		validity_mask: Binary validity mask (255=valid).
		threshold: Motion threshold.
		top_k: Maximum number of blobs to return.

	Returns:
		List of dicts with keys: centroid_x, centroid_y, area,
		integrated_mag, label_id.
	"""
	# threshold and mask
	thresh_mask = (mag > threshold).astype(numpy.uint8)
	thresh_mask = thresh_mask & (validity_mask > 0).astype(numpy.uint8)
	num_labels, labels, label_stats, centroids = cv2.connectedComponentsWithStats(
		thresh_mask, connectivity=8
	)

	blobs = []
	for label_id in range(1, num_labels):
		area = int(label_stats[label_id, cv2.CC_STAT_AREA])
		# 2026-05-28 re-scope: MIN_BLOB_AREA is no longer a hard discard
		# (per audit HIGH finding: small/distant runners were being culled).
		# Expose small_blob as a per-blob feature; the consumer decides
		# what's "too small". See dump_step1/BLOB_EXTRACTION_CODE_AUDIT.md.
		component_pixels = labels == label_id
		integrated = float(numpy.sum(mag[component_pixels]))
		cx = float(centroids[label_id][0])
		cy = float(centroids[label_id][1])
		# bbox via CC stats (left, top, width, height) for the feature set
		bx = int(label_stats[label_id, cv2.CC_STAT_LEFT])
		by = int(label_stats[label_id, cv2.CC_STAT_TOP])
		bw = int(label_stats[label_id, cv2.CC_STAT_WIDTH])
		bh = int(label_stats[label_id, cv2.CC_STAT_HEIGHT])
		blobs.append({
			"centroid_x": cx,
			"centroid_y": cy,
			"area": area,
			"bbox": (bx, by, bw, bh),
			"integrated_mag": integrated,
			"label_id": label_id,
			"small_blob": area < MIN_BLOB_AREA,
		})

	# sort by integrated magnitude descending, keep top K
	blobs.sort(key=lambda b: b["integrated_mag"], reverse=True)
	result = blobs[:top_k]
	return result


#============================================
def filter_blobs_to_corridor(
	blobs: list,
	ref_x: float,
	ref_y: float,
	tangent: tuple,
	corridor_radius: float,
) -> list:
	"""Filter blobs to those within a corridor around a reference point.

	The corridor is defined by a center point, a tangent direction, and
	a half-width. Blobs are kept if their cross-track distance from the
	reference is within the corridor radius.

	Args:
		blobs: List of blob dicts from extract_frame_blobs.
		ref_x: Corridor center x.
		ref_y: Corridor center y.
		tangent: Tuple of (tx, ty, nx, ny) from compute_trajectory_tangent.
		corridor_radius: Half-width of the corridor.

	Returns:
		Filtered list of blob dicts (with cross_track and along_track added).
	"""
	tx, ty, nx, ny = tangent
	result = []
	for blob in blobs:
		dx = blob["centroid_x"] - ref_x
		dy = blob["centroid_y"] - ref_y
		# decompose into along-track and cross-track
		along = dx * tx + dy * ty
		cross = dx * nx + dy * ny
		if abs(cross) <= corridor_radius:
			blob_copy = dict(blob)
			blob_copy["cross_track"] = cross
			blob_copy["along_track"] = along
			result.append(blob_copy)
	return result


#============================================
def _read_gray_frame(
	reader: object,
	frame_index: int,
	cache: dict,
) -> numpy.ndarray:
	"""Read a frame as grayscale float32, using cache when available.

	Cache is bounded to MAX_GRAY_CACHE_FRAMES entries, evicted in
	insertion order (Python 3.7+ dicts preserve insertion order, so
	iterating yields oldest-first). On a hit, the entry is re-inserted
	to mark it as most-recently-used; on a miss, the new entry goes in
	and the oldest entries are evicted until the cap is met. Numpy
	arrays are released as soon as the dict drops the reference.

	Args:
		reader: VideoReader instance.
		frame_index: Frame to read.
		cache: Dict mapping frame_index -> grayscale float32 array.

	Returns:
		Grayscale float32 array.
	"""
	# cache hit: move to recent end so the LRU eviction picks the
	# truly oldest entry rather than this one
	if frame_index in cache:
		gray_float = cache.pop(frame_index)
		cache[frame_index] = gray_float
		return gray_float
	# cache miss: read, convert, insert
	frame_bgr = reader.read_frame(frame_index)
	gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
	gray_float = gray.astype(numpy.float32)
	cache[frame_index] = gray_float
	# evict oldest until at or below cap. dict iteration is in
	# insertion order, so the first key is the oldest.
	while len(cache) > MAX_GRAY_CACHE_FRAMES:
		oldest_key = next(iter(cache))
		del cache[oldest_key]
	return gray_float


#============================================
def _compute_roi(
	pred_cx: float,
	pred_cy: float,
	pred_h: float,
	frame_w: int,
	frame_h: int,
) -> tuple:
	"""Compute ROI bounds for residual computation around predicted position.

	Uses ROI_MULTIPLIER * pred_h as the square crop side length.
	Clamps to frame boundaries. Bounds are snapped to multiples of
	ROI_QUANT so that sub-quantum jitter in pred_cx / pred_cy (e.g.
	the few-pixel FWD vs BWD disagreement common on symmetric motion)
	produces IDENTICAL ROI tuples and therefore shares the cache
	entry. Larger divergence (tight curves, crowds) yields distinct
	ROIs and correctly triggers a per-pass residual computation.

	Args:
		pred_cx: Predicted center x in pixels.
		pred_cy: Predicted center y in pixels.
		pred_h: Predicted box height in pixels.
		frame_w: Full frame width.
		frame_h: Full frame height.

	Returns:
		Tuple of (x1, y1, x2, y2) pixel bounds, quantized to
		ROI_QUANT and clamped to frame.
	"""
	half_side = int(ROI_MULTIPLIER * pred_h / 2)
	# minimum ROI size of 100px
	half_side = max(half_side, 50)

	# quantize the center so small jitter collapses to the same bucket
	quant_cx = int(round(pred_cx / ROI_QUANT)) * ROI_QUANT
	quant_cy = int(round(pred_cy / ROI_QUANT)) * ROI_QUANT

	# clamp BOTH bounds to frame extents; without an upper clamp on x1/y1,
	# an off-frame prediction (pred_cx > frame_w) produced x1 > x2 and a
	# zero-width slice that downstream code mis-handled.
	x1 = max(0, min(frame_w, quant_cx - half_side))
	y1 = max(0, min(frame_h, quant_cy - half_side))
	x2 = max(x1, min(frame_w, quant_cx + half_side))
	y2 = max(y1, min(frame_h, quant_cy + half_side))
	return (x1, y1, x2, y2)


#============================================
def resolve_stride(fps: float) -> int:
	"""Resolve the neighbor stride for the residual sampling pattern.

	The neighbor window is 9 samples (DEFAULT_HALF_WINDOW=4 each side
	plus center, with center skipped). Stride controls the TIME between
	samples so that higher-fps inputs span the same ~133 ms window the
	60 fps default produces. The runner moves roughly the same physical
	distance between samples no matter what fps the camera was set to.

	At 60 fps: stride=1, offsets [-4, -3, -2, -1, 1, 2, 3, 4].
	  Byte-identical to the legacy fixed half_window=4 behavior.
	At 119.94 fps: stride=2, offsets [-8, -6, -4, -2, 2, 4, 6, 8].
	  Same ~133 ms span, half the I/O vs the prior adaptive-count model.
	At 240 fps: stride=4, offsets [-16, -12, -8, -4, 4, 8, 12, 16].
	  Same ~133 ms span, quarter the I/O.

	Args:
		fps: Source video frame rate. Must be > 0.

	Returns:
		Integer stride >= 1.

	Raises:
		ValueError: fps is None, zero, or negative.
	"""
	if fps is None or not (fps > 0):
		raise ValueError(
			f"resolve_stride requires positive fps; got {fps!r}"
		)
	stride = max(1, round(fps / REFERENCE_FPS))
	return int(stride)


#============================================
def compute_residual_for_frame(
	reader: object,
	frame_index: int,
	scene_transform: object,
	half_window: int = DEFAULT_HALF_WINDOW,
	cache: dict = None,
	roi: tuple = None,
	scale_factor: float = 1.0,
	return_extras: bool = False,
	fps: float = None,
	stride: int = None,
) -> tuple:
	"""Compute residual magnitude and validity mask for one frame.

	Warps neighboring frames into frame_index's camera position, builds
	a median background from the aligned stack, and subtracts it to
	reveal moving objects.

	The neighbor offsets use the M2 fps-invariant stride model:
	  offsets = [k * stride for k in range(-half_window, half_window+1) if k != 0]
	where stride = resolve_stride(fps) when stride is None. At 60 fps
	stride=1, so offsets are [-4, -3, -2, -1, 1, 2, 3, 4] -- byte-identical
	to the legacy behavior. At 119.94 fps stride=2, so offsets are
	[-8, -6, -4, -2, 2, 4, 6, 8] -- same ~133 ms time span, half the I/O.

	When roi is provided, only processes that region of each frame,
	reducing compute cost proportional to the area ratio.

	Uses cache dict to avoid re-reading full frames in sequential processing.

	M6 contract: When called via a reader with a given backend (PyAV or cv2),
	the output is byte-identical to the precomputed residual store produced
	by the residual pre-pass for the same backend. The PyAV backend is the
	canonical reference for M6+.

	Args:
		reader: VideoReader instance.
		frame_index: Center frame index.
		scene_transform: SceneTransform instance.
		half_window: Per-side neighbor count. Fixed at DEFAULT_HALF_WINDOW=4
			regardless of fps; time span is controlled by stride instead.
		fps: Source video fps. Used to resolve stride when stride is None.
			If None, pulled from reader.fps. Required (non-positive raises
			ValueError) when stride is also None.
		stride: Neighbor stride derived from fps via resolve_stride. When
			None, resolved automatically from fps or reader.fps.
		cache: Optional dict for frame caching. Modified in place.
		roi: Optional (x1, y1, x2, y2) bounds to restrict computation.
			Blob centroids are returned in ROI coordinates; caller must
			add roi offsets to restore full-frame coordinates.
		scale_factor: Downsample factor applied to full frames before
			warping. Must be 1.0 when roi is set. Default 1.0.
		return_extras: When True, also returns the uncompensated single-pair
			null-baseline residual and the (possibly resized) center BGR
			frame for diagnostic overlays. Not compatible with roi.

	Returns:
		When return_extras=False: (residual_mag, validity_mask) or
			(None, None).
		When return_extras=True: (residual_mag, raw_mag_single,
			validity_mask, display_frame) or (None, None, None, None).
		The 4-tuple order matches tools/diagnose_residual_motion.py's
		historical compute_multiframe_flow contract.
	"""
	# resolve stride from fps when not provided explicitly
	if stride is None:
		effective_fps = fps if fps is not None else getattr(reader, "fps", None)
		if effective_fps is None or not (effective_fps > 0):
			raise ValueError(
				"compute_residual_for_frame requires positive fps to resolve "
				"stride. Pass fps or stride explicitly, or use a reader with "
				f"a fps attribute. Got fps={effective_fps!r}."
			)
		stride = resolve_stride(effective_fps)
	stride = int(stride)

	# guard incompatible option combos
	if return_extras and roi is not None:
		raise ValueError("return_extras cannot be combined with roi")
	if scale_factor < 1.0 and roi is not None:
		raise ValueError("scale_factor<1.0 cannot be combined with roi")

	# diagnose-compatible code path: read BGR directly, optionally resize,
	# then compute the residual + null-baseline + display frame. Bypasses
	# the grayscale cache because the extras need BGR for display.
	if return_extras or scale_factor < 1.0:
		return _compute_residual_with_extras(
			reader, frame_index, scene_transform, half_window,
			scale_factor, return_extras, stride=stride,
		)

	# production code path: grayscale cache + optional ROI; no extras.
	if cache is None:
		cache = {}

	# read center frame as grayscale float (full frame, cached)
	center_full = _read_gray_frame(reader, frame_index, cache)
	h_frame, w_frame = center_full.shape[:2]

	# crop to ROI if specified
	if roi is not None:
		rx1, ry1, rx2, ry2 = roi
		center_float = center_full[ry1:ry2, rx1:rx2]
		roi_h, roi_w = center_float.shape[:2]
	else:
		center_float = center_full
		roi_h, roi_w = h_frame, w_frame
		rx1, ry1 = 0, 0

	# degenerate ROI (prediction off-frame, or clamp produced zero area):
	# downstream cv2.warpAffine calls with a zero-size dsize can yield
	# an unexpected full-frame-shaped result in this code path.
	# This is a caller bug (off-frame prediction), not a frame read error.
	if roi_h <= 0 or roi_w <= 0:
		raise ValueError(
			f"degenerate ROI (h={roi_h}, w={roi_w}) at frame {frame_index};"
			f" prediction off-frame?"
		)

	# collect aligned neighbor frames into a stack for median computation
	aligned_stack = []
	neighbors_read = 0
	# stride-spaced neighbor offsets: k * stride for k in [-half_window..+half_window] \ {0}
	# at stride=1 (60 fps) this is contiguous [-4..-1, 1..4] -- byte-identical to legacy
	# at stride=2 (120 fps) this is [-8, -6, -4, -2, 2, 4, 6, 8] -- same time span, half I/O
	for k in range(-half_window, half_window + 1):
		if k == 0:
			continue
		fi_other = frame_index + k * stride
		if fi_other < 0 or fi_other >= reader.frame_count:
			continue
		neighbors_read += 1

		# read neighbor frame as BGR for warping (use cache to avoid re-reads)
		cache_key_bgr = ("bgr", fi_other)
		if cache_key_bgr in cache:
			other_bgr = cache[cache_key_bgr]
		else:
			other_bgr = reader.read_frame(fi_other)
			cache[cache_key_bgr] = other_bgr

		# warp full frame into center frame's camera position
		# motion_track dx/dy are stored in source-pixel units; frames are
		# post-bin so scale_factor must be 1/bin_factor to match.
		bin_factor = getattr(reader, "bin_factor", 1)
		warp_mat = build_warp_matrix(
			scene_transform, frame_index, fi_other, 1.0 / bin_factor,
		)
		# warp only the ROI region by adjusting output size and offset
		# translate warp matrix to ROI origin
		roi_warp = warp_mat.copy()
		roi_warp[0, 2] -= rx1
		roi_warp[1, 2] -= ry1
		warped = cv2.warpAffine(other_bgr, roi_warp, (roi_w, roi_h))

		# validity mask for warped regions
		pair_validity = compute_validity_mask(warped)

		# convert to grayscale float
		gray_warped = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
		warped_float = gray_warped.astype(numpy.float32)

		# set invalid pixels to NaN so median ignores them
		warped_float[pair_validity == 0] = numpy.nan
		aligned_stack.append(warped_float)

	if len(aligned_stack) < 2:
		raise RuntimeError(
			f"insufficient neighbors at frame {frame_index}:"
			f" aligned_stack len={len(aligned_stack)}"
		)

	# build median background from aligned stack
	stack_array = numpy.stack(aligned_stack, axis=0)
	# suppress All-NaN slice warning; edge pixels may have no valid frames
	with warnings.catch_warnings():
		warnings.simplefilter("ignore", RuntimeWarning)
		median_bg = numpy.nanmedian(stack_array, axis=0).astype(numpy.float32)

	# combined validity mask: valid where at least 2 frames contributed
	valid_count = numpy.sum(~numpy.isnan(stack_array), axis=0)
	validity_mask = (valid_count >= 2).astype(numpy.uint8) * 255

	# compute residual: absolute difference between center and median background
	residual = numpy.abs(center_float - median_bg)
	residual[validity_mask == 0] = 0.0

	return (residual, validity_mask)


#============================================
def _compute_residual_with_extras(
	reader: object,
	frame_index: int,
	scene_transform: object,
	half_window: int,
	scale_factor: float,
	return_extras: bool,
	stride: int = 1,
) -> tuple:
	"""Diagnose-compatible residual computation with optional extras.

	Direct port of the body formerly in
	tools/diagnose_residual_motion.compute_multiframe_flow. Keeps the
	same frame-read order (BGR -> resize -> cvtColor) so diagnostic PNG
	output matches the pre-refactor reference byte-for-byte.

	The stride parameter comes from the M2 fps-invariant model. At
	stride=1 (60 fps) the neighbor offsets are contiguous and the
	output is byte-identical to the pre-M2 behavior.

	Returns a 4-tuple when return_extras is True:
		(residual_mag, raw_mag_single, validity_mask, display_frame)
	or a 2-tuple otherwise:
		(residual_mag, validity_mask)

	Failure sentinel matches the return shape (all None).
	"""
	# read center frame
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

	# convert center frame to grayscale float for residual computation
	gray_center = cv2.cvtColor(center_resized, cv2.COLOR_BGR2GRAY)
	center_float = gray_center.astype(numpy.float32)

	# collect aligned neighbor frames into a stack for median computation
	# stride-spaced offsets match the production path (M2 fps-invariant model)
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

		# warp into frame N's camera position
		warp_mat = build_warp_matrix(
			scene_transform, frame_index, fi_other, scale_factor,
		)
		warped = cv2.warpAffine(other_frame, warp_mat, (new_w, new_h))

		# validity mask for warped regions
		pair_validity = compute_validity_mask(warped)

		# convert to grayscale float
		gray_warped = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
		warped_float = gray_warped.astype(numpy.float32)

		# set invalid pixels to NaN so median ignores them
		warped_float[pair_validity == 0] = numpy.nan

		aligned_stack.append(warped_float)

	if len(aligned_stack) < 2:
		raise RuntimeError(
			f"insufficient neighbors at frame {frame_index} (extras path):"
			f" aligned_stack len={len(aligned_stack)}"
		)

	# build median background from aligned stack
	stack_array = numpy.stack(aligned_stack, axis=0)
	# suppress All-NaN slice warning; edge pixels may have no valid frames
	with warnings.catch_warnings():
		warnings.simplefilter("ignore", RuntimeWarning)
		median_background = numpy.nanmedian(stack_array, axis=0).astype(numpy.float32)

	# combined validity mask: valid where at least 2 frames contributed
	valid_count = numpy.sum(~numpy.isnan(stack_array), axis=0)
	validity_mask = (valid_count >= 2).astype(numpy.uint8) * 255

	# compute residual: absolute difference between frame N and median background
	residual = numpy.abs(center_float - median_background)
	residual[validity_mask == 0] = 0.0

	if not return_extras:
		return (residual, validity_mask)

	# compute raw single-pair residual as null baseline (extras only).
	# frame_index + 1 may be past video end; guard with frame_count.
	if frame_index + 1 < reader.frame_count:
		frame_n1 = reader.read_frame(frame_index + 1)
		if scale_factor < 1.0:
			frame_n1 = cv2.resize(frame_n1, (new_w, new_h))
		gray_n1 = cv2.cvtColor(frame_n1, cv2.COLOR_BGR2GRAY)
		raw_mag = numpy.abs(
			center_float - gray_n1.astype(numpy.float32)
		)
	else:
		raw_mag = numpy.zeros((new_h, new_w), dtype=numpy.float32)

	return (residual, raw_mag, validity_mask, center_resized)


#============================================
def compute_trajectory_tangent(
	trajectory: list,
	frame_index: int,
) -> tuple:
	"""Compute tangent direction from a solved trajectory at a frame.

	Uses minimum +/-TANGENT_MIN_SPAN frames. Falls back to wider window
	(+/-TANGENT_FALLBACK_SPAN) if confidence is low in the primary range.
	Returns (1, 0, 0, 1) if tangent cannot be computed (disables
	anisotropic decomposition for that frame).

	Args:
		trajectory: List of state dicts.
		frame_index: Frame to compute tangent at.

	Returns:
		Tuple of (tx, ty, nx, ny) as unit vectors.
	"""
	num_frames = len(trajectory)

	# try primary window first, then fallback
	for span in (TANGENT_MIN_SPAN, TANGENT_FALLBACK_SPAN):
		lo = max(0, frame_index - span)
		hi = min(num_frames - 1, frame_index + span)
		if lo >= hi:
			continue

		# check that endpoints have valid trajectory entries
		entry_lo = trajectory[lo]
		entry_hi = trajectory[hi]
		if entry_lo is None or entry_hi is None:
			continue

		# for primary span, check confidence threshold
		if span == TANGENT_MIN_SPAN:
			conf_lo = float(entry_lo["conf"])
			conf_hi = float(entry_hi["conf"])
			if conf_lo < TANGENT_CONFIDENCE_THRESHOLD or conf_hi < TANGENT_CONFIDENCE_THRESHOLD:
				# try fallback span
				continue

		dx = float(entry_hi["cx"]) - float(entry_lo["cx"])
		dy = float(entry_hi["cy"]) - float(entry_lo["cy"])
		magnitude = (dx**2 + dy**2)**0.5

		# tangent too short to be meaningful
		if magnitude < 0.001:
			continue

		# normalize to unit vector
		t_x = dx / magnitude
		t_y = dy / magnitude
		# normal is perpendicular (rotate 90 degrees)
		n_x = -t_y
		n_y = t_x
		return (t_x, t_y, n_x, n_y)

	# cannot compute tangent -- disables anisotropic decomposition
	return (1.0, 0.0, 0.0, 1.0)


#============================================
def compute_cue_confidence(
	blob: dict,
	pred_cx: float,
	pred_cy: float,
	pred_w: float,
	pred_h: float,
	tangent: tuple = None,
) -> float:
	"""Compute logged metadata score components for a motion blob.

	2026-05-28 re-scope: this score is METADATA only, used for ranking/
	diagnostics. It does NOT filter blobs and does NOT pick a winner.
	The direction-aware area_ratio penalty was removed (audit HIGH:
	penalized real runners on wide shots). The size-vs-predicted term
	is no longer included in total_score.

	Components retained:
	  - strength_score: integrated_mag normalized (heat-map intensity).
	  - proximity_score: isotropic distance from expected location,
	    LOGGED per audit boundary; not used to discard candidates.

	Args:
		blob: Blob dict with centroid_x, centroid_y, area, integrated_mag.
		pred_cx: Predicted center x.
		pred_cy: Predicted center y.
		pred_w: Predicted box width.
		pred_h: Predicted box height.
		tangent: Accepted for backward compatibility, unused.

	Returns:
		Float in [0, 1]. Metadata only.
	"""
	_ = tangent
	# strength: integrated magnitude normalized
	strength = min(float(blob["integrated_mag"]) / 10000.0, 1.0)

	# proximity: isotropic distance normalized by box diagonal -- LOGGED
	# feature only, never used to filter (audit refined boundary).
	dx = blob["centroid_x"] - pred_cx
	dy = blob["centroid_y"] - pred_cy
	dist = (dx**2 + dy**2)**0.5
	diag = (pred_w**2 + pred_h**2)**0.5
	proximity = max(0.0, 1.0 - dist / diag) if diag > 0 else 0.0

	# total_score: strength + proximity. NO direction-aware terms,
	# NO size_score (removed per audit HIGH finding).
	confidence = strength * 0.5 + proximity * 0.5
	result = max(0.0, min(1.0, confidence))

	# Store per-blob score components on the blob dict for trace capture.
	# These are METADATA only; they do not affect what is returned.
	dist_h = dist / pred_h if pred_h > 0 else 0.0
	blob["dist_h"] = dist_h
	blob["strength_score"] = strength
	blob["proximity_score"] = proximity
	# size_score retained as a logged feature (not used in total_score
	# and not used as a filter). Kept for downstream JSON shape stability.
	pred_area = pred_w * pred_h
	area_ratio = float(blob["area"]) / pred_area if pred_area > 0 else 0.0
	blob["size_score"] = area_ratio
	blob["total_score"] = result

	return result


#============================================
@dataclasses.dataclass
class BlobObservation:
	"""A single per-frame blob measurement. Stateless, carries no history.

	2026-05-28 re-scope: center_pixel is the RAW blob centroid in source
	pixels (no torso re-anchor, no direction-aware projection). The
	consumer interprets body-to-torso offset using LOCAL context only
	(last few accepted walker frames at most -- never derived from a
	full-interval seed chord). See dump_step1/BLOB_EXTRACTION_CODE_AUDIT.md.

	2026-05-29 typed boundary (M2/WS2-B): center_pixel is now a typed
	coord_space.SourcePoint (SOURCE space), not a bare (cx, cy) tuple. The
	observe_blob_at INPUTS are PROCESSED and the RETURN centroid is SOURCE;
	this processed-in / source-out flip was previously silent and fed the
	walker a source centroid as if it were processed (the #101 class of
	bug). The typed SourcePoint makes the space unmistakable: a caller that
	wants to feed it back as a prediction must call .to_processed(geometry)
	explicitly. Read .cx / .cy for the scalar coordinates.

	cross_track and along_track are retained for backward compatibility
	with downstream consumers but are no longer used to filter; both are
	zero in the new contract (consumers that need direction info must
	derive it themselves from LOCAL accepted frames).

	Attributes:
		center_pixel: coord_space.SourcePoint RAW blob centroid in SOURCE
			(full-frame) pixel coords. Read .cx / .cy.
		cross_track: Reserved; always 0.0 in the re-scoped contract.
		along_track: Reserved; always 0.0 in the re-scoped contract.
		confidence: Logged metadata score in [0, 1]. Ranking only.
	"""
	center_pixel: coord_space.SourcePoint
	cross_track: float
	along_track: float
	confidence: float


#============================================
# Two call shapes, same extraction pipeline:
# - Production (FWD/BWD): pred_center/pred_box, default ROI/DoG/corridor, cached.
# - Seed-local (walker): roi_override/dog_diameter_override/acceptance_box, bypassed cache.
def observe_blob_at(
	frame_index: int,
	pred_center: coord_space.ProcessedPoint,
	pred_box: coord_space.ProcessedBox,
	local_tangent: tuple,
	scene_transform: object,
	reader: object,
	residual_cache: dict,
	threshold: float = DEFAULT_THRESHOLD,
	half_window: int = DEFAULT_HALF_WINDOW,
	fps: float = None,
	stride: int = None,
	precomputed_store: "dict | None" = None,
	trace_sink: object | None = None,
	roi_override: "coord_space.ProcessedBox | None" = None,
	dog_diameter_override: "float | None" = None,
	acceptance_box: "coord_space.ProcessedBox | None" = None,
) -> BlobObservation:
	"""Return the best blob observation at one frame, or None.

	Stateless. Reads raw image evidence and returns a single optional
	measurement. The caller owns all gating logic (proximity, direction,
	temporal smoothness). This function owns image extraction and
	corridor selection only.

	Two call shapes, same code path:

	1. **Production-shape (FWD/BWD propagator).** Caller provides
	   pred_center and pred_box (from kinematic Hermite prediction).
	   Uses default ROI sizing, default DoG diameter, and default corridor
	   filter. All kwargs (_override, acceptance_box, precomputed_store)
	   are None. Fully benefits from residual caching.

	2. **Seed-local-shape (walker).** Caller provides roi_override,
	   dog_diameter_override, and acceptance_box tuned to a human-authored
	   seed's geometry. Extracts blob evidence tightly scoped to seed intent
	   without cache pollution. When any override is set, the residual cache
	   is bypassed (different DoG diameter or ROI would otherwise return
	   wrong cached raw blobs). Honors contract C1 (seeds never modified
	   by software).

	Cache content boundary (strict). residual_cache may hold raw image
	data only:
	  - extracted raw-blob lists (pre-gate)
	  - a sub-dict of cached frame reads (grayscale and BGR)
	It MUST NOT hold any per-frame decision: accepted blobs, filtered
	or chained blob lists, gate outcomes, or any value derived from a
	gating decision. Anything decision-shaped in the cache is back-door
	state and fails review.

	The cache is scoped to a single interval by the caller and cleared
	at interval end. FWD and BWD within the same interval legitimately
	share raw residuals and raw blobs; they each apply independent gates
	in the propagator, so sharing image data does not leak decisions.

	Cache bypass (seed-local-shape only). When roi_override,
	dog_diameter_override, or acceptance_box is set (seed-local call shape),
	the residual cache is bypassed entirely. A different ROI or DoG diameter
	would produce a different raw-blob extraction; caching and reusing the
	default-shape extraction would silently return wrong results. The
	bypass ensures seed-local queries never return stale cached blobs from
	a prior default-shape call, and vice versa.

	Cache key is `(frame_index, roi)`. FWD and BWD at the same frame
	usually produce near-identical ROIs (both pass's raw_pred converges
	at endpoints and differs only by Hermite slope asymmetry in between);
	when they DO produce identical ROIs the second caller reuses the
	first caller's raw blobs. When they produce DIFFERENT ROIs (tight
	curves, occlusion edges, crowd scenes) each pass computes its own
	residual against its own ROI. This preserves FWD/BWD independence
	in exactly the regimes where divergence matters most, at the cost
	of up to 2x residual computation on intervals where the two passes
	disagree. Raw frame reads (the nested `_frames` sub-cache) are keyed
	by `frame_index` alone and stay shared -- they don't depend on ROI.

	Coordinate-space boundary (typed, M2/WS2-B, 2026-05-29):
		All geometric INPUTS are PROCESSED space, expressed as typed
		coord_space primitives, and the RETURN centroid is SOURCE space.
		The require_processed_* guards at entry reject a SOURCE-space
		caller LOUDLY (ValueError) at the boundary instead of building a
		degenerate ROI deep inside (the #101 class of bug). See
		docs/COORDINATE_SPACES.md.

	Args:
		frame_index: Frame to observe.
		pred_center: coord_space.ProcessedPoint kinematic prediction in
			PROCESSED pixels. Used only to seed the ROI and to score
			blob proximity; never written into the cache.
		pred_box: coord_space.ProcessedBox predicted box in PROCESSED
			pixels. Only its .w / .h (size) are used (ROI size, DoG
			diameter default, confidence scoring); its center is ignored
			here because pred_center already carries the center.
		local_tangent: (tx, ty, nx, ny) unit vectors describing the
			pass's local motion direction. Pass (1, 0, 0, 1) to fall
			back to axis-aligned decomposition.
		scene_transform: SceneTransform instance used by
			compute_residual_for_frame to align neighbor frames.
		reader: Video reader providing width, height, frame_count, and
			read_frame(index).
		residual_cache: Mutable dict keyed by frame_index. The caller
			creates this empty per interval and drops it after both
			passes complete.
		threshold: Motion intensity threshold for blob extraction.
		half_window: Per-side neighbor count (default DEFAULT_HALF_WINDOW=4).
			Fixed regardless of fps; time span is controlled by stride.
		fps: Source video fps. Used to resolve stride when stride is None.
			Pulled from reader.fps when None.
		stride: Neighbor stride from resolve_stride(fps). When None,
			resolved from fps or reader.fps automatically.
		precomputed_store: Optional per-interval residual store produced
			by residual_pre_pass.precompute_interval_residuals. Keyed by
			(frame_index, roi_tuple) -> (residual_uint8, validity_uint8).
			When non-None and a hit is found, bypasses
			compute_residual_for_frame entirely and populates
			residual_cache so subsequent calls within the same interval
			find the result without rechecking. The store is read-only
			from this function's perspective. When None, legacy reader
			path is used (same as prior behavior).
		trace_sink: Optional object with a settable observer_trace attribute.
			When non-None, a BlobObserverTrace is captured and set on
			trace_sink.observer_trace. Default None preserves byte-identical
			behavior. Trace is never cached; it is computed per call.
		roi_override: Optional coord_space.ProcessedBox in PROCESSED pixels.
			Its .edges() (x1, y1, x2, y2) replace the default ROI sizing
			(seed-local walker shape). Bypasses the residual cache.
		dog_diameter_override: Optional scalar DoG diameter, a LENGTH in
			PROCESSED pixels (not a point, so it stays a bare scalar). When
			set, overrides the default diameter (pred_box.w). Bypasses cache.
		acceptance_box: Optional coord_space.ProcessedBox in PROCESSED
			pixels. Its .edges() define the geometric ROI test that selects
			candidate blobs (seed-local walker shape). Bypasses cache.

	Returns:
		BlobObservation whose center_pixel is a coord_space.SourcePoint
		(SOURCE space) for the best in-corridor candidate, or None when no
		residual was computable, no blobs were extracted, or no extracted
		blob fell inside the corridor. The single processed->source
		conversion happens once, at the exit, on the winner centroid.

	Observable contract (2026-05-28 re-scope):
		Extraction returns raw blob centroids in source pixels. Each blob
		carries features: centroid_x, centroid_y, bbox, area,
		integrated_mag, label_id, dist_to_pred_px (logged, not filtered),
		strength_score, proximity_score, small_blob flag. Consumers
		interpret these features in their own context; extraction does
		not apply velocity, direction, or runner-motion priors.

		The previous along-track / cross-track torso re-anchor (landed
		just before 2026-05-28) was a design error per the audit and per
		the user direction: there is no reliable track direction available
		at extraction time, so any projection against an assumed heading
		silently degrades on non-horizontal runs. This contract change
		REVERTS that re-anchor. `BlobObservation.center_pixel` is now the
		raw winner-blob centroid in source pixels. Consumers that need
		body-to-torso interpretation must derive it from LOCAL context
		(last few accepted walker frames at most), never from a
		full-interval seed chord and never carried interval-to-interval
		(contract C6).

		Corridor / direction-aware filtering inside extraction is also
		removed. All raw blobs whose centroids land inside the
		acceptance_box (when provided) are returned as candidates; with no
		acceptance_box, all raw blobs from the ROI are returned. The
		previous corridor radius (max(1.5*w, 0.75*h) cross-track) is now
		exposed as the per-blob logged feature `dist_to_pred_px`. No
		silent discard based on guessed runner motion.

		Winner selection inside extraction returns the highest
		integrated_mag blob from the candidate set as a quick-lookup
		convenience; cue_confidence is metadata only (strength_score +
		proximity_score, both logged). Consumers may pick their own winner
		from the candidate list (e.g. by proximity_score) as needed.

		When no observation is produced (returns None), the trace's
		`reject_reason` field is populated before return with one of
		"no_residual", "no_raw_blobs", "corridor_empty",
		"acceptance_box_empty", or "no_winner". Capture and audit tools
		differentiate these failure modes without re-instrumenting the
		heat-map pipeline.
	"""

	def _set_reject_reason(reason: str) -> None:
		# Populate trace_sink.observer_trace with a minimal trace carrying
		# the tagged reject reason. Lets capture/audit tools distinguish
		# why observe_blob_at returned None. Safe no-op when trace_sink
		# is not provided.
		if trace_sink is None:
			return
		# When the trace already exists, just stamp the reason; otherwise
		# build a minimal stub so the field can be read.
		existing = getattr(trace_sink, "observer_trace", None)
		if existing is None:
			trace_sink.observer_trace = blob_trace.BlobObserverTrace(
				frame_index=frame_index,
				roi_bounds=(0, 0, 0, 0),
				has_residual=False,
				residual_dog=None,
				residual_pre_dog=None,
				validity_mask=None,
				raw_blobs=[],
				corridor_blobs=[],
				winner_blob=None,
				winner_score=None,
				local_tangent=local_tangent,
				reject_reason=reason,
			)
		else:
			existing.reject_reason = reason
	# Coord contract (Option A, 2026-05-29): all inputs arrive in
	# PROCESSED-pixel coords.  The prior Model B contract (source coords
	# everywhere, converted at this boundary) was retired after three
	# auto-bin coord defects.  Callers that use load_seeds_view or
	# walk_io.load_walker_seeds_view obtain processed coords from the view;
	# solve-mode callers (velocity_model) use bin_factor=1 so
	# source==processed (no behavior change).  The conversion block that
	# existed here between 2026-05-28 and 2026-05-29 was:
	#   pred_cx_p = geometry.source_to_processed(*pred_center)   [removed]
	#   roi_override converted source->processed                  [removed]
	#   dog_diameter_override converted source->processed         [removed]
	# These conversions are now the caller's responsibility.
	geometry = getattr(reader, "geometry", None)
	# Typed boundary (M2/WS2-B): guard the PROCESSED-space inputs LOUDLY so
	# a SOURCE-space caller (the #101 class) fails with ValueError right
	# here instead of building a degenerate ROI deep inside. Unwrap the
	# typed primitives to the bare floats the internal math uses; the
	# internal code below is unchanged.
	coord_space.require_processed_point(pred_center)
	coord_space.require_processed_box(pred_box)
	pred_cx_p = pred_center.cx
	pred_cy_p = pred_center.cy
	# pred_box carries the predicted SIZE; only .w / .h are used (its center
	# is ignored, pred_center owns the center).
	pred_w_p = pred_box.w
	pred_h_p = pred_box.h

	# compute ROI from caller's prediction and use it as part of the
	# cache key so FWD and BWD with divergent raw_pred each get their
	# own residual. _compute_roi returns a 4-tuple, which is hashable.
	# All values here are processed-frame; ROI tuples in the cache key
	# are processed-frame coordinates by contract.
	# 2026-05-28: reader.width/height are required (PYTHON_STYLE forbids
	# defensive defaults on required keys). Raise instead of papering over
	# a missing attribute with a 1920x1080 fallback.
	if not hasattr(reader, "width") or not hasattr(reader, "height"):
		raise AttributeError(
			"observe_blob_at requires reader.width and reader.height; "
			f"got reader={type(reader).__name__}"
		)
	frame_w = reader.width
	frame_h = reader.height
	# Optional caller-supplied ROI override as a PROCESSED-space ProcessedBox.
	# Guard the space at the boundary, then derive its (x1, y1, x2, y2)
	# edges for the clamp math (the numeric clamp below is unchanged).
	if roi_override is not None:
		coord_space.require_processed_box(roi_override)
		roi_x1, roi_y1, roi_x2, roi_y2 = roi_override.edges()
		ox1 = max(0, min(frame_w, int(roi_x1)))
		oy1 = max(0, min(frame_h, int(roi_y1)))
		ox2 = max(ox1, min(frame_w, int(roi_x2)))
		oy2 = max(oy1, min(frame_h, int(roi_y2)))
		roi = (ox1, oy1, ox2, oy2)
	else:
		roi = _compute_roi(pred_cx_p, pred_cy_p, pred_h_p, frame_w, frame_h)
	cache_key = (frame_index, roi)

	# Bypass the residual cache when DoG diameter override is in effect.
	# The cache stores dog_residual + raw_blobs_processed derived from
	# the default DoG diameter (pred_w_p). A cache hit under a different
	# override diameter would return wrong blobs.
	overrides_in_use = (
		dog_diameter_override is not None
		or acceptance_box is not None
	)

	# Determine actual DoG diameter to be used in this call.
	# dog_diameter_override is a scalar LENGTH in PROCESSED pixels (not a
	# point, so it is not a coord_space primitive). It lands directly on the
	# DoG band-pass diameter, in the same space as the processed residual.
	if dog_diameter_override is not None:
		dog_diameter_actual = dog_diameter_override
	else:
		dog_diameter_actual = pred_w_p

	# fetch or compute cached frame data (raw image-derived only).
	# Cache key for raw blob lists is "raw_blobs_processed" to make
	# the processed-frame contract explicit at the read site:
	# the only public consumer is observe_blob_at, which converts
	# back to source-frame at exit before constructing
	# BlobObservation.
	cached = None if overrides_in_use else residual_cache.get(cache_key)
	if cached is None:
		# M3+M4: check the sequential pre-pass store before falling through
		# to compute_residual_for_frame (which does scattered reads). On a
		# hit, treat the stored uint8 arrays as if just computed; populate
		# residual_cache so future calls within this interval find it.
		# The store is read-only here; we never write into it.
		if precomputed_store is not None and cache_key in precomputed_store:
			residual_u8, validity_u8 = precomputed_store[cache_key]
			# convert back to float32 for the downstream DoG/blob pipeline
			residual = residual_u8.astype(numpy.float32)
			validity_mask = validity_u8
		else:
			# nested cache for raw frame reads (keyed by frame_index alone;
			# frame bytes are ROI-independent).
			frame_read_cache = residual_cache.setdefault("_frames", {})
			# resolve stride once at the call site; pass explicitly so
			# compute_residual_for_frame does not re-derive it from fps
			effective_fps = fps if fps is not None else getattr(reader, "fps", None)
			effective_stride = stride if stride is not None else resolve_stride(
				effective_fps if effective_fps is not None and effective_fps > 0
				else float(REFERENCE_FPS)
			)
			residual, validity_mask = compute_residual_for_frame(
				reader, frame_index, scene_transform,
				half_window, frame_read_cache, roi,
				fps=effective_fps, stride=effective_stride,
			)
		if residual is None:
			# negative-result entry avoids re-attempts; holds no decisions
			residual_cache[cache_key] = {"raw_blobs_processed": []}
			_set_reject_reason("no_residual")
			return None
		# Capture pre-DoG residual (always, for cache storage).
		residual_pre_dog = residual.copy()
		# DoG band-pass tuned to the predicted torso width in
		# processed-frame pixels. The residual lives in the processed
		# frame's pixel space; pred_w_p is in the same space, so the
		# diameter argument lands directly without any further scaling.
		# When the caller supplies dog_diameter_override (e.g. seed-local
		# walker, 0.7 * seed_w per 2026-05-24 spec), use that instead.
		dog_residual = dog_filter_blob_scale(
			residual, dog_diameter_actual, k=DOG_K_FACTOR_DEFAULT,
		)
		dog_residual[validity_mask == 0] = 0.0
		raw_blobs = extract_frame_blobs(dog_residual, validity_mask, threshold)
		# restore full-frame (processed) coords so corridor math
		# below works in a single coordinate space.
		roi_x1 = roi[0]
		roi_y1 = roi[1]
		for blob in raw_blobs:
			blob["centroid_x"] += roi_x1
			blob["centroid_y"] += roi_y1
		fresh_entry = {
			"raw_blobs_processed": raw_blobs,
			"residual_pre_dog": residual_pre_dog,
			"dog_residual": dog_residual,
			"validity_mask": validity_mask,
		}
		if not overrides_in_use:
			residual_cache[cache_key] = fresh_entry
		cached = fresh_entry
	else:
		# Cache hit: retrieve raw image data from the cache entry.
		residual_pre_dog = cached.get("residual_pre_dog")
		dog_residual = cached.get("dog_residual")
		validity_mask = cached.get("validity_mask")

	raw_blobs = cached["raw_blobs_processed"]
	if not raw_blobs:
		_set_reject_reason("no_raw_blobs")
		return None

	# 2026-05-28 re-scope: extraction returns raw blob centroids. No
	# direction-aware corridor, no along/cross projection, no torso
	# re-anchor. local_tangent is accepted for backward compatibility
	# but is NOT used to filter or to re-anchor any blob centroid.
	# See docstring "Observable contract (2026-05-28 re-scope)".
	_ = local_tangent
	# Logical placeholder kept for the trace structure; no longer a
	# direction-aware filter radius.
	corridor_radius = 0.0
	if acceptance_box is not None:
		# acceptance_box is a PROCESSED-space ProcessedBox; guard the space
		# at the boundary, then derive its (x1, y1, x2, y2) edges for the
		# geometric ROI test (the test below is unchanged).
		coord_space.require_processed_box(acceptance_box)
		ab_x1, ab_y1, ab_x2, ab_y2 = acceptance_box.edges()
		# Pure geometric ROI test. Keep every blob whose centroid lies
		# inside the acceptance box; do NOT discard for direction or for
		# distance to the predicted center.
		candidate_blobs = [
			b for b in raw_blobs
			if ab_x1 <= b["centroid_x"] <= ab_x2 and ab_y1 <= b["centroid_y"] <= ab_y2
		]
		if not candidate_blobs:
			_set_reject_reason("acceptance_box_empty")
			return None
	else:
		# No acceptance box: return all raw blobs from the ROI. No
		# silent discard based on guessed runner motion direction.
		candidate_blobs = list(raw_blobs)
		if not candidate_blobs:
			_set_reject_reason("no_raw_blobs")
			return None

	# Compute and log per-blob metadata (proximity_score, strength_score,
	# size_score, total_score). These are LOGGED features; they do not
	# filter and the chosen winner is by integrated_mag, not total_score.
	for blob in candidate_blobs:
		# Zero out direction-aware fields (kept for downstream JSON shape
		# compatibility); both are 0.0 in the re-scoped contract.
		blob["cross_track"] = 0.0
		blob["along_track"] = 0.0
		compute_cue_confidence(
			blob, pred_cx_p, pred_cy_p, pred_w_p, pred_h_p, local_tangent,
		)

	# Quick-lookup winner: strongest blob by integrated_mag. This is the
	# raw runner-prior-free pick; consumers may re-rank from the candidate
	# list using proximity_score or any other feature they choose.
	best_blob = max(candidate_blobs, key=lambda b: b["integrated_mag"])
	# best_score reports the logged total_score of the chosen winner for
	# trace continuity. It is metadata, not a gating threshold.
	best_score = float(best_blob["total_score"])

	# Raw centroid (no re-anchor) lives in PROCESSED space. This is the
	# single, explicit processed -> source conversion at the exit boundary:
	# wrap the winner into a typed ProcessedPoint and convert once to a
	# typed SourcePoint, so the caller cannot feed the SOURCE centroid back
	# into a PROCESSED stepping loop without an explicit .to_processed().
	# Numeric behavior is unchanged: at bin_factor==1 (or no geometry) the
	# conversion is a no-op, matching the prior pass-through.
	cx_proc = float(best_blob["centroid_x"])
	cy_proc = float(best_blob["centroid_y"])
	winner_processed = coord_space.ProcessedPoint(cx=cx_proc, cy=cy_proc)
	if geometry is not None and geometry.bin_factor != 1:
		# one explicit processed -> source conversion, routed through the
		# typed primitive (same scale as the prior processed_to_source call).
		center_source = winner_processed.to_source(geometry)
	else:
		# no geometry or bin_factor==1: source==processed, wrap directly so
		# the return type is still a typed SourcePoint (no numeric change).
		center_source = coord_space.SourcePoint(cx=cx_proc, cy=cy_proc)

	observation = BlobObservation(
		center_pixel=center_source,
		cross_track=0.0,
		along_track=0.0,
		confidence=best_score,
	)
	# Trace field aliases the candidate list as `corridor_blobs` for
	# backward compatibility with existing trace consumers; the field
	# now means "blobs that survived the geometric ROI test", with no
	# direction-aware filter applied.
	corridor_blobs = candidate_blobs
	# Capture trace if requested.
	if trace_sink is not None:
		# Build set of corridor blob label_ids for efficient lookup.
		# filter_blobs_to_corridor creates new blob dicts, so object identity check
		# won't work; match by label_id instead.
		corridor_label_ids = {b.get("label_id") for b in corridor_blobs}
		# Enrich raw blobs with trace metadata (in_corridor, in_acceptance_box, dist_to_pred_px).
		# These fields are added to raw_blobs for trace output only; they do not affect
		# the production data flow when trace_sink is None.
		for blob in raw_blobs:
			# in_corridor: True if blob passed the corridor or acceptance-box filter
			blob["in_corridor"] = blob.get("label_id") in corridor_label_ids
			# in_acceptance_box: True if blob is inside acceptance box (None if acceptance box not used)
			if acceptance_box is not None:
				# acceptance_box is already a PROCESSED-space ProcessedBox
				# (guarded above); its edges match the processed-frame blob
				# centroids directly, so no source->processed conversion is
				# needed (the prior conversion was a stale Model-B remnant).
				ab_x1, ab_y1, ab_x2, ab_y2 = acceptance_box.edges()
				blob["in_acceptance_box"] = (
					ab_x1 <= blob["centroid_x"] <= ab_x2 and
					ab_y1 <= blob["centroid_y"] <= ab_y2
				)
			else:
				blob["in_acceptance_box"] = None
			# dist_to_pred_px: Euclidean distance from blob centroid to predicted center
			dx = blob["centroid_x"] - pred_cx_p
			dy = blob["centroid_y"] - pred_cy_p
			blob["dist_to_pred_px"] = (dx**2 + dy**2)**0.5

		# Score components (strength_score, proximity_score, size_score,
		# total_score) are already populated by compute_cue_confidence
		# above. integrated_mag is a required key on every raw blob from
		# extract_frame_blobs, so we read it directly (no defensive .get).
		for blob in corridor_blobs:
			strength = min(float(blob["integrated_mag"]) / 10000.0, 1.0)
			blob["strength_score"] = strength

		# roi_origin_xy: Top-left corner of ROI in processed-frame coords
		roi_origin_xy = (roi[0], roi[1])

		roi_bounds = (roi[0], roi[1], roi[2], roi[3])
		# Trace keeps acceptance_box as a PROCESSED-space (x1,y1,x2,y2) tuple
		# (unchanged trace artifact contract; the renderer unpacks a 4-tuple).
		# Derive it from the typed ProcessedBox here at the single trace site.
		acceptance_box_edges = (
			acceptance_box.edges() if acceptance_box is not None else None
		)
		trace = blob_trace.BlobObserverTrace(
			frame_index=frame_index,
			roi_bounds=roi_bounds,
			has_residual=True,
			residual_dog=dog_residual,
			residual_pre_dog=residual_pre_dog,
			validity_mask=validity_mask,
			raw_blobs=raw_blobs,
			corridor_blobs=corridor_blobs,
			winner_blob=best_blob,
			winner_score=best_score,
			local_tangent=local_tangent,
			roi_origin_xy=roi_origin_xy,
			acceptance_box=acceptance_box_edges,
			dog_diameter=dog_diameter_actual,
			corridor_radius=corridor_radius,
		)
		trace_sink.observer_trace = trace
	return observation
