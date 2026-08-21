"""Seed-assistance utilities.

Provides helpers for user seeding: normalizing user-drawn seed boxes,
proposing detector candidates, and building the canonical v3 seed
dict. Per docs/TRACK_RUNNER_CONTRACT.md clauses C6 and C7, appearance
cues (jersey color, color histograms) are banned as identity evidence,
and only human-drawn torso boxes count as seeds.
"""

# PIP3 modules
import numpy

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
	"""
	# unused arguments retained for callsite compatibility; silence lint
	del frame, confirmed_seeds, frame_index

	# no detections: return empty candidates
	if not detections:
		return {
			"candidates": [],
			"suggestion_index": None,
			"mode": "none",
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
		}

	return {
		"candidates": candidates,
		"suggestion_index": None,
		"mode": "manual",
	}


#============================================

def normalize_seed_box(box: list, config: dict) -> list:
	"""Normalize an inconsistently-drawn seed box.

	Enforces minimum dimensions and clamps the aspect ratio
	to the configured torso range.

	Args:
		box: Rectangle as [x, y, w, h] in pixel coordinates.
	config: Configuration dict with the current processing section.

	Returns:
		Normalized [x, y, w, h] as integers.
	"""
	x, y, w, h = box
	# enforce minimum dimensions of 10 pixels
	w = max(w, 10)
	h = max(h, 10)
	# Read aspect ratio limits from the current processing section.
	processing = config.get("processing", {})
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

def build_seed_dict(
	frame_index: int,
	torso_box: list,
	pass_number: int,
	status: str,
) -> dict:
	"""Build a canonical v3 seed dict with derived geometry attached.

	Canonical on-disk fields are frame_index, torso_box, status, pass.
	The returned dict also carries cx/cy/w/h derived from torso_box so
	in-memory consumers (interval_fingerprint, velocity_model, UI) work
	without re-deriving. write_seeds strips the derived keys back out.

	Args:
		frame_index: Frame index (0-based).
		torso_box: Normalized torso box as [x, y, w, h] (ints).
		pass_number: Seeding pass number (1 = initial).
		status: Explicit seed status selected at the mode decision site.

	Returns:
		Seed dict with canonical keys plus derived cx/cy/w/h.
	"""
	tx, ty, tw, th = torso_box
	# compute derived geometry for in-memory consumers (stripped at write)
	cx = float(tx) + float(tw) / 2.0
	cy = float(ty) + float(th) / 2.0
	seed = {
		"frame_index": frame_index,
		"torso_box": torso_box,
		"status": status,
		"pass": pass_number,
		"cx": cx,
		"cy": cy,
		"w": float(tw),
		"h": float(th),
	}
	return seed
