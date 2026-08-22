"""Camera-motion artifact storage and motion-model resolution."""

# Standard Library
import json
import os
import tempfile
from dataclasses import dataclass

# PIP3 modules
import numpy

# local repo modules
import tr_paths
import tr_video_identity


#============================================
@dataclass
class MotionTrack:
	"""Per-frame camera motion and quality metrics.

	All arrays have length total_frames (number of frames in the video).

	Attributes:
		dx: numpy array of per-frame x translations (pixels).
		dy: numpy array of per-frame y translations (pixels).
		scale: numpy array of per-frame scale factors (1.0 = no change).
			For `fixed_zoom`, this is always 1.0 and is not persisted to
			disk, but the field is kept in memory so downstream
			SceneTransform code works uniformly across motion models.
		quality: numpy array of per-frame confidence (phase correlation
			response). Consumed by scoring.py for `motion_quality`.
	"""
	dx: numpy.ndarray
	dy: numpy.ndarray
	scale: numpy.ndarray
	quality: numpy.ndarray


#============================================
# Motion model identifiers are persisted in solved camera-motion artifacts.
MOTION_MODEL_FIXED = "fixed_zoom"
MOTION_MODEL_DISCRETE = "discrete_zoom"
MOTION_MODEL_CONTINUOUS = "continuous_zoom"

VALID_MOTION_MODELS = frozenset({
	MOTION_MODEL_FIXED, MOTION_MODEL_DISCRETE, MOTION_MODEL_CONTINUOUS,
})


#============================================
# Per-model required array sets. Fixed zoom carries no scale because
# it is constant 1.0 by construction; writing it would be pure ballast.
_REQUIRED_ARRAYS = {
	MOTION_MODEL_FIXED: ("dx", "dy", "quality"),
	MOTION_MODEL_DISCRETE: ("dx", "dy", "scale", "quality"),
	MOTION_MODEL_CONTINUOUS: ("dx", "dy", "scale", "quality"),
}


#============================================
def _estimator_type_to_model(estimator_type: str) -> str:
	"""Map a config-level estimator type string to a motion_model label."""
	if estimator_type in ("FixedZoomEstimator", "fixed"):
		return MOTION_MODEL_FIXED
	if estimator_type in (
		"DiscreteZoomEstimator", "discrete", "iphone_discrete",
	):
		return MOTION_MODEL_DISCRETE
	if estimator_type in ("ContinuousZoomEstimator", "continuous"):
		return MOTION_MODEL_CONTINUOUS
	raise ValueError(f"unsupported estimator type: {estimator_type}")


#============================================
def _motion_model_from_config(config: dict) -> str:
	"""Resolve the motion_model label implied by the current config.

	Reads the current `motion.estimator.type` setting. Config validation
	installs the fixed estimator only for an intentionally minimal config.
	"""
	estimator_config = config.get("motion", {}).get("estimator", {})
	estimator_type = estimator_config.get("type", "fixed")
	motion_model = _estimator_type_to_model(estimator_type)
	return motion_model


#============================================
def _write_motion_cache_atomic(cache_path: str, arrays: dict) -> None:
	"""Write one camera-motion NPZ without exposing a partial artifact."""
	dir_path = os.path.dirname(os.path.abspath(cache_path))
	fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp.npz")
	os.close(fd)
	try:
		numpy.savez(tmp_path, **arrays)
		os.replace(tmp_path, cache_path)
	except Exception:
		if os.path.exists(tmp_path):
			os.unlink(tmp_path)
		raise


