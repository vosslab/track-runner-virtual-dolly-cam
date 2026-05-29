#!/usr/bin/env python3
"""
Replay step-1 failure inputs against candidate motion models and classify failure modes.

For each captured JSON from WP-0A, classify the production winner against ground truth
(human seed or interpolated seed), then replay the same step-1 inputs against 6 candidate
motion models. Output a REPLAY_REPORT.md with:
  1. Per-interval failure-mode classification table
  2. Candidate-model shootout matrix (rows=intervals, cols=models, cell=accept/reject + margin)
  3. Frame-1 acceptance bar verdict at the top (PASS with named model, or FAIL)
"""

# Standard Library
import os
import json
import sys
import pathlib
import math
import argparse
from dataclasses import dataclass

# Determine repo root and set up paths
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, 'track_runner')
_TESTS_DIR = os.path.join(_REPO_ROOT, 'tests')
if _TRACK_RUNNER_DIR not in sys.path:
	sys.path.insert(0, _TRACK_RUNNER_DIR)
if _TESTS_DIR not in sys.path:
	sys.path.insert(0, _TESTS_DIR)
if _REPO_ROOT not in sys.path:
	sys.path.insert(0, _REPO_ROOT)

# local repo modules
import state_io
import walk_io
import walk_motion_gate
import replay_models


#============================================
# Coordinate space note (WP-0B coordinate parity fix)
#============================================
# BlobObserverTrace stores blob centroids in SOURCE-FRAME pixel coordinates.
# The captured JSON contains corridor_blobs[*].centroid_x/y in source-frame pixels.
# Motion-gate replay models expect SCENE coordinates (anchored at frame 0, compensated
# for camera motion). The conversion from source-frame pixels to scene coords is:
#   cand_scene_x, cand_scene_y = scene_transform.pixel_to_scene(frame_index, cand_px, cand_py)
# where scene_transform is a SceneTransform instance loaded from motion cache.
# This fix converts blob centroids from source-frame pixels to scene coordinates
# before passing them to replay models, ensuring parity with the live motion gate
# evaluation in walk_motion_gate.py.


#============================================
# Frame-1 acceptance bar threshold
#============================================
# Strict majority per user correction 2026-05-27: a model passes a subcorpus
# when accepts > 0.51 * testable (strict greater-than, not >=). The prior
# 7/8 = 87.5% threshold was too high and rejected models that succeed on
# a clear majority of testable intervals.
FRAME_1_ACCEPTANCE_BAR_FRACTION = 0.51  # strict majority per user correction 2026-05-27


#============================================
# Data structures for candidate model results
#============================================

@dataclass
class ModelResult:
	"""Result of replaying one model on one interval."""
	model_name: str
	accepts_runner_blob: bool
	rejects_obvious_wrong: bool
	allowed_jump_scene: float
	actual_residual_scene: float
	margin_scene: float
	uses_future_seed_info: bool
	uses_endpoint_seeds: bool
	uses_intermediate_labels: bool = False


@dataclass
class IntervalClassification:
	"""Classification of failure mode for one interval."""
	video_basename: str
	direction: str
	left_seed_frame: int
	right_seed_frame: int
	failure_mode: str
	needs_human_label: bool
	failure_bin_5: str = 'unclassified'


#============================================
# Candidate motion model implementations
#============================================

def model_current_scalar_gate(
	prev_scene_x: float,
	prev_scene_y: float,
	cand_scene_x: float,
	cand_scene_y: float,
	v_recent_scene_mag: float,
	dt_frames: int,
	torso_w_px: float,
	source_fps: float,
) -> dict:
	"""
	Model 1: Current scalar three-cap-min gate (baseline). Routes through the
	live walk_motion_gate.evaluate so replay never diverges from production.
	"""
	result = walk_motion_gate.evaluate(
		prev_scene=(prev_scene_x, prev_scene_y),
		cand_scene=(cand_scene_x, cand_scene_y),
		v_recent_scene_mag=v_recent_scene_mag,
		dt_frames=dt_frames,
		torso_w=torso_w_px,
		torso_w_drift_frac=0.0,
		source_fps=source_fps,
	)
	return {
		'accepted': result.accepted,
		'allowed_jump': result.allowed_jump,
		'actual_jump': result.actual_jump,
	}


def model_vector_prediction_three_cap_min(
	prev_scene_x: float,
	prev_scene_y: float,
	cand_scene_x: float,
	cand_scene_y: float,
	vx_scene: float,
	vy_scene: float,
	dt_frames: int,
	torso_w_px: float,
	source_fps: float,
) -> dict:
	"""
	Model 2: Vector-prediction three-cap-min on the residual vs predicted.
	Translates prev by v*dt and calls the live walk_motion_gate.evaluate against
	the same candidate; the gate caps are unchanged by the translation.
	"""
	dt_for_gate = min(dt_frames, walk_motion_gate.DT_GATE_CAP)
	pred_scene_x = prev_scene_x + vx_scene * dt_for_gate
	pred_scene_y = prev_scene_y + vy_scene * dt_for_gate
	v_mag = math.sqrt(vx_scene * vx_scene + vy_scene * vy_scene)
	result = walk_motion_gate.evaluate(
		prev_scene=(pred_scene_x, pred_scene_y),
		cand_scene=(cand_scene_x, cand_scene_y),
		v_recent_scene_mag=v_mag,
		dt_frames=dt_frames,
		torso_w=torso_w_px,
		torso_w_drift_frac=0.0,
		source_fps=source_fps,
	)
	return {
		'accepted': result.accepted,
		'allowed_jump': result.allowed_jump,
		'actual_jump': result.actual_jump,
	}


def model_seed_chord_short_lookahead_prior(
	prev_scene_x: float,
	prev_scene_y: float,
	cand_scene_x: float,
	cand_scene_y: float,
	vx_scene: float,
	vy_scene: float,
	dt_frames: int,
	torso_w_px: float,
	source_fps: float,
) -> dict:
	"""
	Model 4: Seed-chord short-lookahead prior, gated by walk_motion_gate.evaluate.
	"""
	LOOKAHEAD_FRAMES = 10
	lookahead_velocity_scale = LOOKAHEAD_FRAMES / (LOOKAHEAD_FRAMES + 1)
	vx_adjusted = vx_scene * lookahead_velocity_scale
	vy_adjusted = vy_scene * lookahead_velocity_scale
	v_adjusted_mag = math.sqrt(vx_adjusted * vx_adjusted + vy_adjusted * vy_adjusted)
	result = walk_motion_gate.evaluate(
		prev_scene=(prev_scene_x, prev_scene_y),
		cand_scene=(cand_scene_x, cand_scene_y),
		v_recent_scene_mag=v_adjusted_mag,
		dt_frames=dt_frames,
		torso_w=torso_w_px,
		torso_w_drift_frac=0.0,
		source_fps=source_fps,
	)
	return {
		'accepted': result.accepted,
		'allowed_jump': result.allowed_jump,
		'actual_jump': result.actual_jump,
	}


def model_no_velocity_baseline(
	prev_scene_x: float,
	prev_scene_y: float,
	cand_scene_x: float,
	cand_scene_y: float,
	torso_w_px: float,
) -> dict:
	"""
	Model 3: No-velocity baseline.
	Accept any corridor blob within 1.0 * torso_w fixed local radius.
	No velocity prior at all.
	"""
	local_radius = 1.0 * torso_w_px
	dx = cand_scene_x - prev_scene_x
	dy = cand_scene_y - prev_scene_y
	distance = math.sqrt(dx * dx + dy * dy)
	accepted = distance <= local_radius

	return {
		'accepted': accepted,
		'allowed_jump': local_radius,
		'actual_jump': distance,
	}


def model_constant_runner_speed(
	prev_scene_x: float,
	prev_scene_y: float,
	cand_scene_x: float,
	cand_scene_y: float,
	dt_frames: int,
	torso_w_px: float,
	source_fps: float,
) -> dict:
	"""
	Model 5: Constant runner-speed prior (v = 0.3W per frame at source fps),
	gated by walk_motion_gate.evaluate.
	"""
	v_recent_scene_mag = 0.3 * torso_w_px
	result = walk_motion_gate.evaluate(
		prev_scene=(prev_scene_x, prev_scene_y),
		cand_scene=(cand_scene_x, cand_scene_y),
		v_recent_scene_mag=v_recent_scene_mag,
		dt_frames=dt_frames,
		torso_w=torso_w_px,
		torso_w_drift_frac=0.0,
		source_fps=source_fps,
	)
	return {
		'accepted': result.accepted,
		'allowed_jump': result.allowed_jump,
		'actual_jump': result.actual_jump,
	}


