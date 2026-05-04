# analyze mode

Pre-encode diagnostic. Analyze rebuilds the same trajectory and crop rectangles that encode would use, then reports on the result without writing a video. Use it to check stability and catch problems before kicking off a long encode.

## When to use it

- Before encode, to verify crop-path stability and solver context.
- To diagnose interval scores and seed density without waiting for video encoding.
- To compare what-if scenarios (e.g., different aspect ratios) without re-solving.

## Diagnostic HTML report (--plot)

The `-p`/`--plot` flag writes a self-contained HTML diagnostic report at
`tr_config/<stem>.encode_analysis.html`. One file is written per video; all
canvas JS and JSON data are embedded inline with no external dependencies.

Four panels are rendered:

- **Zoom stability (zoom bouncing).** Raw `crop_h` plus a 9-frame nan-aware
  mean overlay; twin axis shows `torso_h` and `torso_w`. The gap between raw
  and smoothed `crop_h` is the bouncing magnitude.
- **Zoom multiple (achieved vs configured).** `crop_h / torso_h` with a
  dashed reference line at the configured `torso_height_multiple` from
  `tr_config/<stem>.yaml`.
- **Camera motion.** `hypot(dx, dy)` per frame (left axis) and `scale` (right
  axis). Anomalous spikes or scale jumps explain encode-time wobble.
- **Runner ground speed.** Torso centers projected to scene coordinates and
  first-differenced. Raw + 5-frame mean overlay; secondary y-axis shows scene
  units per second when fps is known.

**Graceful degradation:** when camera-motion data is missing, the camera-motion
and runner-speed panels are skipped and a warning is shown at the top of the
report. When the scene transform is unavailable, only the runner-speed panel is
skipped.

**Interaction:** hover for frame/seconds/value tooltip; drag horizontally to
zoom the x-range (synced across all panels); double-click anywhere to reset
zoom; per-panel checkbox row toggles series visibility.

For deeper architecture and per-panel interpretation guidance, see
[docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md](../TRACK_RUNNER_ANALYZE_AND_ENCODE.md).

## Command line reference

<!-- BEGIN AUTO HELP: analyze -->
```text
usage: track_runner.py analyze [-h] [--aspect ASPECT] [-s] [-t TOP_N]
                               [-g GAP_TOP_N] [-p] [-S START_TIME]

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
  -p, --plot            write HTML diagnostic report alongside the
                        encode_analysis.yaml
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
- HTML diagnostic report at `tr_config/<stem>.encode_analysis.html` when `--plot`/`-p` is given. Self-contained (embedded JSON + inlined vanilla JS); see the "Diagnostic HTML report (--plot)" section above.

**Prerequisites:** Analyze needs `setup` and `solve` to have run first. If either is missing the command exits with a "run 'setup' and 'solve' first" message.

**Options:**

- `--aspect` Override the crop aspect ratio (e.g. `1:1`, `16:9`) to test what a different aspect would look like without re-encoding.
- `--seed`, `-s` Chain into the seeding UI after analysis (useful for targeted annotation).
- `-t`, `--top` Limit output to the top N weak intervals (useful for focusing on the worst problems).
- `-g`, `--gaps` Show gap analysis (intervals with no solver prediction).

For the detailed architecture of analyze and encode, see [../TRACK_RUNNER_ANALYZE_AND_ENCODE.md](../TRACK_RUNNER_ANALYZE_AND_ENCODE.md).
