"""Drawing utilities for track_runner overlays.

Provides common drawing functions for video overlays and contact sheets.
"""

# PIP3 modules
import cv2
import numpy


#============================================
def draw_dashed_rect(
	frame: numpy.ndarray,
	x1: int,
	y1: int,
	x2: int,
	y2: int,
	color: tuple,
	thickness: int = 2,
	dash_len: int = 10,
) -> None:
	"""Draw a dashed rectangle on the frame in-place.

	Args:
		frame: BGR image to draw on.
		x1: Left edge of the rectangle.
		y1: Top edge of the rectangle.
		x2: Right edge of the rectangle.
		y2: Bottom edge of the rectangle.
		color: BGR color tuple.
		thickness: Line thickness in pixels.
		dash_len: Length of each dash segment in pixels.
	"""
	# top edge: left to right
	x = x1
	while x < x2:
		x_end = min(x + dash_len, x2)
		cv2.line(frame, (x, y1), (x_end, y1), color, thickness)
		x += 2 * dash_len
	# bottom edge: left to right
	x = x1
	while x < x2:
		x_end = min(x + dash_len, x2)
		cv2.line(frame, (x, y2), (x_end, y2), color, thickness)
		x += 2 * dash_len
	# left edge: top to bottom
	y = y1
	while y < y2:
		y_end = min(y + dash_len, y2)
		cv2.line(frame, (x1, y), (x1, y_end), color, thickness)
		y += 2 * dash_len
	# right edge: top to bottom
	y = y1
	while y < y2:
		y_end = min(y + dash_len, y2)
		cv2.line(frame, (x2, y), (x2, y_end), color, thickness)
		y += 2 * dash_len
