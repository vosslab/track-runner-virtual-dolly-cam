"""Per-frame status and position logic for the windowed blob walker.

Pure functions consumed by walk_engine.py. Given a Viterbi-selected path
over a window of candidate lists, assign each frame one of five statuses and
compute its interpolated / extrapolated position when no candidate was
selected.

Five statuses:
  accepted, interpolated, extrapolated, soft_miss_no_blob, soft_miss_no_path.

These functions carry no cross-frame or cross-pass state; all input arrives
as arguments and all output is returned (per contract C9 FWD/BWD
independence and the chained-blob-state anti-pattern).

Position note: the walker solves runner POSITION per frame (cx, cy). The
per-frame torso SIZE (w, h) is not an independent size solve; it is
seed-derived geometry supplied by the caller through size_at_frame and
attached to every emitted frame for downstream box assembly.
"""

# Max number of consecutive extrapolated frames before demoting to soft_miss_no_path.
EXTRAP_MAX = 2


#============================================
def emit_status_from_path(
	window_frames: list,
	window_candidates: list,
	path: list,
	last_accepted_cx: object,
	last_accepted_cy: object,
	size_at_frame: object = None,
) -> list:
	"""Determine status and interpolated/extrapolated positions from a selected path.

	Args:
		window_frames: List of frame indices in the window.
		window_candidates: List of candidate lists per frame.
		path: List of blob dicts or None from walk_viterbi.select_path.
		last_accepted_cx: x-pixel of the most recent accepted position before this window.
		last_accepted_cy: y-pixel of the most recent accepted position before this window.
		size_at_frame: Optional callable frame_index -> (w, h) returning the
			seed-derived torso box size for that frame. When None, w and h are
		    left None on every result for callers that need positions only. The walker
		    solves position; size is seed-derived geometry, not a size solve.

	Returns:
		List of dicts, one per frame, each with keys:
		  frame_index, status, cx, cy, w, h, blob (the selected blob dict or
		  None), candidates_empty (bool). w and h are present (not None) on
		  every frame when size_at_frame is supplied.
	"""
	n = len(window_frames)
	results = []

	# Find accepted positions within the path for interpolation.
	# accepted_positions: list of (offset, cx, cy) for accepted frames.
	accepted_positions = []
	for t, blob in enumerate(path):
		if blob is not None:
			# A selected blob carries its centroid; read it directly so a
			# malformed candidate fails here instead of entering the path
			# at the frame origin.
			cx = blob["centroid_x"]
			cy = blob["centroid_y"]
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
			cx = blob["centroid_x"]
			cy = blob["centroid_y"]
			consec_extrap = 0
		elif candidates_empty:
			# No candidates extracted at all.
			status = "soft_miss_no_blob"
			cx, cy = interp_or_extrap_position(
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

		# Attach seed-derived torso size (w, h) for this frame. The walker
		# solves position; size is geometry interpolated from the bracketing
		# seed boxes by the caller-supplied size_at_frame (None for callers
		# that do not assemble a box path).
		if size_at_frame is not None:
			box_w, box_h = size_at_frame(frame_index)
		else:
			box_w = None
			box_h = None

		results.append({
			"frame_index": frame_index,
			"status": status,
			"cx": cx,
			"cy": cy,
			"w": box_w,
			"h": box_h,
			"blob": blob,
			"candidates_empty": candidates_empty,
		})

	return results


#============================================
def interp_or_extrap_position(
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
def count_candidates_in_window(window_candidates: list) -> int:
	"""Count frames with at least one candidate across the window.

	Args:
		window_candidates: List of lists (one per frame).

	Returns:
		Integer count of frames with at least one candidate.
	"""
	return sum(1 for c in window_candidates if len(c) > 0)
