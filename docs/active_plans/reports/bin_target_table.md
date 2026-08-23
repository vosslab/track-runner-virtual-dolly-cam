# Default bin target table (evidence)

Evidence record for the production-solve default bin. The selector budgets the
analysis frame by pixel AREA against the project-wide constant
`frame_reader.MAX_ANALYSIS_PIXELS`. The CLI default routes the no-flag case
through this selector; explicit `--bin N` stays exact and `--bin 1` is the
full-res escape hatch.

The budget is a project-wide constant in code (the single source of truth,
change one value to retune). It is not in any config, YAML, schema, or argparse
flag; the only per-run levers are `--bin` / `--auto-bin`.

Formula: `bin = max(1, ceil(sqrt(source_pixels / max_pixels)))`,
max_pixels=1036800. Ceiling division gives the invariant
`processed_area <= max_pixels` for every source, and the `max(1, ...)` clamp
keeps sources already under budget at full resolution. Processed dimensions
below are `source // bin` (post-bin, pre-goodbox snap).

## Current table (area budget 1036800)

| source | bin | processed | processed area | note |
| --- | --- | --- | --- | --- |
| 3840x2160 (4K) | 3 | 1280x720 | 0.92 MP | |
| 2880x1620 (2.8K) | 3 | 960x540 | 0.52 MP | |
| 2704x1520 (2.7K) | 2 | 1352x760 | 1.03 MP | tightest case, 99% of budget |
| 2560x1440 (1440p) | 2 | 1280x720 | 0.92 MP | |
| 1920x1080 (1080p) | 2 | 960x540 | 0.52 MP | |
| 1280x720 (720p) | 1 | 1280x720 | 0.92 MP | already under budget |

### Why area replaced the earlier width target

The previous rule, `floor(source_width / 1440)`, inverted analysis cost against
source resolution: a 1440p source stayed at bin 1 and analyzed at 2560x1440,
larger than a 4K source, which binned to 1920x1080. Budgeting on area removes
the inversion and additionally prices non-16:9 sources correctly, since a
letterboxed source at a given width carries far fewer pixels than a 16:9 one.

### Measured small-target recovery

`tests/tracking/test_small_target_bin_recovery.py` synthesizes a frame pair with
a small displaced target and runs the production residual and blob-extraction
path over a sweep of target sizes and bin factors. The table below is the output
of that harness's `measure_recovery`, on a 1920x1080 synthetic source with the
target displaced 12 px; regenerate it from the harness rather than editing the
numbers by hand:

| source target | bin 1 | bin 2 | bin 3 | bin 4 |
| --- | --- | --- | --- | --- |
| 40x80 | found, err 20.5 px | found, err 21.0 px | found, err 18.5 px | found, err 22.0 px |
| 20x40 | found, err 10.5 px | found, err 11.0 px | found, err 12.0 px | found, err 12.0 px |
| 10x20 | found, err 6.5 px | found, err 7.0 px | found, err 2.2 px | found, err 1.0 px |
| 6x12 | found, err 6.5 px | found, err 7.0 px | found, err 7.8 px | found, err 7.0 px |

The harness asserts recovery and a bounded centroid error across every cell, so
the table and the gate cannot drift apart.

Errors are reported in SOURCE pixels against the true displacement midpoint.
Blob recovery holds at every tested bin factor down to a 6x12 source target, and
centroid error stays flat across bin factors for a given target size: the error
is dominated by the two-lobe leave/arrive structure of a residual, which scales
with target size rather than with bin factor.

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

## Compatibility note: area-based default vs height-based --auto-bin

The shared default selector budgets source pixel AREA against the project-wide
constant. The existing `--auto-bin HEIGHT` flag keys on source HEIGHT via
`max(1, round(source_height / target))` and is unchanged. The two paths use
different quantities on purpose: the default targets a total analysis cost,
while `--auto-bin` lets the user target a specific binned height.

## Status

CURRENT: area budget at 1036800 px, with small-target recovery measured by
`tests/tracking/test_small_target_bin_recovery.py`. Superseded the floor rule at
target width 1440 that was confirmed on 2026-06-14.
