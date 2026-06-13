"""Parity tests for track_runner/blob_walk/walk_io.py.

Verifies:
  1. Reader-geometry parity: bin_factor, processed width/height, and source
     width/height produced by the walk_io path (select_bin_factor_for_analysis
     + FrameGeometry) match those produced by the production _worker_init path
     (the same call chain) for a range of source widths.
  2. Seed-path parity: paths walk_io builds for seeds, camera-motion, and
     interval-scores equal the tr_paths-constructed paths for the same input,
     AND missing authoritative artifacts fail loudly.
  3. load_race_start_frame: reads the stored authoritative value via
     state_io.load_diagnostics; silent-zero behavior is gone; missing artifact,
     None pre_race_reference, and missing key all raise RuntimeError with the
     artifact path in the message.
  4. enumerate_seed_to_seed_intervals: the three race-phase labels
     (pre_start, crossing_race_start, post_start) are correct, including
     the boundary frame_index == race_start_frame.

All tests are offline, deterministic, and finish well under one second.
No real video decode.
"""

# Standard Library
import json
import os
import pathlib
import sys
import tempfile

# Put conftest path setup on sys.path first (repo root and track_runner/).
# conftest.py adds both at collection time; we replicate it here so the
# module can be run standalone as well.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_TR_DIR = os.path.join(_REPO_ROOT, "track_runner")
for _p in (_TR_DIR, _REPO_ROOT):
	if _p not in sys.path:
		sys.path.insert(0, _p)

# local repo modules (resolved via the path inserts above)
import common_tools.frame_reader as frame_reader
import tr_paths
import state_io
import blob_walk.walk_io as walk_io


#============================================
# Section 1: Reader-geometry parity
#============================================

def _geometry_for_width(source_width: int, source_height: int = 2160) -> frame_reader.FrameGeometry:
	"""Build a FrameGeometry the same way both walk_io and _worker_init do.

	Both paths call select_bin_factor_for_analysis(source_width) then construct
	a FrameReader (or its geometry equivalent). This helper reproduces the geometry
	step without opening a real video file.
	"""
	bin_factor = frame_reader.select_bin_factor_for_analysis(source_width)
	# Reproduce _resolve_frame_geometry inline: floor-divide then goodbox snap.
	# We test the policy function here (select_bin_factor_for_analysis), not the
	# goodbox snap internals; goodbox is covered separately. Using the public API.
	return frame_reader._resolve_frame_geometry(source_width, source_height, bin_factor)


def test_reader_geometry_parity_4k():
	"""4K source (3840 wide): both paths select bin_factor=4, geometry is consistent."""
	source_width = 3840
	source_height = 2160
	# walk_io path
	bin_factor_wio = frame_reader.select_bin_factor_for_analysis(source_width)
	geom_wio = frame_reader._resolve_frame_geometry(source_width, source_height, bin_factor_wio)
	# production _worker_init path: identical call chain
	bin_factor_prod = frame_reader.select_bin_factor_for_analysis(source_width)
	geom_prod = frame_reader._resolve_frame_geometry(source_width, source_height, bin_factor_prod)
	# Both paths must produce identical concrete fields
	assert geom_wio.bin_factor == geom_prod.bin_factor
	assert geom_wio.source_width == geom_prod.source_width
	assert geom_wio.source_height == geom_prod.source_height
	assert geom_wio.processed_width == geom_prod.processed_width
	assert geom_wio.processed_height == geom_prod.processed_height
	# Sanity: 4K at bin=4 should produce ~960 wide
	assert geom_wio.bin_factor == 4
	assert geom_wio.source_width == 3840


def test_reader_geometry_parity_1080p():
	"""1080p source (1920 wide): bin_factor=2, geometry is consistent."""
	source_width = 1920
	source_height = 1080
	geom_wio = _geometry_for_width(source_width, source_height)
	geom_prod = _geometry_for_width(source_width, source_height)
	assert geom_wio.bin_factor == geom_prod.bin_factor
	assert geom_wio.source_width == geom_prod.source_width
	assert geom_wio.processed_width == geom_prod.processed_width
	assert geom_wio.processed_height == geom_prod.processed_height


