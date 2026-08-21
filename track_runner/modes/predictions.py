"""SOURCE-space prediction assembly shared by annotation modes."""

import os

import common_tools.coord_space
import interval_solver
import review
import state_io
import torso_box_coords_io


#============================================
def source_box_from_prediction_state(
	state: dict | common_tools.coord_space.SourceBox,
) -> common_tools.coord_space.SourceBox:
	"""Return one SOURCE-frame prediction as its explicit box type.

	The interval artifact deliberately remains a schema-shaped dict. This is
	the in-memory boundary between that persisted representation and UI
	predictions, which must carry an explicit SOURCE-space box.
	"""
	if isinstance(state, common_tools.coord_space.SourceBox):
		box = common_tools.coord_space.require_source_box(state)
		return box
	if isinstance(state, (
		common_tools.coord_space.ProcessedBox,
		common_tools.coord_space.SourcePoint,
		common_tools.coord_space.ProcessedPoint,
	)):
		common_tools.coord_space.require_source_box(state)
	box = common_tools.coord_space.SourceBox(
		cx=float(state["cx"]), cy=float(state["cy"]),
		w=float(state["w"]), h=float(state["h"]),
	)
	return box


#============================================
def build_predictions_from_solved_intervals(solved_data: dict) -> dict:
	"""Build frame-indexed SOURCE predictions with interval metadata.

	Live M3 blended states retain their ephemeral commitment review item. The
	coordinate-only NPZ artifact intentionally does not preserve that item.
	"""
	fps = float(solved_data.get("fps", 30.0))
	predictions = {}
	for interval_idx, iv in enumerate(solved_data.get("intervals", [])):
		fwd_track = iv.get("forward_path")
		bwd_track = iv.get("backward_path")
		blended_path = iv.get("blended_path")

		# Build interval quality metadata once per interval.
		score = iv.get("interval_score", {})
		if score:
			severity = review.classify_interval_severity(iv, fps)
			conf_label = score.get("confidence_tier", "unknown")
			agree_val = float(score.get("agreement", 0.0))
			secondary_val = float(score.get("velocity_consistency", 0.0))
		else:
			severity = None
			conf_label = "unknown"
			agree_val = 0.0
			secondary_val = 0.0
		interval_info = {
			"severity": severity,
			"confidence": conf_label,
			"agreement": agree_val,
			"velocity_consistency": secondary_val,
			"reasons": score.get("failure_reasons", []),
		}

		start_frame = int(iv["start_frame"])
		if fwd_track is not None and bwd_track is not None:
			n = min(len(fwd_track), len(bwd_track))
			for i in range(n):
				frame_index = start_frame + i
				frame_interval_info = interval_info
				fwd_box = source_box_from_prediction_state(fwd_track[i])
				bwd_box = source_box_from_prediction_state(bwd_track[i])
				frame_preds = {"forward": fwd_box, "backward": bwd_box}
				if blended_path is not None and i < len(blended_path):
					blended_state = blended_path[i]
					frame_preds["blended"] = source_box_from_prediction_state(
						blended_state,
					)
					review_item = review.format_blend_commitment_review_item(
						blended_state,
					)
					if review_item is not None:
						frame_interval_info = dict(interval_info)
						frame_interval_info["commitment_review_item"] = review_item
				frame_preds["interval_info"] = frame_interval_info
				frame_preds["consensus"] = common_tools.coord_space.SourceBox(
					cx=(fwd_box.cx + bwd_box.cx) / 2.0,
					cy=(fwd_box.cy + bwd_box.cy) / 2.0,
					w=(fwd_box.w + bwd_box.w) / 2.0,
					h=(fwd_box.h + bwd_box.h) / 2.0,
				)
				predictions[frame_index] = frame_preds
		elif blended_path is not None:
			for i in range(len(blended_path)):
				frame_index = start_frame + i
				blended_state = blended_path[i]
				frame_interval_info = interval_info
				review_item = review.format_blend_commitment_review_item(
					blended_state,
				)
				if review_item is not None:
					frame_interval_info = dict(interval_info)
					frame_interval_info["commitment_review_item"] = review_item
				frame_preds = {
					"blended": source_box_from_prediction_state(blended_state),
					"interval_info": frame_interval_info,
				}
				predictions[frame_index] = frame_preds

	return predictions


#============================================
def predictions_from_torso_box_coords(
	torso_box_coords_path: str,
	diag_path: str,
	fps: float,
	seeds: list | None = None,
) -> dict:
	"""Build SOURCE predictions with advisory scores and optional C3 seeds."""
	intervals_file = torso_box_coords_io.load_torso_box_coords(torso_box_coords_path)
	solved_intervals = intervals_file.get("solved_intervals", {})
	if not solved_intervals:
		return {}
	if seeds is not None:
		interval_solver.restamp_cached_interval_seed_truth(solved_intervals, seeds)
	intervals_list = list(solved_intervals.values())
	if not os.path.isfile(diag_path):
		raise RuntimeError(
			f"interval scores are missing at {diag_path}; run 'solve' first"
		)
	scored_by_key = {}
	score_data = state_io.load_interval_scores(diag_path)
	for scored_iv in score_data.get("intervals", []):
		key = (int(scored_iv["start_frame"]), int(scored_iv["end_frame"]))
		scored_by_key[key] = scored_iv["interval_score"]
	missing_score_count = 0
	for iv in intervals_list:
		key = (int(iv["start_frame"]), int(iv["end_frame"]))
		if key not in scored_by_key:
			missing_score_count += 1
			continue
		iv["interval_score"] = scored_by_key[key]
	if missing_score_count > 0:
		raise RuntimeError(
			f"{missing_score_count} torso intervals lack matching current scores; "
			"run 'solve' first"
		)
	result = build_predictions_from_solved_intervals(
		{"intervals": intervals_list, "fps": float(fps)}
	)
	return result
