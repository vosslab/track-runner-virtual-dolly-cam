"""Reliable frame reader for video tools.

Wraps cv2.VideoCapture with multiple seeking strategies and a sequential
fallback for codecs where random-access seeking is unreliable.

When all strategies fail (common with HEVC in QuickTime MOV containers),
the reader automatically remuxes the video to MKV via mkvmerge and retries.
The temp MKV is cleaned up on close().
"""

# Standard Library
import os
import time
import shutil
import warnings
import subprocess
import tempfile
import dataclasses

# PIP3 modules
import cv2
import numpy

# local repo modules
import common_tools.goodbox


# per-axis safety floor: if the goodbox crop would discard more than
# this fraction of the scaled axis, FrameReader keeps the full
# scaled axis and warns once.
_MAX_CROP_FRACTION = 0.10

# Cache the pid once at module load to avoid repeated os.getpid() calls
_MODULE_PID = os.getpid()

# Module-level accumulator for blob debug instrumentation.
# Enabled by calling enable_blob_debug(); read via get_blob_debug_summary().
# All mutation happens in the worker process where it was enabled.
_BLOB_DEBUG_STATS = {
	"enabled": False,
	# strategy_name -> total call count (successes only)
	"strategy_calls": {},
	# strategy_name -> total elapsed ms (successes only)
	"strategy_total_ms": {},
	# strategy_name -> count of failures (strategy tried but returned False)
	"strategy_failures": {},
	# CRITICAL: count of strategy 4 (sequential walk) firings
	"strategy_4_fired": 0,
	# CRITICAL: count of strategy 5 (remux) firings
	"strategy_5_fired": 0,
	# total ms spent inside strategy 5 remux calls
	"remux_total_ms": 0.0,
}


#============================================
def enable_blob_debug() -> None:
	"""Enable per-strategy blob debug timing in this process.

	Call from _worker_init when debug_blob is True. Safe to call multiple
	times; subsequent calls are no-ops (enabled stays True).
	"""
	global _MODULE_PID
	# refresh pid in case this is called from a child process after fork
	_MODULE_PID = os.getpid()
	_BLOB_DEBUG_STATS["enabled"] = True


#============================================
def disable_blob_debug() -> None:
	"""Disable blob debug timing (used in tests for teardown)."""
	_BLOB_DEBUG_STATS["enabled"] = False
	# reset counters so tests start clean
	_BLOB_DEBUG_STATS["strategy_calls"].clear()
	_BLOB_DEBUG_STATS["strategy_total_ms"].clear()
	_BLOB_DEBUG_STATS["strategy_failures"].clear()
	_BLOB_DEBUG_STATS["strategy_4_fired"] = 0
	_BLOB_DEBUG_STATS["strategy_5_fired"] = 0
	_BLOB_DEBUG_STATS["remux_total_ms"] = 0.0


#============================================
def get_blob_debug_summary() -> dict:
	"""Return a shallow copy of _BLOB_DEBUG_STATS for external consumption.

	Returns:
		Dict with keys: enabled, strategy_calls, strategy_total_ms,
		strategy_failures, strategy_4_fired, strategy_5_fired, remux_total_ms.
	"""
	return {
		"enabled": _BLOB_DEBUG_STATS["enabled"],
		"strategy_calls": dict(_BLOB_DEBUG_STATS["strategy_calls"]),
		"strategy_total_ms": dict(_BLOB_DEBUG_STATS["strategy_total_ms"]),
		"strategy_failures": dict(_BLOB_DEBUG_STATS["strategy_failures"]),
		"strategy_4_fired": _BLOB_DEBUG_STATS["strategy_4_fired"],
		"strategy_5_fired": _BLOB_DEBUG_STATS["strategy_5_fired"],
		"remux_total_ms": _BLOB_DEBUG_STATS["remux_total_ms"],
	}


#============================================
def _accumulate_strategy(name: str, elapsed_ms: float) -> None:
	"""Accumulate a successful strategy call into _BLOB_DEBUG_STATS.

	Only called when _BLOB_DEBUG_STATS["enabled"] is True.

	Args:
		name: Strategy name string (e.g., "seq_fast", "seek_frame").
		elapsed_ms: Elapsed time for this call in milliseconds.
	"""
	calls = _BLOB_DEBUG_STATS["strategy_calls"]
	totals = _BLOB_DEBUG_STATS["strategy_total_ms"]
	calls[name] = calls.get(name, 0) + 1
	totals[name] = totals.get(name, 0.0) + elapsed_ms


