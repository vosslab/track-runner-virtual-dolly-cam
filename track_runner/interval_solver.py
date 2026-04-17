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
import key_input
import state_io
import velocity_model
import race_phases
import residual_motion


#============================================
class BlockBarColumn(rich.progress.ProgressColumn):
	"""Progress bar column using block characters for visual prominence.

	Renders filled portion with full-block and remaining with light-shade.
	Expands to fill available terminal width by querying terminal size.
	"""
	# full block for completed, light shade for remaining
	FILLED = "\u2588"
	EMPTY = "\u2591"
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
		eta_s = max(0, remaining / fps_rate)
		eta_str = self._format_duration(eta_s)
		self._last_text = f"ETA {eta_str}  elapsed {elapsed_str}"
		return rich.text.Text(self._last_text)


#============================================
# Agreement tolerance: Dice coefficient threshold for FWD/BWD agreement.
# Any overlap is meaningful for this method, so a low threshold is used.
AGREE_DICE_THRESHOLD = 0.3


#============================================
def fuse_tracks(
	forward_track: list,
	backward_track: list,
) -> list:
	"""Fuse forward and backward tracking passes frame by frame.

	Where both tracks agree (center within tolerance, scale within tolerance),
	produces a confidence-weighted average position. Where they disagree,
	picks the higher-confidence track and flags the frame. Never averages
	two mediocre conflicting paths into a false consensus.

	Args:
		forward_track: List of tracking state dicts from propagate_forward().
			Index 0 is the seed frame.
		backward_track: List of tracking state dicts from propagate_backward().
			Already reversed so index 0 is the seed frame.

	Returns:
		List of fused tracking state dicts, one per frame. Source field is
		"merged" when both agreed, "propagated" when one was picked over the
		other. A "fuse_flag" key is added when the tracks disagreed.
	"""
	n = min(len(forward_track), len(backward_track))
	fused = []

	for i in range(n):
		fwd = forward_track[i]
		bwd = backward_track[i]

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
				"fuse_flag": False,
				"occlusion_risk": frame_occlusion,
			}
		else:
			# disagreement: pick the higher-confidence track
			if fwd_conf >= bwd_conf:
				winner = dict(fwd)
				winner["source"] = "propagated"
			else:
				winner = dict(bwd)
				winner["source"] = "propagated"
			winner["fuse_flag"] = True
			winner["occlusion_risk"] = frame_occlusion
			state = winner

		fused.append(state)

	return fused


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
) -> dict:
	"""Solve one interval using analytical velocity model (no optical flow).

	Fits directionally asymmetric Hermite curves to seed positions in scene
	coordinates, propagates forward and backward, fuses tracks, and scores
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
			- fused_track: list of tracking state dicts (pixel coords)
			- forward_track: forward pass (for diagnostics)
			- backward_track: backward pass (for diagnostics)
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

	# propagate forward (backward-looking slopes)
	forward_track_scene = velocity_model.propagate_forward_analytical(
		interval_curves, scene_transform,
	)

	# propagate backward (forward-looking slopes)
	backward_track_scene = velocity_model.propagate_backward_analytical(
		interval_curves, scene_transform,
	)

	# velocity model returns pixel coordinates directly (already converted)
	forward_track = list(forward_track_scene)
	backward_track = list(backward_track_scene)

	if debug:
		print(f"    fusing tracks ({len(forward_track)}+{len(backward_track)} "
			f"states)...")

	# fuse forward and backward tracks
	fused_track = fuse_tracks(forward_track, backward_track)

	if debug:
		print("    scoring interval analytically...")

	# score the interval using analytical metrics
	interval_score = scoring.score_interval_analytical(
		forward_track, backward_track, all_seeds_scene,
		interval_curves, scene_transform,
		motion_track=motion_track,
		all_seeds=all_seeds,
		fused_track=fused_track,
		fps=fps,
	)

	result = {
		"start_frame": start_frame,
		"end_frame": end_frame,
		"fused_track": fused_track,
		"forward_track": forward_track,
		"backward_track": backward_track,
		"interval_score": interval_score,
	}

	# in debug mode, capture per-frame agreement records for investigation.
	# the aggregate agreement is already in interval_score; this adds the
	# per-frame iou/center_dist/size_ratio + p10/p50/p90 percentiles that
	# the main metric averages away. sidecar only, never touches the main
	# diagnostics schema.
	if debug:
		agreement_debug = scoring.compute_agreement_debug(
			forward_track, backward_track, start_frame=start_frame,
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
		fused = result["fused_track"]
		for i, state in enumerate(fused):
			frame_index = start + i
			if 0 <= frame_index <= last_end:
				trajectory[frame_index] = state

	return trajectory



#============================================
def _format_interval_result(result: dict, fps: float) -> str:
	"""Format a single interval result as a summary string.

	Args:
		result: Interval result dict from solve_interval_analytical().
		fps: Video frame rate for duration calculation.

	Returns:
		Formatted string with interval metrics.
	"""
	start_frame = result["start_frame"]
	end_frame = result["end_frame"]
	duration_s = (end_frame - start_frame) / fps
	score = result["interval_score"]
	reasons = score.get("failure_reasons", [])
	# detect v3 analytical vs v2 legacy format
	if "confidence_tier" in score:
		# v3 analytical format
		confidence = score["confidence_tier"]
		agree = score.get("agreement", 0.0)
		vel_cons = score.get("velocity_consistency", 0.0)
		size_cons = score.get("size_consistency", 0.0)
		metrics_str = (
			f"agree={agree:.2f}  "
			f"vel_cons={vel_cons:.2f}  "
			f"size_cons={size_cons:.2f}"
		)
	else:
		# v2 legacy format
		confidence = score.get("confidence", "low")
		agree = score.get("agreement_score", 0.0)
		margin = score.get("competitor_margin", 0.0)
		identity = score.get("identity_score", 0.0)
		metrics_str = (
			f"agree={agree:.2f}  "
			f"margin={margin:.2f}  "
			f"identity={identity:.2f}"
		)
	# format label by confidence tier
	_confidence_labels = {
		"high": "TRUST", "good": "GOOD", "fair": "FAIR", "low": "WEAK",
	}
	tag = _confidence_labels.get(confidence, "WEAK")
	if confidence in ("high", "good"):
		label = f"[{tag}]"
	else:
		reason_str = ", ".join(reasons) if reasons else "low_confidence"
		label = f"[{tag}: {reason_str}]"
	line = (
		f"  interval {start_frame:5d}-{end_frame:5d} "
		f"({duration_s:.1f}s)  "
		f"{metrics_str}  {label}"
	)
	return line


#============================================
def _print_interval_result(result: dict, fps: float) -> None:
	"""Print a single interval result summary line.

	Args:
		result: Interval result dict from solve_interval_analytical().
		fps: Video frame rate for duration calculation.
	"""
	print(_format_interval_result(result, fps))


#============================================
def _print_interval_result_rich(
	result: dict,
	fps: float,
	progress: rich.progress.Progress,
) -> None:
	"""Print an interval result line through rich console.

	Uses progress.console.print() so the output does not conflict
	with the live progress bar display.

	Args:
		result: Interval result dict from solve_interval_analytical().
		fps: Video frame rate for duration calculation.
		progress: Active rich Progress instance.
	"""
	progress.console.print(_format_interval_result(result, fps))


#============================================
def _stamp_seed_confidence(
	trajectory: list,
	seeds: list,
) -> list:
	"""Stamp seed confidence and status onto trajectory at seed frames.

	Ensures seed frames have the correct confidence regardless of what
	fuse_tracks() computed. Visible and partial seeds get conf=1.0
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

	Corrects drift in fused trajectories by fitting local splines through
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
def _prepare_usable_seed(seed: dict) -> dict:
	"""Copy seed and set default conf=0.3 for approx seeds.

	When a runner is fully hidden, the user draws a larger approx area
	indicating the general region. This guides the solver through the
	gap but confidence is low because the exact position is unknown.

	Args:
		seed: Seed dict, possibly with status "approximate" or legacy
			"obstructed".

	Returns:
		Original seed if not approx, or a copy with conf=0.3 if
		approx and conf was not already set.
	"""
	if seed["status"] in ("approximate", "obstructed") and seed.get("conf") is None:
		prepared = dict(seed)
		# approx area, uncertain position -- lower confidence
		prepared["conf"] = 0.3
		return prepared
	return seed


