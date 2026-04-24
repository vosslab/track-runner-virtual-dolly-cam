"""Per-interval analytical solving for track_runner.

Splits the video timeline into seed-to-seed intervals, solves each
interval using analytical velocity models in scene coordinates, and
stitches results into a full trajectory.
"""

# Standard Library
import math
import time

# PIP3 modules
import numpy
import scipy.interpolate
import rich.progress
import rich.text
import rich.measure

# local repo modules
import scoring
import velocity_model
import race_start
import solve_queue
import interval_fingerprint

# re-export fingerprint helpers so existing `interval_solver.foo` call
# sites keep working after the extraction. The canonical home is
# `interval_fingerprint.py`; new code should import from there directly.
SOLVER_FINGERPRINT_TAG = interval_fingerprint.SOLVER_FINGERPRINT_TAG
compute_interval_fingerprint = interval_fingerprint.compute_interval_fingerprint
filter_usable_seeds_sorted = interval_fingerprint.filter_usable_seeds_sorted


#============================================
def _coverage_from_track(track: list) -> dict:
	"""Compute blob-snap coverage for one pass from its blob_gate stamps.

	Returns a dict with `fraction` (float in [0, 1] or None),
	`candidate_count`, and `propagated_count`. `skipped` frames
	(endpoints, stationary, no reader) are excluded from both counts.
	"""
	accepted_count = 0
	candidate_count = 0
	propagated_count = 0
	for state in track:
		gate = state.get("blob_gate")
		if gate == "accepted":
			accepted_count += 1
			candidate_count += 1
			propagated_count += 1
		elif gate == "rejected":
			candidate_count += 1
			propagated_count += 1
		elif gate == "absent":
			propagated_count += 1
		# "skipped" excluded
	if candidate_count == 0:
		fraction = None
	else:
		fraction = accepted_count / candidate_count
	result = {
		"fraction": fraction,
		"candidate_count": int(candidate_count),
		"propagated_count": int(propagated_count),
	}
	return result


#============================================
def _stamp_blob_coverage(
	interval_score: dict,
	forward_path: list,
	backward_path: list,
) -> None:
	"""Write per-pass blob-snap coverage fields into an interval_score dict.

	FWD and BWD are reported separately to preserve diagnostic purity --
	asymmetric coverage (one pass good, the other bad) is itself a
	signal and must not be averaged away. Downstream consumers that
	want a single number should take the min or the geometric mean at
	the point of use.

	Fields written to `interval_score`:
	  - blob_coverage_fwd: float in [0, 1] or None.
	  - blob_coverage_bwd: float in [0, 1] or None.
	  - no_candidate_blobs: bool; True only when BOTH passes saw zero
	    candidates. When one pass saw candidates and the other did not,
	    the flag is False and the None-valued pass carries its None.
	  - candidate_frame_count_fwd / _bwd: ints.
	  - propagated_frame_count_fwd / _bwd: ints.
	"""
	fwd = _coverage_from_track(forward_path)
	bwd = _coverage_from_track(backward_path)
	interval_score["blob_coverage_fwd"] = fwd["fraction"]
	interval_score["blob_coverage_bwd"] = bwd["fraction"]
	interval_score["candidate_frame_count_fwd"] = fwd["candidate_count"]
	interval_score["candidate_frame_count_bwd"] = bwd["candidate_count"]
	interval_score["propagated_frame_count_fwd"] = fwd["propagated_count"]
	interval_score["propagated_frame_count_bwd"] = bwd["propagated_count"]
	interval_score["no_candidate_blobs"] = (
		fwd["fraction"] is None and bwd["fraction"] is None
	)


#============================================
class BlockBarColumn(rich.progress.ProgressColumn):
	"""Progress bar column using ASCII progress bar characters.

	Renders filled portion with # and remaining with -. Expands to fill
	available terminal width by querying terminal size.
	"""
	# # for completed, - for remaining (ASCII-only per PYTHON_STYLE)
	FILLED = "#"
	EMPTY = "-"
	# fixed overhead: task description + percentage + ETA/elapsed text + padding
	# "  solving intervals" ~20 + "  50%" ~5 + "ETA 3:42  elapsed 1:15" ~24 + spaces ~10
	_OTHER_COLUMNS_WIDTH = 60

	def render(self, task) -> rich.text.Text:
		"""Render the progress bar with block characters.

		Uses terminal width minus space for other columns to make the
		bar as wide as possible.

		Args:
			task: Rich progress task with completed and total fields.

		Returns:
			Styled Text object containing the block bar.
		"""
		# compute bar width from terminal size
		import shutil
		term_width = shutil.get_terminal_size((80, 24)).columns
		width = max(20, term_width - self._OTHER_COLUMNS_WIDTH)
		if task.total is None or task.total == 0:
			bar = self.EMPTY * width
			return rich.text.Text(bar)
		fraction = min(1.0, task.completed / task.total)
		filled = int(width * fraction)
		bar = self.FILLED * filled + self.EMPTY * (width - filled)
		style = "bright_green" if fraction >= 1.0 else "green"
		return rich.text.Text(bar, style=style)

	def __rich_measure__(self, console, options) -> rich.measure.Measurement:
		"""Claim all available width so the bar expands to fill the terminal."""
		return rich.measure.Measurement(4, options.max_width)


