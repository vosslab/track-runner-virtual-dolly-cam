"""Per-worker per-interval sequential residual pre-pass.

Reduces scattered random-access reads in Stage 4 by walking each contiguous
requested residual neighborhood sequentially, building an in-memory residual
store keyed by (frame_index, roi). Separated startup neighborhoods require one
seek between them instead of decoding the unrequested interval between seeds.

The returned store is consumed by observe_blob_at in residual_motion.py
via the precomputed_store parameter. On a hit the function bypasses
compute_residual_for_frame entirely; on a miss it falls through to the
direct-reader path.

Byte-identical residual contract (PyAV canonical): the precomputed store is
byte-identical to the on-the-fly compute_residual_for_frame path when both
use the same backend (PyAV or cv2). The PyAV backend is the canonical
reference.

Memory safety:
- The rolling BGR and grayscale buffers are evicted by numeric frame index
  after every drain step, so they stay within the neighbor footprint
  regardless of interval length. Gray frames remain uint8; conversion to
  float32 happens only in compute_residual_for_frame.
- Each worker reserves at most PREPASS_RESULT_STORE_MAX_BYTES (512 MiB) for
  cached residual and validity arrays. The store evicts least-recently-used
  entries by array byte count before adding a new entry. A miss uses direct
  residual computation in observe_blob_at.
- MAX_PREPASS_BUFFER_FRAMES is a safety-net cap for each rolling buffer. It
  should never fire under normal operation and turns an algorithmic regression
  into a clear RuntimeError instead of a system OOM.

Design notes:
- Per C5: the store is scoped to a single interval. It lives only
  for the duration of solve_interval_analytical and is destroyed when
  the worker process exits (pool uses max_tasks_per_child=1).
- Per C11: no frame-based state escapes this call; the store is pure
  image-derived data (residual + validity arrays).
- fps-invariant stride model: padding is half_window * stride so the BGR
  cache covers the wider time-span window at high fps.
"""

# Standard Library
import collections

# PIP3 modules
import cv2
import numpy

# local repo modules
import common_tools.coord_space as coord_space
import residual_motion


# Safety net cap on each rolling buffer (BGR and gray independently).
# The algorithm itself bounds each buffer to the rolling neighbor footprint
# of 2 * half_window * stride + 1 frames. Worst case at fps=240
# (half_window=4, stride=4) that is 33 frames. 40 leaves 7-frame slack.
# Matches residual_motion.MAX_GRAY_CACHE_FRAMES = 40 so a single mental
# model covers both modules.
#
MAX_PREPASS_BUFFER_FRAMES = 40

# Per-worker upper bound for cached result arrays. This count deliberately
# includes residual and validity arrays only: they dominate each cache entry,
# while dict/key overhead is small and not data-shape dependent.
PREPASS_RESULT_STORE_MAX_BYTES = 512 * 1024 * 1024


#============================================
def _required_read_ranges(
	centers: list[int],
	pad_extent: int,
	frame_count: int,
) -> list[tuple[int, int]]:
	"""Return merged inclusive frame ranges needed by requested centers.

	Each residual center needs neighbors through ``pad_extent`` on both sides.
	Overlapping or adjacent neighborhoods are merged so reads remain sequential
	inside a range. A gap stays a gap: decoding frames no requested center uses
	would add a complete extra video pass for sparse walker-startup requests.

	Args:
		centers: Strictly increasing requested center-frame indices.
		pad_extent: Maximum neighbor distance in frames.
		frame_count: Advertised video frame count.

	Returns:
		Merged inclusive ``(lo, hi)`` ranges in increasing order.
	"""
	ranges = []
	for center in centers:
		lo = max(0, center - pad_extent)
		hi = min(frame_count - 1, center + pad_extent)
		if ranges and lo <= ranges[-1][1] + 1:
			prior_lo, prior_hi = ranges[-1]
			ranges[-1] = (prior_lo, max(prior_hi, hi))
		else:
			ranges.append((lo, hi))
	return ranges


