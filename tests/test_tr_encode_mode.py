"""CLI override parsing for encode mode.

Focused behavioral tests for the non-trivial pieces of CLI parsing
and contract enforcement: WxH resolution parsing, default-output
container, and the encode-mode overlay/filter/output flag matrix.
Argparse wiring and override-assignment are pure library behavior and
intentionally not tested unless they encode a user-facing contract.
"""

import argparse
import sys

import pytest

import cli
import cli_args
import tr_crop
import tr_paths


#============================================
def test_parse_resolution_WxH_round_trip():
	# parse WxH into [W, H]; this is the one real parsing step
	assert cli._parse_resolution("1920x1080") == [1920, 1080]
	assert cli._parse_resolution("1280X720") == [1280, 720]


#============================================
def test_parse_resolution_rejects_bad_forms():
	# error detection: missing 'x', wrong separator, empty sides
	with pytest.raises(RuntimeError, match="WxH"):
		cli._parse_resolution("1920")
	with pytest.raises(RuntimeError, match="WxH"):
		cli._parse_resolution("1920,1080")
	with pytest.raises(RuntimeError, match="WxH"):
		cli._parse_resolution("x1080")


#============================================
# Default output path: extension is forced to lowercase '.mkv'.
def test_default_output_path_forces_mkv_extension():
	assert tr_paths.default_output_path("Foo-Bar.MOV") == "Foo-Bar_tracked.mkv"
	assert tr_paths.default_output_path("Foo.mp4") == "Foo_tracked.mkv"
	assert tr_paths.default_output_path("path/with.dots.MOV") == "path/with.dots_tracked.mkv"


#============================================
# Filter resolution behavior matrix
def _make_args(**overrides) -> argparse.Namespace:
	"""Build a minimal Namespace for _resolve_encode_filters tests."""
	ns = argparse.Namespace(
		encode_filters=None,
		no_filters=False,
	)
	for k, v in overrides.items():
		setattr(ns, k, v)
	return ns


def test_resolve_filters_no_filters_alone():
	args = _make_args(no_filters=True)
	cfg = {"encode_filters": ["bilateral"]}
	assert cli._resolve_encode_filters(args, cfg) == []


def test_resolve_filters_no_filters_conflicts_with_dash_F():
	args = _make_args(no_filters=True, encode_filters="bilateral")
	with pytest.raises(RuntimeError, match="cannot be combined"):
		cli._resolve_encode_filters(args, {})


def test_resolve_filters_dash_F_none_alias():
	args = _make_args(encode_filters="none")
	assert cli._resolve_encode_filters(args, {"encode_filters": ["bilateral"]}) == []


def test_resolve_filters_dash_F_none_case_insensitive():
	args = _make_args(encode_filters="  NONE  ")
	assert cli._resolve_encode_filters(args, {}) == []


def test_resolve_filters_rejects_mixed_none():
	args = _make_args(encode_filters="none,blur")
	with pytest.raises(RuntimeError, match="cannot be combined with other"):
		cli._resolve_encode_filters(args, {})


#============================================
# Encode subparser CLI contract
def _parse(argv: list) -> argparse.Namespace:
	"""Run cli_args.parse_args() with a temporary sys.argv."""
	saved = sys.argv
	sys.argv = ["track_runner.py"] + argv
	try:
		return cli_args.parse_args()
	finally:
		sys.argv = saved


def test_encode_parser_accepts_draw_flags():
	args = _parse(["-i", "x.MOV", "encode",
		"--draw-tracking-overlay", "--draw-velocity-arrow"])
	assert args.draw_tracking_overlay is True
	assert args.draw_velocity_arrow is True


def test_encode_parser_accepts_debug_overlay():
	args = _parse(["-i", "x.MOV", "encode", "--draw-debug-overlay"])
	assert args.draw_debug_overlay is True
	assert args.draw_tracking_overlay is False


def test_encode_parser_rejects_both_overlay_tiers():
	with pytest.raises(SystemExit):
		_parse(["-i", "x.MOV", "encode",
			"--draw-tracking-overlay", "--draw-debug-overlay"])