#============================================
class FrameETAColumn(rich.progress.ProgressColumn):
	"""ETA column based on a shared frame counter and total frame count.

	Works with both multiprocessing.Value (parallel) and list wrappers
	(sequential) for the frame counter.

	Args:
		frame_counter: Object with a .value attribute (multiprocessing.Value)
			or a list with one int element ([frames_done]).
		total_frames: Total number of frames to process.
	"""

	def __init__(self, frame_counter, total_frames: int):
		super().__init__()
		self.frame_counter = frame_counter
		self.total_frames = total_frames
		self.start_time = time.time()
		# cache the last rendered text and update timestamp
		self._last_text = "ETA --:--  elapsed --:--"
		self._last_update = 0.0

	def _get_done(self) -> int:
		"""Read the current frame count from the counter."""
		# multiprocessing.Value has a get_lock() method
		if hasattr(self.frame_counter, "get_lock"):
			with self.frame_counter.get_lock():
				return self.frame_counter.value
		# list wrapper: [frames_done]
		return self.frame_counter[0]

	@staticmethod
	def _format_duration(seconds: float) -> str:
		"""Format seconds into M:SS or H:MM:SS string.

		Args:
			seconds: Duration in seconds.

		Returns:
			Formatted time string.
		"""
		total_s = int(seconds)
		if total_s < 3600:
			m = total_s // 60
			s = total_s % 60
			return f"{m}:{s:02d}"
		h = total_s // 3600
		m = (total_s % 3600) // 60
		s = total_s % 60
		return f"{h}:{m:02d}:{s:02d}"

	def render(self, task) -> rich.text.Text:
		"""Render ETA and elapsed time based on frame throughput.

		Updates at most once every 2 seconds to reduce flicker.

		Args:
			task: Rich progress task (unused, ETA comes from frame counter).

		Returns:
			Text object with formatted ETA and elapsed time.
		"""
		now = time.time()
		elapsed = now - self.start_time
		# throttle updates to once per 2 seconds
		if now - self._last_update < 2.0 and self._last_update > 0.0:
			return rich.text.Text(self._last_text)
		self._last_update = now
		elapsed_str = self._format_duration(elapsed)
		if elapsed < 1.0:
			self._last_text = f"ETA --:--  elapsed {elapsed_str}"
			return rich.text.Text(self._last_text)
		done = self._get_done()
		if done < 1:
			self._last_text = f"ETA --:--  elapsed {elapsed_str}"
			return rich.text.Text(self._last_text)
		fps_rate = done / elapsed
		remaining = self.total_frames - done
		# ceiling on displayed seconds: avoids truncating 9:27.9 down
		# to 9:27; honest presentation choice (also used by camera_motion
		# and encoder bars that share this column).
		eta_s = math.ceil(max(0, remaining / fps_rate))
		eta_str = self._format_duration(eta_s)
		self._last_text = f"ETA {eta_str}  elapsed {elapsed_str}"
		return rich.text.Text(self._last_text)


#============================================
# Agreement tolerance: Dice coefficient threshold for FWD/BWD agreement.
# Any overlap is meaningful for this method, so a low threshold is used.
AGREE_DICE_THRESHOLD = 0.3


#============================================
def blend_paths(
	forward_path: list,
	backward_path: list,
) -> list:
	"""Blend forward and backward interval paths frame by frame.

	Where both interval paths agree (center within tolerance, scale within
	tolerance), produces a confidence-weighted average position. Where they
	disagree, picks the higher-confidence interval path and flags the frame.
	Never averages two mediocre conflicting paths into a false consensus.

	Args:
		forward_path: List of tracking state dicts from propagate_forward().
			Index 0 is the start_frame seed.
		backward_path: List of tracking state dicts from propagate_backward().
			Chronological from propagate_backward_analytical; index 0 is
			the start_frame seed, aligned frame-by-frame with forward_path
			by shared slot convention.

	Returns:
		List of blended interval path state dicts, one per frame. Source field is
		"merged" when both agreed, "propagated" when one was picked over the
		other. A "blend_flag" key is added when the interval paths disagreed.
	"""
	n = min(len(forward_path), len(backward_path))
	blended = []

	for i in range(n):
		fwd = forward_path[i]
		bwd = backward_path[i]

		fwd_cx = float(fwd["cx"])
		fwd_cy = float(fwd["cy"])
		fwd_h = float(fwd["h"])
		fwd_conf = float(fwd["conf"])

		bwd_cx = float(bwd["cx"])
		bwd_cy = float(bwd["cy"])
		bwd_h = float(bwd["h"])
		bwd_conf = float(bwd["conf"])

		# compute Dice coefficient between FWD and BWD boxes
		fwd_box = {"cx": fwd_cx, "cy": fwd_cy, "w": float(fwd["w"]), "h": fwd_h}
		bwd_box = {"cx": bwd_cx, "cy": bwd_cy, "w": float(bwd["w"]), "h": bwd_h}
		dice = scoring._compute_dice_coefficient(fwd_box, bwd_box)

		# any meaningful overlap counts as agreement
		agree = dice >= AGREE_DICE_THRESHOLD

		# propagate occlusion_risk from either track (True if either says so)
		fwd_occlusion = bool(fwd.get("occlusion_risk", False))
		bwd_occlusion = bool(bwd.get("occlusion_risk", False))
		frame_occlusion = fwd_occlusion or bwd_occlusion

		if agree:
			# confidence-weighted average: stronger track pulls position more
			total_conf = fwd_conf + bwd_conf
			if total_conf <= 0.0:
				w_fwd = 0.5
			else:
				w_fwd = fwd_conf / total_conf
			w_bwd = 1.0 - w_fwd

			merged_cx = w_fwd * fwd_cx + w_bwd * bwd_cx
			merged_cy = w_fwd * fwd_cy + w_bwd * bwd_cy
			merged_w = w_fwd * fwd["w"] + w_bwd * bwd["w"]
			merged_h = w_fwd * fwd_h + w_bwd * bwd_h
			# scale confidence by overlap quality
			merged_conf = dice * max(fwd_conf, bwd_conf)

			state = {
				"cx": merged_cx,
				"cy": merged_cy,
				"w": merged_w,
				"h": merged_h,
				"conf": merged_conf,
				"source": "merged",
				"blend_flag": False,
				"occlusion_risk": frame_occlusion,
			}
		else:
			# disagreement: pick the higher-confidence interval path
			if fwd_conf >= bwd_conf:
				winner = dict(fwd)
				winner["source"] = "propagated"
			else:
				winner = dict(bwd)
				winner["source"] = "propagated"
			winner["blend_flag"] = True
			winner["occlusion_risk"] = frame_occlusion
			state = winner

		blended.append(state)

	return blended


