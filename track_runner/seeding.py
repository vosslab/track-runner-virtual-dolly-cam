"""Interactive seeding UI for track_runner v2.

Collects seed points from the user by showing video frames at intervals
and letting the user draw rectangles around the runner's upper torso.
Seeds are returned in the v2 JSON format (no full-person estimation).
"""

# PIP3 modules
import cv2
from PySide6.QtWidgets import QApplication

# local repo modules
import common_tools.frame_reader as frame_reader
import ui.workspace as workspace_module
import ui.seed_controller as seed_controller_module
import ui.target_controller as target_controller_module

AnnotationWindow = workspace_module.AnnotationWindow
SeedController = seed_controller_module.SeedController
TargetController = target_controller_module.TargetController

#============================================
def collect_seeds(
	video_path: str,
	interval_seconds: float,
	config: dict,
	pass_number: int = 1,
	existing_seeds: list | None = None,
	pre_provided_seeds: list | None = None,
	frame_count_override: int | None = None,
	debug: bool = False,
	save_callback: object = None,
	time_range: tuple | None = None,
	predictions: dict | None = None,
	start_frame: int | None = None,
) -> list:
	"""Collect initial seed points for runner tracking (pass 1).

	Opens an interactive UI at regularly spaced frames for the user to draw
	torso rectangles. New seeds append to existing_seeds; never overwrites.

	If pre_provided_seeds is not None, returns them directly for headless
	or automated testing.

	Args:
		video_path: Path to the input video file.
		interval_seconds: Time between seed frames in seconds.
		config: Configuration dict.
		pass_number: Which collection pass this is (default 1 = initial).
		existing_seeds: Optional list of already-collected seeds to append to.
		pre_provided_seeds: Optional list of pre-built seed dicts for testing.
		frame_count_override: Optional frame count from ffprobe to use instead
			of OpenCV's CAP_PROP_FRAME_COUNT (which can be inaccurate).
		debug: Enable verbose frame-reading output.
		save_callback: Optional callable(seeds_list) invoked after each new
			seed is collected, for crash-safe incremental saving.
		time_range: Optional (start_s, end_s) tuple to limit candidate frames.
			Either value may be None for open-ended ranges.
		predictions: Optional dict mapping frame_index to prediction dicts
			with "forward"/"backward" state dicts for overlay display.
		start_frame: Optional frame index to seek the UI to on launch.

	Returns:
		List of seed dicts in v2 format (existing + newly collected).
	"""
	# headless mode: return pre-provided seeds without opening video
	if pre_provided_seeds is not None:
		return list(pre_provided_seeds)

	# start with a copy of any existing seeds
	all_seeds = list(existing_seeds) if existing_seeds else []

	# open the video file to get metadata
	cap = cv2.VideoCapture(video_path)
	if not cap.isOpened():
		raise RuntimeError(f"cannot open video: {video_path}")
	fps = cap.get(cv2.CAP_PROP_FPS)
	# prefer ffprobe frame count over OpenCV (which can be inaccurate)
	if frame_count_override is not None:
		total_frames = frame_count_override
	else:
		total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
	cap.release()
	if fps <= 0:
		raise RuntimeError(f"invalid fps from video: {video_path}")
	# create reliable frame reader with sequential fallback
	reader = frame_reader.FrameReader(video_path, fps, total_frames, debug=debug)

	# compute frame interval for the requested seed spacing
	frame_interval = int(round(fps * interval_seconds))
	if frame_interval < 1:
		frame_interval = 1

	# generate candidates at the requested interval
	seed_frame_indices = list(range(0, total_frames, frame_interval))
	# filter candidates by time_range if provided
	if time_range is not None:
		start_s, end_s = time_range
		start_frame = int(start_s * fps) if start_s is not None else 0
		end_frame = int(end_s * fps) if end_s is not None else total_frames
		original_count = len(seed_frame_indices)
		seed_frame_indices = [
			f for f in seed_frame_indices
			if start_frame <= f <= end_frame
		]
		filtered = original_count - len(seed_frame_indices)
		if filtered > 0:
			print(f"  time_range filter: kept {len(seed_frame_indices)} "
				f"of {original_count} candidates")
	# filter out frames that already have seeds to prevent duplicates
	if all_seeds:
		existing_frame_set = set(int(s["frame_index"]) for s in all_seeds)
		original_count = len(seed_frame_indices)
		filtered = []
		for fi in seed_frame_indices:
			if fi not in existing_frame_set:
				filtered.append(fi)
			else:
				# bump to next unused frame so user still gets a distinct frame
				bumped = fi + 1
				while bumped in existing_frame_set and bumped < total_frames:
					bumped += 1
				if bumped < total_frames and bumped not in existing_frame_set:
					filtered.append(bumped)
					# mark bumped frame as used to avoid future collisions
					existing_frame_set.add(bumped)
		seed_frame_indices = filtered
		skipped = original_count - len(seed_frame_indices)
		if skipped > 0:
			print(f"  filtered {skipped} candidates that already have seeds")
	print(f"  total_frames={total_frames}, frame_interval={frame_interval}, "
		f"candidates={len(seed_frame_indices)}")
	if all_seeds:
		print(f"  {len(all_seeds)} existing seeds, "
			f"{len(seed_frame_indices)} candidates at {interval_seconds}s interval")

	# Create QApplication if not already running
	app = QApplication.instance()
	if app is None:
		app = QApplication([])

	# Create window and controller
	window = AnnotationWindow("Track Runner - Seed Collection")
	controller = SeedController(
		seed_frame_indices=seed_frame_indices,
		reader=reader,
		fps=fps,
		config=config,
		all_seeds=all_seeds,
		save_callback=save_callback,
		pass_number=pass_number,
		mode_str="initial",
		predictions=predictions,
		start_frame=start_frame,
	)
	window.set_controller(controller)
	window.show()
	app.exec()

	reader.close()
	all_seeds = controller.get_final_seeds()
	return all_seeds


