"""Portable tests for the extracted encode audio seam."""

# Standard Library
from types import SimpleNamespace

# PIP3 modules
import yaml

# local repo modules
import encode_audio
import encode_analysis_report


#============================================
def test_copy_audio_remuxes_video_only_without_input_audio(monkeypatch: object) -> None:
	"""Audio-free inputs remux the cropped stream into the requested container."""
	commands = []
	monkeypatch.setattr(encode_audio.shutil, "which", lambda _name: "/tools/ffmpeg")
	monkeypatch.setattr(encode_audio, "_input_has_audio", lambda _path: False)
	monkeypatch.setattr(
		encode_audio.subprocess,
		"run",
		lambda cmd, **_kwargs: commands.append(cmd) or SimpleNamespace(returncode=0, stderr=""),
	)
	encode_audio.copy_audio("source.mkv", "crop.tmp.mp4", "result.mp4")
	assert commands == [["/tools/ffmpeg", "-y", "-i", "crop.tmp.mp4", "-c:v", "copy", "result.mp4"]]


#============================================
def test_analysis_report_leaf_writes_diagnostic_artifact(tmp_path: object) -> None:
	"""The reporting leaf writes the console and YAML forms from one analysis."""
	analysis = {
		"summary": {"frames": 12, "duration_s": 1.0, "fps": 12.0, "output_size": [640, 360]},
		"motion_stability": {
			"center_jerk_p50": 0.1,
			"center_jerk_p95": 0.2,
			"height_jerk_p50": 0.3,
			"height_jerk_p95": 0.4,
			"crop_size_cv": 0.0,
			"quantization_chatter_fraction": 0.0,
		},
		"confidence": {"mean": 0.9, "low_conf_fraction": 0.0},
		"instability_regions": [],
		"dominant_symptom": "none",
		"seed_suggestions": [],
	}
	solver_context = {
		"seed_density": 1.0,
		"desert_count": 0,
		"seed_gap_mean_s": 0.5,
		"seed_gap_max_s": 0.5,
		"top_seed_gaps": [],
		"velocity_consistency_median": 1.0,
		"size_consistency_median": 1.0,
		"motion_quality_median": 1.0,
	}
	output_path = tmp_path / "report.yaml"
	encode_analysis_report.write_analysis_yaml(analysis, solver_context, str(output_path))
	with open(output_path) as handle:
		doc = yaml.safe_load(handle)
	report = encode_analysis_report.format_analysis_report(
		analysis, solver_context, str(output_path),
	)
	assert doc["track_runner_encode_analysis"] == 1
	assert "instability regions: none detected" in report
