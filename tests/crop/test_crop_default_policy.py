"""Crop geometry from a config that supplies no processing overrides."""

# local repo modules
import tr_crop_direct


#============================================
def _state(cx: float, cy: float, w: float, h: float) -> dict:
	"""Build one dense tracking state for a crop trajectory."""
	state = {"cx": cx, "cy": cy, "w": w, "h": h, "conf": 1.0}
	return state


#============================================
def test_empty_config_produces_square_crops() -> None:
	"""The default aspect yields square crop rectangles."""
	trajectory = [_state(960.0, 540.0, 60.0, 120.0) for _ in range(5)]
	rects = tr_crop_direct.direct_center_crop_trajectory(
		trajectory, 1920, 1080, {},
	)
	assert len(rects) == len(trajectory)
	assert all(width == height for _x, _y, width, height in rects)


#============================================
def test_empty_config_scales_crop_with_torso_height() -> None:
	"""A taller torso yields a taller default crop."""
	short_run = [_state(960.0, 540.0, 40.0, 80.0) for _ in range(5)]
	tall_run = [_state(960.0, 540.0, 80.0, 160.0) for _ in range(5)]
	short_rects = tr_crop_direct.direct_center_crop_trajectory(
		short_run, 1920, 1080, {},
	)
	tall_rects = tr_crop_direct.direct_center_crop_trajectory(
		tall_run, 1920, 1080, {},
	)
	assert tall_rects[0][3] > short_rects[0][3]
