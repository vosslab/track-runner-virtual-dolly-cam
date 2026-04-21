"""Seed-assistance utilities.

Provides functions that support user seeding: extracting the legacy
jersey_hsv tag preserved on disk (not used as identity evidence per
contract C6), normalizing user-drawn seed boxes, and proposing candidate
regions from a detector.

Per docs/TRACK_RUNNER_CONTRACT.md clause C6, jersey color and color
histograms are banned as identity or classification evidence. The
detector-assisted candidate ordering here is OPTIONAL seeding assistance
ranked by detector confidence alone, never by appearance similarity.
Manual annotation remains the authoritative path.
"""

# PIP3 modules
import cv2
import numpy

#============================================

def _clamp_box(frame: numpy.ndarray, box: list) -> tuple:
	"""Clamp a box to frame bounds and return the ROI.

	Args:
		frame: BGR image as a numpy array (H, W, 3).
		box: Rectangle as [x, y, w, h] in pixel coordinates.

	Returns:
		Cropped ROI as a numpy array, or None if the clamped region is empty.
	"""
	frame_h, frame_w = frame.shape[:2]
	x, y, w, h = box
	# clamp top-left corner to frame bounds
	x1 = max(0, int(x))
	y1 = max(0, int(y))
	# clamp bottom-right corner to frame bounds
	x2 = min(frame_w, int(x + w))
	y2 = min(frame_h, int(y + h))
	# check for empty region after clamping
	if x2 <= x1 or y2 <= y1:
		return None
	roi = frame[y1:y2, x1:x2]
	return roi


#============================================

def extract_jersey_color(frame: numpy.ndarray, box: list) -> tuple:
	"""Extract median HSV color from a rectangular region.

	Args:
		frame: BGR image as a numpy array (H, W, 3).
		box: Rectangle as [x, y, w, h] in pixel coordinates.

	Returns:
		Tuple of (h_median, s_median, v_median) as ints,
		or (0, 0, 0) if the box is out of frame bounds.
	"""
	# clamp box to frame bounds and extract ROI
	roi = _clamp_box(frame, box)
	if roi is None:
		return (0, 0, 0)
	# convert from BGR to HSV color space
	hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
	# compute median for each HSV channel
	h_median = int(numpy.median(hsv_roi[:, :, 0]))
	s_median = int(numpy.median(hsv_roi[:, :, 1]))
	v_median = int(numpy.median(hsv_roi[:, :, 2]))
	return (h_median, s_median, v_median)


#============================================

def detection_to_torso_box(bbox: list) -> list:
	"""Extract upper 60% of detection bbox as torso region.

	Args:
		bbox: Bounding box as [x, y, w, h] in pixel coordinates.

	Returns:
		Torso box as [x, y, w, h] representing the upper 60% of bbox.
	"""
	x, y, w, h = bbox
	torso_h = int(h * 0.6)
	return [x, y, w, torso_h]


#============================================