#============================================
def solve_interval_analytical(
	seed_start: dict,
	seed_end: dict,
	scene_transform: object,
	all_seeds_scene: list,
	fps: float,
	debug: bool = False,
	motion_track: object = None,
	all_seeds: list = None,
	reader: object = None,
) -> dict:
	"""Solve one interval using analytical velocity model (no optical flow).

	Fits directionally asymmetric Hermite curves to seed positions in scene
	coordinates, propagates forward and backward, blends interval paths, and scores
	the interval using velocity consistency and size consistency.

	Args:
		seed_start: Seed dict at interval start with cx, cy, w, h, frame_index.
		seed_end: Seed dict at interval end with same fields.
		scene_transform: SceneTransform instance for coordinate conversion.
		all_seeds_scene: List of all seeds as (frame, sx, sy, sw, sh) tuples
			in scene coordinates.
		fps: Video frame rate for duration thresholds.
		debug: If True, print diagnostic information.

	Returns:
		Dict with keys:
			- start_frame, end_frame: frame indices
			- blended_path: list of blended interval path state dicts (pixel coords)
			- forward_path: forward interval path (for diagnostics)
			- backward_path: backward interval path (for diagnostics)
			- interval_score: interval_score_v2 dict from score_interval_analytical
	"""
	start_frame = int(seed_start["frame_index"])
	end_frame = int(seed_end["frame_index"])

	# reject degenerate intervals
	if start_frame >= end_frame:
		raise RuntimeError(
			f"degenerate interval: start_frame={start_frame} >= "
			f"end_frame={end_frame}"
		)

	if debug:
		print(f"    fitting Hermite curves {start_frame}-{end_frame}...")

	# fit interval curves in scene coordinates
	interval_curves = velocity_model.fit_interval_curves(
		seed_start, seed_end, all_seeds_scene, scene_transform,
	)

	if debug:
		print("    propagating forward/backward analytically...")

	# per-interval residual cache, scoped to this call. Shared between
	# FWD and BWD so raw residuals are computed once per frame, but holds
	# image-derived data only (no accepted blobs, no gate decisions).
	# Cleared at the end of this function.
	residual_cache = {} if reader is not None else None

	# propagate forward (backward-looking slopes)
	forward_path_scene = velocity_model.propagate_forward_analytical(
		interval_curves, scene_transform,
		reader=reader, residual_cache=residual_cache,
	)

	# propagate backward (forward-looking slopes)
	backward_path_scene = velocity_model.propagate_backward_analytical(
		interval_curves, scene_transform,
		reader=reader, residual_cache=residual_cache,
	)

	# drop the cache; it must not escape the interval scope
	if residual_cache is not None:
		residual_cache.clear()

	# velocity model returns pixel coordinates directly (already converted)
	forward_path = list(forward_path_scene)
	backward_path = list(backward_path_scene)

	if debug:
		print(f"    blending interval paths ({len(forward_path)}+{len(backward_path)} "
			f"states)...")

	# blend forward and backward interval paths
	blended_path = blend_paths(forward_path, backward_path)

	if debug:
		print("    scoring interval analytically...")

	# score the interval using analytical metrics
	interval_score = scoring.score_interval_analytical(
		forward_path, backward_path, all_seeds_scene,
		interval_curves, scene_transform,
		motion_track=motion_track,
		all_seeds=all_seeds,
		blended_path=blended_path,
		fps=fps,
	)

	# stamp blob-snap coverage diagnostic (additive; legacy diagnostics
	# remain valid). coverage = accepted / frames_with_candidate_blob,
	# excluding seed frames (blob_gate == "skipped"). endpoints carry
	# "skipped" because propagators skip snap at index 0 and -1 of each
	# pass. the denominator is candidate frames only, so heavily occluded
	# intervals (mostly "absent") are not unfairly penalized.
	_stamp_blob_coverage(interval_score, forward_path, backward_path)

	result = {
		"start_frame": start_frame,
		"end_frame": end_frame,
		"blended_path": blended_path,
		"forward_path": forward_path,
		"backward_path": backward_path,
		"interval_score": interval_score,
	}

	# in debug mode, capture per-frame agreement records for investigation.
	# the aggregate agreement is already in interval_score; this adds the
	# per-frame iou/center_dist/size_ratio + p10/p50/p90 percentiles that
	# the main metric averages away. sidecar only, never touches the main
	# diagnostics schema.
	if debug:
		agreement_debug = scoring.compute_agreement_debug(
			forward_path, backward_path, start_frame=start_frame,
		)
		result["agreement_debug"] = agreement_debug
	return result


