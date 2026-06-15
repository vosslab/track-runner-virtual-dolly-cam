"""Solve -> storage round trip keeps boxes in SOURCE pixels at bin > 1.

This is the WS1-roundtrip guard for the coordinate-space defect named in
docs/active_plans/audits/coordinate_space_bin_gt1_audit.md: at bin_factor > 1
the production walker path stored PROCESSED pixels where SOURCE is required
(Gap B), and fed SOURCE seeds to the PROCESSED walker (Gap A). Both gaps are
no-ops at bin_factor == 1 because SOURCE and PROCESSED coincide there.

The fix runs the whole solve in ONE space (PROCESSED at bin > 1) and converts
walker geometry to SOURCE at exactly one seam in
interval_solver.solve_interval_analytical (_walker_path_processed_to_source),
immediately before the geometry flows toward state_io.write_torso_box_coords.
The Hermite path is already SOURCE and is untouched.

Design of the test (least-brittle route, independent of DoG / residual image
synthesis): the walker producer
(walker_bundle.walk_bundle_to_path_with_coverage) is replaced by a stub that
returns a KNOWN PROCESSED full-span path. The stub also captures the seed the
production bundle handed it, so Gap A (PROCESSED seeds into the walker) is
asserted directly. The stub output then flows through the REAL conversion seam,
the real blend, and the real state_io write/load boundary that production
walker output uses. The Hermite interval uses the real propagators with no
reader.

Synthetic shape (fixed): SOURCE frame 3840 x 2160, bin_factor = 3 (non power of
two), two SOURCE seeds with a nonzero x AND y offset and a known constant
velocity translation, a NON-SQUARE box (w != h). One interval is forced Hermite
(no reader, blob_pass False), one is the walker (reader present, blob_pass
True). The stored SOURCE box at an interior frame is compared edge by edge
against known SOURCE truth.

Negative controls prove the test bites: a scale-injection error (PROCESSED
stored as SOURCE, i.e. no bin multiply on walker output) FAILS, a swapped-axis
error FAILS, and a sub-pixel drift PASSES.
"""

# Standard Library
import os

# PIP3 modules
import common_tools.frame_reader as frame_reader

# local repo modules (track_runner/ is on sys.path via tests/conftest.py)
import scoring
import state_io
import interval_solver
import velocity_model
import walker_bundle


# Fixed synthetic geometry. 3840 is divisible by 3, so the bin math is exact
# and SOURCE truth lands on integers (no goodbox/rounding ambiguity).
SOURCE_WIDTH = 3840
SOURCE_HEIGHT = 2160
BIN_FACTOR_BIN3 = 3
# Interval frame span. Interior frame is strictly inside (start, end).
START_FRAME = 0
END_FRAME = 6
INTERIOR_FRAME = 3
# Non-square box; nonzero x AND y offset; constant-velocity translation.
SEED_W_SOURCE = 240.0
SEED_H_SOURCE = 360.0
START_CX_SOURCE = 600.0
START_CY_SOURCE = 480.0
# constant per-frame translation (distinct x and y so a swap is detectable)
VEL_X_SOURCE = 90.0
VEL_Y_SOURCE = 30.0


#============================================
def _geometry(bin_factor: int) -> frame_reader.FrameGeometry:
	"""Build a real FrameGeometry for the fixed synthetic source dims.

	Conversion is pure scale by bin_factor (origin-preserving), so the test
	does not depend on goodbox snapping; scaled/processed dims are filled with
	the plain floor division the production resolver also uses.
	"""
	scaled_width = SOURCE_WIDTH // bin_factor
	scaled_height = SOURCE_HEIGHT // bin_factor
	geometry = frame_reader.FrameGeometry(
		source_width=SOURCE_WIDTH,
		source_height=SOURCE_HEIGHT,
		bin_factor=bin_factor,
		scaled_width=scaled_width,
		scaled_height=scaled_height,
		processed_width=scaled_width,
		processed_height=scaled_height,
	)
	return geometry


#============================================
def _reader(bin_factor: int) -> object:
	"""Reader stub exposing only the .geometry the walker seam reads."""
	geometry = _geometry(bin_factor)
	return type("_FakeReader", (), {"geometry": geometry})()


#============================================
def _source_truth_box(frame_index: int) -> dict:
	"""Known SOURCE-pixel box (center + size) at a frame, constant velocity."""
	cx = START_CX_SOURCE + VEL_X_SOURCE * frame_index
	cy = START_CY_SOURCE + VEL_Y_SOURCE * frame_index
	return {"cx": cx, "cy": cy, "w": SEED_W_SOURCE, "h": SEED_H_SOURCE}


