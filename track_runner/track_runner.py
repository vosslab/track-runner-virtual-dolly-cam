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

# local repo modules
import cli

if __name__ == "__main__":
	cli.main()
