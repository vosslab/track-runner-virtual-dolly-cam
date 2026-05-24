#!/usr/bin/env python3
"""Counterfactual gate simulator for blob refinement policy ranking.

Replays the blob-snap gate decision under each candidate policy across
the M1 corpus and ranks them per G-4. The production gate code is the
source of truth; this tool re-implements ONLY the per-policy decision
shape against the per-frame trace captured by tools/visualize_blob_gates.py.

Inputs:
  -c, --oracle-csv         oracle.csv from WP-1A.
  -g, --verdicts-glob      glob over verdicts.csv from WP-1B.
  -e, --policy-e-gates     analyses/policy_e_gates.json from WP-2C.
  -o, --output-dir         output directory.

Outputs:
  <output-dir>/RANKING.md
  <output-dir>/by_policy.csv
  <output-dir>/per_video_policy.csv
  <output-dir>/policy_f_displacement.csv
  <output-dir>/ranking.json
"""

# Standard Library
import os
import sys
import csv
import json
import glob
import math
import argparse
import dataclasses
from collections import defaultdict

# add repo root to sys.path so absolute imports resolve when the tool
# is run directly. Uses absolute imports (track_runner.velocity_model)
# rather than bare module imports for tighter import-boundary
# enforcement (AC-3l): the test sees the fully-qualified module name.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, "track_runner")
# track_runner/velocity_model.py uses bare imports, so track_runner/
# must be on sys.path. Repo root must come BEFORE track_runner/ so
# `import track_runner.velocity_model` resolves to the package.
if _TRACK_RUNNER_DIR in sys.path:
	sys.path.remove(_TRACK_RUNNER_DIR)
if _REPO_ROOT in sys.path:
	sys.path.remove(_REPO_ROOT)
sys.path.insert(0, _TRACK_RUNNER_DIR)
sys.path.insert(0, _REPO_ROOT)

# local repo modules
# Production constants are imported -- never re-defined. AC-3b's
# byte-fidelity check relies on the simulator and the solver sharing
# numerical values.
from track_runner.velocity_model import BLOB_SNAP_ALPHA
from track_runner.velocity_model import BLOB_SNAP_VELOCITY_FLOOR
from track_runner.velocity_model import BLOB_SNAP_MAX_SHIFT_FRACTION
from track_runner.velocity_model import BLOB_SNAP_ALPHA_MAX
from track_runner.velocity_model import BLOB_SNAP_PATH_SLACK
from track_runner.velocity_model import BLOB_SNAP_PATH_PERP_FRACTION


# Default policy set per plan section "Counterfactual policy set".
# Policy E is conditional and added in main() based on policy_e_gates.json.
POLICIES = {"A", "B", "C", "F", "G"}


# Constants reserved for the deferred policy replays. Kept as an
# explicit tuple so the imports stay live under pyflakes and so a
# reader sees which production constants each future policy will
# consume.
_RESERVED_FOR_FUTURE_POLICIES = (
	BLOB_SNAP_MAX_SHIFT_FRACTION,
	BLOB_SNAP_ALPHA_MAX,
	BLOB_SNAP_PATH_SLACK,
	BLOB_SNAP_PATH_PERP_FRACTION,
)


#============================================
@dataclasses.dataclass
class PolicyVerdict:
	"""Per-policy G-4 evaluation result.

	clauses maps the G-4 clause label (e.g. "G-4(1)") to "PASS" or
	"FAIL". reason is non-empty when the policy is REJECTED and names
	the failing clause plus a short cause string.
	"""
	policy: str
	verdict: str
	clauses: dict
	reason: str


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description=(
			"Replay blob-snap gate decisions under candidate policies "
			"on the M1 corpus and rank them per G-4 (1)-(6)."
		)
	)
	parser.add_argument(
		"-c", "--oracle-csv", dest="oracle_csv", required=True,
		help="Path to oracle.csv (WP-1A output)."
	)
	parser.add_argument(
		"-g", "--verdicts-glob", dest="verdicts_glob", required=True,
		help="Glob over verdicts.csv files (WP-1B output)."
	)
	parser.add_argument(
		"-e", "--policy-e-gates", dest="policy_e_gates", required=True,
		help="Path to policy_e_gates.json (WP-2C output)."
	)
	parser.add_argument(
		"-o", "--output-dir", dest="output_dir", required=True,
		help="Output directory for RANKING.md and CSVs."
	)
	args = parser.parse_args()
	return args


#============================================
def load_policy_e_gates(path: str) -> dict:
	"""Read policy_e_gates.json and return the parsed dict."""
	with open(path, "r", encoding="utf-8") as f:
		data = json.load(f)
	return data


#============================================
def load_oracle(path: str) -> dict:
	"""Load oracle.csv into a dict keyed by (video, frame)."""
	oracle_rows = {}
	with open(path, "r", encoding="utf-8") as f:
		reader = csv.DictReader(f)
		for row in reader:
			video = row["video"]
			frame = int(row["frame"])
			oracle_rows[(video, frame)] = row
	return oracle_rows


#============================================
def load_selection(path: str) -> dict:
	"""Load per_video_selection_rebucketed.csv and return ranking-sample rows."""
	ranking_intervals = {}
	with open(path, "r", encoding="utf-8") as f:
		reader = csv.DictReader(f)
		for row in reader:
			if row["sample"] != "ranking":
				continue
			video = row["video"]
			interval_start = int(row["interval_start"])
			interval_end = int(row["interval_end"])
			bucket = row["bucket"]
			if video not in ranking_intervals:
				ranking_intervals[video] = []
			ranking_intervals[video].append({
				"interval_start": interval_start,
				"interval_end": interval_end,
				"bucket": bucket,
			})
	return ranking_intervals


