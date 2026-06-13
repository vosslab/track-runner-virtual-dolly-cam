"""Viterbi path-selection over a window of per-frame blob candidate lattices.

Pure functions: given a window of candidate lists (one list per frame),
select the lowest-cost trajectory through the lattice using a Viterbi-style
dynamic program. No frame state survives past the window; these functions are
stateless apart from their arguments (and a write-once module weight cache,
see "Cost-weight injection" below).

Cost model: pairwise velocity-delta scoring (audit P2). The spec called for
window-variance trajectory-consistency terms (variance of velocity magnitude
and of velocity heading across the window). Variance penalizes deviation from
a window mean; pairwise deltas penalize acceleration. The two overlap but are
not identical. The pairwise form is the intentional design choice here because
it is additive over DP transitions and optimal-substructure-safe: each
transition cost depends only on two consecutive velocities, so the DP state is
an ordered pair of real nodes and Bellman optimality holds. A true window
variance is not separable across DP edges and would break the DP.

The earlier first-order cost (displacement + raw-mag evidence + skip) enforced
"move as little as possible," so stationary distractors beat the moving runner
(audit P2: 96.2% min-displacement selection FWD, drift-stall). The velocity-
delta terms (speed-delta and heading-delta) reward a smooth constant-velocity
trajectory instead of a frozen one, which is what a running runner produces.

Cost terms (per contract C2, all spatial terms in torso-width units):
  - skip charge: SKIP_COST per skipped frame between two real nodes;
  - soft displacement: WEIGHT_DISPLACEMENT * per-frame speed (torso/frame),
    plus a continuous WEIGHT_OVERSPEED quadratic penalty above the physical
    per-frame speed limit;
  - speed-delta transition: WEIGHT_SPEED_DELTA * |speed2 - speed1| between two
    consecutive real-node velocities;
  - heading-delta transition: WEIGHT_HEADING_DELTA * angle(v1, v2) in radians;
  - normalized evidence: WEIGHT_EVIDENCE_NORM * (1 - normalized integrated_mag)
    per real node, bounded [0, WEIGHT_EVIDENCE_NORM].

Hard prune: an edge whose per-frame speed exceeds ABSOLUTE_MAX_JUMP_W
(1.5 torso-widths per frame) is pruned to +inf. The gap normalization in the
speed bridges skips, so this is the single remaining gate. The DP reads no
search slack (audit P5: BOOTSTRAP_UNCERTAINTY_W removed from the DP edge cap).

Skip is never a geometry node. The DP runs over real observations only: a node
is (frame_index, candidate); a DP state is an ordered pair of real nodes
(prev_node, curr_node). Skipped frames between two real nodes accumulate
SKIP_COST once per skipped frame and contribute no geometry. A window may also
resolve to the all-skip path with cost N * SKIP_COST.

Contract references:
  C2: displacement thresholds in torso-width units.
  C8: no appearance, color, or template terms enter the cost.
  C9: FWD/BWD independence; these functions carry no cross-pass state.

Cost-weight injection:
  The six tunable weights below are module-level defaults. Worker processes
  call set_cost_weights(walker_costs) once at process startup (_worker_init)
  to override them from config. The override is held in a write-once module
  cache (_COST_WEIGHT_OVERRIDES). This global is safe because each interval is
  solved in a fresh process (make_pool max_tasks_per_child=1), the same
  write-once pattern as solver_workers._WORKER_CONTEXT. Callers that never call
  set_cost_weights keep the module-constant defaults. reset_cost_weights_for_tests
  clears or injects the cache for unit tests.
"""

# Standard Library
import math

# local repo modules
import blob_walk.walk_motion_gate as walk_motion_gate

# ============================================================
# Viterbi cost weights (defaults; overridable via set_cost_weights).
# Documented initial values from the WP-COST-1 cost contract.
# ============================================================