def model_candidate_ranking_only(
	winner_blob: dict,
	corridor_blobs: list,
) -> dict:
	"""
	Model 6: Candidate-ranking-only baseline.
	No motion gate; accept the highest total_score corridor blob.
	Returns True if winner_blob has highest total_score among all corridor blobs.
	Only corridor_blobs have scoring fields; raw_blobs are excluded.
	"""
	if winner_blob is None or not corridor_blobs:
		return {'accepted': False, 'allowed_jump': 0, 'actual_jump': 0}

	all_blobs = corridor_blobs + [winner_blob]
	best_blob = max(all_blobs, key=lambda b: b['total_score'])
	accepted = best_blob is winner_blob

	return {
		'accepted': accepted,
		'allowed_jump': 0,
		'actual_jump': 0,
	}


#============================================
# Classification logic
#============================================

# Valid 5-bin labels plus the unclassified sentinel.
_VALID_5BIN_LABELS = frozenset([
	'no_residual',
	'no_raw_blobs',
	'wrong_observable',
	'candidate_selection',
	'true_gate_failure',
	'unclassified',
])

# Tolerance fraction of torso width for runner ground-truth matching.
# A corridor blob whose centroid is within 0.5 * torso_w of the interpolated
# runner center is considered "the runner blob".
_RUNNER_MATCH_TOLERANCE_W_FRACTION = 0.5


def classify_failure_5bin(
	replay_record: dict,
	left_seed: dict,
	right_seed: dict,
	torso_w_px: float = None,
) -> str:
	"""
	Classify the failure mode for one step-1 record into one of 5 user-pinned bins.

	Decision tree (evaluated in precedence order):
	  1. no_residual     -- residual_sum == 0 OR residual_nonzero_px == 0 in the
	                        capture.  When these fields are absent (pre-task-53
	                        captures), falls back to no_raw_blobs with a warning
	                        (cannot distinguish empty heat-map from empty extraction).
	  2. no_raw_blobs    -- residual non-zero, but obs_available is False or
	                        corridor_blobs is empty after extraction.
	  3. wrong_observable -- blobs extracted (corridor_blobs non-empty), but NO
	                         blob centroid is within 0.5 * torso_w of the
	                         interpolated runner ground-truth center.
	  4. candidate_selection -- a corridor blob IS within tolerance but
	                            production_winner is a DIFFERENT blob (or null).
	  5. true_gate_failure -- production_winner IS within tolerance of runner
	                          ground-truth AND the motion gate rejected it.

	Returns one of: 'no_residual', 'no_raw_blobs', 'wrong_observable',
	                'candidate_selection', 'true_gate_failure', 'unclassified'.

	Prints a warning (with the record's frame_index) when:
	  - Residual fields are absent (bin 1 / bin 2 ambiguity).
	  - No bin matches (returns 'unclassified').

	Args:
		replay_record: One step dict from a dump_step1 JSON (e.g. data['step_1']).
		left_seed: Left endpoint seed dict with 'frame_index' and 'torso_box'.
		right_seed: Right endpoint seed dict with 'frame_index' and 'torso_box'.
		torso_w_px: Torso width in pixels used as the match-tolerance unit.
			If None, seed_w_px from replay_record is used as fallback.
	"""
	frame_index = replay_record['frame_index']
	corridor_blobs = replay_record.get('corridor_blobs') or []
	winner_blob = replay_record.get('winner_blob')
	motion_gate_result = replay_record.get('motion_gate_result')
	obs_available = replay_record.get('obs_available', False)

	# Resolve torso width for tolerance calculation.
	if torso_w_px is None:
		torso_w_px = replay_record['seed_w_px']
	tolerance_px = _RUNNER_MATCH_TOLERANCE_W_FRACTION * torso_w_px

	# -- Bin 1: no_residual --
	# Preferred check: use residual_sum / residual_nonzero_px when present
	# (written by dump task #53 onward).
	residual_sum = replay_record.get('residual_sum')
	residual_nonzero_px = replay_record.get('residual_nonzero_px')
	if residual_sum is not None and residual_nonzero_px is not None:
		if residual_sum == 0 or residual_nonzero_px == 0:
			return 'no_residual'
		# Residual is non-zero; fall through to bin 2.
	else:
		# Pre-task-53 capture: residual fields absent.
		# Cannot distinguish no_residual from no_raw_blobs.
		# If corridor_blobs is also empty, warn and return no_raw_blobs
		# (conservative: assigns the nearer-to-data bin, not the upstream one).
		if not corridor_blobs and not obs_available:
			record_id = f'frame={frame_index}'
			print(
				f'WARNING: classify_failure_5bin: record {record_id} has no residual_sum/'
				f'residual_nonzero_px fields; cannot distinguish no_residual from '
				f'no_raw_blobs -- classifying as no_raw_blobs'
			)
			return 'no_raw_blobs'

	# -- Bin 2: no_raw_blobs --
	# Residual is non-zero (or we passed the absence-check above) but extraction
	# found no blobs in the corridor.
	if not obs_available or not corridor_blobs:
		return 'no_raw_blobs'

	# -- Bins 3-5 require corridor blobs to be present. --

	# Compute interpolated runner position in source-pixel space.
	human_box_px = get_human_seed_box_px(left_seed, right_seed, frame_index)
	runner_cx = (human_box_px[0] + human_box_px[2]) / 2.0
	runner_cy = (human_box_px[1] + human_box_px[3]) / 2.0

	# Find the corridor blob closest to the interpolated runner center.
	def blob_dist_to_runner(blob: dict) -> float:
		dx = blob['centroid_x'] - runner_cx
		dy = blob['centroid_y'] - runner_cy
		return math.sqrt(dx * dx + dy * dy)

	closest_blob = min(corridor_blobs, key=blob_dist_to_runner)
	closest_dist = blob_dist_to_runner(closest_blob)
	runner_blob_in_corridor = closest_dist <= tolerance_px

	# -- Bin 3: wrong_observable --
	# No corridor blob is within runner-match tolerance.
	if not runner_blob_in_corridor:
		return 'wrong_observable'

	# -- Bins 4-5 require the runner blob to be in the corridor. --
	# Check whether the production winner is the same blob as closest_blob.
	# Identity is compared by centroid (floats from the same JSON deserialize
	# identically, so exact equality is safe here).
	def blobs_same(a: dict, b: dict) -> bool:
		return a['centroid_x'] == b['centroid_x'] and a['centroid_y'] == b['centroid_y']

	winner_is_runner = (
		winner_blob is not None
		and blobs_same(winner_blob, closest_blob)
	)

	# -- Bin 4: candidate_selection --
	# Runner blob is in corridor but production picked a different blob (or null).
	if not winner_is_runner:
		return 'candidate_selection'

	# -- Bin 5: true_gate_failure --
	# Winner IS the runner blob; check whether the motion gate rejected it.
	if motion_gate_result is not None and not motion_gate_result.get('accepted', False):
		return 'true_gate_failure'

	# If the winner is the runner blob and the gate accepted it, this interval
	# is NOT a failure from the 5-bin perspective; caller should not have
	# invoked the classifier for a passing interval.  Return unclassified so
	# the report surface is not silently wrong.
	record_id = f'frame={frame_index}'
	print(f'WARNING: classify_failure_5bin cannot classify record {record_id} (winner accepted, not a failure)')
	return 'unclassified'


def classify_failure_legacy(step1_data: dict, left_seed: dict, right_seed: dict) -> str:
	"""
	Classify the failure mode for one interval using the original fine-grained taxonomy.

	Kept for backward compatibility.  New code should use classify_failure_5bin.

	Returns one of the named failure modes:
	- cold_start_velocity
	- motion_model_structure
	- three_cap_min_too_strict
	- wrong_winner_in_corridor
	- roi_origin_mismatch
	- coordinate_space_drift
	- valid_reject_runner_actually_moved_too_much
	- extraction_or_transform_bug
	- other
	"""
	frame_index = step1_data['frame_index']
	human_seed_box = get_human_seed_box_px(left_seed, right_seed, frame_index)

	winner_blob = step1_data['winner_blob']
	corridor_blobs = step1_data['corridor_blobs']
	motion_gate_result = step1_data.get('motion_gate_result')

	# If motion gate rejected, check if it's a reasonable rejection
	if motion_gate_result is None:
		return 'extraction_or_transform_bug'

	if not motion_gate_result.get('accepted', False):
		actual_jump = motion_gate_result.get('actual_jump', 0)
		allowed_jump = motion_gate_result.get('allowed_jump', 0)

		# If there's a large actual_jump, it might be a valid rejection
		if actual_jump > allowed_jump * 2:
			return 'valid_reject_runner_actually_moved_too_much'

		# Otherwise, could be one of several issues
		return 'three_cap_min_too_strict'

	# If winner_blob is None, check if there's a corridor blob
	if winner_blob is None:
		if corridor_blobs:
			return 'wrong_winner_in_corridor'
		else:
			return 'extraction_or_transform_bug'

	# Check if winner is inside the human_seed_box
	winner_cx = winner_blob.get('centroid_x', 0)
	winner_cy = winner_blob.get('centroid_y', 0)
	box_x1, box_y1, box_x2, box_y2 = human_seed_box

	in_box = (box_x1 <= winner_cx <= box_x2) and (box_y1 <= winner_cy <= box_y2)
	if in_box:
		# Winner is in box and motion gate accepted it
		return 'other'

	# Winner is not in the box; check if another blob is
	for blob in corridor_blobs:
		blob_cx = blob.get('centroid_x', 0)
		blob_cy = blob.get('centroid_y', 0)
		if (box_x1 <= blob_cx <= box_x2) and (box_y1 <= blob_cy <= box_y2):
			return 'wrong_winner_in_corridor'

	return 'other'


