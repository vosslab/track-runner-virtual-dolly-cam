"""HTML diagnostic report writer for analyze mode.

Builds a self-contained HTML file with embedded JSON data and an inlined
vanilla-JS canvas renderer. See docs/active_plans/ANALYZE_MODE_PLOTS.md
for the full design.
"""

# Standard Library
import html
import json
import pathlib
import subprocess

# PIP3 modules
import numpy


# Smoothing window for the runner-speed overlay (and the zoom-stability
# panel that lands in Patch 4). 5 frames matches the typical stride-phase
# ripple period for sprinting footage. The constant tunes the actual
# smoothing call and the title's parenthetical only; the JSON series
# name `speed_5f_mean` is a stable contract identifier read by the JS
# renderer and intentionally stays hardcoded even if this constant
# changes. If you tune the window, the series-name contract stays put;
# only the title's text reflects the new value.
SPEED_SMOOTH_WINDOW = 5

# Smoothing window for the zoom-stability overlay. 9 frames matches the
# ~6-7 frame time constant of the 0.15 default crop_post_smooth_size_strength
# EMA per the 2026-05-02 changelog. Tunable here without renaming the JSON
# series key (crop_h_9f_mean is a stable contract for the JS renderer).
ZOOM_BOUNCE_SMOOTH_WINDOW = 9


#============================================
def _to_json_values(arr: numpy.ndarray) -> list:
	"""Convert a numpy float array to a JSON-safe Python list (nan -> None)."""
	# cast up front so isnan works on integer-dtype inputs and float() is
	# called once per element instead of twice
	arr = numpy.asarray(arr, dtype=float)
	return [None if numpy.isnan(v) else float(v) for v in arr]


#============================================
def _running_mean_nan_aware(values: numpy.ndarray, window: int) -> numpy.ndarray:
	"""Centered window mean ignoring NaN entries.

	Used to surface zoom bouncing (in the zoom-stability panel) and to
	overlay a smoothed runner-speed line so stride-phase ripple in raw
	speed is visible without being misread as jitter. A single NaN in
	the window does not blank the whole window output; the mean is
	computed over the non-NaN elements only. A window with zero non-NaN
	elements yields NaN.

	Args:
		values: 1-D float array (may contain NaN).
		window: Smoothing window size (any positive int; odd gives
			symmetric centering, even gives a slight left bias).

	Returns:
		1-D float64 array of same length as values.
	"""
	# guard against zero or negative windows that would silently produce
	# all-nan output and mask configuration mistakes upstream
	assert window > 0, f"window must be positive, got {window}"
	values = numpy.asarray(values, dtype=float)
	n = len(values)
	half = window // 2
	out = numpy.full(n, numpy.nan, dtype=float)
	for i in range(n):
		lo = max(0, i - half)
		hi = min(n, i + half + 1)
		segment = values[lo:hi]
		# isfinite guard avoids RuntimeWarning from nanmean on all-nan slices
		if numpy.isfinite(segment).any():
			out[i] = numpy.nanmean(segment)
	return out


