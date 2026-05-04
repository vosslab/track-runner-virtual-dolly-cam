"""Tests for track_runner/residual_pre_pass.py (M3+M4+M6).

Verifies that precompute_interval_residuals:
  - returns a non-empty store for a real interval;
  - produces residuals numerically close to the on-the-fly reader path;
  - returns None when reader is None;
  - returns an empty dict for a degenerate (too short) interval;
  - bounds the rolling BGR/gray buffers to the neighbor footprint (M6);
  - bounds them on long intervals independent of length (M6);
  - raises a clear RuntimeError when the safety-net cap is exceeded (M6);
  - keeps padding frames out of the gray buffer (M6);
  - never falls back to scattered reader.read_frame calls during compute (M6);
  - produces output byte-identical to a sequential-reader baseline (M6).
"""

# Standard Library
import pytest

# PIP3 modules
import numpy

# local repo modules (bare imports resolved by conftest.py)
import camera_motion
import residual_motion
import residual_pre_pass
import scene_coords
import velocity_model


# synthetic video geometry
_WIDTH = 320
_HEIGHT = 240
_N_FRAMES = 30
_FPS = 30.0


#============================================
def _make_gradient_frame(frame_index: int) -> numpy.ndarray:
	"""Build a BGR frame with a moving white square on a gradient background.

	The square moves 4 px/frame so consecutive frames have real motion,
	giving the residual computation a genuine signal to work with.

	Args:
		frame_index: Absolute frame index (0..N_FRAMES-1).

	Returns:
		BGR uint8 frame of shape (_HEIGHT, _WIDTH, 3).
	"""
	frame = numpy.zeros((_HEIGHT, _WIDTH, 3), dtype=numpy.uint8)
	# gradient background: horizontal ramp so warped frames differ
	for col in range(_WIDTH):
		val = int(col * 200 / _WIDTH)
		frame[:, col, :] = val

	# moving white square: 20x20 px, moves 4 px/frame in x
	sq_x = (frame_index * 4) % (_WIDTH - 30)
	sq_y = 80
	frame[sq_y:sq_y + 20, sq_x:sq_x + 20, :] = 255
	return frame


#============================================
class _FakeReader:
	"""In-memory video reader with the minimal API expected by residual code."""

	def __init__(self, frames: list, fps: float = _FPS):
		h, w = frames[0].shape[:2]
		self.width = int(w)
		self.height = int(h)
		self.frame_count = len(frames)
		self.fps = fps
		self._frames = frames
		self.geometry = None  # no binning

	#============================================
	def read_frame(self, frame_index: int):
		"""Return a copy of the stored frame at frame_index, or None."""
		if frame_index < 0 or frame_index >= self.frame_count:
			return None
		return self._frames[frame_index].copy()


#============================================
def _make_identity_transform(n_frames: int) -> scene_coords.SceneTransform:
	"""Build an all-zeros motion track (identity transform).

	Args:
		n_frames: Number of frames to cover.

	Returns:
		SceneTransform wrapping an all-zero MotionTrack.
	"""
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(n_frames, dtype=numpy.float32),
		dy=numpy.zeros(n_frames, dtype=numpy.float32),
		scale=numpy.ones(n_frames, dtype=numpy.float32),
		quality=numpy.ones(n_frames, dtype=numpy.float32),
	)
	return scene_coords.SceneTransform(motion)


