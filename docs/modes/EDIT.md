# edit mode

Fix or double-check existing seeds. Not part of the main pipeline; use it when a seed looks wrong or when the scoring step surfaces a low-quality seed that needs human review.

## When to use it

- When you spot a bad or inconsistent seed while reviewing.
- To validate seeds that the scoring report flagged as low-quality.
- Outside the main target/refine loop, as a targeted fix.

## Command line reference

<!-- BEGIN AUTO HELP: edit -->
```text
usage: track_runner.py edit [-h] [-s {high,medium,low}] [-S START_TIME]

options:
  -h, --help            show this help message and exit
  -s {high,medium,low}, --severity {high,medium,low}
                        Filter seeds near weak intervals at this severity
                        threshold.
  -S START_TIME, --start START_TIME
                        Start time in seconds (seek UI to this position on
                        launch).
```
<!-- END AUTO HELP: edit -->

## Notes

Options: `-s`/`--severity` filters seeds near weak intervals at a threshold level (`high`, `medium`, `low`), showing only seeds near intervals above that severity tier.

Edit mode opens the same annotation UI as seed and target modes. The display highlights seeds near weak intervals so you can review them in context. Changes are saved immediately upon placement.

Unlike target (which adds seeds at weak frames) or seed (which adds fresh seeds), edit focuses on reviewing and correcting existing seeds. Use this as a quality-control pass before encode if you are concerned about seed quality.
