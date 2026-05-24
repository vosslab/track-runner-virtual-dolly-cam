#!/usr/bin/env python3
"""Guard wrapper that refuses to run subcommands that write to tr_config/.

This is a SAFETY rail. tr_config/ holds user-authored seeds and solved interval
data, never to be touched by experiments.

CLI: tools/safe_experiment_wrapper.py -- <subcommand> [<args>...]

Parses argv, scans args for output-related flags and tr_config paths,
resolves paths, and refuses execution if any would write into tr_config/.
"""

# Standard Library
import os
import sys
import subprocess
from pathlib import Path

#============================================

def get_repo_root() -> str:
	"""Return the repository root via git rev-parse --show-toplevel."""
	try:
		result = subprocess.run(
			["git", "rev-parse", "--show-toplevel"],
			capture_output=True,
			text=True,
			check=True
		)
		return result.stdout.strip()
	except (subprocess.CalledProcessError, FileNotFoundError):
		# Fallback: assume tools/ is one level below repo root
		return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#============================================

def is_path_under_tr_config(path_str: str, tr_config_dir: Path) -> bool:
	"""Check if a path is under tr_config or matches the exact pattern.

	Returns True if:
	- resolved path is under tr_config_dir (Path.is_relative_to)
	- resolved path matches */tr_config or */tr_config/* exactly
	"""
	try:
		resolved = Path(path_str).resolve()
	except (ValueError, RuntimeError):
		# Invalid path; be conservative and reject
		return True

	# Check if resolved path is under tr_config_dir
	try:
		resolved.relative_to(tr_config_dir)
		return True
	except ValueError:
		pass

	# Check for */tr_config or */tr_config/* patterns
	parts = resolved.parts
	if "tr_config" in parts:
		return True

	return False

#============================================

def parse_args() -> tuple:
	"""Parse command-line arguments.

	Expected format: safe_experiment_wrapper.py -- <subcommand> [<args>...]
	The -- separator is required.

	Returns (subcommand, args_list).
	"""
	# Check that sys.argv has at least 3 elements: script name, --, subcommand
	if len(sys.argv) < 3:
		sys.stderr.write("ERROR: Expected format: script.py -- <subcommand> [<args>...]\n")
		sys.exit(1)

	# Check that second argument is --
	if sys.argv[1] != "--":
		sys.stderr.write("ERROR: Expected '--' separator after script name\n")
		sys.exit(1)

	subcommand = sys.argv[2]
	subcommand_args = sys.argv[3:]

	return subcommand, subcommand_args

#============================================

def extract_output_paths(args: list) -> list:
	"""Extract candidate output paths from argument list.

	Looks for:
	- -o <path>, --output <path>, --output-dir <path>, --output-root <path>, --out <path>
	- -c <path>, --tr-config-dir <path>
	- key=value pairs where key contains 'output' or 'tr_config'

	Returns list of candidate paths.
	"""
	paths = []
	i = 0
	while i < len(args):
		arg = args[i]

		# Handle flag=value form
		if "=" in arg and not arg.startswith("-"):
			key, value = arg.split("=", 1)
			if "output" in key.lower() or "tr_config" in key.lower():
				paths.append(value)
			i += 1
			continue

		# Handle -flag value form (check if flag is in the set)
		if arg in ["-o", "--output", "--output-dir", "--output-root", "--out", "-c", "--tr-config-dir"]:
			if i + 1 < len(args):
				paths.append(args[i + 1])
				i += 2
			else:
				i += 1
		else:
			i += 1

	return paths

#============================================

def check_positional_args(subcommand: str, args: list) -> bool:
	"""Check if dangerous subcommands or tr_config appear in positional args.

	Returns True if safe, False if we should refuse.
	Refuses if:
	- subcommand is rm, git rm, or similar
	- tr_config appears as a positional argument
	"""
	# Refuse explicit rm commands
	if subcommand in ["rm", "git"]:
		# For 'git', check if next arg is 'rm'
		if subcommand == "git" and args and args[0] == "rm":
			return False
		# For 'rm', any invocation is dangerous near tr_config
		if subcommand == "rm":
			# Check if any arg contains tr_config
			for arg in args:
				if "tr_config" in arg:
					return False

	# Check if tr_config appears as a positional argument
	for arg in args:
		# Skip flags and flag values
		if arg.startswith("-"):
			continue
		if "tr_config" in arg:
			return False

	return True

#============================================

def main():
	"""Main driver: parse args, validate safety, and execute or refuse."""
	subcommand, subcommand_args = parse_args()

	# Get repo root and tr_config directory
	repo_root = get_repo_root()
	tr_config_dir = Path(repo_root) / "tr_config"

	# Check positional args for dangerous subcommands
	if not check_positional_args(subcommand, subcommand_args):
		offending = next((arg for arg in subcommand_args if "tr_config" in arg), subcommand)
		sys.stderr.write(
			f"REFUSED: refusing to run subcommand that would write to tr_config/. "
			f"Offending arg: {offending}\n"
		)
		sys.exit(2)

	# Extract and validate output paths
	output_paths = extract_output_paths(subcommand_args)
	for path in output_paths:
		if is_path_under_tr_config(path, tr_config_dir):
			sys.stderr.write(
				f"REFUSED: refusing to run subcommand that would write to tr_config/. "
				f"Offending arg: {path}\n"
			)
			sys.exit(2)

	# Safe to execute
	result = subprocess.run(
		[subcommand] + subcommand_args,
		check=False
	)
	sys.exit(result.returncode)

#============================================

if __name__ == "__main__":
	main()
