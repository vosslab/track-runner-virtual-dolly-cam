"""off_frame_geometry.py

Pure functions for building edge-anchored NIF (not-in-frame) geometry.

Converts user-authored not_in_frame seed annotations and a solved trajectory
into per-frame edge-anchored crop geometry that fills NIF spans. The output is
consumed at encode time to position the crop camera at the runner's exit edge
rather than holding the last in-frame position.

Per contract C1 and the design philosophy in docs/OFF_FRAME_GEOMETRY.md:
- NIF context is derived from seeds + solved trajectory at fill time, not stored.
- The storage layer (SCHEMA_VERSION, torso_box_coords.npz) is unchanged.
- Edge-anchored cx/cy values are crop intent inside NIF spans only.

Input shapes:
- seeds: list[dict] with keys {frame_index: int, status: str, torso_box, pass}
  - A NIF seed has status="not_in_frame" and torso_box=None.
  - Visible seeds have status in ("visible", "partial") and torso_box=[x,y,w,h].
- solved_trajectory: dict[int, dict] keyed by (global) frame index.
  - Values are {cx: int, cy: int, w: int, h: int} per solved frame.
- frame_size: tuple (frame_width, frame_height) in pixels.
- race_start_frame: int, global frame index where the race begins.
"""

# Standard Library
from dataclasses import dataclass

#============================================

@dataclass
class NifSpan:
	"""Semantic descriptor for one NIF span (not-in-frame interval).

	Fields:
		start_frame: First frame to fill (exclusive of before-bracket seed frame).
		end_frame: Last frame to fill (exclusive of after-bracket seed frame).
		exit_side: "left" | "right" | "top" | "bottom" (inferred from last visible box).
		before_frame: Frame index of last visible seed before this span (or None).
		after_frame: Frame index of first visible seed after this span (or None).
		stable_w: Integer box width across the span (mean of bracketing boxes).
		stable_h: Integer box height across the span (mean of bracketing boxes).
		anchor_start: Non-pinned coordinate (cy for left/right, cx for top/bottom) at start_frame.
		anchor_end: Non-pinned coordinate at end_frame.
	"""
	start_frame: int
	end_frame: int
	exit_side: str
	before_frame: int | None
	after_frame: int | None
	stable_w: int
	stable_h: int
	anchor_start: int
	anchor_end: int


#============================================

def infer_exit_side(
	last_visible_box: dict,
	frame_size: tuple,
) -> str:
	"""Infer runner's exit side from the last visible torso box.

	Uses box-edge distances (not center distance). The four edges are:
	  left = box_left
	  right = frame_width - box_right
	  top = box_top
	  bottom = frame_height - box_bottom

	The smallest distance wins. When horizontal and vertical candidates are
	within 5%, prefer left/right (track-runner footage convention).

	Args:
		last_visible_box: dict with keys {cx, cy, w, h} (center and dimensions).
		frame_size: tuple (frame_width, frame_height).

	Returns:
		"left" | "right" | "top" | "bottom".
	"""
	cx = last_visible_box["cx"]
	cy = last_visible_box["cy"]
	w = last_visible_box["w"]
	h = last_visible_box["h"]
	frame_w, frame_h = frame_size

	# box edges
	box_left = cx - w / 2.0
	box_right = cx + w / 2.0
	box_top = cy - h / 2.0
	box_bottom = cy + h / 2.0

	# distances to frame edges
	left_dist = box_left
	right_dist = frame_w - box_right
	top_dist = box_top
	bottom_dist = frame_h - box_bottom

	# find minimum horizontal and vertical
	horiz_min = min(left_dist, right_dist)
	vert_min = min(top_dist, bottom_dist)

	# tie-breaking: prefer left/right when within 5% of each other
	tie_threshold = 0.05 * max(horiz_min, vert_min) if max(horiz_min, vert_min) > 0 else 0
	if abs(horiz_min - vert_min) <= tie_threshold:
		# within tie tolerance, prefer horizontal
		if left_dist < right_dist:
			return "left"
		else:
			return "right"

	# otherwise, pick the overall minimum
	overall_min = min(left_dist, right_dist, top_dist, bottom_dist)
	if overall_min == left_dist:
		return "left"
	elif overall_min == right_dist:
		return "right"
	elif overall_min == top_dist:
		return "top"
	else:
		return "bottom"


