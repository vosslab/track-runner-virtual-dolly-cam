"""
Motion gate for the production blob walker: per-frame jump acceptance gate.

The motion gate decides whether a candidate blob position is physically
plausible given the recent velocity estimate. This module is pure math: no
I/O, no observer calls, no image reading.

All numeric constants for runner motion physics live here. Every other
module (walk_viterbi.py, blend_commitment.py) imports the constants from
this module, which is the single definition site for them.

Physical envelope (W = torso width):
================================================================
Runner speed is bounded by physical reality, not by sweep tuning.

MAX_RUNNER_SPEED_W_PER_S = 30.0 (W/s):
    20 mph upper bound for a sprinting runner. 20 mph = 8.9408 m/s.
    Adult torso width is roughly 12 inches = 0.3048 m. Therefore
    8.9408 m/s / 0.3048 m/W = 29.33 W/s, rounded to 30.0.
    At 30 fps the per-frame allowance is 1.0 W/frame; at 60 fps it is
    0.5 W/frame; at 120 fps it is 0.25 W/frame.

MIN_RUNNER_SPEED_W_PER_S = 7.3 (W/s):
    5 mph lower bound for a jogging runner. 5 mph = 2.2352 m/s.
    2.2352 / 0.3048 = 7.33 W/s, rounded to 7.3.
    Used for velocity-prior plausibility clamping; runners moving more
    slowly than this are walking, not running, and the bootstrap should
    not amplify noise into a sub-jogging velocity prior.

BOOTSTRAP_UNCERTAINTY_W = 0.30 (W):
    Fixed slack for blob-localization imprecision, ROI quantization, and
    scene-transform jitter during bootstrap. Sub-torso, matching the
    centroid-imprecision allowance in MEASUREMENT_ALLOWANCE_W. This is a
    SEARCH SLACK term, separate from the physical velocity envelope.

Bootstrap search radius (per-step, identical at every bootstrap step):
    search_radius_w = (MAX_RUNNER_SPEED_W_PER_S / source_fps)
                      + BOOTSTRAP_UNCERTAINTY_W

The radius is constant across bootstrap steps because at each step the
predicted center is the WALKER-MEASURED extrapolation from the most
recent accept (step 1 uses the seed as prev; step 2 projects forward by
the displacement just observed at step 1; step 3+ uses a rolling 2-step
mean). The runner can move at most MAX_RUNNER_SPEED_W_PER_S/source_fps
torso widths in a single frame, plus localization slack. There is no
i-scaled velocity term because each step's prediction already absorbs
the prior step's displacement.

Tracking-mode (post-bootstrap) caps preserved below for the three-cap-min
gate. These are unchanged from the prior implementation pending
tracking-mode validation.
================================================================

Constants and formulas use torso WIDTH as the scale unit, not height.
Reference: Track Runner Contract clause C2 (torso box is the unit of scale).
"""

import dataclasses

# ============================================================
# Physical runner-speed envelope (W per second of source time).
# Used for: bootstrap velocity-prior clamping and bootstrap search-radius
# derivation. The per-frame allowance is derived AT THE USE SITE:
#     max_runner_jump_w_per_frame = MAX_RUNNER_SPEED_W_PER_S / source_fps
# Do not pin a per-frame value; per-frame derivation keeps the constant
# physical and the fps explicit.
# ============================================================

# Upper bound: 20 mph sprinter (8.94 m/s) at 12 inch (0.305 m) torso width.
# 8.94 / 0.305 = 29.33; rounded to 30.0.
MAX_RUNNER_SPEED_W_PER_S = 30.0

# Lower bound: 5 mph jogger (2.24 m/s) at 12 inch (0.305 m) torso width.
# 2.24 / 0.305 = 7.33; rounded to 7.3.
MIN_RUNNER_SPEED_W_PER_S = 7.3