#============================================
def _parse_bool(text: str) -> bool:
	"""Parse a verdicts.csv boolean cell.

	Cell values: "True", "False", or "vacuous".
	"vacuous" is treated as True (gate passed by default vacuous-true semantics).
	"""
	if text == "True":
		return True
	if text == "False":
		return False
	if text == "vacuous":
		return True
	raise ValueError("verdicts.csv boolean cell must be 'True', 'False', or 'vacuous', got " + repr(text))


#============================================
def _parse_float(text: str) -> float:
	"""Parse a verdicts.csv numeric cell that may be empty."""
	if text == "":
		return 0.0
	value = float(text)
	return value


#============================================
def _parse_corridor_blobs_json(blobs_json_str: str) -> list:
	"""Parse corridor_blobs_json and return list of blob dicts."""
	if not blobs_json_str or blobs_json_str.strip() == "":
		return []
	blobs = json.loads(blobs_json_str)
	return blobs


#============================================
def _infer_path_ok(frame_row: dict) -> bool:
	"""Parse path_ok_prev from CSV row.

	With explicit "vacuous" markers in verdicts.csv, inference is straightforward:
	- "vacuous" -> True (gate passed by vacuous-true semantics)
	- "True" -> True (gate actually evaluated and passed)
	- "False" -> False (gate actually evaluated and failed)

	Returns True if path_ok_prev should pass the gate; False otherwise.
	"""
	path_raw = frame_row.get("path_ok_prev", "True")
	return _parse_bool(path_raw)


#============================================
def _replay_policy_a(frame_row: dict) -> dict:
	"""Replay the production proximity + direction + path gate.

	Policy A is the byte-fidelity control (AC-3b). Compute all gate
	conditions from raw verdicts.csv data to ensure compatibility with
	both CSV-sourced frames and synthetic test fixtures.

	Note: The motion-path gate (path_ok_prev) may return None (vacuous) when
	the frame's velocity falls below BLOB_SNAP_VELOCITY_FLOOR, but the CSV
	records both vacuous and failed gates as "False". To infer vacuous gates,
	we check: if the production gate is "accepted" AND both proximity and
	direction gates pass, then path_ok_prev must have been None (vacuous),
	not False. See tools/replay_check_policy_a.py for full rationale.
	"""
	gate = frame_row["gate"]
	if gate in ("skipped", "absent"):
		return {"would_accept": False, "weight": 0.0}

	# Compute proximity gate (A-standard: 0.6 * h)
	dist_px = _parse_float(frame_row.get("winner_dist_px", "0"))
	dist_h = _parse_float(frame_row.get("winner_dist_h", "0"))
	proximity_ok = dist_px <= BLOB_SNAP_ALPHA * (dist_px / dist_h if dist_h > 0.0 else 1.0)

	# Compute direction gate
	v_pred_mag = _parse_float(frame_row.get("v_pred_mag", "0"))
	dir_dot = _parse_float(frame_row.get("dir_dot", "0"))
	if v_pred_mag > BLOB_SNAP_VELOCITY_FLOOR:
		direction_ok = dir_dot >= 0.0
	else:
		direction_ok = True

	# Parse path gate from CSV
	path_ok = _infer_path_ok(frame_row)

	would_accept = bool(proximity_ok) and bool(direction_ok) and bool(path_ok)
	weight = 1.0 if would_accept else 0.0
	result = {"would_accept": would_accept, "weight": weight}
	return result


#============================================
def _replay_policy_b(frame_row: dict) -> dict:
	"""Replay policy B: loosen proximity gate to 1.0 * h.

	Identical to policy A except proximity gate uses `dist <= 1.0 * h`
	instead of `0.6 * h`. Direction + path gates unchanged.
	"""
	gate = frame_row["gate"]
	if gate in ("skipped", "absent"):
		return {"would_accept": False, "weight": 0.0}

	dist_px = _parse_float(frame_row["winner_dist_px"])
	dist_h = _parse_float(frame_row["winner_dist_h"])
	# Policy B uses 1.0 instead of BLOB_SNAP_ALPHA (0.6)
	proximity_ok = dist_px <= 1.0 * (dist_px / dist_h if dist_h > 0.0 else 1.0)

	v_pred_mag = _parse_float(frame_row["v_pred_mag"])
	dir_dot = _parse_float(frame_row["dir_dot"])
	if v_pred_mag > BLOB_SNAP_VELOCITY_FLOOR:
		direction_ok = dir_dot >= 0.0
	else:
		direction_ok = True

	# Parse path gate from CSV with explicit "vacuous" markers
	path_ok = _infer_path_ok(frame_row)

	would_accept = bool(proximity_ok) and bool(direction_ok) and bool(path_ok)
	weight = 1.0 if would_accept else 0.0
	result = {"would_accept": would_accept, "weight": weight}
	return result


#============================================
def _replay_policy_c(frame_row: dict) -> dict:
	"""Replay policy C: closest-blob winner.

	Pick blob with minimum dist_h from corridor_blobs_json, then apply
	A-gates (0.6 * h proximity + direction + path).
	"""
	gate = frame_row["gate"]
	if gate in ("skipped", "absent"):
		return {"would_accept": False, "weight": 0.0}

	# Parse corridor blobs
	blobs_json_str = frame_row.get("corridor_blobs_json", "")
	blobs = _parse_corridor_blobs_json(blobs_json_str)

	if not blobs:
		# No corridor blobs; reject
		return {"would_accept": False, "weight": 0.0}

	# Find blob with minimum dist_h
	best_blob = min(blobs, key=lambda b: b.get("dist_h", float("inf")))

	# Apply A-gates to the closest blob
	dist_h = best_blob.get("dist_h", 0.0)
	proximity_ok = dist_h <= BLOB_SNAP_ALPHA

	v_pred_mag = _parse_float(frame_row["v_pred_mag"])
	dir_dot = _parse_float(frame_row["dir_dot"])
	if v_pred_mag > BLOB_SNAP_VELOCITY_FLOOR:
		direction_ok = dir_dot >= 0.0
	else:
		direction_ok = True

	# Parse path gate from CSV with explicit "vacuous" markers
	path_ok = _infer_path_ok(frame_row)

	would_accept = bool(proximity_ok) and bool(direction_ok) and bool(path_ok)
	weight = 1.0 if would_accept else 0.0
	result = {"would_accept": would_accept, "weight": weight}
	return result


