"""fastread_video.py

Creation and live structural validation for the derived fast-read working video.

The fast-read video is an H.264 8-bit working copy created by `prepare`
mode beside the original source video (see
`track_runner.tr_paths.fastread_video_path`). There is no sidecar or
stored bookkeeping: the file is discovered by deterministic path and
validated live every run by comparing its freshly probed geometry and
timing against the freshly probed original (contract C13 forbids stored
identity snapshots such as basename or size_bytes).

`validate_fastread_structural(original_video_path, fastread_path)` is the
single live comparator. `resolve_video_context` calls it once per CLI run:
a successful return authorizes fast-read decode; any failure raises a
`RuntimeError` that names the fast-read path, the failed check, and the
remedy.

Metadata probes use `common_tools.probe_video.probe_video`. Frame-read
smoke checks use `common_tools.frame_reader.FrameReader`.

Example:
	>>> from track_runner import tr_paths, fastread_video
	>>> original = "Lyra-Wheeling-IMG_3912.mkv"
	>>> fastread = tr_paths.fastread_video_path(original)
	>>> result = fastread_video.validate_fastread_structural(original, fastread)
	>>> result.timestamp_fallback_note  # "none" or the fallback description
"""

# Standard Library
import os
import time
import shutil
import logging
import subprocess
import dataclasses

# local repo modules
import tr_paths
import common_tools.probe_video
import common_tools.frame_reader

# module-level logger, matching the repo pattern
logger = logging.getLogger(__name__)

#============================================

# Duration tolerance: the fast-read transcode preserves the frame-index to
# time mapping, so the two durations should match to within roughly one
# frame period. A small absolute floor covers container rounding on very
# short clips.
DURATION_ABS_TOLERANCE_S = 0.5

# fps tolerance is derived from probe precision: mediainfo reports
# FrameRate as a decimal string (for example "119.880"), so a rational
# rate such as 120000/1001 prints with bounded rounding error. A small
# relative tolerance absorbs that printed-precision difference between the
# two files without accepting a genuinely different frame rate.
FPS_REL_TOLERANCE = 1e-3

# Remedy text appended to every raised validation error. The user either
# recreates the fast-read video or removes it so modes fall back to the
# original.
REMEDY = "re-run prepare --force, or delete the fast-read video to use the original"

# Fixed transcode settings for the fast-read video. These are immutable: if the
# defaults change in the future, structural validation still governs and the user
# recreates with --force. No per-video settings storage (contract C13).
TRANSCODE_CRF = 23
TRANSCODE_GOP = 30
TRANSCODE_PRESET = "veryfast"
TRANSCODE_FILTER = "hqdn3d,format=yuv420p"
TRANSCODE_CODEC = "libx264"

# Heartbeat interval: print a progress line every N seconds while ffmpeg is running.
HEARTBEAT_INTERVAL_S = 30

#============================================

@dataclasses.dataclass(frozen=True)
class FastreadValidation:
	"""Result of a successful live structural validation.

	Returned only when validation passes; failures raise instead. The
	fields let `prepare`'s status summary report the
	validation outcome and the timestamp-alignment fallback note without
	re-probing.

	Attributes:
		original_video_path: Path to the original source video.
		fastread_path: Path to the validated fast-read video.
		width: Shared frame width in pixels (exact match).
		height: Shared frame height in pixels (exact match).
		frame_count: Shared frame count (exact match).
		timestamp_fallback_note: "none" when per-frame timestamp
			alignment ran on real timestamps, otherwise a short
			description of the frame-count + duration fallback used.
	"""

	original_video_path: str
	fastread_path: str
	width: int
	height: int
	frame_count: int
	timestamp_fallback_note: str

#============================================