#============================================
def save_motion_cache(
	motion_track: MotionTrack,
	cache_path: str,
	motion_model: str,
	video_identity: dict,
	bin_factor: int = 1,
) -> None:
	"""Save motion track to the canonical camera_motion.npz file.

	Writes per-model arrays (fixed_zoom omits `scale`; discrete and
	continuous include it) plus `motion_model`, source geometry identity,
	`frame_count`, and `bin_factor` as artifact-identity metadata. All
	per-frame arrays are stored as float32. No `event_flags`. This is a
	durable solved-result artifact, not cache.

	bin_factor is persisted as identity metadata (cache-key bookkeeping,
	NOT an on-disk schema change). The phase-correlation estimator runs on
	PROCESSED frames and upscales dx/dy by bin_factor to SOURCE before
	storage, so the stored SOURCE track depends on the analysis bin even
	though its units are SOURCE. A bin change must recompute camera motion;
	`load_motion_cache` treats a bin mismatch as a stale artifact.

	Args:
		motion_track: MotionTrack instance to save.
		cache_path: Target NPZ file path at
			`<video>.track_runner.camera_motion.npz`. This is the
			canonical single-file solved artifact per video. If motion_model
			differs from the stored value on load, the stored version is
			treated as stale and recomputed.
		motion_model: One of MOTION_MODEL_{FIXED,DISCRETE,CONTINUOUS}.
		video_identity: Current source-video geometry identity. Persisted so
			cache reuse requires matching frame geometry.
	"""
	if motion_model not in VALID_MOTION_MODELS:
		raise ValueError(f"unknown motion_model: {motion_model}")
	tr_paths.ensure_parent_dir(cache_path)
	arrays = {
		"motion_model": numpy.frombuffer(
			motion_model.encode("utf-8"), dtype=numpy.uint8
		),
		"video_identity": numpy.frombuffer(
			json.dumps(video_identity, sort_keys=True).encode("utf-8"),
			dtype=numpy.uint8,
		),
		"frame_count": numpy.asarray(
			int(video_identity["frame_count"]), dtype=numpy.int64,
		),
		"bin_factor": numpy.asarray(int(bin_factor), dtype=numpy.int64),
		"dx": numpy.asarray(motion_track.dx, dtype=numpy.float32),
		"dy": numpy.asarray(motion_track.dy, dtype=numpy.float32),
		"quality": numpy.asarray(motion_track.quality, dtype=numpy.float32),
	}
	# fixed zoom omits scale; other models include it
	if motion_model != MOTION_MODEL_FIXED:
		arrays["scale"] = numpy.asarray(
			motion_track.scale, dtype=numpy.float32,
		)
	_write_motion_cache_atomic(cache_path, arrays)