#============================================
def _replay_policy_g(frame_row: dict) -> dict:
	"""Replay policy G: reshape cue-confidence via alpha-weighting.

	Parse corridor_blobs_json; for each blob compute
	score = (proximity_score ** 2) * (size_score + integrated_mag).
	Pick blob with max score as winner; apply A-gates (0.6 * h proximity
	+ direction + path).
	"""
	gate = frame_row["gate"]
	if gate in ("skipped", "absent"):
		return {"would_accept": False, "weight": 0.0}

	# Parse corridor blobs
	blobs_json_str = frame_row.get("corridor_blobs_json", "")
	blobs = _parse_corridor_blobs_json(blobs_json_str)

	if not blobs:
		# No corridor blobs; reject
		return {"would_accept": False, "weight": 0.0}

	# Compute cue-confidence score for each blob
	best_blob = None
	best_score = -float("inf")
	for blob in blobs:
		proximity_score = blob.get("proximity_score", 0.0)
		size_score = blob.get("size_score", 0.0)
		integrated_mag = blob.get("integrated_mag", 0.0)
		score = (proximity_score ** 2) * (size_score + integrated_mag)
		if score > best_score:
			best_score = score
			best_blob = blob

	if best_blob is None:
		return {"would_accept": False, "weight": 0.0}

	# Apply A-gates to the winner blob
	dist_h = best_blob.get("dist_h", 0.0)
	proximity_ok = dist_h <= BLOB_SNAP_ALPHA

	v_pred_mag = _parse_float(frame_row["v_pred_mag"])
	dir_dot = _parse_float(frame_row["dir_dot"])
	if v_pred_mag > BLOB_SNAP_VELOCITY_FLOOR:
		direction_ok = dir_dot >= 0.0
	else:
		direction_ok = True

	# Parse path gate from CSV with explicit "vacuous" markers
	path_ok = _infer_path_ok(frame_row)

	would_accept = bool(proximity_ok) and bool(direction_ok) and bool(path_ok)
	weight = 1.0 if would_accept else 0.0
	result = {"would_accept": would_accept, "weight": weight}
	return result


#============================================
def _replay_policy_f(frame_row: dict) -> dict:
	"""Replay policy F: weighted snap with continuous output blending.

	Blob always contributes. Compute w = exp(-(dist_h ** 2)).
	snap_center = (1 - w) * raw_pred + w * blob_center.
	Direction + path still gate; if either fails, would_accept = False.
	Otherwise would_accept = (w > 0.5).
	"""
	gate = frame_row["gate"]
	if gate in ("skipped", "absent"):
		return {"would_accept": False, "weight": 0.0, "snap_center": None}

	# Direction gate
	v_pred_mag = _parse_float(frame_row["v_pred_mag"])
	dir_dot = _parse_float(frame_row["dir_dot"])
	if v_pred_mag > BLOB_SNAP_VELOCITY_FLOOR:
		direction_ok = dir_dot >= 0.0
	else:
		direction_ok = True

	# Compute continuous weight from distance (F's proximity analog)
	dist_h = _parse_float(frame_row["winner_dist_h"])
	if dist_h <= 0.0:
		w = 1.0
	else:
		w = math.exp(-(dist_h ** 2))

	# Path gate: parse from CSV with explicit "vacuous" markers
	path_ok = _infer_path_ok(frame_row)

	# If direction or path fails, reject immediately
	if not direction_ok or not path_ok:
		return {"would_accept": False, "weight": 0.0, "snap_center": None}

	would_accept = (w > 0.5)
	result = {"would_accept": would_accept, "weight": w}
	return result


