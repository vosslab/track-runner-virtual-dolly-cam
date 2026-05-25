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
                       and residual stages only. Integer >= 1; default 1 (no
                       bin). bin_factor > 1 also crops each scaled axis to the
                       largest FFT-friendly goodbox not exceeding it (origin-
                       preserving right/bottom crop). Source-frame outputs
                       unchanged.
  --auto-bin [HEIGHT]  Auto-pick bin_factor from source height: bin = max(1,
                       round(source_h / target)). bin_factor is a whole
                       number, so actual binned height only approximates the
                       target. Source dims that are not multiples of bin
                       silently drop at most (bin-1) right/bottom pixels, the
                       same kind of crop goodbox already does. Bare flag
                       targets 480; pass --auto-bin 720 for 720. Examples at
                       target=480: 720->bin2 (360), 1080->bin2 (540),
                       1440->bin3 (480), 2160->bin4 (540), 2816->bin6 (469).
                       At target=720: 1080->bin1 (1080), 2160->bin3 (720).
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

**First run after upgrade note:** The first solve run after the 2026-04-25 staging restructure will print "first run after solve restructure: full recompute expected" because the cache namespaces are new. Subsequent runs hit the cache normally. Run `--hermite-only` for a quick first-pass read if full solve time is a concern.

**`--bin N` (optional, speed-focused):** Applies a spatial downsample to the camera-motion (Stage 1) and residual-motion stages, leaving every persisted output in source-frame pixels. Helpful on 4K input. Goodbox crop is automatic when `bin > 1` (right/bottom edges only, capped at 10% per-axis loss). The interval cache is bin-invariant (per-frame work crosses the source<->processed boundary inside the per-frame stages and emits source-frame outputs). The canonical `<video>.track_runner.camera_motion.npz` file is written once per video per motion-model; changing `--bin` between runs reuses the same camera motion artifact because bin and processed geometry are not part of the motion-model staleness check.

For the pipeline philosophy, see [../TRACK_RUNNER_DESIGN.md](../TRACK_RUNNER_DESIGN.md) (stages and signal hierarchy). For the camera motion method, see [../TR_CAMERA_MOTION_METHOD.md](../TR_CAMERA_MOTION_METHOD.md).
