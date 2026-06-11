"""Unit tests for the walker neighbor-seed crossing predicate.

Covers the P12 stride-termination fix in
`blob_walk.walk_walker._neighbor_reached`: the directional
crossing test that replaced a strict equality check so a stride > 1 walk
terminates AT the neighbor seed instead of overrunning into the adjacent
interval when the interval span is not divisible by stride.
"""

# local repo modules
import blob_walk.walk_walker as walk_walker


#============================================
def walk_frames(seed_frame: int, neighbor_seed_frame: int, stride: int, sign: int) -> tuple:
	"""Replay the walker stepping loop using only the crossing predicate.

	Mirrors `_run_windowed_steps`: step from `seed_frame` by `sign * stride`
	and stop on the first frame that reaches or passes the neighbor seed,
	returning the observed (pre-termination) frames plus the clamped final
	frame. The seed endpoints are never in the observed list.

	Args:
		seed_frame: Walk origin seed frame index.
		neighbor_seed_frame: Termination-target seed frame index.
		stride: Per-step frame delta magnitude.
		sign: +1 forward, -1 backward.

	Returns:
		(observed_frames, final_frame) where observed_frames are the frames
		the walk would observe and final_frame is the clamped terminal frame.
	"""
	span = abs(neighbor_seed_frame - seed_frame)
	max_steps = span + 10
	observed = []
	step = 1
	while True:
		if step > max_steps:
			raise RuntimeError(
				f"walk_frames did not terminate after {max_steps} steps "
				f"(seed={seed_frame}, neighbor={neighbor_seed_frame}, stride={stride}); "
				"predicate broken"
			)
		frame_f = seed_frame + sign * step * stride
		if walk_walker._neighbor_reached(frame_f, neighbor_seed_frame, sign):
			frame_f = neighbor_seed_frame
			return observed, frame_f
		observed.append(frame_f)
		step += 1


#============================================
def test_stride1_fwd_fires_exactly_when_equality_would():
	# At stride 1 the step lands exactly on the neighbor seed; the crossing
	# test must fire there and nowhere earlier (equality-equivalent).
	assert walk_walker._neighbor_reached(16591, 16591, 1) is True
	assert walk_walker._neighbor_reached(16590, 16591, 1) is False


#============================================
def test_stride1_fwd_walk_observes_interior_only():
	observed, final_frame = walk_frames(100, 104, stride=1, sign=1)
	# Interior frames observed, both seed endpoints excluded.
	assert observed == [101, 102, 103]
	assert final_frame == 104


#============================================
def test_stride2_even_span_lands_exactly_clamp_is_noop():
	# Even span at stride 2: the step lands exactly on the seed, so the
	# clamp changes nothing and the interior is the stride-2 subset.
	observed, final_frame = walk_frames(100, 106, stride=2, sign=1)
	assert observed == [102, 104]
	assert final_frame == 106


#============================================
def test_stride2_odd_span_crossing_detected_and_clamped_fwd():
	# Interval #164 FWD: span 3 (16588 -> 16591) at stride 2. Step 1 lands
	# on 16590; step 2 computes 16592 which overshoots, so the crossing test
	# fires and frame_f clamps to the seed 16591. Frame 16592 is never
	# observed.
	observed, final_frame = walk_frames(16588, 16591, stride=2, sign=1)
	assert observed == [16590]
	assert final_frame == 16591


#============================================
def test_stride2_odd_span_crossing_detected_and_clamped_bwd():
	# Interval #164 BWD: from 16591 toward 16588 at stride 2. Step 1 lands
	# on 16589; step 2 computes 16587 which overshoots below the seed, so the
	# crossing test fires and frame_f clamps to 16588. Frame 16587 is never
	# observed.
	observed, final_frame = walk_frames(16591, 16588, stride=2, sign=-1)
	assert observed == [16589]
	assert final_frame == 16588


#============================================
def test_bwd_predicate_mirrors_fwd():
	# Descending walk: predicate fires on reach-or-pass below the seed.
	assert walk_walker._neighbor_reached(16588, 16588, -1) is True
	assert walk_walker._neighbor_reached(16587, 16588, -1) is True
	assert walk_walker._neighbor_reached(16589, 16588, -1) is False


#============================================
def test_span_smaller_than_stride_terminates_immediately():
	# Span 1 at stride 2: the first computed frame already crosses the seed,
	# so the walk observes nothing and terminates at once (Hermite fallback
	# covers output downstream).
	observed_fwd, final_fwd = walk_frames(200, 201, stride=2, sign=1)
	assert observed_fwd == []
	assert final_fwd == 201

	observed_bwd, final_bwd = walk_frames(201, 200, stride=2, sign=-1)
	assert observed_bwd == []
	assert final_bwd == 200
