"""Portable CLI parser and dispatch checks for the extracted mode modules."""

# Standard Library
import pathlib
import sys

# PIP3 modules
import pytest

# local repo modules
import cli


#============================================
def _dispatch_mode(
	monkeypatch: pytest.MonkeyPatch,
	tmp_path: pathlib.Path,
	mode_name: str,
) -> list[str]:
	"""Run CLI orchestration with local stand-ins and return the selected mode."""
	input_path = tmp_path / "runner.mkv"
	input_path.write_bytes(b"")
	dispatched_modes = []

	# Keep the test at the CLI boundary: parse a real command line while every
	# external/video dependency is represented by a small deterministic stand-in.
	monkeypatch.setattr(
		sys, "argv", ["track_runner.py", "-i", str(input_path), mode_name],
	)
	monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/tool")
	monkeypatch.setattr(cli.tr_paths, "ensure_data_dir", lambda: None)
	monkeypatch.setattr(cli.tr_paths, "default_config_path", lambda _path: "config.yaml")
	monkeypatch.setattr(cli.tr_paths, "default_seeds_path", lambda _path: "seeds.json")
	monkeypatch.setattr(
		cli.tr_paths, "default_interval_scores_path", lambda _path: "scores.json",
	)
	monkeypatch.setattr(
		cli.tr_paths, "default_torso_box_coords_path", lambda _path: "boxes.npz",
	)
	monkeypatch.setattr(cli.tr_paths, "default_encode_analysis_path", lambda _path: "analysis.yaml")
	monkeypatch.setattr(cli.tr_config, "resolve_config", lambda *_args, **_kwargs: ({}, True))
	monkeypatch.setattr(cli.tr_config, "validate_config", lambda _config: None)
	monkeypatch.setattr(
		cli.modes.video_artifacts,
		"probe_video",
		lambda _path: {"fps": 30.0, "width": 640, "height": 360,
			"frame_count": 3, "duration_s": 0.1},
	)
	monkeypatch.setattr(
		cli.tr_video_identity, "make_video_identity", lambda *_args: {"id": "local"},
	)
	monkeypatch.setattr(
		cli.modes.video_artifacts, "check_identity_mismatch", lambda *_args: None,
	)
	monkeypatch.setattr(cli.fastread_video, "resolve_video_context", lambda _path: None)

	# Each owner is intercepted so the assertion proves the CLI routed to the
	# owning module without opening a reader or invoking an interactive mode.
	for candidate_name in (
		"seed", "edit", "target", "solve", "refine", "setup", "encode", "analyze",
	):
		candidate_module = getattr(cli.modes, candidate_name)
		monkeypatch.setattr(
			candidate_module,
			"run",
			lambda *_args, name=candidate_name: dispatched_modes.append(name),
		)

	cli.main()
	return dispatched_modes


#============================================
@pytest.mark.parametrize(
	("mode_name", "expected_owner"),
	[("seed", "seed"), ("solve", "solve"), ("encode", "encode")],
)
def test_cli_routes_representative_modes_to_their_owners(
	monkeypatch: pytest.MonkeyPatch,
	tmp_path: pathlib.Path,
	mode_name: str,
	expected_owner: str,
) -> None:
	"""Representative working commands dispatch through their mode modules."""
	dispatched_modes = _dispatch_mode(monkeypatch, tmp_path, mode_name)
	assert dispatched_modes == [expected_owner]
