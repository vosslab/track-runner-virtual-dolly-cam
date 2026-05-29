"""
Unit tests for replay_step1.classify_failure_5bin.

Loads one real dump_step1 JSON (BWD_1260.json from Glenbrook) as the
fixture for bins that require corridor blobs.  Synthetic records cover
the remaining bins.
"""

# Standard Library
import sys
import json
import pathlib

# Add tools/blob_walk_v2 and repo root to path so imports work.
_REPO_ROOT = pathlib.Path(__file__).parent.parent
_TOOLS_DIR = _REPO_ROOT / 'tools' / 'blob_walk_v2'
if str(_TOOLS_DIR) not in sys.path:
	sys.path.insert(0, str(_TOOLS_DIR))
if str(_REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(_REPO_ROOT))
if str(_REPO_ROOT / 'track_runner') not in sys.path:
	sys.path.insert(0, str(_REPO_ROOT / 'track_runner'))

import replay_step1

#============================================
# Fixture: real JSON path
#============================================

_FIXTURE_JSON = (
	_REPO_ROOT
	/ 'dump_step1'
	/ '2025-Glenbrook_South-1600m-IMG_1503'
	/ 'BWD_1260.json'
)
_EMPTY_JSON = (
	_REPO_ROOT
	/ 'dump_step1'
	/ 'Conant-4x400-2026_April_15'
	/ 'BWD_1134.json'
)

#============================================
# Helper: build minimal seed pair
#============================================

def make_seed(frame_index: int, cx: float, cy: float, w: float = 20.0, h: float = 50.0) -> dict:
	"""Return a minimal seed dict compatible with get_human_seed_box_px."""
	return {
		'frame_index': frame_index,
		'torso_box': [cx - w / 2, cy - h / 2, w, h],
	}


#============================================
# Valid label set
#============================================

_VALID_LABELS = frozenset([
	'no_residual',
	'no_raw_blobs',
	'wrong_observable',
	'candidate_selection',
	'true_gate_failure',
	'unclassified',
])


#============================================
# Test: fixture JSON exists
#============================================

def test_fixture_json_exists():
	assert _FIXTURE_JSON.exists(), f'Fixture JSON missing: {_FIXTURE_JSON}'
	assert _EMPTY_JSON.exists(), f'Empty fixture JSON missing: {_EMPTY_JSON}'


#============================================
# Test: classifier returns a valid label on a real JSON
#============================================

def test_classify_5bin_returns_valid_label_on_real_json():
	"""Classifier must return one of 6 valid labels for any real capture."""
	with open(_FIXTURE_JSON) as f:
		data = json.load(f)
	step1 = data['step_1']
	# Build seed pair using actual seed frames from the capture.
	cx = step1['seed_cx_px']
	cy = step1['seed_cy_px']
	w = step1['seed_w_px']
	left_seed = make_seed(data['left_seed_frame'], cx, cy, w)
	right_seed = make_seed(data['right_seed_frame'], cx, cy, w)
	result = replay_step1.classify_failure_5bin(step1, left_seed, right_seed)
	assert result in _VALID_LABELS


#============================================
# Test: empty raw_blobs returns no_raw_blobs (pre-task-53 capture)
#============================================

def test_classify_5bin_no_blobs_returns_no_raw_blobs():
	"""
	When corridor_blobs is empty and obs_available is False with no
	residual_sum/residual_nonzero_px fields, classifier returns no_raw_blobs
	(pre-task-53 fallback).
	"""
	with open(_EMPTY_JSON) as f:
		data = json.load(f)
	step1 = data['step_1']
	# Verify the fixture is actually empty.
	assert not step1.get('corridor_blobs')
	assert not step1.get('obs_available', False)
	# No residual fields.
	assert step1.get('residual_sum') is None

	cx = step1['seed_cx_px']
	cy = step1['seed_cy_px']
	w = step1['seed_w_px']
	left_seed = make_seed(data['left_seed_frame'], cx, cy, w)
	right_seed = make_seed(data['right_seed_frame'], cx, cy, w)
	result = replay_step1.classify_failure_5bin(step1, left_seed, right_seed)
	assert result == 'no_raw_blobs'


