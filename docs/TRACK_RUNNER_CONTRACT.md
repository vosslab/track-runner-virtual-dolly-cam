# Track runner contract

Hard, permanent invariants for the track runner. This document is short on
purpose: it lists only non-negotiable rules. Philosophy and rationale live
in [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md). The full technical spec
lives in [TRACK_RUNNER_V3_SPEC.md](TRACK_RUNNER_V3_SPEC.md).

If any other doc or any code appears to conflict with this contract, the
contract wins. The other doc or the code is the thing to fix.

## C1. Torso box is the unit of scale for runner-relative decisions

Runners range from near-full-frame down to roughly 10 px tall across the
scenes this tool is used on. Pixel thresholds for runner-relative decisions
are structurally wrong across that range, not merely inelegant. They are a
design bug this contract forbids going forward.

Not allowed as raw pixels (must be expressed in torso units):

- search radii tied to target location
- gating distances
- min/max allowed target motion; per-frame velocity caps used as tracking
  logic
- target size thresholds; minimum competitor height
- competitor acceptance thresholds
- any decision about runner geometry expressed in scene terms

Allowed as raw pixels (non-scene quantities):

- encoder output sizing (target resolution, bitrate)
- detector network input size
- UI line widths, font sizes, overlay styling
- low-level raster-kernel sizes that are about the image, not the runner

New pixel constants must land in the "allowed" bucket above or be
expressed as a multiple of the current torso box.

## C2. Pre-race frames define a fixed reference

Before `race_start_frame` the runner is stationary relative to the
surroundings, and the camera and zoom are fixed. Seed variation in this
range reflects human annotation noise, not runner motion.

- Torso-box dimensions for frames in `[0, race_start_frame)` are the
  average of user seeds across that range.
- Torso-box center in that range is anchored to the surroundings (in
  scene coordinates), not re-estimated per frame.
- Code that treats pre-race seeds as independent measurements of a moving
  target violates this rule.

## C3. Intervals are independent across intervals

Seeds are hard anchors. An interval runs seed -> seed.

- Solving an interval must not read state from neighboring intervals,
  global accumulators, or prior solve results.
- Solve and refine are both per-interval and parallelizable. No
  exceptions.
- Refine may reuse bookkeeping about which intervals changed (for cache
  invalidation and scheduling), but not trajectory state from prior
  interval solves.
- Future interval-to-interval smoothing, if added, is a separate pass
  layered on top of solve and refine. It never lives inside them.

## C4. Seeds are truth for solve; seed quality is ranked for the user

- The solver treats seeds as hard anchors. It must not silently override
  or soften them.
- A separate scoring step may flag seeds that look inconsistent with
  neighbors, detection evidence, or pre-race averages, and surface those
  seeds to the user.
- Bad seeds are fixed by the user editing them. "Seeds are truth" means
  best-available truth at solve time, not permanent truth.

## C5. No temporal memory inside a pass, beyond raw image-derived caches

Principle: caches may store image-derived observations, never accepted
interpretations of those observations.

Allowed per-frame persistence inside a single solve or refine pass:

- residual magnitude maps
- validity masks
- raw extracted blobs (pre-gate)

Forbidden: any accumulator built from accepted outputs, including but not
limited to accepted blobs, `snap_pred` history, gate decisions,
`last_blob`, `prev_accepted_*`, `*_chain_*`, and miss counters.

Per-interval worker state is allowed; it dies with the worker.

## C6. Jersey color and runner-appearance template matching are not reliable

- Jersey and clothing color, color-histogram matching, and
  runner-appearance template matching are banned as identity or
  classification evidence.
- Local patch correlation used for non-identity purposes, for example
  short-horizon propagation flow, is out of scope for this clause.
- Rationale: runner appearance varies with pose, distance, lighting,
  occlusion, and motion blur, and prior versions produced unreliable
  results. See
  [archive/TRACK_RUNNER_V3_FINDINGS.md](archive/TRACK_RUNNER_V3_FINDINGS.md).
- The current active machine-evidence set lives in
  [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) and may evolve. This
  clause only forbids re-introducing the unreliable cues.

## Relationship to other docs

- [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md): reasoning and
  philosophy, including the current (evolving) signal set.
- [TRACK_RUNNER_V3_SPEC.md](TRACK_RUNNER_V3_SPEC.md): technical
  specification.
- This contract: the non-negotiable subset. On conflict, the contract
  wins and the conflicting doc or code is corrected.