#============================================
def _make_linear_curves(
	start_frame: int,
	end_frame: int,
	scene_transform: object,
) -> dict:
	"""Build fit_interval_curves for a linear-motion interval.

	Runner moves from x=80 to x=200 at constant y=120, with a 40x60
	box. Uses three seeds so slope estimation has neighbors.

	Args:
		start_frame: First seed frame index.
		end_frame: Last seed frame index.
		scene_transform: SceneTransform used to convert pixel -> scene.

	Returns:
		Dict from velocity_model.fit_interval_curves.
	"""
	# build all_seeds_scene with a flanking seed on each side
	n_frames = end_frame + 10
	all_seeds_scene = []
	seed_specs = [
		(max(0, start_frame - 5), 60.0),
		(start_frame, 80.0),
		(end_frame, 200.0),
		(min(n_frames - 1, end_frame + 5), 220.0),
	]
	for fi, cx in seed_specs:
		sx, sy, sw, sh = scene_transform.pixel_box_to_scene(fi, cx, 120.0, 40.0, 60.0)
		all_seeds_scene.append((fi, sx, sy, sw, sh))
	# deduplicate by frame index (in case clamp collides)
	seen = set()
	unique = []
	for entry in all_seeds_scene:
		if entry[0] not in seen:
			seen.add(entry[0])
			unique.append(entry)
	all_seeds_scene = sorted(unique, key=lambda e: e[0])

	left_seed = {
		"frame_index": start_frame, "cx": 80.0, "cy": 120.0,
		"w": 40.0, "h": 60.0, "status": "visible",
	}
	right_seed = {
		"frame_index": end_frame, "cx": 200.0, "cy": 120.0,
		"w": 40.0, "h": 60.0, "status": "visible",
	}
	return velocity_model.fit_interval_curves(
		left_seed, right_seed, all_seeds_scene, scene_transform,
	)


#============================================
def test_store_non_empty_for_normal_interval():
	"""precompute_interval_residuals returns a non-empty dict for a valid interval."""
	frames = [_make_gradient_frame(i) for i in range(_N_FRAMES)]
	reader = _FakeReader(frames)
	transform = _make_identity_transform(_N_FRAMES)
	start_frame, end_frame = 10, 20
	curves = _make_linear_curves(start_frame, end_frame, transform)
	fwd_raw = velocity_model._compute_raw_pred_forward(curves, transform)
	bwd_raw = velocity_model._compute_raw_pred_backward(curves, transform)

	# M2: use DEFAULT_HALF_WINDOW + stride (at _FPS=30, stride=1; behavior
	# is identical to the pre-M2 half_window=4 path)
	half_window = residual_motion.DEFAULT_HALF_WINDOW
	stride = residual_motion.resolve_stride(_FPS)
	seed_start = {"frame_index": start_frame}
	seed_end = {"frame_index": end_frame}

	store = residual_pre_pass.precompute_interval_residuals(
		reader=reader,
		scene_transform=transform,
		seed_start=seed_start,
		seed_end=seed_end,
		fwd_curve=fwd_raw,
		bwd_curve=bwd_raw,
		half_window=half_window,
		fps=_FPS,
		stride=stride,
	)
	assert store is not None
	assert len(store) > 0
	# every value must be a pair of uint8 arrays
	for key, val in store.items():
		res_u8, val_u8 = val
		assert res_u8.dtype == numpy.uint8
		assert val_u8.dtype == numpy.uint8


#============================================
def test_store_values_close_to_reader_path():
	"""Stored uint8 residuals are numerically close (atol=1) to the fresh reader path.

	Compute one (frame_index, roi) entry via precomputed_store, and the
	same entry fresh via compute_residual_for_frame with an open reader.
	The uint8 quantization should cause at most 1-unit error per pixel.
	"""
	frames = [_make_gradient_frame(i) for i in range(_N_FRAMES)]
	reader = _FakeReader(frames)
	transform = _make_identity_transform(_N_FRAMES)
	start_frame, end_frame = 10, 20
	curves = _make_linear_curves(start_frame, end_frame, transform)
	fwd_raw = velocity_model._compute_raw_pred_forward(curves, transform)
	bwd_raw = velocity_model._compute_raw_pred_backward(curves, transform)

	# M2: use DEFAULT_HALF_WINDOW + stride (at _FPS=30, stride=1)
	half_window = residual_motion.DEFAULT_HALF_WINDOW
	stride = residual_motion.resolve_stride(_FPS)
	seed_start = {"frame_index": start_frame}
	seed_end = {"frame_index": end_frame}

	store = residual_pre_pass.precompute_interval_residuals(
		reader=reader,
		scene_transform=transform,
		seed_start=seed_start,
		seed_end=seed_end,
		fwd_curve=fwd_raw,
		bwd_curve=bwd_raw,
		half_window=half_window,
		fps=_FPS,
		stride=stride,
	)
	assert store is not None and len(store) > 0

	# pick the first key to verify
	test_key = next(iter(store))
	test_frame, test_roi = test_key
	stored_u8, _stored_val = store[test_key]

	# compute fresh via the reader path (same half_window + stride so results match)
	fresh_cache = {}
	fresh_residual, _ = residual_motion.compute_residual_for_frame(
		reader=reader,
		frame_index=test_frame,
		scene_transform=transform,
		half_window=half_window,
		cache=fresh_cache,
		roi=test_roi,
		fps=float(_FPS),
		stride=stride,
	)
	assert fresh_residual is not None

	# uint8 quantization: atol=1 allows for rounding in clip+cast
	assert numpy.allclose(
		stored_u8.astype(numpy.float32),
		fresh_residual,
		atol=1.0,
	), "stored uint8 residual deviates from fresh float32 by more than 1"