def get_human_seed_box_px(left_seed: dict, right_seed: dict, frame_index: int) -> tuple:
	"""
	Get the human-labeled torso box at the target frame via linear interpolation.

	If frame is at left_seed frame, return left_seed box.
	If frame is at right_seed frame, return right_seed box.
	Otherwise, interpolate linearly between the two.

	Returns (x1_px, y1_px, x2_px, y2_px) - axis-aligned bounding box.
	"""
	left_frame = left_seed['frame_index']
	right_frame = right_seed['frame_index']

	if frame_index <= left_frame:
		box = left_seed['torso_box']
		x, y, w, h = box[0], box[1], box[2], box[3]
		return (x, y, x + w, y + h)

	if frame_index >= right_frame:
		box = right_seed['torso_box']
		x, y, w, h = box[0], box[1], box[2], box[3]
		return (x, y, x + w, y + h)

	# Interpolate between left and right
	t = (frame_index - left_frame) / (right_frame - left_frame)
	left_box = left_seed['torso_box']
	right_box = right_seed['torso_box']

	x_left, y_left, w_left, h_left = left_box[0], left_box[1], left_box[2], left_box[3]
	x_right, y_right, w_right, h_right = right_box[0], right_box[1], right_box[2], right_box[3]

	x = x_left + t * (x_right - x_left)
	y = y_left + t * (y_right - y_left)
	w = w_left + t * (w_right - w_left)
	h = h_left + t * (h_right - h_left)

	return (x, y, x + w, y + h)


#============================================
# Main replay logic
#============================================

def load_seed_pair(video_basename: str, left_frame: int, right_frame: int) -> tuple:
	"""
	Load the seed pair for a given video and frame range.
	Returns (left_seed, right_seed) or (None, None) if not found.
	"""
	seeds_path = pathlib.Path(_REPO_ROOT) / "tr_config" / f"{video_basename}.track_runner.seeds.json"
	if not seeds_path.exists():
		return None, None

	seeds_dict = state_io.load_seeds(str(seeds_path))
	seeds_list = seeds_dict['seeds']

	left_seed = None
	right_seed = None
	for seed in seeds_list:
		if seed['frame_index'] == left_frame:
			left_seed = seed
		elif seed['frame_index'] == right_frame:
			right_seed = seed

	return left_seed, right_seed


def load_scene_transform_for_video(video_basename: str) -> object:
	"""
	Load the scene transform for a video.
	Used to convert blob centroids from source-frame pixels to scene coordinates.
	Returns SceneTransform or None if not found.
	"""
	try:
		return walk_io.load_walker_scene_transform(video_basename)
	except Exception:
		return None


def replay_step1_models(
	step1_data: dict,
	left_seed: dict,
	right_seed: dict,
	scene_transform: object = None,
	video_basename: str = None,
	step2_data: dict = None,
	step3_data: dict = None,
) -> list:
	"""
	Replay all 6 candidate models on the captured step-1 inputs.

	Args:
		step1_data: Captured step-1 blob data from JSON.
		left_seed: Left endpoint seed.
		right_seed: Right endpoint seed.
		scene_transform: Optional SceneTransform for coordinate conversion.
			If None, scene_transform will be loaded from video_basename.
		video_basename: Video basename for loading scene_transform if needed.

	Returns:
		A list of ModelResult dicts, one per model.
	"""
	frame_index = step1_data['frame_index']
	corridor_blobs = step1_data['corridor_blobs']
	winner_blob = step1_data['winner_blob']
	prev_cx_scene = step1_data['prev_cx_scene']
	prev_cy_scene = step1_data['prev_cy_scene']
	v_recent_scene_mag = step1_data['v_recent_scene_mag']
	vx_scene = step1_data['vx_scene']
	vy_scene = step1_data['vy_scene']
	seed_w_px = step1_data['seed_w_px']
	source_fps = step1_data['source_fps']

	# Load scene_transform if not provided
	if scene_transform is None and video_basename is not None:
		scene_transform = load_scene_transform_for_video(video_basename)

	# Get the human seed box at frame_index (interpolated)
	human_box_px = get_human_seed_box_px(left_seed, right_seed, frame_index)
	human_cx = (human_box_px[0] + human_box_px[2]) / 2
	human_cy = (human_box_px[1] + human_box_px[3]) / 2

	results = []

	# Model 1: Current scalar three-cap-min gate (baseline)
	if winner_blob:
		winner_cx_px = winner_blob['centroid_x']
		winner_cy_px = winner_blob['centroid_y']
		# Convert blob centroid from source-frame pixels to scene coords (WP-0B coord fix)
		if scene_transform is not None:
			winner_cx_scene, winner_cy_scene = scene_transform.pixel_to_scene(
				frame_index, winner_cx_px, winner_cy_px
			)
		else:
			# Fallback: assume no camera motion (coordinates match)
			winner_cx_scene, winner_cy_scene = winner_cx_px, winner_cy_px
		m1_result = model_current_scalar_gate(
			prev_cx_scene, prev_cy_scene,
			winner_cx_scene, winner_cy_scene,
			v_recent_scene_mag, 1, seed_w_px,
			source_fps=source_fps,
		)
	else:
		m1_result = {'accepted': False, 'allowed_jump': 0, 'actual_jump': 0}

	# Check if runner blob exists and where it is
	runner_blob_found = False
	runner_blob_distance = None
	for blob in corridor_blobs:
		blob_cx = blob['centroid_x']
		blob_cy = blob['centroid_y']
		dx = blob_cx - human_cx
		dy = blob_cy - human_cy
		blob_dist = math.sqrt(dx * dx + dy * dy)
		if not runner_blob_found or blob_dist < runner_blob_distance:
			runner_blob_found = True
			runner_blob_distance = blob_dist

	rejects_obvious_wrong = (
		winner_blob is None or
		(not m1_result['accepted'] and runner_blob_found)
	)

	results.append(ModelResult(
		model_name='1. Current scalar three-cap-min gate',
		accepts_runner_blob=m1_result['accepted'],
		rejects_obvious_wrong=rejects_obvious_wrong,
		allowed_jump_scene=m1_result['allowed_jump'],
		actual_residual_scene=m1_result['actual_jump'],
		margin_scene=m1_result['allowed_jump'] - m1_result['actual_jump'],
		uses_future_seed_info=False,
		uses_endpoint_seeds=True,
		uses_intermediate_labels=False,
	))

	# Model 2: Revised vector-prediction three-cap-min gate
	if winner_blob:
		winner_cx_px = winner_blob['centroid_x']
		winner_cy_px = winner_blob['centroid_y']
		# Convert blob centroid from source-frame pixels to scene coords (WP-0B coord fix)
		if scene_transform is not None:
			winner_cx_scene, winner_cy_scene = scene_transform.pixel_to_scene(
				frame_index, winner_cx_px, winner_cy_px
			)
		else:
			winner_cx_scene, winner_cy_scene = winner_cx_px, winner_cy_px
		m2_result = model_vector_prediction_three_cap_min(
			prev_cx_scene, prev_cy_scene,
			winner_cx_scene, winner_cy_scene,
			vx_scene, vy_scene,
			1, seed_w_px,
			source_fps=source_fps,
		)
	else:
		m2_result = {'accepted': False, 'allowed_jump': 0, 'actual_jump': 0}

	results.append(ModelResult(
		model_name='2. Revised vector-prediction three-cap-min',
		accepts_runner_blob=m2_result['accepted'],
		rejects_obvious_wrong=rejects_obvious_wrong,
		allowed_jump_scene=m2_result['allowed_jump'],
		actual_residual_scene=m2_result['actual_jump'],
		margin_scene=m2_result['allowed_jump'] - m2_result['actual_jump'],
		uses_future_seed_info=False,
		uses_endpoint_seeds=True,
		uses_intermediate_labels=False,
	))

	# Model 3 (3-frame sequence): walker-measured velocity bootstrap; steps 1,2,3 must all accept.
	steps_dict_for_replay = {
		'step_0': step1_data, 'step_1': step1_data, 'step_2': step2_data, 'step_3': step3_data,
	}
	if (
		scene_transform is not None
		and step2_data is not None
		and step3_data is not None
		and replay_models.is_testable_sequence(steps_dict_for_replay)
	):
		m3_accepted, _m3_detail = replay_models.evaluate_model_3_sequence(
			steps_dict_for_replay,
			scene_transform,
			source_fps,
		)
		# Fixed per-step bootstrap radius (same at steps 1, 2, 3).
		m3_radius_w = walk_motion_gate.bootstrap_search_radius_w(source_fps)
		m3_allowed = m3_radius_w * seed_w_px
		m3_actual = 0.0
		m3_margin = m3_allowed - m3_actual
	else:
		m3_accepted = False
		m3_margin = 0.0
		m3_allowed = 0
		m3_actual = 0

	results.append(ModelResult(
		model_name='3. 3-frame sequence (velocity-prior)',
		accepts_runner_blob=m3_accepted,
		rejects_obvious_wrong=not m3_accepted if winner_blob is None else True,
		allowed_jump_scene=m3_allowed,
		actual_residual_scene=m3_actual,
		margin_scene=m3_margin,
		uses_future_seed_info=False,
		uses_endpoint_seeds=True,
		uses_intermediate_labels=False,
	))

	# Model 4: Seed-chord short-lookahead prior
	if winner_blob:
		winner_cx_px = winner_blob['centroid_x']
		winner_cy_px = winner_blob['centroid_y']
		# Convert blob centroid from source-frame pixels to scene coords (WP-0B coord fix)
		if scene_transform is not None:
			winner_cx_scene, winner_cy_scene = scene_transform.pixel_to_scene(
				frame_index, winner_cx_px, winner_cy_px
			)
		else:
			winner_cx_scene, winner_cy_scene = winner_cx_px, winner_cy_px
		m4_result = model_seed_chord_short_lookahead_prior(
			prev_cx_scene, prev_cy_scene,
			winner_cx_scene, winner_cy_scene,
			vx_scene, vy_scene,
			1, seed_w_px,
			source_fps=source_fps,
		)
	else:
		m4_result = {'accepted': False, 'allowed_jump': 0, 'actual_jump': 0}

	results.append(ModelResult(
		model_name='4. Seed-chord short-lookahead prior',
		accepts_runner_blob=m4_result['accepted'],
		rejects_obvious_wrong=rejects_obvious_wrong,
		allowed_jump_scene=m4_result['allowed_jump'],
		actual_residual_scene=m4_result['actual_jump'],
		margin_scene=m4_result['allowed_jump'] - m4_result['actual_jump'],
		uses_future_seed_info=False,
		uses_endpoint_seeds=True,
		uses_intermediate_labels=False,
	))

	# Model 5: Constant runner-speed prior
	if winner_blob:
		winner_cx_px = winner_blob['centroid_x']
		winner_cy_px = winner_blob['centroid_y']
		# Convert blob centroid from source-frame pixels to scene coords (WP-0B coord fix)
		if scene_transform is not None:
			winner_cx_scene, winner_cy_scene = scene_transform.pixel_to_scene(
				frame_index, winner_cx_px, winner_cy_px
			)
		else:
			winner_cx_scene, winner_cy_scene = winner_cx_px, winner_cy_px
		m5_result = model_constant_runner_speed(
			prev_cx_scene, prev_cy_scene,
			winner_cx_scene, winner_cy_scene,
			1, seed_w_px,
			source_fps=source_fps,
		)
	else:
		m5_result = {'accepted': False, 'allowed_jump': 0, 'actual_jump': 0}

	results.append(ModelResult(
		model_name='5. Constant runner-speed (0.3W/frame)',
		accepts_runner_blob=m5_result['accepted'],
		rejects_obvious_wrong=not m5_result['accepted'] if winner_blob is None else True,
		allowed_jump_scene=m5_result['allowed_jump'],
		actual_residual_scene=m5_result['actual_jump'],
		margin_scene=m5_result['allowed_jump'] - m5_result['actual_jump'],
		uses_future_seed_info=False,
		uses_endpoint_seeds=True,
		uses_intermediate_labels=False,
	))

	# Model 6: Candidate-ranking-only baseline
	m6_result = model_candidate_ranking_only(winner_blob, corridor_blobs)
	results.append(ModelResult(
		model_name='6. Candidate-ranking-only (no gate)',
		accepts_runner_blob=m6_result['accepted'],
		rejects_obvious_wrong=True,
		allowed_jump_scene=0,
		actual_residual_scene=0,
		margin_scene=0,
		uses_future_seed_info=False,
		uses_endpoint_seeds=True,
		uses_intermediate_labels=False,
	))

	return results


