"""One-direction blob walker for v2: windowed path-selection from seed toward neighbor.

The walker implements a 9-frame rolling buffer of per-frame corridor_blob
candidate lists and selects a trajectory through them using Viterbi dynamic
programming. No per-frame single-winner; the window, not the frame, is the
decision unit.

Design:
  - Walker advances frame_f, calls observe_blob_at at each step, reads
    trace.corridor_blobs as the candidate list for that frame.
  - Candidates accumulate in a rolling buffer (deque) of WALKER_WINDOW_FRAMES
    entries. No emission during fill phase.
  - Once the buffer is full, Viterbi DP selects the lowest-cost path through
    the candidate lattice. Each frame exits the window (and is emitted to the
    debug log) exactly once.
  - Five statuses: accepted, interpolated, extrapolated, soft_miss_no_blob,
    soft_miss_no_path.
  - No production_winner / audit_winner branching in the main walk loop.
  - No consec_miss counters; soft misses do not stop the walk.
  - No cross-frame blob state: the window buffer holds image-derived raw data
    only; no accepted blobs, filtered-blob lists, or gate outcomes survive past
    the active window.

Contract references:
  C2: all displacement thresholds in torso-width units.
  C6: interval independence; no shared state across intervals.
  C8: no appearance cues.
  C9: FWD/BWD independence.
  C10: unified SCHEMA_VERSION; walk_debug_log reads tr_schema.SCHEMA_VERSION.
  TRACK_RUNNER_DESIGN.md: Anti-pattern: chained blob state -- no last_blob,
    no *_chain_* variable survives past the active window.

No Hermite imports: violates contract if any state comes from velocity_model,
interval_solver, or scoring. The walker is pure forward-stepping logic.
"""

# Standard Library
import collections
import dataclasses
import json
import math

# local repo modules
import residual_motion
import blob_trace
import scene_coords
import blob_walk.walk_status as walk_status
import blob_walk.walk_viterbi as walk_viterbi
import blob_walk.walk_debug_log as walk_debug_log
import common_tools.coord_space as coord_space
import common_tools.in_box_heat

# ============================================================
# Rolling-window walker constants.
# ============================================================

# Number of frames in the rolling candidate buffer.
# Odd number so the center frame is well-defined; 9 at 60 fps is ~150 ms.
WALKER_WINDOW_FRAMES = 9

# Bootstrap mode: step <= BOOTSTRAP_N uses wide search radius.
# Set to 1 per the 2026-05-28 bootstrap redesign.
BOOTSTRAP_N = 1

# ============================================================
# Per-frame confidence decay (WS1-A).
# ============================================================
# Mirror velocity_model.py's deterministic distance-from-anchor decay
# verbatim (FWD _compute_raw_pred_forward, BWD _compute_raw_pred_backward):
#   conf = max(conf_floor, start_conf * conf_decay_per_frame ** frames_from_anchor)
# The anchor is the pass's own seed (FWD = left seed, BWD = right seed); the
# walker originates at that seed, so frames_from_anchor = abs(frame - seed).
# This is NOT an image-evidence score; it makes the FWD/BWD blend behave like
# the main solver (the closer-to-seed pass wins). No status-tier fork, no
# window-smoothness variant (WS1-A scope).
CONF_DECAY_PER_FRAME = 0.97
CONF_FLOOR = 0.1
CONF_START = 1.0


#============================================
def conf_from_anchor(frame_index: int, seed_frame: int) -> float:
	"""Deterministic distance-from-anchor confidence decay.

	Mirrors velocity_model.py: max(conf_floor, start_conf * decay ** n) where
	n is the number of frames from the pass's anchoring seed. Identical for FWD
	and BWD because each pass originates at its own seed.

	Args:
		frame_index: Absolute frame index of the emitted frame.
		seed_frame: Absolute frame index of the pass's anchoring seed.

	Returns:
		Confidence in [CONF_FLOOR, CONF_START].
	"""
	frames_from_anchor = abs(frame_index - seed_frame)
	conf = CONF_START * (CONF_DECAY_PER_FRAME ** frames_from_anchor)
	return max(CONF_FLOOR, conf)


#============================================
def make_size_at_frame(
	seed_frame: int,
	seed_w: float,
	seed_h: float,
	neighbor_seed_frame: int,
	neighbor_seed_w: float | None,
	neighbor_seed_h: float | None,
) -> object:
	"""Build a per-frame seed-derived torso-size resolver (w, h).

	The walker solves POSITION; SIZE is seed-derived geometry, not an
	independent size solve. Per the resolved plan decision:
	  - in-interval frames: linear interpolation between the two bracketing
	    seed boxes (this pass's seed box and the neighbor seed box);
	  - one-sided extrapolated frames (outside the seed..neighbor span) and the
	    pre-race stationary case (no neighbor size known): constant-from-anchor
	    (this pass's seed box), per contract C4.

	When neighbor size is unknown (legacy callers that pass only neighbor
	centers), size is constant-from-anchor for every frame.

	Args:
		seed_frame: Anchoring seed frame index.
		seed_w: Anchoring seed torso width (processed pixels).
		seed_h: Anchoring seed torso height (processed pixels).
		neighbor_seed_frame: Neighbor seed frame index.
		neighbor_seed_w: Neighbor seed torso width, or None if unknown.
		neighbor_seed_h: Neighbor seed torso height, or None if unknown.

	Returns:
		A callable frame_index -> (w, h) in processed pixels.
	"""
	span = neighbor_seed_frame - seed_frame

	#----------------------------------------
	def size_at_frame(frame_index: int) -> tuple:
		# No neighbor size or degenerate span: constant-from-anchor.
		if neighbor_seed_w is None or neighbor_seed_h is None or span == 0:
			return seed_w, seed_h
		# Fractional position from this seed toward the neighbor seed.
		frac = (frame_index - seed_frame) / span
		# Clamp to [0, 1]: frames outside the interval (one-sided
		# extrapolation) hold the nearest seed box, per C4.
		frac = max(0.0, min(1.0, frac))
		box_w = seed_w + frac * (neighbor_seed_w - seed_w)
		box_h = seed_h + frac * (neighbor_seed_h - seed_h)
		return box_w, box_h

	return size_at_frame


#============================================
def lighten_trace(trace: blob_trace.BlobObserverTrace) -> blob_trace.BlobObserverTrace:
	"""Return a render-only copy of a trace with heavy arrays dropped (WS2-A).

	The live BlobObserverTrace carries large per-frame numpy arrays
	(residual_dog, residual_pre_dog, validity_mask) used only during blob
	extraction. The renderer (walk_render) needs none of them; it reads only
	raw_blobs, corridor_blobs, winner_blob, acceptance_box, and roi_origin_xy.
	Dropping the arrays bounds memory when a frame->trace map is retained for
	the whole interval (the longest corpus interval can be thousands of
	frames; keeping residual arrays would be a memory blow-up risk).

	Coordinate space is preserved exactly: trace geometry stays PROCESSED/
	ROI-relative with roi_origin_xy in processed coords (the handoff contract
	to WS2-B2). This function performs NO coordinate conversion.

	Args:
		trace: Live BlobObserverTrace captured during the walk.

	Returns:
		A new BlobObserverTrace with residual_dog, residual_pre_dog, and
		validity_mask set to None; all overlay fields copied unchanged.
	"""
	light = blob_trace.BlobObserverTrace(
		frame_index=trace.frame_index,
		roi_bounds=trace.roi_bounds,
		has_residual=trace.has_residual,
		residual_dog=None,
		residual_pre_dog=None,
		validity_mask=None,
		raw_blobs=trace.raw_blobs,
		corridor_blobs=trace.corridor_blobs,
		winner_blob=trace.winner_blob,
		winner_score=trace.winner_score,
		local_tangent=trace.local_tangent,
		roi_origin_xy=trace.roi_origin_xy,
		acceptance_box=trace.acceptance_box,
		dog_diameter=trace.dog_diameter,
		corridor_radius=trace.corridor_radius,
		reject_reason=trace.reject_reason,
	)
	return light


