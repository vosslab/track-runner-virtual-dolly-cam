"""C5 behavior guard for robust torso-size spike hardening in the crop.

The crop SIZE (w/h) channel runs a robust outlier rejector
(track_runner.torso_size_stabilizer) BEFORE the cosmetic size-EMA, wired
once into tr_crop.trajectory_to_crop_rects so both crop modes benefit.
This is size-spike hardening, not a zoom-bounce fix: it rejects isolated
single-frame torso w/h outliers and preserves real multi-frame scale
ramps, but does NOT remove broadband sub-pixel breathing. These tests
lock in the two load-bearing behaviors the wiring must satisfy:

1. Single-frame torso w/h spikes on an otherwise stable-scale runner must
   NOT reach the crop height: with a stable center, the crop-height change
   stays bounded (the isolated spike is rejected).
2. A genuine multi-frame scale ramp (runner approaching, torso growing
   monotonically) MUST still be tracked: the crop height grows across the
   ramp; the robust stage only kills single-frame spikes, never a trend.

Position (cx, cy) is never edited by the size stabilizer (C5
separability); only w/h are routed through the robust filter.
"""

# PIP3 modules
import numpy

# local repo modules
import tr_crop
import torso_size_stabilizer


#============================================
def _video_info(frame_width: int, frame_height: int, n: int) -> dict:
	return {
		"width": frame_width,
		"height": frame_height,
		"frame_count": n,
		"fps": 60.0,
	}


#============================================
def _config(crop_mode: str) -> dict:
	return {
		"processing": {
			"crop_mode": crop_mode,
			"crop_aspect": "16:9",
			"torso_height_multiple": 8.0,
		},
	}


#============================================
def _crop_heights(crop_rects: list) -> numpy.ndarray:
	# crop rect tuple is (x, y, w, h); height is index 3
	heights = numpy.array([rect[3] for rect in crop_rects], dtype=float)
	return heights


#============================================
def _stable_traj_with_spikes(n: int, spike_frames: list) -> list:
	# stable center, stable-scale torso, with single-frame w/h spikes
	base_h = 120.0
	trajectory = []
	for i in range(n):
		h = base_h
		w = 0.45 * base_h
		if i in spike_frames:
			# isolated single-frame spike: torso suddenly 60% larger
			h = base_h * 1.6
			w = 0.45 * h
		state = {
			"cx": 960.0,
			"cy": 540.0,
			"w": w,
			"h": h,
			"conf": 1.0,
			"source": "test",
		}
		trajectory.append(state)
	return trajectory


#============================================
def _ramp_traj(n: int, ramp_start: int, ramp_end: int) -> list:
	# stable center, torso height ramps from 80 to 200 px across the ramp
	# span (genuine scale change: runner approaching the camera)
	lo_h = 80.0
	hi_h = 200.0
	trajectory = []
	for i in range(n):
		if i <= ramp_start:
			h = lo_h
		elif i >= ramp_end:
			h = hi_h
		else:
			frac = (i - ramp_start) / float(ramp_end - ramp_start)
			h = lo_h + frac * (hi_h - lo_h)
		w = 0.45 * h
		state = {
			"cx": 960.0,
			"cy": 540.0,
			"w": w,
			"h": h,
			"conf": 1.0,
			"source": "test",
		}
		trajectory.append(state)
	return trajectory


#============================================
def test_single_frame_spike_rejected_direct_center():
	# Size-spike hardening: isolated single-frame torso spikes on a
	# stable-scale runner do not reach the crop height in direct_center mode.
	frame_width = 1920
	frame_height = 1080
	n = 120
	spike_frames = [30, 60, 90]
	trajectory = _stable_traj_with_spikes(n, spike_frames)
	info = _video_info(frame_width, frame_height, n)
	rects = tr_crop.trajectory_to_crop_rects(
		trajectory, info, _config("direct_center"),
	)
	heights = _crop_heights(rects)
	# on a stable-scale runner the crop height should be effectively
	# constant; max-min spread is dominated by integer rounding, not a
	# 60% spike (which would otherwise blow the spread past ~200 px).
	spread = float(numpy.max(heights) - numpy.min(heights))
	assert spread <= 3.0, f"crop-height spread {spread} px shows spike not rejected"


#============================================
def test_single_frame_spike_rejected_smooth():
	# Same guard for the smooth (CropController) mode: one wiring point in
	# trajectory_to_crop_rects must protect both modes.
	frame_width = 1920
	frame_height = 1080
	n = 120
	spike_frames = [30, 60, 90]
	trajectory = _stable_traj_with_spikes(n, spike_frames)
	info = _video_info(frame_width, frame_height, n)
	rects = tr_crop.trajectory_to_crop_rects(
		trajectory, info, _config("smooth"),
	)
	heights = _crop_heights(rects)
	spread = float(numpy.max(heights) - numpy.min(heights))
	assert spread <= 3.0, f"crop-height spread {spread} px shows spike not rejected"


#============================================
def test_multi_frame_ramp_still_tracked_direct_center():
	# The robust stage must NOT distort a genuine scale ramp: the crop
	# height must grow monotonically (within rounding) across an
	# approaching-runner ramp and reach a clearly larger value at the end.
	frame_width = 1920
	frame_height = 1080
	n = 160
	trajectory = _ramp_traj(n, ramp_start=40, ramp_end=120)
	info = _video_info(frame_width, frame_height, n)
	rects = tr_crop.trajectory_to_crop_rects(
		trajectory, info, _config("direct_center"),
	)
	heights = _crop_heights(rects)
	# crop height before the ramp vs after the ramp: the 2.5x torso growth
	# must be tracked, not flattened by the robust filter.
	pre = float(numpy.mean(heights[10:30]))
	post = float(numpy.mean(heights[135:155]))
	assert post > pre * 1.8, (
		f"ramp not tracked: pre={pre:.1f} post={post:.1f} "
		f"(robust filter wrongly flattened a real scale change)"
	)


#============================================
def test_position_channel_untouched_by_size_stabilizer():
	# C5 separability: the size stabilizer edits only w/h. A trajectory
	# with size spikes but a moving center must produce the same crop
	# CENTER path as the unstabilized trajectory (the spikes change only
	# size). We verify the stabilizer leaves cx/cy byte-equal.
	n = 80
	trajectory = []
	for i in range(n):
		cx = 960.0 + 5.0 * i
		cy = 540.0 - 2.0 * i
		h = 120.0 if i != 40 else 200.0
		state = {"cx": cx, "cy": cy, "w": 0.45 * h, "h": h}
		trajectory.append(state)
	stabilized = torso_size_stabilizer.stabilize_trajectory(
		trajectory, method="hampel", window=7,
	)
	for i in range(n):
		assert stabilized[i]["cx"] == trajectory[i]["cx"]
		assert stabilized[i]["cy"] == trajectory[i]["cy"]
