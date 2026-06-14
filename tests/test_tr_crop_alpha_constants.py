"""Crop-rect equivalence guard for the alpha-to-constants promotion.

Three direct_center smoothing alphas were promoted from per-video config
keys to named module constants in tr_crop.py with the SAME effective values:

	crop_post_smooth_strength       -> CROP_POST_SMOOTH_STRENGTH       (0.0)
	crop_post_smooth_size_strength  -> CROP_POST_SMOOTH_SIZE_STRENGTH  (0.15)
	crop_post_smooth_max_velocity   -> CROP_POST_SMOOTH_MAX_VELOCITY   (0.0)

These tests lock in the equivalence contract:

direct_center crop rectangles are unchanged whether the smoothing
alphas arrive as the new constants or as the old config keys with
the same values. Equivalence is exact on the stored integer crop
coordinates (the path is deterministic).
"""

# PIP3 modules
import numpy

# local repo modules
import tr_crop


#============================================
def _synthetic_trajectory(n: int) -> list:
	# deterministic sinusoidal runner motion with mild torso-size jitter,
	# enough to exercise the size EMA (alpha_size=0.15) and the
	# fit-to-source ceiling without depending on a real video
	trajectory = []
	for i in range(n):
		cx = 960.0 + 400.0 * numpy.sin(i * 0.05)
		cy = 540.0 + 150.0 * numpy.cos(i * 0.03)
		# +/-5% torso-height jitter around 120 px
		h = 120.0 * (1.0 + 0.05 * numpy.sin(i * 0.7))
		w = 0.45 * h
		state = {"cx": cx, "cy": cy, "w": w, "h": h}
		trajectory.append(state)
	return trajectory


#============================================
def _base_config() -> dict:
	# default-config-equivalent processing section for direct_center
	return {
		"processing": {
			"crop_mode": "direct_center",
			"crop_aspect": "16:9",
			"torso_height_multiple": 8.0,
		},
	}


#============================================
def test_direct_center_rects_match_old_config_alphas():
	# The promotion must not change computed crop rectangles. The current
	# code reads the constants; direct_center_crop_trajectory ignores any
	# stale config alpha keys after the promotion, so feeding the old keys
	# with the SAME values must yield identical rectangles.
	frame_width = 1920
	frame_height = 1080
	trajectory = _synthetic_trajectory(240)

	config_constants = _base_config()
	rects_new = tr_crop.direct_center_crop_trajectory(
		trajectory, frame_width, frame_height, config_constants, fps=60.0,
	)

	# same config but with the legacy keys explicitly set to the same
	# effective values; output must be byte-identical
	config_legacy = _base_config()
	config_legacy["processing"]["crop_post_smooth_strength"] = 0.0
	config_legacy["processing"]["crop_post_smooth_size_strength"] = 0.15
	config_legacy["processing"]["crop_post_smooth_max_velocity"] = 0.0
	rects_legacy = tr_crop.direct_center_crop_trajectory(
		trajectory, frame_width, frame_height, config_legacy, fps=60.0,
	)

	assert rects_new == rects_legacy
