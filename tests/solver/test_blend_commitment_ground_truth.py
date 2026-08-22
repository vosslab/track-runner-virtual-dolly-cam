"""Synthetic ground-truth tests for canonical blend heat."""

# Standard Library
import types

# PIP3 modules
import numpy
import pytest

# local repo modules
import common_tools.frame_reader
import residual_motion
import track_runner.blend_commitment
import track_runner.interval_solver


#============================================
def test_canonical_heat_selects_runner_in_both_pass_orders(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""One real canonical field selects the runner as FWD and as BWD.

	Each of the nine frames uses the same deterministic residual lattice: a
	bright impulse falls in the known runner's torso box and the distractor box
	is cold. The test injects only residual generation and reader geometry; the
	production DoG, shared ``measure_in_box_heat`` semantics, canonical
	evaluator, and run-commitment decision all execute unchanged. Reversing the
	FWD/BWD order prevents a forward-order or tie-break bias from satisfying the
	gate.
	"""
	geometry = common_tools.frame_reader.FrameGeometry(
		source_width=256,
		source_height=128,
		bin_factor=1,
		scaled_width=256,
		scaled_height=128,
		processed_width=256,
		processed_height=128,
	)
	reader = types.SimpleNamespace(geometry=geometry)
	residual_mag = numpy.zeros((128, 256), dtype=numpy.float32)
	residual_mag[64, 60] = 20000.0
	validity_mask = numpy.full((128, 256), 255, dtype=numpy.uint8)

	def synthetic_residual(
		reader: object,
		frame_index: int,
		scene_transform: object,
		cache: dict,
		fps: float,
	) -> tuple:
		return (residual_mag, validity_mask)

	monkeypatch.setattr(
		residual_motion,
		"compute_residual_for_frame",
		synthetic_residual,
	)
	evaluator = track_runner.interval_solver.build_canonical_blend_heat_evaluator(
		reader, object(), 60.0,
	)
	runner = {"cx": 60.0, "cy": 64.0, "w": 40.0, "h": 50.0, "conf": 0.5}
	distractor = {"cx": 180.0, "cy": 64.0, "w": 40.0, "h": 50.0, "conf": 0.5}

	for expected_direction, forward_state, backward_state in (
		("fwd", runner, distractor),
		("bwd", distractor, runner),
	):
		path = track_runner.blend_commitment.commit_paths(
			[forward_state.copy() for _ in range(9)],
			[backward_state.copy() for _ in range(9)],
			100,
			evaluator,
		)
		winner_picks = sum(
			state["commitment_direction"] == expected_direction for state in path
		)
		assert winner_picks >= 7, (
			f"Known runner selected as {expected_direction} in {winner_picks}/9 frames; "
			"expected >= 7 from the production canonical heat evaluator."
		)