# WEIGHT_DISPLACEMENT: multiplies per-frame speed (torso-widths per frame).
# Manager-resolved amendment to the WP-COST-1 brief (was 1.0): a 1.0 linear
# displacement term re-encodes the "move as little as possible" bias the cost
# rework exists to remove -- a constant-velocity runner would pay
# WEIGHT_DISPLACEMENT * speed per edge while a frozen distractor pays 0, so the
# model flip can never pass. At 0.25 the in-envelope displacement term is a
# gentle regularizer that bounded evidence (WEIGHT_EVIDENCE_NORM) can outvote
# when the mover carries stronger residual-motion signal (evidence-forward).
# WS-VAL tuning owns final values.
WEIGHT_DISPLACEMENT = 0.25
# WEIGHT_SPEED_DELTA: multiplies abs speed change between consecutive velocities.
WEIGHT_SPEED_DELTA = 1.0
# WEIGHT_HEADING_DELTA: multiplies heading change (radians) between velocities.
WEIGHT_HEADING_DELTA = 0.5
# WEIGHT_OVERSPEED: quadratic penalty weight for speed above the physical limit.
WEIGHT_OVERSPEED = 4.0
# WEIGHT_EVIDENCE_NORM: cost for weak residual-motion evidence (normalized).
WEIGHT_EVIDENCE_NORM = 0.5
# Skip charge per skipped frame (choosing no candidate at a frame).
SKIP_COST = 2.0

# Velocity-magnitude floor (torso/frame) below which heading is undefined; the
# heading-delta term is 0 when either velocity magnitude is below this. Not a
# YAML weight (a numerical-stability epsilon, not a tunable cost).
SPEED_EPSILON_W = 0.02

# Names of the six config-driven weights, in the order config supplies them.
# Used by set_cost_weights to apply overrides and by tr_config validation.
COST_WEIGHT_NAMES = (
	"WEIGHT_DISPLACEMENT",
	"WEIGHT_SPEED_DELTA",
	"WEIGHT_HEADING_DELTA",
	"WEIGHT_OVERSPEED",
	"WEIGHT_EVIDENCE_NORM",
	"SKIP_COST",
)

# ============================================================
# Write-once module weight cache. Populated by set_cost_weights in a worker
# process; read by _active_weights on every DP evaluation. None means "use the
# module-constant defaults above" (callers that never inject overrides).
# ============================================================
_COST_WEIGHT_OVERRIDES: dict | None = None


#============================================
def set_cost_weights(walker_costs: dict) -> None:
	"""Install config-driven cost weights for this process (write-once).

	Called once per worker process by solver_workers._worker_init. All six
	weight keys are required and accessed directly (do-not-hide-bugs-with-
	defaults): a missing key fails loud here rather than silently using a
	module default.

	Args:
		walker_costs: Dict with the six keys in COST_WEIGHT_NAMES, each a float.
	"""
	global _COST_WEIGHT_OVERRIDES
	# Direct access: every weight must be present in a config-supplied section.
	overrides = {}
	for name in COST_WEIGHT_NAMES:
		overrides[name] = float(walker_costs[name])
	_COST_WEIGHT_OVERRIDES = overrides


#============================================
def reset_cost_weights_for_tests(overrides: dict | None = None) -> None:
	"""Clear or inject the module weight cache (test-only hook).

	Args:
		overrides: None clears the cache (back to module-constant defaults);
			a dict installs those overrides directly (already keyed by
			COST_WEIGHT_NAMES with float values).
	"""
	global _COST_WEIGHT_OVERRIDES
	_COST_WEIGHT_OVERRIDES = overrides


#============================================
def _active_weights() -> dict:
	"""Return the active cost weights: overrides if installed, else defaults.

	Returns:
		Dict keyed by COST_WEIGHT_NAMES with the float weight values in force.
	"""
	if _COST_WEIGHT_OVERRIDES is not None:
		return _COST_WEIGHT_OVERRIDES
	# Fall back to the module-constant defaults.
	defaults = {
		"WEIGHT_DISPLACEMENT": WEIGHT_DISPLACEMENT,
		"WEIGHT_SPEED_DELTA": WEIGHT_SPEED_DELTA,
		"WEIGHT_HEADING_DELTA": WEIGHT_HEADING_DELTA,
		"WEIGHT_OVERSPEED": WEIGHT_OVERSPEED,
		"WEIGHT_EVIDENCE_NORM": WEIGHT_EVIDENCE_NORM,
		"SKIP_COST": SKIP_COST,
	}
	return defaults


