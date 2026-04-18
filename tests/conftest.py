"""Shared pytest configuration for track_runner tests.

Adds track_runner/ to sys.path so bare imports (e.g. import scoring)
work the same way they do at runtime via source_me.sh.
"""

# Standard Library
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TR_DIR = os.path.join(_REPO_ROOT, "track_runner")
# track_runner/ for bare submodule imports (e.g. import scoring)
if _TR_DIR not in sys.path:
	sys.path.insert(0, _TR_DIR)
# repo root for package imports that cross the repo (e.g. import
# common_tools.frame_filters) so tests that pull in cli / encoder work
# without needing source_me.sh
if _REPO_ROOT not in sys.path:
	sys.path.insert(0, _REPO_ROOT)
