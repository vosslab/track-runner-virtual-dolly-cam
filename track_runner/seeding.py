"""Interactive seeding UI for track_runner.

Collects seed points from the user by showing video frames at intervals
and letting the user draw rectangles around the runner's upper torso.
Seeds use the canonical current JSON schema (no full-person estimation).
"""

# PIP3 modules
from PySide6.QtWidgets import QApplication

# local repo modules
import common_tools.probe_video as probe_video
import ui.workspace as workspace_module
import ui.seed_controller as seed_controller_module
import ui.target_controller as target_controller_module
import ui.edit_controller as edit_controller_module
import ui.session as session_module
import ui.frame_source as frame_source_module

AnnotationWindow = workspace_module.AnnotationWindow
SeedController = seed_controller_module.SeedController
TargetController = target_controller_module.TargetController
EditController = edit_controller_module.EditController
AnnotationSession = session_module.AnnotationSession
FrameSource = frame_source_module.FrameSource

#============================================
def collect_seeds(
	decode_video_path: str,
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
		decode_video_path: Path to decode frames from. This is the run's
			working_decode path (fast-read when present and valid, else the
			original). Seed identity/state keys off the original elsewhere.
		interval_seconds: Time between seed frames in seconds.
		config: Configuration dict.
		pass_number: Which collection pass this is (default 1 = initial).
		existing_seeds: Optional list of already-collected seeds to append to.
		pre_provided_seeds: Optional list of pre-built seed dicts for testing.
		frame_count_override: Optional frame count to use instead of the
			value probed via mediainfo (e.g. when the caller already
			has a verified count in hand from another tool).
		debug: Enable verbose frame-reading output.
		save_callback: Optional callable(seeds_list) invoked after each new
			seed is collected, for crash-safe incremental saving.
		time_range: Optional (start_s, end_s) tuple to limit candidate frames.
			Either value may be None for open-ended ranges.
		predictions: Optional dict mapping frame_index to prediction dicts
			with "forward"/"backward" state dicts for overlay display.
		start_frame: Optional frame index to seek the UI to on launch.

	Returns:
		List of current-format seed dicts (existing + newly collected).
	"""
	# headless mode: return pre-provided seeds without opening video
	if pre_provided_seeds is not None:
		return list(pre_provided_seeds)

	# start with a copy of any existing seeds
	all_seeds = list(existing_seeds) if existing_seeds else []

	# probe the decode video file to get metadata
	probe_info = probe_video.probe_video(decode_video_path)
	fps = probe_info["fps"]
	# use caller-supplied frame count when provided; otherwise the
	# value probed via mediainfo
	if frame_count_override is not None:
		total_frames = frame_count_override
	else:
		total_frames = probe_info["frame_count"]
	# FrameSource constructs and owns the reader on its decode thread.
	reader = FrameSource({
		"video_path": decode_video_path,
		"fps": fps,
		"total_frames": total_frames,
		"bin_factor": 1,
		"debug": debug,
	})

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

	# One session keeps the reader, human seed list, and predictions alive
	# while its mode controllers are replaced in place.
	def make_seed_controller() -> SeedController:
		return SeedController(
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

	def make_target_controller() -> TargetController:
		return TargetController(
			sorted_targets=seed_frame_indices,
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

	def make_edit_controller() -> EditController:
		return EditController(
			work_seeds=all_seeds,
			filtered_indices=list(range(len(all_seeds))),
			reader=reader,
			fps=fps,
			config=config,
			save_callback=save_callback or (lambda ws: None),
			predictions=predictions,
			start_frame=start_frame,
		)

	session = AnnotationSession(
		video_context={"fps": fps, "total_frames": total_frames, "config": config},
		reader=reader,
		seed_store=all_seeds,
		prediction_store=predictions,
		controller_factories={
			"seed": make_seed_controller,
			"target": make_target_controller,
			"edit": make_edit_controller,
		},
	)
	# Create window and let the session install the initial controller.
	window = AnnotationWindow("Track Runner - Seed Collection")
	window.set_session(session)
	window.show()
	app.exec()

	session.close()
	return list(session.seed_store)


#============================================
def collect_seeds_at_frames(
	decode_video_path: str,
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
		decode_video_path: Path to decode frames from. This is the run's
			working_decode path (fast-read when present and valid, else the
			original). Seed identity/state keys off the original elsewhere.
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
		List of current-format seed dicts (existing + newly collected).
	"""
	if not target_frames:
		return list(existing_seeds) if existing_seeds else []

	# start with a copy of any existing seeds
	all_seeds = list(existing_seeds) if existing_seeds else []

	# probe the decode video file to get metadata
	probe_info = probe_video.probe_video(decode_video_path)
	fps = probe_info["fps"]
	total_frames = probe_info["frame_count"]
	# FrameSource constructs and owns the reader on its decode thread.
	reader = FrameSource({
		"video_path": decode_video_path,
		"fps": fps,
		"total_frames": total_frames,
		"bin_factor": 1,
		"debug": debug,
	})

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

	# Keep all three modes available without reopening the video or reader.
	def make_seed_controller() -> SeedController:
		return SeedController(
			seed_frame_indices=list(range(total_frames)),
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

	def make_target_controller() -> TargetController:
		return TargetController(
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

	def make_edit_controller() -> EditController:
		return EditController(
			work_seeds=all_seeds,
			filtered_indices=list(range(len(all_seeds))),
			reader=reader,
			fps=fps,
			config=config,
			save_callback=save_callback or (lambda ws: None),
			predictions=predictions,
			start_frame=start_frame,
		)

	session = AnnotationSession(
		video_context={"fps": fps, "total_frames": total_frames, "config": config},
		reader=reader,
		seed_store=all_seeds,
		prediction_store=predictions,
		controller_factories={
			"seed": make_seed_controller,
			"target": make_target_controller,
			"edit": make_edit_controller,
		},
	)
	# Create window and let the session install the initial controller.
	window = AnnotationWindow("Track Runner - Target Collection", initial_mode="target")
	window.set_session(session)
	window.show()
	app.exec()

	session.close()
	return list(session.seed_store)
