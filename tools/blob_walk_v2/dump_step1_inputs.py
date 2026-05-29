#!/usr/bin/env python3
"""Dump bootstrap inputs (steps 0..3) for blob walker v2 to JSON for analysis.

WP-1A 3-frame sequence: Capture raw step-0 plus three bootstrap steps (1, 2, 3
for FWD; -1, -2, -3 for BWD) for the blob walker on target intervals. For each
interval, emit a JSON file with seed positions, predicted centers (zero-velocity
bootstrap: prev stays at the seed across all steps per WP-1A), all corridor
blobs, the selected winner, and the per-step motion gate result.

Used by replay_step1.py and sweep_radius.py to evaluate Model 3 as a 3-frame
sequence acceptance test instead of a single-frame bar (matches WP-1A
bootstrap N=3 handoff to tracking).
"""

# Standard Library
import argparse
import json
import os
import pathlib
import random
import sys

# PIP3 modules
import numpy

# Determine repo root from file location and add directories to path
# dump_step1_inputs.py is at tools/blob_walk_v2/, so go up 3 levels to repo root
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, 'track_runner')
_TESTS_DIR = os.path.join(_REPO_ROOT, 'tests')
if _TRACK_RUNNER_DIR not in sys.path:
	sys.path.insert(0, _TRACK_RUNNER_DIR)
if _TESTS_DIR not in sys.path:
	sys.path.insert(0, _TESTS_DIR)
# Ensure repo root is on sys.path for common_tools
if _REPO_ROOT not in sys.path:
	sys.path.insert(0, _REPO_ROOT)

# local repo modules
import walk_io
import walk_walker
import walk_motion_gate
import residual_motion


#============================================
def _residual_stats(residual_pre_dog) -> tuple:
	"""Compute residual_sum and residual_nonzero_px from a residual array.

	These two scalars let downstream classifiers distinguish 'no_residual'
	(heat-map truly empty, sum=0, nonzero_px=0) from 'no_raw_blobs'
	(heat-map has signal but DoG found nothing).

	Args:
		residual_pre_dog: numpy array or None. If None, both values are 0.

	Returns:
		tuple: (residual_sum, residual_nonzero_px) as (float, int).
	"""
	if residual_pre_dog is None:
		return 0.0, 0
	# sum of all values in the ROI residual map
	r_sum = float(numpy.sum(residual_pre_dog))
	# count of pixels with value > 0
	r_nonzero = int(numpy.count_nonzero(residual_pre_dog))
	return r_sum, r_nonzero