#============================================
def stitch_trajectories(
	interval_results: list,
) -> list:
	"""Concatenate interval trajectories into a full video trajectory.

	At interval boundaries (seed frames), uses the seed state from the
	start of the next interval (higher confidence). Gaps between intervals
	(if any) are left as None.

	Args:
		interval_results: List of interval result dicts from solve_interval_analytical(),
			sorted by start_frame.

	Returns:
		List of tracking state dicts indexed by frame number. Frames not
		covered by any interval are None.
	"""
	if not interval_results:
		return []

	# find total frame span
	last_end = max(r["end_frame"] for r in interval_results)
	trajectory = [None] * (last_end + 1)

	for result in interval_results:
		start = result["start_frame"]
		blended = result["blended_path"]
		for i, state in enumerate(blended):
			frame_index = start + i
			if 0 <= frame_index <= last_end:
				trajectory[frame_index] = state

	return trajectory



#============================================
def _stamp_seed_confidence(
	trajectory: list,
	seeds: list,
) -> list:
	"""Stamp seed confidence and status onto trajectory at seed frames.

	Ensures seed frames have the correct confidence regardless of what
	blend_paths() computed. Visible and partial seeds get conf=1.0
	(precise position known). Approx seeds get conf=0.3 (uncertain).

	Also propagates seed_status into the trajectory state for downstream
	consumers (crop, encoder) to know which frames have human-verified
	positions.

	Args:
		trajectory: List of tracking state dicts indexed by frame number.
		seeds: List of all seed dicts.

	Returns:
		The modified trajectory list (same object, modified in place).
	"""
	n = len(trajectory)
	stamped = 0
	for seed in seeds:
		frame_index = int(seed["frame_index"])
		if frame_index < 0 or frame_index >= n:
			continue
		if trajectory[frame_index] is None:
			continue
		status = seed.get("status", "")
		# visible and partial seeds have precise position
		if status in ("visible", "partial"):
			trajectory[frame_index]["conf"] = 1.0
			trajectory[frame_index]["seed_status"] = status
			stamped += 1
		# approx seeds have uncertain but useful position
		elif status in ("approximate", "obstructed"):
			trajectory[frame_index]["conf"] = 0.3
			trajectory[frame_index]["seed_status"] = status
			stamped += 1
	if stamped > 0:
		print(f"  stamped confidence on {stamped} seed frames")
	return trajectory


# erase radius in seconds for trajectory erasure
APPROX_ERASE_RADIUS_S = 0.5     # seconds to erase around approx seeds
NOT_IN_FRAME_ERASE_RADIUS_S = 1.0  # seconds to erase around not_in_frame seeds


#============================================
def _apply_trajectory_erasure(
	trajectory: list,
	seeds: list,
	fps: float,
) -> list:
	"""Erase trajectory near seeds that lack accurate position data.

	Callers pass ALL seeds; this function decides what to erase based
	on the drawing modes:

	- visible: precise torso box, fully visible runner. NO erasure.
	- partial: precise torso box, partially hidden but position known.
	  NO erasure.
	- approximate: larger approx area where runner is believed to be.
	  NO longer erased (provides useful trajectory guidance).
	- not_in_frame: runner completely outside the frame. Erase within
	  NOT_IN_FRAME_ERASE_RADIUS_S. No position data at all.

	Legacy seeds with status "obstructed" are treated the same as
	"approximate" (not erased in analytical mode).

	Args:
		trajectory: List of tracking state dicts (or None) indexed by frame.
		seeds: List of all seed dicts (any status).
		fps: Video frame rate for converting seconds to frames.

	Returns:
		The modified trajectory list (same object, modified in place).
	"""
	n = len(trajectory)
	# build set of visible/partial seed frames that must not be erased;
	# anchor_to_seeds() already pinned these to correct positions
	protected_frames = set()
	for seed in seeds:
		if seed.get("status") in ("visible", "partial"):
			protected_frames.add(int(seed["frame_index"]))
	erase_count = 0
	for seed in seeds:
		status = seed.get("status", "")
		# visible: precise torso box, fully visible -- keep
		if status == "visible":
			continue
		# partial: precise torso box, position known -- keep
		if status == "partial":
			continue
		# approximate (or legacy "obstructed"): NO LONGER ERASED
		# In analytical mode, approximate seeds provide useful guidance
		if status in ("approximate", "obstructed"):
			continue
		# not_in_frame: runner off-screen -- erase
		if status == "not_in_frame":
			radius_frames = int(round(NOT_IN_FRAME_ERASE_RADIUS_S * fps))
		else:
			# unknown status, skip safely
			continue
		erase_count += 1
		seed_frame = int(seed["frame_index"])
		# erase frames within the radius
		erase_start = max(0, seed_frame - radius_frames)
		erase_end = min(n - 1, seed_frame + radius_frames)
		for fi in range(erase_start, erase_end + 1):
			# skip visible/partial seed frames -- they have precise positions
			if fi in protected_frames:
				continue
			# not_in_frame: runner truly off-screen, erase to None
			trajectory[fi] = None
	if erase_count > 0:
		print(f"  erasing trajectory near {erase_count} seeds")
	return trajectory


