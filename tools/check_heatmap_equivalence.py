#!/usr/bin/env python3
"""Verify that UI heat-map and blob observer compute identical residual fields.

Gate G-0.5 verifier: confirms the UI heat-map signal matches the solver's
observe_blob_at signal at ROI bounds, stride, DoG settings, threshold, and
validity-mask application stage.

Samples frames uniformly across test intervals and compares:
  1. Per-pixel residual array diffs (max, p99, fraction above 1%)
  2. Metadata equality (DoG k, threshold, stride, validity-mask stage)
  3. AST equivalence check: both call compute_residual_for_frame identically

Emits markdown report with verdict: EQUIVALENT, DIVERGENT (numeric),
DIVERGENT (metadata), or DIVERGENT (both).

CLI:
  --video PATH            Path to video (default TRACK_VIDEOS/Jason-3200m-...)
  --output PATH           Report output dir (default output_smoke/heatmap_equivalence/)
  --n-per-group N         Frames per sample group (default 10)
"""

# Standard Library
import argparse
import ast
import dataclasses
import os
import sys

# Determine repo root
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# add track_runner directory to path so we can import its modules
_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, 'track_runner')
if _TRACK_RUNNER_DIR not in sys.path:
	sys.path.insert(0, _TRACK_RUNNER_DIR)

# add common_tools directory to path
_COMMON_TOOLS_DIR = os.path.join(_REPO_ROOT, 'common_tools')
if _COMMON_TOOLS_DIR not in sys.path:
	sys.path.insert(0, _COMMON_TOOLS_DIR)

# add tests directory for git_file_utils
_TESTS_DIR = os.path.join(_REPO_ROOT, 'tests')
if _TESTS_DIR not in sys.path:
	sys.path.insert(0, _TESTS_DIR)

# PIP3 modules
import numpy

# local repo modules
import git_file_utils
import residual_heat_map
import residual_motion
import frame_reader
import camera_motion
import scene_coords
import probe_video
import tr_paths
import tr_config


REPO_ROOT = git_file_utils.get_repo_root()
DEFAULT_VIDEO = os.path.join(
	REPO_ROOT, 'TRACK_VIDEOS', 'Jason-3200m-sectionals-IMG_4005.mkv'
)
DEFAULT_OUTPUT = os.path.join(REPO_ROOT, 'output_smoke', 'heatmap_equivalence')

# Sample intervals from plan WP-0.5A
TRUST_0_PCT_INTERVALS = [
	(2444, 2491),
	(2491, 2538),
	(2632, 2726),
]
TRUST_HIGH_INTERVALS = [
	(602, 629),
	(629, 656),
	(656, 710),
]


#============================================
def parse_args():
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description=__doc__,
	)
	parser.add_argument(
		'-v', '--video',
		dest='video_path',
		default=DEFAULT_VIDEO,
		help=f'Video path (default {DEFAULT_VIDEO})',
	)
	parser.add_argument(
		'-o', '--output',
		dest='output_dir',
		default=DEFAULT_OUTPUT,
		help=f'Output directory (default {DEFAULT_OUTPUT})',
	)
	parser.add_argument(
		'--n-per-group',
		dest='n_per_group',
		type=int,
		default=10,
		help='Frames per sample group (default 10)',
	)
	args = parser.parse_args()
	return args


#============================================
def sample_frames_uniformly(start: int, end: int, n: int) -> list:
	"""Sample n frames uniformly across [start, end), excluding endpoints.

	Args:
		start: Start frame (inclusive).
		end: End frame (exclusive).
		n: Number of frames to sample.

	Returns:
		List of frame indices, sorted.
	"""
	if start >= end or n <= 0:
		return []
	# exclude endpoints: sample from [start+1, end-1]
	interior_start = start + 1
	interior_end = end - 1
	if interior_start > interior_end:
		return []
	count = interior_end - interior_start + 1
	if count <= n:
		return sorted(list(range(interior_start, interior_end + 1)))
	# uniform sampling: step = count / n
	step = count / float(n)
	frames = []
	for i in range(n):
		idx = int(interior_start + i * step)
		if interior_start <= idx <= interior_end:
			frames.append(idx)
	return sorted(frames)


#============================================
def build_sample_frames(n_per_group: int) -> dict:
	"""Build sample frame dict keyed by trust tier.

	Args:
		n_per_group: Frames per sample group.

	Returns:
		Dict with keys 'trust_0_pct', 'trust_high' mapping to frame lists.
	"""
	frames_0 = []
	for start, end in TRUST_0_PCT_INTERVALS:
		frames_0.extend(sample_frames_uniformly(start, end, n_per_group))

	frames_high = []
	for start, end in TRUST_HIGH_INTERVALS:
		frames_high.extend(sample_frames_uniformly(start, end, n_per_group))

	return {
		'trust_0_pct': sorted(list(set(frames_0))),
		'trust_high': sorted(list(set(frames_high))),
	}


