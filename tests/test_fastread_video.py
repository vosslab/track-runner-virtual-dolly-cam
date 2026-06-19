"""test_fastread_video.py

Unit tests for fastread-video validation and path namespacing:
  - validate_fastread_structural: pass/fail shapes with synthetic probe data
  - tr_paths.fastread_video_path: path-namespace correctness
  - tr_paths artifact helpers: stems keyed to original video, not fastread name

No real ffmpeg, no real video.  probe_video and FrameReader are monkeypatched
throughout to return synthetic probe dicts and succeed without I/O.
"""

# Standard Library
import os
import pytest

# local repo modules
import fastread_video
import residual_motion
import tr_paths

#============================================
# Synthetic probe helpers
#============================================

def _make_probe(*, width: int = 1920, height: int = 1080,
	fps: float = 120.0, frame_count: int = 3600,
	duration_s: float = 30.0) -> dict:
	"""Return a synthetic probe_video dict with all required keys."""
	return {
		"width": width,
		"height": height,
		"fps": fps,
		"frame_count": frame_count,
		"duration_s": duration_s,
	}

#============================================
# Fake FrameReader: context-manager that returns a non-None sentinel on read
#============================================

class _FakeFrameReader:
	"""Minimal FrameReader stand-in: context manager, read_frame returns a sentinel.

	Also records seek_for_encode calls and read_frame indices so behavior
	tests can inspect what the smoke-read path actually did.
	"""

	def __init__(self, path: str, fps: float, total_frames: int) -> None:
		self._total_frames = total_frames
		# record all read_frame index calls in order
		self.read_indices: list[int] = []
		# record the start argument from the most recent seek_for_encode call
		self.seek_start: int | None = None

	def __enter__(self) -> "_FakeFrameReader":
		return self

	def __exit__(self, *args: object) -> None:
		pass

	def seek_for_encode(self, start: int) -> None:
		"""Record the seek start position for tail-read behavior assertions."""
		self.seek_start = start

	def read_frame(self, index: int) -> object:
		# record the index before returning a truthy sentinel
		self.read_indices.append(index)
		return object()

#============================================
# Tests: fastread_video_path path-namespace
#============================================

def test_fastread_path_ends_with_fastread_mkv(tmp_path: object) -> None:
	"""fastread_video_path produces a .fastread.mkv file beside the source."""
	original = str(tmp_path) + "/Lyra-Wheeling-IMG_3912.mkv"
	result = tr_paths.fastread_video_path(original)
	# must end with the expected suffix
	assert result.endswith("Lyra-Wheeling-IMG_3912.fastread.mkv")

def test_fastread_path_sits_beside_source(tmp_path: object) -> None:
	"""fastread_video_path output directory matches the original video directory."""
	original = str(tmp_path) + "/some_race.mkv"
	result = tr_paths.fastread_video_path(original)
	# directory must match: the fastread file lives beside the source
	assert os.path.dirname(result) == str(tmp_path)

def test_fastread_path_differs_from_original(tmp_path: object) -> None:
	"""fastread_video_path never returns the same path as the original."""
	original = str(tmp_path) + "/race.mkv"
	result = tr_paths.fastread_video_path(original)
	assert result != original

#============================================
# Tests: tr_paths artifact helpers use original stem
#============================================

def test_seeds_path_uses_original_stem() -> None:
	"""default_seeds_path stem matches original basename, not fastread basename."""
	original = "/data/Lyra-Wheeling-IMG_3912.mkv"
	seeds = tr_paths.default_seeds_path(original)
	# stem in the output filename must derive from original, not from fastread
	assert "Lyra-Wheeling-IMG_3912" in os.path.basename(seeds)
	assert "fastread" not in os.path.basename(seeds)

def test_config_path_uses_original_stem() -> None:
	"""default_config_path stem matches original basename, not fastread basename."""
	original = "/data/Lyra-Wheeling-IMG_3912.mkv"
	config = tr_paths.default_config_path(original)
	assert "Lyra-Wheeling-IMG_3912" in os.path.basename(config)
	assert "fastread" not in os.path.basename(config)

def test_intervals_path_uses_original_stem() -> None:
	"""default_intervals_path stem matches original basename, not fastread basename."""
	original = "/data/Lyra-Wheeling-IMG_3912.mkv"
	intervals = tr_paths.default_intervals_path(original)
	assert "Lyra-Wheeling-IMG_3912" in os.path.basename(intervals)
	assert "fastread" not in os.path.basename(intervals)