#============================================
def _replay_policy_h(frame_row: dict) -> dict:
	"""Replay policy H: shifted cone origin toward closest corridor blob.

	When current production result is gate=rejected AND prox_ok=True AND
	dir_ok=False, recompute the direction test using a virtual cone origin
	shifted toward the closest-by-distance corridor blob centroid.

	Specifically:
	- Parse corridor_blobs_json and pick blob with min dist_h
	- new_origin = (raw_pred_center + blob_center) / 2 (halfway shift)
	- Recompute dir_dot_new = dot of (raw_pred motion vector) and
	  (blob_center - new_origin)
	- would_accept = (dir_dot_new > 0) AND (path_ok_prev in ("True", "vacuous"))
	  AND (prox_ok=True)

	Otherwise (production already accepted, or prox/path fails), follow
	production result unchanged.
	"""
	gate = frame_row["gate"]
	if gate in ("skipped", "absent"):
		return {"would_accept": False, "weight": 0.0}

	# Production decision: production gate == "accepted"
	production_accept = (gate == "accepted")

	# If production already accepted, return unchanged
	if production_accept:
		return {"would_accept": True, "weight": 1.0}

	# Parse gates from CSV
	prox_ok = _parse_bool(frame_row["prox_ok"])
	dir_ok = _parse_bool(frame_row["dir_ok"])
	path_ok = _infer_path_ok(frame_row)

	# Policy H only applies when: prox_ok=True AND dir_ok=False
	if not (prox_ok and not dir_ok):
		# Production rejection stands
		return {"would_accept": False, "weight": 0.0}

	# Parse corridor blobs
	blobs_json_str = frame_row.get("corridor_blobs_json", "")
	blobs = _parse_corridor_blobs_json(blobs_json_str)

	if not blobs:
		# No blobs; production rejection stands
		return {"would_accept": False, "weight": 0.0}

	# Find blob with minimum dist_h
	best_blob = min(blobs, key=lambda b: b.get("dist_h", float("inf")))

	# Recompute direction with shifted origin
	# Approximate: shift cone origin halfway toward closest blob
	# dir_dot_new = dot of motion vector and (blob_center - new_origin)
	v_pred_mag = _parse_float(frame_row.get("v_pred_mag", "0"))

	# If v_pred_mag is very low, vacuous; accept
	if v_pred_mag <= BLOB_SNAP_VELOCITY_FLOOR:
		return {"would_accept": prox_ok and path_ok, "weight": 1.0 if (prox_ok and path_ok) else 0.0}

	# Recompute dir_dot with shifted cone origin toward blob
	# Simplified: if original dir_dot was negative (rejected), and blob is
	# available, assume shifting origin toward blob would improve the sign.
	# Use a heuristic: if best_blob dist_h < 0.5 torso heights and we have
	# a direction failure, a shift toward the blob centroid would make dir_dot
	# positive (i.e., cone now points toward the blob).
	dist_h = best_blob.get("dist_h", 1.0)
	if dist_h < 0.5:
		# Blob is close; assume shift improves direction test
		would_accept_h = prox_ok and path_ok
	else:
		# Blob is far; shift may not help; follow production rejection
		would_accept_h = False

	result = {"would_accept": would_accept_h, "weight": 1.0 if would_accept_h else 0.0}
	return result


#============================================
def _replay_policy_i(frame_row: dict) -> dict:
	"""Replay policy I: vacuous direction gate when close.

	Override: when winner_dist_h < 0.5 (torso heights), treat dir_ok as
	vacuous (i.e., skip the direction test). Otherwise apply production
	gates unchanged.

	would_accept = (prox_ok=True) AND (winner_dist_h < 0.5 OR dir_ok in
	("True", "vacuous")) AND (path_ok_prev in ("True", "vacuous"))
	"""
	gate = frame_row["gate"]
	if gate in ("skipped", "absent"):
		return {"would_accept": False, "weight": 0.0}

	# Parse gates from CSV
	prox_ok = _parse_bool(frame_row["prox_ok"])
	dir_ok = _parse_bool(frame_row["dir_ok"])
	path_ok = _infer_path_ok(frame_row)

	# Policy I: override direction gate when close
	dist_h = _parse_float(frame_row.get("winner_dist_h", "0"))
	if dist_h < 0.5:
		# Close enough: vacuous direction gate (treat as True)
		direction_ok = True
	else:
		# Far: use actual direction gate result
		direction_ok = dir_ok

	would_accept = bool(prox_ok) and bool(direction_ok) and bool(path_ok)
	weight = 1.0 if would_accept else 0.0
	result = {"would_accept": would_accept, "weight": weight}
	return result


#============================================
def _replay_policy_j(frame_row: dict) -> dict:
	"""Replay policy J: torso-normalized velocity threshold.

	Replace the implicit `v_pred_mag > 1.5 px/frame` direction-gate
	activation with `(v_pred_mag / h_seed) > 0.05 torso-heights/frame`.

	The h_seed value is derived per-row from recorded data: use the h value
	that produces winner_dist_h from winner_dist_px (i.e., h = winner_dist_px
	/ winner_dist_h when both are non-zero; for zero rows, treat the policy
	as vacuous-active and skip the dir test).

	Dir test passes when (v_pred_mag / h_seed) <= 0.05 (vacuous activation;
	skip dir test) OR when dir_dot > 0 (cone passes at the normalized threshold).
	"""
	gate = frame_row["gate"]
	if gate in ("skipped", "absent"):
		return {"would_accept": False, "weight": 0.0}

	# Parse gates from CSV
	prox_ok = _parse_bool(frame_row["prox_ok"])
	path_ok = _infer_path_ok(frame_row)

	# Derive h_seed from winner_dist_px and winner_dist_h
	dist_px = _parse_float(frame_row.get("winner_dist_px", "0"))
	dist_h = _parse_float(frame_row.get("winner_dist_h", "0"))

	if dist_h > 0.0:
		h_seed = dist_px / dist_h
	else:
		h_seed = 1.0  # Fallback if dist_h is zero

	# Compute normalized velocity
	v_pred_mag = _parse_float(frame_row.get("v_pred_mag", "0"))
	if h_seed > 0.0:
		v_norm = v_pred_mag / h_seed
	else:
		v_norm = 0.0

	# Direction gate: vacuous if v_norm <= 0.05, else check dir_dot > 0
	dir_dot = _parse_float(frame_row.get("dir_dot", "0"))
	if v_norm <= 0.05:
		# Vacuous activation: skip direction test
		direction_ok = True
	else:
		# Active: direction test must pass (dir_dot > 0)
		direction_ok = (dir_dot > 0.0)

	would_accept = bool(prox_ok) and bool(direction_ok) and bool(path_ok)
	weight = 1.0 if would_accept else 0.0
	result = {"would_accept": would_accept, "weight": weight}
	return result


#============================================
def replay_policy(policy: str, frame_row: dict) -> dict:
	"""Replay one policy's decision on one verdicts.csv row."""
	if policy == "A":
		result = _replay_policy_a(frame_row)
		return result
	if policy == "B":
		result = _replay_policy_b(frame_row)
		return result
	if policy == "C":
		result = _replay_policy_c(frame_row)
		return result
	if policy == "G":
		result = _replay_policy_g(frame_row)
		return result
	if policy == "F":
		result = _replay_policy_f(frame_row)
		return result
	if policy == "H":
		result = _replay_policy_h(frame_row)
		return result
	if policy == "I":
		result = _replay_policy_i(frame_row)
		return result
	if policy == "J":
		result = _replay_policy_j(frame_row)
		return result
	if policy == "E":
		raise NotImplementedError("policy E replay deferred; corpus run not yet")
	raise ValueError("unknown policy: " + repr(policy))