def test_encode_parser_rejects_local_d():
	# global -d must come before the subcommand; encode -d should fail
	with pytest.raises(SystemExit):
		_parse(["-i", "x.MOV", "encode", "-d"])


def test_global_debug_does_not_set_overlay_flags():
	# global diagnostic -d does not enable rendered overlays
	args = _parse(["-i", "x.MOV", "-d", "encode"])
	assert args.debug is True
	assert args.draw_tracking_overlay is False
	assert args.draw_debug_overlay is False
	assert args.draw_velocity_arrow is False


def test_velocity_arrow_alone_rejected():
	with pytest.raises(SystemExit):
		_parse(["-i", "x.MOV", "encode", "--draw-velocity-arrow"])


def test_mp4_with_explicit_mkv_output_rejected():
	with pytest.raises(SystemExit):
		_parse(["-i", "x.MOV", "encode", "--mp4", "-o", "out.mkv"])


def test_explicit_mp4_output_accepted():
	args = _parse(["-i", "x.MOV", "encode", "-o", "out.mp4"])
	assert args.output_file == "out.mp4"


def test_uppercase_output_extension_accepted():
	# user-provided path is preserved verbatim; extension comparison is
	# done case-insensitively, so .MKV / .MP4 are accepted
	args = _parse(["-i", "x.MOV", "encode", "-o", "Out.MKV"])
	assert args.output_file == "Out.MKV"
	args = _parse(["-i", "x.MOV", "encode", "-o", "Out.MP4"])
	assert args.output_file == "Out.MP4"


#============================================
# Off-center crop validation. The validator enforces a sustained-run
# contract: a runner inside the safe central window passes; a brief
# glitch passes; a sustained run of bad frames raises with a diagnostic
# pointing at the first frame of the run.
#
# Frame layout used in tests:
#   source frame:  1920x1080
#   crop_rect:     centered, 1280x720  -> placed at (320, 180) of source
#   output:        1280x720
# In this layout a runner at scene (cx, cy) maps to output pixel:
#   out_x = (cx - 320) * (1280/1280) = cx - 320
#   out_y = (cy - 180) * (720/720)   = cy - 180
# So the safe window with default fractions (0.5, 0.7) in output space is:
#   x in [320, 960],  y in [108, 612]
# Mapped back to scene space (still using crop_rect (320,180,1280,720)):
#   cx in [640, 1280], cy in [288, 792]

CROP_RECT = (320, 180, 1280, 720)
OUTPUT_W = 1280
OUTPUT_H = 720
FRAME_W = 1920
FRAME_H = 1080


def _state(cx: float, cy: float) -> dict:
	return {"cx": cx, "cy": cy, "w": 60.0, "h": 100.0, "conf": 1.0}


def _validate(trajectory, crop_rects, **kwargs):
	"""Convenience wrapper with the test layout's defaults."""
	defaults = dict(
		torso_multiple=3.0,
		aspect_ratio=16.0 / 9.0,
	)
	defaults.update(kwargs)
	tr_crop.validate_torso_within_central_window(
		trajectory, crop_rects,
		OUTPUT_W, OUTPUT_H, FRAME_W, FRAME_H,
		**defaults,
	)


def test_validate_torso_passes_when_centered():
	# 50 frames with the runner exactly at scene center (960, 540) ->
	# output (640, 360), dead center. No exception.
	traj = [_state(960.0, 540.0) for _ in range(50)]
	rects = [CROP_RECT] * 50
	_validate(traj, rects)


def test_validate_torso_passes_with_short_glitch():
	# 10 frames; frames 4 and 5 are off-left (run length 2 <= max=3).
	# No exception (short glitches tolerated).
	traj = [_state(960.0, 540.0) for _ in range(10)]
	traj[4] = _state(100.0, 540.0)
	traj[5] = _state(100.0, 540.0)
	rects = [CROP_RECT] * 10
	_validate(traj, rects)


def test_validate_torso_fails_when_off_left_sustained():
	# scene cx=200 -> output_x = -120, far outside safe x in [320, 960].
	traj = [_state(200.0, 540.0) for _ in range(10)]
	rects = [CROP_RECT] * 10
	with pytest.raises(tr_crop.OffCenterCropError):
		_validate(traj, rects)


