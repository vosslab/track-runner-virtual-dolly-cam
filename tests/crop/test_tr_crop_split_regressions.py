"""Portable behavior guards for the crop facade/module split."""

# PIP3 modules
import pytest

# local repo modules
import tr_crop
import tr_crop_direct


#============================================
def _state(height: float) -> dict:
	"""Return one centered state whose width matches its height."""
	state = {
		"cx": 500.0,
		"cy": 500.0,
		"w": height,
		"h": height,
		"conf": 1.0,
	}
	return state


#============================================
def _video_info(frame_count: int) -> dict:
	"""Return portable source-video metadata."""
	info = {
		"width": 1000,
		"height": 1000,
		"frame_count": frame_count,
		"fps": 30.0,
	}
	return info


#============================================
def _direct_config() -> dict:
	"""Return a direct-center config without source-fit interference."""
	config = {
		"processing": {
			"crop_mode": "direct_center",
			"crop_aspect": "1:1",
			"torso_height_multiple": 1.0,
			"crop_centered_fit_to_source": False,
			"crop_containment_radius": 0.0,
			"crop_max_height_change": 0.0,
		},
	}
	return config


#============================================
def test_facade_injects_its_public_size_strength(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Monkeypatching the facade constant changes facade direct output."""
	trajectory = (
		[_state(100.0) for _ in range(5)]
		+ [_state(300.0) for _ in range(10)]
		+ [_state(100.0) for _ in range(5)]
	)
	config = _direct_config()
	default_rects = tr_crop.trajectory_to_crop_rects(
		trajectory,
		_video_info(len(trajectory)),
		config,
	)
	monkeypatch.setattr(tr_crop, "CROP_POST_SMOOTH_SIZE_STRENGTH", 0.0)
	patched_rects = tr_crop.trajectory_to_crop_rects(
		trajectory,
		_video_info(len(trajectory)),
		config,
	)

	assert patched_rects != default_rects
	assert patched_rects[9][3] == 300


#============================================
def test_alpha_zero_zoom_stabilization_allows_only_sustained_reversal() -> None:
	"""A normal reversal needs five meaningful frames before crop shrinkage."""
	trajectory = [_state(100.0), _state(120.0)] + [_state(100.0) for _ in range(5)]
	config = _direct_config()
	config["processing"]["crop_zoom_stabilization"] = True
	config["processing"]["crop_max_height_change"] = 1.0
	rects = tr_crop_direct.direct_center_crop_trajectory(
		trajectory,
		1000,
		1000,
		config,
		_size_smoothing_strength=0.0,
	)
	heights = [rect[3] for rect in rects]

	assert heights[2:6] == [120, 120, 120, 120]
	assert heights[6] < heights[5]


#============================================
def test_direct_mode_missing_keys_reports_the_actual_set() -> None:
	"""Facade validation names exactly which established direct key is absent."""
	trajectory = [_state(100.0)]
	del trajectory[0]["cy"]
	with pytest.raises(RuntimeError) as exc:
		tr_crop.trajectory_to_crop_rects(
			trajectory,
			_video_info(1),
			_direct_config(),
		)

	assert str(exc.value) == (
		"Trajectory entry 0 missing required keys for direct_center mode: {'cy'}"
	)


#============================================
def test_offcenter_diagnosis_retains_size_source_and_black_fill_details() -> None:
	"""An edge failure explains its concrete crop and source consequences."""
	edge, cause = tr_crop._diagnose_offcenter_cause(
		(-20, 10, 100, 80),
		1920,
		1080,
		3.0,
		16.0 / 9.0,
	)

	assert edge == "left"
	assert "1920x1080" in cause and "crop width of 100" in cause
	assert "black-filled" in cause and "left edge" in cause
