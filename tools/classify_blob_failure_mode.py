#!/usr/bin/env python3
"""
Classify blob failure modes from verdicts.csv files.

Reads verdicts.csv files under output_smoke/blob_refinement/{basename}/interval_*/
and produces CLASSIFICATION.md with per-interval funnel analysis.
"""

import argparse
import csv
from collections import Counter
import json
from pathlib import Path


def classify_funnel(row):
	"""
	Classify the funnel stage from a verdicts.csv row.

	Returns the stage name that best describes where the blob observation failed.
	"""
	gate = row.get('gate', '').strip().lower()
	has_residual = row.get('has_residual', '').lower() in ('true', '1', 'yes')
	validity_at_raw_pred = int(row.get('validity_at_raw_pred', 0))
	n_raw_blobs = int(row.get('n_raw_blobs', 0))
	n_corridor_blobs = int(row.get('n_corridor_blobs', 0))
	prox_ok = row.get('prox_ok', '').lower() in ('true', '1', 'yes')
	dir_ok = row.get('dir_ok', '').lower() in ('true', '1', 'yes')
	path_ok_prev = row.get('path_ok_prev', '').lower() in ('true', '1', 'yes')

	if gate == 'absent' and not has_residual:
		return 'lost_at_observer'
	elif gate == 'absent' and validity_at_raw_pred == 0:
		return 'lost_at_observer'
	elif gate == 'absent' and n_raw_blobs == 0:
		return 'lost_at_raw_blobs'
	elif gate == 'absent' and n_corridor_blobs == 0:
		return 'lost_at_corridor'
	elif gate == 'accepted':
		return 'accepted'
	elif gate == 'rejected' and not prox_ok:
		return 'lost_at_gate_prox'
	elif gate == 'rejected' and not dir_ok:
		return 'lost_at_gate_dir'
	elif gate == 'rejected' and not path_ok_prev:
		return 'lost_at_gate_path'
	elif gate == 'skipped':
		return 'none'
	else:
		return 'coverage_bug'


def validate_lost_at_stage(csv_lost_at_stage, computed_lost_at_stage, frame_num):
	"""
	Validate that the CSV's lost_at_stage matches the computed value.

	Returns True if they match, False if they disagree (coverage bug).
	"""
	return csv_lost_at_stage.strip().lower() == computed_lost_at_stage.lower()


def read_verdicts_csv(csv_path):
	"""
	Read and parse a verdicts.csv file.

	Returns list of rows as dicts.
	"""
	rows = []
	with open(csv_path, 'r') as f:
		reader = csv.DictReader(f)
		for row in reader:
			rows.append(row)
	return rows


def get_bucket_for_frame(frame_num, start_seed, end_seed):
	"""
	Classify a frame into an endpoint or interior bucket.

	Returns 'endpoint_start', 'interior', or 'endpoint_end'.
	"""
	if frame_num < start_seed + 5:
		return 'endpoint_start'
	elif frame_num >= end_seed - 5:
		return 'endpoint_end'
	else:
		return 'interior'


def compute_run_length_encoding(tags):
	"""
	Compute run-length encoding of tags.

	Returns list of (tag, count) tuples.
	"""
	if not tags:
		return []
	runs = []
	current_tag = tags[0]
	current_count = 1
	for tag in tags[1:]:
		if tag == current_tag:
			current_count += 1
		else:
			runs.append((current_tag, current_count))
			current_tag = tag
			current_count = 1
	runs.append((current_tag, current_count))
	return runs


def get_top_runs(runs, top_n=3):
	"""
	Get the top N longest runs.

	Returns list of (tag, count) tuples sorted by count descending.
	"""
	sorted_runs = sorted(runs, key=lambda x: x[1], reverse=True)
	return sorted_runs[:top_n]


def get_transitions(tags):
	"""
	Count adjacent tag-pair transitions.

	Returns Counter of (tag1, tag2) tuples.
	"""
	if len(tags) < 2:
		return Counter()
	transitions = Counter()
	for i in range(len(tags) - 1):
		transitions[(tags[i], tags[i + 1])] += 1
	return transitions


