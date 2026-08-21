"""Portable tests for video metadata and identity artifact helpers."""

# Standard Library
import json
import pathlib
import types

# PIP3 modules
import pytest

# local repo modules
import modes.video_artifacts as video_artifacts
import torso_box_coords_io


#============================================
def _mediainfo_result(tracks: list[dict], returncode: int = 0) -> types.SimpleNamespace:
	"""Return a minimal subprocess result carrying mediainfo JSON."""
	stdout = json.dumps({"media": {"track": tracks}})
	result = types.SimpleNamespace(
		returncode=returncode,
		stdout=stdout,
		stderr="mediainfo detail\n",
	)
	return result


#============================================
def test_probe_video_uses_general_frame_count_fallback(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""A General track supplies count when the Video track omits it."""
	result = _mediainfo_result([
		{"@type": "General", "FrameCount": "90"},
		{"@type": "Video", "Width": "640", "Height": "360", "FrameRate": "30"},
	])
	monkeypatch.setattr(video_artifacts.shutil, "which", lambda _name: "/tool/mediainfo")
	monkeypatch.setattr(video_artifacts.subprocess, "run", lambda *_args, **_kwargs: result)

	info = video_artifacts.probe_video("runner.mkv")

	assert info == {
		"width": 640,
		"height": 360,
		"fps": 30.0,
		"frame_count": 90,
		"duration_s": 3.0,
	}


#============================================
def test_probe_video_uses_general_duration_fallback(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""A General duration derives count when no track reports it."""
	result = _mediainfo_result([
		{"@type": "Video", "Width": "1920", "Height": "1080", "FrameRate": "25"},
		{"@type": "General", "Duration": "1.96"},
	])
	monkeypatch.setattr(video_artifacts.shutil, "which", lambda _name: "/tool/mediainfo")
	monkeypatch.setattr(video_artifacts.subprocess, "run", lambda *_args, **_kwargs: result)

	info = video_artifacts.probe_video("runner.mkv")

	assert info["frame_count"] == 49
	assert info["duration_s"] == 1.96


#============================================
def test_probe_video_reports_missing_tool_and_failed_command(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Missing mediainfo and a failed mediainfo command remain explicit errors."""
	monkeypatch.setattr(video_artifacts.shutil, "which", lambda _name: None)
	with pytest.raises(RuntimeError, match="mediainfo not found in PATH"):
		video_artifacts.probe_video("runner.mkv")

	result = _mediainfo_result([], returncode=1)
	monkeypatch.setattr(video_artifacts.shutil, "which", lambda _name: "/tool/mediainfo")
	monkeypatch.setattr(video_artifacts.subprocess, "run", lambda *_args, **_kwargs: result)
	with pytest.raises(RuntimeError, match="mediainfo failed: mediainfo detail"):
		video_artifacts.probe_video("runner.mkv")


#============================================
def _identity(frame_count: int = 10) -> dict:
	"""Return a complete identity block for comparison tests."""
	identity = {
		"basename": "runner.mkv",
		"size_bytes": 100,
		"width": 640,
		"height": 360,
		"fps": 30.0,
		"frame_count": frame_count,
		"duration_s": frame_count / 30.0,
	}
	return identity


#============================================
def test_check_identity_mismatch_rejects_incompatible_json_identity(
	tmp_path: pathlib.Path,
) -> None:
	"""A source-geometry mismatch rejects a consuming JSON artifact."""
	path = tmp_path / "seeds.json"
	path.write_text(json.dumps({"video_identity": _identity(11)}))

	with pytest.raises(RuntimeError, match="incompatible video geometry"):
		video_artifacts.check_identity_mismatch("seeds", str(path), _identity())


#============================================
def test_check_identity_mismatch_rejects_incompatible_npz_identity(
	tmp_path: pathlib.Path,
) -> None:
	"""A source-geometry mismatch rejects a consuming NPZ artifact."""
	path = tmp_path / "boxes.npz"
	torso_box_coords_io.write_torso_box_coords(str(path), {
		"solved_intervals": {},
		"video_identity": _identity(11),
		"solve_complete": True,
	})

	with pytest.raises(RuntimeError, match="incompatible video geometry"):
		video_artifacts.check_identity_mismatch("intervals", str(path), _identity())


#============================================
def test_solve_clears_incompatible_derived_artifact(tmp_path: pathlib.Path) -> None:
	"""Solve discards interval output that belongs to another source video."""
	path = tmp_path / "boxes.npz"
	torso_box_coords_io.write_torso_box_coords(str(path), {
		"solved_intervals": {},
		"video_identity": _identity(11),
		"solve_complete": True,
	})

	cleared = video_artifacts.clear_incompatible_derived_artifact(
		"solved intervals", str(path), _identity(),
	)

	assert cleared is True
	assert not path.exists()