#============================================
def dump_step1_for_interval(
	interval,
	video_basename,
	seeds_dict,
	reader,
	probe_info,
	scene_transform,
	output_dir,
	sign,
) -> dict:
	"""Dump step 0 and step 1 inputs for one direction of one interval.

	Args:
		interval: SeedToSeedInterval with left_seed, right_seed, label.
		video_basename: Video base name for output path.
		seeds_dict: Loaded seeds dict.
		reader: FrameReader instance.
		probe_info: Video probe info (fps, frame_count, etc).
		scene_transform: SceneTransform for pixel_to_scene.
		output_dir: Root output directory (dump_step1/).
		sign: +1 for FWD, -1 for BWD.

	Returns:
		dict with dump data, or None on failure.
	"""
	fps = probe_info["fps"]
	stride = residual_motion.resolve_stride(fps)

	# Pick seed direction based on sign
	if sign > 0:
		seed = interval.left_seed
		neighbor_seed = interval.right_seed
		direction = "FWD"
	else:
		seed = interval.right_seed
		neighbor_seed = interval.left_seed
		direction = "BWD"

	seed_frame = seed["frame_index"]
	# Option A (2026-05-29): convert seed coords to processed-pixel space.
	# observe_blob_at now expects processed coords for all inputs.
	# reader.geometry.source_to_processed / source_to_processed_delta perform
	# the conversion; at bin_factor=1 these are no-ops.
	geometry = reader.geometry
	seed_cx_src = seed["cx"]
	seed_cy_src = seed["cy"]
	seed_cx, seed_cy = geometry.source_to_processed(seed_cx_src, seed_cy_src)
	seed_w, _ = geometry.source_to_processed_delta(seed["w"], 0.0)
	seed_h, _ = geometry.source_to_processed_delta(seed["h"], 0.0)
	# neighbor_seed is no longer used for prediction (no chord prior); kept on
	# the interval object for downstream callers but not consumed here.
	_ = neighbor_seed

	# Prepare output path
	video_dir = pathlib.Path(output_dir) / video_basename
	video_dir.mkdir(parents=True, exist_ok=True)

	output_file = video_dir / f"{direction}_{seed_frame}.json"

	# Prepare residual cache and trace for bootstrap
	residual_cache = {}
	local_tangent = (1.0, 0.0, 0.0, 1.0)

	# Bootstrap (step 0): seed-local-shape observe_blob_at.
	# All coords below are in PROCESSED-pixel space (Option A, 2026-05-29).
	acceptance_box = (
		seed_cx - 0.5 * seed_w,
		seed_cy - 0.75 * seed_h,
		seed_cx + 0.5 * seed_w,
		seed_cy + 0.75 * seed_h,
	)
	roi_pad = max(20, seed_w)
	roi_x1 = max(0, int(acceptance_box[0] - roi_pad))
	roi_y1 = max(0, int(acceptance_box[1] - roi_pad))
	# Clamp against processed-frame dims (reader.width/height post-bin).
	roi_x2 = min(reader.width, int(acceptance_box[2] + roi_pad))
	roi_y2 = min(reader.height, int(acceptance_box[3] + roi_pad))
	roi_override = (roi_x1, roi_y1, roi_x2, roi_y2)
	# dog_diameter_override in processed-pixel space (observe_blob_at contract)
	dog_diameter_override = 0.7 * seed_w

	# Prepare holder object to capture trace data. observe_blob_at will assign
	# the populated trace to trace_sink.observer_trace, not modify it in place.
	trace_sink_step0 = type('TraceSink', (), {'observer_trace': None})()

	# Call observe_blob_at for step 0
	obs_step0 = residual_motion.observe_blob_at(
		frame_index=seed_frame,
		pred_center=(seed_cx, seed_cy),
		pred_box=(seed_w, seed_h),
		local_tangent=local_tangent,
		scene_transform=scene_transform,
		reader=reader,
		residual_cache=residual_cache,
		fps=fps,
		stride=stride,
		trace_sink=trace_sink_step0,
		roi_override=roi_override,
		dog_diameter_override=dog_diameter_override,
		acceptance_box=acceptance_box,
	)

	# Extract step 0 data from the populated trace
	trace_step0 = trace_sink_step0.observer_trace
	step0_data = {
		"frame_index": seed_frame,
		"step": 0,
		"source_fps": float(fps),
		"seed_cx_px": seed_cx,
		"seed_cy_px": seed_cy,
		"seed_w_px": seed_w,
		"seed_h_px": seed_h,
		"pred_cx_px": seed_cx,
		"pred_cy_px": seed_cy,
		"prev_cx_px": seed_cx,
		"prev_cy_px": seed_cy,
		"prev_cx_scene": float(scene_transform.pixel_to_scene(seed_frame, seed_cx, seed_cy)[0]),
		"prev_cy_scene": float(scene_transform.pixel_to_scene(seed_frame, seed_cx, seed_cy)[1]),
		"v_recent_scene_mag": 0.0,
		"vx_scene": 0.0,
		"vy_scene": 0.0,
		"roi_origin_xy": list(trace_step0.roi_origin_xy) if trace_step0 and trace_step0.roi_origin_xy else None,
		"acceptance_box": list(trace_step0.acceptance_box) if trace_step0 and trace_step0.acceptance_box else None,
		"raw_blobs": [
			{
				"centroid_x": blob["centroid_x"],
				"centroid_y": blob["centroid_y"],
				"area": blob["area"],
				"integrated_mag": blob["integrated_mag"],
			}
			for blob in (trace_step0.raw_blobs if trace_step0 else [])
		],
		"corridor_blobs": [
			{
				"centroid_x": blob["centroid_x"],
				"centroid_y": blob["centroid_y"],
				"area": blob["area"],
				"integrated_mag": blob["integrated_mag"],
				"in_corridor": blob["in_corridor"],
				"in_acceptance_box": blob["in_acceptance_box"],
				"dist_to_pred_px": blob["dist_to_pred_px"],
				"strength_score": blob["strength_score"],
				"size_score": blob["size_score"],
				"proximity_score": blob["proximity_score"],
				"total_score": blob["total_score"],
			}
			for blob in (trace_step0.corridor_blobs if trace_step0 else [])
		],
		# Observable contract: centroid_x/y record the torso-corrected
		# position (obs.center_pixel); raw_centroid_x/y preserve the
		# original body centroid for audit. The cross-track component
		# of the body centroid is intentionally dropped.
		"winner_blob": {
			"centroid_x": obs_step0.center_pixel[0],
			"centroid_y": obs_step0.center_pixel[1],
			"raw_centroid_x": trace_step0.winner_blob["centroid_x"],
			"raw_centroid_y": trace_step0.winner_blob["centroid_y"],
			"area": trace_step0.winner_blob["area"],
			"integrated_mag": trace_step0.winner_blob["integrated_mag"],
			"in_corridor": trace_step0.winner_blob["in_corridor"],
			"in_acceptance_box": trace_step0.winner_blob["in_acceptance_box"],
			"dist_to_pred_px": trace_step0.winner_blob["dist_to_pred_px"],
			"strength_score": trace_step0.winner_blob["strength_score"],
			"size_score": trace_step0.winner_blob["size_score"],
			"proximity_score": trace_step0.winner_blob["proximity_score"],
			"total_score": trace_step0.winner_blob["total_score"],
		} if (trace_step0 and trace_step0.winner_blob and obs_step0 is not None) else None,
		"obs_available": obs_step0 is not None,
		# reject_reason: populated from trace.reject_reason when
		# observe_blob_at returns None (no_residual, no_raw_blobs,
		# corridor_empty, acceptance_box_empty, no_winner). Empty string
		# when observation succeeded. None when no trace was captured.
		"reject_reason": trace_step0.reject_reason if trace_step0 is not None else None,
		# residual_sum and residual_nonzero_px allow downstream classifiers to
		# distinguish no_residual (truly empty heat-map) from no_raw_blobs
		# (heat-map has signal, DoG found nothing). Both are 0 when the
		# residual is None (no valid residual computed at this frame).
		"residual_sum": _residual_stats(trace_step0.residual_pre_dog if trace_step0 else None)[0],
		"residual_nonzero_px": _residual_stats(trace_step0.residual_pre_dog if trace_step0 else None)[1],
		"audit_winner_alternatives": [
			{
				"rule": "center_of_mass",
				"blob": walk_walker.resolve_audit_winner(trace_step0, "center_of_mass", (seed_cx, seed_cy)),
			},
			{
				"rule": "strongest_blob",
				"blob": walk_walker.resolve_audit_winner(trace_step0, "strongest_blob", (seed_cx, seed_cy)),
			},
			{
				"rule": "body_position",
				"blob": walk_walker.resolve_audit_winner(trace_step0, "body_position", (seed_cx, seed_cy)),
			},
		] if trace_step0 else [],
	}

	# Bootstrap steps 1, 2, 3 (FWD) or -1, -2, -3 (BWD).
	# New design (2026-05-28): NO endpoint-seed chord prior. Step 1 is geometry-
	# only (predicted center = seed). Step 2 projects forward by the displacement
	# the walker measured at step 1 (winner_blob_scene - seed_scene). Step 3 uses
	# a rolling mean of the last 2 measured displacements. prev_cx/cy is rolled
	# forward through the accepted winner blobs (in source-pixel coords for the
	# acceptance box; scene-space for prediction comparison). If a step has no
	# winner, the sequence fails at that step and subsequent steps roll prev as
	# the seed (their data is captured but the sequence is already a failure).
	seed_scene = scene_transform.pixel_to_scene(seed_frame, seed_cx, seed_cy)
	# Track accepted (scene_x, scene_y) winners across steps; index 0 is the seed.
	accept_scene_history = [seed_scene]
	prev_cx = seed_cx
	prev_cy = seed_cy
	dt = stride
	step_data_by_index = {}
	for step in (1, 2, 3):
		# Predicted scene center for this step (walker-measured velocity).
		# Step 1: predicted = seed (no prior displacement observed).
		# Step 2: predicted = step1_accept + (step1_accept - seed).
		# Step 3: predicted = step2_accept + 0.5 * ((step2-step1) + (step1-seed))
		#                   = step2_accept + 0.5 * (step2_accept - seed).
		if step == 1:
			pred_scene_x = seed_scene[0]
			pred_scene_y = seed_scene[1]
			vx_scene = 0.0
			vy_scene = 0.0
		elif step == 2:
			last_x, last_y = accept_scene_history[-1]
			d1x = last_x - seed_scene[0]
			d1y = last_y - seed_scene[1]
			pred_scene_x = last_x + d1x
			pred_scene_y = last_y + d1y
			vx_scene = d1x
			vy_scene = d1y
		else:
			# step == 3
			last_x, last_y = accept_scene_history[-1]
			d_total_x = last_x - seed_scene[0]
			d_total_y = last_y - seed_scene[1]
			mean_step_x = 0.5 * d_total_x
			mean_step_y = 0.5 * d_total_y
			pred_scene_x = last_x + mean_step_x
			pred_scene_y = last_y + mean_step_y
			vx_scene = mean_step_x
			vy_scene = mean_step_y
		v_recent_scene_mag = (vx_scene ** 2 + vy_scene ** 2) ** 0.5
		frame_f = seed_frame + sign * step * dt
		# Bounds check
		if frame_f < 0 or frame_f >= reader.frame_count:
			step_data_by_index[step] = None
			continue
		# Convert predicted scene center back to source-frame pixels, then to
		# processed-pixel coords (Option A, 2026-05-29: observe_blob_at expects
		# processed coords for pred_center and all override args).
		pred_cx_src, pred_cy_src = scene_transform.scene_to_pixel(
			frame_f, pred_scene_x, pred_scene_y
		)
		pred_cx_px, pred_cy_px = geometry.source_to_processed(pred_cx_src, pred_cy_src)
		# Construct acceptance box anchored to the predicted center (processed space).
		acceptance_box_stepN = (
			pred_cx_px - 0.5 * seed_w,
			pred_cy_px - 0.75 * seed_h,
			pred_cx_px + 0.5 * seed_w,
			pred_cy_px + 0.75 * seed_h,
		)
		roi_pad_stepN = max(20, seed_w)
		roi_x1_stepN = max(0, int(acceptance_box_stepN[0] - roi_pad_stepN))
		roi_y1_stepN = max(0, int(acceptance_box_stepN[1] - roi_pad_stepN))
		# Clamp against processed-frame dims (reader.width/height post-bin).
		roi_x2_stepN = min(reader.width, int(acceptance_box_stepN[2] + roi_pad_stepN))
		roi_y2_stepN = min(reader.height, int(acceptance_box_stepN[3] + roi_pad_stepN))
		roi_override_stepN = (roi_x1_stepN, roi_y1_stepN, roi_x2_stepN, roi_y2_stepN)
		# dog_diameter_override in processed-pixel space
		dog_diameter_override_stepN = 0.7 * seed_w

		# Skip this step if the clamped ROI is degenerate (prediction off-frame).
		# record None so the output JSON reflects the gap rather than crashing.
		if roi_x2_stepN <= roi_x1_stepN or roi_y2_stepN <= roi_y1_stepN:
			step_data_by_index[step] = None
			continue

		# Holder for trace data
		trace_sink_stepN = type('TraceSink', (), {'observer_trace': None})()

		# Call observe_blob_at for this bootstrap step.
		# pred_center is the walker's projected center (geometry-only at
		# step 1, walker-measured displacement at step 2+).
		obs_stepN = residual_motion.observe_blob_at(
			frame_index=frame_f,
			pred_center=(pred_cx_px, pred_cy_px),
			pred_box=(seed_w, seed_h),
			local_tangent=local_tangent,
			scene_transform=scene_transform,
			reader=reader,
			residual_cache=residual_cache,
			fps=fps,
			stride=stride,
			trace_sink=trace_sink_stepN,
			roi_override=roi_override_stepN,
			dog_diameter_override=dog_diameter_override_stepN,
			acceptance_box=acceptance_box_stepN,
		)

		trace_stepN = trace_sink_stepN.observer_trace

		# Motion gate evaluation against the WALKER-PREDICTED center; this
		# mirrors the new bootstrap design where each step compares the
		# candidate to its projected position, not to the seed.
		motion_gate_result = None
		cand_scene = None
		if obs_stepN is not None:
			cand_cx, cand_cy = obs_stepN.center_pixel
			cand_scene = scene_transform.pixel_to_scene(frame_f, cand_cx, cand_cy)

			motion_gate_result = walk_motion_gate.evaluate(
				prev_scene=(pred_scene_x, pred_scene_y),
				cand_scene=cand_scene,
				v_recent_scene_mag=v_recent_scene_mag,
				dt_frames=1,
				torso_w=seed_w,
				torso_w_drift_frac=0.0,
				source_fps=float(fps),
			)

		# Assemble per-step data dict. prev_cx_scene/cy_scene carry the
		# WALKER-PREDICTED scene center for this step (the comparison anchor
		# for the new bootstrap radius check). pred_cx_px/cy_px carry the
		# same prediction in source-pixel coordinates.
		step_data_by_index[step] = {
			"frame_index": frame_f,
			"step": step,
			"source_fps": float(fps),
			"seed_cx_px": seed_cx,
			"seed_cy_px": seed_cy,
			"seed_w_px": seed_w,
			"seed_h_px": seed_h,
			"pred_cx_px": pred_cx_px,
			"pred_cy_px": pred_cy_px,
			"prev_cx_px": prev_cx,
			"prev_cy_px": prev_cy,
			"prev_cx_scene": float(pred_scene_x),
			"prev_cy_scene": float(pred_scene_y),
			"v_recent_scene_mag": v_recent_scene_mag,
			"vx_scene": vx_scene,
			"vy_scene": vy_scene,
			"roi_origin_xy": list(trace_stepN.roi_origin_xy) if trace_stepN and trace_stepN.roi_origin_xy else None,
			"acceptance_box": list(trace_stepN.acceptance_box) if trace_stepN and trace_stepN.acceptance_box else None,
			"raw_blobs": [
				{
					"centroid_x": blob["centroid_x"],
					"centroid_y": blob["centroid_y"],
					"area": blob["area"],
					"integrated_mag": blob["integrated_mag"],
				}
				for blob in (trace_stepN.raw_blobs if trace_stepN else [])
			],
			"corridor_blobs": [
				{
					"centroid_x": blob["centroid_x"],
					"centroid_y": blob["centroid_y"],
					"area": blob["area"],
					"integrated_mag": blob["integrated_mag"],
					"in_corridor": blob["in_corridor"],
					"in_acceptance_box": blob["in_acceptance_box"],
					"dist_to_pred_px": blob["dist_to_pred_px"],
					"strength_score": blob["strength_score"],
					"size_score": blob["size_score"],
					"proximity_score": blob["proximity_score"],
					"total_score": blob["total_score"],
				}
				for blob in (trace_stepN.corridor_blobs if trace_stepN else [])
			],
			# Per the Observable contract on residual_motion.observe_blob_at,
			# the AUTHORITATIVE runner position is obs.center_pixel (torso
			# center), not the raw body centroid on trace_stepN.winner_blob.
			# We overwrite centroid_x / centroid_y with the torso-corrected
			# position so downstream replay (replay_step1, replay_models)
			# consumes the right observable. The raw body centroid is
			# preserved under raw_centroid_x / raw_centroid_y for audit.
			"winner_blob": {
				"centroid_x": obs_stepN.center_pixel[0],
				"centroid_y": obs_stepN.center_pixel[1],
				"raw_centroid_x": trace_stepN.winner_blob["centroid_x"],
				"raw_centroid_y": trace_stepN.winner_blob["centroid_y"],
				"area": trace_stepN.winner_blob["area"],
				"integrated_mag": trace_stepN.winner_blob["integrated_mag"],
				"in_corridor": trace_stepN.winner_blob["in_corridor"],
				"in_acceptance_box": trace_stepN.winner_blob["in_acceptance_box"],
				"dist_to_pred_px": trace_stepN.winner_blob["dist_to_pred_px"],
				"strength_score": trace_stepN.winner_blob["strength_score"],
				"size_score": trace_stepN.winner_blob["size_score"],
				"proximity_score": trace_stepN.winner_blob["proximity_score"],
				"total_score": trace_stepN.winner_blob["total_score"],
			} if (trace_stepN and trace_stepN.winner_blob and obs_stepN is not None) else None,
			"obs_available": obs_stepN is not None,
			# reject_reason: populated from trace.reject_reason when
			# observe_blob_at returns None (no_residual, no_raw_blobs,
			# corridor_empty, acceptance_box_empty, no_winner). Empty string
			# when observation succeeded. None when no trace was captured.
			"reject_reason": trace_stepN.reject_reason if trace_stepN is not None else None,
			# residual_sum and residual_nonzero_px: see step0 comment above.
			"residual_sum": _residual_stats(trace_stepN.residual_pre_dog if trace_stepN else None)[0],
			"residual_nonzero_px": _residual_stats(trace_stepN.residual_pre_dog if trace_stepN else None)[1],
			"motion_gate_result": {
				"accepted": motion_gate_result.accepted,
				"expected_jump": motion_gate_result.expected_jump,
				"allowed_jump": motion_gate_result.allowed_jump,
				"actual_jump": motion_gate_result.actual_jump,
				"v_recent_scene_mag": motion_gate_result.v_recent_scene_mag,
				"dt_for_gate": motion_gate_result.dt_for_gate,
				"reject_reason": motion_gate_result.reject_reason,
			} if motion_gate_result is not None else None,
			"audit_winner_alternatives": [
				{
					"rule": "center_of_mass",
					"blob": walk_walker.resolve_audit_winner(trace_stepN, "center_of_mass", (prev_cx, prev_cy)),
				},
				{
					"rule": "strongest_blob",
					"blob": walk_walker.resolve_audit_winner(trace_stepN, "strongest_blob", (prev_cx, prev_cy)),
				},
				{
					"rule": "body_position",
					"blob": walk_walker.resolve_audit_winner(trace_stepN, "body_position", (prev_cx, prev_cy)),
				},
			] if trace_stepN else [],
		}

		# Roll forward through the winner (if present) so the next bootstrap
		# step's prediction uses the measured displacement. If no winner,
		# accept_scene_history keeps its last entry and subsequent steps will
		# project from that, but the sequence is already a failure.
		if (
			trace_stepN is not None
			and trace_stepN.winner_blob is not None
			and obs_stepN is not None
		):
			# Roll forward via the AUTHORITATIVE torso-corrected position
			# (obs.center_pixel) per the Observable contract, NOT the raw
			# body centroid on trace_stepN.winner_blob. The raw centroid
			# carries 0.5-0.75 H of cross-track body-extent noise that
			# would corrupt the next-step prediction.
			win_cx_px = obs_stepN.center_pixel[0]
			win_cy_px = obs_stepN.center_pixel[1]
			win_scene = scene_transform.pixel_to_scene(frame_f, win_cx_px, win_cy_px)
			accept_scene_history.append(win_scene)
			# Roll prev_cx/cy forward so the per-step dump records the most
			# recently accepted source-pixel position.
			prev_cx = win_cx_px
			prev_cy = win_cy_px

	# Assemble final output
	output_data = {
		"video_basename": video_basename,
		"source_fps": float(fps),
		"interval_label": interval.label,
		"direction": direction,
		"left_seed_frame": interval.left_seed["frame_index"],
		"right_seed_frame": interval.right_seed["frame_index"],
		"step_0": step0_data,
		"step_1": step_data_by_index[1],
		"step_2": step_data_by_index[2],
		"step_3": step_data_by_index[3],
	}

	# Write to JSON
	with open(output_file, "w") as f:
		json.dump(output_data, f, indent=2)

	return output_data