# ============================================================
# Bootstrap search slack (W, fixed).
# Sub-torso allowance for blob-localization imprecision, ROI quantization,
# and scene-transform jitter. Independent of velocity. Same scale as the
# centroid-imprecision allowance MEASUREMENT_ALLOWANCE_W.
# ============================================================
BOOTSTRAP_UNCERTAINTY_W = 0.30

# ============================================================
# Tracking-mode three-cap-min constants (post-bootstrap, step > N).
# These are carried forward from the prior implementation and have not
# been validated against the baseline acceptance corpus.
# ============================================================

# Hard ceiling on allowed displacement (multiple of torso width).
# Prevents runaway even if per_step_cap accumulates across missed frames.
ABSOLUTE_MAX_JUMP_W = 1.5

# Velocity tolerance: allowed_jump is
# min(absolute_cap, per_step_cap, expected_jump * VELOCITY_TOLERANCE).
VELOCITY_TOLERANCE = 1.75

# dt_for_gate clamped to this many frames.
DT_GATE_CAP = 3

# Centroid-noise allowance (multiple of torso width).
# Allows perfectly stationary runner with v_recent_scene_mag == 0 and
# torso_w_drift_frac == 0 to accept small centroid jitter without failing.
MEASUREMENT_ALLOWANCE_W = 0.10


# ============================================================
# Bootstrap helpers (pure math; no I/O, no observer calls).
# ============================================================

def max_runner_jump_per_frame(source_fps: float) -> float:
	"""
	Derive the per-frame max runner displacement from the physical envelope.

	Args:
		source_fps: source video frame rate (frames per second).

	Returns:
		Maximum plausible runner displacement in torso widths per source frame.
		At 30 fps: 1.0 W/frame. At 60 fps: 0.5 W/frame. At 120 fps: 0.25 W/frame.
	"""
	# source_fps must be a positive real fps; missing fps is a design bug,
	# not an optional default.
	per_frame_w = MAX_RUNNER_SPEED_W_PER_S / source_fps
	return per_frame_w


def clamp_velocity_w_per_s(v_w_per_s: float) -> float:
	"""
	Clamp a velocity magnitude (W/s) to the physical envelope.

	Args:
		v_w_per_s: input velocity magnitude in torso widths per second.

	Returns:
		Velocity clamped to [MIN_RUNNER_SPEED_W_PER_S, MAX_RUNNER_SPEED_W_PER_S].
	"""
	if v_w_per_s < MIN_RUNNER_SPEED_W_PER_S:
		return MIN_RUNNER_SPEED_W_PER_S
	if v_w_per_s > MAX_RUNNER_SPEED_W_PER_S:
		return MAX_RUNNER_SPEED_W_PER_S
	return v_w_per_s


def bootstrap_search_radius_w(source_fps: float) -> float:
	"""
	Per-step bootstrap search radius in torso-width units.

	radius_w = (MAX_RUNNER_SPEED_W_PER_S / source_fps) + BOOTSTRAP_UNCERTAINTY_W

	The first term is the per-frame physical envelope (max plausible
	runner displacement in one source frame); the second term is fixed
	search slack for blob-localization imprecision. The radius is the
	SAME at every bootstrap step because each step's predicted center is
	derived from the most recent walker-measured displacement, not from
	the seed.

	Args:
		source_fps: source video frame rate (frames per second).

	Returns:
		Search radius in torso-width units.
	"""
	per_frame_w = max_runner_jump_per_frame(source_fps)
	radius_w = per_frame_w + BOOTSTRAP_UNCERTAINTY_W
	return radius_w


# ============================================================
# Tracking-mode three-cap-min gate (post-bootstrap).
# ============================================================