#============================================

def build_nif_spans(
	seeds: list,
	solved_trajectory: dict,
	frame_size: tuple,
	race_start_frame: int,
	last_frame_index: int | None = None,
) -> list:
	"""Build a list of NifSpan objects from seeds and solved trajectory.

	A NIF span is the set of frames strictly between bracketing visible
	torso-box seeds. Multiple contiguous not_in_frame seeds collapse into
	one span.

	Pre-race NIF (any not_in_frame seed with frame_index < race_start_frame)
	raises ValueError with the message verbatim from the plan.

	Args:
		seeds: list[dict] with keys {frame_index, status, torso_box, pass}.
		solved_trajectory: dict[int, dict] keyed by frame index, values {cx, cy, w, h}.
		frame_size: tuple (frame_width, frame_height).
		race_start_frame: int, frame where the race starts.
		last_frame_index: Optional final source frame for an open-ended span.

	Returns:
		list[NifSpan], one per contiguous NIF interval.

	Raises:
		ValueError: If any not_in_frame seed lies in [0, race_start_frame).
	"""
	# Validate pre-race NIF
	for seed in seeds:
		if seed.get("status") == "not_in_frame":
			if seed["frame_index"] < race_start_frame:
				raise ValueError(
					"not_in_frame before race_start_frame is not supported. "
					"Truncate the video or move race_start_frame so the pre-race "
					"reference contains only visible runner frames."
				)

	# Filter visible seeds (status in ("visible", "partial"))
	visible_seeds = [
		s for s in seeds
		if s.get("status") in ("visible", "partial")
	]
	visible_frames = [s["frame_index"] for s in visible_seeds]

	# Find NIF seed runs (contiguous sequences of not_in_frame seeds)
	nif_seeds = [
		s for s in seeds
		if s.get("status") == "not_in_frame"
	]

	if not nif_seeds:
		return []

	nif_frames = sorted([s["frame_index"] for s in nif_seeds])

	# Collapse contiguous NIF frames into runs
	runs = []
	current_run_start = nif_frames[0]
	current_run_end = nif_frames[0]

	for frame_idx in nif_frames[1:]:
		if frame_idx == current_run_end + 1:
			# contiguous, extend
			current_run_end = frame_idx
		else:
			# gap, save run and start new
			runs.append((current_run_start, current_run_end))
			current_run_start = frame_idx
			current_run_end = frame_idx
	runs.append((current_run_start, current_run_end))

	# Build NifSpan for each run
	nif_spans = []
	for nif_start_frame, nif_end_frame in runs:
		# Find the last visible seed before this NIF run
		before_frame = None
		for vf in reversed(visible_frames):
			if vf < nif_start_frame:
				before_frame = vf
				break

		# Find the first visible seed after this NIF run
		after_frame = None
		for vf in visible_frames:
			if vf > nif_end_frame:
				after_frame = vf
				break

		# Validate bracket availability
		if before_frame is None:
			raise ValueError(
				"not_in_frame before race_start_frame is not supported. "
				"Truncate the video or move race_start_frame so the pre-race "
				"reference contains only visible runner frames."
			)

		# Get the solved boxes at bracketing frames
		before_solved = solved_trajectory.get(before_frame)
		after_solved = solved_trajectory.get(after_frame) if after_frame is not None else None

		# Compute stable size
		if after_solved is not None:
			stable_w = int(round((before_solved["w"] + after_solved["w"]) / 2.0))
			stable_h = int(round((before_solved["h"] + after_solved["h"]) / 2.0))
		else:
			stable_w = int(before_solved["w"])
			stable_h = int(before_solved["h"])

		# Determine exit side from last visible box
		exit_side = infer_exit_side(before_solved, frame_size)

		# Compute anchors (non-pinned coordinates at start and end)
		if exit_side in ("left", "right"):
			# cy is the non-pinned axis; interpolate between before and after
			before_cy = before_solved["cy"]
			after_cy = after_solved["cy"] if after_solved is not None else before_cy
			anchor_start = before_cy
			anchor_end = after_cy
		else:  # top or bottom
			# cx is the non-pinned axis
			before_cx = before_solved["cx"]
			after_cx = after_solved["cx"] if after_solved is not None else before_cx
			anchor_start = before_cx
			anchor_end = after_cx

		# NIF span is frames strictly between the bracketing seeds
		span_start = before_frame + 1
		if after_frame is not None:
			span_end = after_frame - 1
		else:
			# A caller with the trajectory extent can make an open-ended NIF
			# statement cover the remaining source frames.
			span_end = nif_end_frame if last_frame_index is None else last_frame_index

		span = NifSpan(
			start_frame=span_start,
			end_frame=span_end,
			exit_side=exit_side,
			before_frame=before_frame,
			after_frame=after_frame,
			stable_w=stable_w,
			stable_h=stable_h,
			anchor_start=anchor_start,
			anchor_end=anchor_end,
		)
		nif_spans.append(span)

	return nif_spans


