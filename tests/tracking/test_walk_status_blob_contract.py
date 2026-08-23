"""Selected-path blobs must carry their own centroid."""

# PIP3 modules
import pytest

# local repo modules
import blob_walk.walk_status as walk_status


#============================================
def test_selected_blob_without_centroid_raises() -> None:
	"""A path blob missing centroid_x fails where it is read."""
	malformed_blob = {"centroid_y": 4.0, "integrated_mag": 1.0}
	with pytest.raises(KeyError):
		walk_status.emit_status_from_path(
			[0], [[malformed_blob]], [malformed_blob], None, None,
		)


#============================================
def test_selected_blob_with_centroid_reports_accepted() -> None:
	"""A well-formed path blob yields its own centroid as the frame position."""
	blob = {"centroid_x": 7.0, "centroid_y": 4.0, "integrated_mag": 1.0}
	results = walk_status.emit_status_from_path(
		[0], [[blob]], [blob], None, None,
	)
	assert results[0]["status"] == "accepted"
	assert (results[0]["cx"], results[0]["cy"]) == (7.0, 4.0)
