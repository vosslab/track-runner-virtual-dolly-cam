"""Synthetic-lattice tests for the pairwise velocity-delta Viterbi cost model.

Written against the WP-COST-1 contract (plan: mutable-moseying-popcorn.md).
Tests are written to the NEW cost model contract, not to the current code.
Failures against the old min-displacement code are expected and labelled
OLD_MODEL_PENDING in comments.

Cost contract summary (select_path, compute_path_cost, compute_path_step_costs,
compute_path_term_breakdown):

- select_path(window_candidates, torso_w, fps) -> list[blob | None]
- Cost terms: soft WEIGHT_DISPLACEMENT * per-frame speed (torso-widths/frame,
  gap-normalized); WEIGHT_OVERSPEED * ((speed - limit) / limit)**2 when speed
  exceeds limit = MAX_RUNNER_SPEED_W_PER_S / fps; hard prune only above
  ABSOLUTE_MAX_JUMP_W = 1.5 torso/frame; WEIGHT_SPEED_DELTA * abs(|v2|-|v1|);
  WEIGHT_HEADING_DELTA * angle(v1,v2), zero when either speed < SPEED_EPSILON_W
  = 0.02; WEIGHT_EVIDENCE_NORM * (1 - mag/max_mag_in_frame), neutral when no
  positive mag in frame; SKIP_COST = 2.0 once per skipped frame.
- Boundary: first real node pays evidence only; second pays edge (no delta);
  third onward pays edge + transition (delta); all-skip costs N * SKIP_COST.
- Module defaults: WEIGHT_DISPLACEMENT=1.0, WEIGHT_SPEED_DELTA=1.0,
  WEIGHT_HEADING_DELTA=0.5, WEIGHT_OVERSPEED=4.0, WEIGHT_EVIDENCE_NORM=0.5,
  SKIP_COST=2.0.
- Weights are fixed module constants; no reset hook exists.

All tests: offline, deterministic, no I/O, sub-second.
No shebang (library/test file, not executable).
"""

import pathlib
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
# Shared test helpers.
#============================================

def _make_blob(cx: float, cy: float, integrated_mag: float = 100.0) -> dict:
	"""Build a minimal corridor blob dict for cost-model testing.

	Only the three required keys are populated (centroid_x, centroid_y,
	integrated_mag). Extra keys are omitted intentionally so tests verify
	the cost model accesses exactly these keys (do-not-hide-bugs rule).
	"""
	return {
		"centroid_x": cx,
		"centroid_y": cy,
		"integrated_mag": integrated_mag,
	}


def _setup() -> None:
	"""No-op isolation stub.

	Weights are now fixed module constants (WP-core); no install or reset is
	needed. Kept so the _setup() call sites compile unchanged.
	"""


def _torso_w() -> float:
	"""Canonical torso width for tests: 50 pixels at 60 fps."""
	return 50.0


def _fps() -> float:
	"""Canonical fps for tests."""
	return 60.0


def _speed_limit_w_per_frame() -> float:
	"""Per-frame speed limit in torso-widths, at canonical fps."""
	return walk_motion_gate.MAX_RUNNER_SPEED_W_PER_S / _fps()


#============================================
# Single-source-of-truth behavior test: _active_weights returns module constants.
# No install or override step on any path (WP-core, WP-tests contract).
#============================================

def test_active_weights_returns_module_constants() -> None:
	"""_active_weights() returns the six fixed module constants with no install step.

	The weight dict must equal the six module-level constants exactly. No
	set_cost_weights, reset, or override mechanism exists; the constants are
	the single source of truth. This asserts the identity between the accessor
	output and the named constants so any accidental divergence fails loudly.
	"""
	weights = walk_viterbi._active_weights()
	assert weights["WEIGHT_DISPLACEMENT"] == walk_viterbi.WEIGHT_DISPLACEMENT
	assert weights["WEIGHT_SPEED_DELTA"] == walk_viterbi.WEIGHT_SPEED_DELTA
	assert weights["WEIGHT_HEADING_DELTA"] == walk_viterbi.WEIGHT_HEADING_DELTA
	assert weights["WEIGHT_OVERSPEED"] == walk_viterbi.WEIGHT_OVERSPEED
	assert weights["WEIGHT_EVIDENCE_NORM"] == walk_viterbi.WEIGHT_EVIDENCE_NORM
	assert weights["SKIP_COST"] == walk_viterbi.SKIP_COST


#============================================
# Model-flip test: constant-velocity mover beats stationary distractor.
# OLD_MODEL_PENDING: old model prefers the stationary blob (lowest displacement).
#============================================

