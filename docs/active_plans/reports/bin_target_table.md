# Default bin target table (evidence)

Evidence record for M2 / WS2-default. The production-solve default bin is the
shared floor selector at target width 1440 (option B), CONFIRMED by the human on
2026-06-14 (floor rule, target 1440). The CLI default now routes the no-flag
case through this selector; explicit `--bin N` stays exact and `--bin 1` is the
full-res escape hatch.

The target is a project-wide constant in code:
`frame_reader.TARGET_DEFAULT_WIDTH_PX` (the single source of truth, change one
value to retune). It is not in any config, YAML, schema, or argparse flag; the
only per-run levers are `--bin` / `--auto-bin`.

Formula: `bin = max(1, floor(source_width / target_width))`, target_width=1440.
Integer floor division (`//`) is exactly floor for positive ints.
Processed width below is `source_width // bin` (post-bin, pre-goodbox snap).

## Confirmed table (target 1440, floor)

| source_width | bin | processed_width | note |
| --- | --- | --- | --- |
| 3840 (4K) | 2 | 1920 | analyses at 1080p band |
| 2880 (2.8K) | 2 | 1440 | |
| 2704 | 1 | 2704 | full-res |
| 2560 (1440p) | 1 | 2560 | full-res, stays bin 1 |
| 1920 (1080p) | 1 | 1920 | full-res, stays bin 1 |
| 1440 | 1 | 1440 | full-res |

Under floor@1440, 4K (3840-wide) analyses bin to the 1080p band (1920x1080),
and 1440p (2560-wide) and below stay full-res: `floor(2560/1440)=1`. A round
rule would over-bin both (`round(3840/1440)=3`, `round(2560/1440)=2`); floor is
the confirmed policy precisely to avoid that.

## Non-power-of-two support

Non-power-of-two bins (e.g. bin 3) already ship via `--auto-bin` (the CLI
computes `max(1, round(source_height / target))` at
[cli.py](../../../track_runner/cli.py)), so the items below are
confirmation, not new support. All bin handling accepts arbitrary integer bins:

- `FrameReader._apply_bin` resizes via `cv2.resize` to
  `(scaled_width, scaled_height)` where each dim is `source // bin_factor`;
  no power-of-two assumption. See
  [frame_reader.py](../../../common_tools/frame_reader.py).
- `_resolve_frame_geometry` computes `scaled = source // bin_factor` for both
  axes at any integer bin. See
  [frame_reader.py](../../../common_tools/frame_reader.py).
- Goodbox snap (`_snap_or_keep`) snaps the already-scaled dimension down to the
  largest goodbox; it operates on `scaled_width` regardless of bin_factor, so
  it is bin-agnostic. See
  [frame_reader.py](../../../common_tools/frame_reader.py).
- coord_space scaling is pure scale-by-bin_factor with no offset
  (`source_to_processed` divides by `bin_factor`,
  `processed_to_source` multiplies); valid for any integer bin. See
  [frame_reader.py](../../../common_tools/frame_reader.py).
- M2 cache / view boundary carries `bin_factor` and asserts equality on use
  (`SeedsView.bin_factor` plus the mismatch guard), so any non-1 bin artifact
  is keyed and validated like any other. See
  [state_io.py](../../../track_runner/state_io.py).

## Compatibility note: width-based default vs height-based --auto-bin

The shared default selector keys on source WIDTH against the project-wide
target (1440). The existing `--auto-bin HEIGHT` flag keys on source HEIGHT via
`max(1, round(source_height / target))` and is unchanged. The two paths use
different axes on purpose: the default targets a horizontal-resolution band,
while `--auto-bin` lets the user target a specific binned height.

## Status

CONFIRMED: human approved floor rule at target 1440 (option B) on 2026-06-14.
