#!/usr/bin/env python3
"""Compare M4 post-fix blob acceptance against M1 baseline and M3 simulation.

Reads:
- M1 baseline from m2_sweep/per_video_selection_rebucketed.csv
- M3 simulated from counterfactual/by_policy.csv (requires winning policy from RANKING.md)
- M4 post-fix from validation/{pass1,pass2}/**/verdicts.csv files

Emits:
- validation.md (formatted summary and per-interval table)
- validation.csv (machine-readable companion)

Contract: must check sentinel and refuse cleanly if pass1 outputs absent.
"""

# Standard Library
import os
import sys
import csv
import re
from pathlib import Path
from collections import defaultdict

#============================================

def get_repo_root() -> str:
	"""Return the repository root via git rev-parse --show-toplevel."""
	import subprocess
	try:
		result = subprocess.run(
			["git", "rev-parse", "--show-toplevel"],
			capture_output=True,
			text=True,
			check=True
		)
		return result.stdout.strip()
	except (subprocess.CalledProcessError, FileNotFoundError):
		return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#============================================

def read_baseline_csv(baseline_path: str) -> dict:
	"""Read M1 baseline from per_video_selection_rebucketed.csv.

	Key: (video, interval_start, interval_end)
	Value: {'actual_accept_fwd': int, 'actual_accept_bwd': int, 'sample': str}
	"""
	result = {}
	try:
		with open(baseline_path, 'r') as f:
			reader = csv.DictReader(f)
			for row in reader:
				video = row.get('video', '').strip()
				interval_start = row.get('interval_start', '').strip()
				interval_end = row.get('interval_end', '').strip()
				sample = row.get('sample', '').strip()

				if not video or not interval_start or not interval_end:
					continue

				# Handle empty/pending values
				actual_fwd_str = row.get('actual_accept_fwd', '').strip()
				actual_bwd_str = row.get('actual_accept_bwd', '').strip()

				try:
					actual_fwd = int(actual_fwd_str) if actual_fwd_str else None
					actual_bwd = int(actual_bwd_str) if actual_bwd_str else None
				except ValueError:
					actual_fwd = None
					actual_bwd = None

				key = (video, interval_start, interval_end)
				result[key] = {
					'actual_accept_fwd': actual_fwd,
					'actual_accept_bwd': actual_bwd,
					'sample': sample
				}
	except FileNotFoundError:
		sys.stderr.write(f"ERROR: Baseline CSV not found: {baseline_path}\n")
		sys.exit(1)

	return result

#============================================

def read_ranking_md(ranking_path: str) -> str:
	"""Parse RANKING.md to find winning policy name.

	Looks for line like: Winner: <policy>
	Returns the policy name or raises if not found.
	"""
	try:
		with open(ranking_path, 'r') as f:
			content = f.read()
			# Look for "Winner: X" pattern
			match = re.search(r'Winner:\s*([A-Z])\b', content)
			if match:
				return match.group(1)
	except FileNotFoundError:
		pass

	return None

#============================================

def read_by_policy_csv(policy_path: str, winning_policy: str) -> dict:
	"""Read M3 simulated by_policy.csv, filter to winning policy.

	Key: (video, interval_start, interval_end)
	Value: {'simulated_accept_fwd': float, 'simulated_accept_bwd': float}
	"""
	result = {}
	try:
		with open(policy_path, 'r') as f:
			reader = csv.DictReader(f)
			for row in reader:
				video = row.get('video', '').strip()
				interval_start = row.get('interval_start', '').strip()
				interval_end = row.get('interval_end', '').strip()
				policy = row.get('policy', '').strip()

				if not video or not interval_start or not interval_end:
					continue

				if policy != winning_policy:
					continue

				try:
					simulated_fwd = float(row.get('blob_accept_fwd', 0) or 0)
					simulated_bwd = float(row.get('blob_accept_bwd', 0) or 0)
				except (ValueError, TypeError):
					simulated_fwd = 0.0
					simulated_bwd = 0.0

				key = (video, interval_start, interval_end)
				result[key] = {
					'simulated_accept_fwd': simulated_fwd,
					'simulated_accept_bwd': simulated_bwd
				}
	except FileNotFoundError:
		sys.stderr.write(f"WARNING: by_policy.csv not found: {policy_path}\n")
		return {}

	return result

#============================================

