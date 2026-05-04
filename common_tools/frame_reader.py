"""Reliable frame reader for video via PyAV decode backend.

FrameReader wraps PyAV (av module) for video decode and provides a public API
for frame reading: read_frame(index), frame_count, fps, width, height, geometry,
bin_factor. Supports bin_factor downsample and goodbox snap (origin-preserving
crop to FFT-friendly dimensions) on every frame, regardless of bin_factor value.
"""

# Standard Library
import warnings
import dataclasses

# PIP3 modules
import av
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
	# divisibility warnings only meaningful when binning is requested:
	# a fractional source-pixel column or row is silently dropped by
	# the floor division below.
	if bin_factor > 1:
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
	"""Read video frames via PyAV decode backend.

	Decodes frames using PyAV (av module) with lazy keyframe seek for random
	access. Sequential reads of consecutive frame indices efficiently use the
	last-PTS cache (one decoded packet per consecutive pair).

	Args:
		video_path: Path to the video file.
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
			video_path: Path to the video file.
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

		# Initialize PyAV backend
		self._init_pyav_backend(video_path)

		# resolve scaled and processed dims; FrameGeometry is the
		# single source of coordinate-conversion truth and exposes
		# the resolved dims downstream.
		self._geometry = _resolve_frame_geometry(
			self._source_width, self._source_height, bin_factor
		)
		# public width/height return processed dims; image consumers
		# downstream do not need to know bin_factor exists, but
		# coordinate-aware callers should use FrameReader.geometry.
		self._width = self._geometry.processed_width
		self._height = self._geometry.processed_height

	#============================================
	def _init_pyav_backend(self, video_path: str) -> None:
		"""Initialize PyAV backend."""
		# Open the container
		self._av_container = av.open(video_path)

		# Get the video stream
		self._av_stream = self._av_container.streams.video[0]

		# Read source dimensions from the stream
		self._source_width = self._av_stream.codec_context.width
		self._source_height = self._av_stream.codec_context.height

		# Decoder iterator state
		self._av_iter = None
		# Last successfully decoded frame PTS (in stream time_base units)
		self._av_last_pts = -1

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

		The goodbox crop applies at any bin_factor, including 1, so
		downstream FFT consumers (Stage 1 phase correlate, Stage 4
		residual algorithms) always see FFT-friendly dimensions. At
		bin_factor == 1 with a goodbox-sized source, the crop is a
		no-op and the frame is returned unchanged.
		"""
		if frame is None:
			return None
		geom = self._geometry
		# downsample only when binning is requested
		if self._bin_factor > 1:
			frame = cv2.resize(
				frame,
				(geom.scaled_width, geom.scaled_height),
				interpolation=cv2.INTER_AREA,
			)
		# origin-preserving right/bottom crop to goodbox-snapped dims
		if (
			frame.shape[0] != geom.processed_height
			or frame.shape[1] != geom.processed_width
		):
			frame = frame[
				0 : geom.processed_height, 0 : geom.processed_width
			]
		return frame

	#============================================
	def seek_for_encode(self, start_frame: int) -> None:
		"""Seek to start_frame and arm the sequential fast-path."""
		if start_frame < 0 or start_frame >= self._total_frames:
			raise ValueError(
				f"start_frame={start_frame} out of range [0, {self._total_frames})"
			)
		# PyAV backend: perform a seek and arm for sequential continuation
		self._seek_pyav(start_frame)
		self._av_last_pts = start_frame - 1

	#============================================
	def read_frame(self, frame_index: int) -> numpy.ndarray | None:
		"""Read a single frame by index using lazy keyframe seek.

		When frame_index is not sequential (frame_index != last_decoded_pts + 1),
		seeks to the keyframe at-or-before the target and decodes forward to it.
		Maintains a last-PTS cache so consecutive reads decode one packet.

		Args:
			frame_index: Target frame index (0-based).

		Returns:
			BGR frame as numpy array, or None if decode fails.
		"""
		return self._read_frame_pyav(frame_index)

	#============================================
	def _read_frame_pyav(self, frame_index: int) -> numpy.ndarray | None:
		"""Read a frame using the PyAV backend with lazy keyframe seek.

		Implements lazy keyframe seek: when read_frame(N) is called for
		N != last_decoded_pts + 1, seek to the keyframe at-or-before N
		and decode forward to N. Maintains _av_last_pts cache so
		read_frame(N) then read_frame(N+1) decodes exactly one packet.

		Args:
			frame_index: Target frame index (0-based).

		Returns:
			BGR frame as numpy array, or None if decode fails.
		"""
		# Check if we need to seek (not sequential continuation)
		if self._av_iter is None or frame_index != self._av_last_pts + 1:
			# Need a fresh seek
			self._seek_pyav(frame_index)

		# Decode forward to target frame
		frame = self._decode_next_pyav(frame_index)
		if frame is None:
			return None

		# Update cache to mark this frame as decoded
		self._av_last_pts = frame_index

		# Apply bin + goodbox crop semantics
		return self._apply_bin(frame)

	#============================================
	def _seek_pyav(self, target_frame_index: int) -> None:
		"""Seek to keyframe at-or-before target_frame_index.

		Computes PTS in the stream's time_base and seeks backward to
		the nearest keyframe. After seek, _av_iter is reset so the next
		decode starts from the keyframe.

		Args:
			target_frame_index: Target frame index (0-based).
		"""
		# Convert frame index to PTS in stream time_base units
		# frame_index -> time in seconds -> PTS in stream time_base
		time_sec = target_frame_index / self._fps
		time_base = self._av_stream.time_base
		# Approximate PTS; stream start_time may not be 0
		target_pts = int(time_sec / float(time_base))

		# Seek backward to keyframe at-or-before target_pts
		self._av_container.seek(
			target_pts,
			stream=self._av_stream,
			backward=True,
			any_frame=False,
		)

		# Reset the iterator so the next call to decode_next_pyav
		# starts fresh from the seeked position
		self._av_iter = None

	#============================================
	def _decode_next_pyav(self, target_frame_index: int) -> numpy.ndarray | None:
		"""Decode forward from current position to target_frame_index.

		Lazily opens the iterator if needed, then walks forward,
		comparing frame.dts to the target frame's expected PTS.
		Returns the first frame at-or-after the target PTS.

		Args:
			target_frame_index: Target frame index (0-based).

		Returns:
			BGR uint8 ndarray of shape (height, width, 3), or None on failure.
		"""
		# Lazy-open the iterator
		if self._av_iter is None:
			self._av_iter = self._av_container.decode(self._av_stream)

		# Compute the target PTS in stream time_base units
		time_sec = target_frame_index / self._fps
		time_base = self._av_stream.time_base
		target_pts = int(time_sec / float(time_base))

		# Walk forward until we reach the target PTS
		for av_frame in self._av_iter:
			# Get the frame's PTS; use dts as fallback
			frame_pts = av_frame.pts if av_frame.pts is not None else av_frame.dts
			if frame_pts is None:
				continue

			# Check if we've reached or passed the target PTS
			if frame_pts >= target_pts:
				# Convert to BGR uint8 ndarray
				return self._frame_to_bgr_pyav(av_frame)

		# Reached end of stream without finding target
		return None

	#============================================
	def _frame_to_bgr_pyav(self, av_frame) -> numpy.ndarray:
		"""Convert a PyAV VideoFrame to BGR uint8 ndarray.

		Reformats from the frame's native format to BGR24.

		Args:
			av_frame: PyAV VideoFrame object.

		Returns:
			BGR uint8 ndarray of shape (height, width, 3).
		"""
		# Reformat to BGR24 (OpenCV standard)
		av_frame_bgr = av_frame.reformat(format="bgr24")
		# Convert to numpy ndarray
		bgr_array = av_frame_bgr.to_ndarray()
		return bgr_array

	#============================================
	def __iter__(self):
		"""Iterate over all frames, yielding (frame_index, frame) tuples.

		Resets to frame 0 at iteration start. Each frame is returned via
		_apply_bin so bin/goodbox semantics are honored.

		Yields:
			(frame_index, frame) tuples where frame_index goes from 0 to
			frame_count-1 and frame is the BGR numpy array (or None if
			stream ends).
		"""
		yield from self._iter_pyav()

	#============================================
	def _iter_pyav(self):
		"""Iterate over frames using PyAV backend."""
		# Seek to frame 0 and reset iterator
		self._seek_pyav(0)
		self._av_last_pts = -1
		frame_index = 0
		while frame_index < self._total_frames:
			frame = self._decode_next_pyav(frame_index)
			if frame is None:
				break
			# Update last-PTS cache for sequential continuation
			self._av_last_pts = frame_index
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
		"""Release PyAV container and iterator."""
		if self._av_container is not None:
			self._av_container.close()
			self._av_container = None
		self._av_iter = None
