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
  C10: unified SCHEMA_VERSION (walk_debug_log.SCHEMA_VERSION).
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
import os
import sys

# Determine repo root from file location and add directories to path.
# walk_walker.py is at tools/blob_walk_v2/, so go up 3 levels to repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, 'track_runner')
if _TRACK_RUNNER_DIR not in sys.path:
	sys.path.insert(0, _TRACK_RUNNER_DIR)
if _REPO_ROOT not in sys.path:
	sys.path.insert(0, _REPO_ROOT)

# local repo modules
import residual_motion
import blob_trace
import scene_coords
import walk_motion_gate
import walk_debug_log

# ============================================================
# Rolling-window Viterbi constants.
# Tunable weight stubs; keep near the top for easy adjustment.
# ============================================================

# Number of frames in the rolling candidate buffer.
# Odd number so the center frame is well-defined; 9 at 60 fps is ~150 ms.
WALKER_WINDOW_FRAMES = 9

# Viterbi cost weights.
# WEIGHT_DISPLACEMENT: multiplies per-step displacement in torso-width units.
WEIGHT_DISPLACEMENT = 1.0
# WEIGHT_MAG_VAR: multiplies variance of per-step velocity magnitudes across window.
WEIGHT_MAG_VAR = 0.5
# WEIGHT_ANGLE_VAR: multiplies variance of per-step velocity angle in radians.
WEIGHT_ANGLE_VAR = 0.3
# WEIGHT_EVIDENCE: negative weight on integrated_mag (lower cost for stronger evidence).
WEIGHT_EVIDENCE = -0.05

# Skip-node cost per frame (choosing None when a real candidate exists).
SKIP_COST = 2.0

# Max number of consecutive extrapolated frames before demoting to soft_miss_no_path.
EXTRAP_MAX = 2

# Bootstrap mode: step <= BOOTSTRAP_N uses wide search radius.
# Set to 1 per the 2026-05-28 bootstrap redesign.
BOOTSTRAP_N = 1


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


