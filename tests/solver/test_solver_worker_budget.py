"""Behavior tests for automatic solver worker selection."""

# PIP3 modules
import pytest

# local repo modules
import solver_workers


#============================================
def test_budgeted_worker_count_caps_cpu_target_by_memory() -> None:
	"""Automatic selection chooses the largest worker count that fits."""
	count = solver_workers.select_budgeted_worker_count(
		available_bytes=450,
		parent_bytes=100,
		worker_bytes=100,
		reserve_bytes=150,
		cpu_count=12,
	)
	assert count == 2


#============================================
def test_budgeted_worker_count_fails_when_one_worker_cannot_fit() -> None:
	"""Automatic selection fails rather than exceeding its memory budget."""
	with pytest.raises(RuntimeError, match="no solver worker fits memory budget"):
		solver_workers.select_budgeted_worker_count(
			available_bytes=299,
			parent_bytes=100,
			worker_bytes=100,
			reserve_bytes=100,
			cpu_count=2,
		)


#============================================
def test_budgeted_worker_count_preserves_explicit_override() -> None:
	"""An explicit worker request remains the caller's choice."""
	count = solver_workers.select_budgeted_worker_count(
		available_bytes=1,
		parent_bytes=0,
		worker_bytes=100,
		reserve_bytes=100,
		cpu_count=1,
		requested_workers=7,
	)
	assert count == 7
