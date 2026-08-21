"""Seed collection controller for track runner annotation.

Manages the Seed mode annotation workflow with keyboard shortcuts and
mouse drawing for seed collection.
"""

import collections.abc

# PIP3 modules
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton

# local repo modules
import seed_color
import common_tools.coord_space
import ui.base_controller as base_controller_module
import ui.frame_source as frame_source_module
import ui.status_presenter as status_presenter_module

BaseAnnotationController = base_controller_module.BaseAnnotationController
StatusPresenter = status_presenter_module.StatusPresenter

#============================================


class SeedController(BaseAnnotationController):
	"""Manages the Seed mode annotation workflow.

	Handles keyboard shortcuts and mouse drawing for collect_seeds().
	"""

	def __init__(
		self,
		seed_frame_indices: list,
		reader: frame_source_module.FrameSource,
		fps: float,
		config: dict,
		all_seeds: list,
		save_callback: collections.abc.Callable[[list], None] | None,
		pass_number: int = 1,
		mode_str: str = "initial",
		predictions: dict | None = None,
		return_callback: collections.abc.Callable[[list], None] | None = None,
		start_frame: int | None = None,
	) -> None:
		"""Initialize the SeedController.

		Args:
			seed_frame_indices: List of frame indices to collect seeds at.
			reader: FrameSource that asynchronously provides requested frames.
			fps: Frames per second of the video.
			config: Configuration dict.
			all_seeds: List of existing seeds to preserve.
			save_callback: Callable(seeds_list) to save seeds incrementally.
			pass_number: Which collection pass this is (default 1).
			mode_str: Seed collection mode string (default "initial").
			predictions: Optional dict mapping frame_index to prediction dicts.
			return_callback: Optional callable(new_seeds) to return to edit mode.
			start_frame: Optional frame index to seek to on first activate.
		"""
		super().__init__(
			reader=reader,
			fps=fps,
			config=config,
			save_callback=save_callback,
			predictions=predictions,
		)
		self._frame_source_connected = False
		self._connect_frame_source()

		self._seed_frame_indices = seed_frame_indices
		self._all_seeds = all_seeds
		self._pass_number = pass_number
		self._mode_str = mode_str
		self._return_callback = return_callback
		self._start_frame = start_frame
		self._start_frame_used = False

		self._list_idx = 0
		self._current_frame = seed_frame_indices[0] if seed_frame_indices else 0
		self._new_seeds: list = []

		# scrub step in frames, adjustable via [ and ]
		self._scrub_step_frames: int = 2
		self._step_value_label: QLabel | None = None

		# auto-seed suggestion state
		self._suggestion: dict | None = None
		self._detector: object = None
		self._detector_unavailable: bool = False
		self._detection_cache: dict = {}
		# QWidget construction belongs to activation, after QApplication
		# exists. Controller construction also happens in headless tests.
		self._status_presenter: StatusPresenter | None = None

	#============================================

	def _build_toolbar(self) -> QWidget:
		"""Build the toolbar widget with nav and draw mode buttons.

		Returns:
			QWidget containing prev/next and draw mode buttons.
		"""
		widget = QWidget()
		layout = QHBoxLayout(widget)
		layout.setContentsMargins(4, 0, 4, 0)
		layout.setSpacing(4)

		# Navigation buttons
		btn_prev = QPushButton("<  Prev")
		btn_prev.setToolTip("Previous frame (Shift+LEFT)")
		btn_prev.clicked.connect(self._on_prev_button)
		layout.addWidget(btn_prev)

		btn_next = QPushButton("Next  >")
		btn_next.setToolTip("Next frame (Shift+RIGHT)")
		btn_next.clicked.connect(self._on_next_button)
		layout.addWidget(btn_next)

		btn_skip = QPushButton("Skip")
		btn_skip.setToolTip("Skip this frame (SPACE)")
		btn_skip.clicked.connect(self._on_skip)
		layout.addWidget(btn_skip)

		# Step size control: [ - ] N [ + ]
		layout.addSpacing(8)
		step_label = QLabel("Step:")
		layout.addWidget(step_label)
		btn_step_down = QPushButton("[")
		btn_step_down.setFixedWidth(24)
		btn_step_down.setToolTip("Decrease step size ([)")
		btn_step_down.clicked.connect(self._decrease_step)
		layout.addWidget(btn_step_down)
		self._step_value_label = QLabel(self._step_label())
		layout.addWidget(self._step_value_label)
		btn_step_up = QPushButton("]")
		btn_step_up.setFixedWidth(24)
		btn_step_up.setToolTip("Increase step size (])")
		btn_step_up.clicked.connect(self._increase_step)
		layout.addWidget(btn_step_up)

		# Separator space
		layout.addSpacing(12)

		# Draw mode toggle buttons (checkable for visual state)
		self._btn_partial = QPushButton("Partial")
		self._btn_partial.setCheckable(True)
		self._btn_partial.setToolTip("Toggle partial draw mode (P)")
		self._btn_partial.clicked.connect(self._on_partial_toggle)
		layout.addWidget(self._btn_partial)

		self._btn_approx = QPushButton("Approx")
		self._btn_approx.setCheckable(True)
		self._btn_approx.setToolTip("Toggle approx/obstruction draw mode (A)")
		self._btn_approx.clicked.connect(self._on_approx_toggle)
		layout.addWidget(self._btn_approx)

		return widget

	#============================================

	def _on_activated(self) -> None:
		"""Show keybinding instructions and load the first frame."""
		self._connect_frame_source()
		# Keep all annotation results in the window's persistent status area.
		self._status_presenter = StatusPresenter()
		self._window.statusBar().addWidget(self._status_presenter.get_widget())
		self._set_status_text(self._get_default_status_text())

		# One-shot seek to start_frame if provided
		if self._start_frame is not None and not self._start_frame_used:
			self._start_frame_used = True
			# advance _list_idx to first candidate at or after start_frame
			for i, fi in enumerate(self._seed_frame_indices):
				if fi >= self._start_frame:
					self._list_idx = i
					break
			else:
				# all candidates before start_frame, go to last
				self._list_idx = len(self._seed_frame_indices) - 1
			self._current_frame = self._seed_frame_indices[self._list_idx]

		# Load and display the first frame
		self._refresh_frame()
		self._update_scale_bar()

	#============================================

	def _on_deactivated(self) -> None:
		"""Clean up seed-specific state (counters, etc)."""
		self._disconnect_frame_source()
		if self._window is not None and self._status_presenter is not None:
			self._window.statusBar().removeWidget(
				self._status_presenter.get_widget()
			)
		if self._status_presenter is not None:
			self._status_presenter.clear()
			self._status_presenter = None

	#============================================

	def _connect_frame_source(self) -> None:
		"""Subscribe only while this controller can own window updates."""
		if not self._frame_source_connected:
			self._reader.frame_ready.connect(self._on_frame_ready)
			self._frame_source_connected = True

	#============================================

	def _disconnect_frame_source(self) -> None:
		"""Prevent late worker results reaching a deactivated controller."""
		if self._frame_source_connected:
			self._reader.frame_ready.disconnect(self._on_frame_ready)
			self._frame_source_connected = False

	#============================================

	def _get_default_status_text(self) -> str:
		"""Short mode summary for the status bar.

		Returns:
			String with mode summary.
		"""
		if self._return_callback is not None:
			text = "Seed mode - add seeds (ESC to return)"
		else:
			text = "Seed mode - draw torso box"
		return text

	#============================================

	def _get_mode_name(self) -> str:
		"""Mode name for display.

		Returns:
			String "seed".
		"""
		return "seed"

	#============================================

	def _set_status_text(self, text: str) -> None:
		"""Route annotation feedback through the persistent GUI presenter."""
		if self._status_presenter is not None:
			self._status_presenter.show_feedback(text)

	#============================================

	def _show_seed_feedback(self, text: str) -> None:
		"""Show a user-facing seed result in the persistent status area."""
		if self._window is not None:
			self._set_status_text(text)

	#============================================

	def _refresh_frame(self) -> None:
		"""Request the current frame without blocking the Qt event loop."""
		self._reader.request_frame(self._current_frame)

	#============================================

	def _on_frame_ready(self, frame_index: int, frame: object) -> None:
		"""Display the asynchronously decoded current frame."""
		if frame_index != self._current_frame:
			return
		if frame is not None:
			self._window.set_frame(frame)
			self._current_bgr = frame
			self._update_fwd_bwd_overlays()
			self._update_scale_bar()
			# Recenter on prediction center when zoomed in
			self._recenter_on_prediction()
			# compute auto-seed suggestion for this frame
			self._compute_suggestion()

		# update progress bar
		self._window.set_progress(
			self._list_idx + 1, len(self._seed_frame_indices)
		)

		# update title bar with current state
		self._refresh_frame_title()

	#============================================

	def _get_detector(self) -> object | None:
		"""Get or create a YOLO detector instance.

		Lazy-loads the detector on first call. Returns None if YOLO
		weights are not available.

		Returns:
			YoloDetector instance or None if weights unavailable.
		"""
		if self._detector_unavailable:
			return None
		if self._detector is None:
			# lazy import to avoid circular dependencies
			import tr_detection as detection_module

			# ensure weights exist before creating detector
			weights_path = (
				detection_module.ensure_yolo_weights(quiet=True)
			)
			if not weights_path:
				# seed UI can function without YOLO suggestions
				self._detector_unavailable = True
				return None

			# create detector directly without create_detector()
			# to avoid config structure issues
			self._detector = detection_module.YoloDetector(
				weights_path,
				confidence_threshold=detection_module.YOLO_CONFIDENCE_THRESHOLD,
				nms_threshold=detection_module.YOLO_NMS_THRESHOLD,
			)
		return self._detector

	#============================================

	def _compute_suggestion(self) -> None:
		"""Compute and store auto-seed suggestion for current frame.

		Runs YOLO detection and calls suggest_seed_candidates().
		Results cached per frame. Updates self._suggestion and
		redraws frame overlay.
		"""
		# check cache first
		if self._current_frame in self._detection_cache:
			self._suggestion = self._detection_cache[self._current_frame]
			return

		# try to get detector
		detector = self._get_detector()
		if detector is None or self._current_bgr is None:
			# detector unavailable, no suggestions
			self._suggestion = {
				"candidates": [],
				"suggestion_index": None,
				"mode": "none",
				"scores": None,
			}
			self._detection_cache[self._current_frame] = self._suggestion
			return

		# run YOLO detection on current frame
		try:
			detections = detector.detect(self._current_bgr)
		except RuntimeError:
			# Detector failures are normalized in tr_detection.py, so this
			# path stays cv2-free and treats any detector error as "no suggestions".
			self._suggestion = {
				"candidates": [],
				"suggestion_index": None,
				"mode": "none",
				"scores": None,
			}
			self._detection_cache[self._current_frame] = self._suggestion
			return

		# get confirmed seeds from all_seeds + new_seeds
		confirmed_seeds = self._all_seeds + self._new_seeds

		# compute suggestion
		suggestion = seed_color.suggest_seed_candidates(
			self._current_bgr,
			detections,
			confirmed_seeds,
			self._current_frame,
		)
		self._suggestion = suggestion
		self._detection_cache[self._current_frame] = suggestion

	#============================================

	def _refresh_frame_title(self) -> None:
		"""Update window title with frame, step, zoom, and interval quality info."""
		step_frames = self._scrub_step_frames
		zoom = self._window.get_frame_view().get_zoom_factor()
		title = (
			f"Seed {self._list_idx + 1}/{len(self._seed_frame_indices)} | "
			f"Frame {self._current_frame} | "
			f"Step {step_frames}f | "
			f"Zoom {zoom:.1f}x"
		)
		# append interval quality info from predictions if available
		quality_text = self._get_interval_quality_text()
		if quality_text:
			title += f" | {quality_text}"
		self._window.setWindowTitle(title)

		# Show targeting reasons in the persistent annotation status area.
		reason_text = self._get_targeting_reason_text()
		if reason_text:
			self._show_seed_feedback(reason_text)

	#============================================

	def _get_interval_quality_text(self) -> str:
		"""Build a short quality string from the current frame's interval info.

		Returns:
			String like "HIGH: agree=0.12 velocity=0.08 (FWD/BWD diverge)"
			or empty string if no info is available.
		"""
		if self._predictions is None:
			return ""
		preds = self._predictions.get(self._current_frame)
		if preds is None:
			return ""
		info = preds.get("interval_info")
		if info is None:
			return ""

		# Pre-race intervals (and any future severity-untagged kind) have
		# severity=None; render as "PRE-RACE" rather than crash.
		raw_severity = info["severity"]
		severity = raw_severity.upper() if raw_severity else "PRE-RACE"
		agreement = info["agreement"]
		velocity_consistency = info["velocity_consistency"]
		# start with severity and key scores
		text = (
			f"{severity}: agree={agreement:.2f} "
			f"velocity={velocity_consistency:.2f}"
		)
		# append short failure reasons if present
		reasons = info.get("reasons", [])
		if reasons:
			# use short labels: strip common prefixes for brevity
			short_reasons = [r.replace("likely_", "").replace("_", " ") for r in reasons]
			text += f" ({', '.join(short_reasons)})"
		return text

	#============================================

	def _get_targeting_reason_text(self) -> str:
		"""Build a human-readable targeting reason from interval info.

		Returns:
			String like "Targeted: FWD/BWD diverge, low agreement"
			or empty string if no targeting info available.
		"""
		if self._predictions is None:
			return ""
		preds = self._predictions.get(self._current_frame)
		if preds is None:
			return ""
		info = preds.get("interval_info")
		if info is None:
			return ""
		reasons = info.get("reasons", [])
		if not reasons:
			return ""
		# format reasons into readable text
		readable = [r.replace("_", " ") for r in reasons]
		text = f"Targeted: {', '.join(readable)}"
		return text

	#============================================

	def _recenter_on_prediction(self) -> None:
		"""Recenter the view on the prediction center when zoomed in."""
		frame_view = self._window.get_frame_view()
		zoom = frame_view.get_zoom_factor()
		if zoom <= 1.05:
			return
		center = self._get_prediction_center()
		if center is not None:
			frame_view.set_zoom(zoom, center[0], center[1])

	#============================================

	def handle_key_press(self, key: int, modifiers: object = None) -> bool:
		"""Dispatch a keyboard event through the declarative key map."""
		handled = self._dispatch_keybinding(key, modifiers)
		return handled

	#============================================

	def _key_action_accept_suggestion(self, binding: object, key: int, modifiers: object) -> bool:
		"""Accept the current suggestion when ENTER is requested."""
		_ = binding, key, modifiers
		self._accept_suggestion_if_available()
		return True

	#============================================

	def _key_action_select_candidate(self, binding: object, key: int, modifiers: object) -> bool:
		"""Accept the declared numeric suggestion candidate."""
		_ = binding, modifiers
		candidate_idx = int(key) - int(Qt.Key.Key_1)
		self._accept_candidate(candidate_idx)
		return True

	#============================================

	def _key_action_skip(self, binding: object, key: int, modifiers: object) -> bool:
		"""Skip the current seed frame."""
		_ = binding, key, modifiers
		self._on_skip()
		return True

	#============================================

	def _key_action_scrub_previous_or_pan(self, binding: object, key: int, modifiers: object) -> bool:
		"""Scrub at fit zoom and otherwise leave LEFT to the frame view."""
		_ = binding, key
		if not self._window.get_frame_view().is_fit_zoom():
			return False
		mult = self._step_multiplier(modifiers)
		self._on_prev(mult)
		return True

	#============================================

	def _key_action_scrub_next_or_pan(self, binding: object, key: int, modifiers: object) -> bool:
		"""Scrub at fit zoom and otherwise leave RIGHT to the frame view."""
		_ = binding, key
		if not self._window.get_frame_view().is_fit_zoom():
			return False
		mult = self._step_multiplier(modifiers)
		self._on_next(mult)
		return True

	#============================================

	def _key_action_scrub_previous(self, binding: object, key: int, modifiers: object) -> bool:
		"""Scrub backward for a declared shifted binding."""
		_ = binding, key
		self._on_prev(self._step_multiplier(modifiers))
		return True

	#============================================

	def _key_action_scrub_next(self, binding: object, key: int, modifiers: object) -> bool:
		"""Scrub forward for a declared shifted binding."""
		_ = binding, key
		self._on_next(self._step_multiplier(modifiers))
		return True

	#============================================

	def _key_action_decrease_step(self, binding: object, key: int, modifiers: object) -> bool:
		"""Decrease the scrub step."""
		_ = binding, key, modifiers
		self._decrease_step()
		return True

	#============================================

	def _key_action_increase_step(self, binding: object, key: int, modifiers: object) -> bool:
		"""Increase the scrub step."""
		_ = binding, key, modifiers
		self._increase_step()
		return True

	#============================================

	def _key_action_not_in_frame(self, binding: object, key: int, modifiers: object) -> bool:
		"""Mark the current seed position as not in frame."""
		_ = binding, key, modifiers
		self._on_not_in_frame()
		return True

	#============================================

	def _key_action_fwd_bwd_average(self, binding: object, key: int, modifiers: object) -> bool:
		"""Use the FWD/BWD average as the current seed."""
		_ = binding, key, modifiers
		self._on_fwd_bwd_avg()
		return True

	#============================================

	def _accept_suggestion_if_available(self) -> None:
		"""Accept current suggestion if available.

		Calls _accept_candidate() with the suggestion_index if
		suggestion_index is not None.
		"""
		if self._suggestion is None:
			return
		suggestion_idx = self._suggestion.get("suggestion_index")
		if suggestion_idx is not None:
			self._accept_candidate(suggestion_idx)

	#============================================

	def _accept_candidate(self, candidate_idx: int) -> None:
		"""Accept a candidate from suggestion and create a seed.

		Args:
			candidate_idx: Index into candidates list (0-based).
		"""
		if self._suggestion is None:
			return
		candidates = self._suggestion.get("candidates", [])
		if candidate_idx < 0 or candidate_idx >= len(candidates):
			return

		candidate = candidates[candidate_idx]
		# check for duplicate seed at this frame first
		for seed in self._all_seeds:
			if int(seed["frame_index"]) == self._current_frame:
				self._show_seed_feedback(
					"Seed already exists at this frame"
				)
				return
		for seed in self._new_seeds:
			if int(seed["frame_index"]) == self._current_frame:
				self._show_seed_feedback(
					"Seed already exists at this frame"
				)
				return

		# extract torso_box and build canonical v3 seed
		torso_box = candidate["torso_box"]
		seed = seed_color.build_seed_dict(
			self._current_frame,
			torso_box,
			self._pass_number,
			"visible",
		)
		self._commit_seed(seed)
		self._advance()

	#============================================

	def _on_box_drawn(self, box: list) -> None:
		"""Process a drawn box.

		Args:
			box: Box as [x, y, w, h].
		"""
		# Check for duplicate seed at this frame
		for seed in self._all_seeds:
			if int(seed["frame_index"]) == self._current_frame:
				self._show_seed_feedback(
					"Seed already exists at this frame"
				)
				return
		for seed in self._new_seeds:
			if int(seed["frame_index"]) == self._current_frame:
				self._show_seed_feedback(
					"Seed already exists at this frame"
				)
				return

		status = "visible"
		if self._approx_mode:
			self._approx_mode = False
			self._update_mode_badge()
			status = "approximate"
		elif self._partial_mode:
			self._partial_mode = False
			self._update_mode_badge()
			status = "partial"

		norm_box = seed_color.normalize_seed_box(box, self._config)
		seed = seed_color.build_seed_dict(
			self._current_frame,
			norm_box,
			self._pass_number,
			status,
		)
		self._commit_seed(seed)
		self._advance()

	#============================================

	def _commit_seed(self, seed: dict) -> None:
		"""Save a seed and invoke the save callback.

		Args:
			seed: Seed dict to save.
		"""
		self._new_seeds.append(seed)
		if self._save_callback is not None:
			self._save_callback(self._all_seeds + self._new_seeds)

	#============================================

	def _on_quit(self, exhausted: bool = False) -> None:
		"""Handle quit request.

		Args:
			exhausted: True when called from `_advance` after running off
				the end of the seed-frame list. False (default) when the
				user pressed quit explicitly. The printed message
				distinguishes the two so the log doesn't claim "user
				quit" when the UI ran out of work.
		"""
		self._done = True
		total = len(self._seed_frame_indices)
		if exhausted:
			print(
				f"  finished all {total} target frames; "
				f"closing seeding UI"
			)
		else:
			print(
				f"  user quit at frame {self._current_frame} "
				f"({self._list_idx + 1}/{total})"
			)
		# Keep the summary in both the GUI and the run log. The latter is
		# useful after an unattended/batch annotation pass has closed.
		all_seeds = self._all_seeds + self._new_seeds
		stats_text = self._print_seed_stats(all_seeds)
		self._show_seed_feedback(stats_text)
		if self._return_callback is not None:
			self._preserve_completion_feedback(stats_text)
			# Return to edit mode with collected seeds
			self._return_callback(self._new_seeds)
			return
		if self._window is not None:
			self._window.close()

	#============================================

	def _preserve_completion_feedback(self, text: str) -> None:
		"""Retain add-mode feedback until the resumed controller can display it."""
		if self._window is None or not hasattr(self._window, "get_session"):
			return
		session = self._window.get_session()
		if session is not None and hasattr(session, "set_annotation_feedback"):
			session.set_annotation_feedback(text)

	#============================================

	def _print_seed_stats(self, seeds: list) -> str:
		"""Present and log seed coverage statistics.

		Shows total count, average spacing, and largest gap to help
		the user judge whether coverage is sufficient.

		Args:
			seeds: List of all seed dicts (existing + new).
		"""
		# count only usable seeds (visible or partial)
		usable = [
			s for s in seeds
			if s.get("status") in ("visible", "partial")
		]
		not_in_frame = sum(
			1 for s in seeds if s.get("status") == "not_in_frame"
		)
		approximate = sum(
			1 for s in seeds if s.get("status") == "approximate"
		)
		stats_text = (f"Seed stats: {len(seeds)} total, "
			f"{len(usable)} usable, "
			f"{not_in_frame} not-in-frame, "
			f"{approximate} approximate")
		print(f"  {stats_text.lower()}")
		if len(usable) < 2:
			warning = "Warning: need at least 2 usable seeds to solve"
			print(f"  {warning.lower()}")
			result = f"{stats_text} -- {warning}"
			return result
		# compute gaps between usable seeds sorted by frame index
		sorted_frames = sorted(
			float(s["frame_index"]) for s in usable
		)
		gaps = []
		for i in range(1, len(sorted_frames)):
			gap_s = (sorted_frames[i] - sorted_frames[i - 1]) / self._fps
			gaps.append(gap_s)
		avg_gap = sum(gaps) / len(gaps)
		max_gap = max(gaps)
		spacing_text = (f"average spacing: {avg_gap:.1f}s, "
			f"largest gap: {max_gap:.1f}s")
		print(f"  {spacing_text}")
		result = f"{stats_text} -- {spacing_text}"
		# warn if largest gap is more than double the average
		if max_gap > avg_gap * 2.5:
			warning = (f"Warning: largest gap ({max_gap:.1f}s) is "
				f"much larger than average ({avg_gap:.1f}s) "
				f"-- consider adding seeds in that region")
			print(f"  {warning.lower()}")
			result += f" -- {warning}"
		return result

	#============================================

	def _on_skip(self) -> None:
		"""Skip current frame."""
		self._partial_mode = False
		self._advance()

	#============================================

	def _step_multiplier(self, modifiers: object) -> int:
		"""Compute a temporary step multiplier from held modifier keys.

		Alt multiplies by 5. Shift is NOT used here because it already
		means "force scrub when zoomed".

		Args:
			modifiers: Qt keyboard modifiers.

		Returns:
			Integer multiplier (1 or 5).
		"""
		mult = 1
		if modifiers is not None:
			if bool(modifiers & Qt.KeyboardModifier.AltModifier):
				mult = 5
		return mult

	#============================================

	def _on_prev(self, multiplier: int = 1) -> None:
		"""Scrub backward by the current step size times multiplier.

		Args:
			multiplier: Temporary speed multiplier (default 1).
		"""
		self._current_frame = max(0, self._current_frame - self._scrub_step_frames * multiplier)
		self._refresh_frame()

	#============================================

	def _on_prev_button(self, checked: bool = False) -> None:
		"""Handle toolbar previous button clicks."""
		_ = checked
		self._on_prev(1)

	#============================================

	def _on_next(self, multiplier: int = 1) -> None:
		"""Scrub forward by the current step size times multiplier.

		Args:
			multiplier: Temporary speed multiplier (default 1).
		"""
		# Use last seed frame as upper bound for scrubbing
		max_frame = self._seed_frame_indices[-1] if self._seed_frame_indices else 0
		self._current_frame = min(max_frame, self._current_frame + self._scrub_step_frames * multiplier)
		self._refresh_frame()

	#============================================

	def _on_next_button(self, checked: bool = False) -> None:
		"""Handle toolbar next button clicks."""
		_ = checked
		self._on_next(1)

	#============================================

	def _step_label(self) -> str:
		"""Format the current step size for display.

		Returns:
			String like "2f (0.07s)" showing frames and seconds.
		"""
		step_sec = self._scrub_step_frames / self._fps
		label = f"{self._scrub_step_frames}f ({step_sec:.2f}s)"
		return label

	#============================================

	def _increase_step(self) -> None:
		"""Double the scrub step in frames, ceiling at fps*10."""
		max_frames = int(self._fps * 10)
		self._scrub_step_frames = min(self._scrub_step_frames * 2, max_frames)
		self._update_step_display()

	#============================================

	def _decrease_step(self) -> None:
		"""Halve the scrub step in frames, floor at 1 frame."""
		self._scrub_step_frames = max(self._scrub_step_frames // 2, 1)
		self._update_step_display()

	#============================================

	def _update_step_display(self) -> None:
		"""Update the step label in the toolbar and window title."""
		if self._step_value_label is not None:
			self._step_value_label.setText(self._step_label())
		# refresh window title to show new step size
		if self._window is not None:
			self._refresh_frame_title()

	#============================================

	def _on_not_in_frame(self) -> None:
		"""Mark runner as not in frame."""
		seed = {
			"frame_index": self._current_frame,
			"time_s": round(self._current_frame / self._fps, 3),
			"status": "not_in_frame",
			"conf": None,
			"pass": self._pass_number,
			"source": "human",
			"mode": self._mode_str,
		}
		self._commit_seed(seed)
		self._advance()

	#============================================

	def _on_fwd_bwd_avg(self) -> None:
		"""Auto-accept average of FWD/BWD predictions if overlap sufficient."""
		if self._predictions is None:
			self._show_seed_feedback("No predictions available for F-key")
			return

		preds = self._predictions.get(self._current_frame)
		if preds is None:
			self._show_seed_feedback("No predictions available for F-key")
			return

		fwd = preds.get("forward")
		bwd = preds.get("backward")
		if fwd is None or bwd is None:
			self._show_seed_feedback(
				"Need both FWD and BWD predictions for F-key"
			)
			return

		# Compute FWD and BWD boxes
		fwd = common_tools.coord_space.require_source_box(fwd)
		bwd = common_tools.coord_space.require_source_box(bwd)
		fwd_cx = fwd.cx
		fwd_cy = fwd.cy
		fwd_w = fwd.w
		fwd_h = fwd.h
		bwd_cx = bwd.cx
		bwd_cy = bwd.cy
		bwd_w = bwd.w
		bwd_h = bwd.h

		# Compute intersection area
		f_x1 = fwd_cx - fwd_w / 2.0
		f_y1 = fwd_cy - fwd_h / 2.0
		f_x2 = fwd_cx + fwd_w / 2.0
		f_y2 = fwd_cy + fwd_h / 2.0
		b_x1 = bwd_cx - bwd_w / 2.0
		b_y1 = bwd_cy - bwd_h / 2.0
		b_x2 = bwd_cx + bwd_w / 2.0
		b_y2 = bwd_cy + bwd_h / 2.0
		inter_w = max(0.0, min(f_x2, b_x2) - max(f_x1, b_x1))
		inter_h = max(0.0, min(f_y2, b_y2) - max(f_y1, b_y1))
		intersection = inter_w * inter_h
		fwd_area = fwd_w * fwd_h
		bwd_area = bwd_w * bwd_h
		total = fwd_area + bwd_area

		# Check overlap ratio
		if total <= 0 or intersection / total < 0.1:
			self._show_seed_feedback(
				"FWD/BWD overlap too low to auto-accept"
			)
			return

		# Compute average box
		avg_cx = (fwd_cx + bwd_cx) / 2.0
		avg_cy = (fwd_cy + bwd_cy) / 2.0
		avg_w = (fwd_w + bwd_w) / 2.0
		avg_h = (fwd_h + bwd_h) / 2.0
		avg_x = int(avg_cx - avg_w / 2.0)
		avg_y = int(avg_cy - avg_h / 2.0)

		box = [avg_x, avg_y, int(avg_w), int(avg_h)]
		self._on_box_drawn(box)

	#============================================

	def _advance(self) -> None:
		"""Advance to next seed frame."""
		self._list_idx += 1
		if self._list_idx >= len(self._seed_frame_indices):
			self._on_quit(exhausted=True)
			return
		self._current_frame = self._seed_frame_indices[self._list_idx]
		self._refresh_frame()

	#============================================

	def get_final_seeds(self) -> list:
		"""Get all seeds collected.

		Returns:
			List of all seeds (existing + new).
		"""
		return self._all_seeds + self._new_seeds

	#============================================

	def get_new_seeds(self) -> list:
		"""Get only newly collected seeds.

		Returns:
			List of newly collected seeds.
		"""
		return self._new_seeds
