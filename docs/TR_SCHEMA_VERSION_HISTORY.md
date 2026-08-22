# Track Runner Schema Version History

This document logs the evolution of `SCHEMA_VERSION` across releases. The version controls on-disk format changes to `interval_scores.json`, `torso_box_coords.npz`, and their required metadata.

Per contract C10, there is one unified `SCHEMA_VERSION` for the entire track_runner project, defined in [tr_schema.py](../track_runner/tr_schema.py). All schema references must use this constant; independent versions are forbidden. This file records the versioning history specifically, separate from the changelog which records code changes. A new version number is issued when the persisted artifact contract changes. Use `solve` for method-derived stale values and diagnostic history for telemetry.

## When to change SCHEMA_VERSION

Use `SCHEMA_VERSION` for approved persisted artifact contract changes (field names, layout, dtype/encoding, coordinate meaning, required metadata, approved per-video variables). Use `solve` to refresh method-derived stale values. Document diagnostic telemetry without changing the solver schema.

When a method change makes old cached values stale, run `solve` for a full re-solve and keep `SCHEMA_VERSION` fixed. `solve` is the cache-invalidation path for method changes.

Get explicit human approval before changing the persisted artifact contract or `SCHEMA_VERSION`.

| Change | Bump schema? | Why |
| --- | --- | --- |
| Add a necessary persisted per-video variable (after human approval; confirmed it cannot stay a constant, runtime-derived value, or diagnostic) | YES | The artifact contract changed; the bump is the consequence, not the approval |
| Change a stored dtype/number type (v10 uint16) | YES | Readers must decode the bytes differently |
| Change a computation method (v11 residual stride, v13/v14 walker) | Use `solve`; keep schema fixed | The stored format is the same; `solve` refreshes values |
| Add a diagnostic-only CSV column (v12) | Use diagnostic history; keep solver schema fixed | The verdict CSV is a diagnostic artifact |
| Change a stored value for a new video | Use existing contract; keep schema fixed | Values change per video; the contract does not |

## Checklist before changing SCHEMA_VERSION

Choose the correct path first: use schema review for persisted artifact contract changes, use `solve` for method-derived stale values, and use the diagnostic history for telemetry changes.

Get explicit human approval before changing the persisted artifact contract or `SCHEMA_VERSION`.

Every schema-history entry must state the stored field or contract changed and
the human approver.

A manager proposing any `SCHEMA_VERSION` change completes these affirmative steps in order:

1. Name the persisted artifact you are changing (`torso_box_coords.npz` or the
   interval-scores JSON). For changes that leave every persisted artifact identical,
   route to `solve` or diagnostic history and keep `SCHEMA_VERSION` fixed.
2. Route computation-method changes (walker DP, cost weights, residual stride,
   heat-map sampling) to `solve`: run `solve` to refresh stale values and keep
   `SCHEMA_VERSION` fixed. `solve` is the cache-invalidation path.
3. Route diagnostic telemetry changes (verdict-CSV columns) to the diagnostic
   history: document the column history and keep the solver `SCHEMA_VERSION` fixed.
4. For a genuine persisted-contract change, confirm the data earns persistence: it
   is human-approved and belongs in the artifact rather than staying a static
   constant, a runtime-derived value (residual stride comes from fps), or
   diagnostic output. Keep artifacts minimal.
5. Confirm the stored read/validate contract changed: a field added, removed, or
   renamed; dtype/encoding; layout; coordinate meaning; required metadata; or an
   approved per-video persisted variable.
6. Get explicit human approval for the stored-contract change before editing
   `SCHEMA_VERSION` (C10). State it in its own request so the approver sees it.
7. Bump `SCHEMA_VERSION` by one in `tr_schema.py` (the single authority) and
   update `SUPPORTED_ARTIFACT_SCHEMAS` so readers accept the new layout only.
8. Add a one-line `TR_SCHEMA_VERSION_HISTORY.md` entry that answers this checklist:
   version, date, the stored field or contract that changed, and the human
   approver.
9. Update the governance tripwire test's expected version in the same change, so
   the bump stays intentional and reviewed.

## What the schema owns

`SCHEMA_VERSION` tracks the contract needed to read and validate persisted artifacts -- field names, layout, dtype/encoding, coordinate meaning, required metadata, and per-video persisted variables. It does not track the runtime values stored under that contract, nor the algorithms that produced those values; runtime values and method-derived stale outputs are refreshed with `solve`.

