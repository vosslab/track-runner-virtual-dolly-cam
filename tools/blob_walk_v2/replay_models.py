"""
Authoritative evaluators for replay candidate motion models on captured step-1 JSONs.

This module is the single source of truth for Models 1-6 used by both
`replay_step1.py` (per-interval shootout report) and `sweep_radius.py`
(radius parameter sweep). Both callers must import from here so the two
reports cannot diverge again. See dump_step1/MODEL_DEFINITIONS.md.

Coordinate convention:
- Blob centroids in the captured JSON are in source-frame PIXELS.
- prev_cx_scene/prev_cy_scene in the captured JSON are in SCENE coordinates.
- To compare a blob to prev_scene, the blob centroid must be converted via
  scene_transform.pixel_to_scene(frame_index, px, py).
- seed_w_px is the torso width unit used to scale gate radii.
"""

# Standard Library
import math

# local repo modules
import walk_motion_gate


#============================================
# Testable interval definition
#============================================

def is_testable(step_1: dict) -> bool:
	"""
	Pinned definition: an interval's step-1 frame is testable when
	corridor_blobs is non-empty AND winner_blob is non-null.
	If either condition fails, the interval cannot be used by any motion
	model and must be excluded from per-video accept ratios.
	"""
	corridor_blobs = step_1['corridor_blobs']
	winner_blob = step_1['winner_blob']
	testable = len(corridor_blobs) > 0 and winner_blob is not None
	return testable


#============================================
# Helpers
#============================================

def _winner_scene_xy(step_1: dict, scene_transform, frame_index: int) -> tuple:
	"""
	Convert the production winner blob centroid from source-frame pixels
	to scene coordinates. Returns (winner_x_scene, winner_y_scene).
	Caller must ensure winner_blob is not None.
	"""
	winner = step_1['winner_blob']
	wx_px = winner['centroid_x']
	wy_px = winner['centroid_y']
	wx_scene, wy_scene = scene_transform.pixel_to_scene(frame_index, wx_px, wy_px)
	return wx_scene, wy_scene


#============================================
# Model 1: Live production scalar three-cap-min gate
#============================================

def evaluate_model_1(step_1: dict) -> tuple:
	"""
	Model 1 is the LIVE production gate. We do not reimplement it here;
	we read the captured motion_gate_result that was written by the live
	walk_motion_gate.evaluate call. Returns (accepted, margin) where
	margin = allowed_jump - actual_jump.
	"""
	mgr = step_1['motion_gate_result']
	accepted = mgr['accepted']
	margin = mgr['allowed_jump'] - mgr['actual_jump']
	return accepted, margin


#============================================
# Model 3: Pinned no-velocity baseline
#============================================
# Pinned formula: Accept the step-1 frame if and only if the PRODUCTION
# winner_blob exists AND its scene-converted centroid lies within
# radius_w * seed_w_px of (prev_cx_scene, prev_cy_scene). If
# corridor_blobs is empty OR winner_blob is null, reject.
#
# Alternative considered and rejected: "accept if ANY corridor blob is
# within radius". That alternative is what sweep_radius.py used before
# reconciliation. We reject it because the production walker only
# advances using its winner blob, so a model that grades an interval by
# "could any blob have been the runner" overstates real acceptance and
# is not the geometry the bootstrap gate would actually run.

def evaluate_model_3(
	step_1: dict,
	scene_transform,
	frame_index: int,
	radius_w: float = 1.0,
) -> tuple:
	"""
	Returns (accepted, margin) where margin = allowed_radius - distance.
	A negative margin means the winner blob is outside the radius.
	"""
	if not is_testable(step_1):
		return False, 0.0
	winner = step_1['winner_blob']
	if winner is None:
		return False, 0.0
	seed_w_px = step_1['seed_w_px']
	prev_x = step_1['prev_cx_scene']
	prev_y = step_1['prev_cy_scene']
	wx_scene, wy_scene = _winner_scene_xy(step_1, scene_transform, frame_index)
	dx = wx_scene - prev_x
	dy = wy_scene - prev_y
	distance = math.sqrt(dx * dx + dy * dy)
	allowed_radius = radius_w * seed_w_px
	accepted = distance <= allowed_radius
	margin = allowed_radius - distance
	return accepted, margin


#============================================
# Models 2, 4, 5: route through the live walk_motion_gate.evaluate
#============================================
# Single source of truth: walk_motion_gate.MAX_JUMP_W_PER_MS,
# walk_motion_gate.ABSOLUTE_MAX_JUMP_W, walk_motion_gate.VELOCITY_TOLERANCE,
# walk_motion_gate.DT_GATE_CAP, walk_motion_gate.MEASUREMENT_ALLOWANCE_W.
# These models compute a candidate velocity prior and call evaluate() so that
# replay never diverges from production.


