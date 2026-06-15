"""Heat movie encode helper.

Drives the raw-BGR spill + ffmpeg image2 encode for the optional `--heat-movie`
diagnostic.  The two public functions are:

  encode_heat_movie_for_direction(
      direction_label, left_seed_box, right_seed_box, direction_path,
      reader, scene_transform, fps, output_interval_dir,
      scratch_parent=None,
  ) -> str | None

    Spills one per-direction heat movie to disk next to the tile output.
    Returns the destination .mkv path on success, None if ffmpeg is absent
    and `--heat-movie` was not set (see note in check_ffmpeg_available).

  check_ffmpeg_available() -> str

    Returns the absolute ffmpeg path, or raises RuntimeError with a clean
    user-facing message when ffmpeg is absent.  Called at flag-parse time so
    a missing ffmpeg is diagnosed immediately, not mid-encode.

Design notes:
  - ffmpeg REQUIRED only for --heat-movie.  This module is imported only
    when --heat-movie is set; the normal --walk path does not import or call
    anything from here.
  - All .bgr frames AND the intermediate heat.mkv live under /tmp
    (run-scoped, deterministic/injectable scratch parent) so the ffmpeg path
    arguments are all under /tmp.  The finished .mkv is copied beside the
    per-interval render output afterward.
  - Verified-before-cleanup order: verifications (Steps 5 and 7) run inside
    the `with tempfile.TemporaryDirectory(...)` block before the context
    exits on the success path; the context manager then deletes the entire
    scratch dir automatically on both normal exit and any raised exception
    (C13: within-run scratch only, never persisted between runs).
  - Per-direction (FWD/BWD independent), matching how the manifest/tiles are
    already split.
  - Extra frame reads: compute_heat_map_roi (called inside
    build_heat_movie_frame) decodes the center frame plus its warp neighbors
    through the bounded gray cache already present in residual_motion.
    No new per-frame-limit constant is introduced here.
"""

# Standard Library
import os
import shutil
import logging
import pathlib
import tempfile
import subprocess

# shared sys.path bootstrap (track_runner, tests, repo root, blob_walk_v2)
import walk_paths
walk_paths.setup()

# local repo modules
import common_tools.coord_space
import heat_movie_frame

logger = logging.getLogger(__name__)


#============================================
def check_ffmpeg_available() -> str:
	"""Return the absolute ffmpeg path or raise RuntimeError.

	Called by the CLI at flag-parse time so a missing ffmpeg is diagnosed
	immediately, not mid-encode when frames are already on disk.

	Returns:
		str: absolute path to the ffmpeg binary.

	Raises:
		RuntimeError: ffmpeg not found in PATH with a clean user-facing message.
	"""
	ffmpeg_path = shutil.which("ffmpeg")
	if ffmpeg_path is None:
		raise RuntimeError(
			"--heat-movie requires ffmpeg, but ffmpeg was not found in PATH. "
			"Install it (e.g. 'brew install ffmpeg') and retry."
		)
	return ffmpeg_path