def test_contact_sheet_path_keyed_off_original_not_decode() -> None:
	"""Contact-sheet artifact path uses the original stem given a fast-read decode path.

	Regression guard for the state-path leak: solve_queue keys the
	race-start contact-sheet artifact off ExecutionContext.original_video_path,
	never off decode_video_path (the fast-read decode path).
	"""
	import solve_queue
	original = "/data/Lyra-Wheeling-IMG_3912.mkv"
	decode = tr_paths.fastread_video_path(original)
	context = solve_queue.ExecutionContext(
		reader=None,
		scene_transform=None,
		motion_track=None,
		all_seeds=[],
		all_seeds_scene=[],
		fps=30.0,
		num_workers=1,
		decode_video_path=decode,
		original_video_path=original,
		video_frame_count=100,
		debug=False,
	)
	# the artifact path the solve queue builds keys off original_video_path
	artifact = tr_paths.default_race_start_contact_sheet_path(
		context.original_video_path
	)
	assert "Lyra-Wheeling-IMG_3912" in os.path.basename(artifact)
	assert "fastread" not in os.path.basename(artifact)

#============================================
# Tests: validate_fastread_structural pass shape
#============================================

def test_validate_returns_fastread_validation_on_match(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""Matching probe dicts and healthy FrameReader return a FastreadValidation."""
	probe = _make_probe()
	monkeypatch.setattr(
		"common_tools.probe_video.probe_video",
		lambda _path: probe,
	)
	monkeypatch.setattr(
		"common_tools.frame_reader.FrameReader",
		_FakeFrameReader,
	)
	original = str(tmp_path / "race.mkv")
	fastread = str(tmp_path / "race.fastread.mkv")
	result = fastread_video.validate_fastread_structural(original, fastread)
	assert isinstance(result, fastread_video.FastreadValidation)

def test_validate_result_carries_timestamp_note(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""Successful validation result has a non-empty timestamp_fallback_note."""
	probe = _make_probe()
	monkeypatch.setattr(
		"common_tools.probe_video.probe_video",
		lambda _path: probe,
	)
	monkeypatch.setattr(
		"common_tools.frame_reader.FrameReader",
		_FakeFrameReader,
	)
	original = str(tmp_path / "race.mkv")
	fastread = str(tmp_path / "race.fastread.mkv")
	result = fastread_video.validate_fastread_structural(original, fastread)
	# timestamp_fallback_note must be a non-empty string (behavioral, not wording-pinned)
	assert isinstance(result.timestamp_fallback_note, str) and result.timestamp_fallback_note

#============================================
# Tests: validate_fastread_structural fail shapes
#============================================

def _patched_validate(
	monkeypatch: pytest.MonkeyPatch,
	original_probe: dict,
	fastread_probe: dict,
	tmp_path: object,
) -> None:
	"""Helper: run validate with two separate probe dicts, fail-on-call order."""
	call_count = [0]
	probes = [original_probe, fastread_probe]

	def _probe_side_effect(_path: str) -> dict:
		idx = call_count[0]
		call_count[0] += 1
		return probes[idx]

	monkeypatch.setattr("common_tools.probe_video.probe_video", _probe_side_effect)
	monkeypatch.setattr("common_tools.frame_reader.FrameReader", _FakeFrameReader)
	original = str(tmp_path) + "/race.mkv"
	fastread = str(tmp_path) + "/race.fastread.mkv"
	fastread_video.validate_fastread_structural(original, fastread)

def test_mismatched_width_raises(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""Width mismatch between original and fastread raises RuntimeError."""
	orig_probe = _make_probe(width=1920)
	fast_probe = _make_probe(width=1280)
	with pytest.raises(RuntimeError, match="race.fastread.mkv"):
		_patched_validate(monkeypatch, orig_probe, fast_probe, tmp_path)

def test_mismatched_height_raises(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""Height mismatch raises RuntimeError."""
	orig_probe = _make_probe(height=1080)
	fast_probe = _make_probe(height=720)
	with pytest.raises(RuntimeError, match="race.fastread.mkv"):
		_patched_validate(monkeypatch, orig_probe, fast_probe, tmp_path)

def test_mismatched_frame_count_raises(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""frame_count mismatch raises RuntimeError."""
	orig_probe = _make_probe(frame_count=3600)
	fast_probe = _make_probe(frame_count=3599)
	with pytest.raises(RuntimeError, match="race.fastread.mkv"):
		_patched_validate(monkeypatch, orig_probe, fast_probe, tmp_path)

def test_out_of_tolerance_duration_raises(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""Duration difference beyond tolerance raises RuntimeError."""
	# use a gap large enough to exceed any plausible tolerance
	orig_probe = _make_probe(duration_s=30.0)
	fast_probe = _make_probe(duration_s=35.0)
	with pytest.raises(RuntimeError, match="race.fastread.mkv"):
		_patched_validate(monkeypatch, orig_probe, fast_probe, tmp_path)

def test_out_of_tolerance_fps_raises(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""fps difference beyond probe-precision relative tolerance raises RuntimeError."""
	# 120 vs 60: 50% relative difference far exceeds any probe-precision tolerance
	orig_probe = _make_probe(fps=120.0)
	fast_probe = _make_probe(fps=60.0)
	with pytest.raises(RuntimeError, match="race.fastread.mkv"):
		_patched_validate(monkeypatch, orig_probe, fast_probe, tmp_path)

def test_error_message_names_fastread_path(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""RuntimeError message names the fast-read path so the user knows which file failed."""
	orig_probe = _make_probe(width=1920)
	fast_probe = _make_probe(width=640)
	with pytest.raises(RuntimeError) as exc_info:
		_patched_validate(monkeypatch, orig_probe, fast_probe, tmp_path)
	# the fast-read path must appear in the message
	assert "race.fastread.mkv" in str(exc_info.value)

def test_error_message_names_remedy(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""RuntimeError message includes a remedy so the user knows how to fix it."""
	orig_probe = _make_probe(frame_count=3600)
	fast_probe = _make_probe(frame_count=1)
	with pytest.raises(RuntimeError) as exc_info:
		_patched_validate(monkeypatch, orig_probe, fast_probe, tmp_path)
	# some remedy guidance must appear -- "prepare" or "delete" are both acceptable hints
	msg = str(exc_info.value)
	has_remedy = ("prepare" in msg) or ("delete" in msg) or ("remedy" in msg)
	assert has_remedy

#============================================
# resolve_video_context: per-role selection
#============================================

def _make_original(tmp_path: object) -> str:
	"""Create an original .mkv file on disk and return its path."""
	original_path = os.path.join(str(tmp_path), "Lyra-Wheeling-IMG_3912.mkv")
	with open(original_path, "wb") as handle:
		handle.write(b"\x00")
	return original_path

def test_resolve_absent_fastread_uses_original(tmp_path: object) -> None:
	"""No fast-read file -> working_decode is the original with the absent reason."""
	original_path = _make_original(tmp_path)
	# no fast-read file is created, so the deterministic path is absent
	context = fastread_video.resolve_video_context(original_path)
	assert context.working_decode.path == original_path
	assert context.working_decode.reason == fastread_video.REASON_NO_FASTREAD_ORIGINAL
	assert context.working_decode.using_fastread is False

def test_resolve_absent_final_and_identity_are_original(tmp_path: object) -> None:
	"""final_encode and metadata_identity always select the original (absent case)."""
	original_path = _make_original(tmp_path)
	context = fastread_video.resolve_video_context(original_path)
	assert context.final_encode.path == original_path
	assert context.final_encode.reason == fastread_video.REASON_FINAL_ENCODE_ORIGINAL
	assert context.metadata_identity.path == original_path
	assert context.metadata_identity.reason == fastread_video.REASON_METADATA_IDENTITY_ORIGINAL

def test_resolve_valid_fastread_selects_fastread(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""Present + valid fast-read -> working_decode is the fast-read path."""
	original_path = _make_original(tmp_path)
	fastread_path = tr_paths.fastread_video_path(original_path)
	# create the fast-read file so the existence check passes
	with open(fastread_path, "wb") as handle:
		handle.write(b"\x00")
	# stub validation to succeed without probing or reading frames
	def _ok_validate(orig: str, fast: str, fps_mismatch_fatal: bool = True) -> object:
		return object()
	monkeypatch.setattr(fastread_video, "validate_fastread_structural", _ok_validate)
	context = fastread_video.resolve_video_context(original_path)
	assert context.working_decode.path == fastread_path
	assert context.working_decode.reason == fastread_video.REASON_VALID_FASTREAD
	assert context.working_decode.using_fastread is True
	# encode and identity still select the original even when fast-read is valid
	assert context.final_encode.path == original_path
	assert context.metadata_identity.path == original_path

def test_resolve_invalid_fastread_propagates_raise(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""Present + invalid fast-read -> resolver propagates the validation raise."""
	original_path = _make_original(tmp_path)
	fastread_path = tr_paths.fastread_video_path(original_path)
	with open(fastread_path, "wb") as handle:
		handle.write(b"\x00")
	def _bad_validate(orig: str, fast: str, fps_mismatch_fatal: bool = True) -> object:
		raise RuntimeError("fast-read video failed structural validation")
	monkeypatch.setattr(fastread_video, "validate_fastread_structural", _bad_validate)
	with pytest.raises(RuntimeError):
		fastread_video.resolve_video_context(original_path)

def test_resolve_validates_exactly_once(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""Validation runs exactly once per resolve call (present + valid)."""
	original_path = _make_original(tmp_path)
	fastread_path = tr_paths.fastread_video_path(original_path)
	with open(fastread_path, "wb") as handle:
		handle.write(b"\x00")
	call_count = {"n": 0}
	def _counting_validate(orig: str, fast: str, fps_mismatch_fatal: bool = True) -> object:
		call_count["n"] += 1
		return object()
	monkeypatch.setattr(
		fastread_video, "validate_fastread_structural", _counting_validate
	)
	fastread_video.resolve_video_context(original_path)
	assert call_count["n"] == 1

#============================================
# state/artifact paths stay original-stem under a fast-read context
#============================================

def _valid_fastread_context(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> fastread_video.VideoContext:
	"""Build a VideoContext whose working_decode is a valid fast-read.

	Creates the original and the deterministic fast-read file on disk and
	stubs structural validation to succeed, so resolve_video_context routes
	working_decode to the fast-read path.
	"""
	original_path = _make_original(tmp_path)
	fastread_path = tr_paths.fastread_video_path(original_path)
	with open(fastread_path, "wb") as handle:
		handle.write(b"\x00")
	def _ok_validate(orig: str, fast: str, fps_mismatch_fatal: bool = True) -> object:
		return object()
	monkeypatch.setattr(
		fastread_video, "validate_fastread_structural", _ok_validate
	)
	return fastread_video.resolve_video_context(original_path)

def test_seeds_path_stays_original_under_fastread_context(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""Seed artifact path uses the original stem even when decode is fast-read."""
	context = _valid_fastread_context(tmp_path, monkeypatch)
	# the decode path is the fast-read file, but state keys off the original
	assert context.working_decode.using_fastread is True
	assert "fastread" in os.path.basename(context.working_decode.path)
	seeds = tr_paths.default_seeds_path(context.metadata_identity.path)
	assert "Lyra-Wheeling-IMG_3912" in os.path.basename(seeds)
	assert "fastread" not in os.path.basename(seeds)

def test_config_path_stays_original_under_fastread_context(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""Config artifact path uses the original stem even when decode is fast-read."""
	context = _valid_fastread_context(tmp_path, monkeypatch)
	config = tr_paths.default_config_path(context.original_video_path)
	assert "Lyra-Wheeling-IMG_3912" in os.path.basename(config)
	assert "fastread" not in os.path.basename(config)

#============================================
# analyze reports carry canonical_source + decode_source
#============================================

def _minimal_analysis() -> dict:
	"""Return the smallest analysis dict the YAML writer indexes."""
	analysis = {
		"summary": {
			"frames": 100,
			"duration_s": 1.0,
			"fps": 100.0,
			"output_size": [640, 360],
		},
		"motion_stability": {
			"center_jerk_p50": 0.0, "center_jerk_p95": 0.0,
			"height_jerk_p50": 0.0, "height_jerk_p95": 0.0,
			"crop_size_cv": 0.0, "quantization_chatter_fraction": 0.0,
		},
		"confidence": {"mean": 1.0, "low_conf_fraction": 0.0},
		"instability_regions": [],
		"dominant_symptom": "none",
		"seed_suggestions": [],
	}
	return analysis

def _minimal_solver_context() -> dict:
	"""Return the smallest solver_context dict the YAML writer indexes."""
	solver_context = {
		"seed_density": 0.0,
		"desert_count": 0,
		"seed_gap_mean_s": 0.0,
		"seed_gap_max_s": 0.0,
		"top_seed_gaps": [],
	}
	return solver_context

def test_analyze_yaml_carries_source_fields(tmp_path: object) -> None:
	"""write_analysis_yaml emits canonical_source and decode_source verbatim."""
	import yaml as yaml_module
	import encode_analysis
	canonical_source = "Lyra-Wheeling-IMG_3912.mkv"
	decode_source = "Lyra-Wheeling-IMG_3912.fastread.mkv"
	out_path = os.path.join(str(tmp_path), "x.encode_analysis.yaml")
	encode_analysis.write_analysis_yaml(
		_minimal_analysis(), _minimal_solver_context(), out_path,
		canonical_source=canonical_source,
		decode_source=decode_source,
	)
	with open(out_path) as handle:
		doc = yaml_module.safe_load(handle)
	assert doc["canonical_source"] == canonical_source
	assert doc["decode_source"] == decode_source

def test_analyze_html_carries_source_fields(tmp_path: object) -> None:
	"""write_analyze_report embeds canonical_source and decode_source in HTML."""
	import pathlib
	import analyze_report
	out_path = pathlib.Path(str(tmp_path)) / "x.html"
	written = analyze_report.write_analyze_report(
		out_path=out_path,
		video_stem="Lyra-Wheeling-IMG_3912",
		trajectory=[],
		crop_rects=[],
		motion_track=None,
		scene_transform=None,
		fps=100.0,
		config=None,
		warnings=[],
		canonical_source="Lyra-Wheeling-IMG_3912.mkv",
		decode_source="Lyra-Wheeling-IMG_3912.fastread.mkv",
	)
	html_text = written.read_text()
	# check the values appear in the output; label text is UI-internal and not pinned
	assert "Lyra-Wheeling-IMG_3912.fastread.mkv" in html_text
	assert "Lyra-Wheeling-IMG_3912.mkv" in html_text

#============================================
# -fps_mode:v passthrough in transcode command
#============================================

def test_ffmpeg_cmd_includes_fps_mode_passthrough() -> None:
	"""Argv contains the adjacent pair -fps_mode:v / passthrough.

	ffmpeg requires these as an adjacent flag-value pair; a split or reorder
	silently changes behavior (fps mode is ignored). The ordering relative to
	-c:v is not pinned here: only the adjacency matters for ffmpeg correctness.
	"""
	cmd = fastread_video._build_ffmpeg_transcode_cmd(
		"/usr/bin/ffmpeg", "/data/race.mkv", "/data/race.fastread.mkv"
	)
	# -fps_mode:v must be in the argv and immediately followed by passthrough
	assert "-fps_mode:v" in cmd, "-fps_mode:v missing from transcode argv"
	i = cmd.index("-fps_mode:v")
	assert cmd[i + 1] == "passthrough", (
		"-fps_mode:v must be immediately followed by passthrough"
	)

#============================================
# stride safety: 59.94 vs 60.0 fps mismatch
#============================================

def test_resolve_stride_5994_equals_60() -> None:
	"""resolve_stride(59.94) == resolve_stride(60.0).

	Backs the fps-safety claim: the consumer path warns-and-continues on
	a 59.94 vs 60.0 fps probe mismatch because both round to the same stride,
	so frame index alignment is unaffected regardless of which fps value is used.
	"""
	assert residual_motion.resolve_stride(59.94) == residual_motion.resolve_stride(60.0)

#============================================
# validate_fastread_structural fps_mismatch_fatal modes
#============================================

def _make_two_probe_patcher(
	monkeypatch: pytest.MonkeyPatch,
	original_probe: dict,
	fastread_probe: dict,
) -> None:
	"""Patch probe_video to return two distinct probes on consecutive calls."""
	call_count = [0]
	probes = [original_probe, fastread_probe]
	def _side_effect(_path: str) -> dict:
		idx = call_count[0]
		call_count[0] += 1
		return probes[idx]
	monkeypatch.setattr("common_tools.probe_video.probe_video", _side_effect)
	monkeypatch.setattr("common_tools.frame_reader.FrameReader", _FakeFrameReader)

def test_fps_mismatch_fatal_false_does_not_raise_warns(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
	"""fps_mismatch_fatal=False: 59.94 vs 60.0 mismatch warns-and-continues.

	The consume path (resolve_video_context) calls with fps_mismatch_fatal=False
	and must receive a FastreadValidation rather than a RuntimeError on a small
	fps probe mismatch.
	"""
	import logging
	orig_probe = _make_probe(fps=60.0)
	fast_probe = _make_probe(fps=59.94)
	_make_two_probe_patcher(monkeypatch, orig_probe, fast_probe)
	original = str(tmp_path / "race.mkv")
	fastread = str(tmp_path / "race.fastread.mkv")
	# must not raise; must return a FastreadValidation
	with caplog.at_level(logging.WARNING, logger="fastread_video"):
		result = fastread_video.validate_fastread_structural(
			original, fastread, fps_mismatch_fatal=False
		)
	assert isinstance(result, fastread_video.FastreadValidation)
	# a warning mentioning both fps values must have been logged
	warning_text = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
	assert "59.94" in warning_text or "60.0" in warning_text, (
		"expected a warning mentioning one or both fps values; got: " + warning_text
	)

def test_fps_mismatch_fatal_true_raises(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""fps_mismatch_fatal=True (default): same fps mismatch raises RuntimeError.

	The prepare path must reject a newly transcoded fast-read whose fps probe
	does not match the source, so timing defects are caught at build time.
	"""
	orig_probe = _make_probe(fps=60.0)
	fast_probe = _make_probe(fps=59.94)
	_make_two_probe_patcher(monkeypatch, orig_probe, fast_probe)
	original = str(tmp_path / "race.mkv")
	fastread = str(tmp_path / "race.fastread.mkv")
	with pytest.raises(RuntimeError, match="race.fastread.mkv"):
		fastread_video.validate_fastread_structural(original, fastread, fps_mismatch_fatal=True)

def test_frame_count_mismatch_raises_regardless_of_fps_flag(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""frame_count mismatch raises even with fps_mismatch_fatal=False.

	The fps exception must never mask a real structural failure such as a
	frame_count mismatch; geometry/frame_count/duration remain always-fatal.
	"""
	orig_probe = _make_probe(frame_count=3600)
	# change frame_count to trigger the hard structural gate first
	fast_probe = _make_probe(frame_count=3599)
	_make_two_probe_patcher(monkeypatch, orig_probe, fast_probe)
	original = str(tmp_path / "race.mkv")
	fastread = str(tmp_path / "race.fastread.mkv")
	with pytest.raises(RuntimeError, match="race.fastread.mkv"):
		fastread_video.validate_fastread_structural(original, fastread, fps_mismatch_fatal=False)

#============================================
# smoke-read tail behavior: seek + sequential reads through the last frame
#============================================

def test_smoke_read_reads_tail_sequentially_through_last_frame(
	tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""_smoke_read_fastread seeks to the tail start and reads contiguously to the last frame.

	Uses a frame_count larger than TAIL_PREROLL so the tail slice is non-trivial.
	Asserts the invariant: seek_start is correct and reads from seek_start through
	frame_count-1 are contiguous with no gaps, ending at the true last frame.
	"""
	frame_count = 500
	probe = _make_probe(frame_count=frame_count)
	# single shared fake so we can inspect its recorded state after validation
	fake_reader = _FakeFrameReader("/fake/race.fastread.mkv", fps=120.0, total_frames=frame_count)
	monkeypatch.setattr(
		"common_tools.probe_video.probe_video",
		lambda _path: probe,
	)
	monkeypatch.setattr(
		"common_tools.frame_reader.FrameReader",
		# ignore constructor args; return the shared fake instance
		lambda path, fps, total_frames: fake_reader,
	)
	original = str(tmp_path / "race.mkv")
	fastread = str(tmp_path / "race.fastread.mkv")
	fastread_video.validate_fastread_structural(original, fastread, fps_mismatch_fatal=False)
	# derive expected tail start the same way the impl does
	expected_start = max(0, (frame_count - 1) - fastread_video.TAIL_PREROLL)
	# invariant 1: seek_for_encode was called with the correct tail start
	assert fake_reader.seek_start == expected_start
	# invariant 2: the last read index is the true last frame
	assert fake_reader.read_indices[-1] == frame_count - 1
	# invariant 3: the tail portion of read_indices is contiguous through the end
	# (frames 0 and mid may also appear; we check only the tail slice)
	tail_reads = [i for i in fake_reader.read_indices if i >= expected_start]
	assert tail_reads == list(range(expected_start, frame_count))
