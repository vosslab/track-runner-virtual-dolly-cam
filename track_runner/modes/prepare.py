"""Implementation for the track_runner prepare CLI mode."""

import argparse

import fastread_video
import tr_paths


def run(args: argparse.Namespace, video_info: dict) -> None:
	"""Prepare mode: create the fast-read working video.

	Always rebuilds the fast-read from scratch: any existing fast-read is
	deleted and the transcode runs unconditionally.

	prepare uses the ORIGINAL video only (it is the creator). No config,
	seeds, or diagnostics are read. This mode does not require setup to have
	been run first.

	Args:
		args: Parsed argparse namespace (expects verbose).
		video_info: Video metadata dict from video_artifacts.probe_video (not used directly;
			kept for signature consistency with other mode functions).
	"""
	fastread_path = tr_paths.fastread_video_path(args.input_file)
	verbose = args.verbose
	fastread_video.create_fastread_video(
		args.input_file,
		fastread_path,
		verbose=verbose,
	)
