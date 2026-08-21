"""Pair-local analytical interpolation between human torso-box anchors.

An interval derives geometry only from its two endpoint seeds.  Human boxes
measure position and size at those frames; they do not measure velocity or
size derivatives.  The endpoint chord is used at both Hermite ends, which is
equivalent to linear center interpolation and log-linear size interpolation.
FWD and BWD still build independent raw passes by using separate confidence
anchors; the optional walker then gathers image evidence independently.
"""

# Standard Library
import math


# Raw-prediction confidence decays from the pass's human-seed anchor.  Keep
# both directions in one builder so their conventions stay aligned.
RAW_PRED_CONFIDENCE_DECAY = 0.97
RAW_PRED_CONFIDENCE_FLOOR = 0.1
RAW_PRED_START_CONFIDENCE = 1.0
RAW_PRED_FORWARD = "forward"
RAW_PRED_BACKWARD = "backward"
_RAW_PRED_DIRECTIONS = frozenset((RAW_PRED_FORWARD, RAW_PRED_BACKWARD))


#============================================
def hermite_interpolate(
	t: float,
	p0: float,
	p1: float,
	m0: float,
	m1: float,
) -> float:
	"""Return cubic Hermite interpolation at normalized position ``t``.

	``m0`` and ``m1`` are derivatives scaled to the interval's unit domain.
	Pair-local callers pass the endpoint chord for both values.
	"""
	h00 = (1.0 + 2.0 * t) * (1.0 - t) ** 2
	h10 = t * (1.0 - t) ** 2
	h01 = t * t * (3.0 - 2.0 * t)
	h11 = t * t * (t - 1.0)
	value = h00 * p0 + h10 * m0 + h01 * p1 + h11 * m1
	return value


#============================================
def fit_interval_curves(
	left_seed: dict,
	right_seed: dict,
	scene_transform: object,
) -> dict:
	"""Convert the two seed boxes for one interval into scene coordinates.

	No third seed contributes to this result.  That keeps the geometry exactly
	within the endpoint fingerprint used for cache reuse.
	"""
	start_frame = int(left_seed["frame_index"])
	end_frame = int(right_seed["frame_index"])
	if start_frame >= end_frame:
		raise ValueError(
			f"degenerate interval: start_frame={start_frame} >= end_frame={end_frame}"
		)
	left_pos_size = scene_transform.pixel_box_to_scene(
		start_frame,
		float(left_seed["cx"]),
		float(left_seed["cy"]),
		float(left_seed["w"]),
		float(left_seed["h"]),
	)
	right_pos_size = scene_transform.pixel_box_to_scene(
		end_frame,
		float(right_seed["cx"]),
		float(right_seed["cy"]),
		float(right_seed["w"]),
		float(right_seed["h"]),
	)
	left_sx, left_sy, left_sw, left_sh = left_pos_size
	right_sx, right_sy, right_sw, right_sh = right_pos_size
	return {
		"left_pos": (left_sx, left_sy),
		"right_pos": (right_sx, right_sy),
		"left_size": (left_sw, left_sh),
		"right_size": (right_sw, right_sh),
		"start_frame": start_frame,
		"end_frame": end_frame,
	}


#============================================
def _log_size(size: float) -> float:
	"""Return a finite log-size for interpolation of positive box dimensions."""
	return math.log(size) if size > 1e-6 else 0.0


#============================================
def _compute_raw_pred(
	interval_curves: dict,
	scene_transform: object,
	direction: str,
) -> list:
	"""Build one chronological raw pass from pair-local endpoint geometry."""
	if direction not in _RAW_PRED_DIRECTIONS:
		raise ValueError(f"Unknown raw prediction direction: {direction}")
	start_frame = interval_curves["start_frame"]
	end_frame = interval_curves["end_frame"]
	left_sx, left_sy = interval_curves["left_pos"]
	right_sx, right_sy = interval_curves["right_pos"]
	left_sw, left_sh = interval_curves["left_size"]
	right_sw, right_sh = interval_curves["right_size"]
	interval_length = float(end_frame - start_frame)
	chord_x = (right_sx - left_sx) / interval_length
	chord_y = (right_sy - left_sy) / interval_length
	log_left_w, log_right_w = _log_size(left_sw), _log_size(right_sw)
	log_left_h, log_right_h = _log_size(left_sh), _log_size(right_sh)
	chord_log_w = (log_right_w - log_left_w) / interval_length
	chord_log_h = (log_right_h - log_left_h) / interval_length
	fallback_w = left_sw if direction == RAW_PRED_FORWARD else right_sw
	fallback_h = left_sh if direction == RAW_PRED_FORWARD else right_sh
	raw = []
	for frame_index in range(start_frame, end_frame + 1):
		t = (frame_index - start_frame) / interval_length
		scene_cx = hermite_interpolate(
			t, left_sx, right_sx, chord_x * interval_length, chord_x * interval_length,
		)
		scene_cy = hermite_interpolate(
			t, left_sy, right_sy, chord_y * interval_length, chord_y * interval_length,
		)
		log_w = hermite_interpolate(
			t, log_left_w, log_right_w,
			chord_log_w * interval_length, chord_log_w * interval_length,
		)
		log_h = hermite_interpolate(
			t, log_left_h, log_right_h,
			chord_log_h * interval_length, chord_log_h * interval_length,
		)
		scene_w = math.exp(log_w) if log_w < 100.0 else fallback_w
		scene_h = math.exp(log_h) if log_h < 100.0 else fallback_h
		pixel_cx, pixel_cy, pixel_w, pixel_h = scene_transform.scene_box_to_pixel(
			frame_index, scene_cx, scene_cy, scene_w, scene_h,
		)
		frames_from_anchor = (
			frame_index - start_frame
			if direction == RAW_PRED_FORWARD
			else end_frame - frame_index
		)
		confidence = max(
			RAW_PRED_CONFIDENCE_FLOOR,
			RAW_PRED_START_CONFIDENCE * RAW_PRED_CONFIDENCE_DECAY ** frames_from_anchor,
		)
		raw.append((
			int(frame_index), float(pixel_cx), float(pixel_cy),
			float(pixel_w), float(pixel_h), float(confidence),
		))
	return raw


#============================================
def _raw_pred_to_states(raw: list) -> list:
	"""Wrap raw endpoint interpolation as normal chronological state dicts."""
	states = []
	for unused_frame_index, raw_cx, raw_cy, width, height, confidence in raw:
		states.append({
			"cx": float(raw_cx),
			"cy": float(raw_cy),
			"w": float(width),
			"h": float(height),
			"conf": float(confidence),
			"source": "propagated",
		})
	return states


#============================================
def propagate_forward_analytical(
	interval_curves: dict,
	scene_transform: object,
) -> list:
	"""Return the FWD raw pass with confidence anchored at the left seed."""
	raw = _compute_raw_pred(interval_curves, scene_transform, RAW_PRED_FORWARD)
	return _raw_pred_to_states(raw)


#============================================
def propagate_backward_analytical(
	interval_curves: dict,
	scene_transform: object,
) -> list:
	"""Return the BWD raw pass with confidence anchored at the right seed."""
	raw = _compute_raw_pred(interval_curves, scene_transform, RAW_PRED_BACKWARD)
	return _raw_pred_to_states(raw)
