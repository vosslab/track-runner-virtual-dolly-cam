"""Drift gate for contract C9: single source of truth for SCHEMA_VERSION.

The unified `SCHEMA_VERSION` lives in `track_runner/tr_schema.py`. Other
modules may re-export it (e.g. `state_io.SCHEMA_VERSION = tr_schema.SCHEMA_VERSION`)
or alias it for back-compat reading, but no other module may define a
parallel schema/cache/observer/fingerprint version constant.

The pattern this test catches is named-suffix `*_VERSION` constants that
look like schema authorities -- specifically any name containing SCHEMA,
CACHE, OBSERVER, or FINGERPRINT. Unrelated VERSION constants
(__version__, OPENCV_VERSION, MODEL_VERSION, etc.) are intentionally
NOT flagged. Genuine exceptions go in EXPLICIT_NON_SCHEMA_VERSION_EXEMPTIONS
below with a written rationale -- inline comments are not accepted, to
keep the exemption list visible in one place.
"""

# Standard Library
import os
import re

# local repo modules
import pytest
import file_utils


REPO_ROOT = file_utils.get_repo_root()
PRODUCTION_DIRS = [
	os.path.join(REPO_ROOT, "track_runner"),
	os.path.join(REPO_ROOT, "common_tools"),
	os.path.join(REPO_ROOT, "tools"),
]

# explicit legacy alias names that must equal SCHEMA_VERSION when present
LEGACY_NAMES = {
	"DIAGNOSTICS_HEADER_VALUE",
	"PRE_RACE_REFERENCE_SCHEMA_VERSION",
	"INTERVAL_SCORE_SCHEMA_VERSION",
}

# matches assignments to constants that look like schema authorities
# (contain SCHEMA, CACHE, OBSERVER, or FINGERPRINT in the name and end
# in _VERSION). Examples flagged: BLOB_OBSERVER_VERSION, CACHE_VERSION,
# SCHEMA_VERSION (outside tr_schema.py), CUSTOM_FINGERPRINT_VERSION.
# Examples NOT flagged: __version__, OPENCV_VERSION, MODEL_VERSION.
_SHADOW_AUTHORITY_RE = re.compile(
	r"^(?P<name>\w*?(?:SCHEMA|CACHE|OBSERVER|FINGERPRINT)\w*_VERSION)\s*="
)

# files that are allowed to define authority-named constants
_ALLOWED_AUTHORITY_FILES = {
	# the one and only authority module
	"tr_schema.py",
}

# right-hand sides that are accepted as legitimate re-exports
_ALLOWED_RHS = {
	"SCHEMA_VERSION",
	"state_io.SCHEMA_VERSION",
	"tr_schema.SCHEMA_VERSION",
}

# Explicit, whitelisted exceptions to the no-shadow-authority rule.
# Each entry is `(filename_basename, constant_name): "rationale"`. Add
# here only when the constant genuinely is not a schema authority and
# the name pattern accidentally collides. Empty by default; review every
# addition against C9.
EXPLICIT_NON_SCHEMA_VERSION_EXEMPTIONS: dict = {}


#============================================
def _scan_python_files() -> list:
	"""Yield (filepath, line_no, line_text) for every line in production code."""
	results = []
	for directory in PRODUCTION_DIRS:
		if not os.path.isdir(directory):
			continue
		for filename in os.listdir(directory):
			if not filename.endswith(".py"):
				continue
			filepath = os.path.join(directory, filename)
			if not os.path.isfile(filepath):
				continue
			with open(filepath, "r", encoding="utf-8") as fh:
				lines = fh.read().split("\n")
			for line_no, line in enumerate(lines, start=1):
				results.append((filepath, line_no, line))
	return results


#============================================
def test_legacy_aliases_equal_schema_version():
	"""Every legacy alias must be assigned exactly from SCHEMA_VERSION."""
	failures = []
	for filepath, line_no, line in _scan_python_files():
		stripped = line.strip()
		if stripped.startswith("#"):
			continue
		if "import" in stripped and "=" not in stripped:
			continue
		for legacy_name in LEGACY_NAMES:
			pattern = rf"^{legacy_name}\s*=\s*(.+?)(?:\s*#|$)"
			match = re.match(pattern, stripped)
			if match:
				rhs = match.group(1).strip()
				if rhs not in _ALLOWED_RHS:
					failures.append(
						f"{filepath}:{line_no}: {legacy_name} = {rhs} "
						f"(must be SCHEMA_VERSION)"
					)
	if failures:
		msg = "C9 alias drift detected:\n" + "\n".join(failures)
		pytest.fail(msg)


#============================================
def test_no_shadow_schema_authority_constants():
	"""No module outside tr_schema.py may define a schema-authority constant.

	Catches re-introduction of BLOB_OBSERVER_VERSION or any other parallel
	version authority. Constants that legitimately need this name pattern
	can opt out with `# tr_schema-exempt: <reason>` on the same line.
	"""
	failures = []
	for filepath, line_no, line in _scan_python_files():
		basename = os.path.basename(filepath)
		if basename in _ALLOWED_AUTHORITY_FILES:
			continue
		stripped = line.strip()
		if stripped.startswith("#"):
			continue
		match = _SHADOW_AUTHORITY_RE.match(stripped)
		if not match:
			continue
		name = match.group("name")
		# whitelist exemption (filename, constant) keyed
		exemption_key = (basename, name)
		if exemption_key in EXPLICIT_NON_SCHEMA_VERSION_EXEMPTIONS:
			continue
		# extract the right-hand side after the =, before any trailing comment
		rhs_match = re.match(r"^[^=]+=\s*(.+?)(?:\s*#|$)", stripped)
		if rhs_match is None:
			continue
		rhs = rhs_match.group(1).strip()
		if rhs in _ALLOWED_RHS:
			continue
		failures.append(
			f"{filepath}:{line_no}: {name} = {rhs} "
			f"(parallel schema-authority constants are forbidden by C9; "
			f"either re-export tr_schema.SCHEMA_VERSION or add "
			f"({basename!r}, {name!r}) to "
			f"EXPLICIT_NON_SCHEMA_VERSION_EXEMPTIONS in this test with a rationale)"
		)
	if failures:
		msg = "C9 shadow-authority drift detected:\n" + "\n".join(failures)
		pytest.fail(msg)
