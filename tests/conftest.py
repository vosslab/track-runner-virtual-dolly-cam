# Exclude both end-to-end tiers from pytest collection. tests/playwright/
# holds browser-driven tests (Playwright), and tests/e2e/ holds heavier
# shell/Python whole-system runners. Both run outside pytest -- see
# docs/PLAYWRIGHT_USAGE.md and docs/E2E_TESTS.md.
collect_ignore = ["e2e", "playwright"]

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

# REPO_HYGIENE_FILTERS is the repo-local hygiene-exclusion registry (Layer 2).
# file_utils.discover_files reads it from this conftest, which is the right
# home because propagation only merges the collect_ignore block above into this
# file; the rest of conftest survives and may differ per repo. Vendored files
# (file_utils.py and every tests/test_*.py) get overwritten by propagation,
# so they must hold no repo-specific data. Put repo-specific exclusions here.
#
# Shape and rules:
#   - It is a dict: key -> list of repo-relative POSIX glob patterns.
#   - Keys are "all" or a vendored test key. A test key is the test filename
#     stem with the leading "test_" removed (test_pyflakes_code_lint.py ->
#     "pyflakes_code_lint", test_ascii_compliance.py -> "ascii_compliance").
#   - Patterns match repo-relative POSIX paths via fnmatch.fnmatchcase
#     (case-sensitive). A match excludes the file from that test.
#   - "all" patterns apply to every test; a test-key list applies only when
#     that test_key is passed to discover_files.
#   - Recursive directory exclusions need an explicit /** because fnmatch's *
#     does not cross "/". Use "temp_scripts/**" to exclude a whole subtree.
#
# This template has no repo-specific exclusions, so the registry is empty.
# Example entries (commented out; this repo needs none):
#   REPO_HYGIENE_FILTERS = {
#       "all": ["temp_scripts/**", "TEMPLATE.py"],
#       "ascii_compliance": ["human_readable-*.html"],
#       "pyflakes_code_lint": ["devel/scratch_*.py"],
#   }
REPO_HYGIENE_FILTERS = {}