def longest_rejected_run(tags):
	"""
	Find the longest contiguous run of non-accepted tags.

	Returns the length of the longest such run.
	"""
	if not tags:
		return 0
	max_run = 0
	current_run = 0
	for tag in tags:
		if tag != 'accepted':
			current_run += 1
			max_run = max(max_run, current_run)
		else:
			current_run = 0
	return max_run


def read_interval_metadata(interval_dir):
	"""
	Read interval.json to get seed frame numbers.

	Returns dict with 'start_seed' and 'end_seed' keys, or None if not found.
	"""
	metadata_path = Path(interval_dir) / 'interval.json'
	if not metadata_path.exists():
		return None
	with open(metadata_path, 'r') as f:
		metadata = json.load(f)
	return metadata


def read_interval_scores(interval_dir):
	"""
	Read interval_scores.json to get blob_accept and confidence tier.

	Returns dict, or None if not found.
	"""
	scores_path = Path(interval_dir) / 'interval_scores.json'
	if not scores_path.exists():
		return None
	with open(scores_path, 'r') as f:
		scores = json.load(f)
	return scores


def read_hypothesis_tests(seed_oracle_dir):
	"""
	Read HYPOTHESIS_TESTS.md if it exists.

	Returns dict mapping interval_id to verdicts, or empty dict if not found.
	"""
	tests_path = Path(seed_oracle_dir) / 'HYPOTHESIS_TESTS.md'
	if not tests_path.exists():
		return {}

	verdicts = {}
	with open(tests_path, 'r') as f:
		content = f.read()

	for line in content.split('\n'):
		line = line.strip()
		if line.startswith('## Interval'):
			parts = line.split()
			if len(parts) >= 2:
				interval_id = parts[1].rstrip(':')
				verdicts[interval_id] = []
		elif 'H1' in line or 'H2' in line or 'H6' in line or 'H8' in line:
			if verdicts:
				last_key = list(verdicts.keys())[-1]
				verdicts[last_key].append(line)

	return verdicts