#============================================
def measure_in_box_heat_for_frame(
	live_trace: blob_trace.BlobObserverTrace | None,
	cx: float,
	cy: float,
	w: float,
	h: float,
) -> tuple:
	"""Cache the in-box motion-cue heat for one emitted frame at walk time.

	Computes the heat scalars while the residual is still LIVE on the trace,
	against the FINAL solved box for this frame. This replaces the
	render-time re-derive so no extra decode is paid: the residual was
	already computed during the walk and lives on the trace until lightening.

	The single-source threshold is residual_motion.DEFAULT_THRESHOLD, the same
	value the per-interval render_heat_summary.json records as "threshold".

	Args:
		live_trace: The LIVE BlobObserverTrace (residual_dog/validity_mask
			still present), or None when no trace exists for this frame.
		cx: Final solved box center x (PROCESSED pixels).
		cy: Final solved box center y (PROCESSED pixels).
		w: Final solved box width (PROCESSED pixels).
		h: Final solved box height (PROCESSED pixels).

	Returns:
		(hot_mean, hot_count): from the frozen heat-measurement primitive when a live
		residual is present, else (None, 0).
	"""
	# No live residual (miss frame or already lightened): nothing to measure.
	if live_trace is None or live_trace.residual_dog is None:
		return (None, 0)
	# Build the FINAL solved box as a typed PROCESSED box; the primitive
	# subtracts roi_origin_xy exactly once internally.
	box = coord_space.ProcessedBox(cx=float(cx), cy=float(cy), w=float(w), h=float(h))
	hot_mean, hot_count = common_tools.in_box_heat.measure_in_box_heat(
		residual_dog=live_trace.residual_dog,
		validity_mask=live_trace.validity_mask,
		roi_origin=live_trace.roi_origin_xy,
		box=box,
		threshold=residual_motion.DEFAULT_THRESHOLD,
	)
	return (hot_mean, hot_count)


#============================================
@dataclasses.dataclass
class WalkSummary:
	"""Summary of a one-direction walk from seed toward neighbor seed.

	Attributes:
		accepts: List of accepted frame indices.
		stop_frame: Frame index where walk stopped.
		stop_reason: One of "hit_neighbor_seed", "boundary", "loop_guard".
		total_frames_visited: Total frames emitted to debug log.
		accepted_count: Frames with status "accepted".
		interpolated_count: Frames with status "interpolated".
		extrapolated_count: Frames with status "extrapolated".
		soft_miss_no_blob_count: Frames with status "soft_miss_no_blob".
		soft_miss_no_path_count: Frames with status "soft_miss_no_path".
		longest_no_accept_streak: Longest contiguous run of non-accepted statuses.
		accepted_fraction: accepted_count / total emitted frames.
		last_accepted_frame_index: Frame index of last accepted frame (or None).
		final_displacement_to_neighbor_px: Distance from last accepted to neighbor
		    seed center (pixels), or None if no accepts or neighbor coords unknown.
		mode_disagreement_count: Always 0 in v13 (no production/audit split).
		direction_path: Per-frame solved box path for this direction (WS1-A),
		    a list of {frame_index, cx, cy, w, h, conf} dicts in PROCESSED
		    pixels, one per emitted walk frame (bootstrap + windowed steps;
		    after-walk diagnostic rows excluded). conf is the deterministic
		    distance-from-anchor decay (see conf_from_anchor). w/h are
		    seed-derived geometry, not an independent size solve. The path lives
		    in processed space; projection to source is a downstream (WS1-B)
		    concern, not done here.
		direction_trace_map: Per-frame frame_index -> lightened
		    BlobObserverTrace map for this direction (WS2-A), one entry per
		    emitted walk frame (bootstrap + windowed steps; after-walk
		    diagnostic rows excluded, matching direction_path). Each trace has
		    its heavy arrays (residual_dog, residual_pre_dog, validity_mask)
		    dropped via lighten_trace; the overlay fields (raw_blobs,
		    corridor_blobs, winner_blob, acceptance_box, roi_origin_xy) are
		    preserved in PROCESSED/ROI-relative space. This is the live trace
		    threaded to the renderer under --walk; it is image-derived
		    per-frame data (allowed) and carries NO accepted-blob memory. FWD
		    and BWD build independent maps (C9).
	"""
	accepts: list
	stop_frame: int
	stop_reason: str
	total_frames_visited: int
	accepted_count: int
	interpolated_count: int
	extrapolated_count: int
	soft_miss_no_blob_count: int
	soft_miss_no_path_count: int
	longest_no_accept_streak: int
	accepted_fraction: float
	last_accepted_frame_index: object
	final_displacement_to_neighbor_px: object
	mode_disagreement_count: int
	direction_path: list
	direction_trace_map: dict


