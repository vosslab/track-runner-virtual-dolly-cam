"""Per-frame blob observation facade over camera-compensated residuals.

This module owns DoG/blob observation; residual_frame owns image mechanics.
The public residual API remains here so existing callers and monkeypatches
continue to work unchanged.
"""

# Standard Library
import dataclasses

# PIP3 modules
import cv2
import numpy

# local repo modules
import blob_trace
import common_tools.coord_space as coord_space
import residual_frame

# === Tunable constants ===

# Components below this area remain evidence; ``small_blob`` labels them for
# diagnostics only. It is not an extraction or candidate-selection filter.
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

# Frame-residual constants have one canonical owner.  Keep these facade
# aliases for callers and tests that import residual_motion.
REFERENCE_FPS = residual_frame.REFERENCE_FPS
DEFAULT_HALF_WINDOW = residual_frame.DEFAULT_HALF_WINDOW
MAX_GRAY_CACHE_FRAMES = residual_frame.MAX_GRAY_CACHE_FRAMES
RESIDUAL_OBSERVATION_CACHE_MAX_BYTES = (
	residual_frame.RESIDUAL_OBSERVATION_CACHE_MAX_BYTES
)

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

#============================================
# Frame-residual image helpers used by observation and focused tests.
_cache_value_bytes = residual_frame._cache_value_bytes
ByteBoundedResidualCache = residual_frame.ByteBoundedResidualCache
make_bounded_residual_cache = residual_frame.make_bounded_residual_cache
build_warp_matrix = residual_frame.build_warp_matrix
compute_validity_mask = residual_frame.compute_validity_mask
colorize_jet = residual_frame.colorize_jet
_evict_frame_cache_to_limit = residual_frame._evict_frame_cache_to_limit
resolve_stride = residual_frame.resolve_stride


#============================================
def _read_gray_frame(
	reader: object,
	frame_index: int,
	cache: dict,
) -> numpy.ndarray:
	"""Facade gray-frame read that retains the patchable eviction seam."""
	gray_frame = residual_frame._read_gray_frame(
		reader, frame_index, cache, _evict_frame_cache_to_limit,
	)
	return gray_frame


#============================================
def compute_residual_for_frame(
	reader: object,
	frame_index: int,
	scene_transform: object,
	half_window: int = DEFAULT_HALF_WINDOW,
	cache: dict | None = None,
	roi: tuple | None = None,
	scale_factor: float = 1.0,
	return_extras: bool = False,
	fps: float | None = None,
	stride: int | None = None,
) -> tuple:
	"""Facade preserving the public residual-motion patch seams."""
	result = residual_frame.compute_residual_for_frame(
		reader, frame_index, scene_transform, half_window, cache, roi,
		scale_factor, return_extras, fps, stride,
		warp_builder=build_warp_matrix,
		validity_builder=compute_validity_mask,
		read_gray_frame=_read_gray_frame,
		evict_frame_cache=_evict_frame_cache_to_limit,
		stride_resolver=resolve_stride,
	)
	return result


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
	"""Facade for the diagnose-compatible residual extras path."""
	result = residual_frame._compute_residual_with_extras(
		reader, frame_index, scene_transform, half_window, scale_factor,
		return_extras, stride,
		warp_builder=build_warp_matrix,
		validity_builder=compute_validity_mask,
	)
	return result


