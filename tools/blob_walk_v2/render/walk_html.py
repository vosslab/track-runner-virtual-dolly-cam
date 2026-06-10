"""Generate walk.html: per-interval power-of-2 sampled walk view with dense-fill failure windows.

For each (video, interval [F_L, F_R]):
- FWD column: frames at F_L + offset for offset in {0, 1, 2, 4, 8, ...} up to interval_length
  PLUS dense-fill offsets in failure windows (between the walker's stop row and the next
  power-of-2 boundary, allowing frame-by-frame step through the failure transition)
- BWD column: frames at F_R - offset for the same offset set

Each tile carries per-frame caption data: dt, actual_jump / allowed_jump (as W-fractions),
obs_confidence, winner_mode, audit rule name on mode disagreement. Status border color
encodes walker state: green=accepted, orange=soft miss, red=rejected, gray=no blob, etc.
Vector overlays (velocity arrow, allowed-jump circle, residual line) are baked directly
into each PNG by walk_render.py. No inline SVG in the HTML.

Missing-PNG behavior: CSV-only rows render as "no_tile" cells when the driver legitimately
skipped rendering (bootstrap step 0 with empty pred_cx/pred_cy, or non-accepted status).
Real render failures (accepted row with non-empty pred_cx but missing PNG) still abort the
build LOUDLY with RuntimeError.

Per-interval: trajectory PNG (FWD/BWD accepted polylines + seed markers) written to
<seed_dir>/trajectory.png and referenced by <img> tag.

Per-interval summary line: FWD walk-interval length, BWD walk-interval length, gap,
stop reasons, max actual_jump as W-fraction, median actual_jump as W-fraction,
mode-disagreement count.

Legend in HTML only: gate constants verbatim, real-world sanity comment from walk_motion_gate.
"""

import csv
import dataclasses
import math
import pathlib

# PIP3 modules
import matplotlib
matplotlib.use('Agg')  # non-interactive backend, no display needed
import matplotlib.pyplot

# local repo modules
import walk_util
import walk_palette
import blob_walk.walk_motion_gate as walk_motion_gate

# Shared walk_overlays palette (trajectory/seed-marker colors + legend swatches).
_WALK_PALETTE = walk_palette.load_walk_overlays()

# Status border colors for tile cells: encode walker state visually.
# v13 status enum: accepted, interpolated, extrapolated,
#   soft_miss_no_blob, soft_miss_no_path.
# Legacy values (soft_miss_low_conf, rejected_motion_gate) kept for v12 CSV
# backward read compat; never emitted by v13 walker.
_STATUS_BORDER_COLORS = {
	# v13 primary statuses
	'accepted': '#00cc00',
	'interpolated': '#60b4e8',
	'extrapolated': '#e8c840',
	'soft_miss_no_blob': '#888888',
	'soft_miss_no_path': '#e87060',
	# Terminal markers (always emitted)
	'after_walk_terminated': '#444444',
	'hit_neighbor_seed': '#444444',
	'boundary': '#444444',
	'no_tile': '#444444',
	'unknown': '#cc00cc',
	# Legacy v12 values: never emitted by v13 walker; kept for CSV read compat
	'soft_miss_low_conf': '#ff8800',
	'rejected_motion_gate': '#ff0000',
}


#============================================
# Gate constants are sourced from walk_motion_gate.py (single source of truth).
# Physical runner-speed envelope in W/s; per-frame derivation = SPEED / fps.
MAX_RUNNER_SPEED_W_PER_S = walk_motion_gate.MAX_RUNNER_SPEED_W_PER_S
MIN_RUNNER_SPEED_W_PER_S = walk_motion_gate.MIN_RUNNER_SPEED_W_PER_S
BOOTSTRAP_UNCERTAINTY_W = walk_motion_gate.BOOTSTRAP_UNCERTAINTY_W
ABSOLUTE_MAX_JUMP_W = walk_motion_gate.ABSOLUTE_MAX_JUMP_W
VELOCITY_TOLERANCE = walk_motion_gate.VELOCITY_TOLERANCE
DT_GATE_CAP = walk_motion_gate.DT_GATE_CAP
MEASUREMENT_ALLOWANCE_W = walk_motion_gate.MEASUREMENT_ALLOWANCE_W

# Real-world sanity comment from walk_motion_gate.py.
REAL_WORLD_SANITY_COMMENT = """A runner at 16 mph travels roughly 7.15 m/s (16 * 1609.34 / 3600).
At 60 fps, the displacement per frame is ~12 cm = ~4.7 inches.
A typical adult torso width is ~16 inches = ~40 cm.
So a 16 mph displacement is roughly 0.3 torso widths per frame.

Torso HEIGHT is ~2x torso width. Using 1.0 height as a motion budget allows
~2 torso-width teleports, which is too loose for the spatial precision we
need. WIDTH is the correct scale: it expresses motion in the runner's
natural proportions and prevents multi-torso jumps at 60 fps.

The motion gate scales motion allowance with dt_for_gate, permitting missed
frames without velocity inflation. A hard absolute_cap prevents runaway
even at dt_for_gate == 3 (300 milliseconds / 18 frames)."""


#============================================
# Corpus quality classification thresholds (per-interval accepted_fraction).
# An interval is "no_signal" when BOTH passes accept < NO_SIGNAL_ACCEPTED_FRACTION
# of their walk steps (genuinely empty residual heat-map, not a walker failure).
# Above that, an interval is "completed_low_quality" if EITHER pass accepts
# < LOW_QUALITY_ACCEPTED_FRACTION; otherwise "completed_normal".
NO_SIGNAL_ACCEPTED_FRACTION = 0.1
LOW_QUALITY_ACCEPTED_FRACTION = 0.3


#============================================

def _classify_interval(
	fwd_accepted_fraction: float | None,
	bwd_accepted_fraction: float | None,
) -> str:
	"""Classify one interval from its FWD/BWD accepted fractions.

	A missing (None) accepted_fraction means the pass had no walkable rows;
	it is treated as 0.0 for classification so an empty pass cannot mask a
	no_signal interval.

	Args:
		fwd_accepted_fraction: FWD accepted_fraction (0.0-1.0) or None.
		bwd_accepted_fraction: BWD accepted_fraction (0.0-1.0) or None.

	Returns:
		'no_signal', 'completed_low_quality', or 'completed_normal'.
	"""
	fwd_af = fwd_accepted_fraction if fwd_accepted_fraction is not None else 0.0
	bwd_af = bwd_accepted_fraction if bwd_accepted_fraction is not None else 0.0
	if fwd_af < NO_SIGNAL_ACCEPTED_FRACTION and bwd_af < NO_SIGNAL_ACCEPTED_FRACTION:
		return 'no_signal'
	if fwd_af < LOW_QUALITY_ACCEPTED_FRACTION or bwd_af < LOW_QUALITY_ACCEPTED_FRACTION:
		return 'completed_low_quality'
	return 'completed_normal'


#============================================