def compute_accept_from_verdicts(verdicts_path: str) -> dict:
	"""Compute blob_accept rates from verdicts.csv.

	verdicts.csv has: frame_index, pass, endpoint, verdict, ... (ACCEPT or other)
	Computes percentage: (# ACCEPT / # non-endpoint) * 100 per pass

	Returns: {'fwd_accept': float, 'bwd_accept': float}
	"""
	accept_counts = {'FWD': 0, 'BWD': 0}
	total_counts = {'FWD': 0, 'BWD': 0}

	try:
		with open(verdicts_path, 'r') as f:
			reader = csv.DictReader(f)
			for row in reader:
				pass_name = row.get('pass', '').strip()
				endpoint = row.get('endpoint', '').strip()
				verdict = row.get('verdict', '').strip()

				if pass_name not in accept_counts:
					continue

				# Skip endpoints
				if endpoint and endpoint.lower() in ['true', '1', 'yes']:
					continue

				total_counts[pass_name] += 1
				if verdict and verdict.upper() == 'ACCEPT':
					accept_counts[pass_name] += 1
	except FileNotFoundError:
		return None

	# Compute percentages
	fwd_pct = 100.0 * accept_counts['FWD'] / total_counts['FWD'] if total_counts['FWD'] > 0 else 0.0
	bwd_pct = 100.0 * accept_counts['BWD'] / total_counts['BWD'] if total_counts['BWD'] > 0 else 0.0

	return {'fwd_accept': fwd_pct, 'bwd_accept': bwd_pct}

#============================================

def find_verdicts_files(validation_root: str) -> list:
	"""Recursively find all verdicts.csv files under validation_root.

	Returns list of absolute paths.
	"""
	files = []
	for root, dirs, filenames in os.walk(validation_root):
		for fname in filenames:
			if fname == 'verdicts.csv':
				files.append(os.path.join(root, fname))
	return files

#============================================

def extract_interval_from_path(verdicts_path: str) -> tuple:
	"""Extract (video, interval_start, interval_end) from verdicts.csv path.

	Expected path format:
	.../<video>/interval_<start>_<end>/verdicts.csv

	Returns (video, str(start), str(end)) or None
	"""
	parts = Path(verdicts_path).parts
	# Find 'interval_' substring
	for i, part in enumerate(parts):
		if part.startswith('interval_'):
			interval_part = part
			# video is the previous directory
			if i > 0:
				video = parts[i - 1]
				# Parse interval_<start>_<end>
				match = re.match(r'interval_(\d+)_(\d+)', interval_part)
				if match:
					return (video, match.group(1), match.group(2))

	return None

#============================================