#============================================
def collect_seeds_at_frames(
	video_path: str,
	target_frames: list,
	config: dict,
	pass_number: int = 2,
	mode: str = "suggested_refine",
	existing_seeds: list | None = None,
	predictions: dict | None = None,
	debug: bool = False,
	save_callback: object = None,
	start_frame: int | None = None,
) -> list:
	"""Collect seed points at specific frame indices (refinement passes).

	Opens an interactive UI at each target frame, with arrow key
	scrubbing and optional trajectory prediction overlay. New seeds
	append to existing_seeds; never overwrites.

	Args:
		video_path: Path to the input video file.
		target_frames: List of frame indices to seed at.
		config: Configuration dict.
		pass_number: Which collection pass this is (default 2 = first refinement).
		mode: Seed mode string such as "suggested_refine", "interval_refine",
			or "gap_refine".
		existing_seeds: Optional list of already-collected seeds to append to.
		predictions: Optional dict mapping frame_index (int) to prediction
			dicts with "forward"/"backward" state dicts for overlay display.
		debug: Enable verbose frame-reading output.
		save_callback: Optional callable(seeds_list) invoked after each new
			seed is collected, for crash-safe incremental saving.
		start_frame: Optional frame index to seek the UI to on launch.

	Returns:
		List of seed dicts in v2 format (existing + newly collected).
	"""
	if not target_frames:
		return list(existing_seeds) if existing_seeds else []

	# start with a copy of any existing seeds
	all_seeds = list(existing_seeds) if existing_seeds else []

	# open the video file to get metadata
	cap = cv2.VideoCapture(video_path)
	if not cap.isOpened():
		raise RuntimeError(f"cannot open video: {video_path}")
	fps = cap.get(cv2.CAP_PROP_FPS)
	total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
	cap.release()
	if fps <= 0:
		raise RuntimeError(f"invalid fps from video: {video_path}")
	# create reliable frame reader with sequential fallback
	reader = frame_reader.FrameReader(video_path, fps, total_frames, debug=debug)

	sorted_targets = sorted(target_frames)
	# filter out frames that already have seeds to prevent duplicates
	if all_seeds:
		existing_frame_set = set(int(s["frame_index"]) for s in all_seeds)
		original_count = len(sorted_targets)
		sorted_targets = [fi for fi in sorted_targets if fi not in existing_frame_set]
		skipped = original_count - len(sorted_targets)
		if skipped > 0:
			print(f"  filtered {skipped} target frames that already have seeds")
	if not sorted_targets:
		# all targets already seeded, nothing to do
		reader.close()
		return all_seeds

	# Create QApplication if not already running
	app = QApplication.instance()
	if app is None:
		app = QApplication([])

	# Create window and controller
	window = AnnotationWindow("Track Runner - Target Collection", initial_mode="target")
	controller = TargetController(
		sorted_targets=sorted_targets,
		reader=reader,
		fps=fps,
		config=config,
		all_seeds=all_seeds,
		save_callback=save_callback,
		pass_number=pass_number,
		mode_str=mode,
		predictions=predictions,
		start_frame=start_frame,
	)
	window.set_controller(controller)
	window.show()
	app.exec()

	reader.close()
	all_seeds = controller.get_final_seeds()
	return all_seeds
