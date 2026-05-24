#!/usr/bin/env python3
"""Dump every track_runner subcommand's --help text to stdout.

Used by the CLI argument audit (docs/active_plans/audits/cli_argument_audit.md)
to produce a stable, reviewable artifact of the live argparse surface
without running the actual tool. Imports cli_args._build_parser() so
required-input validation does not fire.

Usage:
	source source_me.sh && python3 tools/dump_cli_help.py > docs/active_plans/audits/cli_argparse_dump.txt
"""

# Standard Library
import os
import sys
import argparse

# put track_runner/ on sys.path so cli_args' internal `import ui.base_controller` resolves
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
TR_DIR = os.path.join(REPO_ROOT, "track_runner")
if TR_DIR not in sys.path:
	sys.path.insert(0, TR_DIR)
if REPO_ROOT not in sys.path:
	sys.path.insert(0, REPO_ROOT)

# local repo modules
import cli_args


#============================================
def find_subparsers(parser: argparse.ArgumentParser) -> dict:
	"""Return ordered {name: subparser} dict from a parser with subcommands."""
	# argparse stores subparser choices on the _SubParsersAction.
	for action in parser._actions:
		if isinstance(action, argparse._SubParsersAction):
			return action.choices
	return {}


#============================================
def print_header(title: str) -> None:
	bar = "=" * 72
	print(bar)
	print(title)
	print(bar)


#============================================
def main() -> None:
	parser = cli_args._build_parser()
	print_header("track_runner global --help")
	print(parser.format_help())

	subs = find_subparsers(parser)
	# preserve registration order from cli_args._build_parser
	for name, subparser in subs.items():
		print_header(f"track_runner {name} --help")
		print(subparser.format_help())


if __name__ == "__main__":
	main()
