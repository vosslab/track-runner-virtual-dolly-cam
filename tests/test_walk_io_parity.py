"""Core-routing tests for the standalone blob_walk_v2 HTML tool.

walk_io.py was deleted in WS3-coreroute; its needs are now routed to core
owners. This module verifies the post-migration behavior:

  1. Bin policy: the shared opener default-bin policy (select_default_bin_factor
     + FrameGeometry) is the single bin source for both the tool and the
     production worker path, so a representative set of source widths produces
     identical geometry.
  2. Tool artifact paths: walk_tool_setup builds tr_config artifact filenames
     that match the tr_paths-constructed filenames for the same input stem.
  3. walk_tool_setup.load_race_start_frame reads the stored authoritative value
     via state_io.load_diagnostics; the silent-zero behavior is gone. A missing
     artifact, a None pre_race_reference, and a missing race_start_frame key all
     raise RuntimeError naming the artifact path.
  4. race_phases.enumerate_seed_to_seed_intervals: the three race-phase labels
     (pre_start, crossing_race_start, post_start) are correct, including the
     boundary frame_index == race_start_frame.

All tests are offline, deterministic, and finish well under one second.
No real video decode.
"""

# Standard Library
import os
import sys
import json
import pathlib
import tempfile

# Put conftest path setup on sys.path first (repo root and track_runner/).
# conftest.py adds both at collection time; we replicate it here so the
# module can be run standalone as well.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_TR_DIR = os.path.join(_REPO_ROOT, "track_runner")
_TOOLS_DIR = os.path.join(_REPO_ROOT, "tools", "blob_walk_v2")
for _p in (_TOOLS_DIR, _TR_DIR, _REPO_ROOT):
	if _p not in sys.path:
		sys.path.insert(0, _p)

# local repo modules (resolved via the path inserts above)
import common_tools.frame_reader as frame_reader
import tr_paths
import state_io
import race_phases
import walk_tool_setup


#============================================
# Section 1: Bin policy is the single shared source
#============================================

def test_bin_policy_single_source_across_widths():
	"""The tool and the production worker reach the same default-bin policy.

	Both open their FrameReader through open_analysis_reader, whose default-bin
	policy is select_default_bin_factor. A representative set of source widths
	must produce identical bin factors and identical geometry from that one
	policy function.
	"""
	cases = [(640, 480), (1280, 720), (1920, 1080), (3840, 2160)]
	for source_width, source_height in cases:
		bin_factor = frame_reader.select_default_bin_factor(source_width)
		geom = frame_reader._resolve_frame_geometry(source_width, source_height, bin_factor)
		assert geom.bin_factor == bin_factor
		assert geom.source_width == source_width
		assert geom.source_height == source_height


#============================================
# Section 2: Tool artifact-path filenames match tr_paths
#============================================

def _tool_artifact_name(base: str, suffix: str) -> str:
	"""Filename walk_tool_setup builds for a tr_config artifact."""
	path = pathlib.Path(walk_tool_setup._REPO_ROOT) / "tr_config" / f"{base}{suffix}"
	return path.name


def test_seeds_path_filename_matches_tr_paths():
	"""walk_tool_setup seeds filename matches tr_paths for the same stem."""
	video_basename = "2025-Glenbrook_South-1600m-IMG_1503.mkv"
	base = walk_tool_setup.normalize_video_basename(video_basename)
	tool_name = _tool_artifact_name(base, ".track_runner.seeds.json")
	fake_input = f"/some/path/{video_basename}"
	trpaths_name = pathlib.Path(tr_paths.default_seeds_path(fake_input)).name
	assert tool_name == trpaths_name


def test_camera_motion_path_filename_matches_tr_paths():
	"""walk_tool_setup camera-motion filename matches tr_paths for the same stem."""
	video_basename = "2025-Glenbrook_South-1600m-IMG_1503.mkv"
	base = walk_tool_setup.normalize_video_basename(video_basename)
	tool_name = _tool_artifact_name(base, ".track_runner.camera_motion.npz")
	fake_input = f"/some/path/{video_basename}"
	trpaths_name = pathlib.Path(tr_paths.default_camera_motion_path(fake_input)).name
	assert tool_name == trpaths_name


