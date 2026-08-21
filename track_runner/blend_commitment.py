"""Pure run-level FWD/BWD blend commitment policy.

This is the only owner of the decision that replaces the old per-frame
confidence winner.  It deliberately knows nothing about video readers or
residual construction: callers supply one offline heat evaluator built from a
single canonical residual/DoG field.  That boundary keeps C9 intact (the raw
passes remain independent) and makes missing image evidence fail loud rather
than quietly falling back to a distance-decay winner.
"""

# Standard Library
import math
import dataclasses

# local repo modules
import trajectory_confidence
import blob_walk.walk_motion_gate


# The shortest supported temporal ramp. A three-frame ramp removes the
# hard handoff without needlessly turning a short disagreement into a long
# mixed run.  ``commit_paths`` lengthens it deterministically when its C2 step
# measurement requires that.
TRANSITION_BAND_FRAMES = 3
MAX_TRANSITION_BAND_FRAMES = 32



#============================================
class BlendEvidenceRequiredError(ValueError):
	"""Raised when disagreement reaches the policy without image evidence."""


#============================================
class BlendTransitionInfeasibleError(ValueError):
	"""Raised when no bounded transition band satisfies the C2 step cap."""


#============================================
@dataclasses.dataclass(frozen=True)
class HeatEvidence:
	"""Comparable run evidence from one canonical residual/DoG field.

	``available=False`` is explicit unknown evidence.  Computed cold evidence is
	``available=True`` with zero energies and can still take the tie rule.
	"""
	available: bool
	forward_energy: float = 0.0
	backward_energy: float = 0.0


#============================================
def _baseline_merged_state(forward_state: dict, backward_state: dict) -> dict:
	"""Return the confidence-weighted average used as the transition baseline."""
	fwd_conf = float(forward_state["conf"])
	bwd_conf = float(backward_state["conf"])
	total_conf = fwd_conf + bwd_conf
	weight_fwd = 0.5 if total_conf <= 0.0 else fwd_conf / total_conf
	weight_bwd = 1.0 - weight_fwd
	state = {
		"cx": weight_fwd * float(forward_state["cx"]) + weight_bwd * float(backward_state["cx"]),
		"cy": weight_fwd * float(forward_state["cy"]) + weight_bwd * float(backward_state["cy"]),
		"w": weight_fwd * float(forward_state["w"]) + weight_bwd * float(backward_state["w"]),
		"h": weight_fwd * float(forward_state["h"]) + weight_bwd * float(backward_state["h"]),
		"source": "merged",
		"blend_flag": False,
	}
	# Some diagnostic/tool paths carry an absolute frame index while ordinary
	# solver paths remain slot-aligned. Preserve this nonpersistent linkage so
	# baseline-versus-committed overlays cannot drift by an interval offset.
	if "frame_index" in forward_state:
		state["frame_index"] = int(forward_state["frame_index"])
	return state


#============================================
def build_baseline_path(forward_path: list, backward_path: list) -> list:
	"""Build the pre-commit weighted-average geometry for diagnostic overlays.

	This is a diagnostic adapter only.  It has no winner branch and therefore
	cannot be used as a hidden fallback when canonical heat is unavailable.
	"""
	result = []
	for forward_state, backward_state in zip(forward_path, backward_path):
		state = _baseline_merged_state(forward_state, backward_state)
		state["conf"] = trajectory_confidence.frame_confidence(
			forward_state, backward_state,
		)
		result.append(state)
	return result


#============================================
def _disagreement_runs(forward_path: list, backward_path: list) -> list:
	"""Return half-open slots whose single-owner raw geometry disagrees."""
	runs = []
	run_start = None
	for index, (forward_state, backward_state) in enumerate(zip(forward_path, backward_path)):
		is_disagreement = trajectory_confidence.frame_disagrees(forward_state, backward_state)
		if is_disagreement and run_start is None:
			run_start = index
		if not is_disagreement and run_start is not None:
			runs.append((run_start, index))
			run_start = None
	if run_start is not None:
		runs.append((run_start, len(forward_path)))
	return runs


#============================================
def _transition_alpha(offset: int, run_length: int, band_frames: int) -> float:
	"""Return the monotone-in/out commitment weight for one run slot."""
	enter_alpha = min(1.0, float(offset + 1) / float(band_frames))
	exit_alpha = min(1.0, float(run_length - offset) / float(band_frames))
	alpha = min(enter_alpha, exit_alpha)
	return alpha


#============================================
def _interpolate_state(baseline: dict, winner: dict, alpha: float) -> dict:
	"""Linearly mix baseline and one committed pass with an explicit alpha."""
	state = {}
	for key in ("cx", "cy", "w", "h"):
		state[key] = (1.0 - alpha) * float(baseline[key]) + alpha * float(winner[key])
	state["source"] = "committed_fwd" if winner["_commit_direction"] == "fwd" else "committed_bwd"
	state["blend_flag"] = True
	state["commitment_alpha"] = alpha
	state["commitment_direction"] = winner["_commit_direction"]
	if "frame_index" in baseline:
		state["frame_index"] = baseline["frame_index"]
	return state


#============================================
def max_center_step_widths(path: list) -> float:
	"""Measure the maximum adjacent center step in mean torso-width units."""
	maximum = 0.0
	for prior, current in zip(path, path[1:]):
		step = _center_step_widths(prior, current)
		maximum = max(maximum, step)
	return maximum


