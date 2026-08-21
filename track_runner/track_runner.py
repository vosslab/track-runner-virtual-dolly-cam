#!/usr/bin/env python3
"""Entry point for the track_runner tool."""

# Standard Library
import os
import sys

# ensure repo root is importable when launched as ./track_runner/track_runner.py
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
if REPO_ROOT not in sys.path:
	sys.path.insert(0, REPO_ROOT)

if __name__ == "__main__":
	# Keep the CLI dependency graph out of a bare ``import track_runner``.
	# That import name can occur when track_runner/ itself is on sys.path; it
	# must remain able to host project-qualified submodules such as
	# track_runner.blend_commitment without pulling in UI mode imports.
	import cli
	cli.main()
else:
	# The launcher shares its name with the track_runner package. In bare-module
	# environments, make it a package-compatible parent so qualified
	# project imports resolve beside this file instead of entering the CLI graph.
	__path__ = [THIS_DIR]
