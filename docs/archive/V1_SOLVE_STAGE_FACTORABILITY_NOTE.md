# SOLVE_STAGE_FACTORABILITY_NOTE

## Executive Summary

GREEN light to proceed with the 5-stage solve restructure. The blob observer
reads only frozen `raw_pred[t-1:t+1]` plus image data; `raw_pred` is immutable
within blob snap. Hermite recomputation is negligible (~3 ms per 100-frame
interval, < 2% of typical solve time). No cross-frame blob state detected.
Stage 4/5 may recompute Hermite on promoted intervals without caching risk.

## Q1. Blob Snap Mutability

**Finding: `raw_pred` is strictly read-only; all output goes to local `snap_cx`/`snap_cy`.**

File: `track_runner/velocity_model.py`

- Lines 633-681: `_apply_blob_snap` docstring explicitly states "STRICT separation
  of raw and snap: raw is the frozen kinematic trajectory. Never mutated by this
  function." Comments at lines 643-645 and 700-701 reinforce this.
- Line 697: Loop destructures `raw[i]` into immutable tuple elements.
- Lines 724-726: Neighbors extracted from `raw` ONLY; no prior snap_pred reads.
- Lines 728-739: Motion vectors `v_prev`, `v_next`, `v_pred` computed from raw[i-1/i/i+1].
- Lines 813-814: Accepted blob writes to local `snap_cx`, `snap_cy`. The raw_* locals
  remain unchanged; no write-back to raw.
- Line 820-829: snap_pred list built fresh each frame, populated with snap_cx/snap_cy.
- Return line 831: Returns fresh `snap_pred` list; raw array is not returned or modified.

**Conclusion:** raw_pred is immutable. Blob snap produces a distinct output layer.

## Q2. Blob Observer Read Set

**Finding: observe_blob_at reads ONLY raw_pred[t], plus fresh image-derived residual
data per frame. No cross-frame state. No velocity, scale, or prior snap_* values.**

File: `track_runner/residual_motion.py`, lines 816-963

- Lines 862-869: API contract specifies pred_center and pred_box are "used only to
  seed the ROI and to decompose blob displacement; never written into the cache."
- Lines 835-841: Cache content boundary states "residual_cache may hold raw image
  data only: extracted raw-blob lists (pre-gate), frame reads. It MUST NOT hold
  any per-frame decision: accepted blobs, filtered or chained blob lists, gate
  outcomes, or any value derived from a gating decision."
- Lines 888-928: compute_residual_for_frame called with pred_cx, pred_cy, pred_h
  ONLY for ROI sizing. The residual is image-derived, not dependent on motion state.
- Line 902-905: residual cache keyed by (frame_index, roi); FWD/BWD with identical
  raw_pred share the entry; with different ROIs they compute separately (contract C5).
- Lines 930-963: Corridor filtering and blob ranking use pred_cx/cy/w/h for geometry,
  tangent for local orientation, but never consult prior frames or state.

**Anti-pattern check:** No `last_blob`, `prev_blob`, `miss_count`, `*_chain_*`,
or accumulated state found. velocity_model.py lines 741-749 show a single stateless
call to `residual_motion.observe_blob_at`; no loop memory or feedback.

**Conclusion:** Blob layer reads raw_pred[t] geometry + fresh residual image per frame.
Contract C5/C7 compliant; no cross-frame coupling.

## Q3. Hermite Recomputation Cost

**Finding: Hermite-only is ~3 ms per 100-frame interval. Blob snap overhead is
negligible (~0.02 ms). Hermite is << 1 second and safe to recompute in Stage 4/5.**

Measurement: (100-frame interval, 20 iterations, wall-clock on macOS)
  - FWD _compute_raw_pred_forward: 2.978 ms / iter
  - BWD _compute_raw_pred_backward: 0.137 ms / iter
  - Combined per-interval Hermite: 3.11 ms
  - _apply_blob_snap (reader=None, pure pass-through): 0.02 ms / iter

Baseline expectation (from CHANGELOG.md 2026-04-25): per-worker spawn + module
import + WorkerContext pickle is ~1-3 seconds per interval, so 3 ms Hermite is
< 0.3% of per-interval worker overhead.

Real solve time per interval is tens of seconds (residual computation, blob
extraction, scoring), so Hermite (3 ms) is < 0.1% of actual solve time.

**Conclusion:** Recomputing Hermite in Stage 4/5 is free. Blob observer (expensive)
runs once per interval per blob stage, per the plan. No caching needed; no
performance risk.

## Decision

**GREEN.** Proceed with M1+ as written:

- Stage 3 cache stores forward_path/backward_path (frozen Hermite outputs only).
- Stage 4/5 recomputes Hermite on promoted intervals (negligible cost).
- Blob observer called once per interval during Stage 2 (FWD/BWD) only.
- No `raw_pred` cache needed; no cross-frame blob state to guard.
- Contract C5 (per-interval independence) and C8 (FWD/BWD separation) preserved.