Two-step gate, in order. First decide whether the data earns persistence: it needs explicit human approval and belongs in the artifact only when it must survive a run as something other than a static constant, a runtime-derived value, or diagnostic output. After persistence is approved, ask whether the stored-artifact contract changed and let the bump follow. Keep artifacts minimal: persist values that must survive, and route recomputable values to runtime computation.

Schema-owned (justifies a bump when the field/meaning/encoding changes):

| Stored item | Artifact | Why it is schema-owned |
| --- | --- | --- |
| `blended` cx/cy/w/h per interval | torso_box_coords.npz | The solved geometry readers consume; its presence and key names are the read contract |
| dtype/encoding (uint16, pixel-snapped) | torso_box_coords.npz | Readers must decode the bytes correctly; the v10 float32->uint16 change was exactly this |
| coordinate semantics (SOURCE-space cx/cy/w/h) | torso_box_coords.npz | The same bytes would mean different geometry if the space or meaning changed |
| manifest (fingerprint, start/end frame, array_index) | torso_box_coords.npz | Readers reassemble per-interval arrays and map frames from it |
| `schema_version` | torso_box_coords.npz | The validate contract itself |
| `video_identity` | torso_box_coords.npz | Guards artifact-to-video compatibility; the field/meaning is schema-owned (its per-video value is not) |
| `solve_complete` | torso_box_coords.npz | Determines whether the artifact is complete output or a partial resume |
| `i<k>_conf` uint8 arrays | torso_box_coords.npz | Durable raw FWD/BWD agreement transport, including when identical quantized raw paths are omitted |
| optional paired `i<k>_fwd_*` / `i<k>_bwd_*` groups | torso_box_coords.npz | Conditional raw-pass evidence; the all-or-neither presence rule is part of the read contract |
| optional `race_start` block | torso_box_coords.npz | Sole durable owner of detected race-start state; absence is a valid no-pre-race result |
| `interval_score` fields (agreement, velocity/size consistency, motion quality, occlusion fraction, confidence tier, failure reasons, warning flags) | interval-scores JSON | Advisory diagnostics consumed by analyze and reporting; operational target/refine/encode use the torso artifact |

Method-owned (changes computed values; use `solve` when the stored contract is unchanged):

- residual stride / `REFERENCE_FPS` / `resolve_stride` (runtime-computed from fps, never persisted) -- v11.
- walker Viterbi DP, cost weights, stride-termination, heat-map sampling -- v13/v14.
- walker verdict debug-CSV telemetry columns (diagnostic artifact, not loaded as solver state) -- v12.
- pass-local temporary solve state.

Boundary classification (a reviewer must classify each correctly):

| Example change | Verdict |
| --- | --- |
| Change cx/cy/w/h dtype, scale, or decode semantics (after approval) | Schema review; likely bump |
| Add a necessary persisted per-video variable (after approval; confirmed it cannot stay a constant, runtime-derived value, or diagnostic) | Schema review; likely bump |
| Change `video_identity` field semantics | Schema review; likely bump |
| Change Viterbi weights | Use `solve` to refresh stale values; keep schema fixed |
| Change residual stride logic | Use `solve` to refresh stale values; keep schema fixed |
| Add a debug-CSV telemetry column | Use diagnostic history; keep solver schema fixed |
| Change actual coordinate values for a new video | Use existing contract; keep schema fixed |

## Reuse identity rule

The interval reuse key (interval fingerprint) carries exactly three inputs:

1. Seed-pair **frame indices** (start frame and end frame of the interval).
2. Human-authored **SOURCE-frame seed geometry** -- the x, y, w, h box coords of
   each bracketing seed. Redrawing a box at the same frame index produces a
   different key.
3. The current `SCHEMA_VERSION` tag.

Anything not on this allow-list stays out of the key. Examples of values that
must never enter the key:

- bin factor, processed dimensions, or any runtime performance setting
- CLI flags or defaults (auto-bin mode, bin height target)
- script choice, entry-point, or caller identity
- solver mode, pipeline stage, or stage selection flags
- tuning constants, Viterbi weights, cost model parameters
- basename, file size, container extension, or any video-container metadata
- telemetry schema or diagnostic-artifact versioning