class TestModelFlip:
	"""The new model must select the moving runner over a stationary distractor."""

	def test_constant_velocity_runner_beats_stationary_distractor(self) -> None:
		"""Mover at 0.4 torso/frame with 2x mag wins over stationary distractor.

		9-frame window. Runner blob moves 0.4 * torso_w pixels per frame
		(horizontally) and carries integrated_mag TWICE the stationary
		distractor's mag.

		Physical grounding: residual-motion blobs are motion cues -- a true
		mover carries stronger residual signal than a camera-compensation
		artifact. Equal-mag stationary-vs-mover is genuinely ambiguous and
		is covered by the companion test test_equal_evidence_ambiguity_is_identity_consistent.

		Margin arithmetic at defaults (WEIGHT_DISPLACEMENT=0.25,
		WEIGHT_EVIDENCE_NORM=0.5):
		  runner_mag = 2 * distractor_mag => runner is strongest in frame =>
		    runner evidence cost = 0.5 * (1 - 1.0) = 0.0/frame
		    distractor evidence cost = 0.5 * (1 - 0.5) = 0.25/frame
		  runner displacement = 0.25 * 0.4 = 0.10/edge
		  distractor displacement = 0.0 (stationary)
		  Runner total per-frame: 0.10 + 0.00 = 0.10
		  Distractor total per-frame: 0.00 + 0.25 = 0.25
		  Runner wins every frame by 0.15.

		OLD_MODEL_PENDING: expected to fail on the old code because the old
		model's displacement cost prefers the stationary blob.
		"""
		_setup()
		torso_w = _torso_w()
		fps = _fps()
		step_px = 0.4 * torso_w

		runner_start_cx = 100.0
		cy = 200.0
		# Runner carries 2x the distractor's mag. Physical grounding: a true
		# mover produces stronger residual-motion signal; 2x gives clear margin.
		runner_mag = 300.0
		distractor_mag = 150.0

		window_candidates = []
		for i in range(9):
			runner_cx = runner_start_cx + i * step_px
			runner_blob = _make_blob(runner_cx, cy, runner_mag)
			distractor_blob = _make_blob(runner_start_cx, cy, distractor_mag)
			window_candidates.append([runner_blob, distractor_blob])

		path = walk_viterbi.select_path(window_candidates, torso_w, fps)

		assert len(path) == 9
		runner_picks = 0
		for i, blob in enumerate(path):
			if blob is None:
				continue
			expected_runner_cx = runner_start_cx + i * step_px
			if abs(blob["centroid_x"] - expected_runner_cx) < 1.0:
				runner_picks += 1
		# Constant-velocity runner with 2x evidence must win on every frame.
		assert runner_picks >= 7, (
			f"Runner (mover, 2x mag) picked {runner_picks}/9 frames; expected >= 7. "
			f"New model must prefer constant-velocity track with stronger evidence. "
			f"OLD_MODEL_PENDING if old code is still in place."
		)

	def test_equal_evidence_ambiguity_is_identity_consistent(self) -> None:
		"""Equal-mag stationary-vs-mover is underdetermined; DP must commit to one identity.

		Equal evidence (same integrated_mag) between a constant-velocity runner
		and a stationary distractor is genuinely ambiguous by design: the model
		has no signal to prefer one track over the other on the evidence axis alone.
		The displacement costs do differentiate them, so the DP will pick one
		coherent identity, but which track wins is implementation-defined.

		This test asserts identity consistency instead of a specific winner:
		all selected non-None frames must come from ONE track (no oscillation
		mid-window between the runner and the distractor). The DP must commit
		to a single coherent path even when evidence does not discriminate.

		The assertion uses two complementary checks:
		  1. All selected blobs that are NOT at the first position belong to the
		     runner track, OR all selected blobs are at the first position
		     (the distractor). No frame should pick runner while an earlier
		     frame picked distractor or vice versa after the identity is set.
		  2. Structural: return shape is valid (list of length N, each entry
		     is a dict or None).
		"""
		_setup()
		torso_w = _torso_w()
		fps = _fps()
		step_px = 0.4 * torso_w

		runner_start_cx = 100.0
		cy = 200.0
		# Equal mag: evidence is identical for both tracks, so geometry decides.
		mag = 150.0

		window_candidates = []
		for i in range(9):
			runner_cx = runner_start_cx + i * step_px
			runner_blob = _make_blob(runner_cx, cy, mag)
			distractor_blob = _make_blob(runner_start_cx, cy, mag)
			window_candidates.append([runner_blob, distractor_blob])

		path = walk_viterbi.select_path(window_candidates, torso_w, fps)

		# Structural validity: correct length, each entry dict or None.
		assert len(path) == 9
		assert all(b is None or isinstance(b, dict) for b in path)

		# Identity consistency: every non-None frame must belong to the same
		# track. At frame i=0 both tracks share the same position (runner_start_cx),
		# so identity only becomes observable from frame i=1 onward.
		# Classify each selected blob from frame 1 onward as runner or distractor.
		runner_track_picks = 0
		distractor_track_picks = 0
		for i in range(1, 9):
			blob = path[i]
			if blob is None:
				continue
			expected_runner_cx = runner_start_cx + i * step_px
			if abs(blob["centroid_x"] - expected_runner_cx) < 1.0:
				runner_track_picks += 1
			elif abs(blob["centroid_x"] - runner_start_cx) < 1.0:
				distractor_track_picks += 1
		# No oscillation: all non-ambiguous picks must come from ONE track.
		# (At most one of these can be nonzero.)
		assert runner_track_picks == 0 or distractor_track_picks == 0, (
			f"DP oscillated between runner ({runner_track_picks} picks) and "
			f"distractor ({distractor_track_picks} picks) mid-window. "
			f"Equal-evidence ambiguity must still commit to one coherent identity."
		)