#============================================

def erase_nif_span_truth(
	trajectory: list,
	nif_spans: list,
) -> set:
	"""Erase runner truth over the exact derived NIF span indices.

	Args:
		trajectory: Authoritative runner states, modified in place.
		nif_spans: NIF spans from build_nif_spans.

	Returns:
		Set of in-range frame indices erased from runner truth.
	"""
	nif_frames = set()
	for span in nif_spans:
		for frame_index in range(span.start_frame, span.end_frame + 1):
			if 0 <= frame_index < len(trajectory):
				trajectory[frame_index] = None
				nif_frames.add(frame_index)
	return nif_frames


#============================================

def build_off_frame_states(
	nif_spans: list,
	frame_size: tuple,
) -> dict:
	"""Build per-frame edge-anchored geometry for all NIF spans.

	Returns a dict mapping frame_index to a state dict with keys
	{cx, cy, w, h, conf, source}. All numeric values are integers
	safe for uint16 storage.

	Args:
		nif_spans: list[NifSpan], output from build_nif_spans.
		frame_size: tuple (frame_width, frame_height).

	Returns:
		dict[int, dict] with per-frame state for every frame in every span.
	"""
	frame_w, frame_h = frame_size
	off_frame_states = {}

	for span in nif_spans:
		num_frames = span.end_frame - span.start_frame + 1

		if num_frames == 0:
			continue

		# Interpolate the non-pinned coordinate across the span
		if num_frames == 1:
			# Single frame, use the starting anchor
			anchor_values = [span.anchor_start]
		else:
			# Linear interpolation from anchor_start to anchor_end
			anchor_values = []
			for i in range(num_frames):
				# position in [0, num_frames - 1]
				t = i / (num_frames - 1)
				value = span.anchor_start + t * (span.anchor_end - span.anchor_start)
				anchor_values.append(int(round(value)))

		# Generate state dict for each frame in the span
		for i, frame_idx in enumerate(range(span.start_frame, span.end_frame + 1)):
			anchor = anchor_values[i]

			# Pin the appropriate coordinate based on exit side
			if span.exit_side == "right":
				cx = frame_w
				cy = anchor
			elif span.exit_side == "left":
				cx = 0
				cy = anchor
			elif span.exit_side == "bottom":
				cy = frame_h
				cx = anchor
			else:  # top
				cy = 0
				cx = anchor

			# Clamp to uint16 range
			cx = int(max(0, min(cx, 65535)))
			cy = int(max(0, min(cy, 65535)))
			w = int(max(0, min(span.stable_w, 65535)))
			h = int(max(0, min(span.stable_h, 65535)))

			off_frame_states[frame_idx] = {
				"cx": cx,
				"cy": cy,
				"w": w,
				"h": h,
				"conf": 1.0,
				"source": "nif_edge_anchor",
			}

	return off_frame_states
