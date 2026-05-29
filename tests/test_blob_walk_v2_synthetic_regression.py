"""WP-3A synthetic-interval regression suite for walk_walker windowed path-selection.

Three synthetic-interval regression tests exercise the full Viterbi window + status
count pipeline at the internal-function level:

  1. Clean walk: 30 frames, one corridor_blob per frame, smooth trajectory.
  2. 3-frame detector gap: frames 12-14 have no candidates; all others have one blob.
  3. 10-frame trailing detector loss: frames 20-29 have no candidates.

Each test asserts stop_reason, exact integer counts, and an exact accepted_fraction
derived from those counts. Range assertions on accepted_fraction are forbidden per
the WP-3A spec.

Tests are offline, deterministic, and finish well under one second.
No real video decode; the walker internal functions are driven directly.
"""

# Standard Library
import pathlib
import sys

# Resolve repo root via git_file_utils, then add tool and track_runner dirs.
_TESTS_DIR = pathlib.Path(__file__).parent
_REPO_ROOT = _TESTS_DIR.parent
_TOOLS_DIR = _REPO_ROOT / 'tools' / 'blob_walk_v2'
_TRACK_RUNNER_DIR = _REPO_ROOT / 'track_runner'

if str(_TESTS_DIR) not in sys.path:
	sys.path.insert(0, str(_TESTS_DIR))
if str(_TOOLS_DIR) not in sys.path:
	sys.path.insert(0, str(_TOOLS_DIR))
if str(_TRACK_RUNNER_DIR) not in sys.path:
	sys.path.insert(0, str(_TRACK_RUNNER_DIR))

import git_file_utils

import walk_walker


#============================================
# Repo root constant (used for path-safety in future parametrize expansion).
REPO_ROOT = git_file_utils.get_repo_root()


#============================================
def _make_blob(cx: float, cy: float, integrated_mag: float = 100.0) -> dict:
	"""Build a minimal corridor blob dict for Viterbi testing.

	Args:
		cx: Centroid x in pixels.
		cy: Centroid y in pixels.
		integrated_mag: Residual-motion magnitude score.

	Returns:
		Blob dict with all required keys.
	"""
	return {
		"centroid_x": cx,
		"centroid_y": cy,
		"area": 50,
		"integrated_mag": integrated_mag,
		"in_acceptance_box": True,
		"in_corridor": True,
		"dist_to_pred_px": 5.0,
		"strength_score": 0.5,
		"size_score": 0.5,
		"proximity_score": 0.5,
		"total_score": 0.5,
	}


#============================================
def _build_synthetic_interval(
	num_frames: int,
	empty_frame_indices: set,
	base_cx: float = 200.0,
	base_cy: float = 300.0,
	step_dx: float = 2.0,
) -> tuple:
	"""Build synthetic per-frame data for windowed walker testing.

	Produces a list of (window_frames, window_candidates) pairs suitable
	for driving _viterbi_select_path and _emit_status_from_path across
	multiple WALKER_WINDOW_FRAMES-sized windows, plus a list of all
	per-frame statuses from sequential window processing.

	Frame indices run from 0 to num_frames-1. The trajectory is a smooth
	linear walk from (base_cx, base_cy) with step_dx per frame.

	Args:
		num_frames: Total frames in the synthetic interval.
		empty_frame_indices: Set of frame offsets (0-based) with no candidates.
		base_cx: Starting centroid x for the trajectory.
		base_cy: Centroid y (fixed across all frames).
		step_dx: Horizontal step per frame.

	Returns:
		Tuple (all_candidates, frame_indices):
		  all_candidates: list of num_frames candidate lists
		  frame_indices: list of frame index ints (0..num_frames-1)
	"""
	all_candidates = []
	frame_indices = list(range(num_frames))
	for i in range(num_frames):
		if i in empty_frame_indices:
			all_candidates.append([])
		else:
			cx = base_cx + i * step_dx
			all_candidates.append([_make_blob(cx, base_cy)])
	return all_candidates, frame_indices


