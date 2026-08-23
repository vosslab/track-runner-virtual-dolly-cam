"""Regression coverage for human seed boxes after FWD/BWD blending."""

# local repo modules
import interval_solver
import interval_seed_anchoring
import track_runner.blend_commitment


#============================================
def _state(cx: float, cy: float, w: float, h: float, conf: float) -> dict:
	"""Build one synthetic FWD or BWD tracking state."""
	state = {
		"cx": cx,
		"cy": cy,
		"w": w,
		"h": h,
		"conf": conf,
		"source": "synthetic",
	}
	return state


#============================================
def _box(state: dict) -> tuple:
	"""Return the trajectory geometry in the seed-box field order."""
	box = (state["cx"], state["cy"], state["w"], state["h"])
	return box


#============================================
def test_seed_truth_overrides_disagreeing_blend_endpoints() -> None:
	"""Visible and partial seed boxes survive deliberately wrong pass endpoints."""
	forward_path = [
		_state(10.0, 20.0, 30.0, 40.0, 1.0),
		_state(40.0, 50.0, 30.0, 40.0, 0.5),
		_state(70.0, 80.0, 30.0, 40.0, 0.1),
	]
	backward_path = [
		_state(110.0, 120.0, 130.0, 140.0, 0.1),
		_state(140.0, 150.0, 130.0, 140.0, 0.5),
		_state(170.0, 180.0, 130.0, 140.0, 1.0),
	]
	seeds = [
		{"frame_index": 0, "status": "visible",
			"cx": 1.0, "cy": 2.0, "w": 3.0, "h": 4.0},
		{"frame_index": 2, "status": "partial",
			"cx": 5.0, "cy": 6.0, "w": 7.0, "h": 8.0},
	]

	def heat(frame_index: int, forward_state: dict, backward_state: dict) -> track_runner.blend_commitment.HeatEvidence:
		return track_runner.blend_commitment.HeatEvidence(available=True, forward_energy=1.0, backward_energy=0.0)

	trajectory = interval_solver.blend_paths(
		forward_path, backward_path, heat_evaluator=heat,
	)
	interval_seed_anchoring.stamp_seed_truth(trajectory, seeds)

	assert _box(trajectory[0]) == (1.0, 2.0, 3.0, 4.0)
	assert _box(trajectory[2]) == (5.0, 6.0, 7.0, 8.0)
	assert trajectory[0]["conf"] == 1.0 and trajectory[0]["seed_status"] == "visible"
	assert trajectory[2]["conf"] == 1.0 and trajectory[2]["seed_status"] == "partial"
