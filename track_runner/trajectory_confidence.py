"""Confidence and agreement computed from independent trajectory passes.

This is the single owner of FWD/BWD geometry confidence. It deliberately
accepts only raw forward and backward states so output blending cannot feed
back into uncertainty scoring (contract C9).
"""

# Standard Library
import math

# PIP3 modules
import numpy


# This is the one raw-pass agreement threshold used by output policy.  It is
# expressed in the owner confidence space; callers must not recreate it from
# a separate overlap or pixel-distance rule.
MERGE_MINIMUM_CONFIDENCE = math.exp(-1.0)


def frame_confidence(
	forward_state: dict,
	backward_state: dict,
) -> float:
	"""Return FWD/BWD agreement for one aligned raw-pass frame.

	Center displacement is normalized by the mean raw-pass torso width, so the
	metric is invariant to footage scale (contract C2). Invalid or nonpositive
	widths cannot establish agreement and return 0.0.
	"""
	fwd_w = float(forward_state["w"])
	bwd_w = float(backward_state["w"])
	if fwd_w <= 0.0 or bwd_w <= 0.0:
		return 0.0
	mean_width = (fwd_w + bwd_w) / 2.0
	if not math.isfinite(mean_width) or mean_width <= 0.0:
		return 0.0
	dx = float(forward_state["cx"]) - float(backward_state["cx"])
	dy = float(forward_state["cy"]) - float(backward_state["cy"])
	if not math.isfinite(dx) or not math.isfinite(dy):
		return 0.0
	distance_in_widths = math.hypot(dx, dy) / mean_width
	confidence = math.exp(-distance_in_widths)
	return max(0.0, min(1.0, confidence))


#============================================
def frame_disagrees(forward_state: dict, backward_state: dict) -> bool:
	"""Return whether raw-pass owner confidence requires blend review."""
	result = frame_confidence(forward_state, backward_state) < MERGE_MINIMUM_CONFIDENCE
	return result


#============================================
def interval_agreement(
	forward_path: list,
	backward_path: list,
) -> float:
	"""Return mean aligned-frame confidence, or 0.0 for an empty overlap."""
	span = min(len(forward_path), len(backward_path))
	if span == 0:
		return 0.0
	confidences = [
		frame_confidence(forward_path[i], backward_path[i])
		for i in range(span)
	]
	return float(numpy.mean(confidences))


#============================================
def compute_agreement_debug(
	forward_path: list,
	backward_path: list,
	start_frame: int = 0,
) -> dict:
	"""Return owner agreement with per-frame raw-pass diagnostics."""
	span = min(len(forward_path), len(backward_path))
	if span == 0:
		return {
			"agreement": 0.0,
			"confidence_p10": 0.0,
			"confidence_p50": 0.0,
			"confidence_p90": 0.0,
			"per_frame": [],
		}

	per_frame = []
	confidences = []
	for i in range(span):
		fwd = forward_path[i]
		bwd = backward_path[i]
		confidence = frame_confidence(fwd, bwd)
		confidences.append(confidence)
		fwd_w = float(fwd["w"])
		bwd_w = float(bwd["w"])
		mean_width = (fwd_w + bwd_w) / 2.0
		dx = float(fwd["cx"]) - float(bwd["cx"])
		dy = float(fwd["cy"]) - float(bwd["cy"])
		center_dist = math.hypot(dx, dy)
		per_frame.append({
			"frame_index": start_frame + i,
			"fwd_cx": round(float(fwd["cx"]), 2),
			"fwd_cy": round(float(fwd["cy"]), 2),
			"fwd_w": round(fwd_w, 2),
			"fwd_h": round(float(fwd["h"]), 2),
			"bwd_cx": round(float(bwd["cx"]), 2),
			"bwd_cy": round(float(bwd["cy"]), 2),
			"bwd_w": round(bwd_w, 2),
			"bwd_h": round(float(bwd["h"]), 2),
			"confidence": round(confidence, 4),
			"center_dist_px": round(center_dist, 2),
			"center_dist_widths": round(center_dist / mean_width, 4)
				if mean_width > 0.0 else None,
		})

	return {
		"agreement": interval_agreement(forward_path, backward_path),
		"confidence_p10": float(numpy.percentile(confidences, 10)),
		"confidence_p50": float(numpy.percentile(confidences, 50)),
		"confidence_p90": float(numpy.percentile(confidences, 90)),
		"per_frame": per_frame,
	}


#============================================
def derive_per_frame_confidence(
	interval_results: list,
	n_frames: int,
) -> list:
	"""Derive persisted-frame confidence solely from independent raw passes.

	Pre-race synthetic intervals have no raw passes and occupy their declared
	frame span with confidence 1.0. Frames outside every declared interval
	remain 0.0.
	"""
	confs = [0.0] * n_frames
	for result in interval_results:
		start = int(result["start_frame"])
		fwd = result.get("forward_path")
		bwd = result.get("backward_path")
		if fwd is None or bwd is None:
			end = int(result["end_frame"])
			for frame_index in range(max(0, start), min(n_frames - 1, end) + 1):
				confs[frame_index] = 1.0
			continue
		span = min(len(fwd), len(bwd))
		for i in range(span):
			frame_index = start + i
			if 0 <= frame_index < n_frames:
				confs[frame_index] = frame_confidence(fwd[i], bwd[i])
	return confs


#============================================
def apply_per_frame_confidence(
	interval_results: list,
	trajectory: list,
) -> list:
	"""Apply owner-derived confidence to one reconstructed trajectory.

	Stored interval geometry omits per-frame confidence, so fresh and cached
	solve, analyze, and encode paths must all cross this same boundary before a
	consumer reads ``state["conf"]``.
	"""
	confs = derive_per_frame_confidence(interval_results, len(trajectory))
	for frame_index, state in enumerate(trajectory):
		if state is not None:
			state["conf"] = confs[frame_index]
	return trajectory