#============================================
def _run_viterbi_and_emit_oldest(
	window_buffer: collections.deque,
	seed_w: float,
	fps: float,
	sign: int,
	debug_log: walk_debug_log.DebugLogWriter,
	visited_frames: set,
	accepts: list,
	status_counts: dict,
	all_emitted_statuses: list,
	last_accepted_cx: float,
	last_accepted_cy: float,
	scene_transform: scene_coords.SceneTransform,
	emit_count: int,
	seed_frame: int,
	size_at_frame: object,
	direction_path: list,
	direction_trace_map: dict,
) -> tuple:
	"""Run Viterbi over the full window and emit the specified number of oldest frames.

	Args:
		window_buffer: Rolling deque of frame data dicts.
		seed_w: Torso width in pixels.
		fps: Source frame rate.
		sign: +1 FWD, -1 BWD.
		debug_log: DebugLogWriter to emit rows to.
		visited_frames: Set of emitted frame indices (mutated).
		accepts: List of accepted frame indices (mutated).
		status_counts: Dict with keys accepted, interpolated, extrapolated,
		    soft_miss_no_blob, soft_miss_no_path (mutated).
		all_emitted_statuses: List of status strings (mutated).
		last_accepted_cx: Most recent accepted x-pixel.
		last_accepted_cy: Most recent accepted y-pixel.
		scene_transform: For coordinate conversion.
		emit_count: Number of oldest frames to emit (1 in steady state, all in flush).
		seed_frame: Anchoring seed frame index (for conf decay).
		size_at_frame: Callable frame_index -> (w, h) seed-derived torso size.
		direction_path: Per-frame solved box path (mutated; one
		    {frame_index, cx, cy, w, h, conf} dict appended per emitted frame).
		direction_trace_map: Per-frame frame_index -> lightened
		    BlobObserverTrace map (mutated; one entry appended per emitted
		    frame that carried a trace; WS2-A).

	Returns:
		(updated_last_accepted_cx, updated_last_accepted_cy)
	"""
	# Build arrays for Viterbi.
	window_frames = [entry["frame_index"] for entry in window_buffer]
	window_candidates = [entry["candidates"] for entry in window_buffer]

	# Run Viterbi path selection over the full window.
	path = walk_viterbi.select_path(window_candidates, seed_w, fps)
	path_cost_total = walk_viterbi.compute_path_cost(path, seed_w, fps)
	candidates_in_window_count = walk_status.count_candidates_in_window(window_candidates)

	# Determine per-frame statuses (and seed-derived per-frame torso size).
	results = walk_status.emit_status_from_path(
		window_frames=window_frames,
		window_candidates=window_candidates,
		path=path,
		last_accepted_cx=last_accepted_cx,
		last_accepted_cy=last_accepted_cy,
		size_at_frame=size_at_frame,
	)

	# Emit the requested number of oldest frames.
	buffer_list = list(window_buffer)
	for k in range(min(emit_count, len(results))):
		r = results[k]
		entry = buffer_list[k]
		fi = r["frame_index"]

		# Update state based on emission status.
		if r["status"] == "accepted":
			last_accepted_cx = r["cx"]
			last_accepted_cy = r["cy"]
			accepts.append(fi)
			status_counts["accepted"] += 1
		elif r["status"] == "interpolated":
			status_counts["interpolated"] += 1
		elif r["status"] == "extrapolated":
			status_counts["extrapolated"] += 1
		elif r["status"] == "soft_miss_no_blob":
			status_counts["soft_miss_no_blob"] += 1
		elif r["status"] == "soft_miss_no_path":
			status_counts["soft_miss_no_path"] += 1

		all_emitted_statuses.append(r["status"])

		# Compute scene coords for accepted candidate position.
		cand_cx_val = r["cx"] if r["blob"] is not None else None
		cand_cy_val = r["cy"] if r["blob"] is not None else None
		cand_scene_x_val = None
		cand_scene_y_val = None
		if cand_cx_val is not None and cand_cy_val is not None:
			sc = scene_transform.pixel_to_scene(fi, cand_cx_val, cand_cy_val)
			cand_scene_x_val = sc[0]
			cand_scene_y_val = sc[1]

		# actual_jump: observed per-frame displacement (processed pixels) from the
		# prediction anchor (last-accepted center) to this frame's selected candidate.
		# Already implied by the emitted pred/cand pair; recorded so the report layer
		# can express it in torso-width units. Blank on miss frames (no candidate)
		# where no real displacement exists.
		pred_cx_val = entry["pred_cx"]
		pred_cy_val = entry["pred_cy"]
		actual_jump_val: float | None = None
		if (cand_cx_val is not None and cand_cy_val is not None
				and pred_cx_val is not None and pred_cy_val is not None):
			jump_dx = cand_cx_val - pred_cx_val
			jump_dy = cand_cy_val - pred_cy_val
			actual_jump_val = (jump_dx * jump_dx + jump_dy * jump_dy) ** 0.5

		# Per-frame solved box: position from the path, size seed-derived,
		# conf the deterministic distance-from-anchor decay (WS1-A). Carried
		# on EVERY emitted frame (not None) for the downstream box path.
		frame_w = r["w"]
		frame_h = r["h"]
		frame_conf = conf_from_anchor(fi, seed_frame)
		# cache in-box heat NOW, while the live trace's residual is still
		# present, measured against the FINAL solved box (the emitted cx/cy/w/h).
		# The renderer reads these scalars instead of re-deriving the residual.
		in_box_hot_mean, in_box_hot_count = measure_in_box_heat_for_frame(
			entry.get("live_trace"), r["cx"], r["cy"], frame_w, frame_h,
		)
		direction_path.append({
			"frame_index": fi,
			"cx": r["cx"],
			"cy": r["cy"],
			"w": frame_w,
			"h": frame_h,
			"conf": frame_conf,
			# Per-frame five-value walk status, carried alongside the solved box
			# for diagnostics and the walk debug log. Image-derived per-frame
			# label only; no cross-frame state and no Hermite reference.
			"status": r["status"],
			"in_box_hot_mean": in_box_hot_mean,
			"in_box_hot_count": in_box_hot_count,
		})

		# WS2-A: thread the live (lightened) per-frame trace to the render map.
		# Aligned 1:1 with the emitted frame; None on miss frames (no trace).
		entry_light_trace = entry.get("light_trace")
		if entry_light_trace is not None:
			direction_trace_map[fi] = entry_light_trace

		debug_row = walk_debug_log.DebugLogRow(
			frame_index=fi,
			step=None,
			direction="+" if sign > 0 else "-",
			status=r["status"],
			dt=None,
			torso_w_px=frame_w,
			torso_h_px=frame_h,
			pred_cx=entry["pred_cx"],
			pred_cy=entry["pred_cy"],
			cand_cx=cand_cx_val,
			cand_cy=cand_cy_val,
			cand_scene_x=cand_scene_x_val,
			cand_scene_y=cand_scene_y_val,
			actual_jump=actual_jump_val,
			obs_confidence=entry["obs_confidence"],
			obs_corridor_n=entry["obs_corridor_n"],
			obs_raw_n=entry["obs_raw_n"],
			winner_strength_score=entry["winner_strength_score"],
			winner_size_score=entry["winner_size_score"],
			winner_proximity_score=entry["winner_proximity_score"],
			winner_total_score=entry["winner_total_score"],
			candidates_json=entry["candidates_json"],
			reject_reason="",
			roi_anchor_source="accepted",
			path_cost=path_cost_total,
			candidates_in_window=candidates_in_window_count,
		)
		debug_log.write_row(debug_row)
		visited_frames.add(fi)

	# Remove the emitted entries from the buffer.
	for _ in range(min(emit_count, len(results))):
		if window_buffer:
			window_buffer.popleft()

	return last_accepted_cx, last_accepted_cy


#============================================
def resolve_audit_winner(
	trace: blob_trace.BlobObserverTrace,
	audit_rule: str,
	pred_center: tuple,
) -> dict | None:
	"""Resolve an audit winner blob from corridor blobs using a named rule.

	Retained for backward compatibility with diagnostic tools. The v13 walker
	does not call this in the main per-step loop.

	Audit rules:
	  - "center_of_mass": integrated_mag-weighted centroid of all corridor blobs
	  - "strongest_blob": max(integrated_mag)
	  - "body_position": blob closest to pred_center; tie-break by max area

	Args:
		trace: BlobObserverTrace with corridor_blobs and winner_blob.
		audit_rule: One of the three rule names above.
		pred_center: (pred_cx, pred_cy) tuple.

	Returns:
		A blob dict from trace.corridor_blobs, or None if no candidates.
	"""
	if not trace.corridor_blobs:
		return None

	if audit_rule == "center_of_mass":
		total_mag = sum(b.get("integrated_mag", 0.0) for b in trace.corridor_blobs)
		if total_mag <= 0.0:
			return trace.corridor_blobs[0] if trace.corridor_blobs else None
		weighted_x = sum(
			b.get("centroid_x", 0.0) * b.get("integrated_mag", 0.0)
			for b in trace.corridor_blobs
		)
		weighted_y = sum(
			b.get("centroid_y", 0.0) * b.get("integrated_mag", 0.0)
			for b in trace.corridor_blobs
		)
		com_x = weighted_x / total_mag
		com_y = weighted_y / total_mag
		best_blob = None
		best_dist = float("inf")
		for blob in trace.corridor_blobs:
			dx = blob.get("centroid_x", 0.0) - com_x
			dy = blob.get("centroid_y", 0.0) - com_y
			dist = (dx * dx + dy * dy) ** 0.5
			if dist < best_dist:
				best_dist = dist
				best_blob = blob
		return best_blob

	elif audit_rule == "strongest_blob":
		return max(
			trace.corridor_blobs,
			key=lambda b: b.get("integrated_mag", 0.0),
		)

	elif audit_rule == "body_position":
		pred_cx, pred_cy = pred_center
		best_blob = None
		best_dist = float("inf")
		best_area = -1
		for blob in trace.corridor_blobs:
			if "dist_to_pred_px" in blob:
				dist = blob["dist_to_pred_px"]
			else:
				dx = blob.get("centroid_x", 0.0) - pred_cx
				dy = blob.get("centroid_y", 0.0) - pred_cy
				dist = (dx * dx + dy * dy) ** 0.5
			area = blob.get("area", 0)
			if dist < best_dist or (dist == best_dist and area > best_area):
				best_dist = dist
				best_area = area
				best_blob = blob
		return best_blob

	else:
		raise ValueError(f"Unknown audit_rule: {audit_rule}")