def classify_interval(interval_dir, basename, seed_oracle_dir):
	"""
	Classify a single interval.

	Returns dict with classification results.
	"""
	verdicts_csv = Path(interval_dir) / 'verdicts.csv'
	if not verdicts_csv.exists():
		return None

	interval_id = Path(interval_dir).name
	rows = read_verdicts_csv(verdicts_csv)
	metadata = read_interval_metadata(interval_dir)
	scores = read_interval_scores(interval_dir)
	hypothesis_verdicts = read_hypothesis_tests(seed_oracle_dir)

	if not metadata:
		return None

	start_seed = metadata.get('start_seed')
	end_seed = metadata.get('end_seed')
	if start_seed is None or end_seed is None:
		return None

	fwd_rows = [r for r in rows if r.get('pass', '').lower() == 'fwd']
	bwd_rows = [r for r in rows if r.get('pass', '').lower() == 'bwd']

	coverage_bugs = []
	fwd_buckets = {
		'endpoint_start': [],
		'interior': [],
		'endpoint_end': []
	}
	bwd_buckets = {
		'endpoint_start': [],
		'interior': [],
		'endpoint_end': []
	}

	fwd_tags = []
	bwd_tags = []

	for row in fwd_rows:
		frame_num = int(row.get('frame_num', -1))
		computed = classify_funnel(row)
		csv_stage = row.get('lost_at_stage', '').strip()

		if not validate_lost_at_stage(csv_stage, computed, frame_num):
			coverage_bugs.append({
				'frame': frame_num,
				'pass': 'fwd',
				'csv_stage': csv_stage,
				'computed': computed
			})

		bucket = get_bucket_for_frame(frame_num, start_seed, end_seed)
		fwd_buckets[bucket].append(computed)
		fwd_tags.append(computed)

	for row in bwd_rows:
		frame_num = int(row.get('frame_num', -1))
		computed = classify_funnel(row)
		csv_stage = row.get('lost_at_stage', '').strip()

		if not validate_lost_at_stage(csv_stage, computed, frame_num):
			coverage_bugs.append({
				'frame': frame_num,
				'pass': 'bwd',
				'csv_stage': csv_stage,
				'computed': computed
			})

		bucket = get_bucket_for_frame(frame_num, start_seed, end_seed)
		bwd_buckets[bucket].append(computed)
		bwd_tags.append(computed)

	fwd_runs = compute_run_length_encoding(fwd_tags)
	bwd_runs = compute_run_length_encoding(bwd_tags)

	fwd_transitions = get_transitions(fwd_tags)
	bwd_transitions = get_transitions(bwd_tags)

	fwd_longest_rejected = longest_rejected_run(fwd_tags)
	bwd_longest_rejected = longest_rejected_run(bwd_tags)

	fwd_top_transitions = fwd_transitions.most_common(3)
	bwd_top_transitions = bwd_transitions.most_common(3)

	fwd_tag_counts = Counter(fwd_tags)
	bwd_tag_counts = Counter(bwd_tags)

	fwd_counts_str = ', '.join([f'{tag}: {count}' for tag, count in fwd_tag_counts.most_common()])
	bwd_counts_str = ', '.join([f'{tag}: {count}' for tag, count in bwd_tag_counts.most_common()])

	asymmetric_deltas = {}
	for tag in set(fwd_tag_counts.keys()) | set(bwd_tag_counts.keys()):
		fwd_count = fwd_tag_counts.get(tag, 0)
		bwd_count = bwd_tag_counts.get(tag, 0)
		delta = abs(fwd_count - bwd_count)
		if delta >= 20:
			asymmetric_deltas[tag] = delta

	fwd_runs_longest = get_top_runs(fwd_runs, 3)
	bwd_runs_longest = get_top_runs(bwd_runs, 3)

	blob_accept = None
	trust_level = None
	if scores:
		blob_accept = scores.get('blob_accept', None)
		if blob_accept is not None:
			trust_level = 'high-accept' if blob_accept else 'TRUST 0%'

	oracle_ref = hypothesis_verdicts.get(interval_id, [])

	return {
		'interval_id': interval_id,
		'basename': basename,
		'start_seed': start_seed,
		'end_seed': end_seed,
		'fwd_buckets': fwd_buckets,
		'bwd_buckets': bwd_buckets,
		'fwd_tags': fwd_tags,
		'bwd_tags': bwd_tags,
		'fwd_runs': fwd_runs_longest,
		'bwd_runs': bwd_runs_longest,
		'fwd_transitions': fwd_top_transitions,
		'bwd_transitions': bwd_top_transitions,
		'fwd_longest_rejected': fwd_longest_rejected,
		'bwd_longest_rejected': bwd_longest_rejected,
		'fwd_counts_str': fwd_counts_str,
		'bwd_counts_str': bwd_counts_str,
		'asymmetric_deltas': asymmetric_deltas,
		'coverage_bugs': coverage_bugs,
		'blob_accept': blob_accept,
		'trust_level': trust_level,
		'oracle_ref': oracle_ref
	}


def format_bucket_histogram(buckets):
	"""
	Format bucket histogram for display.
	"""
	lines = []
	for bucket_name in ['endpoint_start', 'interior', 'endpoint_end']:
		tags = buckets[bucket_name]
		if tags:
			counts = Counter(tags)
			counts_str = ', '.join([f'{tag}: {c}' for tag, c in counts.most_common()])
			lines.append(f"  {bucket_name}: {counts_str}")
	return '\n'.join(lines)


