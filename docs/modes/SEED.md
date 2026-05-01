# seed mode

Interactively place seed annotations on the runner. Seeds are human-authored anchor frames that establish the runner's identity and position; they are the truth anchors for solve (contract C1).

## When to use it

- After setup, to establish the initial seed set.
- Whenever you need to manually add or adjust seed annotations.
- Before running `solve` for the first time.

## Command line reference

<!-- BEGIN AUTO HELP: seed -->
```text
usage: track_runner.py seed [-h] [-I SEED_INTERVAL] [-S START_TIME]

options:
  -h, --help            show this help message and exit
  -I SEED_INTERVAL, --seed-interval SEED_INTERVAL
                        Interval in seconds between seed frames (default 10).
  -S START_TIME, --start START_TIME
                        Start time in seconds (seek UI to this position on
                        launch).
```
<!-- END AUTO HELP: seed -->

## Notes

Options: `-I`/`--seed-interval` sets the interval in seconds between candidate seed frames (default 10).

The annotation UI supports keyboard shortcuts for fast placement and frame navigation. See [../TRACK_RUNNER_KEYBINDINGS.md](../TRACK_RUNNER_KEYBINDINGS.md) for the full keybindings reference including box drawing, frame advance, zoom, and heat-map overlay controls.

Per contract C1, seeds are human-authored and are the source of truth for tracking. Machine-produced geometry (predictions, suggestions, machine-confirmed positions) is not a seed until the user commits it via the annotation UI.