**Method-only changes refresh derived values via `solve`.** When a solver
algorithm or observer method changes such that old stored results are stale,
the correct path is to run `solve` for a fresh compute. Method changes do not
widen the fingerprint key and do not require a `SCHEMA_VERSION` bump -- stored
SOURCE coordinate arrays are unchanged.

**Adding a fingerprint input requires justification.** Each proposed new
fingerprint input must name the persisted artifact field or approved schema rule
that justifies it. If the proposed input is a runtime-performance setting, a
computation method detail, or anything not stored in the SOURCE-frame artifact,
it does not belong in the key.

This rule is backed by contract invariants C10 (unified schema) and C13
(minimal artifacts). The behavioral interval-fingerprint tests cover seed
geometry and bin-invariance for current fingerprints.

## Schema bumps and the solved-geometry cache

The solver cache key in `track_runner/interval_fingerprint.py` embeds the
current `SCHEMA_VERSION` as `schema_v<N>`. A stored-format change therefore
creates a fresh cache namespace. Method changes keep the schema fixed and use
`solve` to refresh derived values. Loaders accept only the declared current
artifact layouts; unsupported data is replaced by a fresh solve, never
converted in place.

## Config-key removals (2026-06-13, no schema bump)

**Walker costs, detection thresholds, and crop alphas moved to code constants. Config shape is not under SCHEMA_VERSION.**
Geometry-affecting: no. No schema bump.

`SCHEMA_VERSION` governs on-disk solver artifacts (`interval_scores.json`,
`torso_box_coords.npz`, and the geometry fingerprint key). The YAML
config files are runtime inputs, not stored artifacts versioned by this
system. Removing keys from the config schema does not change any on-disk
artifact layout and does not require a `SCHEMA_VERSION` bump under C10.

**What changed (M1 + M2, 2026-06-13):**

- `walker_costs` section removed from `track_runner/track_runner.config.yaml`
  and the config-to-worker supply chain removed. Six Viterbi cost weights
  (`WEIGHT_DISPLACEMENT`, `WEIGHT_SPEED_DELTA`, `WEIGHT_HEADING_DELTA`,
  `WEIGHT_OVERSPEED`, `WEIGHT_EVIDENCE_NORM`, `SKIP_COST`) are now fixed
  constants in `track_runner/blob_walk/walk_viterbi.py`. Human decision
  2026-06-13: too obscure for per-video user config.
- `detection.confidence_threshold` (0.25) and `detection.nms_threshold`
  (0.45) removed from config; now fixed constants in `tr_detection.py`.
  Human decision 2026-06-13: too obscure for per-video user config.
- `detection.model` (dead key, was `yolov8n`) removed from config; no
  reader existed in production code.
- `processing.crop_post_smooth_strength`, `processing.crop_post_smooth_size_strength`,
  `processing.crop_post_smooth_max_velocity` removed from config; now fixed
  constants in `tr_crop.py` with identical effective values. Human decision
  2026-06-13: too obscure for per-video user config.
- `processing.crop_min_size` removed from config; was already absent (removed
  2026-05-02); only doc references remained.

**Current-config policy:** Configs must carry the current header and required
keys. Removed keys do not supply an alternate behavior; update the config to
the documented current shape before running a mode.

**What was kept:** The crop `smooth` path (`crop_mode == "smooth"`,
`CropController`, and the five smooth-only knobs `crop_smoothing_attack`,
`crop_smoothing_release`, `crop_max_velocity`, `crop_velocity_scale`,
`crop_displacement_alpha`) was investigated and proven reachable. It was not
removed.

## Rolled back: 11, 12, 13, 14 (2026-06-14)

Rolled back because these changes did not alter the stored solver artifact format or any per-video variable. They are not readable artifact layouts.

- v11: changed the residual-sampling computation method (fps-invariant stride). The on-disk layout was unchanged from v10; the stride is runtime-computed from fps and was never persisted. This was a method change; the correct path is `solve`.
- v12: versioned walker debug verdict-CSV telemetry columns. The verdict CSV is a diagnostic artifact; it stores no solver geometry. This was a diagnostic telemetry change; the correct path is the diagnostic history.
- v13: fixed the walker stride-termination overrun. The on-disk layout was unchanged; only computed walk outputs changed on high-fps sources. This was a method change; the correct path is `solve`.
- v14: rewrote the Viterbi cost model and added the seed-only Hermite fallback gate. The on-disk layout was unchanged; only computed values on Stage-4-promoted intervals changed. This was a method change; the correct path is `solve`.

