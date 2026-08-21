# News

Curated release highlights and announcements. For the full dated change log
see [CHANGELOG.md](CHANGELOG.md).

## 2026-06 -- version 26.06

- **prepare mode** (fast-read video): new `prepare` subcommand transcodes a
  4K HEVC source to an H.264 fast-read working copy. Working modes decode from
  the fast-read video; the final encode always uses the original. Reduces
  per-frame decode cost from 450-575 ms to 6-14 ms on 4K HEVC Main-10 HDR
  sources. See [PREPARE.md](modes/PREPARE.md).

- **Windowed Viterbi walker (default)**: the pairwise velocity-delta cost model
  replaced the earlier first-order displacement cost. Pairwise velocity deltas
  penalise acceleration, keeping a moving runner preferred over a stationary
  distractor. Walker is now the default blob pass on Stage-4-promoted intervals;
  Hermite fallback in place when the walker produces zero post-seed accepted
  frames.

- **Stage-4 worker pool**: Stage-4 blob-promoted intervals now dispatch through
  the same worker pool as Stage 3 (previously ran in the main process). Parallel
  solve times on multi-interval runs are reduced.

- **Windowed Viterbi walker parity tests**: `tests/test_walk_cost_model.py`,
  `tests/test_walk_io_parity.py`, and `tests/test_walk_viterbi_brute_force.py`
  cover the cost model, IO parity, and DP correctness respectively.

## Known gaps

- No entries before 2026-06. Earlier milestones are in
  [CHANGELOG.md](CHANGELOG.md) and [RELEASE_HISTORY.md](RELEASE_HISTORY.md).
