#!/usr/bin/env python3
"""Consolidated entry point for blob_walk_v2 tools.

Subcommands:
  dump    -- Dump step-0 and step-1 bootstrap inputs to JSON.
  replay  -- Replay step-1 inputs against candidate models and generate REPLAY_REPORT.md.
  score   -- Score corpus walk quality from existing verdict CSVs.
  render  -- Run walker and/or build walk.html from existing data.
  audit   -- Regenerate REPLAY_REPORT.md, QUALITY_SCORES.md, and corpus CSV if source data changed.
  all     -- Run dump -> replay -> score -> render -> audit in sequence.

Each subcommand proxies to the corresponding standalone script via subprocess.
The standalone scripts remain callable directly; this file is a convenience wrapper.
"""

# Standard Library
import argparse
import os
import subprocess
import sys

#============================================

# Locate the directory containing this file so we can resolve sibling scripts.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))

# Map subcommand name -> script path relative to _THIS_DIR.
_SCRIPT = {
	'dump':    os.path.join(_THIS_DIR, 'dump_step1_inputs.py'),
	'replay':  os.path.join(_THIS_DIR, 'replay_step1.py'),
	'score':   os.path.join(_THIS_DIR, 'score_corpus_quality.py'),
	'render':  os.path.join(_THIS_DIR, 'make_walk_html_v2.py'),
}

#============================================

# Default corpus paths used across subcommands.
_DEFAULT_DUMP_ROOT   = 'dump_step1/24corpus'
_DEFAULT_CORPUS_ROOT = 'blob_walk_v2/24corpus'


def _python3() -> str:
	"""Return the Python 3 interpreter to use."""
	return sys.executable


def _run(cmd: list) -> int:
	"""Run a subprocess command and return its exit code."""
	result = subprocess.run(cmd)
	return result.returncode

#============================================

def _build_dump_cmd(args: argparse.Namespace) -> list:
	"""Build the subprocess command for the dump subcommand."""
	cmd = [_python3(), _SCRIPT['dump']]
	cmd += ['--videos'] + args.videos
	cmd += ['--output-dir', args.output_dir]
	if args.random_corpus:
		cmd.append('--random-corpus')
	cmd += ['--per-video-n', str(args.per_video_n)]
	return cmd


def _build_replay_cmd(args: argparse.Namespace) -> list:
	"""Build the subprocess command for the replay subcommand."""
	cmd = [_python3(), _SCRIPT['replay']]
	cmd += ['--corpus-dir', args.corpus_dir]
	return cmd


def _build_score_cmd(args: argparse.Namespace) -> list:
	"""Build the subprocess command for the score subcommand."""
	cmd = [_python3(), _SCRIPT['score']]
	cmd += ['--corpus-root', args.corpus_root]
	cmd += ['--dump-root', args.dump_root]
	return cmd


def _build_render_cmd(args: argparse.Namespace) -> list:
	"""Build the subprocess command for the render subcommand."""
	cmd = [_python3(), _SCRIPT['render']]
	cmd += ['--output-root', args.output_root]
	cmd += ['--intervals-from-corpus', args.intervals_from_corpus]
	cmd += ['--workers', str(args.workers)]
	# render subcommand always walks and renders.
	cmd.append('--walk')
	return cmd

#============================================

def run_dump(args: argparse.Namespace) -> int:
	"""Run the dump subcommand."""
	cmd = _build_dump_cmd(args)
	print(f'[run.py dump] {" ".join(cmd)}')
	return _run(cmd)


def run_replay(args: argparse.Namespace) -> int:
	"""Run the replay subcommand."""
	cmd = _build_replay_cmd(args)
	print(f'[run.py replay] {" ".join(cmd)}')
	return _run(cmd)


def run_score(args: argparse.Namespace) -> int:
	"""Run the score subcommand."""
	cmd = _build_score_cmd(args)
	print(f'[run.py score] {" ".join(cmd)}')
	return _run(cmd)