@dataclasses.dataclass
class MotionGateResult:
	"""
	Result of a motion gate evaluation.

	Attributes:
		accepted (bool): True if the candidate blob position is accepted.
		expected_jump (float): Expected displacement in pixels based on velocity.
		allowed_jump (float): Maximum allowed displacement in pixels.
		actual_jump (float): Observed Euclidean distance in pixels.
		v_recent_scene_mag (float): Recent velocity magnitude used (passed in).
		dt_for_gate (int): Clamped frame count used in the gate.
		reject_reason (str): Empty string on accept; one of "absolute_cap",
			"per_step_cap", "velocity_tolerance" on reject (the FIRST cap hit).
	"""
	accepted: bool
	expected_jump: float
	allowed_jump: float
	actual_jump: float
	v_recent_scene_mag: float
	dt_for_gate: int
	reject_reason: str


def evaluate(
	prev_scene: tuple,
	cand_scene: tuple,
	v_recent_scene_mag: float,
	dt_frames: int,
	torso_w: float,
	torso_w_drift_frac: float,
	source_fps: float,
) -> MotionGateResult:
	"""
	Evaluate whether a candidate blob position passes the tracking-mode gate.

	Args:
		prev_scene: (x, y) coordinates of the previous accepted position (scene coords).
		cand_scene: (x, y) coordinates of the candidate position (scene coords).
		v_recent_scene_mag: Recent velocity magnitude (scene units per frame).
		dt_frames: Frame count since the last accepted position.
		torso_w: Torso width in pixels (scale unit per C2).
		torso_w_drift_frac: Magnitude of recent fractional torso-width change
			(handles toward/away camera motion). Typically 0.0 to 0.5.
		source_fps: source video frame rate (frames per second). REQUIRED.

	Returns:
		MotionGateResult with acceptance decision and detailed metrics.
	"""

	# Clamp dt_for_gate to prevent runaway allowance on missed frames.
	dt_for_gate = min(dt_frames, DT_GATE_CAP)

	# Centroid-noise allowance: stationary runner still exhibits small blob jitter.
	measurement_allowance = MEASUREMENT_ALLOWANCE_W * torso_w

	# Radial-allowance contribution from toward/away camera motion.
	radial_allowance = torso_w_drift_frac * torso_w

	# Expected jump: velocity-driven motion plus measurement noise plus radial change.
	expected_jump = (v_recent_scene_mag * dt_for_gate) + measurement_allowance + radial_allowance

	# Per-frame cap: physical max runner speed per frame at source_fps.
	# At 30 fps this is 1.0 W/frame; at 60 fps 0.5 W/frame; at 120 fps 0.25 W/frame.
	max_jump_w_per_frame = max_runner_jump_per_frame(source_fps)
	per_step_cap = max_jump_w_per_frame * torso_w * dt_for_gate

	# Absolute cap: hard ceiling preventing runaway even at dt_for_gate == 3.
	absolute_cap = ABSOLUTE_MAX_JUMP_W * torso_w

	# Allowed jump: minimum of three constraints.
	allowed_jump = min(absolute_cap, per_step_cap, expected_jump * VELOCITY_TOLERANCE)

	# Actual displacement: Euclidean distance between prev and candidate.
	prev_x, prev_y = prev_scene
	cand_x, cand_y = cand_scene
	dx = cand_x - prev_x
	dy = cand_y - prev_y
	actual_jump = (dx * dx + dy * dy) ** 0.5

	# Determine acceptance and reject reason.
	reject_reason = ""
	if actual_jump <= allowed_jump:
		accepted = True
	else:
		accepted = False
		# Determine which cap was hit FIRST (in evaluation order).
		if actual_jump > absolute_cap:
			reject_reason = "absolute_cap"
		elif actual_jump > per_step_cap:
			reject_reason = "per_step_cap"
		else:
			reject_reason = "velocity_tolerance"

	return MotionGateResult(
		accepted=accepted,
		expected_jump=expected_jump,
		allowed_jump=allowed_jump,
		actual_jump=actual_jump,
		v_recent_scene_mag=v_recent_scene_mag,
		dt_for_gate=dt_for_gate,
		reject_reason=reject_reason,
	)
