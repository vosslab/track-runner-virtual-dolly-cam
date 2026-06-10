"""Flag-routing test for the Stage 4 walker (WP-5b).

solve_interval_analytical takes a `blob_pass` flag (default True for the
Stage-4 promoted path). When off, the FWD/BWD interval paths come from the
pure-Hermite propagators (velocity_model.propagate_forward/backward_analytical).
When on (and a reader is present), they come from the windowed walker adapter
(walker_bundle.walk_bundle_to_path_with_coverage) instead.

This test records which producer ran for each flag value, passing the flag
explicitly so it stays pinned to the branch under test regardless of the
default. Both cases pass blob_pass explicitly.
Heavy collaborators (pre-pass, curve fit, scoring) are stubbed so the test
stays fast and isolates the branch decision.
"""

# local repo modules (track_runner/ is on sys.path via tests/conftest.py)
import interval_solver
import velocity_model
import walker_bundle
import residual_pre_pass


#============================================
def _seed(frame_index, cx, cy, w, h):
	return {"frame_index": frame_index, "cx": cx, "cy": cy, "w": w, "h": h}


#============================================
def _stub_heavy(monkeypatch, calls):
	"""Stub curve fit, pre-pass, blend, and scoring; record path producers.

	Each propagator and the walker adapter append a tag to `calls` so the test
	can see which one produced the interval paths. The stubs return trivial
	full-span path stand-ins; blend and score are stubbed to no-op shapes.
	"""
	# curve fit and pre-pass: harmless stand-ins (their outputs are only
	# consumed by the producers we stub below).
	monkeypatch.setattr(
		velocity_model, "fit_interval_curves",
		lambda *a, **k: {"start_frame": 0, "end_frame": 2},
	)
	monkeypatch.setattr(
		velocity_model, "_compute_raw_pred_forward", lambda *a, **k: [],
	)
	monkeypatch.setattr(
		velocity_model, "_compute_raw_pred_backward", lambda *a, **k: [],
	)
	monkeypatch.setattr(
		residual_pre_pass, "precompute_interval_residuals",
		lambda **k: {},
	)

	def fake_forward(*a, **k):
		calls.append("hermite_forward")
		return [{"cx": 0.0, "cy": 0.0, "w": 1.0, "h": 1.0, "conf": 1.0,
			"source": "propagated"}]

	def fake_backward(*a, **k):
		calls.append("hermite_backward")
		return [{"cx": 0.0, "cy": 0.0, "w": 1.0, "h": 1.0, "conf": 1.0,
			"source": "propagated"}]

	monkeypatch.setattr(
		velocity_model, "propagate_forward_analytical", fake_forward,
	)
	monkeypatch.setattr(
		velocity_model, "propagate_backward_analytical", fake_backward,
	)

	def fake_adapter(bundle):
		calls.append("walker")
		# coverage-returning adapter: (full_span_path, accepted_count). A
		# nonzero accepted_count keeps the walker path (no stall fallback) so
		# this test isolates the OFF-vs-ON branch, not the fallback.
		path = [{"cx": 0.0, "cy": 0.0, "w": 1.0, "h": 1.0, "conf": 1.0,
			"source": "propagated"}]
		return path, 1

	monkeypatch.setattr(
		walker_bundle, "walk_bundle_to_path_with_coverage", fake_adapter,
	)
	monkeypatch.setattr(interval_solver, "blend_paths", lambda f, b: list(f))
	monkeypatch.setattr(
		interval_solver.scoring, "score_interval_analytical",
		lambda *a, **k: {"confidence_tier": "low"},
	)


#============================================
def test_flag_off_takes_hermite_propagator_path(monkeypatch):
	"""Default (blob_pass off): the pure-Hermite propagators produce the paths."""
	calls = []
	_stub_heavy(monkeypatch, calls)

	interval_solver.solve_interval_analytical(
		_seed(0, 0.0, 0.0, 1.0, 1.0),
		_seed(2, 2.0, 0.0, 1.0, 1.0),
		scene_transform=object(),
		all_seeds_scene=[],
		fps=30.0,
		reader=object(),
		blob_pass=False,
	)

	# the Hermite propagators ran; the walker adapter did not
	assert "hermite_forward" in calls
	assert "hermite_backward" in calls
	assert "walker" not in calls


#============================================
def test_flag_on_takes_walker_adapter_path(monkeypatch):
	"""blob_pass on (reader present): the walker produces the paths."""
	calls = []
	_stub_heavy(monkeypatch, calls)

	interval_solver.solve_interval_analytical(
		_seed(0, 0.0, 0.0, 1.0, 1.0),
		_seed(2, 2.0, 0.0, 1.0, 1.0),
		scene_transform=object(),
		all_seeds_scene=[],
		fps=30.0,
		reader=object(),
		blob_pass=True,
	)

	# the walker adapter ran (twice: FWD + BWD); the Hermite propagators did not
	assert calls.count("walker") == 2
	assert "hermite_forward" not in calls
	assert "hermite_backward" not in calls
