"""Stable input-contract tests for the rolling Viterbi cost memo."""

# PIP3 modules
import pytest

# local repo modules
import blob_walk.walk_viterbi as walk_viterbi


#============================================
def _blob(cx: float, integrated_mag: float) -> dict:
	"""Return a minimal deterministic candidate."""
	return {
		"centroid_x": cx,
		"centroid_y": 100.0,
		"integrated_mag": integrated_mag,
	}


#============================================
def _rolling_lattice() -> list:
	"""Build six frames whose five-frame windows overlap."""
	return [
		(_blob(100.0 + frame * 8.0, 200.0), _blob(180.0, 80.0))
		for frame in range(6)
	]


#============================================
def test_cost_memo_rejects_misaligned_absolute_frames() -> None:
	"""Absolute frame keys cannot silently drift from candidate positions."""
	with pytest.raises(ValueError, match="align 1:1"):
		walk_viterbi.select_path(_rolling_lattice()[:2], 50.0, 60.0, [7])


#============================================
def test_cost_memo_rejects_mutated_candidate_input() -> None:
	"""A cached cost cannot be reused after its source candidate changes."""
	frames = _rolling_lattice()
	memo = walk_viterbi.WalkCostMemo()
	walk_viterbi.select_path(frames[:5], 50.0, 60.0, [10, 11, 12, 13, 14], memo)
	frames[1][0]["integrated_mag"] = 201.0
	with pytest.raises(RuntimeError, match="candidate inputs changed"):
		walk_viterbi.select_path(frames[1:], 50.0, 60.0, [11, 12, 13, 14, 15], memo)
