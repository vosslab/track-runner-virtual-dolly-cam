"""Deterministic containment and fallback guards for ``crop_mode: dolly``."""

# Standard Library
import pathlib
import types

# PIP3 modules
import numpy
import pytest
import yaml

# local repo modules
import dolly_path
import encode_analysis_report
import modes.analyze as analyze_mode
import modes.encode as encode_mode
import modes.shared as mode_shared
import tr_config
import tr_crop


#============================================
def _video_info(n: int) -> dict:
	return {"width": 100, "height": 100, "frame_count": n, "fps": 30.0}


#============================================
def _state(cx: float, cy: float, conf: float = 1.0) -> dict:
	return {"cx": cx, "cy": cy, "w": 10.0, "h": 10.0, "conf": conf}


#============================================
def _dolly_config() -> dict:
	return {
		"processing": {
			"crop_mode": "dolly",
			"crop_aspect": "1:1",
			"torso_height_multiple": 4.0,
			"crop_containment_radius": 0.20,
			"crop_centered_fit_to_source": True,
			"crop_dolly_smoothness": 4.0,
		},
	}


#============================================
def test_dolly_rasterization_places_the_rounded_size_about_the_solved_center() -> None:
	"""Integer size selection must not add a second center-rounding error."""
	center_x = numpy.asarray([100.21, 200.79])
	center_y = numpy.asarray([300.21, 400.79])
	crop_w = numpy.asarray([1.43, 9.57])
	crop_h = numpy.asarray([3.43, 11.57])
	rects = tr_crop._rasterize_dolly_rects(
		center_x, center_y, crop_w, crop_h,
	)
	for i, (x, y, width, height) in enumerate(rects):
		assert width == round(crop_w[i])
		assert height == round(crop_h[i])
		assert abs((x + width / 2.0) - center_x[i]) <= 0.5
		assert abs((y + height / 2.0) - center_y[i]) <= 0.5


#============================================
def test_dolly_re_solves_after_source_fit_pin(monkeypatch: pytest.MonkeyPatch) -> None:
	"""A fit-bound frame is pinned and needs a second whole-path solve."""
	trajectory = [_state(10.0, 50.0) for _ in range(5)]
	calls = []
	real_solve = tr_crop.dolly_path.solve_dolly_path

	def recording_solve(targets: list, weights: list, smoothness: float) -> dolly_path.DollyPath:
		calls.append((targets, weights))
		return real_solve(targets, weights, smoothness)

	monkeypatch.setattr(tr_crop.dolly_path, "solve_dolly_path", recording_solve)
	rects, report = tr_crop.trajectory_to_crop_rects(
		trajectory, _video_info(5), _dolly_config(), return_dolly_report=True,
	)

	assert report == tr_crop.DollyCropReport(True, 2, False)
	assert len(calls) == 2
	# The crop remains centered at x=10 and shrinks to its 20px source-fit
	# ceiling rather than sliding outward or producing a black border.
	assert all(rect[0] == 0 and rect[2] == 20 for rect in rects)
	assert all(weights[0] == tr_crop.DOLLY_PIN_WEIGHT for _, weights in calls[1:])