def test_validate_torso_fails_when_off_right_sustained():
	# symmetric: scene cx=1700 -> output_x=1380, past xhi=960.
	traj = [_state(1700.0, 540.0) for _ in range(10)]
	rects = [CROP_RECT] * 10
	with pytest.raises(tr_crop.OffCenterCropError):
		_validate(traj, rects)


def test_validate_edge_attribute_left():
	# crop extends past the left edge of the source frame.
	# crop_rect=(-300, 180, 1280, 720): crop_x < 0.
	# scene cx=1000 -> output_x = 1300 > xhi=960 -> violation.
	rect = (-300, 180, 1280, 720)
	traj = [_state(1000.0, 540.0) for _ in range(10)]
	rects = [rect] * 10
	with pytest.raises(tr_crop.OffCenterCropError) as exc:
		_validate(traj, rects)
	assert exc.value.edge == "left"


def test_validate_edge_attribute_right():
	# crop_rect=(900, 180, 1280, 720): crop_x + cw = 2180 > 1920.
	# scene cx=900 -> output_x = 0 < xlo=320 -> violation.
	rect = (900, 180, 1280, 720)
	traj = [_state(900.0, 540.0) for _ in range(10)]
	rects = [rect] * 10
	with pytest.raises(tr_crop.OffCenterCropError) as exc:
		_validate(traj, rects)
	assert exc.value.edge == "right"


def test_validate_edge_attribute_empty_when_crop_fits():
	# generic case: crop fits in source but runner is outside the
	# crop window. edge attribute is the empty string.
	traj = [_state(200.0, 540.0) for _ in range(10)]
	rects = [CROP_RECT] * 10
	with pytest.raises(tr_crop.OffCenterCropError) as exc:
		_validate(traj, rects)
	assert exc.value.edge == ""


def test_validate_torso_vertical_threshold_is_70_percent():
	# safe y window in output space: [108, 612] (i.e., 30% offset from
	# center 360, since central_y_fraction=0.7 means half=0.35,
	# bounds = 360 +- 0.35*720 = 360 +- 252).
	# cy=540 -> output_y=360 (dead center): pass, even sustained.
	traj_pass = [_state(960.0, 540.0) for _ in range(10)]
	rects = [CROP_RECT] * 10
	_validate(traj_pass, rects)
	# cy=900 -> output_y=720 (output_h), well past 612: violates.
	traj_fail = [_state(960.0, 900.0) for _ in range(10)]
	with pytest.raises(tr_crop.OffCenterCropError):
		_validate(traj_fail, rects)


def test_validate_torso_horizontal_threshold_is_50_percent():
	# safe x window in output space: [320, 960].
	# cx=900 -> output_x=580 (within), pass.
	traj_pass = [_state(900.0, 540.0) for _ in range(10)]
	rects = [CROP_RECT] * 10
	_validate(traj_pass, rects)
	# cx=1300 -> output_x=980 (just past 960), fail.
	traj_fail = [_state(1300.0, 540.0) for _ in range(10)]
	with pytest.raises(tr_crop.OffCenterCropError):
		_validate(traj_fail, rects)


def test_validate_torso_inclusive_bounds():
	# A frame whose output_x equals the lower bound exactly should pass.
	# cx=640 -> output_x=320 == xlo: inclusive, no violation even
	# sustained.
	traj = [_state(640.0, 540.0) for _ in range(10)]
	rects = [CROP_RECT] * 10
	_validate(traj, rects)


def test_validate_torso_skips_none_and_non_finite():
	# 4 valid bad-left frames interspersed with None and NaN entries.
	# Skipped frames pause the run; total bad count is still > max=3,
	# so this raises (see test_validate_skipped_frames_pause_run for
	# the explicit pause semantics).
	traj = [
		_state(200.0, 540.0),  # bad (run=1)
		None,                   # skip
		_state(200.0, 540.0),  # bad (run=2)
		_state(float("nan"), 540.0),  # skip
		_state(200.0, 540.0),  # bad (run=3)
		_state(200.0, 540.0),  # bad (run=4 > max=3) -> raise here
	]
	rects = [CROP_RECT] * 6
	with pytest.raises(tr_crop.OffCenterCropError):
		_validate(traj, rects)