# anchor interpolation constants
ANCHOR_PROXIMITY_SKIP = 7        # frames near seeds to skip (~0.23s at 30fps)
ANCHOR_BLEND_SCALE_XY = 0.5     # max blend for cx/cy at zero confidence
ANCHOR_BLEND_SCALE_WH = 0.3     # max blend for w/h (weaker, avoid zoom pumping)
ANCHOR_MAX_DISP_XY = 0.25       # fraction of box dimension for cx/cy cap
ANCHOR_MAX_DISP_WH = 0.15       # fraction of dimension for w/h cap
ANCHOR_WINDOW_SEEDS = 4         # max seeds to include on each side of target


#============================================
def _collect_anchor_knots(
	seeds: list,
) -> list:
	"""Collect trusted knots from seeds for anchor interpolation.

	Filters to visible/partial seeds with valid torso_box, extracts
	(frame_index, cx, cy, w, h, status) tuples, deduplicates by
	frame_index preferring visible over partial and larger area.

	Args:
		seeds: List of all seed dicts.

	Returns:
		List of knot tuples sorted by frame_index, deduplicated.
	"""
	raw_knots = []
	for seed in seeds:
		status = seed.get("status", "")
		if status not in ("visible", "partial"):
			continue
		# use the seed's center coordinates (cx, cy) directly;
		# torso_box stores [x, y, w, h] (top-left), not center
		seed_cx = seed.get("cx")
		seed_cy = seed.get("cy")
		seed_w = seed.get("w")
		seed_h = seed.get("h")
		if seed_cx is None or seed_cy is None:
			# fall back to torso_box and convert to center
			torso_box = seed.get("torso_box")
			if torso_box is None:
				continue
			seed_cx = float(torso_box[0]) + float(torso_box[2]) / 2.0
			seed_cy = float(torso_box[1]) + float(torso_box[3]) / 2.0
			seed_w = float(torso_box[2])
			seed_h = float(torso_box[3])
		cx = float(seed_cx)
		cy = float(seed_cy)
		w = float(seed_w)
		h = float(seed_h)
		# skip invalid dimensions
		if w <= 0 or h <= 0:
			continue
		fi = int(seed["frame_index"])
		raw_knots.append((fi, cx, cy, w, h, status))

	# sort by frame_index
	raw_knots.sort(key=lambda k: k[0])

	# deduplicate by frame_index:
	# prefer visible over partial; among same status, prefer larger area
	deduped = {}
	for knot in raw_knots:
		fi = knot[0]
		status = knot[5]
		area = knot[3] * knot[4]
		if fi not in deduped:
			deduped[fi] = knot
		else:
			existing = deduped[fi]
			existing_status = existing[5]
			existing_area = existing[3] * existing[4]
			# prefer visible over partial
			if status == "visible" and existing_status != "visible":
				deduped[fi] = knot
			elif status == existing_status and area > existing_area:
				deduped[fi] = knot

	# return sorted list
	result = sorted(deduped.values(), key=lambda k: k[0])
	return result


#============================================
def _build_local_fit(
	knots: list,
	center_frame: int,
	window_seeds: int,
) -> tuple:
	"""Build local interpolators from a subset of knots near center_frame.

	Selects up to window_seeds knots on each side of center_frame.
	Builds CubicSpline for cx/cy and PchipInterpolator for log(w)/log(h).
	Falls back to numpy.interp (linear) when only 2 knots are available.

	Args:
		knots: Full sorted list of knot tuples (fi, cx, cy, w, h, status).
		center_frame: Frame index to center the window around.
		window_seeds: Max seeds to include on each side.

	Returns:
		Tuple of (interpolators_dict, knot_frame_tuple) or None if < 2 knots.
		interpolators_dict has keys: cx_interp, cy_interp, logw_interp, logh_interp.
		Each value is either a callable or a tuple (frames, values) for linear fallback.
	"""
	# find knots before and after center_frame
	before = []
	after = []
	for knot in knots:
		if knot[0] < center_frame:
			before.append(knot)
		elif knot[0] > center_frame:
			after.append(knot)
		else:
			# knot at center_frame goes to both sides conceptually
			before.append(knot)
			after.append(knot)

	# take nearest window_seeds from each side
	selected_before = before[-window_seeds:]
	selected_after = after[:window_seeds]

	# combine and deduplicate by frame index
	combined = {}
	for knot in selected_before + selected_after:
		combined[knot[0]] = knot
	local_knots = sorted(combined.values(), key=lambda k: k[0])

	if len(local_knots) < 2:
		return None

	frames = numpy.array([k[0] for k in local_knots], dtype=float)
	cx_vals = numpy.array([k[1] for k in local_knots], dtype=float)
	cy_vals = numpy.array([k[2] for k in local_knots], dtype=float)
	w_vals = numpy.array([k[3] for k in local_knots], dtype=float)
	h_vals = numpy.array([k[4] for k in local_knots], dtype=float)

	# log-space for w and h
	logw_vals = numpy.array([math.log(v) for v in w_vals], dtype=float)
	logh_vals = numpy.array([math.log(v) for v in h_vals], dtype=float)

	interps = {}
	knot_frames = tuple(int(k[0]) for k in local_knots)

	if len(local_knots) == 2:
		# linear fallback: store arrays for numpy.interp
		interps["cx_interp"] = (frames, cx_vals)
		interps["cy_interp"] = (frames, cy_vals)
		interps["logw_interp"] = (frames, logw_vals)
		interps["logh_interp"] = (frames, logh_vals)
	else:
		# CubicSpline for cx, cy
		interps["cx_interp"] = scipy.interpolate.CubicSpline(
			frames, cx_vals, bc_type="natural",
		)
		interps["cy_interp"] = scipy.interpolate.CubicSpline(
			frames, cy_vals, bc_type="natural",
		)
		# PCHIP for log(w), log(h) to avoid overshoot
		interps["logw_interp"] = scipy.interpolate.PchipInterpolator(
			frames, logw_vals,
		)
		interps["logh_interp"] = scipy.interpolate.PchipInterpolator(
			frames, logh_vals,
		)

	return (interps, knot_frames)


