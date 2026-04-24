"""Helpers for pre-race frame range analysis and race-start semantics.

This module owns contract C2 implementation (averaged pre-race torso box,
scene-anchored center; see TRACK_RUNNER_CONTRACT.md). Implements two-stage
race-start boundary detection via Stage 1 (seed-pair displacement) and Stage 2
(fine velocity detector on interval trajectory).

Exports: detect_race_start, locate_race_start_interval, detect_race_start_in_interval,
compute_pre_race_reference, print_race_phase_summary.
"""

# Standard Library
import math

# local repo modules
import race_phases
import state_io

# Re-export unified schema version (per contract C8)
SCHEMA_VERSION = state_io.SCHEMA_VERSION
# Legacy alias for backward compatibility
PRE_RACE_REFERENCE_SCHEMA_VERSION = SCHEMA_VERSION

# Stage-1 interval detection uses a windowed directional-coherence test
# normalized by a provisional torso width. Pre-race seeds are independent
# human annotations of a stationary moment: individual pair vectors vary
# but net direction cancels. Race-start motion is the first window where
# vectors align coherently and cumulative displacement grows in one
# direction, measured in torso widths per contract C1.
#
# MIN_PRE_RACE_PAIR_DT_S: windows shorter than this in wall-clock are
# treated as dense-annotation jitter and skipped. Adjacent-frame seeds
# are a feature (they strengthen the pre-race torso reference), not a
# motion signal.
#
# PRE_RACE_MIN_WINDOW_SEEDS: minimum seeds per sliding window. With 3,
# the path is two vectors whose coherence distinguishes aligned motion
# from random jitter.
#
# PRE_RACE_NET_DISP_THRESHOLD_TORSO_UNITS: window net displacement
# (first seed to last seed) must exceed this many torso widths.
#
# PRE_RACE_COHERENCE_THRESHOLD: net_disp / path_len. A value near 1.0
# means all vectors align; jitter scores near 0. 0.7 rejects a single
# big annotation jump that returns to baseline.
MIN_PRE_RACE_PAIR_DT_S = 0.5
PRE_RACE_MIN_WINDOW_SEEDS = 3
PRE_RACE_NET_DISP_THRESHOLD_TORSO_UNITS = 0.75
PRE_RACE_COHERENCE_THRESHOLD = 0.7

# Frame selection offsets for race-start confirmation contact sheet.
# 11 tiles arranged in 5/1/5 layout around the detected race_start_frame.
#TODO: this should be a range function
CONFIRMATION_OFFSETS_S = (-0.5, -0.4, -0.3, -0.2, -0.1,
	0.0,
	0.1, 0.2, 0.3, 0.4, 0.5)


#============================================
def detect_race_start(trajectory: list, scene_transform, fps: float) -> dict:
	"""Thin wrapper over race_phases.detect_race_start.

	Detects the frame where the target runner transitions to sustained motion.
	Post-hoc analysis of a solved trajectory; does not modify it. Provided for
	tests and debug paths that need the unbounded detector (Stage 2 uses this
	internally on the bracket slice).

	Args:
		trajectory: List of per-frame state dicts (or None), indexed by frame.
		scene_transform: SceneTransform for pixel-to-scene conversion.
		fps: Video frame rate in frames per second.

	Returns:
		Dict with keys: race_start_frame (int or None), race_start_s (float
		or None), confidence (float in [0.0, 1.0]), method (str), threshold_used
		(float), debounce_frames (int).

	See race_phases.detect_race_start for full documentation.
	"""
	return race_phases.detect_race_start(trajectory, scene_transform, fps)


#============================================
def _seed_scene_center(scene_transform, seed: dict) -> tuple:
	"""Project a seed's pixel center into scene coordinates.

	Args:
		scene_transform: SceneTransform with pixel_box_to_scene.
		seed: Seed dict with frame_index, cx, cy, w, h.

	Returns:
		(scene_cx, scene_cy) pair.
	"""
	sx, sy, _sw, _sh = scene_transform.pixel_box_to_scene(
		seed["frame_index"], seed["cx"], seed["cy"], seed["w"], seed["h"],
	)
	return (sx, sy)


