"""M1-D measurement finding: verify that lighten_trace drops residual_dog.

The M1-D corpus run revealed that ALL tiles in a live --walk run appear as
NOT-COMPUTED (in_box_heat_computed=False) because walk_walker.lighten_trace
drops residual_dog/validity_mask BEFORE the trace reaches the manifest heat
computation in walk_driver._render_direction_tiles.

This test captures that structural fact:
  1. lighten_trace always sets residual_dog=None.
  2. When residual_dog is None on a trace, in_box_heat_computed is False
     (has_live_residual=False in the driver).
  3. The check_render_manifest heat report correctly categorizes these tiles
     as not-computed (skipped from the denominator).

These are pure structural/contract tests -- no corpus video required.
"""

# Standard Library
import sys
import pathlib

# shared sys.path bootstrap
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import file_utils
_REPO_ROOT = file_utils.get_repo_root()
sys.path.insert(0, str(pathlib.Path(_REPO_ROOT) / "tools" / "blob_walk_v2"))
sys.path.insert(0, str(pathlib.Path(_REPO_ROOT) / "track_runner"))
sys.path.insert(0, str(pathlib.Path(_REPO_ROOT) / "common_tools"))
sys.path.insert(0, str(pathlib.Path(_REPO_ROOT) / "tests"))

# local repo modules
import walk_paths
walk_paths.setup()

import numpy
import blob_trace
import blob_walk.walk_walker as walk_walker


#============================================
def _make_live_trace(frame_index: int = 42) -> blob_trace.BlobObserverTrace:
	"""Build a minimal 'live' trace with a non-None residual_dog array."""
	residual_dog = numpy.ones((10, 10), dtype=numpy.float32) * 20.0
	validity_mask = numpy.ones((10, 10), dtype=numpy.uint8) * 255
	return blob_trace.BlobObserverTrace(
		frame_index=frame_index,
		roi_bounds=(0, 0, 10, 10),
		has_residual=True,
		residual_dog=residual_dog,
		residual_pre_dog=None,
		validity_mask=validity_mask,
		raw_blobs=[],
		corridor_blobs=[],
		winner_blob=None,
		winner_score=None,
		local_tangent=(1.0, 0.0, 0.0, 1.0),
		roi_origin_xy=(0, 0),
	)


#============================================
def test_lighten_trace_drops_residual_dog():
	"""lighten_trace always sets residual_dog=None -- confirmed design choice."""
	live_trace = _make_live_trace()
	# Before lightening: residual_dog is a real array.
	assert live_trace.residual_dog is not None
	assert live_trace.residual_dog.shape == (10, 10)

	light_trace = walk_walker.lighten_trace(live_trace)
	# After lightening: residual_dog is None.
	assert light_trace.residual_dog is None


#============================================
def test_lighten_trace_drops_validity_mask():
	"""lighten_trace also drops validity_mask, mirroring residual_dog."""
	live_trace = _make_live_trace()
	assert live_trace.validity_mask is not None

	light_trace = walk_walker.lighten_trace(live_trace)
	assert light_trace.validity_mask is None


#============================================
def test_lighten_trace_preserves_blobs_and_origin():
	"""lighten_trace preserves corridor_blobs, winner_blob, roi_origin_xy."""
	live_trace = _make_live_trace()
	# Add some blob data.
	live_trace = blob_trace.BlobObserverTrace(
		frame_index=live_trace.frame_index,
		roi_bounds=live_trace.roi_bounds,
		has_residual=live_trace.has_residual,
		residual_dog=live_trace.residual_dog,
		residual_pre_dog=live_trace.residual_pre_dog,
		validity_mask=live_trace.validity_mask,
		raw_blobs=[{"centroid_x": 5.0, "centroid_y": 5.0}],
		corridor_blobs=[{"centroid_x": 5.0, "centroid_y": 5.0}],
		winner_blob={"centroid_x": 5.0, "centroid_y": 5.0},
		winner_score=0.9,
		local_tangent=(1.0, 0.0, 0.0, 1.0),
		roi_origin_xy=(3, 7),
	)

	light_trace = walk_walker.lighten_trace(live_trace)
	# Heavy arrays dropped, overlay fields preserved.
	assert light_trace.residual_dog is None
	assert light_trace.roi_origin_xy == (3, 7)
	assert len(light_trace.corridor_blobs) == 1
	assert light_trace.winner_blob is not None


#============================================
def test_heat_computed_requires_live_residual_dog():
	"""Confirm: has_live_residual = (trace.residual_dog is not None).

	This mirrors walk_driver._render_direction_tiles line 635.
	A lightened trace has residual_dog=None -> has_live_residual=False ->
	in_box_heat_computed=False (NOT-COMPUTED).
	"""
	live_trace = _make_live_trace()
	light_trace = walk_walker.lighten_trace(live_trace)

	# Live trace: eligible for heat computation.
	has_live_residual_live = (live_trace.residual_dog is not None)
	assert has_live_residual_live is True

	# Lightened trace: NOT eligible.
	has_live_residual_light = (light_trace.residual_dog is not None)
	assert has_live_residual_light is False


#============================================
def test_check_manifest_skips_not_computed_tiles():
	"""A manifest with no heat keys still passes the two gates (C13 rework).

	Heat is no longer stored per tile; the gate over per-tile records only
	checks conversion_count and non-seed-missing-solved-box. A manifest from a
	post-lighten / render-only walk carries no heat keys and must still PASS.
	"""
	# Build a synthetic manifest with only gate fields (no heat keys).
	import check_render_manifest
	records = []
	for i in range(10):
		records.append({
			"frame_index": i,
			"seed_box_present": (i == 0),
			"solved_box_present": True,
			"conversion_count": 1,
			"direction": "FWD",
			"non_seed_missing_solved_box": False,
		})

	# check_records returns no failures (NOT-COMPUTED does not fail the gate).
	failures = check_render_manifest.check_records(records, "synthetic_manifest.json")
	assert failures == []