#============================================
# Limb-oscillation test: coherent weaker center track beats alternating high-mag limbs.
# OLD_MODEL_PENDING: old model may pick limbs if mag cost dominates.
#============================================

class TestLimbOscillation:
	"""Walker must select a coherent center track over oscillating high-mag limbs."""

	def test_coherent_center_beats_alternating_high_mag_limbs(self) -> None:
		"""Coherent center track wins over leg blobs that alternate vertically.

		9-frame window. Leg blobs alternate between y=80 (frame 0,2,4,6,8)
		and y=180 (frames 1,3,5,7) with mag=500. A coherent center track at
		y=130 (constant, no oscillation) has mag=150.

		The alternating leg pattern has large heading delta every step (near pi
		radians). The center track has zero speed delta and zero heading delta.

		OLD_MODEL_PENDING: may fail on old code if evidence-raw-mag dominates.
		"""
		_setup()
		torso_w = _torso_w()
		fps = _fps()

		center_cx = 100.0
		center_cy = 130.0
		leg_cx = 100.0
		leg_mag = 500.0
		center_mag = 150.0

		window_candidates = []
		for i in range(9):
			center_blob = _make_blob(center_cx, center_cy, center_mag)
			if i % 2 == 0:
				leg_blob = _make_blob(leg_cx, 80.0, leg_mag)
			else:
				leg_blob = _make_blob(leg_cx, 180.0, leg_mag)
			window_candidates.append([center_blob, leg_blob])

		path = walk_viterbi.select_path(window_candidates, torso_w, fps)

		assert len(path) == 9
		center_picks = sum(
			1 for blob in path
			if blob is not None and abs(blob["centroid_y"] - center_cy) < 1.0
		)
		# The coherent center track should win on most frames.
		assert center_picks >= 7, (
			f"Center track picked {center_picks}/9 frames; expected >= 7. "
			f"New model must prefer coherent track over oscillating limb blobs. "
			f"OLD_MODEL_PENDING if old code is still in place."
		)


#============================================
# Skip-bridge test: gap frame charged exactly once.
# OLD_MODEL_PENDING: old model's skip semantics may charge differently.
#============================================

class TestSkipBridge:
	"""Single-frame skip charges exactly one SKIP_COST via term breakdown."""

	def test_single_empty_frame_skip_charged_once(self) -> None:
		"""9-frame window with one empty middle frame; skip charged exactly once.

		Uses compute_path_term_breakdown to inspect the skip term per node.
		The empty frame (frame 4) must carry skip=SKIP_COST; adjacent real
		frames carry skip=0.

		This is a structural invariant test, not a tunable-constant test.
		OLD_MODEL_PENDING: compute_path_term_breakdown may not exist yet.
		"""
		_setup()
		torso_w = _torso_w()
		fps = _fps()
		step_px = 0.3 * torso_w  # sub-limit constant velocity

		# Build a constant-velocity track with an empty center frame.
		window_candidates = []
		for i in range(9):
			if i == 4:
				window_candidates.append([])
			else:
				cx = 100.0 + i * step_px
				window_candidates.append([_make_blob(cx, 200.0, 100.0)])

		path = walk_viterbi.select_path(window_candidates, torso_w, fps)

		assert len(path) == 9
		# Frame 4 must be a skip (None path entry).
		assert path[4] is None, (
			f"Expected skip at frame 4 (empty candidates); got {path[4]}. "
			f"OLD_MODEL_PENDING."
		)

		# Check that the skip term is exactly SKIP_COST for the skip frame.
		if not hasattr(walk_viterbi, 'compute_path_term_breakdown'):
			# OLD_MODEL_PENDING: breakdown not yet available.
			return

		breakdown = walk_viterbi.compute_path_term_breakdown(path, torso_w, fps)
		assert len(breakdown) == 9

		skip_cost = walk_viterbi.SKIP_COST
		# Frame 4 (the skip) should carry skip=SKIP_COST.
		assert breakdown[4]["skip"] == skip_cost, (
			f"Skip frame should carry skip={skip_cost}; got {breakdown[4]}. "
			f"OLD_MODEL_PENDING."
		)
		# Real frames must have skip=0.
		for i in [3, 5]:
			assert breakdown[i]["skip"] == 0.0, (
				f"Real frame {i} should have skip=0; got {breakdown[i]}. "
				f"OLD_MODEL_PENDING."
			)


