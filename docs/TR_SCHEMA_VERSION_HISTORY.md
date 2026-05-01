# Track Runner Schema Version History

This document logs the evolution of `SCHEMA_VERSION` across releases. The version controls on-disk format changes to diagnostics files, interval scores, pre-race references, and solver diagnostic headers.

Per contract C9, there is one unified `SCHEMA_VERSION` for the entire track_runner project, defined in [track_runner/tr_schema.py](../track_runner/tr_schema.py). All schema references must use this constant; independent versions are forbidden. This file records the versioning history specifically, separate from the changelog which records code changes. Even when on-disk format is byte-identical, a new version number is issued to avoid mixed numbers across outputs; this prevents silent schema mismatches that propagate through cached and derived artifacts.

## Schema bumps and the solved-geometry cache

Schema bumps are metadata-only by default: they do NOT invalidate solved-geometry cache entries. The solver cache key lives in `track_runner/interval_fingerprint.py` as `GEOMETRY_TAG`, which embeds the highest member of `tr_schema.GEOMETRY_AFFECTING_SCHEMAS` <= `SCHEMA_VERSION` as a `geometry_schema_v<N>` token. A separate informational tag, `SOLVER_FINGERPRINT_TAG`, includes `/schema/<SCHEMA_VERSION>` and is used for diagnostics headers only.

To bump observer/solver behavior in a way that invalidates geometry caches, increment `SCHEMA_VERSION` and add the new version to `GEOMETRY_AFFECTING_SCHEMAS`. Loaders consult `tr_schema.SUPPORTED_ARTIFACT_SCHEMAS` to decide which on-disk versions remain readable, so a schema bump does not automatically reject older artifacts.

When an old cache carries a schema-tagged fingerprint (e.g. `/schema/5`), a pre-unification tail (`/score_schema/4/prerace/4`), or the legacy `blob_snap/v1/...` form, `migrate_legacy_fingerprints` rewrites the key into the unified `geometry_schema_v<N>` namespace at load time. Each entry below marks whether it was geometry-affecting; only geometry-affecting bumps are cache invalidators.

## 9 (2026-05-01)

**Residual-motion geometry changed: adaptive heat map window resolution.** Geometry-affecting: yes.

- The motion-cue background window is now resolved from `(window_seconds, fps)` via `resolve_half_window()` instead of a fixed `half_window=4` frame count. This fixes silent collapse of the heat map at higher frame rates: 120 fps daughter-clip footage previously returned all-zero residuals because the runner's pixels overlapped across all 9 neighbors in a ~4-frame window, polluting the nanmedian background estimate. See [track_runner/residual_motion.py](../track_runner/residual_motion.py) `DEFAULT_BACKGROUND_WINDOW_SECONDS` (8.0/60.0 ~ 133 ms) and `resolve_half_window()` helper.
- `compute_residual_for_frame()` and `observe_blob_at()` now accept `window_seconds` and `fps` keywords (resolving `half_window` via the helper when `half_window=None`). Production caller [track_runner/velocity_model.py](../track_runner/velocity_model.py) routes `reader.fps` automatically; call sites retain backward compatibility via optional keyword defaults.
- Cached intervals computed under the prior fixed `half_window=4` frame-count window are invalidated. Hermite recomputation is negligible; no legacy migration path is provided.
- `GEOMETRY_AFFECTING_SCHEMAS` now includes 9 (was {3, 6, 7, 8}).
- `SUPPORTED_ARTIFACT_SCHEMAS` adds 9 to both artifact sets (diagnostics and torso_box_coords) with stable on-disk layout through v9.
- Design link: plan `~/.claude/plans/federated-knitting-tome.md`, WS-1 Patch 2.

## 7 (2026-04-25)

**Fingerprint tag split into Hermite-only and blob-cached namespaces; geometry-affecting: yes.**