#============================================
def _node_velocity(
	cx_a: float,
	cy_a: float,
	frame_a: int,
	cx_b: float,
	cy_b: float,
	frame_b: int,
	torso_w: float,
) -> tuple:
	"""Velocity vector (torso-widths per frame) between two real nodes.

	v = (pos_b - pos_a) / ((frame_b - frame_a) * torso_w). Gap normalization
	(division by the frame gap) bridges skipped frames so a two-frame skip
	produces a half-magnitude per-frame velocity, not a doubled one.

	Args:
		cx_a, cy_a: predecessor centroid (pixels).
		frame_a: predecessor frame index.
		cx_b, cy_b: successor centroid (pixels).
		frame_b: successor frame index (frame_b > frame_a).
		torso_w: torso width in pixels (scale unit per C2).

	Returns:
		(vx, vy) in torso-widths per frame.
	"""
	gap = frame_b - frame_a
	# gap and torso_w are positive by construction at the call sites.
	denom = gap * torso_w
	vx = (cx_b - cx_a) / denom
	vy = (cy_b - cy_a) / denom
	return (vx, vy)


#============================================
def _angle_between(v1: tuple, v2: tuple) -> float:
	"""Angle in radians between two velocity vectors, 0 if either is near-zero.

	The heading term is undefined for a near-stationary velocity, so it is
	defined as 0 when either magnitude is below SPEED_EPSILON_W (per the cost
	contract). The cosine is clamped to [-1, 1] for numerical safety.

	Args:
		v1: (vx, vy) first velocity (torso/frame).
		v2: (vx, vy) second velocity (torso/frame).

	Returns:
		Angle in radians in [0, pi].
	"""
	mag1 = math.hypot(v1[0], v1[1])
	mag2 = math.hypot(v2[0], v2[1])
	# Heading is undefined when either velocity is essentially stationary.
	if mag1 < SPEED_EPSILON_W or mag2 < SPEED_EPSILON_W:
		return 0.0
	dot = v1[0] * v2[0] + v1[1] * v2[1]
	cos_theta = dot / (mag1 * mag2)
	# Clamp against floating-point drift past the valid acos domain.
	if cos_theta > 1.0:
		cos_theta = 1.0
	elif cos_theta < -1.0:
		cos_theta = -1.0
	return math.acos(cos_theta)


#============================================
def _edge_cost(
	node_a: dict,
	node_b: dict,
	frame_a: int,
	frame_b: int,
	torso_w: float,
	fps: float,
	evidence_b: float,
	weights: dict,
) -> float:
	"""Cost of moving from real node a to real node b (no transition term).

	The edge cost is: skip charge for frames bridged + soft displacement
	(linear speed term plus a continuous quadratic overspeed penalty) +
	evidence cost for node b. Returns +inf when the per-frame speed exceeds
	the absolute hard cap (the single remaining prune gate).

	Args:
		node_a: predecessor blob dict (centroid_x, centroid_y required).
		node_b: successor blob dict (centroid keys required).
		frame_a: predecessor frame index.
		frame_b: successor frame index (frame_b > frame_a).
		torso_w: torso width in pixels (scale unit per C2).
		fps: source video frame rate.
		evidence_b: precomputed evidence cost for node b (already in [0, w_ev]).
		weights: active cost weights keyed by COST_WEIGHT_NAMES.

	Returns:
		Edge cost (float); +inf if the per-frame speed exceeds the hard cap.
	"""
	gap = frame_b - frame_a
	# Required centroid keys accessed directly (do-not-hide-bugs-with-defaults).
	dx = node_b["centroid_x"] - node_a["centroid_x"]
	dy = node_b["centroid_y"] - node_a["centroid_y"]
	disp_px = math.hypot(dx, dy)
	# Per-frame speed in torso-widths per frame; gap normalization bridges skips.
	speed = disp_px / (gap * torso_w)

	# Hard prune: per-frame speed above the absolute physical cap kills the edge.
	if speed > walk_motion_gate.ABSOLUTE_MAX_JUMP_W:
		return float("inf")

	# Skip charge: one SKIP_COST per bridged (skipped) frame between a and b.
	cost = weights["SKIP_COST"] * (gap - 1)

	# Soft displacement: linear speed term, always charged.
	cost += weights["WEIGHT_DISPLACEMENT"] * speed

	# Continuous overspeed penalty above the physical per-frame limit. The limit
	# is the physical envelope MAX_RUNNER_SPEED_W_PER_S converted to per-frame.
	limit = walk_motion_gate.MAX_RUNNER_SPEED_W_PER_S / fps
	if speed > limit:
		overspeed_frac = (speed - limit) / limit
		cost += weights["WEIGHT_OVERSPEED"] * (overspeed_frac * overspeed_frac)

	# Evidence cost for the node we move into.
	cost += evidence_b

	return cost