#============================================
def filter_usable_seeds_sorted(seeds: list, verbose: bool = True) -> list:
	"""Filter, sort, and deduplicate seeds to their interval-endpoint form.

	This is the single source of truth for which seeds become interval
	endpoints. `solve_all_intervals` and `cli._mode_refine` both call this
	so they compute identical `state_io.interval_fingerprint` keys.

	Args:
		seeds: Raw seed list from the seeds file.
		verbose: If True, print WARNING lines for duplicate frame_index.

	Returns:
		List of usable-seed dicts (post `_prepare_usable_seed`), sorted by
		frame_index, deduplicated by frame_index (keeping the latest pass).
		May be empty if fewer than 2 usable seeds remain.
	"""
	# accept visible/partial/approximate unconditionally; accept legacy
	# obstructed only when a torso_box is present
	usable_seeds = [
		_prepare_usable_seed(s) for s in seeds
		if s["status"] in ("visible", "partial", "approximate")
		or (s["status"] == "obstructed" and s.get("torso_box") is not None)
	]
	if not usable_seeds:
		return []
	# sort by frame_index so consecutive pairs are adjacent
	usable_sorted = sorted(usable_seeds, key=lambda s: int(s["frame_index"]))
	# deduplicate: keep the seed from the latest pass when frames collide
	seen_frames = {}
	for seed in usable_sorted:
		fi = int(seed["frame_index"])
		if fi in seen_frames:
			existing = seen_frames[fi]
			if int(seed["pass"]) >= int(existing["pass"]):
				if verbose:
					print(f"  WARNING: duplicate seed at frame {fi}, "
						f"keeping pass {seed['pass']} over pass {existing['pass']}")
				seen_frames[fi] = seed
			else:
				if verbose:
					print(f"  WARNING: duplicate seed at frame {fi}, "
						f"keeping pass {existing['pass']} over pass {seed['pass']}")
		else:
			seen_frames[fi] = seed
	if len(seen_frames) < len(usable_sorted):
		dropped = len(usable_sorted) - len(seen_frames)
		if verbose:
			print(f"  deduplicated {dropped} seeds with duplicate frame_index values")
		usable_sorted = sorted(seen_frames.values(), key=lambda s: int(s["frame_index"]))
	return usable_sorted


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
		num_workers: Number of parallel workers for solving (currently unused).
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

	# convert all seeds to scene coordinates
	all_seeds_scene = []
	for seed in seeds:
		frame_index = int(seed["frame_index"])
		cx = float(seed["cx"])
		cy = float(seed["cy"])
		w = float(seed["w"])
		h = float(seed["h"])
		sx, sy, sw, sh = scene_transform.pixel_box_to_scene(
			frame_index, cx, cy, w, h,
		)
		all_seeds_scene.append((frame_index, sx, sy, sw, sh))

	# filter, sort, and deduplicate seeds to interval-endpoint form.
	# this MUST match cli._mode_refine's fingerprint computation so cached
	# intervals are found on reuse.
	usable_seeds_sorted = filter_usable_seeds_sorted(seeds)
	if len(usable_seeds_sorted) < 2:
		print("  interval_solver: need at least 2 usable seeds to solve intervals")
		return {"intervals": [], "trajectory": []}
	total_intervals = len(usable_seeds_sorted) - 1
	interval_results = []

	print(f"  solving {total_intervals} intervals (analytical mode)...")
	seq_frame_counter = [0]
	# track cache reuse vs fresh solves for the post-loop summary
	reused_count = 0
	solved_count = 0
	with rich.progress.Progress(
		rich.progress.TextColumn("{task.description}"),
		BlockBarColumn(),
		rich.progress.TaskProgressColumn(),
		rich.progress.TimeRemainingColumn(),
		refresh_per_second=1,
	) as progress:
		task = progress.add_task(
			"  solving intervals", total=total_intervals,
		)
		for pair_idx in range(total_intervals):
			if run_control is not None and run_control.quit_requested:
				break
			if key_reader is not None and run_control is not None:
				ch = key_reader.poll()
				key_input.handle_key(ch, run_control, key_reader, progress)
			if run_control is not None and run_control.quit_requested:
				break

			seed_start = usable_seeds_sorted[pair_idx]
			seed_end = usable_seeds_sorted[pair_idx + 1]
			start_frame = int(seed_start["frame_index"])
			end_frame = int(seed_end["frame_index"])

			# compute fingerprint up front so we can check the cache before
			# calling the analytical solver
			fingerprint = state_io.interval_fingerprint(seed_start, seed_end)

			# cache hit: reuse prior result, skip solver and skip write-back
			if prior_intervals is not None and fingerprint in prior_intervals:
				result = prior_intervals[fingerprint]
				reused_count += 1
				if debug:
					progress.console.print(
						f"  reusing interval {pair_idx + 1}/{total_intervals} "
						f"(frames {start_frame}-{end_frame}) from cache"
					)
			else:
				if debug:
					progress.console.print(
						f"  solving interval {pair_idx + 1}/{total_intervals} "
						f"(frames {start_frame}-{end_frame})"
					)
				result = solve_interval_analytical(
					seed_start, seed_end, scene_transform,
					all_seeds_scene, fps, debug=debug,
					motion_track=motion_track,
					all_seeds=seeds,
				)
				solved_count += 1
				if on_interval_solved is not None:
					on_interval_solved(fingerprint, result)
				if debug:
					_print_interval_result_rich(result, fps, progress)

			interval_results.append(result)
			if on_interval_complete is not None:
				on_interval_complete(result)
			n_frames = result["end_frame"] - result["start_frame"]
			seq_frame_counter[0] += n_frames
			progress.update(task, advance=1)

	# post-loop summary: make cache behavior visible on stdout
	print(f"  solver: reused {reused_count}, solved {solved_count} "
		f"of {total_intervals}")
	# visibility warning if we had a non-empty cache but matched nothing --
	# likely a fingerprint drift from seed filter or rounding changes
	if prior_intervals and reused_count == 0 and solved_count > 0:
		print(f"  solver: WARNING 0 of {len(prior_intervals)} prior intervals "
			f"reused -- possible fingerprint mismatch "
			f"(check seed filter / rounding)")

	if run_control is not None and run_control.quit_requested:
		done = len([r for r in interval_results if r is not None])
		key_input._quit_trace(
			"FUNCTION_RETURN", context="solve_all_intervals_analytical",
			quit_requested=True, intervals_done=done,
			intervals_total=total_intervals,
		)
		print(f"  quit requested: {done}/{total_intervals} intervals completed")
		print("  hint: re-run 'solve' to resume")

	# stitch and finalize
	trajectory = stitch_trajectories(interval_results)
	# race phase detection runs BEFORE motion-cue fusion so the detected
	# race start can gate motion-cue (pre-race frames have no valid motion
	# signal). Operates on the stitched trajectory before any refinement.
	race_phase = race_phases.detect_race_start(trajectory, scene_transform, fps)
	# only gate on strong detections; weak detections fall through and
	# motion-cue runs on all frames as before.
	gate_race_start = race_phase["race_start_frame"]
	if race_phase["confidence"] < race_phases.GATE_MIN_CONFIDENCE:
		gate_race_start = None
	# per-frame motion-cue observation fusion (Hermite scaffold + blob center)
	trajectory = residual_motion.refine_with_motion_cues(
		trajectory, reader, scene_transform, seeds,
		race_start_frame=gate_race_start,
	)
	trajectory = anchor_to_seeds(trajectory, seeds)
	trajectory = _stamp_seed_confidence(trajectory, seeds)
	trajectory = _apply_trajectory_erasure(trajectory, seeds, fps)

	output = {
		"intervals": interval_results,
		"trajectory": trajectory,
		"race_phase": race_phase,
	}
	return output
