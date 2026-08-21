"""Worker routing checks for the configured Stage-4 walker pass."""

# local repo modules
import pytest

import interval_solver
import solver_workers


#============================================
def _make_context(blob_pass: bool) -> solver_workers.WorkerContext:
	"""Build a worker context whose solver dependencies are stubbed below."""
	return solver_workers.WorkerContext(
		reader=object(), scene_transform=object(), motion_track=object(),
		all_seeds_scene=[], all_seeds=[], fps=30.0, debug=False,
		blob_pass=blob_pass,
	)


#============================================
def _routed_blob_pass(monkeypatch: pytest.MonkeyPatch, blob_pass: bool) -> bool:
	"""Run the worker once and return the flag delivered to its solver."""
	recorded = {}

	def fake_solve(*unused_args: object, **kwargs: object) -> dict:
		recorded["blob_pass"] = kwargs["blob_pass"]
		return {}

	monkeypatch.setattr(interval_solver, "solve_interval_analytical", fake_solve)
	monkeypatch.setattr(
		interval_solver, "compute_interval_fingerprint",
		lambda seed_start, seed_end, bin_factor=1: "fp",
	)
	monkeypatch.setattr(
		solver_workers, "_WORKER_CONTEXT", _make_context(blob_pass),
	)
	solver_workers._solve_interval_worker((
		0, {"frame_index": 0}, {"frame_index": 10},
	))
	return recorded["blob_pass"]


#============================================
def test_worker_routes_blob_pass_true(monkeypatch: pytest.MonkeyPatch) -> None:
	"""A promoted interval reaches the Stage-4 walker path."""
	assert _routed_blob_pass(monkeypatch, True) is True


#============================================
def test_worker_routes_blob_pass_false(monkeypatch: pytest.MonkeyPatch) -> None:
	"""An unpromoted interval stays on the analytical path."""
	assert _routed_blob_pass(monkeypatch, False) is False