#============================================
def _evidence_costs_for_frame(candidates: list, weights: dict) -> list:
	"""Per-candidate evidence cost for one frame's real candidates.

	Evidence is normalized within the frame: ev = max(0, integrated_mag) /
	max_mag where max_mag is the largest positive integrated_mag among real
	candidates. Cost is WEIGHT_EVIDENCE_NORM * (1 - ev), bounded
	[0, WEIGHT_EVIDENCE_NORM]. When no candidate has positive magnitude the
	evidence cost is 0 for every candidate (no evidence signal to weigh).

	Args:
		candidates: list of blob dicts for one frame (integrated_mag required).
		weights: active cost weights keyed by COST_WEIGHT_NAMES.

	Returns:
		list of floats aligned 1:1 with candidates.
	"""
	w_ev = weights["WEIGHT_EVIDENCE_NORM"]
	# integrated_mag accessed directly (required key per the cost contract).
	mags = [max(0.0, cand["integrated_mag"]) for cand in candidates]
	max_mag = max(mags) if mags else 0.0
	costs = []
	if max_mag > 0.0:
		for mag in mags:
			ev = mag / max_mag
			# 1 - ev is already in [0, 1]; scale to [0, w_ev].
			costs.append(w_ev * (1.0 - ev))
	else:
		# No positive evidence anywhere this frame: no signal to weigh.
		costs.extend(0.0 for _ in mags)
	return costs


