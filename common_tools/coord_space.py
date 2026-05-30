"""Typed coordinate primitives for the binning pipeline's two pixel spaces.

THE ONE CONTRACT
================

The binning pipeline tracks runner geometry in two distinct pixel spaces:

  SOURCE    - full-frame source-video pixels (npz storage, encoder output).
  PROCESSED - post-bin, goodbox-snapped pixels (residual-motion observer,
              blob walker, anything that reads decoded analysis frames).

Each space has its OWN Python type.  The TYPE encodes the space:

  SourcePoint / SourceBox       live in SOURCE pixels.
  ProcessedPoint / ProcessedBox live in PROCESSED pixels.

Because type == space, a space mismatch is a loud, mechanical error rather
than a silent numeric bug.  Two design properties follow:

  1. Double conversion is unrepresentable.  A SourcePoint has only
     .to_processed(); a ProcessedPoint has only .to_source().  Calling
     SourcePoint(...).to_source() is AttributeError (method-not-found), caught
     by pyflakes before the code ever runs.  The wrong direction does not
     exist on the type.

  2. Conversion is PURE SCALE by bin_factor (routed through FrameGeometry),
     never clamped.  Whether a converted coordinate actually lands inside the
     processed frame is a SEPARATE, EXPLICIT predicate: .in_bounds(geometry).
     This is the elegant resolution of the goodbox issue: scaled_width is
     snapped DOWN to a goodbox, so a pure /bin conversion of a near-edge
     source coord can land at or past processed_width.  Rather than silently
     clamp (which built degenerate ROIs in bug #101), conversion stays pure
     and the caller asks in_bounds() to decide off-frame.  in_bounds() on a
     box is the off-frame predicate that replaces the old deep
     degenerate-ROI crash.

Boxes are CENTER-SIZE (cx, cy, w, h), NOT edge boxes.  Use .edges() to derive
(x1, y1, x2, y2) from the float center before any rounding.

The require_* helpers are boundary guards: a migrated function that accepts a
typed box can call require_processed_box(obj) at its boundary to fail loud
(ValueError) when a caller hands it the wrong space.

Geometry math (the actual /bin and *bin) lives in
common_tools.frame_reader.FrameGeometry and is NOT duplicated here; this
module only routes through it and adds the type-level space distinction.
"""

# Standard Library
import dataclasses

# local repo modules
import common_tools.frame_reader


#============================================
# SOURCE-space primitives
#============================================

@dataclasses.dataclass(frozen=True)
class SourcePoint:
	"""A single point in SOURCE (full-frame) pixels."""
	cx: float
	cy: float

	#============================================
	def to_processed(
		self, geometry: common_tools.frame_reader.FrameGeometry
	) -> "ProcessedPoint":
		"""Convert to PROCESSED pixels via pure /bin_factor scale.

		Result is NOT clamped to the processed frame; call
		ProcessedPoint.in_bounds(geometry) to test off-frame.
		"""
		# pure-scale center conversion routed through FrameGeometry
		px, py = geometry.source_to_processed(self.cx, self.cy)
		processed_point = ProcessedPoint(cx=px, cy=py)
		return processed_point


@dataclasses.dataclass(frozen=True)
class SourceBox:
	"""A center-size torso box in SOURCE (full-frame) pixels."""
	cx: float
	cy: float
	w: float
	h: float

	#============================================
	def to_processed(
		self, geometry: common_tools.frame_reader.FrameGeometry
	) -> "ProcessedBox":
		"""Convert to PROCESSED pixels via pure /bin_factor scale.

		Center scales via source_to_processed; width/height scale via
		source_to_processed_delta.  Result is NOT clamped; call
		ProcessedBox.in_bounds(geometry) to test off-frame.
		"""
		# center scales as a position
		px, py = geometry.source_to_processed(self.cx, self.cy)
		# width/height scale as a delta (no origin offset)
		pw, ph = geometry.source_to_processed_delta(self.w, self.h)
		processed_box = ProcessedBox(cx=px, cy=py, w=pw, h=ph)
		return processed_box

	#============================================
	def edges(self) -> tuple:
		"""Return (x1, y1, x2, y2) derived from the float center.

		Math stays in float; no rounding happens here.
		"""
		x1 = self.cx - self.w / 2.0
		y1 = self.cy - self.h / 2.0
		x2 = self.cx + self.w / 2.0
		y2 = self.cy + self.h / 2.0
		edge_tuple = (x1, y1, x2, y2)
		return edge_tuple


#============================================
# PROCESSED-space primitives
#============================================

