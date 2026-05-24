#!/usr/bin/env python3
"""
Rebucket selection.csv rows using actual verdicts from M2 sweep.

For each row in selection.csv, find the corresponding verdicts.csv,
compute accept percentages per pass, and assign the correct bucket
based on tier and min_accept percentage.
"""

import argparse
import csv
import os
import sys
from pathlib import Path

#============================================

def compute_bucket(tier, min_accept, accept_fwd, accept_bwd):
	"""
	Compute the bucket name and note based on tier and acceptance percentages.

	Args:
		tier: The confidence tier (low, fair, high)
		min_accept: Minimum of accept_fwd and accept_bwd percentages
		accept_fwd: FWD acceptance percentage
		accept_bwd: BWD acceptance percentage

	Returns:
		Tuple of (bucket_name, note_text)
	"""
	# Normalize tier to consistent case
	tier_lower = tier.lower()

	# Apply bucketing rules
	if tier_lower == 'high' or tier_lower == 'trust':
		if min_accept == 0:
			return 'trust_0', ''
		elif min_accept >= 60:
			return 'trust_high', ''
		elif 30 <= min_accept < 60:
			return 'mixed', ''
		elif 20 <= min_accept < 30:
			return 'trust_low_other', ''
		else:
			return 'other_unbucketed', f'tier={tier_lower}, min_accept={min_accept}'
	elif tier_lower in ('low', 'weak', 'fair'):
		if min_accept < 20:
			return 'weak_fair_low', ''
		else:
			return 'other_unbucketed', f'tier={tier_lower}, min_accept={min_accept}'
	else:
		return 'other_unbucketed', f'tier={tier_lower}, min_accept={min_accept}'

#============================================

def find_verdicts_csv(m2_sweep_root, video, interval_start, interval_end):
	"""
	Find the verdicts.csv file for a given interval.

	Handles both standard and double-nested dir layouts via glob.

	Args:
		m2_sweep_root: Root path to the m2_sweep directory
		video: Video name
		interval_start: Interval start frame
		interval_end: Interval end frame

	Returns:
		Path to verdicts.csv if found, None otherwise
	"""
	interval_dir = f"interval_{interval_start}_{interval_end}"
	sweep_path = Path(m2_sweep_root) / video / interval_dir

	if not sweep_path.exists():
		return None

	# Try standard path first
	verdicts = sweep_path / "verdicts.csv"
	if verdicts.exists():
		return verdicts

	# Try double-nested path (known sweep wrapper bug)
	verdicts_nested = sweep_path / interval_dir / "verdicts.csv"
	if verdicts_nested.exists():
		return verdicts_nested

	return None

#============================================

def compute_accept_percentages(verdicts_path):
	"""
	Read verdicts.csv and compute accept percentages per pass.

	Filters to non-endpoint rows (skipped gates are endpoints).

	Args:
		verdicts_path: Path to verdicts.csv

	Returns:
		Tuple of (accept_fwd_pct, accept_bwd_pct) as integers,
		or (None, None) if file cannot be read or has no data.
	"""
	if not verdicts_path or not verdicts_path.exists():
		return None, None

	try:
		with open(verdicts_path) as f:
			rdr = csv.DictReader(f)
			rows = list(rdr)
	except Exception:
		return None, None

	if not rows:
		return None, None

	# Separate by pass and filter out endpoint rows (gate='skipped')
	fwd_rows = [r for r in rows if r['pass'] == 'FWD' and r['gate'] != 'skipped']
	bwd_rows = [r for r in rows if r['pass'] == 'BWD' and r['gate'] != 'skipped']

	# Count accepts in each pass
	fwd_accept = sum(1 for r in fwd_rows if r['gate'] == 'accepted')
	bwd_accept = sum(1 for r in bwd_rows if r['gate'] == 'accepted')

	# Compute percentages (rounded to nearest integer)
	if len(fwd_rows) == 0:
		fwd_pct = None
	else:
		fwd_pct = round(100 * fwd_accept / len(fwd_rows))

	if len(bwd_rows) == 0:
		bwd_pct = None
	else:
		bwd_pct = round(100 * bwd_accept / len(bwd_rows))

	return fwd_pct, bwd_pct

#============================================

def rebucket_selection(selection_csv, m2_sweep_root, output_csv):
	"""
	Read selection.csv, compute actual buckets from verdicts, write output.

	Args:
		selection_csv: Path to input per_video_selection.csv
		m2_sweep_root: Path to m2_sweep root directory
		output_csv: Path to output per_video_selection_rebucketed.csv
	"""
	# Read input
	with open(selection_csv) as f:
		rdr = csv.DictReader(f)
		rows = list(rdr)

	output_rows = []

	for row in rows:
		video = row['video']
		interval_start = row['interval_start']
		interval_end = row['interval_end']
		tier = row['tier']

		# Find verdicts file
		verdicts_path = find_verdicts_csv(m2_sweep_root, video, interval_start, interval_end)

		# Compute accept percentages
		accept_fwd, accept_bwd = compute_accept_percentages(verdicts_path)

		# Determine note and bucket
		if accept_fwd is None or accept_bwd is None:
			# Missing or incomplete verdicts
			new_bucket = row['bucket']  # Keep placeholder
			if verdicts_path is None:
				note = 'verdicts.csv not found'
			else:
				note = 'verdicts.csv has no non-endpoint rows'
			actual_fwd_str = ''
			actual_bwd_str = ''
		else:
			# Compute bucket
			min_accept = min(accept_fwd, accept_bwd)
			new_bucket, note = compute_bucket(tier, min_accept, accept_fwd, accept_bwd)
			actual_fwd_str = str(accept_fwd)
			actual_bwd_str = str(accept_bwd)

		# Build output row
		output_row = dict(row)
		output_row['bucket'] = new_bucket
		output_row['actual_accept_fwd'] = actual_fwd_str
		output_row['actual_accept_bwd'] = actual_bwd_str
		output_row['rebucket_note'] = note

		output_rows.append(output_row)

	# Write output
	if output_rows:
		fieldnames = list(output_rows[0].keys())
		with open(output_csv, 'w', newline='') as f:
			wtr = csv.DictWriter(f, fieldnames=fieldnames)
			wtr.writeheader()
			wtr.writerows(output_rows)

#============================================

def parse_args():
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description='Rebucket selection.csv rows using verdicts from M2 sweep'
	)
	parser.add_argument(
		'-s', '--selection',
		dest='selection_csv',
		default='/Users/vosslab/nsh/track-runner-virtual-dolly-cam/output_smoke/blob_refine_fix/2026-05-23/m2_sweep/per_video_selection.csv',
		help='Path to per_video_selection.csv'
	)
	parser.add_argument(
		'-r', '--root',
		dest='m2_sweep_root',
		default='/Users/vosslab/nsh/track-runner-virtual-dolly-cam/output_smoke/blob_refine_fix/2026-05-23/m2_sweep',
		help='Path to m2_sweep root directory'
	)
	parser.add_argument(
		'-o', '--output',
		dest='output_csv',
		default='/Users/vosslab/nsh/track-runner-virtual-dolly-cam/output_smoke/blob_refine_fix/2026-05-23/m2_sweep/per_video_selection_rebucketed.csv',
		help='Path to output per_video_selection_rebucketed.csv'
	)
	return parser.parse_args()

#============================================

def main():
	"""Main driver."""
	args = parse_args()

	rebucket_selection(args.selection_csv, args.m2_sweep_root, args.output_csv)

	print(f'Rebucketed {args.selection_csv}')
	print(f'Output: {args.output_csv}')

if __name__ == '__main__':
	main()