#============================================
def select_path(
	window_candidates: list,
	torso_w: float,
	fps: float,
) -> list:
	"""Run pairwise velocity-delta DP path selection over a window.

	Returns the lowest-cost path through the candidate lattice. Each entry in
	the returned list is either a blob dict (selected candidate) or None
	(skipped frame / no candidate at that frame). All-None means the window
	resolved to the all-skip path.

	DP structure (audit P2): skip is never a geometry node. The DP runs over
	real observations only; a DP state is an ordered pair (prev_real, curr_real)
	of real nodes, so a transition cost can depend on two consecutive
	velocities. Skipped frames between two real nodes accumulate SKIP_COST and
	contribute no geometry. The all-skip path (cost N * SKIP_COST) is the
	baseline every real path competes against.

	On exact cost ties the earlier state in iteration order wins, which
	preserves the incoming candidate order as the deterministic tie order.

	Args:
		window_candidates: List of N lists. Each sub-list contains blob dicts
			(with centroid_x, centroid_y, integrated_mag) for one frame, in the
			frame order of the window. An empty sub-list means no candidates.
		torso_w: Torso width in pixels (scale unit per C2).
		fps: Source video frame rate.

	Returns:
		List of N items: each is a blob dict (selected) or None (skip).
	"""
	n = len(window_candidates)
	if n == 0:
		return []

	weights = _active_weights()
	skip_cost = weights["SKIP_COST"]

	# Precompute per-frame, per-candidate evidence costs once.
	evidence_by_frame = [
		_evidence_costs_for_frame(candidates, weights)
		for candidates in window_candidates
	]

	# real_nodes[t] = list of (cand_index, blob_dict) real candidates at frame t.
	# Window frame index t maps to the per-frame gap arithmetic directly: the
	# gap between two real nodes at frames t_a and t_b is (t_b - t_a).
	real_nodes = []
	for candidates in window_candidates:
		frame_nodes = list(enumerate(candidates))
		real_nodes.append(frame_nodes)

	# DP over ordered pairs of real nodes. A "single" entry is a path whose last
	# real node is at frame t with no prior real node (the first real pick); a
	# "pair" entry is a path whose last two real nodes are (prev, curr).
	#
	# We track, for each window, the minimum cost to END at a given real-node
	# state, plus a backpointer to reconstruct the path. States:
	#   single[(t, j)] -> (cost, back=None)
	#   pair[(t_prev, j_prev, t, j)] -> (cost, back=(prev_state_key))
	# To keep it tractable we store costs keyed by the LAST EDGE's endpoints
	# plus, for the velocity (transition) term, the velocity coming into the
	# last node. The DP state is (prev_frame, prev_cand, curr_frame, curr_cand).

	# best_single[(t, j)] = (cost, leading_skip_count)
	# A first real node pays only its evidence cost plus the leading skip charge
	# for frames skipped before it.
	best_single = {}
	for t in range(n):
		# Leading skip charge: every frame before t in the window is skipped.
		leading_skip = skip_cost * t
		for j, blob in real_nodes[t]:
			cost = leading_skip + evidence_by_frame[t][j]
			best_single[(t, j)] = cost

	# best_pair[(ta, ja, tb, jb)] = cost: the minimum total path cost that ends
	# with real nodes a=(ta,ja) then b=(tb,jb). pair_back holds the backpointer:
	# ("single", (ta, ja)) or ("pair", (tx, jx, ta, ja)). Pairs are built in
	# increasing tb so a transition into b reads only already-final pair costs
	# (every predecessor pair ends at frame ta < tb).
	best_pair = {}
	pair_back = {}
	# Index: pairs_ending_at[(ta, ja)] = list of (tx, jx) such that the pair
	# (tx, jx, ta, ja) exists. Lets the transition step look up predecessor
	# pairs in O(deg) instead of scanning all pairs (the 9-frame window keeps
	# this small, but the index keeps the DP linear in pair count).
	pairs_ending_at = {}

	# For each candidate "b" at frame tb, consider every earlier real node "a"
	# at frame ta < tb as the predecessor edge a->b. The cost of reaching the
	# pair (a, b) is the min over how we reached "a":
	#   - "a" was the first real node (best_single[a]) -> edge a->b, no delta.
	#   - "a" was reached as a pair (x, a) -> edge a->b PLUS the speed/heading
	#       delta between velocity(x->a) and velocity(a->b).
	for tb in range(n):
		for jb, blob_b in real_nodes[tb]:
			ev_b = evidence_by_frame[tb][jb]
			for ta in range(tb):
				for ja, blob_a in real_nodes[ta]:
					edge = _edge_cost(
						blob_a, blob_b, ta, tb, torso_w, fps, ev_b, weights,
					)
					if edge == float("inf"):
						continue
					# Velocity entering node b along edge a->b.
					v_ab = _node_velocity(
						blob_a["centroid_x"], blob_a["centroid_y"], ta,
						blob_b["centroid_x"], blob_b["centroid_y"], tb,
						torso_w,
					)

					# Option 1: "a" is the first real node (no transition term).
					best_cost = best_single[(ta, ja)] + edge
					best_back = ("single", (ta, ja))

					# Option 2: "a" was reached as a pair (x, a); add delta term.
					for (tx, jx) in pairs_ending_at.get((ta, ja), []):
						prior_cost = best_pair[(tx, jx, ta, ja)]
						blob_x = window_candidates[tx][jx]
						v_xa = _node_velocity(
							blob_x["centroid_x"], blob_x["centroid_y"], tx,
							blob_a["centroid_x"], blob_a["centroid_y"], ta,
							torso_w,
						)
						# Transition (delta) cost between v(x->a) and v(a->b).
						speed_xa = math.hypot(v_xa[0], v_xa[1])
						speed_ab = math.hypot(v_ab[0], v_ab[1])
						delta = weights["WEIGHT_SPEED_DELTA"] * abs(speed_ab - speed_xa)
						delta += weights["WEIGHT_HEADING_DELTA"] * _angle_between(v_xa, v_ab)
						cand_cost = prior_cost + edge + delta
						if cand_cost < best_cost:
							best_cost = cand_cost
							best_back = ("pair", (tx, jx, ta, ja))

					best_pair[(ta, ja, tb, jb)] = best_cost
					pair_back[(ta, ja, tb, jb)] = best_back
					pairs_ending_at.setdefault((tb, jb), []).append((ta, ja))

	# Trailing skip charge: frames after the last real node are skipped. The
	# all-skip baseline is N * SKIP_COST (every frame skipped).
	all_skip_cost = skip_cost * n

	best_total = all_skip_cost
	best_kind = "all_skip"
	best_key = None

	# A path ending at a single real node at frame t pays trailing skips for
	# frames t+1..n-1.
	for (t, j), cost in best_single.items():
		trailing = skip_cost * (n - 1 - t)
		total = cost + trailing
		if total < best_total:
			best_total = total
			best_kind = "single"
			best_key = (t, j)

	# A path ending at a pair whose last node is at frame tb pays trailing skips
	# for frames tb+1..n-1.
	for (ta, ja, tb, jb), cost in best_pair.items():
		trailing = skip_cost * (n - 1 - tb)
		total = cost + trailing
		if total < best_total:
			best_total = total
			best_kind = "pair"
			best_key = (ta, ja, tb, jb)

	# Reconstruct the chosen real-node sequence as a list of (frame, cand).
	chosen = _reconstruct_chosen(best_kind, best_key, pair_back)

	# Build the per-frame path: real node where chosen, None elsewhere.
	chosen_by_frame = {t: j for (t, j) in chosen}
	path = []
	for t in range(n):
		if t in chosen_by_frame:
			path.append(window_candidates[t][chosen_by_frame[t]])
		else:
			path.append(None)
	return path


