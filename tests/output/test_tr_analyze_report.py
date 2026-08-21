"""Tests for track_runner.analyze_report (Groups A-G: HTML structure, JSON
schema, helpers, panel data builders, end-to-end degradation cases,
and integration tests via direct call to the analyze-mode plot path)."""

# Standard Library
import json
import pathlib
import re
import types

# PIP3 modules
import numpy
import pytest

# local repo modules (track_runner/ is on sys.path via tests/conftest.py)
import analyze_report
import analyze_report_fixtures
import modes.analyze as analyze_mode


#============================================
def test_attach_interval_scores_pairs_geometry_by_frame_range() -> None:
	"""Analyze receives score data that corresponds to every NPZ interval."""
	geometry = [{"start_frame": 10, "end_frame": 20, "blended_path": []}]
	score = {"confidence_tier": "high"}
	scores = {
		"intervals": [{
			"start_frame": 10,
			"end_frame": 20,
			"interval_score": score,
		}],
	}

	attached = analyze_mode._attach_interval_scores(geometry, scores)

	assert attached[0]["interval_score"] is score


#============================================
def test_attach_interval_scores_rejects_unscored_geometry() -> None:
	"""Analyze fails clearly instead of treating missing scores as current."""
	with pytest.raises(RuntimeError, match="lacks current interval scores"):
		analyze_mode._attach_interval_scores(
			[{"start_frame": 10, "end_frame": 20}], {"intervals": []},
		)


#============================================
def _call_write(
	monkeypatch: pytest.MonkeyPatch,
	tmp_path: pathlib.Path,
	**overrides,
) -> tuple[pathlib.Path, str]:
	"""Build standard kwargs for write_analyze_report and call it.

	Monkeypatches _load_renderer_js so tests do not depend on the on-disk
	asset file. Returns (out_path, body_text) so callers can assert on either.
	"""
	monkeypatch.setattr(analyze_report, '_load_renderer_js', lambda: '/* test stub */')
	kwargs = {
		'out_path': tmp_path / 'foo.encode_analysis.html',
		'video_stem': 'foo',
		'trajectory': [],
		'crop_rects': [],
		'motion_track': None,
		'scene_transform': None,
		'fps': None,
		'config': None,
		'warnings': [],
	}
	kwargs.update(overrides)
	out = analyze_report.write_analyze_report(**kwargs)
	body = out.read_text(encoding='ascii')
	return out, body


#============================================
def _extract_embedded_json(body: str) -> dict:
	"""Extract and parse the embedded JSON payload from the HTML body.

	The closing </script> tag is always emitted on its own line (newline-
	joined output in _write_analyze_report_html), so we anchor on \\n</script>
	to handle JSON content that itself contains </script> substrings (the
	HTML-escape test deliberately exercises this).
	"""
	match = re.search(
		r'<script type="application/json"[^>]*>(.*?)\n</script>',
		body,
		re.DOTALL,
	)
	assert match is not None, 'JSON script block not found in HTML'
	return json.loads(match.group(1))


#============================================
# Group A: HTML structural tests (WP-1C-1)