#============================================
def is_well_formed_interval(interval, race_start_frame: int) -> bool:
	"""Return True when an interval meets well-formed criteria.

	Well-formed requires:
	- Both seeds are post-race (frame_index > race_start_frame).
	- Neither seed is flagged not_in_frame (status == 'not_in_frame').
	- Both seeds have derived geometry (cx/cy/w/h present, i.e. torso_box loaded).
	- Interval length (right_frame - left_frame) is between 2 and 10000 frames.

	These match the plan definition verbatim so the corpus is not cherry-picked.

	Args:
		interval: SeedToSeedInterval with left_seed and right_seed.
		race_start_frame: Frame index where the race starts.

	Returns:
		bool: True if the interval passes all well-formed checks.
	"""
	left = interval.left_seed
	right = interval.right_seed
	left_frame = left["frame_index"]
	right_frame = right["frame_index"]
	# Both seeds must be after race_start_frame (strictly post-race)
	if left_frame <= race_start_frame or right_frame <= race_start_frame:
		return False
	# Neither seed may be not_in_frame (status is required on all loaded seeds)
	if left["status"] == "not_in_frame":
		return False
	if right["status"] == "not_in_frame":
		return False
	# Both seeds must have derived geometry (torso_box was present at load)
	if "cx" not in left or "cy" not in left:
		return False
	if "cx" not in right or "cy" not in right:
		return False
	# Interval length must be between 2 and 10000 frames
	length = right_frame - left_frame
	if length < 2 or length > 10000:
		return False
	return True


