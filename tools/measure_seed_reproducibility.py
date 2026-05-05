#!/usr/bin/env python3
"""Measure human-seed reproducibility variance from pre-race-start seeds.

Per contract C4, the pre-race-start frames are stationary: the camera is
fixed and the runner has not yet moved. Any variation across seeds in
[0, race_start_frame) is human seed reproducibility noise, not real
runner motion or scale change. This tool quantifies that noise from the
seeds.json directly; no video file is read.

Per contract C2, metrics are reported in fractional torso units in
addition to pixels. A 1-pixel std on a 30-px torso (3.3 percent) is not
comparable to a 1-pixel std on a 200-px torso (0.5 percent); only the
fractional column is the reproducibility floor.

Usage:
  python tools/measure_seed_reproducibility.py \
      -i tr_config/IMG_3830.track_runner.seeds.json
"""

# Standard Library
import os
import sys
import json
import argparse
import statistics

#============================================
# argparse
#============================================

def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Measure pre-race seed-box reproducibility variance "
		            "from a seeds.json (no video required)."
	)
	src_group = parser.add_mutually_exclusive_group(required=True)
	src_group.add_argument(
		'-i', '--input', dest='input_file',
		help='Path to a single tr_config/*.track_runner.seeds.json file. '
		     'Emits a per-video summary.',
	)
	src_group.add_argument(
		'-d', '--directory', dest='directory',
		help='Directory containing many *.track_runner.seeds.json files '
		     '(typically tr_config/). Emits a single corpus table.',
	)
	parser.add_argument(
		'-r', '--race-start-frame', dest='race_start_frame',
		type=int, default=None,
		help='Override race_start_frame. Default: read from the '
		     'co-located *.track_runner.interval_scores.json '
		     'pre_race_reference.race_start_frame. Only valid with -i.',
	)
	parser.add_argument(
		'-o', '--output', dest='output_file', default=None,
		help='Write the markdown summary to a file in addition to '
		     'stdout. Default: stdout only.',
	)
	args = parser.parse_args()
	return args


#============================================
# data loading
#============================================

def load_seeds_json(path: str) -> dict:
	"""Load and return the parsed seeds.json contents."""
	with open(path) as f:
		data = json.load(f)
	return data


def discover_race_start_frame(seeds_path: str) -> int:
	"""Read race_start_frame from the co-located interval_scores.json.

	The interval_scores.json sits next to the seeds.json with the same
	basename and the suffix .track_runner.interval_scores.json. Its
	pre_race_reference block holds race_start_frame.
	"""
	# build the interval_scores path from the seeds path
	scores_path = seeds_path.replace(
		'.track_runner.seeds.json',
		'.track_runner.interval_scores.json',
	)
	if not os.path.exists(scores_path):
		raise RuntimeError(
			f"Expected interval_scores at {scores_path}; "
			f"pass --race-start-frame to override."
		)
	with open(scores_path) as f:
		scores = json.load(f)
	# pre_race_reference holds the persisted race_start_frame
	prr = scores['pre_race_reference']
	rsf = prr['race_start_frame']
	return int(rsf)


def filter_pre_race_visible_seeds(
	seeds: list, race_start_frame: int,
) -> list:
	"""Return seeds in [0, race_start_frame) with status 'visible'.

	Approximate / not_in_frame seeds are excluded because they do not
	carry a torso-box geometry suitable for reproducibility analysis.
	"""
	pre_race = []
	for s in seeds:
		# frame_index gates pre-race membership
		frame = s['frame_index']
		# only visible seeds carry a real torso_box geometry
		status = s.get('status', 'visible')
		if frame < race_start_frame and status == 'visible':
			pre_race.append(s)
	return pre_race


#============================================
# statistics
#============================================

def per_axis_stats(values: list) -> dict:
	"""Return mean, stdev, min, max, range for a list of floats.

	Returns NaN-style sentinels (None) when n < 2 because stdev is
	undefined for a single sample.
	"""
	n = len(values)
	if n == 0:
		return {'n': 0, 'mean': None, 'stdev': None, 'min': None,
		        'max': None, 'range': None}
	mean = statistics.fmean(values)
	vmin = min(values)
	vmax = max(values)
	vrange = vmax - vmin
	# stdev is only defined for n >= 2
	stdev = statistics.stdev(values) if n >= 2 else None
	stats = {
		'n': n, 'mean': mean, 'stdev': stdev,
		'min': vmin, 'max': vmax, 'range': vrange,
	}
	return stats