#============================================
def test_dolly_ten_pass_cap_settles_a_bounded_pin_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Exercise the bounded pin loop and smooth-fallback plumbing deterministically."""
	trajectory = [_state(50.0, 50.0)]
	config = _dolly_config()

	class FakePath:
		def __init__(self, crop_h: float) -> None:
			self.center_x = numpy.array([50.0])
			self.center_y = numpy.array([50.0])
			self.log_size = numpy.array([numpy.log(crop_h)])

	def run_with_cap(cap: int) -> tuple:
		calls = 0

		def fake_solve(targets: list, weights: list, smoothness: float) -> FakePath:
			nonlocal calls
			calls += 1
			return FakePath(80.0 if calls < 10 else 100.0)

		def fake_containment(
			raw_cx: numpy.ndarray, raw_cy: numpy.ndarray,
			center_x: numpy.ndarray, center_y: numpy.ndarray,
			crop_h: numpy.ndarray, frame_width: int, frame_height: int,
			aspect_ratio: float, containment_radius: float, fit_to_source: bool,
			use_rolling_min_ceiling: bool,
		) -> tuple:
			# The first nine solves pin an undersized crop to the source-fit
			# ceiling. The tenth accepts that pinned target unchanged.
			settled = bool(calls >= 10)
			return (
				numpy.array([50.0]), numpy.array([50.0]), numpy.array([100.0]),
				numpy.array([100.0]), numpy.array([not settled]),
			)

		monkeypatch.setattr(tr_crop, "DOLLY_MAX_CONTAINMENT_ITERATIONS", cap)
		monkeypatch.setattr(tr_crop.dolly_path, "solve_dolly_path", fake_solve)
		monkeypatch.setattr(tr_crop, "_apply_dolly_containment", fake_containment)
		return tr_crop.dolly_crop_trajectory(trajectory, 100, 100, config)

	too_early_rects, too_early_report = run_with_cap(8)
	assert too_early_rects is None
	assert too_early_report == tr_crop.DollyCropReport(False, 8, True)

	rects, report = run_with_cap(10)
	assert report == tr_crop.DollyCropReport(True, 10, False)
	assert rects == [(0, 0, 100, 100)]
	# The converged fixed point still obeys source fit and torso containment.
	assert rects[0][0] >= 0 and rects[0][1] >= 0
	assert rects[0][0] + rects[0][2] <= 100
	assert rects[0][1] + rects[0][3] <= 100
	assert numpy.hypot(
		(trajectory[0]["cx"] - 50.0) / 100.0,
		(trajectory[0]["cy"] - 50.0) / 100.0,
	) <= config["processing"]["crop_containment_radius"]


#============================================
def test_dolly_center_containment_is_satisfied_after_fixed_point() -> None:
	"""The returned solved path stays inside the existing torso-relative radius."""
	trajectory = [
		_state(15.0 if frame < 3 else 85.0, 50.0)
		for frame in range(7)
	]
	config = _dolly_config()
	config["processing"]["crop_centered_fit_to_source"] = False
	rects, report = tr_crop.trajectory_to_crop_rects(
		trajectory, _video_info(7), config, return_dolly_report=True,
	)

	assert report.converged
	for state, rect in zip(trajectory, rects):
		cx = rect[0] + rect[2] / 2.0
		cy = rect[1] + rect[3] / 2.0
		offset = numpy.hypot(
			(state["cx"] - cx) / rect[2],
			(state["cy"] - cy) / rect[3],
		)
		# Integer rectangle rounding has at most half a source pixel of error.
		assert offset <= 0.20 + 0.04


#============================================
def test_dolly_nonconvergence_reports_and_uses_current_smooth_fallback(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Bound exhaustion returns provenance and the unchanged smooth-mode output."""
	trajectory = [_state(10.0, 50.0) for _ in range(5)]
	config = _dolly_config()
	monkeypatch.setattr(tr_crop, "DOLLY_MAX_CONTAINMENT_ITERATIONS", 1)

	rects, report = tr_crop.trajectory_to_crop_rects(
		trajectory, _video_info(5), config, return_dolly_report=True,
	)
	smooth_config = _dolly_config()
	smooth_config["processing"]["crop_mode"] = "smooth"
	expected = tr_crop.trajectory_to_crop_rects(
		trajectory, _video_info(5), smooth_config,
	)

	assert report == tr_crop.DollyCropReport(False, 1, True)
	assert rects == expected


#============================================
def test_default_crop_mode_is_the_adopted_dolly_path() -> None:
	"""A missing mode and the shipped template use the adopted whole-path solve."""
	trajectory = [_state(45.0 + frame, 50.0) for frame in range(5)]
	default_config = {
		"processing": {"crop_aspect": "1:1", "torso_height_multiple": 4.0},
	}
	_, report = tr_crop.trajectory_to_crop_rects(
		trajectory, _video_info(5), default_config, return_dolly_report=True,
	)
	assert report is not None and report.converged and not report.fallback_used
	assert tr_config.read_default_config()["processing"]["crop_mode"] == "dolly"