#============================================
def has_valid_seed_roi(interval, frame_width: int, frame_height: int) -> bool:
	"""Return True when both seeds produce non-degenerate ROIs within the frame.

	A seed ROI is degenerate when the acceptance box padded by roi_pad produces
	roi_x1 >= roi_x2 or roi_y1 >= roi_y2 after clamping to [0, frame_width) x
	[0, frame_height). This mirrors the clamp logic in dump_step1_for_interval.

	Args:
		interval: SeedToSeedInterval with left_seed and right_seed.
		frame_width: Width of the video frame in pixels.
		frame_height: Height of the video frame in pixels.

	Returns:
		bool: True if both seeds produce valid ROIs.
	"""
	for seed in (interval.left_seed, interval.right_seed):
		cx = seed["cx"]
		cy = seed["cy"]
		w = seed["w"]
		h = seed["h"]
		roi_pad = max(20, w)
		acceptance_x1 = cx - 0.5 * w
		acceptance_y1 = cy - 0.75 * h
		acceptance_x2 = cx + 0.5 * w
		acceptance_y2 = cy + 0.75 * h
		roi_x1 = max(0, int(acceptance_x1 - roi_pad))
		roi_y1 = max(0, int(acceptance_y1 - roi_pad))
		roi_x2 = min(frame_width, int(acceptance_x2 + roi_pad))
		roi_y2 = min(frame_height, int(acceptance_y2 + roi_pad))
		if roi_x2 <= roi_x1 or roi_y2 <= roi_y1:
			return False
	return True