def suggest_seed_candidates(
	frame: numpy.ndarray,
	detections: list,
	confirmed_seeds: list,
	frame_index: int,
) -> dict:
	"""Suggest seed candidate regions from detector output.

	Optional seeding assistance only. Ranks candidates by the detector's
	own confidence score. No appearance, color, or histogram cue is
	computed here (contract C6 forbids jersey / HSV / histogram cues as
	identity or classification evidence). Manual annotation remains the
	authoritative path; this function just orders candidates to help the
	user pick faster.

	Args:
		frame: BGR image as a numpy array (H, W, 3). Unused for cue
			extraction; kept in the signature for callsite compatibility.
		detections: List of detection dicts from the detector with keys
			bbox, confidence, class_id.
		confirmed_seeds: List of already-seeded dicts. Unused here; kept
			in the signature for callsite compatibility and to make it
			clear that prior seeds do NOT influence candidate ranking.
		frame_index: Current frame index for reference. Unused.

	Returns:
		Dict with keys:
			candidates: List of candidate dicts (bbox, torso_box,
				detection_confidence), sorted by detection_confidence
				descending.
			suggestion_index: 0 if there is exactly one candidate, else
				None (ambiguous -> manual pick).
			mode: "none" (no detections), "single" (one candidate), or
				"manual" (multiple candidates, user picks).
			scores: None. Legacy Bhattacharyya-distance field retained
				as None so older consumers that read it continue to work
				without crashing.
	"""
	# unused arguments retained for callsite compatibility; silence lint
	del frame, confirmed_seeds, frame_index

	# no detections: return empty candidates
	if not detections:
		return {
			"candidates": [],
			"suggestion_index": None,
			"mode": "none",
			"scores": None,
		}

	# build candidate list; no appearance cues are computed
	candidates = []
	for det in detections:
		bbox = det["bbox"]
		torso_box = detection_to_torso_box(bbox)
		candidate = {
			"bbox": bbox,
			"torso_box": torso_box,
			"detection_confidence": det["confidence"],
		}
		candidates.append(candidate)

	# order by detector confidence descending so the first entry is the
	# detector's strongest pick; ordering is not a tracking decision
	candidates.sort(key=lambda c: c["detection_confidence"], reverse=True)

	# auto-suggest only when exactly one candidate exists; otherwise the
	# user picks manually. This is the most conservative behavior after
	# removing histogram-based tie-breaking.
	if len(candidates) == 1:
		return {
			"candidates": candidates,
			"suggestion_index": 0,
			"mode": "single",
			"scores": None,
		}

	return {
		"candidates": candidates,
		"suggestion_index": None,
		"mode": "manual",
		"scores": None,
	}


#============================================

def normalize_seed_box(box: list, config: dict) -> list:
	"""Normalize an inconsistently-drawn seed box.

	Enforces minimum dimensions and clamps the aspect ratio
	to the configured torso range.

	Args:
		box: Rectangle as [x, y, w, h] in pixel coordinates.
		config: Configuration dict optionally containing seeding section.

	Returns:
		Normalized [x, y, w, h] as integers.
	"""
	x, y, w, h = box
	# enforce minimum dimensions of 10 pixels
	w = max(w, 10)
	h = max(h, 10)
	# read aspect ratio limits from config; v2 config uses flat processing section
	processing = config.get("processing", config.get("settings", {}).get("seeding", {}))
	aspect_min = float(processing.get("torso_aspect_min", 0.3))
	aspect_max = float(processing.get("torso_aspect_max", 0.8))
	# compute current aspect ratio (width / height)
	aspect = w / h
	if aspect > aspect_max:
		# too wide, shrink width to match max aspect
		w = int(h * aspect_max)
	elif aspect < aspect_min:
		# too narrow, shrink height to match min aspect
		h = int(w / aspect_min)
	return [int(x), int(y), int(w), int(h)]


#============================================

def _build_seed_dict(
	frame_index: int,
	time_sec: float,
	torso_box: list,
	jersey_hsv: tuple,
	pass_number: int,
	mode: str,
) -> dict:
	"""Build a v2 seed dict from collected fields.

	Args:
		frame_index: Frame index (0-based).
		time_sec: Time in seconds.
		torso_box: Normalized torso box as [x, y, w, h].
		jersey_hsv: Tuple of (h, s, v) median HSV values. Preserved in the
			on-disk schema per the 2026-04-20 design note; not used as
			identity evidence at solve time per contract C6.
		pass_number: Which collection pass this seed came from (1 = initial).
		mode: Seed collection mode string.

	Returns:
		Seed dict in v2 format with frame, time_s, torso_box, jersey_hsv,
		cx, cy, w, h, pass, source, mode, and status keys.
	"""
	tx, ty, tw, th = torso_box
	# compute center format for propagator compatibility
	cx = float(tx + tw / 2.0)
	cy = float(ty + th / 2.0)
	seed = {
		"frame_index": frame_index,
		"time_s": round(time_sec, 3),
		"torso_box": torso_box,
		"jersey_hsv": list(jersey_hsv),
		"cx": cx,
		"cy": cy,
		"w": float(tw),
		"h": float(th),
		"pass": pass_number,
		"conf": None,
		"source": "human",
		"mode": mode,
		"status": "visible",
	}
	return seed
