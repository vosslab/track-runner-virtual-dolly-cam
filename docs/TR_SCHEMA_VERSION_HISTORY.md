# Track Runner Schema Version History

This document logs the evolution of `SCHEMA_VERSION` across releases. The version controls on-disk format changes to diagnostics files, interval scores, pre-race references, and solver diagnostic headers.

Per contract C9, there is one unified `SCHEMA_VERSION` for the entire track_runner project, defined in [tr_schema.py](../track_runner/tr_schema.py). All schema references must use this constant; independent versions are forbidden. This file records the versioning history specifically, separate from the changelog which records code changes. Even when on-disk format is byte-identical, a new version number is issued to avoid mixed numbers across outputs; this prevents silent schema mismatches that propagate through cached and derived artifacts.

## Schema bumps and the solved-geometry cache

Schema bumps are metadata-only by default: they do NOT invalidate solved-geometry cache entries. The solver cache key lives in `track_runner/interval_fingerprint.py` as `GEOMETRY_TAG`, which embeds the highest member of `tr_schema.GEOMETRY_AFFECTING_SCHEMAS` <= `SCHEMA_VERSION` as a `geometry_schema_v<N>` token. A separate informational tag, `SOLVER_FINGERPRINT_TAG`, includes `/schema/<SCHEMA_VERSION>` and is used for diagnostics headers only.

To bump observer/solver behavior in a way that invalidates geometry caches, increment `SCHEMA_VERSION` and add the new version to `GEOMETRY_AFFECTING_SCHEMAS`. Loaders consult `tr_schema.SUPPORTED_ARTIFACT_SCHEMAS` to decide which on-disk versions remain readable, so a schema bump does not automatically reject older artifacts.

When an old cache carries a schema-tagged fingerprint (e.g. `/schema/5`), a pre-unification tail (`/score_schema/4/prerace/4`), or the legacy `blob_snap/v1/...` form, `migrate_legacy_fingerprints` rewrites the key into the unified `geometry_schema_v<N>` namespace at load time. Each entry below marks whether it was geometry-affecting; only geometry-affecting bumps are cache invalidators.

## 14 (2026-06-12)

**Viterbi cost-model rewrite (WP-COST-1) and seed-only Hermite fallback (P10/WP-P10-1).**
Geometry-affecting: yes (Stage-4-promoted intervals only; byte-identical for pure-Hermite paths).

This bumps the unified `tr_schema.SCHEMA_VERSION` from 13 to 14 and adds 14
to `GEOMETRY_AFFECTING_SCHEMAS`. Per contract C10, one unified bump covers both
geometry-affecting changes. The on-disk layout of both the `diagnostics` and
`torso_box_coords` artifacts is unchanged from v13, so v10-v14 files all remain
readable.

