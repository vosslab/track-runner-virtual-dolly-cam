# Usage

Track Runner follows one selected athlete through a track-meet video. Human
seed boxes establish runner identity; solve creates the trajectory; encode
produces the cropped output video.

## Quick start

Use an MKV source. Run the commands from the repository root after installing
the dependencies described in [INSTALL.md](INSTALL.md).

```bash
source source_me.sh
python3 track_runner/track_runner.py -i RACE.mkv setup
python3 track_runner/track_runner.py -i RACE.mkv seed
python3 track_runner/track_runner.py -i RACE.mkv solve
python3 track_runner/track_runner.py -i RACE.mkv encode
```

`setup` creates the per-video configuration. `seed` collects human torso-box
anchors. `solve` writes the current interval-score, torso-coordinate, and
camera-motion artifacts under `tr_config/`. `encode` reads those artifacts and
writes the tracked video beside the source unless `-o` selects another path.

For a 4K HEVC source, create the optional fast-read decode before setup:

```bash
source source_me.sh
python3 track_runner/track_runner.py -i RACE.mkv prepare
```

## Refine a solve

Use `target` to find weak intervals and add more seeds. `refine` recalculates
only the intervals changed by those seeds. `analyze` reports crop-path and
solver diagnostics without encoding a video. `edit` changes existing seed
annotations directly.

```bash
source source_me.sh
python3 track_runner/track_runner.py -i RACE.mkv target
python3 track_runner/track_runner.py -i RACE.mkv refine
python3 track_runner/track_runner.py -i RACE.mkv analyze
python3 track_runner/track_runner.py -i RACE.mkv encode
```

## Common commands

```bash
# Show global options and mode names.
python3 track_runner/track_runner.py --help

# Show options for one mode.
python3 track_runner/track_runner.py -i RACE.mkv solve --help

# Re-solve without an interactive clear prompt.
python3 track_runner/track_runner.py -i RACE.mkv solve --yes

# Choose the output path and container.
python3 track_runner/track_runner.py -i RACE.mkv -o RACE_tracked.mp4 encode
```

Global options, including `-i`, `-c`, `-w`, and `--time-range`, appear before
the mode name. Each mode page in [MODES.md](MODES.md) contains its complete
current option reference.

## Inputs and outputs

- Input video: one `.mkv` source video.
- Human input: seed torso boxes in the seed, target, or edit interface.
- Per-video state: current configuration, seeds, interval scores, torso paths,
  and camera motion under `tr_config/`.
- Output video: a cropped tracked MKV by default, or MP4 when the output path
  uses `.mp4`.

Current artifacts remain bound to their source geometry. A consuming mode
rejects coordinates or scores from a video with different dimensions or frame
count; run `solve` to regenerate derived output for the current video.

## More detail

- [MODES.md](MODES.md) lists the workflow and every mode page.
- [TRACK_RUNNER_KEYBINDINGS.md](TRACK_RUNNER_KEYBINDINGS.md) lists annotation
  shortcuts.
- [TR_CONFIG_FILES.md](TR_CONFIG_FILES.md) documents persisted artifacts.
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) covers supported recovery steps.