def _compute_fwd_bwd_agreement_px(fwd_rows: dict, bwd_rows: dict) -> float | None:
	"""Median per-frame FWD/BWD distance over frames accepted in BOTH passes.

	Per contract C9, FWD and BWD remain independent: this reads each pass's
	own accepted positions and only measures their disagreement; neither pass
	is modified. The distance is a raw pixel quantity reported for diagnostics,
	not a runner-relative tracking decision, so raw pixels are allowed (C2).

	Args:
		fwd_rows: {frame_index: row_dict} from fwd_verdicts.csv.
		bwd_rows: {frame_index: row_dict} from bwd_verdicts.csv.

	Returns:
		Median Euclidean distance in pixels, or None if no common frame is
		accepted in both passes.
	"""
	# Build frame -> (cx, cy) for accepted rows in each pass independently.
	fwd_accepted = {}
	for fi, r in fwd_rows.items():
		if (r.get('status') or '').strip() != 'accepted':
			continue
		cx = walk_util._to_float_or_none(r.get('cand_cx'))
		cy = walk_util._to_float_or_none(r.get('cand_cy'))
		if cx is not None and cy is not None:
			fwd_accepted[fi] = (cx, cy)

	bwd_accepted = {}
	for fi, r in bwd_rows.items():
		if (r.get('status') or '').strip() != 'accepted':
			continue
		cx = walk_util._to_float_or_none(r.get('cand_cx'))
		cy = walk_util._to_float_or_none(r.get('cand_cy'))
		if cx is not None and cy is not None:
			bwd_accepted[fi] = (cx, cy)

	common_frames = sorted(set(fwd_accepted) & set(bwd_accepted))
	if not common_frames:
		return None

	dists = []
	for fi in common_frames:
		fx, fy = fwd_accepted[fi]
		bx, by = bwd_accepted[fi]
		dists.append(math.hypot(fx - bx, fy - by))

	dists.sort()
	n = len(dists)
	mid = n // 2
	if n % 2 == 1:
		median_px = dists[mid]
	else:
		median_px = (dists[mid - 1] + dists[mid]) / 2.0
	return median_px


#============================================

def _median_of_values(vals: list) -> float | None:
	"""Median of a list of floats, ignoring None entries.

	Args:
		vals: list of floats and/or None.

	Returns:
		Median float, or None if no usable value is present.
	"""
	clean = sorted(v for v in vals if v is not None)
	if not clean:
		return None
	n = len(clean)
	mid = n // 2
	if n % 2 == 1:
		return clean[mid]
	return (clean[mid - 1] + clean[mid]) / 2.0


#============================================

def _powers_of_two_up_to(max_offset: int) -> list:
	"""Return [0, 1, 2, 4, 8, ...] up to max_offset."""
	out = [0]
	if max_offset >= 1:
		out.append(1)
	k = 2
	while k <= max_offset:
		out.append(k)
		k *= 2
	return out


#============================================

def _read_debug_log_csv(path: pathlib.Path) -> dict:
	"""Return {frame_index: row_dict} for the given debug log CSV."""
	out = {}
	if not path.exists():
		return out
	with open(path) as f:
		for row in csv.DictReader(f):
			try:
				frame_index = int(row['frame_index'])
			except (ValueError, KeyError, TypeError):
				continue
			out[frame_index] = row
	return out


#============================================

def _compute_dense_fill_offsets(
	walker_stop_row: int,
	next_power_of_two: int,
) -> list:
	"""Return the list of offsets to fill between walker_stop_row and next_power_of_two.

	If walker stops at row K, and the next power-of-2 boundary would be at row 16,
	this returns [K, K+1, ..., 15] to enable frame-by-frame stepping through failure.
	"""
	if walker_stop_row >= next_power_of_two:
		return []
	return list(range(walker_stop_row, next_power_of_two))


#============================================

def _determine_walker_stop_row(rows_by_frame: dict, anchor_frame: int, direction: str) -> int | None:
	"""Determine the last row the walker visited (max or min frame_index).

	Returns the walker's final step offset, or None if no rows exist.
	For FWD from anchor, stop_row = max(frame_index) - anchor.
	For BWD from anchor, stop_row = anchor - min(frame_index).
	"""
	if not rows_by_frame:
		return None
	if direction == 'FWD':
		max_frame = max(int(r['frame_index']) for r in rows_by_frame.values())
		return max_frame - anchor_frame
	else:  # BWD
		min_frame = min(int(r['frame_index']) for r in rows_by_frame.values())
		return anchor_frame - min_frame


#============================================

def _build_sampled_offsets(
	walker_stop_row: int | None,
	interval_length: int,
	direction: str,
) -> list:
	"""Build the list of offsets to sample: power-of-2 + dense-fill in failure windows.

	Walker_stop_row is the max offset the walker visited.
	Power-of-2 offsets go up to interval_length.
	If walker stopped before a power-of-2 boundary, fill the gap densely.
	"""
	offsets_set = set()
	power_of_two = _powers_of_two_up_to(interval_length)
	offsets_set.update(power_of_two)

	if walker_stop_row is not None and walker_stop_row < interval_length:
		# Find the next power-of-2 after walker_stop_row.
		next_po2 = 1
		while next_po2 <= walker_stop_row:
			next_po2 *= 2
		# Dense-fill from walker_stop_row to next_po2 (exclusive).
		dense = _compute_dense_fill_offsets(walker_stop_row, min(next_po2, interval_length + 1))
		offsets_set.update(dense)

	return sorted(offsets_set)


#============================================

def _extract_audit_rule_name(row: dict) -> str:
	"""Extract audit rule name when modes disagree.

	If audit_winner_rule is present and non-empty, return it; else empty string.
	"""
	rule = (row.get('audit_winner_rule') or '').strip()
	return rule if rule else ""


#============================================

def _render_cell_caption(row: dict) -> str:
	"""Build per-tile caption with frame_index, status, stop_reason always included, plus optional fields."""
	parts = []

	# frame_index: always required
	frame_index = row.get('frame_index')
	if frame_index:
		parts.append(f"frame {frame_index}")

	# status: always required
	status = (row.get('status') or '').strip()
	if status:
		parts.append(f"status: {status}")

	# stop_reason: include if non-empty
	stop_reason = (row.get('stop_reason') or '').strip()
	if stop_reason:
		parts.append(f"stop_reason: {stop_reason}")

	# dt: frames since last accept
	dt = row.get('dt')
	if dt and dt.strip():
		parts.append(f"dt: {dt}")

	# actual_jump / allowed_jump as W-fractions
	actual = row.get('actual_jump')
	allowed = row.get('allowed_jump')
	torso_w = row.get('torso_w_px')
	if actual and allowed and torso_w:
		try:
			actual_f = float(actual)
			allowed_f = float(allowed)
			torso_w_f = float(torso_w)
			if torso_w_f > 0:
				actual_w = actual_f / torso_w_f
				allowed_w = allowed_f / torso_w_f
				parts.append(f"jump: {actual_w:.2f} / {allowed_w:.2f} W")
		except (ValueError, TypeError):
			pass

	# obs_confidence
	conf = row.get('obs_confidence')
	if conf and conf.strip():
		try:
			conf_f = float(conf)
			parts.append(f"conf: {conf_f:.2f}")
		except ValueError:
			pass

	# winner_mode
	mode = row.get('winner_mode')
	if mode and mode.strip():
		parts.append(f"winner: {mode}")

	# audit rule name on disagreement
	rule = _extract_audit_rule_name(row)
	if rule:
		parts.append(f"rule: {rule}")

	return " | ".join(parts)


