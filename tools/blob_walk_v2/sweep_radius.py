#!/usr/bin/env python3
"""
Sweep the BOOTSTRAP_UNCERTAINTY_W search slack across the 3-frame Model 3
bootstrap sequence test on the captured JSONs.

PHYSICAL VELOCITY ENVELOPE IS NOT SWEPT. MAX_RUNNER_SPEED_W_PER_S is fixed
at 30.0 W/s (20 mph upper bound, derived from runner physics; see
walk_motion_gate.py). Sweeping the speed envelope would re-introduce the
circular-reasoning bug where a fragile sweep value gets pinned as design
truth. Instead this sweep varies only the fixed slack term, which is the
legitimate localization-uncertainty knob.

Per-step bootstrap radius formula (constant across bootstrap steps):
    radius_w = (MAX_RUNNER_SPEED_W_PER_S / source_fps) + bootstrap_uncertainty_w

The candidate prediction comes from walker-measured velocity (the
prediction is already encoded in the dump's prev_cx_scene/prev_cy_scene
per step): step 1 anchors on the seed, step 2 projects forward by the
step-1 displacement, step 3 projects forward by the rolling 2-step mean.

Outputs a per-video x per-slack-W matrix. Pass bar is >51% of testable
intervals per video.
"""

# Standard Library
import os
import json
import sys
import pathlib

# Determine repo root and set up paths
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, 'track_runner')
_TESTS_DIR = os.path.join(_REPO_ROOT, 'tests')
if _TRACK_RUNNER_DIR not in sys.path:
	sys.path.insert(0, _TRACK_RUNNER_DIR)
if _TESTS_DIR not in sys.path:
	sys.path.insert(0, _TESTS_DIR)
if _REPO_ROOT not in sys.path:
	sys.path.insert(0, _REPO_ROOT)

# local repo modules
import walk_io
import walk_motion_gate
import replay_models


#============================================
# Sweep configuration
#============================================

# Slack values to test (W). Centered on the pinned BOOTSTRAP_UNCERTAINTY_W = 0.30.
# Sweep is for sensitivity, not pinning; physical velocity stays fixed.
SLACK_W_CANDIDATES = [0.10, 0.20, 0.30, 0.50, 0.75]

# 3-frame sequence acceptance bar: >51% (strict majority) testable intervals.
SEQUENCE_BAR_FRACTION = 0.51

# Target videos (6 outdoor 400m videos, outdoor corpus 2026-05-28).
TARGET_VIDEOS = {
	'Conant-4x400-2026_April_15',
	'Jason-3200m-sectionals-IMG_4005',
	'Lyra-Hersey-800m-IMG_3882',
	'Lyra-Wheeling-IMG_3912',
	'IMG_3823',
	'IMG_3830',
}


def load_scene_transform_for_video(video_basename: str):
	"""Load scene_transform cache for a video, or None if not available."""
	try:
		return walk_io.load_walker_scene_transform(video_basename)
	except Exception:
		return None


def evaluate_sequence_with_slack(
	steps: list,
	scene_transform,
	source_fps: float,
	slack_w: float,
) -> bool:
	"""
	Evaluate the 3-frame sequence with a custom BOOTSTRAP_UNCERTAINTY_W.

	Same shape as replay_models.evaluate_model_3_sequence but with an
	overridden slack term. The per-step prediction comes from
	`prev_cx_scene/prev_cy_scene` in the dump (already walker-measured).
	Per-step radius is constant: per-frame envelope + slack.
	"""
	import math
	per_frame_w = walk_motion_gate.max_runner_jump_per_frame(source_fps)
	radius_w = per_frame_w + slack_w
	for step in steps:
		corridor = step['corridor_blobs']
		winner = step['winner_blob']
		if not corridor or winner is None:
			return False
		seed_w_px = step['seed_w_px']
		pred_x = step['prev_cx_scene']
		pred_y = step['prev_cy_scene']
		frame_index = step['frame_index']
		wx_px = winner['centroid_x']
		wy_px = winner['centroid_y']
		wx_scene, wy_scene = scene_transform.pixel_to_scene(frame_index, wx_px, wy_px)
		dx = wx_scene - pred_x
		dy = wy_scene - pred_y
		distance = math.sqrt(dx * dx + dy * dy)
		effective_radius = radius_w * seed_w_px
		if distance > effective_radius:
			return False
	return True