#============================================
# Test: residual_sum == 0 returns no_residual
#============================================

def test_classify_5bin_zero_residual_sum_returns_no_residual():
	"""
	When residual_sum and residual_nonzero_px are both 0, bin is no_residual.
	"""
	record = {
		'frame_index': 100,
		'seed_w_px': 20.0,
		'corridor_blobs': [],
		'winner_blob': None,
		'obs_available': False,
		'motion_gate_result': None,
		'residual_sum': 0,
		'residual_nonzero_px': 0,
	}
	left_seed = make_seed(90, 500.0, 400.0)
	right_seed = make_seed(110, 510.0, 400.0)
	result = replay_step1.classify_failure_5bin(record, left_seed, right_seed)
	assert result == 'no_residual'


#============================================
# Test: no corridor blob within tolerance returns wrong_observable
#============================================

def test_classify_5bin_no_close_blob_returns_wrong_observable():
	"""
	When corridor_blobs are all far from runner ground truth, return wrong_observable.
	"""
	record = {
		'frame_index': 100,
		'seed_w_px': 20.0,
		'corridor_blobs': [
			{'centroid_x': 1000.0, 'centroid_y': 1000.0},  # far from runner
		],
		'winner_blob': {'centroid_x': 1000.0, 'centroid_y': 1000.0},
		'obs_available': True,
		'motion_gate_result': {'accepted': False},
		'residual_sum': 100,
		'residual_nonzero_px': 5,
	}
	# Runner ground truth is near (200, 200), far from blob at (1000, 1000).
	left_seed = make_seed(90, 200.0, 200.0, w=20.0)
	right_seed = make_seed(110, 200.0, 200.0, w=20.0)
	result = replay_step1.classify_failure_5bin(record, left_seed, right_seed)
	assert result == 'wrong_observable'


#============================================
# Test: runner blob in corridor but winner is different -> candidate_selection
#============================================

def test_classify_5bin_wrong_winner_returns_candidate_selection():
	"""
	When a corridor blob is within tolerance of the runner but production picked
	a different blob, return candidate_selection.
	"""
	runner_blob = {'centroid_x': 200.0, 'centroid_y': 200.0}
	wrong_winner = {'centroid_x': 300.0, 'centroid_y': 300.0}
	record = {
		'frame_index': 100,
		'seed_w_px': 20.0,
		'corridor_blobs': [runner_blob, wrong_winner],
		'winner_blob': wrong_winner,
		'obs_available': True,
		'motion_gate_result': {'accepted': True},
		'residual_sum': 50,
		'residual_nonzero_px': 3,
	}
	# Runner is at (200, 200); runner_blob centroid matches within tolerance.
	left_seed = make_seed(90, 200.0, 200.0, w=20.0)
	right_seed = make_seed(110, 200.0, 200.0, w=20.0)
	result = replay_step1.classify_failure_5bin(record, left_seed, right_seed)
	assert result == 'candidate_selection'


#============================================
# Test: winner is runner blob, gate rejected -> true_gate_failure
#============================================

def test_classify_5bin_gate_rejected_correct_winner_returns_true_gate_failure():
	"""
	When the winner IS the runner blob but the motion gate rejected it, return
	true_gate_failure.
	"""
	runner_blob = {'centroid_x': 200.0, 'centroid_y': 200.0}
	record = {
		'frame_index': 100,
		'seed_w_px': 20.0,
		'corridor_blobs': [runner_blob],
		'winner_blob': runner_blob,
		'obs_available': True,
		'motion_gate_result': {'accepted': False},
		'residual_sum': 50,
		'residual_nonzero_px': 3,
	}
	left_seed = make_seed(90, 200.0, 200.0, w=20.0)
	right_seed = make_seed(110, 200.0, 200.0, w=20.0)
	result = replay_step1.classify_failure_5bin(record, left_seed, right_seed)
	assert result == 'true_gate_failure'