#============================================
def _spill_direction_frames(
	direction_label: str,
	direction_path: list,
	left_seed_box: common_tools.coord_space.ProcessedBox,
	right_seed_box: common_tools.coord_space.ProcessedBox,
	reader,
	scene_transform,
	fps: float,
	roi_size: tuple,
	scratch_dir: str,
) -> list:
	"""Spill per-frame .bgr files to scratch_dir for one direction.

	Iterates direction_path in frame-index order, calls
	heat_movie_frame.build_heat_movie_frame for each frame, and collects
	the returned paths.  Missing frames in direction_path are skipped with
	a WARNING (they would leave a gap in the numbered sequence which ffmpeg
	cannot fill).

	Args:
		direction_label: 'FWD' or 'BWD', for logging.
		direction_path: list of {frame_index, cx, cy, w, h, ...} in PROCESSED px.
		left_seed_box: left bracketing seed (PROCESSED).
		right_seed_box: right bracketing seed (PROCESSED).
		reader: VideoReader.
		scene_transform: SceneTransform.
		fps: source frame rate.
		roi_size: fixed (roiW, roiH) from compute_fixed_heat_roi_size.
		scratch_dir: absolute path to the scratch directory.

	Returns:
		list of str: absolute paths to the written .bgr files, in frame-index order.
	"""
	written_paths = []
	# Sort direction_path by frame_index so the zero-padded filenames are sequential.
	sorted_path = sorted(direction_path, key=lambda d: d["frame_index"])
	for entry in sorted_path:
		frame_index = entry["frame_index"]
		solved_box = common_tools.coord_space.ProcessedBox(
			cx=float(entry["cx"]),
			cy=float(entry["cy"]),
			w=float(entry["w"]),
			h=float(entry["h"]),
		)
		frame_path = heat_movie_frame.build_heat_movie_frame(
			reader=reader,
			frame_index=frame_index,
			scene_transform=scene_transform,
			solved_box=solved_box,
			roi_size=roi_size,
			scratch_dir=scratch_dir,
			fps=fps,
		)
		written_paths.append(frame_path)
	logger.info(
		f"  heat-movie {direction_label}: spilled {len(written_paths)} frames "
		f"to {scratch_dir}"
	)
	return written_paths


#============================================
def _run_ffmpeg_encode(
	ffmpeg_path: str,
	scratch_dir: str,
	roi_size: tuple,
	fps: float,
) -> str:
	"""Run ffmpeg image2 encode on the .bgr scratch frames.

	The output heat.mkv is written inside scratch_dir.

	Args:
		ffmpeg_path: absolute path to ffmpeg binary.
		scratch_dir: directory containing frame_%06d.bgr files.
		roi_size: (roiW, roiH) in pixels.
		fps: source frame rate for -framerate flag.

	Returns:
		str: absolute path to the written heat.mkv.

	Raises:
		RuntimeError: ffmpeg exited non-zero with stderr detail.
	"""
	roi_w, roi_h = int(roi_size[0]), int(roi_size[1])
	input_pattern = os.path.join(scratch_dir, "frame_%06d.bgr")
	output_mkv = os.path.join(scratch_dir, "heat.mkv")
	cmd = [
		ffmpeg_path,
		"-y",
		"-f", "image2",
		"-vcodec", "rawvideo",
		"-framerate", str(fps),
		"-s", f"{roi_w}x{roi_h}",
		"-pix_fmt", "bgr24",
		"-i", input_pattern,
		"-c:v", "libx264",
		"-pix_fmt", "yuv420p",
		output_mkv,
	]
	result = subprocess.run(cmd, capture_output=True, text=True)
	if result.returncode != 0:
		raise RuntimeError(
			f"ffmpeg image2 encode failed (returncode={result.returncode}): "
			f"{result.stderr}"
		)
	return output_mkv


#============================================
def _verify_nonempty(path: str, label: str) -> None:
	"""Raise RuntimeError if path does not exist or is empty.

	Args:
		path: file path to verify.
		label: human-readable label for error messages.
	"""
	if not os.path.isfile(path):
		raise RuntimeError(f"{label} not found after encode: {path}")
	size = os.path.getsize(path)
	if size == 0:
		raise RuntimeError(f"{label} is empty after encode: {path}")