#============================================
def _runner_speed_per_frame(trajectory: list, scene_transform: object) -> numpy.ndarray:
	"""Compute per-frame runner ground speed in scene coordinate space.

	For each frame, projects the torso center to scene coordinates using
	scene_transform.pixel_to_scene, then computes first differences.
	Frames with missing geometry (None entries or missing required keys)
	record numpy.nan for their scene coordinates; their speed and the
	speed of any immediately following frame are also nan (no valid
	previous-frame delta available).

	Scene coordinates remove camera pan/zoom, so the resulting speed
	reflects runner motion relative to the ground, not relative to the
	frame.

	Args:
		trajectory: Per-frame list of state dicts or None. Each present
			entry must have either ('cx', 'cy') or ('x', 'y', 'w', 'h').
			Production trajectories use 'cx'/'cy' directly; synthetic
			test data may use 'x'/'y'/'w'/'h' (top-left + dimensions).
		scene_transform: Object with pixel_to_scene(frame_index, px, py)
			method returning (scene_x, scene_y).

	Returns:
		1-D float64 numpy array of length len(trajectory).
		speed[0] is always nan (no previous frame).
	"""
	n = len(trajectory)
	# arrays for per-frame scene coordinates
	sx = numpy.full(n, numpy.nan, dtype=float)
	sy = numpy.full(n, numpy.nan, dtype=float)

	for i, entry in enumerate(trajectory):
		# skip missing frames
		if entry is None:
			continue
		# prefer explicit cx/cy; fall back to top-left + dimensions
		if 'cx' in entry and 'cy' in entry:
			px = float(entry['cx'])
			py = float(entry['cy'])
		elif 'x' in entry and 'y' in entry and 'w' in entry and 'h' in entry:
			# compute center from top-left corner and box dimensions
			px = float(entry['x']) + float(entry['w']) / 2.0
			py = float(entry['y']) + float(entry['h']) / 2.0
		else:
			# missing required geometry; leave as nan
			continue
		# project pixel center to scene coordinates for this frame
		scene_x, scene_y = scene_transform.pixel_to_scene(i, px, py)
		sx[i] = float(scene_x)
		sy[i] = float(scene_y)

	# compute euclidean distance between consecutive valid scene positions
	speed = numpy.full(n, numpy.nan, dtype=float)
	for i in range(1, n):
		# both current and previous frame must have valid scene coords
		if numpy.isfinite(sx[i]) and numpy.isfinite(sy[i]) \
				and numpy.isfinite(sx[i - 1]) and numpy.isfinite(sy[i - 1]):
			dx = sx[i] - sx[i - 1]
			dy = sy[i] - sy[i - 1]
			speed[i] = float(numpy.sqrt(dx * dx + dy * dy))
	return speed


#============================================
def _runner_speed_panel_data(
	trajectory: list,
	scene_transform: object,
	fps: float | None,
) -> dict:
	"""Build the runner-speed panel data dict for the analyze report.

	Emits raw per-frame ground speed and a 5-frame centered mean overlay.
	The smoothed overlay makes stride-phase ripple visible without
	being mistaken for path jitter.

	Args:
		trajectory: Per-frame torso-box list (see _runner_speed_per_frame).
		scene_transform: Object with pixel_to_scene method.
		fps: Video frame rate. Used for the secondary axis label only.
			Pass None or a non-positive value to omit the label.

	Returns:
		Panel dict matching the embedded JSON schema:
		{id, title, y_left_label, y_right_label, default_visible, series}.
	"""
	speed_raw = _runner_speed_per_frame(trajectory, scene_transform)
	speed_smoothed = _running_mean_nan_aware(speed_raw, window=SPEED_SMOOTH_WINDOW)

	# secondary axis label only meaningful when fps is a valid positive number
	if fps is not None and fps > 0:
		y_right_label = 'speed (scene units / second)'
	else:
		y_right_label = None

	# Title reflects the actual smoothing window; the series name below
	# (`speed_5f_mean`) is a stable JSON-schema contract identifier and
	# stays hardcoded even if SPEED_SMOOTH_WINDOW is retuned.
	panel = {
		'id': 'runner-speed',
		'title': f'Runner ground speed (raw + {SPEED_SMOOTH_WINDOW}-frame mean overlay)',
		'y_left_label': 'speed (scene units / frame)',
		'y_right_label': y_right_label,
		'default_visible': ['speed_raw', 'speed_5f_mean'],
		'series': [
			{
				'name': 'speed_raw',
				'axis': 'left',
				'values': _to_json_values(speed_raw),
			},
			{
				'name': 'speed_5f_mean',
				'axis': 'left',
				'values': _to_json_values(speed_smoothed),
			},
		],
	}
	return panel


