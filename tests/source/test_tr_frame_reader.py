"""Fast unit tests for FrameReader validation and frame geometry."""

# PIP3 modules
import pytest

# local repo modules
import common_tools.frame_reader


#============================================
def test_rejects_non_mkv_path() -> None:
	# .mkv-only restriction: a .mov path must raise before any decode
	# is attempted, with a message naming mkvmerge so the user knows
	# how to remux.
	with pytest.raises(ValueError, match="mkvmerge"):
		common_tools.frame_reader.FrameReader(
			video_path="not_real.mov",
			fps=30.0,
			total_frames=10,
		)


#============================================
def test_geometry_roundtrip_identity() -> None:
	geom = common_tools.frame_reader._resolve_frame_geometry(3840, 2160, 4)
	# round trip source -> processed -> source within 0.5 px
	src_x, src_y = 1234.0, 567.0
	px, py = geom.source_to_processed(src_x, src_y)
	rx, ry = geom.processed_to_source(px, py)
	assert abs(rx - src_x) < 0.5 and abs(ry - src_y) < 0.5
	# delta round trip
	dx, dy = 12.0, -7.5
	pdx, pdy = geom.source_to_processed_delta(dx, dy)
	rdx, rdy = geom.processed_to_source_delta(pdx, pdy)
	assert abs(rdx - dx) < 1e-9 and abs(rdy - dy) < 1e-9


#============================================
def test_geometry_pure_scale_no_offset() -> None:
	# origin maps to origin both directions
	geom = common_tools.frame_reader._resolve_frame_geometry(3840, 2160, 4)
	assert geom.source_to_processed(0.0, 0.0) == (0.0, 0.0)
	assert geom.processed_to_source(0.0, 0.0) == (0.0, 0.0)