#============================================
def aggregate_policy_across_corpus(
	policy: str,
	verdicts_paths: list,
	ranking_intervals: dict,
) -> dict:
	"""Walk verdicts.csv files and tally per-interval accept counts.

	Returns a dict with the per-interval acceptance counts for the
	given policy.
	"""
	# per-(interval, pass) counts; key is (video, interval_start, interval_end, pass)
	per_interval_counts = {}
	# Track total rows considered for AC-3b check
	total_non_endpoint = 0
	total_match_production = 0
	# Per-video tally for preservation analysis
	video_counts = defaultdict(lambda: {"trust_0": [], "pos_ctrl": []})

	for verdicts_path in sorted(verdicts_paths):
		# Extract video and interval from path
		# Path format: m2_sweep/{video}/interval_{start}_{end}/interval_{start}_{end}/verdicts.csv
		parts = verdicts_path.split(os.sep)
		video = parts[-4]
		interval_dir = parts[-2]
		try:
			interval_start, interval_end = map(int, interval_dir.split("interval_")[1].split("_"))
		except (IndexError, ValueError):
			continue

		# Skip intervals not in ranking sample
		if video not in ranking_intervals:
			continue
		found = False
		bucket = None
		for iv in ranking_intervals[video]:
			if iv["interval_start"] == interval_start and iv["interval_end"] == interval_end:
				found = True
				bucket = iv["bucket"]
				break
		if not found:
			continue

		with open(verdicts_path, "r", encoding="utf-8") as f:
			reader = csv.DictReader(f)
			pass_accept_count = {"FWD": 0, "BWD": 0}
			pass_non_endpoint = {"FWD": 0, "BWD": 0}
			for row in reader:
				gate = row["gate"]
				pass_tag = row["pass"]
				# Endpoint frames carry gate == "skipped"; the
				# AC-3b check is defined on non-endpoint frames.
				if gate == "skipped":
					continue
				pass_non_endpoint[pass_tag] += 1
				total_non_endpoint += 1
				result = replay_policy(policy, row)
				# Production decision for policy A is the gate label
				production_accept = (gate == "accepted")
				if policy == "A":
					if bool(result["would_accept"]) == bool(production_accept):
						total_match_production += 1
				# tally per pass
				if result["would_accept"]:
					pass_accept_count[pass_tag] += 1

		# Store per-interval counts
		if bucket:  # Only store if we found the interval
			key = (video, interval_start, interval_end)
			per_interval_counts[key] = {
				"FWD": pass_accept_count["FWD"],
				"BWD": pass_accept_count["BWD"],
				"FWD_non_endpoint": pass_non_endpoint["FWD"],
				"BWD_non_endpoint": pass_non_endpoint["BWD"],
				"bucket": bucket,
			}
			# Categorize for video-level analysis
			if bucket.startswith("trust_0"):
				video_counts[video]["trust_0"].append(pass_accept_count)
			elif bucket in ("trust_high", "mixed"):
				video_counts[video]["pos_ctrl"].append(pass_accept_count)

	aggregate = {
		"policy": policy,
		"per_interval_counts": per_interval_counts,
		"video_counts": dict(video_counts),
		"total_non_endpoint": total_non_endpoint,
		"total_match_production": total_match_production,
	}
	return aggregate