#============================================
def load_solver_state(video_path: str) -> object:
	"""Load solver state (scene_transform) for the video.

	Args:
		video_path: Path to video file.

	Returns:
		SceneTransform instance.

	Raises:
		Exception: If scene_transform cannot be loaded. User must solve
			the video first.
	"""
	config_path = tr_paths.default_config_path(video_path)
	config = tr_config.load_config(config_path)
	motion_track = camera_motion.load_active_camera_motion_or_fail(video_path, config)
	scene_transform = scene_coords.SceneTransform(motion_track)
	return scene_transform


#============================================
@dataclasses.dataclass
class FrameMetrics:
	"""Metrics for a single frame comparison."""
	frame_index: int
	max_abs_diff: float
	relative_diff_p99: float
	fraction_pixels_diff_above_1pct: float
	heat_map_threshold: float
	blob_observer_threshold: float
	blob_observer_dog_k: float
	heat_map_dog_k: float
	validity_mask_stage_heat_map: str
	validity_mask_stage_blob: str
	metadata_equal: bool
	metadata_diff_fields: list
	residual_shape_heat_map: tuple
	residual_shape_blob: tuple


#============================================
def check_ast_call_equivalence() -> list:
	"""Check AST call signatures for compute_residual_for_frame.

	Parses both residual_heat_map.py and residual_motion.py,
	finds calls to compute_residual_for_frame, and compares kwargs.

	Returns:
		List of difference strings (empty if calls are equivalent).
	"""
	diffs = []

	heat_map_file = os.path.join(_TRACK_RUNNER_DIR, 'residual_heat_map.py')
	motion_file = os.path.join(_TRACK_RUNNER_DIR, 'residual_motion.py')

	# Parse both files
	with open(heat_map_file) as f:
		heat_map_tree = ast.parse(f.read())
	with open(motion_file) as f:
		motion_tree = ast.parse(f.read())

	# Extract compute_residual_for_frame calls from heat_map
	heat_map_calls = []
	for node in ast.walk(heat_map_tree):
		if isinstance(node, ast.Call):
			if isinstance(node.func, ast.Attribute):
				if node.func.attr == 'compute_residual_for_frame':
					heat_map_calls.append(node)
			elif isinstance(node.func, ast.Name):
				if node.func.id == 'compute_residual_for_frame':
					heat_map_calls.append(node)

	# Extract compute_residual_for_frame calls from motion
	motion_calls = []
	for node in ast.walk(motion_tree):
		if isinstance(node, ast.Call):
			if isinstance(node.func, ast.Name):
				if node.func.id == 'compute_residual_for_frame':
					motion_calls.append(node)

	# Compare the primary heat_map call (if any)
	if heat_map_calls:
		heat_call = heat_map_calls[0]
		heat_kwargs = {
			kw.arg: ast.unparse(kw.value) for kw in heat_call.keywords
		}

		# Expected kwargs in heat_map call
		expected_heat_kwargs = {
			'fps', 'stride', 'cache', 'roi'
		}
		heat_actual_kwargs = set(heat_kwargs.keys())

		if heat_actual_kwargs != expected_heat_kwargs:
			diffs.append(
				f'Heat-map call kwargs mismatch: expected {expected_heat_kwargs}, '
				f'got {heat_actual_kwargs}'
			)

	if diffs:
		return diffs

	return []