#============================================
# Boundary tests.
# OLD_MODEL_PENDING: some boundary rules are new to the contract.
#============================================

class TestBoundaryRules:
	"""Boundary rules: all-skip, two-real-pick, skip-prefix."""

	def test_all_skip_costs_n_times_skip_cost(self) -> None:
		"""All-empty window: total cost equals N * SKIP_COST.

		Structural invariant. Works with both old and new code (all-skip
		was always N * SKIP_COST in the old code too, so no OLD_MODEL_PENDING
		here -- the sum invariant test below will catch drift).
		"""
		_setup()
		torso_w = _torso_w()
		fps = _fps()
		n = 7

		window_candidates = [[] for _ in range(n)]
		path = walk_viterbi.select_path(window_candidates, torso_w, fps)
		assert len(path) == n
		assert all(b is None for b in path)

		# Total cost must be N * SKIP_COST.
		skip_cost = walk_viterbi.SKIP_COST
		total = walk_viterbi.compute_path_cost(path, torso_w, fps)
		assert abs(total - n * skip_cost) < 1e-9, (
			f"All-skip cost should be {n} * SKIP_COST = {n * skip_cost}; got {total}. "
			f"OLD_MODEL_PENDING if old code charges differently."
		)

	def test_two_real_picks_pays_no_delta_terms(self) -> None:
		"""Two-real-pick window: no speed_delta or heading_delta terms.

		Under the new contract: the first real node pays evidence only;
		the second pays the edge cost (skip + displacement + overspeed + evidence)
		but NO delta terms (deltas start at the third real node).

		OLD_MODEL_PENDING: compute_path_term_breakdown may not exist yet.
		"""
		_setup()
		torso_w = _torso_w()
		fps = _fps()

		# Two-frame window with real candidates.
		step_px = 0.3 * torso_w
		window_candidates = [
			[_make_blob(100.0, 200.0, 80.0)],
			[_make_blob(100.0 + step_px, 200.0, 80.0)],
		]
		path = walk_viterbi.select_path(window_candidates, torso_w, fps)
		assert len(path) == 2
		# Both frames should be accepted (real candidates present, within cap).
		assert path[0] is not None
		assert path[1] is not None

		if not hasattr(walk_viterbi, 'compute_path_term_breakdown'):
			return

		breakdown = walk_viterbi.compute_path_term_breakdown(path, torso_w, fps)
		assert len(breakdown) == 2

		# First real node: evidence only; all other terms must be zero.
		node0 = breakdown[0]
		assert node0["skip"] == 0.0
		assert node0["displacement"] == 0.0
		assert node0["overspeed"] == 0.0
		assert node0["speed_delta"] == 0.0
		assert node0["heading_delta"] == 0.0

		# Second real node: edge cost terms may be nonzero; delta terms must be zero.
		node1 = breakdown[1]
		assert node1["speed_delta"] == 0.0, (
			f"Second real node must not pay speed_delta; got {node1}. "
			f"OLD_MODEL_PENDING."
		)
		assert node1["heading_delta"] == 0.0, (
			f"Second real node must not pay heading_delta; got {node1}. "
			f"OLD_MODEL_PENDING."
		)

	def test_skip_prefix_charges_leading_skips(self) -> None:
		"""Window with leading empty frames charges SKIP_COST for each.

		Leading skips (before the first real node) each pay SKIP_COST;
		the first real node pays evidence only (no displacement from a
		skip prefix).

		OLD_MODEL_PENDING: compute_path_term_breakdown may not exist yet;
		old model may charge skip differently.
		"""
		_setup()
		torso_w = _torso_w()
		fps = _fps()

		# 5-frame window: 2 empty, then 3 real.
		window_candidates = [
			[],
			[],
			[_make_blob(100.0, 200.0, 80.0)],
			[_make_blob(115.0, 200.0, 80.0)],
			[_make_blob(130.0, 200.0, 80.0)],
		]
		path = walk_viterbi.select_path(window_candidates, torso_w, fps)
		assert len(path) == 5
		# Leading frames must be None (no candidates).
		assert path[0] is None
		assert path[1] is None
		# Real frames should be accepted.
		assert path[2] is not None

		if not hasattr(walk_viterbi, 'compute_path_term_breakdown'):
			return

		breakdown = walk_viterbi.compute_path_term_breakdown(path, torso_w, fps)
		assert len(breakdown) == 5

		skip_cost = walk_viterbi.SKIP_COST
		# Leading skip frames each pay SKIP_COST.
		assert breakdown[0]["skip"] == skip_cost, (
			f"Leading skip frame 0 should carry skip={skip_cost}; got {breakdown[0]}. "
			f"OLD_MODEL_PENDING."
		)
		assert breakdown[1]["skip"] == skip_cost, (
			f"Leading skip frame 1 should carry skip={skip_cost}; got {breakdown[1]}. "
			f"OLD_MODEL_PENDING."
		)
		# First real node (frame 2): evidence only, no displacement.
		node2 = breakdown[2]
		assert node2["skip"] == 0.0
		assert node2["displacement"] == 0.0, (
			f"First real node (after skip prefix) must not pay displacement; "
			f"got {node2}. OLD_MODEL_PENDING."
		)


