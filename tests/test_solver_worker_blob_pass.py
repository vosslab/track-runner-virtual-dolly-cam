"""Worker honors WorkerContext.blob_pass when solving an interval.

Placement: a dedicated module rather than test_tr_solver_integration.py
because this isolates one behavioral contract -- the pool worker routes the
dispatch's run-invariant blob_pass flag into solve_interval_analytical. The
solver call is stubbed, so no video decode or subprocess runs; the test is
pure in-process plumbing verification and stays well under one second.

Bare imports (e.g. import solver_workers) are resolved via conftest.py.
"""

# local repo modules (bare imports resolved by conftest.py)
import interval_solver
import solver_workers


#============================================
def _make_context(blob_pass: bool) -> solver_workers.WorkerContext:
	"""Build a WorkerContext with stub run-invariant state.

	Only blob_pass is exercised; every other field is an inert placeholder
	because solve_interval_analytical is stubbed and never reads them.
	"""
	context = solver_workers.WorkerContext(
		reader=object(),
		scene_transform=object(),
		motion_track=object(),
		all_seeds_scene=[],
		all_seeds=[],
		fps=30.0,
		debug=False,
		blob_pass=blob_pass,
	)
	return context


#============================================
def _run_worker_capturing_blob_pass(monkeypatch, blob_pass: bool) -> bool:
	"""Invoke the worker once and return the blob_pass kwarg it routed."""
	recorded = {}
	# stub the solver so no video decode runs; record the routed kwarg
	def fake_solve(*args, **kwargs) -> dict:
		recorded["blob_pass"] = kwargs["blob_pass"]
		return {}
	monkeypatch.setattr(
		interval_solver, "solve_interval_analytical", fake_solve,
	)
	# stub the fingerprint so tiny stub seeds need no real geometry
	monkeypatch.setattr(
		interval_solver, "compute_interval_fingerprint",
		lambda seed_start, seed_end, bin_factor=1: "fp",
	)
	monkeypatch.setattr(
		solver_workers, "_WORKER_CONTEXT", _make_context(blob_pass),
	)
	seed_start = {"frame_index": 0}
	seed_end = {"frame_index": 10}
	solver_workers._solve_interval_worker((0, seed_start, seed_end))
	return recorded["blob_pass"]


#============================================
def test_worker_routes_blob_pass_true(monkeypatch):
	"""A blob_pass=True context drives the Stage-4 walker pass."""
	assert _run_worker_capturing_blob_pass(monkeypatch, True) is True


#============================================
def test_worker_routes_blob_pass_false(monkeypatch):
	"""A blob_pass=False context keeps Stage-3 pure Hermite."""
	assert _run_worker_capturing_blob_pass(monkeypatch, False) is False