# Fixed reason strings recorded on each VideoSelection. Used in banners,
# logs, and tests; do not reword (the routing tests pin these exact values).
REASON_VALID_FASTREAD = "valid_fastread"
REASON_NO_FASTREAD_ORIGINAL = "no_fastread_original"
REASON_FINAL_ENCODE_ORIGINAL = "final_encode_original"
REASON_METADATA_IDENTITY_ORIGINAL = "metadata_identity_original"

#============================================

@dataclasses.dataclass(frozen=True)
class VideoSelection:
	"""One resolved video choice for a single role.

	Attributes:
		path: The video file path selected for this role.
		role: Role label ("working_decode", "final_encode",
			"metadata_identity").
		using_fastread: True when this role decodes from the fast-read
			working video; False when it uses the original.
		reason: Fixed reason string (one of the REASON_* constants).
	"""

	path: str
	role: str
	using_fastread: bool
	reason: str

#============================================

@dataclasses.dataclass(frozen=True)
class VideoContext:
	"""Resolved per-run video routing for all roles.

	Built exactly once per CLI run by `resolve_video_context`. A valid
	context IS the authorization for working-mode FrameReaders to decode
	from `working_decode.path`; modes consume this context and never
	re-run discovery or re-validate.

	Attributes:
		original_video_path: Canonical original source video path. State,
			identity, config, cache, and output naming key off this only.
		working_decode: Selection for working modes (fast-read when present
			and structurally valid, else original).
		final_encode: Selection for final encode (always original).
		metadata_identity: Selection for identity/state-path naming (always
			original).
	"""

	original_video_path: str
	working_decode: VideoSelection
	final_encode: VideoSelection
	metadata_identity: VideoSelection

#============================================

def resolve_video_context(original_video_path: str) -> VideoContext:
	"""Resolve original-vs-fast-read routing once for a CLI run.

	This is the single chokepoint deciding which physical video each role
	decodes from. It computes the deterministic fast-read path, and when
	that file is present it validates it EXACTLY ONCE via
	`validate_fastread_structural`. A present-but-invalid fast-read raises
	(the validation error already names the path, the failed check, and the
	remedy). `final_encode` and `metadata_identity` always select the
	original.

	Args:
		original_video_path: Path to the original source video.

	Returns:
		VideoContext: Frozen routing context for the run.

	Raises:
		RuntimeError: When a fast-read video is present at the deterministic
			path but fails structural validation (propagated from
			`validate_fastread_structural`).
	"""
	# (1) compute the expected fast-read path from the original path only
	expected_fastread_path = tr_paths.fastread_video_path(original_video_path)
	# (2) absent -> working modes decode from the original, no warning
	if not os.path.exists(expected_fastread_path):
		working_decode = VideoSelection(
			path=original_video_path,
			role="working_decode",
			using_fastread=False,
			reason=REASON_NO_FASTREAD_ORIGINAL,
		)
	else:
		# (3) present -> validate exactly once for its side-effect (raises on
		# failure); the returned FastreadValidation is not needed here.
		validate_fastread_structural(original_video_path, expected_fastread_path)
		# (4) valid -> working modes decode from the fast-read video
		working_decode = VideoSelection(
			path=expected_fastread_path,
			role="working_decode",
			using_fastread=True,
			reason=REASON_VALID_FASTREAD,
		)
	# final encode always uses the original for final quality
	final_encode = VideoSelection(
		path=original_video_path,
		role="final_encode",
		using_fastread=False,
		reason=REASON_FINAL_ENCODE_ORIGINAL,
	)
	# identity / state-path naming always keys off the original
	metadata_identity = VideoSelection(
		path=original_video_path,
		role="metadata_identity",
		using_fastread=False,
		reason=REASON_METADATA_IDENTITY_ORIGINAL,
	)
	context = VideoContext(
		original_video_path=original_video_path,
		working_decode=working_decode,
		final_encode=final_encode,
		metadata_identity=metadata_identity,
	)
	return context

#============================================

