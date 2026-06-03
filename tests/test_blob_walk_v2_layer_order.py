"""Unit tests for walk_palette.resolve_layer_order validation behavior.

Covers the resolver contract:
  - Missing walk_tile_layer_order key -> returns the built-in default (backward compat).
  - Unknown layer name in list -> raises ValueError.
  - Duplicate layer name in list -> raises ValueError.
  - Omitted known layer name -> raises ValueError.

Does NOT assert tunable constants or the exact contents of the default order.
All offline, deterministic, well under one second.
"""

import sys
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_BLOB_WALK_DIR = _REPO_ROOT / 'tools' / 'blob_walk_v2'
_RENDER_DIR = _BLOB_WALK_DIR / 'render'
if str(_RENDER_DIR) not in sys.path:
	sys.path.insert(0, str(_RENDER_DIR))
if str(_BLOB_WALK_DIR) not in sys.path:
	sys.path.insert(0, str(_BLOB_WALK_DIR))
import walk_paths
walk_paths.setup()

import walk_palette


#============================================
def _make_valid_order():
	"""Return a list with every known layer exactly once (valid input)."""
	# Use the private constant so the test stays consistent with the module.
	return list(walk_palette._KNOWN_LAYERS)


#============================================
def test_missing_key_returns_default(monkeypatch):
	"""Missing walk_tile_layer_order key falls back to the built-in default."""
	# Patch load_palette to return a dict without the key.
	import overlay_config
	monkeypatch.setattr(overlay_config, 'load_palette', lambda: {})
	result = walk_palette.resolve_layer_order()
	# Result must be a non-empty sequence and contain all known layers exactly once.
	known_set = set(walk_palette._KNOWN_LAYERS)
	result_set = set(result)
	assert result_set == known_set


#============================================
def test_unknown_name_raises(monkeypatch):
	"""Unknown layer name in the YAML list raises ValueError loudly."""
	import overlay_config
	valid = _make_valid_order()
	# Replace one entry with an unknown name.
	bad_order = valid[:-1] + ["not_a_real_layer"]
	monkeypatch.setattr(overlay_config, 'load_palette', lambda: {"walk_tile_layer_order": bad_order})
	with pytest.raises(ValueError, match="unknown"):
		walk_palette.resolve_layer_order()


#============================================
def test_duplicate_name_raises(monkeypatch):
	"""Duplicate layer name in the YAML list raises ValueError loudly."""
	import overlay_config
	valid = _make_valid_order()
	# Remove last, add first entry twice -> duplicate + omission.
	dup_order = valid[:-1] + [valid[0]]
	monkeypatch.setattr(overlay_config, 'load_palette', lambda: {"walk_tile_layer_order": dup_order})
	with pytest.raises(ValueError, match="duplicate"):
		walk_palette.resolve_layer_order()


#============================================
def test_omitted_layer_raises(monkeypatch):
	"""Omitting a known layer name from the YAML list raises ValueError loudly."""
	import overlay_config
	valid = _make_valid_order()
	# Drop one layer from the list.
	omitted_order = valid[:-1]
	monkeypatch.setattr(overlay_config, 'load_palette', lambda: {"walk_tile_layer_order": omitted_order})
	with pytest.raises(ValueError, match="omitted"):
		walk_palette.resolve_layer_order()


#============================================
def test_valid_order_is_accepted(monkeypatch):
	"""A complete, non-duplicate list of all known layers is accepted without error."""
	import overlay_config
	valid = _make_valid_order()
	monkeypatch.setattr(overlay_config, 'load_palette', lambda: {"walk_tile_layer_order": valid})
	result = walk_palette.resolve_layer_order()
	assert set(result) == set(walk_palette._KNOWN_LAYERS)