#============================================
def _zoom_stability_panel_data(trajectory: list, crop_rects: list) -> dict:
	"""Build the zoom-stability panel data dict for the analyze report.

	Emits raw crop_h, a 9-frame nan-aware mean overlay (window matches the
	~6-7 frame time constant of the 0.15 default EMA), plus torso_h and
	torso_w on the twin right axis.

	Args:
		trajectory: Per-frame torso-box list (state dict or None per frame).
			Present entries must have 'h' and 'w' keys.
		crop_rects: Per-frame crop rectangle tuples (x, y, w, h). Must be
			the same length as trajectory; one rect per frame.

	Returns:
		Panel dict matching the embedded JSON schema.
	"""
	# trajectory and crop_rects must cover the same frame range
	assert len(trajectory) == len(crop_rects), (
		f"trajectory length {len(trajectory)} != crop_rects length {len(crop_rects)}"
	)
	n = len(trajectory)

	crop_h_arr = numpy.full(n, numpy.nan, dtype=float)
	torso_h_arr = numpy.full(n, numpy.nan, dtype=float)
	torso_w_arr = numpy.full(n, numpy.nan, dtype=float)

	# extract crop_h from each crop rect tuple; None or short tuple -> nan
	for i, rect in enumerate(crop_rects):
		if rect is not None and len(rect) >= 4:
			crop_h_arr[i] = float(rect[3])

	# extract torso h and w from each trajectory state dict
	for i, state in enumerate(trajectory):
		if state is not None and 'h' in state:
			torso_h_arr[i] = float(state['h'])
		if state is not None and 'w' in state:
			torso_w_arr[i] = float(state['w'])

	# compute 9-frame nan-aware mean overlay for crop_h
	crop_h_smooth = _running_mean_nan_aware(crop_h_arr, window=ZOOM_BOUNCE_SMOOTH_WINDOW)

	# title uses the constant so it stays in sync if the window is retuned
	panel = {
		'id': 'zoom-stability',
		'title': (
			f'Zoom stability (raw + {ZOOM_BOUNCE_SMOOTH_WINDOW}-frame mean overlay; '
			'twin axis: torso h, w)'
		),
		'y_left_label': 'crop_h (source-px)',
		'y_right_label': 'torso h, w (source-px)',
		'default_visible': ['crop_h', 'crop_h_9f_mean'],
		'series': [
			{
				'name': 'crop_h',
				'axis': 'left',
				'values': _to_json_values(crop_h_arr),
			},
			{
				# crop_h_9f_mean is a stable contract identifier for the JS renderer;
				# stays hardcoded even if ZOOM_BOUNCE_SMOOTH_WINDOW is retuned
				'name': 'crop_h_9f_mean',
				'axis': 'left',
				'values': _to_json_values(crop_h_smooth),
			},
			{
				'name': 'torso_h',
				'axis': 'right',
				'values': _to_json_values(torso_h_arr),
			},
			{
				'name': 'torso_w',
				'axis': 'right',
				'values': _to_json_values(torso_w_arr),
			},
		],
	}
	return panel