#============================================
def test_returns_none_when_reader_is_none():
	"""precompute_interval_residuals returns None when reader=None."""
	transform = _make_identity_transform(_N_FRAMES)
	curves = _make_linear_curves(10, 20, transform)
	fwd_raw = velocity_model._compute_raw_pred_forward(curves, transform)
	bwd_raw = velocity_model._compute_raw_pred_backward(curves, transform)
	result = residual_pre_pass.precompute_interval_residuals(
		reader=None,
		scene_transform=transform,
		seed_start={"frame_index": 10},
		seed_end={"frame_index": 20},
		fwd_curve=fwd_raw,
		bwd_curve=bwd_raw,
		half_window=residual_motion.DEFAULT_HALF_WINDOW,
		fps=_FPS,
	)
	assert result is None


#============================================
def test_returns_empty_dict_for_degenerate_interval():
	"""precompute_interval_residuals returns {} when interval span < 2 frames."""
	frames = [_make_gradient_frame(i) for i in range(_N_FRAMES)]
	reader = _FakeReader(frames)
	transform = _make_identity_transform(_N_FRAMES)
	# span = 1 (too short)
	curves = _make_linear_curves(10, 10, transform)
	fwd_raw = velocity_model._compute_raw_pred_forward(curves, transform)
	bwd_raw = velocity_model._compute_raw_pred_backward(curves, transform)
	result = residual_pre_pass.precompute_interval_residuals(
		reader=reader,
		scene_transform=transform,
		seed_start={"frame_index": 10},
		seed_end={"frame_index": 10},
		fwd_curve=fwd_raw,
		bwd_curve=bwd_raw,
		half_window=residual_motion.DEFAULT_HALF_WINDOW,
		fps=_FPS,
	)
	assert result == {}


#============================================
def _run_pre_pass_for_m6(
	n_frames: int,
	start_frame: int,
	end_frame: int,
	half_window: int = None,
	stride: int = None,
	debug_stats: dict = None,
	read_log: list = None,
) -> dict:
	"""Run precompute_interval_residuals with a fresh synthetic reader.

	Shared harness used by the M6 memory-safety tests so each test does
	not have to repeat fake-reader/curves/transform setup.

	Args:
		n_frames: Total frames in the synthetic video.
		start_frame: First seed frame (inclusive).
		end_frame: Last seed frame (inclusive).
		half_window: Override DEFAULT_HALF_WINDOW.
		stride: Override resolve_stride(_FPS).
		debug_stats: Optional debug_stats dict to thread in.
		read_log: Optional read_log list to thread in.

	Returns:
		The store dict returned by precompute_interval_residuals.
	"""
	if half_window is None:
		half_window = residual_motion.DEFAULT_HALF_WINDOW
	if stride is None:
		stride = residual_motion.resolve_stride(_FPS)
	frames = [_make_gradient_frame(i) for i in range(n_frames)]
	reader = _FakeReader(frames)
	transform = _make_identity_transform(n_frames)
	curves = _make_linear_curves(start_frame, end_frame, transform)
	fwd_raw = velocity_model._compute_raw_pred_forward(curves, transform)
	bwd_raw = velocity_model._compute_raw_pred_backward(curves, transform)
	return residual_pre_pass.precompute_interval_residuals(
		reader=reader,
		scene_transform=transform,
		seed_start={"frame_index": start_frame},
		seed_end={"frame_index": end_frame},
		fwd_curve=fwd_raw,
		bwd_curve=bwd_raw,
		half_window=half_window,
		fps=_FPS,
		stride=stride,
		debug_stats=debug_stats,
		read_log=read_log,
	)