@dataclasses.dataclass(frozen=True)
class ProcessedPoint:
	"""A single point in PROCESSED (post-bin, goodbox-snapped) pixels."""
	cx: float
	cy: float

	#============================================
	def to_source(
		self, geometry: common_tools.frame_reader.FrameGeometry
	) -> "SourcePoint":
		"""Convert to SOURCE pixels via pure *bin_factor scale."""
		# pure-scale center conversion routed through FrameGeometry
		sx, sy = geometry.processed_to_source(self.cx, self.cy)
		source_point = SourcePoint(cx=sx, cy=sy)
		return source_point

	#============================================
	def in_bounds(
		self, geometry: common_tools.frame_reader.FrameGeometry
	) -> bool:
		"""True iff the point lies inside the processed frame.

		Off-frame predicate: 0 <= cx < processed_width and
		0 <= cy < processed_height.  Because conversion is pure scale and
		processed_width may be < scaled_width (goodbox snap-down), a
		near-edge source coord can convert to an out-of-bounds processed
		coord; this predicate makes that explicit instead of clamping.
		"""
		# half-open interval on both axes against the goodbox-snapped frame
		x_ok = 0 <= self.cx < geometry.processed_width
		y_ok = 0 <= self.cy < geometry.processed_height
		inside = x_ok and y_ok
		return inside


@dataclasses.dataclass(frozen=True)
class ProcessedBox:
	"""A center-size torso box in PROCESSED (post-bin) pixels."""
	cx: float
	cy: float
	w: float
	h: float

	#============================================
	def to_source(
		self, geometry: common_tools.frame_reader.FrameGeometry
	) -> "SourceBox":
		"""Convert to SOURCE pixels via pure *bin_factor scale.

		Center scales via processed_to_source; width/height scale via
		processed_to_source_delta.
		"""
		# center scales as a position
		sx, sy = geometry.processed_to_source(self.cx, self.cy)
		# width/height scale as a delta (no origin offset)
		sw, sh = geometry.processed_to_source_delta(self.w, self.h)
		source_box = SourceBox(cx=sx, cy=sy, w=sw, h=sh)
		return source_box

	#============================================
	def edges(self) -> tuple:
		"""Return (x1, y1, x2, y2) derived from the float center.

		Math stays in float; no rounding happens here.
		"""
		x1 = self.cx - self.w / 2.0
		y1 = self.cy - self.h / 2.0
		x2 = self.cx + self.w / 2.0
		y2 = self.cy + self.h / 2.0
		edge_tuple = (x1, y1, x2, y2)
		return edge_tuple

	#============================================
	def in_bounds(
		self, geometry: common_tools.frame_reader.FrameGeometry
	) -> bool:
		"""True iff the box CENTER lies inside the processed frame.

		Off-frame predicate that replaces the deep degenerate-ROI crash
		(bug #101): at bin_factor > 1 a SOURCE coord fed where PROCESSED
		was expected converts to an out-of-frame center, which previously
		built a degenerate ROI.  Callers test in_bounds() and treat a
		False result as off-frame.
		"""
		# the box center reuses the point predicate
		center = ProcessedPoint(cx=self.cx, cy=self.cy)
		inside = center.in_bounds(geometry)
		return inside


#============================================
# Boundary guards
#============================================

def require_source_point(obj) -> SourcePoint:
	"""Return obj if it is a SourcePoint, else raise ValueError (loud)."""
	if not isinstance(obj, SourcePoint):
		raise ValueError(f"expected SourcePoint (SOURCE space), got {type(obj).__name__}")
	return obj


#============================================
def require_processed_point(obj) -> ProcessedPoint:
	"""Return obj if it is a ProcessedPoint, else raise ValueError (loud)."""
	if not isinstance(obj, ProcessedPoint):
		raise ValueError(f"expected ProcessedPoint (PROCESSED space), got {type(obj).__name__}")
	return obj


#============================================
def require_source_box(obj) -> SourceBox:
	"""Return obj if it is a SourceBox, else raise ValueError (loud)."""
	if not isinstance(obj, SourceBox):
		raise ValueError(f"expected SourceBox (SOURCE space), got {type(obj).__name__}")
	return obj


#============================================
def require_processed_box(obj) -> ProcessedBox:
	"""Return obj if it is a ProcessedBox, else raise ValueError (loud)."""
	if not isinstance(obj, ProcessedBox):
		raise ValueError(f"expected ProcessedBox (PROCESSED space), got {type(obj).__name__}")
	return obj
