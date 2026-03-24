#!/usr/bin/env python3
"""Scene coordinate transformations for tracked camera motion.

Converts between pixel coordinates (frame-specific) and scene coordinates
(anchored at frame 0) using cumulative camera motion (translation and scale).
"""

# Standard Library
# (none)

# PIP3 modules
import numpy

# local repo modules
import camera_motion


#============================================
class SceneTransform:
	"""Convert between pixel and scene coordinates using camera motion.

	Scene coordinates are anchored at frame 0. For any frame, we compute
	cumulative translation and scale from the motion track, then use these
	to transform between pixel and scene spaces.

	Args:
		motion_track: MotionTrack instance with per-frame motion data.
	"""

	#============================================
	def __init__(
		self,
		motion_track: object,
	):
		"""Initialize SceneTransform from motion track.

		Args:
			motion_track: MotionTrack with dx, dy, scale arrays.
		"""
		self.motion_track = motion_track
		# precompute cumulative motion for efficiency
		self.cum_dx = numpy.cumsum(motion_track.dx)
		self.cum_dy = numpy.cumsum(motion_track.dy)
		self.cum_scale = numpy.cumprod(motion_track.scale)

	#============================================
	def pixel_to_scene(
		self,
		frame_idx: int,
		px: float,
		py: float,
	) -> tuple[float, float]:
		"""Convert pixel coordinates to scene coordinates.

		Args:
			frame_idx: Frame index (0-based).
			px: X coordinate in frame pixels.
			py: Y coordinate in frame pixels.

		Returns:
			Tuple (scene_x, scene_y) in scene coordinate space.
		"""
		# retrieve cumulative translation and scale at this frame
		cum_dx = float(self.cum_dx[frame_idx])
		cum_dy = float(self.cum_dy[frame_idx])
		cum_scale = float(self.cum_scale[frame_idx])

		# invert the affine transform: pixel = scene * scale + translation
		# so scene = (pixel - translation) / scale
		scene_x = (px - cum_dx) / cum_scale
		scene_y = (py - cum_dy) / cum_scale

		return (scene_x, scene_y)

	#============================================
	def scene_to_pixel(
		self,
		frame_idx: int,
		sx: float,
		sy: float,
	) -> tuple[float, float]:
		"""Convert scene coordinates to pixel coordinates.

		Args:
			frame_idx: Frame index (0-based).
			sx: X coordinate in scene space.
			sy: Y coordinate in scene space.

		Returns:
			Tuple (pixel_x, pixel_y) in frame pixel space.
		"""
		# retrieve cumulative translation and scale at this frame
		cum_dx = float(self.cum_dx[frame_idx])
		cum_dy = float(self.cum_dy[frame_idx])
		cum_scale = float(self.cum_scale[frame_idx])

		# forward transform: pixel = scene * scale + translation
		px = sx * cum_scale + cum_dx
		py = sy * cum_scale + cum_dy

		return (px, py)

	#============================================
	def pixel_box_to_scene(
		self,
		frame_idx: int,
		cx: float,
		cy: float,
		w: float,
		h: float,
	) -> tuple[float, float, float, float]:
		"""Transform bounding box from pixel to scene coordinates.

		Args:
			frame_idx: Frame index (0-based).
			cx: X position of box center in pixels.
			cy: Y position of box center in pixels.
			w: Width of box in pixels.
			h: Height of box in pixels.

		Returns:
			Tuple (scene_cx, scene_cy, scene_w, scene_h).
		"""
		# transform center position
		scene_cx, scene_cy = self.pixel_to_scene(frame_idx, cx, cy)

		# retrieve cumulative scale at this frame
		cum_scale = float(self.cum_scale[frame_idx])

		# scale the size: scene_size = pixel_size / cum_scale
		scene_w = w / cum_scale
		scene_h = h / cum_scale

		return (scene_cx, scene_cy, scene_w, scene_h)

	#============================================
	def scene_box_to_pixel(
		self,
		frame_idx: int,
		sx: float,
		sy: float,
		sw: float,
		sh: float,
	) -> tuple[float, float, float, float]:
		"""Transform bounding box from scene to pixel coordinates.

		Args:
			frame_idx: Frame index (0-based).
			sx: X position of box center in scene space.
			sy: Y position of box center in scene space.
			sw: Width of box in scene space.
			sh: Height of box in scene space.

		Returns:
			Tuple (pixel_cx, pixel_cy, pixel_w, pixel_h).
		"""
		# transform center position
		pixel_cx, pixel_cy = self.scene_to_pixel(frame_idx, sx, sy)

		# retrieve cumulative scale at this frame
		cum_scale = float(self.cum_scale[frame_idx])

		# scale the size: pixel_size = scene_size * cum_scale
		pixel_w = sw * cum_scale
		pixel_h = sh * cum_scale

		return (pixel_cx, pixel_cy, pixel_w, pixel_h)
