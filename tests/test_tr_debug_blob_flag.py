"""Smoke test for --debug-blob CLI flag plumbing (M0a).

Verifies:
- `--debug-blob` parses correctly and sets `args.debug_blob = True`.
- Default parse leaves `args.debug_blob = False`.
- `ExecutionContext` accepts and stores `debug_blob`.
- The field round-trips: True in -> True out, False in -> False out.
"""

# PIP3 modules
import pytest

pytest.importorskip("cli_args")
pytest.importorskip("solve_queue")

# local repo modules
import cli_args
import solve_queue


#============================================
def _parse(argv: list) -> object:
	"""Parse a minimal argv through the real top-level parser."""
	# cli_args.parse_args() reads sys.argv; call the parser directly
	# by reconstructing it via the public parse_args function with
	# a patched sys.argv. Simpler: reach into the parser construction.
	import sys
	old_argv = sys.argv
	sys.argv = argv
	try:
		args = cli_args.parse_args()
	finally:
		sys.argv = old_argv
	return args


#============================================
def test_debug_blob_flag_parses_true():
	"""--debug-blob sets args.debug_blob to True (registered on solve subparser)."""
	args = _parse([
		"track_runner.py", "-i", "/tmp/dummy.mkv",
		"solve", "--debug-blob",
	])
	assert args.debug_blob is True


#============================================
def test_debug_blob_flag_parses_true_on_refine():
	"""--debug-blob also works on the refine subparser."""
	args = _parse([
		"track_runner.py", "-i", "/tmp/dummy.mkv",
		"refine", "--debug-blob",
	])
	assert args.debug_blob is True


#============================================
def test_debug_blob_flag_default_false():
	"""Without --debug-blob, args.debug_blob is False."""
	args = _parse([
		"track_runner.py", "-i", "/tmp/dummy.mkv", "solve",
	])
	assert args.debug_blob is False


#============================================
def test_execution_context_debug_blob_field():
	"""ExecutionContext stores debug_blob and round-trips the value."""
	ctx_on = solve_queue.ExecutionContext(
		reader=None,
		scene_transform=None,
		motion_track=None,
		all_seeds=[],
		all_seeds_scene=[],
		fps=30.0,
		num_workers=1,
		video_path="/tmp/dummy.mkv",
		video_frame_count=100,
		debug=False,
		debug_blob=True,
	)
	ctx_off = solve_queue.ExecutionContext(
		reader=None,
		scene_transform=None,
		motion_track=None,
		all_seeds=[],
		all_seeds_scene=[],
		fps=30.0,
		num_workers=1,
		video_path="/tmp/dummy.mkv",
		video_frame_count=100,
		debug=False,
		debug_blob=False,
	)
	assert ctx_on.debug_blob is True
	assert ctx_off.debug_blob is False