def fractional_of_mean(value, mean) -> float:
	"""Return value / mean as a fractional ratio, with zero-mean guard."""
	if mean is None or value is None or mean == 0:
		return None
	frac = float(value) / float(mean)
	return frac


#============================================
# rendering
#============================================

def format_optional(value, fmt: str) -> str:
	"""Format a value with the given fmt, or '-' if None."""
	if value is None:
		return '-'
	return fmt.format(value)


def render_summary_md(
	seeds_path: str, race_start_frame: int,
	pre_race_seeds: list, axis_stats: dict,
) -> str:
	"""Build the markdown summary string."""
	out = ''
	out += f"# Pre-race seed reproducibility: {os.path.basename(seeds_path)}\n\n"
	out += f"`race_start_frame` = {race_start_frame}\n\n"
	out += f"`n_pre_race_visible_seeds` = {len(pre_race_seeds)}\n\n"
	if len(pre_race_seeds) < 2:
		# stdev is undefined; emit a clear note and skip the table
		out += "Fewer than 2 pre-race visible seeds; reproducibility "
		out += "stdev is undefined. Annotate more pre-race frames or "
		out += "use a different video.\n"
		return out
	out += "## Per-axis statistics (pixels)\n\n"
	out += "Pixel statistics are meaningful within this one video "
	out += "(camera and runner stationary across the pre-race range "
	out += "per contract C4) but cannot be compared across videos "
	out += "because runner-pixel scale varies. Use the fractional "
	out += "table for cross-video comparison.\n\n"
	out += "| Axis | n | mean | stdev | min | max | range |\n"
	out += "| --- | --- | --- | --- | --- | --- | --- |\n"
	for axis in ('cx', 'cy', 'w', 'h'):
		stats = axis_stats[axis]
		row = f"| {axis} | {stats['n']} "
		row += f"| {format_optional(stats['mean'], '{:.2f}')} "
		row += f"| {format_optional(stats['stdev'], '{:.3f}')} "
		row += f"| {format_optional(stats['min'], '{:.2f}')} "
		row += f"| {format_optional(stats['max'], '{:.2f}')} "
		row += f"| {format_optional(stats['range'], '{:.2f}')} |\n"
		out += row
	out += "\n## Per-axis statistics (fractional units)\n\n"
	out += "Per contract C2, runner-relative metrics are reported as a "
	out += "fraction of the per-axis mean. Fractional stdev is the "
	out += "human-reproducibility floor: any frame-to-frame variation "
	out += "below this floor is unrecoverable signal noise.\n\n"
	out += "| Axis | stdev/mean | range/mean |\n"
	out += "| --- | --- | --- |\n"
	for axis in ('cx', 'cy', 'w', 'h'):
		stats = axis_stats[axis]
		frac_std = fractional_of_mean(stats['stdev'], stats['mean'])
		frac_rng = fractional_of_mean(stats['range'], stats['mean'])
		out += f"| {axis} "
		out += f"| {format_optional(frac_std, '{:.4f}')} "
		out += f"| {format_optional(frac_rng, '{:.4f}')} |\n"
	out += "\n## Interpretation\n\n"
	# pull the fractional w / h stdev for the one-line summary
	w_frac = fractional_of_mean(
		axis_stats['w']['stdev'], axis_stats['w']['mean'],
	)
	h_frac = fractional_of_mean(
		axis_stats['h']['stdev'], axis_stats['h']['mean'],
	)
	out += "Fractional stdev for `w` and `h` is the per-seed "
	out += "boundary-imprecision floor implied by the pre-race "
	out += "annotations. Crop-input stabilization that reduces "
	out += "frame-to-frame torso h/w jitter below this floor is "
	out += "removing real signal, not noise.\n\n"
	out += "- `w` fractional stdev: "
	out += f"{format_optional(w_frac, '{:.4f}')} "
	out += f"({format_optional(w_frac, '{:.1%}')})\n"
	out += "- `h` fractional stdev: "
	out += f"{format_optional(h_frac, '{:.4f}')} "
	out += f"({format_optional(h_frac, '{:.1%}')})\n"
	return out


#============================================
# per-video computation
#============================================

