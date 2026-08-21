"""Declarative keyboard bindings for the annotation workspace.

This module is the single source of truth for controller dispatch, visible
shortcut hints, the F1 dialog, and the generated keybinding reference.
"""

# Standard Library
from dataclasses import dataclass

# PIP3 modules
from PySide6.QtCore import Qt

#============================================

COMMON_MODES = ("seed", "target", "edit")


@dataclass(frozen=True)
class KeyBinding:
	"""One keyboard binding and the action it requests.

	Attributes:
		key: Qt key code, or an inclusive pair for a key range.
		modifiers: Required Qt keyboard modifiers.
		action: Stable controller action identifier.
		label: Human-readable action label.
		modes: Annotation modes where the binding applies.
	"""

	key: int | tuple[int, int]
	modifiers: Qt.KeyboardModifier
	action: str
	label: str
	modes: tuple[str, ...]


# The table deliberately lists binding variants separately.  This keeps modifier
# matching exact, avoids action precedence hidden in controller conditionals, and
# makes every visible shortcut available to documentation generation.
BINDINGS = (
	KeyBinding(Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier, "quit", "Quit / return", COMMON_MODES),
	KeyBinding(Qt.Key.Key_Q, Qt.KeyboardModifier.NoModifier, "quit", "Quit", COMMON_MODES),
	KeyBinding(Qt.Key.Key_P, Qt.KeyboardModifier.NoModifier, "partial_toggle", "Toggle partial draw", COMMON_MODES),
	KeyBinding(Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "approx_toggle", "Toggle approximate draw", COMMON_MODES),
	KeyBinding(Qt.Key.Key_Z, Qt.KeyboardModifier.NoModifier, "zoom_cycle", "Cycle zoom", COMMON_MODES),
	KeyBinding(Qt.Key.Key_V, Qt.KeyboardModifier.NoModifier, "hide_predictions", "Hide predictions", COMMON_MODES),
	KeyBinding(Qt.Key.Key_H, Qt.KeyboardModifier.NoModifier, "toggle_heat", "Toggle heat map", COMMON_MODES),
	KeyBinding(Qt.Key.Key_F1, Qt.KeyboardModifier.NoModifier, "show_help", "Show shortcut help", COMMON_MODES),
	KeyBinding(Qt.Key.Key_Question, Qt.KeyboardModifier.ShiftModifier, "show_help", "Show shortcut help", COMMON_MODES),
	KeyBinding(Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "accept_suggestion", "Accept suggestion", ("seed", "target")),
	KeyBinding(Qt.Key.Key_Enter, Qt.KeyboardModifier.NoModifier, "accept_suggestion", "Accept suggestion", ("seed", "target")),
	KeyBinding((Qt.Key.Key_1, Qt.Key.Key_9), Qt.KeyboardModifier.NoModifier, "select_candidate", "Select suggestion candidate", ("seed", "target")),
	KeyBinding(Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, "skip", "Skip frame", ("seed", "target")),
	KeyBinding(Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier, "scrub_previous_or_pan", "Previous frame / pan left", ("seed", "target")),
	KeyBinding(Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier, "scrub_next_or_pan", "Next frame / pan right", ("seed", "target")),
	KeyBinding(Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier, "scrub_previous", "Previous frame", ("seed", "target")),
	KeyBinding(Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier, "scrub_next", "Next frame", ("seed", "target")),
	KeyBinding(Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier, "scrub_previous", "Previous frame (5x)", ("seed", "target")),
	KeyBinding(Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier, "scrub_next", "Next frame (5x)", ("seed", "target")),
	KeyBinding(Qt.Key.Key_BracketLeft, Qt.KeyboardModifier.NoModifier, "decrease_step", "Decrease scrub step", ("seed", "target")),
	KeyBinding(Qt.Key.Key_BracketRight, Qt.KeyboardModifier.NoModifier, "increase_step", "Increase scrub step", ("seed", "target")),
	KeyBinding(Qt.Key.Key_N, Qt.KeyboardModifier.NoModifier, "not_in_frame", "Mark not in frame", ("seed", "target", "edit")),
	KeyBinding(Qt.Key.Key_F, Qt.KeyboardModifier.NoModifier, "fwd_bwd_average", "Use FWD/BWD average", ("seed", "target")),
	KeyBinding(Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, "keep_or_accept_polish", "Keep seed / accept polish", ("edit",)),
	KeyBinding(Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier, "pan_left", "Pan left", ("edit",)),
	KeyBinding(Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier, "pan_right", "Pan right", ("edit",)),
	KeyBinding(Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier, "previous_seed", "Previous seed", ("edit",)),
	KeyBinding(Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier, "keep_or_accept_polish", "Keep seed / accept polish", ("edit",)),
	KeyBinding(Qt.Key.Key_D, Qt.KeyboardModifier.NoModifier, "delete_seed", "Delete seed", ("edit",)),
	KeyBinding(Qt.Key.Key_Y, Qt.KeyboardModifier.NoModifier, "yolo_polish", "YOLO polish", ("edit",)),
	KeyBinding(Qt.Key.Key_F, Qt.KeyboardModifier.NoModifier, "consensus_polish", "Consensus polish", ("edit",)),
	KeyBinding(Qt.Key.Key_BracketLeft, Qt.KeyboardModifier.NoModifier, "jump_backward", "Jump backward", ("edit",)),
	KeyBinding(Qt.Key.Key_BracketRight, Qt.KeyboardModifier.NoModifier, "jump_forward", "Jump forward", ("edit",)),
	KeyBinding(Qt.Key.Key_L, Qt.KeyboardModifier.NoModifier, "jump_low_confidence", "Jump to low confidence", ("edit",)),
	KeyBinding(Qt.Key.Key_U, Qt.KeyboardModifier.NoModifier, "enter_add_mode", "Add seeds", ("edit",)),
)