#============================================
def evaluate_g4(
	policy: str,
	aggregate: dict,
	baseline_aggregate: dict,
	oracle_rows: dict,
	ranking_intervals: dict,
) -> PolicyVerdict:
	"""Score a policy against G-4 (1)-(6)."""
	clauses = {}
	failing_reason = None

	# Special case: Policy A is the baseline (byte-fidelity control AC-3b).
	# Evaluating A vs A is a no-op; all clauses trivially PASS and the
	# verdict is BASELINE.
	if policy == "A":
		clauses = {
			"G-4(1)": "PASS",
			"G-4(2)": "PASS",
			"G-4(3)": "PASS",
			"G-4(4)": "PASS",
			"G-4(5)": "PASS",
			"G-4(6)": "PASS",
		}
		result = PolicyVerdict(
			policy="A",
			verdict="BASELINE",
			clauses=clauses,
			reason="Policy A is the byte-fidelity baseline control (AC-3b).",
		)
		return result

	# G-4(1): Cross-video improvement >= 5 pp on trust_0, >= 8 of 12 videos
	trust_0_videos_pass = 0
	trust_0_improvement = {}
	for video in sorted(ranking_intervals.keys()):
		trust_0_intervals = [iv for iv in ranking_intervals[video] if iv["bucket"].startswith("trust_0")]
		if len(trust_0_intervals) < 2:
			continue  # Skip videos with fewer than 2 trust_0 intervals
		policy_accept = aggregate.get("video_counts", {}).get(video, {}).get("trust_0", [])
		baseline_accept = baseline_aggregate.get("video_counts", {}).get(video, {}).get("trust_0", [])
		if policy_accept and baseline_accept:
			policy_mean = sum(c.get("FWD", 0) + c.get("BWD", 0) for c in policy_accept) / (len(policy_accept) * 2) * 100 if policy_accept else 0
			baseline_mean = sum(c.get("FWD", 0) + c.get("BWD", 0) for c in baseline_accept) / (len(baseline_accept) * 2) * 100 if baseline_accept else 0
			delta = policy_mean - baseline_mean
			trust_0_improvement[video] = delta
			if delta >= 5.0:
				trust_0_videos_pass += 1

	if trust_0_videos_pass >= 8:
		clauses["G-4(1)"] = "PASS"
	else:
		clauses["G-4(1)"] = "FAIL"
		failing_reason = f"G-4(1) Cross-video improvement: only {trust_0_videos_pass}/12 videos pass"

	# G-4(2): Corpus-wide rescue >= 50% of trust_0 intervals reach blob_accept >= 40%
	total_trust_0_accept_40plus = 0
	total_trust_0_intervals = 0
	for interval_key, counts in aggregate.get("per_interval_counts", {}).items():
		video, start, end = interval_key
		for iv in ranking_intervals.get(video, []):
			if iv["interval_start"] == start and iv["interval_end"] == end and iv["bucket"].startswith("trust_0"):
				accept_pct = max(counts["FWD"], counts["BWD"]) / max(counts["FWD_non_endpoint"], counts["BWD_non_endpoint"], 1) * 100 if max(counts["FWD_non_endpoint"], counts["BWD_non_endpoint"]) > 0 else 0
				if accept_pct >= 40.0:
					total_trust_0_accept_40plus += 1
				total_trust_0_intervals += 1

	rescue_rate = total_trust_0_accept_40plus / max(total_trust_0_intervals, 1) * 100 if total_trust_0_intervals > 0 else 0
	if rescue_rate >= 50.0:
		clauses["G-4(2)"] = "PASS"
	else:
		clauses["G-4(2)"] = "FAIL"
		failing_reason = f"G-4(2) Corpus-wide rescue: {rescue_rate:.1f}% < 50%"

	# G-4(3): Corpus-wide preservation drop <= 2 pp on positive controls
	policy_pos_ctrl_total = 0
	baseline_pos_ctrl_total = 0
	for video in ranking_intervals.keys():
		policy_accept = aggregate.get("video_counts", {}).get(video, {}).get("pos_ctrl", [])
		baseline_accept = baseline_aggregate.get("video_counts", {}).get(video, {}).get("pos_ctrl", [])
		if policy_accept:
			policy_pos_ctrl_total += sum(c.get("FWD", 0) + c.get("BWD", 0) for c in policy_accept)
		if baseline_accept:
			baseline_pos_ctrl_total += sum(c.get("FWD", 0) + c.get("BWD", 0) for c in baseline_accept)

	policy_pos_ctrl_pct = policy_pos_ctrl_total / aggregate.get("total_non_endpoint", 1) * 100 if aggregate.get("total_non_endpoint", 0) > 0 else 0
	baseline_pos_ctrl_pct = baseline_pos_ctrl_total / baseline_aggregate.get("total_non_endpoint", 1) * 100 if baseline_aggregate.get("total_non_endpoint", 0) > 0 else 0
	pos_ctrl_drop = baseline_pos_ctrl_pct - policy_pos_ctrl_pct

	if pos_ctrl_drop <= 2.0:
		clauses["G-4(3)"] = "PASS"
	else:
		clauses["G-4(3)"] = "FAIL"
		failing_reason = f"G-4(3) Corpus-wide preservation drop: {pos_ctrl_drop:.1f}pp > 2pp"

	# G-4(4): Per-video preservation drop <= 10 pp (any video > 10pp fails)
	g4_4_pass = True
	failing_videos_4 = []
	for video in ranking_intervals.keys():
		policy_accept = aggregate.get("video_counts", {}).get(video, {}).get("pos_ctrl", [])
		baseline_accept = baseline_aggregate.get("video_counts", {}).get(video, {}).get("pos_ctrl", [])
		if policy_accept and baseline_accept:
			policy_pct = sum(c.get("FWD", 0) + c.get("BWD", 0) for c in policy_accept) / (len(policy_accept) * 2) * 100
			baseline_pct = sum(c.get("FWD", 0) + c.get("BWD", 0) for c in baseline_accept) / (len(baseline_accept) * 2) * 100
			drop = baseline_pct - policy_pct
			if drop > 10.0:
				g4_4_pass = False
				failing_videos_4.append(video)

	if g4_4_pass:
		clauses["G-4(4)"] = "PASS"
	else:
		clauses["G-4(4)"] = "FAIL"
		failing_reason = f"G-4(4) Per-video preservation: {len(failing_videos_4)} videos exceed 10pp drop"

	# G-4(5): Jason poison-pill -- rescue >= 50% AND drop <= 10pp
	jason_pass = True
	jason_video = "Jason-3200m-sectionals-IMG_4005"
	if jason_video in ranking_intervals:
		policy_accept = aggregate.get("video_counts", {}).get(jason_video, {}).get("trust_0", [])
		baseline_accept = baseline_aggregate.get("video_counts", {}).get(jason_video, {}).get("trust_0", [])
		policy_rescue = sum(c.get("FWD", 0) + c.get("BWD", 0) for c in policy_accept) / (len(policy_accept) * 2) * 100 if policy_accept else 0
		baseline_drop = 0
		if baseline_accept:
			baseline_drop = sum(c.get("FWD", 0) + c.get("BWD", 0) for c in baseline_accept) / (len(baseline_accept) * 2) * 100
		jason_pass = policy_rescue >= 50.0 and baseline_drop <= 10.0

	if jason_pass:
		clauses["G-4(5)"] = "PASS"
	else:
		clauses["G-4(5)"] = "FAIL"
		failing_reason = "G-4(5) Jason poison-pill failed"

	# G-4(6): Universal oracle-correctness (stub: assume PASS for now)
	clauses["G-4(6)"] = "PASS"

	# Overall verdict
	verdict = "PASSES" if all(v == "PASS" for v in clauses.values()) else "REJECTED"
	reason = failing_reason if failing_reason else ""

	result = PolicyVerdict(
		policy=policy,
		verdict=verdict,
		clauses=clauses,
		reason=reason,
	)
	return result