- Two distinct cache-key tags now exist: `HERMITE_GEOMETRY_TAG` (geometry-schema v<N> only, for Stage 3 Hermite-cached intervals) and `BLOB_GEOMETRY_TAG` (geometry-schema v<N> plus the six blob-snap numeric constants, for Stages 1-2 and 4-5). Tuning a blob constant no longer invalidates Hermite cache entries; tuning a geometry-affecting schema constant invalidates both tags. See [track_runner/interval_fingerprint.py](../track_runner/interval_fingerprint.py) `build_hermite_geometry_tag()` and `build_blob_geometry_tag()`.
- `compute_interval_fingerprint` and `state_io.interval_fingerprint` both gain a `stage` parameter ("hermite" or "blob", default "blob") to route cache lookups to the appropriate tag and (future work) stage-specific cache subdirectories. Back-compat alias `GEOMETRY_TAG = BLOB_GEOMETRY_TAG` preserves existing call sites.
- Rationale: The split enables Stage 3 (Hermite-only refinement) and Stages 4-5 (full solve or refinement with blob observation) to maintain independent caches without ambiguity about which stage produced a cached entry. Hermite recomputation is negligible (~3 ms per 100-frame interval per M0 findings); no legacy migration path is provided for v6->v7, so existing v6 blob cache entries are invalidated on first run after upgrade. This matches the plan's first-run cold-solve expectation.
- `GEOMETRY_AFFECTING_SCHEMAS` now includes 7 (was {3, 6}).
- `SUPPORTED_ARTIFACT_SCHEMAS` adds 7 to all three artifact sets (diagnostics, geometry_cache, debug_paths) with stable on-disk layout through v7.

## 6 (2026-04-24)

**DoG band-pass added to the production observer; versioning unified under tr_schema.** Geometry-affecting: yes.

- DoG band-pass pre-filter (`dog_filter_blob_scale`) wired into `observe_blob_at` and `compute_heat_map_roi`. Enhances torso-scale blobs and suppresses sub-torso speckle. Default `k=3.0`.
- Versioning refactor for contract C9 compliance: introduces [track_runner/tr_schema.py](../track_runner/tr_schema.py) as the single schema-version authority. New constants: `SCHEMA_VERSION`, `GEOMETRY_AFFECTING_SCHEMAS = {3, 6}`, `SUPPORTED_ARTIFACT_SCHEMAS`, helpers `latest_geometry_affecting_schema()` and `is_supported_artifact_schema()`.
- Removes the parallel `BLOB_OBSERVER_VERSION = "v1"` constant from [track_runner/residual_motion.py](../track_runner/residual_motion.py); cache invalidation for observer changes now flows through `GEOMETRY_AFFECTING_SCHEMAS` keyed off the unified `SCHEMA_VERSION`.
- Geometry tag format changes from `blob_snap/v1/...` to `geometry_schema_v<N>/...`. `migrate_legacy_fingerprints` rewrites legacy `blob_snap/v1` keys to `geometry_schema_v3` (the schema version current when v1 ruled); migrated v3-era keys correctly fail the v6 match and are re-solved.
- Loaders for diagnostics, geometry_cache.npz, and debug_paths.npz no longer test equality against current; they consult `tr_schema.is_supported_artifact_schema(...)` against the per-artifact compatibility set.
- `GEOMETRY_CACHE_SCHEMA_VERSION` and `DEBUG_PATHS_SCHEMA_VERSION` are now aliases of `tr_schema.SCHEMA_VERSION`, not independent constants.
- `race_start.py` and `scoring.py` aliases now import `tr_schema` directly instead of chaining through `state_io`.
- Drift-prevention test [tests/test_tr_schema_version_single_source.py](../tests/test_tr_schema_version_single_source.py) tightened to flag any module-level `*(SCHEMA|CACHE|OBSERVER|FINGERPRINT)_VERSION` constant outside `tr_schema.py` that is not a literal re-export, with a whitelist-only exemption mechanism.

