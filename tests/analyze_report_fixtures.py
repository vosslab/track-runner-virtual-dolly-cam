"""Shared synthetic-input fixture for analyze_report integration tests.

Lives in tests/ (not in track_runner/) so it does not accidentally ship as
production code. Imported by tests that need a deterministic 30-frame
trajectory + matching crop rects + a MotionTrack-shaped object + an
identity SceneTransform stub + fps + config.
"""

# Standard Library
import types

# PIP3 modules
import numpy


#============================================
class _IdentitySceneTransform:
	"""Stub SceneTransform that returns pixel coords unchanged.

	Used by tests where the runner-speed projection helper must operate on
	known scene coordinates without depending on a real camera-motion-derived
	transform.
	"""

	def pixel_to_scene(self, frame_index, px, py):
		return (px, py)


#============================================
def make_synthetic_inputs():
	"""Return (trajectory, crop_rects, motion_track, scene_transform, fps, config).

	30 frames of constant-velocity motion in pixel space; identity scene
	transform; constant-magnitude camera motion; achieved zoom multiple of 5.
	"""
	n_frames = 30
	# constant velocity: +2px/frame in x, +1.5px/frame in y; fixed torso size
	trajectory = [
		{
			'cx': 100.0 + 2.0 * i,
			'cy': 50.0 + 1.5 * i,
			'w': 20.0,
			'h': 40.0,
		}
		for i in range(n_frames)
	]
	# crop_h=200, torso_h=40 -> achieved multiple = 5 (matches torso_height_multiple in config)
	crop_rects = [(0, 0, 200, 200)] * n_frames
	# constant dx=1, dy=0, scale=1, quality=0.9 for all frames
	motion_track = types.SimpleNamespace(
		dx=numpy.ones(n_frames),
		dy=numpy.zeros(n_frames),
		scale=numpy.ones(n_frames),
		quality=numpy.full(n_frames, 0.9),
	)
	scene_transform = _IdentitySceneTransform()
	fps = 30.0
	config = {'processing': {'torso_height_multiple': 5.0}}
	return trajectory, crop_rects, motion_track, scene_transform, fps, config
