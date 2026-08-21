"""Behavioral tests for track_runner/off_frame_geometry.py

Pure-numpy fixtures covering the 8 invariants from WP-A2:
1. Right-edge exit detection and cx pinning
2. Left-edge exit detection (mirrored)
3. Stable size equals bracketing mean
4. Linearly interpolated cy within 1 px tolerance
5. Single-bracket open-ended span
6. Bottom-edge exit with vertical interpolation
7. 5% tie-favoring rule for horizontal over vertical
8. All values in uint16 range
"""

# local repo modules
import off_frame_geometry


#============================================


def test_right_edge_exit_pinning() -> None:
	"""Right-edge exit: last visible box near right edge -> cx == frame_width."""
	frame_w, frame_h = 1280, 720
	frame_size = (frame_w, frame_h)

	# Last visible box very close to right edge
	seeds = [
		{"frame_index": 0, "status": "visible", "torso_box": [640, 360, 100, 150]},
		{"frame_index": 50, "status": "not_in_frame"},
		{"frame_index": 51, "status": "not_in_frame"},
		{"frame_index": 100, "status": "visible", "torso_box": [640, 360, 100, 150]},
	]

	# Last visible box: cx=1240, cy=360, w=100, h=150
	# box_right = 1240 + 50 = 1290 (outside frame right edge)
	# This ensures right exit is chosen
	solved_trajectory = {
		0: {"cx": 1240, "cy": 360, "w": 100, "h": 150},
		100: {"cx": 640, "cy": 400, "w": 100, "h": 150},
	}

	nif_spans = off_frame_geometry.build_nif_spans(
		seeds, solved_trajectory, frame_size, race_start_frame=0
	)

	assert len(nif_spans) == 1
	span = nif_spans[0]
	assert span.exit_side == "right"

	# Fill and check: every frame in span should have cx == frame_w
	off_frame_states = off_frame_geometry.build_off_frame_states([span], frame_size)

	for frame_idx in range(span.start_frame, span.end_frame + 1):
		assert frame_idx in off_frame_states
		assert off_frame_states[frame_idx]["cx"] == frame_w


def test_left_edge_exit_pinning() -> None:
	"""Left-edge exit: last visible box near left edge -> cx == 0."""
	frame_w, frame_h = 1280, 720
	frame_size = (frame_w, frame_h)

	# Last visible box very close to left edge
	seeds = [
		{"frame_index": 0, "status": "visible", "torso_box": [50, 360, 100, 150]},
		{"frame_index": 50, "status": "not_in_frame"},
		{"frame_index": 51, "status": "not_in_frame"},
		{"frame_index": 100, "status": "visible", "torso_box": [640, 360, 100, 150]},
	]

	# Last visible box: cx=50, cy=360, w=100, h=150
	# box_left = 50 - 50 = 0 (at frame left edge)
	solved_trajectory = {
		0: {"cx": 50, "cy": 360, "w": 100, "h": 150},
		100: {"cx": 640, "cy": 400, "w": 100, "h": 150},
	}

	nif_spans = off_frame_geometry.build_nif_spans(
		seeds, solved_trajectory, frame_size, race_start_frame=0
	)

	assert len(nif_spans) == 1
	span = nif_spans[0]
	assert span.exit_side == "left"

	# Fill and check: every frame should have cx == 0
	off_frame_states = off_frame_geometry.build_off_frame_states([span], frame_size)

	for frame_idx in range(span.start_frame, span.end_frame + 1):
		assert off_frame_states[frame_idx]["cx"] == 0


def test_stable_size_is_bracketing_mean() -> None:
	"""Size across NIF span equals integer mean of bracketing solved boxes."""
	frame_w, frame_h = 1280, 720
	frame_size = (frame_w, frame_h)

	# Two different box sizes at brackets
	seeds = [
		{"frame_index": 0, "status": "visible", "torso_box": [640, 360, 100, 120]},
		{"frame_index": 50, "status": "not_in_frame"},
		{"frame_index": 100, "status": "visible", "torso_box": [640, 360, 200, 180]},
	]

	solved_trajectory = {
		0: {"cx": 640, "cy": 360, "w": 100, "h": 120},
		100: {"cx": 640, "cy": 360, "w": 200, "h": 180},
	}

	nif_spans = off_frame_geometry.build_nif_spans(
		seeds, solved_trajectory, frame_size, race_start_frame=0
	)

	assert len(nif_spans) == 1
	span = nif_spans[0]

	# Expected size: integer mean
	expected_w = int(round((100 + 200) / 2.0))  # 150
	expected_h = int(round((120 + 180) / 2.0))  # 150

	assert span.stable_w == expected_w
	assert span.stable_h == expected_h

	# Check filled frames carry the same size
	off_frame_states = off_frame_geometry.build_off_frame_states([span], frame_size)

	for frame_idx in range(span.start_frame, span.end_frame + 1):
		assert off_frame_states[frame_idx]["w"] == expected_w
		assert off_frame_states[frame_idx]["h"] == expected_h