#============================================
def _accumulate_failure(name: str) -> None:
	"""Accumulate a failed strategy attempt into _BLOB_DEBUG_STATS.

	Only called when _BLOB_DEBUG_STATS["enabled"] is True.

	Args:
		name: Strategy name that failed.
	"""
	failures = _BLOB_DEBUG_STATS["strategy_failures"]
	failures[name] = failures.get(name, 0) + 1


#============================================
@dataclasses.dataclass(frozen=True)
class FrameGeometry:
	"""Source <-> processed coordinate mapping for a binned reader.

	Origin-preserving: cropping happens only from the right and
	bottom edges of the scaled frame; the top-left origin is fixed
	at (0, 0). Conversion is therefore pure scale by `bin_factor`,
	with no offset term.

	Attributes:
		source_width: original frame width in pixels.
		source_height: original frame height in pixels.
		bin_factor: integer downsample factor; 1 means no bin.
		scaled_width: floor(source_width / bin_factor).
		scaled_height: floor(source_height / bin_factor).
		processed_width: scaled_width snapped down to the largest
			goodbox not exceeding it (or scaled_width when the
			safety floor disables the crop on this axis or when
			bin_factor == 1).
		processed_height: same rule, for height.
	"""

	source_width: int
	source_height: int
	bin_factor: int
	scaled_width: int
	scaled_height: int
	processed_width: int
	processed_height: int

	#============================================
	def source_to_processed(self, x: float, y: float) -> tuple[float, float]:
		"""Convert a source-frame point to processed-frame coords."""
		return (x / self.bin_factor, y / self.bin_factor)

	#============================================
	def processed_to_source(self, x: float, y: float) -> tuple[float, float]:
		"""Convert a processed-frame point to source-frame coords."""
		return (x * self.bin_factor, y * self.bin_factor)

	#============================================
	def source_to_processed_delta(self, dx: float, dy: float) -> tuple[float, float]:
		"""Scale a source-frame delta to processed-frame pixels."""
		return (dx / self.bin_factor, dy / self.bin_factor)

	#============================================
	def processed_to_source_delta(self, dx: float, dy: float) -> tuple[float, float]:
		"""Scale a processed-frame delta to source-frame pixels."""
		return (dx * self.bin_factor, dy * self.bin_factor)


#============================================
def _resolve_frame_geometry(
	source_width: int, source_height: int, bin_factor: int
) -> FrameGeometry:
	"""Resolve a FrameGeometry from raw source dims and a bin factor.

	bin_factor == 1 short-circuits: source == scaled == processed.

	Otherwise: scaled = floor(source / bin_factor), then snap each
	axis to the largest goodbox not exceeding it. If snapping
	would discard more than _MAX_CROP_FRACTION of the scaled axis,
	the snap is skipped on that axis (warned once); the other axis
	can still snap.
	"""
	# bin == 1 short-circuit; no resize, no crop
	if bin_factor == 1:
		return FrameGeometry(
			source_width=source_width,
			source_height=source_height,
			bin_factor=1,
			scaled_width=source_width,
			scaled_height=source_height,
			processed_width=source_width,
			processed_height=source_height,
		)
	# divisibility warnings: a fractional source-pixel column or row
	# is silently dropped by floor division below.
	if source_width % bin_factor != 0:
		warnings.warn(
			f"FrameReader: source_width={source_width} is not"
			f" divisible by bin_factor={bin_factor}; the rightmost"
			f" {source_width % bin_factor} source-pixel column(s)"
			f" will be dropped by INTER_AREA downsample.",
			stacklevel=3,
		)
	if source_height % bin_factor != 0:
		warnings.warn(
			f"FrameReader: source_height={source_height} is not"
			f" divisible by bin_factor={bin_factor}; the bottom"
			f" {source_height % bin_factor} source-pixel row(s)"
			f" will be dropped by INTER_AREA downsample.",
			stacklevel=3,
		)
	scaled_width = source_width // bin_factor
	scaled_height = source_height // bin_factor
	processed_width = _snap_or_keep(scaled_width, "width")
	processed_height = _snap_or_keep(scaled_height, "height")
	return FrameGeometry(
		source_width=source_width,
		source_height=source_height,
		bin_factor=bin_factor,
		scaled_width=scaled_width,
		scaled_height=scaled_height,
		processed_width=processed_width,
		processed_height=processed_height,
	)