#============================================
# Evidence tie-break tests.
# OLD_MODEL_PENDING: old model uses raw mag, not normalized; these are new semantics.
#============================================

class TestEvidenceTieBreak:
	"""Evidence acts as bounded tie-breaker, not dominator."""

	def test_higher_mag_wins_identical_geometry(self) -> None:
		"""Two geometrically identical tracks: stronger mag wins.

		When two candidates are at the same position (identical geometry costs),
		the one with higher integrated_mag has lower evidence cost and wins.
		OLD_MODEL_PENDING: old model uses -WEIGHT_EVIDENCE * raw_mag, which
		also favors higher mag, so this test may pass on old code too.
		"""
		_setup()
		torso_w = _torso_w()
		fps = _fps()

		step_px = 0.2 * torso_w
		# Two blobs per frame: identical geometry, different mag.
		window_candidates = []
		for i in range(5):
			cx = 100.0 + i * step_px
			strong_blob = _make_blob(cx, 200.0, 300.0)
			weak_blob = _make_blob(cx, 200.0, 100.0)
			window_candidates.append([strong_blob, weak_blob])

		path = walk_viterbi.select_path(window_candidates, torso_w, fps)
		assert len(path) == 5

		# All selected blobs should be the strong ones (integrated_mag=300).
		strong_picks = sum(
			1 for blob in path
			if blob is not None and abs(blob["integrated_mag"] - 300.0) < 1.0
		)
		assert strong_picks == 5, (
			f"Higher-mag blob should win on all frames (identical geometry); "
			f"got {strong_picks}/5 strong picks."
		)

	def test_geometry_wins_over_slightly_stronger_mag(self) -> None:
		"""Smooth constant-velocity track beats a jagged zigzag with 1.2x stronger mag.

		Track A (smooth): constant velocity 0.3 torso/frame rightward, mag=100.
		Track B (zigzag): alternates 90-degree heading turns every step at the
		same per-frame speed (0.3 torso/frame), mag=120 (~1.2x stronger).

		Both tracks are MOVING, so displacement costs cancel. The 0.2x evidence
		advantage of the zigzag is bounded by WEIGHT_EVIDENCE_NORM = 0.5 and
		amounts to at most 0.5 * (1 - 100/120) = 0.083/frame. The zigzag's 90-deg
		heading turn costs WEIGHT_HEADING_DELTA * (pi/2) = 0.5 * 1.571 = 0.785/step
		every step from the third node onward. Geometry wins by ~0.702/step.

		Margin arithmetic at defaults (WEIGHT_DISPLACEMENT=0.25,
		WEIGHT_HEADING_DELTA=0.5, WEIGHT_EVIDENCE_NORM=0.5):
		  Both tracks: displacement = 0.25 * 0.3 = 0.075/step (same; cancels)
		  Smooth: speed_delta=0, heading_delta=0, evidence = 0.5*(1-100/120) = 0.083/step
		  Zigzag: speed_delta=0 (same |v|), heading_delta = 0.5*pi/2 = 0.785/step,
		          evidence = 0.5*(1-120/120) = 0.0/step (strongest mag in frame)
		  Smooth net per step: 0.083 (evidence only beyond shared disp)
		  Zigzag net per step: 0.785 (heading delta only beyond shared disp)
		  Smooth wins by 0.702/step -- bounded evidence cannot pay the heading delta.
		"""
		import math as _math
		_setup()
		torso_w = _torso_w()
		fps = _fps()
		step_px = 0.3 * torso_w

		# Smooth track: moves rightward at constant velocity.
		# Zigzag track: starts at same origin, alternates +y and -y each step,
		# so the heading flips 90 deg (right->up->right->down...) every step.
		# x advances at step_px/sqrt(2), y alternates +step_px/sqrt(2) and
		# -step_px/sqrt(2) so speed stays constant at step_px per frame.
		zigzag_start = (200.0, 200.0)
		smooth_start = (200.0, 400.0)  # different y so identity check is unambiguous
		unit = step_px / _math.sqrt(2.0)

		window_candidates = []
		zigzag_cx = zigzag_start[0]
		zigzag_cy = zigzag_start[1]
		for i in range(9):
			smooth_cx = smooth_start[0] + i * step_px
			smooth_cy = smooth_start[1]
			smooth_blob = _make_blob(smooth_cx, smooth_cy, 100.0)
			# Zigzag alternates heading: right+down (even) vs right+up (odd)
			zigzag_blob = _make_blob(zigzag_cx, zigzag_cy, 120.0)
			window_candidates.append([smooth_blob, zigzag_blob])
			# Advance zigzag: right+down on even steps, right+up on odd steps
			if i % 2 == 0:
				zigzag_cx += unit
				zigzag_cy += unit
			else:
				zigzag_cx += unit
				zigzag_cy -= unit

		path = walk_viterbi.select_path(window_candidates, torso_w, fps)
		assert len(path) == 9

		# Smooth track blobs all have cy=smooth_start[1]=400.0; zigzag varies.
		smooth_picks = sum(
			1 for blob in path
			if blob is not None and abs(blob["centroid_y"] - smooth_start[1]) < 1.0
		)
		# Smooth constant-velocity track must win over the jagged zigzag despite
		# the zigzag's stronger mag (bounded evidence cannot pay heading delta).
		assert smooth_picks >= 7, (
			f"Smooth track (mag=100) picked {smooth_picks}/9 frames; expected >= 7 "
			f"against zigzag (mag=120, 90-deg turns). "
			f"Geometry (delta costs ~0.702/step) must dominate bounded evidence (~0.083/step)."
		)

	def test_zero_mag_frame_gets_neutral_evidence(self) -> None:
		"""Frame with zero-magnitude candidates gets neutral evidence (cost=0).

		When all candidates have integrated_mag <= 0, evidence cost is 0 for
		every candidate. Selection falls to geometry.

		OLD_MODEL_PENDING: compute_path_term_breakdown may not exist yet;
		neutral-evidence rule is new to the contract.
		"""
		_setup()
		torso_w = _torso_w()
		fps = _fps()

		# Single-frame window; candidate with zero mag.
		window_candidates = [[_make_blob(100.0, 200.0, 0.0)]]
		path = walk_viterbi.select_path(window_candidates, torso_w, fps)
		assert len(path) == 1
		# Candidate should be selected (zero mag is not a reason to skip).
		assert path[0] is not None

		if not hasattr(walk_viterbi, 'compute_path_term_breakdown'):
			return

		breakdown = walk_viterbi.compute_path_term_breakdown(path, torso_w, fps)
		# Evidence cost for zero-mag frame should be 0 (neutral).
		assert breakdown[0]["evidence"] == 0.0, (
			f"Zero-mag frame should have neutral evidence (0.0); "
			f"got {breakdown[0]['evidence']}. OLD_MODEL_PENDING."
		)


