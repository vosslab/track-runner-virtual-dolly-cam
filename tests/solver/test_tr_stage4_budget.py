"""Tests for the approved read-only Stage-4 walker budget policy."""

# local repo modules
import interval_solver


#============================================
def _result(
	start_frame: int,
	end_frame: int,
	confidence_tier: str,
) -> dict:
	"""Build one minimal interval result for walker-budget policy checks."""
	result = {
		"start_frame": start_frame,
		"end_frame": end_frame,
		"interval_score": {"confidence_tier": confidence_tier},
	}
	return result


#============================================
def test_stage4_walker_frame_budget_counts_measured_intervals_inclusively() -> None:
	"""A measured low/fair span includes both endpoint frames."""
	budget = interval_solver.stage4_walker_frame_budget(
		[_result(10, 12, "fair")], 20, 0,
	)

	assert budget == 3


#============================================
def test_stage4_walker_frame_budget_uses_larger_of_measured_and_post_race_floor() -> None:
	"""The durable policy selects the controlling measured or post-race floor."""
	measured_budget = interval_solver.stage4_walker_frame_budget(
		[_result(20, 29, "low")], 60, 10,
	)
	floor_budget = interval_solver.stage4_walker_frame_budget(
		[_result(20, 29, "high")], 60, 10,
	)

	assert (measured_budget, floor_budget) == (10, 5)