#============================================
def locate_race_start_interval(seeds: list, scene_transform, fps: float) -> tuple:
	"""Stage 1: Localize the interval containing race start via directional
	coherence over a sliding window, normalized by a provisional pre-race
	torso width (contract C1).

	Pre-race seeds are independent human annotations of the stationary runner,
	so per-pair vectors vary but their net direction cancels. Race-start
	motion is the first window where per-pair vectors align and cumulative
	displacement accumulates. The first coherent window is found; within it,
	the largest single pair is identified as the transition and returned as
	the interval.

	Args:
		seeds: Raw seed list (filtered via filter_usable_seeds_sorted).
		scene_transform: SceneTransform for pixel-to-scene conversion.
		fps: Video frame rate in frames per second.

	Returns:
		(low_frame_index, high_frame_index) interval tuple. low is the last
		pre-race seed's frame; high is the first moving seed's frame.

	Raises:
		RuntimeError: fewer than the minimum seeds for detection, no
			visible/partial seed to anchor torso scale, no coherent motion
			window found, or the transition falls at the first seed pair
			(no pre-race seed exists).
	"""
	# Lazy import avoids circular dependency with interval_fingerprint.
	import interval_fingerprint

	usable = interval_fingerprint.filter_usable_seeds_sorted(
		seeds, verbose=False,
	)

	if len(usable) < 2:
		raise RuntimeError(
			"fewer than 2 usable seeds; cannot solve as a track clip",
		)
	# Return None when there are too few seeds to evaluate pre-race coherence.
	# Downstream code treats None as "no pre-race phase" and skips Stage 2.
	if len(usable) < PRE_RACE_MIN_WINDOW_SEEDS:
		return None

	# Precompute scene-space centers once; the loop reuses them.
	scene_centers = [_seed_scene_center(scene_transform, s) for s in usable]

	window_size = PRE_RACE_MIN_WINDOW_SEEDS

	def _window_metrics(
		start_idx: int, torso_scale: float,
	) -> tuple:
		"""Return (net_disp_torso, coherence, pair_disps) for a window.

		pair_disps is a list of raw per-pair scene distances in the window.
		"""
		ax, ay = scene_centers[start_idx]
		bx, by = scene_centers[start_idx + window_size - 1]
		net_disp = math.hypot(bx - ax, by - ay)
		net_torso = net_disp / torso_scale
		disps = []
		path_len = 0.0
		for j in range(start_idx, start_idx + window_size - 1):
			ux, uy = scene_centers[j]
			vx, vy = scene_centers[j + 1]
			d = math.hypot(vx - ux, vy - uy)
			disps.append(d)
			path_len += d
		path_torso = path_len / torso_scale
		coherence_val = net_torso / max(path_torso, 1e-6)
		return (net_torso, coherence_val, disps)

	def _window_triggers(net_torso: float, coherence_val: float) -> bool:
		return (
			net_torso >= PRE_RACE_NET_DISP_THRESHOLD_TORSO_UNITS
			and coherence_val >= PRE_RACE_COHERENCE_THRESHOLD
		)

	# Slide a window of PRE_RACE_MIN_WINDOW_SEEDS seeds. Skip windows whose
	# total duration is below the debounce threshold (dense pre-race
	# annotation clusters, not motion). The first window that triggers AND
	# whose next window also triggers (or is out of range) is accepted as
	# race-start. The next-window check rejects one-off annotation jumps
	# that return to baseline.
	last_window_idx = len(usable) - window_size
	for i in range(last_window_idx + 1):
		window_dt = (
			usable[i + window_size - 1]["frame_index"]
			- usable[i]["frame_index"]
		) / fps
		if window_dt < MIN_PRE_RACE_PAIR_DT_S:
			continue

		# Provisional torso scale: mean torso width across visible/partial
		# seeds strictly before the window (post-boundary seeds must not
		# influence the scale). Fall back to the window's first seed width
		# only when no earlier qualifying seed exists so the i==0 path can
		# still evaluate and raise correctly.
		pre_boundary_widths = [
			float(s["w"]) for s in usable[:i]
			if s["status"] in ("visible", "partial")
		]
		if pre_boundary_widths:
			torso_scale = sum(pre_boundary_widths) / len(pre_boundary_widths)
		elif usable[i]["status"] in ("visible", "partial"):
			torso_scale = float(usable[i]["w"])
		else:
			raise RuntimeError(
				"cannot build provisional pre-race torso scale; no "
				"visible or partial seeds before the first candidate "
				"motion window. Add a visible pre-race seed.",
			)

		net_torso, coherence, pair_disps = _window_metrics(i, torso_scale)
		if not _window_triggers(net_torso, coherence):
			continue

		# Confirmation: the next window must also trigger. This rejects
		# a one-off annotation jump that returns to baseline and also
		# rejects the mirror window that sees the return. Requires at
		# least window_size+1 post-transition seeds; track-runner clips
		# normally have many more.
		if i >= last_window_idx:
			continue
		next_net, next_coh, _ = _window_metrics(i + 1, torso_scale)
		if not _window_triggers(next_net, next_coh):
			continue

		# Accepted. Transition pair inside the window is the pair with the
		# largest torso-normalized displacement.
		max_pair_offset = max(
			range(len(pair_disps)), key=lambda k: pair_disps[k],
		)
		transition_low_idx = i + max_pair_offset
		transition_high_idx = transition_low_idx + 1

		# If the transition is at the very first seed pair there is no
		# pre-race seed to anchor to; treat as "no pre-race phase."
		if transition_low_idx == 0:
			return None

		return (
			usable[transition_low_idx]["frame_index"],
			usable[transition_high_idx]["frame_index"],
		)

	# No confirmed coherent window; treat as "no pre-race phase."
	return None