#============================================
def _reconstruct_chosen(best_kind: str, best_key: object, pair_back: dict) -> list:
	"""Reconstruct the chosen (frame, candidate) sequence from DP backpointers.

	Args:
		best_kind: "all_skip", "single", or "pair".
		best_key: None for all_skip, (t, j) for single, or the pair key tuple.
		pair_back: backpointer map built by select_path.

	Returns:
		List of (frame_index_in_window, candidate_index) in ascending frame
		order. Empty for the all-skip path.
	"""
	if best_kind == "all_skip":
		return []
	if best_kind == "single":
		return [best_key]
	# Walk the pair backpointers from the final pair back to the first node.
	chosen = []
	ta, ja, tb, jb = best_key
	# Append the last node, then the predecessor, then follow back links.
	chosen.append((tb, jb))
	chosen.append((ta, ja))
	back = pair_back[best_key]
	while back is not None and back[0] == "pair":
		tx, jx, ta_prev, ja_prev = back[1]
		chosen.append((tx, jx))
		back = pair_back[(tx, jx, ta_prev, ja_prev)]
	# back is now ("single", ...) or None; the first node is already appended.
	chosen.reverse()
	return chosen


#============================================
def compute_path_term_breakdown(
	path: list,
	torso_w: float,
	fps: float,
	window_candidates: list | None = None,
) -> list:
	"""Per-node term-by-term cost breakdown along a selected path.

	Telemetry/diagnostic helper. Evaluates the SAME shared term evaluator that
	compute_path_step_costs sums, exposing each term separately so tests and
	diagnostics can inspect the cost composition. compute_path_step_costs sums
	each node's terms; compute_path_cost sums the steps. Three views derived
	from one evaluator.

	Term keys per node dict: skip, displacement, overspeed, speed_delta,
	heading_delta, evidence. Every node carries all six keys (0.0 where the
	term does not apply at that node).

	Args:
		path: List of blob dicts or None, as returned by select_path.
		torso_w: Torso width in pixels (scale unit per C2).
		fps: Source video frame rate.
		window_candidates: Optional index-aligned list of the per-frame
			candidate lists select_path saw. When provided, the evidence term is
			computed exactly as the DP computed it (per-frame normalization
			against the frame's full candidate list), so sum(step_costs) equals
			the DP-selected path cost. When None (legacy callers without the
			candidate lists), evidence terms are 0.0 and the sum invariant holds
			against the geometry+skip subset of the cost.

	Returns:
		List of N dicts, one per path node, aligned 1:1 with path. Empty list
		when path is empty.
	"""
	breakdown = _evaluate_path_terms(path, torso_w, fps, window_candidates)
	return breakdown


