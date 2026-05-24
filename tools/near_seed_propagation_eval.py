#!/usr/bin/env python3
"""
Evaluate blob detection as seed propagation, not midpoint rescue.

Reframed evaluation per user: blob detection is seed propagation, and the
success metric is near-seed accept rate with graceful decay. Near-seed
failure indicates broken observer/gates; midpoint failure with graceful decay
is acceptable.
"""

import argparse
import csv
import json
import os


def parse_args():
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description="Evaluate blob detection as seed propagation."
	)
	parser.add_argument(
		'-s', '--selection', dest='selection_csv', required=True,
		help='Path to per_video_selection_rebucketed.csv'
	)
	parser.add_argument(
		'-r', '--sweep-root', dest='sweep_root', required=True,
		help='Path to sweep output root (e.g., output_smoke/blob_refine_fix/2026-05-23/m2_sweep)'
	)
	parser.add_argument(
		'-o', '--output-dir', dest='output_dir', required=True,
		help='Output directory for report and data files'
	)
	parser.add_argument(
		'--buckets', dest='buckets', default='trust_0,trust_high,mixed,weak_fair_low',
		help='Comma-separated bucket filter (default: trust_0,trust_high,mixed,weak_fair_low)'
	)
	args = parser.parse_args()
	return args


def load_selection_csv(selection_csv, buckets):
	"""Load and filter selection CSV by bucket."""
	intervals = []
	with open(selection_csv) as f:
		reader = csv.DictReader(f)
		for row in reader:
			if row['bucket'] in buckets and row['sample'] == 'ranking':
				intervals.append({
					'video': row['video'],
					'start_frame': int(row['interval_start']),
					'end_frame': int(row['interval_end']),
					'bucket': row['bucket'],
				})
	return intervals


def load_verdicts_csv(sweep_root, video, start, end):
	"""Load verdicts.csv for an interval."""
	verdict_path = os.path.join(
		sweep_root, video, f'interval_{start}_{end}',
		f'interval_{start}_{end}', 'verdicts.csv'
	)
	if not os.path.exists(verdict_path):
		return []

	verdicts = []
	with open(verdict_path) as f:
		reader = csv.DictReader(f)
		for row in reader:
			verdicts.append(row)
	return verdicts


def compute_distance_from_seed(frame_index, start_frame, end_frame):
	"""Compute minimum distance from either seed."""
	dist_from_start = frame_index - start_frame
	dist_from_end = end_frame - frame_index
	return min(dist_from_start, dist_from_end)


def assign_bin(distance, interval_len):
	"""Assign frame to bin based on distance from seed."""
	if 1 <= distance <= 10:
		return 'bin_near'
	for pct_name, pct_val in [('25pct', 0.25), ('50pct', 0.50), ('75pct', 0.75)]:
		center_idx = round(pct_val * interval_len)
		if center_idx - 2 <= distance <= center_idx + 2:
			return f'bin_{pct_name}'
	return None


def compute_per_bin_stats(verdicts, start_frame, end_frame):
	"""
	Compute per-bin statistics for an interval.
	Returns {pass: {bin_name: {...}}}
	"""
	interval_len = end_frame - start_frame
	bins = {f'bin_{b}': {
		'n_frames': 0,
		'n_accept': 0,
		'n_reject_prox': 0,
		'n_reject_dir': 0,
		'n_reject_path': 0,
		'n_reject_corridor': 0,
		'n_observer_absent': 0,
	} for b in ['near', '25pct', '50pct', '75pct']}

	by_pass = {}

	for row in verdicts:
		frame_idx = int(row['frame_index'])
		pass_name = row['pass']
		gate = row['gate']

		if pass_name not in by_pass:
			by_pass[pass_name] = {k: dict(v) for k, v in bins.items()}

		if frame_idx == start_frame or frame_idx == end_frame:
			continue

		distance = compute_distance_from_seed(frame_idx, start_frame, end_frame)
		bin_name = assign_bin(distance, interval_len)

		if bin_name is None:
			continue

		stats = by_pass[pass_name][bin_name]
		stats['n_frames'] += 1

		if gate == 'accepted':
			stats['n_accept'] += 1
		elif gate == 'rejected':
			lost_at = row.get('lost_at_stage', '')
			if lost_at == 'gate_prox':
				stats['n_reject_prox'] += 1
			elif lost_at == 'gate_dir':
				stats['n_reject_dir'] += 1
			elif lost_at == 'gate_path':
				stats['n_reject_path'] += 1
			elif lost_at == 'no_corridor_blobs':
				stats['n_reject_corridor'] += 1
			else:
				if row.get('n_corridor_blobs', '0') == '0' and row.get('gate') == 'rejected':
					stats['n_reject_corridor'] += 1
		elif gate == 'absent':
			stats['n_observer_absent'] += 1

		if row.get('has_residual') == 'False' and gate == 'rejected':
			stats['n_observer_absent'] += 1

	return by_pass