#============================================
def detect_race_start_in_interval(
	interval_trajectory: list,
	scene_transform,
	fps: float,
	interval_start_frame: int
) -> int:
	"""Stage 2: Fine-grained race-start detection within an interval trajectory.

	Wraps race_phases.detect_race_start on the interval's trajectory.
	The interval trajectory is the solved interval from Stage 1 (the crossing
	interval that contains the actual race-start frame).

	Args:
		interval_trajectory: Per-frame state list for the interval.
		scene_transform: SceneTransform for pixel-to-scene conversion.
		fps: Video frame rate in frames per second.
		interval_start_frame: Start frame of the interval (for validation).

	Returns:
		int: The authoritative race_start_frame.

	Raises:
		RuntimeError: if detector returns None or a frame outside the interval.
	"""
	result = race_phases.detect_race_start(
		interval_trajectory, scene_transform, fps
	)

	race_start_frame = result["race_start_frame"]

	if race_start_frame is None:
		interval_end_frame = interval_start_frame + len(interval_trajectory) - 1
		raise RuntimeError(
			f"fine detector found no velocity onset in interval frames "
			f"{interval_start_frame}-{interval_end_frame}; add a seed closer to "
			f"the actual race start"
		)

	# Validate the frame is within interval range
	interval_end_frame = interval_start_frame + len(interval_trajectory) - 1
	if not (interval_start_frame <= race_start_frame <= interval_end_frame):
		raise RuntimeError(
			f"internal bug: detector returned race_start_frame={race_start_frame} "
			f"outside interval [{interval_start_frame}, {interval_end_frame}]"
		)

	return race_start_frame


