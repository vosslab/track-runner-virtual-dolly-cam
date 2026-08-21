"""Persistent ownership for an interactive annotation window.

An annotation session owns the shared video and annotation state while the
window moves between Seed, Target, and Edit controllers.  Controllers are
short-lived views of that state; committing a seed remains responsible for
calling the existing save callback.
"""

# Standard Library
import collections.abc

# local repo modules
import ui.seed_controller as seed_controller_module


#============================================

class AnnotationSession:
	"""Own annotation state and controller lifetime for one UI process."""

	def __init__(
		self,
		video_context: dict,
		reader: object,
		seed_store: list,
		prediction_store: dict | None,
		controller_factories: dict[str, collections.abc.Callable[[], object]],
		decode_thread: object | None = None,
	) -> None:
		"""Initialize shared state and the mode-controller factories.

		Args:
			video_context: Immutable-per-session video metadata and UI config.
			reader: FrameSource for production UI paths. Tests may supply a
				reader double that follows the same request/close protocol.
			seed_store: Mutable, process-lifetime human seed list.
			prediction_store: Process-lifetime prediction mapping, if available.
			controller_factories: One factory for each reachable UI mode.
			decode_thread: Optional explicit worker thread for callers whose
				reader does not expose decode_thread.
		"""
		self.video_context = video_context
		self.reader = reader
		self.seed_store = seed_store
		self.prediction_store = prediction_store
		self._controller_factories = controller_factories
		if decode_thread is None and hasattr(reader, "decode_thread"):
			decode_thread = reader.decode_thread
		self._decode_thread = decode_thread
		self._window: object | None = None
		self._active_controller: object | None = None
		self._last_controller: object | None = None
		self._controller_cache: dict[str, object] = {}
		self._active_mode: str | None = None
		self._pending_feedback: str | None = None
		self._closed = False

	#============================================

	@property
	def active_controller(self) -> object | None:
		"""Return the controller currently installed in the window."""
		return self._active_controller

	#============================================

	@property
	def active_mode(self) -> str | None:
		"""Return the active mode name, or None before activation."""
		return self._active_mode

	#============================================

	@property
	def last_controller(self) -> object | None:
		"""Return the most recently active controller after teardown."""
		return self._last_controller

	#============================================

	def attach_window(self, window: object) -> None:
		"""Attach the one annotation window managed by this session."""
		if self._window is not None:
			raise RuntimeError("AnnotationSession already has a window")
		self._window = window

	#============================================

	def activate_mode(self, mode: str) -> None:
		"""Replace the controller with the selected mode's controller."""
		if self._closed:
			raise RuntimeError("Cannot activate a closed AnnotationSession")
		if self._window is None:
			raise RuntimeError("Attach an AnnotationWindow before activating a mode")
		if mode not in self._controller_factories:
			raise ValueError(f"Unknown annotation mode: {mode}")
		if mode == self._active_mode:
			return

		self._sync_seed_store()
		if mode == "edit" and mode in self._controller_cache:
			controller = self._controller_cache[mode]
		else:
			controller = self._controller_factories[mode]()
		self._activate_controller(mode, controller)

	#============================================

	def begin_add_seed(self, edit_controller: object) -> None:
		"""Enter Seed from Edit while retaining the edit transaction."""
		if edit_controller is not self._active_controller:
			raise RuntimeError("Only the active EditController can add seeds")
		controller = edit_controller.create_add_seed_controller(
			total_frames=self.video_context["total_frames"],
			seed_store=self.seed_store,
			prediction_store=self.prediction_store,
		)
		self._activate_controller("seed", controller)

	#============================================

	def set_annotation_feedback(self, text: str) -> None:
		"""Carry a completed mode's user feedback across a controller swap.

		Args:
			text: Visible annotation result to present after the next activation.
		"""
		self._pending_feedback = text

	#============================================

	def resume_edit_after_add(
		self,
		edit_controller: object,
		work_seeds: list,
	) -> None:
		"""Restore Edit after its controller completes an add-seed transaction."""
		self.seed_store[:] = work_seeds
		self._activate_controller("edit", edit_controller)

	#============================================

	def close(self) -> None:
		"""Tear down the active controller, decode worker, and reader once."""
		if self._closed:
			return
		self._sync_seed_store()
		self._last_controller = self._active_controller
		if self._window is not None:
			self._window.set_controller(None)
		self._active_controller = None
		self._active_mode = None
		if hasattr(self.reader, "close"):
			self.reader.close()
		elif self._decode_thread is not None and self._decode_thread.isRunning():
			self._decode_thread.quit()
			self._decode_thread.wait()
		self._closed = True

	#============================================

	def _activate_controller(self, mode: str, controller: object) -> None:
		"""Install a controller and retain Edit's pending transaction state."""
		self._window.set_session_mode(mode)
		self._window.set_controller(controller)
		self._last_controller = self._active_controller
		self._active_controller = controller
		self._active_mode = mode
		if mode == "edit":
			self._controller_cache["edit"] = controller
		if self._pending_feedback is not None:
			if hasattr(controller, "show_session_feedback"):
				controller.show_session_feedback(self._pending_feedback)
				self._pending_feedback = None

	#============================================

	def _sync_seed_store(self) -> None:
		"""Copy a departing seed controller's committed view into the store."""
		if self._active_controller is None:
			return
		if not isinstance(self._active_controller, seed_controller_module.SeedController):
			return
		self.seed_store[:] = self._active_controller.get_final_seeds()