def find_binding(mode: str, key: int, modifiers: Qt.KeyboardModifier | None) -> KeyBinding | None:
	"""Return the exact declarative binding for one key event in an annotation mode."""
	actual_modifiers = modifiers or Qt.KeyboardModifier.NoModifier
	for binding in BINDINGS:
		if mode not in binding.modes or binding.modifiers != actual_modifiers:
			continue
		if isinstance(binding.key, tuple):
			key_matches = binding.key[0] <= key <= binding.key[1]
		else:
			key_matches = binding.key == key
		if not key_matches:
			continue
		return binding
	return None


def bindings_for_mode(mode: str) -> tuple[KeyBinding, ...]:
	"""Return bindings applicable to one mode in declaration order."""
	bindings = tuple(binding for binding in BINDINGS if mode in binding.modes)
	return bindings


def key_label(binding: KeyBinding) -> str:
	"""Format a binding's key and modifier for UI and Markdown rendering."""
	modifier_labels = []
	if binding.modifiers & Qt.KeyboardModifier.ShiftModifier:
		modifier_labels.append("Shift")
	if binding.modifiers & Qt.KeyboardModifier.AltModifier:
		modifier_labels.append("Alt")
	key_names = {
		Qt.Key.Key_Escape: "ESC",
		Qt.Key.Key_Return: "ENTER",
		Qt.Key.Key_Enter: "ENTER",
		Qt.Key.Key_Space: "SPACE",
		Qt.Key.Key_Left: "LEFT",
		Qt.Key.Key_Right: "RIGHT",
		Qt.Key.Key_BracketLeft: "[",
		Qt.Key.Key_BracketRight: "]",
		Qt.Key.Key_F1: "F1",
		Qt.Key.Key_Question: "?",
	}
	if isinstance(binding.key, tuple):
		key_text = "1-9"
	else:
		key_text = key_names.get(binding.key)
		if key_text is None:
			key_text = chr(int(binding.key))
	parts = modifier_labels + [key_text]
	text = "+".join(parts)
	return text


def hint_text(mode: str) -> str:
	"""Render the current mode's shortcut hint text directly from bindings."""
	pairs = [f"{key_label(binding)}={binding.label}" for binding in bindings_for_mode(mode)]
	text = "  ".join(pairs)
	return text


def render_keybindings_markdown() -> str:
	"""Render the generated keyboard reference from the declarative table."""
	lines = [
		"# Track runner keybindings",
		"",
		"Generated by `tools/refresh_mode_docs.py` from `track_runner/ui/keymap.py`. Do not edit manually.",
		"",
		"| Key | Action | Modes |",
		"| --- | --- | --- |",
	]
	for binding in BINDINGS:
		modes = ", ".join(binding.modes)
		lines.append(f"| {key_label(binding)} | {binding.label} | {modes} |")
	lines.append("")
	text = "\n".join(lines)
	return text