def compute_accept_rate(stats):
	"""Compute accept rate from stats dict."""
	n_frames = stats['n_frames']
	if n_frames == 0:
		return 0.0
	return stats['n_accept'] / n_frames


def aggregate_per_bucket(all_interval_data):
	"""
	Aggregate per-bucket, per-bin statistics.
	Returns {bucket: {bin: {...aggregated stats...}}}
	"""
	by_bucket = {}
	for bucket in ['trust_0', 'trust_high', 'mixed', 'weak_fair_low']:
		by_bucket[bucket] = {}
		for bin_name in ['bin_near', 'bin_25pct', 'bin_50pct', 'bin_75pct']:
			by_bucket[bucket][bin_name] = {
				'accept_rates': [],
				'failure_modes': {},
			}

	for interval_data in all_interval_data:
		bucket = interval_data['bucket']
		if bucket not in by_bucket:
			continue

		for pass_name in ['FWD', 'BWD']:
			if pass_name not in interval_data['by_pass']:
				continue

			for bin_name in ['bin_near', 'bin_25pct', 'bin_50pct', 'bin_75pct']:
				if bin_name not in interval_data['by_pass'][pass_name]:
					continue

				stats = interval_data['by_pass'][pass_name][bin_name]
				if stats['n_frames'] == 0:
					continue

				accept_rate = compute_accept_rate(stats)
				by_bucket[bucket][bin_name]['accept_rates'].append(accept_rate)

				if bin_name == 'bin_near':
					mode = {
						'prox': stats['n_reject_prox'],
						'dir': stats['n_reject_dir'],
						'path': stats['n_reject_path'],
						'corridor': stats['n_reject_corridor'],
						'observer_absent': stats['n_observer_absent'],
						'accepted': stats['n_accept'],
					}
					for k, v in mode.items():
						if k not in by_bucket[bucket][bin_name]['failure_modes']:
							by_bucket[bucket][bin_name]['failure_modes'][k] = []
						by_bucket[bucket][bin_name]['failure_modes'][k].append(v)

	return by_bucket


def compute_near_seed_summary(aggregated):
	"""
	Compute summary metrics per bucket for bin_near.
	Returns {bucket: {metric: value}}
	"""
	summary = {}
	for bucket in aggregated:
		near_bin = aggregated[bucket]['bin_near']
		summary[bucket] = {
			'accept_rate': 0.0,
			'n_samples': 0,
			'dominant_failure_mode': None,
			'mode_pct': 0.0,
		}

		if len(near_bin['accept_rates']) == 0:
			continue

		summary[bucket]['accept_rate'] = sum(near_bin['accept_rates']) / len(near_bin['accept_rates'])
		summary[bucket]['n_samples'] = len(near_bin['accept_rates'])

		if near_bin['failure_modes']:
			totals = {}
			for mode, counts in near_bin['failure_modes'].items():
				totals[mode] = sum(counts)

			total_rejects = sum(v for k, v in totals.items() if k != 'accepted')
			if total_rejects > 0:
				reject_modes = {k: v for k, v in totals.items() if k != 'accepted'}
				dominant = max(reject_modes, key=reject_modes.get)
				summary[bucket]['dominant_failure_mode'] = dominant
				summary[bucket]['mode_pct'] = reject_modes[dominant] / total_rejects * 100

	return summary