#============================================
# race_start_interval is a (low_frame, high_frame) tuple of seed
# frame indices. It is NOT a solver interval result dict; it only
# names the seed-to-seed range that contains race_start_frame.
#============================================
def compute_pre_race_reference(
	seeds: list,
	race_start_frame: int,
	scene_transform,
	race_start_interval: tuple
) -> dict:
	"""Compute averaged pre-race reference from qualifying seeds.

	Contract C2: pre-race frames use averaged torso dimensions and
	scene-anchored center, computed from seeds with frame_index < race_start_frame.

	Args:
		seeds: Raw seed list.
		race_start_frame: The authoritative race-start boundary.
		scene_transform: SceneTransform for scene-coordinate operations.
		race_start_interval: Tuple (low_frame, high_frame) of seed frame indices
			from Stage 1 race-start interval detection. NOT a solver interval result dict.

	Returns:
		Dict with keys: race_start_frame, race_start_interval, torso_w, torso_h,
		scene_anchor_x, scene_anchor_y, source_frame_indices, source_count, method, warnings.

	Raises:
		RuntimeError: if no visible/partial pre-race seeds with torso_box.
	"""
	# Filter to visible/partial seeds with torso_box and frame_index < race_start_frame
	qualifying = [
		s for s in seeds
		if s["status"] in ("visible", "partial")
		and s.get("torso_box") is not None
		and s["frame_index"] < race_start_frame
	]

	if not qualifying:
		raise RuntimeError(
			"no visible or partial pre-race torso boxes are available"
		)

	# Average torso dimensions
	torso_w = sum(s["w"] for s in qualifying) / len(qualifying)
	torso_h = sum(s["h"] for s in qualifying) / len(qualifying)

	# Compute scene anchor as mean of scene-space centers
	scene_cx_values = []
	scene_cy_values = []
	for seed in qualifying:
		cx, cy, w, h = seed["cx"], seed["cy"], seed["w"], seed["h"]
		scene_cx, scene_cy, _sw, _sh = scene_transform.pixel_box_to_scene(
			seed["frame_index"], cx, cy, w, h
		)
		scene_cx_values.append(scene_cx)
		scene_cy_values.append(scene_cy)

	scene_anchor_x = sum(scene_cx_values) / len(scene_cx_values)
	scene_anchor_y = sum(scene_cy_values) / len(scene_cy_values)

	# Warnings
	warnings = []
	if len(qualifying) == 1:
		warnings.append("only_one_qualifying_seed")

	result = {
		"race_start_frame": race_start_frame,
		"race_start_interval": list(race_start_interval),
		"torso_w": torso_w,
		"torso_h": torso_h,
		"scene_anchor_x": scene_anchor_x,
		"scene_anchor_y": scene_anchor_y,
		"source_frame_indices": [s["frame_index"] for s in qualifying],
		"source_count": len(qualifying),
		"method": "seed_scene_displacement",
		"warnings": warnings,
	}
	return result


#============================================
def print_race_phase_summary(pre_race_reference: dict) -> None:
	"""Print a single-line summary of race-start detection.

	Reads race_start_frame, source_count, and warnings from the
	pre_race_reference dict (output of compute_pre_race_reference or loaded
	from diagnostics).

	Args:
		pre_race_reference: Dict from compute_pre_race_reference or None if
			no pre-race window exists.
	"""
	if pre_race_reference is None:
		print("  race start: not detected")
		return

	frame = pre_race_reference["race_start_frame"]
	source_count = pre_race_reference["source_count"]
	warnings = pre_race_reference["warnings"]

	warnings_tail = ""
	if warnings:
		warnings_tail = f"; warnings={warnings}"

	print(f"  race start: frame {frame} (source_count={source_count}{warnings_tail})")


#============================================
def choose_race_start_confirmation_frames(
	race_start_frame: int,
	fps: float,
	video_frame_count: int,
) -> list:
	"""Select frames for race-start confirmation contact sheet.

	Returns one dict per tile, in row-major order (top row left-to-right,
	center, bottom row left-to-right).

	Args:
		race_start_frame: The detected race-start frame index.
		fps: Video frame rate in frames per second.
		video_frame_count: Total frames in the video.

	Returns:
		List of 11 tile dicts in row-major order. Each tile dict contains:
		- frame_index: Clamped to [0, video_frame_count - 1]
		- requested_frame_index: Pre-clamp value (may be out of range)
		- offset_s: Fixed offset in seconds (-0.5 to +0.5)
		- label: "PRE", "START", or "POST"
		- row: "top", "center", or "bottom"
		- clamped: True if frame_index != requested_frame_index
	"""
	tiles = []

	for offset_s in CONFIRMATION_OFFSETS_S:
		requested_frame = round(race_start_frame + offset_s * fps)
		frame_index = max(0, min(requested_frame, video_frame_count - 1))
		clamped = (frame_index != requested_frame)

		# Determine label based on offset
		if offset_s < 0:
			label = "PRE"
		elif offset_s > 0:
			label = "POST"
		else:
			label = "START"

		# Determine row based on position in CONFIRMATION_OFFSETS_S
		idx_in_offsets = CONFIRMATION_OFFSETS_S.index(offset_s)
		if idx_in_offsets < 5:
			row = "top"
		elif idx_in_offsets == 5:
			row = "center"
		else:
			row = "bottom"

		tile = {
			"frame_index": frame_index,
			"requested_frame_index": requested_frame,
			"offset_s": offset_s,
			"label": label,
			"row": row,
			"clamped": clamped,
		}
		tiles.append(tile)

	return tiles