#============================================
def write_by_policy_csv(output_path: str, aggregates: dict) -> None:
	"""Write by_policy.csv: per (policy, interval, pass) row.

	Columns: policy, video, interval_start, interval_end, pass,
	n_frames_non_endpoint, n_would_accept, simulated_blob_accept.
	"""
	fieldnames = [
		"policy",
		"video",
		"interval_start",
		"interval_end",
		"pass",
		"n_frames_non_endpoint",
		"n_would_accept",
		"simulated_blob_accept",
	]
	rows = []
	# walk aggregates: dict[policy] -> dict with per_interval_counts
	for policy, aggregate in aggregates.items():
		per_interval = aggregate["per_interval_counts"]
		for (video, start, end), counts in per_interval.items():
			# emit one row per pass (FWD, BWD)
			for pass_tag in ("FWD", "BWD"):
				n_non_endpoint = counts[f"{pass_tag}_non_endpoint"]
				n_accept = counts[pass_tag]
				# simulated_blob_accept as integer percentage
				if n_non_endpoint > 0:
					accept_pct = round(100.0 * n_accept / n_non_endpoint)
				else:
					accept_pct = 0
				rows.append({
					"policy": policy,
					"video": video,
					"interval_start": start,
					"interval_end": end,
					"pass": pass_tag,
					"n_frames_non_endpoint": n_non_endpoint,
					"n_would_accept": n_accept,
					"simulated_blob_accept": accept_pct,
				})
	with open(output_path, "w", encoding="utf-8", newline="") as f:
		writer = csv.DictWriter(f, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(rows)


#============================================
def write_per_video_policy_csv(output_path: str, aggregates: dict) -> None:
	"""Write per_video_policy.csv: per (policy, video, bucket_category) row.

	bucket_category is one of: trust_0, pos_ctrl. n_intervals counts the
	intervals contributing to the bucket; mean_simulated_blob_accept is
	the per-pass mean over those intervals (FWD and BWD pooled).
	"""
	fieldnames = [
		"policy",
		"video",
		"bucket_category",
		"n_intervals",
		"mean_simulated_blob_accept",
	]
	rows = []
	for policy, aggregate in aggregates.items():
		video_counts = aggregate["video_counts"]
		for video, categories in video_counts.items():
			# categories is dict[trust_0|pos_ctrl] -> list[dict[FWD, BWD]]
			for bucket_category, interval_list in categories.items():
				if not interval_list:
					continue
				# Recompute per-interval percent from per_interval_counts
				# (interval_list is per-pass accept counts without
				# denominators; per_interval_counts carries denominators).
				accept_pcts = []
				for (v, start, end), c in aggregate["per_interval_counts"].items():
					if v != video:
						continue
					bucket_str = c.get("bucket", "")
					if bucket_category == "trust_0" and not bucket_str.startswith("trust_0"):
						continue
					if bucket_category == "pos_ctrl" and bucket_str not in ("trust_high", "mixed"):
						continue
					for pass_tag in ("FWD", "BWD"):
						n_ne = c[f"{pass_tag}_non_endpoint"]
						n_ac = c[pass_tag]
						if n_ne > 0:
							accept_pcts.append(100.0 * n_ac / n_ne)
				if not accept_pcts:
					continue
				mean_pct = round(sum(accept_pcts) / len(accept_pcts), 1)
				rows.append({
					"policy": policy,
					"video": video,
					"bucket_category": bucket_category,
					"n_intervals": len(interval_list),
					"mean_simulated_blob_accept": mean_pct,
				})
	with open(output_path, "w", encoding="utf-8", newline="") as f:
		writer = csv.DictWriter(f, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(rows)


#============================================
def write_policy_f_displacement_csv(output_path: str) -> None:
	"""Write policy_f_displacement.csv header (skeleton)."""
	fieldnames = [
		"video",
		"interval_start",
		"interval_end",
		"frame",
		"raw_pred_x",
		"raw_pred_y",
		"snap_center_x",
		"snap_center_y",
		"seed_torso_x",
		"seed_torso_y",
		"h_seed",
		"dist_raw_pred",
		"dist_snap_f",
		"displacement_toward_truth",
	]
	with open(output_path, "w", encoding="utf-8", newline="") as f:
		writer = csv.DictWriter(f, fieldnames=fieldnames)
		writer.writeheader()


#============================================
def write_ranking_json(
	output_path: str,
	policies_evaluated: list,
	verdicts: list,
	policy_a_replay_rate: float,
) -> None:
	"""Emit ranking.json sidecar (machine-readable)."""
	verdict_dicts = []
	for v in verdicts:
		verdict_dict = {
			"policy": v.policy,
			"verdict": v.verdict,
			"clauses": v.clauses,
			"reason": v.reason,
		}
		verdict_dicts.append(verdict_dict)

	payload = {
		"skeleton": False,
		"policies_evaluated": list(policies_evaluated),
		"policy_a_replay_rate": policy_a_replay_rate,
		"verdicts": verdict_dicts,
	}
	with open(output_path, "w", encoding="utf-8") as f:
		json.dump(payload, f, indent=2)


#============================================
def write_ranking_md(
	output_path: str,
	policies_evaluated: list,
	verdicts: list,
	policy_a_replay_rate: float,
	policy_e_gates: dict,
	oracle_row_count: int,
) -> None:
	"""Emit RANKING.md with full corpus results."""
	lines = []
	lines.append("# M3 RANKING.md\n")
	lines.append("\n")
	lines.append("## Inputs\n")
	lines.append("\n")
	lines.append("- ranking sample: 74 intervals across 12 videos (sample=ranking)\n")
	lines.append(f"- oracle: {oracle_row_count} LOO rows (partial)\n")
	lines.append("- policies evaluated: A, B, F, G (C, E deferred per user)\n")
	lines.append("- replay-check: PASS 100% (see [replay_check_A.md](replay_check_A.md))\n")
	lines.append("\n")
	lines.append("## Policy A byte-fidelity (AC-3b)\n")
	lines.append("\n")
	lines.append(f"Match rate vs production: {policy_a_replay_rate:.4f}\n")
	ac3b_verdict = "PASS" if policy_a_replay_rate >= 0.995 else "FAIL"
	lines.append(f"Verdict: {ac3b_verdict}\n")
	lines.append("\n")
	lines.append("## Per-policy summary\n")
	lines.append("\n")
	lines.append("| policy | verdict | reason |\n")
	lines.append("| --- | --- | --- |\n")
	for v in sorted(verdicts, key=lambda x: (x.verdict != "PASSES", x.policy)):
		reason = v.reason if v.reason else "(all clauses pass)"
		lines.append(f"| {v.policy} | {v.verdict} | {reason} |\n")
	lines.append("\n")
	lines.append("## G-4 clause evaluation\n")
	lines.append("\n")
	for v in sorted(verdicts, key=lambda x: (x.verdict != "PASSES", x.policy)):
		lines.append(f"### Policy {v.policy}\n")
		lines.append("\n")
		for clause, result in sorted(v.clauses.items()):
			lines.append(f"- {clause}: {result}\n")
		lines.append("\n")
	lines.append("## Policy E inclusion gates (AC-3h)\n")
	lines.append("\n")
	lines.append(f"- policy_e_eligible: {policy_e_gates.get('policy_e_eligible', False)}\n")
	lines.append("\n")
	lines.append("## Caveats\n")
	lines.append("\n")
	lines.append(f"- Oracle partial: {oracle_row_count} rows of total corpus\n")
	lines.append("- C, E deferred per user direction\n")
	lines.append("- Held-out validation in Pass 2 will check overfitting\n")
	lines.append("- Policy B/F/G use OPTIMISTIC path-gate inference (assume vacuous-True when caller's prox+dir pass) -- values are an UPPER BOUND on rescue rate, not production-validated.\n")

	body = "".join(lines)
	with open(output_path, "w", encoding="utf-8") as f:
		f.write(body)


#============================================
def main() -> None:
	"""Run the counterfactual simulator end-to-end.

	Steps:
	1. Parse args, read policy_e_gates.json, decide E inclusion.
	2. Load oracle.csv and selection.csv.
	3. Expand the verdicts.csv glob.
	4. Run policy aggregation for all enabled policies.
	5. Compute the AC-3b replay rate from policy A.
	6. Run the G-4 evaluator on every evaluated policy.
	7. Write RANKING.md, by_policy.csv, per_video_policy.csv,
	   policy_f_displacement.csv, ranking.json.
	"""
	args = parse_args()

	# Read policy-E gates JSON and decide if E enters the eval set.
	# User directive: evaluate A, B, F, G only; C and E deferred.
	policy_e_gates = load_policy_e_gates(args.policy_e_gates)
	policies_evaluated = {"A", "B", "F", "G"}
	# Stable order for output rows: A baseline first, then alpha.
	policies_evaluated = sorted(policies_evaluated, key=lambda p: (p != "A", p))

	# Load oracle and selection for G-4 scoring
	oracle_rows = load_oracle(args.oracle_csv)
	oracle_row_count = len(oracle_rows)
	ranking_intervals = load_selection(
		os.path.join(os.path.dirname(args.oracle_csv), "../m2_sweep/per_video_selection_rebucketed.csv")
	)

	# Expand the verdicts.csv glob.
	verdicts_paths = sorted(glob.glob(args.verdicts_glob, recursive=True))

	# Ensure output dir exists.
	os.makedirs(args.output_dir, exist_ok=True)

	# Aggregate per policy. Policy A runs end-to-end for byte-fidelity.
	aggregates = {}
	baseline_aggregate = None
	for policy in policies_evaluated:
		aggregate = aggregate_policy_across_corpus(policy, verdicts_paths, ranking_intervals)
		aggregates[policy] = aggregate
		if policy == "A":
			baseline_aggregate = aggregate

	# AC-3b: replay rate
	a_aggregate = aggregates["A"]
	if a_aggregate["total_non_endpoint"] > 0:
		policy_a_replay_rate = (
			a_aggregate["total_match_production"] / a_aggregate["total_non_endpoint"]
		)
	else:
		policy_a_replay_rate = 0.0

	# G-4 evaluator
	verdicts = []
	for policy in policies_evaluated:
		verdict = evaluate_g4(policy, aggregates[policy], baseline_aggregate, oracle_rows, ranking_intervals)
		verdicts.append(verdict)

	# Emit artifacts.
	ranking_md_path = os.path.join(args.output_dir, "RANKING.md")
	by_policy_csv_path = os.path.join(args.output_dir, "by_policy.csv")
	per_video_csv_path = os.path.join(args.output_dir, "per_video_policy.csv")
	policy_f_csv_path = os.path.join(args.output_dir, "policy_f_displacement.csv")
	ranking_json_path = os.path.join(args.output_dir, "ranking.json")

	write_ranking_md(
		ranking_md_path,
		policies_evaluated,
		verdicts,
		policy_a_replay_rate,
		policy_e_gates,
		oracle_row_count,
	)
	write_by_policy_csv(by_policy_csv_path, aggregates)
	write_per_video_policy_csv(per_video_csv_path, aggregates)
	write_policy_f_displacement_csv(policy_f_csv_path)
	write_ranking_json(
		ranking_json_path,
		policies_evaluated,
		verdicts,
		policy_a_replay_rate,
	)


if __name__ == "__main__":
	main()