These bumps were avoidable mistakes. The rule above -- "use `solve` for method-derived stale values; use `SCHEMA_VERSION` only for approved persisted artifact contract changes" -- now makes the correct decision visible on first read.

Pre-existing v10 artifacts were the rollback floor until the later v15 format
change. They now require a fresh solve along with rolled-back stamps 11-14.

Human approver: user decision 2026-06-14 (rollback floor v10; full re-solve via `solve`).

## 15 (2026-08-21)

**Per-interval raw-pass confidence and conditional raw-path storage.**
Geometry-affecting: yes.

- `torso_box_coords.npz` always stores uint16 SOURCE-space blended coordinates
  and a uint8 `i<k>_conf` array, decoded as `value / 255`. Stored confidence is
  the authoritative raw FWD/BWD agreement when both raw paths are omitted
  because their uint16 quantizations are identical.
- FWD and BWD coordinate groups remain all-or-neither and are written only when
  a raw-path difference survives uint16 quantization. Omission no longer means
  pre-race or agreement 1.0.
- `torso_box_coords.npz` is the sole durable owner of a detected `race_start`
  block. Absence preserves the established no-pre-race result for ordinary
  target, refine, and encode; race-start targeting requires the block.
- Artifact video identity contains only width, height, and frame count. The
  diagnostics JSON remains advisory and stores neither race-start nor retired
  cyclical provenance.
- `SUPPORTED_ARTIFACT_SCHEMAS` for both diagnostics and torso coordinates is
  `{15}`. v10 and rolled-back v11-v14 artifacts require a fresh `solve`; no
  conversion path exists.
- Human approver: user-approved schema-15 persistence plan, 2026-08-21.

## Historical entries

The checklist above is the current rule. Older entries preserve historical rationale and may describe cache-invalidation practices no longer used for method-only changes.

## 10 (2026-05-03)

**Per-frame coordinate arrays changed dtype: float32 -> uint16 per C12.4.** Geometry-affecting: yes.

- Per-frame torso-box coordinate arrays (`i<k>_blended_cx`, `i<k>_blended_cy`, `i<k>_blended_w`, `i<k>_blended_h` and their FWD/BWD counterparts) are now stored as `uint16` (pixel-snapped integers, 0-65535 range covers up to ~16K frame dimensions) instead of `float32`.
- Rationale: Coordinates are rounded to nearest integer before storage (subpixel precision is fictional after interval fingerprinting rounds to 2 decimals). uint16 saves disk space and matches the resolution of the persisted data; the dtype change affects deserialization and reconstruction so an artifact-schema version bump is required.
- Hard-cut cache policy: `SUPPORTED_ARTIFACT_SCHEMAS["torso_box_coords"]` is now `{10}` only; v8 and v9 are dropped. User must re-solve all intervals. This is acceptable per user feedback: "everything else is gonna have to be recalculated anyway" when schema changes affect geometry.
- Writer [state_io.py](../track_runner/state_io.py) `write_torso_box_coords()` rounds float coords to nearest int, clips to [0, 65535], and casts to uint16 before storage. Defensive clipping guards against future >16K source frames.
- Loader [state_io.py](../track_runner/state_io.py) `load_torso_box_coords()` reconstructs loaded uint16 arrays as Python `int` (not numpy types) so downstream consumers do not silently overflow on arithmetic. Improved error message for rejected schemas directs users to re-solve.
- Tests [test_tr_state_io.py](../tests/storage/test_tr_state_io.py) verify round-trip rounding tolerance (+-1), uint16 on-disk dtype, and v9 rejection with clear error.
- `GEOMETRY_AFFECTING_SCHEMAS` now includes 10 (was {3, 6, 7, 8, 9}).
- `SUPPORTED_ARTIFACT_SCHEMAS["diagnostics"]` adds 10 (stable metadata JSON shape); `SUPPORTED_ARTIFACT_SCHEMAS["torso_box_coords"]` hard-cuts to {10}.

## 9 (2026-05-01)