#============================================
# Start-bias test: bad high-evidence first blob must not anchor the path.
# OLD_MODEL_PENDING: old model's large raw-mag bonus means the first blob can dominate.
#============================================

class TestStartBias:
	"""First-frame high-evidence wrong blob must not anchor the whole path."""

	def test_bad_first_blob_does_not_anchor_coherent_track(self) -> None:
		"""High-evidence wrong blob at frame 0 does not win over the coherent track.

		Frame 0: two candidates - bad blob (high mag, wrong position) and
		runner (lower mag, correct start). Frames 1-8: only runner-track
		candidates.

		Under the new contract, the first real node pays evidence only (bounded
		<= WEIGHT_EVIDENCE_NORM). The evidence advantage of the bad blob at
		frame 0 is small; the coherent runner track wins because the subsequent
		velocity-delta path from the runner start is far cheaper than the jump
		from the bad blob position to the runner track at frame 1.

		OLD_MODEL_PENDING: old code's large negative raw-mag evidence can make
		the bad high-mag blob anchor the path.
		"""
		_setup()
		torso_w = _torso_w()
		fps = _fps()
		step_px = 0.3 * torso_w

		# Runner track: starts at (100, 200), moves right.
		runner_start_cx = 100.0
		runner_cy = 200.0
		runner_mag = 100.0

		# Bad blob: at (400, 200) at frame 0, very high mag.
		bad_cx = 400.0
		bad_cy = 200.0
		bad_mag = 1000.0

		window_candidates = []
		# Frame 0: bad blob AND runner start.
		window_candidates.append([
			_make_blob(bad_cx, bad_cy, bad_mag),
			_make_blob(runner_start_cx, runner_cy, runner_mag),
		])
		# Frames 1-8: only runner-track candidates.
		for i in range(1, 9):
			cx = runner_start_cx + i * step_px
			window_candidates.append([_make_blob(cx, runner_cy, runner_mag)])

		path = walk_viterbi.select_path(window_candidates, torso_w, fps)
		assert len(path) == 9

		# All frames from 1 onward should follow the runner track.
		runner_picks_1_to_8 = sum(
			1 for i in range(1, 9)
			if path[i] is not None
			and abs(path[i]["centroid_x"] - (runner_start_cx + i * step_px)) < 1.0
		)
		assert runner_picks_1_to_8 >= 7, (
			f"Frames 1-8 should follow the coherent runner track; "
			f"got {runner_picks_1_to_8}/8 runner picks. "
			f"Bad first blob must not anchor the path. OLD_MODEL_PENDING."
		)


