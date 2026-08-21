"""Deterministic run-commitment policy tests."""

# PIP3 modules
import pytest

# local repo modules
import track_runner.blend_commitment
import trajectory_confidence


#============================================
def _state(cx: float, conf: float = 0.5) -> dict:
	"""Build one constant-size raw path state."""
	result = {"cx": cx, "cy": 0.0, "w": 100.0, "h": 120.0, "conf": conf}
	return result


#============================================
def _evidence(forward_energy: float, backward_energy: float) -> object:
	"""Return a computed canonical-field evaluator with fixed energies."""
	def evaluate(frame_index: int, forward_state: dict, backward_state: dict) -> track_runner.blend_commitment.HeatEvidence:
		result = track_runner.blend_commitment.HeatEvidence(
			available=True,
			forward_energy=forward_energy,
			backward_energy=backward_energy,
		)
		return result
	return evaluate


#============================================
def test_unavailable_is_not_cold_and_does_not_choose_a_pass() -> None:
	"""Missing canonical fields preserve a visible unavailable run marker."""
	forward = [_state(0.0), _state(1.0)]
	backward = [_state(300.0), _state(301.0)]
	def unavailable(frame_index: int, forward_state: dict, backward_state: dict) -> track_runner.blend_commitment.HeatEvidence:
		return track_runner.blend_commitment.HeatEvidence(available=False)
	path = track_runner.blend_commitment.commit_paths(forward, backward, 0, unavailable)
	assert all(state["commitment_direction"] == "unavailable" for state in path)
	assert all(state["commitment_alpha"] == 0.0 for state in path)


#============================================
def test_unavailable_run_does_not_enter_transition_feasibility() -> None:
	"""Unavailable evidence retains baseline geometry without a fictitious ramp."""
	forward = [_state(0.0), _state(1000.0)]
	backward = [_state(300.0), _state(1300.0)]
	def unavailable(frame_index: int, forward_state: dict, backward_state: dict) -> track_runner.blend_commitment.HeatEvidence:
		return track_runner.blend_commitment.HeatEvidence(available=False)
	path = track_runner.blend_commitment.commit_paths(
		forward, backward, 0, unavailable, max_step_widths=0.01,
	)
	assert [state["commitment_direction"] for state in path] == ["unavailable", "unavailable"]
	assert track_runner.blend_commitment.max_center_step_widths(path) > 0.01


#============================================
def test_disagreement_without_evidence_fails_loudly() -> None:
	"""The policy has no hidden confidence or distance-decay fallback."""
	with pytest.raises(track_runner.blend_commitment.BlendEvidenceRequiredError):
		track_runner.blend_commitment.commit_paths([_state(0.0)], [_state(300.0)], 0)


#============================================
def test_cold_tie_is_deterministic_forward_not_unavailable() -> None:
	"""Computed zero heat is cold evidence and uses the documented stable tie."""
	path = track_runner.blend_commitment.commit_paths(
		[_state(0.0)], [_state(300.0)], 0, _evidence(0.0, 0.0),
	)
	assert path[0]["commitment_direction"] == "fwd"
	assert path[0]["commitment_alpha"] > 0.0


#============================================
def test_separate_runs_choose_their_own_heat_winner() -> None:
	"""A later disagreement cannot inherit the earlier run's pass identity."""
	forward = [_state(0.0), _state(1.0), _state(2.0), _state(3.0), _state(4.0)]
	backward = [_state(120.0), _state(1.0), _state(2.0), _state(123.0), _state(124.0)]
	def heat(frame_index: int, forward_state: dict, backward_state: dict) -> track_runner.blend_commitment.HeatEvidence:
		if frame_index == 0:
			return track_runner.blend_commitment.HeatEvidence(True, 5.0, 0.0)
		return track_runner.blend_commitment.HeatEvidence(True, 0.0, 5.0)
	path = track_runner.blend_commitment.commit_paths(forward, backward, 0, heat)
	assert path[0]["commitment_direction"] == "fwd"
	assert path[1]["blend_flag"] is False
	assert path[2]["blend_flag"] is False
	assert path[3]["commitment_direction"] == "bwd"
	assert path[4]["commitment_direction"] == "bwd"


#============================================
def test_infeasible_intrinsic_step_fails_instead_of_hiding_jump() -> None:
	"""A band cannot mask a raw pass whose own adjacent geometry is impossible."""
	forward = [_state(0.0), _state(1000.0)]
	backward = [_state(300.0), _state(1300.0)]
	with pytest.raises(track_runner.blend_commitment.BlendTransitionInfeasibleError):
		track_runner.blend_commitment.commit_paths(forward, backward, 0, _evidence(1.0, 0.0))


