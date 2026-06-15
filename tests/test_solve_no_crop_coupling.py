"""
AST guard: solve/walker modules must not import tr_crop or read crop-feel
config keys at runtime.

Checked modules:
  track_runner/blob_walk/*.py
  track_runner/interval_solver.py
  track_runner/solver_workers.py
  track_runner/residual_motion.py
  track_runner/velocity_model.py

Two invariants:
  (a) None of the target modules import tr_crop (or track_runner.tr_crop).
  (b) None read crop-feel config keys via subscript ([key]) or .get(key)
      where key is one of: crop_smoothing_attack, crop_smoothing_release,
      crop_max_velocity, crop_velocity_scale, crop_displacement_alpha, or
      any key starting with crop_post_smooth.

AST-based: ignores comments and docstrings by construction.

Guard integrity: if any required named module is absent from disk the
test suite fails loudly at collection time (not silently skipped).
"""

# Standard Library
import ast
import os

# PIP3 modules
import pytest

# local repo modules
import file_utils

#============================================
REPO_ROOT = file_utils.get_repo_root()

# Crop-feel key names that must not appear in solve/walker code.
# crop_post_smooth* means any key starting with this prefix.
CROP_FEEL_EXACT_KEYS: frozenset[str] = frozenset({
	"crop_smoothing_attack",
	"crop_smoothing_release",
	"crop_max_velocity",
	"crop_velocity_scale",
	"crop_displacement_alpha",
})
CROP_FEEL_PREFIX: str = "crop_post_smooth"

# Module names that indicate a tr_crop import.
TR_CROP_MODULE_NAMES: frozenset[str] = frozenset({"tr_crop", "track_runner.tr_crop"})

# Named modules that MUST be present on disk; a missing file is a guard failure.
# This set is the canonical list - if any entry is absent the test collection
# step raises so pytest reports a collection error, never a silent skip.
REQUIRED_NAMED_MODULES: tuple[str, ...] = (
	os.path.join(REPO_ROOT, "track_runner", "interval_solver.py"),
	os.path.join(REPO_ROOT, "track_runner", "solver_workers.py"),
	os.path.join(REPO_ROOT, "track_runner", "residual_motion.py"),
	os.path.join(REPO_ROOT, "track_runner", "velocity_model.py"),
)

# Fail loudly at module-load time if any required file is missing.
_missing_required = [p for p in REQUIRED_NAMED_MODULES if not os.path.isfile(p)]
if _missing_required:
	raise FileNotFoundError(
		"Guard target(s) missing from disk - a missing required module is a guard failure:\n"
		+ "\n".join(f"  {p}" for p in _missing_required)
	)


#============================================
def _collect_target_files() -> list[str]:
	"""
	Return absolute paths to the solve/walker modules under guard.

	Collects blob_walk/*.py plus the named modules in REQUIRED_NAMED_MODULES.
	All named modules are guaranteed to exist (checked at module load above).

	Returns:
		list[str]: Sorted absolute paths.
	"""
	blob_walk_dir = os.path.join(REPO_ROOT, "track_runner", "blob_walk")
	paths = []
	# Collect all .py files from blob_walk.
	for entry in sorted(os.listdir(blob_walk_dir)):
		if entry.endswith(".py"):
			paths.append(os.path.join(blob_walk_dir, entry))
	# Add the required named modules (all confirmed present above).
	for path in REQUIRED_NAMED_MODULES:
		paths.append(path)
	paths.sort()
	return paths


#============================================
def _imports_tr_crop(tree: ast.Module) -> list[tuple[int, str]]:
	"""
	Return (lineno, description) for each tr_crop import node in tree.

	Checks ast.Import (bare import) and ast.ImportFrom (from X import Y).
	Matches both 'tr_crop' and 'track_runner.tr_crop'.

	Args:
		tree: Parsed ast.Module to inspect.

	Returns:
		list[tuple[int, str]]: (lineno, description) pairs, empty if clean.
	"""
	hits = []
	for node in file_utils.iter_imports(tree):
		if isinstance(node, ast.Import):
			# ast.Import: names is a list of alias objects.
			for alias in node.names:
				# Match the module name or any dotted prefix.
				if alias.name in TR_CROP_MODULE_NAMES:
					hits.append((node.lineno, f"import {alias.name}"))
		elif isinstance(node, ast.ImportFrom):
			# ast.ImportFrom: module is the base module name.
			module = node.module or ""
			if module in TR_CROP_MODULE_NAMES:
				hits.append((node.lineno, f"from {module} import ..."))
	return hits


#============================================
def _is_crop_feel_key(key_value: str) -> bool:
	"""
	Return True when key_value is a crop-feel config key.

	Exact match against CROP_FEEL_EXACT_KEYS or prefix match against
	CROP_FEEL_PREFIX (crop_post_smooth*).

	Args:
		key_value: String literal value extracted from an AST node.

	Returns:
		bool: True when the key is a crop-feel key.
	"""
	if key_value in CROP_FEEL_EXACT_KEYS:
		return True
	if key_value.startswith(CROP_FEEL_PREFIX):
		return True
	return False