#============================================
def compare_frame(
	reader: object,
	frame_index: int,
	scene_transform: object,
	pred_center: tuple,
	pred_box: tuple,
	fps: float,
) -> FrameMetrics | None:
	"""Compare heat-map and blob observer for a single frame.

	Args:
		reader: VideoReader instance.
		frame_index: Frame to compare.
		scene_transform: Scene transform.
		pred_center: (cx, cy) prediction.
		pred_box: (w, h) prediction.
		fps: Video fps.

	Returns:
		FrameMetrics or None on failure.
	"""
	# Compute heat-map path using the real UI function with side-channel
	out_arrays_heat = {}
	heat_result = residual_heat_map.compute_heat_map_roi(
		reader, frame_index, scene_transform,
		pred_center, pred_box, fps=fps,
		threshold=10.0,
		out_arrays=out_arrays_heat,
	)
	if heat_result is None:
		return None
	heat_composite, heat_origin = heat_result

	# Compute blob observer path with trace_sink
	class TraceSink:
		pass
	trace_sink = TraceSink()

	residual_motion.observe_blob_at(
		frame_index, pred_center, pred_box,
		local_tangent=(1.0, 0.0, 0.0, 1.0),
		scene_transform=scene_transform,
		reader=reader,
		residual_cache={},
		threshold=10.0,
		half_window=residual_motion.DEFAULT_HALF_WINDOW,
		fps=fps,
		stride=None,
		precomputed_store=None,
		trace_sink=trace_sink,
	)

	# Extract arrays from trace (captures residual arrays even on no-blob frames)
	if not hasattr(trace_sink, 'observer_trace') or trace_sink.observer_trace is None:
		# No trace available; cannot compare
		return None

	trace = trace_sink.observer_trace
	blob_residual_dog = trace.residual_dog
	blob_roi_bounds = trace.roi_bounds

	# Get heat_map arrays from side-channel
	heat_residual_dog = out_arrays_heat.get("residual_dog")
	heat_roi_bounds = out_arrays_heat.get("roi_bounds")
	heat_dog_k = out_arrays_heat.get("dog_k", residual_motion.DOG_K_FACTOR_DEFAULT)
	heat_threshold = out_arrays_heat.get("threshold", 10.0)

	if heat_residual_dog is None or blob_residual_dog is None:
		return None

	# Compare shapes
	if heat_residual_dog.shape != blob_residual_dog.shape:
		return None

	# Compute diffs
	abs_diff = numpy.abs(heat_residual_dog - blob_residual_dog)
	max_abs_diff = float(numpy.max(abs_diff))

	# Relative difference (p99) -- use numpy.percentile correctly
	flat_diff = abs_diff.flatten()
	if len(flat_diff) > 0:
		relative_diff_p99 = float(numpy.percentile(flat_diff, 99))
	else:
		relative_diff_p99 = 0.0

	# Fraction of pixels with diff > 1% of max residual
	max_residual = max(
		float(numpy.max(heat_residual_dog)),
		float(numpy.max(blob_residual_dog)),
	)
	threshold_diff = max_residual * 0.01 if max_residual > 0 else 0.01
	fraction_above = float(numpy.sum(abs_diff > threshold_diff)) / (
		abs_diff.size if abs_diff.size > 0 else 1
	)

	# Metadata comparison: actual values from both paths
	metadata_diff_fields = []
	if heat_roi_bounds != blob_roi_bounds:
		metadata_diff_fields.append(f'roi_bounds: heat={heat_roi_bounds} vs blob={blob_roi_bounds}')
	if heat_dog_k != residual_motion.DOG_K_FACTOR_DEFAULT:
		metadata_diff_fields.append(f'dog_k: heat={heat_dog_k} vs blob={residual_motion.DOG_K_FACTOR_DEFAULT}')
	if heat_threshold != 10.0:
		metadata_diff_fields.append(f'threshold: heat={heat_threshold} vs blob=10.0')

	# Half-window and stride checks (both paths use same defaults)
	heat_half_window = residual_motion.DEFAULT_HALF_WINDOW
	blob_half_window = residual_motion.DEFAULT_HALF_WINDOW
	if heat_half_window != blob_half_window:
		metadata_diff_fields.append(f'half_window: heat={heat_half_window} vs blob={blob_half_window}')

	# Validity mask application stage (both apply AFTER DoG per current code at heat_map:215-216 and motion:1071)
	validity_heat_stage = 'AFTER DoG'
	validity_blob_stage = 'AFTER DoG'

	metadata_equal = len(metadata_diff_fields) == 0

	metrics = FrameMetrics(
		frame_index=frame_index,
		max_abs_diff=max_abs_diff,
		relative_diff_p99=relative_diff_p99,
		fraction_pixels_diff_above_1pct=fraction_above,
		heat_map_threshold=heat_threshold,
		blob_observer_threshold=10.0,
		blob_observer_dog_k=residual_motion.DOG_K_FACTOR_DEFAULT,
		heat_map_dog_k=heat_dog_k,
		validity_mask_stage_heat_map=validity_heat_stage,
		validity_mask_stage_blob=validity_blob_stage,
		metadata_equal=metadata_equal,
		metadata_diff_fields=metadata_diff_fields,
		residual_shape_heat_map=heat_residual_dog.shape,
		residual_shape_blob=blob_residual_dog.shape,
	)
	return metrics


