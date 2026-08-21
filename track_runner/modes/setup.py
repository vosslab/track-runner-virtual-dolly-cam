"""Implementation for the track_runner setup CLI mode."""

import argparse

import fastread_video
import setup_mode


def run(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	config_path: str,
	video_context: fastread_video.VideoContext,
) -> None:
	"""Setup mode: interactive questionnaire for camera configuration.

	Launches an interactive CLI questionnaire to collect per-video camera
	settings (zoom type, camera height, position, track size) and stores
	them in the configuration file.

	Setup may decode the fast-read working video for any display, but the
	configuration it writes always keys off the original video: config_path
	is derived from the original and video_context.metadata_identity (the
	original) is the recorded source. The fast-read video must never be
	recorded as the configured source.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict (may be modified).
		video_info: Video metadata dict.
		config_path: Path to the configuration file (keyed off the original).
		video_context: Resolved per-run routing. Config/state writes use
			video_context.original_video_path; only frame decode for display
			may use video_context.working_decode.path.
	"""
	# show which physical video frames decode from for this run; the config
	# written by run_setup still keys off the original (config_path).
	fastread_video.print_video_routing_banner(
		video_context.original_video_path,
		video_context.working_decode.path,
	)
	setup_mode.run_setup(config_path, cfg)
