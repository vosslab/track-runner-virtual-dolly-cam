"""Asynchronous, session-scoped video-frame source for annotation UI."""

# Standard Library
import collections
import threading

# PIP3 modules
from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt

# local repo modules
import common_tools.frame_reader as frame_reader_module
import residual_heat_map


#============================================

class _LatestFrameRequest:
	"""Keep at most one newest decode request between UI and worker threads."""

	def __init__(self) -> None:
		"""Create the synchronized, initially open request mailbox."""
		self._lock = threading.Lock()
		self._pending: tuple[int, int] | None = None
		self._wakeup_queued = False
		self._closed = False

	#============================================

	def submit(self, request_id: int, frame_index: int) -> bool:
		"""Replace a pending request and report whether the worker needs waking."""
		with self._lock:
			if self._closed:
				return False
			self._pending = (request_id, frame_index)
			if self._wakeup_queued:
				return False
			self._wakeup_queued = True
			return True

	#============================================

	def take(self) -> tuple[int, int] | None:
		"""Return and clear the one pending request for the decode worker."""
		with self._lock:
			self._wakeup_queued = False
			if self._closed:
				return None
			request = self._pending
			self._pending = None
			return request

	#============================================

	def take_or_prefetch(self, prefetch_index: int) -> tuple[int | None, int] | None:
		"""Atomically prioritize one pending request over a prefetch candidate."""
		with self._lock:
			if self._closed:
				return None
			if self._pending is not None:
				request = self._pending
				self._pending = None
				self._wakeup_queued = False
				return request
			return (None, prefetch_index)

	#============================================

	def close(self) -> None:
		"""Discard queued work so shutdown does not wait behind stale requests."""
		with self._lock:
			self._closed = True
			self._pending = None


#============================================

class _DecodeWorker(QObject):
	"""Own the reader and every decode operation in one QThread."""

	frame_ready = Signal(int, int, object)
	heat_ready = Signal(int, object)
	stopped = Signal()

	def __init__(
		self, reader_args: dict, cache_bytes: int, request_mailbox: _LatestFrameRequest,
	) -> None:
		"""Store the reader construction data and bounded cache policy."""
		super().__init__()
		self._reader_args = reader_args
		self._cache_bytes = cache_bytes
		self._reader: object | None = None
		self._cache: collections.OrderedDict[int, object] = collections.OrderedDict()
		self._held_bytes = 0
		self._last_index: int | None = None
		self._request_mailbox = request_mailbox

	#============================================

	@Slot()
	def start(self) -> None:
		"""Construct the reader after this worker has entered its QThread."""
		self._reader = frame_reader_module.open_analysis_reader(**self._reader_args)

	#============================================

	@Slot()
	def request(self) -> None:
		"""Decode the newest queued frame and warm the directional next frame."""
		request = self._request_mailbox.take()
		if request is None:
			return
		while request is not None:
			request_id, frame_index = request
			previous_index = self._last_index
			frame = self._get_frame(frame_index)
			self.frame_ready.emit(request_id, frame_index, frame)
			self._last_index = frame_index
			if previous_index is None:
				return
			direction = 1 if frame_index >= previous_index else -1
			next_decode = self._request_mailbox.take_or_prefetch(frame_index + direction)
			if next_decode is None:
				return
			next_request_id, next_frame_index = next_decode
			if next_request_id is None:
				self._get_frame(next_frame_index)
				return
			request = (next_request_id, next_frame_index)

	#============================================

	@Slot()
	def stop(self) -> None:
		"""Release the session-only cache and destroy the owned reader."""
		self._cache.clear()
		self._held_bytes = 0
		if self._reader is not None:
			self._reader.close()
			self._reader = None
		self.stopped.emit()
		QThread.currentThread().quit()

	#============================================

	@Slot(int, object)
	def compute_heat(self, request_id: int, request: dict) -> None:
		"""Compute residual heat using the reader owned by this decode thread."""
		result = residual_heat_map.compute_heat_map_roi(
			self._reader, request["frame_index"], request["scene_transform"],
			request["pred_center"], request["pred_box"],
			fps=self._reader_args["fps"], threshold=request["threshold"],
			fixed_max=request["fixed_max"], blend_alpha=request["blend_alpha"],
		)
		self.heat_ready.emit(request_id, result)

	#============================================

	def _get_frame(self, frame_index: int) -> object:
		"""Return an LRU-cached frame, reading only in the decode thread."""
		if frame_index < 0 or frame_index >= self._reader_args["total_frames"]:
			return None
		if frame_index in self._cache:
			frame = self._cache.pop(frame_index)
			self._cache[frame_index] = frame
			return frame
		frame = self._reader.read_frame(frame_index)
		if frame is not None:
			self._store_frame(frame_index, frame)
		return frame

	#============================================

	def _store_frame(self, frame_index: int, frame: object) -> None:
		"""Add a frame and evict least-recently-used frames by byte count."""
		frame_bytes = int(frame.nbytes)
		if frame_bytes > self._cache_bytes:
			return
		while self._cache and self._held_bytes + frame_bytes > self._cache_bytes:
			_old_index, old_frame = self._cache.popitem(last=False)
			_ = _old_index
			self._held_bytes -= int(old_frame.nbytes)
		self._cache[frame_index] = frame
		self._held_bytes += frame_bytes