#============================================
def _center_step_widths(prior: dict, current: dict) -> float:
	"""Return one adjacent center step in mean torso-width units."""
	mean_width = (float(prior["w"]) + float(current["w"])) / 2.0
	if mean_width <= 0.0:
		raise BlendTransitionInfeasibleError("Nonpositive torso width in blend path")
	step = math.hypot(
		float(current["cx"]) - float(prior["cx"]),
		float(current["cy"]) - float(prior["cy"]),
	) / mean_width
	return step


#============================================
def _max_committed_transition_step_widths(
	path: list,
	runs: list,
	run_winners: dict,
) -> float:
	"""Measure only edges touched by evidence-backed disagreement runs.

	The spatial cap governs the transition into, through, and out of a
	committed run.  It does not relabel unrelated baseline geometry elsewhere in
	the interval as a transition failure.  An unavailable run leaves baseline
	geometry intact, so it has no commitment transition to validate here.
	"""
	maximum = 0.0
	last_path_edge = len(path) - 2
	for run_start, run_end in runs:
		if run_winners[(run_start, run_end)] is None:
			continue
		first_edge = max(0, run_start - 1)
		last_edge = min(last_path_edge, run_end - 1)
		for prior_index in range(first_edge, last_edge + 1):
			step = _center_step_widths(path[prior_index], path[prior_index + 1])
			maximum = max(maximum, step)
	return maximum


#============================================
def _run_winner(
	forward_path: list,
	backward_path: list,
	run_start: int,
	run_end: int,
	start_frame: int,
	heat_evaluator: object,
) -> str | None:
	"""Choose the hotter raw pass for a complete run, or return unavailable.

	The evaluator must return a numeric value for cold (including zero) and
	``None`` only when the canonical field cannot be evaluated.  A partial field
	does not authorize a run decision, because it would make missing evidence
	indistinguishable from cold evidence.
	"""
	forward_heat = 0.0
	backward_heat = 0.0
	for index in range(run_start, run_end):
		frame_index = start_frame + index
		evidence = heat_evaluator(frame_index, forward_path[index], backward_path[index])
		if not isinstance(evidence, HeatEvidence):
			raise TypeError("heat evaluator must return blend_commitment.HeatEvidence")
		if not evidence.available:
			return None
		forward_heat += float(evidence.forward_energy)
		backward_heat += float(evidence.backward_energy)
	# Exact ties are cold-or-equal evidence, not unavailable.  The deterministic
	# FWD tie break avoids reintroducing an unrelated confidence/distance winner.
	winner = "fwd" if forward_heat >= backward_heat else "bwd"
	return winner


#============================================
def commit_paths(
	forward_path: list,
	backward_path: list,
	start_frame: int,
	heat_evaluator: object = None,
	max_step_widths: float = blob_walk.walk_motion_gate.ABSOLUTE_MAX_JUMP_W,
) -> list:
	"""Blend agreement slots and commit every evidence-backed disagreement run.

	Args:
		forward_path: Chronological raw FWD SOURCE-space states.
		backward_path: Chronological raw BWD SOURCE-space states.
		start_frame: Video frame for index zero.
		heat_evaluator: Required callable ``(frame_index, fwd, bwd) ->
			HeatEvidence``. It must score both boxes from the same canonical
			residual/DoG field. Numeric zero is cold; unavailable is explicit.
		max_step_widths: C2 spatial cap in torso widths per frame.

	Returns:
		Chronological blended states.  ``commitment_direction == 'unavailable'``
		marks an uncommitted disagreement run; cold runs remain committed.

	Raises:
		BlendEvidenceRequiredError: A disagreement exists without an evaluator.
		BlendTransitionInfeasibleError: No allowed ramp meets the C2 step cap.
	"""
	n = min(len(forward_path), len(backward_path))
	forward_path = forward_path[:n]
	backward_path = backward_path[:n]
	runs = _disagreement_runs(forward_path, backward_path)
	if runs and heat_evaluator is None:
		raise BlendEvidenceRequiredError(
			"blend commitment requires an explicit canonical heat evaluator for disagreement"
		)
	baseline = build_baseline_path(forward_path, backward_path)
	if not runs:
		return baseline
	# Heat is independent of the transition-band length.  Evaluate each raw
	# candidate pair once before the cheap geometry-only band sweep; otherwise a
	# A longer legal band would multiply residual decoding beyond the runtime
	# budget.
	run_winners = {}
	for run_start, run_end in runs:
		run_winners[(run_start, run_end)] = _run_winner(
			forward_path, backward_path, run_start, run_end, start_frame, heat_evaluator,
		)

	for band_frames in range(TRANSITION_BAND_FRAMES, MAX_TRANSITION_BAND_FRAMES + 1):
		candidate = [dict(state) for state in baseline]
		for run_start, run_end in runs:
			winner_direction = run_winners[(run_start, run_end)]
			for index in range(run_start, run_end):
				if winner_direction is None:
					state = candidate[index]
					state["blend_flag"] = True
					state["commitment_direction"] = "unavailable"
					state["commitment_alpha"] = 0.0
					continue
				winner = dict(forward_path[index] if winner_direction == "fwd" else backward_path[index])
				winner["_commit_direction"] = winner_direction
				alpha = _transition_alpha(index - run_start, run_end - run_start, band_frames)
				candidate[index] = _interpolate_state(baseline[index], winner, alpha)
		for state, fwd, bwd in zip(candidate, forward_path, backward_path):
			state["conf"] = trajectory_confidence.frame_confidence(fwd, bwd)
		maximum = _max_committed_transition_step_widths(candidate, runs, run_winners)
		if maximum <= max_step_widths:
			return candidate
	raise BlendTransitionInfeasibleError(
		f"No transition band through {MAX_TRANSITION_BAND_FRAMES} frames met "
		f"{max_step_widths:.3f} torso-widths/frame"
	)
