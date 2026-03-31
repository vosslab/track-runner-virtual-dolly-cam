"""Tests for YOLO weight discovery behavior."""

# Standard Library
import os

# local repo modules (bare imports resolved by conftest.py)
import tr_detection


#============================================
def test_ensure_yolo_weights_quiet_missing(tmp_path, capsys):
	"""Quiet mode suppresses missing-weights console instructions."""
	result = tr_detection.ensure_yolo_weights(
		cache_dir=str(tmp_path),
		quiet=True,
	)
	captured = capsys.readouterr()
	assert result == ""
	assert captured.out == ""


#============================================
def test_ensure_yolo_weights_reports_missing(tmp_path, capsys):
	"""Default mode prints missing-weights instructions once requested."""
	result = tr_detection.ensure_yolo_weights(
		cache_dir=str(tmp_path),
	)
	captured = capsys.readouterr()
	assert result == ""
	assert "YOLO ONNX weights not found" in captured.out
	assert os.path.join(str(tmp_path), tr_detection.YOLO_WEIGHTS_FILENAME) in captured.out