#============================================

class FrameSource(QObject):
	"""Request frames without allowing UI code to touch ``FrameReader``."""

	_request = Signal()
	_heat_request = Signal(int, object)
	_stop = Signal()
	frame_ready = Signal(int, object)
	heat_ready = Signal(int, object)

	def __init__(self, reader_args: dict, cache_bytes: int = 128 * 1024 * 1024) -> None:
		"""Start the owned decode thread with explicit reader construction data."""
		super().__init__()
		self._next_request_id = 0
		self._current_request_id = 0
		self._next_heat_request_id = 0
		self._closed = False
		self._request_mailbox = _LatestFrameRequest()
		self._thread = QThread()
		self._worker = _DecodeWorker(reader_args, cache_bytes, self._request_mailbox)
		self._worker.moveToThread(self._thread)
		self._thread.started.connect(self._worker.start)
		self._request.connect(self._worker.request, Qt.ConnectionType.QueuedConnection)
		self._heat_request.connect(self._worker.compute_heat, Qt.ConnectionType.QueuedConnection)
		self._stop.connect(self._worker.stop, Qt.ConnectionType.QueuedConnection)
		self._worker.frame_ready.connect(self._on_frame_ready, Qt.ConnectionType.QueuedConnection)
		self._worker.heat_ready.connect(self._on_heat_ready, Qt.ConnectionType.QueuedConnection)
		self._thread.start()

	#============================================

	@property
	def decode_thread(self) -> QThread:
		"""Return the session-owned worker thread for lifecycle joining."""
		return self._thread

	@property
	def fps(self) -> float:
		"""Return immutable video frame rate without exposing the reader."""
		return self._worker._reader_args["fps"]

	@property
	def frame_count(self) -> int:
		"""Return immutable frame count without exposing the reader."""
		return self._worker._reader_args["total_frames"]

	@property
	def video_path(self) -> str:
		"""Return immutable source path without exposing the reader."""
		return self._worker._reader_args["video_path"]

	#============================================

	def request_frame(self, frame_index: int) -> int:
		"""Queue a frame request and return its monotonically increasing id."""
		if self._closed:
			raise RuntimeError("Cannot request a frame from a closed FrameSource")
		self._next_request_id += 1
		request_id = self._next_request_id
		self._current_request_id = request_id
		if self._request_mailbox.submit(request_id, frame_index):
			self._request.emit()
		return request_id

	#============================================

	def close(self) -> None:
		"""Stop and join the decode thread, releasing its reader and cache."""
		if self._closed:
			return
		self._closed = True
		self._request_mailbox.close()
		if self._thread.isRunning():
			self._stop.emit()
			self._thread.wait()

	#============================================

	def request_heat(
		self, frame_index: int, scene_transform: object, pred_center: tuple,
		pred_box: tuple, style: dict,
	) -> int:
		"""Queue one residual calculation on the reader-owning decode thread."""
		if self._closed:
			raise RuntimeError("Cannot request heat from a closed FrameSource")
		self._next_heat_request_id += 1
		request_id = self._next_heat_request_id
		request = {
			"frame_index": frame_index, "scene_transform": scene_transform,
			"pred_center": pred_center, "pred_box": pred_box,
			"threshold": style["threshold"], "fixed_max": style["fixed_max"],
			"blend_alpha": style["blend_alpha"],
		}
		self._heat_request.emit(request_id, request)
		return request_id

	#============================================

	@Slot(int, int, object)
	def _on_frame_ready(self, request_id: int, frame_index: int, frame: object) -> None:
		"""Forward only the newest result; queued older frames are stale."""
		if request_id != self._current_request_id or self._closed:
			return
		self.frame_ready.emit(frame_index, frame)

	#============================================

	@Slot(int, object)
	def _on_heat_ready(self, request_id: int, result: object) -> None:
		"""Forward worker heat results to the UI without exposing its reader."""
		if not self._closed:
			self.heat_ready.emit(request_id, result)