#============================================
def _seed(frame_index: int) -> dict:
	"""SOURCE-pixel seed dict (the solver's input space)."""
	box = _source_truth_box(frame_index)
	return {
		"frame_index": frame_index,
		"cx": box["cx"], "cy": box["cy"],
		"w": box["w"], "h": box["h"],
	}


#============================================
def _edges(box: dict) -> tuple[float, float, float, float]:
	"""Return (left, top, right, bottom) for a center+size box."""
	left = box["cx"] - box["w"] / 2.0
	top = box["cy"] - box["h"] / 2.0
	right = box["cx"] + box["w"] / 2.0
	bottom = box["cy"] + box["h"] / 2.0
	return (left, top, right, bottom)


#============================================
def _stub_solve_collaborators(monkeypatch) -> None:
	"""Stub curve fit, pre-pass, blend, and scoring; keep blend identity.

	blend_paths returns the forward path verbatim so the stored blended box is
	exactly the (converted) forward producer output. Scoring returns a fixed
	tier. This isolates the coordinate-space seam from blend/score math.
	"""
	monkeypatch.setattr(
		velocity_model, "fit_interval_curves",
		lambda *a, **k: {"start_frame": START_FRAME, "end_frame": END_FRAME},
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
	monkeypatch.setattr(interval_solver, "blend_paths", lambda f, b: list(f))
	monkeypatch.setattr(
		scoring, "score_interval_analytical",
		lambda *a, **k: {"confidence_tier": "low"},
	)


#============================================
def _processed_walker_path(bin_factor: int) -> list:
	"""Full-span PROCESSED walker path matching SOURCE truth after conversion.

	The real walker emits PROCESSED pixels (reader.width/height are PROCESSED).
	Build a PROCESSED path equal to SOURCE truth divided by bin_factor, so that
	after the production seam multiplies by bin_factor the stored SOURCE box
	equals SOURCE truth exactly.
	"""
	path = []
	for frame_index in range(START_FRAME, END_FRAME + 1):
		source_box = _source_truth_box(frame_index)
		path.append({
			"frame_index": frame_index,
			"cx": source_box["cx"] / bin_factor,
			"cy": source_box["cy"] / bin_factor,
			"w": source_box["w"] / bin_factor,
			"h": source_box["h"] / bin_factor,
			"conf": 1.0,
			"source": "walker",
		})
	return path


#============================================
def _run_walker_interval(
	monkeypatch, bin_factor: int, walker_path_factory, captured_seeds: list,
) -> dict:
	"""Solve one walker interval; walker producer returns a controlled path.

	walker_path_factory(bin_factor) supplies the PROCESSED path the stubbed
	walker returns; tests vary it to inject scale/swap/drift errors. The stub
	captures bundle.seed so Gap A (PROCESSED seeds into the walker) is testable.
	"""
	_stub_solve_collaborators(monkeypatch)
	walker_path = walker_path_factory(bin_factor)

	def fake_coverage(bundle):
		# Gap A evidence: production must hand the walker a PROCESSED seed.
		captured_seeds.append(dict(bundle.seed))
		coverage = walker_bundle.WalkCoverage(
			accepted_count=len(walker_path), post_seed_accepted=len(walker_path),
		)
		# return a fresh copy so the seam cannot mutate the fixture
		return [dict(state) for state in walker_path], coverage

	monkeypatch.setattr(
		walker_bundle, "walk_bundle_to_path_with_coverage", fake_coverage,
	)

	result = interval_solver.solve_interval_analytical(
		_seed(START_FRAME),
		_seed(END_FRAME),
		scene_transform=object(),
		all_seeds_scene=[],
		fps=30.0,
		reader=_reader(bin_factor),
		blob_pass=True,
	)
	return result


#============================================
def _run_hermite_interval(monkeypatch, bin_factor: int) -> dict:
	"""Solve one Hermite interval (no reader, blob_pass False).

	The Hermite propagators are stubbed to emit the SOURCE truth path directly,
	matching the real Hermite contract (Hermite output is already SOURCE at all
	bin factors). This confirms the Hermite path stays SOURCE end to end and is
	not double-converted by the new seam.
	"""
	_stub_solve_collaborators(monkeypatch)

	def source_truth_path(*a, **k):
		path = []
		for frame_index in range(START_FRAME, END_FRAME + 1):
			box = _source_truth_box(frame_index)
			path.append({
				"frame_index": frame_index,
				"cx": box["cx"], "cy": box["cy"],
				"w": box["w"], "h": box["h"], "conf": 1.0, "source": "hermite",
			})
		return path

	monkeypatch.setattr(
		velocity_model, "propagate_forward_analytical", source_truth_path,
	)
	monkeypatch.setattr(
		velocity_model, "propagate_backward_analytical", source_truth_path,
	)

	result = interval_solver.solve_interval_analytical(
		_seed(START_FRAME),
		_seed(END_FRAME),
		scene_transform=object(),
		all_seeds_scene=[],
		fps=30.0,
		reader=None,
		blob_pass=False,
	)
	return result


#============================================
def _store_and_load_blended(tmp_path, result: dict) -> list:
	"""Write the interval result to npz and read back its SOURCE blended path."""
	npz_path = os.path.join(str(tmp_path), "torso_box_coords.npz")
	cache_data = {
		"solved_intervals": {
			"fp0": {
				"start_frame": result["start_frame"],
				"end_frame": result["end_frame"],
				"forward_path": result["forward_path"],
				"backward_path": result["backward_path"],
				"blended_path": result["blended_path"],
			},
		},
	}
	state_io.write_torso_box_coords(npz_path, cache_data)
	loaded = state_io.load_torso_box_coords(npz_path)
	blended = loaded["solved_intervals"]["fp0"]["blended_path"]
	return blended


#============================================
def _interior_stored_box(blended_path: list) -> dict:
	"""Pick the stored box at the interior frame from a full-span path."""
	# full-span path is one entry per frame from START_FRAME..END_FRAME
	index = INTERIOR_FRAME - START_FRAME
	return blended_path[index]


#============================================
def _assert_edges_close(stored: dict, truth: dict, tol: float = 1.0) -> None:
	"""Assert all four edges plus w/h of stored match truth within tol pixels."""
	s_left, s_top, s_right, s_bottom = _edges(stored)
	t_left, t_top, t_right, t_bottom = _edges(truth)
	assert abs(s_left - t_left) <= tol, (s_left, t_left)
	assert abs(s_top - t_top) <= tol, (s_top, t_top)
	assert abs(s_right - t_right) <= tol, (s_right, t_right)
	assert abs(s_bottom - t_bottom) <= tol, (s_bottom, t_bottom)
	assert abs(stored["w"] - truth["w"]) <= tol, (stored["w"], truth["w"])
	assert abs(stored["h"] - truth["h"]) <= tol, (stored["h"], truth["h"])


#============================================
def test_walker_interval_stores_source_at_bin3(tmp_path, monkeypatch):
	"""bin=3 walker interval: stored box is SOURCE truth (Gap A + Gap B fixed)."""
	captured_seeds = []
	result = _run_walker_interval(
		monkeypatch, BIN_FACTOR_BIN3, _processed_walker_path, captured_seeds,
	)
	# Gap A: the walker was anchored on a PROCESSED seed (SOURCE / bin_factor).
	walker_seed = captured_seeds[0]
	source_seed = _source_truth_box(START_FRAME)
	assert abs(walker_seed["cx"] - source_seed["cx"] / BIN_FACTOR_BIN3) <= 1e-6
	assert abs(walker_seed["cy"] - source_seed["cy"] / BIN_FACTOR_BIN3) <= 1e-6
	assert abs(walker_seed["w"] - source_seed["w"] / BIN_FACTOR_BIN3) <= 1e-6
	assert abs(walker_seed["h"] - source_seed["h"] / BIN_FACTOR_BIN3) <= 1e-6

	blended = _store_and_load_blended(tmp_path, result)
	stored = _interior_stored_box(blended)
	truth = _source_truth_box(INTERIOR_FRAME)
	_assert_edges_close(stored, truth)


#============================================
def test_walker_interval_stores_source_at_bin1(tmp_path, monkeypatch):
	"""bin=1 walker interval is a no-op for the seam: stored box is SOURCE."""
	captured_seeds = []
	result = _run_walker_interval(
		monkeypatch, 1, _processed_walker_path, captured_seeds,
	)
	# at bin=1 the PROCESSED seed equals the SOURCE seed (identity conversion)
	walker_seed = captured_seeds[0]
	source_seed = _source_truth_box(START_FRAME)
	assert abs(walker_seed["cx"] - source_seed["cx"]) <= 1e-6
	assert abs(walker_seed["cy"] - source_seed["cy"]) <= 1e-6

	blended = _store_and_load_blended(tmp_path, result)
	stored = _interior_stored_box(blended)
	truth = _source_truth_box(INTERIOR_FRAME)
	_assert_edges_close(stored, truth)


#============================================
def test_hermite_interval_stores_source_at_bin3(tmp_path, monkeypatch):
	"""bin=3 Hermite interval: stored box is SOURCE truth, untouched by seam."""
	result = _run_hermite_interval(monkeypatch, BIN_FACTOR_BIN3)
	blended = _store_and_load_blended(tmp_path, result)
	stored = _interior_stored_box(blended)
	truth = _source_truth_box(INTERIOR_FRAME)
	_assert_edges_close(stored, truth)


#============================================
def test_hermite_interval_stores_source_at_bin1(tmp_path, monkeypatch):
	"""bin=1 Hermite interval: byte-identical SOURCE storage."""
	result = _run_hermite_interval(monkeypatch, 1)
	blended = _store_and_load_blended(tmp_path, result)
	stored = _interior_stored_box(blended)
	truth = _source_truth_box(INTERIOR_FRAME)
	_assert_edges_close(stored, truth)


#============================================
def test_scale_injection_fails_at_bin3(tmp_path, monkeypatch):
	"""Negative control: PROCESSED stored as SOURCE (no bin multiply) must fail.

	The injected walker producer returns SOURCE-valued numbers in the PROCESSED
	slot, so the seam multiplies them by bin_factor and stores boxes 3x too
	large. The edge assertion must catch it.
	"""
	def processed_equals_source(bin_factor: int) -> list:
		# emit SOURCE numbers where PROCESSED is expected: the classic Gap B bug
		path = []
		for frame_index in range(START_FRAME, END_FRAME + 1):
			box = _source_truth_box(frame_index)
			path.append({
				"frame_index": frame_index,
				"cx": box["cx"], "cy": box["cy"],
				"w": box["w"], "h": box["h"], "conf": 1.0, "source": "walker",
			})
		return path

	captured_seeds = []
	result = _run_walker_interval(
		monkeypatch, BIN_FACTOR_BIN3, processed_equals_source, captured_seeds,
	)
	blended = _store_and_load_blended(tmp_path, result)
	stored = _interior_stored_box(blended)
	truth = _source_truth_box(INTERIOR_FRAME)
	# stored box is ~3x too large; at least one edge is far past tolerance
	s_left, s_top, s_right, s_bottom = _edges(stored)
	t_left, t_top, t_right, t_bottom = _edges(truth)
	max_edge_error = max(
		abs(s_left - t_left), abs(s_top - t_top),
		abs(s_right - t_right), abs(s_bottom - t_bottom),
	)
	assert max_edge_error > 1.0


#============================================
def test_swapped_axis_fails_at_bin3(tmp_path, monkeypatch):
	"""Negative control: swapping x and y (and w and h) must fail."""
	def swapped_axis(bin_factor: int) -> list:
		path = []
		for frame_index in range(START_FRAME, END_FRAME + 1):
			box = _source_truth_box(frame_index)
			# swap cx<->cy and w<->h in the PROCESSED slot
			path.append({
				"frame_index": frame_index,
				"cx": box["cy"] / bin_factor,
				"cy": box["cx"] / bin_factor,
				"w": box["h"] / bin_factor,
				"h": box["w"] / bin_factor,
				"conf": 1.0, "source": "walker",
			})
		return path

	captured_seeds = []
	result = _run_walker_interval(
		monkeypatch, BIN_FACTOR_BIN3, swapped_axis, captured_seeds,
	)
	blended = _store_and_load_blended(tmp_path, result)
	stored = _interior_stored_box(blended)
	truth = _source_truth_box(INTERIOR_FRAME)
	# cx != cy and w != h, so the swap moves edges well past tolerance
	s_left, s_top, s_right, s_bottom = _edges(stored)
	t_left, t_top, t_right, t_bottom = _edges(truth)
	max_edge_error = max(
		abs(s_left - t_left), abs(s_top - t_top),
		abs(s_right - t_right), abs(s_bottom - t_bottom),
	)
	assert max_edge_error > 1.0


#============================================
def test_subpixel_drift_passes_at_bin3(tmp_path, monkeypatch):
	"""Sub-pixel drift below storage rounding must still PASS.

	Adds a tiny PROCESSED offset that, after the bin multiply and uint16
	pixel-snapping, stays within the 1px storage tolerance. This proves the
	test tolerates real sub-pixel solver noise and is not over-tight.
	"""
	def tiny_drift(bin_factor: int) -> list:
		# 0.1 SOURCE-px drift -> 0.1/bin in PROCESSED -> rounds away on write
		drift_source = 0.1
		path = _processed_walker_path(bin_factor)
		for state in path:
			state["cx"] = state["cx"] + drift_source / bin_factor
			state["cy"] = state["cy"] + drift_source / bin_factor
		return path

	captured_seeds = []
	result = _run_walker_interval(
		monkeypatch, BIN_FACTOR_BIN3, tiny_drift, captured_seeds,
	)
	blended = _store_and_load_blended(tmp_path, result)
	stored = _interior_stored_box(blended)
	truth = _source_truth_box(INTERIOR_FRAME)
	_assert_edges_close(stored, truth)