#============================================
def _zoom_multiple_panel_data(
	trajectory: list,
	crop_rects: list,
	config: dict | None,
) -> dict:
	"""Build the zoom-multiple panel data dict for the analyze report.

	Emits the achieved crop_h / torso_h ratio per frame, with an optional
	dashed reference line at the configured torso_height_multiple value.

	The configured value lives at config['processing']['torso_height_multiple']
	in the standard track_runner.config.yaml shape (nested under 'processing:').
	Patch 6, which loads the YAML before calling this function, should pass
	the full config dict so this function can read the nested path.

	Args:
		trajectory: Per-frame torso-box list (state dict or None per frame).
		crop_rects: Per-frame crop rectangle tuples (x, y, w, h). Same
			length as trajectory.
		config: Full config dict loaded from tr_config/<stem>.yaml, or None.
			When None or when the configured key is absent, the reference
			line is omitted from the panel dict.

	Returns:
		Panel dict matching the embedded JSON schema.
	"""
	assert len(trajectory) == len(crop_rects), (
		f"trajectory length {len(trajectory)} != crop_rects length {len(crop_rects)}"
	)
	n = len(trajectory)

	crop_h_arr = numpy.full(n, numpy.nan, dtype=float)
	torso_h_arr = numpy.full(n, numpy.nan, dtype=float)

	# extract crop_h from each crop rect tuple; None or short tuple -> nan
	for i, rect in enumerate(crop_rects):
		if rect is not None and len(rect) >= 4:
			crop_h_arr[i] = float(rect[3])

	# extract torso h from each trajectory state dict
	for i, state in enumerate(trajectory):
		if state is not None and 'h' in state:
			torso_h_arr[i] = float(state['h'])

	# compute achieved_multiple = crop_h / torso_h, nan where undefined
	achieved = numpy.full(n, numpy.nan, dtype=float)
	for i in range(n):
		if numpy.isfinite(crop_h_arr[i]) and numpy.isfinite(torso_h_arr[i]):
			# guard against divide-by-zero or zero torso_h
			if torso_h_arr[i] > 0:
				achieved[i] = crop_h_arr[i] / torso_h_arr[i]

	panel = {
		'id': 'zoom-multiple',
		'title': 'Zoom multiple: achieved vs configured',
		'y_left_label': 'crop_h / torso_h',
		'y_right_label': None,
		'default_visible': ['achieved_multiple'],
		'series': [
			{
				'name': 'achieved_multiple',
				'axis': 'left',
				'values': _to_json_values(achieved),
			},
		],
	}

	# include reference_lines only when the configured value is available and finite.
	# The key lives at config['processing']['torso_height_multiple'] per the standard
	# track_runner.config.yaml shape (see track_runner/track_runner.config.yaml).
	# Do NOT use dict.get(); use 'in' guards instead.
	if config is not None:
		configured_value = None
		if 'processing' in config:
			processing = config['processing']
			if 'torso_height_multiple' in processing:
				configured_value = float(processing['torso_height_multiple'])
		elif 'torso_height_multiple' in config:
			# flat config dict (test convenience or future format)
			configured_value = float(config['torso_height_multiple'])
		if configured_value is not None and numpy.isfinite(configured_value):
			panel['reference_lines'] = [{
				'axis': 'left',
				'value': configured_value,
				'label': f'configured ({configured_value:.1f})',
			}]

	return panel


#============================================
def _camera_motion_panel_data(motion_track: object) -> dict:
	"""Build the camera-motion panel data dict for the analyze report.

	Emits the per-frame camera translation magnitude (hypot of dx, dy) on
	the left axis and the scale factor on the right axis.

	Args:
		motion_track: MotionTrack dataclass from camera_motion.py with numpy
			arrays dx, dy, scale, quality. Must not be None (caller's
			responsibility).

	Returns:
		Panel dict matching the embedded JSON schema.
	"""
	# compute euclidean camera translation magnitude per frame
	camera_dxy = numpy.hypot(motion_track.dx, motion_track.dy)

	panel = {
		'id': 'camera-motion',
		'title': 'Camera motion',
		'y_left_label': 'hypot(dx, dy) (px)',
		'y_right_label': 'scale',
		'default_visible': ['camera_dxy', 'scale'],
		'series': [
			{
				'name': 'camera_dxy',
				'axis': 'left',
				'values': _to_json_values(camera_dxy),
			},
			{
				'name': 'scale',
				'axis': 'right',
				'values': _to_json_values(numpy.asarray(motion_track.scale, dtype=float)),
			},
		],
	}
	return panel


#============================================
def _load_renderer_js() -> str:
	"""Read the renderer JS asset from the repo's data/js/ directory.

	Locates the repo root via `git rev-parse --show-toplevel` (per
	docs/REPO_STYLE.md) so the loader works regardless of CWD. Captures
	git stderr so a failed lookup raises a structured CalledProcessError
	instead of bleeding diagnostics to the user's terminal.

	Returns:
		ASCII string contents of analyze_report_renderer.js.
	"""
	# anchor cwd to this file's parent so git rev-parse works from any caller CWD
	raw = subprocess.check_output(
		['git', 'rev-parse', '--show-toplevel'],
		cwd=pathlib.Path(__file__).parent,
		stderr=subprocess.PIPE,
	)
	repo_root = pathlib.Path(raw.decode('ascii').strip())
	js_path = repo_root / 'data' / 'js' / 'analyze_report_renderer.js'
	if not js_path.is_file():
		raise FileNotFoundError(
			f"Renderer JS asset not found at {js_path}. "
			"It is created in Patch 2 of the analyze-mode HTML report plan."
		)
	return js_path.read_text(encoding='ascii')