**Residual-motion geometry changed: adaptive heat map window resolution.** Geometry-affecting: yes.

- The motion-cue background window is now resolved from `(window_seconds, fps)` via `resolve_half_window()` instead of a fixed `half_window=4` frame count. This fixes silent collapse of the heat map at higher frame rates: 120 fps daughter-clip footage previously returned all-zero residuals because the runner's pixels overlapped across all 9 neighbors in a ~4-frame window, polluting the nanmedian background estimate. See [residual_motion.py](../track_runner/residual_motion.py) `DEFAULT_BACKGROUND_WINDOW_SECONDS` (8.0/60.0 ~ 133 ms) and `resolve_half_window()` helper.
- `compute_residual_for_frame()` and `observe_blob_at()` now accept `window_seconds` and `fps` keywords (resolving `half_window` via the helper when `half_window=None`). Production caller [velocity_model.py](../track_runner/velocity_model.py) routes `reader.fps` automatically; call sites retain backward compatibility via optional keyword defaults.
- Cached intervals computed under the prior fixed `half_window=4` frame-count window are invalidated. Hermite recomputation is negligible; no conversion path is provided.
- `GEOMETRY_AFFECTING_SCHEMAS` now includes 9 (was {3, 6, 7, 8}).
- `SUPPORTED_ARTIFACT_SCHEMAS` adds 9 to both artifact sets (diagnostics and torso_box_coords) with stable on-disk layout through v9.
- Design link: plan `~/.claude/plans/federated-knitting-tome.md`, WS-1 Patch 2.

## 7 (2026-04-25)

**Fingerprint tag split into Hermite-only and blob-cached namespaces; geometry-affecting: yes.**

- Two distinct cache-key tags now exist: `HERMITE_GEOMETRY_TAG` (geometry-schema v<N> only, for Stage 3 Hermite-cached intervals) and `BLOB_GEOMETRY_TAG` (geometry-schema v<N> plus the six blob-snap numeric constants, for Stages 1-2 and 4-5). Tuning a blob constant no longer invalidates Hermite cache entries; tuning a geometry-affecting schema constant invalidates both tags. See [interval_fingerprint.py](../track_runner/interval_fingerprint.py) `build_hermite_geometry_tag()` and `build_blob_geometry_tag()`.
- `compute_interval_fingerprint` and `state_io.interval_fingerprint` both gain a `stage` parameter ("hermite" or "blob", default "blob") to route cache lookups to the appropriate tag and (future work) stage-specific cache subdirectories. Back-compat alias `GEOMETRY_TAG = BLOB_GEOMETRY_TAG` preserves existing call sites.
- Rationale: The split enables Stage 3 (Hermite-only refinement) and Stages 4-5 (full solve or refinement with blob observation) to maintain independent caches without ambiguity about which stage produced a cached entry. Hermite recomputation is negligible (~3 ms per 100-frame interval per M0 findings); no conversion path is provided for v6->v7, so existing v6 blob cache entries are invalidated on first run after upgrade. This matches the plan's first-run cold-solve expectation.
- `GEOMETRY_AFFECTING_SCHEMAS` now includes 7 (was {3, 6}).
- `SUPPORTED_ARTIFACT_SCHEMAS` adds 7 to all three artifact sets (diagnostics, geometry_cache, debug_paths) with stable on-disk layout through v7.

## 6 (2026-04-24)

**DoG band-pass added to the production observer; versioning unified under tr_schema.** Geometry-affecting: yes.