def test_cy_linear_interpolation() -> None:
	"""cy linearly interpolated: midpoint equals integer mean within 1 px."""
	frame_w, frame_h = 1280, 720
	frame_size = (frame_w, frame_h)

	# One NIF seed in the middle; all between brackets
	# Before bracket at frame 0 (left edge exit), after bracket at frame 100
	# Last visible box near left edge so cy interpolation is the non-pinned axis
	seeds = [
		{"frame_index": 0, "status": "visible", "torso_box": [50, 100, 100, 150]},
		{"frame_index": 50, "status": "not_in_frame"},
		{"frame_index": 100, "status": "visible", "torso_box": [640, 400, 100, 150]},
	]

	solved_trajectory = {
		0: {"cx": 50, "cy": 100, "w": 100, "h": 150},
		100: {"cx": 640, "cy": 400, "w": 100, "h": 150},
	}

	nif_spans = off_frame_geometry.build_nif_spans(
		seeds, solved_trajectory, frame_size, race_start_frame=0
	)

	assert len(nif_spans) == 1
	span = nif_spans[0]

	# Check that span covers frames 1-99
	assert span.start_frame == 1
	assert span.end_frame == 99
	assert span.exit_side == "left"

	off_frame_states = off_frame_geometry.build_off_frame_states([span], frame_size)

	# Expected cy at midpoint: (100 + 400) / 2 = 250
	midpoint_frame = span.start_frame + (span.end_frame - span.start_frame) // 2
	actual_cy = off_frame_states[midpoint_frame]["cy"]

	assert abs(actual_cy - 250) <= 1


def test_single_bracket_open_ended_span() -> None:
	"""Single-bracket case (NIF runs to end): cy held flat; cx pinned to edge."""
	frame_w, frame_h = 1280, 720
	frame_size = (frame_w, frame_h)

	# Last visible seed at frame 0, then single NIF seed at frame 50
	# No visible seed after, so span runs to end (which we need to handle)
	seeds = [
		{"frame_index": 0, "status": "visible", "torso_box": [1200, 360, 100, 150]},
		{"frame_index": 50, "status": "not_in_frame"},
	]

	solved_trajectory = {
		0: {"cx": 1200, "cy": 360, "w": 100, "h": 150},
	}

	nif_spans = off_frame_geometry.build_nif_spans(
		seeds, solved_trajectory, frame_size, race_start_frame=0
	)

	assert len(nif_spans) == 1
	span = nif_spans[0]

	# No after_frame since there's no visible seed after the NIF run
	assert span.after_frame is None

	# Exit side should be "right" (box very close to right edge)
	assert span.exit_side == "right"

	off_frame_states = off_frame_geometry.build_off_frame_states([span], frame_size)

	# All frames in span should have cx pinned and cy held flat
	for frame_idx in range(span.start_frame, span.end_frame + 1):
		assert off_frame_states[frame_idx]["cx"] == frame_w
		assert off_frame_states[frame_idx]["cy"] == 360


#============================================
def test_open_ended_span_uses_known_trajectory_bound() -> None:
	"""An open NIF statement remains absent through the final known frame."""
	seeds = [
		{"frame_index": 0, "status": "visible"},
		{"frame_index": 50, "status": "not_in_frame"},
	]
	solved_trajectory = {
		0: {"cx": 1200, "cy": 360, "w": 100, "h": 150},
	}
	nif_spans = off_frame_geometry.build_nif_spans(
		seeds, solved_trajectory, (1280, 720), race_start_frame=0,
		last_frame_index=120,
	)

	assert nif_spans[0].start_frame == 1
	assert nif_spans[0].end_frame == 120


#============================================
def test_nif_truth_erasure_keeps_closed_span_endpoints() -> None:
	"""A closed NIF span erases only strict-between human brackets."""
	seeds = [
		{"frame_index": 45, "status": "visible"},
		{"frame_index": 50, "status": "not_in_frame"},
		{"frame_index": 55, "status": "partial"},
	]
	solved_trajectory = {
		45: {"cx": 1200, "cy": 360, "w": 100, "h": 150},
		55: {"cx": 640, "cy": 360, "w": 100, "h": 150},
	}
	trajectory = [{"frame": frame_index} for frame_index in range(100)]
	nif_spans = off_frame_geometry.build_nif_spans(
		seeds, solved_trajectory, (1280, 720), race_start_frame=0,
		last_frame_index=99,
	)
	erased = off_frame_geometry.erase_nif_span_truth(trajectory, nif_spans)

	assert erased == set(range(46, 55))
	assert trajectory[45] is not None and trajectory[55] is not None


