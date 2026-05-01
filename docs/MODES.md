# Track runner modes

Each subcommand of `track_runner.py` is documented in its own page. Open the page for the mode you want to learn about; CLI flag tables on those pages are auto-regenerated from `--help` and stay in sync with the code.

For the typical workflow (which mode to run when), see [docs/USAGE.md](USAGE.md).

| Mode | Purpose | Page |
| --- | --- | --- |
| `setup` | One-time per-video camera configuration. | [modes/SETUP.md](modes/SETUP.md) |
| `seed` | Place anchor seed annotations on the runner. | [modes/SEED.md](modes/SEED.md) |
| `solve` | Full re-solve from the current seed set. | [modes/SOLVE.md](modes/SOLVE.md) |
| `target` | Add seeds at weak interval frames. | [modes/TARGET.md](modes/TARGET.md) |
| `refine` | Incremental re-solve of changed intervals. | [modes/REFINE.md](modes/REFINE.md) |
| `edit` | Fix or double-check existing seeds. | [modes/EDIT.md](modes/EDIT.md) |
| `encode` | Produce the final cropped output video. | [modes/ENCODE.md](modes/ENCODE.md) |
| `analyze` | Pre-encode diagnostic without writing video. | [modes/ANALYZE.md](modes/ANALYZE.md) |

## Refreshing the help blocks

Run `python tools/refresh_mode_docs.py` (after `source source_me.sh`) to re-stamp the `--help` block in each mode page.
