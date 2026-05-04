"""Guards for trajectory_to_crop_rects input bounds.

Locks in P2-1 from /Users/vosslab/.claude/plans/rustling-juggling-creek.md:
a trajectory longer than the source frame count is a wiring bug (mismatched
video_info, stale solve cache) and must fail loudly instead of silently
truncating to total_frames.
"""

# PIP3 modules
import pytest

# local repo modules
import tr_crop


#============================================
def _video_info(frame_count: int) -> dict:
	return {"frame_count": frame_count, "width": 100, "height": 100, "fps": 30.0}


def _config() -> dict:
	# trajectory_to_crop_rects -> CropController.from_config requires these
	return {"processing": {"crop_aspect": "16:9", "torso_height_multiple": 3.5}}


def test_raises_when_trajectory_longer_than_total_frames():
	# 5 trajectory entries vs. 3 declared frames must raise
	trajectory = [{"cx": 50.0, "cy": 50.0, "w": 20.0, "h": 40.0}] * 5
	with pytest.raises(AssertionError, match="trajectory longer"):
		tr_crop.trajectory_to_crop_rects(trajectory, _video_info(3), _config())


def test_shorter_trajectory_pads_via_hold_state():
	# 2 real entries followed by 3 implicit gap-fill frames is the happy
	# path the assertion does not trip on; the result is exactly
	# total_frames long and the trailing rects reuse the last known size.
	trajectory = [
		{"cx": 50.0, "cy": 50.0, "w": 20.0, "h": 40.0, "conf": 0.9},
		{"cx": 60.0, "cy": 50.0, "w": 20.0, "h": 40.0, "conf": 0.9},
	]
	rects = tr_crop.trajectory_to_crop_rects(trajectory, _video_info(5), _config())
	assert len(rects) == 5
