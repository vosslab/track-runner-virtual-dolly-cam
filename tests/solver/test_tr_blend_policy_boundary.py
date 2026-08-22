"""Regression tests for the M2/M3 blend-policy attribution boundary."""

# Standard Library
import math

# PIP3 modules
import pytest

# local repo modules
import interval_solver
import scoring
import trajectory_confidence
import track_runner.blend_commitment


#============================================
class _IdentitySceneTransform:
	"""Minimal scene transform for velocity-consistency comparisons."""

	def pixel_to_scene(self, frame_index: int, px: float, py: float) -> tuple:
		return (px, py)

	#============================================
	def pixel_box_to_scene(
		self,
		frame_index: int,
		cx: float,
		cy: float,
		w: float,
		h: float,
	) -> tuple:
		"""Keep SOURCE box geometry unchanged for the boundary test."""
		return (cx, cy, w, h)


#============================================
def _state(
	cx: float,
	cy: float,
	w: float,
	h: float,
	conf: float,
) -> dict:
	"""Build one raw-pass state for the confidence owner."""
	return {
		"cx": cx,
		"cy": cy,
		"w": w,
		"h": h,
		"conf": conf,
	}


#============================================
#============================================
def test_m3_keeps_owner_confidence_separate_from_heat_commitment() -> None:
	"""M3 must not turn output selection into a new confidence definition."""
	forward_path = [
		_state(0.0, 0.0, 100.0, 120.0, 0.8),
		# Owner confidence is below exp(-1), so M3 must make a heat-run decision.
		_state(10.0, 0.0, 100.0, 120.0, 0.9),
		_state(20.0, 0.0, 100.0, 120.0, 0.1),
		_state(30.0, 0.0, 100.0, 120.0, 0.8),
	]
	backward_path = [
		_state(0.0, 0.0, 100.0, 120.0, 0.7),
		_state(220.0, 0.0, 100.0, 120.0, 0.1),
		_state(20.0, 0.0, 100.0, 120.0, 0.9),
		_state(30.0, 0.0, 100.0, 120.0, 0.7),
	]

	assert trajectory_confidence.frame_confidence(forward_path[1], backward_path[1]) \
		< math.exp(-1.0)

	def heat(frame_index: int, forward_state: dict, backward_state: dict) -> track_runner.blend_commitment.HeatEvidence:
		return track_runner.blend_commitment.HeatEvidence(available=True, forward_energy=2.0, backward_energy=0.0)

	actual = interval_solver.blend_paths(forward_path, backward_path, heat_evaluator=heat)
	assert actual[1]["commitment_direction"] == "fwd"
	assert actual[1]["blend_flag"] is True
	assert actual[1]["conf"] == trajectory_confidence.frame_confidence(
		forward_path[1], backward_path[1],
	)


#============================================
def test_overlap_cannot_select_output_or_score_agreement(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Raw-pass owner geometry, not overlap, determines M3 output and score."""
	forward_path = [_state(float(frame), 0.0, 20.0, 20.0, 0.8) for frame in range(3)]
	backward_path = [_state(float(frame), 0.0, 200.0, 200.0, 0.8) for frame in range(3)]
	assert trajectory_confidence.interval_agreement(forward_path, backward_path) == 1.0
	actual = interval_solver.blend_paths(forward_path, backward_path)
	assert actual[0]["source"] == "merged"
	interval_curves = {
		"start_frame": 0,
		"end_frame": 2,
		"left_size": (20.0, 20.0),
		"right_size": (20.0, 20.0),
	}
	score = scoring.score_interval_analytical(
		forward_path, backward_path, [], interval_curves,
		_IdentitySceneTransform(), blended_path=forward_path, fps=30.0,
	)
	assert score["agreement"] == 1.0

	# This seam guards the ownership boundary, not merely numerical coincidence
	# with a duplicate center-distance calculation inside scoring.
	monkeypatch.setattr(
		trajectory_confidence, "interval_agreement", lambda fwd, bwd: 0.314159,
	)
	changed_blended_path = [_state(999.0, -999.0, 1.0, 1.0, 0.0) for _ in range(3)]
	score = scoring.score_interval_analytical(
		forward_path, backward_path, [], interval_curves,
		_IdentitySceneTransform(), blended_path=changed_blended_path, fps=30.0,
	)
	assert score["agreement"] == 0.314159