def format_interval_section(result):
	"""
	Format a single interval's results for markdown output.
	"""
	lines = []
	lines.append(f"## {result['interval_id']}")
	lines.append(f"Seed frames: {result['start_seed']} to {result['end_seed']}")

	if result['trust_level']:
		lines.append(f"Trust level: {result['trust_level']}")
	if result['oracle_ref']:
		lines.append(f"Oracle verdicts: {'; '.join(result['oracle_ref'][:3])}")

	lines.append("")
	lines.append("### FWD Pass")
	lines.append("**Tag counts:** " + result['fwd_counts_str'])
	lines.append("**Buckets:**")
	lines.append(format_bucket_histogram(result['fwd_buckets']))
	lines.append(f"**Top runs:** {'; '.join([f'{tag} x{count}' for tag, count in result['fwd_runs']])}")
	lines.append(f"**Top transitions:** {'; '.join([f'{t0}->{t1} x{c}' for (t0, t1), c in result['fwd_transitions']])}")
	lines.append(f"**Longest rejected run:** {result['fwd_longest_rejected']} frames")

	lines.append("")
	lines.append("### BWD Pass")
	lines.append("**Tag counts:** " + result['bwd_counts_str'])
	lines.append("**Buckets:**")
	lines.append(format_bucket_histogram(result['bwd_buckets']))
	lines.append(f"**Top runs:** {'; '.join([f'{tag} x{count}' for tag, count in result['bwd_runs']])}")
	lines.append(f"**Top transitions:** {'; '.join([f'{t0}->{t1} x{c}' for (t0, t1), c in result['bwd_transitions']])}")
	lines.append(f"**Longest rejected run:** {result['bwd_longest_rejected']} frames")

	if result['asymmetric_deltas']:
		lines.append("")
		lines.append("### FWD vs BWD Parity")
		for tag, delta in sorted(result['asymmetric_deltas'].items()):
			lines.append(f"  {tag}: Δ{delta} frames (asymmetric)")

	if result['coverage_bugs']:
		lines.append("")
		lines.append("### Coverage Bugs")
		for bug in result['coverage_bugs']:
			lines.append(f"  Frame {bug['frame']} {bug['pass'].upper()}: expected '{bug['csv_stage']}' got '{bug['computed']}'")

	lines.append("")
	return '\n'.join(lines)


def main():
	parser = argparse.ArgumentParser(
		description='Classify blob failure modes from verdicts.csv files.'
	)
	parser.add_argument(
		'-i', '--input',
		dest='input_dir',
		required=True,
		help='Input directory (output_smoke/blob_refinement/{basename}/)'
	)
	parser.add_argument(
		'-o', '--output',
		dest='output_dir',
		required=True,
		help='Output directory for CLASSIFICATION.md'
	)
	args = parser.parse_args()

	input_path = Path(args.input_dir)
	output_path = Path(args.output_dir)

	if not input_path.exists():
		raise FileNotFoundError(f"Input directory does not exist: {input_path}")

	output_path.mkdir(parents=True, exist_ok=True)

	basename_dirs = sorted([d for d in input_path.iterdir() if d.is_dir()])
	all_results = []

	for basename_dir in basename_dirs:
		basename = basename_dir.name
		interval_dirs = sorted([d for d in basename_dir.iterdir() if d.is_dir() and d.name.startswith('interval_')])

		seed_oracle_dir = input_path.parent / 'seed_oracle'

		for interval_dir in interval_dirs:
			result = classify_interval(interval_dir, basename, seed_oracle_dir)
			if result:
				all_results.append(result)

	if not all_results:
		print("No intervals found to classify.")
		return

	trust_0_count = sum(1 for r in all_results if r['trust_level'] == 'TRUST 0%')
	trust_high_count = sum(1 for r in all_results if r['trust_level'] == 'high-accept')
	trust_unknown = sum(1 for r in all_results if r['trust_level'] is None)

	output_file = output_path / 'CLASSIFICATION.md'
	with open(output_file, 'w') as f:
		f.write("# Blob Failure Mode Classification\n\n")

		for result in all_results:
			f.write(format_interval_section(result))

		f.write("## Summary\n")
		f.write(f"**Total intervals:** {len(all_results)}\n")
		f.write(f"**TRUST 0%:** {trust_0_count}\n")
		f.write(f"**High-accept:** {trust_high_count}\n")
		f.write(f"**Unknown trust:** {trust_unknown}\n")

		total_coverage_bugs = sum(len(r['coverage_bugs']) for r in all_results)
		if total_coverage_bugs > 0:
			f.write(f"\n**Total coverage bugs:** {total_coverage_bugs}\n")

	print(f"Classification written to {output_file}")


if __name__ == '__main__':
	main()