#============================================
def _reads_crop_feel_key(tree: ast.Module) -> list[tuple[int, str]]:
	"""
	Return (lineno, description) for crop-feel key accesses in tree.

	Detects two runtime config-read shapes:
	  - Subscript: expr[key_str]  ->  ast.Subscript with a string literal slice
	  - .get call: expr.get(key_str, ...)  ->  ast.Call to an ast.Attribute named get

	Args:
		tree: Parsed ast.Module to inspect.

	Returns:
		list[tuple[int, str]]: (lineno, description) pairs, empty if clean.
	"""
	hits = []
	for node in ast.walk(tree):
		# Pattern 1: subscript access config["crop_..."]
		if isinstance(node, ast.Subscript):
			slice_node = node.slice
			# ast.Constant for string literals (Python 3.8+).
			if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
				if _is_crop_feel_key(slice_node.value):
					hits.append((node.lineno, f'subscript [{slice_node.value!r}]'))
		# Pattern 2: .get(key) call like config.get("crop_...", default)
		elif isinstance(node, ast.Call):
			func = node.func
			if not isinstance(func, ast.Attribute):
				continue
			if func.attr != "get":
				continue
			if not node.args:
				continue
			first_arg = node.args[0]
			if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
				if _is_crop_feel_key(first_arg.value):
					hits.append((node.lineno, f'.get({first_arg.value!r})'))
	return hits


#============================================
# Collect target files once at module load; parametrize from this list.
TARGET_FILES: list[str] = _collect_target_files()


#============================================
@pytest.mark.parametrize("path", TARGET_FILES, ids=file_utils.rel_id)
def test_no_tr_crop_import(path: str) -> None:
	"""
	Assert that the target module does not import tr_crop.

	Args:
		path: Absolute path to the module under test.
	"""
	tree, error = file_utils.parse_source(path)
	rel = file_utils.rel_to_root(path)
	assert tree is not None, f"{rel}: SyntaxError: {error}"
	hits = _imports_tr_crop(tree)
	assert not hits, (
		f"{rel}: found tr_crop import(s):\n"
		+ "\n".join(f"  line {ln}: {desc}" for ln, desc in hits)
	)


#============================================
@pytest.mark.parametrize("path", TARGET_FILES, ids=file_utils.rel_id)
def test_no_crop_feel_key_access(path: str) -> None:
	"""
	Assert that the target module does not access crop-feel config keys.

	Args:
		path: Absolute path to the module under test.
	"""
	tree, error = file_utils.parse_source(path)
	rel = file_utils.rel_to_root(path)
	assert tree is not None, f"{rel}: SyntaxError: {error}"
	hits = _reads_crop_feel_key(tree)
	assert not hits, (
		f"{rel}: found crop-feel key access(es):\n"
		+ "\n".join(f"  line {ln}: {desc}" for ln, desc in hits)
	)


#============================================
def test_all_required_named_modules_covered() -> None:
	"""
	Assert that every module in REQUIRED_NAMED_MODULES appears in TARGET_FILES.

	This is a structural guard: if a required module is present on disk but was
	accidentally dropped from TARGET_FILES, this test fails loudly instead of
	silently skipping coverage.
	"""
	covered = set(TARGET_FILES)
	missing_coverage = [p for p in REQUIRED_NAMED_MODULES if p not in covered]
	assert not missing_coverage, (
		"Required guard target(s) are not covered by TARGET_FILES:\n"
		+ "\n".join(f"  {p}" for p in missing_coverage)
	)


#============================================
def test_flipped_fixture_import_detector_fires() -> None:
	"""
	Prove the import detector catches a module that does import tr_crop.

	Parses a synthetic source string via ast; nothing is written to disk.
	"""
	# Synthetic module that imports tr_crop two ways.
	source = (
		"import tr_crop\n"
		"from track_runner.tr_crop import something\n"
		"x = 1\n"
	)
	tree = ast.parse(source, filename="<synthetic>")
	hits = _imports_tr_crop(tree)
	assert len(hits) >= 2, (
		f"Expected at least 2 tr_crop import hits from synthetic source, got {len(hits)}: {hits}"
	)


#============================================
def test_flipped_fixture_crop_feel_detector_fires() -> None:
	"""
	Prove the crop-feel detector catches a module that reads crop-feel keys.

	Parses a synthetic source string via ast; nothing is written to disk.
	"""
	# Synthetic module accessing several crop-feel keys via subscript and .get.
	source = (
		"x = cfg['crop_smoothing_attack']\n"
		"y = cfg.get('crop_max_velocity', 0)\n"
		"z = cfg['crop_post_smooth_frames']\n"
		"w = cfg.get('crop_displacement_alpha', 1.0)\n"
	)
	tree = ast.parse(source, filename="<synthetic>")
	hits = _reads_crop_feel_key(tree)
	assert len(hits) >= 4, (
		f"Expected at least 4 crop-feel key hits from synthetic source, got {len(hits)}: {hits}"
	)
