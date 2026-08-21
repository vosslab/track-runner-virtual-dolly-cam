"""Audio-stream detection and muxing for encoded track_runner video."""

# Standard Library
import shutil
import subprocess


#============================================
def _input_has_audio(input_path: str) -> bool:
	"""Return whether a video file contains at least one audio stream."""
	ffprobe_path = shutil.which("ffprobe")
	if ffprobe_path is None:
		raise RuntimeError("ffprobe not found in PATH")
	cmd = [
		ffprobe_path,
		"-v", "error",
		"-select_streams", "a",
		"-show_entries", "stream=index",
		"-of", "csv=p=0",
		input_path,
	]
	result = subprocess.run(cmd, capture_output=True, text=True)
	has_audio = len(result.stdout.strip()) > 0
	return has_audio


#============================================
def copy_audio(input_path: str, video_path: str, output_path: str) -> None:
	"""Mux input audio with a cropped video, preserving existing failures."""
	ffmpeg_path = shutil.which("ffmpeg")
	if ffmpeg_path is None:
		raise RuntimeError("ffmpeg not found in PATH")
	if not _input_has_audio(input_path):
		print(f"No audio stream in {input_path}, remuxing video only")
		cmd = [
			ffmpeg_path,
			"-y",
			"-i", video_path,
			"-c:v", "copy",
			output_path,
		]
		result = subprocess.run(cmd, capture_output=True, text=True)
		if result.returncode != 0:
			raise RuntimeError(
				f"ffmpeg remux (no audio) failed with code "
				f"{result.returncode}: {result.stderr}"
			)
		return
	cmd = [
		ffmpeg_path,
		"-y",
		"-i", video_path,
		"-i", input_path,
		"-c:v", "copy",
		"-c:a", "aac",
		"-map", "0:v:0",
		"-map", "1:a:0",
		"-shortest",
		output_path,
	]
	result = subprocess.run(cmd, capture_output=True, text=True)
	if result.returncode != 0:
		raise RuntimeError(
			f"ffmpeg mux failed with code {result.returncode}: "
			f"{result.stderr}"
		)