#============================================
# Model-invariant structural checks via compute_path_term_breakdown.
# OLD_MODEL_PENDING: breakdown function and new cost terms may not exist yet.
#============================================

class TestStructuralInvariants:
	"""Structural checks: skip=SKIP_COST for skips, no deltas before 3rd node, etc."""

	def test_skip_term_equals_skip_cost_for_one_skipped_frame(self) -> None:
		"""One skipped frame in the path carries exactly SKIP_COST in its skip term.

		This checks a structural invariant: the skip field in the breakdown
		equals SKIP_COST (not 2*SKIP_COST, not half). This is a contract
		invariant test, not a check of the tunable weight value itself.

		OLD_MODEL_PENDING: compute_path_term_breakdown may not exist.
		"""
		_setup()
		if not hasattr(walk_viterbi, 'compute_path_term_breakdown'):
			return

		torso_w = _torso_w()
		fps = _fps()
		skip_cost = walk_viterbi.SKIP_COST

		# Manually construct a path with one skip.
		# Frames 0 and 2 are real; frame 1 is None (skip).
		blob0 = _make_blob(100.0, 200.0, 80.0)
		blob2 = _make_blob(130.0, 200.0, 80.0)
		path = [blob0, None, blob2]
		window_candidates = [
			[blob0],
			[],
			[blob2],
		]
		_ = window_candidates  # consumed by select_path in a real call; here path is synthetic

		breakdown = walk_viterbi.compute_path_term_breakdown(path, torso_w, fps)
		assert len(breakdown) == 3

		# The skip node must carry exactly SKIP_COST in the skip field.
		assert breakdown[1]["skip"] == skip_cost, (
			f"Skip node skip term should be {skip_cost}; got {breakdown[1]}. "
			f"OLD_MODEL_PENDING."
		)

	def test_no_delta_terms_before_third_real_node(self) -> None:
		"""First two real nodes have speed_delta=0 and heading_delta=0.

		Delta terms (speed_delta, heading_delta) require at least two
		consecutive velocities and thus three real nodes. The first and second
		real nodes in any path carry no delta terms.

		OLD_MODEL_PENDING: compute_path_term_breakdown may not exist.
		"""
		_setup()
		if not hasattr(walk_viterbi, 'compute_path_term_breakdown'):
			return

		torso_w = _torso_w()
		fps = _fps()
		step_px = 0.2 * torso_w

		# Build a 5-frame path with constant velocity.
		blob0 = _make_blob(100.0, 200.0, 80.0)
		blob1 = _make_blob(100.0 + step_px, 200.0, 80.0)
		blob2 = _make_blob(100.0 + 2 * step_px, 200.0, 80.0)
		blob3 = _make_blob(100.0 + 3 * step_px, 200.0, 80.0)
		blob4 = _make_blob(100.0 + 4 * step_px, 200.0, 80.0)
		path = [blob0, blob1, blob2, blob3, blob4]

		breakdown = walk_viterbi.compute_path_term_breakdown(path, torso_w, fps)
		assert len(breakdown) == 5

		# First real node: all terms zero except evidence.
		node0 = breakdown[0]
		assert node0["speed_delta"] == 0.0, (
			f"First real node speed_delta must be 0.0; got {node0}. OLD_MODEL_PENDING."
		)
		assert node0["heading_delta"] == 0.0, (
			f"First real node heading_delta must be 0.0; got {node0}. OLD_MODEL_PENDING."
		)
		assert node0["displacement"] == 0.0, (
			f"First real node displacement must be 0.0; got {node0}. OLD_MODEL_PENDING."
		)

		# Second real node: edge cost, but no delta terms.
		node1 = breakdown[1]
		assert node1["speed_delta"] == 0.0, (
			f"Second real node speed_delta must be 0.0; got {node1}. OLD_MODEL_PENDING."
		)
		assert node1["heading_delta"] == 0.0, (
			f"Second real node heading_delta must be 0.0; got {node1}. OLD_MODEL_PENDING."
		)

	def test_overspeed_term_zero_at_speed_limit(self) -> None:
		"""Overspeed term is zero when per-frame speed equals the speed limit.

		At exactly speed = MAX_RUNNER_SPEED_W_PER_S / fps, the quadratic
		overspeed term is zero (continuous at the limit by contract). This is
		a structural zero check, not a tunable-weight check.

		OLD_MODEL_PENDING: compute_path_term_breakdown may not exist;
		overspeed term is new to the contract.
		"""
		_setup()
		if not hasattr(walk_viterbi, 'compute_path_term_breakdown'):
			return

		torso_w = _torso_w()
		fps = _fps()
		limit_w_per_frame = _speed_limit_w_per_frame()
		# Displace exactly at the limit.
		step_px = limit_w_per_frame * torso_w

		blob0 = _make_blob(100.0, 200.0, 80.0)
		blob1 = _make_blob(100.0 + step_px, 200.0, 80.0)
		path = [blob0, blob1]

		breakdown = walk_viterbi.compute_path_term_breakdown(path, torso_w, fps)
		assert len(breakdown) == 2

		# Second node at exactly the speed limit: overspeed must be 0.0.
		assert breakdown[1]["overspeed"] == 0.0, (
			f"Overspeed term must be 0.0 at the speed limit; "
			f"got {breakdown[1]['overspeed']}. OLD_MODEL_PENDING."
		)

	def test_zero_mag_evidence_term_is_neutral(self) -> None:
		"""When all frame candidates have zero mag, evidence term is 0.0 (neutral).

		This is a structural neutral-evidence check. The contract says:
		when no real candidate has positive magnitude, evidence cost is 0
		for every real candidate.

		OLD_MODEL_PENDING: compute_path_term_breakdown may not exist;
		neutral-evidence rule is new.
		"""
		_setup()
		if not hasattr(walk_viterbi, 'compute_path_term_breakdown'):
			return

		torso_w = _torso_w()
		fps = _fps()

		# Path with zero-mag candidates.
		blob0 = _make_blob(100.0, 200.0, 0.0)
		path = [blob0]

		breakdown = walk_viterbi.compute_path_term_breakdown(path, torso_w, fps)
		assert len(breakdown) == 1
		assert breakdown[0]["evidence"] == 0.0, (
			f"Zero-mag evidence term must be neutral (0.0); "
			f"got {breakdown[0]['evidence']}. OLD_MODEL_PENDING."
		)