#============================================

def _read_png_size(png_path: pathlib.Path) -> tuple | None:
	"""Return (width, height) of PNG, or None if unreadable.

	Reads the IHDR chunk directly to avoid pulling pillow into this path.
	"""
	if not png_path.exists():
		return None
	with open(png_path, 'rb') as f:
		head = f.read(24)
	if len(head) < 24:
		return None
	if head[:8] != b'\x89PNG\r\n\x1a\n':
		return None
	# IHDR width/height: big-endian uint32 at offsets 16 and 20.
	width = int.from_bytes(head[16:20], 'big')
	height = int.from_bytes(head[20:24], 'big')
	return (width, height)


#============================================
def _render_trajectory_png(
	fwd_rows: dict,
	bwd_rows: dict,
	left_seed_xy: tuple,
	right_seed_xy: tuple,
	out_path: pathlib.Path,
) -> bool:
	"""Render a per-interval trajectory PNG using matplotlib.

	Plots accepted FWD/BWD walker positions as polylines and left/right seeds
	as filled circles. Saves to out_path. Returns True if rendered, False if
	no accepted positions exist (file not written).

	Args:
		fwd_rows: {frame_index: row_dict} from fwd_verdicts.csv.
		bwd_rows: {frame_index: row_dict} from bwd_verdicts.csv.
		left_seed_xy: (cx, cy) for left seed position in source pixels.
		right_seed_xy: (cx, cy) for right seed position in source pixels.
		out_path: Destination PNG path (parent must exist).

	Returns:
		True if PNG was written; False if skipped (no data).
	"""
	def _accepted_xy(rows: dict) -> list:
		points = []
		for fi in sorted(rows.keys()):
			r = rows[fi]
			if (r.get('status') or '').strip() != 'accepted':
				continue
			cx = walk_util._to_float_or_none(r.get('cand_cx'))
			cy = walk_util._to_float_or_none(r.get('cand_cy'))
			if cx is None or cy is None:
				cx = walk_util._to_float_or_none(r.get('pred_cx'))
				cy = walk_util._to_float_or_none(r.get('pred_cy'))
			if cx is None or cy is None:
				continue
			points.append((cx, cy))
		return points

	fwd_pts = _accepted_xy(fwd_rows)
	bwd_pts = _accepted_xy(bwd_rows)

	# Nothing to render if no accepted positions.
	if not fwd_pts and not bwd_pts:
		return False

	fwd_color = _WALK_PALETTE['trajectory_fwd']
	bwd_color = _WALK_PALETTE['trajectory_bwd']
	seed_color = _WALK_PALETTE['seed_marker']

	fig, ax = matplotlib.pyplot.subplots(figsize=(6, 2.5))
	fig.patch.set_facecolor('#111111')
	ax.set_facecolor('#111111')

	# FWD polyline: x = frame position (cx), y = cy
	if fwd_pts:
		xs = [p[0] for p in fwd_pts]
		ys = [p[1] for p in fwd_pts]
		ax.plot(xs, ys, color=fwd_color, linewidth=1.5, label='FWD')

	# BWD polyline
	if bwd_pts:
		xs = [p[0] for p in bwd_pts]
		ys = [p[1] for p in bwd_pts]
		ax.plot(xs, ys, color=bwd_color, linewidth=1.5, label='BWD')

	# Seed markers: filled circles
	ax.scatter([left_seed_xy[0]], [left_seed_xy[1]], color=seed_color, s=40, zorder=5)
	ax.scatter([right_seed_xy[0]], [right_seed_xy[1]], color=seed_color, s=40, zorder=5)

	# Axis styling: tick marks, labels in muted color.
	ax.tick_params(colors='#888888', labelsize=7)
	for spine in ax.spines.values():
		spine.set_edgecolor('#444444')
	ax.set_xlabel('x (px)', color='#888888', fontsize=7)
	ax.set_ylabel('y (px)', color='#888888', fontsize=7)
	# 5 tick marks on each axis.
	ax.xaxis.set_major_locator(matplotlib.pyplot.MaxNLocator(5))
	ax.yaxis.set_major_locator(matplotlib.pyplot.MaxNLocator(5))
	ax.legend(fontsize=7, facecolor='#222222', edgecolor='#555555', labelcolor='#cccccc')
	# Invert y-axis so image y=0 is at top (matches screen coordinates).
	ax.invert_yaxis()

	matplotlib.pyplot.tight_layout(pad=0.4)
	fig.savefig(str(out_path), dpi=90, bbox_inches='tight', facecolor=fig.get_facecolor())
	matplotlib.pyplot.close(fig)
	return True


#============================================
# Statuses for which the driver legitimately skips tile rendering.
# v13 primary: soft_miss_no_blob, soft_miss_no_path, interpolated, extrapolated
# Terminal markers: after_walk_terminated, hit_neighbor_seed, boundary
# Legacy v12 (never emitted by v13 walker; kept for backward CSV read compat):
#   soft_miss_low_conf, rejected_motion_gate
_NO_TILE_STATUSES = frozenset({
	'soft_miss_no_blob',
	'soft_miss_no_path',
	'interpolated',
	'extrapolated',
	'after_walk_terminated',
	'hit_neighbor_seed',
	'boundary',
	# Legacy v12 values: backward read compat only
	'soft_miss_low_conf',
	'rejected_motion_gate',
})


def _render_column_cells(
	interval_dir: pathlib.Path,
	anchor_frame: int,
	interval_length: int,
	direction: str,
	rows_by_frame: dict,
	output_root: pathlib.Path,
) -> list:
	"""Build per-cell HTML for one column (FWD or BWD).

	Include power-of-2 offsets + dense-fill in failure windows.
	CSV-only rows (no PNG, but legitimately skipped by driver) render as
	"no_tile" cells. Abort LOUDLY only if an accepted row with non-empty
	pred_cx is missing its PNG.
	"""
	cells = []

	walker_stop_row = _determine_walker_stop_row(rows_by_frame, anchor_frame, direction)
	offsets = _build_sampled_offsets(walker_stop_row, interval_length, direction)

	subdir = 'fwd' if direction == 'FWD' else 'bwd'

	for offset in offsets:
		if direction == 'FWD':
			frame_index = anchor_frame + offset
			label_sign = '+'
		else:
			frame_index = anchor_frame - offset
			label_sign = '-'

		row = rows_by_frame.get(frame_index)
		if row is None:
			# No row at this frame; skip entirely.
			continue

		png_path = interval_dir / subdir / f'frame_{frame_index:06d}.png'

		status = (row.get('status') or 'unknown').strip()
		pred_cx = (row.get('pred_cx') or '').strip()
		caption = _render_cell_caption(row)

		if not png_path.exists():
			# Tolerate legitimate driver skips (CSV-only rows).
			if status in _NO_TILE_STATUSES or not pred_cx:
				cells.append({
					'frame_index': frame_index,
					'offset': offset,
					'label_sign': label_sign,
					'rel_path': None,
					'status': 'no_tile',
					'caption': caption,
				})
				continue
			# Real render failure.
			raise RuntimeError(
				f"Missing PNG for {interval_dir.name} {direction} frame {frame_index}: "
				f"{png_path} (status={status}, pred_cx={pred_cx!r})"
			)

		rel_path = png_path.relative_to(output_root).as_posix()

		cells.append({
			'frame_index': frame_index,
			'offset': offset,
			'label_sign': label_sign,
			'rel_path': rel_path,
			'status': status,
			'caption': caption,
		})

	return cells