#============================================
def test_dolly_requires_owner_confidence() -> None:
	"""Dolly must not replace a missing confidence-owner value with 1.0."""
	state = _state(50.0, 50.0)
	del state["conf"]
	with pytest.raises(KeyError, match="conf"):
		tr_crop.trajectory_to_crop_rects(
			[state], _video_info(1), _dolly_config(),
		)


#============================================
@pytest.mark.parametrize("report", [
	tr_crop.DollyCropReport(True, 2, False),
	tr_crop.DollyCropReport(False, 8, True),
])
def test_encode_and_analyze_helpers_retain_dolly_report(
	monkeypatch: pytest.MonkeyPatch,
	report: tr_crop.DollyCropReport,
) -> None:
	"""Both live mode callers request and preserve normal/fallback provenance."""
	rects = [(1, 2, 30, 30)]
	calls = []

	def crop_spy(*args, **kwargs) -> tuple:
		calls.append(kwargs)
		return (rects, report)

	monkeypatch.setattr(tr_crop, "trajectory_to_crop_rects", crop_spy)
	config = {"processing": {"crop_mode": "dolly"}}
	info = _video_info(1)
	assert encode_mode._compute_crop_trajectory([], info, config, set()) == (rects, report)
	assert analyze_mode._compute_crop_trajectory([], info, config, set()) == (rects, report)
	assert all(call["return_dolly_report"] is True for call in calls)


#============================================
def test_nif_crop_inputs_keep_runner_absent_and_match_mode_crops() -> None:
	"""NIF edge anchors are crop-only while Analyze and Encode share them."""
	trajectory = [
		{"cx": 95, "cy": 50, "w": 10, "h": 10, "conf": 1.0},
		None,
		None,
		None,
		{"cx": 50, "cy": 60, "w": 10, "h": 10, "conf": 1.0},
	]
	seeds = [
		{"frame_index": 0, "status": "visible"},
		{"frame_index": 2, "status": "not_in_frame"},
		{"frame_index": 4, "status": "visible"},
	]
	info = _video_info(5)
	crop_trajectory, nif_frames = mode_shared.build_nif_crop_inputs(
		trajectory, seeds, {}, info,
	)
	config = {"processing": {"crop_mode": "direct_center", "crop_aspect": "1:1"}}

	assert trajectory[2] is None and crop_trajectory[2]["source"] == "nif_edge_anchor" and 2 in nif_frames
	assert analyze_mode._compute_crop_trajectory(
		crop_trajectory, info, config, nif_frames,
	) == encode_mode._compute_crop_trajectory(
		crop_trajectory, info, config, nif_frames,
	)


#============================================
def test_nif_crop_inputs_erase_entire_sparse_bracket_from_runner_truth() -> None:
	"""One sparse NIF seed erases every strict bracket frame, not one second."""
	trajectory = [_state(50.0, 50.0) for _ in range(101)]
	trajectory[0] = _state(95.0, 50.0)
	trajectory[100] = _state(50.0, 60.0)
	seeds = [
		{"frame_index": 0, "status": "visible"},
		{"frame_index": 50, "status": "not_in_frame"},
		{"frame_index": 100, "status": "visible"},
	]

	crop_trajectory, nif_frames = mode_shared.build_nif_crop_inputs(
		trajectory, seeds, {}, _video_info(101),
	)

	assert nif_frames == set(range(1, 100))
	assert all(state is None for state in trajectory[1:100])
	assert trajectory[0] is not None and trajectory[100] is not None
	assert all(crop_trajectory[index]["source"] == "nif_edge_anchor"
		for index in nif_frames)


#============================================
class _NifCropStop(Exception):
	"""Stop a mode after its crop-input seam has executed."""


