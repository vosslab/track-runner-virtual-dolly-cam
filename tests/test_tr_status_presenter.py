"""Tests for StatusPresenter severity-badge rendering.

Locks in the contract C4 behavior that pre-race intervals (severity=None)
render a neutral [PRE-RACE] badge instead of crashing in
overlay_config.get_severity_style. Also confirms a classified severity
still routes through overlay_config so the palette remains the source of
truth for color and label.
"""

# PIP3 modules
import pytest

pytest.importorskip("PySide6")

# local repo modules (bare imports resolved by conftest.py)
import ui.status_presenter as status_presenter_module  # noqa: E402

StatusPresenter = status_presenter_module.StatusPresenter


#============================================
def _seed() -> dict:
	"""Minimal seed dict accepted by StatusPresenter.update."""
	return {"frame_index": 100, "status": "visible"}


#============================================
def test_pre_race_severity_none_renders_pre_race_badge(qtbot) -> None:
	"""interval_info with severity=None must not crash and shows PRE-RACE."""
	_ = qtbot
	presenter = StatusPresenter()
	interval_info = {
		"severity": None,
		"agreement": 0.9,
		"margin": 0.5,
		"reasons": [],
	}
	presenter.update(
		seed=_seed(),
		seed_index=0,
		total_seeds=1,
		fps=60.0,
		confidence=None,
		interval_info=interval_info,
	)
	html = presenter.get_widget().text()
	assert "PRE-RACE" in html
	assert status_presenter_module.PRE_RACE_COLOR in html


#============================================
def test_classified_severity_uses_overlay_config_style(qtbot) -> None:
	"""A classified severity routes through overlay_config.get_severity_style."""
	_ = qtbot
	presenter = StatusPresenter()
	interval_info = {
		"severity": "high",
		"agreement": 0.1,
		"margin": 0.05,
		"reasons": [],
	}
	presenter.update(
		seed=_seed(),
		seed_index=0,
		total_seeds=1,
		fps=60.0,
		confidence=None,
		interval_info=interval_info,
	)
	html = presenter.get_widget().text()
	# overlay_config default for "high" is label "HIGH"; palette may
	# override but the label is what the badge prints.
	import overlay_config
	expected_label = overlay_config.get_severity_style("high")["label"]
	assert f"[{expected_label}]" in html
	assert "PRE-RACE" not in html
