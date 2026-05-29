"""Test winner mode disagreement: production_winner vs audit_winner.

Constructs a synthetic trace with two corridor blobs where the
production_winner and audit_winner (center_of_mass rule) disagree.
Verifies both modes resolve a winner and the debug row carries both choices.
"""

import sys
import os

# Ensure imports work
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tools_blob_walk_v2 = os.path.join(repo_root, "tools", "blob_walk_v2")

if tools_blob_walk_v2 not in sys.path:
	sys.path.insert(0, tools_blob_walk_v2)
# Package root is on sys.path, so the bare import walk_paths resolves; setup()
# adds track_runner, tests, repo root, and the core/ and render/ subdirs where
# walk_walker now lives.
import walk_paths
walk_paths.setup()

import walk_walker
import blob_trace


def test_audit_winner_center_of_mass_disagrees_with_production():
	"""Test resolve_audit_winner when production and audit winners differ."""
	# Create two blobs: one stronger (production winner), one heavier-integrated
	blob_1 = {
		"centroid_x": 100.0,
		"centroid_y": 150.0,
		"area": 50,
		"integrated_mag": 100.0,  # weaker but closer to the reference
		"cross_track": 0.0,
		"along_track": 0.0,
		"strength_score": 0.8,
		"size_score": 0.7,
		"proximity_score": 0.9,
		"total_score": 0.8,  # production winner by score
	}
	blob_2 = {
		"centroid_x": 120.0,
		"centroid_y": 180.0,
		"area": 60,
		"integrated_mag": 500.0,  # much heavier; will dominate center of mass
		"cross_track": 20.0,
		"along_track": 30.0,
		"strength_score": 0.6,
		"size_score": 0.5,
		"proximity_score": 0.5,
		"total_score": 0.53,  # weaker by score
	}

	# Create trace with both blobs in corridor, blob_1 as production winner
	trace = blob_trace.BlobObserverTrace(
		frame_index=100,
		roi_bounds=(0, 0, 200, 300),
		has_residual=True,
		residual_dog=None,
		residual_pre_dog=None,
		validity_mask=None,
		raw_blobs=[blob_1, blob_2],
		corridor_blobs=[blob_1, blob_2],
		winner_blob=blob_1,  # production chose blob_1
		winner_score=0.8,
		local_tangent=(1.0, 0.0, 0.0, 1.0),
		roi_origin_xy=(0, 0),
		acceptance_box=None,
		dog_diameter=50.0,
		corridor_radius=100.0,
	)

	# Resolve audit winner using center_of_mass rule.
	# pred_center is arbitrary for center_of_mass; use blob_1's position.
	pred_center = (blob_1["centroid_x"], blob_1["centroid_y"])
	audit_blob = walk_walker.resolve_audit_winner(trace, "center_of_mass", pred_center)

	# The center of mass is weighted toward blob_2 (integrated_mag=500 vs 100).
	# Expected: the audit winner should be blob_2 (or at least closer to its centroid).
	assert audit_blob is not None, "audit_winner should find a blob"
	assert audit_blob["integrated_mag"] == 500.0, (
		f"audit_winner should favor blob_2 (integrated_mag=500), got {audit_blob['integrated_mag']}"
	)

	# Verify production winner is different.
	assert trace.winner_blob["integrated_mag"] == 100.0, "production winner should be blob_1"

	# Both are valid blobs; they should differ in centroid.
	assert (
		abs(trace.winner_blob["centroid_x"] - audit_blob["centroid_x"]) > 0.5 or
		abs(trace.winner_blob["centroid_y"] - audit_blob["centroid_y"]) > 0.5
	), "production_winner and audit_winner should have different centroids"


def test_audit_winner_strongest_blob():
	"""Test resolve_audit_winner with strongest_blob rule."""
	blobs = [
		{
			"centroid_x": 100.0,
			"centroid_y": 150.0,
			"area": 50,
			"integrated_mag": 100.0,
		},
		{
			"centroid_x": 120.0,
			"centroid_y": 180.0,
			"area": 60,
			"integrated_mag": 500.0,  # strongest
		},
		{
			"centroid_x": 110.0,
			"centroid_y": 160.0,
			"area": 55,
			"integrated_mag": 250.0,
		},
	]

	trace = blob_trace.BlobObserverTrace(
		frame_index=100,
		roi_bounds=(0, 0, 200, 300),
		has_residual=True,
		residual_dog=None,
		residual_pre_dog=None,
		validity_mask=None,
		raw_blobs=blobs,
		corridor_blobs=blobs,
		winner_blob=blobs[0],  # production chose first
		winner_score=0.5,
		local_tangent=(1.0, 0.0, 0.0, 1.0),
	)

	pred_center = (100.0, 150.0)
	audit_blob = walk_walker.resolve_audit_winner(trace, "strongest_blob", pred_center)

	assert audit_blob is not None
	assert audit_blob["integrated_mag"] == 500.0, "Should return strongest blob"


def test_audit_winner_body_position():
	"""Test resolve_audit_winner with body_position rule using pred_center."""
	blobs = [
		{
			"centroid_x": 100.0,
			"centroid_y": 150.0,
			"area": 50,
			"integrated_mag": 100.0,
			"dist_to_pred_px": 10.0,  # far from predicted center at (110, 160)
		},
		{
			"centroid_x": 105.0,
			"centroid_y": 152.0,
			"area": 60,
			"integrated_mag": 200.0,
			"dist_to_pred_px": 5.0,  # closer to predicted center
		},
		{
			"centroid_x": 200.0,
			"centroid_y": 300.0,
			"area": 100,
			"integrated_mag": 300.0,
			"dist_to_pred_px": 200.0,  # far away
		},
	]

	trace = blob_trace.BlobObserverTrace(
		frame_index=100,
		roi_bounds=(0, 0, 400, 400),
		has_residual=True,
		residual_dog=None,
		residual_pre_dog=None,
		validity_mask=None,
		raw_blobs=blobs,
		corridor_blobs=blobs,
		winner_blob=blobs[0],  # production chose blob_0
		winner_score=0.8,
		local_tangent=(1.0, 0.0, 0.0, 1.0),
	)

	# Predicted center is at (110, 160), which is far from blob_0 but close to blob_1.
	pred_center = (110.0, 160.0)
	audit_blob = walk_walker.resolve_audit_winner(trace, "body_position", pred_center)

	assert audit_blob is not None
	# body_position rule should pick the blob closest to pred_center (110, 160).
	# blob_1 at (105, 152) is closer than blob_0 at (100, 150).
	assert audit_blob["centroid_x"] == blobs[1]["centroid_x"], (
		"body_position rule should favor blob_1, closest to pred_center (110, 160)"
	)


def test_audit_winner_empty_corridor():
	"""Test resolve_audit_winner with no corridor blobs returns None."""
	trace = blob_trace.BlobObserverTrace(
		frame_index=100,
		roi_bounds=(0, 0, 200, 300),
		has_residual=True,
		residual_dog=None,
		residual_pre_dog=None,
		validity_mask=None,
		raw_blobs=[],
		corridor_blobs=[],  # empty
		winner_blob=None,
		winner_score=None,
		local_tangent=(1.0, 0.0, 0.0, 1.0),
	)

	pred_center = (100.0, 150.0)
	audit_blob = walk_walker.resolve_audit_winner(trace, "center_of_mass", pred_center)
	assert audit_blob is None, "Should return None for empty corridor"