- DoG band-pass pre-filter (`dog_filter_blob_scale`) wired into `observe_blob_at` and `compute_heat_map_roi`. Enhances torso-scale blobs and suppresses sub-torso speckle. Default `k=3.0`.
- Versioning refactor for contract C9 compliance: introduces [tr_schema.py](../track_runner/tr_schema.py) as the single schema-version authority. New constants: `SCHEMA_VERSION`, `GEOMETRY_AFFECTING_SCHEMAS = {3, 6}`, `SUPPORTED_ARTIFACT_SCHEMAS`, helpers `latest_geometry_affecting_schema()` and `is_supported_artifact_schema()`.
- Removes the parallel `BLOB_OBSERVER_VERSION = "v1"` constant from [residual_motion.py](../track_runner/residual_motion.py); cache invalidation for observer changes now flows through `GEOMETRY_AFFECTING_SCHEMAS` keyed off the unified `SCHEMA_VERSION`.
- Geometry tag format changed from `blob_snap/v1/...` to `geometry_schema_v<N>/...`. Those old keys are unsupported and a fresh solve replaces them.
- Loaders for diagnostics, geometry_cache.npz, and debug_paths.npz no longer test equality against current; they consult `tr_schema.is_supported_artifact_schema(...)` against the per-artifact compatibility set.
- `GEOMETRY_CACHE_SCHEMA_VERSION` and `DEBUG_PATHS_SCHEMA_VERSION` are now aliases of `tr_schema.SCHEMA_VERSION`, not independent constants.
- `race_start.py` and `scoring.py` aliases now import `tr_schema` directly instead of chaining through `state_io`.
- Drift-prevention test `test_tr_schema_version_single_source.py` tightened to flag any module-level `*(SCHEMA|CACHE|OBSERVER|FINGERPRINT)_VERSION` constant outside `tr_schema.py` that is not a literal re-export, with a whitelist-only exemption mechanism.

## 5 (2026-04-24)

**Unified schema versions per contract C8.** Geometry-affecting: no.

- Collapses three independent version constants (`DIAGNOSTICS_HEADER_VALUE`, `INTERVAL_SCORE_SCHEMA_VERSION`, `PRE_RACE_REFERENCE_SCHEMA_VERSION`) into a single unified `SCHEMA_VERSION` across [state_io.py](../track_runner/state_io.py), [scoring.py](../track_runner/scoring.py), and [race_start.py](../track_runner/race_start.py).
- Adds new fields to `pre_race_reference` dict: `race_start_interval` (list of two frame indices) for the detected race-start seed pair.
- Cache impact: schema bump alone does NOT invalidate solved-geometry entries. The cache key (`GEOMETRY_TAG`) depends only on blob-snap geometry constants; `/schema/N` moved out of the cache key into `SOLVER_FINGERPRINT_TAG` (telemetry only). Old pre-unification keys are unsupported and are replaced by a fresh solve.

## 4 (2026-04-23)

**Final pre-unification lockstep bump.** Geometry-affecting: no.

- Synchronized `DIAGNOSTICS_HEADER_VALUE` (diagnostics JSON header), `INTERVAL_SCORE_SCHEMA_VERSION` (nested interval_score dict), and `PRE_RACE_REFERENCE_SCHEMA_VERSION` (pre_race_reference fields) to version 4 across [state_io.py](../track_runner/state_io.py), [scoring.py](../track_runner/scoring.py), and the newly introduced [race_start.py](../track_runner/race_start.py).
- Introduces `SOLVER_FINGERPRINT_TAG` fragments `/score_schema/4` and `/prerace/4` so cache invalidates when either schema version changes independently (no lockstep enforcement yet).
- Pre-race interval synthesis first implemented: `_solve_pre_race_interval` in [solve_queue.py](../track_runner/solve_queue.py) produces intervals classified with `confidence_tier="pre_race"` in the interval_score.
- Adds `pre_race_reference` optional field to diagnostics JSON (omitted if no pre-race data); contains `race_start_frame`, torso dimensions, scene anchor coordinates, and warnings list.

## 3 (2026-03-24)

**Initial analytical solver schema.** Geometry-affecting: yes.

- Establishes nested `interval_score` dict with v3 fields: `agreement`, `velocity_consistency`, `size_consistency`, `confidence_tier`, `severity`, `failure_reasons`, `warning_flags`. Replaces flat v2 field layout.
- Introduces `INTERVAL_SCORE_SCHEMA_VERSION` constant in [scoring.py](../track_runner/scoring.py) to version the nested structure.
- Writer [state_io.py](../track_runner/state_io.py) emits v3 nested shape only. The former flat shape is no longer readable.
- Introduces `DIAGNOSTICS_HEADER_VALUE` versioning in [state_io.py](../track_runner/state_io.py) (value=3) to distinguish v2 flat layout from v3 nested layout.
- `SOLVER_FINGERPRINT_TAG` first includes `/score_schema/3` fragment so cache invalidates on schema changes.

## 2 and earlier

Pre-history: not logged prospectively. Versions 1 and 2 predate the current
analytical solver contract. They are unsupported; the repository keeps no
readers, converters, or test fixtures for them.
