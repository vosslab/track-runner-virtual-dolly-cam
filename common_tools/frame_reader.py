"""Reliable frame reader for video via cv2.VideoCapture.

FrameReader wraps cv2.VideoCapture for video decode and provides a public API
for frame reading: read_frame(index), frame_count, fps, width, height, geometry,
bin_factor. Supports bin_factor downsample and goodbox snap (origin-preserving
crop to FFT-friendly dimensions) on every frame, regardless of bin_factor value.

Two seek strategies fire in read_frame:
  - Strategy 0 (sequential fast-path): when frame_index == self._cap_next_index,
    no cap.set is called; cap.read() advances the decoder cursor.
  - Strategy 1 (random access): cap.set(CAP_PROP_POS_FRAMES, idx) followed by
    cap.read(). FFmpeg seeks to the nearest preceding keyframe and decodes
    forward to the target frame.

No further fallbacks are kept. Source videos must be .mkv; MP4/MOV users
should remux losslessly via `mkvmerge -o out.mkv in.mov` before use.
"""

# Standard Library
import os
import warnings
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
			safety floor disables the crop on this axis). The snap
			applies at any bin_factor including 1; only the safety
			floor (when the crop would discard >10% of the axis)
			can disable it.
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

	scaled = floor(source / bin_factor), then snap each axis down to
	the largest goodbox not exceeding it. The snap applies at any
	bin_factor (including 1) so Stage 1 phase correlate and Stage 4
	residual array shapes are always FFT-friendly. If snapping would
	discard more than _MAX_CROP_FRACTION of the scaled axis, the
	snap is skipped on that axis (warned once); the other axis can
	still snap.
	"""
	# Non-divisible source dims silently drop at most (bin_factor - 1)
	# right/bottom pixels, identical in spirit to the goodbox snap below
	# (origin-preserving right/bottom crop). The loss is bounded by
	# bin_factor-1 over thousands of pixels and is always far under the
	# goodbox _MAX_CROP_FRACTION safety floor, so no warning is emitted;
	# _snap_or_keep still warns if its own snap would exceed the floor.
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
	"""Read video frames via cv2.VideoCapture.

	Decodes frames using cv2.VideoCapture with a sequential fast-path for
	consecutive reads (no cap.set when frame_index == cap_next_index) and
	a single random-access seek strategy (cap.set(CAP_PROP_POS_FRAMES, idx))
	for non-sequential reads. No further fallback strategies are kept; source
	videos must be .mkv.

	Args:
		video_path: Path to the video file. Must end in .mkv.
		fps: Video frame rate (frames per second).
		total_frames: Total number of frames in the video.
		debug: If True, print per-frame debug output.
		bin_factor: Integer downsample factor (>= 1). When > 1, every frame
			is downsampled via cv2.INTER_AREA to floor(W/bin) x floor(H/bin).
			At any bin_factor the goodbox snap applies (largest FFT-friendly box
			not exceeding scaled dims, cropping only from right/bottom edges).
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
			video_path: Path to the video file. Must end in .mkv.
			fps: Video frame rate.
			total_frames: Total number of frames in the video.
			debug: Enable verbose per-frame debug output.
			bin_factor: optional integer downsample factor (>= 1).
				When > 1, every frame returned by `read_frame` is
				downsampled by `cv2.INTER_AREA` to floor(W/bin)
				x floor(H/bin). The goodbox snap (largest
				FFT-friendly box not exceeding each scaled dim,
				cropping only from the right and bottom edges --
				origin-preserving) applies at any bin_factor,
				including 1, so downstream FFT consumers always see
				FFT-friendly dims. At bin_factor=1 with a
				goodbox-sized source the snap is a no-op; with a
				non-goodbox source (e.g. 1080-row HD or 2160-row
				4K) a small bottom-edge sliver is cropped (1080 ->
				1056, 2160 -> 2112, 24-48 px depending on source).
		"""
		# .mkv-only restriction: the rollback from PyAV dropped the
		# 5-strategy seek waterfall and mkvmerge remux fallback that
		# made HEVC-in-MOV decode work. MP4/MOV users must remux to
		# MKV losslessly before use.
		if os.path.splitext(video_path)[1].lower() != ".mkv":
			raise ValueError(
				f"FrameReader requires a .mkv source, got {video_path!r}."
				f" Remux losslessly with: mkvmerge -o out.mkv in.mov"
			)
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

		# Initialize cv2 backend
		self._init_cv2_backend(video_path)

		# resolve scaled and processed dims; FrameGeometry is the
		# single source of coordinate-conversion truth and exposes
		# the resolved dims downstream. If geometry resolution
		# raises, release the capture so we do not leak the FD.
		try:
			self._geometry = _resolve_frame_geometry(
				self._source_width, self._source_height, bin_factor
			)
		except Exception:
			self._cap.release()
			self._cap = None
			raise
		# public width/height return processed dims; image consumers
		# downstream do not need to know bin_factor exists, but
		# coordinate-aware callers should use FrameReader.geometry.
		self._width = self._geometry.processed_width
		self._height = self._geometry.processed_height

	#============================================
	def _init_cv2_backend(self, video_path: str) -> None:
		"""Initialize cv2.VideoCapture backend."""
		self._cap = cv2.VideoCapture(video_path)
		if not self._cap.isOpened():
			raise RuntimeError(
				f"cv2.VideoCapture failed to open {video_path!r}"
			)
		self._source_width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
		self._source_height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
		# strategy-0 cursor: index of the frame the next cap.read() will
		# return. -1 means cursor is invalid (force a cap.set on next read).
		self._cap_next_index = 0

	#============================================
	@property
	def video_path(self) -> str:
		"""Path to the input video file."""
		return self._video_path

	#============================================
	@property
	def frame_count(self) -> int:
		"""Total number of frames in the video."""
		return self._total_frames

	#============================================
	@property
	def fps(self) -> float:
		"""Video frame rate."""
		return self._fps

	#============================================
	@property
	def width(self) -> int:
		"""Frame width in pixels (processed dims)."""
		return self._width

	#============================================
	@property
	def height(self) -> int:
		"""Frame height in pixels (processed dims)."""
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

		The goodbox crop applies at any bin_factor, including 1, so
		downstream FFT consumers (Stage 1 phase correlate, Stage 4
		residual algorithms) always see FFT-friendly dimensions. At
		bin_factor == 1 with a goodbox-sized source, the crop is a
		no-op and the frame is returned unchanged.
		"""
		if frame is None:
			return None
		geom = self._geometry
		# bin via cv2.INTER_AREA when requested
		if self._bin_factor > 1:
			frame = cv2.resize(
				frame,
				(geom.scaled_width, geom.scaled_height),
				interpolation=cv2.INTER_AREA,
			)
		# origin-preserving right/bottom crop to goodbox-snapped dims.
		# numpy.ascontiguousarray forces a copy so the returned frame
		# does not pin the full-resolution decoded buffer via its base
		# reference -- significant in the Stage 4 rolling buffer where
		# up to 40 frames are held simultaneously per worker.
		if (
			frame.shape[0] != geom.processed_height
			or frame.shape[1] != geom.processed_width
		):
			frame = numpy.ascontiguousarray(
				frame[0 : geom.processed_height, 0 : geom.processed_width]
			)
		return frame

	#============================================
	def seek_for_encode(self, start_frame: int) -> None:
		"""Seek to start_frame and arm the sequential fast-path.

		The next read_frame(start_frame) hits strategy 0 with no
		additional cap.set call.
		"""
		if start_frame < 0 or start_frame >= self._total_frames:
			raise ValueError(
				f"start_frame={start_frame} out of range [0, {self._total_frames})"
			)
		self._cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
		self._cap_next_index = start_frame

	#============================================
	def read_frame(self, frame_index: int) -> numpy.ndarray | None:
		"""Read a single frame by index.

		Strategy 0 fires when frame_index == self._cap_next_index: no
		cap.set is called; cap.read() advances the cursor. Strategy 1
		fires otherwise: cap.set(CAP_PROP_POS_FRAMES, idx) then cap.read().

		Args:
			frame_index: Target frame index (0-based).

		Returns:
			BGR frame as numpy array, or None if decode fails.
		"""
		if frame_index != self._cap_next_index:
			# strategy 1: random-access seek
			self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
		# strategy 0 falls through: cap.read() reads the next frame
		ret, frame = self._cap.read()
		if not ret or frame is None:
			# invalidate cursor so a subsequent read forces a cap.set
			self._cap_next_index = -1
			return None
		self._cap_next_index = frame_index + 1
		return self._apply_bin(frame)

	#============================================
	def __iter__(self):
		"""Iterate over all frames, yielding (frame_index, frame) tuples.

		Resets to frame 0 at iteration start. Each frame is returned via
		_apply_bin so bin/goodbox semantics are honored.

		Yields:
			(frame_index, frame) tuples where frame_index goes from 0 to
			frame_count-1 and frame is the BGR numpy array. Stops early
			if the stream ends before frame_count.
		"""
		self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
		self._cap_next_index = 0
		frame_index = 0
		while frame_index < self._total_frames:
			ret, frame = self._cap.read()
			if not ret or frame is None:
				self._cap_next_index = -1
				break
			self._cap_next_index = frame_index + 1
			yield (frame_index, self._apply_bin(frame))
			frame_index += 1

	#============================================
	def __enter__(self):
		"""Context manager entry: return self."""
		return self

	#============================================
	def __exit__(self, exc_type, exc_value, traceback):
		"""Context manager exit: call close()."""
		self.close()

	#============================================
	def close(self) -> None:
		"""Release the cv2.VideoCapture."""
		# getattr handles partial __init__ where _cap was never set
		cap = getattr(self, "_cap", None)
		if cap is not None:
			cap.release()
			self._cap = None
