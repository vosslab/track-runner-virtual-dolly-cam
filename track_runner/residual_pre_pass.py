"""Per-worker per-interval sequential residual pre-pass.

Eliminates scattered random-access reads in Stage 4 by walking an
interval's frame range sequentially, building an in-memory residual
store keyed by (frame_index, roi). The FrameReader strategy-0 fast-path
fires for every read after the first because the access pattern is
monotonically increasing.

The returned dict is consumed by observe_blob_at in residual_motion.py
via the precomputed_store parameter. On a hit the function bypasses
compute_residual_for_frame entirely; on a miss it falls through to the
legacy reader path.

Memory safety (M6, 2026-05-04 OOM hotfix):
- The first M3+M4 ship held the entire interval+padding range of
  frames in a single dict with no eviction. At --bin 2 across 7 workers
  this multiplied to ~40 GB; at --bin 1 ~157 GB. The user's MacStudio
  (36 GB) crashed.
- M6 splits BGR and gray storage into two dicts and evicts each by
  numeric frame index after every drain step, so the rolling buffer
  stays within the neighbor footprint regardless of interval length.
- MAX_PREPASS_BUFFER_FRAMES is the safety net cap on each buffer; it
  matches the MAX_GRAY_CACHE_FRAMES = 40 cap in residual_motion.py for
  one mental model. Should never fire under normal operation; surfaces
  algorithmic regressions immediately as a clear RuntimeError instead
  of a system OOM.

Design notes:
- Per C5: the store is scoped to a single interval. It lives only
  for the duration of solve_interval_analytical and is destroyed when
  the worker process exits (pool uses max_tasks_per_child=1).
- Per C11: no frame-based state escapes this call; the dict is pure
  image-derived data (residual + validity arrays).
- M2 stride model: padding is half_window * stride so the BGR cache
  covers the wider time-span window at high fps. At 60 fps stride=1
  and padding is identical to the pre-M2 behavior.
- Per plan memoized-percolating-moler.md M3+M4+M2+M6.
"""

# Standard Library
import collections

# PIP3 modules
import cv2
import numpy

# local repo modules
import residual_motion


# Safety net cap on each rolling buffer (BGR and gray independently).
# The algorithm itself bounds each buffer to the rolling neighbor footprint
# of 2 * half_window * stride + 1 frames. Worst case at fps=240
# (half_window=4, stride=4) that is 33 frames. 40 leaves 7-frame slack.
# Matches residual_motion.MAX_GRAY_CACHE_FRAMES = 40 so a single mental
# model covers both modules.
#
# Per worker peak memory at the cap:
#   --bin 4: 40 * (1.56 MB BGR + 2.07 MB gray) = ~145 MB
#   --bin 2: 40 * (6.22 MB + 8.29 MB)          = ~582 MB
#   --bin 1: 40 * (24.9 MB + 33.2 MB)          = ~2.32 GB
# Across 7 workers at --bin 1 the worst case is ~16 GB on a 36 GB system.
# Realistic peak under the algorithm sits near 33; the cap is failsafe.
MAX_PREPASS_BUFFER_FRAMES = 40