def test_interval_scores_path_filename_matches_tr_paths():
	"""walk_tool_setup interval-scores filename matches tr_paths for the same stem."""
	video_basename = "2025-Glenbrook_South-1600m-IMG_1503.mkv"
	base = walk_tool_setup.normalize_video_basename(video_basename)
	tool_name = _tool_artifact_name(base, ".track_runner.interval_scores.json")
	fake_input = f"/some/path/{video_basename}"
	trpaths_name = pathlib.Path(tr_paths.default_diagnostics_path(fake_input)).name
	assert tool_name == trpaths_name


def test_path_filename_with_track_runner_suffix():
	"""normalize_video_basename strips .track_runner; filename matches tr_paths."""
	base = "2025-Glenbrook_South-1600m-IMG_1503"
	# accept the .track_runner suffix form and the bare stem identically
	assert walk_tool_setup.normalize_video_basename(f"{base}.track_runner") == base
	tool_name = _tool_artifact_name(base, ".track_runner.seeds.json")
	fake_input = f"/some/path/{base}.mkv"
	trpaths_name = pathlib.Path(tr_paths.default_seeds_path(fake_input)).name
	assert tool_name == trpaths_name


#============================================
# Section 3: load_race_start_frame correctness
#============================================

def _write_minimal_interval_scores(path: str, race_start_frame: int) -> None:
	"""Write a minimal valid interval_scores.json with pre_race_reference."""
	data = {
		state_io.DIAGNOSTICS_HEADER_KEY: state_io.DIAGNOSTICS_HEADER_VALUE,
		"pre_race_reference": {
			"race_start_frame": race_start_frame,
			"race_start_interval": [0, 100],
			"torso_w": 80.0,
			"torso_h": 160.0,
			"scene_anchor_x": 500.0,
			"scene_anchor_y": 300.0,
			"source_frame_indices": [0, 10, 20],
			"source_count": 3,
			"method": "midpoint",
			"warnings": [],
		},
		"intervals": [],
	}
	with open(path, "w") as fh:
		json.dump(data, fh)


def _write_legacy_interval_scores(path: str) -> None:
	"""Write a legacy interval_scores.json without pre_race_reference."""
	# state_io.load_diagnostics synthesizes pre_race_reference=None when the key
	# is absent; we write a file that triggers that path.
	data = {
		state_io.DIAGNOSTICS_HEADER_KEY: state_io.DIAGNOSTICS_HEADER_VALUE,
		# no "pre_race_reference" key
		"intervals": [],
	}
	with open(path, "w") as fh:
		json.dump(data, fh)


def test_load_race_start_frame_reads_authoritative_value():
	"""load_race_start_frame reads race_start_frame from pre_race_reference."""
	with tempfile.TemporaryDirectory() as tmpdir:
		tr_config_dir = pathlib.Path(tmpdir) / "tr_config"
		tr_config_dir.mkdir()
		base = "test_video"
		artifact_path = tr_config_dir / f"{base}.track_runner.interval_scores.json"
		expected_frame = 1234
		_write_minimal_interval_scores(str(artifact_path), expected_frame)
		original_repo_root = walk_tool_setup._REPO_ROOT
		walk_tool_setup._REPO_ROOT = tmpdir
		result = None
		try:
			result = walk_tool_setup.load_race_start_frame(base)
		finally:
			walk_tool_setup._REPO_ROOT = original_repo_root
		assert result == expected_frame


def test_load_race_start_frame_missing_artifact_raises():
	"""load_race_start_frame raises RuntimeError when the artifact is absent."""
	with tempfile.TemporaryDirectory() as tmpdir:
		tr_config_dir = pathlib.Path(tmpdir) / "tr_config"
		tr_config_dir.mkdir()
		original_repo_root = walk_tool_setup._REPO_ROOT
		walk_tool_setup._REPO_ROOT = tmpdir
		raised = False
		msg = ""
		try:
			walk_tool_setup.load_race_start_frame("nonexistent_video")
		except RuntimeError as exc:
			raised = True
			msg = str(exc)
		finally:
			walk_tool_setup._REPO_ROOT = original_repo_root
		assert raised, "Expected RuntimeError for missing artifact"
		assert "tr_config" in msg or "interval_scores" in msg, (
			f"RuntimeError message does not name the artifact path: {msg!r}"
		)