#============================================
def _compute_roi(
	pred_cx: float,
	pred_cy: float,
	pred_h: float,
	frame_w: int,
	frame_h: int,
) -> tuple:
	"""Compute the quantized, frame-clamped residual ROI."""
	half_side = max(int(ROI_MULTIPLIER * pred_h / 2), 50)
	quant_cx = int(round(pred_cx / ROI_QUANT)) * ROI_QUANT
	quant_cy = int(round(pred_cy / ROI_QUANT)) * ROI_QUANT
	x1 = max(0, min(frame_w, quant_cx - half_side))
	y1 = max(0, min(frame_h, quant_cy - half_side))
	x2 = max(x1, min(frame_w, quant_cx + half_side))
	y2 = max(y1, min(frame_h, quant_cy + half_side))
	result = (x1, y1, x2, y2)
	return result


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
		# Preserve small components as evidence; downstream policy decides
		# whether a candidate is useful.
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
def compute_cue_confidence(
	blob: dict,
	pred_cx: float,
	pred_cy: float,
	pred_w: float,
	pred_h: float,
) -> float:
	"""Compute logged metadata score components for a motion blob.

	This score is metadata only. It does not filter blobs or select the winner.

	Components retained:
	  - strength_score: integrated_mag normalized (heat-map intensity).
	  - proximity_score: isotropic distance from expected location,
	    Logged only; not used to discard candidates.

	Args:
		blob: Blob dict with centroid_x, centroid_y, area, integrated_mag.
		pred_cx: Predicted center x.
		pred_cy: Predicted center y.
		pred_w: Predicted box width.
		pred_h: Predicted box height.
	Returns:
		Float in [0, 1]. Metadata only.
	"""
	# strength: integrated magnitude normalized
	strength = min(float(blob["integrated_mag"]) / 10000.0, 1.0)

	# Proximity is logged only and never filters a candidate.
	dx = blob["centroid_x"] - pred_cx
	dy = blob["centroid_y"] - pred_cy
	dist = (dx**2 + dy**2)**0.5
	diag = (pred_w**2 + pred_h**2)**0.5
	proximity = max(0.0, 1.0 - dist / diag) if diag > 0 else 0.0

	# total_score combines logged strength and proximity only.
	confidence = strength * 0.5 + proximity * 0.5
	result = max(0.0, min(1.0, confidence))

	# Store per-blob score components on the blob dict for trace capture.
	# These are METADATA only; they do not affect what is returned.
	dist_h = dist / pred_h if pred_h > 0 else 0.0
	blob["dist_h"] = dist_h
	blob["strength_score"] = strength
	blob["proximity_score"] = proximity
	# size_score is logged but does not influence selection.
	pred_area = pred_w * pred_h
	area_ratio = float(blob["area"]) / pred_area if pred_area > 0 else 0.0
	blob["size_score"] = area_ratio
	blob["total_score"] = result

	return result


#============================================
@dataclasses.dataclass
class BlobObservation:
	"""A single per-frame blob measurement. Stateless, carries no history.

	center_pixel is the raw blob centroid as a typed SOURCE-space point.
	Inputs are PROCESSED, so callers must convert explicitly through
	FrameGeometry before using the point in another processed operation.

	Attributes:
		center_pixel: coord_space.SourcePoint RAW blob centroid in SOURCE
			(full-frame) pixel coords. Read .cx / .cy.
		confidence: Logged metadata score in [0, 1]. Ranking only.
	"""
	center_pixel: coord_space.SourcePoint
	confidence: float


