"""Shared sys.path bootstrap for blob_walk_v2 scripts.

Every blob_walk_v2 entry point needs the same directories on sys.path so that
track_runner modules, repo-root packages (common_tools), tests helpers, and
sibling blob_walk_v2 modules all import by bare name. That bootstrap was
copy-pasted at the top of each script; this module is the single source of
truth for it.

Chicken-and-egg note: a module that sets up sys.path cannot be imported by bare
name until tools/blob_walk_v2 is already on sys.path. In every real launch
context that directory is already present before any blob_walk_v2 module loads:
running a script directly puts the script's own directory on sys.path[0], and
the e2e harness inserts tools/blob_walk_v2 explicitly before its first import.
So a plain `import walk_paths` resolves; callers then call setup() to apply the
path entries. setup() returns the repo root so callers that need it (for
locating tr_config, dump_step1, overlay_styles.yaml) get it from one place.

The inserted set is the superset relied on across all callers (track_runner,
tests, repo root, and this blob_walk_v2 directory). Inserting the superset is
safe: these are the same directories the standalone scripts and the e2e harness
already place on sys.path, so no existing import resolves differently.
"""

# Standard Library
import os
import sys

# This file lives at tools/blob_walk_v2/, so the repo root is three levels up.
_BLOB_WALK_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_BLOB_WALK_DIR))
_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, 'track_runner')
_TESTS_DIR = os.path.join(_REPO_ROOT, 'tests')
# The package modules are split into core/ and render/ subdirs. Both must sit
# on sys.path so the bare-name sibling imports (import walk_walker, etc.) used
# throughout the package keep resolving after the reorg.
_CORE_DIR = os.path.join(_BLOB_WALK_DIR, 'core')
_RENDER_DIR = os.path.join(_BLOB_WALK_DIR, 'render')


#============================================
def setup() -> str:
	"""Insert the blob_walk_v2 import directories onto sys.path.

	Inserts each directory once at the front of sys.path so that track_runner
	modules, repo-root packages, tests helpers, and sibling blob_walk_v2
	modules (root, core/, and render/) all resolve by bare name. Order matches
	the legacy per-script blocks: track_runner, tests, repo root, this
	directory, then the core/ and render/ subdirs.

	Returns:
		str: the absolute repo-root path, for callers that build paths from it.
	"""
	# Insert each directory once, front of sys.path, so bare-name imports work.
	for path in (_TRACK_RUNNER_DIR, _TESTS_DIR, _REPO_ROOT, _BLOB_WALK_DIR, _CORE_DIR, _RENDER_DIR):
		if path not in sys.path:
			sys.path.insert(0, path)
	return _REPO_ROOT