#============================================
def encode_heat_movie_for_direction(
	direction_label: str,
	left_seed_box: common_tools.coord_space.ProcessedBox,
	right_seed_box: common_tools.coord_space.ProcessedBox,
	direction_path: list,
	reader,
	scene_transform,
	fps: float,
	output_interval_dir: pathlib.Path,
	scratch_parent: str | None = None,
) -> str | None:
	"""Spill + encode one per-direction heat movie, copy beside render output.

	Orchestrates the full encode pipeline for one FWD or BWD direction:
	  1. Compute fixed ROI size from the two bracketing seeds (once per interval).
	  2. Enter a tempfile.TemporaryDirectory context (under scratch_parent when
	     provided, otherwise the system tempdir /tmp) -- the context manager owns
	     the entire scratch dir for the lifetime of this block.
	  3. Spill per-frame .bgr files via build_heat_movie_frame.
	  4. Run ffmpeg image2 encode -> scratch/heat.mkv.
	  5. Verify the scratch .mkv is non-empty.
	  6. Copy the .mkv beside the per-interval render output as
	     heat_<direction>.mkv.
	  7. Verify the destination is non-empty.
	  8. Context manager exits -- the entire scratch dir is deleted automatically,
	     both when the body returns normally after Step 7 and when any step raises
	     (the exception propagates after deletion).

	Args:
		direction_label: 'FWD' or 'BWD'.
		left_seed_box: left bracketing seed box (PROCESSED).
		right_seed_box: right bracketing seed box (PROCESSED).
		direction_path: per-frame box path list in PROCESSED pixels.
		reader: VideoReader.
		scene_transform: SceneTransform.
		fps: source frame rate.
		output_interval_dir: the per-interval output directory where
			heat_<direction>.mkv will be copied.
		scratch_parent: optional base dir under /tmp for an injectable/
			deterministic scratch path.  None uses a fresh tempdir.

	Returns:
		str: absolute path to the destination .mkv (beside the render output).

	Raises:
		RuntimeError: ffmpeg missing, encode failure, or copy failure.
	"""
	if not direction_path:
		logger.warning(
			f"  heat-movie {direction_label}: empty direction_path; skipping encode"
		)
		return None

	# Step 1: compute fixed ROI size once for the interval.
	roi_size = heat_movie_frame.compute_fixed_heat_roi_size(left_seed_box, right_seed_box)

	ffmpeg_path = check_ffmpeg_available()

	# Steps 2-7 run inside the TemporaryDirectory context.
	# The context manager auto-deletes the scratch dir on both normal exit and
	# any raised exception, so no manual cleanup is needed.
	direction_label_lower = direction_label.lower()
	tmp_prefix = f"heat_movie_{direction_label_lower}_"
	# Use scratch_parent when provided (injectable for tests), else default to /tmp.
	with tempfile.TemporaryDirectory(prefix=tmp_prefix, dir=scratch_parent) as scratch_dir:
		logger.info(
			f"  heat-movie {direction_label}: scratch dir {scratch_dir} "
			f"(roi_size={roi_size})"
		)

		# Step 3: spill per-frame .bgr files.
		_spill_direction_frames(
			direction_label=direction_label,
			direction_path=direction_path,
			left_seed_box=left_seed_box,
			right_seed_box=right_seed_box,
			reader=reader,
			scene_transform=scene_transform,
			fps=fps,
			roi_size=roi_size,
			scratch_dir=scratch_dir,
		)

		# Step 4: ffmpeg image2 encode -> scratch/heat.mkv.
		tmp_mkv = _run_ffmpeg_encode(ffmpeg_path, scratch_dir, roi_size, fps)
		logger.info(f"  heat-movie {direction_label}: encoded {tmp_mkv}")

		# Step 5: verify the scratch .mkv is non-empty.
		_verify_nonempty(tmp_mkv, f"heat.mkv ({direction_label})")

		# Step 6: copy the .mkv beside the per-interval render output.
		# dest_path is OUTSIDE scratch_dir and survives context exit.
		output_interval_dir.mkdir(parents=True, exist_ok=True)
		dest_name = f"heat_{direction_label_lower}.mkv"
		dest_path = str(output_interval_dir / dest_name)
		shutil.copy2(tmp_mkv, dest_path)
		logger.info(f"  heat-movie {direction_label}: copied to {dest_path}")

		# Step 7: verify the destination is non-empty.
		_verify_nonempty(dest_path, f"destination heat_mkv ({direction_label})")

	# Context manager exits here -- scratch dir deleted automatically.
	return dest_path