# Per-panel interpretive notes rendered as a short <p> below each summary.
# Hardcoded by panel id to keep the HTML emitter pure-string. Edit here if
# panel semantics change. Keys must match the panel 'id' values produced by
# the panel-data builders.
PANEL_NOTES = {
	'zoom-stability':
		'Visible gap between raw and smoothed crop_h is the zoom-bouncing '
		'magnitude. Twin axis shows the torso-h and torso-w inputs.',
	'zoom-multiple':
		'Achieved torso-height multiple per frame. Dashed reference line '
		'(when present) is the configured torso_height_multiple.',
	'camera-motion':
		'Camera shift magnitude (hypot of dx, dy) and per-frame scale.',
	'runner-speed':
		'Runner ground speed in scene units per frame. Raw and 5-frame '
		'mean overlay; the gap reveals stride-phase ripple.',
}


#============================================
def _build_analyze_report_data(
	*,
	video_stem: str,
	trajectory: list,
	crop_rects: list,
	motion_track: object,
	scene_transform: object,
	fps: float | None,
	config: dict | None,
	erased_frames: set | None = None,
) -> dict:
	"""Build the JSON-serializable data payload for the report.

	Wires the four panel-data builders into the panels list using the
	documented degradation matrix:

	- No trajectory          -> 0 panels (warnings explain why)
	- Trajectory only        -> 2 panels (zoom-stability, zoom-multiple)
	- Trajectory + motion    -> 3 panels (+ camera-motion)
	- All inputs present     -> 4 panels (+ runner-speed)

	Args:
		video_stem: Basename of the source video (no extension).
		trajectory: Per-frame torso-box list from the solver.
		crop_rects: Per-frame crop rectangle list from tr_crop.
		motion_track: MotionTrack object from camera_motion (or None).
		scene_transform: SceneTransform object (or None).
		fps: Frames-per-second float, or None when not available.
		config: Solve/encode config dict, or None.

	Returns:
		Dict with keys video_stem, fps, frames, panels, warnings.
		warnings is always [] here; the caller supplies final warnings.
	"""
	# build a simple frame-index list covering the full trajectory
	frame_count = len(trajectory)
	frames = list(range(frame_count))

	# assemble panels with the documented degradation matrix
	panels = []
	if trajectory:
		panels.append(_zoom_stability_panel_data(trajectory, crop_rects))
		panels.append(_zoom_multiple_panel_data(trajectory, crop_rects, config))
		if motion_track is not None:
			panels.append(_camera_motion_panel_data(motion_track))
			if scene_transform is not None:
				panels.append(_runner_speed_panel_data(trajectory, scene_transform, fps))

	# normalize fps: accept only positive finite values; everything else -> None
	if fps is not None and fps > 0:
		fps_value = fps
	else:
		fps_value = None

	# build a parallel boolean array marking frames erased on purpose
	# (not_in_frame seeds) so the renderer can distinguish them from
	# generic tracker gaps. Length matches frame_count; default all False
	# when no erasure set was supplied.
	if erased_frames is None:
		frames_erased = [False] * frame_count
	else:
		frames_erased = [i in erased_frames for i in range(frame_count)]

	report_data = {
		'video_stem': video_stem,
		'fps': fps_value,
		'frames': frames,
		'frames_erased': frames_erased,
		'panels': panels,
		# warnings filled by _write_analyze_report_html from its parameter
		'warnings': [],
	}
	return report_data