#============================================
# Two call shapes share one extraction pipeline:
# - Production FWD/BWD uses the prediction-derived ROI and cached image evidence.
# - Seed-local walker calls may supply ROI, DoG, and acceptance-box overrides.
def observe_blob_at(
	frame_index: int,
	pred_center: coord_space.ProcessedPoint,
	pred_box: coord_space.ProcessedBox,
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
	measurement. The caller owns all motion and temporal gating. This
	function owns image extraction and optional geometric acceptance-box
	filtering only.

	Two call shapes, same code path:

	1. **Analytical-prediction shape.** Caller provides `pred_center` and
	   `pred_box` from pair-local endpoint interpolation.
	   Uses prediction-derived ROI sizing and DoG diameter. With no
	   acceptance_box, every raw blob in that ROI remains eligible. Fully
	   benefits from residual caching.

	2. **Seed-local-shape (walker).** Caller provides roi_override,
	   dog_diameter_override, and acceptance_box tuned to a human-authored
	   seed's geometry. The ROI is part of the cache key; changed DoG or
	   acceptance-box settings intentionally use uncached evidence. Honors
	   contract C1 (seeds never modified by software).

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

	Cache behavior. The cache key includes both frame and ROI, so an
	roi_override safely reuses only matching image evidence. A changed DoG
	diameter or an acceptance-box query computes uncached evidence because
	the stored blob set would not represent that request.

	Cache key is `(frame_index, roi)`. FWD and BWD at the same frame
	usually produce near-identical ROIs. Stage-3 analytical geometry coincides;
	other callers may still supply distinct directional predictions.
	when they DO produce identical ROIs the second caller reuses the
	first caller's raw blobs. When they produce DIFFERENT ROIs (tight
	curves, occlusion edges, crowd scenes) each pass computes its own
	residual against its own ROI. This preserves FWD/BWD independence
	in exactly the regimes where divergence matters most, at the cost
	of up to 2x residual computation on intervals where the two passes
	disagree. Raw frame reads (the nested `_frames` sub-cache) are keyed
	by `frame_index` alone and stay shared -- they don't depend on ROI.

	Coordinate-space boundary:
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
		scene_transform: SceneTransform instance used by
			compute_residual_for_frame to align neighbor frames.
		reader: Video reader providing width, height, frame_count, and
			read_frame(index).
		residual_cache: Mutable dict keyed by ``(frame_index, roi)`` plus
			a nested frame-read cache. The caller creates it per interval and
			drops it after both passes complete.
		threshold: Motion intensity threshold for blob extraction.
		half_window: Per-side neighbor count (default DEFAULT_HALF_WINDOW=4).
			Fixed regardless of fps; time span is controlled by stride.
		fps: Source video fps. Used to resolve stride when stride is None.
			Pulled from reader.fps when None.
		stride: Neighbor stride from resolve_stride(fps). When None,
			resolved from fps or reader.fps automatically.
		precomputed_store: Optional per-interval residual store produced
			by residual_pre_pass.precompute_interval_residuals. Keyed by
			(frame_index, roi_tuple) -> (residual_float32, validity_uint8).
			When non-None and a hit is found, bypasses
			compute_residual_for_frame entirely and populates
			residual_cache so subsequent calls within the same interval
			find the result without rechecking. The store is read-only
			from this function's perspective. When None, the direct-reader
			path computes the residual.
		trace_sink: Optional object with a settable observer_trace attribute.
			When non-None, a BlobObserverTrace is captured and set on
			trace_sink.observer_trace. Trace capture is optional and never cached;
			without a trace sink, normal observation behavior is unchanged.
		roi_override: Optional coord_space.ProcessedBox in PROCESSED pixels.
			Its .edges() replace the default ROI sizing and become part of the
			residual-cache key.
		dog_diameter_override: Optional scalar DoG diameter, a LENGTH in
			PROCESSED pixels (not a point, so it stays a bare scalar). When
			set, overrides the default diameter (pred_box.w) and uses uncached
			blob extraction.
		acceptance_box: Optional coord_space.ProcessedBox in PROCESSED
			pixels. Its .edges() define the geometric ROI test that selects
			candidate blobs (seed-local walker shape) and uses uncached blob
			extraction.

	Returns:
		BlobObservation whose center_pixel is a coord_space.SourcePoint
		(SOURCE space) for the highest-integrated-magnitude eligible blob, or
		None when no residual was computable, no blobs were extracted, or no
		blob fell inside an optional acceptance box. The single processed->source
		conversion happens once, at the exit, on the winner centroid.

	Observable contract:
		Extraction retains raw blob centroids and image-derived features.
		It does not apply direction, velocity, or runner-motion filtering.
		An optional acceptance box supplies the only geometric filter.

		The returned center is the selected raw blob centroid in SOURCE
		space. Selection uses highest integrated magnitude among eligible
		blobs; the cue-confidence fields are descriptive metadata.

		When no observation is produced, a trace records one of
		"no_residual", "no_raw_blobs", "acceptance_box_empty", or
		"off_frame".
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
				candidate_blobs=[],
				winner_blob=None,
				winner_score=None,
				reject_reason=reason,
			)
		else:
			existing.reject_reason = reason
	# All geometric inputs are PROCESSED coordinates. Convert explicitly at
	# the caller boundary, then preserve that space through residual extraction.
	geometry = getattr(reader, "geometry", None)
	# Typed boundary: guard the PROCESSED-space inputs LOUDLY so
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
	# Reader dimensions are required to construct a processed-space ROI.
	if not hasattr(reader, "width") or not hasattr(reader, "height"):
		raise AttributeError(
			"observe_blob_at requires reader.width and reader.height; "
			f"got reader={type(reader).__name__}"
		)
	frame_w = reader.width
	frame_h = reader.height
	# Guard: predicted center off-frame -> no observation (soft miss).
	# Upstream off-frame predictions clamp the ROI to zero width/height,
	# which causes a degenerate-ROI crash in compute_residual_for_frame.
	# Treat off-frame predictions as missing evidence rather than an error.
	if not (0 <= pred_cx_p < frame_w and 0 <= pred_cy_p < frame_h):
		_set_reject_reason("off_frame")
		return None
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
	# Guard: degenerate ROI after clamping (e.g. roi_override that barely
	# clips the frame edge and clamps to zero extent). Return None so the
	# walker can fall back to the analytical path rather than crashing downstream.
	if roi[2] <= roi[0] or roi[3] <= roi[1]:
		_set_reject_reason("off_frame")
		return None
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
		# check the sequential pre-pass store before falling through
		# to compute_residual_for_frame (which does scattered reads). On a
		# hit, treat the stored float32 arrays as if just computed; populate
		# residual_cache so future calls within this interval find it.
		# The store is read-only here; we never write into it.
		if precomputed_store is not None and cache_key in precomputed_store:
			residual, validity_u8 = precomputed_store[cache_key]
			# The pre-pass stores float32 residuals because downstream DoG/blob
			# extraction is float32. Never quantize a cache hit.
			if residual.dtype != numpy.float32:
				residual = residual.astype(numpy.float32)
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
			# Cache a normal-call miss so repeated evidence reads do not retry.
			# Override calls intentionally keep their evidence local.
			if not overrides_in_use:
				residual_cache[cache_key] = {"raw_blobs_processed": []}
			_set_reject_reason("no_residual")
			return None
		# Capture pre-DoG residual (always, for cache storage).
		residual_pre_dog = residual.copy()
		# DoG band-pass tuned to the predicted torso width in
		# processed-frame pixels. The residual lives in the processed
		# frame's pixel space; pred_w_p is in the same space, so the
		# diameter argument lands directly without any further scaling.
		# A seed-local walker may supply its own DoG diameter.
		dog_residual = dog_filter_blob_scale(
			residual, dog_diameter_actual, k=DOG_K_FACTOR_DEFAULT,
		)
		dog_residual[validity_mask == 0] = 0.0
		raw_blobs = extract_frame_blobs(dog_residual, validity_mask, threshold)
		# Restore full-frame processed coordinates for acceptance-box checks.
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

	# Extraction returns raw blob centroids without directional filtering.
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
		compute_cue_confidence(blob, pred_cx_p, pred_cy_p, pred_w_p, pred_h_p)

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
		confidence=best_score,
	)
	if trace_sink is not None:
		acceptance_box_edges = (
			acceptance_box.edges() if acceptance_box is not None else None
		)
		blob_trace.assign_observer_trace(
			trace_sink=trace_sink, frame_index=frame_index, roi=roi,
			dog_residual=dog_residual, residual_pre_dog=residual_pre_dog,
			validity_mask=validity_mask, raw_blobs=raw_blobs,
			candidate_blobs=candidate_blobs, best_blob=best_blob,
			best_score=best_score,
			acceptance_box_edges=acceptance_box_edges,
			dog_diameter=dog_diameter_actual,
			pred_cx=pred_cx_p, pred_cy=pred_cy_p,
		)
	return observation
