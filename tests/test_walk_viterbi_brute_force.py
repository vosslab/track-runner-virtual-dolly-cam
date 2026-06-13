"""Exhaustive brute-force verification of walk_viterbi.select_path optimality.

The DP selects the minimum-cost path through a per-frame blob-candidate lattice.
This module verifies that property by:
  1. Enumerating EVERY possible path through a small lattice.
  2. Scoring each path with compute_path_cost(path, torso_w, fps,
     window_candidates=window) -- the shared term evaluator documented as
     reproducing the exact DP cost when window_candidates is supplied.
  3. Asserting that select_path returns a path whose cost equals the
     enumerated minimum.

The property tested: cost equality between the DP-selected path and the
brute-force minimum (not path identity -- ties may differ legitimately).

Lattice shapes covered (fixed-seed random.Random instances, never unseeded):
  (a) Dense lattices -- 2-3 candidates per frame, 4-6 frames.
  (b) Lattices with 1-2 empty frames (skip nodes forced).
  (c) Lattices with candidates beyond ABSOLUTE_MAX_JUMP_W (+inf edges occur).
  (d) Near-stationary lattices with per-frame speed below SPEED_EPSILON_W.
  (e) Zero/negative integrated_mag frames (neutral evidence).

A determinism test confirms same lattice -> identical selected path twice.

All tests: offline, deterministic, no I/O, sub-second. Tabs. No shebang.
"""

# Standard Library
import math
import pathlib
import random
import sys

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_TOOLS_DIR = _REPO_ROOT / 'tools' / 'blob_walk_v2'
if str(_TOOLS_DIR) not in sys.path:
	sys.path.insert(0, str(_TOOLS_DIR))
import walk_paths
walk_paths.setup()

import blob_walk.walk_viterbi as walk_viterbi
import blob_walk.walk_motion_gate as walk_motion_gate


#============================================
# Constants used across tests.
#============================================

# Canonical torso width and fps for all tests.
TORSO_W = 50.0
FPS = 60.0

# Per-frame speed limit (torso-widths/frame) used in lattice construction.
# Below this limit = physically plausible; above ABSOLUTE_MAX_JUMP_W = hard prune.
_SPEED_LIMIT_W_PER_FRAME = walk_motion_gate.MAX_RUNNER_SPEED_W_PER_S / FPS
_HARD_PRUNE_W = walk_motion_gate.ABSOLUTE_MAX_JUMP_W


#============================================
# Helpers.
#============================================

def _make_blob(cx: float, cy: float, integrated_mag: float = 100.0) -> dict:
	"""Build a minimal corridor blob dict for brute-force testing.

	Only the three keys required by the cost model are populated so that any
	accidental access to extra keys raises a KeyError rather than succeeding
	silently (do-not-hide-bugs rule).

	Args:
		cx: centroid x in pixels.
		cy: centroid y in pixels.
		integrated_mag: residual-motion magnitude (can be zero or negative).

	Returns:
		Minimal blob dict with centroid_x, centroid_y, integrated_mag.
	"""
	return {
		"centroid_x": float(cx),
		"centroid_y": float(cy),
		"integrated_mag": float(integrated_mag),
	}


def _enumerate_all_paths(window_candidates: list) -> list:
	"""Enumerate every possible path through the candidate lattice.

	Each frame contributes one of its real candidates or None (skip). The
	Cartesian product of (candidates[t] + [None]) over all frames t gives the
	full path space.

	Args:
		window_candidates: list of N per-frame candidate lists.

	Returns:
		List of paths; each path is a list of N items (blob dict or None).
	"""
	# Build per-frame choice lists: candidates + None (skip).
	choices_per_frame = []
	for candidates in window_candidates:
		# Each frame: the actual blob objects from the candidate list, plus None.
		choices_per_frame.append(list(candidates) + [None])

	# Recursive Cartesian product over all frames.
	paths = [[]]
	for frame_choices in choices_per_frame:
		new_paths = []
		for partial_path in paths:
			for choice in frame_choices:
				new_paths.append(partial_path + [choice])
		paths = new_paths
	return paths