#============================================
class _ByteBoundedLruStore(collections.OrderedDict):
	"""Read-mostly residual store with array-byte LRU eviction.

	The store is compatible with the mapping protocol consumed by
	residual_motion.observe_blob_at. Membership checks record genuine consumer
	cache hits and misses, while __getitem__ refreshes an entry's LRU position.
	Pre-pass construction uses contains_without_accounting so its duplicate
	guard does not distort runtime cache-miss measurements.
	"""
	def __init__(self, max_bytes: int) -> None:
		super().__init__()
		self.max_bytes = max_bytes
		self.total_bytes = 0
		self.lookup_count = 0
		self.miss_count = 0
		self.eviction_count = 0
		self.oversize_count = 0

	def __contains__(self, key: object) -> bool:
		self.lookup_count += 1
		found = super().__contains__(key)
		if not found:
			self.miss_count += 1
		return found

	def __getitem__(self, key: object) -> tuple:
		value = super().__getitem__(key)
		self.move_to_end(key)
		return value

	def contains_without_accounting(self, key: object) -> bool:
		"""Return membership for construction-time duplicate protection."""
		found = super().__contains__(key)
		return found

	def store_result(self, key: tuple, value: tuple) -> None:
		"""Store one residual pair, evicting least-recently-used pairs first."""
		entry_bytes = value[0].nbytes + value[1].nbytes
		if entry_bytes > self.max_bytes:
			self.oversize_count += 1
			return
		# Replacing an existing entry makes the new value most recent. Remove
		# its old byte contribution before deciding whether other entries need
		# eviction; an existing valid entry stays intact for an oversize update.
		if super().__contains__(key):
			old_value = super().__getitem__(key)
			self.total_bytes -= old_value[0].nbytes + old_value[1].nbytes
			super().__delitem__(key)
		while self and self.total_bytes + entry_bytes > self.max_bytes:
			_old_key, old_value = self.popitem(last=False)
			self.total_bytes -= old_value[0].nbytes + old_value[1].nbytes
			self.eviction_count += 1
		super().__setitem__(key, value)
		self.total_bytes += entry_bytes

	@property
	def miss_rate(self) -> float:
		"""Return consumer lookup misses as a fraction of consumer lookups."""
		if self.lookup_count == 0:
			return 0.0
		rate = self.miss_count / self.lookup_count
		return rate

	def clear(self) -> None:
		"""Clear cached arrays and their byte accounting together."""
		super().clear()
		self.total_bytes = 0


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
	rois_for_frame: dict | None = None,
) -> _ByteBoundedLruStore | None:
	"""Pre-compute residuals for requested centers via sequential frame reads.

	Walks only the merged neighborhoods needed by requested centers. Reads are
	monotonic inside each neighborhood, with one seek between separated
	neighborhoods; unrequested gaps are not decoded. Builds an in-memory
	byte-bounded LRU store keyed by (frame_index, roi) ->
	(residual_float32, validity_uint8).

	Memory is bounded by an interleaved walk-drain-evict loop:
	  - read one frame, store BGR (and gray for centers only),
	  - drain every pending center whose right neighbor is now in the
	    buffer,
	  - evict frames that no remaining pending center will read again.
	At any moment each buffer holds at most 2 * half_window * stride + 1
	frames; MAX_PREPASS_BUFFER_FRAMES = 40 is the safety-net cap.

	The store is consumed by residual_motion.observe_blob_at via its
	precomputed_store parameter. On a cache hit the function bypasses
	compute_residual_for_frame; on a miss it uses the direct-reader path.

	Byte-identical residual contract: the output is byte-identical to the
	on-the-fly compute_residual_for_frame path for all (frame_index, roi)
	keys when both use the same backend. The PyAV backend is canonical.

	Args:
		reader: FrameReader with read_frame, frame_count, width, height.
			When None, returns None (caller handles fallback).
		scene_transform: SceneTransform for warp matrix construction.
		seed_start: Seed dict with frame_index, cx, cy, w, h.
		seed_end: Seed dict with frame_index, cx, cy, w, h.
		fwd_curve: FWD raw_pred list from the direction-parameterized builder.
			Each entry is (frame_index, cx, cy, w, h, conf).
		bwd_curve: BWD raw_pred list from the direction-parameterized builder.
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
		rois_for_frame: Optional exact frame -> ROI-set map. When supplied,
			it replaces analytical-path ROI construction. Used by the walker
			pre-pass so only deterministic seed-local observations are cached.

	Returns:
		Byte-bounded LRU store mapping (frame_index, roi_tuple) ->
			(residual_float32 ndarray, validity_uint8 ndarray).
		Returns None when reader is None.
		Returns an empty store when no residuals are requested.

	Raises:
		RuntimeError: when either bgr_buf or gray_buf exceeds
			MAX_PREPASS_BUFFER_FRAMES. Should never fire under normal
			operation; signals an algorithmic regression.
	"""
	if reader is None:
		return None

	start_frame = int(seed_start["frame_index"])
	end_frame = int(seed_end["frame_index"])

	# Degenerate short interval: caller handles normally. Explicit ROIs may
	# still include a usable bootstrap observation on a short interval.
	if rois_for_frame is None and end_frame - start_frame < 2:
		return _ByteBoundedLruStore(PREPASS_RESULT_STORE_MAX_BYTES)

	frame_count = reader.frame_count
	# padding: half_window * stride covers the widest neighbor offset at
	# any fps.
	pad_extent = half_window * stride
	if rois_for_frame is not None and not rois_for_frame:
		return _ByteBoundedLruStore(PREPASS_RESULT_STORE_MAX_BYTES)

	frame_w = reader.width
	frame_h = reader.height

	if rois_for_frame is None:
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
		# pre-build ROIs once before the walk.
		rois_for_frame = _build_rois_for_frame(
			start_frame, end_frame, fwd_by_frame, bwd_by_frame,
			geometry, frame_w, frame_h,
		)
	else:
		# Copy outer mapping so caller mutation cannot change this pre-pass.
		rois_for_frame = {
			int(frame): set(rois)
			for frame, rois in rois_for_frame.items()
		}

	# Mapping keys are unique; sorting them produces the ascending order the
	# eviction logic requires.
	pending = sorted(rois_for_frame)
	read_ranges = _required_read_ranges(pending, pad_extent, frame_count)
	if not read_ranges:
		return _ByteBoundedLruStore(PREPASS_RESULT_STORE_MAX_BYTES)
	last_read_frame = read_ranges[-1][1]
	read_frames = (
		frame_index
		for range_lo, range_hi in read_ranges
		for frame_index in range(range_lo, range_hi + 1)
	)
	next_idx = 0

	# Two separate buffers so the cap is meaningful (one entry per unique
	# frame index per buffer). Mixed-key dicts make len() ambiguous and
	# eviction unsafe across interleaved key types.
	bgr_buf = collections.OrderedDict()  # fi -> BGR uint8 ndarray
	gray_buf = {}                        # fi -> gray uint8, CENTERS ONLY

	# result store: (frame_index, roi) -> (residual_float32, validity_uint8)
	store = _ByteBoundedLruStore(PREPASS_RESULT_STORE_MAX_BYTES)

	# segmented sequential walk: read fi, drain ready centers, evict stale
	# frames. Read progress drives the walk; compute progress drives eviction.
	for fi in read_frames:
		bgr = reader.read_frame(fi)
		if read_log is not None:
			read_log.append(fi)
		bgr_buf[fi] = bgr
		if fi in rois_for_frame:
			gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
			gray_buf[fi] = gray

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
			leftmost_needed = last_read_frame + 1

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
def _center_in_frame(
	cx_p: float,
	cy_p: float,
	geometry: object,
	frame_w: int,
	frame_h: int,
) -> bool:
	"""Return True when the processed-space center lies within the frame bounds.

	Uses geometry.in_bounds when a geometry object is present (bin_factor > 1
	coordinate space); falls back to raw pixel bounds otherwise.

	Args:
		cx_p: Processed-space center x.
		cy_p: Processed-space center y.
		geometry: Reader geometry, or None when bin_factor == 1.
		frame_w: Processed-frame width in pixels.
		frame_h: Processed-frame height in pixels.

	Returns:
		True when the center is within the frame; False otherwise.
	"""
	if geometry is not None:
		return coord_space.ProcessedPoint(cx=cx_p, cy=cy_p).in_bounds(geometry)
	return 0 <= cx_p < frame_w and 0 <= cy_p < frame_h