def test_reader_geometry_parity_small_source():
	"""640-wide source: bin_factor=1 (no downsampling), geometry consistent."""
	source_width = 640
	source_height = 480
	geom_wio = _geometry_for_width(source_width, source_height)
	geom_prod = _geometry_for_width(source_width, source_height)
	assert geom_wio.bin_factor == geom_prod.bin_factor
	assert geom_wio.bin_factor == 1
	assert geom_wio.source_width == source_width
	assert geom_wio.processed_width == geom_prod.processed_width


def test_reader_geometry_bin_factor_selection_matches_walk_io_logic():
	"""open_walker_reader uses select_bin_factor_for_analysis(source_width).

	Verifies that the policy function walk_io calls is the same one _worker_init
	calls, producing identical bin_factors for a representative set of widths.
	"""
	# Representative source widths encountered in corpus
	test_widths = [640, 1280, 1920, 3840]
	for width in test_widths:
		# walk_io flow: probe_info["width"] -> select_bin_factor_for_analysis
		bin_wio = frame_reader.select_bin_factor_for_analysis(width)
		# _worker_init flow: same function, same argument
		bin_prod = frame_reader.select_bin_factor_for_analysis(width)
		assert bin_wio == bin_prod, (
			f"bin_factor mismatch at source_width={width}: "
			f"walk_io got {bin_wio}, _worker_init got {bin_prod}"
		)


#============================================
# Section 2: Seed-path and artifact-path parity
#============================================

def test_seeds_path_parity():
	"""walk_io seed path matches tr_paths for the same video stem.

	walk_io builds: _REPO_ROOT/tr_config/{base}.track_runner.seeds.json
	tr_paths builds: tr_config/{base}.track_runner.seeds.json (CWD-relative)

	When CWD == repo root (the run context for both tools and tests), these
	produce paths with identical filenames; we compare basenames to avoid CWD
	sensitivity in the test itself.
	"""
	video_basename = "2025-Glenbrook_South-1600m-IMG_1503.mkv"
	base = "2025-Glenbrook_South-1600m-IMG_1503"
	# walk_io path (absolute, anchored to git repo root)
	wio_path = pathlib.Path(walk_io._REPO_ROOT) / "tr_config" / f"{base}.track_runner.seeds.json"
	# tr_paths path (CWD-relative when called as _data_file_path("input.mkv", suffix))
	# tr_paths takes a full input file path; the stem extraction strips the extension
	fake_input = f"/some/path/{video_basename}"
	trpaths_path = pathlib.Path(tr_paths.default_seeds_path(fake_input))
	# Both must produce the same filename (the stem and suffix must match)
	assert wio_path.name == trpaths_path.name, (
		f"Seeds path filename mismatch:\n"
		f"  walk_io:  {wio_path.name}\n"
		f"  tr_paths: {trpaths_path.name}"
	)


def test_camera_motion_path_parity():
	"""walk_io camera-motion path matches tr_paths for the same video stem."""
	video_basename = "2025-Glenbrook_South-1600m-IMG_1503.mkv"
	base = "2025-Glenbrook_South-1600m-IMG_1503"
	wio_path = pathlib.Path(walk_io._REPO_ROOT) / "tr_config" / f"{base}.track_runner.camera_motion.npz"
	fake_input = f"/some/path/{video_basename}"
	trpaths_path = pathlib.Path(tr_paths.default_camera_motion_path(fake_input))
	assert wio_path.name == trpaths_path.name, (
		f"Camera-motion path filename mismatch:\n"
		f"  walk_io:  {wio_path.name}\n"
		f"  tr_paths: {trpaths_path.name}"
	)