def _brute_force_min_cost(window_candidates: list, torso_w: float, fps: float) -> float:
	"""Return the minimum path cost over all enumerated paths.

	Uses compute_path_cost(path, torso_w, fps, window_candidates=window) so the
	same shared term evaluator the DP uses is the cost oracle. Paths that
	exercise +inf edges are expected: compute_path_cost will include math.inf
	when a hard-pruned edge is traversed (infinity is a valid float cost).

	Args:
		window_candidates: list of N per-frame candidate lists.
		torso_w: torso width in pixels.
		fps: source video frame rate.

	Returns:
		Minimum cost over all enumerated paths.
	"""
	all_paths = _enumerate_all_paths(window_candidates)
	min_cost = float("inf")
	for path in all_paths:
		cost = walk_viterbi.compute_path_cost(path, torso_w, fps, window_candidates=window_candidates)
		if cost < min_cost:
			min_cost = cost
	return min_cost


def _dp_cost(window_candidates: list, torso_w: float, fps: float) -> float:
	"""Run select_path and score the result with the shared term evaluator.

	Args:
		window_candidates: list of N per-frame candidate lists.
		torso_w: torso width in pixels.
		fps: source video frame rate.

	Returns:
		Cost of the DP-selected path, using compute_path_cost with
		window_candidates so evidence normalization is included.
	"""
	path = walk_viterbi.select_path(window_candidates, torso_w, fps)
	return walk_viterbi.compute_path_cost(path, torso_w, fps, window_candidates=window_candidates)


#============================================
# Lattice builders for the five shapes.
#============================================

def _build_dense_lattice(rng: random.Random, n_frames: int, n_cands: int) -> list:
	"""Dense lattice: n_cands real candidates per frame, all within the speed envelope.

	Candidate positions are sampled as small steps from a base position to
	keep all inter-frame speeds well below the hard prune.

	Args:
		rng: seeded random.Random instance.
		n_frames: number of frames.
		n_cands: number of candidates per frame.

	Returns:
		List of n_frames per-frame candidate lists.
	"""
	base_cx = 200.0
	base_cy = 200.0
	# Max displacement per frame in pixels: stay well inside the hard prune.
	# Hard prune at ABSOLUTE_MAX_JUMP_W * torso_w = 1.5 * 50 = 75 px.
	# Use at most 0.3 torso-widths/frame = 15px so no edge is pruned.
	max_step_px = 0.3 * TORSO_W
	window = []
	for _ in range(n_frames):
		candidates = []
		for _ in range(n_cands):
			cx = base_cx + rng.uniform(-max_step_px, max_step_px)
			cy = base_cy + rng.uniform(-max_step_px, max_step_px)
			mag = rng.uniform(10.0, 300.0)
			candidates.append(_make_blob(cx, cy, mag))
		window.append(candidates)
	return window


def _build_sparse_lattice(rng: random.Random, n_frames: int, n_empty: int) -> list:
	"""Lattice with n_empty empty frames inserted at random positions.

	Non-empty frames have 1-2 candidates placed near a random walk trajectory.

	Args:
		rng: seeded random.Random instance.
		n_frames: total frames.
		n_empty: number of frames to leave empty (0 candidates).

	Returns:
		List of n_frames per-frame candidate lists.
	"""
	# Choose which frames are empty.
	empty_frames = set(rng.sample(range(n_frames), min(n_empty, n_frames)))
	base_cx = 200.0
	base_cy = 200.0
	max_step_px = 0.3 * TORSO_W
	window = []
	for fi in range(n_frames):
		if fi in empty_frames:
			window.append([])
		else:
			n_cands = rng.randint(1, 2)
			candidates = []
			for _ in range(n_cands):
				cx = base_cx + rng.uniform(-max_step_px, max_step_px)
				cy = base_cy + rng.uniform(-max_step_px, max_step_px)
				mag = rng.uniform(10.0, 200.0)
				candidates.append(_make_blob(cx, cy, mag))
			window.append(candidates)
	return window