def main():
	"""Main driver."""
	repo_root = get_repo_root()
	validation_root = os.path.join(repo_root, 'output_smoke', 'blob_refine_fix', '2026-05-23', 'validation')

	baseline_path = os.path.join(repo_root, 'output_smoke', 'blob_refine_fix', '2026-05-23', 'm2_sweep', 'per_video_selection_rebucketed.csv')
	ranking_path = os.path.join(repo_root, 'output_smoke', 'blob_refine_fix', '2026-05-23', 'counterfactual', 'RANKING.md')
	by_policy_path = os.path.join(repo_root, 'output_smoke', 'blob_refine_fix', '2026-05-23', 'counterfactual', 'by_policy.csv')

	# Check if pass1 outputs exist
	pass1_root = os.path.join(validation_root, 'pass1')
	verdicts_files = find_verdicts_files(pass1_root)
	if not verdicts_files:
		sys.stderr.write(f"ABORT: No validation/pass1/**/verdicts.csv files found. Run Pass 1 first.\n")
		sys.exit(1)

	# Read all three data sources
	baseline = read_baseline_csv(baseline_path)
	print(f"Baseline: {len(baseline)} intervals loaded from m2_sweep/per_video_selection_rebucketed.csv", file=sys.stderr)

	winning_policy = read_ranking_md(ranking_path)
	if winning_policy:
		print(f"Winning policy from RANKING.md: {winning_policy}", file=sys.stderr)
		simulated = read_by_policy_csv(by_policy_path, winning_policy)
		print(f"Simulated: {len(simulated)} intervals loaded from counterfactual/by_policy.csv", file=sys.stderr)
	else:
		print(f"WARNING: RANKING.md not found or no Winner line; skipping M3 simulation comparison", file=sys.stderr)
		simulated = {}

	# Process all verdicts.csv files
	post_fix = {}
	for verdicts_path in verdicts_files:
		interval_key = extract_interval_from_path(verdicts_path)
		if not interval_key:
			print(f"WARNING: Could not extract interval from {verdicts_path}", file=sys.stderr)
			continue

		video, start, end = interval_key
		key = (video, start, end)

		accept_rates = compute_accept_from_verdicts(verdicts_path)
		if accept_rates:
			post_fix[key] = {
				'post_fix_accept_fwd': accept_rates['fwd_accept'],
				'post_fix_accept_bwd': accept_rates['bwd_accept']
			}

	print(f"Post-fix: {len(post_fix)} intervals with verdicts.csv", file=sys.stderr)

	# Build comparison table: collect all intervals present in at least one source
	all_keys = set(baseline.keys()) | set(simulated.keys()) | set(post_fix.keys())
	comparison_rows = []

	for key in sorted(all_keys):
		video, start, end = key
		row_data = {
			'video': video,
			'interval_start': start,
			'interval_end': end,
			'sample': baseline.get(key, {}).get('sample', 'unknown'),
		}

		# M1 baseline
		baseline_data = baseline.get(key, {})
		row_data['m1_baseline_fwd'] = baseline_data.get('actual_accept_fwd', '')
		row_data['m1_baseline_bwd'] = baseline_data.get('actual_accept_bwd', '')

		# M3 simulated
		simulated_data = simulated.get(key, {})
		row_data['m3_simulated_fwd'] = simulated_data.get('simulated_accept_fwd', '')
		row_data['m3_simulated_bwd'] = simulated_data.get('simulated_accept_bwd', '')

		# M4 post-fix
		postfix_data = post_fix.get(key, {})
		row_data['m4_post_fix_fwd'] = postfix_data.get('post_fix_accept_fwd', '')
		row_data['m4_post_fix_bwd'] = postfix_data.get('post_fix_accept_bwd', '')

		# Compute deltas
		if (isinstance(row_data['m3_simulated_fwd'], (int, float)) and
			isinstance(row_data['m4_post_fix_fwd'], (int, float))):
			row_data['delta_actual_vs_sim_fwd'] = row_data['m4_post_fix_fwd'] - row_data['m3_simulated_fwd']
			row_data['delta_actual_vs_sim_bwd'] = row_data['m4_post_fix_bwd'] - row_data['m3_simulated_bwd']
		else:
			row_data['delta_actual_vs_sim_fwd'] = ''
			row_data['delta_actual_vs_sim_bwd'] = ''

		if (isinstance(row_data['m1_baseline_fwd'], (int, float)) and
			isinstance(row_data['m4_post_fix_fwd'], (int, float))):
			row_data['delta_post_vs_baseline_fwd'] = row_data['m4_post_fix_fwd'] - row_data['m1_baseline_fwd']
			row_data['delta_post_vs_baseline_bwd'] = row_data['m4_post_fix_bwd'] - row_data['m1_baseline_bwd']
		else:
			row_data['delta_post_vs_baseline_fwd'] = ''
			row_data['delta_post_vs_baseline_bwd'] = ''

		comparison_rows.append(row_data)

	# Write CSV output
	csv_output_path = os.path.join(validation_root, 'validation.csv')
	os.makedirs(validation_root, exist_ok=True)

	csv_fieldnames = [
		'video', 'interval_start', 'interval_end', 'sample',
		'm1_baseline_fwd', 'm1_baseline_bwd',
		'm3_simulated_fwd', 'm3_simulated_bwd',
		'm4_post_fix_fwd', 'm4_post_fix_bwd',
		'delta_actual_vs_sim_fwd', 'delta_actual_vs_sim_bwd',
		'delta_post_vs_baseline_fwd', 'delta_post_vs_baseline_bwd'
	]

	with open(csv_output_path, 'w', newline='') as f:
		writer = csv.DictWriter(f, fieldnames=csv_fieldnames)
		writer.writeheader()
		writer.writerows(comparison_rows)

	print(f"CSV written to: {csv_output_path}", file=sys.stderr)

	# Write markdown output
	md_output_path = os.path.join(validation_root, 'validation.md')

	with open(md_output_path, 'w') as f:
		f.write('# M4 Post-Fix Validation Report\n\n')
		f.write('## Summary\n\n')
		f.write(f'- M1 baseline intervals: {len(baseline)}\n')
		f.write(f'- M3 simulated intervals: {len(simulated)} (winning policy: {winning_policy or "unknown"})\n')
		f.write(f'- M4 post-fix intervals: {len(post_fix)}\n')
		f.write(f'- Total intervals in comparison: {len(comparison_rows)}\n\n')

		# Verdict AC-4b / 4b'
		f.write('## Validation Verdict\n\n')
		f.write('**AC-4b/4b\' criteria:**\n')
		f.write('- PASS iff all per-interval deltas within +/- 5 percentage points\n')
		f.write('- PASS iff no single video aggregate exceeds 10 percentage points\n\n')

		# Compute verdict
		pass_4b = True
		max_delta_per_interval_fwd = 0.0
		max_delta_per_interval_bwd = 0.0
		per_video_deltas = defaultdict(lambda: {'fwd': [], 'bwd': []})

		for row in comparison_rows:
			if row['delta_actual_vs_sim_fwd'] != '':
				delta = abs(row['delta_actual_vs_sim_fwd'])
				max_delta_per_interval_fwd = max(max_delta_per_interval_fwd, delta)
				per_video_deltas[row['video']]['fwd'].append(delta)
				if delta > 5.0:
					pass_4b = False

			if row['delta_actual_vs_sim_bwd'] != '':
				delta = abs(row['delta_actual_vs_sim_bwd'])
				max_delta_per_interval_bwd = max(max_delta_per_interval_bwd, delta)
				per_video_deltas[row['video']]['bwd'].append(delta)
				if delta > 5.0:
					pass_4b = False

		# Check per-video aggregates
		for video, deltas in per_video_deltas.items():
			if deltas['fwd']:
				avg_fwd = sum(deltas['fwd']) / len(deltas['fwd'])
				if avg_fwd > 10.0:
					pass_4b = False
			if deltas['bwd']:
				avg_bwd = sum(deltas['bwd']) / len(deltas['bwd'])
				if avg_bwd > 10.0:
					pass_4b = False

		verdict = 'PASS' if pass_4b else 'FAIL'
		f.write(f'**Result: {verdict}**\n\n')

		f.write(f'- Max per-interval delta (FWD): {max_delta_per_interval_fwd:.2f} pp\n')
		f.write(f'- Max per-interval delta (BWD): {max_delta_per_interval_bwd:.2f} pp\n\n')

		# Per-video table
		f.write('## Per-Video Aggregates\n\n')
		f.write('| Video | FWD Intervals | FWD Avg Delta | BWD Intervals | BWD Avg Delta |\n')
		f.write('| --- | --- | --- | --- | --- |\n')
		for video in sorted(per_video_deltas.keys()):
			deltas = per_video_deltas[video]
			fwd_avg = sum(deltas['fwd']) / len(deltas['fwd']) if deltas['fwd'] else 0.0
			bwd_avg = sum(deltas['bwd']) / len(deltas['bwd']) if deltas['bwd'] else 0.0
			f.write(f'| {video} | {len(deltas["fwd"])} | {fwd_avg:.2f} | {len(deltas["bwd"])} | {bwd_avg:.2f} |\n')

		f.write('\n## Full Per-Interval Table\n\n')
		f.write('| Video | Start | End | Sample | M1 FWD | M3 FWD | M4 FWD | Delta Sim | M1 BWD | M3 BWD | M4 BWD | Delta Sim |\n')
		f.write('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n')

		for row in sorted(comparison_rows, key=lambda r: (r['video'], int(r['interval_start']))):
			m1_fwd = row['m1_baseline_fwd'] if row['m1_baseline_fwd'] != '' else '-'
			m3_fwd = f"{row['m3_simulated_fwd']:.1f}" if row['m3_simulated_fwd'] != '' else '-'
			m4_fwd = f"{row['m4_post_fix_fwd']:.1f}" if row['m4_post_fix_fwd'] != '' else '-'
			delta_fwd = f"{row['delta_actual_vs_sim_fwd']:.1f}" if row['delta_actual_vs_sim_fwd'] != '' else '-'

			m1_bwd = row['m1_baseline_bwd'] if row['m1_baseline_bwd'] != '' else '-'
			m3_bwd = f"{row['m3_simulated_bwd']:.1f}" if row['m3_simulated_bwd'] != '' else '-'
			m4_bwd = f"{row['m4_post_fix_bwd']:.1f}" if row['m4_post_fix_bwd'] != '' else '-'
			delta_bwd = f"{row['delta_actual_vs_sim_bwd']:.1f}" if row['delta_actual_vs_sim_bwd'] != '' else '-'

			f.write(f'| {row["video"]} | {row["interval_start"]} | {row["interval_end"]} | {row["sample"]} | {m1_fwd} | {m3_fwd} | {m4_fwd} | {delta_fwd} | {m1_bwd} | {m3_bwd} | {m4_bwd} | {delta_bwd} |\n')

	print(f"Markdown written to: {md_output_path}", file=sys.stderr)

#============================================

if __name__ == '__main__':
	main()