#============================================
def test_nif_truth_erasure_spans_sparse_long_bracket() -> None:
	"""One sparse NIF seed erases the complete bracketed absence interval."""
	seeds = [
		{"frame_index": 10, "status": "visible"},
		{"frame_index": 50, "status": "not_in_frame"},
		{"frame_index": 100, "status": "partial"},
	]
	solved_trajectory = {
		10: {"cx": 1200, "cy": 360, "w": 100, "h": 150},
		100: {"cx": 640, "cy": 360, "w": 100, "h": 150},
	}
	trajectory = [{"frame": frame_index} for frame_index in range(120)]
	nif_spans = off_frame_geometry.build_nif_spans(
		seeds, solved_trajectory, (1280, 720), race_start_frame=0,
		last_frame_index=119,
	)
	erased = off_frame_geometry.erase_nif_span_truth(trajectory, nif_spans)

	assert erased == set(range(11, 100))
	assert trajectory[10] is not None and trajectory[100] is not None


#============================================
def test_nif_truth_erasure_covers_open_ended_span() -> None:
	"""An open NIF span erases only frames after its visible bracket."""
	seeds = [
		{"frame_index": 10, "status": "visible"},
		{"frame_index": 50, "status": "not_in_frame"},
	]
	solved_trajectory = {
		10: {"cx": 1200, "cy": 360, "w": 100, "h": 150},
	}
	trajectory = [{"frame": frame_index} for frame_index in range(120)]
	nif_spans = off_frame_geometry.build_nif_spans(
		seeds, solved_trajectory, (1280, 720), race_start_frame=0,
		last_frame_index=119,
	)
	erased = off_frame_geometry.erase_nif_span_truth(trajectory, nif_spans)

	assert erased == set(range(11, 120))
	assert trajectory[10] is not None


#============================================
def test_bottom_edge_exit() -> None:
	"""Bottom-edge exit: last visible center near bottom, cx mid-frame."""
	frame_w, frame_h = 1280, 720
	frame_size = (frame_w, frame_h)

	# Last visible box very close to bottom edge, cx in the middle
	seeds = [
		{"frame_index": 0, "status": "visible", "torso_box": [640, 680, 100, 80]},
		{"frame_index": 50, "status": "not_in_frame"},
		{"frame_index": 100, "status": "visible", "torso_box": [600, 350, 100, 150]},
	]

	# Last visible: cx=640, cy=680, w=100, h=80
	# box_bottom = 680 + 40 = 720 (at frame bottom edge)
	solved_trajectory = {
		0: {"cx": 640, "cy": 680, "w": 100, "h": 80},
		100: {"cx": 600, "cy": 350, "w": 100, "h": 150},
	}

	nif_spans = off_frame_geometry.build_nif_spans(
		seeds, solved_trajectory, frame_size, race_start_frame=0
	)

	assert len(nif_spans) == 1
	span = nif_spans[0]
	assert span.exit_side == "bottom"

	off_frame_states = off_frame_geometry.build_off_frame_states([span], frame_size)

	# Every frame should have cy == frame_h and cx interpolated
	for frame_idx in range(span.start_frame, span.end_frame + 1):
		assert off_frame_states[frame_idx]["cy"] == frame_h