def _build_far_candidate_lattice(rng: random.Random) -> list:
	"""Lattice with some candidates placed beyond ABSOLUTE_MAX_JUMP_W.

	The near candidate at each frame is within the envelope; the far candidate
	is explicitly placed at 2.0 torso-widths away from the base, above
	ABSOLUTE_MAX_JUMP_W = 1.5 torso-widths/frame, so those edges get +inf.

	Args:
		rng: seeded random.Random instance.

	Returns:
		List of 4 per-frame candidate lists, each with near + far blob.
	"""
	base_cx = 200.0
	base_cy = 200.0
	# Near blobs: small jitter.
	near_offset = 0.2 * TORSO_W
	# Far blobs: 2.0 torso-widths away -- above the 1.5 hard prune.
	far_offset = 2.0 * TORSO_W
	window = []
	for _ in range(4):
		near = _make_blob(
			base_cx + rng.uniform(-near_offset, near_offset),
			base_cy + rng.uniform(-near_offset, near_offset),
			rng.uniform(50.0, 200.0),
		)
		far = _make_blob(
			base_cx + far_offset,
			base_cy + rng.uniform(-near_offset, near_offset),
			rng.uniform(50.0, 200.0),
		)
		window.append([near, far])
	return window


def _build_stationary_lattice(rng: random.Random, n_frames: int) -> list:
	"""Near-stationary lattice: per-frame speed well below SPEED_EPSILON_W.

	Candidate positions are placed at < 0.001 torso-widths/frame displacement
	so the heading-delta term is always zeroed by the SPEED_EPSILON_W floor.

	Args:
		rng: seeded random.Random instance.
		n_frames: number of frames.

	Returns:
		List of n_frames per-frame candidate lists with 2 candidates each.
	"""
	base_cx = 200.0
	base_cy = 200.0
	# Sub-epsilon displacement: < SPEED_EPSILON_W * torso_w = 0.02 * 50 = 1px.
	tiny_step_px = 0.5
	window = []
	for _ in range(n_frames):
		candidates = [
			_make_blob(
				base_cx + rng.uniform(-tiny_step_px, tiny_step_px),
				base_cy + rng.uniform(-tiny_step_px, tiny_step_px),
				rng.uniform(5.0, 50.0),
			),
			_make_blob(
				base_cx + rng.uniform(-tiny_step_px, tiny_step_px),
				base_cy + rng.uniform(-tiny_step_px, tiny_step_px),
				rng.uniform(5.0, 50.0),
			),
		]
		window.append(candidates)
	return window


def _build_zero_mag_lattice(rng: random.Random, n_frames: int) -> list:
	"""Lattice with zero or negative integrated_mag -- neutral evidence test.

	Some frames have zero-magnitude candidates, others have negative (clamped
	to zero by the evidence function), and others have positive. The brute-
	force minimum and the DP must agree regardless.

	Args:
		rng: seeded random.Random instance.
		n_frames: number of frames.

	Returns:
		List of n_frames per-frame candidate lists.
	"""
	base_cx = 200.0
	base_cy = 200.0
	max_step_px = 0.3 * TORSO_W
	window = []
	for fi in range(n_frames):
		# Alternate between zero/negative and positive mag.
		mag = 0.0 if fi % 2 == 0 else -10.0
		candidates = [
			_make_blob(
				base_cx + rng.uniform(-max_step_px, max_step_px),
				base_cy + rng.uniform(-max_step_px, max_step_px),
				mag,
			),
			_make_blob(
				base_cx + rng.uniform(-max_step_px, max_step_px),
				base_cy + rng.uniform(-max_step_px, max_step_px),
				rng.uniform(-5.0, 0.0),
			),
		]
		window.append(candidates)
	return window


#============================================
# Test parameters: (lattice_kind, seed) pairs.
# 5-10 seeds per shape as instructed.
#============================================