#============================================
def test_m6_algorithmic_bound_on_short_interval():
	"""Peak BGR/gray buffer occupancy stays within the rolling footprint.

	At half_window=4, stride=1, the rolling footprint is
	2 * 4 * 1 + 1 = 9 frames. Peak occupancy may transiently include
	one extra frame between read and eviction in the same iteration, so
	the test allows footprint + 1.
	"""
	half_window = 4
	stride = 1
	footprint = 2 * half_window * stride + 1
	debug_stats = {"peak_bgr": 0, "peak_gray": 0, "gray_frames": set()}
	store = _run_pre_pass_for_m6(
		n_frames=_N_FRAMES,
		start_frame=10,
		end_frame=20,
		half_window=half_window,
		stride=stride,
		debug_stats=debug_stats,
	)
	assert store is not None and len(store) > 0
	assert debug_stats["peak_bgr"] <= footprint + 1
	assert debug_stats["peak_gray"] <= footprint + 1


#============================================
def test_m6_algorithmic_bound_on_long_interval():
	"""Peak buffer occupancy is bounded independent of interval length.

	A 200-frame interval still respects the same neighbor-footprint
	bound, proving the eviction logic does not allow growth proportional
	to interval length (which was the OOM regression).
	"""
	half_window = 4
	stride = 1
	footprint = 2 * half_window * stride + 1
	debug_stats = {"peak_bgr": 0, "peak_gray": 0, "gray_frames": set()}
	store = _run_pre_pass_for_m6(
		n_frames=240,
		start_frame=20,
		end_frame=220,
		half_window=half_window,
		stride=stride,
		debug_stats=debug_stats,
	)
	assert store is not None and len(store) > 0
	assert debug_stats["peak_bgr"] <= footprint + 1, (
		"BGR buffer grew beyond rolling footprint on long interval: "
		+ str(debug_stats["peak_bgr"])
	)
	assert debug_stats["peak_gray"] <= footprint + 1, (
		"gray buffer grew beyond rolling footprint on long interval: "
		+ str(debug_stats["peak_gray"])
	)


#============================================
def test_m6_safety_net_cap_fires_for_bgr_buf(monkeypatch):
	"""When MAX_PREPASS_BUFFER_FRAMES is set below the algorithmic
	footprint, the safety-net cap raises RuntimeError instead of
	silently exceeding the documented memory budget.
	"""
	# Cap below the rolling footprint (2 * 4 * 1 + 1 = 9). bgr_buf will
	# be the first to exceed since it accepts both interval and padding
	# frames; gray_buf only accepts interval frames.
	monkeypatch.setattr(residual_pre_pass, "MAX_PREPASS_BUFFER_FRAMES", 3)
	with pytest.raises(RuntimeError) as excinfo:
		_run_pre_pass_for_m6(
			n_frames=_N_FRAMES,
			start_frame=10,
			end_frame=20,
			half_window=4,
			stride=1,
		)
	msg = str(excinfo.value)
	assert "buffer exceeded cap" in msg
	assert "interval" in msg


#============================================
def test_m6_padding_frames_stay_bgr_only():
	"""Padding frames at [pad_lo, start_frame) and (end_frame, pad_hi]
	may enter bgr_buf as warp neighbors but must never enter gray_buf.

	gray_buf is only used for the center frame inside
	compute_residual_for_frame, and centers are exactly the interval
	frames. A padding frame appearing in gray_buf would mean the
	pre-pass converted a frame to grayscale-float32 (the most expensive
	per-frame operation in the pre-pass) for no reason.
	"""
	half_window = 4
	stride = 1
	debug_stats = {"peak_bgr": 0, "peak_gray": 0, "gray_frames": set()}
	start_frame, end_frame = 10, 18
	store = _run_pre_pass_for_m6(
		n_frames=_N_FRAMES,
		start_frame=start_frame,
		end_frame=end_frame,
		half_window=half_window,
		stride=stride,
		debug_stats=debug_stats,
	)
	assert store is not None
	gray_frames = debug_stats["gray_frames"]
	assert len(gray_frames) > 0
	assert min(gray_frames) >= start_frame
	assert max(gray_frames) <= end_frame