#============================================
def _viterbi_select_path(
	window_candidates: list,
	torso_w: float,
	fps: float,
) -> list:
	"""Run Viterbi DP path selection over a window of candidate lists.

	Returns the lowest-cost path through the candidate lattice. Each entry
	in the returned list is either a blob dict (selected candidate) or None
	(skip / no candidate at that frame).

	Args:
		window_candidates: List of N lists. Each sub-list contains blob dicts
		    (with centroid_x, centroid_y, integrated_mag) for one frame.
		    An empty sub-list means no candidates at that frame.
		torso_w: Torso width in pixels (scale unit per C2).
		fps: Source video frame rate.

	Returns:
		List of N items: each is a blob dict or None.
	"""
	n = len(window_candidates)
	if n == 0:
		return []

	# Per-frame cap in pixels for one step (torso-unit based, per C2).
	# Above this displacement, the edge is pruned (infinite cost).
	max_jump_per_frame_w = walk_motion_gate.MAX_RUNNER_SPEED_W_PER_S / fps
	# Add BOOTSTRAP_UNCERTAINTY_W slack to avoid over-pruning near the seed.
	max_jump_px = (max_jump_per_frame_w + walk_motion_gate.BOOTSTRAP_UNCERTAINTY_W) * torso_w

	# Build per-frame state lists. Each frame has real candidates + one skip node (None).
	# states[t] = list of (blob_dict | None) for frame t.
	states = []
	for candidates in window_candidates:
		# Real candidates first, then one skip node.
		frame_states = list(candidates) + [None]
		states.append(frame_states)

	# dp_cost[t][i] = minimum total cost to reach state i at frame t.
	# dp_back[t][i] = index of predecessor state at frame t-1.
	dp_cost = []
	dp_back = []

	# Initialize frame 0: cost is skip cost for skip node, evidence bonus for real blobs.
	cost0 = []
	for blob in states[0]:
		if blob is None:
			cost0.append(SKIP_COST)
		else:
			integrated_mag = blob.get("integrated_mag", 0.0)
			cost0.append(WEIGHT_EVIDENCE * integrated_mag)
	dp_cost.append(cost0)
	dp_back.append([None] * len(states[0]))

	# Fill DP forward.
	for t in range(1, n):
		prev_states = states[t - 1]
		curr_states = states[t]
		cost_t = []
		back_t = []
		for j, blob_j in enumerate(curr_states):
			# Local cost of being at state j.
			if blob_j is None:
				local_cost = SKIP_COST
				cx_j = None
				cy_j = None
			else:
				integrated_mag = blob_j.get("integrated_mag", 0.0)
				local_cost = WEIGHT_EVIDENCE * integrated_mag
				cx_j = blob_j.get("centroid_x", 0.0)
				cy_j = blob_j.get("centroid_y", 0.0)

			# Find best predecessor at frame t-1.
			best_pred_cost = float("inf")
			best_pred_idx = 0
			for i, blob_i in enumerate(prev_states):
				if blob_i is None:
					cx_i = None
					cy_i = None
				else:
					cx_i = blob_i.get("centroid_x", 0.0)
					cy_i = blob_i.get("centroid_y", 0.0)

				trans_cost = _transition_cost(cx_i, cy_i, cx_j, cy_j, max_jump_px, torso_w)
				total = dp_cost[t - 1][i] + trans_cost
				if total < best_pred_cost:
					best_pred_cost = total
					best_pred_idx = i

			cost_t.append(best_pred_cost + local_cost)
			back_t.append(best_pred_idx)

		dp_cost.append(cost_t)
		dp_back.append(back_t)

	# Traceback: find best final state.
	best_final_idx = 0
	best_final_cost = float("inf")
	for i, c in enumerate(dp_cost[n - 1]):
		if c < best_final_cost:
			best_final_cost = c
			best_final_idx = i

	# Backtrack through dp_back.
	path_indices = [0] * n
	path_indices[n - 1] = best_final_idx
	for t in range(n - 1, 0, -1):
		path_indices[t - 1] = dp_back[t][path_indices[t]]

	# Build path: map state indices to blob dicts (or None).
	path = []
	for t in range(n):
		idx = path_indices[t]
		blob = states[t][idx]
		path.append(blob)

	return path


#============================================
def _transition_cost(
	cx_i: object,
	cy_i: object,
	cx_j: object,
	cy_j: object,
	max_jump_px: float,
	torso_w: float,
) -> float:
	"""Compute Viterbi transition cost between two consecutive candidate positions.

	If either position is None (skip node), returns SKIP_COST.
	If displacement exceeds max_jump_px, returns +infinity (edge pruned).

	Args:
		cx_i: x-pixel of predecessor (or None if skip node).
		cy_i: y-pixel of predecessor (or None if skip node).
		cx_j: x-pixel of successor (or None if skip node).
		cy_j: y-pixel of successor (or None if skip node).
		max_jump_px: Maximum allowed displacement in pixels.
		torso_w: Torso width in pixels (used for torso-unit scaling per C2).

	Returns:
		Transition cost (float). +inf if displacement cap exceeded.
	"""
	# Any transition involving a skip node has a fixed skip cost.
	if cx_i is None or cy_i is None or cx_j is None or cy_j is None:
		return SKIP_COST

	dx = cx_j - cx_i
	dy = cy_j - cy_i
	disp_px = math.sqrt(dx * dx + dy * dy)

	# Displacement cap: prune edge if above physical limit.
	if disp_px > max_jump_px:
		return float("inf")

	# Displacement cost in torso-width units (per C2).
	if torso_w > 0.0:
		disp_w = disp_px / torso_w
	else:
		disp_w = disp_px

	return WEIGHT_DISPLACEMENT * disp_w