#============================================
def select_random_corpus_intervals(
	intervals: list,
	race_start_frame: int,
	n: int,
	rng: random.Random,
) -> list:
	"""Pick n well-formed intervals at random from the interval list.

	Uses the caller-provided Random instance so the seed is externally pinned
	(seed 42) and never re-rolled. If fewer than n well-formed intervals exist,
	returns all of them with a warning.

	Args:
		intervals: Full list of SeedToSeedInterval for one video.
		race_start_frame: Frame index where the race starts.
		n: Target number of intervals to select.
		rng: Seeded random.Random instance (seed 42 per plan).

	Returns:
		list: Selected SeedToSeedInterval objects (length <= n).
	"""
	# Collect all well-formed candidates first
	candidates = [iv for iv in intervals if is_well_formed_interval(iv, race_start_frame)]
	if len(candidates) <= n:
		# Return all candidates; caller will warn if count < n
		return list(candidates)
	# Pick n without replacement; rng.sample preserves list order and is
	# deterministic given the pinned seed.
	selected = rng.sample(candidates, n)
	# Sort by left_seed frame_index so output ordering is stable and readable
	selected.sort(key=lambda iv: iv.left_seed["frame_index"])
	return selected


#============================================
def parse_args():
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description="Dump step 0 and step 1 inputs for blob walker v2 to JSON."
	)
	parser.add_argument(
		"-v", "--videos",
		dest="videos",
		nargs="+",
		required=True,
		help="Space-separated video basenames (e.g. '2025-Glenbrook_South-1600m-IMG_1503')",
	)
	parser.add_argument(
		"-i", "--max-intervals",
		dest="max_intervals",
		type=int,
		default=None,
		help="Maximum post_start intervals per video (default: all)",
	)
	parser.add_argument(
		"-o", "--output-dir",
		dest="output_dir",
		default="dump_step1",
		help="Output root directory (default: dump_step1)",
	)
	parser.add_argument(
		"--random-corpus",
		dest="random_corpus",
		action="store_true",
		default=False,
		help=(
			"Pick n random well-formed intervals per video and write to "
			"<output_dir>/<n*nvideos>corpus/<video_basename>/. "
			"Ignores --max-intervals. Seed is fixed at 42 (never re-rolled)."
		),
	)
	parser.add_argument(
		"--per-video-n",
		dest="per_video_n",
		type=int,
		default=4,
		help=(
			"Intervals per video for --random-corpus mode (default 4). "
			"Output dir: dump_step1/{n*nvideos}corpus/."
		),
	)
	args = parser.parse_args()
	return args