def write_report(aggregated, summary, output_dir):
	"""Write PROPAGATION_REPORT.md."""
	os.makedirs(output_dir, exist_ok=True)
	report_path = os.path.join(output_dir, 'PROPAGATION_REPORT.md')

	with open(report_path, 'w') as f:
		f.write('# Near-seed propagation evaluation\n\n')

		f.write('## Overview\n\n')
		f.write('Blob detection is evaluated as seed propagation, not midpoint rescue. '
			'The primary metric is near-seed accept rate (frames 1-10 from each seed). '
			'See docs/active_plans/active/blob_refine_microscope_phase.md for the reframing rationale.\n\n')

		f.write('## Per-bucket accept-rate table\n\n')
		f.write('| Bucket | bin_near | bin_25pct | bin_50pct | bin_75pct |\n')
		f.write('| --- | --- | --- | --- | --- |\n')
		for bucket in ['trust_0', 'trust_high', 'mixed', 'weak_fair_low']:
			row = [bucket]
			for bin_name in ['bin_near', 'bin_25pct', 'bin_50pct', 'bin_75pct']:
				rates = aggregated[bucket][bin_name]['accept_rates']
				if rates:
					mean_rate = sum(rates) / len(rates)
					row.append(f'{mean_rate:.1%} ({len(rates)})')
				else:
					row.append('N/A')
			f.write('| ' + ' | '.join(row) + ' |\n')

		f.write('\n## Decay curve per bucket\n\n')
		for bucket in ['trust_0', 'trust_high', 'mixed', 'weak_fair_low']:
			f.write(f'### {bucket}\n\n')
			f.write('```\n')
			for bin_name in ['bin_near', 'bin_25pct', 'bin_50pct', 'bin_75pct']:
				rates = aggregated[bucket][bin_name]['accept_rates']
				if rates:
					mean_rate = sum(rates) / len(rates)
					filled = int(mean_rate * 20)
					bar = '#' * filled + '.' * (20 - filled)
					f.write(f'{bin_name:10s} |{bar}| {mean_rate:.1%}\n')
			f.write('```\n\n')

		f.write('## Failure mode breakdown at bin_near per bucket\n\n')
		f.write('| Bucket | prox% | dir% | path% | corridor% | observer_absent% | accepted% |\n')
		f.write('| --- | --- | --- | --- | --- | --- | --- |\n')
		for bucket in ['trust_0', 'trust_high', 'mixed', 'weak_fair_low']:
			near_bin = aggregated[bucket]['bin_near']
			if not near_bin['failure_modes']:
				f.write(f'| {bucket} | N/A | N/A | N/A | N/A | N/A | N/A |\n')
				continue

			total_counts = {}
			for mode, counts in near_bin['failure_modes'].items():
				total_counts[mode] = sum(counts)

			total_frames = sum(total_counts.values())
			if total_frames == 0:
				f.write(f'| {bucket} | N/A | N/A | N/A | N/A | N/A | N/A |\n')
				continue

			row = [bucket]
			for mode in ['prox', 'dir', 'path', 'corridor', 'observer_absent', 'accepted']:
				pct = total_counts.get(mode, 0) / total_frames * 100
				row.append(f'{pct:.0f}%')
			f.write('| ' + ' | '.join(row) + ' |\n')

		f.write('\n## Decision lookup per bucket\n\n')
		f.write('| Bucket | near-seed rate | dominant mode | decision |\n')
		f.write('| --- | --- | --- | --- |\n')
		for bucket in ['trust_0', 'trust_high', 'mixed', 'weak_fair_low']:
			near_rate = summary[bucket]['accept_rate']
			dominant = summary[bucket]['dominant_failure_mode'] or 'none'
			mode_pct = summary[bucket]['mode_pct']

			decision = 'inconclusive'
			if near_rate < 0.5 and dominant != 'none' and mode_pct > 60:
				decision = 'observer/gates broken'
			elif near_rate >= 0.75:
				decision = 'design works'
			elif near_rate >= 0.5 and near_rate < 0.75:
				decision = 'propagation issue'

			f.write(f'| {bucket} | {near_rate:.1%} | {dominant} ({mode_pct:.0f}%) | {decision} |\n')

		f.write('\n## Per-interval sample (10 random intervals)\n\n')
		f.write('Sample of 10 intervals across all buckets showing near-seed accept count and dominant failure mode.\n\n')
		f.write('(See propagation_data.json for complete per-interval details.)\n')

	print(f'Report written to {report_path}')


def write_data_json(aggregated, all_interval_data, output_dir):
	"""Write propagation_data.json with raw statistics."""
	os.makedirs(output_dir, exist_ok=True)
	data_path = os.path.join(output_dir, 'propagation_data.json')

	data = {
		'per_bucket': {},
		'per_interval_sample': all_interval_data[:10],
	}

	for bucket in aggregated:
		data['per_bucket'][bucket] = {}
		for bin_name in aggregated[bucket]:
			rates = aggregated[bucket][bin_name]['accept_rates']
			data['per_bucket'][bucket][bin_name] = {
				'accept_rates': rates,
				'mean_accept_rate': sum(rates) / len(rates) if rates else 0.0,
				'n_intervals': len(rates),
			}

	with open(data_path, 'w') as f:
		json.dump(data, f, indent=2)

	print(f'Data JSON written to {data_path}')


def main():
	"""Main entry point."""
	args = parse_args()

	buckets = args.buckets.split(',')

	intervals = load_selection_csv(args.selection_csv, buckets)
	print(f'Loaded {len(intervals)} ranking intervals in buckets {buckets}')

	all_interval_data = []

	for interval in intervals:
		verdicts = load_verdicts_csv(
			args.sweep_root, interval['video'],
			interval['start_frame'], interval['end_frame']
		)
		if not verdicts:
			continue

		by_pass = compute_per_bin_stats(
			verdicts, interval['start_frame'], interval['end_frame']
		)

		interval_data = {
			'video': interval['video'],
			'start_frame': interval['start_frame'],
			'end_frame': interval['end_frame'],
			'bucket': interval['bucket'],
			'by_pass': by_pass,
		}
		all_interval_data.append(interval_data)

	print(f'Processed {len(all_interval_data)} intervals with verdicts')

	aggregated = aggregate_per_bucket(all_interval_data)
	summary = compute_near_seed_summary(aggregated)

	write_report(aggregated, summary, args.output_dir)
	write_data_json(aggregated, all_interval_data, args.output_dir)

	print('\nPer-bucket near-seed accept rates:')
	for bucket in ['trust_0', 'trust_high', 'mixed', 'weak_fair_low']:
		rate = summary[bucket]['accept_rate']
		print(f'  {bucket}: {rate:.1%}')


if __name__ == '__main__':
	main()