#============================================
def _write_analyze_report_html(
	*,
	out_path: pathlib.Path,
	video_stem: str,
	report_data: dict,
	warnings: list,
) -> pathlib.Path:
	"""Write the HTML diagnostic report file.

	Emits a self-contained HTML file with:
	- four canvas markers (zoom-stability, zoom-multiple,
	  camera-motion, runner-speed)
	- an embedded JSON data block
	- an inlined vanilla-JS renderer script

	Args:
		out_path: Destination path for the HTML file.
		video_stem: Basename of the source video (no extension).
		report_data: JSON-serializable dict from _build_analyze_report_data.
		warnings: List of warning strings (HTML-escaped before output).

	Returns:
		pathlib.Path pointing at the written file.
	"""
	out_path = pathlib.Path(out_path)
	# create parent directory tree if needed
	out_path.parent.mkdir(parents=True, exist_ok=True)

	# HTML-escape user-supplied strings to prevent injection
	safe_stem = html.escape(video_stem)

	# build the combined report data dict with caller-supplied warnings
	report_data_with_warnings = dict(report_data)
	report_data_with_warnings['warnings'] = list(warnings)

	# inline CSS: ASCII-only, no external fonts or images. Section selectors
	# match the markup emitted below; canvas height is fixed in CSS so the
	# JS renderer can size to the canvas client rect without layout flicker.
	inline_css = (
		'body { font-family: sans-serif; max-width: 1200px; '
		'margin: 1em auto; padding: 0 1em; }\n'
		'h1 { border-bottom: 2px solid #333; padding-bottom: 0.3em; }\n'
		'section.warnings { background: #fff8e6; '
		'border-left: 4px solid #d97706; padding: 0.5em 1em; margin: 1em 0; }\n'
		'section.warnings ul { margin: 0; padding-left: 1.5em; }\n'
		'section.references { margin: 1em 0; font-size: 0.9em; color: #555; }\n'
		'section.references code, section.references a '
		'{ font-family: monospace; }\n'
		'details { border: 1px solid #ddd; border-radius: 4px; '
		'padding: 0.5em 1em; margin: 0.5em 0; background: #fafafa; }\n'
		'details summary { font-weight: bold; cursor: pointer; '
		'padding: 0.3em 0; font-size: 1.1em; }\n'
		'details > p { color: #555; font-size: 0.9em; margin-top: 0.3em; }\n'
		'.panel-controls { display: flex; flex-wrap: wrap; gap: 0.5em; '
		'margin: 0.5em 0; font-size: 0.85em; color: #555; }\n'
		'.panel-controls label { user-select: none; cursor: pointer; }\n'
		'canvas { display: block; width: 100%; max-width: 1100px; '
		'height: 320px; margin: 0.5em 0; border: 1px solid #eee; }\n'
		'.tooltip { position: absolute; background: rgba(0,0,0,0.85); '
		'color: #fff; padding: 0.3em 0.6em; border-radius: 3px; '
		'font-size: 0.85em; pointer-events: none; white-space: pre-line; '
		'z-index: 10; }\n'
	)

	# load the renderer JS (reads data/js/analyze_report_renderer.js)
	renderer_js = _load_renderer_js()

	# assemble the HTML document in document order
	lines = []
	lines.append('<!doctype html>')
	lines.append('<html lang="en"><head>')
	lines.append('<meta charset="utf-8">')
	lines.append(f'<title>analyze report: {safe_stem}</title>')
	lines.append('<style>')
	lines.append(inline_css)
	lines.append('</style>')
	lines.append('</head><body>')
	lines.append(f'<h1>Analyze diagnostic report: {safe_stem}</h1>')

	# warnings section: only emitted when warnings list is non-empty
	if warnings:
		lines.append('<section class="warnings"><ul>')
		for w in warnings:
			# HTML-escape each warning string before inserting into markup
			lines.append(f'<li>{html.escape(w)}</li>')
		lines.append('</ul></section>')

	# references section: same-directory relative paths to the YAML report
	# and the per-video config. The "if present" caveat is informational
	# only -- we cannot check from here, so render unconditionally and let
	# the user click through to discover absence.
	yaml_href = html.escape(f'{video_stem}.encode_analysis.yaml')
	yaml_label = html.escape(f'{video_stem}.encode_analysis.yaml')
	cfg_href = html.escape(f'{video_stem}.yaml')
	cfg_label = html.escape(f'{video_stem}.yaml')
	lines.append('<section class="references">')
	lines.append('<h2>References</h2>')
	lines.append('<ul>')
	lines.append(
		f'<li>YAML report: <a href="{yaml_href}"><code>{yaml_label}</code></a></li>'
	)
	lines.append(
		f'<li>Per-video config: <a href="{cfg_href}"><code>{cfg_label}</code></a> '
		'(if present)</li>'
	)
	lines.append('</ul>')
	lines.append('</section>')

	# panels section: emit one <details> per panel in report_data['panels'].
	# The four canvas markers therefore appear only when the corresponding
	# inputs were available (see _build_analyze_report_data degradation
	# matrix). When the panels list is empty the section is still emitted
	# (empty) so structural tests can find it; the warnings section above
	# explains why no panels rendered.
	lines.append('<section class="panels">')
	for panel in report_data_with_warnings['panels']:
		panel_id = html.escape(panel['id'])
		panel_title = html.escape(panel['title'])
		# interpretive note keyed by panel id; required key (a panel without
		# a documented note is a code bug, not a user-supplied surprise)
		note = PANEL_NOTES[panel['id']]
		safe_note = html.escape(note)
		lines.append(f'<details open><summary>{panel_title}</summary>')
		lines.append(f'<p>{safe_note}</p>')
		# checkbox row populated by JS at render time
		lines.append('<div class="panel-controls"></div>')
		lines.append(f'<canvas id="{panel_id}"></canvas>')
		lines.append('</details>')
	lines.append('</section>')

	# embedded JSON data block for the renderer to consume
	lines.append('<script type="application/json" id="encode-analysis-data">')
	# ensure_ascii=True so no UTF-8 bytes sneak into the output
	lines.append(json.dumps(report_data_with_warnings, indent=2, ensure_ascii=True))
	lines.append('</script>')

	# inlined renderer JS verbatim (no src= URL; keeps file self-contained)
	lines.append('<script>')
	lines.append(renderer_js)
	lines.append('</script>')

	lines.append('</body></html>')

	# join with newlines and write as ASCII
	html_text = '\n'.join(lines) + '\n'
	out_path.write_text(html_text, encoding='ascii')

	return out_path