#============================================
def load_motion_cache(
	cache_path: str,
	expected_motion_model: str | None = None,
	expected_bin_factor: int | None = None,
	expected_video_identity: dict | None = None,
) -> MotionTrack | None:
	"""Load motion track from camera_motion.npz.

	Returns None if the file does not exist OR the persisted
	`motion_model` differs from `expected_motion_model` OR the persisted
	`bin_factor` differs from `expected_bin_factor`, or its complete source
	video geometry differs from `expected_video_identity`. A stale artifact
	is treated as absent so the caller recomputes and overwrites atomically.
	No merge, no partial reuse.

	The phase-correlation estimator runs on PROCESSED frames, so the stored
	SOURCE dx/dy depend on the analysis bin even though their units are
	SOURCE. A bin change must recompute camera motion. Every current artifact
	persists `bin_factor`; an artifact without it is stale and recomputes.

	For `fixed_zoom`, the on-disk file carries no `scale` array; the
	loader synthesizes an all-ones scale array so downstream
	SceneTransform code sees the same shape regardless of model.

	Args:
		cache_path: Path to `<video>.track_runner.camera_motion.npz`.
		expected_motion_model: Current motion_model derived from config
			(one of MOTION_MODEL_*); if provided and disagreeing with
			the stored value, the artifact is treated as stale and None
			is returned.
		expected_video_identity: Current source-video geometry identity. When
			provided, a missing or geometry-mismatched identity is stale.

	Returns:
		MotionTrack instance, or None if missing / stale / unknown
		motion_model.

	Raises:
		RuntimeError: If the file exists but a required per-model
			array is missing.
	"""
	if not os.path.isfile(cache_path):
		return None
	with numpy.load(cache_path, allow_pickle=False) as npz:
		motion_model = bytes(npz["motion_model"]).decode("utf-8")
		if motion_model not in VALID_MOTION_MODELS:
				raise RuntimeError(
					f"unknown motion_model {motion_model!r} in {cache_path}; "
					f"delete the artifact and recompute"
				)
		# stale artifact: behave as if file is absent so caller recomputes
		if expected_motion_model is not None and motion_model != expected_motion_model:
			return None
		# Every current artifact records the processed-frame bin used to
		# measure its SOURCE-space motion. Missing metadata is stale.
		if "bin_factor" not in npz.files:
			return None
		stored_bin_factor = int(npz["bin_factor"])
		if (expected_bin_factor is not None
				and stored_bin_factor != int(expected_bin_factor)):
			return None
		if expected_video_identity is not None:
			if "video_identity" not in npz.files:
				return None
			stored_identity = json.loads(
				bytes(npz["video_identity"]).decode("utf-8"),
			)
			comparison = tr_video_identity.compare_video_identity(
				stored_identity, expected_video_identity,
			)
			if comparison["blocking"]:
				return None
		required = _REQUIRED_ARRAYS[motion_model]
		for key in required:
			if key not in npz.files:
				raise RuntimeError(
					f"motion artifact missing required array {key!r} "
					f"for model {motion_model} in {cache_path}"
				)
		dx = numpy.asarray(npz["dx"], dtype=numpy.float32)
		dy = numpy.asarray(npz["dy"], dtype=numpy.float32)
		quality = numpy.asarray(npz["quality"], dtype=numpy.float32)
		if motion_model == MOTION_MODEL_FIXED:
			# synthesize a constant-1.0 scale so downstream code sees
			# a uniform MotionTrack shape regardless of model
			scale = numpy.ones(len(dx), dtype=numpy.float32)
		else:
			scale = numpy.asarray(npz["scale"], dtype=numpy.float32)
		# Internal consistency check: if frame_count is persisted, verify
		# it agrees with the length of the dx array. Mismatches indicate
		# file corruption or a partially-written file.
		if "frame_count" in npz.files:
			persisted_frame_count = int(npz["frame_count"])
			actual_length = len(dx)
			if persisted_frame_count != actual_length:
				raise RuntimeError(
					f"camera_motion.npz frame_count={persisted_frame_count} "
					f"but dx array has {actual_length} entries; file is corrupt"
				)
	motion = MotionTrack(dx=dx, dy=dy, scale=scale, quality=quality)
	return motion


#============================================
def load_active_camera_motion_or_fail(
	input_file: str,
	config: dict,
	expected_bin_factor: int | None = None,
	video_info: dict | None = None,
) -> MotionTrack:
	"""Load the canonical camera-motion artifact from disk.

	Refine entry point. Loads the canonical solved artifact file
	`<video>.track_runner.camera_motion.npz`. Refine never recomputes
	Stage 1, so a missing file, a stored motion_model that disagrees with
	the current config, or a stored bin_factor that disagrees with the
	refine run's bin raises with a "run solve first" message.

	Args:
		input_file: Path to the input video.
		config: Configuration dict with motion estimator settings.
		expected_bin_factor: The refine run's bin_factor. When provided and
			it disagrees with the stored bin, the artifact is treated as
			stale (the stored SOURCE track was computed at a different
			analysis resolution) and the load fails. None skips the check.
		video_info: Current video metadata. When provided, the saved motion
			must carry the same source-video identity.

	Returns:
		MotionTrack from the canonical solved artifact file.

	Raises:
		RuntimeError: If the artifact file is missing or its stored
			motion_model / bin_factor does not match. Tells the caller to
			run solve first.
	"""
	expected_motion_model = _motion_model_from_config(config)
	expected_video_identity = None
	if video_info is not None:
		expected_video_identity = tr_video_identity.make_video_identity(
			input_file, video_info,
		)
	cache_path = tr_paths.default_camera_motion_path(input_file)
	if not os.path.isfile(cache_path):
		raise RuntimeError(
			"Camera-motion artifact for this solve is missing."
			" Run solve first."
		)
	cached = load_motion_cache(
		cache_path, expected_motion_model, expected_bin_factor,
		expected_video_identity,
	)
	if cached is None:
		raise RuntimeError(
			"Camera-motion artifact for this solve is missing."
			" Run solve first."
		)
	return cached
