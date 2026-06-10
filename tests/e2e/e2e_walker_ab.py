#!/usr/bin/env python3
"""M4 A/B harness: Hermite-only vs Stage 4 walker, held-out-seed accuracy.

Non-browser E2E (see docs/E2E_TESTS.md). This is the corrected M4 A/B
evaluation. The first version (4 fixed intervals, FWD/BWD agreement metric)
was a selection + metric artifact: it reused the blob-walk baseline's four
fixed intervals and classified on FWD/BWD agreement, which is structurally
biased toward Hermite (Hermite's two passes mirror one fitted curve, so they
agree by construction; the walker's two passes are independent per contract
C9, so honest disagreement is penalized as "regression").

Corrected design:

  SELECTION -- the established outdoor corpus: the six videos in
  data/outdoor_corpus.txt, 20 random intervals per video (120 target),
  restricted to DURING-RACE (a.k.a. post-start: start_frame > race_start_frame,
  contract C4 separates this from the pre-race stationary phase that Stage 3b
  synthesis owns, NOT the walker) and to seeds that are human-VISIBLE torso
  boxes on BOTH ends (status == "visible"; not_in_frame / approximate /
  partial excluded). Selection reuses walk_io.load_race_start_frame (the same
  race_start_frame the corpus driver uses) and the same fixed-seed sampling
  shape as walk_util.select_random_visible. A fixed --random-seed makes the
  sample reproducible.

  METRIC -- an INDEPENDENT accuracy proxy, not FWD/BWD agreement. Where three
  consecutive during-race VISIBLE seeds A, B, C exist, the interior human seed
  B is HELD OUT. The merged interval A->C is solved both ways (Hermite-only
  with blob off, walker-on with blob on). Each method's solved torso box at
  frame B is compared to the held-out human seed B by center distance,
  normalized to torso-width units (contract C2). The held-out human seed is
  ground truth, independent of both methods, so the distance delta classifies
  honestly:

    rescued     -- walker materially closer to truth than Hermite
    preserved   -- walker independently matches a good Hermite result (both
                   close to truth, or walker within tolerance of Hermite).
                   PRESERVED COUNTS AS SUCCESS: the walker reaching the right
                   answer on its own image evidence (it never reads raw_pred;
                   the no-Hermite import gate + WP-5a data-boundary test
                   enforce that independence) is the goal, even when Hermite
                   was already fine.
    regressed   -- walker materially worse than Hermite.
    needs_review-- both methods far from truth (ambiguous interval).

  success = rescued + preserved.

This is a runner, not a pytest module (it opens real videos and decodes real
frames). Run:

  source source_me.sh && python3 tests/e2e/e2e_walker_ab.py
  source source_me.sh && python3 tests/e2e/e2e_walker_ab.py --random-seed 12345 -n 20
"""

# Standard Library
import os
import sys
import time
import random
import argparse

# Resolve repo root: tests/e2e/ -> repo root is three levels up.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BLOB_WALK_DIR = os.path.join(_REPO_ROOT, 'tools', 'blob_walk_v2')
if _BLOB_WALK_DIR not in sys.path:
	sys.path.insert(0, _BLOB_WALK_DIR)
import walk_paths
walk_paths.setup()

# local repo modules (blob_walk_v2 + track_runner, imported by bare name)
import blob_walk.walk_io as walk_io
import interval_solver


#============================================
# The established outdoor corpus (data/outdoor_corpus.txt), already resolved to
# the basenames open_walker_reader accepts. Kept as a constant so the harness
# is a single-purpose runner; the corpus file is the source of truth and this
# mirror is asserted against it at startup.
CORPUS_FILE = os.path.join(_REPO_ROOT, "data", "outdoor_corpus.txt")
CORPUS_VIDEOS = (
	"IMG_3830.mkv",
	"IMG_3823.mkv",
	"Jason-3200m-sectionals-IMG_4005.mkv",
	"Lyra-Hersey-800m-IMG_3882.mkv",
	"Conant-4x400-2026_April_15.mkv",
	"Lyra-Wheeling-IMG_3912.mkv",
)

