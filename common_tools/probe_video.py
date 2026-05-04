"""Probe video metadata via the mediainfo CLI.

Provides a single public function `probe_video(path) -> dict` that returns
the metadata (width, height, fps, frame_count, duration_s) needed to
construct a `common_tools.frame_reader.FrameReader`. Uses mediainfo
because OpenCV-reported metadata is unreliable on some MOV/HEVC files
(see common_tools/frame_reader.py for the underlying decode path).

Single source of truth for production probes. Do not re-add bare
`cv2.VideoCapture(...).get(CAP_PROP_*)` probes; call this instead.
"""

# Standard Library
import json
import shutil
import subprocess


#============================================
def probe_video(input_file: str) -> dict:
	"""Probe video metadata using mediainfo JSON output.

	Extracts resolution, fps, frame count, and duration from the first
	video track. Falls back to General track for frame count and
	duration when the Video track lacks them.

	Args:
		input_file: Path to the input video file.

	Returns:
		Dict with keys: width, height, fps, frame_count, duration_s.

	Raises:
		RuntimeError: If mediainfo is not on PATH, mediainfo fails,
			the file has no Video track, or fps is not positive.
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
		raise RuntimeError(
			f"no frame count or duration from mediainfo: {input_file}"
		)
	info = {
		"width": width,
		"height": height,
		"fps": fps,
		"frame_count": frame_count,
		"duration_s": duration_s,
	}
	return info