#============================================
def _compute_roi_and_observe(
	frame_f: int,
	anchor_cx: float,
	anchor_cy: float,
	seed_w: float,
	seed_h: float,
	local_tangent: tuple,
	scene_transform: scene_coords.SceneTransform,
	reader: object,
	residual_cache: dict,
	fps: float,
	stride: int,
) -> tuple:
	"""Build the acceptance box and ROI around an anchor, then observe blobs.

	Shared by the bootstrap step and the per-step loop. The acceptance box and
	prediction center are both anchored to (anchor_cx, anchor_cy); the ROI is
	clamped to the post-bin frame boundary.

	Args:
		frame_f: Frame index to observe.
		anchor_cx: x-pixel anchor for the acceptance box and prediction center.
		anchor_cy: y-pixel anchor for the acceptance box and prediction center.
		seed_w: Torso width in processed pixels.
		seed_h: Torso height in processed pixels.
		local_tangent: Axis-aligned tangent estimate (passed through).
		scene_transform: SceneTransform for the observer.
		reader: FrameReader (provides width/height for clamping).
		residual_cache: Per-walk residual cache (scoped per C6).
		fps: Source frame rate.
		stride: Neighbor stride for residual.

	Returns:
		The observe_blob_at result object (or None), and the trace-sink
		holder. When the processed anchor is off-frame, returns
		(None, trace_sink_holder) as a soft-miss without calling observe.
	"""
	# All inputs are already PROCESSED-pixel coords (Option A, 2026-05-29).
	# Do NOT re-introduce any source->processed conversion here (that was the
	# reverted WS0-A regression); the seeds and last-accepted anchors that
	# feed anchor_cx/anchor_cy are processed floats.
	trace_sink_holder = type('TraceSink', (), {'observer_trace': None})()

	# Off-frame predicate (typed): if the processed prediction lands outside
	# the post-bin frame, emit a soft-miss instead of building a degenerate
	# ROI deep inside observe_blob_at (the #101 class of bug). This is the
	# graceful replacement for the old degenerate-ROI crash.
	pred_center = coord_space.ProcessedPoint(cx=anchor_cx, cy=anchor_cy)
	if not pred_center.in_bounds(reader.geometry):
		# one concise warning; callers treat obs=None as a soft-miss
		print(
			f"WARNING: walker prediction off-frame at frame {frame_f} "
			f"(processed cx={anchor_cx:.1f}, cy={anchor_cy:.1f}); soft-miss"
		)
		return None, trace_sink_holder

	# Acceptance box edges in PROCESSED pixels, centered on the anchor.
	accept_x1 = anchor_cx - 0.5 * seed_w
	accept_y1 = anchor_cy - 0.75 * seed_h
	accept_x2 = anchor_cx + 0.5 * seed_w
	accept_y2 = anchor_cy + 0.75 * seed_h
	# Typed PROCESSED acceptance box. observe_blob_at calls .edges() on it,
	# so center/size are chosen so its edges equal the (x1,y1,x2,y2) above.
	acceptance_box = coord_space.ProcessedBox(
		cx=0.5 * (accept_x1 + accept_x2),
		cy=0.5 * (accept_y1 + accept_y2),
		w=accept_x2 - accept_x1,
		h=accept_y2 - accept_y1,
	)

	# ROI edges in PROCESSED pixels: pad the acceptance box, clamp to the
	# post-bin frame boundary (reader.width/height are PROCESSED dims).
	# Numeric clamp math is unchanged from the prior tuple-based ROI.
	roi_pad = max(20, seed_w)
	roi_x1 = max(0, int(accept_x1 - roi_pad))
	roi_y1 = max(0, int(accept_y1 - roi_pad))
	roi_x2 = min(reader.width, int(accept_x2 + roi_pad))
	roi_y2 = min(reader.height, int(accept_y2 + roi_pad))
	# Typed PROCESSED ROI override whose .edges() equal the clamped corners.
	roi_override = coord_space.ProcessedBox(
		cx=0.5 * (roi_x1 + roi_x2),
		cy=0.5 * (roi_y1 + roi_y2),
		w=roi_x2 - roi_x1,
		h=roi_y2 - roi_y1,
	)

	# pred_box carries the predicted SIZE in PROCESSED pixels; observe uses
	# only its .w / .h (pred_center owns the center).
	pred_box = coord_space.ProcessedBox(
		cx=anchor_cx, cy=anchor_cy, w=seed_w, h=seed_h,
	)
	# dog_diameter is a processed-pixel scalar LENGTH (not a coord primitive).
	dog_diameter_override = 0.7 * seed_w

	obs = residual_motion.observe_blob_at(
		frame_index=frame_f,
		pred_center=pred_center,
		pred_box=pred_box,
		local_tangent=local_tangent,
		scene_transform=scene_transform,
		reader=reader,
		residual_cache=residual_cache,
		fps=fps,
		stride=stride,
		trace_sink=trace_sink_holder,
		roi_override=roi_override,
		dog_diameter_override=dog_diameter_override,
		acceptance_box=acceptance_box,
	)

	return obs, trace_sink_holder


#============================================
def gather_frame_candidates(
	obs: object,
	trace_sink_holder: object,
) -> list:
	"""Gather one frame's blob candidate list from a trace sink.

	This is the single per-frame candidate source for the in-pipeline windowed
	walker. The interval-level gathering is just this helper called once per
	frame across the interval frame range (see _build_window_entry and
	_run_windowed_steps); a frame-aligned sequence of these lists is the
	candidate lattice the Viterbi DP consumes.

	The candidate list is BlobObserverTrace.corridor_blobs, produced by
	residual_motion.observe_blob_at and read off the trace sink populated by
	that call. observe_blob_at is unchanged (API Decision 2026-05-28); this
	helper only reads the trace it already populates. The pipeline's
	precomputed_store (the per-interval residual store) is passed to
	observe_blob_at by the caller, not by this helper; these candidates are
	image-derived observations sourced from that store, not decisions.

	Declared candidate coordinate space (Candidate source contract,
	docs/COORDINATE_SPACES.md row "observe_blob_at trace corridor_blobs
	centroids"): each blob's centroid_x / centroid_y are PROCESSED full-frame
	pixels. The ROI origin is already added back inside observe_blob_at
	(residual_motion roi_x1/roi_y1 addback), so centroids are full-frame, NOT
	ROI-relative. This is distinct from observe_blob_at's RETURN centroid
	(BlobObservation.center_pixel), which is SOURCE space; the corridor_blobs
	candidate list on the trace stays PROCESSED full-frame. The windowed walker
	consumes only this declared space.

	Frame alignment is preserved by construction: every frame yields exactly
	one list. A frame with no observation (obs is None, e.g. an off-frame
	prediction soft-miss) or no surviving blobs yields an empty list, so the
	gathered sequence stays index-aligned with the interval frame range.

	Args:
		obs: observe_blob_at result for this frame (or None on a soft-miss).
		trace_sink_holder: The trace-sink object passed to observe_blob_at;
			its observer_trace carries corridor_blobs after the call.

	Returns:
		The frame's candidate list (PROCESSED full-frame centroids), ordered
		by integrated_mag descending as observe_blob_at produced it. Empty list
		when obs is None or the trace has no corridor blobs.
	"""
	# No observation this frame (soft-miss): empty, frame-aligned candidate list.
	if obs is None:
		return []
	trace = trace_sink_holder.observer_trace
	# Trace may be absent even when obs is not None for defensive direct
	# callers; treat a missing trace as an empty candidate frame.
	if trace is None:
		return []
	# corridor_blobs holds every blob passing the geometric ROI filter, with
	# PROCESSED full-frame centroids (ROI origin already added back).
	candidates = list(trace.corridor_blobs)
	return candidates