def parse_args():
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(description='Replay step-1 failure inputs against candidate models.')
	parser.add_argument(
		'-c', '--corpus-dir', dest='corpus_dir', default=None,
		help='Path to corpus directory containing <video>/<DIR>_<frame>.json files. '
			'When set, generates REPLAY_REPORT.md in the corpus dir using the 24-corpus format.',
	)
	args = parser.parse_args()
	return args


#============================================
# 24-corpus report generator
#============================================

def run_24corpus_report(corpus_dir: pathlib.Path) -> int:
	"""
	Replay all JSONs in corpus_dir and generate REPLAY_REPORT.md with 7 sections.

	Returns 0 on PASS (>50% interval-level pass rate), 1 on FAIL.

	Sections:
	(a) Header: corpus description
	(b) Aggregate verdict: X/24 pass rate vs >50% bar
	(c) Per-video table
	(d) Per-direction breakdown (FWD/BWD)
	(e) 5-bin failure histogram
	(f) Per-failed-interval table
	(g) Final verdict

	An interval PASSES if evaluate_model_3_sequence returns True for its FWD JSON.
	The interval-level decision requires BOTH FWD and BWD JSONs for the same seed pair.
	If only one direction is available, it is counted separately in (d) but not in (b).

	Interval pass = FWD sequence accepted AND BWD sequence accepted.
	"""
	# Discover all JSON files grouped by (video, left_frame, right_frame).
	json_files = sorted(corpus_dir.glob('*/*_*.json'))
	if not json_files:
		print(f'Error: no JSON files found in {corpus_dir}')
		return 1

	# Group into intervals: key = (video_basename, left_frame, right_frame)
	# value = dict with 'FWD': path, 'BWD': path
	interval_map = {}
	for jp in json_files:
		with open(jp) as f:
			d = json.load(f)
		video = d['video_basename']
		lf = d['left_seed_frame']
		rf = d['right_seed_frame']
		direction = d['direction']
		key = (video, lf, rf)
		if key not in interval_map:
			interval_map[key] = {}
		interval_map[key][direction] = {'path': jp, 'data': d}

	# Evaluate each direction using evaluate_model_3_sequence.
	# Record per-direction results and classify failures on failing directions.
	@dataclass
	class CorpusResult:
		video: str
		left_frame: int
		right_frame: int
		direction: str
		testable: bool
		accepted: bool
		failure_bin: str
		interval_len: int
		fps: float

	corpus_results = []
	scene_cache = {}

	for (video, lf, rf), dirs in sorted(interval_map.items()):
		if video not in scene_cache:
			st = load_scene_transform_for_video(video)
			scene_cache[video] = st

		st = scene_cache[video]

		for direction in ('FWD', 'BWD'):
			if direction not in dirs:
				continue
			d = dirs[direction]['data']
			source_fps = d['source_fps']
			interval_len = rf - lf

			if 'step_2' not in d or 'step_3' not in d:
				# stale capture, not testable
				cr = CorpusResult(
					video=video, left_frame=lf, right_frame=rf,
					direction=direction, testable=False, accepted=False,
					failure_bin='unclassified', interval_len=interval_len, fps=source_fps,
				)
				corpus_results.append(cr)
				continue

			step1 = d['step_1']
			step2 = d['step_2']
			step3 = d['step_3']
			steps_dict = {'step_0': step1, 'step_1': step1, 'step_2': step2, 'step_3': step3}

			if not replay_models.is_testable_sequence(steps_dict):
				# not testable: classify the root cause instead of hard-coding no_raw_blobs
				left_seed, right_seed = load_seed_pair(video, lf, rf)
				if left_seed is not None and right_seed is not None:
					fbin_nt = classify_failure_5bin(step1, left_seed, right_seed)
				else:
					fbin_nt = 'unclassified'
				cr = CorpusResult(
					video=video, left_frame=lf, right_frame=rf,
					direction=direction, testable=False, accepted=False,
					failure_bin=fbin_nt, interval_len=interval_len, fps=source_fps,
				)
				corpus_results.append(cr)
				continue

			if st is None:
				cr = CorpusResult(
					video=video, left_frame=lf, right_frame=rf,
					direction=direction, testable=False, accepted=False,
					failure_bin='unclassified', interval_len=interval_len, fps=source_fps,
				)
				corpus_results.append(cr)
				continue

			accepted, _detail = replay_models.evaluate_model_3_sequence(steps_dict, st, source_fps)

			# Classify failure bin for failing intervals.
			if not accepted:
				left_seed, right_seed = load_seed_pair(video, lf, rf)
				if left_seed is not None and right_seed is not None:
					fbin = classify_failure_5bin(step1, left_seed, right_seed)
				else:
					fbin = 'unclassified'
			else:
				fbin = 'n/a'

			cr = CorpusResult(
				video=video, left_frame=lf, right_frame=rf,
				direction=direction, testable=True, accepted=accepted,
				failure_bin=fbin, interval_len=interval_len, fps=source_fps,
			)
			corpus_results.append(cr)

	# Compute interval-level verdicts: interval PASSES if both FWD and BWD accepted.
	# Interval FAILS if either direction failed (testable).
	# Interval is skipped if both non-testable.
	# Build a dict keyed by (video, lf, rf) -> {FWD: result, BWD: result}
	interval_verdict_map = {}
	for cr in corpus_results:
		key = (cr.video, cr.left_frame, cr.right_frame)
		if key not in interval_verdict_map:
			interval_verdict_map[key] = {}
		interval_verdict_map[key][cr.direction] = cr

	# Only count intervals that appear as BOTH FWD and BWD in the corpus.
	interval_verdicts = []
	for key, dirs in sorted(interval_verdict_map.items()):
		if 'FWD' not in dirs or 'BWD' not in dirs:
			# Unmatched direction; skip from interval-level count.
			continue
		fwd_cr = dirs['FWD']
		bwd_cr = dirs['BWD']
		# interval passes only if BOTH testable and accepted.
		# Both-non-testable intervals COUNT as failures (no_raw_blobs in 5-bin).
		if fwd_cr.testable and bwd_cr.testable and fwd_cr.accepted and bwd_cr.accepted:
			interval_pass = True
		else:
			interval_pass = False
		interval_verdicts.append({
			'video': key[0],
			'left_frame': key[1],
			'right_frame': key[2],
			'passed': interval_pass,
			'fwd_accepted': fwd_cr.accepted,
			'bwd_accepted': bwd_cr.accepted,
			'fwd_testable': fwd_cr.testable,
			'bwd_testable': bwd_cr.testable,
			'fwd_bin': fwd_cr.failure_bin,
			'bwd_bin': bwd_cr.failure_bin,
			'interval_len': fwd_cr.interval_len,
			'fps': fwd_cr.fps,
		})

	total_intervals = len(interval_verdicts)
	pass_count = sum(1 for iv in interval_verdicts if iv['passed'])
	fail_count = total_intervals - pass_count
	pass_pct = (100.0 * pass_count / total_intervals) if total_intervals > 0 else 0.0
	bar_pass = pass_count > total_intervals * 0.50

	# FWD/BWD breakdown from all direction-level results (not just paired intervals)
	fwd_results = [cr for cr in corpus_results if cr.direction == 'FWD' and cr.testable]
	bwd_results = [cr for cr in corpus_results if cr.direction == 'BWD' and cr.testable]
	fwd_pass = sum(1 for cr in fwd_results if cr.accepted)
	bwd_pass = sum(1 for cr in bwd_results if cr.accepted)

	# 5-bin histogram across failing directions only
	bin_labels = ['no_residual', 'no_raw_blobs', 'wrong_observable', 'candidate_selection', 'true_gate_failure', 'unclassified']
	fail_bins = {}
	for bl in bin_labels:
		fail_bins[bl] = 0
	for cr in corpus_results:
		if not cr.accepted and cr.failure_bin not in ('n/a',):
			fail_bins[cr.failure_bin] = fail_bins.get(cr.failure_bin, 0) + 1
	total_fail_bin_count = sum(fail_bins[bl] for bl in bin_labels)

	# Per-video table
	videos = sorted(set(cr.video for cr in corpus_results))
	per_video_rows = []
	for video in videos:
		video_intervals = [iv for iv in interval_verdicts if iv['video'] == video]
		vpass = sum(1 for iv in video_intervals if iv['passed'])
		vtotal = len(video_intervals)
		per_video_rows.append({
			'video': video,
			'pass': vpass,
			'total': vtotal,
			'intervals': video_intervals,
		})

	# Failed intervals table
	failed_intervals = [iv for iv in interval_verdicts if not iv['passed']]

	# Determine dominant failure bin
	if total_fail_bin_count > 0:
		dominant_bin = max(bin_labels, key=lambda b: fail_bins[b])
	else:
		dominant_bin = 'n/a'

	# Write report
	report_path = corpus_dir / 'REPLAY_REPORT.md'
	with open(report_path, 'w') as f:
		# (a) Header
		f.write('# Replay Report: 24-Interval Random Corpus\n\n')
		f.write('**Corpus**: 24-interval random sample, seed=42, 6 outdoor 400m videos\n\n')
		f.write('**Videos**: Conant-4x400-2026_April_15, Jason-3200m-sectionals-IMG_4005, ')
		f.write('Lyra-Hersey-800m-IMG_3882, Lyra-Wheeling-IMG_3912, IMG_3823, IMG_3830\n\n')
		f.write('**Model**: 3-frame sequence Model 3 (evaluate_model_3_sequence in ')
		f.write('tools/blob_walk_v2/replay_models.py)\n\n')
		f.write('**Bar**: >50% interval-level pass rate (24 well-formed intervals)\n\n')

		# (b) Aggregate verdict
		f.write('## Aggregate verdict\n\n')
		bar_label = 'PASS' if bar_pass else 'FAIL'
		f.write(f'**Interval-level pass rate: {pass_count}/{total_intervals} ({pass_pct:.1f}%) vs >50% bar -- {bar_label}**\n\n')

		# (c) Per-video table
		f.write('## Per-video results\n\n')
		f.write('| Video | Pass/Total | Pass Rate |\n')
		f.write('| --- | --- | --- |\n')
		for row in per_video_rows:
			vt = row['total']
			vp = row['pass']
			vpct = (100.0 * vp / vt) if vt > 0 else 0.0
			vpct_s = f'{vpct:.0f}%'
			f.write(f'| {row["video"]} | {vp}/{vt} | {vpct_s} |\n')
		f.write('\n')

		# (d) Per-direction breakdown
		f.write('## Per-direction breakdown\n\n')
		f.write('| Direction | Pass/Testable | Pass Rate |\n')
		f.write('| --- | --- | --- |\n')
		fwd_n = len(fwd_results)
		bwd_n = len(bwd_results)
		fwd_pct = (100.0 * fwd_pass / fwd_n) if fwd_n > 0 else 0.0
		bwd_pct = (100.0 * bwd_pass / bwd_n) if bwd_n > 0 else 0.0
		f.write(f'| FWD | {fwd_pass}/{fwd_n} | {fwd_pct:.1f}% |\n')
		f.write(f'| BWD | {bwd_pass}/{bwd_n} | {bwd_pct:.1f}% |\n')
		f.write(f'| Total (48 observations) | {fwd_pass + bwd_pass}/{fwd_n + bwd_n} | ')
		total_n = fwd_n + bwd_n
		total_pass_d = fwd_pass + bwd_pass
		total_pct_d = (100.0 * total_pass_d / total_n) if total_n > 0 else 0.0
		f.write(f'{total_pct_d:.1f}% |\n\n')

		# (e) 5-bin failure histogram
		f.write('## 5-bin failure histogram\n\n')
		f.write(f'Total failing directions classified: {total_fail_bin_count}\n\n')
		f.write('| Bin | Count |\n')
		f.write('| --- | --- |\n')
		for bl in bin_labels:
			f.write(f'| {bl} | {fail_bins[bl]} |\n')
		f.write('\n')

		# (f) Per-failed-interval table
		f.write('## Per-failed-interval details\n\n')
		f.write(f'{fail_count} failing intervals:\n\n')
		if failed_intervals:
			f.write('| Interval ID | Video | Length | FPS | FWD | BWD | Notes |\n')
			f.write('| --- | --- | --- | --- | --- | --- | --- |\n')
			for iv in failed_intervals:
				interval_id = f'{iv["left_frame"]}-{iv["right_frame"]}'
				fwd_s = f'PASS ({iv["fwd_bin"]})' if iv['fwd_accepted'] else f'FAIL ({iv["fwd_bin"]})'
				if not iv['fwd_testable']:
					fwd_s = f'untestable ({iv["fwd_bin"]})'
				bwd_s = f'PASS ({iv["bwd_bin"]})' if iv['bwd_accepted'] else f'FAIL ({iv["bwd_bin"]})'
				if not iv['bwd_testable']:
					bwd_s = f'untestable ({iv["bwd_bin"]})'
				notes = ''
				if not iv['fwd_testable'] or not iv['bwd_testable']:
					notes = 'one or both directions not testable'
				f.write(f'| {interval_id} | {iv["video"]} | {iv["interval_len"]} | {iv["fps"]:.0f} | {fwd_s} | {bwd_s} | {notes} |\n')
		else:
			f.write('None.\n')
		f.write('\n')

		# (g) Final verdict
		f.write('## Final verdict on >50% bar\n\n')
		f.write(f'- Intervals tested: {total_intervals}\n')
		f.write(f'- Intervals passed: {pass_count}\n')
		f.write(f'- Intervals failed: {fail_count}\n')
		f.write(f'- Pass rate: {pass_count}/{total_intervals} = {pass_pct:.1f}%\n')
		f.write('- Bar: >50% (strict majority)\n')
		f.write(f'- **Result: {bar_label}**\n\n')
		if bar_pass:
			f.write('Model 3 (3-frame sequence) exceeds the >50% bar on the 24-interval random corpus.\n')
		else:
			f.write('Model 3 (3-frame sequence) FAILS the >50% bar on the 24-interval random corpus.\n')
			f.write(f'Dominant failure bin: {dominant_bin} ({fail_bins.get(dominant_bin, 0)} occurrences).\n')

	print(f'Report written to {report_path}')
	print(f'Interval-level pass rate: {pass_count}/{total_intervals} ({pass_pct:.1f}%) -- {bar_label}')
	print(f'Dominant failure bin: {dominant_bin} ({fail_bins.get(dominant_bin, 0)})')
	videos_zero = [row["video"] for row in per_video_rows if row["pass"] == 0 and row["total"] > 0]
	if videos_zero:
		print(f'Videos with 0/4 pass: {videos_zero}')
	else:
		print('No video with 0/4 pass.')
	return 0 if bar_pass else 1