# Standard corpus sampling: 20 random during-race visible triples per video.
DEFAULT_SAMPLE_N = 20
DEFAULT_RANDOM_SEED = 12345

# Classification tolerances, expressed in TORSO-WIDTH units (contract C2), not
# raw pixels. delta = walker_err - hermite_err (both in torso widths); a
# positive delta means the walker is farther from the held-out human truth.
RESCUE_TORSO = 0.15    # walker beats Hermite by >= this many torso widths
REGRESS_TORSO = 0.15   # walker worse than Hermite by >= this many torso widths
# Both methods "far" from truth -> ambiguous interval (needs_review), not a
# verdict on the walker. A torso width is the unit of runner scale.
FAR_TORSO = 1.0


#============================================
def _assert_corpus_matches_file() -> None:
	"""Loudly fail if the constant corpus drifts from data/outdoor_corpus.txt."""
	with open(CORPUS_FILE) as handle:
		file_basenames = []
		for raw in handle:
			line = raw.strip()
			if not line or line.startswith("#"):
				continue
			# corpus lines are TRACK_VIDEOS/<name>.mkv; keep the basename
			file_basenames.append(os.path.basename(line))
	if tuple(file_basenames) != CORPUS_VIDEOS:
		raise RuntimeError(
			"CORPUS_VIDEOS drifted from data/outdoor_corpus.txt:\n"
			f"  file:     {file_basenames}\n"
			f"  constant: {list(CORPUS_VIDEOS)}"
		)


#============================================
def _seeds_scene(seeds: list, scene_transform) -> list:
	"""Convert seeds to scene tuples (frame, sx, sy, sw, sh) for scoring.

	not_in_frame seeds carry no torso box (no cx/cy/w/h) and are not position
	anchors for Hermite curve fitting, so they are skipped here -- matching the
	walker pipeline, which treats only boxed seeds as walk anchors.
	"""
	all_seeds_scene = []
	for seed in seeds:
		if "cx" not in seed:
			continue
		frame_index = int(seed["frame_index"])
		sx, sy, sw, sh = scene_transform.pixel_box_to_scene(
			frame_index, float(seed["cx"]), float(seed["cy"]),
			float(seed["w"]), float(seed["h"]),
		)
		all_seeds_scene.append((frame_index, sx, sy, sw, sh))
	return all_seeds_scene


#============================================
def select_random_visible_triples(seeds: list, race_start_frame: int,
		n: int, rng: random.Random) -> list:
	"""Pick up to n random A,B,C triples of consecutive during-race visible seeds.

	Mirrors walk_util.select_random_visible's shape (filter, then fixed-seed
	rng.sample, then sort by left frame for stable output), but operates on
	consecutive seed triples so an interior human seed B can be held out.

	A triple qualifies when all three seeds are status == "visible" and the
	left seed A is strictly after race_start_frame (during-race / post-start,
	contract C4). Because seeds are sorted ascending by frame_index, A being
	during-race implies B and C are too.

	Args:
		seeds: Seed dicts sorted ascending by frame_index.
		race_start_frame: Frame where the race begins; intervals at or before
			it are pre-race and excluded.
		n: Maximum number of triples to return.
		rng: random.Random instance (seeded for reproducibility).

	Returns:
		list of (A, B, C) seed-dict tuples sorted by A["frame_index"].
	"""
	qualifying = []
	for i in range(len(seeds) - 2):
		a, b, c = seeds[i], seeds[i + 1], seeds[i + 2]
		if a["frame_index"] <= race_start_frame:
			continue
		if not (a["status"] == "visible" and b["status"] == "visible"
				and c["status"] == "visible"):
			continue
		qualifying.append((a, b, c))
	if len(qualifying) <= n:
		chosen = qualifying
	else:
		chosen = rng.sample(qualifying, n)
	chosen_sorted = sorted(chosen, key=lambda triple: triple[0]["frame_index"])
	return chosen_sorted