#============================================
def _evaluate_path_terms(
	path: list,
	torso_w: float,
	fps: float,
	window_candidates: list | None = None,
) -> list:
	"""Shared per-node term evaluator for the three telemetry views.

	Reconstructs the run of real nodes in the path (skips are None) and charges
	each node the terms it is responsible for: each skipped (None) frame carries
	its own SKIP_COST; the soft displacement (and overspeed) land on the second-
	and-later real node along its inbound edge; the delta terms land on the
	third-and-later real node; and evidence lands on every real node. By
	construction the sum of all node term dicts equals compute_path_cost for the
	same path (and, when window_candidates is supplied, equals the DP-selected
	path cost including the same evidence normalization).

	Required blob keys (integrated_mag, centroid_x, centroid_y) accessed
	directly per do-not-hide-bugs-with-defaults; skip nodes are None.

	This reads the already-selected path only; it runs no DP, holds no
	cross-pass state (C9), and keeps spatial terms in torso-width units (C2).

	Args:
		path: List of blob dicts or None, as returned by select_path.
		torso_w: Torso width in pixels.
		fps: Source video frame rate.
		window_candidates: Optional per-frame candidate lists (index-aligned
			with path). Enables exact per-frame evidence normalization; None
			zeroes the evidence term (see compute_path_term_breakdown).

	Returns:
		List of N per-node term dicts (keys: skip, displacement, overspeed,
		speed_delta, heading_delta, evidence). Empty list when path is empty.
	"""
	n = len(path)
	if n == 0:
		return []

	weights = _active_weights()
	skip_cost = weights["SKIP_COST"]
	limit = walk_motion_gate.MAX_RUNNER_SPEED_W_PER_S / fps

	# One term dict per node, all six terms initialized to 0.0.
	terms = [
		{
			"skip": 0.0,
			"displacement": 0.0,
			"overspeed": 0.0,
			"speed_delta": 0.0,
			"heading_delta": 0.0,
			"evidence": 0.0,
		}
		for _ in range(n)
	]

	# Indices of real (non-skip) nodes in window-frame order.
	real_indices = [t for t in range(n) if path[t] is not None]

	# Evidence term. The DP normalizes a candidate's evidence within its frame
	# against the frame's full candidate list (the strongest integrated_mag).
	# When the caller supplies window_candidates we reproduce that normalization
	# exactly, so the breakdown's evidence matches the DP and sum(steps) equals
	# the DP-selected path cost. Without window_candidates the siblings are not
	# visible, so the evidence term is 0.0 (the sum invariant then holds against
	# the geometry + skip subset of the cost). The integrated_mag key is read
	# directly so a malformed real node fails loud either way.
	if window_candidates is not None:
		for t in real_indices:
			candidates = window_candidates[t]
			# Per-frame evidence costs aligned 1:1 with this frame's candidates.
			frame_ev_costs = _evidence_costs_for_frame(candidates, weights)
			# Find the selected node's position within its frame candidate list
			# by identity so duplicate-magnitude siblings do not alias.
			selected = path[t]
			sel_idx = None
			for k, cand in enumerate(candidates):
				if cand is selected:
					sel_idx = k
					break
			# A selected real node must come from its own frame's candidate list.
			# If it is absent, the path and candidate lists are inconsistent --
			# fail loud rather than letting a bare TypeError hide the cause.
			if sel_idx is None:
				raise RuntimeError(
					f"selected path node not present in window_candidates "
					f"for frame index t={t}: path={selected!r}, "
					f"candidates={candidates!r}"
				)
			terms[t]["evidence"] = frame_ev_costs[sel_idx]
	else:
		for t in real_indices:
			blob = path[t]
			# Read the required key so a malformed real node fails loud here too.
			_ = max(0.0, blob["integrated_mag"])
			terms[t]["evidence"] = 0.0

	# Skip term lands on the skip node itself: every None entry carries exactly
	# one SKIP_COST (a skipped frame), and real nodes carry skip=0. Leading,
	# bridged, and trailing skips are all just None entries in the path, so this
	# one rule covers all three and keeps the breakdown 1:1 with the path. The
	# sum of skip terms equals (number of skipped frames) * SKIP_COST, which is
	# exactly the total skip charge select_path computes (leading + bridged +
	# trailing). The all-skip path is the special case where every node is None.
	for t in range(n):
		if path[t] is None:
			terms[t]["skip"] = skip_cost

	# Edge terms (displacement, overspeed) on each real node from its prior real
	# node. The bridged-skip frames already carry their own SKIP_COST on their
	# None positions above, so the edge loop adds no skip charge here.
	for idx in range(1, len(real_indices)):
		ta = real_indices[idx - 1]
		tb = real_indices[idx]
		blob_a = path[ta]
		blob_b = path[tb]
		gap = tb - ta
		dx = blob_b["centroid_x"] - blob_a["centroid_x"]
		dy = blob_b["centroid_y"] - blob_a["centroid_y"]
		disp_px = math.hypot(dx, dy)
		speed = disp_px / (gap * torso_w)
		terms[tb]["displacement"] = weights["WEIGHT_DISPLACEMENT"] * speed
		if speed > limit:
			overspeed_frac = (speed - limit) / limit
			terms[tb]["overspeed"] = weights["WEIGHT_OVERSPEED"] * (overspeed_frac * overspeed_frac)

	# Delta (transition) terms on each real node from frame index >= 2 along the
	# real-node run: speed-delta and heading-delta between consecutive velocities.
	for idx in range(2, len(real_indices)):
		tx = real_indices[idx - 2]
		ta = real_indices[idx - 1]
		tb = real_indices[idx]
		blob_x = path[tx]
		blob_a = path[ta]
		blob_b = path[tb]
		v_xa = _node_velocity(
			blob_x["centroid_x"], blob_x["centroid_y"], tx,
			blob_a["centroid_x"], blob_a["centroid_y"], ta,
			torso_w,
		)
		v_ab = _node_velocity(
			blob_a["centroid_x"], blob_a["centroid_y"], ta,
			blob_b["centroid_x"], blob_b["centroid_y"], tb,
			torso_w,
		)
		speed_xa = math.hypot(v_xa[0], v_xa[1])
		speed_ab = math.hypot(v_ab[0], v_ab[1])
		terms[tb]["speed_delta"] = weights["WEIGHT_SPEED_DELTA"] * abs(speed_ab - speed_xa)
		terms[tb]["heading_delta"] = weights["WEIGHT_HEADING_DELTA"] * _angle_between(v_xa, v_ab)

	return terms


