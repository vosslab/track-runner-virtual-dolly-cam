# blob_walk_v2

Blob walker v2 tools for track runner virtual dolly cam.

## Entry point

All pipeline operations are available through the single consolidated driver:

```
python3 tools/blob_walk_v2/blob_walk_v2_cli.py <subcommand> [flags]
```

### Subcommands

| Subcommand | What it does | Key flags |
| --- | --- | --- |
| `dump` | Dump step-0 and step-1 bootstrap inputs to JSON | `--videos LIST`, `--random-corpus`, `--output-dir` |
| `replay` | Replay step-1 inputs against candidate models; write `REPLAY_REPORT.md` | `--corpus-dir` |
| `score` | Score corpus walk quality from existing verdict CSVs; write `corpus_quality.csv` and `QUALITY_SCORES.md` | `--corpus-root`, `--dump-root` |
| `render` | Run the walker batch and build `walk.html` | `--output-root`, `--intervals-from-corpus`, `--workers` |
| `audit` | Regenerate `REPLAY_REPORT.md` and `QUALITY_SCORES.md` without re-running the walker | `--corpus-dir`, `--corpus-root` |
| `all` | Run dump -> replay -> score -> render -> audit in sequence | `--videos LIST`, `--workers` |

### Examples

```bash
# Get top-level help
python3 tools/blob_walk_v2/blob_walk_v2_cli.py --help

# Get help for a specific subcommand
python3 tools/blob_walk_v2/blob_walk_v2_cli.py dump --help
python3 tools/blob_walk_v2/blob_walk_v2_cli.py render --help

# Dump 24-corpus inputs (4 intervals per video, seed=42)
python3 tools/blob_walk_v2/blob_walk_v2_cli.py dump --videos VIDEO_A VIDEO_B --random-corpus

# Replay existing dump corpus and write REPLAY_REPORT.md
python3 tools/blob_walk_v2/blob_walk_v2_cli.py replay

# Score existing walk verdict CSVs
python3 tools/blob_walk_v2/blob_walk_v2_cli.py score

# Render walk.html (single worker, uses default corpus paths)
python3 tools/blob_walk_v2/blob_walk_v2_cli.py render --workers 1

# Re-generate reports without re-running the walker
python3 tools/blob_walk_v2/blob_walk_v2_cli.py audit
```

## Default output paths

- Dump JSONs: `dump_step1/24corpus/`
- Walk tiles and `walk.html`: `blob_walk_v2/24corpus/`
- Quality CSV: `corpus_quality.csv` (repo root)
- Quality Markdown: `dump_step1/24corpus/QUALITY_SCORES.md`
- Replay report: `dump_step1/24corpus/REPLAY_REPORT.md`

## Standalone scripts (deprecated-in-place)

The individual scripts below remain callable directly for now. New usage should
go through `blob_walk_v2_cli.py` instead. They will not be deleted while the
#78 corpus rerun is active.

- `dump_step1_inputs.py` -- use `blob_walk_v2_cli.py dump`
- `replay_step1.py` -- use `blob_walk_v2_cli.py replay`
- `score_corpus_quality.py` -- use `blob_walk_v2_cli.py score`
- `make_walk_html_v2.py` -- use `blob_walk_v2_cli.py render`

## Library modules

These modules are imported by the driver scripts and are not entry points:

- `walk_walker.py`, `walk_motion_gate.py`, `walk_html.py`, `walk_render.py`
- `walk_io.py`, `walk_debug_log.py`, `walk_driver.py`