#============================================
def test_m6_no_fallback_reader_reads_during_compute():
	"""Each padding/interval frame is read exactly once via the
	sequential walk; compute never falls back to scattered
	reader.read_frame calls.

	If compute_residual_for_frame ever fell through to its legacy
	reader fallback on a missing cache key, that frame's index would
	appear twice in read_log: once during the walk, once during
	compute. The rolling buffer's contract is to always have every
	neighbor any pending center will ask for.
	"""
	half_window = 4
	stride = 1
	read_log = []
	start_frame, end_frame = 10, 20
	pad_extent = half_window * stride
	# pad_lo = max(0, start_frame - pad_extent) = max(0, 6) = 6
	# pad_hi = min(N-1, end_frame + pad_extent) = min(29, 24) = 24
	expected = list(range(
		max(0, start_frame - pad_extent),
		min(_N_FRAMES - 1, end_frame + pad_extent) + 1,
	))
	store = _run_pre_pass_for_m6(
		n_frames=_N_FRAMES,
		start_frame=start_frame,
		end_frame=end_frame,
		half_window=half_window,
		stride=stride,
		read_log=read_log,
	)
	assert store is not None and len(store) > 0
	assert read_log == expected, (
		"read_log mismatch: each fi should appear exactly once. Got "
		+ str(len(read_log)) + " reads vs " + str(len(expected)) + " expected"
	)


#============================================
def test_m6_output_parity_with_sequential_reader_baseline():
	"""M6 output is byte-identical to the on-the-fly reader path for
	every (frame_index, roi) key.

	Builds the M6 store, then for each entry recomputes the same
	residual via compute_residual_for_frame with a fresh empty cache
	(legacy reader path, scattered seeks). uint8 quantization is
	deterministic, so output must be numpy.array_equal.
	"""
	half_window = residual_motion.DEFAULT_HALF_WINDOW
	stride = residual_motion.resolve_stride(_FPS)
	frames = [_make_gradient_frame(i) for i in range(_N_FRAMES)]
	reader = _FakeReader(frames)
	transform = _make_identity_transform(_N_FRAMES)
	start_frame, end_frame = 10, 20
	curves = _make_linear_curves(start_frame, end_frame, transform)
	fwd_raw = velocity_model._compute_raw_pred_forward(curves, transform)
	bwd_raw = velocity_model._compute_raw_pred_backward(curves, transform)

	store = residual_pre_pass.precompute_interval_residuals(
		reader=reader,
		scene_transform=transform,
		seed_start={"frame_index": start_frame},
		seed_end={"frame_index": end_frame},
		fwd_curve=fwd_raw,
		bwd_curve=bwd_raw,
		half_window=half_window,
		fps=_FPS,
		stride=stride,
	)
	assert store is not None and len(store) > 0

	# recompute every key via the legacy reader path
	for (test_frame, test_roi), (stored_u8, stored_val) in store.items():
		fresh_cache = {}
		fresh_residual, fresh_validity = residual_motion.compute_residual_for_frame(
			reader=reader,
			frame_index=test_frame,
			scene_transform=transform,
			half_window=half_window,
			cache=fresh_cache,
			roi=test_roi,
			fps=float(_FPS),
			stride=stride,
		)
		assert fresh_residual is not None
		fresh_u8 = numpy.clip(fresh_residual, 0, 255).astype(numpy.uint8)
		fresh_val_u8 = fresh_validity.astype(numpy.uint8)
		assert numpy.array_equal(stored_u8, fresh_u8), (
			"residual byte-mismatch at (fi=" + str(test_frame)
			+ ", roi=" + str(test_roi) + ")"
		)
		assert numpy.array_equal(stored_val, fresh_val_u8), (
			"validity byte-mismatch at (fi=" + str(test_frame)
			+ ", roi=" + str(test_roi) + ")"
		)