def test_validate_skipped_frames_pause_run():
	# pattern [bad, bad, None, bad, bad] with max_offcenter_run=3:
	# Skipped frame pauses the run rather than resetting it. Run length
	# accumulates to 4 -> raises.
	traj = [
		_state(200.0, 540.0),  # bad (run=1)
		_state(200.0, 540.0),  # bad (run=2)
		None,                   # skip (run paused at 2)
		_state(200.0, 540.0),  # bad (run=3)
		_state(200.0, 540.0),  # bad (run=4 > max=3) -> raise
	]
	rects = [CROP_RECT] * 5
	with pytest.raises(tr_crop.OffCenterCropError):
		_validate(traj, rects)


def test_validate_reports_first_violation():
	# Trajectory: 50 ok, 10 bad, 50 ok. Error reports frame 50.
	traj = (
		[_state(960.0, 540.0) for _ in range(50)] +
		[_state(200.0, 540.0) for _ in range(10)] +
		[_state(960.0, 540.0) for _ in range(50)]
	)
	rects = [CROP_RECT] * len(traj)
	with pytest.raises(tr_crop.OffCenterCropError) as exc:
		_validate(traj, rects)
	assert exc.value.first_violating_frame == 50


def test_validate_ignores_later_violations_after_first():
	# Two separate bad runs; the error reports only the first.
	traj = (
		[_state(960.0, 540.0) for _ in range(50)] +
		[_state(200.0, 540.0) for _ in range(10)] +
		[_state(960.0, 540.0) for _ in range(50)] +
		[_state(200.0, 540.0) for _ in range(10)]
	)
	rects = [CROP_RECT] * len(traj)
	with pytest.raises(tr_crop.OffCenterCropError) as exc:
		_validate(traj, rects)
	assert exc.value.first_violating_frame == 50


def test_encode_parser_accepts_allow_offcenter_crop():
	args = _parse(["-i", "x.MOV", "encode", "--allow-offcenter-crop"])
	assert args.allow_offcenter_crop is True


def test_encode_parser_default_disallows_offcenter_crop():
	args = _parse(["-i", "x.MOV", "encode"])
	assert args.allow_offcenter_crop is False


#============================================
# Centered-fit-to-source helper. Pure-math tests on _max_centered_fit_size.
# All asserts are on geometric invariants (aspect preserved, fit obeys
# bounds), not on tunable constants.

def _aspect_ok(fit_w: float, fit_h: float, aspect_ratio: float) -> bool:
	"""True when fit_w / fit_h matches aspect_ratio within float epsilon."""
	if fit_h <= 0:
		return False
	return abs(fit_w / fit_h - aspect_ratio) < 1e-9


def test_max_centered_fit_passes_through_when_fits():
	# cx, cy at frame center; desired crop smaller than frame; helper
	# returns the requested size unchanged.
	fw, fh = 1920, 1080
	aspect = 16.0 / 9.0
	desired_h = 540.0
	desired_w = desired_h * aspect
	fit_w, fit_h = tr_crop._max_centered_fit_size(
		fw / 2.0, fh / 2.0, desired_w, fw, fh, aspect,
	)
	assert fit_w == desired_w
	assert fit_h == desired_h


def test_max_centered_fit_horizontal_bound():
	# cx near the left edge: horizontal bound = 2*cx.
	fw, fh = 2000, 2000
	aspect = 1.0
	# desired huge so the bound binds; cx=300 -> max_w_horiz = 600.
	fit_w, fit_h = tr_crop._max_centered_fit_size(
		300.0, 1000.0, 5000.0, fw, fh, aspect,
	)
	assert fit_w == 600.0
	assert _aspect_ok(fit_w, fit_h, aspect)


def test_max_centered_fit_vertical_bound():
	# cy near the top edge with a wide aspect; vertical bound binds.
	fw, fh = 4000, 1000
	aspect = 4.0  # wide
	# cy=200 -> max_w_vert = 4 * 2 * 200 = 1600. cx far from edges.
	fit_w, fit_h = tr_crop._max_centered_fit_size(
		2000.0, 200.0, 5000.0, fw, fh, aspect,
	)
	assert fit_w == 1600.0
	assert _aspect_ok(fit_w, fit_h, aspect)