#============================================
def _eval_fit(interps: dict, frame: float) -> tuple:
	"""Evaluate interpolators at a given frame index.

	Handles both callable (CubicSpline/PCHIP) and tuple (linear) forms.

	Args:
		interps: Dict with cx_interp, cy_interp, logw_interp, logh_interp.
		frame: Frame index to evaluate at.

	Returns:
		Tuple of (ref_cx, ref_cy, ref_w, ref_h).
	"""
	# evaluate cx
	cx_obj = interps["cx_interp"]
	if callable(cx_obj):
		ref_cx = float(cx_obj(frame))
	else:
		ref_cx = float(numpy.interp(frame, cx_obj[0], cx_obj[1]))

	# evaluate cy
	cy_obj = interps["cy_interp"]
	if callable(cy_obj):
		ref_cy = float(cy_obj(frame))
	else:
		ref_cy = float(numpy.interp(frame, cy_obj[0], cy_obj[1]))

	# evaluate w in log-space, exponentiate
	logw_obj = interps["logw_interp"]
	if callable(logw_obj):
		ref_w = math.exp(float(logw_obj(frame)))
	else:
		ref_w = math.exp(float(numpy.interp(frame, logw_obj[0], logw_obj[1])))

	# evaluate h in log-space, exponentiate
	logh_obj = interps["logh_interp"]
	if callable(logh_obj):
		ref_h = math.exp(float(logh_obj(frame)))
	else:
		ref_h = math.exp(float(numpy.interp(frame, logh_obj[0], logh_obj[1])))

	return (ref_cx, ref_cy, ref_w, ref_h)


#============================================
def _segment_by_knot_window(
	frame_range: range,
	knots: list,
	window_seeds: int,
) -> list:
	"""Group consecutive frames into segments sharing the same knot window.

	For each frame, determines which knots fall in the local window.
	Consecutive frames with identical knot sets are grouped into segments.

	Args:
		frame_range: Range of frame indices to segment.
		knots: Full sorted list of knot tuples.
		window_seeds: Max seeds on each side of each frame.

	Returns:
		List of (start_frame, end_frame, knot_subset) tuples.
		end_frame is inclusive.
	"""
	if not knots:
		return []

	segments = []
	current_key = None
	current_start = None
	current_knots = None

	for fi in frame_range:
		# find nearest window_seeds knots on each side
		before = []
		after = []
		for knot in knots:
			if knot[0] < fi:
				before.append(knot)
			elif knot[0] > fi:
				after.append(knot)
			else:
				before.append(knot)
				after.append(knot)

		selected_before = before[-window_seeds:]
		selected_after = after[:window_seeds]

		# combine and deduplicate
		combined = {}
		for knot in selected_before + selected_after:
			combined[knot[0]] = knot
		local_knots = sorted(combined.values(), key=lambda k: k[0])
		# key is the tuple of frame indices in this window
		key = tuple(k[0] for k in local_knots)

		if key != current_key:
			# start a new segment
			if current_key is not None:
				segments.append((current_start, fi - 1, current_knots))
			current_key = key
			current_start = fi
			current_knots = local_knots
		# else: extend current segment

	# close the last segment
	if current_key is not None:
		last_frame = frame_range[-1] if frame_range else current_start
		segments.append((current_start, last_frame, current_knots))

	return segments