#============================================
def _compute_path_cost(path: list, torso_w: float, fps: float) -> float:
	"""Compute total Viterbi cost for a selected path (for debug logging).

	Args:
		path: List of blob dicts or None, as returned by _viterbi_select_path.
		torso_w: Torso width in pixels.
		fps: Source video frame rate.

	Returns:
		Total cost (float). Lower is better.
	"""
	if not path:
		return 0.0

	max_jump_per_frame_w = walk_motion_gate.MAX_RUNNER_SPEED_W_PER_S / fps
	max_jump_px = (max_jump_per_frame_w + walk_motion_gate.BOOTSTRAP_UNCERTAINTY_W) * torso_w

	total = 0.0
	for i, blob in enumerate(path):
		if blob is not None:
			integrated_mag = blob.get("integrated_mag", 0.0)
			total += WEIGHT_EVIDENCE * integrated_mag
		else:
			total += SKIP_COST

		if i > 0:
			prev_blob = path[i - 1]
			cx_i = prev_blob.get("centroid_x", 0.0) if prev_blob is not None else None
			cy_i = prev_blob.get("centroid_y", 0.0) if prev_blob is not None else None
			cx_j = blob.get("centroid_x", 0.0) if blob is not None else None
			cy_j = blob.get("centroid_y", 0.0) if blob is not None else None
			total += _transition_cost(cx_i, cy_i, cx_j, cy_j, max_jump_px, torso_w)

	return total


#============================================
def _emit_status_from_path(
	window_frames: list,
	window_candidates: list,
	path: list,
	last_accepted_cx: object,
	last_accepted_cy: object,
) -> list:
	"""Determine status and interpolated/extrapolated positions from a selected path.

	Args:
		window_frames: List of frame indices in the window.
		window_candidates: List of candidate lists per frame.
		path: List of blob dicts or None from _viterbi_select_path.
		last_accepted_cx: x-pixel of the most recent accepted position before this window.
		last_accepted_cy: y-pixel of the most recent accepted position before this window.

	Returns:
		List of dicts, one per frame, each with keys:
		  frame_index, status, cx, cy, blob (the selected blob dict or None),
		  candidates_empty (bool).
	"""
	n = len(window_frames)
	results = []

	# Find accepted positions within the path for interpolation.
	# accepted_positions: list of (offset, cx, cy) for accepted frames.
	accepted_positions = []
	for t, blob in enumerate(path):
		if blob is not None:
			cx = blob.get("centroid_x", 0.0)
			cy = blob.get("centroid_y", 0.0)
			accepted_positions.append((t, cx, cy))

	# Consec extrap counter; reset at each accept or soft_miss_no_blob.
	consec_extrap = 0

	for t in range(n):
		frame_index = window_frames[t]
		candidates_empty = len(window_candidates[t]) == 0
		blob = path[t]

		if blob is not None:
			# Real candidate on the selected path.
			status = "accepted"
			cx = blob.get("centroid_x", 0.0)
			cy = blob.get("centroid_y", 0.0)
			consec_extrap = 0
		elif candidates_empty:
			# No candidates extracted at all.
			status = "soft_miss_no_blob"
			cx, cy = _interp_or_extrap_position(
				t, accepted_positions, last_accepted_cx, last_accepted_cy,
			)
			consec_extrap = 0
		else:
			# Candidates existed but Viterbi chose skip.
			# Determine interpolated vs extrapolated vs soft_miss_no_path.
			next_accept = None
			for ta, cxa, cya in accepted_positions:
				if ta > t:
					next_accept = (ta, cxa, cya)
					break

			prev_accept = None
			for ta, cxa, cya in reversed(accepted_positions):
				if ta < t:
					prev_accept = (ta, cxa, cya)
					break

			if prev_accept is not None and next_accept is not None:
				# Bracketed on both sides: interpolate.
				status = "interpolated"
				t0, cx0, cy0 = prev_accept
				t1, cx1, cy1 = next_accept
				frac = (t - t0) / (t1 - t0) if t1 != t0 else 0.0
				cx = cx0 + frac * (cx1 - cx0)
				cy = cy0 + frac * (cy1 - cy0)
				consec_extrap = 0
			elif prev_accept is not None:
				# Past last accept in window: extrapolate if within EXTRAP_MAX.
				consec_extrap += 1
				if consec_extrap <= EXTRAP_MAX:
					status = "extrapolated"
					# Simple hold: use last accepted position.
					_, cx, cy = prev_accept
				else:
					status = "soft_miss_no_path"
					_, cx, cy = prev_accept
			else:
				# No accepted position before this frame.
				status = "soft_miss_no_path"
				cx = last_accepted_cx if last_accepted_cx is not None else 0.0
				cy = last_accepted_cy if last_accepted_cy is not None else 0.0
				consec_extrap = 0

		results.append({
			"frame_index": frame_index,
			"status": status,
			"cx": cx,
			"cy": cy,
			"blob": blob,
			"candidates_empty": candidates_empty,
		})

	return results