**WP-COST-1 -- Viterbi cost-model rewrite.**
Replaces the first-order `WEIGHT_DISPLACEMENT * disp` cost (which penalized
motion itself, causing the walker to prefer stationary distractors over a
moving runner) with pairwise velocity-delta scoring: two new terms
`WEIGHT_SPEED_DELTA` and `WEIGHT_HEADING_DELTA` penalize acceleration and
heading changes between consecutive real observations. The dead module constants
`WEIGHT_MAG_VAR` and `WEIGHT_ANGLE_VAR` (specified but never wired into the DP
per audit P2) are removed. Evidence is normalized per-frame against the strongest
candidate in that frame (bounded by `WEIGHT_EVIDENCE_NORM = 0.5`) so it acts as
a tie-breaker rather than a dominator. The tight hard displacement prune is
replaced by a soft linear cost plus a quadratic overspeed penalty above the
physical envelope, with a single generous hard prune at `ABSOLUTE_MAX_JUMP_W =
1.5` torso-widths/frame. Skip is charged once per skipped frame and geometry
bridges across gaps via gap-normalized velocity.
Cost weights now live in the `walker_costs` section of
`track_runner/track_runner.config.yaml` (resolving the P3 doc-code conflict
in favor of `docs/TRACK_RUNNER_DESIGN.md`). Defaults: `WEIGHT_DISPLACEMENT =
0.25` (lowered from the plan's 1.0 per manager resolve -- evidence-forward),
`WEIGHT_SPEED_DELTA = 1.0`, `WEIGHT_HEADING_DELTA = 0.5`, `WEIGHT_OVERSPEED =
4.0`, `WEIGHT_EVIDENCE_NORM = 0.5`, `SKIP_COST = 2.0`. The variance-to-
pairwise-delta design choice is intentional: pairwise deltas penalize
acceleration, are additive, and satisfy optimal-substructure; variance over a
window mean is not DP-compatible without global rollback. The legacy constant
`BOOTSTRAP_UNCERTAINTY_W` is no longer read by the DP (the DP reads no bootstrap
slack); rename to `SEED_SEARCH_SLACK_W` is recorded as a follow-up, not done
here. Weights are threaded to Stage-4 workers through the existing frozen
`WorkerContext.walker_costs` field and `make_pool` initargs, resolving a wiring
gap where `interval_solver._dispatch_blob_pass` omitted `walker_costs` from its
`make_pool` call (spec-review F1 fix).

**WP-P10-1 -- seed-only Hermite fallback (P10 fix).**
The Stage-4 Hermite fallback gate previously fired only when
`accepted_count == 0`. A pass with exactly one accepted frame at the bootstrap
(seed) position and all remaining frames `soft_miss_no_blob` was not gated,
producing a path frozen at the seed for all non-seed frames -- strictly worse
than Hermite. The `WalkCoverage` dataclass (`accepted_count`,
`post_seed_accepted`) and helper `count_post_seed_accepts` make the distinction
explicit; the gate now reads `coverage.post_seed_accepted == 0` (the seed-only
fallback). Terminology: "seed" replaces "bootstrap" in all new code and docs per
user decision 2026-06-12; the legacy `BOOTSTRAP_UNCERTAINTY_W` identifier
appears only in existing code pending the follow-up rename.

Why geometry-affecting: both changes alter walk outputs (accepted positions,
statuses, path) on Stage-4-promoted intervals where blobs are present. On
pure-Hermite paths (Stage 3, `blob_pass=False`) no walker code runs and output
is byte-identical to v13. A single `SCHEMA_VERSION` line cannot be
geometry-affecting for some dispatch paths and not others per the unified
contract; v14 enters `GEOMETRY_AFFECTING_SCHEMAS` to prevent silently mixing
pre-fix and post-fix geometry in cached artifacts.

## 13 (2026-06-10)

**Walker P12 stride-termination overrun fix.** Geometry-affecting: yes
(stride > 1 sources only; byte-identical at stride 1).

This bumps the unified `tr_schema.SCHEMA_VERSION` from 12 to 13 and adds 13
to `GEOMETRY_AFFECTING_SCHEMAS`. The on-disk layout of both the `diagnostics`
and `torso_box_coords` artifacts is unchanged from v12, so v10-v13 files all
remain readable.

Audit finding P12 (see
[blob_walk_v2_implementation_audit.md](active_plans/audits/blob_walk_v2_implementation_audit.md))
and its validation
([blob_walk_v2_check0_stride_overrun.md](active_plans/workstreams/blob_walk_v2_check0_stride_overrun.md))
proved the walker termination test `frame_f == neighbor_seed_frame` in
[walk_walker.py](../track_runner/blob_walk/walk_walker.py) `_run_windowed_steps`
misses when stride > 1 and the interval span is not divisible by stride. The
stepped frame skips over the seed instead of landing on it, so the equality
check never fires and the walk overruns into the adjacent interval (observed:
119.94 fps Lyra-Wheeling interval #164, frames 16588-16591, FWD overran to
16592+, BWD to 16587). This violated contract C5/C6 interval independence in
spirit: a walk observed frames belonging to neighbor intervals. The fix
replaces equality with the directional crossing test
`sign * (frame_f - neighbor_seed_frame) >= 0` and clamps `frame_f` to the
neighbor seed, so the walk terminates at the seed with
`stop_reason = "hit_neighbor_seed"`.

Why geometry-affecting: on stride > 1 (>= ~90 fps) sources whose span is not
divisible by stride, walk outputs change (per-frame statuses, accepted
positions, paths). At stride 1 (30/60 fps) the crossing test fires exactly
when equality fired and the clamp is a no-op, so output is byte-identical.

Honest tradeoff: a single unified `SCHEMA_VERSION` line cannot be
geometry-affecting for some sources and not others. Marking v13
geometry-affecting invalidates geometry-derived caches on 30/60 fps videos
too, where output is unchanged -- a re-solve cost paid for nothing on those
videos. The alternative (no bump, or metadata-only) risks silently mixing
pre-fix overrun geometry with post-fix geometry on 120 fps videos, exactly the
mismatch contract C10 exists to prevent. The bump wins.

## 12 (2026-06-10)

**Walker CSV debug-log P15 telemetry-truthfulness fix.** Geometry-affecting: no.

This bumps the unified `tr_schema.SCHEMA_VERSION` from 11 to 12. It is a
metadata-only bump: the walker verdict CSV is a diagnostic artifact, no solved
geometry changed, and `GEOMETRY_AFFECTING_SCHEMAS` is unchanged (12 is
intentionally absent), so solved-geometry cache entries stay valid across the
v11 -> v12 line. `walk_debug_log.SCHEMA_VERSION` reads the unified constant, so
its exported value advances 11 -> 12 with it; that value is never written into
the CSV (the CSV header is the `HEADER` column-name tuple).

Audit finding P15 (see
[blob_walk_v2_implementation_audit.md](active_plans/audits/blob_walk_v2_implementation_audit.md))
proved the `path_cost` column lied about its own meaning: its header doc
claimed "Viterbi DP cost contribution at this frame," but the writer stamped
the SAME whole-window Viterbi total on every emitted row. This fix is telemetry
only -- no change to selected path, statuses, positions, accepted counts, or
the Hermite fallback. Decision equality was verified field-wise against the
`e2e_blob_walk_baseline` golden on the two diagnosed stall intervals
(Conant 1080-1111 FWD, Jason 564-583 FWD) plus steady-state intervals; only the
new telemetry columns differ. Three coordinated changes to
[walk_debug_log.py](../track_runner/blob_walk/walk_debug_log.py) HEADER
(now 45 columns, up from 43):

- `path_cost` documentation corrected to its true meaning: the WHOLE-WINDOW
  Viterbi total for the window that produced this frame's decision. Column
  values and behavior are unchanged; only the documentation is fixed.
- NEW `path_step_cost` (float): the per-frame Viterbi cost contribution of the
  selected node -- its local node cost (evidence bonus for a real blob, else
  SKIP_COST) plus the transition cost into it from the previous node. This is
  the value `path_cost` falsely claimed to be. Summing `path_step_cost` across
  one window equals that window's `path_cost`. Blank for bootstrap and terminal
  marker rows. Computed by the new `walk_viterbi.compute_path_step_costs`
  helper, which reads the already-selected path only and does not run, alter, or
  re-bias the Viterbi DP (no change to backpointers, argmin, or costs).
- NEW `window_head_frame` (int): the source frame index of the window head
  (newest frame in the rolling buffer) at the moment this frame's window
  decision was finalized, per spec section 7 of
  [windowed_path_selection_amendment.md](archive/windowed_path_selection_amendment.md).
  Blank for bootstrap and terminal marker rows.

Pre-v14 CSVs (43 columns) remain readable by tools that iterate `HEADER`
directly; the two new columns are simply absent and default to blank on read.
The CSV column-meaning history advances to v14 (v12 added the provisional
columns, v13 the window-selection redesign, v14 this P15 fix); that
column-meaning label sequence is independent of and faster than the unified
integer, which is now 12.

## Walker CSV debug-log constant folded under tr_schema (2026-06-08)

**Verdict-CSV `walk_debug_log.SCHEMA_VERSION` now reads `tr_schema.SCHEMA_VERSION` (C10).** Geometry-affecting: no.

The relocated [walk_debug_log.py](../track_runner/blob_walk/walk_debug_log.py) (moved into
`track_runner/blob_walk/` by WP-1) previously carried its own `SCHEMA_VERSION = 13`. Once it sits
inside `track_runner/` beside [tr_schema.py](../track_runner/tr_schema.py), two schema constants
violate contract C10 (one unified `SCHEMA_VERSION`). WP-4 folds it: the module now defines
`SCHEMA_VERSION = tr_schema.SCHEMA_VERSION` (currently 11).

- Header-stamp value change: the exported constant value changes from 13 to 11. This is metadata
  only. `walk_debug_log.SCHEMA_VERSION` is never written into the verdict CSV; the CSV header is the
  `HEADER` column-name tuple (43 columns), which is unchanged. No CSV cell, no row count, and no
  column changes. The `e2e_blob_walk_baseline` golden compares CSV columns and cell values, so the
  fold does not alter the baseline.
- The torso_box_coords writer in [state_io.py](../track_runner/state_io.py) is untouched (already
  unified and additive per WS1-C); `GEOMETRY_AFFECTING_SCHEMAS` is unchanged.
- The CSV column-meaning history (v12 below, v13 below) is retained verbatim for readers parsing
  older verdict CSVs; only the running stamp source changed.

## Walker-local CSV v13 (2026-05-28, superseded by unified schema)

**Walker CSV debug-log schema: window-level path-selection redesign.** Geometry-affecting: no.

This version governed `track_runner/blob_walk/walk_debug_log.py` SCHEMA_VERSION before
the module was relocated into `track_runner/` and folded under the unified `tr_schema.SCHEMA_VERSION`
constant (see the "Walker CSV debug-log constant folded under tr_schema" entry above). It is a
walker-local CSV column-meaning label, not a unified schema integer. It is listed here per contract
C10 (one unified schema history). The `track_runner/tr_schema.py` SCHEMA_VERSION was 11 at this time.

Column changes from v12 (43 columns total, down from 42 in v12 + 2 new - 1 deleted = 43):

- DELETED: `torso_w_drift_frac` (unused placeholder per scout audit in
  [window_level_touchpoint_map.md](archive/window_level_touchpoint_map.md)).
- NEW: `path_cost` (float) -- Viterbi DP cost contribution at the frame when this
  decision was finalized. Blank for bootstrap and terminal marker rows.
- NEW: `candidates_in_window` (int) -- count of non-empty corridor_blob candidate
  lists in the 9-frame window when this frame's decision was finalized.

Status enum changes:

- ADDED: `interpolated` -- frame has candidates-present path gap; position is linearly
  interpolated between bracketing accepted frames.
- ADDED: `extrapolated` -- past last accepted in window but within EXTRAP_MAX=2 frames.
- ADDED: `soft_miss_no_path` -- candidates existed but Viterbi assigned skip at this
  frame because no plausible path through them.
- NO LONGER EMITTED: `rejected_motion_gate` -- per-frame gate removed; replaced by
  9-frame Viterbi DP. Legacy value remains parseable via `ALL_KNOWN_STATUS`.

Semantic changes (columns retained but meaning changed):

- `status`: value set updated as described above.
- `pred_cx` / `pred_cy`: now the last-accepted position used for ROI anchoring
  (no velocity-projection model in the windowed walker).
- `reject_reason`: always blank in new walker (no per-step gate).
- `roi_anchor_source`: always `"accepted"` in new walker (no provisional state).
- `provisional_cx_px` / `provisional_cy_px`: always blank in new walker; retained
  for backward CSV-read compat.

Rationale: the per-frame single-winner model (v12) was causing leg/torso blob
oscillation that destroyed lock even when the torso blob was present every frame.
Per-frame max(integrated_mag) selection is replaced by 9-frame rolling buffer +
Viterbi DP path selection over `trace.corridor_blobs` candidate lists. The design
is described in
[windowed_path_selection_amendment.md](archive/windowed_path_selection_amendment.md).

## 12 (2026-05-28)

**Walker CSV debug-log schema: provisional-observation anti-freeze columns added.** Geometry-affecting: no.

This version governs `track_runner/blob_walk/walk_debug_log.py` SCHEMA_VERSION (the blob-walker CSV format),
not the track_runner on-disk solver artifact schema. It is listed here per contract C10 (one unified
schema history). The `track_runner/tr_schema.py` SCHEMA_VERSION remains at 11; the walker CSV schema
advances independently because the walker lives in `tools/blob_walk_v2/` and does not write
track_runner solver artifacts.

- Three new columns added to `walk_debug_log.HEADER` (now 42 columns, up from 39 in v11):
  - `roi_anchor_source`: string recording which recent position anchored the ROI and
    acceptance-box for this step. Values: `"accepted"` (anchored to last accepted position),
    `"provisional"` (anchored to the most recent gate-rejected candidate position),
    `"extrapolated"` (anchored to velocity-projected prediction with no provisional available).
  - `provisional_cx_px`: float, the provisional candidate x-coordinate (pixels) used when
    `roi_anchor_source == "provisional"`; blank otherwise.
  - `provisional_cy_px`: float, the provisional candidate y-coordinate (pixels) used when
    `roi_anchor_source == "provisional"`; blank otherwise.
- Mechanism: after a motion-gate rejection where a visible candidate was found, the walker
  records the candidate's position as `last_provisional_cx/cy/frame`. On the following step,
  if `last_provisional_frame > last_accepted_frame`, the provisional position anchors the
  ROI/acceptance-box instead of the stale last-accepted position. This breaks the H4
  velocity-freeze cascade: 28+ consecutive `soft_miss_no_blob` frames after one `rejected_gate`
  (Conant 1389-1420 BWD) are caused by the acceptance-box drifting away from the runner;
  the provisional anchor keeps the acceptance-box near the visible runner.
- Velocity history and accepted-state are unchanged: `last_provisional_*` never feeds into
  `accept_frames_and_scenes`, `vx_recent_scene`, or `vy_recent_scene`.
- Pre-v12 CSVs with 39 columns remain readable by tools that iterate `HEADER` directly; the
  three new columns are simply absent and default to blank on read.

## 11 (2026-05-03)

**M2 fps-invariant stride model replaces adaptive-count window.** Geometry-affecting: yes.

- `DEFAULT_BACKGROUND_WINDOW_SECONDS` and `resolve_half_window()` removed from
  [residual_motion.py](../track_runner/residual_motion.py). Replaced by
  `REFERENCE_FPS = 60` and `resolve_stride(fps)` which computes
  `stride = max(1, round(fps / REFERENCE_FPS))`.
- Neighbor offsets in `compute_residual_for_frame` and `_compute_residual_with_extras` are
  now `k * stride` for `k in range(-DEFAULT_HALF_WINDOW, DEFAULT_HALF_WINDOW + 1) if k != 0`.
  At 60 fps stride=1, offsets `[-4, -3, -2, -1, 1, 2, 3, 4]` -- byte-identical to the
  legacy behavior. At 119.94 fps stride=2, offsets `[-8, -6, -4, -2, 2, 4, 6, 8]` --
  same ~133 ms time span, half the I/O vs the 17-sample window the old model produced.
  At 240 fps stride=4, quarter the I/O.
- `precompute_interval_residuals` in [residual_pre_pass.py](../track_runner/residual_pre_pass.py)
  gains a `stride` parameter; padding is `half_window * stride` so the BGR cache covers
  the wider time-span window at high fps.
- `observe_blob_at` signature updated: `window_seconds` parameter removed, `stride`
  parameter added (default None, resolved from `reader.fps` automatically).
- `compute_heat_map_roi` in [residual_heat_map.py](../track_runner/residual_heat_map.py)
  migrated to stride model; `window_seconds` parameter removed.
- `tools/diagnose_residual_motion.py` argparse migrated: `--window-seconds` removed,
  `--stride` added (default: resolved from video fps).
- `GEOMETRY_AFFECTING_SCHEMAS` now includes 11 (was {3, 6, 7, 8, 9, 10}).
- `SUPPORTED_ARTIFACT_SCHEMAS["torso_box_coords"]` is `{10, 11}`. On-disk layout is
  unchanged from v10; v10 files remain readable. Only the residual-sampling semantics
  changed, so cache invalidation happens naturally via the geometry fingerprint.
- `SUPPORTED_ARTIFACT_SCHEMAS["diagnostics"]` adds 11 (stable metadata JSON shape).
- Plan: `~/.claude/plans/memoized-percolating-moler.md` M2.

## 10 (2026-05-03)

**Per-frame coordinate arrays changed dtype: float32 -> uint16 per C12.4.** Geometry-affecting: yes.

- Per-frame torso-box coordinate arrays (`i<k>_blended_cx`, `i<k>_blended_cy`, `i<k>_blended_w`, `i<k>_blended_h` and their FWD/BWD counterparts) are now stored as `uint16` (pixel-snapped integers, 0-65535 range covers up to ~16K frame dimensions) instead of `float32`.
- Rationale: Coordinates are rounded to nearest integer before storage (subpixel precision is fictional after interval fingerprinting rounds to 2 decimals). uint16 saves disk space and matches the resolution of the persisted data; the dtype change affects deserialization and reconstruction so an artifact-schema version bump is required.
- Hard-cut cache policy: `SUPPORTED_ARTIFACT_SCHEMAS["torso_box_coords"]` is now `{10}` only; v8 and v9 are dropped. User must re-solve all intervals. This is acceptable per user feedback: "everything else is gonna have to be recalculated anyway" when schema changes affect geometry.
- Writer [state_io.py](../track_runner/state_io.py) `write_torso_box_coords()` rounds float coords to nearest int, clips to [0, 65535], and casts to uint16 before storage. Defensive clipping guards against future >16K source frames.
- Loader [state_io.py](../track_runner/state_io.py) `load_torso_box_coords()` reconstructs loaded uint16 arrays as Python `int` (not numpy types) so downstream consumers do not silently overflow on arithmetic. Improved error message for rejected schemas directs users to re-solve.
- Tests [test_tr_state_io.py](../tests/test_tr_state_io.py) verify round-trip rounding tolerance (+-1), uint16 on-disk dtype, and v9 rejection with clear error.
- `GEOMETRY_AFFECTING_SCHEMAS` now includes 10 (was {3, 6, 7, 8, 9}).
- `SUPPORTED_ARTIFACT_SCHEMAS["diagnostics"]` adds 10 (stable metadata JSON shape); `SUPPORTED_ARTIFACT_SCHEMAS["torso_box_coords"]` hard-cuts to {10}.

## 9 (2026-05-01)

**Residual-motion geometry changed: adaptive heat map window resolution.** Geometry-affecting: yes.

- The motion-cue background window is now resolved from `(window_seconds, fps)` via `resolve_half_window()` instead of a fixed `half_window=4` frame count. This fixes silent collapse of the heat map at higher frame rates: 120 fps daughter-clip footage previously returned all-zero residuals because the runner's pixels overlapped across all 9 neighbors in a ~4-frame window, polluting the nanmedian background estimate. See [residual_motion.py](../track_runner/residual_motion.py) `DEFAULT_BACKGROUND_WINDOW_SECONDS` (8.0/60.0 ~ 133 ms) and `resolve_half_window()` helper.
- `compute_residual_for_frame()` and `observe_blob_at()` now accept `window_seconds` and `fps` keywords (resolving `half_window` via the helper when `half_window=None`). Production caller [velocity_model.py](../track_runner/velocity_model.py) routes `reader.fps` automatically; call sites retain backward compatibility via optional keyword defaults.
- Cached intervals computed under the prior fixed `half_window=4` frame-count window are invalidated. Hermite recomputation is negligible; no legacy migration path is provided.
- `GEOMETRY_AFFECTING_SCHEMAS` now includes 9 (was {3, 6, 7, 8}).
- `SUPPORTED_ARTIFACT_SCHEMAS` adds 9 to both artifact sets (diagnostics and torso_box_coords) with stable on-disk layout through v9.
- Design link: plan `~/.claude/plans/federated-knitting-tome.md`, WS-1 Patch 2.

## 7 (2026-04-25)

**Fingerprint tag split into Hermite-only and blob-cached namespaces; geometry-affecting: yes.**

- Two distinct cache-key tags now exist: `HERMITE_GEOMETRY_TAG` (geometry-schema v<N> only, for Stage 3 Hermite-cached intervals) and `BLOB_GEOMETRY_TAG` (geometry-schema v<N> plus the six blob-snap numeric constants, for Stages 1-2 and 4-5). Tuning a blob constant no longer invalidates Hermite cache entries; tuning a geometry-affecting schema constant invalidates both tags. See [interval_fingerprint.py](../track_runner/interval_fingerprint.py) `build_hermite_geometry_tag()` and `build_blob_geometry_tag()`.
- `compute_interval_fingerprint` and `state_io.interval_fingerprint` both gain a `stage` parameter ("hermite" or "blob", default "blob") to route cache lookups to the appropriate tag and (future work) stage-specific cache subdirectories. Back-compat alias `GEOMETRY_TAG = BLOB_GEOMETRY_TAG` preserves existing call sites.
- Rationale: The split enables Stage 3 (Hermite-only refinement) and Stages 4-5 (full solve or refinement with blob observation) to maintain independent caches without ambiguity about which stage produced a cached entry. Hermite recomputation is negligible (~3 ms per 100-frame interval per M0 findings); no legacy migration path is provided for v6->v7, so existing v6 blob cache entries are invalidated on first run after upgrade. This matches the plan's first-run cold-solve expectation.
- `GEOMETRY_AFFECTING_SCHEMAS` now includes 7 (was {3, 6}).
- `SUPPORTED_ARTIFACT_SCHEMAS` adds 7 to all three artifact sets (diagnostics, geometry_cache, debug_paths) with stable on-disk layout through v7.

## 6 (2026-04-24)

**DoG band-pass added to the production observer; versioning unified under tr_schema.** Geometry-affecting: yes.

- DoG band-pass pre-filter (`dog_filter_blob_scale`) wired into `observe_blob_at` and `compute_heat_map_roi`. Enhances torso-scale blobs and suppresses sub-torso speckle. Default `k=3.0`.
- Versioning refactor for contract C9 compliance: introduces [tr_schema.py](../track_runner/tr_schema.py) as the single schema-version authority. New constants: `SCHEMA_VERSION`, `GEOMETRY_AFFECTING_SCHEMAS = {3, 6}`, `SUPPORTED_ARTIFACT_SCHEMAS`, helpers `latest_geometry_affecting_schema()` and `is_supported_artifact_schema()`.
- Removes the parallel `BLOB_OBSERVER_VERSION = "v1"` constant from [residual_motion.py](../track_runner/residual_motion.py); cache invalidation for observer changes now flows through `GEOMETRY_AFFECTING_SCHEMAS` keyed off the unified `SCHEMA_VERSION`.
- Geometry tag format changes from `blob_snap/v1/...` to `geometry_schema_v<N>/...`. `migrate_legacy_fingerprints` rewrites legacy `blob_snap/v1` keys to `geometry_schema_v3` (the schema version current when v1 ruled); migrated v3-era keys correctly fail the v6 match and are re-solved.
- Loaders for diagnostics, geometry_cache.npz, and debug_paths.npz no longer test equality against current; they consult `tr_schema.is_supported_artifact_schema(...)` against the per-artifact compatibility set.
- `GEOMETRY_CACHE_SCHEMA_VERSION` and `DEBUG_PATHS_SCHEMA_VERSION` are now aliases of `tr_schema.SCHEMA_VERSION`, not independent constants.
- `race_start.py` and `scoring.py` aliases now import `tr_schema` directly instead of chaining through `state_io`.
- Drift-prevention test [test_tr_schema_version_single_source.py](../tests/test_tr_schema_version_single_source.py) tightened to flag any module-level `*(SCHEMA|CACHE|OBSERVER|FINGERPRINT)_VERSION` constant outside `tr_schema.py` that is not a literal re-export, with a whitelist-only exemption mechanism.

## 5 (2026-04-24)

**Unified schema versions per contract C8.** Geometry-affecting: no.

- Collapses three independent version constants (`DIAGNOSTICS_HEADER_VALUE`, `INTERVAL_SCORE_SCHEMA_VERSION`, `PRE_RACE_REFERENCE_SCHEMA_VERSION`) into a single unified `SCHEMA_VERSION` across [state_io.py](../track_runner/state_io.py), [scoring.py](../track_runner/scoring.py), and [race_start.py](../track_runner/race_start.py).
- Adds new fields to `pre_race_reference` dict: `race_start_interval` (list of two frame indices) for the detected race-start seed pair.
- Cache impact (post-migration split): schema bump alone does NOT invalidate solved-geometry entries. The cache key (`GEOMETRY_TAG`) depends only on blob-snap geometry constants; `/schema/N` moved out of the cache key into `SOLVER_FINGERPRINT_TAG` (telemetry only). `migrate_legacy_fingerprints` rewrites pre-unification tails (`/score_schema/4/prerace/4`) and schema-tagged tails (`/schema/5`) to the current `GEOMETRY_TAG` at load time.

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
- Writer [state_io.py](../track_runner/state_io.py) emits v3 nested shape only; on load migrates legacy v2 flat shape to v3 in memory.
- Introduces `DIAGNOSTICS_HEADER_VALUE` versioning in [state_io.py](../track_runner/state_io.py) (value=3) to distinguish v2 flat layout from v3 nested layout.
- `SOLVER_FINGERPRINT_TAG` first includes `/score_schema/3` fragment so cache invalidates on schema changes.

## 2 and earlier

Pre-history: not logged prospectively. Version 1 predates the analytical solver; version 2 is the legacy optical-flow solver schema with flat per-interval fields (`confidence`, `agreement_score`, `competitor_margin`, etc.). Schemas 1 and 2 are obsolete; they are not reconstructed here to avoid guessing. If git history is needed for legacy schema details, consult `git log --all` entries prior to 2026-03-24 or review the `tests/test_seed_schema_v3.py` migration helpers which capture some v2/v3 shape differences for testing purposes.