#============================================

@dataclasses.dataclass
class WalkQualityMetrics:
	"""Per-walk quality metrics for one FWD or BWD walk."""
	# Fraction of non-bootstrap walk steps that were accepted (0.0-1.0 or None).
	accepted_fraction: float | None
	# Longest run of consecutive non-accepted frames (0 if walk always accepted).
	longest_no_accept_streak: int
	# Euclidean distance in px from last accepted position to neighbor seed.
	# None if the walk never accepted or positions unavailable.
	final_displacement_to_neighbor_px: float | None
	# Reason token naming why a metric is undefined (None when the metric is
	# defined). The formatter prints this token instead of a bare "?".
	accepted_fraction_reason: str | None = None
	final_displacement_reason: str | None = None


#============================================

def _compute_walk_quality(
	rows_by_frame: dict,
	anchor_frame: int,
	direction: str,
	neighbor_cx: float | None,
	neighbor_cy: float | None,
) -> WalkQualityMetrics:
	"""Compute per-walk quality metrics from a verdict CSV row dict.

	Args:
		rows_by_frame: {frame_index: row_dict} from fwd_verdicts.csv or bwd_verdicts.csv.
		anchor_frame: Left seed frame (FWD) or right seed frame (BWD).
		direction: 'FWD' or 'BWD'.
		neighbor_cx: Neighbor seed cx in px (right seed for FWD, left seed for BWD).
		neighbor_cy: Neighbor seed cy in px.

	Returns:
		WalkQualityMetrics instance.
	"""
	# Bootstrap row (step==0 at anchor) is excluded from accepted_fraction denominator.
	# Walk steps are all rows with step != 0.
	# v13 status enum: accepted, interpolated, extrapolated,
	#   soft_miss_no_blob, soft_miss_no_path.
	# Legacy v12 values kept for backward read compat of older CSVs.
	walkable_statuses = frozenset({
		'accepted',
		'interpolated',
		'extrapolated',
		'soft_miss_no_blob',
		'soft_miss_no_path',
		# Legacy v12 values: never emitted by v13 walker
		'soft_miss_low_conf',
		'rejected_motion_gate',
	})
	walk_rows = []
	for frame_index in sorted(rows_by_frame.keys()):
		row = rows_by_frame[frame_index]
		# Bootstrap detection: only the bootstrap row carries an explicit step of
		# 0; the walker writes blank step on every emitted walk row. Excluding
		# blank-step rows (the old behavior) discarded every real walk row, so
		# the metrics always came back undefined. Treat blank step as a walk row
		# and exclude only the explicit step == 0 bootstrap row.
		step_str = (row.get('step') or '').strip()
		if step_str == '0':
			continue
		status = (row.get('status') or '').strip()
		if status not in walkable_statuses:
			continue
		walk_rows.append((frame_index, row))

	if not walk_rows:
		return WalkQualityMetrics(
			accepted_fraction=None,
			longest_no_accept_streak=0,
			final_displacement_to_neighbor_px=None,
			accepted_fraction_reason='no_walkable_frames',
			final_displacement_reason='no_walkable_frames',
		)

	# Count accepted rows.
	accepted_count = sum(
		1 for _, row in walk_rows
		if (row.get('status') or '').strip() == 'accepted'
	)
	accepted_fraction = accepted_count / len(walk_rows) if walk_rows else None

	# Longest consecutive non-accepted streak.
	longest_streak = 0
	current_streak = 0
	last_accepted_cx: float | None = None
	last_accepted_cy: float | None = None
	for _, row in walk_rows:
		status = (row.get('status') or '').strip()
		if status == 'accepted':
			current_streak = 0
			# Track last accepted candidate position.
			cx = walk_util._to_float_or_none(row.get('cand_cx'))
			cy = walk_util._to_float_or_none(row.get('cand_cy'))
			if cx is not None and cy is not None:
				last_accepted_cx = cx
				last_accepted_cy = cy
		else:
			current_streak += 1
			if current_streak > longest_streak:
				longest_streak = current_streak

	# Final displacement: distance from the terminal accepted row's candidate
	# position to the neighbor seed. last_accepted_cx/cy track the most recent
	# accepted row's cand_cx/cand_cy above.
	final_displacement: float | None = None
	final_displacement_reason: str | None = None
	if last_accepted_cx is None or last_accepted_cy is None:
		final_displacement_reason = 'no_accepted_position'
	elif neighbor_cx is None or neighbor_cy is None:
		final_displacement_reason = 'no_neighbor_seed'
	else:
		dx = last_accepted_cx - neighbor_cx
		dy = last_accepted_cy - neighbor_cy
		final_displacement = (dx * dx + dy * dy) ** 0.5

	return WalkQualityMetrics(
		accepted_fraction=accepted_fraction,
		longest_no_accept_streak=longest_streak,
		final_displacement_to_neighbor_px=final_displacement,
		accepted_fraction_reason=None,
		final_displacement_reason=final_displacement_reason,
	)


#============================================

@dataclasses.dataclass
class IntervalSummaryStats:
	"""Summary statistics for one interval."""
	fwd_length: int
	bwd_length: int
	gap: int
	fwd_stop_reason: str
	bwd_stop_reason: str
	max_actual_jump_w: str
	median_actual_jump_w: str
	mode_disagreement_count: int
	fwd_quality: WalkQualityMetrics
	bwd_quality: WalkQualityMetrics
	# Corpus-quality fields folded in from the former score_corpus_quality tool.
	classification: str
	# Median FWD/BWD distance (px) over frames accepted in both passes; None if none.
	fwd_bwd_agreement_median_px: float | None


