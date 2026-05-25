# refine mode

Incremental re-solve that only re-solves changed or new intervals and reuses prior results for unchanged intervals. Refine is the fast follow-up to target: after placing new seeds in target mode, run refine to pick them up and recompute affected intervals.

## When to use it

- After target, to incorporate new seeds placed in the previous annotation pass.
- Repeatedly with target: target -> refine -> (repeat if needed) until interval scores are acceptable.
- Never forces a full solve: if refine detects that a full solve is needed, it exits and directs you to run `solve` instead (per contract C6).

## Command line reference

<!-- BEGIN AUTO HELP: refine -->
```text
usage: track_runner.py refine [-h] [-f | -H]
                              [--bin BIN_FACTOR | --auto-bin [HEIGHT]]

options:
  -h, --help           show this help message and exit
  -f, --full           Run Stage 5: blob pass on every interval refine touches
                       (slow).
  -H, --hermite-only   Stop after Stage 3: Hermite-only refine (fast
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
<!-- END AUTO HELP: refine -->

## Notes

Refine operates within the same stage pipeline as solve (stages 1-4 default, or respecting `--full` or `--hermite-only` flags). Only intervals that contain new or changed seeds are re-solved; untouched intervals retain their prior results.

If refine exits with a message to run solve, heed it. The reason is usually:
- A bulk seed change that affects the race-start detection or scene transform in a way that invalidates cached upstream state.
- Structural changes to the seed set that cannot be handled incrementally.
- Missing camera-motion artifact: refine never recomputes Stage 1. It loads the canonical `<video>.track_runner.camera_motion.npz` and validates that the persisted `motion_model` matches the current configuration. Refine aborts with "Camera-motion artifact for this solve is missing. Run solve first." if the file is absent or stale.

**`--bin` and refine:** Refine accepts `--bin` for the per-frame residual stages, but refine never recomputes Stage 1 regardless of `--bin`. Camera motion is a property of the video, not of the seeds, so changing `--bin` between solve and refine does not re-run Stage 1; refine reuses the canonical camera-motion artifact stored by the prior solve. The motion-model staleness check ensures the artifact matches the current configuration.

For interval independence philosophy, see contract C5 in [../TRACK_RUNNER_CONTRACT.md](../TRACK_RUNNER_CONTRACT.md).