#============================================
def precompute_interval_residuals(
	reader: object,
	scene_transform: object,
	seed_start: dict,
	seed_end: dict,
	fwd_curve: list,
	bwd_curve: list,
	half_window: int,
	fps: float,
	stride: int = 1,
	debug_stats: dict = None,
	read_log: list = None,
) -> "dict | None":
	"""Pre-compute residuals for an entire interval via a sequential frame read.

	Walks frame range pad_lo..pad_hi in monotonic order so FrameReader's
	strategy-0 fast-path fires for every read after the first (sequential,
	no scattered seeks). Builds an in-memory dict keyed by
	(frame_index, roi) -> (residual_uint8, validity_uint8).

	Memory is bounded by an interleaved walk-drain-evict loop:
	  - read one frame, store BGR (and gray for centers only),
	  - drain every pending center whose right neighbor is now in the
	    buffer,
	  - evict frames that no remaining pending center will read again.
	At any moment each buffer holds at most 2 * half_window * stride + 1
	frames; MAX_PREPASS_BUFFER_FRAMES = 40 is the safety-net cap.

	The dict is consumed by residual_motion.observe_blob_at via its
	precomputed_store parameter. On a cache hit the function bypasses
	compute_residual_for_frame; on a miss it falls through to the legacy
	reader path.

	Args:
		reader: FrameReader with read_frame, frame_count, width, height.
			When None, returns None (caller handles fallback).
		scene_transform: SceneTransform for warp matrix construction.
		seed_start: Seed dict with frame_index, cx, cy, w, h.
		seed_end: Seed dict with frame_index, cx, cy, w, h.
		fwd_curve: FWD raw_pred list from _compute_raw_pred_forward.
			Each entry is (frame_index, cx, cy, w, h, conf).
		bwd_curve: BWD raw_pred list from _compute_raw_pred_backward.
			Same shape as fwd_curve.
		half_window: Per-side neighbor count for residual computation.
			Must match what compute_residual_for_frame would use.
		fps: Source video fps (passed to compute_residual_for_frame).
		stride: Neighbor stride from residual_motion.resolve_stride(fps).
			Default 1 (60 fps behavior).
		debug_stats: Optional dict pre-populated with keys peak_bgr (int),
			peak_gray (int), and gray_frames (set). When non-None the
			function records peak buffer occupancies and every fi that
			ever entered the gray buffer. Used by tests; production
			callers leave None.
		read_log: Optional list. When non-None the function appends fi
			every time reader.read_frame is called from the sequential
			walk. Used by tests to verify no fallback reads occur during
			compute. Production callers leave None.

	Returns:
		Dict mapping (frame_index, roi_tuple) ->
			(residual_uint8 ndarray, validity_uint8 ndarray).
		Returns None when reader is None.
		Returns empty dict when interval span < 2 frames.

	Raises:
		RuntimeError: when either bgr_buf or gray_buf exceeds
			MAX_PREPASS_BUFFER_FRAMES. Should never fire under normal
			operation; signals an algorithmic regression.
	"""
	if reader is None:
		return None

	start_frame = int(seed_start["frame_index"])
	end_frame = int(seed_end["frame_index"])

	# degenerate interval: caller handles normally
	if end_frame - start_frame < 2:
		return {}

	frame_count = reader.frame_count
	# padding: half_window * stride covers the widest neighbor offset at
	# any fps. At stride=1 this equals the old half_window padding.
	pad_extent = half_window * stride
	pad_lo = max(0, start_frame - pad_extent)
	pad_hi = min(frame_count - 1, end_frame + pad_extent)

	frame_w = getattr(reader, "width", 1920)
	frame_h = getattr(reader, "height", 1080)

	# geometry for bin conversion (same path as observe_blob_at)
	geometry = getattr(reader, "geometry", None)

	# index pass-curves by absolute frame index for O(1) ROI lookup
	fwd_by_frame = {}
	for entry in fwd_curve:
		fi, cx, cy, w, h, _conf = entry
		fwd_by_frame[int(fi)] = (float(cx), float(cy), float(h))
	bwd_by_frame = {}
	for entry in bwd_curve:
		fi, cx, cy, w, h, _conf = entry
		bwd_by_frame[int(fi)] = (float(cx), float(cy), float(h))

	# pre-build rois_for_frame[t] -> set of (rx1, ry1, rx2, ry2) tuples
	# so the drain loop just iterates the set instead of recomputing.
	rois_for_frame = _build_rois_for_frame(
		start_frame, end_frame, fwd_by_frame, bwd_by_frame,
		geometry, frame_w, frame_h,
	)

	# pending centers (interval frames) in strict ascending order. The
	# eviction logic depends on this ordering; assert it explicitly so a
	# future caller passing an unsorted list fails loudly.
	pending = list(range(start_frame, end_frame + 1))
	for i in range(1, len(pending)):
		assert pending[i] > pending[i - 1], (
			"residual pre-pass requires strictly ascending pending "
			"centers; got " + repr(pending)
		)
	next_idx = 0

	# Two separate buffers so the cap is meaningful (one entry per unique
	# frame index per buffer). Mixed-key dicts make len() ambiguous and
	# eviction unsafe across interleaved key types.
	bgr_buf = collections.OrderedDict()  # fi -> BGR uint8 ndarray
	gray_buf = {}                        # fi -> gray float32, CENTERS ONLY

	# result store: (frame_index, roi) -> (residual_uint8, validity_uint8)
	store = {}

	# sequential walk: read fi, drain ready centers, evict stale frames.
	# Read progress drives the walk; compute progress drives eviction.
	for fi in range(pad_lo, pad_hi + 1):
		bgr = reader.read_frame(fi)
		if read_log is not None:
			read_log.append(fi)
		if bgr is None:
			continue
		bgr_buf[fi] = bgr
		if start_frame <= fi <= end_frame:
			gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
			gray_buf[fi] = gray.astype(numpy.float32)

		# drain: any pending center whose right neighbor is now in
		# bgr_buf is ready to compute.
		while next_idx < len(pending):
			t = pending[next_idx]
			rightmost = min(frame_count - 1, t + half_window * stride)
			if fi < rightmost:
				break
			_compute_center(
				t, rois_for_frame.get(t, ()),
				bgr_buf, gray_buf, store,
				reader, scene_transform, half_window, stride, fps,
			)
			next_idx += 1

		# leftmost frame still needed by any remaining pending center
		if next_idx < len(pending):
			next_t = pending[next_idx]
			leftmost_needed = max(0, next_t - half_window * stride)
		else:
			# all centers done; clear everything
			leftmost_needed = pad_hi + 1

		# evict by numeric frame index on each buffer independently
		while bgr_buf:
			first_fi = next(iter(bgr_buf))
			if first_fi >= leftmost_needed:
				break
			del bgr_buf[first_fi]
		for gray_fi in list(gray_buf):
			if gray_fi < leftmost_needed:
				del gray_buf[gray_fi]

		# safety net: cap each buffer by unique frame indices. Should
		# never fire under normal operation. Surfaces algorithmic
		# regressions as a clear memory-limit error rather than a
		# system OOM.
		if len(bgr_buf) > MAX_PREPASS_BUFFER_FRAMES:
			raise RuntimeError(
				"residual pre-pass BGR buffer exceeded cap "
				+ "(" + str(len(bgr_buf)) + " > "
				+ str(MAX_PREPASS_BUFFER_FRAMES) + "); "
				+ "interval [" + str(start_frame) + ".."
				+ str(end_frame) + "] at fi=" + str(fi)
			)
		if len(gray_buf) > MAX_PREPASS_BUFFER_FRAMES:
			raise RuntimeError(
				"residual pre-pass gray buffer exceeded cap "
				+ "(" + str(len(gray_buf)) + " > "
				+ str(MAX_PREPASS_BUFFER_FRAMES) + "); "
				+ "interval [" + str(start_frame) + ".."
				+ str(end_frame) + "] at fi=" + str(fi)
			)

		if debug_stats is not None:
			debug_stats["peak_bgr"] = max(debug_stats["peak_bgr"], len(bgr_buf))
			debug_stats["peak_gray"] = max(debug_stats["peak_gray"], len(gray_buf))
			debug_stats["gray_frames"].update(gray_buf.keys())

	return store