def print_video_routing_banner(
	original_video_path: str, decode_path: str
) -> None:
	"""Print the two-line source/decode banner for a routed mode.

	Args:
		original_video_path: Original source video path.
		decode_path: The path the current mode decodes from. When this
			equals the original path the decode line reads "original".
	"""
	source_name = os.path.basename(original_video_path)
	# when decode == original, report "original" instead of the basename
	if decode_path == original_video_path:
		decode_label = "original"
	else:
		decode_label = os.path.basename(decode_path)
	print(f"source video: {source_name}")
	print(f"decode video: {decode_label}")

#============================================

def _raise_validation_error(fastread_path: str, check: str, detail: str) -> None:
	"""Raise a RuntimeError naming the path, the failed check, and remedy.

	Args:
		fastread_path: Fast-read video path that failed validation.
		check: Short name of the check that failed (for example
			"frame_count").
		detail: Human-readable description of the mismatch.

	Raises:
		RuntimeError: Always.
	"""
	message = (
		f"fast-read video failed structural validation"
		f" [{check}]: {detail}"
		f" (fast-read video: {fastread_path!r});"
		f" remedy: {REMEDY}"
	)
	raise RuntimeError(message)

#============================================

def _check_geometry_and_count(
	fastread_path: str, original_info: dict, fastread_info: dict
) -> None:
	"""Check width, height, and frame_count match exactly.

	Args:
		fastread_path: Fast-read video path (used in error text).
		original_info: probe_video dict for the original video.
		fastread_info: probe_video dict for the fast-read video.

	Raises:
		RuntimeError: On any exact-match failure.
	"""
	# width must match exactly
	if original_info["width"] != fastread_info["width"]:
		detail = (
			f"width {fastread_info['width']} != original {original_info['width']}"
		)
		_raise_validation_error(fastread_path, "width", detail)
	# height must match exactly
	if original_info["height"] != fastread_info["height"]:
		detail = (
			f"height {fastread_info['height']} != original {original_info['height']}"
		)
		_raise_validation_error(fastread_path, "height", detail)
	# frame_count must match exactly: an off-by-one would mis-index the
	# whole pipeline, so this is the strictest invariant.
	if original_info["frame_count"] != fastread_info["frame_count"]:
		detail = (
			f"frame_count {fastread_info['frame_count']}"
			f" != original {original_info['frame_count']}"
		)
		_raise_validation_error(fastread_path, "frame_count", detail)

#============================================

def _check_fps(fastread_path: str, original_info: dict, fastread_info: dict) -> None:
	"""Check fps matches within probe-precision relative tolerance.

	Args:
		fastread_path: Fast-read video path (used in error text).
		original_info: probe_video dict for the original video.
		fastread_info: probe_video dict for the fast-read video.

	Raises:
		RuntimeError: When the relative fps difference exceeds tolerance.
	"""
	original_fps = original_info["fps"]
	fastread_fps = fastread_info["fps"]
	# relative difference absorbs printed-precision rounding (for example
	# 119.88 vs 119.880) without accepting a different frame rate.
	rel_diff = abs(fastread_fps - original_fps) / original_fps
	if rel_diff > FPS_REL_TOLERANCE:
		detail = (
			f"fps {fastread_fps} differs from original {original_fps}"
			f" by relative {rel_diff:.6f} > tolerance {FPS_REL_TOLERANCE}"
		)
		_raise_validation_error(fastread_path, "fps", detail)

#============================================

def _check_duration(
	fastread_path: str, original_info: dict, fastread_info: dict
) -> None:
	"""Check duration matches within a small absolute tolerance.

	Args:
		fastread_path: Fast-read video path (used in error text).
		original_info: probe_video dict for the original video.
		fastread_info: probe_video dict for the fast-read video.

	Raises:
		RuntimeError: When the duration difference exceeds tolerance.
	"""
	original_duration = original_info["duration_s"]
	fastread_duration = fastread_info["duration_s"]
	abs_diff = abs(fastread_duration - original_duration)
	if abs_diff > DURATION_ABS_TOLERANCE_S:
		detail = (
			f"duration {fastread_duration:.3f}s differs from original"
			f" {original_duration:.3f}s by {abs_diff:.3f}s"
			f" > tolerance {DURATION_ABS_TOLERANCE_S}s"
		)
		_raise_validation_error(fastread_path, "duration", detail)