#============================================
def test_unrelated_baseline_jump_does_not_reject_committed_run() -> None:
	"""Commitment validates touched edges, not untouched baseline geometry."""
	forward = [_state(0.0), _state(1000.0), _state(1000.0), _state(1000.0)]
	backward = [_state(0.0), _state(1000.0), _state(1101.0), _state(1000.0)]
	path = track_runner.blend_commitment.commit_paths(
		forward, backward, 0, _evidence(1.0, 0.0), max_step_widths=0.5,
	)
	assert path[1]["cx"] == 1000.0
	assert track_runner.blend_commitment.max_center_step_widths(path) > 0.5
	assert path[2]["commitment_direction"] == "fwd"


#============================================
@pytest.mark.parametrize(("forward", "backward"), [
	(
		[_state(0.0), _state(1000.0), _state(1000.0)],
		[_state(0.0), _state(1200.0), _state(1000.0)],
	),
	(
		[_state(133.0), _state(0.0), _state(1000.0), _state(0.0), _state(133.0)],
		[_state(133.0), _state(400.0), _state(1400.0), _state(400.0), _state(133.0)],
	),
	(
		[_state(66.0), _state(0.0), _state(1000.0)],
		[_state(66.0), _state(200.0), _state(1000.0)],
	),
])
def test_oversized_edges_touching_committed_run_fail_loudly(
	forward: list,
	backward: list,
) -> None:
	"""Entry, internal, and exit transition edges remain subject to the C2 cap."""
	with pytest.raises(track_runner.blend_commitment.BlendTransitionInfeasibleError):
		track_runner.blend_commitment.commit_paths(
			forward, backward, 0, _evidence(1.0, 0.0), max_step_widths=1.0,
		)


#============================================
def test_transition_band_lengthens_for_a_committed_run() -> None:
	"""A legal longer band is selected before transition infeasibility is raised."""
	forward = [_state(float(index)) for index in range(8)]
	backward = [_state(120.0 + float(index)) for index in range(8)]
	path = track_runner.blend_commitment.commit_paths(
		forward, backward, 0, _evidence(1.0, 0.0), max_step_widths=0.1,
	)
	assert 0.0 < path[0]["commitment_alpha"] < 1.0 / 3.0
	assert track_runner.blend_commitment.max_center_step_widths(path) <= 0.1


#============================================
def test_agreement_only_path_keeps_existing_baseline_behavior() -> None:
	"""Without disagreement, policy remains the weighted baseline without a gate."""
	forward = [_state(0.0), _state(1000.0)]
	backward = [_state(0.0), _state(1000.0)]
	path = track_runner.blend_commitment.commit_paths(
		forward, backward, 0, max_step_widths=0.01,
	)
	assert path == track_runner.blend_commitment.build_baseline_path(forward, backward)


#============================================
def test_transition_is_monotone_and_confidence_stays_raw_pass_owned() -> None:
	"""Long runs ramp in/out monotonically while C9 confidence remains untouched."""
	forward = [_state(float(index), 0.9) for index in range(10)]
	backward = [_state(400.0 + float(index), 0.1) for index in range(10)]
	path = track_runner.blend_commitment.commit_paths(forward, backward, 0, _evidence(9.0, 1.0))
	alphas = [state["commitment_alpha"] for state in path]
	assert alphas[:3] == sorted(alphas[:3])
	assert alphas[-3:] == sorted(alphas[-3:], reverse=True)
	assert track_runner.blend_commitment.max_center_step_widths(path) <= 1.5
	for index, state in enumerate(path):
		assert state["conf"] == trajectory_confidence.frame_confidence(
			forward[index], backward[index],
		)


#============================================
def test_baseline_and_committed_paths_preserve_absolute_frame_linkage() -> None:
	"""Overlay adapters retain supplied absolute frame identity."""
	forward = [_state(0.0), _state(1.0)]
	backward = [_state(120.0), _state(121.0)]
	for index, state in enumerate(forward):
		state["frame_index"] = 500 + index
	for index, state in enumerate(backward):
		state["frame_index"] = 500 + index
	baseline = track_runner.blend_commitment.build_baseline_path(forward, backward)
	committed = track_runner.blend_commitment.commit_paths(forward, backward, 500, _evidence(3.0, 0.0))
	assert [state["frame_index"] for state in baseline] == [500, 501]
	assert [state["frame_index"] for state in committed] == [500, 501]
