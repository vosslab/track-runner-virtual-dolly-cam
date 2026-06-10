"""AST scan test: blob_walk_v2 must not import Hermite-related modules.

Enforces contract: the walker is stateless and never leaks Hermite state
into blob evidence selection. Violates contract if any v2 file imports
velocity_model, interval_solver, or scoring.

Exception: the orchestration/driver tier (walk_driver.py, make_walk_html_v2.py)
calls track_runner infrastructure -- walk_driver.py for persistence
(blend_paths, state_io, interval_fingerprint) and make_walk_html_v2.py for the
shared rich progress renderer (make_solve_progress, FrameETAColumn). Neither
uses Hermite propagation state, so they are intentionally excluded from this
ban; the rule still binds every walker-algorithm file.

The gate also covers track_runner/walker_bundle.py (the Stage-4 adapter that
calls the walker). interval_solver imports walker_bundle, not the reverse;
walker_bundle is expected to be clean and this test enforces that invariant.
"""

import ast
import pathlib

# The driver tier is not the walker algorithm. walk_driver.py imports
# interval_solver only for blend_paths (a pure path-blending utility, no Hermite
# state); make_walk_html_v2.py imports it only for the progress-bar classes
# (pure UI, no Hermite state). Excluding both preserves the intent of the
# no-Hermite rule for the walker-algorithm files.
_EXCLUDED_FROM_NO_HERMITE_BAN = {"walk_driver.py", "make_walk_html_v2.py"}


def test_blob_walk_v2_no_hermite_import():
	"""Scan the relocated walker core and deny Hermite imports.

	The walker core now lives in track_runner/blob_walk/ (relocated from
	tools/blob_walk_v2/core/ in WP-1). The tool-side render/HTML/heat-movie
	tier still lives under tools/blob_walk_v2/; both are scanned so the
	no-Hermite boundary keeps binding every walker-algorithm file.
	"""
	repo_root = pathlib.Path(__file__).parent.parent
	scan_dirs = [
		repo_root / "track_runner" / "blob_walk",
		repo_root / "tools" / "blob_walk_v2",
	]
	for scan_dir in scan_dirs:
		assert scan_dir.is_dir(), f"Directory not found: {scan_dir}"

	forbidden_modules = {"velocity_model", "interval_solver", "scoring"}
	violations = []

	scan_files = []
	for scan_dir in scan_dirs:
		scan_files.extend(scan_dir.rglob("*.py"))

	for py_file in sorted(scan_files):
		if py_file.name in _EXCLUDED_FROM_NO_HERMITE_BAN:
			continue
		try:
			source = py_file.read_text()
			tree = ast.parse(source)
		except Exception as e:
			violations.append(f"{py_file.name}: parse error: {e}")
			continue

		for node in ast.walk(tree):
			# Check 'import X' statements
			if isinstance(node, ast.Import):
				for alias in node.names:
					module_name = alias.name.split(".")[0]
					if module_name in forbidden_modules:
						violations.append(
							f"{py_file.name}:{node.lineno}: "
							f"imports {alias.name} (forbidden)"
						)

			# Check 'from X import Y' statements
			elif isinstance(node, ast.ImportFrom):
				if node.module is not None:
					module_name = node.module.split(".")[0]
					if module_name in forbidden_modules:
						violations.append(
							f"{py_file.name}:{node.lineno}: "
							f"from {node.module} import ... (forbidden)"
						)

	assert not violations, (
		"blob_walk_v2 contains forbidden Hermite imports:\n" +
		"\n".join(violations)
	)


def test_walker_bundle_no_hermite_import():
	"""Scan walker_bundle.py and deny Hermite imports.

	walker_bundle.py is the Stage-4 adapter: interval_solver calls into it,
	not the reverse. It legitimately imports blob_walk.walk_walker (the core
	walker), but must never import velocity_model, interval_solver, or scoring.
	This test enforces that boundary so a future edit cannot silently introduce
	a Hermite dependency through the adapter seam.
	"""
	repo_root = pathlib.Path(__file__).parent.parent
	bundle_file = repo_root / "track_runner" / "walker_bundle.py"
	assert bundle_file.is_file(), f"File not found: {bundle_file}"

	forbidden_modules = {"velocity_model", "interval_solver", "scoring"}
	violations = []

	source = bundle_file.read_text()
	tree = ast.parse(source)

	for node in ast.walk(tree):
		# Check 'import X' statements
		if isinstance(node, ast.Import):
			for alias in node.names:
				module_name = alias.name.split(".")[0]
				if module_name in forbidden_modules:
					violations.append(
						f"{bundle_file.name}:{node.lineno}: "
						f"imports {alias.name} (forbidden)"
					)

		# Check 'from X import Y' statements
		elif isinstance(node, ast.ImportFrom):
			if node.module is not None:
				module_name = node.module.split(".")[0]
				if module_name in forbidden_modules:
					violations.append(
						f"{bundle_file.name}:{node.lineno}: "
						f"from {node.module} import ... (forbidden)"
					)

	assert not violations, (
		"walker_bundle.py contains forbidden Hermite imports:\n" +
		"\n".join(violations)
	)
