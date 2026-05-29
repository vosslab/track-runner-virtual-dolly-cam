"""
Tests for walk_debug_log.DebugLogWriter and DebugLogRow.

Covers CSV value round-trip, None-to-blank rendering, status validation, and
the context-manager protocol -- behaviors the e2e baseline gate reads but does
not isolate at the writer level.
"""

import csv
import pathlib
import sys

# Ensure tools/blob_walk_v2 is importable. The package root goes on sys.path so
# the bare import walk_paths resolves; setup() then adds the core/ subdir where
# walk_debug_log now lives.
REPO_ROOT = pathlib.Path(__file__).parent.parent
_BLOB_WALK_DIR = str(REPO_ROOT / "tools" / "blob_walk_v2")
if _BLOB_WALK_DIR not in sys.path:
	sys.path.insert(0, _BLOB_WALK_DIR)
import walk_paths
walk_paths.setup()

import walk_debug_log


#============================================

def test_write_row_round_trips_values(tmp_path):
	"""Write one row, read it back; the written values survive the CSV round-trip."""
	csv_file = tmp_path / "debug.csv"
	writer = walk_debug_log.DebugLogWriter(csv_file)

	row = walk_debug_log.DebugLogRow(
		frame_index=100,
		step=0,
		direction="+",
		status="accepted"
	)
	writer.write_row(row)
	writer.close()

	with open(csv_file, 'r') as f:
		reader = csv.DictReader(f)
		rows = list(reader)

	assert rows[0]["frame_index"] == "100"
	assert rows[0]["status"] == "accepted"


def test_none_fields_render_blank(tmp_path):
	"""
	Write a row with most optional fields None.
	Read CSV. Assert empty cells for None columns (not literal "None").
	"""
	csv_file = tmp_path / "debug.csv"
	writer = walk_debug_log.DebugLogWriter(csv_file)

	row = walk_debug_log.DebugLogRow(
		frame_index=50,
		step=1,
		direction="-",
		status="soft_miss_no_blob",
		dt=None,
		torso_w_px=None,
		cand_cx=None,
		pred_cy=None
	)
	writer.write_row(row)
	writer.close()

	# Read the CSV
	with open(csv_file, 'r') as f:
		reader = csv.DictReader(f)
		rows = list(reader)

	data_row = rows[0]
	# Assert None fields are rendered as empty string, not "None"
	assert data_row["dt"] == ""
	assert data_row["torso_w_px"] == ""
	assert data_row["cand_cx"] == ""
	assert data_row["pred_cy"] == ""


def test_invalid_status_raises_value_error(tmp_path):
	"""
	Construct writer, attempt write_row with invalid status.
	Assert ValueError raised and message names the allowed set.
	"""
	csv_file = tmp_path / "debug.csv"
	writer = walk_debug_log.DebugLogWriter(csv_file)

	row = walk_debug_log.DebugLogRow(
		frame_index=10,
		step=0,
		direction="+",
		status="not_a_real_status"
	)

	try:
		writer.write_row(row)
		assert False, "Expected ValueError"
	except ValueError as e:
		error_msg = str(e)
		# Assert message mentions the invalid status
		assert "not_a_real_status" in error_msg
		# Assert message mentions allowed set
		assert "accepted" in error_msg
		assert "soft_miss_no_blob" in error_msg
	finally:
		writer.close()


def test_context_manager_closes_file(tmp_path):
	"""
	Use DebugLogWriter as a context manager.
	After the with block, assert file exists and handle is closed.
	"""
	csv_file = tmp_path / "debug.csv"

	with walk_debug_log.DebugLogWriter(csv_file) as writer:
		row = walk_debug_log.DebugLogRow(
			frame_index=5,
			step=0,
			direction="+",
			status="accepted"
		)
		writer.write_row(row)

	# After the with block the file should be closed and readable, proving the
	# context manager flushed and released the handle.
	with open(csv_file, 'r') as f:
		reader = csv.DictReader(f)
		rows = list(reader)
	assert rows[0]["frame_index"] == "5"