# Dense lattice seeds: 4-6 frames, 2-3 candidates. 4^6 = 4096 paths max.
_DENSE_PARAMS = [
	(4, 2, s) for s in (1, 2, 3, 4, 5)
] + [
	(5, 2, s) for s in (10, 20, 30)
] + [
	(6, 3, s) for s in (100, 101)
]

# Sparse lattice seeds: 5-6 frames, 1-2 empty frames.
_SPARSE_PARAMS = [(6, 1, s) for s in (200, 201, 202, 203, 204)] + [(5, 2, s) for s in (300, 301, 302)]

# Far-candidate lattice seeds (fixed shape, vary seed).
_FAR_PARAMS = list(range(400, 408))

# Stationary lattice seeds.
_STATIONARY_PARAMS = [(5, s) for s in (500, 501, 502, 503, 504, 505)]

# Zero/negative mag lattice seeds.
_ZERO_MAG_PARAMS = [(5, s) for s in (600, 601, 602, 603, 604, 605)]


#============================================
# Core property check function (used by all parametrized tests).
#============================================

def _assert_dp_equals_brute_force_min(
	window_candidates: list,
	torso_w: float,
	fps: float,
	label: str,
) -> None:
	"""Assert that select_path returns a path with cost equal to the brute-force minimum.

	Args:
		window_candidates: the lattice to test.
		torso_w: torso width in pixels.
		fps: source video frame rate.
		label: human-readable label for failure messages.
	"""
	# Reset weights to module defaults before each check so no prior test leaks.
	walk_viterbi.reset_cost_weights_for_tests()

	dp_cost = _dp_cost(window_candidates, torso_w, fps)
	min_cost = _brute_force_min_cost(window_candidates, torso_w, fps)

	# Both the DP and the brute force use the same compute_path_cost scorer.
	# If the DP-selected path has cost > brute-force minimum, the DP is sub-optimal.
	# Use math.isclose with tight relative tolerance (1e-9) and a small absolute
	# tolerance to handle cases where both costs are zero or very small.
	assert math.isclose(dp_cost, min_cost, rel_tol=1e-9, abs_tol=1e-12), (
		f"{label}: DP cost ({dp_cost}) != brute-force minimum ({min_cost}). "
		f"DP found a sub-optimal path -- second-order Viterbi DP bug."
	)


#============================================
# Dense-lattice property test.
#============================================

import pytest

@pytest.mark.parametrize("n_frames,n_cands,seed", _DENSE_PARAMS)
def test_dp_optimal_dense_lattice(n_frames: int, n_cands: int, seed: int) -> None:
	"""DP cost equals brute-force minimum on dense lattices (all candidates in envelope).

	Dense = n_cands real candidates per frame, all within ABSOLUTE_MAX_JUMP_W.
	No +inf edges in this shape. Tests that the DP handles the pure cost
	comparison correctly without the hard-prune path.

	Args:
		n_frames: number of window frames.
		n_cands: candidates per frame.
		seed: RNG seed for reproducibility.
	"""
	rng = random.Random(seed)
	window = _build_dense_lattice(rng, n_frames, n_cands)
	_assert_dp_equals_brute_force_min(window, TORSO_W, FPS, f"dense(n={n_frames}, k={n_cands}, seed={seed})")


#============================================
# Sparse-lattice property test (empty frames -> forced skips).
#============================================

@pytest.mark.parametrize("n_frames,n_empty,seed", _SPARSE_PARAMS)
def test_dp_optimal_sparse_lattice(n_frames: int, n_empty: int, seed: int) -> None:
	"""DP cost equals brute-force minimum on lattices with 1-2 empty frames.

	Empty frames force skip nodes, exercising the leading/bridged/trailing skip
	charge accounting in the DP initialization and termination logic.

	Args:
		n_frames: number of window frames.
		n_empty: number of empty frames injected.
		seed: RNG seed for reproducibility.
	"""
	rng = random.Random(seed)
	window = _build_sparse_lattice(rng, n_frames, n_empty)
	_assert_dp_equals_brute_force_min(window, TORSO_W, FPS, f"sparse(n={n_frames}, empty={n_empty}, seed={seed})")