#============================================
def _build_rois_for_frame(
	start_frame: int,
	end_frame: int,
	fwd_by_frame: dict,
	bwd_by_frame: dict,
	geometry: object,
	frame_w: int,
	frame_h: int,
) -> dict:
	"""Pre-compute per-frame ROI tuples once before the walk.

	Builds a dict keyed by interval frame index whose values are sets of
	(rx1, ry1, rx2, ry2) tuples. The drain loop then iterates each set
	directly instead of recomputing FWD/BWD ROIs on every iteration.

	Args:
		start_frame: First interval frame (inclusive).
		end_frame: Last interval frame (inclusive).
		fwd_by_frame: Map fi -> (cx, cy, h) for FWD pass.
		bwd_by_frame: Map fi -> (cx, cy, h) for BWD pass.
		geometry: Reader geometry for source-to-processed conversion,
			or None when bin_factor == 1.
		frame_w: Processed-frame width.
		frame_h: Processed-frame height.

	Returns:
		Dict mapping fi -> set of ROI 4-tuples.
	"""
	out = {}
	for t in range(start_frame, end_frame + 1):
		rois = set()
		if t in fwd_by_frame:
			cx, cy, h = fwd_by_frame[t]
			cx_p, cy_p, h_p = _to_processed(cx, cy, h, geometry)
			rois.add(residual_motion._compute_roi(cx_p, cy_p, h_p, frame_w, frame_h))
		if t in bwd_by_frame:
			cx, cy, h = bwd_by_frame[t]
			cx_p, cy_p, h_p = _to_processed(cx, cy, h, geometry)
			rois.add(residual_motion._compute_roi(cx_p, cy_p, h_p, frame_w, frame_h))
		if rois:
			out[t] = rois
	return out


