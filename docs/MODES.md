# Track runner modes

Each subcommand of `track_runner.py` is documented in its own page. Open the page for the mode you want to learn about; CLI flag tables on those pages are auto-regenerated from `--help` and stay in sync with the code.

For the typical workflow (which mode to run when), see [USAGE.md](USAGE.md).

## Canonical workflow

```text
                       +----------------------+
                       |   target + refine    |
                       |  (repeat N times)    |
                       +----------+-----------+
                                  ^
                                  | iterate until
                                  | scores acceptable
                                  v
  setup  ->  seed  ->  solve  -------------------->  encode
   (1x)     (1x)       (1x)                          (final)

  side trips: edit (fix bad seeds), analyze (pre-encode diagnostic)
```

Run order in one line: `setup` -> `seed` -> `solve` -> (`target` -> `refine`) x N -> `encode`. `edit` and `analyze` are optional detours, not part of the main path.

## Mode reference

| Step | Mode | Purpose | Page |
| --- | --- | --- | --- |
| 1 | `setup` | One-time per-video camera configuration. | [modes/SETUP.md](modes/SETUP.md) |
| 2 | `seed` | Place anchor seed annotations on the runner. | [modes/SEED.md](modes/SEED.md) |
| 3 | `solve` | Full re-solve from the current seed set. | [modes/SOLVE.md](modes/SOLVE.md) |
| 4a | `target` | Add seeds at weak interval frames. | [modes/TARGET.md](modes/TARGET.md) |
| 4b | `refine` | Incremental re-solve of changed intervals. | [modes/REFINE.md](modes/REFINE.md) |
| 5 | `encode` | Produce the final cropped output video. | [modes/ENCODE.md](modes/ENCODE.md) |
| -- | `edit` | Fix or double-check existing seeds (off-path). | [modes/EDIT.md](modes/EDIT.md) |
| -- | `analyze` | Pre-encode diagnostic without writing video. | [modes/ANALYZE.md](modes/ANALYZE.md) |

## Refreshing the help blocks

Run `python tools/refresh_mode_docs.py` (after `source source_me.sh`) to re-stamp the `--help` block in each mode page.