## 5 (2026-04-24)

**Unified schema versions per contract C8.** Geometry-affecting: no.

- Collapses three independent version constants (`DIAGNOSTICS_HEADER_VALUE`, `INTERVAL_SCORE_SCHEMA_VERSION`, `PRE_RACE_REFERENCE_SCHEMA_VERSION`) into a single unified `SCHEMA_VERSION` across [track_runner/state_io.py](../track_runner/state_io.py), [track_runner/scoring.py](../track_runner/scoring.py), and [track_runner/race_start.py](../track_runner/race_start.py).
- Adds new fields to `pre_race_reference` dict: `race_start_interval` (list of two frame indices) for the detected race-start seed pair.
- Cache impact (post-migration split): schema bump alone does NOT invalidate solved-geometry entries. The cache key (`GEOMETRY_TAG`) depends only on blob-snap geometry constants; `/schema/N` moved out of the cache key into `SOLVER_FINGERPRINT_TAG` (telemetry only). `migrate_legacy_fingerprints` rewrites pre-unification tails (`/score_schema/4/prerace/4`) and schema-tagged tails (`/schema/5`) to the current `GEOMETRY_TAG` at load time.

## 4 (2026-04-23)

**Final pre-unification lockstep bump.** Geometry-affecting: no.

- Synchronized `DIAGNOSTICS_HEADER_VALUE` (diagnostics JSON header), `INTERVAL_SCORE_SCHEMA_VERSION` (nested interval_score dict), and `PRE_RACE_REFERENCE_SCHEMA_VERSION` (pre_race_reference fields) to version 4 across [track_runner/state_io.py](../track_runner/state_io.py), [track_runner/scoring.py](../track_runner/scoring.py), and the newly introduced [track_runner/race_start.py](../track_runner/race_start.py).
- Introduces `SOLVER_FINGERPRINT_TAG` fragments `/score_schema/4` and `/prerace/4` so cache invalidates when either schema version changes independently (no lockstep enforcement yet).
- Pre-race interval synthesis first implemented: `_solve_pre_race_interval` in [track_runner/solve_queue.py](../track_runner/solve_queue.py) produces intervals classified with `confidence_tier="pre_race"` in the interval_score.
- Adds `pre_race_reference` optional field to diagnostics JSON (omitted if no pre-race data); contains `race_start_frame`, torso dimensions, scene anchor coordinates, and warnings list.

## 3 (2026-03-24)

**Initial analytical solver schema.** Geometry-affecting: yes.

- Establishes nested `interval_score` dict with v3 fields: `agreement`, `velocity_consistency`, `size_consistency`, `confidence_tier`, `severity`, `failure_reasons`, `warning_flags`. Replaces flat v2 field layout.
- Introduces `INTERVAL_SCORE_SCHEMA_VERSION` constant in [track_runner/scoring.py](../track_runner/scoring.py) to version the nested structure.
- Writer [track_runner/state_io.py](../track_runner/state_io.py) emits v3 nested shape only; on load migrates legacy v2 flat shape to v3 in memory.
- Introduces `DIAGNOSTICS_HEADER_VALUE` versioning in [track_runner/state_io.py](../track_runner/state_io.py) (value=3) to distinguish v2 flat layout from v3 nested layout.
- `SOLVER_FINGERPRINT_TAG` first includes `/score_schema/3` fragment so cache invalidates on schema changes.

## 2 and earlier

Pre-history: not logged prospectively. Version 1 predates the analytical solver; version 2 is the legacy optical-flow solver schema with flat per-interval fields (`confidence`, `agreement_score`, `competitor_margin`, etc.). Schemas 1 and 2 are obsolete; they are not reconstructed here to avoid guessing. If git history is needed for legacy schema details, consult `git log --all` entries prior to 2026-03-24 or review the `tests/test_seed_schema_v3.py` migration helpers which capture some v2/v3 shape differences for testing purposes.
