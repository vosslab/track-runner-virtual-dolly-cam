#!/usr/bin/env python3
"""Regenerate --help blocks in docs/modes/*.md files."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "track_runner"))

# local repo modules
import ui.keymap as keymap_module

MODES = ["prepare", "setup", "seed", "solve", "target", "refine", "edit", "encode", "analyze"]

def get_repo_root() -> str:
	"""Get repository root using git."""
	result = subprocess.run(
		["git", "rev-parse", "--show-toplevel"],
		check=True,
		capture_output=True,
		text=True
	)
	return result.stdout.strip()

def get_help_text(repo_root: str, mode: str) -> str:
	"""Run track_runner.py <mode> -h and capture output."""
	result = subprocess.run(
		["python3", "track_runner/track_runner.py", "-i", "_dummy.mp4", mode, "-h"],
		cwd=repo_root,
		check=False,
		capture_output=True,
		text=True
	)
	# Combine stdout and stderr; help usually goes to stdout but may go to stderr
	output = result.stdout + result.stderr
	return output

def normalize_help_text(text: str) -> str:
	"""Normalize help text for idempotency."""
	# Split into lines, rstrip each line, rejoin
	lines = text.split('\n')
	lines = [line.rstrip() for line in lines]
	# Remove leading/trailing empty lines
	while lines and not lines[0]:
		lines.pop(0)
	while lines and not lines[-1]:
		lines.pop()
	# Rejoin with single trailing newline
	return '\n'.join(lines) + '\n'

def refresh_mode_doc(repo_root: str, mode: str) -> None:
	"""Refresh docs/modes/<MODE>.md with help text for mode."""
	mode_upper = mode.upper()
	doc_path = Path(repo_root) / "docs" / "modes" / f"{mode_upper}.md"

	if not doc_path.exists():
		raise SystemExit(f"ERROR: docs/modes/{mode_upper}.md does not exist")

	# Read current file
	content = doc_path.read_text()

	begin_marker = f"<!-- BEGIN AUTO HELP: {mode} -->"
	end_marker = f"<!-- END AUTO HELP: {mode} -->"

	if begin_marker not in content:
		raise SystemExit(f"ERROR: {doc_path} missing begin marker: {begin_marker}")
	if end_marker not in content:
		raise SystemExit(f"ERROR: {doc_path} missing end marker: {end_marker}")

	begin_idx = content.find(begin_marker)
	end_idx = content.find(end_marker)

	if begin_idx > end_idx:
		raise SystemExit(f"ERROR: {doc_path} markers out of order")

	# Get help text
	help_text = get_help_text(repo_root, mode)
	help_text = normalize_help_text(help_text)

	# Build replacement: blank line, fenced block, blank line
	replacement = f"\n```text\n{help_text}```\n"

	# Build new content
	before = content[:begin_idx + len(begin_marker)]
	after = content[end_idx:]
	new_content = before + replacement + after

	# Check if changed
	if new_content == content:
		print(f"unchanged docs/modes/{mode_upper}.md")
		return

	# Write back
	doc_path.write_text(new_content)
	print(f"refreshed docs/modes/{mode_upper}.md")


def refresh_keybindings_doc(repo_root: str, check_only: bool = False) -> None:
	"""Generate or verify the keybinding reference from ui.keymap."""
	doc_path = Path(repo_root) / "docs" / "TRACK_RUNNER_KEYBINDINGS.md"
	generated = keymap_module.render_keybindings_markdown()
	current = doc_path.read_text()
	if current == generated:
		print("unchanged docs/TRACK_RUNNER_KEYBINDINGS.md")
		return
	if check_only:
		raise SystemExit(
			"ERROR: docs/TRACK_RUNNER_KEYBINDINGS.md is out of date; "
			"run tools/refresh_mode_docs.py --keybindings"
		)
	doc_path.write_text(generated)
	print("refreshed docs/TRACK_RUNNER_KEYBINDINGS.md")

def main() -> None:
	"""Refresh all mode docs."""
	repo_root = get_repo_root()
	if len(sys.argv) == 2 and sys.argv[1] == "--keybindings":
		refresh_keybindings_doc(repo_root)
		return
	if len(sys.argv) == 2 and sys.argv[1] == "--check-keybindings":
		refresh_keybindings_doc(repo_root, check_only=True)
		return
	if len(sys.argv) != 1:
		raise SystemExit(
			"Usage: refresh_mode_docs.py [--keybindings|--check-keybindings]"
		)

	for mode in MODES:
		try:
			refresh_mode_doc(repo_root, mode)
		except SystemExit as e:
			print(str(e), file=sys.stderr)
			sys.exit(1)

if __name__ == '__main__':
	main()