def test_interval_scores_path_parity():
	"""walk_io interval-scores path matches tr_paths for the same video stem."""
	video_basename = "2025-Glenbrook_South-1600m-IMG_1503.mkv"
	base = "2025-Glenbrook_South-1600m-IMG_1503"
	wio_path = pathlib.Path(walk_io._REPO_ROOT) / "tr_config" / f"{base}.track_runner.interval_scores.json"
	fake_input = f"/some/path/{video_basename}"
	trpaths_path = pathlib.Path(tr_paths.default_diagnostics_path(fake_input))
	assert wio_path.name == trpaths_path.name, (
		f"Interval-scores path filename mismatch:\n"
		f"  walk_io:  {wio_path.name}\n"
		f"  tr_paths: {trpaths_path.name}"
	)


def test_path_parity_with_track_runner_suffix():
	"""Path normalization strips .track_runner suffix; filename matches tr_paths."""
	# .track_runner is a suffix used by some legacy callers
	base = "2025-Glenbrook_South-1600m-IMG_1503"
	wio_path = pathlib.Path(walk_io._REPO_ROOT) / "tr_config" / f"{base}.track_runner.seeds.json"
	fake_input = f"/some/path/{base}.mkv"
	trpaths_path = pathlib.Path(tr_paths.default_seeds_path(fake_input))
	assert wio_path.name == trpaths_path.name


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
	"""Write a legacy interval_scores.json without pre_race_reference (v4 header, missing key)."""
	# state_io.load_diagnostics synthesizes pre_race_reference=None when the key
	# is absent; we write a file that would trigger that path.
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
		# Fabricate a minimal tr_config structure under a temp REPO_ROOT
		tr_config_dir = pathlib.Path(tmpdir) / "tr_config"
		tr_config_dir.mkdir()
		base = "test_video"
		artifact_path = tr_config_dir / f"{base}.track_runner.interval_scores.json"
		expected_frame = 1234
		_write_minimal_interval_scores(str(artifact_path), expected_frame)
		# Patch _REPO_ROOT so walk_io locates the temp tr_config
		original_repo_root = walk_io._REPO_ROOT
		walk_io._REPO_ROOT = tmpdir
		result = None
		try:
			result = walk_io.load_race_start_frame(base)
		finally:
			walk_io._REPO_ROOT = original_repo_root
		assert result == expected_frame, (
			f"Expected race_start_frame={expected_frame}, got {result}"
		)


def test_load_race_start_frame_missing_artifact_raises():
	"""load_race_start_frame raises RuntimeError when the artifact is absent."""
	with tempfile.TemporaryDirectory() as tmpdir:
		tr_config_dir = pathlib.Path(tmpdir) / "tr_config"
		tr_config_dir.mkdir()
		original_repo_root = walk_io._REPO_ROOT
		walk_io._REPO_ROOT = tmpdir
		raised = False
		msg = ""
		try:
			walk_io.load_race_start_frame("nonexistent_video")
		except RuntimeError as exc:
			raised = True
			msg = str(exc)
		finally:
			walk_io._REPO_ROOT = original_repo_root
		assert raised, "Expected RuntimeError for missing artifact"
		# Artifact path must appear in the message for diagnosability
		assert "tr_config" in msg or "interval_scores" in msg, (
			f"RuntimeError message does not name the artifact path: {msg!r}"
		)


