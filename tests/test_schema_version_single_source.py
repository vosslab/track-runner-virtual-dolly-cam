"""Test that enforces contract C8: single source of truth for SCHEMA_VERSION.

This test scans production code for legacy constant names
(DIAGNOSTICS_HEADER_VALUE, PRE_RACE_REFERENCE_SCHEMA_VERSION,
INTERVAL_SCORE_SCHEMA_VERSION) and verifies they are either:
1. Absent, or
2. Present as aliases whose right-hand side is exactly SCHEMA_VERSION

This prevents accidental C8 drift where independent versions creep back in.
"""

# Standard Library
import os
import re

# local repo modules
import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCTION_DIRS = [
	os.path.join(REPO_ROOT, "track_runner"),
	os.path.join(REPO_ROOT, "common_tools"),
]

LEGACY_NAMES = {
	"DIAGNOSTICS_HEADER_VALUE",
	"PRE_RACE_REFERENCE_SCHEMA_VERSION",
	"INTERVAL_SCORE_SCHEMA_VERSION",
}


def test_schema_version_single_source():
	"""Verify all schema references use unified SCHEMA_VERSION per C8.

	Scans production modules for legacy constant names. Each legacy name
	must either be absent or an alias whose RHS is exactly SCHEMA_VERSION.
	"""
	failures = []

	for directory in PRODUCTION_DIRS:
		if not os.path.isdir(directory):
			continue

		for filename in os.listdir(directory):
			if not filename.endswith(".py"):
				continue

			filepath = os.path.join(directory, filename)
			if not os.path.isfile(filepath):
				continue

			with open(filepath, "r") as fh:
				content = fh.read()

			# Skip import lines and comments
			lines = content.split("\n")
			for line_no, line in enumerate(lines, start=1):
				# Skip comments and imports
				if line.strip().startswith("#"):
					continue
				if "import" in line and "=" not in line:
					continue

				for legacy_name in LEGACY_NAMES:
					# Look for assignments: LEGACY_NAME = something
					pattern = rf"^{legacy_name}\s*=\s*(.+?)(?:\s*#|$)"
					match = re.match(pattern, line.strip())
					if match:
						rhs = match.group(1).strip()
						# Allow assignment from SCHEMA_VERSION or state_io.SCHEMA_VERSION
						if rhs not in ("SCHEMA_VERSION", "state_io.SCHEMA_VERSION"):
							failures.append(
								f"{filepath}:{line_no}: {legacy_name} = {rhs} "
								f"(must be SCHEMA_VERSION)"
							)

	if failures:
		msg = "C8 drift detected:\n" + "\n".join(failures)
		pytest.fail(msg)