#============================================
def _assert_mode_calls_nif_crop_builder(
	monkeypatch: pytest.MonkeyPatch,
	mode: object,
	args: types.SimpleNamespace,
) -> None:
	"""Exercise one mode through crop-input construction with memory-only IO."""
	seeds = [
		{"frame_index": 0, "status": "visible"},
		{"frame_index": 2, "status": "not_in_frame"},
		{"frame_index": 4, "status": "visible"},
	]
	trajectory = [_state(50.0, 50.0) for _ in range(5)]
	calls = []

	def crop_input_spy(*crop_args: object) -> tuple:
		calls.append(crop_args)
		return (trajectory, {1, 2, 3})

	def stop_after_crop(*crop_args: object) -> tuple:
		raise _NifCropStop()

	monkeypatch.setattr(mode.os.path, "isfile", lambda path: True)
	monkeypatch.setattr(mode.state_io, "load_interval_scores", lambda path: {"fps": 30.0})
	monkeypatch.setattr(mode.torso_box_coords_io, "load_torso_box_coords", lambda path: {
		"solved_intervals": {"interval": {"start_frame": 0}},
	})
	monkeypatch.setattr(mode.state_io, "load_seeds", lambda path: {"seeds": seeds})
	monkeypatch.setattr(mode.tr_paths, "default_seeds_path", lambda path: "seeds")
	monkeypatch.setattr(
		mode.interval_solver, "reconstruct_trajectory_with_confidence",
		lambda results: trajectory,
	)
	monkeypatch.setattr(mode.interval_solver, "anchor_to_seeds", lambda path, records: path)
	monkeypatch.setattr(mode.interval_solver, "_stamp_seed_truth", lambda path, records: path)
	monkeypatch.setattr(mode.modes.shared, "build_nif_crop_inputs", crop_input_spy)
	monkeypatch.setattr(mode, "_compute_crop_trajectory", stop_after_crop)
	if mode is encode_mode:
		monkeypatch.setattr(mode.modes.shared, "_resolve_workers", lambda parsed: 1)

	with pytest.raises(_NifCropStop):
		mode.run(args, {"processing": {"crop_mode": "direct_center"}}, _video_info(5), "diag", "intervals")
	assert calls and calls[0][1] == seeds


#============================================
def test_analyze_and_encode_entrypoints_build_nif_crop_inputs(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Both modes reach the crop-only NIF builder before crop rendering."""
	analyze_args = types.SimpleNamespace(
		input_file="input", aspect=None, write_plots=False,
	)
	encode_args = types.SimpleNamespace(
		input_file="input", aspect=None, torso_multiple=None,
		output_resolution=None, crf=None, video_codec=None,
		draw_tracking_overlay=False, draw_debug_overlay=False,
		draw_velocity_arrow=False,
	)
	_assert_mode_calls_nif_crop_builder(monkeypatch, analyze_mode, analyze_args)
	_assert_mode_calls_nif_crop_builder(monkeypatch, encode_mode, encode_args)


@pytest.mark.parametrize("report", [
	{"converged": True, "iterations": 2, "fallback_used": False},
	{"converged": False, "iterations": 8, "fallback_used": True},
])
def test_analyze_yaml_records_normal_and_fallback_dolly_provenance(
	tmp_path: pathlib.Path,
	report: dict,
) -> None:
	"""The existing per-clip analysis artifact preserves crop-solve provenance."""
	analysis = {
		"summary": {"frames": 1, "duration_s": 0.0, "fps": 30.0, "output_size": [30, 30]},
		"motion_stability": {"quantization_chatter_fraction": 0.0},
		"confidence": {}, "instability_regions": [], "dominant_symptom": "none",
		"seed_suggestions": [],
	}
	solver_context = {
		"seed_density": 0.0, "desert_count": 0, "seed_gap_mean_s": 0.0,
		"seed_gap_max_s": 0.0, "top_seed_gaps": [],
	}
	path = tmp_path / "clip.encode_analysis.yaml"
	encode_analysis_report.write_analysis_yaml(
		analysis, solver_context, str(path), dolly_crop_report=report,
	)
	with open(path) as handle:
		doc = yaml.safe_load(handle)
	assert doc["dolly_crop_report"] == report