#============================================
def compute_path_step_costs(
	path: list,
	torso_w: float,
	fps: float,
	window_candidates: list | None = None,
) -> list:
	"""Per-node total Viterbi cost contribution along a selected path.

	Sums each node's term dict from the shared evaluator into one float per
	node, so the debug log can record a truthful per-frame cost contribution.
	By construction sum(compute_path_step_costs(...)) == compute_path_cost(...)
	for the same path. Telemetry-only; reads the selected path, runs no DP,
	holds no cross-pass state (C9), spatial terms in torso-width units (C2).

	Args:
		path: List of blob dicts or None, as returned by select_path.
		torso_w: Torso width in pixels (scale unit per C2).
		fps: Source video frame rate.
		window_candidates: Optional per-frame candidate lists (see
			compute_path_term_breakdown). Threads through to evidence
			normalization; None zeroes the evidence term.

	Returns:
		List of N floats, one per path node, aligned 1:1 with path. Empty list
		when path is empty.
	"""
	breakdown = _evaluate_path_terms(path, torso_w, fps, window_candidates)
	step_costs = [sum(term_dict.values()) for term_dict in breakdown]
	return step_costs


#============================================
def compute_path_cost(
	path: list,
	torso_w: float,
	fps: float,
	window_candidates: list | None = None,
) -> float:
	"""Total Viterbi cost for a selected path (for debug logging).

	Delegates to compute_path_step_costs and sums the results so the invariant
	sum(steps) == total is structural, not just documented.

	Args:
		path: List of blob dicts or None, as returned by select_path.
		torso_w: Torso width in pixels.
		fps: Source video frame rate.
		window_candidates: Optional per-frame candidate lists (see
			compute_path_term_breakdown). When provided, the returned total
			matches the DP-selected path cost (evidence included).

	Returns:
		Total cost (float). Lower is better.
	"""
	step_costs = compute_path_step_costs(path, torso_w, fps, window_candidates)
	total = sum(step_costs)
	return total
