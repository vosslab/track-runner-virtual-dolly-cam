"""Per-pass walker stall fallback in solve_interval_analytical.

The Stage-4 walker is default-on for promoted intervals (reader present). A
known bootstrap-stall bug makes the walker reject every candidate on some
intervals and emit a degenerate path with ZERO accepted frames. On those the
walker output is strictly worse than Hermite, so solve_interval_analytical
falls back per pass: a pass with zero accepted frames uses its Hermite path,
while a pass with >=1 accepted frame keeps the walker path. This guarantees
"never worse than Hermite" on promoted intervals.

These tests inject a fake walk_bundle_to_path_with_coverage that returns a
controlled (path, accepted_count) for each direction, and stub the Hermite
propagators with tagged outputs, so the test sees which producer drove each
pass without decoding any video. The fallback is output selection: it reads
the walker's own accepted-frame coverage, never raw_pred and never FWD/BWD
agreement.
"""

# local repo modules (track_runner/ is on sys.path via tests/conftest.py)
import interval_solver
import velocity_model
import walker_bundle


#============================================
def _seed(frame_index, cx, cy, w, h):
	return {"frame_index": frame_index, "cx": cx, "cy": cy, "w": w, "h": h}


#============================================
def _state(source):
	"""One full-span state dict carrying a source tag the test can read back."""
	return {"cx": 0.0, "cy": 0.0, "w": 1.0, "h": 1.0, "conf": 1.0,
		"source": source}


#============================================
def _stub_collaborators(monkeypatch):
	"""Stub curve fit, pre-pass, blend, and scoring; keep blend identity.

	blend_paths returns the forward path verbatim so the test can read the
	winning forward producer off blended_path[0]['source']. Scoring returns a
	fixed tier. The Hermite propagators are tagged so a fallback is visible.
	"""
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
		interval_solver.residual_pre_pass, "precompute_interval_residuals",
		lambda **k: {},
	)
	monkeypatch.setattr(
		velocity_model, "propagate_forward_analytical",
		lambda *a, **k: [_state("hermite_fwd")],
	)
	monkeypatch.setattr(
		velocity_model, "propagate_backward_analytical",
		lambda *a, **k: [_state("hermite_bwd")],
	)
	monkeypatch.setattr(interval_solver, "blend_paths", lambda f, b: list(f))
	monkeypatch.setattr(
		interval_solver.scoring, "score_interval_analytical",
		lambda *a, **k: {"confidence_tier": "low"},
	)


#============================================
def test_zero_accepted_pass_falls_back_to_hermite(monkeypatch):
	"""A pass with zero accepted walker frames uses its Hermite path."""
	_stub_collaborators(monkeypatch)

	# both passes stall: accepted_count == 0 for each direction
	def fake_coverage(bundle):
		return [_state("walker")], 0

	monkeypatch.setattr(
		walker_bundle, "walk_bundle_to_path_with_coverage", fake_coverage,
	)

	result = interval_solver.solve_interval_analytical(
		_seed(0, 0.0, 0.0, 1.0, 1.0),
		_seed(2, 2.0, 0.0, 1.0, 1.0),
		scene_transform=object(),
		all_seeds_scene=[],
		fps=30.0,
		reader=object(),
		blob_pass=True,
	)

	# both passes stalled -> Hermite path won and the interval reports hermite
	assert result["forward_path"][0]["source"] == "hermite_fwd"
	assert result["backward_path"][0]["source"] == "hermite_bwd"
	assert result["walker_fallback_fwd"] is True
	assert result["walker_fallback_bwd"] is True
	assert result["propagator_path"] == "hermite"


#============================================
def test_accepted_pass_keeps_walker_path(monkeypatch):
	"""A pass with >=1 accepted walker frame keeps the walker path."""
	_stub_collaborators(monkeypatch)

	# both passes have real coverage: accepted_count >= 1
	def fake_coverage(bundle):
		return [_state("walker")], 3

	monkeypatch.setattr(
		walker_bundle, "walk_bundle_to_path_with_coverage", fake_coverage,
	)

	result = interval_solver.solve_interval_analytical(
		_seed(0, 0.0, 0.0, 1.0, 1.0),
		_seed(2, 2.0, 0.0, 1.0, 1.0),
		scene_transform=object(),
		all_seeds_scene=[],
		fps=30.0,
		reader=object(),
		blob_pass=True,
	)

	# both passes covered -> walker path retained, no fallback
	assert result["forward_path"][0]["source"] == "walker"
	assert result["backward_path"][0]["source"] == "walker"
	assert result["walker_fallback_fwd"] is False
	assert result["walker_fallback_bwd"] is False
	assert result["propagator_path"] == "walker"
