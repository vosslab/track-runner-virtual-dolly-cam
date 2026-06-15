"""Per-pass walker stall fallback in solve_interval_analytical.

The Stage-4 walker is default-on for promoted intervals (reader present). A
stall occurs when a pass has zero post-seed accepted frames. Two stall shapes:
- Pure stall: accepted_count == 0 (every frame missed).
- Seed-only stall: accepted_count == 1, post_seed_accepted == 0 (only the
  seed frame was observed via bootstrap; all remaining frames missed).

Both produce a degenerate path strictly worse than Hermite. solve_interval_analytical
falls back per pass when post_seed_accepted == 0, reading the WalkCoverage
named field so the seed-only case cannot silently bypass the gate. A pass with
post_seed_accepted >= 1 keeps the walker path. This guarantees "never worse
than Hermite" on promoted intervals.

These tests inject a fake walk_bundle_to_path_with_coverage that returns a
controlled (path, WalkCoverage) for each direction, and stub the Hermite
propagators with tagged outputs, so the test sees which producer drove each
pass without decoding any video. The fallback is output selection: it reads
the walker's own post-seed coverage, never raw_pred and never FWD/BWD
agreement.
"""

# PIP3 modules
import common_tools.frame_reader as frame_reader

# local repo modules (track_runner/ is on sys.path via tests/conftest.py)
import interval_solver
import velocity_model
import walker_bundle


#============================================
class _FakeReader:
	"""Minimal reader stub carrying a real FrameGeometry (bin_factor=1).

	The walker branch of solve_interval_analytical reads reader.geometry to
	project seeds into PROCESSED and the walker output back into SOURCE. At
	bin_factor=1 those conversions are identity, so the fallback behavior under
	test is unchanged; the stub only needs the geometry attribute to exist.
	"""

	def __init__(self) -> None:
		self.geometry = frame_reader.FrameGeometry(
			source_width=64, source_height=64, bin_factor=1,
			scaled_width=64, scaled_height=64,
			processed_width=64, processed_height=64,
		)


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

	# both passes pure-stall: accepted_count == 0, post_seed_accepted == 0
	def fake_coverage(bundle):
		coverage = walker_bundle.WalkCoverage(accepted_count=0, post_seed_accepted=0)
		return [_state("walker")], coverage

	monkeypatch.setattr(
		walker_bundle, "walk_bundle_to_path_with_coverage", fake_coverage,
	)

	result = interval_solver.solve_interval_analytical(
		_seed(0, 0.0, 0.0, 1.0, 1.0),
		_seed(2, 2.0, 0.0, 1.0, 1.0),
		scene_transform=object(),
		all_seeds_scene=[],
		fps=30.0,
		reader=_FakeReader(),
		blob_pass=True,
	)

	# both passes stalled -> Hermite path won and the interval reports hermite
	assert result["forward_path"][0]["source"] == "hermite_fwd"
	assert result["backward_path"][0]["source"] == "hermite_bwd"
	assert result["walker_fallback_fwd"] is True
	assert result["walker_fallback_bwd"] is True
	assert result["propagator_path"] == "hermite"


#============================================
def test_seed_only_accepted_pass_falls_back_to_hermite(monkeypatch):
	"""A bootstrap-only pass (seed frame accepted, all others missed) falls back.

	This is the P10 case: accepted_count == 1 but post_seed_accepted == 0.
	The previous gate (accepted_count == 0) did not fire; this case proves
	the corrected gate (post_seed_accepted == 0) does fire.

	Reproduces the Conant seed_1126_1134 FWD failure at unit level: 8-frame
	interval, seed accepted at bootstrap, 7 remaining frames soft_miss_no_blob.
	"""
	_stub_collaborators(monkeypatch)

	# bootstrap-only stall: seed frame accepted, nothing else
	def fake_coverage(bundle):
		coverage = walker_bundle.WalkCoverage(accepted_count=1, post_seed_accepted=0)
		return [_state("walker")], coverage

	monkeypatch.setattr(
		walker_bundle, "walk_bundle_to_path_with_coverage", fake_coverage,
	)

	result = interval_solver.solve_interval_analytical(
		_seed(0, 0.0, 0.0, 1.0, 1.0),
		_seed(2, 2.0, 0.0, 1.0, 1.0),
		scene_transform=object(),
		all_seeds_scene=[],
		fps=30.0,
		reader=_FakeReader(),
		blob_pass=True,
	)

	# bootstrap-only stall must trigger the fallback (gate on post_seed_accepted)
	assert result["walker_fallback_fwd"] is True
	assert result["walker_fallback_bwd"] is True
	assert result["forward_path"][0]["source"] == "hermite_fwd"
	assert result["backward_path"][0]["source"] == "hermite_bwd"
	assert result["propagator_path"] == "hermite"


#============================================
def test_accepted_pass_keeps_walker_path(monkeypatch):
	"""A pass with post_seed_accepted >= 1 keeps the walker path."""
	_stub_collaborators(monkeypatch)

	# both passes have real post-seed coverage
	def fake_coverage(bundle):
		coverage = walker_bundle.WalkCoverage(accepted_count=3, post_seed_accepted=3)
		return [_state("walker")], coverage

	monkeypatch.setattr(
		walker_bundle, "walk_bundle_to_path_with_coverage", fake_coverage,
	)

	result = interval_solver.solve_interval_analytical(
		_seed(0, 0.0, 0.0, 1.0, 1.0),
		_seed(2, 2.0, 0.0, 1.0, 1.0),
		scene_transform=object(),
		all_seeds_scene=[],
		fps=30.0,
		reader=_FakeReader(),
		blob_pass=True,
	)

	# both passes covered -> walker path retained, no fallback
	assert result["forward_path"][0]["source"] == "walker"
	assert result["backward_path"][0]["source"] == "walker"
	assert result["walker_fallback_fwd"] is False
	assert result["walker_fallback_bwd"] is False
	assert result["propagator_path"] == "walker"