#============================================
def _interp_or_extrap_position(
	t: int,
	accepted_positions: list,
	last_accepted_cx: object,
	last_accepted_cy: object,
) -> tuple:
	"""Compute position for a frame without a selected blob.

	Tries interpolation between bracketing accepts first; falls back to
	last accepted position.

	Args:
		t: Offset within the window.
		accepted_positions: List of (offset, cx, cy) for accepted frames in window.
		last_accepted_cx: cx from pre-window accepted state.
		last_accepted_cy: cy from pre-window accepted state.

	Returns:
		(cx, cy) tuple.
	"""
	prev_accept = None
	next_accept = None
	for ta, cxa, cya in accepted_positions:
		if ta < t:
			prev_accept = (ta, cxa, cya)
		elif ta > t and next_accept is None:
			next_accept = (ta, cxa, cya)

	if prev_accept is not None and next_accept is not None:
		t0, cx0, cy0 = prev_accept
		t1, cx1, cy1 = next_accept
		frac = (t - t0) / (t1 - t0) if t1 != t0 else 0.0
		return cx0 + frac * (cx1 - cx0), cy0 + frac * (cy1 - cy0)
	elif prev_accept is not None:
		return prev_accept[1], prev_accept[2]
	elif next_accept is not None:
		return next_accept[1], next_accept[2]
	else:
		cx = last_accepted_cx if last_accepted_cx is not None else 0.0
		cy = last_accepted_cy if last_accepted_cy is not None else 0.0
		return cx, cy