#============================================
def anchor_to_seeds(
	trajectory: list,
	seeds: list,
) -> list:
	"""Apply multi-seed anchored interpolation to a stitched trajectory.

	Corrects drift in blended interval path trajectories by fitting local splines through
	seed positions and blending corrections toward the reference path.
	Visible seeds are hard-pinned; partial seeds guide the fit but are
	not forced to exact values.

	This is a weak kinematic prior: runners move smoothly over short
	windows. Confidence-modulated blending improves tracking stability
	without hiding real motion.

	Args:
		trajectory: List of tracking state dicts indexed by frame number.
		seeds: List of all seed dicts.

	Returns:
		The corrected trajectory list (same length).
	"""
	# guard: check if already applied
	first_state = None
	for state in trajectory:
		if state is not None:
			first_state = state
			break
	if first_state is None:
		return trajectory
	if first_state.get("_anchor_applied"):
		return trajectory

	# collect trusted knots from seeds
	knots = _collect_anchor_knots(seeds)
	if len(knots) < 2:
		return trajectory

	# correction range: first knot frame to last knot frame
	first_knot_frame = knots[0][0]
	last_knot_frame = knots[-1][0]

	# build set of all knot frame indices for proximity check
	knot_frame_set = set(k[0] for k in knots)

	# build dicts of seed knots by status for pinning and source restoration
	visible_knots = {}
	partial_knots = {}
	for knot in knots:
		if knot[5] == "visible":
			visible_knots[knot[0]] = knot
		elif knot[5] == "partial":
			partial_knots[knot[0]] = knot

	# segment the correction range by knot window
	correction_range = range(first_knot_frame, last_knot_frame + 1)
	segments = _segment_by_knot_window(
		correction_range, knots, ANCHOR_WINDOW_SEEDS,
	)

	n = len(trajectory)
	corrected_count = 0

	for seg_start, seg_end, seg_knots in segments:
		# build one fit for this segment
		# use the midpoint of the segment as center_frame for the fit
		seg_mid = (seg_start + seg_end) // 2
		fit_result = _build_local_fit(knots, seg_mid, ANCHOR_WINDOW_SEEDS)
		if fit_result is None:
			continue
		interps, _ = fit_result

		for fi in range(seg_start, seg_end + 1):
			if fi < 0 or fi >= n:
				continue
			state = trajectory[fi]
			if state is None:
				continue

			# proximity skip: do not correct frames near any knot
			near_seed = False
			for kf in knot_frame_set:
				if abs(fi - kf) <= ANCHOR_PROXIMITY_SKIP:
					near_seed = True
					break
			if near_seed:
				continue

			# current tracker values
			cur_cx = float(state["cx"])
			cur_cy = float(state["cy"])
			cur_w = float(state["w"])
			cur_h = float(state["h"])
			conf = float(state.get("conf", 0.5))

			# evaluate reference from fit
			ref_cx, ref_cy, ref_w, ref_h = _eval_fit(interps, float(fi))

			# compute blend factors (stronger correction at low confidence)
			blend_xy = ANCHOR_BLEND_SCALE_XY * (1.0 - conf) ** 2
			blend_wh = ANCHOR_BLEND_SCALE_WH * (1.0 - conf) ** 2

			# compute raw displacements
			dx = ref_cx - cur_cx
			dy = ref_cy - cur_cy
			dw = ref_w - cur_w
			dh = ref_h - cur_h

			# clamp displacements by axis-appropriate caps
			max_dx = ANCHOR_MAX_DISP_XY * cur_w
			max_dy = ANCHOR_MAX_DISP_XY * cur_h
			max_dw = ANCHOR_MAX_DISP_WH * cur_w
			max_dh = ANCHOR_MAX_DISP_WH * cur_h

			dx = max(-max_dx, min(max_dx, dx))
			dy = max(-max_dy, min(max_dy, dy))
			dw = max(-max_dw, min(max_dw, dw))
			dh = max(-max_dh, min(max_dh, dh))

			# apply blended corrections
			new_cx = cur_cx + blend_xy * dx
			new_cy = cur_cy + blend_xy * dy
			new_w = cur_w + blend_wh * dw
			new_h = cur_h + blend_wh * dh

			# ensure positive dimensions
			if new_w > 0 and new_h > 0:
				state["cx"] = new_cx
				state["cy"] = new_cy
				state["w"] = new_w
				state["h"] = new_h
				corrected_count += 1

	# hard-pin visible seed frames to exact seed positions
	# also restore source/seed_status so the color system routes correctly
	pinned_count = 0
	for fi, knot in visible_knots.items():
		if fi < 0 or fi >= n:
			continue
		state = trajectory[fi]
		if state is None:
			continue
		state["cx"] = knot[1]
		state["cy"] = knot[2]
		state["w"] = knot[3]
		state["h"] = knot[4]
		# restore seed identity so overlay color uses seed_status palette
		state["source"] = "seed"
		state["seed_status"] = knot[5]
		pinned_count += 1

	# restore source/seed_status on partial seed frames (not hard-pinned,
	# but need correct color in debug overlay)
	for fi, knot in partial_knots.items():
		if fi < 0 or fi >= n:
			continue
		state = trajectory[fi]
		if state is None:
			continue
		state["source"] = "seed"
		state["seed_status"] = knot[5]

	if corrected_count > 0 or pinned_count > 0:
		print(
			f"  anchor_to_seeds: corrected {corrected_count} frames, "
			f"pinned {pinned_count} visible seeds"
		)

	# stamp guard flag on first non-None state
	for state in trajectory:
		if state is not None:
			state["_anchor_applied"] = True
			break

	return trajectory


