"""Tests for the declarative annotation keybinding registry."""

# Standard Library
from pathlib import Path

# PIP3 modules
import PySide6.QtCore
import pytest

# local repo modules
import file_utils
import ui.keymap as keymap_module
import tools.refresh_mode_docs as refresh_mode_docs_module


#============================================


def test_keymap_resolves_shifted_seed_scrub_binding() -> None:
	"""A modifier-specific seed binding resolves to its declared action."""
	binding = keymap_module.find_binding(
		"seed",
		PySide6.QtCore.Qt.Key.Key_Left,
		PySide6.QtCore.Qt.KeyboardModifier.ShiftModifier,
	)

	assert binding.action == "scrub_previous"


#============================================


def test_keymap_requires_declared_modifier_variant() -> None:
	"""A modifier changes dispatch unless the registry declares that variant."""
	binding = keymap_module.find_binding(
		"edit",
		PySide6.QtCore.Qt.Key.Key_P,
		PySide6.QtCore.Qt.KeyboardModifier.ControlModifier,
	)

	assert binding is None


#============================================


def test_keymap_resolves_shifted_question_help_binding() -> None:
	"""The question-mark binding carries the Shift modifier declaratively."""
	binding = keymap_module.find_binding(
		"seed",
		PySide6.QtCore.Qt.Key.Key_Question,
		PySide6.QtCore.Qt.KeyboardModifier.ShiftModifier,
	)

	assert binding.action == "show_help"


#============================================


def test_generated_keybindings_doc_matches_registry() -> None:
	"""The checked-in reference is exactly the registry's generated text."""
	doc_path = Path(file_utils.get_repo_root()) / "docs" / "TRACK_RUNNER_KEYBINDINGS.md"

	assert doc_path.read_text() == keymap_module.render_keybindings_markdown()


#============================================


def test_keybindings_drift_check_rejects_mismatched_doc(tmp_path: Path) -> None:
	"""The check mode fails when a generated reference is deliberately altered."""
	docs_dir = tmp_path / "docs"
	docs_dir.mkdir()
	doc_path = docs_dir / "TRACK_RUNNER_KEYBINDINGS.md"
	doc_path.write_text("deliberate mismatch\n")

	with pytest.raises(SystemExit, match="out of date"):
		refresh_mode_docs_module.refresh_keybindings_doc(tmp_path, check_only=True)