#============================================
# Far-candidate lattice property test (+inf edges).
#============================================

@pytest.mark.parametrize("seed", _FAR_PARAMS)
def test_dp_optimal_far_candidate_lattice(seed: int) -> None:
	"""DP cost equals brute-force minimum when some candidates are beyond ABSOLUTE_MAX_JUMP_W.

	Far candidates produce +inf edges, which the DP skips with 'continue'. The
	brute-force also computes +inf for those paths, so the minimum is drawn only
	from paths that avoid the far candidate or reach it via skip. Tests that the
	hard prune does not create phantom minimum-cost paths in the DP.

	Args:
		seed: RNG seed for reproducibility.
	"""
	rng = random.Random(seed)
	window = _build_far_candidate_lattice(rng)
	_assert_dp_equals_brute_force_min(window, TORSO_W, FPS, f"far_candidate(seed={seed})")


#============================================
# Near-stationary lattice property test (SPEED_EPSILON_W floor).
#============================================

@pytest.mark.parametrize("n_frames,seed", _STATIONARY_PARAMS)
def test_dp_optimal_stationary_lattice(n_frames: int, seed: int) -> None:
	"""DP cost equals brute-force minimum on near-stationary lattices.

	Per-frame speed is below SPEED_EPSILON_W so the heading-delta term is
	always zeroed. Tests that the speed-epsilon floor in _angle_between does
	not introduce any asymmetry between the DP and the brute-force scorer.

	Args:
		n_frames: number of window frames.
		seed: RNG seed for reproducibility.
	"""
	rng = random.Random(seed)
	window = _build_stationary_lattice(rng, n_frames)
	_assert_dp_equals_brute_force_min(window, TORSO_W, FPS, f"stationary(n={n_frames}, seed={seed})")


#============================================
# Zero/negative integrated_mag lattice property test (neutral evidence).
#============================================

@pytest.mark.parametrize("n_frames,seed", _ZERO_MAG_PARAMS)
def test_dp_optimal_zero_mag_lattice(n_frames: int, seed: int) -> None:
	"""DP cost equals brute-force minimum when frames have zero or negative integrated_mag.

	When max_mag <= 0, the evidence term is neutral (0.0) for every candidate
	at that frame. Tests the edge case where the evidence normalizer denominator
	is zero, so selection falls purely to geometry + skip.

	Args:
		n_frames: number of window frames.
		seed: RNG seed for reproducibility.
	"""
	rng = random.Random(seed)
	window = _build_zero_mag_lattice(rng, n_frames)
	_assert_dp_equals_brute_force_min(window, TORSO_W, FPS, f"zero_mag(n={n_frames}, seed={seed})")


#============================================
# Determinism test: same lattice twice -> identical selected path.
#============================================

def test_select_path_determinism() -> None:
	"""select_path called twice with identical inputs returns identical path objects sequence.

	Two calls with the same window produce paths where each position holds the
	same blob object (or both None). Since the DP is deterministic by
	construction (documented tie-break: first state in iteration order), this
	must hold unconditionally.
	"""
	walk_viterbi.reset_cost_weights_for_tests()

	rng = random.Random(42)
	window = _build_dense_lattice(rng, 5, 2)

	path1 = walk_viterbi.select_path(window, TORSO_W, FPS)
	path2 = walk_viterbi.select_path(window, TORSO_W, FPS)

	# Both paths must have the same length.
	assert len(path1) == len(path2)
	# Each position must hold the same object (identity equality for blobs,
	# both None for skip nodes).
	for i, (a, b) in enumerate(zip(path1, path2)):
		if a is None and b is None:
			continue
		assert a is b, (
			f"Position {i}: first call returned {a!r}, second returned {b!r}. "
			f"select_path must be deterministic."
		)
