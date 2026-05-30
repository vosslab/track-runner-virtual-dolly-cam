#!/usr/bin/env python3
"""Deterministic reproduction of bug #101 (degenerate ROI w=0 at bin>1).

Non-browser E2E gate (see docs/E2E_TESTS.md). Reproduces bug #101: at
bin_factor > 1 the blob_walk_v2 batch entry point (make_walk_html_v2.process_video)
feeds SOURCE-pixel seeds into walk_driver.run_interval_walk, which then builds a
SOURCE-scale roi_override and hands it (plus a SOURCE pred_center) to
residual_motion.observe_blob_at. observe_blob_at clamps that source-scale ROI
against the PROCESSED frame width, collapsing the x-range to zero, and
compute_residual_for_frame raises
  ValueError: degenerate ROI (h=..., w=0) at frame ...; prediction off-frame?

Fixture: Jason-3200m-sectionals-IMG_4005 (source 2816x1584 -> bin_factor=2,
processed 1408x784), interval [5781, 5828]. Seed cx=1870 (source) exceeds the
processed width 1408, so the bootstrap-step ROI collapses on the first observe.

Exit codes (so the gate flips green when the typed-coord M2 fix lands):
  - 1: the degenerate-ROI ValueError fired (bug #101 still present, EXPECTED today)
  - 0: the walk completed without the degenerate-ROI raise (bug #101 fixed)
  - 2: an UNEXPECTED error (wiring/fixture problem, not bug #101)

This file is an E2E runner (tests/e2e/, e2e_*.py), not a pytest module.
Run it directly:
  source source_me.sh && python3 tests/e2e/e2e_bug_101_degenerate_roi.py
"""

# Standard Library
import os
import sys
import pathlib

# Resolve repo root from this file: tests/e2e/ -> repo root is three levels up.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BLOB_WALK_DIR = os.path.join(_REPO_ROOT, 'tools', 'blob_walk_v2')
# blob_walk_v2 modules (root, core/, render/) and track_runner modules import
# each other by bare name. Put the package root on sys.path so the bare import
# walk_paths resolves, then call walk_paths.setup() to wire up track_runner,
# tests, repo root, and the core/ and render/ subdirs.
if _BLOB_WALK_DIR not in sys.path:
	sys.path.insert(0, _BLOB_WALK_DIR)
import walk_paths
walk_paths.setup()

# local repo modules (blob_walk_v2 + track_runner, imported by bare name)
import walk_io
import walk_driver

# Fixture: bin_factor=2 video with a near-right-edge seed.
VIDEO_BASENAME = 'Jason-3200m-sectionals-IMG_4005'
LEFT_FRAME = 5781
RIGHT_FRAME = 5828


#============================================
def reproduce_degenerate_roi() -> None:
	"""Drive the batch call path that triggers bug #101.

	Mirrors make_walk_html_v2.process_video's wiring: load SOURCE-pixel seeds
	via load_walker_seeds (NOT the processed SeedsView), build the interval, and
	call run_interval_walk with the source seed dicts. Raises ValueError
	(degenerate ROI) today.
	"""
	# SOURCE-pixel seeds: this is the batch-path loader. walk_driver.main() uses
	# the PROCESSED SeedsView instead, which is why isolated walk_driver.main
	# runs did not crash.
	seeds_dict = walk_io.load_walker_seeds(VIDEO_BASENAME)
	reader, probe_info = walk_io.open_walker_reader(VIDEO_BASENAME)
	scene_transform = walk_io.load_walker_scene_transform(VIDEO_BASENAME)
	race_start_frame = walk_io.load_race_start_frame(VIDEO_BASENAME)

	intervals = walk_io.enumerate_seed_to_seed_intervals(seeds_dict, race_start_frame)
	# Select the known-crashing interval by its seed-pair frame indices.
	target = [
		i for i in intervals
		if i.left_seed['frame_index'] == LEFT_FRAME
		and i.right_seed['frame_index'] == RIGHT_FRAME
	]
	if not target:
		reader.close()
		raise RuntimeError(
			f'fixture interval [{LEFT_FRAME}, {RIGHT_FRAME}] not found in '
			f'{VIDEO_BASENAME} seeds; cannot reproduce bug #101'
		)
	interval = target[0]

	out_dir = pathlib.Path('/tmp/e2e_bug_101') / VIDEO_BASENAME / f'seed_{LEFT_FRAME}_{RIGHT_FRAME}'  # nosec B108 - E2E scratch under /tmp per E2E_TESTS.md convention
	# render_tiles=False keeps the run fast; the crash fires at the first observe
	# (bootstrap step), well before any tile rendering.
	walk_driver.run_interval_walk(
		left_seed=interval.left_seed,
		right_seed=interval.right_seed,
		reader=reader,
		scene_transform=scene_transform,
		probe_info=probe_info,
		output_interval_dir=out_dir,
		winner_mode='production_winner',
		audit_rule=None,
		render_tiles=False,
		proc_seed_by_frame=None,
	)
	reader.close()


#============================================
def main() -> None:
	"""Run the reproduction and map the outcome to an exit code."""
	# Narrow except: only the degenerate-ROI ValueError is the #101 signature.
	# Any other exception is an unexpected wiring/fixture failure (exit 2).
	caught = None
	raised_degenerate = False
	try:
		reproduce_degenerate_roi()
	except ValueError as exc:
		caught = exc
		raised_degenerate = 'degenerate ROI' in str(exc)

	if raised_degenerate:
		print(f'BUG #101 REPRODUCED: {caught}', file=sys.stderr)
		print('Exit 1: degenerate-ROI ValueError fired (bug still present).', file=sys.stderr)
		sys.exit(1)
	if caught is not None:
		print(f'UNEXPECTED ValueError (not bug #101): {caught}', file=sys.stderr)
		sys.exit(2)
	print('NO CRASH: walk completed without degenerate-ROI raise (bug #101 fixed).')
	sys.exit(0)


if __name__ == '__main__':
	main()