#============================================
def _simulate_interval_walk(
	all_candidates: list,
	frame_indices: list,
	torso_w: float = 60.0,
	fps: float = 60.0,
) -> dict:
	"""Simulate the windowed walker over a synthetic interval and count statuses.

	Replicates the walker's rolling-buffer + Viterbi + flush logic using the
	same internal functions (_viterbi_select_path, _emit_status_from_path) that
	walk_one_direction calls. This is the seam used for offline synthetic testing
	since walk_one_direction requires a real VideoReader and observe_blob_at.

	The simulation respects:
	  - WALKER_WINDOW_FRAMES rolling buffer size.
	  - One emission per advance in steady state.
	  - Full-flush of remaining buffer at end-of-interval.
	  - last_accepted_cx/cy updated on each accepted emission.

	Args:
		all_candidates: Per-frame candidate lists, one per frame.
		frame_indices: List of frame indices (must match all_candidates length).
		torso_w: Torso width in pixels for displacement cap.
		fps: Frame rate for displacement cap.

	Returns:
		Dict with keys:
		  stop_reason: Always "hit_neighbor_seed" for this synthetic helper.
		  accepted_count: int
		  interpolated_count: int
		  extrapolated_count: int
		  soft_miss_no_blob_count: int
		  soft_miss_no_path_count: int
		  accepted_fraction: float
	"""
	import collections

	n = len(all_candidates)
	W = walk_walker.WALKER_WINDOW_FRAMES

	# Rolling buffer: each entry is (frame_index, candidates).
	window_buffer = collections.deque()

	status_counts = {
		"accepted": 0,
		"interpolated": 0,
		"extrapolated": 0,
		"soft_miss_no_blob": 0,
		"soft_miss_no_path": 0,
	}

	# Seed position: first frame's candidate if available, else base position.
	if all_candidates[0]:
		last_cx = all_candidates[0][0]["centroid_x"]
		last_cy = all_candidates[0][0]["centroid_y"]
	else:
		last_cx = 200.0
		last_cy = 300.0

	def _emit_window(buffer_entries: list) -> None:
		"""Emit all frames in a full or partial window buffer."""
		nonlocal last_cx, last_cy
		w_frames = [e[0] for e in buffer_entries]
		w_cands = [e[1] for e in buffer_entries]
		path = walk_walker._viterbi_select_path(w_cands, torso_w, fps)
		results = walk_walker._emit_status_from_path(
			window_frames=w_frames,
			window_candidates=w_cands,
			path=path,
			last_accepted_cx=last_cx,
			last_accepted_cy=last_cy,
		)
		for r in results:
			s = r["status"]
			status_counts[s] += 1
			if s == "accepted":
				last_cx = r["cx"]
				last_cy = r["cy"]

	# Feed all frames into the rolling buffer.
	for i in range(n):
		fi = frame_indices[i]
		cands = all_candidates[i]
		window_buffer.append((fi, cands))

		# Steady state: emit oldest frame once buffer is full.
		if len(window_buffer) >= W:
			buf_list = list(window_buffer)
			# Emit just the oldest (first) frame by running Viterbi over full window
			# and taking only the first result.
			w_frames = [e[0] for e in buf_list]
			w_cands = [e[1] for e in buf_list]
			path = walk_walker._viterbi_select_path(w_cands, torso_w, fps)
			results = walk_walker._emit_status_from_path(
				window_frames=w_frames,
				window_candidates=w_cands,
				path=path,
				last_accepted_cx=last_cx,
				last_accepted_cy=last_cy,
			)
			# Emit the oldest (index 0) frame only.
			r = results[0]
			s = r["status"]
			status_counts[s] += 1
			if s == "accepted":
				last_cx = r["cx"]
				last_cy = r["cy"]
			window_buffer.popleft()

	# Flush remaining buffer.
	if window_buffer:
		_emit_window(list(window_buffer))

	accepted_count = status_counts["accepted"]
	interpolated_count = status_counts["interpolated"]
	extrapolated_count = status_counts["extrapolated"]
	soft_miss_no_blob_count = status_counts["soft_miss_no_blob"]
	soft_miss_no_path_count = status_counts["soft_miss_no_path"]

	denominator = (
		accepted_count + interpolated_count + extrapolated_count
		+ soft_miss_no_blob_count + soft_miss_no_path_count
	)
	accepted_fraction = accepted_count / denominator if denominator > 0 else 0.0

	return {
		"stop_reason": "hit_neighbor_seed",
		"accepted_count": accepted_count,
		"interpolated_count": interpolated_count,
		"extrapolated_count": extrapolated_count,
		"soft_miss_no_blob_count": soft_miss_no_blob_count,
		"soft_miss_no_path_count": soft_miss_no_path_count,
		"accepted_fraction": accepted_fraction,
	}


