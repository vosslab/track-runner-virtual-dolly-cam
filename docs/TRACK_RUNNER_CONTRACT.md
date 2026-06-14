# Track runner contract

Hard, permanent invariants for the track runner. This document is short on
purpose: it lists only non-negotiable rules. Philosophy and rationale live
in [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md). The full technical spec
lives in [TRACK_RUNNER_V3_SPEC.md](TRACK_RUNNER_V3_SPEC.md).

If any other doc or any code appears to conflict with this contract, the
contract wins. The other doc or the code is the thing to fix.

User must approve any new contract items. Agents are not allowed to edit.

## C1. A seed is a human-authored annotation of a torso box for one frame

- A seed is a human-authored annotation of a torso box for one frame.
- Usually this annotation is a torso box. It may also be a human-confirmed not_in_frame state.
- Seeds are the truth anchors for solve.
- Machine-produced geometry, including predictions, suggestions, polish outputs, and heat-map blob adjustments, is not a seed until a human commits it.
- Code and docs must not label uncommitted machine geometry as a seed.

## C2. Torso box is the unit of scale for runner-relative decisions

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

## C3. Seeds are truth for solve; seed quality is ranked for the user

- The solver treats seeds as hard anchors. It must not silently override
  or soften them.
- A separate scoring step may flag seeds that look inconsistent with
  neighbors, detection evidence, or pre-race averages, and surface those
  seeds to the user.
- Bad seeds are fixed by the user editing them. "Seeds are truth" means
  best-available truth at solve time, not permanent truth.

## C4. Pre-race start frames define a fixed reference

Before `race_start_frame` the runner is stationary relative to the
surroundings, and the camera and zoom are fixed. Seed variation in this
range reflects human annotation noise, not runner motion.

- Torso-box dimensions for frames in `[TRACK_RUNNER_V3_FINDINGS.md](archive/TRACK_RUNNER_V3_FINDINGS.md).
- The current active machine-evidence set lives in
  [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) and may evolve. This
  clause only forbids re-introducing the unreliable cues.

## C9. FWD/BWD must remain independent for scoring

Within a single interval, the forward pass and backward pass may each keep
their own per-pass working state. That state must remain pass-local.

Terminology for per-interval geometry ("forward interval path",
"backward interval path", "blended interval path") is defined in
`FWD_BWD_MODEL_METHODOLOGY.md`. "Blended
output" below refers to the blended interval path.

For scoring and review:
- agreement and uncertainty must be computed from the two independent
    pass trajectories, not from blended output
- the forward pass must not read backward-pass trajectory state
- the backward pass must not read forward-pass trajectory state
- neither pass may read blended output or stitched output while solving

Allowed:
- pass-local temporary state that exists only while solving one interval
- raw image-derived observations and caches
- a separate output-only corrected track, if added later, provided it is
    not used for FWD/BWD agreement scoring

## C10. Keep `SCHEMA_VERSION` unified

- Do not use multiple schema version constants.
- This is forbidden: `ITEM_SCHEMA_VERSION = 3` and `OBJECT_SCHEMA_VERSION = 4`.
- Use one `SCHEMA_VERSION` value everywhere schema is recorded.
- A `SCHEMA_VERSION` bump means an artifact was written under a new schema contract.
  It does not mean older schema versions are automatically invalid.
- Older schema versions should remain readable or migratable when safe.
- Metadata-only schema changes must not invalidate derived artifacts or caches by default.
- Only schema changes that affect the meaning, structure, or computation of a derived artifact
  should invalidate that artifact.
- Do not create separate version constants, such as observer or cache versions, to bypass `SCHEMA_VERSION`.
- If a stored artifact needs versioning, that versioning belongs under the unified `SCHEMA_VERSION` contract.
- Keep a history of `SCHEMA_VERSION` changes in `docs/TR_SCHEMA_VERSION_HISTORY.md`.
- In some cases, schema 1 might be byte-identical to schema 8.
  The version still changes to avoid mixed numbers across outputs and
  prevent silent mismatches in cached or derived artifacts.
- Any and all schema changes REQUIRE EXPLICIT HUMAN APPROVAL, no hiding it in a larger plan.
  The human user cannot be expected to read all details.
- Schema versions should not be changed unless it is completely necessary to store a variable
  on a per video basis and not a static constant. Many of the recent schema changes
  should NOT have been made.

## C11. Torso box information should be solved and stored for all frames

- When targeting frames for seeds in target mode, predicted torso boxes should
    be displayed. If predicted torso boxes are missing, the interval should be
    considered unsolved.
- Solved intervals have torso boxes for all frames.

## C12. Limit per frame content to only content that is needed

- Per-frame stats and metadata are discouraged if unused.
- Torso box data is x, y, w, h.
- Scaling, shifts, and confidence are fine for camera motion.
- Do not store parameters per frame when they are not frame-based, such as
    source frame size, binning, or motion model. That is unneeded data.

## C13. Cleaner configuration files

- No directories in tr_config.
- Cache is temporary and never saved or depended on between runs. If it is needed, for future
    runs then it is not cache.
- Frame-based data is minimal, stored in .npz, and should prefer integers over
    floats.
- Interval and seed data is usually JSON.
- Reject intervals only if needed data is missing.
    Time stamps are not relevant for solve quality.
- Do not use a config_hash field for bookkeeping or diagnostics. It is too
    fragile.

Examples of fragile values:
basename       <-- filename string (broke on .MOV -> .mkv rename)
size_bytes     <-- container size (broke on MOV -> MKV remux: 2.96GB -> 2.95GB)


## Relationship to other docs

- [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md): reasoning and
  philosophy, including the current (evolving) signal set.
- [TRACK_RUNNER_V3_SPEC.md](TRACK_RUNNER_V3_SPEC.md): technical
  specification.
- This contract: the non-negotiable subset. On conflict, the contract
  wins and the conflicting doc or code is corrected.