def evaluate_model_2(
	step_1: dict,
	scene_transform,
	frame_index: int,
	dt_frames: int = 1,
) -> tuple:
	"""
	Vector-prediction three-cap-min on the residual vs predicted position.
	Predicts pred = prev + v * dt_for_gate, then runs the live gate against the
	residual: an equivalent reformulation moves prev to pred (a translation),
	leaving the gate caps unchanged. We pass a zero velocity to evaluate()
	because the residual already has velocity subtracted.
	"""
	if not is_testable(step_1) or step_1['winner_blob'] is None:
		return False, 0.0
	seed_w_px = step_1['seed_w_px']
	source_fps = step_1['source_fps']
	prev_x = step_1['prev_cx_scene']
	prev_y = step_1['prev_cy_scene']
	vx = step_1['vx_scene']
	vy = step_1['vy_scene']
	wx_scene, wy_scene = _winner_scene_xy(step_1, scene_transform, frame_index)
	dt_for_gate = min(dt_frames, walk_motion_gate.DT_GATE_CAP)
	pred_x = prev_x + vx * dt_for_gate
	pred_y = prev_y + vy * dt_for_gate
	v_mag = math.sqrt(vx * vx + vy * vy)
	result = walk_motion_gate.evaluate(
		prev_scene=(pred_x, pred_y),
		cand_scene=(wx_scene, wy_scene),
		v_recent_scene_mag=v_mag,
		dt_frames=dt_frames,
		torso_w=seed_w_px,
		torso_w_drift_frac=0.0,
		source_fps=source_fps,
	)
	margin = result.allowed_jump - result.actual_jump
	return result.accepted, margin


def evaluate_model_4(
	step_1: dict,
	scene_transform,
	frame_index: int,
	dt_frames: int = 1,
) -> tuple:
	"""Seed-chord short-lookahead prior, gated by the live walk_motion_gate."""
	if not is_testable(step_1) or step_1['winner_blob'] is None:
		return False, 0.0
	seed_w_px = step_1['seed_w_px']
	source_fps = step_1['source_fps']
	prev_x = step_1['prev_cx_scene']
	prev_y = step_1['prev_cy_scene']
	vx = step_1['vx_scene']
	vy = step_1['vy_scene']
	wx_scene, wy_scene = _winner_scene_xy(step_1, scene_transform, frame_index)
	lookahead_frames = 10
	scale = lookahead_frames / (lookahead_frames + 1)
	v_a_mag = math.sqrt((vx * scale) ** 2 + (vy * scale) ** 2)
	result = walk_motion_gate.evaluate(
		prev_scene=(prev_x, prev_y),
		cand_scene=(wx_scene, wy_scene),
		v_recent_scene_mag=v_a_mag,
		dt_frames=dt_frames,
		torso_w=seed_w_px,
		torso_w_drift_frac=0.0,
		source_fps=source_fps,
	)
	margin = result.allowed_jump - result.actual_jump
	return result.accepted, margin


def evaluate_model_5(
	step_1: dict,
	scene_transform,
	frame_index: int,
	dt_frames: int = 1,
) -> tuple:
	"""Constant runner-speed (0.3W/frame) prior gated by the live walk_motion_gate."""
	if not is_testable(step_1) or step_1['winner_blob'] is None:
		return False, 0.0
	seed_w_px = step_1['seed_w_px']
	source_fps = step_1['source_fps']
	prev_x = step_1['prev_cx_scene']
	prev_y = step_1['prev_cy_scene']
	wx_scene, wy_scene = _winner_scene_xy(step_1, scene_transform, frame_index)
	v_recent = 0.3 * seed_w_px
	result = walk_motion_gate.evaluate(
		prev_scene=(prev_x, prev_y),
		cand_scene=(wx_scene, wy_scene),
		v_recent_scene_mag=v_recent,
		dt_frames=dt_frames,
		torso_w=seed_w_px,
		torso_w_drift_frac=0.0,
		source_fps=source_fps,
	)
	margin = result.allowed_jump - result.actual_jump
	return result.accepted, margin