#============================================
def _count_candidates_in_window(window_candidates: list) -> int:
	"""Count frames with at least one candidate across the window.

	Args:
		window_candidates: List of lists (one per frame).

	Returns:
		Integer count of frames with at least one candidate.
	"""
	return sum(1 for c in window_candidates if len(c) > 0)


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

	Returns:
		(updated_last_accepted_cx, updated_last_accepted_cy)
	"""
	# Build arrays for Viterbi.
	window_frames = [entry["frame_index"] for entry in window_buffer]
	window_candidates = [entry["candidates"] for entry in window_buffer]

	# Run Viterbi path selection over the full window.
	path = _viterbi_select_path(window_candidates, seed_w, fps)
	path_cost_total = _compute_path_cost(path, seed_w, fps)
	candidates_in_window_count = _count_candidates_in_window(window_candidates)

	# Determine per-frame statuses.
	results = _emit_status_from_path(
		window_frames=window_frames,
		window_candidates=window_candidates,
		path=path,
		last_accepted_cx=last_accepted_cx,
		last_accepted_cy=last_accepted_cy,
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

		debug_row = walk_debug_log.DebugLogRow(
			frame_index=fi,
			step=None,
			direction="+" if sign > 0 else "-",
			status=r["status"],
			dt=None,
			torso_w_px=seed_w,
			torso_h_px=None,
			pred_cx=entry["pred_cx"],
			pred_cy=entry["pred_cy"],
			cand_cx=cand_cx_val,
			cand_cy=cand_cy_val,
			cand_scene_x=cand_scene_x_val,
			cand_scene_y=cand_scene_y_val,
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

	Returns:
		WalkSummary with counts, metrics, and accepted frame list.
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

	# Residual cache (scoped to this walk; not shared across intervals per C6).
	residual_cache = {}

	# Bootstrap state: start at the seed frame position.
	prev_cx = seed_cx
	prev_cy = seed_cy

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
	# Bootstrap (step == 0)
	# ============================================================
	step = 0
	frame_f = seed_frame

	acceptance_box = (
		seed_cx - 0.5 * seed_w,
		seed_cy - 0.75 * seed_h,
		seed_cx + 0.5 * seed_w,
		seed_cy + 0.75 * seed_h,
	)
	# seed coords are in PROCESSED-pixel space (Option A, 2026-05-29).
	# Clamp against reader.width/height (post-bin frame boundary).
	roi_pad = max(20, seed_w)
	roi_x1 = max(0, int(acceptance_box[0] - roi_pad))
	roi_y1 = max(0, int(acceptance_box[1] - roi_pad))
	roi_x2 = min(reader.width, int(acceptance_box[2] + roi_pad))
	roi_y2 = min(reader.height, int(acceptance_box[3] + roi_pad))
	roi_override = (roi_x1, roi_y1, roi_x2, roi_y2)
	# dog_diameter is in processed-pixel space; observe_blob_at receives it as-is
	dog_diameter_override = 0.7 * seed_w

	trace_sink_holder = type('TraceSink', (), {'observer_trace': None})()

	obs = residual_motion.observe_blob_at(
		frame_index=frame_f,
		pred_center=(prev_cx, prev_cy),
		pred_box=(seed_w, seed_h),
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

	# Bootstrap row is always emitted immediately.
	bootstrap_status = "accepted" if obs is not None else "soft_miss_no_blob"
	if obs is not None:
		accepts.append(frame_f)
		status_counts["accepted"] += 1
	else:
		status_counts["soft_miss_no_blob"] += 1

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

	# ============================================================
	# Per-step loop (step >= 1): fill buffer then run windowed selection.
	# ============================================================
	step = 1
	dt = stride
	# Loop-bound guard: protects against malformed intervals.
	max_steps_guard = abs(neighbor_seed_frame - seed_frame) + 1
	stop_reason = "loop_guard"

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

		# Construct acceptance box anchored to last-accepted position.
		acceptance_box = (
			pred_cx - 0.5 * seed_w,
			pred_cy - 0.75 * seed_h,
			pred_cx + 0.5 * seed_w,
			pred_cy + 0.75 * seed_h,
		)
		# seed coords are in PROCESSED-pixel space (Option A, 2026-05-29).
		# Clamp against reader.width/height (post-bin frame boundary).
		roi_pad = max(20, seed_w)
		roi_x1 = max(0, int(acceptance_box[0] - roi_pad))
		roi_y1 = max(0, int(acceptance_box[1] - roi_pad))
		roi_x2 = min(reader.width, int(acceptance_box[2] + roi_pad))
		roi_y2 = min(reader.height, int(acceptance_box[3] + roi_pad))
		roi_override = (roi_x1, roi_y1, roi_x2, roi_y2)
		# dog_diameter is in processed-pixel space; observe_blob_at receives it as-is
		dog_diameter_override = 0.7 * seed_w

		# Call observe_blob_at; read trace.corridor_blobs as candidate list.
		trace_sink_holder = type('TraceSink', (), {'observer_trace': None})()

		obs = residual_motion.observe_blob_at(
			frame_index=frame_f,
			pred_center=(pred_cx, pred_cy),
			pred_box=(seed_w, seed_h),
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

		trace = trace_sink_holder.observer_trace if obs is not None else None

		# Extract candidate list from trace.corridor_blobs (NOT obs.center_pixel).
		# corridor_blobs holds every blob passing the geometric ROI filter.
		if trace is not None:
			candidates = list(trace.corridor_blobs)
		else:
			candidates = []

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

		# Append to rolling buffer.
		window_buffer.append({
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
		})

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
		)

	# ============================================================
	# After termination: emit diagnostic rows.
	# ============================================================
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

	# ============================================================
	# Compute summary metrics.
	# ============================================================
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

	return WalkSummary(
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
	)