def compute_axis_stats_for_seeds_file(
	seeds_path: str, race_start_override: int = None,
) -> tuple:
	"""Return (race_start_frame, n_pre_race, axis_stats) for one seeds.json.

	axis_stats is a dict keyed by 'cx', 'cy', 'w', 'h' with per_axis_stats
	dicts as values. n_pre_race is the count of visible seeds in
	[0, race_start_frame).
	"""
	data = load_seeds_json(seeds_path)
	all_seeds = data['seeds']
	if race_start_override is not None:
		race_start_frame = int(race_start_override)
	else:
		race_start_frame = discover_race_start_frame(seeds_path)
	pre_race = filter_pre_race_visible_seeds(all_seeds, race_start_frame)
	cx_vals = []
	cy_vals = []
	w_vals = []
	h_vals = []
	for s in pre_race:
		# torso_box is [x, y, w, h] with x, y at the top-left corner
		x, y, w, h = s['torso_box']
		cx = float(x) + 0.5 * float(w)
		cy = float(y) + 0.5 * float(h)
		cx_vals.append(cx)
		cy_vals.append(cy)
		w_vals.append(float(w))
		h_vals.append(float(h))
	axis_stats = {
		'cx': per_axis_stats(cx_vals),
		'cy': per_axis_stats(cy_vals),
		'w': per_axis_stats(w_vals),
		'h': per_axis_stats(h_vals),
	}
	return race_start_frame, len(pre_race), axis_stats


#============================================
# corpus rendering
#============================================

def render_corpus_md(rows: list) -> str:
	"""Build a corpus-wide markdown table.

	rows is a list of (basename, n_pre_race, axis_stats) tuples sorted
	descending by n_pre_race.
	"""
	out = ''
	out += "# Pre-race seed reproducibility: corpus summary\n\n"
	out += "Per contract C4, pre-race seeds annotate a stationary scene; "
	out += "their variance is human seed reproducibility (per C5). Per "
	out += "C2, fractional columns are the cross-video-comparable measure. "
	out += "Pixel columns are video-internal only.\n\n"
	out += "| video | n_pre | w_mean (px) | h_mean (px) | w stdev (px) "
	out += "| h stdev (px) | w stdev / mean | h stdev / mean |\n"
	out += "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
	for base, n_pre, stats in rows:
		if n_pre < 2:
			out += f"| {base} | {n_pre} | (n<2) | | | | | |\n"
			continue
		w = stats['w']; h = stats['h']
		w_frac = fractional_of_mean(w['stdev'], w['mean'])
		h_frac = fractional_of_mean(h['stdev'], h['mean'])
		out += f"| {base} | {n_pre} "
		out += f"| {format_optional(w['mean'], '{:.2f}')} "
		out += f"| {format_optional(h['mean'], '{:.2f}')} "
		out += f"| {format_optional(w['stdev'], '{:.3f}')} "
		out += f"| {format_optional(h['stdev'], '{:.3f}')} "
		out += f"| {format_optional(w_frac, '{:.1%}')} "
		out += f"| {format_optional(h_frac, '{:.1%}')} |\n"
	return out


#============================================
# main
#============================================

def main() -> None:
	args = parse_args()
	# single-file mode
	if args.input_file is not None:
		if not os.path.exists(args.input_file):
			raise RuntimeError(f"Input not found: {args.input_file}")
		race_start_frame, _n_pre, axis_stats = (
			compute_axis_stats_for_seeds_file(
				args.input_file, args.race_start_frame,
			)
		)
		# re-fetch the filtered pre_race list for the per-seed render path
		data = load_seeds_json(args.input_file)
		pre_race = filter_pre_race_visible_seeds(
			data['seeds'], race_start_frame,
		)
		md = render_summary_md(
			args.input_file, race_start_frame, pre_race, axis_stats,
		)
	else:
		# directory mode: scan and emit a single corpus table
		if args.race_start_frame is not None:
			raise RuntimeError(
				"--race-start-frame is only valid with -i (single file)."
			)
		import glob
		pattern = os.path.join(args.directory, '*.track_runner.seeds.json')
		paths = sorted(glob.glob(pattern))
		if not paths:
			raise RuntimeError(
				f"No *.track_runner.seeds.json under {args.directory}"
			)
		# accumulate per-video rows; tolerate missing interval_scores
		rows = []
		for p in paths:
			base = os.path.basename(p).replace(
				'.track_runner.seeds.json', '',
			)
			# missing interval_scores -> skip with note in stderr
			scores_path = p.replace(
				'.seeds.json', '.interval_scores.json',
			)
			if not os.path.exists(scores_path):
				sys.stderr.write(
					f"skip {base}: no interval_scores.json\n"
				)
				continue
			_rsf, n_pre, stats = compute_axis_stats_for_seeds_file(p)
			rows.append((base, n_pre, stats))
		# sort descending by n_pre so the most reliable rows come first
		rows.sort(key=lambda r: -r[1])
		md = render_corpus_md(rows)
	# emit
	sys.stdout.write(md)
	if args.output_file:
		with open(args.output_file, 'w') as f:
			f.write(md)


if __name__ == '__main__':
	main()
