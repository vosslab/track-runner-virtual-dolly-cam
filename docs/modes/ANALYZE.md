# analyze mode

Pre-encode diagnostic. Analyze rebuilds the same trajectory and crop rectangles that encode would use, then reports on the result without writing a video. Use it to check stability and catch problems before kicking off a long encode.

## When to use it

- Before encode, to verify crop-path stability and solver context.
- To diagnose interval scores and seed density without waiting for video encoding.
- To compare what-if scenarios (e.g., different aspect ratios) without re-solving.

## Command line reference

<!-- BEGIN AUTO HELP: analyze -->
```text
usage: track_runner.py analyze [-h] [--aspect ASPECT] [-s] [-t TOP_N]
                               [-g GAP_TOP_N] [-S START_TIME]

options:
  -h, --help            show this help message and exit
  --aspect ASPECT       Override crop aspect ratio (e.g. '1:1', '16:9').
  -s, --seed            After printing the analyze report, open the seeding UI
                        on the worst-N instability-region peak frames (same
                        set used by 'target --from-analyze'). Implied by '-t
                        N'.
  -t TOP_N, --top TOP_N
                        limit output to the worst N intervals (sorted worst-
                        first by rank_key).
  -g GAP_TOP_N, --gaps GAP_TOP_N
                        add midpoints of the N largest seed gaps to the
                        seeding target list (independent of -t/--top). Implies
                        -s on analyze.
  -S START_TIME, --start START_TIME
                        Start time in seconds (seek UI to this position on
                        launch).
```
<!-- END AUTO HELP: analyze -->

## Notes

**Computation and output:**

- Crop-path stability: smoothness metrics on the crop rectangle trajectory (jitter, velocity, acceleration, deadband behavior).
- Solver context: per-interval confidence, seed density, and agreement summary derived from solved intervals and seeds.
- Motion regime summary: straight/curve/stationary span classification used by smart-mode crop policies.

**Output files:**

- Formatted console report to stdout.
- YAML report at `<video>.track_runner.encode_analysis.yaml` (path from `tr_paths.default_encode_analysis_path`). Other modes print this path as a diagnostic-awareness hint; they do not read it.

**Prerequisites:** Analyze needs `setup` and `solve` to have run first. If either is missing the command exits with a "run 'setup' and 'solve' first" message.

**Options:**

- `--aspect` Override the crop aspect ratio (e.g. `1:1`, `16:9`) to test what a different aspect would look like without re-encoding.
- `--seed`, `-s` Chain into the seeding UI after analysis (useful for targeted annotation).
- `-t`, `--top` Limit output to the top N weak intervals (useful for focusing on the worst problems).
- `-g`, `--gaps` Show gap analysis (intervals with no solver prediction).

For the detailed architecture of analyze and encode, see [../TRACK_RUNNER_ANALYZE_AND_ENCODE.md](../TRACK_RUNNER_ANALYZE_AND_ENCODE.md).