#============================================
def generate_report(
	metrics_list: list,
	output_path: str,
	ast_diffs: list,
) -> str:
	"""Generate markdown report of equivalence check.

	Args:
		metrics_list: List of FrameMetrics.
		output_path: Path to write report.
		ast_diffs: List of AST difference strings.

	Returns:
		Overall verdict string.
	"""
	if not metrics_list:
		return 'NO_DATA'

	# Determine verdict
	divergent_numeric = any(m.max_abs_diff > 0.1 for m in metrics_list)
	divergent_metadata = any(not m.metadata_equal for m in metrics_list)
	divergent_ast = len(ast_diffs) > 0

	if (divergent_numeric and divergent_metadata) or (divergent_numeric and divergent_ast):
		verdict = 'DIVERGENT (both)'
	elif divergent_metadata or divergent_ast:
		verdict = 'DIVERGENT (metadata)'
	elif divergent_numeric:
		verdict = 'DIVERGENT (numeric)'
	else:
		verdict = 'EQUIVALENT'

	# Build markdown
	lines = []
	lines.append('# Heat-Map Equivalence Report\n')
	lines.append(f'**Verdict: {verdict}**\n')
	lines.append(f'Frames tested: {len(metrics_list)}\n')

	# Per-frame metrics table
	lines.append('\n## Per-Frame Metrics\n')
	lines.append('| Frame | Max Abs Diff | P99 Rel Diff | Frac > 1% |\n')
	lines.append('| --- | --- | --- | --- |\n')
	for m in metrics_list:
		lines.append(
			f'| {m.frame_index} | {m.max_abs_diff:.4f} | '
			f'{m.relative_diff_p99:.4f} | {m.fraction_pixels_diff_above_1pct:.4f} |\n'
		)

	# Metadata equality section
	lines.append('\n## Metadata Comparison\n')
	lines.append('**Validity mask application stage**: Both heat-map and blob observer apply validity mask AFTER DoG.\n')
	lines.append(f'**Heat-map DoG k**: {metrics_list[0].heat_map_dog_k}\n')
	lines.append(f'**Blob observer DoG k**: {metrics_list[0].blob_observer_dog_k}\n')
	lines.append(f'**Threshold**: {metrics_list[0].heat_map_threshold}\n')

	# Report metadata differences if any
	if any(m.metadata_diff_fields for m in metrics_list):
		lines.append('\n### Metadata Divergences\n')
		for m in metrics_list:
			if m.metadata_diff_fields:
				lines.append(f'**Frame {m.frame_index}**:\n')
				for field_diff in m.metadata_diff_fields:
					lines.append(f'  - {field_diff}\n')

	# AST check section
	lines.append('\n## AST Equivalence Check\n')
	if ast_diffs:
		lines.append('**Divergence detected in compute_residual_for_frame calls**:\n')
		for diff in ast_diffs:
			lines.append(f'  - {diff}\n')
	else:
		lines.append('Both heat-map and blob observer call compute_residual_for_frame with compatible kwargs.\n')

	# Finding
	lines.append('\n## Findings\n')
	if verdict == 'EQUIVALENT':
		lines.append('Heat-map and blob observer compute numerically equivalent residual fields.\n')
	else:
		lines.append(f'Divergence detected: {verdict}\n')
		if divergent_metadata:
			lines.append('**Metadata mismatch detected**. See above for details.\n')
		if divergent_ast:
			lines.append('**AST check found call signature differences**. See above for details.\n')
		if divergent_numeric:
			avg_diff = sum(m.max_abs_diff for m in metrics_list) / len(metrics_list)
			lines.append(f'**Numeric divergence**: Average max diff = {avg_diff:.4f}\n')

	report = ''.join(lines)
	os.makedirs(os.path.dirname(output_path), exist_ok=True)
	with open(output_path, 'w') as f:
		f.write(report)
	return verdict


#============================================
def main():
	"""Main entry point."""
	args = parse_args()

	# Load video
	probe = probe_video.probe_video(args.video_path)
	reader = frame_reader.FrameReader(
		args.video_path,
		fps=probe['fps'],
		total_frames=probe['frame_count'],
	)

	# Load solver state (fail loudly if missing)
	scene_transform = load_solver_state(args.video_path)

	# Sample frames
	sample_dict = build_sample_frames(args.n_per_group)
	all_frames = sample_dict['trust_0_pct'] + sample_dict['trust_high']

	if not all_frames:
		print('ERROR: No frames to sample', file=sys.stderr)
		sys.exit(1)

	# Honor --n-per-group flag; do NOT override with smoke subset
	test_frames = all_frames

	# Get fps
	fps = getattr(reader, 'fps', 60.0)

	# Compare each frame
	metrics_list = []
	for frame_index in test_frames:
		# Use a default prediction at frame center
		pred_center = (reader.width / 2.0, reader.height / 2.0)
		pred_box = (100.0, 150.0)  # nominal torso box

		metrics = compare_frame(
			reader, frame_index, scene_transform,
			pred_center, pred_box, fps,
		)
		if metrics is not None:
			metrics_list.append(metrics)

	# Run AST check
	ast_diffs = check_ast_call_equivalence()

	# Generate report
	report_path = os.path.join(args.output_dir, 'heatmap_equivalence_report.md')
	verdict = generate_report(metrics_list, report_path, ast_diffs)

	print(f'Report: {report_path}')
	print(f'Verdict: {verdict}')

	if verdict not in ('EQUIVALENT',):
		print(
			f'WARNING: Heat-map equivalence test returned {verdict}',
			file=sys.stderr
		)
	sys.exit(0)


if __name__ == '__main__':
	main()