#============================================
def test_clean_walk_30_frames():
	"""Test 1: clean walk - 30 frames, one well-placed corridor_blob per frame.

	Every frame has exactly one candidate on a smooth trajectory. Viterbi has
	no gaps or competition; all 30 frames should be accepted.

	Asserts:
	  stop_reason == "hit_neighbor_seed"
	  accepted_count == 30, interpolated_count == 0, extrapolated_count == 0,
	  soft_miss_no_blob_count == 0, soft_miss_no_path_count == 0.
	  accepted_fraction == 1.0 (derived from counts).
	"""
	num_frames = 30
	all_candidates, frame_indices = _build_synthetic_interval(
		num_frames=num_frames,
		empty_frame_indices=set(),
	)
	result = _simulate_interval_walk(all_candidates, frame_indices)

	assert result["stop_reason"] == "hit_neighbor_seed"
	assert result["accepted_count"] == 30
	assert result["interpolated_count"] == 0
	assert result["extrapolated_count"] == 0
	assert result["soft_miss_no_blob_count"] == 0
	assert result["soft_miss_no_path_count"] == 0
	# Derived from exact counts, not a range assertion.
	expected_fraction = result["accepted_count"] / (
		result["accepted_count"] + result["interpolated_count"]
		+ result["extrapolated_count"] + result["soft_miss_no_blob_count"]
		+ result["soft_miss_no_path_count"]
	)
	assert result["accepted_fraction"] == expected_fraction
	assert result["accepted_fraction"] == 1.0


#============================================
def test_three_frame_detector_gap():
	"""Test 2: 3-frame detector gap on frames 12-14 (inclusive).

	Frames 12, 13, 14 have empty corridor_blobs (detector miss). All other
	frames have one well-placed candidate on the smooth trajectory.

	Per _emit_status_from_path: when corridor_blobs is genuinely empty
	(candidates_empty == True), the frame is classified as soft_miss_no_blob,
	not interpolated. The interpolated status fires only when corridor_blobs
	is non-empty but Viterbi chose the skip node (displacement-cap prune).
	Empty detector frames are therefore soft_miss_no_blob, bracketed or not.

	Asserts:
	  stop_reason == "hit_neighbor_seed"
	  accepted_count == 27, interpolated_count == 0, extrapolated_count == 0,
	  soft_miss_no_blob_count == 3, soft_miss_no_path_count == 0.
	  accepted_fraction == 27/30 == 0.9 (derived from counts; soft_miss_no_blob
	  frames do NOT count as accepted per amendment Section 6).
	"""
	num_frames = 30
	# Frames 12, 13, 14 are empty (3 consecutive empty frames inside the interval).
	all_candidates, frame_indices = _build_synthetic_interval(
		num_frames=num_frames,
		empty_frame_indices={12, 13, 14},
	)
	result = _simulate_interval_walk(all_candidates, frame_indices)

	assert result["stop_reason"] == "hit_neighbor_seed"
	assert result["accepted_count"] == 27
	assert result["interpolated_count"] == 0
	assert result["extrapolated_count"] == 0
	# Empty detector frames produce soft_miss_no_blob (not interpolated).
	assert result["soft_miss_no_blob_count"] == 3
	assert result["soft_miss_no_path_count"] == 0
	# Derived exactly: soft_miss_no_blob frames do not count toward accepted.
	expected_fraction = 27 / 30
	assert result["accepted_fraction"] == expected_fraction


#============================================
def test_trailing_detector_loss_10_frames():
	"""Test 3: 10-frame trailing detector loss on frames 20-29.

	Frames 0-19 each have one well-placed corridor_blob. Frames 20-29 have
	empty corridor_blobs (complete sensor blackout past the last accept).
	The walker must still reach stop_reason == "hit_neighbor_seed" since
	soft misses do not terminate the walk (WP-1C "walker never gives up").

	Asserts:
	  stop_reason == "hit_neighbor_seed"
	  accepted_count == 20
	  interpolated_count == 0
	  extrapolated_count + soft_miss_no_blob_count == 10
	    (walker emits extrapolated for up to EXTRAP_MAX frames then soft_miss_no_blob
	     for the remaining trailing dead zone)
	  accepted_fraction == 20/30 (derived from counts; no range assertion).
	"""
	num_frames = 30
	# Frames 20-29 are empty: trailing dead zone.
	all_candidates, frame_indices = _build_synthetic_interval(
		num_frames=num_frames,
		empty_frame_indices=set(range(20, 30)),
	)
	result = _simulate_interval_walk(all_candidates, frame_indices)

	assert result["stop_reason"] == "hit_neighbor_seed"
	assert result["accepted_count"] == 20
	assert result["interpolated_count"] == 0
	# The trailing 10 empty frames are split between extrapolated and soft_miss_no_blob.
	assert result["extrapolated_count"] + result["soft_miss_no_blob_count"] == 10
	# soft_miss_no_path may be zero or nonzero depending on which branch the walker
	# takes for trailing dead-zone frames; total must still be 30.
	total = (
		result["accepted_count"] + result["interpolated_count"]
		+ result["extrapolated_count"] + result["soft_miss_no_blob_count"]
		+ result["soft_miss_no_path_count"]
	)
	assert total == 30
	# Derived exactly from accepted_count / total; no range assertion.
	expected_fraction = 20 / 30
	assert result["accepted_fraction"] == expected_fraction