def run_render(args: argparse.Namespace) -> int:
	"""Run the render subcommand."""
	cmd = _build_render_cmd(args)
	print(f'[run.py render] {" ".join(cmd)}')
	return _run(cmd)


def run_audit(args: argparse.Namespace) -> int:
	"""Regenerate REPLAY_REPORT.md and QUALITY_SCORES.md from existing corpus data.

	Audit does not re-run the walker or re-dump step-1 inputs. It only
	replays the existing dump JSONs and re-scores the existing verdict CSVs.
	"""
	# Build a minimal args namespace for replay using audit's corpus_dir.
	replay_args = argparse.Namespace(
		corpus_dir=args.corpus_dir,
	)
	code = run_replay(replay_args)
	if code != 0:
		return code

	# Re-score using the same roots as the standard score subcommand.
	score_args = argparse.Namespace(
		corpus_root=args.corpus_root,
		dump_root=args.corpus_dir,
	)
	return run_score(score_args)


def run_all(args: argparse.Namespace) -> int:
	"""Run dump -> replay -> score -> render -> audit in sequence."""
	# dump
	dump_args = argparse.Namespace(
		videos=args.videos,
		output_dir=args.output_dir,
		random_corpus=True,
		per_video_n=args.per_video_n,
	)
	code = run_dump(dump_args)
	if code != 0:
		return code

	# replay
	replay_args = argparse.Namespace(
		corpus_dir=os.path.join(args.output_dir),
	)
	code = run_replay(replay_args)
	if code != 0:
		return code

	# score
	score_args = argparse.Namespace(
		corpus_root=args.corpus_root,
		dump_root=args.output_dir,
	)
	code = run_score(score_args)
	if code != 0:
		return code

	# render
	render_args = argparse.Namespace(
		output_root=args.corpus_root,
		intervals_from_corpus=args.output_dir,
		workers=args.workers,
	)
	code = run_render(render_args)
	if code != 0:
		return code

	# audit (read-only, uses already-generated artifacts)
	audit_args = argparse.Namespace(
		corpus_dir=args.output_dir,
		corpus_root=args.corpus_root,
	)
	return run_audit(audit_args)

#============================================