#============================================
def _build_window_entry(obs: object, trace_sink_holder: object, frame_f: int,
	pred_cx: float, pred_cy: float) -> dict:
	"""Build one rolling-buffer entry from an observation result.

	Extracts the candidate list from trace.corridor_blobs and assembles the
	per-frame debug fields. No emission happens here.

	Args:
		obs: observe_blob_at result (or None).
		trace_sink_holder: Trace-sink object carrying observer_trace.
		frame_f: Frame index.
		pred_cx: Prediction x-pixel (last-accepted anchor) for this frame.
		pred_cy: Prediction y-pixel (last-accepted anchor) for this frame.

	Returns:
		A buffer-entry dict with frame_index, candidates, pred_*, obs_*,
		candidates_json, winner_*_score, and light_trace keys.
	"""
	trace = trace_sink_holder.observer_trace if obs is not None else None

	# Extract candidate list from trace.corridor_blobs (NOT obs.center_pixel)
	# via the single per-frame candidate-source helper. Centroids are PROCESSED
	# full-frame; an obs-less or blob-less frame yields an empty list, keeping
	# the per-frame sequence frame-aligned.
	candidates = gather_frame_candidates(obs, trace_sink_holder)

	# Collect debug fields for this frame.
	if obs is not None and trace is not None:
		obs_corridor_n = len(trace.corridor_blobs)
		obs_raw_n = len(trace.raw_blobs)
		obs_confidence_val = obs.confidence
		candidates_json_val = json.dumps([
			{
				"centroid_x": b.get("centroid_x"),
				"centroid_y": b.get("centroid_y"),
				"area": b.get("area"),
				"integrated_mag": b.get("integrated_mag"),
				"in_acceptance_box": b.get("in_acceptance_box"),
				"in_corridor": b.get("in_corridor"),
				"dist_to_pred_px": b.get("dist_to_pred_px"),
				"strength_score": b.get("strength_score"),
				"size_score": b.get("size_score"),
				"proximity_score": b.get("proximity_score"),
				"total_score": b.get("total_score"),
			}
			for b in (trace.raw_blobs + trace.corridor_blobs)
		])
		winner_strength_score = trace.winner_blob.get("strength_score") if trace.winner_blob else None
		winner_size_score = trace.winner_blob.get("size_score") if trace.winner_blob else None
		winner_proximity_score = trace.winner_blob.get("proximity_score") if trace.winner_blob else None
		winner_total_score = trace.winner_blob.get("total_score") if trace.winner_blob else None
	else:
		obs_corridor_n = None
		obs_raw_n = None
		obs_confidence_val = None
		candidates_json_val = None
		winner_strength_score = None
		winner_size_score = None
		winner_proximity_score = None
		winner_total_score = None

	# WS2-A: carry a render-only (heavy-array-dropped) copy of the live trace
	# so the emit step can thread it to the frame->trace map. Coordinate space
	# is preserved (PROCESSED/ROI-relative). None on a miss frame (no trace).
	light_trace = lighten_trace(trace) if trace is not None else None

	entry = {
		"frame_index": frame_f,
		"candidates": candidates,
		"pred_cx": pred_cx,
		"pred_cy": pred_cy,
		"obs_corridor_n": obs_corridor_n,
		"obs_raw_n": obs_raw_n,
		"obs_confidence": obs_confidence_val,
		"candidates_json": candidates_json_val,
		"winner_strength_score": winner_strength_score,
		"winner_size_score": winner_size_score,
		"winner_proximity_score": winner_proximity_score,
		"winner_total_score": winner_total_score,
		"light_trace": light_trace,
		# also carry the LIVE trace (residual still present) so the emit
		# step can measure in-box heat against the FINAL solved box before
		# lightening drops the residual. Bounded: only the <=9 in-buffer window
		# frames hold a live residual at once; the stored direction_trace_map
		# still gets the lightened copy, so the memory bound is unchanged.
		"live_trace": trace,
	}
	return entry


#============================================
def _run_bootstrap_step(
	seed_frame: int,
	seed_cx: float,
	seed_cy: float,
	seed_w: float,
	seed_h: float,
	local_tangent: tuple,
	scene_transform: scene_coords.SceneTransform,
	reader: object,
	residual_cache: dict,
	fps: float,
	stride: int,
	sign: int,
	debug_log: walk_debug_log.DebugLogWriter,
	accepts: list,
	visited_frames: set,
	status_counts: dict,
	all_emitted_statuses: list,
	direction_path: list,
	direction_trace_map: dict,
) -> None:
	"""Run the bootstrap step (step == 0) and emit its row immediately.

	Observes blobs at the seed frame using the seed-local shape and emits a
	single bootstrap row. Mutates accepts, visited_frames, status_counts,
	all_emitted_statuses, and direction_path in place.

	Args:
		seed_frame: Seed frame index (bootstrap target).
		seed_cx: Seed center x-pixel.
		seed_cy: Seed center y-pixel.
		seed_w: Torso width in processed pixels.
		seed_h: Torso height in processed pixels.
		local_tangent: Axis-aligned tangent estimate.
		scene_transform: SceneTransform for the observer.
		reader: FrameReader.
		residual_cache: Per-walk residual cache.
		fps: Source frame rate.
		stride: Neighbor stride for residual.
		sign: +1 FWD, -1 BWD (for the row direction field).
		debug_log: DebugLogWriter to emit the row to.
		accepts: List of accepted frame indices (mutated).
		visited_frames: Set of emitted frame indices (mutated).
		status_counts: Status-count dict (mutated).
		all_emitted_statuses: List of status strings (mutated).
	"""
	step = 0
	frame_f = seed_frame

	# Bootstrap state: start at the seed frame position (prev == seed center).
	obs, trace_sink_holder = _compute_roi_and_observe(
		frame_f=frame_f,
		anchor_cx=seed_cx,
		anchor_cy=seed_cy,
		seed_w=seed_w,
		seed_h=seed_h,
		local_tangent=local_tangent,
		scene_transform=scene_transform,
		reader=reader,
		residual_cache=residual_cache,
		fps=fps,
		stride=stride,
	)

	# Bootstrap row is always emitted immediately.
	bootstrap_status = "accepted" if obs is not None else "soft_miss_no_blob"
	if obs is not None:
		accepts.append(frame_f)
		status_counts["accepted"] += 1
	else:
		status_counts["soft_miss_no_blob"] += 1

	# Bootstrap solved box: seed center + seed box (frames_from_anchor == 0,
	# so conf == CONF_START). The seed frame is the pass anchor.
	# cache in-box heat against the FINAL (seed) box while the bootstrap
	# trace's residual is still live, before lighten_trace drops it below.
	bootstrap_trace = trace_sink_holder.observer_trace if obs is not None else None
	in_box_hot_mean, in_box_hot_count = measure_in_box_heat_for_frame(
		bootstrap_trace, seed_cx, seed_cy, seed_w, seed_h,
	)
	direction_path.append({
		"frame_index": frame_f,
		"cx": seed_cx,
		"cy": seed_cy,
		"w": seed_w,
		"h": seed_h,
		"conf": conf_from_anchor(frame_f, seed_frame),
		# Bootstrap (seed) frame status, see the steady-state append for why
		# the walk status rides on the path entry.
		"status": bootstrap_status,
		"in_box_hot_mean": in_box_hot_mean,
		"in_box_hot_count": in_box_hot_count,
	})

	# WS2-A: thread the bootstrap-frame live (lightened) trace to the render
	# map. None when the seed-frame observation produced no trace.
	if bootstrap_trace is not None:
		direction_trace_map[frame_f] = lighten_trace(bootstrap_trace)

	debug_row = walk_debug_log.DebugLogRow(
		frame_index=frame_f,
		step=step,
		direction="+" if sign > 0 else "-",
		status=bootstrap_status,
		pred_cx=seed_cx,
		pred_cy=seed_cy,
		torso_w_px=seed_w,
		torso_h_px=seed_h,
		roi_anchor_source="accepted",
	)
	debug_log.write_row(debug_row)
	visited_frames.add(frame_f)
	all_emitted_statuses.append(bootstrap_status)