#============================================
def _snap_or_keep(scaled_dim: int, axis_name: str) -> int:
	"""Snap a scaled axis to its goodbox, or keep it if the snap
	would discard more than _MAX_CROP_FRACTION of the axis.

	Args:
		scaled_dim: scaled axis size in processed pixels.
		axis_name: "width" or "height" (used in the warning text).

	Returns:
		Processed axis size: either the largest goodbox not
		exceeding scaled_dim, or scaled_dim itself if the safety
		floor disables the snap on this axis.
	"""
	if scaled_dim < 4:
		# nothing useful to snap; goodbox helper would raise
		return scaled_dim
	snapped = common_tools.goodbox.largest_goodbox_at_most(scaled_dim)
	loss = scaled_dim - snapped
	if loss <= 0:
		return snapped
	if loss / scaled_dim > _MAX_CROP_FRACTION:
		warnings.warn(
			f"FrameReader: goodbox {axis_name} crop would discard"
			f" {loss}/{scaled_dim} pixels (> {int(_MAX_CROP_FRACTION * 100)}%);"
			f" keeping full scaled {axis_name}.",
			stacklevel=4,
		)
		return scaled_dim
	return snapped


#============================================
class FrameReader:
	"""Read video frames reliably using multiple seek strategies.

	Tries five strategies in order:
	1. Seek by milliseconds on the existing capture
	2. Seek by frame index on the existing capture
	3. Reopen capture and seek by frame index
	4. Sequential forward read on a dedicated capture (never touched by 1-3)
	5. Remux to MKV via mkvmerge, reopen all captures, retry from strategy 1

	Strategies 1-3 share one cv2.VideoCapture (self._cap) for seek-based access.
	Strategy 4 uses a separate cv2.VideoCapture (self._seq_cap) so that seek
	operations in 1-3 never corrupt the sequential reader's position.

	Strategy 5 fires once: on first all-fail it remuxes the source video to a
	temp MKV file, reopens both captures on the MKV, and retries. This fixes
	HEVC/H.265 in QuickTime MOV containers where OpenCV cannot seek at all.

	Strategy 4 is efficient when candidates are processed in ascending order,
	since total sequential reads across all candidates is at most total_frames.

	Args:
		video_path: Path to the video file.
		fps: Video frame rate (frames per second).
		total_frames: Total number of frames in the video.
		debug: If True, print per-frame strategy results.
	"""

	#============================================
	def __init__(
		self,
		video_path: str,
		fps: float,
		total_frames: int,
		debug: bool = False,
		bin_factor: int = 1,
	):
		"""Initialize FrameReader with a video file.

		Args:
			video_path: Path to the video file.
			fps: Video frame rate.
			total_frames: Total number of frames in the video.
			debug: Enable verbose per-frame debug output.
			bin_factor: optional integer downsample factor (>= 1).
				When > 1, every frame returned by `read_frame` is
				downsampled by `cv2.INTER_AREA` to floor(W/bin)
				x floor(H/bin) and then snapped to the largest
				FFT-friendly goodbox not exceeding each scaled
				dim, cropping only from the right and bottom edges
				(origin-preserving). bin_factor=1 short-circuits
				both the resize and the crop branch and is
				byte-identical to the pre-bin reader.
		"""
		# bin_factor validation
		if not isinstance(bin_factor, int):
			raise TypeError(
				f"bin_factor must be int, got {type(bin_factor).__name__}"
			)
		if bin_factor < 1:
			raise ValueError(
				f"bin_factor must be >= 1, got {bin_factor}"
			)
		self._video_path = video_path
		self._fps = fps
		self._total_frames = total_frames
		self._debug = debug
		self._bin_factor = bin_factor
		# path currently used for captures (changes if remuxed)
		self._active_path = video_path
		# temp MKV path, set if remux occurs
		self._temp_mkv = None
		# whether remux has already been attempted (only try once)
		self._remux_attempted = False
		# seek-based capture used by strategies 1-3
		self._cap = cv2.VideoCapture(video_path)
		if not self._cap.isOpened():
			raise RuntimeError(f"cannot open video: {video_path}")
		# source-frame dimensions read from the capture
		source_width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
		source_height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
		# resolve scaled and processed dims; FrameGeometry is the
		# single source of coordinate-conversion truth and exposes
		# the resolved dims downstream.
		self._geometry = _resolve_frame_geometry(
			source_width, source_height, bin_factor
		)
		# public width/height return processed dims; image consumers
		# downstream do not need to know bin_factor exists, but
		# coordinate-aware callers should use FrameReader.geometry.
		self._width = self._geometry.processed_width
		self._height = self._geometry.processed_height
		# dedicated sequential capture used only by strategy 4
		# lazily opened on first sequential read to avoid wasting resources
		self._seq_cap = None
		# sequential position tracker (-1 means not initialized)
		self._seq_pos = -1
		# Sequential fast-path tracker on the seek capture (`self._cap`).
		# Populated after a successful read; -1 means "not in a known
		# sequential state -- next read_frame must seek." Mirrors the
		# `_next_frame` trick from VideoReader: when the requested
		# index equals this value, we call `cap.read()` directly with
		# no `cap.set(...)` call, which is dramatically faster on
		# codecs/containers where `set(POS_MSEC)` triggers a keyframe
		# re-seek per frame (observed 100x+ penalty on H.264 mp4).
		# Strategies 2-5 invalidate the tracker (they reposition the
		# seek capture or the active source), so the fast-path always
		# self-disarms when a non-sequential access fires.
		self._cap_next_index = -1

	#============================================
	@property
	def video_path(self) -> str:
		"""Path to the input video file (public; matches VideoReader)."""
		return self._video_path

	#============================================
	@property
	def frame_count(self) -> int:
		"""Total number of frames in the video (public; matches VideoReader)."""
		return self._total_frames

	#============================================
	@property
	def fps(self) -> float:
		"""Video frame rate (public; matches VideoReader)."""
		return self._fps

	#============================================
	@property
	def width(self) -> int:
		"""Frame width in pixels (public; matches VideoReader)."""
		return self._width

	#============================================
	@property
	def height(self) -> int:
		"""Frame height in pixels (public; matches VideoReader)."""
		return self._height

	#============================================
	@property
	def geometry(self) -> "FrameGeometry":
		"""Source <-> processed coordinate mapper for this reader."""
		return self._geometry

	#============================================
	@property
	def bin_factor(self) -> int:
		"""Integer bin factor; 1 means no bin (byte-identical reads)."""
		return self._bin_factor

	#============================================
	def _apply_bin(self, frame: numpy.ndarray | None) -> numpy.ndarray | None:
		"""Apply bin + origin-preserving goodbox crop to a raw frame.

		Returns frame unchanged when bin_factor == 1.
		"""
		if frame is None:
			return None
		if self._bin_factor == 1:
			return frame
		geom = self._geometry
		# downsample with INTER_AREA (best for shrink); use
		# (scaled_width, scaled_height) as the (cols, rows) target
		scaled = cv2.resize(
			frame,
			(geom.scaled_width, geom.scaled_height),
			interpolation=cv2.INTER_AREA,
		)
		# origin-preserving right/bottom crop to processed dims
		if (
			scaled.shape[0] != geom.processed_height
			or scaled.shape[1] != geom.processed_width
		):
			scaled = scaled[
				0 : geom.processed_height, 0 : geom.processed_width
			]
		return scaled

	#============================================
	def read_frame(self, frame_index: int) -> numpy.ndarray | None:
		"""Read a single frame by index, trying multiple strategies.

		Strategy 0 (sequential fast-path): when `frame_index ==
		self._cap_next_index`, call `cap.read()` directly with no
		`cap.set(...)` call. On many codecs/containers `set(POS_MSEC)`
		causes a keyframe re-seek per frame; the fast-path is the
		main reason VideoReader's sequential reads were so much
		faster than FrameReader's. Strategies 1-5 are unchanged and
		still fire for non-sequential access; any of them that
		repositions or re-opens the seek capture invalidates the
		tracker so the fast-path self-disarms when a scattered
		access fires.

		Args:
			frame_index: Target frame index (0-based).

		Returns:
			BGR frame as numpy array, or None if all strategies fail.
		"""
		results = {}
		# check debug flag once so all branches pay only one attribute lookup
		debug_on = _BLOB_DEBUG_STATS["enabled"]

		# strategy 0: sequential fast-path. No `cap.set(...)`; just
		# `cap.read()`. The seek capture's position is known
		# (`self._cap_next_index`) only when the previous read
		# succeeded via strategy 0 or strategy 1. Anything that
		# reseats the capture (strategies 2/3, the remux path,
		# `__init__` initialization to -1) invalidates the tracker.
		if (
			self._cap_next_index >= 0
			and frame_index == self._cap_next_index
		):
			if debug_on:
				t0 = time.perf_counter()
			ret, frame = self._cap.read()
			if ret:
				if debug_on:
					elapsed_ms = (time.perf_counter() - t0) * 1000.0
					_accumulate_strategy("seq_fast", elapsed_ms)
					print(
						f"[blob][pid={_MODULE_PID}] read_frame fi={frame_index}"
						f" strategy=seq_fast elapsed={elapsed_ms:.1f}ms",
						flush=True,
					)
				results["seq_fast"] = "OK"
				# advance tracker for the next call
				self._cap_next_index = frame_index + 1
				self._print_debug(frame_index, results)
				return self._apply_bin(frame)
			# fast-path failed: tracker was wrong (EOF or hiccup);
			# fall through to the existing seek strategies.
			self._cap_next_index = -1
			results["seq_fast"] = "FAIL"

		# strategy 1: seek by frame index. POS_FRAMES is faster than
		# POS_MSEC on H.264 mp4/MOV scattered reads (POS_MSEC tends to
		# force a keyframe re-seek per call). For sequential reads the
		# strategy 0 fast-path above already skips this entirely.
		if debug_on:
			t0 = time.perf_counter()
		self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
		ret, frame = self._cap.read()
		if ret:
			if debug_on:
				elapsed_ms = (time.perf_counter() - t0) * 1000.0
				_accumulate_strategy("seek_frame", elapsed_ms)
				print(
					f"[blob][pid={_MODULE_PID}] read_frame fi={frame_index}"
					f" strategy=seek_frame elapsed={elapsed_ms:.1f}ms",
					flush=True,
				)
			results["seek_frame"] = "OK"
			# strategy 1 succeeded: arm the fast-path for the
			# next consecutive index.
			self._cap_next_index = frame_index + 1
			self._print_debug(frame_index, results)
			return self._apply_bin(frame)
		results["seek_frame"] = "FAIL"
		# any failed strategy below repositions or re-opens the
		# capture; invalidate the fast-path tracker.
		self._cap_next_index = -1
		if debug_on:
			_accumulate_failure("seek_frame")
			print(
				f"[blob][pid={_MODULE_PID}] read_frame fi={frame_index}"
				f" STRATEGY_FAIL strategy=seek_frame retry=seek_msec",
				flush=True,
			)

		# strategy 2: seek by milliseconds (fallback for codecs where
		# POS_FRAMES does not honor random access).
		if debug_on:
			t0 = time.perf_counter()
		time_msec = (frame_index / self._fps) * 1000.0
		self._cap.set(cv2.CAP_PROP_POS_MSEC, time_msec)
		ret, frame = self._cap.read()
		if ret:
			if debug_on:
				elapsed_ms = (time.perf_counter() - t0) * 1000.0
				_accumulate_strategy("seek_msec", elapsed_ms)
				print(
					f"[blob][pid={_MODULE_PID}] read_frame fi={frame_index}"
					f" strategy=seek_msec elapsed={elapsed_ms:.1f}ms",
					flush=True,
				)
			results["seek_msec"] = "OK"
			self._print_debug(frame_index, results)
			return self._apply_bin(frame)
		results["seek_msec"] = "FAIL"
		if debug_on:
			_accumulate_failure("seek_msec")
			print(
				f"[blob][pid={_MODULE_PID}] read_frame fi={frame_index}"
				f" STRATEGY_FAIL strategy=seek_msec retry=reopen",
				flush=True,
			)

		# strategy 3: reopen seek capture and seek by frame index
		if debug_on:
			t0 = time.perf_counter()
		self._cap.release()
		self._cap = cv2.VideoCapture(self._active_path)
		if self._cap.isOpened():
			self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
			ret, frame = self._cap.read()
			if ret:
				if debug_on:
					elapsed_ms = (time.perf_counter() - t0) * 1000.0
					_accumulate_strategy("reopen", elapsed_ms)
					print(
						f"[blob][pid={_MODULE_PID}] read_frame fi={frame_index}"
						f" strategy=reopen elapsed={elapsed_ms:.1f}ms",
						flush=True,
					)
				results["reopen"] = "OK"
				self._print_debug(frame_index, results)
				return self._apply_bin(frame)
		results["reopen"] = "FAIL"
		if debug_on:
			_accumulate_failure("reopen")
			print(
				f"[blob][pid={_MODULE_PID}] read_frame fi={frame_index}"
				f" STRATEGY_FAIL strategy=reopen retry=sequential",
				flush=True,
			)

		# strategy 4: sequential forward read on dedicated capture
		last_seq_pos = self._seq_pos
		if debug_on:
			t0 = time.perf_counter()
		frame = self._sequential_read(frame_index)
		if frame is not None:
			if debug_on:
				elapsed_ms = (time.perf_counter() - t0) * 1000.0
				_BLOB_DEBUG_STATS["strategy_4_fired"] += 1
				_accumulate_strategy("sequential", elapsed_ms)
				print(
					f"[blob][pid={_MODULE_PID}] read_frame fi={frame_index}"
					f" CRITICAL strategy=sequential"
					f" walked from {last_seq_pos} to {frame_index}",
					flush=True,
				)
			results["sequential"] = "OK"
			self._print_debug(frame_index, results)
			return self._apply_bin(frame)
		results["sequential"] = "FAIL"
		if debug_on:
			_accumulate_failure("sequential")

		# strategy 5: remux to MKV and retry (one-shot, never retried)
		if not self._remux_attempted:
			if debug_on:
				t0_remux = time.perf_counter()
			remuxed = self._remux_to_mkv()
			if remuxed:
				if debug_on:
					remux_ms = (time.perf_counter() - t0_remux) * 1000.0
					_BLOB_DEBUG_STATS["strategy_5_fired"] += 1
					_BLOB_DEBUG_STATS["remux_total_ms"] += remux_ms
					print(
						f"[blob][pid={_MODULE_PID}] read_frame fi={frame_index}"
						f" CRITICAL strategy=remux"
						f" elapsed={remux_ms / 1000.0:.1f}s (one-shot)",
						flush=True,
					)
				results["remux"] = "OK"
				# retry strategies 1-4 on the remuxed file
				frame = self._retry_after_remux(frame_index, results)
				if frame is not None:
					self._print_debug(frame_index, results)
					return self._apply_bin(frame)
			else:
				results["remux"] = "FAIL"

		self._print_debug(frame_index, results)
		return None

	#============================================
	def _remux_to_mkv(self) -> bool:
		"""Remux the source video to a temporary MKV file via mkvmerge.

		HEVC/H.265 in QuickTime MOV containers causes OpenCV seek failures.
		Remuxing to MKV (lossless, just repackaging) fixes the seeking.

		Returns:
			True if remux succeeded and captures were reopened.
		"""
		self._remux_attempted = True
		# check if mkvmerge is available
		mkvmerge_path = shutil.which("mkvmerge")
		if mkvmerge_path is None:
			if self._debug:
				print("  remux: mkvmerge not found, skipping")
			return False
		# create temp MKV file next to the original video
		video_dir = os.path.dirname(self._video_path) or "."
		video_base = os.path.basename(self._video_path)
		stem = os.path.splitext(video_base)[0]
		# use tempfile to get a unique name, but keep it in the same directory
		fd, temp_path = tempfile.mkstemp(suffix=".mkv", prefix=f".{stem}_remux_", dir=video_dir)
		os.close(fd)
		print(f"  remuxing to MKV for reliable seeking: {video_base}")
		# run mkvmerge to remux (lossless, just repackages the streams)
		cmd = [mkvmerge_path, self._video_path, "-o", temp_path]
		result = subprocess.run(cmd, capture_output=True, text=True)
		if result.returncode not in (0, 1):
			# mkvmerge returns 1 for warnings, 2 for errors
			if self._debug:
				print(f"  remux failed: {result.stderr.strip()}")
			# clean up partial temp file
			if os.path.isfile(temp_path):
				os.remove(temp_path)
			return False
		self._temp_mkv = temp_path
		self._active_path = temp_path
		# release old captures and reopen on the MKV
		self._cap.release()
		self._cap = cv2.VideoCapture(temp_path)
		if self._seq_cap is not None:
			self._seq_cap.release()
			self._seq_cap = None
		self._seq_pos = -1
		if not self._cap.isOpened():
			if self._debug:
				print("  remux: cannot open remuxed MKV")
			return False
		print("  remux complete, reopened on MKV")
		return True

	#============================================
	def _retry_after_remux(self, frame_index: int, results: dict) -> numpy.ndarray | None:
		"""Retry strategies 1-4 after successful remux.

		Args:
			frame_index: Target frame index.
			results: Strategy results dict to update.

		Returns:
			BGR frame as numpy array, or None if still failing.
		"""
		# retry strategy 1: seek by msec on remuxed file
		time_msec = (frame_index / self._fps) * 1000.0
		self._cap.set(cv2.CAP_PROP_POS_MSEC, time_msec)
		ret, frame = self._cap.read()
		if ret:
			results["retry_seek_msec"] = "OK"
			return frame
		results["retry_seek_msec"] = "FAIL"
		# retry strategy 2: seek by frame index on remuxed file
		self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
		ret, frame = self._cap.read()
		if ret:
			results["retry_seek_frame"] = "OK"
			return frame
		results["retry_seek_frame"] = "FAIL"
		# retry strategy 4: sequential on remuxed file
		frame = self._sequential_read(frame_index)
		if frame is not None:
			results["retry_sequential"] = "OK"
			return frame
		results["retry_sequential"] = "FAIL"
		return None

	#============================================
	def _sequential_read(self, frame_index: int) -> numpy.ndarray | None:
		"""Read forward sequentially to the target frame.

		Uses a dedicated capture (self._seq_cap) that is never touched
		by seek-based strategies, so its position is always reliable.

		If target is behind the current sequential position, reopens
		the capture from the beginning.

		Args:
			frame_index: Target frame index.

		Returns:
			BGR frame as numpy array, or None on failure.
		"""
		# if target is behind current position or cap not initialized, reopen
		if self._seq_cap is None or frame_index <= self._seq_pos or self._seq_pos < 0:
			if self._seq_cap is not None:
				self._seq_cap.release()
			self._seq_cap = cv2.VideoCapture(self._active_path)
			if not self._seq_cap.isOpened():
				return None
			self._seq_pos = -1

		# read forward, discarding frames until we reach target
		frame = None
		while self._seq_pos < frame_index:
			ret, frame = self._seq_cap.read()
			if not ret:
				return None
			self._seq_pos += 1

		# self._seq_pos should now equal frame_index
		return frame

	#============================================
	def _print_debug(self, frame_index: int, results: dict) -> None:
		"""Print per-frame debug output showing strategy results.

		Args:
			frame_index: Frame index that was read.
			results: Dict mapping strategy name to "OK" or "FAIL".
		"""
		if not self._debug:
			return
		# build a compact status string
		parts = []
		strategy_order = (
			"seek_msec", "seek_frame", "reopen", "sequential",
			"remux", "retry_seek_msec", "retry_seek_frame", "retry_sequential",
		)
		for strategy in strategy_order:
			if strategy in results:
				parts.append(f"{strategy}={results[strategy]}")
		status_str = " ".join(parts)
		print(f"  frame {frame_index}: {status_str}")

	#============================================
	def close(self) -> None:
		"""Release video captures and clean up temp MKV if created."""
		if self._cap is not None:
			self._cap.release()
			self._cap = None
		if self._seq_cap is not None:
			self._seq_cap.release()
			self._seq_cap = None
		# remove temporary remuxed MKV
		if self._temp_mkv is not None and os.path.isfile(self._temp_mkv):
			os.remove(self._temp_mkv)
			if self._debug:
				print(f"  cleaned up temp MKV: {self._temp_mkv}")
			self._temp_mkv = None