#============================================
# Model 3 (3-frame sequence): Bootstrap sequence acceptance test
#============================================
# New bootstrap design (2026-05-28). Three prior bootstrap models failed
# the 3-frame test on >=4 of 6 videos: single-frame radius, static
# no-velocity radius, and endpoint-seed chord-prior. Failure pattern was
# documented in dump_step1/BOOTSTRAP_REDESIGN.md: the chord-derived
# velocity is an average over hundreds of frames, not the local frame
# velocity, so chord-anchored predictions disagree with the instantaneous
# runner position.
#
# Replacement design:
#   - Step 1: GEOMETRY-ONLY. Predicted center = seed_scene. Radius =
#     MAX_RUNNER_SPEED_W_PER_S / source_fps + BOOTSTRAP_UNCERTAINTY_W
#     (W units, scaled by seed_w_px). The runner can move at most one
#     per-frame envelope plus localization slack from the seed.
#   - Step 2: walker-measured. Predicted center = step1_winner_scene +
#     (step1_winner_scene - seed_scene). Same radius.
#   - Step 3: rolling 2-step mean. Predicted center = step2_winner_scene +
#     0.5 * (step2_winner_scene - seed_scene). Same radius.
# Bootstrap N = 1 (single permissive frame). After step 1 the walker is
# already in tracking mode using walker-measured velocity.
#
# Implementation note: the dump (dump_step1_inputs.py) writes the predicted
# scene center into prev_cx_scene/prev_cy_scene for each step, so this
# evaluator only has to compare the winner blob to that anchor.

def evaluate_model_3_sequence(
	steps: dict,
	scene_transform,
	source_fps: float,
) -> tuple:
	"""
	Evaluate the 3-frame sequence Model 3 bootstrap test (new design).

	The dump's per-step `prev_cx_scene/prev_cy_scene` fields already encode
	the walker-measured prediction for that step (seed at step 1; rolling
	displacement at step 2+); this evaluator compares the winner blob's
	scene coordinates to that anchor within a fixed per-step radius.

	Args:
		steps: dict with keys 'step_0', 'step_1', 'step_2', 'step_3'.
			Required, no defaults. step_0 is the seed (provides seed
			scene + torso_w but is not acceptance-tested). step_1..step_3
			each carry corridor_blobs, winner_blob, prev_cx_scene/cy_scene
			(predicted center for that step in scene coords), seed_w_px,
			frame_index.
		scene_transform: SceneTransform for pixel_to_scene conversion.
		source_fps: source video fps. REQUIRED.

	Returns:
		(all_three_accepted, detail_dict). detail_dict has keys step_1,
		step_2, step_3 (bool), distances (list of float), radii (list of
		float). Empty corridor or null winner at any step is a sequence
		FAILURE, not untestable.
	"""
	# Required, no defaults; missing keys are a design bug.
	step_seq = [steps['step_1'], steps['step_2'], steps['step_3']]
	distances = []
	radii = []
	step_results = {}
	all_accepted = True
	for idx, step in enumerate(step_seq, start=1):
		corridor = step['corridor_blobs']
		winner = step['winner_blob']
		if not corridor or winner is None:
			step_results[f'step_{idx}'] = False
			distances.append(None)
			radii.append(None)
			all_accepted = False
			continue
		seed_w_px = step['seed_w_px']
		pred_x = step['prev_cx_scene']
		pred_y = step['prev_cy_scene']
		frame_index = step['frame_index']
		wx_px = winner['centroid_x']
		wy_px = winner['centroid_y']
		wx_scene, wy_scene = scene_transform.pixel_to_scene(frame_index, wx_px, wy_px)
		dx = wx_scene - pred_x
		dy = wy_scene - pred_y
		distance = math.sqrt(dx * dx + dy * dy)
		# Fixed per-step radius: per-frame envelope + localization slack.
		radius_w = walk_motion_gate.bootstrap_search_radius_w(source_fps)
		effective_radius = radius_w * seed_w_px
		accepted_i = distance <= effective_radius
		distances.append(distance)
		radii.append(effective_radius)
		step_results[f'step_{idx}'] = accepted_i
		if not accepted_i:
			all_accepted = False
	detail = {
		'step_1': step_results['step_1'],
		'step_2': step_results['step_2'],
		'step_3': step_results['step_3'],
		'distances': distances,
		'radii': radii,
	}
	return all_accepted, detail


def is_testable_sequence(steps: dict) -> bool:
	"""
	Sequence testability: step_1 must have non-empty corridor and a
	non-null winner. Steps 2 and 3 with empty corridor or null winner are
	failures of the sequence, not untestable.
	"""
	if not steps:
		return False
	step1 = steps['step_1']
	corridor = step1['corridor_blobs']
	winner = step1['winner_blob']
	testable = len(corridor) > 0 and winner is not None
	return testable


def evaluate_model_6(step_1: dict) -> tuple:
	"""
	Candidate-ranking-only baseline: accept the highest total_score blob
	among corridor_blobs. Returns (accepted, margin=0). The winner blob
	is the live production winner; we accept if it has the maximum
	total_score among all corridor blobs.
	"""
	if not is_testable(step_1):
		return False, 0.0
	winner = step_1['winner_blob']
	corridor = step_1['corridor_blobs']
	if winner is None or not corridor:
		return False, 0.0
	all_blobs = corridor + [winner]
	best = max(all_blobs, key=lambda b: b['total_score'])
	accepted = best is winner
	return accepted, 0.0