#============================================
def main():
	"""Dump step 1 inputs for a corpus of videos and intervals."""
	args = parse_args()

	if args.random_corpus:
		# --random-corpus: pick per_video_n random well-formed intervals per video,
		# write to <output_dir>/<n_total>corpus/<video_basename>/
		# Seed is pinned at 42 per plan -- never re-roll.
		n_total = args.per_video_n * len(args.videos)
		corpus_dir = pathlib.Path(args.output_dir) / f"{n_total}corpus"
		corpus_dir.mkdir(parents=True, exist_ok=True)
		# One RNG instance shared across all videos so the draw order is
		# deterministic end to end (advancing the RNG per video in alphabetical
		# order means re-running the same list always yields the same result).
		rng = random.Random(42)
		total_written = 0
		for video_basename in args.videos:
			print(f"Processing {video_basename} (random-corpus mode)...")
			seeds_dict = walk_io.load_walker_seeds(video_basename)
			reader, probe_info = walk_io.open_walker_reader(video_basename)
			scene_transform = walk_io.load_walker_scene_transform(video_basename)
			race_start_frame = walk_io.load_race_start_frame(video_basename)
			# Enumerate all seed-to-seed intervals (not just post_start; the
			# well-formed filter rejects non-post-race intervals explicitly)
			all_intervals = walk_io.enumerate_seed_to_seed_intervals(seeds_dict, race_start_frame)
			# Select per_video_n random well-formed intervals using the pinned RNG
			selected = select_random_corpus_intervals(all_intervals, race_start_frame, args.per_video_n, rng)
			# Filter out intervals where either seed has an off-frame ROI (degenerate).
			# These would crash dump_step1_for_interval; excluding them is the design fix.
			# has_valid_seed_roi uses source-pixel seed coords; compare against
			# source frame dims (Option A, 2026-05-29: reader.geometry.source_width/height).
			valid_selected = [
				iv for iv in selected
				if has_valid_seed_roi(iv, reader.geometry.source_width, reader.geometry.source_height)
			]
			if len(valid_selected) < len(selected):
				n_dropped = len(selected) - len(valid_selected)
				print(
					f"  WARNING: {video_basename} dropped {n_dropped} interval(s) with "
					f"off-frame seed ROI (degenerate); {len(valid_selected)} remain."
				)
			selected = valid_selected
			if len(selected) < args.per_video_n:
				print(
					f"  WARNING: {video_basename} has only {len(selected)} well-formed "
					f"intervals (wanted {args.per_video_n}); using all of them."
				)
			if len(selected) == 0:
				print(f"  SKIP: {video_basename} produced 0 well-formed intervals.")
				continue
			for interval in selected:
				left_frame = interval.left_seed["frame_index"]
				# Dump FWD (+1) direction into 24corpus subdir
				dump_step1_for_interval(
					interval,
					video_basename,
					seeds_dict,
					reader,
					probe_info,
					scene_transform,
					str(corpus_dir),
					sign=1,
				)
				total_written += 1
				print(f"  Wrote FWD for interval left_frame={left_frame}")
				# Dump BWD (-1) direction
				dump_step1_for_interval(
					interval,
					video_basename,
					seeds_dict,
					reader,
					probe_info,
					scene_transform,
					str(corpus_dir),
					sign=-1,
				)
				total_written += 1
				print(f"  Wrote BWD for interval left_frame={left_frame}")
		print(f"\nWrote {total_written} JSON files to {corpus_dir}")
		return

	output_dir = pathlib.Path(args.output_dir)
	output_dir.mkdir(parents=True, exist_ok=True)

	total_written = 0

	for video_basename in args.videos:
		print(f"Processing {video_basename}...")

		# Load seeds, reader, scene transform
		seeds_dict = walk_io.load_walker_seeds(video_basename)
		reader, probe_info = walk_io.open_walker_reader(video_basename)
		scene_transform = walk_io.load_walker_scene_transform(video_basename)
		race_start_frame = walk_io.load_race_start_frame(video_basename)

		# Enumerate intervals
		intervals = walk_io.enumerate_seed_to_seed_intervals(seeds_dict, race_start_frame)

		# Filter to post_start intervals only
		post_start_intervals = [i for i in intervals if i.label == "post_start"]

		# Cap to max_intervals if specified
		if args.max_intervals is not None:
			post_start_intervals = post_start_intervals[:args.max_intervals]

		for interval in post_start_intervals:
			# Dump FWD (+1) direction
			dump_step1_for_interval(
				interval,
				video_basename,
				seeds_dict,
				reader,
				probe_info,
				scene_transform,
				str(output_dir),
				sign=1,
			)
			total_written += 1
			print(f"  Wrote FWD for interval {interval.left_seed['frame_index']}")

			# Dump BWD (-1) direction
			dump_step1_for_interval(
				interval,
				video_basename,
				seeds_dict,
				reader,
				probe_info,
				scene_transform,
				str(output_dir),
				sign=-1,
			)
			total_written += 1
			print(f"  Wrote BWD for interval {interval.left_seed['frame_index']}")

	print(f"\nWrote {total_written} JSON files to {output_dir}")


#============================================
if __name__ == "__main__":
	main()