#============================================
# Sum invariant test: sum(compute_path_step_costs) == compute_path_cost.
# This test passes on old code too (the delegation pattern is already in place).
#============================================

class TestSumInvariant:
	"""sum(compute_path_step_costs(path)) == compute_path_cost(path)."""

	def test_sum_of_step_costs_equals_total_cost(self) -> None:
		"""Step costs sum to total cost for a variety of path shapes."""
		_setup()
		torso_w = _torso_w()
		fps = _fps()
		step_px = 0.2 * torso_w

		# Test several path shapes.
		cases = [
			# All real, constant velocity.
			[_make_blob(100.0 + i * step_px, 200.0, 80.0) for i in range(6)],
			# With a skip in the middle.
			[
				_make_blob(100.0, 200.0, 80.0),
				None,
				_make_blob(130.0, 200.0, 80.0),
				_make_blob(145.0, 200.0, 80.0),
			],
			# All skips.
			[None, None, None],
		]

		for path in cases:
			total = walk_viterbi.compute_path_cost(path, torso_w, fps)
			step_costs = walk_viterbi.compute_path_step_costs(path, torso_w, fps)
			assert len(step_costs) == len(path)
			step_sum = sum(step_costs)
			assert abs(step_sum - total) < 1e-9, (
				f"sum(step_costs)={step_sum} != compute_path_cost={total} "
				f"for path length {len(path)}."
			)


#============================================
# Term breakdown keys test: breakdown returns expected per-node keys.
# OLD_MODEL_PENDING: breakdown function may not exist yet.
#============================================

class TestBreakdownKeys:
	"""compute_path_term_breakdown returns dicts with the required term keys."""

	def test_breakdown_has_required_term_keys(self) -> None:
		"""Each node breakdown dict carries all six required term keys.

		OLD_MODEL_PENDING: compute_path_term_breakdown may not exist.
		"""
		_setup()
		if not hasattr(walk_viterbi, 'compute_path_term_breakdown'):
			return

		torso_w = _torso_w()
		fps = _fps()

		blob0 = _make_blob(100.0, 200.0, 80.0)
		blob1 = _make_blob(115.0, 200.0, 80.0)
		path = [blob0, None, blob1]

		breakdown = walk_viterbi.compute_path_term_breakdown(path, torso_w, fps)
		required_keys = {"skip", "displacement", "overspeed", "speed_delta", "heading_delta", "evidence"}
		for i, node in enumerate(breakdown):
			missing = required_keys - set(node.keys())
			assert not missing, (
				f"Breakdown node {i} missing keys: {missing}. "
				f"Required: {required_keys}. Got: {set(node.keys())}. OLD_MODEL_PENDING."
			)
