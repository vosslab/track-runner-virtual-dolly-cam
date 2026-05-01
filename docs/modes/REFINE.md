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

options:
  -h, --help          show this help message and exit
  -f, --full          Run Stage 5: blob pass on every interval refine touches
                      (slow).
  -H, --hermite-only  Stop after Stage 3: Hermite-only refine (fast
                      diagnostics).
```
<!-- END AUTO HELP: refine -->

## Notes

Refine operates within the same stage pipeline as solve (stages 1-4 default, or respecting `--full` or `--hermite-only` flags). Only intervals that contain new or changed seeds are re-solved; untouched intervals retain their prior results.

If refine exits with a message to run solve, heed it. The reason is usually:
- A bulk seed change that affects the race-start detection or scene transform in a way that invalidates cached upstream state.
- Structural changes to the seed set that cannot be handled incrementally.

For interval independence philosophy, see contract C5 in [../TRACK_RUNNER_CONTRACT.md](../TRACK_RUNNER_CONTRACT.md).