def test_writes_html_at_supplied_out_path(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Returned path equals out_path; file exists; parent dir created on demand."""
	# use a sub-directory that does not pre-exist
	out_path = tmp_path / 'sub' / 'foo.encode_analysis.html'
	out, _body = _call_write(monkeypatch, tmp_path, out_path=out_path)
	assert out == out_path
	assert out_path.exists()


def test_html_contains_required_structural_markers(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""HTML body contains each required structural marker (presence only)."""
	# Provide synthetic inputs so all four panels render. Previously this
	# test was satisfied by an unconditional four-canvas stub; Patch 6
	# emits canvases only for panels that have data.
	import types as _types
	trajectory = [{'cx': 100.0, 'cy': 50.0, 'w': 20.0, 'h': 40.0}] * 3
	crop_rects = [(0, 0, 200, 200)] * 3
	mt = _types.SimpleNamespace(
		dx=numpy.array([0.0] * 3),
		dy=numpy.array([0.0] * 3),
		scale=numpy.array([1.0] * 3),
		quality=numpy.array([1.0] * 3),
	)
	_out, body = _call_write(
		monkeypatch, tmp_path,
		trajectory=trajectory, crop_rects=crop_rects,
		motion_track=mt, scene_transform=_IdentitySceneTransform(),
		fps=30.0, config={'processing': {'torso_height_multiple': 5.0}},
	)

	# each marker must be present (no ordering checks here)
	assert '<h1>' in body
	assert '<section class="references">' in body
	assert '<canvas id="zoom-stability">' in body
	assert '<canvas id="zoom-multiple">' in body
	assert '<canvas id="camera-motion">' in body
	assert '<canvas id="runner-speed">' in body
	assert '<script type="application/json" id="encode-analysis-data">' in body
	# inlined renderer <script> block (any plain <script> tag without attributes)
	assert '<script>' in body


def test_canvas_ids_appear_in_spec_order(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""The four canvas IDs appear in the required document order."""
	# Provide synthetic inputs so all four panels render.
	import types as _types
	trajectory = [{'cx': 100.0, 'cy': 50.0, 'w': 20.0, 'h': 40.0}] * 3
	crop_rects = [(0, 0, 200, 200)] * 3
	mt = _types.SimpleNamespace(
		dx=numpy.array([0.0] * 3),
		dy=numpy.array([0.0] * 3),
		scale=numpy.array([1.0] * 3),
		quality=numpy.array([1.0] * 3),
	)
	_out, body = _call_write(
		monkeypatch, tmp_path,
		trajectory=trajectory, crop_rects=crop_rects,
		motion_track=mt, scene_transform=_IdentitySceneTransform(),
		fps=30.0, config={'processing': {'torso_height_multiple': 5.0}},
	)

	# four canvas markers must appear in spec order
	pos_zoom_stab = body.find('<canvas id="zoom-stability">')
	pos_zoom_mult = body.find('<canvas id="zoom-multiple">')
	pos_cam_motion = body.find('<canvas id="camera-motion">')
	pos_runner_spd = body.find('<canvas id="runner-speed">')
	assert pos_zoom_stab < pos_zoom_mult < pos_cam_motion < pos_runner_spd, (
		'canvas IDs must appear in spec order: '
		'zoom-stability, zoom-multiple, camera-motion, runner-speed'
	)


def test_renderer_script_follows_json_data_block(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""The inlined renderer <script> block appears after the JSON data block."""
	_out, body = _call_write(monkeypatch, tmp_path)

	json_block_pos = body.find('<script type="application/json" id="encode-analysis-data">')
	renderer_script_pos = body.find('<script>', json_block_pos + 1)
	assert renderer_script_pos > json_block_pos, (
		'inline renderer <script> must appear after the JSON data block'
	)


def test_html_contains_video_stem_in_h1(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Stem appears immediately after the <h1> opening tag."""
	stem = 'my_race_video'
	_out, body = _call_write(monkeypatch, tmp_path, video_stem=stem)
	# The body looks like '<h1>Analyze diagnostic report: my_race_video</h1>'
	# We assert the stem appears in the H1 region by searching for it
	# between '<h1>' and '</h1>'.
	assert '<h1>' in body
	h1_content = body.split('<h1>', 1)[1].split('</h1>', 1)[0]
	assert stem in h1_content


#============================================
# Group B: JSON parse, no-external-URL, and ASCII-only tests (WP-1C-2)

def test_embedded_json_is_valid_json(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""The embedded JSON block is valid JSON with required M1 keys."""
	_out, body = _call_write(monkeypatch, tmp_path, fps=30.0)
	data = _extract_embedded_json(body)
	# required keys
	assert 'video_stem' in data
	assert 'fps' in data
	assert 'frames' in data
	assert 'panels' in data
	assert 'warnings' in data
	# M1 stub: panels must be empty list
	assert data['panels'] == []


def test_html_contains_no_external_url(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""HTML must be self-contained: no http:// or https:// URLs."""
	_out, body = _call_write(monkeypatch, tmp_path)
	assert 'http://' not in body
	assert 'https://' not in body


def test_html_is_ascii_only(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""HTML output must be ASCII-only (no non-ASCII bytes)."""
	_out, body = _call_write(monkeypatch, tmp_path)
	# encode('ascii') raises UnicodeEncodeError if any non-ASCII byte is present
	body.encode('ascii')


#============================================
# Group C: Warnings tests (WP-1C-3)

def test_warnings_section_omitted_when_warnings_empty(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Warnings section must not appear when warnings=[]."""
	_out, body = _call_write(monkeypatch, tmp_path, warnings=[])
	assert '<section class="warnings">' not in body


def test_warnings_section_present_when_warnings_supplied(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Warnings section appears and the warning text is present in body and JSON."""
	warning_text = 'Camera-motion data unavailable; camera and speed panels were skipped.'
	_out, body = _call_write(monkeypatch, tmp_path, warnings=[warning_text])

	# section tag present
	assert '<section class="warnings">' in body
	# warning text appears in an <li> in the body
	assert warning_text in body

	# warning text also appears in the embedded JSON warnings array
	data = _extract_embedded_json(body)
	assert warning_text in data['warnings']


def test_warnings_html_escaped(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Warning strings are HTML-escaped in <li> markup but stored raw in JSON."""
	raw_warning = '<script>alert(1)</script> & <b>bold</b>'
	escaped_warning = '&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;bold&lt;/b&gt;'
	_out, body = _call_write(monkeypatch, tmp_path, warnings=[raw_warning])

	# the HTML-escaped form must appear inside an <li> element in the body
	assert f'<li>{escaped_warning}</li>' in body

	# JSON data must store the raw unescaped string (JSON uses its own encoding)
	data = _extract_embedded_json(body)
	assert raw_warning in data['warnings']


#============================================
# Group D: Patch 5 helpers (WP-2B-1 through WP-2B-4)

# _IdentitySceneTransform is shared with Group G via analyze_report_fixtures
_IdentitySceneTransform = analyze_report_fixtures._IdentitySceneTransform


def test_running_mean_nan_aware_constant_input_passes_through() -> None:
	"""Constant input -> smoothed output equals raw within float epsilon."""
	values = numpy.full(20, 7.0)
	smoothed = analyze_report._running_mean_nan_aware(values, window=5)
	assert numpy.allclose(smoothed, values)


def test_running_mean_nan_aware_single_nan_does_not_blank_window() -> None:
	"""A single NaN in a window does NOT blank the entire window output."""
	values = numpy.array([1.0, 1.0, numpy.nan, 1.0, 1.0])
	smoothed = analyze_report._running_mean_nan_aware(values, window=3)
	# Output index 2 has neighbors [1.0, nan, 1.0]; mean of non-nan = 1.0
	assert smoothed[2] == 1.0


def test_running_mean_nan_aware_all_nan_window_yields_nan() -> None:
	"""A window with zero non-NaN elements yields nan."""
	values = numpy.array([numpy.nan, numpy.nan, numpy.nan])
	smoothed = analyze_report._running_mean_nan_aware(values, window=3)
	assert numpy.isnan(smoothed).all()


def test_runner_speed_per_frame_uses_cx_cy_when_present() -> None:
	"""Production-path trajectories use cx/cy directly; verify they are read."""
	# Constant-velocity in scene-coord space: 3.0 units per frame in x.
	trajectory = [
		{'cx': 100.0 + 3.0 * i, 'cy': 50.0, 'w': 20.0, 'h': 40.0}
		for i in range(10)
	]

	speed = analyze_report._runner_speed_per_frame(trajectory, _IdentitySceneTransform())
	assert numpy.isnan(speed[0])
	# Frames 1..9 should all show speed 3.0 in scene-units / frame
	assert numpy.allclose(speed[1:], 3.0)


def test_runner_speed_per_frame_constant_velocity() -> None:
	"""Constant scene velocity -> per-frame raw speed array is approximately constant.

	Synthetic trajectory of 30 frames where the torso center advances by
	(2.0, 1.5) scene units per frame. The identity SceneTransform stub returns
	(px, py) unchanged, so scene coords equal pixel coords.

	Spec: RMS deviation about the mean is < 5% of the mean.
	"""
	# build a synthetic trajectory with constant velocity using x/y/w/h keys
	trajectory = []
	for i in range(30):
		trajectory.append({
			'x': 100.0 + 2.0 * i,
			'y':  50.0 + 1.5 * i,
			'w':  20.0,
			'h':  40.0,
		})

	speed = analyze_report._runner_speed_per_frame(trajectory, _IdentitySceneTransform())

	# speed[0] is nan; rest should be approximately sqrt(2^2 + 1.5^2) = 2.5
	finite = speed[1:]
	assert numpy.isfinite(finite).all()
	mean = numpy.mean(finite)
	rms_deviation = numpy.sqrt(numpy.mean((finite - mean) ** 2))
	assert rms_deviation < 0.05 * mean


def test_runner_speed_per_frame_handles_missing_frame_data() -> None:
	"""Frames with missing torso geometry produce nan in the output."""
	# Frame 0: present. Frame 1: missing (None). Frame 2: present.
	trajectory = [
		{'x': 100.0, 'y': 50.0, 'w': 20.0, 'h': 40.0},
		None,
		{'x': 110.0, 'y': 55.0, 'w': 20.0, 'h': 40.0},
	]

	speed = analyze_report._runner_speed_per_frame(trajectory, _IdentitySceneTransform())
	assert len(speed) == 3
	# speed[1] is nan (missing frame); speed[2] is nan because previous frame was missing
	assert numpy.isnan(speed[1])
	assert numpy.isnan(speed[2])


def test_runner_speed_panel_data_shape() -> None:
	"""Returns a panel dict with the documented schema keys and series."""
	trajectory = [
		{'x': 100.0, 'y': 50.0, 'w': 20.0, 'h': 40.0},
		{'x': 102.0, 'y': 51.5, 'w': 20.0, 'h': 40.0},
		{'x': 104.0, 'y': 53.0, 'w': 20.0, 'h': 40.0},
	]

	panel = analyze_report._runner_speed_panel_data(trajectory, _IdentitySceneTransform(), fps=30.0)
	assert panel['id'] == 'runner-speed'
	assert panel['y_right_label'] == 'speed (scene units / second)'
	# Two series (raw + smoothed); names are renderer contract, not test contract
	assert len(panel['series']) == 2
	# all series values length == trajectory length
	for series in panel['series']:
		assert len(series['values']) == len(trajectory)
	# first entry of the raw series is None (no previous frame)
	assert panel['series'][0]['values'][0] is None


def test_runner_speed_panel_data_no_fps_omits_secondary_axis() -> None:
	"""When fps is None or non-positive, y_right_label is None."""
	trajectory = [{'x': 0.0, 'y': 0.0, 'w': 1.0, 'h': 1.0}]

	for fps in (None, 0, -1.0):
		panel = analyze_report._runner_speed_panel_data(trajectory, _IdentitySceneTransform(), fps=fps)
		assert panel['y_right_label'] is None, f'expected None for fps={fps}'


#============================================
# Group E: Patch 4 panel data builders (WP-2A-4)

def test_zoom_stability_smoothed_passes_through_when_input_smooth() -> None:
	"""Constant crop_h -> smoothed series equals raw within float epsilon."""
	# 30-frame trajectory with constant torso geometry, constant crop
	trajectory = [{'cx': 100.0, 'cy': 50.0, 'w': 20.0, 'h': 40.0}] * 30
	crop_rects = [(0, 0, 200, 480)] * 30   # constant crop_h=480

	panel = analyze_report._zoom_stability_panel_data(trajectory, crop_rects)
	raw = panel['series'][0]['values']
	smoothed = panel['series'][1]['values']
	# Smoothed should equal raw (modulo edge effects: nanmean of constant = constant)
	assert all(s == r for s, r in zip(smoothed, raw))


def test_zoom_stability_smoothed_attenuates_alternating_input() -> None:
	"""Zoom-bounce signal: alternating crop_h -> smoothed values sit between extremes."""
	# Alternating crop_h: 400, 500, 400, 500, ...  -> smoothed should approach 450
	trajectory = [{'cx': 100.0, 'cy': 50.0, 'w': 20.0, 'h': 40.0}] * 30
	crop_rects = [(0, 0, 200, 400 if i % 2 == 0 else 500) for i in range(30)]

	panel = analyze_report._zoom_stability_panel_data(trajectory, crop_rects)
	raw = panel['series'][0]['values']
	smoothed = panel['series'][1]['values']
	# Far from the edges (frames 15-25), smoothed should be close to 450
	# (mean of alternating 400/500 over a 9-frame window)
	for i in range(15, 25):
		assert 440 < smoothed[i] < 460, f'frame {i}: raw={raw[i]} smoothed={smoothed[i]}'
		assert raw[i] in (400, 500)


def test_zoom_stability_panel_shape() -> None:
	"""Returns documented schema with four series spanning twin axes."""
	trajectory = [{'cx': 100.0, 'cy': 50.0, 'w': 20.0, 'h': 40.0}] * 5
	crop_rects = [(0, 0, 200, 480)] * 5

	panel = analyze_report._zoom_stability_panel_data(trajectory, crop_rects)
	assert panel['id'] == 'zoom-stability'
	# Four series (raw + smoothed crop_h on left axis; torso_h, torso_w on right).
	# Names are renderer contract, not test contract; assert behavior via axis split.
	assert len(panel['series']) == 4
	left_count = sum(1 for s in panel['series'] if s['axis'] == 'left')
	right_count = sum(1 for s in panel['series'] if s['axis'] == 'right')
	assert left_count == 2
	assert right_count == 2


def test_zoom_multiple_constant_input_flat_achieved_multiple() -> None:
	"""When crop_h and torso_h scale by the same factor, achieved is constant."""
	trajectory = [{'cx': 100.0, 'cy': 50.0, 'w': 20.0, 'h': 40.0}] * 10
	crop_rects = [(0, 0, 200, 200)] * 10  # crop_h = 200, torso_h = 40, ratio = 5

	panel = analyze_report._zoom_multiple_panel_data(trajectory, crop_rects, config=None)
	values = panel['series'][0]['values']
	assert all(v == 5.0 for v in values)


def test_zoom_multiple_omits_reference_lines_when_config_missing_key() -> None:
	"""When config does not provide torso_height_multiple, reference_lines key is absent."""
	trajectory = [{'cx': 100.0, 'cy': 50.0, 'w': 20.0, 'h': 40.0}]
	crop_rects = [(0, 0, 200, 200)]

	panel = analyze_report._zoom_multiple_panel_data(trajectory, crop_rects, config=None)
	assert 'reference_lines' not in panel

	# Same when config is an empty dict
	panel = analyze_report._zoom_multiple_panel_data(trajectory, crop_rects, config={})
	assert 'reference_lines' not in panel

	# Nested-but-empty processing dict: production nested-key fallback should also reject
	panel = analyze_report._zoom_multiple_panel_data(
		trajectory, crop_rects,
		config={'processing': {}},
	)
	assert 'reference_lines' not in panel


def test_zoom_multiple_emits_reference_line_when_config_provides_value() -> None:
	"""When the configured value is present, a reference_lines entry is emitted."""
	trajectory = [{'cx': 100.0, 'cy': 50.0, 'w': 20.0, 'h': 40.0}]
	crop_rects = [(0, 0, 200, 200)]
	# The production config nests torso_height_multiple under 'processing'.
	# This test uses that nested path to exercise the real code path.
	config = {'processing': {'torso_height_multiple': 5.0}}

	panel = analyze_report._zoom_multiple_panel_data(trajectory, crop_rects, config=config)
	assert 'reference_lines' in panel
	# At least one reference line for the configured value (allow future
	# additions like warning bands without breaking this test).
	assert len(panel['reference_lines']) >= 1
	ref = panel['reference_lines'][0]
	assert ref['axis'] == 'left'
	assert ref['value'] == 5.0


def test_camera_motion_constant_dxy_flat_magnitude() -> None:
	"""Constant-dx MotionTrack -> constant camera_dxy series."""
	mt = types.SimpleNamespace(
		dx=numpy.array([3.0] * 10),
		dy=numpy.array([4.0] * 10),
		scale=numpy.array([1.0] * 10),
		quality=numpy.array([0.9] * 10),
	)
	panel = analyze_report._camera_motion_panel_data(mt)
	camera_dxy = panel['series'][0]['values']
	# hypot(3, 4) = 5
	assert all(v == 5.0 for v in camera_dxy)


def test_camera_motion_panel_shape() -> None:
	"""Returns documented schema with two series spanning twin axes."""
	mt = types.SimpleNamespace(
		dx=numpy.array([0.0]),
		dy=numpy.array([0.0]),
		scale=numpy.array([1.0]),
		quality=numpy.array([1.0]),
	)
	panel = analyze_report._camera_motion_panel_data(mt)
	assert panel['id'] == 'camera-motion'
	# Two series (translation magnitude + scale); names are renderer contract.
	assert len(panel['series']) == 2
	axes = {s['axis'] for s in panel['series']}
	assert axes == {'left', 'right'}


#============================================
# Group F: HTML structural assertions for each degradation case (WP-2C-4)
#============================================

def test_full_html_when_all_inputs_present(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Happy path: all four panels rendered, no warnings, JSON has 4 panels."""
	trajectory, crop_rects, mt, st, fps, cfg = analyze_report_fixtures.make_synthetic_inputs()
	_out, body = _call_write(
		monkeypatch, tmp_path,
		trajectory=trajectory, crop_rects=crop_rects,
		motion_track=mt, scene_transform=st, fps=fps, config=cfg,
	)
	# Four required canvas IDs in the body
	for panel_id in ('zoom-stability', 'zoom-multiple', 'camera-motion', 'runner-speed'):
		assert f'<canvas id="{panel_id}"' in body
	# JSON panels list has at least the four required panels (allow future additions)
	data = _extract_embedded_json(body)
	panel_ids = [p['id'] for p in data['panels']]
	required = ['zoom-stability', 'zoom-multiple', 'camera-motion', 'runner-speed']
	assert panel_ids[:len(required)] == required
	# No warnings section
	assert '<section class="warnings">' not in body


def test_html_when_motion_missing(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""When motion_track=None, only the two zoom panels render."""
	trajectory, crop_rects, _mt, st, fps, cfg = analyze_report_fixtures.make_synthetic_inputs()
	_out, body = _call_write(
		monkeypatch, tmp_path,
		trajectory=trajectory, crop_rects=crop_rects,
		motion_track=None, scene_transform=st, fps=fps, config=cfg,
		warnings=['Camera-motion data unavailable; camera and speed panels were skipped.'],
	)
	for panel_id in ('zoom-stability', 'zoom-multiple'):
		assert f'<canvas id="{panel_id}"' in body
	for panel_id in ('camera-motion', 'runner-speed'):
		assert f'<canvas id="{panel_id}"' not in body
	data = _extract_embedded_json(body)
	assert [p['id'] for p in data['panels']] == ['zoom-stability', 'zoom-multiple']
	assert 'Camera-motion data unavailable' in body


def test_html_when_scene_transform_missing(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""When scene_transform=None but motion_track present, three panels render (no speed)."""
	trajectory, crop_rects, mt, _st, fps, cfg = analyze_report_fixtures.make_synthetic_inputs()
	_out, body = _call_write(
		monkeypatch, tmp_path,
		trajectory=trajectory, crop_rects=crop_rects,
		motion_track=mt, scene_transform=None, fps=fps, config=cfg,
		warnings=['Scene transform unavailable; runner-speed panel was skipped.'],
	)
	for panel_id in ('zoom-stability', 'zoom-multiple', 'camera-motion'):
		assert f'<canvas id="{panel_id}"' in body
	assert '<canvas id="runner-speed"' not in body
	data = _extract_embedded_json(body)
	assert [p['id'] for p in data['panels']] == ['zoom-stability', 'zoom-multiple', 'camera-motion']
	assert 'Scene transform unavailable' in body


def test_html_no_external_url_under_all_degradation_cases(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""no-CDN contract holds across all three degradation cases."""
	trajectory, crop_rects, mt, st, fps, cfg = analyze_report_fixtures.make_synthetic_inputs()
	common = {'trajectory': trajectory, 'crop_rects': crop_rects}
	cases = (
		('full',      {**common, 'motion_track': mt,   'scene_transform': st}),
		('no_motion', {**common, 'motion_track': None, 'scene_transform': st}),
		('no_scene',  {**common, 'motion_track': mt,   'scene_transform': None}),
	)
	for tag, kw in cases:
		_out, body = _call_write(monkeypatch, tmp_path, fps=fps, config=cfg, **kw)
		assert 'http://' not in body, f'http:// found in {tag} case'
		assert 'https://' not in body, f'https:// found in {tag} case'


def test_embedded_json_series_lengths_match_frames(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Every series.values array has the same length as data.frames."""
	trajectory, crop_rects, mt, st, fps, cfg = analyze_report_fixtures.make_synthetic_inputs()
	_out, body = _call_write(
		monkeypatch, tmp_path,
		trajectory=trajectory, crop_rects=crop_rects,
		motion_track=mt, scene_transform=st, fps=fps, config=cfg,
	)
	data = _extract_embedded_json(body)
	frames_len = len(data['frames'])
	for panel in data['panels']:
		for series in panel['series']:
			assert len(series['values']) == frames_len, (
				f"panel {panel['id']} series {series['name']}: "
				f"{len(series['values'])} != {frames_len}"
			)


def test_html_when_trajectory_empty(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Empty trajectory produces 0 panels and no canvas elements."""
	_out, body = _call_write(
		monkeypatch, tmp_path,
		trajectory=[], crop_rects=[],
		motion_track=None, scene_transform=None, fps=None, config=None,
	)
	# No canvas IDs in the body
	for panel_id in ('zoom-stability', 'zoom-multiple', 'camera-motion', 'runner-speed'):
		assert f'<canvas id="{panel_id}"' not in body
	# Embedded JSON has empty panels list and no frames
	data = _extract_embedded_json(body)
	assert data['panels'] == []
	assert data['frames'] == []


def test_zoom_panels_require_aligned_trajectory_and_crop_rects() -> None:
	"""The zoom panel builders demand equal-length trajectory and crop_rects.

	Production caller (_mode_analyze in cli.py) must pad trajectory to match
	crop_rects (which tr_crop.trajectory_to_crop_rects pads to the full
	source frame count). Document the contract by asserting both panel
	builders raise AssertionError on a length mismatch. Caught the live-run
	bug where trajectory was post-erasure (27145 frames) but crop_rects
	was pre-padded to source-frame-count (27348 frames).
	"""
	import pytest
	trajectory = [{'cx': 100.0, 'cy': 50.0, 'w': 20.0, 'h': 40.0}] * 5
	crop_rects = [(0, 0, 200, 200)] * 7   # deliberately mismatched

	with pytest.raises(AssertionError):
		analyze_report._zoom_stability_panel_data(trajectory, crop_rects)

	with pytest.raises(AssertionError):
		analyze_report._zoom_multiple_panel_data(trajectory, crop_rects, config=None)


#============================================
# Group G: integration tests via direct call to the analyze-mode plot path (WP-3C-2)
#============================================

def test_integration_full_html_under_full_inputs(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""End-to-end: write_analyze_report under the WP-3C-1 fixture renders all four panels."""
	monkeypatch.setattr(analyze_report, '_load_renderer_js', lambda: '/* test stub */')
	trajectory, crop_rects, mt, st, fps, cfg = analyze_report_fixtures.make_synthetic_inputs()
	out = tmp_path / 'integration.encode_analysis.html'
	report_path = analyze_report.write_analyze_report(
		out_path=out,
		video_stem='integration',
		trajectory=trajectory,
		crop_rects=crop_rects,
		motion_track=mt,
		scene_transform=st,
		fps=fps,
		config=cfg,
		warnings=[],
	)
	assert report_path == out
	body = report_path.read_text(encoding='ascii')
	# Four canvases present
	for panel_id in ('zoom-stability', 'zoom-multiple', 'camera-motion', 'runner-speed'):
		assert f'<canvas id="{panel_id}"' in body
	# JSON shape: at least the four required panels (allow future additions)
	data = _extract_embedded_json(body)
	assert len(data['panels']) >= 4
	assert data['fps'] == 30.0
	assert len(data['frames']) == 30
	# Series lengths match frames
	for panel in data['panels']:
		for series in panel['series']:
			assert len(series['values']) == 30


def test_integration_motion_missing_skips_camera_and_speed(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""When motion_track is None (CLI degradation path), only zoom panels render."""
	monkeypatch.setattr(analyze_report, '_load_renderer_js', lambda: '/* test stub */')
	trajectory, crop_rects, _mt, st, fps, cfg = analyze_report_fixtures.make_synthetic_inputs()
	out = tmp_path / 'integration_no_motion.encode_analysis.html'
	report_path = analyze_report.write_analyze_report(
		out_path=out,
		video_stem='integration_no_motion',
		trajectory=trajectory,
		crop_rects=crop_rects,
		motion_track=None,
		scene_transform=st,
		fps=fps,
		config=cfg,
		warnings=['Camera-motion data unavailable; camera and speed panels were skipped.'],
	)
	body = report_path.read_text(encoding='ascii')
	# Only two canvases
	for panel_id in ('zoom-stability', 'zoom-multiple'):
		assert f'<canvas id="{panel_id}"' in body
	for panel_id in ('camera-motion', 'runner-speed'):
		assert f'<canvas id="{panel_id}"' not in body
	# Warning surfaced
	assert 'Camera-motion data unavailable' in body
	# JSON degradation
	data = _extract_embedded_json(body)
	panel_ids = [p['id'] for p in data['panels']]
	assert panel_ids == ['zoom-stability', 'zoom-multiple']


def test_integration_trajectory_padded_to_match_crop_rects(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""When upstream pads trajectory with None to match crop_rects length, panels render correctly.

	Replicates the cli.py wiring path: tr_crop.trajectory_to_crop_rects pads
	crop_rects to video_info["frame_count"], so trajectory may need padding
	with None for the trailing frames. Panel builders must accept None entries
	and emit nan in the corresponding series positions.
	"""
	monkeypatch.setattr(analyze_report, '_load_renderer_js', lambda: '/* test stub */')
	trajectory, crop_rects, mt, st, fps, cfg = analyze_report_fixtures.make_synthetic_inputs()
	# Simulate post-erasure trajectory shorter than crop_rects: drop the last
	# 5 entries from trajectory and pad with None (the cli.py fix path).
	short_trajectory = list(trajectory[:-5]) + [None] * 5
	out = tmp_path / 'integration_padded.encode_analysis.html'
	report_path = analyze_report.write_analyze_report(
		out_path=out,
		video_stem='integration_padded',
		trajectory=short_trajectory,
		crop_rects=crop_rects,
		motion_track=mt,
		scene_transform=st,
		fps=fps,
		config=cfg,
		warnings=[],
	)
	body = report_path.read_text(encoding='ascii')
	# All four canvases still rendered
	for panel_id in ('zoom-stability', 'zoom-multiple', 'camera-motion', 'runner-speed'):
		assert f'<canvas id="{panel_id}"' in body
	# JSON: torso_h series last 5 entries are None (padded frames)
	data = _extract_embedded_json(body)
	zoom_stability = next(p for p in data['panels'] if p['id'] == 'zoom-stability')
	torso_h_series = next(s for s in zoom_stability['series'] if s['name'] == 'torso_h')
	assert torso_h_series['values'][-5:] == [None, None, None, None, None]
	# But early entries are populated (real data)
	assert torso_h_series['values'][0] is not None


def test_integration_scene_transform_missing_skips_speed(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""When scene_transform is None (motion present, projection unavailable), speed panel drops."""
	monkeypatch.setattr(analyze_report, '_load_renderer_js', lambda: '/* test stub */')
	trajectory, crop_rects, mt, _st, fps, cfg = analyze_report_fixtures.make_synthetic_inputs()
	out = tmp_path / 'integration_no_scene.encode_analysis.html'
	report_path = analyze_report.write_analyze_report(
		out_path=out,
		video_stem='integration_no_scene',
		trajectory=trajectory,
		crop_rects=crop_rects,
		motion_track=mt,
		scene_transform=None,
		fps=fps,
		config=cfg,
		warnings=['Scene transform unavailable; runner-speed panel was skipped.'],
	)
	body = report_path.read_text(encoding='ascii')
	# Three canvases
	for panel_id in ('zoom-stability', 'zoom-multiple', 'camera-motion'):
		assert f'<canvas id="{panel_id}"' in body
	assert '<canvas id="runner-speed"' not in body
	# Warning surfaced
	assert 'Scene transform unavailable' in body
	# JSON degradation
	data = _extract_embedded_json(body)
	panel_ids = [p['id'] for p in data['panels']]
	assert panel_ids == ['zoom-stability', 'zoom-multiple', 'camera-motion']


def test_frames_erased_default_all_false_when_no_set_supplied(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Without an erased_frames argument the JSON exposes an all-False mask."""
	trajectory = [{'cx': 100.0, 'cy': 50.0, 'w': 20.0, 'h': 40.0}] * 4
	crop_rects = [(0, 0, 200, 200)] * 4
	_out, body = _call_write(
		monkeypatch, tmp_path,
		trajectory=trajectory, crop_rects=crop_rects,
	)
	data = _extract_embedded_json(body)
	assert data['frames_erased'] == [False, False, False, False]


def test_frames_erased_marks_intentional_dropouts(
	tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Frames listed in erased_frames serialize as True in the JSON mask.

	Locks in P1-1 from /Users/vosslab/.claude/plans/rustling-juggling-creek.md:
	the renderer needs to distinguish user-flagged not_in_frame dropouts from
	generic tracker gaps even though the trajectory entries are None for both.
	"""
	trajectory = [None, {'cx': 100.0, 'cy': 50.0, 'w': 20.0, 'h': 40.0}, None, None]
	crop_rects = [(0, 0, 200, 200)] * 4
	_out, body = _call_write(
		monkeypatch, tmp_path,
		trajectory=trajectory, crop_rects=crop_rects,
		erased_frames={2, 3},
	)
	data = _extract_embedded_json(body)
	assert data['frames_erased'] == [False, False, True, True]
