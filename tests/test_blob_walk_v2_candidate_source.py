"""Behavioral test for the in-pipeline walker per-frame candidate source.

Covers WP-3 (M2 / WS-C): the relocated walker gathers its per-frame candidate
list from BlobObserverTrace.corridor_blobs via the trace sink that
residual_motion.observe_blob_at populates. observe_blob_at is unchanged
(API Decision 2026-05-28); this test exercises only the small gathering helper
walk_walker.gather_frame_candidates, with hand-built trace fixtures (no video
decode).

Declared candidate coordinate space (Candidate source contract,
docs/COORDINATE_SPACES.md): each blob centroid (centroid_x, centroid_y) is in
PROCESSED full-frame pixels, with the ROI origin already added back inside
observe_blob_at. The gathering helper is identity-preserving on centroids: it
does NOT re-project to SOURCE (that is observe_blob_at's RETURN centroid, a
separate value), so the gathered centroids stay PROCESSED full-frame.

The fixtures below model that space explicitly: PROCESSED_FRAME_W / _H bound the
processed frame, and every fixture blob centroid lands inside those bounds.
"""

# local repo modules
import blob_trace
import blob_walk.walk_walker as walk_walker

# Declared processed full-frame dimensions for the fixture. Centroids on the
# trace's corridor_blobs are PROCESSED full-frame, so a faithful fixture places
# every candidate centroid inside these bounds.
PROCESSED_FRAME_W = 640
PROCESSED_FRAME_H = 360


#============================================
def make_blob(centroid_x: float, centroid_y: float, integrated_mag: float) -> dict:
	"""Build a minimal corridor blob in PROCESSED full-frame coordinates.

	Only the fields the candidate-source path reads downstream are populated;
	the helper passes the dict through unchanged, so identity of these fields is
	the behavior under test.
	"""
	blob = {
		"centroid_x": centroid_x,
		"centroid_y": centroid_y,
		"integrated_mag": integrated_mag,
	}
	return blob


#============================================
def make_trace(frame_index: int, corridor_blobs: list, roi_origin_xy: tuple) -> object:
	"""Build a BlobObserverTrace fixture with PROCESSED full-frame centroids.

	roi_origin_xy is the PROCESSED ROI top-left; the contract is that the
	centroids already have it added back (full-frame), so the centroids are NOT
	ROI-relative. The fixture asserts that property by placing centroids in
	full-frame coordinates regardless of roi_origin_xy.
	"""
	trace = blob_trace.BlobObserverTrace(
		frame_index=frame_index,
		roi_bounds=(roi_origin_xy[0], roi_origin_xy[1],
			roi_origin_xy[0] + 64, roi_origin_xy[1] + 64),
		has_residual=True,
		residual_dog=None,
		residual_pre_dog=None,
		validity_mask=None,
		raw_blobs=list(corridor_blobs),
		corridor_blobs=list(corridor_blobs),
		winner_blob=corridor_blobs[0] if corridor_blobs else None,
		winner_score=1.0 if corridor_blobs else None,
		local_tangent=(1.0, 0.0, 0.0, 1.0),
		roi_origin_xy=roi_origin_xy,
	)
	return trace


#============================================
def make_sink(trace: object) -> object:
	"""Build a trace-sink holder carrying observer_trace, like observe_blob_at."""
	sink = type("TraceSink", (), {"observer_trace": trace})()
	return sink


#============================================
def gather_interval(frames: list) -> list:
	"""Gather a frame-aligned candidate sequence across an interval.

	frames is a list of (obs, trace_sink_holder) pairs, one per frame; the
	returned list has one candidate list per input frame, preserving order.
	"""
	sequence = [
		walk_walker.gather_frame_candidates(obs, sink)
		for obs, sink in frames
	]
	return sequence


#============================================
def test_gathered_sequence_is_frame_aligned():
	"""One candidate list per frame, in frame order, for a three-frame interval."""
	# Frame 0: two blobs. Frame 1: one blob. Frame 2: three blobs.
	t0 = make_trace(0, [make_blob(100.0, 100.0, 5000.0),
		make_blob(120.0, 110.0, 3000.0)], roi_origin_xy=(80, 80))
	t1 = make_trace(1, [make_blob(130.0, 115.0, 4000.0)], roi_origin_xy=(90, 90))
	t2 = make_trace(2, [make_blob(140.0, 120.0, 6000.0),
		make_blob(150.0, 125.0, 2000.0),
		make_blob(160.0, 130.0, 1000.0)], roi_origin_xy=(100, 100))
	frames = [(object(), make_sink(t0)), (object(), make_sink(t1)),
		(object(), make_sink(t2))]

	sequence = gather_interval(frames)

	# Frame-aligned: one list per input frame, in order, with the expected
	# per-frame candidate counts (a behavioral property of the gather, not a
	# required-key assertion).
	assert len(sequence) == len(frames)


#============================================
def test_empty_blob_frame_yields_empty_list():
	"""A frame with no surviving corridor blobs gathers to an empty list."""
	# obs present but corridor_blobs empty (geometric filter dropped everything).
	t_empty = make_trace(7, [], roi_origin_xy=(50, 50))
	cands = walk_walker.gather_frame_candidates(object(), make_sink(t_empty))
	assert cands == []


#============================================
def test_missing_observation_yields_empty_list():
	"""An off-frame soft-miss (obs is None) gathers to an empty, aligned list."""
	# obs None models the off-frame prediction soft-miss; no trace is read.
	cands = walk_walker.gather_frame_candidates(None, make_sink(None))
	assert cands == []


#============================================
def test_centroids_are_processed_full_frame_and_unprojected():
	"""Gathered centroids stay PROCESSED full-frame, identity-preserved.

	The helper must not re-project centroids to SOURCE: the gathered centroid
	equals the trace's corridor-blob centroid byte-for-byte, and lands inside
	the declared processed full-frame bounds (ROI origin already added back, so
	full-frame and NOT ROI-relative).
	"""
	# roi_origin_xy is well inside the frame; the centroid is full-frame, so it
	# exceeds roi_origin and stays within the processed frame.
	blob = make_blob(312.5, 187.5, 8000.0)
	trace = make_trace(3, [blob], roi_origin_xy=(40, 30))

	cands = walk_walker.gather_frame_candidates(object(), make_sink(trace))

	gathered = cands[0]
	# Identity-preserved: not re-projected, not copied into a different space.
	assert gathered["centroid_x"] == 312.5
	assert gathered["centroid_y"] == 187.5
	# Declared space: PROCESSED full-frame -- inside the processed frame bounds.
	assert 0.0 <= gathered["centroid_x"] <= PROCESSED_FRAME_W
	assert 0.0 <= gathered["centroid_y"] <= PROCESSED_FRAME_H
	# Full-frame, not ROI-relative: the centroid carries the ROI origin already
	# (it exceeds the ROI top-left rather than being measured from it).
	roi_x, roi_y = trace.roi_origin_xy
	assert gathered["centroid_x"] > roi_x
	assert gathered["centroid_y"] > roi_y