#============================================
def build_walker_initial_rois(
	seed_start: dict,
	seed_end: dict,
	reader: object,
	stride: int,
	window_frames: int,
) -> dict:
	"""Return exact seed-local ROIs queried before walker anchoring adapts.

	This mirrors ``walk_walker._compute_roi_and_observe`` exactly: the
	bootstrap and first ``window_frames`` non-seed observations in each
	direction retain their own anchor seed. Once Viterbi emits, later anchors
	may adapt, so they deliberately remain cache misses and use direct
	computation rather than risk a wrong-ROI residual.

	Seeds must already be in PROCESSED coordinates, matching the walker.
	Frame/ROI collisions between FWD and BWD are deduplicated into one set.
	"""
	rois_by_frame = {}

	def add_pass(seed: dict, neighbor_seed: dict, sign: int) -> None:
		seed_frame = int(seed["frame_index"])
		neighbor_frame = int(neighbor_seed["frame_index"])
		anchor_cx = float(seed["cx"])
		anchor_cy = float(seed["cy"])
		seed_w = float(seed["w"])
		seed_h = float(seed["h"])
		if not _center_in_frame(
			anchor_cx, anchor_cy, getattr(reader, "geometry", None),
			reader.width, reader.height,
		):
			return
		accept_x1 = anchor_cx - 0.5 * seed_w
		accept_y1 = anchor_cy - 0.75 * seed_h
		accept_x2 = anchor_cx + 0.5 * seed_w
		accept_y2 = anchor_cy + 0.75 * seed_h
		roi_pad = max(20, seed_w)
		roi = (
			max(0, int(accept_x1 - roi_pad)),
			max(0, int(accept_y1 - roi_pad)),
			min(reader.width, int(accept_x2 + roi_pad)),
			min(reader.height, int(accept_y2 + roi_pad)),
		)
		for step in range(window_frames + 1):
			frame_index = seed_frame + sign * step * stride
			if frame_index < 0 or frame_index >= reader.frame_count:
				break
			if sign * (frame_index - neighbor_frame) >= 0 and step > 0:
				break
			rois_by_frame.setdefault(frame_index, set()).add(roi)

	add_pass(seed_start, seed_end, 1)
	add_pass(seed_end, seed_start, -1)
	return rois_by_frame


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
			# skip FWD prediction if center is off-frame (avoids degenerate ROI crash #101)
			if _center_in_frame(cx_p, cy_p, geometry, frame_w, frame_h):
				rois.add(residual_motion._compute_roi(cx_p, cy_p, h_p, frame_w, frame_h))
		if t in bwd_by_frame:
			cx, cy, h = bwd_by_frame[t]
			cx_p, cy_p, h_p = _to_processed(cx, cy, h, geometry)
			# skip BWD prediction if center is off-frame (avoids degenerate ROI crash #101)
			if _center_in_frame(cx_p, cy_p, geometry, frame_w, frame_h):
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
		gray_buf: Rolling uint8 gray buffer; reads only.
		store: Byte-bounded output store; writes (frame_index, roi) ->
			(float32, uint8).
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
	# center gray (the key shape compute_residual_for_frame reads first).
	# Convert this one live center only for residual computation, matching the
	# direct-reader path without retaining float32 frames in the rolling buffer.
	compute_cache[t] = gray_buf[t].astype(numpy.float32)

	for roi in rois:
		cache_key = (t, roi)
		if store.contains_without_accounting(cache_key):
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
		# Preserve the live residual exactly. DoG/blob extraction operates on
		# float32, so uint8 quantization here would make a pre-pass cache hit
		# semantically different from direct residual computation.
		residual_f32 = residual.astype(numpy.float32, copy=False)
		validity_u8 = validity_mask.astype(numpy.uint8)
		store.store_result(cache_key, (residual_f32, validity_u8))