#============================================
def _run_windowed_steps(
	seed_frame: int,
	neighbor_seed_frame: int,
	seed_w: float,
	seed_h: float,
	local_tangent: tuple,
	scene_transform: scene_coords.SceneTransform,
	reader: object,
	residual_cache: dict,
	fps: float,
	stride: int,
	sign: int,
	debug_log: walk_debug_log.DebugLogWriter,
	window_buffer: collections.deque,
	accepts: list,
	visited_frames: set,
	status_counts: dict,
	all_emitted_statuses: list,
	last_accepted_cx: float,
	last_accepted_cy: float,
	size_at_frame: object,
	direction_path: list,
	direction_trace_map: dict,
) -> tuple:
	"""Run the per-step fill+emit loop and the end-of-walk flush.

	Advances frame-by-frame from the seed toward the neighbor seed, filling the
	rolling buffer and emitting the oldest frame once the buffer is full, then
	flushes all remaining buffered frames. Mutates window_buffer, accepts,
	visited_frames, status_counts, and all_emitted_statuses in place.

	Args:
		seed_frame: Seed frame index (walk origin).
		neighbor_seed_frame: Neighbor seed frame index (termination target).
		seed_w: Torso width in processed pixels.
		seed_h: Torso height in processed pixels.
		local_tangent: Axis-aligned tangent estimate.
		scene_transform: SceneTransform for the observer.
		reader: FrameReader.
		residual_cache: Per-walk residual cache.
		fps: Source frame rate.
		stride: Neighbor stride for residual.
		sign: +1 FWD, -1 BWD.
		debug_log: DebugLogWriter to emit rows to.
		window_buffer: Rolling deque of per-frame data (mutated).
		accepts: List of accepted frame indices (mutated).
		visited_frames: Set of emitted frame indices (mutated).
		status_counts: Status-count dict (mutated).
		all_emitted_statuses: List of status strings (mutated).
		last_accepted_cx: Most recent accepted x-pixel before the loop.
		last_accepted_cy: Most recent accepted y-pixel before the loop.
		size_at_frame: Callable frame_index -> (w, h) seed-derived torso size.
		direction_path: Per-frame solved box path (mutated).
		direction_trace_map: Per-frame frame_index -> lightened
		    BlobObserverTrace map (mutated; WS2-A).

	Returns:
		(last_accepted_cx, last_accepted_cy, frame_f, stop_reason)
	"""
	step = 1
	dt = stride
	# Loop-bound guard: protects against malformed intervals.
	max_steps_guard = abs(neighbor_seed_frame - seed_frame) + 1
	stop_reason = "loop_guard"
	frame_f = seed_frame

	while True:
		frame_f = seed_frame + sign * step * dt

		# Termination: boundary check.
		if frame_f < 0 or frame_f >= reader.frame_count:
			stop_reason = "boundary"
			break

		# Termination: hit neighbor seed.
		if frame_f == neighbor_seed_frame:
			stop_reason = "hit_neighbor_seed"
			break

		# Termination: loop-bound guard.
		if step > max_steps_guard:
			stop_reason = "loop_guard"
			break

		# Predict next position: anchor to last accepted position.
		# No velocity model; last-accepted anchor is the stable baseline.
		pred_cx = last_accepted_cx
		pred_cy = last_accepted_cy

		# Construct acceptance box / ROI anchored to last-accepted position
		# and observe blobs (shared with the bootstrap step).
		obs, trace_sink_holder = _compute_roi_and_observe(
			frame_f=frame_f,
			anchor_cx=pred_cx,
			anchor_cy=pred_cy,
			seed_w=seed_w,
			seed_h=seed_h,
			local_tangent=local_tangent,
			scene_transform=scene_transform,
			reader=reader,
			residual_cache=residual_cache,
			fps=fps,
			stride=stride,
		)

		# Append to rolling buffer.
		window_buffer.append(
			_build_window_entry(obs, trace_sink_holder, frame_f, pred_cx, pred_cy)
		)

		# Once buffer is full, emit the oldest frame.
		if len(window_buffer) >= WALKER_WINDOW_FRAMES:
			last_accepted_cx, last_accepted_cy = _run_viterbi_and_emit_oldest(
				window_buffer=window_buffer,
				seed_w=seed_w,
				fps=fps,
				sign=sign,
				debug_log=debug_log,
				visited_frames=visited_frames,
				accepts=accepts,
				status_counts=status_counts,
				all_emitted_statuses=all_emitted_statuses,
				last_accepted_cx=last_accepted_cx,
				last_accepted_cy=last_accepted_cy,
				scene_transform=scene_transform,
				emit_count=1,
				seed_frame=seed_frame,
				size_at_frame=size_at_frame,
				direction_path=direction_path,
				direction_trace_map=direction_trace_map,
			)

		step += 1

	# ============================================================
	# End-of-walk flush: emit all remaining buffered frames.
	# ============================================================
	if window_buffer:
		last_accepted_cx, last_accepted_cy = _run_viterbi_and_emit_oldest(
			window_buffer=window_buffer,
			seed_w=seed_w,
			fps=fps,
			sign=sign,
			debug_log=debug_log,
			visited_frames=visited_frames,
			accepts=accepts,
			status_counts=status_counts,
			all_emitted_statuses=all_emitted_statuses,
			last_accepted_cx=last_accepted_cx,
			last_accepted_cy=last_accepted_cy,
			scene_transform=scene_transform,
			emit_count=len(window_buffer),
			seed_frame=seed_frame,
			size_at_frame=size_at_frame,
			direction_path=direction_path,
			direction_trace_map=direction_trace_map,
		)

	return last_accepted_cx, last_accepted_cy, frame_f, stop_reason