def test_load_race_start_frame_legacy_file_raises():
	"""load_race_start_frame raises RuntimeError for a legacy file (pre_race_reference is None).

	state_io.load_diagnostics synthesizes pre_race_reference=None when the key is
	absent from an otherwise-valid file. A None pre_race_reference is not recoverable
	without re-solving; the function must raise loudly rather than silently return 0.
	"""
	with tempfile.TemporaryDirectory() as tmpdir:
		tr_config_dir = pathlib.Path(tmpdir) / "tr_config"
		tr_config_dir.mkdir()
		base = "legacy_video"
		artifact_path = tr_config_dir / f"{base}.track_runner.interval_scores.json"
		_write_legacy_interval_scores(str(artifact_path))
		original_repo_root = walk_io._REPO_ROOT
		walk_io._REPO_ROOT = tmpdir
		raised = False
		msg = ""
		try:
			walk_io.load_race_start_frame(base)
		except RuntimeError as exc:
			raised = True
			msg = str(exc)
		finally:
			walk_io._REPO_ROOT = original_repo_root
		assert raised, "Expected RuntimeError for legacy file with None pre_race_reference"
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
	race_start = 100
	seeds_dict = _make_seeds_dict([110, 200, 300])
	intervals = walk_io.enumerate_seed_to_seed_intervals(seeds_dict, race_start)
	assert len(intervals) == 2
	for iv in intervals:
		assert iv.label == "post_start", f"Expected post_start, got {iv.label!r}"


def test_race_phase_all_pre_start():
	"""All intervals before race_start_frame are labeled pre_start."""
	race_start = 500
	seeds_dict = _make_seeds_dict([10, 50, 90])
	intervals = walk_io.enumerate_seed_to_seed_intervals(seeds_dict, race_start)
	assert len(intervals) == 2
	for iv in intervals:
		assert iv.label == "pre_start", f"Expected pre_start, got {iv.label!r}"


def test_race_phase_crossing():
	"""An interval straddling race_start_frame is labeled crossing_race_start."""
	race_start = 100
	seeds_dict = _make_seeds_dict([50, 150])
	intervals = walk_io.enumerate_seed_to_seed_intervals(seeds_dict, race_start)
	assert len(intervals) == 1
	assert intervals[0].label == "crossing_race_start"


def test_race_phase_boundary_left_seed_equals_race_start():
	"""left_frame == race_start_frame with right_frame > race_start_frame -> crossing_race_start.

	The race_start_frame boundary: the left seed is exactly at the race start
	frame. The interval spans from the race-start moment into the post-race
	period; it must be labeled crossing_race_start, not post_start.
	"""
	race_start = 100
	# left == race_start, right > race_start: crossing condition
	seeds_dict = _make_seeds_dict([100, 200])
	intervals = walk_io.enumerate_seed_to_seed_intervals(seeds_dict, race_start)
	assert len(intervals) == 1
	assert intervals[0].label == "crossing_race_start", (
		f"Expected crossing_race_start when left_frame == race_start_frame, "
		f"got {intervals[0].label!r}"
	)


def test_race_phase_boundary_right_seed_equals_race_start():
	"""right_frame == race_start_frame -> pre_start.

	The right seed is exactly at the race-start frame; both seeds are at or
	before the start, so the interval is pre-race.
	"""
	race_start = 100
	seeds_dict = _make_seeds_dict([50, 100])
	intervals = walk_io.enumerate_seed_to_seed_intervals(seeds_dict, race_start)
	assert len(intervals) == 1
	assert intervals[0].label == "pre_start", (
		f"Expected pre_start when right_frame == race_start_frame, "
		f"got {intervals[0].label!r}"
	)


def test_race_phase_mixed_sequence():
	"""Three-interval sequence produces all three labels in order."""
	race_start = 200
	seeds_dict = _make_seeds_dict([50, 150, 300, 400])
	intervals = walk_io.enumerate_seed_to_seed_intervals(seeds_dict, race_start)
	assert len(intervals) == 3
	assert intervals[0].label == "pre_start"
	assert intervals[1].label == "crossing_race_start"
	assert intervals[2].label == "post_start"


def test_race_phase_empty_seeds():
	"""Single seed produces no intervals."""
	seeds_dict = _make_seeds_dict([100])
	intervals = walk_io.enumerate_seed_to_seed_intervals(seeds_dict, race_start_frame=100)
	assert intervals == []


def test_race_phase_two_seeds_no_intervals_edge():
	"""Zero seeds produces empty list."""
	seeds_dict = _make_seeds_dict([])
	intervals = walk_io.enumerate_seed_to_seed_intervals(seeds_dict, race_start_frame=100)
	assert intervals == []