def test_max_centered_fit_corner_bound_takes_smaller():
	# cx and cy both near edges; helper returns the smaller bound.
	fw, fh = 1000, 1000
	aspect = 1.0
	# cx=100 -> horiz=200; cy=300 -> vert=600. horiz wins.
	fit_w, _ = tr_crop._max_centered_fit_size(
		100.0, 300.0, 5000.0, fw, fh, aspect,
	)
	assert fit_w == 200.0


def test_max_centered_fit_does_not_exceed_desired():
	# When the centered crop fits with room to spare, the helper still
	# caps at desired_w (not the bound).
	fw, fh = 5000, 5000
	aspect = 1.0
	fit_w, fit_h = tr_crop._max_centered_fit_size(
		2500.0, 2500.0, 1000.0, fw, fh, aspect,
	)
	assert fit_w == 1000.0
	assert fit_h == 1000.0


#============================================
# direct_center end-to-end: with the new flag default-on, the crop is
# always centered on the runner regardless of how aggressive the
# torso_multiple is.

def _full_traj(n: int, cx: float, cy: float) -> list:
	"""Build a constant-position trajectory for direct_center tests."""
	return [
		{"cx": cx, "cy": cy, "h": 100.0, "w": 60.0, "conf": 1.0}
		for _ in range(n)
	]


def test_direct_center_keeps_runner_centered_when_zoom_too_aggressive():
	# Reproduces the user's scenario: source 2816x1584, aspect 23:9,
	# very aggressive torso_multiple. Runner left of frame center.
	# With centered_fit_to_source on (default), the runner must end
	# up at output center for every frame.
	fw, fh = 2816, 1584
	traj = _full_traj(20, cx=1048.9, cy=703.4)
	cfg = {
		"processing": {
			"crop_aspect": "23:9",
			"torso_height_multiple": 8.0,
			"crop_min_size": 200,
			"crop_containment_radius": 0.0,
			"crop_max_height_change": 0.0,
		},
	}
	rects = tr_crop.direct_center_crop_trajectory(traj, fw, fh, cfg)
	for x, y, w, h in rects:
		# crop center == runner scene center (within 1 px due to int cast)
		crop_cx = x + w / 2.0
		crop_cy = y + h / 2.0
		assert abs(crop_cx - 1048.9) <= 1.0
		assert abs(crop_cy - 703.4) <= 1.0


def test_direct_center_aspect_preserved_under_fit():
	# Same scenario; assert every crop_rect preserves the configured
	# aspect within 1 px tolerance.
	fw, fh = 2816, 1584
	aspect = 23.0 / 9.0
	traj = _full_traj(10, cx=600.0, cy=400.0)
	cfg = {
		"processing": {
			"crop_aspect": "23:9",
			"torso_height_multiple": 8.0,
			"crop_min_size": 200,
			"crop_containment_radius": 0.0,
			"crop_max_height_change": 0.0,
		},
	}
	rects = tr_crop.direct_center_crop_trajectory(traj, fw, fh, cfg)
	for _, _, w, h in rects:
		assert h > 0
		assert abs(w / h - aspect) <= 0.01


def test_direct_center_legacy_black_fill_when_flag_disabled():
	# When crop_centered_fit_to_source is false, the legacy behavior
	# returns -- the crop slides off the frame edge (negative x).
	fw, fh = 2816, 1584
	traj = _full_traj(10, cx=200.0, cy=400.0)
	cfg = {
		"processing": {
			"crop_aspect": "23:9",
			"torso_height_multiple": 8.0,
			"crop_min_size": 200,
			"crop_containment_radius": 0.0,
			"crop_max_height_change": 0.0,
			"crop_centered_fit_to_source": False,
		},
	}
	rects = tr_crop.direct_center_crop_trajectory(traj, fw, fh, cfg)
	# at least one frame has crop_x < 0 (legacy black-fill)
	assert any(rect[0] < 0 for rect in rects)