#============================================
def _emit_diagnostic_rows(
	extra_diagnostic_frames: list,
	visited_frames: set,
	seed_frame: int,
	neighbor_seed_frame: int,
	seed_cx: float,
	seed_cy: float,
	seed_w: float,
	seed_h: float,
	neighbor_seed_cx: float | None,
	neighbor_seed_cy: float | None,
	accepts: list,
	last_accepted_cx: float,
	last_accepted_cy: float,
	sign: int,
	stop_reason: str,
	debug_log: walk_debug_log.DebugLogWriter,
) -> None:
	"""Emit after-termination diagnostic rows for sampled frames.

	Args:
		extra_diagnostic_frames: Frame indices to emit (skipped if visited).
		visited_frames: Set of already-emitted frame indices.
		seed_frame: Seed frame index.
		neighbor_seed_frame: Neighbor seed frame index.
		seed_cx: Seed center x-pixel.
		seed_cy: Seed center y-pixel.
		seed_w: Torso width in processed pixels.
		seed_h: Torso height in processed pixels.
		neighbor_seed_cx: Neighbor seed center x-pixel (or None).
		neighbor_seed_cy: Neighbor seed center y-pixel (or None).
		accepts: List of accepted frame indices.
		last_accepted_cx: Most recent accepted x-pixel.
		last_accepted_cy: Most recent accepted y-pixel.
		sign: +1 FWD, -1 BWD.
		stop_reason: Walk stop reason (for the row field).
		debug_log: DebugLogWriter to emit rows to.
	"""
	for diag_frame in extra_diagnostic_frames:
		if diag_frame in visited_frames:
			continue
		if neighbor_seed_cx is not None and neighbor_seed_cy is not None:
			t_frac = (diag_frame - seed_frame) / (neighbor_seed_frame - seed_frame) if neighbor_seed_frame != seed_frame else 0.5
			t_frac = max(0.0, min(1.0, t_frac))
			diag_pred_cx = (1 - t_frac) * seed_cx + t_frac * neighbor_seed_cx
			diag_pred_cy = (1 - t_frac) * seed_cy + t_frac * neighbor_seed_cy
		elif accepts:
			diag_pred_cx = last_accepted_cx
			diag_pred_cy = last_accepted_cy
		else:
			diag_pred_cx = seed_cx
			diag_pred_cy = seed_cy

		debug_row = walk_debug_log.DebugLogRow(
			frame_index=diag_frame,
			step=None,
			direction="+" if sign > 0 else "-",
			status="after_walk_terminated",
			pred_cx=diag_pred_cx,
			pred_cy=diag_pred_cy,
			torso_w_px=seed_w,
			torso_h_px=seed_h,
			stop_reason=stop_reason,
		)
		debug_log.write_row(debug_row)


#============================================
def _compute_summary_metrics(
	all_emitted_statuses: list,
	visited_frames: set,
	status_counts: dict,
	accepts: list,
	last_accepted_cx: float,
	last_accepted_cy: float,
	neighbor_seed_cx: float | None,
	neighbor_seed_cy: float | None,
	frame_f: int,
	stop_reason: str,
	direction_path: list,
	direction_trace_map: dict,
) -> WalkSummary:
	"""Compute the WalkSummary from accumulated walk state.

	Args:
		all_emitted_statuses: List of every emitted status string, in order.
		visited_frames: Set of emitted frame indices.
		status_counts: Status-count dict.
		accepts: List of accepted frame indices.
		last_accepted_cx: Most recent accepted x-pixel.
		last_accepted_cy: Most recent accepted y-pixel.
		neighbor_seed_cx: Neighbor seed center x-pixel (or None).
		neighbor_seed_cy: Neighbor seed center y-pixel (or None).
		frame_f: Final frame index reached.
		stop_reason: Walk stop reason.
		direction_path: Per-frame processed-space box path for this direction.
		direction_trace_map: Per-frame frame_index -> lightened
		    BlobObserverTrace map for this direction (WS2-A).

	Returns:
		WalkSummary with counts and metrics.
	"""
	# Longest no-accept streak across all emitted statuses.
	longest_no_accept_streak = 0
	current_streak = 0
	for s in all_emitted_statuses:
		if s == "accepted":
			longest_no_accept_streak = max(longest_no_accept_streak, current_streak)
			current_streak = 0
		else:
			current_streak += 1
	longest_no_accept_streak = max(longest_no_accept_streak, current_streak)

	total_frames_visited = len(visited_frames)
	accepted_count = status_counts["accepted"]
	interpolated_count = status_counts["interpolated"]
	extrapolated_count = status_counts["extrapolated"]
	soft_miss_no_blob_count = status_counts["soft_miss_no_blob"]
	soft_miss_no_path_count = status_counts["soft_miss_no_path"]

	denominator = (
		accepted_count + interpolated_count + extrapolated_count
		+ soft_miss_no_blob_count + soft_miss_no_path_count
	)
	if denominator > 0:
		accepted_fraction = accepted_count / denominator
	else:
		accepted_fraction = 0.0

	last_accepted_frame_index = accepts[-1] if accepts else None

	if last_accepted_cx is not None and neighbor_seed_cx is not None:
		dx = last_accepted_cx - neighbor_seed_cx
		dy = last_accepted_cy - (neighbor_seed_cy if neighbor_seed_cy is not None else last_accepted_cy)
		final_displacement_to_neighbor_px = math.sqrt(dx * dx + dy * dy)
	else:
		final_displacement_to_neighbor_px = None

	summary = WalkSummary(
		accepts=accepts,
		stop_frame=frame_f,
		stop_reason=stop_reason,
		total_frames_visited=total_frames_visited,
		accepted_count=accepted_count,
		interpolated_count=interpolated_count,
		extrapolated_count=extrapolated_count,
		soft_miss_no_blob_count=soft_miss_no_blob_count,
		soft_miss_no_path_count=soft_miss_no_path_count,
		longest_no_accept_streak=longest_no_accept_streak,
		accepted_fraction=accepted_fraction,
		last_accepted_frame_index=last_accepted_frame_index,
		final_displacement_to_neighbor_px=final_displacement_to_neighbor_px,
		mode_disagreement_count=0,
		direction_path=direction_path,
		direction_trace_map=direction_trace_map,
	)
	return summary