def main():
	"""Sweep slack values across all captured JSONs and generate report."""
	dump_root = pathlib.Path(_REPO_ROOT) / 'dump_step1'
	if not dump_root.exists():
		print(f"Error: {dump_root} does not exist.")
		sys.exit(1)

	json_files = list(dump_root.glob('*/*_*.json'))
	if not json_files:
		print(f"Error: no JSON files found in {dump_root}.")
		sys.exit(1)
	json_files.sort()

	results = {}

	for json_path in json_files:
		with open(json_path) as f:
			data = json.load(f)

		video_basename = data['video_basename']
		if video_basename not in TARGET_VIDEOS:
			continue

		step_1 = data['step_1']
		step_2 = data['step_2']
		step_3 = data['step_3']
		if step_1 is None or step_2 is None or step_3 is None:
			continue
		steps_dict = {'step_0': step_1, 'step_1': step_1, 'step_2': step_2, 'step_3': step_3}
		if not replay_models.is_testable_sequence(steps_dict):
			continue

		scene_transform = load_scene_transform_for_video(video_basename)
		if scene_transform is None:
			print(f"Warning: scene_transform not available for {video_basename}; skipping")
			continue

		source_fps = data['source_fps']

		for slack_w in SLACK_W_CANDIDATES:
			key = (video_basename, slack_w)
			if key not in results:
				results[key] = {'testable': 0, 'accepts': 0}
			results[key]['testable'] += 1
			accepted = evaluate_sequence_with_slack(
				[step_1, step_2, step_3], scene_transform, source_fps, slack_w,
			)
			if accepted:
				results[key]['accepts'] += 1

	# Compute verdicts per slack value.
	slack_verdicts = {}
	for slack_w in SLACK_W_CANDIDATES:
		slack_verdicts[slack_w] = {}
		for video in sorted(TARGET_VIDEOS):
			key = (video, slack_w)
			if key in results:
				stat = results[key]
				testable_count = stat['testable']
				accepts_count = stat['accepts']
				if testable_count > 0:
					threshold_value = SEQUENCE_BAR_FRACTION * testable_count
					verdict = 'PASS' if accepts_count > threshold_value else 'FAIL'
					slack_verdicts[slack_w][video] = {
						'verdict': verdict,
						'accepts': accepts_count,
						'testable': testable_count,
					}
				else:
					slack_verdicts[slack_w][video] = {
						'verdict': 'N/A', 'accepts': 0, 'testable': 0,
					}
			else:
				slack_verdicts[slack_w][video] = {
					'verdict': 'N/A', 'accepts': 0, 'testable': 0,
				}

	# Find smallest universal-pass slack.
	universal_slack = None
	for slack_w in SLACK_W_CANDIDATES:
		all_pass = all(
			slack_verdicts[slack_w].get(video, {}).get('verdict') == 'PASS'
			for video in TARGET_VIDEOS
		)
		if all_pass:
			universal_slack = slack_w
			break

	# Generate report.
	report_path = dump_root / 'RADIUS_SWEEP.md'
	with open(report_path, 'w') as f:
		f.write('# Bootstrap Slack Sweep Report\n\n')
		f.write('## Methodology\n\n')
		f.write('Physical runner-speed envelope is FIXED at ')
		f.write(f'`MAX_RUNNER_SPEED_W_PER_S = {walk_motion_gate.MAX_RUNNER_SPEED_W_PER_S}` ')
		f.write('(20 mph upper bound at torso_width = 12 inches). Per-frame ')
		f.write('runner allowance is derived as ')
		f.write('`MAX_RUNNER_SPEED_W_PER_S / source_fps`. Velocity is NOT swept.\n\n')
		f.write('This sweep varies only `BOOTSTRAP_UNCERTAINTY_W`, the fixed ')
		f.write('localization slack added to the per-step radius. Pinned ')
		f.write(f'value: `BOOTSTRAP_UNCERTAINTY_W = {walk_motion_gate.BOOTSTRAP_UNCERTAINTY_W}`. ')
		f.write('The candidate prediction at step i is the seed center plus the ')
		f.write('endpoint-seed velocity prior (clamped to [MIN, MAX] runner speed) ')
		f.write('times step i.\n\n')
		f.write('Per-step radius formula:\n\n')
		f.write('```\n')
		f.write('search_radius_w = (MAX_RUNNER_SPEED_W_PER_S / source_fps) + slack_w\n')
		f.write('pred_scene_step_1 = seed_scene\n')
		f.write('pred_scene_step_2 = step1_winner_scene + (step1_winner_scene - seed_scene)\n')
		f.write('pred_scene_step_3 = step2_winner_scene + 0.5 * (step2_winner_scene - seed_scene)\n')
		f.write('accept_step_i = dist(winner_scene, pred_scene_step_i) <= search_radius_w * torso_w_scene\n')
		f.write('```\n\n')
		f.write('Per-interval rule: steps 1, 2, 3 must all accept. Empty corridor ')
		f.write('or null winner at step 2 or 3 is a sequence FAILURE, not untestable.\n\n')
		f.write(f'Per-video bar: >{int(SEQUENCE_BAR_FRACTION * 100)}% testable intervals.\n\n')
		f.write('Slack candidates (W): ')
		f.write(', '.join(f'{s:.2f}' for s in SLACK_W_CANDIDATES))
		f.write('.\n\n')
		f.write('Target videos:\n')
		for v in sorted(TARGET_VIDEOS):
			f.write(f'- {v}\n')
		f.write('\n')

		f.write('## Per-Slack x Per-Video Matrix\n\n')
		header_parts = ['Slack (W)'] + sorted(TARGET_VIDEOS)
		f.write('| ' + ' | '.join(header_parts) + ' |\n')
		f.write('| ' + ' | '.join(['---'] * len(header_parts)) + ' |\n')

		for slack_w in SLACK_W_CANDIDATES:
			row_parts = [f'{slack_w:.2f}']
			for video in sorted(TARGET_VIDEOS):
				cell = slack_verdicts[slack_w].get(video, {})
				verdict = cell.get('verdict', 'N/A')
				testable = cell.get('testable', 0)
				accepts = cell.get('accepts', 0)
				if verdict == 'PASS':
					row_parts.append(f'PASS ({accepts}/{testable})')
				elif verdict == 'FAIL':
					row_parts.append(f'FAIL ({accepts}/{testable})')
				else:
					row_parts.append('N/A')
			f.write('| ' + ' | '.join(row_parts) + ' |\n')
		f.write('\n')

		f.write('## Universal Pass Result\n\n')
		if universal_slack is not None:
			f.write(f'**Smallest universal-pass slack: {universal_slack:.2f} W**\n\n')
			f.write('All target videos PASS the per-video bar:\n\n')
			for video in sorted(TARGET_VIDEOS):
				cell = slack_verdicts[universal_slack].get(video, {})
				accepts = cell.get('accepts', 0)
				testable = cell.get('testable', 0)
				f.write(f'- {video}: {accepts}/{testable}\n')
			f.write('\n')
		else:
			f.write('**NO UNIVERSAL SLACK FOUND** in the swept range.\n\n')
			f.write('Per-video winning slack (smallest that passes each):\n\n')
			for video in sorted(TARGET_VIDEOS):
				winner_s = None
				for s in SLACK_W_CANDIDATES:
					if slack_verdicts[s].get(video, {}).get('verdict') == 'PASS':
						winner_s = s
						break
				if winner_s is not None:
					f.write(f'- {video}: {winner_s:.2f} W\n')
				else:
					f.write(f'- {video}: NO PASSING SLACK\n')
			f.write('\n')

	print(f'Report written to {report_path}')
	if universal_slack is not None:
		print(f'Universal pass slack: {universal_slack:.2f} W')
	else:
		print('No universal slack found in swept range')
	return 0


if __name__ == '__main__':
	sys.exit(main())