#============================================
def write_analyze_report(
	*,
	out_path: pathlib.Path,
	video_stem: str,
	trajectory: list,
	crop_rects: list,
	motion_track: object,
	scene_transform: object,
	fps: float | None,
	config: dict | None,
	warnings: list,
	erased_frames: set | None = None,
) -> pathlib.Path:
	"""Write one HTML diagnostic report; return the path written.

	Entry point for analyze mode. Builds report data then writes HTML.

	Args:
		out_path: Destination path for the HTML file.
		video_stem: Basename of the source video (no extension).
		trajectory: Per-frame torso-box list from the solver.
		crop_rects: Per-frame crop rectangle list from tr_crop.
		motion_track: MotionTrack object from camera_motion (or None).
		scene_transform: SceneTransform object (or None).
		fps: Frames-per-second float, or None when not available.
		config: Solve/encode config dict, or None.
		warnings: List of warning strings to surface in the report.

	Returns:
		pathlib.Path pointing at the written HTML file.
	"""
	# build the data payload (panels empty in M1)
	report_data = _build_analyze_report_data(
		video_stem=video_stem,
		trajectory=trajectory,
		crop_rects=crop_rects,
		motion_track=motion_track,
		scene_transform=scene_transform,
		fps=fps,
		config=config,
		erased_frames=erased_frames,
	)

	# write HTML to disk, threading in the caller-supplied warnings
	out_file = _write_analyze_report_html(
		out_path=out_path,
		video_stem=video_stem,
		report_data=report_data,
		warnings=warnings,
	)

	return out_file