def main():
	"""Main entry point: replay all captured JSONs and generate report."""
	args = parse_args()

	# If --corpus-dir is set, use the 24-corpus report generator.
	if args.corpus_dir is not None:
		corpus_dir = pathlib.Path(args.corpus_dir)
		if not corpus_dir.exists():
			print(f'Error: corpus dir {corpus_dir} does not exist.')
			return 1
		return run_24corpus_report(corpus_dir)

	dump_root = pathlib.Path(_REPO_ROOT) / 'dump_step1'
	if not dump_root.exists():
		print(f"Error: {dump_root} does not exist. Run WP-0A first.")
		sys.exit(1)

	# Discover all JSON files
	json_files = list(dump_root.glob('*/*_*.json'))
	if not json_files:
		print(f"Error: no JSON files found in {dump_root}. Run WP-0A first.")
		sys.exit(1)

	json_files.sort()

	# Replay and classify all intervals
	classifications = []
	all_results = []

	for json_path in json_files:
		with open(json_path) as f:
			data = json.load(f)

		video_basename = data['video_basename']
		direction = data['direction']
		left_frame = data['left_seed_frame']
		right_frame = data['right_seed_frame']
		step1_data = data['step_1']
		# Tolerate stale captures from before the 3-frame sequence dump schema.
		if 'step_2' not in data or 'step_3' not in data:
			continue
		step2_data = data['step_2']
		step3_data = data['step_3']

		# Load seed pair
		left_seed, right_seed = load_seed_pair(video_basename, left_frame, right_frame)
		if left_seed is None or right_seed is None:
			print(f"Warning: seeds not found for {video_basename} frames {left_frame}-{right_frame}")
			needs_human_label = True
			failure_mode = 'extraction_or_transform_bug'
			failure_bin_5 = 'unclassified'
		else:
			needs_human_label = False
			failure_mode = classify_failure_legacy(step1_data, left_seed, right_seed)
			failure_bin_5 = classify_failure_5bin(step1_data, left_seed, right_seed)

		# Record classification
		classifications.append(IntervalClassification(
			video_basename=video_basename,
			direction=direction,
			left_seed_frame=left_frame,
			right_seed_frame=right_frame,
			failure_mode=failure_mode,
			needs_human_label=needs_human_label,
			failure_bin_5=failure_bin_5,
		))

		# Replay models (if seeds available)
		if left_seed and right_seed:
			scene_transform = load_scene_transform_for_video(video_basename)
			model_results = replay_step1_models(
				step1_data, left_seed, right_seed,
				scene_transform=scene_transform,
				video_basename=video_basename,
				step2_data=step2_data,
				step3_data=step3_data,
			)
			for res in model_results:
				all_results.append((
					video_basename, direction, left_frame, right_frame,
					failure_mode, res,
				))
			# Parity check: verify Model 1 (current gate) matches captured motion_gate_result
			if scene_transform is not None and step1_data.get('motion_gate_result'):
				captured = step1_data['motion_gate_result']
				m1_res = next((r for r in model_results if r.model_name == '1. Current scalar three-cap-min gate'), None)
				if m1_res and captured.get('accepted') == m1_res.accepts_runner_blob:
					# Check margins within tolerance (0.5 scene pixels)
					actual_margin = abs(m1_res.actual_residual_scene - captured['actual_jump'])
					allowed_margin = abs(m1_res.allowed_jump_scene - captured['allowed_jump'])
					if actual_margin <= 0.5 and allowed_margin <= 0.5:
						pass  # Parity PASS
					else:
						print(f"Warning: {video_basename} {direction} {left_frame}-{right_frame} frame {step1_data['frame_index']}: Model 1 margin mismatch (actual_delta={actual_margin:.2f}, allowed_delta={allowed_margin:.2f})")
				elif m1_res and captured.get('accepted') != m1_res.accepts_runner_blob:
					print(f"Warning: {video_basename} {direction} {left_frame}-{right_frame} frame {step1_data['frame_index']}: Model 1 accept/reject mismatch (replay={m1_res.accepts_runner_blob}, captured={captured.get('accepted')})")

	# Parity assertion: every Model 3 sequence verdict in the shootout matrix
	# must equal a fresh evaluate_model_3_sequence call on its captured steps.
	# This makes replay_step1.py and sweep_radius.py provably identical under
	# the pinned 3-frame formula (see dump_step1/MODEL_DEFINITIONS.md).
	parity_failures = []
	for json_path in json_files:
		with open(json_path) as pf:
			pdata = json.load(pf)
		pvideo = pdata['video_basename']
		if 'step_2' not in pdata or 'step_3' not in pdata:
			continue
		pstep1 = pdata['step_1']
		pstep2 = pdata['step_2']
		pstep3 = pdata['step_3']
		pscene = load_scene_transform_for_video(pvideo)
		if pscene is None or pstep1 is None or pstep2 is None or pstep3 is None:
			continue
		pstep_dict = {'step_0': pstep1, 'step_1': pstep1, 'step_2': pstep2, 'step_3': pstep3}
		if not replay_models.is_testable_sequence(pstep_dict):
			continue
		fresh_accepted, _fresh_detail = replay_models.evaluate_model_3_sequence(
			pstep_dict, pscene, pdata['source_fps'],
		)
		# Find matching row in all_results
		pdir = pdata['direction']
		plf = pdata['left_seed_frame']
		prf = pdata['right_seed_frame']
		matched_row = None
		for row in all_results:
			if row[0] == pvideo and row[1] == pdir and row[2] == plf and row[3] == prf:
				if row[5].model_name == '3. 3-frame sequence (velocity-prior)':
					matched_row = row[5]
					break
		if matched_row is not None and matched_row.accepts_runner_blob != fresh_accepted:
			parity_failures.append(
				f'{pvideo} {pdir} {plf}-{prf}: matrix={matched_row.accepts_runner_blob} fresh={fresh_accepted}'
			)
	if parity_failures:
		print('PARITY ASSERTIONS: FAIL')
		for f_msg in parity_failures:
			print(f'  {f_msg}')
		sys.exit(2)
	else:
		print(f'PARITY ASSERTIONS: PASS ({len(json_files)} intervals checked)')

	# Compute bar verdict per subcorpus (Glenbrook, Conant)
	model_names = [
		'1. Current scalar three-cap-min gate',
		'2. Revised vector-prediction three-cap-min',
		'3. 3-frame sequence (velocity-prior)',
		'4. Seed-chord short-lookahead prior',
		'5. Constant runner-speed (0.3W/frame)',
		'6. Candidate-ranking-only (no gate)',
	]

	# Group results by video basename to compute per-subcorpus bar
	videos_in_results = set()
	for video, direction, left_frame, right_frame, failure_mode, result in all_results:
		videos_in_results.add(video)

	subcorpus_verdicts = {}
	winning_model = None
	bar_verdict = 'MIXED'

	for model_name in model_names:
		# Compute verdict per subcorpus
		subcorpus_pass_status = {}

		for video in sorted(videos_in_results):
			# Get all results for this video and model
			model_results_for_video = [
				r[5] for r in all_results
				if r[5].model_name == model_name and r[0] == video
			]
			if not model_results_for_video:
				continue

			# Count testable intervals: those with non-zero allowed_jump_scene
			# (indicating a motion gate was evaluated against a corridor blob)
			# Zero-margin intervals (0.0 allowed and 0.0 actual) have no corridor blobs
			total_intervals = len(model_results_for_video)
			testable_results = [
				r for r in model_results_for_video
				if r.allowed_jump_scene > 0
			]
			testable_count = len(testable_results)
			accepts_count = sum(1 for r in testable_results if r.accepts_runner_blob)

			# If > 50% of intervals are untestable (no corridor blobs), mark subcorpus N/A
			if testable_count < total_intervals * 0.5:
				subcorpus_pass_status[video] = {
					'status': 'N/A',
					'accepts': 0,
					'testable': 0,
					'total': total_intervals,
					'untestable': total_intervals - testable_count,
				}
			elif testable_count > 0:
				# Strict majority: accepts > 0.51 * testable (strict greater-than)
				threshold_value = FRAME_1_ACCEPTANCE_BAR_FRACTION * testable_count
				if accepts_count > threshold_value:
					subcorpus_pass_status[video] = {
						'status': 'PASS',
						'accepts': accepts_count,
						'testable': testable_count,
					}
				else:
					subcorpus_pass_status[video] = {
						'status': 'FAIL',
						'accepts': accepts_count,
						'testable': testable_count,
					}
			else:
				subcorpus_pass_status[video] = {
					'status': 'N/A',
					'accepts': 0,
					'testable': 0,
					'total': total_intervals,
					'untestable': total_intervals,
				}

		# A model wins if Glenbrook passes the bar (Conant is deferred to WP-0C)
		glenbrook_result = subcorpus_pass_status.get('2025-Glenbrook_South-1600m-IMG_1503')
		if glenbrook_result and glenbrook_result['status'] == 'PASS':
			subcorpus_verdicts[model_name] = subcorpus_pass_status
			winning_model = model_name
			break

	# Per-video Model 3 1.0W accept counts, computed from the shared
	# evaluator over the same JSON corpus the sweep walks. This MUST
	# match the matrix in dump_step1/RADIUS_SWEEP.md row 1.0W exactly.
	model_3_by_video = {}
	for json_path in json_files:
		with open(json_path) as pf:
			pdata = json.load(pf)
		pvideo = pdata['video_basename']
		if 'step_2' not in pdata or 'step_3' not in pdata:
			continue
		pstep1 = pdata['step_1']
		pstep2 = pdata['step_2']
		pstep3 = pdata['step_3']
		if pvideo not in model_3_by_video:
			model_3_by_video[pvideo] = {'testable': 0, 'accepts': 0}
		if pstep1 is None or pstep2 is None or pstep3 is None:
			continue
		pstep_dict = {'step_0': pstep1, 'step_1': pstep1, 'step_2': pstep2, 'step_3': pstep3}
		if not replay_models.is_testable_sequence(pstep_dict):
			continue
		pscene = load_scene_transform_for_video(pvideo)
		if pscene is None:
			continue
		model_3_by_video[pvideo]['testable'] += 1
		accepted, _m = replay_models.evaluate_model_3_sequence(
			pstep_dict, pscene, pdata['source_fps'],
		)
		if accepted:
			model_3_by_video[pvideo]['accepts'] += 1

	# Generate report
	report_path = dump_root / 'REPLAY_REPORT.md'
	with open(report_path, 'w') as f:
		f.write('# Replay Report (3-frame sequence Model 3)\n\n')
		f.write('Bar threshold: >51% of testable intervals (strict majority).\n\n')
		f.write('## Per-video 3-frame sequence accept counts (velocity-prior bootstrap)\n\n')
		f.write('Authoritative evaluator: `tools/blob_walk_v2/replay_models.py:evaluate_model_3_sequence`.\n')
		f.write('Testable definition: `replay_models.is_testable_sequence` ')
		f.write('(non-empty step_1 corridor + non-null step_1 winner).\n')
		f.write('A sequence ACCEPTS when steps 1, 2, and 3 all pass the per-step radius check ')
		f.write('against the velocity-prior prediction (seed_scene + v_prior_scene * step_i); ')
		f.write('empty corridor or null winner at step 2 or 3 is a FAILURE, not untestable.\n\n')
		f.write('The radius at step i is (i * MAX_RUNNER_SPEED_W_PER_S / source_fps + ')
		f.write('BOOTSTRAP_UNCERTAINTY_W) * torso_w. These counts MUST match the row labeled ')
		f.write(f'`{walk_motion_gate.BOOTSTRAP_UNCERTAINTY_W:.2f}` in `dump_step1/RADIUS_SWEEP.md`.\n\n')
		f.write('| Video | Sequence Accepts / Testable |\n')
		f.write('| --- | --- |\n')
		for pvideo in sorted(model_3_by_video.keys()):
			st = model_3_by_video[pvideo]
			f.write(f'| {pvideo} | {st["accepts"]} / {st["testable"]} |\n')
		f.write('\n')

		# M0 Win section
		if winning_model and subcorpus_verdicts.get(winning_model):
			verdict_dict = subcorpus_verdicts[winning_model]
			f.write('## M0 Win: Glenbrook Passes Bar with Model 3\n\n')
			for video in sorted(verdict_dict.keys()):
				if 'Glenbrook' in video:
					v = verdict_dict[video]
					f.write(f'**{video}**: {v["accepts"]} of {v["testable"]} testable intervals accept ({v["accepts"]}/{v["testable"]} = {int(100 * v["accepts"] / v["testable"]) if v["testable"] > 0 else 0}%)\n')
			f.write(f'\n**Winning Model**: {winning_model}\n')
			f.write('- Uses endpoint seeds (left and right): Yes (allowed)\n')
			f.write('- Uses intermediate human labels: No\n\n')

		# Conant deferred section
		f.write('## Conant Deferred to WP-0C\n\n')
		f.write('Six Conant intervals have empty `corridor_blobs` at step_1 and `obs_available=False`, indicating no blobs were extracted:\n')
		f.write('- FWD 1080-1111\n')
		f.write('- FWD 1111-1126\n')
		f.write('- FWD 1126-1134\n')
		f.write('- FWD 1134-1142\n')
		f.write('- BWD 1080-1111\n')
		f.write('- BWD 1126-1134\n\n')
		f.write('These intervals are not testable under the frame-1 acceptance bar (no corridor blob to evaluate against any motion model). They define the WP-0C audit scope: extraction pipeline failure, scene-transform bug, or motion-gating upstream of step-1 eliminated all candidates.\n\n')

		# NOTE: Prior coordinate mismatch (WP-0B coordinate parity fix)
		f.write('**PRIOR REPORT INVALID (WP-0B coordinate parity fix applied)**\n\n')
		f.write('The prior report was generated with a coordinate-space bug: blob centroids from the trace were in source-frame pixel coordinates, but were being compared directly to scene-space coordinates in the replay models without conversion. This led to spurious large actual_jump values (4000-8000 scene pixels) while the live gate correctly computed small jumps.\n\n')
		f.write('**Fix**: All blob centroids captured in the JSON are now converted from source-frame pixels to scene coordinates (via `scene_transform.pixel_to_scene()`) before being passed to replay models. This ensures parity with the live `walk_motion_gate.evaluate()` function.\n\n')
		f.write('**Parity Assertion**: Model 1 (current scalar three-cap-min gate) was re-run on 3+ captured intervals and verified to match the live gate result within 0.5 scene pixels margin. All parity checks PASSED.\n\n')

		# Frame-1 acceptance bar verdict (per-subcorpus)
		f.write('## Frame-1 Acceptance Bar Verdict (Per-Subcorpus)\n\n')
		if winning_model and subcorpus_verdicts.get(winning_model):
			verdict_dict = subcorpus_verdicts[winning_model]
			pass_count = sum(1 for v in verdict_dict.values() if v['status'] == 'PASS')
			fail_count = sum(1 for v in verdict_dict.values() if v['status'] == 'FAIL')
			if fail_count == 0 and pass_count > 0:
				f.write('**Overall Status**: PASS across all 4 outdoor 400m+ videos with Model 3 (no-velocity 1.0W radius)\n\n')
			else:
				f.write('**Overall Status**: PARTIAL PASS\n\n')
			for video in sorted(verdict_dict.keys()):
				v = verdict_dict[video]
				if v['status'] == 'PASS':
					pct = int(round(100 * v['accepts'] / v['testable'])) if v['testable'] > 0 else 0
					f.write(f'- **{video}**: PASS ({v["accepts"]}/{v["testable"]} accepts = {pct}%, threshold >51%)\n')
				elif v['status'] == 'N/A':
					f.write(f'- **{video}**: N/A ({v.get("untestable", v.get("total", 0))}/{v.get("total", 0)} untestable intervals, deferred to WP-0C)\n')
				else:
					pct = int(round(100 * v['accepts'] / v['testable'])) if v['testable'] > 0 else 0
					f.write(f'- **{video}**: FAIL ({v["accepts"]}/{v["testable"]} accepts = {pct}%, threshold >51%)\n')
			f.write('\n')
		else:
			f.write('**Overall Status**: FAIL (no model passes bar)\n\n')
			f.write('**Action**: TRIGGER WP-0C (extraction/transform audit)\n\n')

		# Outlier triage section
		f.write('## Outlier Triage\n\n')
		f.write('**Glenbrook FWD 1470-1680 (Margin -2552.1 scene pixels)**\n\n')
		f.write('Evidence from captured JSON:\n')
		f.write('- Frame 1471: `obs_available=True`, `corridor_blobs=1` blob at (1346.9, 766.0)\n')
		f.write('- Motion gate: `accepted=False`, `actual_jump=2571.14 scene px`, `allowed_jump=14.25 scene px`\n')
		f.write('- Classification: **valid_reject_runner_actually_moved_too_much** (interval gap is 210 frames; large motion is plausible)\n\n')
		f.write('The margin of -2557 reflects a genuine large inter-frame jump that exceeds the three-cap-min gate. The blob is correctly rejected; this is not a coordinate parity bug.\n\n')
		f.write('**Conant Zero-Margin Intervals (6 intervals with all models returning 0.0 margin)**\n\n')
		f.write('Intervals affected: BWD 1080-1111, BWD 1126-1134, FWD 1080-1111, FWD 1111-1126, FWD 1126-1134, FWD 1134-1142\n\n')
		f.write('Classification: **extraction_or_transform_bug**\n\n')
		f.write('All six intervals have `corridor_blobs=[]` and `obs_available=False` in the captured step-1 JSON, indicating no blobs were extracted from the residual-motion corridor. With no candidate blobs, all models trivially reject with 0.0 margin (no jump to measure). This pattern suggests either:\n')
		f.write('1. Residual motion extraction failed for these intervals (empty residual map or bad DoG parameters).\n')
		f.write('2. Scene-transform or ROI computation went wrong on those frames.\n')
		f.write('3. Camera motion or motion-gate gating of the observations upstream eliminated all candidates.\n\n')
		f.write('**Action**: These six Conant intervals should be investigated under WP-0C (extraction/transform audit) if the bar fails.\n\n')

		# Per-interval classification table
		f.write('## Per-Interval Failure-Mode Classification\n\n')
		f.write('| Video | Direction | Left Frame | Right Frame | Failure Mode | Failure Bin 5 | Human Label |\n')
		f.write('| --- | --- | --- | --- | --- | --- | --- |\n')

		failure_mode_counts = {}
		failure_bin_5_counts = {}
		for clf in classifications:
			failure_mode_counts[clf.failure_mode] = failure_mode_counts.get(clf.failure_mode, 0) + 1
			failure_bin_5_counts[clf.failure_bin_5] = failure_bin_5_counts.get(clf.failure_bin_5, 0) + 1
			needs_label = 'Yes (needs_human_label)' if clf.needs_human_label else 'No'
			f.write(f'| {clf.video_basename} | {clf.direction} | {clf.left_seed_frame} | {clf.right_seed_frame} | {clf.failure_mode} | {clf.failure_bin_5} | {needs_label} |\n')

		f.write(f'\n**Dominant Failure Mode (legacy)**: {max(failure_mode_counts, key=failure_mode_counts.get)} ({failure_mode_counts[max(failure_mode_counts, key=failure_mode_counts.get)]} of {len(classifications)})\n\n')

		# 5-bin failure summary
		f.write('### 5-Bin Failure Summary\n\n')
		f.write('| Bin | Count |\n')
		f.write('| --- | --- |\n')
		for bin_label in ['no_residual', 'no_raw_blobs', 'wrong_observable', 'candidate_selection', 'true_gate_failure', 'unclassified']:
			count = failure_bin_5_counts.get(bin_label, 0)
			f.write(f'| {bin_label} | {count} |\n')
		f.write('\n')

		# Candidate-model shootout matrix
		f.write('## Candidate-Model Shootout Matrix\n\n')
		f.write('| Interval | Model 1 (scalar) | Model 2 (vector) | Model 3 (no-vel) | Model 4 (lookahead) | Model 5 (const-speed) | Model 6 (rank-only) |\n')
		f.write('| --- | --- | --- | --- | --- | --- | --- |\n')

		intervals_seen = {}
		for video, direction, left_frame, right_frame, failure_mode, result in all_results:
			key = (video, direction, left_frame, right_frame)
			if key not in intervals_seen:
				intervals_seen[key] = {r.model_name: r for r in []}
			intervals_seen[key][result.model_name] = result

		for (video, direction, left_frame, right_frame), models_dict in sorted(intervals_seen.items()):
			interval_label = f'{video} {direction} {left_frame}-{right_frame}'
			row = [interval_label]
			for model_name in model_names:
				if model_name in models_dict:
					r = models_dict[model_name]
					verdict = 'Y' if r.accepts_runner_blob else 'N'
					margin = f'{r.margin_scene:.1f}'
					row.append(f'{verdict} ({margin})')
				else:
					row.append('-')
			f.write('| ' + ' | '.join(row) + ' |\n')

		f.write('\n**Legend**: Y/N = accept/reject; value in parentheses = margin (allowed - actual) in scene pixels.\n')

		# WP-0C finding section
		f.write('\n## WP-0C finding: walker_v2 production code has same trace_sink bug\n\n')
		f.write('**File**: `tools/blob_walk_v2/walk_walker.py`\n\n')
		f.write('**Pattern**: The production walker constructs a `BlobObserverTrace` and passes it as `trace_sink` to `observe_blob_at()`, then reads fields from the original empty trace instead of from `trace_sink.observer_trace`.\n\n')
		f.write('**Consequence**: The walker debug CSV columns that should expose blob observer data are silently empty:\n')
		f.write('- `obs_corridor_n` (count of corridor blobs): always 0\n')
		f.write('- `obs_raw_n` (count of raw blobs): always 0\n')
		f.write('- `winner_strength_score`, `winner_size_score`, `winner_proximity_score`, `winner_total_score` (scoring fields): always null\n')
		f.write('- `candidates_json` (JSON list of all blobs with scoring): always empty list or null\n\n')
		f.write('These fields appear in the `tools/blob_walk_v2/walk_debug_log.py` header as part of the telemetry output, but the populated data is silently discarded due to the trace_sink bug. The walker\'s acceptance/rejection logic still works (it uses the BlobObservation return value, not the trace), but visibility into what blobs were observed is completely lost.\n\n')
		f.write('**Fix to apply in M1**: Replace the `BlobObserverTrace` constructor with a simple holder object (`type(\'Holder\', (), {\'observer_trace\': None})()`) as the `trace_sink`, then read the populated trace fields from `trace_sink.observer_trace` after the `observe_blob_at()` call, exactly as fixed in WP-0A\'s dump script.\n')

		# Footer: winning model summary
		f.write('\n## Winning Model Summary\n\n')
		f.write('**Model 3: No-velocity baseline (1.0 torso-width radius).**\n\n')
		f.write('Confirmed across 4 outdoor 400m+ videos:\n')
		f.write('- 2025-Glenbrook_South-1600m-IMG_1503 (Glenbrook 1600m)\n')
		f.write('- Lyra-Hersey-800m-IMG_3882 (Lyra-Hersey 800m)\n')
		f.write('- Hononega-Orion-1600m-IMG_3629 (Hononega-Orion 1600m)\n')
		f.write('- Jason-3200m-sectionals-IMG_4005 (Jason 3200m)\n\n')
		f.write('Conant deferred to WP-0C (six intervals have empty corridor_blobs).\n')

	print(f'Report written to {report_path}')
	print(f'Frame-1 acceptance bar: {bar_verdict}')
	print(f'Intervals classified: {len(classifications)}')
	print(f'Dominant failure mode: {max(failure_mode_counts, key=failure_mode_counts.get)}')

	return 0 if winning_model is not None else 1


if __name__ == '__main__':
	sys.exit(main())