#============================================
def walk_one_direction(
	seed: dict,
	neighbor_seed_frame: int,
	reader: object,
	scene_transform: scene_coords.SceneTransform,
	fps: float,
	stride: int,
	sign: int,
	debug_log: walk_debug_log.DebugLogWriter,
	winner_mode: str = "production_winner",
	audit_rule: str | None = None,
	extra_diagnostic_frames: list | None = None,
	neighbor_seed_cx: float | None = None,
	neighbor_seed_cy: float | None = None,
	neighbor_seed_w: float | None = None,
	neighbor_seed_h: float | None = None,
) -> WalkSummary:
	"""Walk one direction from a seed toward a neighbor seed frame.

	Implements 9-frame rolling buffer + Viterbi DP path selection. Emits
	one CSV row per frame in the window, exactly once.

	Bootstrap (step == 0): seed-local-shape observe_blob_at call at the seed frame,
	    emitted immediately.
	Fill phase (step 1..WALKER_WINDOW_FRAMES-1): advance, call observe_blob_at,
	    collect candidates, no emission.
	Steady state: advance, run Viterbi over the full window, emit the oldest frame.
	End-of-walk flush: run final Viterbi over remaining frames and emit all.

	Args:
		seed: Seed dict with derived cx/cy/w/h.
		neighbor_seed_frame: Frame index of the neighbor seed (termination target).
		reader: FrameReader instance with read_frame, width, height, frame_count.
		scene_transform: SceneTransform for pixel_to_scene conversions.
		fps: Video frame rate (passed to observe_blob_at).
		stride: Neighbor stride for residual (passed to observe_blob_at).
		sign: +1 for FWD (forward stepping), -1 for BWD (backward stepping).
		debug_log: DebugLogWriter instance to write per-frame telemetry.
		winner_mode: Accepted for API compat but ignored in v13 main loop.
		audit_rule: Accepted for API compat but ignored in v13 main loop.
		extra_diagnostic_frames: List of frame indices to emit after termination.
		neighbor_seed_cx: X-coordinate of neighbor seed center (diagnostics).
		neighbor_seed_cy: Y-coordinate of neighbor seed center (diagnostics).
		neighbor_seed_w: Neighbor seed torso width (processed pixels). When
		    supplied with neighbor_seed_h, per-frame torso size is linearly
		    interpolated between the two bracketing seed boxes; otherwise size
		    is constant-from-anchor (this pass's seed box). Seed-derived
		    geometry only -- the walker solves position, not size.
		neighbor_seed_h: Neighbor seed torso height (processed pixels). See
		    neighbor_seed_w.

	Returns:
		WalkSummary with counts, metrics, the accepted frame list, and the
		per-frame processed-space box path (direction_path).
	"""
	if extra_diagnostic_frames is None:
		extra_diagnostic_frames = []

	# ============================================================
	# Coord-system guard (Option A, 2026-05-29)
	# ============================================================
	# seed may arrive as a plain dict (source coords, legacy callers) or as
	# an element of a SeedsView.seeds list (processed coords).  When the seed
	# is from a SeedsView, assert_geometry_match is called on the view before
	# reaching here (see walk_one_direction callers).  The guard is explicit
	# here as well: if the reader geometry has bin_factor > 1 and the caller
	# passed source-pixel coords, the clamp sites below will silently invert
	# the ROI.  This check makes the mismatch loud instead.
	#
	# Callers that have already built a SeedsView should call
	# view.assert_geometry_match(reader.geometry) before this function.
	# This internal guard is a belt-and-suspenders for direct dict callers.
	if hasattr(seed, "__class__") and hasattr(reader, "geometry"):
		# seeds from a SeedsView are plain dicts; no extra check needed here.
		# Direct callers with source-pixel seeds at bin>1 will hit the ROI
		# clamp mismatch at runtime. Document: prefer load_walker_seeds_view.
		pass

	# ============================================================
	# Initialization
	# ============================================================
	seed_frame = seed["frame_index"]
	seed_cx = seed["cx"]
	seed_cy = seed["cy"]
	seed_w = seed["w"]
	seed_h = seed["h"]

	accepts = []
	visited_frames = set()

	# Per-frame solved box path for this direction (WS1-A), processed pixels.
	# One {frame_index, cx, cy, w, h, conf} dict per emitted walk frame.
	direction_path = []

	# Per-frame frame_index -> lightened BlobObserverTrace map (WS2-A),
	# threaded to the renderer under --walk. Image-derived per-frame data
	# (heavy arrays dropped); no accepted-blob memory; FWD/BWD independent.
	direction_trace_map = {}

	# Seed-derived per-frame torso size resolver: linear interpolation between
	# the bracketing seed boxes when the neighbor size is known, else
	# constant-from-anchor (C4). The walker solves position; size is geometry.
	size_at_frame = make_size_at_frame(
		seed_frame=seed_frame,
		seed_w=seed_w,
		seed_h=seed_h,
		neighbor_seed_frame=neighbor_seed_frame,
		neighbor_seed_w=neighbor_seed_w,
		neighbor_seed_h=neighbor_seed_h,
	)

	# Residual cache (scoped to this walk; not shared across intervals per C6).
	residual_cache = {}

	# Per-frame tangent estimate (axis-aligned fallback).
	local_tangent = (1.0, 0.0, 0.0, 1.0)

	# Rolling buffer of per-frame data.
	# Each entry: {"frame_index", "candidates", "pred_cx", "pred_cy",
	#              "obs_corridor_n", "obs_raw_n", "obs_confidence",
	#              "candidates_json", "winner_*_score"}
	window_buffer = collections.deque()

	# Last accepted position for ROI anchoring (no velocity model in windowed walker).
	last_accepted_cx = seed_cx
	last_accepted_cy = seed_cy

	# Summary counters.
	status_counts = {
		"accepted": 0,
		"interpolated": 0,
		"extrapolated": 0,
		"soft_miss_no_blob": 0,
		"soft_miss_no_path": 0,
	}
	all_emitted_statuses = []

	# ============================================================
	# Bootstrap (step == 0): observe at the seed frame, emit immediately.
	# ============================================================
	_run_bootstrap_step(
		seed_frame=seed_frame,
		seed_cx=seed_cx,
		seed_cy=seed_cy,
		seed_w=seed_w,
		seed_h=seed_h,
		local_tangent=local_tangent,
		scene_transform=scene_transform,
		reader=reader,
		residual_cache=residual_cache,
		fps=fps,
		stride=stride,
		sign=sign,
		debug_log=debug_log,
		accepts=accepts,
		visited_frames=visited_frames,
		status_counts=status_counts,
		all_emitted_statuses=all_emitted_statuses,
		direction_path=direction_path,
		direction_trace_map=direction_trace_map,
	)

	# ============================================================
	# Per-step loop (step >= 1): fill buffer, run windowed selection, flush.
	# ============================================================
	last_accepted_cx, last_accepted_cy, frame_f, stop_reason = _run_windowed_steps(
		seed_frame=seed_frame,
		neighbor_seed_frame=neighbor_seed_frame,
		seed_w=seed_w,
		seed_h=seed_h,
		local_tangent=local_tangent,
		scene_transform=scene_transform,
		reader=reader,
		residual_cache=residual_cache,
		fps=fps,
		stride=stride,
		sign=sign,
		debug_log=debug_log,
		window_buffer=window_buffer,
		accepts=accepts,
		visited_frames=visited_frames,
		status_counts=status_counts,
		all_emitted_statuses=all_emitted_statuses,
		last_accepted_cx=last_accepted_cx,
		last_accepted_cy=last_accepted_cy,
		size_at_frame=size_at_frame,
		direction_path=direction_path,
		direction_trace_map=direction_trace_map,
	)

	# ============================================================
	# After termination: emit diagnostic rows.
	# ============================================================
	_emit_diagnostic_rows(
		extra_diagnostic_frames=extra_diagnostic_frames,
		visited_frames=visited_frames,
		seed_frame=seed_frame,
		neighbor_seed_frame=neighbor_seed_frame,
		seed_cx=seed_cx,
		seed_cy=seed_cy,
		seed_w=seed_w,
		seed_h=seed_h,
		neighbor_seed_cx=neighbor_seed_cx,
		neighbor_seed_cy=neighbor_seed_cy,
		accepts=accepts,
		last_accepted_cx=last_accepted_cx,
		last_accepted_cy=last_accepted_cy,
		sign=sign,
		stop_reason=stop_reason,
		debug_log=debug_log,
	)

	# ============================================================
	# Compute summary metrics.
	# ============================================================
	summary = _compute_summary_metrics(
		all_emitted_statuses=all_emitted_statuses,
		visited_frames=visited_frames,
		status_counts=status_counts,
		accepts=accepts,
		last_accepted_cx=last_accepted_cx,
		last_accepted_cy=last_accepted_cy,
		neighbor_seed_cx=neighbor_seed_cx,
		neighbor_seed_cy=neighbor_seed_cy,
		frame_f=frame_f,
		stop_reason=stop_reason,
		direction_path=direction_path,
		direction_trace_map=direction_trace_map,
	)
	return summary
