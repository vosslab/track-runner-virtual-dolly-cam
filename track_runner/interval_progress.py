"""Shared Rich progress columns for interval solving."""

# Standard Library
import math
import shutil
import time

# PIP3 modules
import rich.console
import rich.measure
import rich.progress
import rich.text


#============================================
class BlockBarColumn(rich.progress.ProgressColumn):
	"""Progress bar column using ASCII progress bar characters."""
	FILLED = "#"
	EMPTY = "-"
	_OTHER_COLUMNS_WIDTH = 60

	def render(self, task: rich.progress.Task) -> rich.text.Text:
		"""Render the progress bar using the available terminal width."""
		term_width = shutil.get_terminal_size((80, 24)).columns
		width = max(20, term_width - self._OTHER_COLUMNS_WIDTH)
		if task.total is None or task.total == 0:
			bar = self.EMPTY * width
			return rich.text.Text(bar)
		fraction = min(1.0, task.completed / task.total)
		filled = int(width * fraction)
		bar = self.FILLED * filled + self.EMPTY * (width - filled)
		style = "bright_green" if fraction >= 1.0 else "green"
		return rich.text.Text(bar, style=style)

	def __rich_measure__(
		self,
		console: rich.console.Console,
		options: rich.console.ConsoleOptions,
	) -> rich.measure.Measurement:
		"""Claim all available width so the bar expands to fill the terminal."""
		return rich.measure.Measurement(4, options.max_width)


#============================================
class FrameETAColumn(rich.progress.ProgressColumn):
	"""ETA column based on a shared frame counter and total frame count."""

	def __init__(self, frame_counter: object, total_frames: int) -> None:
		super().__init__()
		self.frame_counter = frame_counter
		self.total_frames = total_frames
		self.start_time = time.time()
		self._last_text = "ETA --:--  elapsed --:--"
		self._last_update = 0.0

	def _get_done(self) -> int:
		"""Read the current frame count from a shared counter."""
		if hasattr(self.frame_counter, "get_lock"):
			with self.frame_counter.get_lock():
				return self.frame_counter.value
		return self.frame_counter[0]

	@staticmethod
	def _format_duration(seconds: float) -> str:
		"""Format seconds into M:SS or H:MM:SS."""
		total_s = int(seconds)
		if total_s < 3600:
			minutes = total_s // 60
			seconds_remaining = total_s % 60
			return f"{minutes}:{seconds_remaining:02d}"
		hours = total_s // 3600
		minutes = (total_s % 3600) // 60
		seconds_remaining = total_s % 60
		return f"{hours}:{minutes:02d}:{seconds_remaining:02d}"

	def render(self, task: rich.progress.Task) -> rich.text.Text:
		"""Render ETA and elapsed time based on frame throughput."""
		now = time.time()
		elapsed = now - self.start_time
		if now - self._last_update < 2.0 and self._last_update > 0.0:
			return rich.text.Text(self._last_text)
		self._last_update = now
		elapsed_str = self._format_duration(elapsed)
		if elapsed < 1.0:
			self._last_text = f"ETA --:--  elapsed {elapsed_str}"
			return rich.text.Text(self._last_text)
		done = self._get_done()
		if done < 1:
			self._last_text = f"ETA --:--  elapsed {elapsed_str}"
			return rich.text.Text(self._last_text)
		fps_rate = done / elapsed
		remaining = self.total_frames - done
		eta_s = math.ceil(max(0, remaining / fps_rate))
		eta_str = self._format_duration(eta_s)
		self._last_text = f"ETA {eta_str}  elapsed {elapsed_str}"
		return rich.text.Text(self._last_text)


#============================================
class TaskETAColumn(rich.progress.ProgressColumn):
	"""ETA and elapsed column driven by task.completed and task.total."""

	def __init__(self) -> None:
		super().__init__()
		self.start_time = time.time()
		self._last_text = "ETA --:--  elapsed --:--"
		self._last_update = 0.0

	def render(self, task: rich.progress.Task) -> rich.text.Text:
		"""Render ETA and elapsed time based on completed task count."""
		now = time.time()
		elapsed = now - self.start_time
		if now - self._last_update < 2.0 and self._last_update > 0.0:
			return rich.text.Text(self._last_text)
		self._last_update = now
		elapsed_str = FrameETAColumn._format_duration(elapsed)
		done = int(task.completed)
		total = int(task.total) if task.total else 0
		if elapsed < 1.0 or done < 1 or total < 1:
			self._last_text = f"ETA --:--  elapsed {elapsed_str}"
			return rich.text.Text(self._last_text)
		rate = done / elapsed
		remaining = total - done
		eta_s = math.ceil(max(0, remaining / rate))
		eta_str = FrameETAColumn._format_duration(eta_s)
		self._last_text = f"ETA {eta_str}  elapsed {elapsed_str}"
		return rich.text.Text(self._last_text)


#============================================
def make_solve_progress(
	eta_column: rich.progress.ProgressColumn | None = None,
) -> rich.progress.Progress:
	"""Build the canonical solve progress bar."""
	if eta_column is None:
		eta_column = TaskETAColumn()
	progress = rich.progress.Progress(
		rich.progress.TextColumn("{task.description}"),
		BlockBarColumn(),
		rich.progress.MofNCompleteColumn(),
		rich.progress.TaskProgressColumn(),
		eta_column,
		refresh_per_second=2,
	)
	return progress
