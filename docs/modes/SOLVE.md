# solve mode

Full solve runs through multiple stages: camera motion precompute, race-start identification, Hermite-only interpolation on all post-race intervals, and optional blob-coupled refinement on weak intervals. Clears all prior results and solves every interval from scratch.

## When to use it

- Initial solve after establishing a seed set.
- Whenever you need a complete recompute (e.g., after bulk seed edits or camera configuration changes).
- If you are unsure whether incremental refine would suffice, solve errs on the side of correctness.

## Command line reference

<!-- BEGIN AUTO HELP: solve -->
```text
usage: track_runner.py solve [-h] [-y | --keep | --upgrade] [-f | -H]
                             [--bin BIN_FACTOR | --auto-bin [HEIGHT]]

options:
  -h, --help           show this help message and exit
  -y, --yes            Auto-confirm the 'clear and re-solve from scratch?'
                       prompt. Use this for scripted re-solve runs.
  --keep               Auto-decline the 'clear and re-solve from scratch?'
                       prompt. Skip videos that already have a complete solve.
                       Use this for scripted runs that should only solve
                       missing videos.
  --upgrade            Run Stage 4 blob promotion on the existing
                       torso_box_coords store without re-doing Stage 3. Use
                       after a 'solve --hermite-only' batch to upgrade weak
                       intervals to blob results.
  -f, --full           Run Stage 5: blob pass on every post-race interval
                       (slow).
  -H, --hermite-only   Stop after Stage 3: Hermite-only solve (fast
                       diagnostics).
  --bin BIN_FACTOR     Optional spatial downsample applied to camera-motion
                       and residual stages only. Integer >= 1. When neither
                       --bin nor --auto-bin is given, the production default
                       selector picks a bin from source width (floor at the
                       project-wide default target; 1440p and below stay full-
                       res). Pass --bin 1 to force full resolution. bin_factor
                       > 1 also crops each scaled axis to the largest FFT-
                       friendly goodbox not exceeding it (origin-preserving
                       right/bottom crop). Source-frame outputs unchanged.
  --auto-bin [HEIGHT]  Auto-pick bin_factor from source. Bare flag (--auto-bin
                       with no value) routes through the project-wide width-
                       floor selector (same as the no-flag default:
                       floor(source_width / 1440)). With an explicit HEIGHT
                       value (--auto-bin 720), uses the height-based selector:
                       bin = max(1, round(source_h / HEIGHT)). bin_factor is a
                       whole number, so actual binned size only approximates
                       the target. Source dims that are not multiples of bin
                       silently drop at most (bin-1) right/bottom pixels, the
                       same kind of crop goodbox already does. Examples at
                       --auto-bin 720: 1080->bin1 (1080), 2160->bin3 (720).
                       Mutually exclusive with --bin.
```
<!-- END AUTO HELP: solve -->

## Notes

**Solve modes (choose at most one):**

- **(default)** Stages 1-4: Hermite on all intervals, blob-coupled re-solve on promoted intervals (low/fair confidence). Wall time ~5-10 min.
- `--full`, `-f` Stages 1-5: Hermite on all, then blob on every interval. Maximum fidelity. Wall time ~30-60 min.
- `--hermite-only`, `-H` Stages 1-3: Camera motion, race-start, Hermite only. Fast diagnostics, no blob. Wall time ~2-5 min.

**Common options:**

- `-y`, `--yes` Auto-confirm the "clear and re-solve from scratch?" prompt (useful in scripts).

**Default bin behavior (binned by default as of 2026-06-14):** Solve now picks
a bin factor automatically when no `--bin` or `--auto-bin` flag is given. The
rule is `floor(source_width / 1440)` (`TARGET_DEFAULT_WIDTH_PX` constant in
`common_tools/frame_reader.py`, not a config value):

| Source resolution | Bin factor | Processed resolution |
| --- | --- | --- |
| 4K 3840 x 2160 | 2 | 1920 x 1080 |
| 2.8K 2880 x 1620 | 2 | 1440 x 810 |
| 1440p 2560 x 1440 | 1 | full-res |
| 1080p 1920 x 1080 | 1 | full-res |

Use `--bin 1` to force full-resolution analysis (slower). Use `--bin N` for an
exact override. Use `--auto-bin HEIGHT` for a height-based target (different
formula; documented in the help above).

**Durable upgrade note:** The camera-motion artifact (`<video>.track_runner.camera_motion.npz`)
keys on `bin_factor`. The first solve run after upgrading to binned-by-default behavior
recomputes the camera-motion artifact for 4K and 2.8K sources because the prior artifact was
computed at `bin_factor=1` and the new default differs. Subsequent runs hit the camera-motion
cache normally. Interval cache entries are bin-invariant: a load-time migration strips any
legacy `/bin<B>` suffix from stored keys so no full interval re-solve is needed on a bin change
or upgrade. No SCHEMA_VERSION bump occurred; this is cache-key bookkeeping only.

**First run after staging restructure (2026-04-25):** The first solve run after
the 2026-04-25 staging restructure will print "first run after solve restructure:
full recompute expected" because the cache namespaces are new. Subsequent runs
hit the cache normally. Run `--hermite-only` for a quick first-pass read if full
solve time is a concern.

**`--bin N` (explicit override):** Applies a spatial downsample to the
camera-motion (Stage 1) and residual-motion stages, leaving every persisted
output in source-frame pixels. Goodbox crop is automatic when `bin > 1`
(right/bottom edges only, capped at 10% per-axis loss). The entire solve runs in
one coordinate space (PROCESSED at bin > 1) and converts to SOURCE exactly once,
at the storage boundary before `state_io.write_torso_box_coords`. Hermite and
walker both produce correct SOURCE boxes via that single boundary.

For the pipeline philosophy, see [../TRACK_RUNNER_DESIGN.md](../TRACK_RUNNER_DESIGN.md) (stages and signal hierarchy). For the camera motion method, see [../TR_CAMERA_MOTION_METHOD.md](../TR_CAMERA_MOTION_METHOD.md).
