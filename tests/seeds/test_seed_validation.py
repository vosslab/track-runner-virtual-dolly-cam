"""Portable tests for solve-mode seed endpoint preflight validation."""

# Standard Library
import pathlib

# PIP3 modules
import pytest

# local repo modules
import modes.shared as mode_shared
import modes.seed_validation as seed_validation
import state_io


#============================================
def _seed(
	frame_index: int,
	status: str,
	pass_num: int = 1,
	torso_box: list[int] | None = None,
) -> dict:
	"""Build a minimal interval-endpoint seed."""
	seed = {
		"frame_index": frame_index,
		"status": status,
		"pass": pass_num,
	}
	if torso_box is not None:
		seed["torso_box"] = torso_box
	return seed


#============================================
def test_validate_usable_seeds_accepts_approximate_only_endpoints() -> None:
	"""Two approximate seeds form a valid, low-confidence interval."""
	usable, visible_count, partial_count = seed_validation.validate_usable_seeds([
		_seed(30, "approximate", torso_box=[10, 20, 30, 40]),
		_seed(10, "approximate", torso_box=[11, 21, 30, 40]),
	])

	assert [seed["frame_index"] for seed in usable] == [10, 30]
	assert (visible_count, partial_count) == (0, 0)


#============================================
def test_validate_usable_seeds_excludes_not_in_frame_and_deduplicates(
	capsys: pytest.CaptureFixture[str],
) -> None:
	"""NIF seeds are excluded and duplicate frames keep their latest pass."""
	usable, visible_count, partial_count = seed_validation.validate_usable_seeds([
		_seed(40, "not_in_frame"),
		_seed(20, "partial", pass_num=1),
		_seed(20, "approximate", pass_num=2, torso_box=[10, 20, 30, 40]),
		_seed(10, "visible"),
	])

	output = capsys.readouterr().out
	assert [(seed["frame_index"], seed["status"]) for seed in usable] == [
		(10, "visible"), (20, "approximate"),
	]
	assert (visible_count, partial_count) == (1, 0)
	assert "1 approximate" in output and "1 not_in_frame" in output


#============================================
def test_validate_usable_seeds_reports_canonical_endpoint_shortage() -> None:
	"""The failure says canonical filtering excluded the insufficient seeds."""
	with pytest.raises(RuntimeError, match="canonical filtering; got 1 from 2 raw seeds"):
		seed_validation.validate_usable_seeds([
			_seed(10, "visible"),
			_seed(20, "not_in_frame"),
		])


#============================================
def test_seed_deduplication_keeps_source_identity(tmp_path: pathlib.Path) -> None:
	"""Deduplicating a seed file retains the current source identity."""
	seeds_path = str(tmp_path / "clip.seeds.json")
	identity = {"source_sha256": "current-source"}
	state_io.write_seeds(seeds_path, {
		"header": "track_runner_seeds_v3",
		"video_identity": identity,
		"seeds": [
			_seed(10, "visible", pass_num=1, torso_box=[10, 20, 30, 40]),
			_seed(10, "visible", pass_num=2, torso_box=[11, 21, 30, 40]),
		],
	})

	seeds = mode_shared._load_and_deduplicate_seeds(seeds_path)

	assert [(seed["frame_index"], seed["pass"]) for seed in seeds] == [(10, 2)]
	assert state_io.load_seeds(seeds_path)["video_identity"] == identity
