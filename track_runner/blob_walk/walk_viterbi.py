"""Viterbi path-selection over a window of per-frame blob candidate lattices.

Pure functions extracted from walk_walker.py: given a window of candidate
lists (one list per frame), select the lowest-cost trajectory through the
lattice using a Viterbi-style dynamic program. No frame state survives past
the window; these functions are stateless apart from their arguments.

Cost terms (per contract C2, all spatial terms in torso-width units):
  - per-step displacement cap, edges above the cap pruned to +inf;
  - evidence bonus scaled by candidate integrated_mag;
  - a fixed skip cost for choosing None when a candidate exists.

Contract references:
  C2: displacement thresholds in torso-width units.
  C9: FWD/BWD independence; these functions carry no cross-pass state.

No appearance, color, or template terms enter the cost (C8).
"""

# Standard Library
import math

# local repo modules
import blob_walk.walk_motion_gate as walk_motion_gate

# ============================================================
# Viterbi cost weights.
# Tunable weight stubs; keep near the top for easy adjustment.
# ============================================================

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


#============================================
def select_path(
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

				trans_cost = transition_cost(cx_i, cy_i, cx_j, cy_j, max_jump_px, torso_w)
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
def transition_cost(
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
def compute_path_cost(path: list, torso_w: float, fps: float) -> float:
	"""Compute total Viterbi cost for a selected path (for debug logging).

	Args:
		path: List of blob dicts or None, as returned by select_path.
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
			total += transition_cost(cx_i, cy_i, cx_j, cy_j, max_jump_px, torso_w)

	return total