def _compute_interval_stats(
	fwd_rows: dict,
	bwd_rows: dict,
	f_l: int,
	f_r: int,
	right_seed_cx: float | None = None,
	right_seed_cy: float | None = None,
	left_seed_cx: float | None = None,
	left_seed_cy: float | None = None,
) -> IntervalSummaryStats:
	"""Compute per-interval summary statistics."""
	fwd_frames = sorted(int(r['frame_index']) for r in fwd_rows.values())
	bwd_frames = sorted(int(r['frame_index']) for r in bwd_rows.values())

	fwd_length = fwd_frames[-1] - f_l if fwd_frames else 0
	bwd_length = f_r - bwd_frames[0] if bwd_frames else 0
	gap = 0  # Will compute below if needed.

	fwd_stop_reason = next(
		(r.get('stop_reason', '?').strip() for r in fwd_rows.values() if r.get('stop_reason')),
		'?'
	)
	bwd_stop_reason = next(
		(r.get('stop_reason', '?').strip() for r in bwd_rows.values() if r.get('stop_reason')),
		'?'
	)

	# Compute max and median actual_jump in torso-width (W) units. Only rows
	# that carry a real per-frame displacement (actual_jump present AND
	# torso_w_px > 0) contribute; miss frames have no actual_jump and are
	# skipped. Undefined when no such row exists in either pass.
	actual_jumps_w = []
	for row in list(fwd_rows.values()) + list(bwd_rows.values()):
		actual_f = walk_util._to_float_or_none(row.get('actual_jump'))
		torso_w_f = walk_util._to_float_or_none(row.get('torso_w_px'))
		if actual_f is not None and torso_w_f is not None and torso_w_f > 0:
			actual_jumps_w.append(actual_f / torso_w_f)

	if actual_jumps_w:
		max_actual_jump_w = f"{max(actual_jumps_w):.2f}"
		sorted_jumps = sorted(actual_jumps_w)
		median = sorted_jumps[len(sorted_jumps) // 2]
		median_actual_jump_w = f"{median:.2f}"
	else:
		# Reason token replaces a bare "?" per the metric contract.
		max_actual_jump_w = "no_jump_rows"
		median_actual_jump_w = "no_jump_rows"

	# Count mode disagreements.
	mode_disagreement_count = 0
	for row in fwd_rows.values():
		prod = (row.get('production_winner_cx') or '').strip()
		audit = (row.get('audit_winner_cx') or '').strip()
		if prod and audit and prod != audit:
			mode_disagreement_count += 1
	for row in bwd_rows.values():
		prod = (row.get('production_winner_cx') or '').strip()
		audit = (row.get('audit_winner_cx') or '').strip()
		if prod and audit and prod != audit:
			mode_disagreement_count += 1

	# Compute per-walk quality metrics.
	fwd_quality = _compute_walk_quality(
		rows_by_frame=fwd_rows,
		anchor_frame=f_l,
		direction='FWD',
		neighbor_cx=right_seed_cx,
		neighbor_cy=right_seed_cy,
	)
	bwd_quality = _compute_walk_quality(
		rows_by_frame=bwd_rows,
		anchor_frame=f_r,
		direction='BWD',
		neighbor_cx=left_seed_cx,
		neighbor_cy=left_seed_cy,
	)

	# Corpus-quality readout folded in from the former score_corpus_quality tool:
	# classification from the two accepted fractions, plus FWD/BWD agreement.
	classification = _classify_interval(
		fwd_quality.accepted_fraction,
		bwd_quality.accepted_fraction,
	)
	fwd_bwd_agreement_median_px = _compute_fwd_bwd_agreement_px(fwd_rows, bwd_rows)

	return IntervalSummaryStats(
		fwd_length=fwd_length,
		bwd_length=bwd_length,
		gap=gap,
		fwd_stop_reason=fwd_stop_reason,
		bwd_stop_reason=bwd_stop_reason,
		max_actual_jump_w=max_actual_jump_w,
		median_actual_jump_w=median_actual_jump_w,
		mode_disagreement_count=mode_disagreement_count,
		fwd_quality=fwd_quality,
		bwd_quality=bwd_quality,
		classification=classification,
		fwd_bwd_agreement_median_px=fwd_bwd_agreement_median_px,
	)


#============================================

def _build_quality_summary_html(intervals: list) -> list:
	"""Build the corpus-level Quality summary section (folded-in scorer output).

	Aggregates the per-interval classification and accepted_fraction metrics
	across the whole run, plus a per-video rollup table. This replaces the
	former standalone QUALITY_SCORES.md aggregate.

	Args:
		intervals: list of interval dicts built in build_walk_html; each has
			'video' and a 'stats' IntervalSummaryStats.

	Returns:
		List of HTML line strings.
	"""
	out = []
	out.append('<div class="quality-summary">')
	out.append('  <strong>Quality summary (corpus-level)</strong>')

	total = len(intervals)
	if total == 0:
		out.append('  <br>No intervals.')
		out.append('</div>')
		return out

	# Classification counts across all intervals.
	no_signal = sum(1 for iv in intervals if iv['stats'].classification == 'no_signal')
	low_quality = sum(
		1 for iv in intervals if iv['stats'].classification == 'completed_low_quality'
	)
	normal = sum(
		1 for iv in intervals if iv['stats'].classification == 'completed_normal'
	)

	# Aggregate accepted_fraction and agreement over non-no_signal intervals only
	# (no_signal intervals have genuinely empty residual, so their fractions
	# would drag the medians toward zero and obscure real tracking quality).
	non_ns = [iv for iv in intervals if iv['stats'].classification != 'no_signal']
	fwd_afs = [iv['stats'].fwd_quality.accepted_fraction for iv in non_ns]
	bwd_afs = [iv['stats'].bwd_quality.accepted_fraction for iv in non_ns]
	agreements = [iv['stats'].fwd_bwd_agreement_median_px for iv in non_ns]
	median_fwd_af = _median_of_values(fwd_afs)
	median_bwd_af = _median_of_values(bwd_afs)
	median_combined_af = _median_of_values(fwd_afs + bwd_afs)
	median_agreement = _median_of_values(agreements)

	def _frac(f: float | None) -> str:
		return f"{f:.2%}" if f is not None else "?"

	def _px(d: float | None) -> str:
		return f"{d:.1f}px" if d is not None else "?"

	out.append('  <ul>')
	out.append(f'    <li>Total intervals: {total}</li>')
	out.append(f'    <li>no_signal: {no_signal}</li>')
	out.append(f'    <li>completed_low_quality: {low_quality}</li>')
	out.append(f'    <li>completed_normal: {normal}</li>')
	out.append(
		f'    <li>Median accepted_fraction, non-no_signal: '
		f'FWD {_frac(median_fwd_af)} &middot; BWD {_frac(median_bwd_af)} &middot; '
		f'combined {_frac(median_combined_af)}</li>'
	)
	out.append(
		f'    <li>Median FWD/BWD agreement, non-no_signal: '
		f'{_px(median_agreement)}</li>'
	)
	out.append('  </ul>')

	# Per-video rollup: interval count, classification breakdown, median fractions.
	videos_seen = []
	by_video = {}
	for iv in intervals:
		v = iv['video']
		if v not in by_video:
			by_video[v] = []
			videos_seen.append(v)
		by_video[v].append(iv)

	out.append('  <table>')
	out.append(
		'    <tr><th>video</th><th>intervals</th><th>no_signal</th>'
		'<th>low_quality</th><th>normal</th>'
		'<th>median FWD acc</th><th>median BWD acc</th>'
		'<th>median agree</th></tr>'
	)
	for v in videos_seen:
		ivs = by_video[v]
		v_ns = sum(1 for iv in ivs if iv['stats'].classification == 'no_signal')
		v_lq = sum(
			1 for iv in ivs if iv['stats'].classification == 'completed_low_quality'
		)
		v_nm = sum(
			1 for iv in ivs if iv['stats'].classification == 'completed_normal'
		)
		v_non_ns = [iv for iv in ivs if iv['stats'].classification != 'no_signal']
		v_fwd = _median_of_values(
			[iv['stats'].fwd_quality.accepted_fraction for iv in v_non_ns]
		)
		v_bwd = _median_of_values(
			[iv['stats'].bwd_quality.accepted_fraction for iv in v_non_ns]
		)
		v_agree = _median_of_values(
			[iv['stats'].fwd_bwd_agreement_median_px for iv in v_non_ns]
		)
		out.append(
			f'    <tr><td class="video">{v}</td><td>{len(ivs)}</td>'
			f'<td>{v_ns}</td><td>{v_lq}</td><td>{v_nm}</td>'
			f'<td>{_frac(v_fwd)}</td><td>{_frac(v_bwd)}</td>'
			f'<td>{_px(v_agree)}</td></tr>'
		)
	out.append('  </table>')
	out.append('</div>')
	return out


#============================================

def build_walk_html(run_root: pathlib.Path) -> pathlib.Path:
	"""Walk the blob_walk_v2/ output directory and write walk.html.

	Args:
		run_root: Root directory containing the blob_walk_v2 output structure.

	Returns:
		The Path to the written walk.html file.

	Raises:
		RuntimeError: If any sampled PNG is missing.
	"""
	run_root = pathlib.Path(run_root)
	html_output_path = run_root / 'walk.html'

	intervals = []

	# Walk the directory structure: run_root/<video>/<seed_F_L_F_R>/
	for video_dir in sorted(run_root.iterdir()):
		if not video_dir.is_dir():
			continue
		video = video_dir.name
		if video in ('visual', 'heatmaps', 'sanity_pack'):
			continue

		for seed_dir in sorted(video_dir.iterdir()):
			if not seed_dir.is_dir() or not seed_dir.name.startswith('seed_'):
				continue

			# Require interval_summary.csv (skip incomplete interval dirs).
			if not (seed_dir / 'interval_summary.csv').exists():
				continue

			parts = seed_dir.name.split('_')
			if len(parts) < 3:
				continue

			try:
				f_l = int(parts[1])
				f_r = int(parts[2])
			except (ValueError, IndexError):
				continue

			interval_length = f_r - f_l

			# Read FWD and BWD debug logs.
			fwd_csv = seed_dir / 'fwd_verdicts.csv'
			bwd_csv = seed_dir / 'bwd_verdicts.csv'

			fwd_rows = _read_debug_log_csv(fwd_csv)
			bwd_rows = _read_debug_log_csv(bwd_csv)

			# Render FWD and BWD columns.
			fwd_cells = _render_column_cells(
				seed_dir, f_l, interval_length, 'FWD', fwd_rows, run_root,
			)
			bwd_cells = _render_column_cells(
				seed_dir, f_r, interval_length, 'BWD', bwd_rows, run_root,
			)

			# Seed positions for the trajectory plot and quality metrics:
			# FWD bootstrap row at f_l carries left seed pred;
			# BWD bootstrap row at f_r carries right seed.
			left_seed_xy = (0.0, 0.0)
			right_seed_xy = (0.0, 0.0)
			left_seed_cx: float | None = None
			left_seed_cy: float | None = None
			right_seed_cx: float | None = None
			right_seed_cy: float | None = None
			if f_l in fwd_rows:
				lx = walk_util._to_float_or_none(fwd_rows[f_l].get('pred_cx'))
				ly = walk_util._to_float_or_none(fwd_rows[f_l].get('pred_cy'))
				if lx is not None and ly is not None:
					left_seed_xy = (lx, ly)
					left_seed_cx = lx
					left_seed_cy = ly
			if f_r in bwd_rows:
				rx = walk_util._to_float_or_none(bwd_rows[f_r].get('pred_cx'))
				ry = walk_util._to_float_or_none(bwd_rows[f_r].get('pred_cy'))
				if rx is not None and ry is not None:
					right_seed_xy = (rx, ry)
					right_seed_cx = rx
					right_seed_cy = ry

			# Compute summary stats (quality metrics need seed positions).
			stats = _compute_interval_stats(
				fwd_rows, bwd_rows, f_l, f_r,
				right_seed_cx=right_seed_cx,
				right_seed_cy=right_seed_cy,
				left_seed_cx=left_seed_cx,
				left_seed_cy=left_seed_cy,
			)
			# Render trajectory PNG and store relative path (or None if no data).
			trajectory_png_path = seed_dir / 'trajectory.png'
			trajectory_written = _render_trajectory_png(
				fwd_rows, bwd_rows, left_seed_xy, right_seed_xy,
				trajectory_png_path,
			)
			if trajectory_written:
				trajectory_png_rel = trajectory_png_path.relative_to(run_root).as_posix()
			else:
				trajectory_png_rel = None

			intervals.append({
				'video': video,
				'f_l': f_l,
				'f_r': f_r,
				'interval_length': interval_length,
				'fwd_cells': fwd_cells,
				'bwd_cells': bwd_cells,
				'stats': stats,
				'trajectory_png_rel': trajectory_png_rel,
			})

	# Build HTML.
	out = []
	out.append('<!DOCTYPE html>')
	out.append('<html lang="en">')
	out.append('<head>')
	out.append('<meta charset="UTF-8">')
	out.append('<title>Blob walk v2 per-interval (power-of-2 + dense-fill)</title>')
	out.append('<style>')
	out.append('body { font-family: monospace; background: #1a1a1a; color: #ddd; margin: 20px; max-width: 2400px; }')
	out.append('h1 { color: #fff; border-bottom: 2px solid #555; padding-bottom: 8px; }')
	out.append('h2 { color: #ffd; margin-top: 24px; font-size: 1.05em; }')
	out.append('.summary { background: #2a2a2a; padding: 12px; border-left: 4px solid #888; margin: 12px 0; }')
	out.append('.quality-summary { background: #2a2a2a; padding: 12px; border-left: 4px solid #6c6; margin: 12px 0; }')
	out.append('.quality-summary table { border-collapse: collapse; margin: 8px 0; }')
	out.append('.quality-summary th, .quality-summary td { border: 1px solid #555; padding: 3px 8px; text-align: right; }')
	out.append('.quality-summary th { color: #fff; }')
	out.append('.quality-summary td.video { text-align: left; color: #cce; }')
	out.append('.legend { background: #2a2a2a; padding: 12px; border-left: 4px solid #ff0; margin: 12px 0; }')
	out.append('.legend ul { margin: 4px 0; padding-left: 20px; }')
	out.append('.legend li { margin: 3px 0; }')
	out.append('.legend pre { background: #1a1a1a; padding: 8px; border: 1px solid #555; overflow-x: auto; }')
	out.append('.swatch { display: inline-block; width: 14px; height: 14px; vertical-align: middle; border: 1px solid #555; margin-right: 4px; }')
	out.append('.interval { background: #232323; padding: 14px; margin: 18px 0; border: 1px solid #444; }')
	out.append('.interval-summary { font-size: 0.9em; color: #aaa; margin-bottom: 12px; padding: 8px; background: #1a1a1a; border-left: 2px solid #666; }')
	out.append('.trajectory { max-width: 600px; height: auto; border: 1px solid #444; margin: 8px 0; display: block; }')
	out.append('.columns { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }')
	out.append('.col-label { font-weight: bold; color: #fff; margin-bottom: 6px; }')
	out.append('.col-strip { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; }')
	out.append('.cell { background: #1a1a1a; padding: 4px; border-width: 3px; border-style: solid; border-color: #666; }')
	out.append('.cell img { width: 100%; height: auto; display: block; cursor: pointer; }')
	out.append('.cell .no-tile { background: #2a2a2a; color: #666; text-align: center; padding: 30px 4px; font-size: 0.8em; font-style: italic; }')
	out.append('.cell .caption { font-size: 0.75em; color: #aaa; padding: 4px 2px 0; text-align: center; }')
	out.append('.cell .status-label { font-size: 0.72em; padding: 2px; text-align: center; font-weight: bold; }')
	out.append('.cell a { text-decoration: none; }')
	out.append('code { background: #333; padding: 1px 4px; }')
	out.append('</style>')
	out.append('</head>')
	out.append('<body>')

	out.append('<h1>Blob walk v2 per-interval (power-of-2 + dense-fill)</h1>')

	# Legend at TOP
	out.append('<div class="legend">')
	out.append('  <strong>Status border colors:</strong>')
	out.append('  <ul>')
	for st, col in sorted(_STATUS_BORDER_COLORS.items()):
		out.append(f'    <li><span class="swatch" style="background: {col};"></span>{st}</li>')
	out.append('  </ul>')
	out.append('  <strong>PNG-baked overlays (drawn directly into each tile PNG):</strong>')
	out.append('  <ul>')
	out.append('    <li><span class="swatch" style="background: #FFD000;"></span>Winner (yellow ellipse)</li>')
	out.append('    <li><span class="swatch" style="background: #06B6D4;"></span>Corridor non-winner (cyan ellipse)</li>')
	out.append('    <li><span class="swatch" style="background: #808080;"></span>Raw outside corridor (gray ellipse)</li>')
	out.append('    <li><span class="swatch" style="background: #FF6464;"></span>Rejected winner (light red ellipse)</li>')
	out.append('    <li><span class="swatch" style="background: #FF00FF;"></span>Prior position (magenta +)</li>')
	out.append('    <li><span class="swatch" style="background: #FBBF24;"></span>Acceptance box (yellow dashed rect)</li>')
	out.append('    <li><span class="swatch" style="background: #FFFFFF;"></span>Audit disagreement (white open square)</li>')
	out.append(f'    <li><span class="swatch" style="background: {_WALK_PALETTE["velocity_vector"]};"></span>Velocity vector (arrow, M1 schema)</li>')
	out.append(f'    <li><span class="swatch" style="background: {_WALK_PALETTE["allowed_jump_circle"]};"></span>Allowed-jump circle (dashed, M1 schema)</li>')
	out.append(f'    <li><span class="swatch" style="background: {_WALK_PALETTE["residual_accept"]};"></span>Residual line, accepted (green)</li>')
	out.append(f'    <li><span class="swatch" style="background: {_WALK_PALETTE["residual_reject"]};"></span>Residual line, rejected (red)</li>')
	out.append('  </ul>')
	out.append('  <strong>Per-interval trajectory PNG:</strong>')
	out.append('  <ul>')
	out.append(f'    <li><span class="swatch" style="background: {_WALK_PALETTE["trajectory_fwd"]};"></span>FWD accepted positions</li>')
	out.append(f'    <li><span class="swatch" style="background: {_WALK_PALETTE["trajectory_bwd"]};"></span>BWD accepted positions</li>')
	out.append(f'    <li><span class="swatch" style="background: {_WALK_PALETTE["seed_marker"]};"></span>Seed markers (filled circles)</li>')
	out.append('  </ul>')
	out.append('  <strong>Motion gate constants (from walk_motion_gate.py):</strong>')
	out.append('  <ul>')
	out.append(f'    <li>MAX_RUNNER_SPEED_W_PER_S = {MAX_RUNNER_SPEED_W_PER_S} (W/s; 20 mph upper bound at 12 inch torso width)</li>')
	out.append(f'    <li>MIN_RUNNER_SPEED_W_PER_S = {MIN_RUNNER_SPEED_W_PER_S} (W/s; 5 mph lower bound)</li>')
	out.append(f'    <li>BOOTSTRAP_UNCERTAINTY_W = {BOOTSTRAP_UNCERTAINTY_W} (W; fixed localization slack)</li>')
	out.append(f'    <li>per-frame derivation: MAX_RUNNER_SPEED_W_PER_S / source_fps (30fps={MAX_RUNNER_SPEED_W_PER_S/30:.3f}, 60fps={MAX_RUNNER_SPEED_W_PER_S/60:.3f}, 120fps={MAX_RUNNER_SPEED_W_PER_S/120:.3f} W/frame)</li>')
	out.append(f'    <li>ABSOLUTE_MAX_JUMP_W = {ABSOLUTE_MAX_JUMP_W}</li>')
	out.append(f'    <li>VELOCITY_TOLERANCE = {VELOCITY_TOLERANCE}</li>')
	out.append(f'    <li>DT_GATE_CAP = {DT_GATE_CAP}</li>')
	out.append(f'    <li>MEASUREMENT_ALLOWANCE_W = {MEASUREMENT_ALLOWANCE_W}</li>')
	out.append('  </ul>')
	out.append('  <strong>Per-tile caption fields:</strong>')
	out.append('  <ul>')
	out.append('    <li><code>frame N</code> - absolute frame index</li>')
	out.append('    <li><code>status</code> - walker state (accepted, rejected_motion_gate, after_walk_terminated, etc.)</li>')
	out.append('    <li><code>stop_reason</code> - termination reason (if non-empty)</li>')
	out.append('    <li><code>dt</code> - frames since last accepted position</li>')
	out.append('    <li><code>jump X.XX / Y.YY W</code> - actual_jump / allowed_jump as torso-width fractions</li>')
	out.append('    <li><code>conf X.XX</code> - observer confidence score</li>')
	out.append('    <li><code>winner: &lt;mode&gt;</code> - winner resolution (production_winner or audit_winner)</li>')
	out.append('    <li><code>rule=&lt;name&gt;</code> - audit rule on mode disagreement (center_of_mass, strongest_blob, body_position)</li>')
	out.append('  </ul>')
	out.append('  <strong>Real-world sanity check:</strong>')
	out.append('  <pre>')
	for line in REAL_WORLD_SANITY_COMMENT.split('\n'):
		out.append(line)
	out.append('  </pre>')
	out.append('</div>')

	total_cells = sum(len(iv['fwd_cells']) + len(iv['bwd_cells']) for iv in intervals)
	out.append('<div class="summary">')
	out.append(f'  <strong>Intervals</strong>: {len(intervals)}.')
	out.append(f'  <strong>Sampled frames</strong>: {total_cells}.')
	out.append('  <br>Sampling: power-of-2 offsets {0, 1, 2, 4, 8, 16, ...} up to interval length.')
	out.append('  PLUS dense-fill rendering in failure windows: when the walker dies short of')
	out.append('  the neighbor seed, the offsets between the stop row and the next power-of-2')
	out.append('  boundary render as consecutive in-between tiles so the user can step through')
	out.append('  the failure transition frame-by-frame.')
	out.append('  <br>Border color encodes status. Status label names the stop_reason.')
	out.append('  Missing PNG aborts the build LOUDLY.')
	out.append('  <br>Click any thumbnail to open the full PNG in a new tab.')
	out.append('</div>')

	# Corpus-level quality summary (folded in from the former score_corpus_quality
	# tool: classification counts, median accepted_fraction, FWD/BWD agreement).
	out.extend(_build_quality_summary_html(intervals))

	# Per-interval sections
	# Interval summary formatters -- defined once, used inside each interval block.
	def _fmt_frac(f: float | None, reason: str | None = None) -> str:
		# Returns a formatted percentage when defined; when undefined (None),
		# returns the reason token instead of a bare "?" per the metric contract.
		if f is not None:
			return f"{f:.2%}"
		return reason if reason is not None else "undefined"

	def _fmt_disp(d: float | None, reason: str | None = None) -> str:
		# Returns a formatted pixel displacement when defined; when undefined,
		# returns the reason token instead of a bare "?".
		if d is not None:
			return f"{d:.1f}px"
		return reason if reason is not None else "undefined"

	def _fmt_px(d: float | None) -> str:
		return f"{d:.1f}px" if d is not None else "undefined"

	# _fmt_jump appends " W" unit only when the value is numeric; reason tokens
	# (e.g. "no_jump_rows") are emitted as-is without a stray " W" suffix.
	def _fmt_jump(val: str) -> str:
		try:
			float(val)
			return f"{val} W"
		except (ValueError, TypeError):
			return val

	# _fmt_streak: when accepted_fraction_reason is 'no_walkable_frames' the
	# streak value is a meaningless 0 (no frames were walked at all); print the
	# reason token instead so the reader is not misled.
	def _fmt_streak(streak: int, reason: str | None) -> str:
		if reason == 'no_walkable_frames':
			return reason
		return str(streak)

	for iv in intervals:
		video = iv['video']
		f_l = iv['f_l']
		f_r = iv['f_r']
		length = iv['interval_length']
		stats = iv['stats']

		out.append('<div class="interval">')
		out.append(f'  <h2>{video} &middot; seed {f_l} - {f_r} (len {length} frames)</h2>')

		fq = stats.fwd_quality
		bq = stats.bwd_quality
		summary_parts = [
			f"FWD walk-length={stats.fwd_length}",
			f"BWD walk-length={stats.bwd_length}",
			f"gap={stats.gap}",
			f"FWD stop_reason={stats.fwd_stop_reason}",
			f"BWD stop_reason={stats.bwd_stop_reason}",
			f"max actual_jump={_fmt_jump(stats.max_actual_jump_w)}",
			f"median actual_jump={_fmt_jump(stats.median_actual_jump_w)}",
			f"mode_disagreement_count={stats.mode_disagreement_count}",
		]
		out.append(f'  <div class="interval-summary">{" &middot; ".join(summary_parts)}</div>')
		# Quality metrics row (FWD and BWD side-by-side).
		# _fmt_frac / _fmt_disp / _fmt_streak print a reason token when the value is
		# None or undefined (see helpers defined above the loop).
		quality_parts = [
			f"FWD accepted_fraction={_fmt_frac(fq.accepted_fraction, fq.accepted_fraction_reason)}",
			f"FWD longest_no_accept_streak={_fmt_streak(fq.longest_no_accept_streak, fq.accepted_fraction_reason)}",
			f"FWD final_disp_to_neighbor={_fmt_disp(fq.final_displacement_to_neighbor_px, fq.final_displacement_reason)}",
			f"BWD accepted_fraction={_fmt_frac(bq.accepted_fraction, bq.accepted_fraction_reason)}",
			f"BWD longest_no_accept_streak={_fmt_streak(bq.longest_no_accept_streak, bq.accepted_fraction_reason)}",
			f"BWD final_disp_to_neighbor={_fmt_disp(bq.final_displacement_to_neighbor_px, bq.final_displacement_reason)}",
		]
		out.append(f'  <div class="interval-summary" style="color: #cce;">{" &middot; ".join(quality_parts)}</div>')
		# Corpus-quality row: classification + FWD/BWD agreement (folded in from
		# the former score_corpus_quality tool).
		corpus_quality_parts = [
			f"classification={stats.classification}",
			f"FWD/BWD agreement (median)={_fmt_px(stats.fwd_bwd_agreement_median_px)}",
		]
		out.append(
			f'  <div class="interval-summary" style="color: #ada;">'
			f'{" &middot; ".join(corpus_quality_parts)}</div>'
		)

		# Trajectory PNG (one per interval, FWD + BWD accepted positions).
		if iv.get('trajectory_png_rel'):
			out.append(
				f'  <img class="trajectory" src="{iv["trajectory_png_rel"]}" '
				f'alt="trajectory" loading="lazy">'
			)

		out.append('  <div class="columns">')

		def _render_cells(cells):
			out_local = []
			for cell in cells:
				border_color = _STATUS_BORDER_COLORS.get(cell['status'], '#666666')
				status_text_color = border_color
				out_local.append(f'        <div class="cell" style="border-color: {border_color};">')
				if cell['rel_path']:
					out_local.append(
						f'          <a href="{cell["rel_path"]}" target="_blank">'
						f'<img src="{cell["rel_path"]}" alt="" loading="lazy"></a>'
					)
				else:
					out_local.append('          <div class="no-tile">(no tile)</div>')
				out_local.append(
					f'          <div class="status-label" style="color: {status_text_color};">'
					f'{cell["status"]}</div>'
				)
				out_local.append(f'          <div class="caption">{cell["caption"]}</div>')
				out_local.append('        </div>')
			return out_local

		# FWD column
		out.append('    <div>')
		out.append(f'      <div class="col-label">FWD walk from seed {f_l} (offset = frame - {f_l})</div>')
		out.append('      <div class="col-strip">')
		out.extend(_render_cells(iv['fwd_cells']))
		out.append('      </div>')
		out.append('    </div>')

		# BWD column
		out.append('    <div>')
		out.append(f'      <div class="col-label">BWD walk from seed {f_r} (offset = {f_r} - frame)</div>')
		out.append('      <div class="col-strip">')
		out.extend(_render_cells(iv['bwd_cells']))
		out.append('      </div>')
		out.append('    </div>')

		out.append('  </div>')
		out.append('</div>')

	out.append('</body>')
	out.append('</html>')

	content = '\n'.join(out)
	html_output_path.write_text(content, encoding='utf-8')
	return html_output_path


if __name__ == '__main__':
	path = build_walk_html(pathlib.Path('blob_walk_v2'))
	print(f'wrote {path}')