#============================================
def solve_all_intervals(
	reader: object,
	seeds: list,
	detector: object,
	config: dict,
	num_workers: int = 1,
	debug: bool = False,
	on_interval_complete: object = None,
	prior_intervals: dict = None,
	on_interval_solved: object = None,
	run_control: object = None,
	key_reader: object = None,
	scene_transform: object = None,
	motion_track: object = None,
	video_path: str = None,
) -> dict:
	"""Solve all seed-to-seed intervals and stitch into a full trajectory.

	Splits the seed list into consecutive pairs, solves each interval using
	analytical velocity models in scene coordinates, stitches results, and
	returns a diagnostics-format dict with per-interval scoring and the
	full trajectory.

	Args:
		reader: VideoReader with read_frame() and get_info() methods.
		seeds: List of seed dicts sorted by frame_index. Each seed must have
			cx, cy, w, h, frame_index keys. Non-visible seeds are skipped.
		detector: Person detector with a detect(frame) method (currently unused).
		config: Project configuration dict (currently unused; reserved).
		num_workers: Number of parallel solver workers. 1 runs in-process.
			>= 2 opens a ProcessPoolExecutor and dispatches cache-miss
			intervals across workers; completions are aggregated into
			seed order by the main process.
		video_path: Path to the input video. Required when num_workers >= 2
			so each worker can open its own VideoReader.
		debug: If True, show per-frame debug output and progress bars.
		on_interval_complete: Optional callback called with each interval result
			dict as intervals finish. Used for interactive seed requesting.
		prior_intervals: Optional dict of fingerprint->result for reusing
			previously solved intervals. Keys are from state_io.interval_fingerprint().
		on_interval_solved: Optional callback(fingerprint, result) called when
			a new interval is solved, for persisting to the solved-intervals file.
		scene_transform: SceneTransform instance for coordinate conversion.
		motion_track: Optional motion track for scoring.
		run_control: Optional run control object for quit handling.
		key_reader: Optional key reader for interactive quit.

	Returns:
		Dict with keys:
			- "intervals": list of interval result dicts
			- "trajectory": full frame-by-frame tracking state list
	"""
	info = reader.get_info()
	fps = float(info.get("fps", 30.0))

	# convert all seeds to scene coordinates up front so both the in-process
	# solve path and the pool-worker initializer see identical precomputed
	# scene boxes. not_in_frame seeds carry no cx/cy geometry (they mark
	# frames where the runner is off-screen) and never participate in
	# interval solving; filter them out so the loop does not KeyError on
	# a valid but non-geometric seed. Matches the established status
	# filter in interval_fingerprint.compute_run_fingerprint_digest().
	all_seeds_scene = []
	for seed in seeds:
		if seed.get("status") == "not_in_frame":
			continue
		frame_index = int(seed["frame_index"])
		cx = float(seed["cx"])
		cy = float(seed["cy"])
		w = float(seed["w"])
		h = float(seed["h"])
		sx, sy, sw, sh = scene_transform.pixel_box_to_scene(
			frame_index, cx, cy, w, h,
		)
		all_seeds_scene.append((frame_index, sx, sy, sw, sh))

	# Stage 1: Locate race-start bracket via seed-pair displacement.
	# Raises RuntimeError if fewer than 2 usable seeds or degenerate cases.
	race_start_bracket = race_start.locate_race_start_bracket(
		seeds, scene_transform, fps
	)
	bracket_low, bracket_high = race_start_bracket

	# plan_interval_work is the single source of truth for seed filter +
	# fingerprint computation + cache partition. refine mode also calls
	# it, so solve and refine agree on every cache key byte-for-byte.
	# Pass race_start_bracket for classification.
	plan = solve_queue.plan_interval_work(
		seeds, prior_intervals, race_start_bracket=race_start_bracket
	)
	if plan.total_intervals == 0:
		print("  interval_solver: need at least 2 usable seeds to solve intervals")
		return {"intervals": [], "trajectory": []}

	# execute_interval_work owns the in-process-vs-pool decision, the
	# progress bar, the per-completion callback fire-order, and the
	# quit/drain path. It returns a seed-ordered list with quit holes
	# already dropped on the interrupted path.
	context = solve_queue.ExecutionContext(
		reader=reader,
		scene_transform=scene_transform,
		motion_track=motion_track,
		all_seeds=seeds,
		all_seeds_scene=all_seeds_scene,
		fps=fps,
		num_workers=num_workers,
		video_path=video_path,
		debug=debug,
		race_start_bracket=race_start_bracket,
		pre_race_reference=None,
	)
	interval_results = solve_queue.execute_interval_work(
		plan, context,
		on_interval_complete=on_interval_complete,
		on_interval_solved=on_interval_solved,
		run_control=run_control,
		key_reader=key_reader,
	)

	# stitch and finalize
	trajectory = stitch_trajectories(interval_results)
	trajectory = anchor_to_seeds(trajectory, seeds)
	trajectory = _stamp_seed_confidence(trajectory, seeds)
	trajectory = _apply_trajectory_erasure(trajectory, seeds, fps)

	# Surface race-start detection so the user can sanity-check the
	# pre-race reference (contract C2) without opening diagnostics files.
	pre_race_reference = context.pre_race_reference
	race_start.print_race_phase_summary(pre_race_reference)

	output = {
		"intervals": interval_results,
		"trajectory": trajectory,
		"pre_race_reference": pre_race_reference,
	}
	return output