def test_tie_favoring_left_right() -> None:
	"""5% tie-favoring rule: when horiz/vert distances within 5%, prefer left/right."""
	frame_w, frame_h = 1280, 720
	frame_size = (frame_w, frame_h)

	# Position box so that left and bottom distances are nearly equal,
	# within the 5% tie threshold.
	# If left_dist ~ bottom_dist within 5%, should favor "left" (or "right")
	#
	# Let's put the box at a corner: cx=50, cy=700
	# left = 50, bottom = 720 - 700 + h/2 = 720 - (700 - 40) = 60 (if h=80)
	# Difference: |50 - 60| = 10. If we scale by the max (60), threshold = 0.05*60 = 3.
	# So 10 > 3, not a tie. Let's use values that tie.
	#
	# Actually, let's be more deliberate. Say:
	# cx = 100, cy = 680, w = 100, h = 80
	# left = 100 - 50 = 50
	# bottom = 720 - (680 + 40) = 0
	# That's not a tie. Let me try:
	# cx = 50, cy = 680, w = 100, h = 80 (goes past left and bottom edges)
	# left = 50 - 50 = 0
	# bottom = 720 - (680 + 40) = 0
	# That's a perfect tie, but both are 0, so horiz_min = 0, vert_min = 0.
	# max(0, 0) = 0, threshold = 0. |0 - 0| <= 0, so within tie.
	# Prefer left/right: since left_dist == right_dist, we need left_dist < right_dist.
	# left_dist = 0, right_dist = 1280 - (50 + 50) = 1180. So left < right -> "left".
	#
	# Let me try a different example where the tie is clearer.
	# cx = 100, cy = 680, w = 100, h = 100
	# left = 100 - 50 = 50
	# right = 1280 - (100 + 50) = 1130
	# top = 680 - 50 = 630
	# bottom = 720 - (680 + 50) = -10 (negative means box extends past edge)
	# horiz_min = min(50, 1130) = 50
	# vert_min = min(630, -10) = -10 (clamped? or compared as is?)
	# In the infer_exit_side code, it uses min(left_dist, right_dist, top_dist, bottom_dist)
	# at the end, so negative values are allowed. So vert_min = -10.
	# max(50, -10) = 50, threshold = 0.05 * 50 = 2.5.
	# |50 - (-10)| = 60 > 2.5, not within tie.
	# overall_min = -10 (bottom), so exit_side = "bottom".
	#
	# Let me construct a true tie: distances equal and both positive.
	# cx = 300, cy = 300, w = 100, h = 100
	# left = 300 - 50 = 250
	# right = 1280 - 350 = 930
	# top = 300 - 50 = 250
	# bottom = 720 - 350 = 370
	# horiz_min = 250, vert_min = 250. Within 5%? threshold = 0.05 * 250 = 12.5.
	# |250 - 250| = 0 <= 12.5, yes, within tie. So prefer left/right.
	# left_dist < right_dist? 250 < 930, yes, so "left".

	seeds = [
		{"frame_index": 0, "status": "visible", "torso_box": [300, 300, 100, 100]},
		{"frame_index": 50, "status": "not_in_frame"},
		{"frame_index": 100, "status": "visible", "torso_box": [640, 360, 100, 150]},
	]

	solved_trajectory = {
		0: {"cx": 300, "cy": 300, "w": 100, "h": 100},
		100: {"cx": 640, "cy": 360, "w": 100, "h": 150},
	}

	nif_spans = off_frame_geometry.build_nif_spans(
		seeds, solved_trajectory, frame_size, race_start_frame=0
	)

	assert len(nif_spans) == 1
	span = nif_spans[0]

	# Within 5% tie, should favor left (since left_dist < right_dist)
	assert span.exit_side == "left"


def test_all_uint16_range() -> None:
	"""All filled values fit in uint16 range (0 <= v < 2**16)."""
	frame_w, frame_h = 65535, 65535  # extreme case
	frame_size = (frame_w, frame_h)

	seeds = [
		{"frame_index": 0, "status": "visible", "torso_box": [32767, 32767, 1000, 1000]},
		{"frame_index": 50, "status": "not_in_frame"},
		{"frame_index": 100, "status": "visible", "torso_box": [40000, 40000, 1000, 1000]},
	]

	solved_trajectory = {
		0: {"cx": 32767, "cy": 32767, "w": 1000, "h": 1000},
		100: {"cx": 40000, "cy": 40000, "w": 1000, "h": 1000},
	}

	nif_spans = off_frame_geometry.build_nif_spans(
		seeds, solved_trajectory, frame_size, race_start_frame=0
	)

	assert len(nif_spans) == 1
	off_frame_states = off_frame_geometry.build_off_frame_states(nif_spans, frame_size)

	# Check every filled frame
	for frame_idx, state in off_frame_states.items():
		assert 0 <= state["cx"] < 2**16
		assert 0 <= state["cy"] < 2**16
		assert 0 <= state["w"] < 2**16
		assert 0 <= state["h"] < 2**16


def test_pre_race_nif_raises_error() -> None:
	"""Pre-race NIF (seed before race_start_frame) raises ValueError."""
	frame_w, frame_h = 1280, 720
	frame_size = (frame_w, frame_h)

	# NIF seed at frame 10, race starts at frame 50
	seeds = [
		{"frame_index": 10, "status": "not_in_frame"},
		{"frame_index": 50, "status": "visible", "torso_box": [640, 360, 100, 150]},
	]

	solved_trajectory = {
		50: {"cx": 640, "cy": 360, "w": 100, "h": 150},
	}

	try:
		off_frame_geometry.build_nif_spans(
			seeds, solved_trajectory, frame_size, race_start_frame=50
		)
		assert False, "Expected ValueError"
	except ValueError as e:
		assert "not_in_frame before race_start_frame" in str(e)
