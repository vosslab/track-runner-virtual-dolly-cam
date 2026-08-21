"""Stateful smooth crop controller and controller dispatch helpers."""

# Standard Library
import math

# local repo modules
import tr_crop_math


#============================================
class CropController:
	"""Adaptive online crop controller."""

	def __init__(
		self,
		frame_width: int,
		frame_height: int,
		aspect_ratio: float = 1.0,
		target_fill_ratio: float = 0.30,
		smoothing_attack: float = 0.15,
		smoothing_release: float = 0.05,
		max_crop_velocity: float = 30.0,
		deadband_fraction: float = 0.02,
		velocity_scale: float = 2.0,
		displacement_alpha: float = 0.1,
	) -> None:
		self.frame_width = frame_width
		self.frame_height = frame_height
		self.aspect_ratio = aspect_ratio
		self.target_fill_ratio = target_fill_ratio
		self.smoothing_attack = smoothing_attack
		self.smoothing_release = smoothing_release
		self.max_crop_velocity = max_crop_velocity
		self.deadband_fraction = deadband_fraction
		self.velocity_scale = velocity_scale
		self.displacement_alpha = displacement_alpha
		self.smooth_cx = None
		self.smooth_cy = None
		self.smooth_size = None
		self._ema_displacement = 0.0
		self._prev_desired_cx = None
		self._prev_desired_cy = None

	#============================================
	def update(self, state: dict) -> tuple:
		"""Update and return one contained integer crop rectangle."""
		desired_h = max(1.0, min(state["h"] / self.target_fill_ratio, self.frame_height))
		desired_w = desired_h * self.aspect_ratio
		if desired_w > self.frame_width:
			desired_w = float(self.frame_width)
			desired_h = desired_w / self.aspect_ratio
		desired_cx = state["cx"]
		desired_cy = state["cy"]
		if self._prev_desired_cx is None:
			adaptive_cap = self.max_crop_velocity
		else:
			displacement = math.hypot(
				desired_cx - self._prev_desired_cx,
				desired_cy - self._prev_desired_cy,
			)
			self._ema_displacement = (
				self.displacement_alpha * displacement
				+ (1.0 - self.displacement_alpha) * self._ema_displacement
			)
			adaptive_cap = max(self.max_crop_velocity, self.velocity_scale * self._ema_displacement)
		self._prev_desired_cx = desired_cx
		self._prev_desired_cy = desired_cy
		if self.smooth_cx is None:
			self.smooth_cx = desired_cx
			self.smooth_cy = desired_cy
			self.smooth_size = desired_h
		else:
			old_cx = self.smooth_cx
			old_cy = self.smooth_cy
			deadband = self.deadband_fraction * self.smooth_size
			attack = deadband * 4.0
			for name, desired in (
				("smooth_cx", desired_cx),
				("smooth_cy", desired_cy),
				("smooth_size", desired_h),
			):
				current = getattr(self, name)
				error = desired - current
				if abs(error) >= deadband:
					alpha = self.smoothing_attack if abs(error) > attack else self.smoothing_release
					setattr(self, name, current + max(alpha * state["conf"], 0.02) * error)
			self.smooth_cx = old_cx + max(-adaptive_cap, min(adaptive_cap, self.smooth_cx - old_cx))
			self.smooth_cy = old_cy + max(-adaptive_cap, min(adaptive_cap, self.smooth_cy - old_cy))
		crop_h = self.smooth_size
		crop_w = crop_h * self.aspect_ratio
		crop_x = max(0.0, min(self.smooth_cx - crop_w / 2.0, self.frame_width - crop_w))
		crop_y = max(0.0, min(self.smooth_cy - crop_h / 2.0, self.frame_height - crop_h))
		return (int(crop_x), int(crop_y), int(crop_w), int(crop_h))

	#============================================
	def reset(self) -> None:
		"""Reset the online smoothing state."""
		self.smooth_cx = None
		self.smooth_cy = None
		self.smooth_size = None
		self._ema_displacement = 0.0
		self._prev_desired_cx = None
		self._prev_desired_cy = None

	#============================================
	def get_state(self) -> dict | None:
		"""Return the current smooth state when initialized."""
		if self.smooth_cx is None:
			return None
		return {"cx": self.smooth_cx, "cy": self.smooth_cy, "size": self.smooth_size}


#============================================
def create_crop_controller(config: dict, frame_width: int, frame_height: int) -> CropController:
	"""Create an online crop controller from processing configuration."""
	processing = config["processing"]
	return CropController(
		frame_width,
		frame_height,
		tr_crop_math.parse_aspect_ratio(processing["crop_aspect"]),
		1.0 / float(processing["torso_height_multiple"]),
		float(processing.get("crop_smoothing_attack", 0.15)),
		float(processing.get("crop_smoothing_release", 0.05)),
		float(processing.get("crop_max_velocity", 30.0)),
		velocity_scale=float(processing.get("crop_velocity_scale", 2.0)),
		displacement_alpha=float(processing.get("crop_displacement_alpha", 0.1)),
	)


#============================================
def compute_crop_trajectory(
	trajectory: list,
	frame_width: int,
	frame_height: int,
	config: dict,
) -> list:
	"""Run the online controller over a dense trajectory."""
	controller = create_crop_controller(config, frame_width, frame_height)
	return [controller.update(state) for state in trajectory]