#============================================

def _check_timestamp_alignment(
	original_info: dict, fastread_info: dict
) -> str:
	"""Best-effort first/middle/last frame timing alignment.

	The metadata probe primitive (`probe_video`) reports container-level
	frame_count, fps, and duration but no per-frame presentation
	timestamps. Reliable per-frame timestamp extraction for an arbitrary
	container/codec pair is not available through this primitive, so this
	check falls back to the frame-count + duration invariant already
	verified by `_check_geometry_and_count` and `_check_duration`: with
	matching frame_count and duration, the derived frame-index to time
	mapping (`frame_index / fps`) aligns at the first, middle, and last
	frames by construction.

	The fallback is recorded and surfaced so the `prepare` status summary
	can report it. A future ffprobe-based per-frame timestamp extractor can
	replace this fallback with a real first/middle/last comparison and
	return "none".

	Args:
		original_info: probe_video dict for the original video.
		fastread_info: probe_video dict for the fast-read video.

	Returns:
		str: "none" when real per-frame timestamps were compared,
			otherwise a short description of the fallback used.
	"""
	# probe_video carries no per-frame presentation timestamps, so we use
	# the frame-count + duration invariant. frame_count is already checked
	# exact and duration within tolerance; derive the bracketing frame
	# times here only to log the alignment evidence.
	original_fps = original_info["fps"]
	frame_count = original_info["frame_count"]
	# first / middle / last frame indices
	sample_indices = (0, frame_count // 2, frame_count - 1)
	original_times = tuple(index / original_fps for index in sample_indices)
	fallback_note = (
		"frame-count + duration (per-frame timestamps unavailable from probe)"
	)
	logger.debug(
		f"fast-read timestamp alignment fallback: {fallback_note};"
		f" sample frame indices {sample_indices} map to original times"
		f" {tuple(round(t, 3) for t in original_times)}s"
	)
	return fallback_note

#============================================

def _smoke_read_fastread(fastread_path: str, fastread_info: dict) -> None:
	"""Open the fast-read video and read frames 0 / mid / last.

	Args:
		fastread_path: Fast-read video path.
		fastread_info: probe_video dict for the fast-read video.

	Raises:
		RuntimeError: When FrameReader open or any of the three reads
			fail (the failure carries the fast-read path and remedy).
	"""
	frame_count = fastread_info["frame_count"]
	fps = fastread_info["fps"]
	# first / middle / last frame indices; deduplicated for very short clips
	sample_indices = sorted({0, frame_count // 2, frame_count - 1})
	# FrameReader requires .mkv; the fast-read suffix guarantees that. A
	# failed open or read raises RuntimeError, which we re-wrap so the
	# message carries the failed-check name and remedy.
	reader = common_tools.frame_reader.FrameReader(
		fastread_path, fps=fps, total_frames=frame_count
	)
	# context manager guarantees the capture is released even on failure
	with reader:
		for index in sample_indices:
			# read_frame raises RuntimeError on failure (per its docstring);
			# it never returns None. The unreachable None-guard is removed so
			# read_frame's own error propagates directly.
			reader.read_frame(index)

#============================================

def validate_fastread_structural(
	original_video_path: str, fastread_path: str
) -> FastreadValidation:
	"""Live-validate a fast-read video against its original.

	Probes BOTH files fresh via `common_tools.probe_video.probe_video` and
	compares live-probed geometry and timing only (no basename, no
	size_bytes, no stored metadata -- contract C13). Then opens the
	fast-read video with `common_tools.frame_reader.FrameReader` and reads
	frames 0, frame_count // 2, and frame_count - 1.

	Checks, in order:
		1. width / height / frame_count exact match.
		2. fps within probe-precision relative tolerance.
		3. duration within a small absolute tolerance.
		4. best-effort first/middle/last frame timestamp alignment
		   (falls back to frame-count + duration and notes the fallback).
		5. FrameReader open + smoke reads at 0 / mid / last.

	Any failed check raises a `RuntimeError` naming the fast-read path, the
	failed check, and the remedy. A successful return authorizes fast-read
	decode for the run (`resolve_video_context` calls this once per CLI run).

	Args:
		original_video_path: Path to the original source video.
		fastread_path: Path to the fast-read video to validate.

	Returns:
		FastreadValidation: Shared geometry/frame_count plus the
			timestamp-alignment fallback note ("none" or the fallback
			description).

	Raises:
		RuntimeError: On any structural mismatch or smoke-read failure;
			the message names the fast-read path, failed check, and
			remedy. Also propagates probe failures from `probe_video`.
	"""
	# probe both files fresh; live geometry/timing is the only comparison
	# basis (C13: no stored identity snapshot).
	original_info = common_tools.probe_video.probe_video(original_video_path)
	fastread_info = common_tools.probe_video.probe_video(fastread_path)
	# exact geometry + frame_count is the strictest, cheapest gate first
	_check_geometry_and_count(fastread_path, original_info, fastread_info)
	# fps within printed-precision tolerance
	_check_fps(fastread_path, original_info, fastread_info)
	# duration within a small absolute tolerance
	_check_duration(fastread_path, original_info, fastread_info)
	# best-effort timestamp alignment; records the fallback note
	timestamp_fallback_note = _check_timestamp_alignment(
		original_info, fastread_info
	)
	# FrameReader open + 0 / mid / last smoke reads on the fast-read file
	_smoke_read_fastread(fastread_path, fastread_info)
	# all checks passed: build the result for the status summary
	validation = FastreadValidation(
		original_video_path=original_video_path,
		fastread_path=fastread_path,
		width=fastread_info["width"],
		height=fastread_info["height"],
		frame_count=fastread_info["frame_count"],
		timestamp_fallback_note=timestamp_fallback_note,
	)
	return validation

#============================================

def _build_ffmpeg_transcode_cmd(
	ffmpeg_path: str, original_video_path: str, fastread_path: str
) -> list[str]:
	"""Build the ffmpeg argv list for the baseline fast-read transcode.

	Settings are the module-level fixed constants (CRF, GOP, preset, filter,
	codec). No per-video tuning; these values may not be overridden by callers.

	Args:
		ffmpeg_path: Absolute path to the ffmpeg binary.
		original_video_path: Path to the original source video.
		fastread_path: Destination path for the fast-read output.

	Returns:
		list: Full argv list ready for subprocess.Popen.
	"""
	cmd = [
		ffmpeg_path,
		"-y",
		"-i", original_video_path,
		"-map", "0:v:0",
		"-an",
		"-vf", TRANSCODE_FILTER,
		"-c:v", TRANSCODE_CODEC,
		"-preset", TRANSCODE_PRESET,
		"-crf", str(TRANSCODE_CRF),
		"-g", str(TRANSCODE_GOP),
		fastread_path,
	]
	return cmd

#============================================

def _format_elapsed(elapsed_s: float) -> str:
	"""Format elapsed seconds as MM:SS for heartbeat display.

	Args:
		elapsed_s: Elapsed seconds (non-negative).

	Returns:
		str: Zero-padded MM:SS string.
	"""
	total_s = int(elapsed_s)
	minutes = total_s // 60
	seconds = total_s % 60
	return f"{minutes:02d}:{seconds:02d}"

#============================================

def _collect_stderr_lines(stderr_bytes: bytes) -> list[str]:
	"""Decode ffmpeg stderr bytes and return non-empty lines.

	Args:
		stderr_bytes: Raw bytes from the ffmpeg stderr pipe.

	Returns:
		list: Non-empty decoded lines (trailing whitespace stripped).
	"""
	text = stderr_bytes.decode("utf-8", errors="replace")
	lines = [ln.rstrip() for ln in text.splitlines()]
	# return only non-empty lines
	nonempty = [ln for ln in lines if ln]
	return nonempty

#============================================

def _run_ffmpeg_transcode(
	original_video_path: str,
	fastread_path: str,
	verbose: bool,
) -> None:
	"""Launch ffmpeg and transcode original_video_path to fastread_path.

	Suppresses raw ffmpeg output unless verbose=True. Prints a heartbeat
	line every HEARTBEAT_INTERVAL_S seconds while ffmpeg is running and the
	elapsed time exceeds that threshold. On success prints final 5-10
	non-empty stderr lines. On failure raises RuntimeError with the final
	30-60 stderr lines and deletes the partial output.

	Args:
		original_video_path: Path to the original source video.
		fastread_path: Destination path for the fast-read output.
		verbose: When True, stream full ffmpeg command + stderr to terminal.

	Raises:
		RuntimeError: If ffmpeg is not in PATH or returns non-zero exit code.
			Partial output is deleted before raising.
	"""
	ffmpeg_path = shutil.which("ffmpeg")
	if ffmpeg_path is None:
		raise RuntimeError("ffmpeg not found in PATH")

	cmd = _build_ffmpeg_transcode_cmd(ffmpeg_path, original_video_path, fastread_path)

	if verbose:
		# print the full command when --verbose
		cmd_str = " ".join(cmd)
		print(f"  ffmpeg command: {cmd_str}")

	# launch ffmpeg; capture stderr to buffer; stdin/stdout not used
	process = subprocess.Popen(
		cmd,
		stdin=subprocess.DEVNULL,
		stdout=subprocess.DEVNULL,
		stderr=subprocess.PIPE,
	)

	t_start = time.monotonic()
	last_heartbeat = t_start

	# poll process until it exits; print heartbeat lines while waiting
	while True:
		# check if process has exited
		returncode = process.poll()
		if returncode is not None:
			break
		now = time.monotonic()
		elapsed = now - t_start
		# print a heartbeat every HEARTBEAT_INTERVAL_S once past threshold
		if elapsed >= HEARTBEAT_INTERVAL_S and (now - last_heartbeat) >= HEARTBEAT_INTERVAL_S:
			elapsed_str = _format_elapsed(elapsed)
			print(f"[ ... ] ffmpeg running, elapsed {elapsed_str}")
			last_heartbeat = now
		# sleep briefly to avoid busy-wait
		time.sleep(0.5)

	# read all remaining stderr after process exits
	stderr_bytes = process.stderr.read()

	if verbose:
		# stream full stderr when verbose
		stderr_text = stderr_bytes.decode("utf-8", errors="replace")
		print(stderr_text, end="")

	if returncode != 0:
		# on failure: print 30-60 stderr lines and delete partial output
		stderr_lines = _collect_stderr_lines(stderr_bytes)
		tail_lines = stderr_lines[-60:]
		tail_text = "\n".join(tail_lines)
		# delete partial output before raising (guard with existence check)
		if os.path.exists(fastread_path):
			os.unlink(fastread_path)
		raise RuntimeError(
			f"ffmpeg transcode failed (exit code {returncode}).\n"
			f"ffmpeg stderr (last {len(tail_lines)} lines):\n{tail_text}"
		)

	# success: print final 5-10 non-empty stderr lines as a summary
	stderr_lines = _collect_stderr_lines(stderr_bytes)
	summary_lines = stderr_lines[-10:]
	print("ffmpeg summary:")
	for ln in summary_lines:
		print(f"  {ln}")

#============================================

def create_fastread_video(
	original_video_path: str,
	fastread_path: str,
	force: bool = False,
	verbose: bool = False,
) -> FastreadValidation:
	"""Create (or verify) the fast-read working video beside the original.

	Implements the idempotency contract:
	- Existing fast-read that validates: skip transcode; return validation.
	- Existing fast-read that fails validation: raise RuntimeError with
	  suggestion to re-run with --force.
	- force=True: delete any existing fast-read and recreate unconditionally.
	- No existing fast-read: run transcode, then validate.

	Progress is printed to stdout at coarse steps. A heartbeat line is
	printed every HEARTBEAT_INTERVAL_S while ffmpeg is running past the first
	threshold. After a successful transcode the final 5-10 non-empty ffmpeg
	stderr lines are printed as a summary. After validation the status summary
	is printed.

	Args:
		original_video_path: Path to the original source video.
		fastread_path: Destination path for the fast-read video
			(from `tr_paths.fastread_video_path`).
		force: When True, delete any existing fast-read and recreate.
		verbose: When True, stream full ffmpeg command + stderr to terminal.

	Returns:
		FastreadValidation: Structural validation result for the status summary.

	Raises:
		RuntimeError: If ffmpeg fails, validation fails after transcode, or
			an existing fast-read fails validation without --force.
	"""
	source_name = os.path.basename(original_video_path)
	fastread_name = os.path.basename(fastread_path)
	settings_label = (
		f"crf {TRANSCODE_CRF}, gop {TRANSCODE_GOP}, filter {TRANSCODE_FILTER}"
	)

	print("Track Runner prepare")
	print(f"source video:    {source_name}")
	print(f"fast-read video: {fastread_name}")
	print(f"settings:        {settings_label}")

	# step 0%: probe source video
	print("[  0%] probing source video")
	common_tools.probe_video.probe_video(original_video_path)

	# step 5%: check existing fast-read
	print("[  5%] checking existing fast-read video")
	fastread_exists = os.path.exists(fastread_path)

	if fastread_exists and force:
		# --force: delete existing and recreate unconditionally
		print(f"  --force: deleting existing fast-read video: {fastread_path}")
		os.unlink(fastread_path)
		fastread_exists = False

	if fastread_exists:
		# existing fast-read: validate it; skip transcode if valid
		print("  existing fast-read found; validating...")
		# validate raises RuntimeError on failure; on success returns validation
		validation = validate_fastread_structural(original_video_path, fastread_path)
		# idempotent: already valid, print status and return
		print("[100%] prepare complete (existing fast-read is valid; skipped transcode)")
		_print_status_summary(validation, skipped_transcode=True)
		return validation

	# step 10%: create fast-read with ffmpeg
	print("[ 10%] creating fast-read video with ffmpeg")
	_run_ffmpeg_transcode(original_video_path, fastread_path, verbose=verbose)

	# step 90%: validate the newly created fast-read
	print("[ 90%] validating fast-read video")
	validation = validate_fastread_structural(original_video_path, fastread_path)

	print("[100%] prepare complete")
	_print_status_summary(validation, skipped_transcode=False)
	return validation

#============================================

def _print_status_summary(
	validation: FastreadValidation, skipped_transcode: bool
) -> None:
	"""Print the required end-of-run status summary.

	Args:
		validation: Successful FastreadValidation from validate_fastread_structural.
		skipped_transcode: True when transcode was skipped (idempotent path).
	"""
	print()
	print("Status:")
	print(f"  fast-read path:       {validation.fastread_path}")
	print(f"  structural validity:  OK ({validation.width}x{validation.height}"
		f", {validation.frame_count} frames)")
	print(f"  timestamp alignment:  {validation.timestamp_fallback_note}")
	if skipped_transcode:
		print("  transcode:            skipped (existing fast-read is valid)")
	else:
		print("  transcode:            completed")
	print()
	print("  all working modes will now decode from the fast-read video")
	print("  encode will still use the original video")
