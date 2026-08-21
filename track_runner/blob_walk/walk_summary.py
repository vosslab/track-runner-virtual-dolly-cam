"""One-direction walker summary data and metrics."""

# Standard Library
import dataclasses
import math


#============================================
@dataclasses.dataclass
class WalkSummary:
	"""Summary of a one-direction processed-space blob walk."""
	accepts: list
	stop_frame: int
	stop_reason: str
	total_frames_visited: int
	accepted_count: int
	interpolated_count: int
	extrapolated_count: int
	soft_miss_no_blob_count: int
	soft_miss_no_path_count: int
	longest_no_accept_streak: int
	accepted_fraction: float
	last_accepted_frame_index: object
	final_displacement_to_neighbor_px: object
	mode_disagreement_count: int
	direction_path: list
	direction_trace_map: dict


#============================================
def compute_summary_metrics(
	all_emitted_statuses: list,
	visited_frames: set,
	status_counts: dict,
	accepts: list,
	last_accepted_cx: float,
	last_accepted_cy: float,
	neighbor_seed_cx: float | None,
	neighbor_seed_cy: float | None,
	frame_f: int,
	stop_reason: str,
	direction_path: list,
	direction_trace_map: dict,
) -> WalkSummary:
	"""Build metrics from one independently accumulated walk state."""
	longest_no_accept_streak = 0
	current_streak = 0
	for status in all_emitted_statuses:
		if status == "accepted":
			longest_no_accept_streak = max(longest_no_accept_streak, current_streak)
			current_streak = 0
		else:
			current_streak += 1
	longest_no_accept_streak = max(longest_no_accept_streak, current_streak)

	accepted_count = status_counts["accepted"]
	interpolated_count = status_counts["interpolated"]
	extrapolated_count = status_counts["extrapolated"]
	soft_miss_no_blob_count = status_counts["soft_miss_no_blob"]
	soft_miss_no_path_count = status_counts["soft_miss_no_path"]
	denominator = (
		accepted_count + interpolated_count + extrapolated_count
		+ soft_miss_no_blob_count + soft_miss_no_path_count
	)
	accepted_fraction = accepted_count / denominator if denominator > 0 else 0.0
	last_accepted_frame_index = accepts[-1] if accepts else None
	if last_accepted_cx is not None and neighbor_seed_cx is not None:
		dx = last_accepted_cx - neighbor_seed_cx
		neighbor_y = neighbor_seed_cy if neighbor_seed_cy is not None else last_accepted_cy
		dy = last_accepted_cy - neighbor_y
		final_displacement_to_neighbor_px = math.sqrt(dx * dx + dy * dy)
	else:
		final_displacement_to_neighbor_px = None
	summary = WalkSummary(
		accepts=accepts,
		stop_frame=frame_f,
		stop_reason=stop_reason,
		total_frames_visited=len(visited_frames),
		accepted_count=accepted_count,
		interpolated_count=interpolated_count,
		extrapolated_count=extrapolated_count,
		soft_miss_no_blob_count=soft_miss_no_blob_count,
		soft_miss_no_path_count=soft_miss_no_path_count,
		longest_no_accept_streak=longest_no_accept_streak,
		accepted_fraction=accepted_fraction,
		last_accepted_frame_index=last_accepted_frame_index,
		final_displacement_to_neighbor_px=final_displacement_to_neighbor_px,
		mode_disagreement_count=0,
		direction_path=direction_path,
		direction_trace_map=direction_trace_map,
	)
	return summary
