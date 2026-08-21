"""Video metadata probes and saved-artifact identity checks."""

import json
import os
import shutil
import subprocess

import tr_schema
import tr_video_identity
import torso_box_coords_io


#============================================
def probe_video(input_file: str) -> dict:
	"""Probe video metadata using mediainfo JSON output.

	Extracts resolution, fps, frame count, and duration from the first
	video track. Falls back to General track for frame count and duration
	when the Video track lacks them.

	Args:
		input_file: Path to the input video file.

	Returns:
		Dict with keys: width, height, fps, frame_count, duration_s.

	Raises:
		RuntimeError: If mediainfo fails or returns no video track.
	"""
	mediainfo_path = shutil.which("mediainfo")
	if mediainfo_path is None:
		raise RuntimeError("mediainfo not found in PATH")
	cmd = [mediainfo_path, "--Output=JSON", input_file]
	result = subprocess.run(cmd, capture_output=True, text=True)
	if result.returncode != 0:
		raise RuntimeError(f"mediainfo failed: {result.stderr.strip()}")
	data = json.loads(result.stdout)
	media = data.get("media")
	if media is None:
		raise RuntimeError(f"mediainfo returned no media for: {input_file}")
	tracks = media.get("track", [])
	# find the first Video track and General track
	video_track = None
	general_track = None
	for track in tracks:
		track_type = track.get("@type", "")
		if track_type == "Video" and video_track is None:
			video_track = track
		elif track_type == "General" and general_track is None:
			general_track = track
	if video_track is None:
		raise RuntimeError(f"no video track found in: {input_file}")
	# extract resolution
	width = int(video_track["Width"])
	height = int(video_track["Height"])
	# extract fps (mediainfo provides FrameRate as a decimal string)
	fps = float(video_track.get("FrameRate", "0"))
	if fps <= 0:
		raise RuntimeError(f"invalid fps from mediainfo: {input_file}")
	# extract frame count; fall back to General track, then duration * fps
	frame_count_str = video_track.get("FrameCount")
	if frame_count_str is None and general_track is not None:
		frame_count_str = general_track.get("FrameCount")
	duration_str = video_track.get("Duration")
	if duration_str is None and general_track is not None:
		duration_str = general_track.get("Duration")
	if frame_count_str is not None:
		frame_count = int(frame_count_str)
		duration_s = frame_count / fps
	elif duration_str is not None:
		duration_s = float(duration_str)
		frame_count = int(duration_s * fps)
	else:
		raise RuntimeError(f"no frame count or duration from mediainfo: {input_file}")
	info = {
		"width": width,
		"height": height,
		"fps": fps,
		"frame_count": frame_count,
		"duration_s": duration_s,
	}
	return info


#============================================
def _load_stored_video_identity(path: str) -> dict | None:
	"""Return an artifact's persisted video identity when it is readable."""
	if path.endswith(".npz"):
		schema = torso_box_coords_io.peek_torso_box_coords_schema(path)
		if schema is None or not tr_schema.is_supported_artifact_schema(
			"torso_box_coords", schema,
		):
			return None
		coords_data = torso_box_coords_io.load_torso_box_coords(path)
		stored = coords_data.get("video_identity")
		return stored
	with open(path, "r") as fh:
		data = json.load(fh)
	stored = data.get("video_identity")
	return stored


#============================================
def check_identity_mismatch(
	label: str,
	path: str,
	video_identity: dict | None,
) -> None:
	"""Reject a data file whose geometry belongs to another video.

	Reads the stored `video_identity` block from the file (JSON or
	NPZ) and compares it against `video_identity`. A source geometry or
	frame-count mismatch rejects the artifact before a consuming mode can
	reuse it. Filename and container-metadata differences remain advisory:
	their frame geometry is still compatible.

	Args:
		label: Human-readable name for the data file (e.g. "seeds").
		path: Path to the data file (`.json` or `.npz`).
		video_identity: Current run identity fingerprint, if available.

	Raises:
		RuntimeError: If the stored artifact has incompatible frame geometry.
	"""
	if video_identity is None:
		return
	if not os.path.isfile(path):
		return
	stored = _load_stored_video_identity(path)
	if stored is None:
		raise RuntimeError(
			f"{label} file lacks a current video identity; "
			"run solve to rebuild derived artifacts or re-annotate seeds"
		)
	result = tr_video_identity.compare_video_identity(stored, video_identity)
	if result["blocking"]:
		summary = tr_video_identity.summarize_mismatches(result)
		raise RuntimeError(
			f"{label} file belongs to incompatible video geometry:\n{summary}\n"
			"Run solve to regenerate derived artifacts; re-annotate seeds for "
			"a different video."
		)
	if result["informational"]:
		print(f"  warning: {label} file video identity mismatch:")
		summary = tr_video_identity.summarize_mismatches(result)
		for line in summary.split("\n"):
			print(f"    {line}")


#============================================
def clear_incompatible_derived_artifact(
	label: str,
	path: str,
	video_identity: dict,
) -> bool:
	"""Remove a derived artifact with incompatible video geometry for solve.

	Solve is the one operation that rebuilds derived interval artifacts. It
	clears a saved diagnostic or coordinate store only when the persisted
	geometry cannot match the current input. Seeds are human-authored and are
	never passed here.

	Returns:
		True when an incompatible artifact was removed.
	"""
	if not os.path.isfile(path):
		return False
	stored = _load_stored_video_identity(path)
	if stored is None:
		print(f"  existing {label} lacks current video identity; solve will regenerate it")
		os.remove(path)
		return True
	result = tr_video_identity.compare_video_identity(stored, video_identity)
	if not result["blocking"]:
		return False
	summary = tr_video_identity.summarize_mismatches(result)
	print(f"  existing {label} belongs to another video; solve will regenerate it")
	for line in summary.split("\n"):
		print(f"    {line}")
	os.remove(path)
	return True