def parse_args() -> argparse.Namespace:
	"""Parse top-level subcommand and delegate to per-subcommand parsers."""
	parser = argparse.ArgumentParser(
		description=(
			'Blob walker v2 consolidated entry point. '
			'Run a single subcommand or "all" to run the full pipeline.'
		),
		formatter_class=argparse.RawDescriptionHelpFormatter,
		epilog=(
			'Examples:\n'
			'  python3 tools/blob_walk_v2/run.py dump\n'
			'  python3 tools/blob_walk_v2/run.py replay\n'
			'  python3 tools/blob_walk_v2/run.py score\n'
			'  python3 tools/blob_walk_v2/run.py render --workers 1\n'
			'  python3 tools/blob_walk_v2/run.py audit\n'
			'  python3 tools/blob_walk_v2/run.py all\n'
		),
	)
	subparsers = parser.add_subparsers(dest='subcommand', metavar='SUBCOMMAND')
	subparsers.required = True

	# --- dump ---
	dump_p = subparsers.add_parser(
		'dump',
		help='Dump step-0 and step-1 bootstrap inputs to JSON.',
	)
	dump_p.add_argument(
		'-v', '--videos', dest='videos', nargs='+', required=True,
		help='Video basenames to process.',
	)
	dump_p.add_argument(
		'-o', '--output-dir', dest='output_dir',
		default=_DEFAULT_DUMP_ROOT,
		help=f'Output root directory (default: {_DEFAULT_DUMP_ROOT}).',
	)
	dump_p.add_argument(
		'--random-corpus', dest='random_corpus',
		action='store_true', default=False,
		help='Pick n random well-formed intervals per video (seed=42).',
	)
	dump_p.add_argument(
		'--per-video-n', dest='per_video_n', type=int, default=4,
		help='Intervals per video for --random-corpus mode (default 4).',
	)

	# --- replay ---
	replay_p = subparsers.add_parser(
		'replay',
		help='Replay step-1 inputs against candidate models; generate REPLAY_REPORT.md.',
	)
	replay_p.add_argument(
		'-c', '--corpus-dir', dest='corpus_dir',
		default=_DEFAULT_DUMP_ROOT,
		help=f'Path to dump corpus directory (default: {_DEFAULT_DUMP_ROOT}).',
	)

	# --- score ---
	score_p = subparsers.add_parser(
		'score',
		help='Score corpus walk quality from existing verdict CSVs.',
	)
	score_p.add_argument(
		'--corpus-root', dest='corpus_root',
		default=_DEFAULT_CORPUS_ROOT,
		help=f'Path to blob_walk_v2 corpus root (default: {_DEFAULT_CORPUS_ROOT}).',
	)
	score_p.add_argument(
		'--dump-root', dest='dump_root',
		default=_DEFAULT_DUMP_ROOT,
		help=f'Path to dump_step1 corpus root (default: {_DEFAULT_DUMP_ROOT}).',
	)

	# --- render ---
	render_p = subparsers.add_parser(
		'render',
		help='Run the walker and build walk.html.',
	)
	render_p.add_argument(
		'-o', '--output-root', dest='output_root',
		default=_DEFAULT_CORPUS_ROOT,
		help=f'Root output directory (default: {_DEFAULT_CORPUS_ROOT}).',
	)
	render_p.add_argument(
		'--intervals-from-corpus', dest='intervals_from_corpus',
		default=_DEFAULT_DUMP_ROOT,
		help=f'Corpus dir for interval filter (default: {_DEFAULT_DUMP_ROOT}).',
	)
	render_p.add_argument(
		'-j', '--workers', dest='workers', type=int, default=1,
		help='Number of parallel video workers (default: 1).',
	)

	# --- audit ---
	audit_p = subparsers.add_parser(
		'audit',
		help='Regenerate REPLAY_REPORT.md and QUALITY_SCORES.md without re-running the walker.',
	)
	audit_p.add_argument(
		'-c', '--corpus-dir', dest='corpus_dir',
		default=_DEFAULT_DUMP_ROOT,
		help=f'Path to dump corpus directory (default: {_DEFAULT_DUMP_ROOT}).',
	)
	audit_p.add_argument(
		'--corpus-root', dest='corpus_root',
		default=_DEFAULT_CORPUS_ROOT,
		help=f'Path to blob_walk_v2 corpus root (default: {_DEFAULT_CORPUS_ROOT}).',
	)

	# --- all ---
	all_p = subparsers.add_parser(
		'all',
		help='Run dump -> replay -> score -> render -> audit in sequence.',
	)
	all_p.add_argument(
		'-v', '--videos', dest='videos', nargs='+', required=True,
		help='Video basenames to process.',
	)
	all_p.add_argument(
		'-o', '--output-dir', dest='output_dir',
		default=_DEFAULT_DUMP_ROOT,
		help=f'Dump output root directory (default: {_DEFAULT_DUMP_ROOT}).',
	)
	all_p.add_argument(
		'--corpus-root', dest='corpus_root',
		default=_DEFAULT_CORPUS_ROOT,
		help=f'Walk output root directory (default: {_DEFAULT_CORPUS_ROOT}).',
	)
	all_p.add_argument(
		'-j', '--workers', dest='workers', type=int, default=1,
		help='Number of parallel video workers for render stage (default: 1).',
	)

	# suppress unused variable warnings from parsers only used for --help
	_ = dump_p
	_ = replay_p
	_ = score_p
	_ = render_p
	_ = audit_p
	_ = all_p

	return parser.parse_args()

#============================================

def main() -> None:
	"""Entry point: parse subcommand and dispatch."""
	args = parse_args()
	dispatch = {
		'dump':   run_dump,
		'replay': run_replay,
		'score':  run_score,
		'render': run_render,
		'audit':  run_audit,
		'all':    run_all,
	}
	fn = dispatch[args.subcommand]
	code = fn(args)
	sys.exit(code)


if __name__ == '__main__':
	main()
