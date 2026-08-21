# Roadmap

Track Runner is a current-only single-runner workflow: annotate human seed
anchors, solve FWD and BWD paths independently, review results, and encode a
stable crop. The active mode reference is [MODES.md](MODES.md).

## Current priorities

- Keep the seed, solve, review, and encode workflow portable and deterministic.
- Preserve source-coordinate seed truth and torso-width-normalized decisions.
- Keep the production windowed Viterbi walker under `track_runner/blob_walk/`.
- Improve only behavior that has a clear user-facing workflow and an automated,
  self-contained validation path.

## Deferred ideas

- Advisory seed-review suggestions from residual-motion evidence.
- Optional race-end estimates when they support a real review or encode flow.

Retired experiments and historical implementation detail are recorded in the
changelog and archive rather than treated as active roadmap work.
