"""
Smoke tests for tools/blob_walk_v2/blob_walk_v2_cli.py entry point.

Three tests:
  test_help_returns_zero          -- cli --help exits 0
  test_dump_help_returns_zero     -- cli dump --help exits 0
  test_all_subcommands_registered -- all six subcommands present in parser
"""

# Standard Library
import subprocess
import sys

# local repo modules
import git_file_utils

REPO_ROOT = git_file_utils.get_repo_root()
CLI_PY = REPO_ROOT + '/tools/blob_walk_v2/blob_walk_v2_cli.py'


def test_help_returns_zero():
	"""cli --help must exit with code 0."""
	result = subprocess.run(
		[sys.executable, CLI_PY, '--help'],
		capture_output=True,
	)
	assert result.returncode == 0


def test_dump_help_returns_zero():
	"""cli dump --help must exit with code 0."""
	result = subprocess.run(
		[sys.executable, CLI_PY, 'dump', '--help'],
		capture_output=True,
	)
	assert result.returncode == 0


def test_all_subcommands_registered():
	"""All six subcommands must be registered in the cli parser."""
	result = subprocess.run(
		[sys.executable, CLI_PY, '--help'],
		capture_output=True,
		text=True,
	)
	help_text = result.stdout + result.stderr
	expected = ['dump', 'replay', 'score', 'render', 'audit', 'all']
	for name in expected:
		assert name in help_text, f'subcommand {name!r} not found in --help output'
