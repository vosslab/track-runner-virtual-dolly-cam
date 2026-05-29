"""AST scan test: blob_walk_v2 must not import Hermite-related modules.

Enforces contract: the walker is stateless and never leaks Hermite state
into blob evidence selection. Violates contract if any v2 file imports
velocity_model, interval_solver, or scoring.
"""

import ast
import pathlib


def test_blob_walk_v2_no_hermite_import():
	"""Scan all tools/blob_walk_v2/*.py files and deny Hermite imports."""
	blob_walk_v2_dir = pathlib.Path(__file__).parent.parent / "tools" / "blob_walk_v2"
	assert blob_walk_v2_dir.is_dir(), f"Directory not found: {blob_walk_v2_dir}"

	forbidden_modules = {"velocity_model", "interval_solver", "scoring"}
	violations = []

	for py_file in sorted(blob_walk_v2_dir.rglob("*.py")):
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