#============================================
def _to_processed(
	cx: float,
	cy: float,
	h: float,
	geometry: object,
) -> tuple:
	"""Convert source-frame coords to processed-frame coords if binned.

	Args:
		cx: Source-frame center x.
		cy: Source-frame center y.
		h: Source-frame box height.
		geometry: Reader geometry, or None for identity.

	Returns:
		Tuple of (cx_p, cy_p, h_p) in processed-frame coords.
	"""
	if geometry is None or geometry.bin_factor == 1:
		return (cx, cy, h)
	cx_p, cy_p = geometry.source_to_processed(cx, cy)
	_, h_p = geometry.source_to_processed_delta(0.0, h)
	return (cx_p, cy_p, h_p)


#============================================
def _compute_center(
	t: int,
	rois: object,
	bgr_buf: dict,
	gray_buf: dict,
	store: dict,
	reader: object,
	scene_transform: object,
	half_window: int,
	stride: int,
	fps: float,
) -> None:
	"""Compute and store residuals for one interval center frame.

	Builds a small throwaway cache adapter that contains exactly the
	keys compute_residual_for_frame reads at this center: neighbor BGRs
	at offsets k*stride (k != 0) plus the center as gray-float32. Also
	includes ("bgr", t) defensively in case any code path reads the
	center frame as BGR. One extra entry, prevents fragile hidden
	dependency on which exact paths use which key shapes.

	Args:
		t: Center frame index.
		rois: Iterable of ROI 4-tuples to compute at this center.
		bgr_buf: Rolling BGR buffer; reads only.
		gray_buf: Rolling gray buffer; reads only.
		store: Output dict; writes (frame_index, roi) -> (uint8, uint8).
		reader: FrameReader (passed through to compute_residual_for_frame).
		scene_transform: SceneTransform instance.
		half_window: Per-side neighbor count.
		stride: Neighbor stride.
		fps: Source video fps.
	"""
	if not rois:
		return
	# build the throwaway adapter
	compute_cache = {}
	for k in range(-half_window, half_window + 1):
		if k == 0:
			continue
		fi_other = t + k * stride
		if 0 <= fi_other < reader.frame_count and fi_other in bgr_buf:
			compute_cache[("bgr", fi_other)] = bgr_buf[fi_other]
	# defensive center BGR
	if t in bgr_buf:
		compute_cache[("bgr", t)] = bgr_buf[t]
	# center gray (the key shape compute_residual_for_frame reads first)
	compute_cache[t] = gray_buf[t]

	for roi in rois:
		cache_key = (t, roi)
		if cache_key in store:
			continue
		residual, validity_mask = residual_motion.compute_residual_for_frame(
			reader=reader,
			frame_index=t,
			scene_transform=scene_transform,
			half_window=half_window,
			cache=compute_cache,
			roi=roi,
			fps=fps,
			stride=stride,
		)
		if residual is None:
			continue
		# quantize to uint8 (atol=1 in the parity test). The store consumer
		# (observe_blob_at) converts back to float32 before passing to
		# DoG/blob extraction.
		residual_u8 = numpy.clip(residual, 0, 255).astype(numpy.uint8)
		validity_u8 = validity_mask.astype(numpy.uint8)
		store[cache_key] = (residual_u8, validity_u8)