#============================================
def _solved_box_at_frame(result: dict, frame_index: int) -> dict:
	"""Read the solved blended box at frame_index from a solve result.

	The blended_path is index-aligned with start_frame + i (no per-frame
	frame_index key), so the held-out frame's slot is frame_index -
	start_frame.
	"""
	start_frame = int(result["start_frame"])
	idx = frame_index - start_frame
	blended_path = result["blended_path"]
	if idx < 0 or idx >= len(blended_path):
		raise RuntimeError(
			f"held-out frame {frame_index} outside solved span "
			f"[{start_frame}, {start_frame + len(blended_path) - 1}]"
		)
	return blended_path[idx]


#============================================
def _torso_err(solved_box: dict, human_seed: dict) -> float:
	"""Center distance from solved box to human seed, in torso-width units (C2)."""
	dx = float(solved_box["cx"]) - float(human_seed["cx"])
	dy = float(solved_box["cy"]) - float(human_seed["cy"])
	dist_px = (dx * dx + dy * dy) ** 0.5
	# torso width is the runner-scale unit; human seed w is the held-out truth
	torso_w = float(human_seed["w"])
	return dist_px / torso_w


#============================================
def _classify(hermite_err: float, walker_err: float) -> str:
	"""Classify the walker outcome from held-out-truth error (torso widths).

	preserved counts as success: it covers both "both close to truth" and
	"walker independently within tolerance of an already-good Hermite". A
	regression is only called when the walker is materially worse AND Hermite
	was not itself far from truth. When both methods miss badly the interval is
	ambiguous (needs_review), not a verdict on the walker.
	"""
	delta = walker_err - hermite_err
	if hermite_err >= FAR_TORSO and walker_err >= FAR_TORSO:
		return "needs_review"
	if delta <= -RESCUE_TORSO:
		return "rescued"
	if delta >= REGRESS_TORSO:
		return "regressed"
	# small swing and at least one method is reasonably close: the walker
	# independently arrived at (or matched) a good answer.
	return "preserved"


#============================================
def _solve_held_out(reader, scene_transform, all_seeds_scene, fps,
		seed_a, seed_c, blob_pass: bool) -> dict:
	"""Solve the merged A->C interval one way and return the solve result.

	The baseline runs pure Hermite (blob_pass off: no decode, pure interval
	geometry); the walker path runs with blob_pass on. The walker never reads
	raw_pred (Hermite independence; enforced by the no-Hermite import gate and
	the WP-5a data-boundary test).
	"""
	result = interval_solver.solve_interval_analytical(
		seed_a, seed_c, scene_transform, all_seeds_scene, fps,
		reader=reader,
		blob_pass=blob_pass,
	)
	return result


