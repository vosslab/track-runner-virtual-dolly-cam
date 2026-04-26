"""CLI override parsing for encode mode.

Focused behavioral tests for the one non-trivial piece of CLI parsing
logic added in Phase 4: the WxH resolution parser. Argparse wiring and
override-assignment are pure library behavior and intentionally not
tested here per docs/PYTHON_STYLE.md pytest guidance.
"""

import pytest

import cli


#============================================
def test_parse_resolution_WxH_round_trip():
	# parse WxH into [W, H]; this is the one real parsing step
	assert cli._parse_resolution("1920x1080") == [1920, 1080]
	assert cli._parse_resolution("1280X720") == [1280, 720]


#============================================
def test_parse_resolution_rejects_bad_forms():
	# error detection: missing 'x', wrong separator, empty sides
	with pytest.raises(RuntimeError, match="WxH"):
		cli._parse_resolution("1920")
	with pytest.raises(RuntimeError, match="WxH"):
		cli._parse_resolution("1920,1080")
	with pytest.raises(RuntimeError, match="WxH"):
		cli._parse_resolution("x1080")