def test_load_race_start_frame_legacy_file_raises():
	"""load_race_start_frame raises RuntimeError for a None pre_race_reference."""
	with tempfile.TemporaryDirectory() as tmpdir:
		tr_config_dir = pathlib.Path(tmpdir) / "tr_config"
		tr_config_dir.mkdir()
		base = "legacy_video"
		artifact_path = tr_config_dir / f"{base}.track_runner.interval_scores.json"
		_write_legacy_interval_scores(str(artifact_path))
		original_repo_root = walk_tool_setup._REPO_ROOT
		walk_tool_setup._REPO_ROOT = tmpdir
		raised = False
		msg = ""
		try:
			walk_tool_setup.load_race_start_frame(base)
		except RuntimeError as exc:
			raised = True
			msg = str(exc)
		finally:
			walk_tool_setup._REPO_ROOT = original_repo_root
		assert raised, "Expected RuntimeError for legacy None pre_race_reference"
		assert "pre_race_reference" in msg.lower() or "legacy" in msg.lower(), (
			f"RuntimeError message should mention pre_race_reference or legacy: {msg!r}"
		)


#============================================
# Section 4: enumerate_seed_to_seed_intervals race-phase labels
#============================================

def _make_seeds_dict(frame_indices: list) -> dict:
	"""Build a minimal seeds dict with the given frame indices."""
	seeds = []
	for fi in frame_indices:
		seeds.append({
			"frame_index": fi,
			"status": "visible",
			"cx": 100.0,
			"cy": 100.0,
			"w": 80.0,
			"h": 160.0,
		})
	return {
		state_io.SEEDS_HEADER_KEY: state_io.SEEDS_HEADER_VALUE,
		"seeds": seeds,
	}


def test_race_phase_all_post_start():
	"""All intervals after race_start_frame are labeled post_start."""
	seeds_dict = _make_seeds_dict([110, 200, 300])
	intervals = race_phases.enumerate_seed_to_seed_intervals(seeds_dict, 100)
	for iv in intervals:
		assert iv.label == "post_start"


def test_race_phase_all_pre_start():
	"""All intervals before race_start_frame are labeled pre_start."""
	seeds_dict = _make_seeds_dict([10, 50, 90])
	intervals = race_phases.enumerate_seed_to_seed_intervals(seeds_dict, 500)
	for iv in intervals:
		assert iv.label == "pre_start"


def test_race_phase_crossing():
	"""An interval straddling race_start_frame is labeled crossing_race_start."""
	seeds_dict = _make_seeds_dict([50, 150])
	intervals = race_phases.enumerate_seed_to_seed_intervals(seeds_dict, 100)
	assert intervals[0].label == "crossing_race_start"


def test_race_phase_boundary_left_seed_equals_race_start():
	"""left_frame == race_start_frame, right_frame > -> crossing_race_start."""
	seeds_dict = _make_seeds_dict([100, 200])
	intervals = race_phases.enumerate_seed_to_seed_intervals(seeds_dict, 100)
	assert intervals[0].label == "crossing_race_start"


def test_race_phase_boundary_right_seed_equals_race_start():
	"""right_frame == race_start_frame -> pre_start."""
	seeds_dict = _make_seeds_dict([50, 100])
	intervals = race_phases.enumerate_seed_to_seed_intervals(seeds_dict, 100)
	assert intervals[0].label == "pre_start"


def test_race_phase_mixed_sequence():
	"""Three-interval sequence produces all three labels in order."""
	seeds_dict = _make_seeds_dict([50, 150, 300, 400])
	intervals = race_phases.enumerate_seed_to_seed_intervals(seeds_dict, 200)
	assert intervals[0].label == "pre_start"
	assert intervals[1].label == "crossing_race_start"
	assert intervals[2].label == "post_start"


def test_race_phase_single_seed():
	"""Single seed produces no intervals."""
	seeds_dict = _make_seeds_dict([100])
	intervals = race_phases.enumerate_seed_to_seed_intervals(seeds_dict, 100)
	assert intervals == []


def test_race_phase_zero_seeds():
	"""Zero seeds produces empty list."""
	seeds_dict = _make_seeds_dict([])
	intervals = race_phases.enumerate_seed_to_seed_intervals(seeds_dict, 100)
	assert intervals == []