#============================================
def run_ab(sample_n: int, random_seed: int, per_video_budget_s: float) -> None:
	"""Run the held-out-seed A/B over the corpus and print a comparison table."""
	_assert_corpus_matches_file()

	print("# M4 walker A/B (held-out-seed accuracy, torso-width units)")
	print(f"# random_seed={random_seed} sample_n_per_video={sample_n} "
		f"per_video_budget_s={per_video_budget_s}")
	print("video,a_frame,b_frame,c_frame,hermite_err,walker_err,delta,"
		"classification")

	counts = {"rescued": 0, "preserved": 0, "regressed": 0, "needs_review": 0}
	per_video_counts = {}
	evaluated = 0
	attempted = 0

	for video in CORPUS_VIDEOS:
		reader, probe_info = walk_io.open_walker_reader(video)
		seeds_view = walk_io.load_walker_seeds_view(video, reader.geometry)
		seeds_view.assert_geometry_match(reader.geometry)
		scene_transform = walk_io.load_walker_scene_transform(video)
		race_start_frame = walk_io.load_race_start_frame(video)
		fps = float(probe_info["fps"])
		# sort seeds ascending so triples are consecutive in frame order
		seeds = sorted(seeds_view.seeds, key=lambda s: s["frame_index"])
		all_seeds_scene = _seeds_scene(seeds, scene_transform)

		rng = random.Random(random_seed)
		triples = select_random_visible_triples(
			seeds, race_start_frame, sample_n, rng)

		vcounts = {"rescued": 0, "preserved": 0, "regressed": 0,
			"needs_review": 0, "skipped_budget": 0}
		video_start = time.time()
		for seed_a, seed_b, seed_c in triples:
			attempted += 1
			# budget guard: stop this video if it is overrunning, so a slow
			# decode video cannot starve the rest of the corpus.
			if time.time() - video_start > per_video_budget_s:
				vcounts["skipped_budget"] += 1
				continue
			hermite_res = _solve_held_out(
				reader, scene_transform, all_seeds_scene, fps,
				seed_a, seed_c, blob_pass=False)
			walker_res = _solve_held_out(
				reader, scene_transform, all_seeds_scene, fps,
				seed_a, seed_c, blob_pass=True)

			b_frame = int(seed_b["frame_index"])
			hermite_err = _torso_err(
				_solved_box_at_frame(hermite_res, b_frame), seed_b)
			walker_err = _torso_err(
				_solved_box_at_frame(walker_res, b_frame), seed_b)
			delta = walker_err - hermite_err
			classification = _classify(hermite_err, walker_err)

			counts[classification] += 1
			vcounts[classification] += 1
			evaluated += 1
			print(
				f"{video},{seed_a['frame_index']},{b_frame},"
				f"{seed_c['frame_index']},{hermite_err:.3f},{walker_err:.3f},"
				f"{delta:+.3f},{classification}"
			)
			sys.stdout.flush()

		per_video_counts[video] = vcounts
		reader.close()
		elapsed = time.time() - video_start
		print(f"# [{video}] done in {elapsed:.1f}s: " + ", ".join(
			f"{key}={vcounts[key]}" for key in sorted(vcounts)))
		sys.stdout.flush()

	# ---- distribution headline ----
	success = counts["rescued"] + counts["preserved"]
	print("")
	print("# ===== DISTRIBUTION HEADLINE =====")
	print(f"# evaluated {evaluated} of {attempted} attempted during-race "
		f"visible triples across {len(CORPUS_VIDEOS)} videos")
	print(f"# success (rescued+preserved) = {success}/{evaluated}  "
		f"(rescued={counts['rescued']}, preserved={counts['preserved']})")
	print(f"# regressed = {counts['regressed']}/{evaluated}")
	print(f"# needs_review = {counts['needs_review']}/{evaluated}")
	print("# per-video counts:")
	for video in CORPUS_VIDEOS:
		vc = per_video_counts[video]
		print(f"#   {video}: " + ", ".join(
			f"{key}={vc[key]}" for key in sorted(vc)))


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description="M4 walker A/B held-out-seed accuracy over the outdoor corpus.")
	parser.add_argument(
		'-n', '--sample-n', dest='sample_n', type=int, default=DEFAULT_SAMPLE_N,
		help="random during-race visible triples per video (default 20)")
	parser.add_argument(
		'-s', '--random-seed', dest='random_seed', type=int,
		default=DEFAULT_RANDOM_SEED,
		help="fixed RNG seed for reproducible sampling")
	parser.add_argument(
		'-b', '--per-video-budget', dest='per_video_budget_s', type=float,
		default=1800.0,
		help="seconds budget per video before skipping remaining triples")
	args = parser.parse_args()
	return args


#============================================
def main() -> None:
	"""Entry point."""
	args = parse_args()
	run_ab(args.sample_n, args.random_seed, args.per_video_budget_s)


#============================================
if __name__ == '__main__':
	main()
