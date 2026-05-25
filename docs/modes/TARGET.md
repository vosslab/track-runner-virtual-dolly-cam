# target mode

Add seeds at weak interval frames. The annotation UI shows forward/backward propagation overlays to help you see where the two passes diverge and to place corrective seeds at those uncertain locations.

## When to use it

- After solve, to identify and fix weak intervals.
- Iteratively with refine: target weak intervals, refine to incorporate the new seeds, repeat.
- Part of the canonical workflow after the first solve.

## Command line reference

<!-- BEGIN AUTO HELP: target -->
```text
usage: track_runner.py target [-h] [--race-start | -A]
                              [-s {high,medium,low} | -H | -L] [-t TOP_N]
                              [-g GAP_TOP_N] [-I SEED_INTERVAL]
                              [-S START_TIME]

options:
  -h, --help            show this help message and exit
  --race-start          Target frames around the detected race-start
                        transition for confirmation.
  -A, --from-analyze    Target frames from the latest 'analyze' report: union
                        of seed_suggestions and instability-region midpoints.
                        Run 'analyze' first to refresh the report.
  -s {high,medium,low}, --severity {high,medium,low}
                        Minimum severity of weak intervals to target.
  -H, --high            Alias for -s high.
  -L, --low             Alias for -s low.
  -t TOP_N, --top TOP_N
                        limit output to the worst N intervals (sorted worst-
                        first by rank_key).
  -g GAP_TOP_N, --gaps GAP_TOP_N
                        add midpoints of the N largest seed gaps to the
                        seeding target list (independent of -t/--top). Implies
                        -s on analyze.
  -I SEED_INTERVAL, --seed-interval SEED_INTERVAL
                        Interval in seconds between seed frames (default 10).
  -S START_TIME, --start START_TIME
                        Start time in seconds (seek UI to this position on
                        launch).
```
<!-- END AUTO HELP: target -->

## Notes

**Options:**

- `-s`, `--severity` Minimum severity of weak intervals to target (filters to candidate frames).
- `-H`, `--high` Alias for `-s high`.
- `-L`, `--low` Alias for `-s low`.
- `-A`, `--from-analyze` Target frames from the latest analyze report.
- `-I`, `--seed-interval` Seconds between candidate target frames (default 10).
- `--race-start` Target frames around the detected race-start transition for confirmation. Selects interval endpoints and offset-derived frames around `race_start_frame`, prints the race-start contact-sheet path, and enters the target UI. Use this after viewing the contact-sheet PNG produced during solve/refine to refine race-start seeds.

**Mutual exclusivity:** `--race-start` and `-A`/`--from-analyze` are mutually exclusive; use only one per invocation.

The annotation UI is the same as seed mode. See [../TRACK_RUNNER_KEYBINDINGS.md](../TRACK_RUNNER_KEYBINDINGS.md) for navigation and box-drawing keyboard shortcuts.

Seeds placed in target mode follow contract C1: they are human-authored anchors that propagate into the next refine pass. Each seed is saved immediately upon placement.
