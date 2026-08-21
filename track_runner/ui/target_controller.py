"""Target collection controller for track runner annotation.

Manages the Target mode annotation workflow, which is a subclass of
SeedController with different defaults for refinement passes.
"""

import collections.abc

# local repo modules
import ui.frame_source as frame_source_module
import ui.seed_controller as seed_controller_module

#============================================


class TargetController(seed_controller_module.SeedController):
	"""Manages the Target mode annotation workflow.

	Inherits all functionality from SeedController, with different
	default parameters for refinement passes (pass 2+).
	"""

	def __init__(
		self,
		sorted_targets: list,
		reader: frame_source_module.FrameSource,
		fps: float,
		config: dict,
		all_seeds: list,
		save_callback: collections.abc.Callable[[list], None] | None,
		pass_number: int = 2,
		mode_str: str = "suggested_refine",
		predictions: dict | None = None,
		start_frame: int | None = None,
	) -> None:
		"""Initialize the TargetController.

		Args:
			sorted_targets: List of frame indices to collect seeds at.
			reader: FrameSource that asynchronously provides requested frames.
			fps: Frames per second of the video.
			config: Configuration dict.
			all_seeds: List of existing seeds to preserve.
			save_callback: Callable(seeds_list) to save seeds incrementally.
			pass_number: Which collection pass this is (default 2).
			mode_str: Seed collection mode string (default "suggested_refine").
			predictions: Optional dict mapping frame_index to prediction dicts.
			start_frame: Optional frame index to seek to on first activate.
		"""
		super().__init__(
			seed_frame_indices=sorted_targets,
			reader=reader,
			fps=fps,
			config=config,
			all_seeds=all_seeds,
			save_callback=save_callback,
			pass_number=pass_number,
			mode_str=mode_str,
			predictions=predictions,
			start_frame=start_frame,
		)

	#============================================

	def _refresh_frame(self) -> None:
		"""Queue the current target frame without reading on the UI thread."""
		self._reader.request_frame(self._current_frame)

	#============================================

	def _get_mode_name(self) -> str:
		"""Return the target mode label for shared workspace chrome."""
		return "target"

	#============================================

	def _on_frame_ready(self, frame_index: int, frame: object) -> None:
		"""Render only the current frame result from the asynchronous source."""
		if frame_index != self._current_frame:
			return
		super()._on_frame_ready(frame_index, frame)
