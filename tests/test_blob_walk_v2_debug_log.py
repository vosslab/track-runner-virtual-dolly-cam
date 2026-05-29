"""
Tests for walk_debug_log.DebugLogWriter and DebugLogRow.

Covers CSV I/O, header locking, status validation, and context-manager protocol.
Five deterministic test cases per T1.4 spec.
"""

import csv
import pathlib
import sys

# Ensure tools/blob_walk_v2 is importable
REPO_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools" / "blob_walk_v2"))

import walk_debug_log


#============================================

def test_init_writes_locked_header(tmp_path):
	"""
	Construct DebugLogWriter, close it, read the file.
	Assert first line matches walk_debug_log.HEADER tuple joined by commas.
	"""
	csv_file = tmp_path / "debug.csv"
	writer = walk_debug_log.DebugLogWriter(csv_file)
	writer.close()

	# Read the CSV and extract header
	with open(csv_file, 'r') as f:
		reader = csv.reader(f)
		header_line = next(reader)

	# Assert header matches HEADER tuple
	assert tuple(header_line) == walk_debug_log.HEADER


def test_write_row_appends_data(tmp_path):
	"""
	Construct writer, write one row with minimal required fields,
	close, read CSV. Assert 2 lines (header + 1 row) and data matches.
	"""
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

	# Read the CSV
	with open(csv_file, 'r') as f:
		reader = csv.DictReader(f)
		rows = list(reader)

	# Assert exactly 1 data row
	assert len(rows) == 1
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

	# After with block, file should exist
	assert csv_file.exists()

	# File should be readable (proving it was closed properly)
	with open(csv_file, 'r') as f:
		reader = csv.DictReader(f)
		rows = list(reader)
	assert len(rows) == 1
