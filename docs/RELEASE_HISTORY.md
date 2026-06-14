# Release history

Organized log of released versions and their release dates.

## 26.06 -- 2026-06

First versioned release under the CalVer `YY.MM` scheme.

Notable shipped qualities:

- **Five-stage solve pipeline**: Stage 1 (camera motion), Stage 2 (race-start
  ID), Stage 3 (Hermite pass on all intervals), Stage 4 (blob-promoted
  re-solve on low/fair-confidence intervals), Stage 5 (optional full blob pass
  via `--full`).
- **Windowed Viterbi walker** as the default Stage-4 blob pass. Pairwise
  velocity-delta cost model. Hermite fallback for zero-accept
  stall.
- **prepare mode** for fast-read video creation. Reduces 4K HEVC random-read
  cost from 450-575 ms to 6-14 ms per frame. See
  [docs/modes/PREPARE.md](modes/PREPARE.md).
- **Stage-4 worker pool dispatch**: Stage-4 promoted intervals use the same
  parallel worker pool as Stage 3.
- **Viterbi cost weights**: fixed constants in
  `track_runner/blob_walk/walk_viterbi.py` (as of 2026-06-13); the
  `walker_costs` YAML section was removed and weights require code
  changes to tune.
- **SeedsView / state_io unification**: seeds and geometry loaded through a
  single `SeedsView` abstraction.

## Known gaps

- No earlier release entries. History before 2026-06 is in
  [CHANGELOG.md](CHANGELOG.md) commit messages.
