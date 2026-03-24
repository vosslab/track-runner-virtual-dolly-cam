# Usage

## Entry point

```bash
source source_me.sh
python track_runner/track_runner.py -i VIDEO.mp4 <subcommand>
```

## Global options

Global options must appear before the subcommand.

| Flag | Description |
| --- | --- |
| `-i`, `--input` | Input video file path (required). |
| `-c`, `--config` | Config YAML file path (default: auto-detected). |
| `-d`, `--debug` | Enable debug video output with tracking overlays. |
| `-w`, `--workers` | Number of parallel workers (default: half of CPU cores). |
| `--time-range` | Limit processing to a time range in seconds. Format: `START:END`, `START:`, or `:END`. |

## Subcommands

### seed

Interactively place seed annotations on the runner. Seeds are anchor frames that establish the runner's identity and position.

```bash
python track_runner/track_runner.py -i VIDEO.mp4 seed
```

Options: `-I`/`--seed-interval` sets the interval in seconds between seed frames (default 10).

### edit

Review, fix, or delete existing seeds interactively.

```bash
python track_runner/track_runner.py -i VIDEO.mp4 edit
```

Options: `-s`/`--severity` filters seeds near weak intervals at a threshold (`high`, `medium`, `low`).

### target

Add seeds at weak interval frames. Shows forward/backward propagation overlays to help place corrections.

```bash
python track_runner/track_runner.py -i VIDEO.mp4 target
```

Options: `-s`/`--severity` sets the minimum severity of weak intervals to target.

### solve

Full re-solve. Clears all prior results and solves every interval from scratch.

```bash
python track_runner/track_runner.py -i VIDEO.mp4 solve
```

### refine

Incremental re-solve. Only re-solves changed or new intervals; reuses prior results for unchanged intervals.

```bash
python track_runner/track_runner.py -i VIDEO.mp4 refine
```

### encode

Encode cropped video from the existing trajectory.

```bash
python track_runner/track_runner.py -i VIDEO.mp4 encode
```

Options:

| Flag | Description |
| --- | --- |
| `-o`, `--output` | Output video file path (auto-generated if omitted). |
| `--aspect` | Override crop aspect ratio (e.g. `1:1`, `16:9`). |
| `--keep-temp` | Keep temporary files after encoding. |
| `-F`, `--encode-filters` | Comma-separated filter pipeline (e.g. `bilateral,hqdn3d`). |

### analyze

Analyze crop path stability before encoding.

```bash
python track_runner/track_runner.py -i VIDEO.mp4 analyze
```

Options: `--aspect` overrides the crop aspect ratio.

## Typical workflow

1. **Seed** -- place anchor annotations on the runner at regular intervals.
2. **Solve** -- run the full solver to propagate tracking between seeds.
3. **Analyze** (optional) -- check crop path stability and identify weak intervals.
4. **Target** (optional) -- add corrective seeds at weak intervals.
5. **Refine** (optional) -- incrementally re-solve after adding seeds.
6. **Encode** -- produce the final cropped output video.

## Configuration

The default config file is [track_runner/track_runner.config.yaml](track_runner/track_runner.config.yaml). Override with `-c`/`--config`. Settings include detection confidence threshold, crop aspect ratio, fill ratio, video codec, CRF, and encode filter pipeline.

## Input and output

- **Input:** any video file readable by ffmpeg/mediainfo.
- **Output:** cropped and stabilized video file. State data (seeds, intervals, diagnostics) is stored as JSON alongside the input video.
