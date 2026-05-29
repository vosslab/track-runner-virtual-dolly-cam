## CLOSED 2026-05-29

Status: closed as part of windowed-walker plan closure (WP-3C of
`~/.claude/plans/sequential-soaring-hopper.md`). This document served as the
tracking record for HIGH/MEDIUM findings from `dump_step1/BLOB_EXTRACTION_CODE_AUDIT.md`.

Shipped against this ticket family: the 2026-05-28 extraction re-scope returned
`observe_blob_at` to a single-winner observation plus a `BlobObserverTrace.corridor_blobs`
candidate list. The windowed walker reads `trace.corridor_blobs` directly as its
per-frame candidate input. This covers ticket E-1 partially (consumer-list access
without a formal `extract_blobs_at` API split).

The remaining tickets (E-2 through E-9A) are out of scope under the final
windowed-walker design, which contained windowing to `tools/blob_walk_v2/walk_walker.py`
and left `track_runner/residual_motion.py` untouched per the amendment's
"API Decision (2026-05-28)" rationale. No further dispatch is planned against
this doc. Refer to the windowed-path-selection amendment (also archived
2026-05-29) for the authoritative scope decision.

---

# Extraction re-scope follow-ups

Ticketed follow-on tasks derived from `dump_step1/BLOB_EXTRACTION_CODE_AUDIT.md`
(2026-05-28). HIGH-severity findings first, in audit order. MEDIUM-severity
findings appear in the secondary section at the bottom. LOW/INFO findings are
intentionally omitted.

## Ticket E-1: Split observe_blob_at into raw extraction + consumer logic

**Source**: BLOB_EXTRACTION_CODE_AUDIT.md finding F1 row 1
**File:line**: `track_runner/residual_motion.py:924-1392`
**Severity**: HIGH
**Description**: Mixes raw blob detection (extraction job) with corridor filtering, cue scoring, winner selection, and torso re-anchor (consumer concerns). Forces every caller through identical direction-aware gating regardless of whether the caller knows the runner direction. Violates principle 1 (no reliable direction at extraction).
**Recommendation**: Split: (a) `extract_blobs_at(frame, roi)` returns raw blob list with features only; (b) winner / corridor / confidence logic moves to per-consumer helpers.
**Suggested follow-on**: introduce `extract_blobs_at` returning raw blob list; migrate consumers off `observe_blob_at` incrementally.
**Blocked-by**: none

## Ticket E-2: Strip corridor filter from observe_blob_at

**Source**: BLOB_EXTRACTION_CODE_AUDIT.md finding F1 row 2
**File:line**: `track_runner/residual_motion.py:1275`
**Severity**: HIGH
**Description**: Corridor radius hardcoded as `max(1.5*pred_w_p, 0.75*pred_h_p)` and applied via cross-axis projection against tangent. Direction-aware filter inside extraction. Violates principle 1.
**Recommendation**: Strip the corridor filter from `observe_blob_at`. Return the full `raw_blobs` list.
**Suggested follow-on**: convert hard discard to logged `cross_track_distance_px` feature on each blob; expose in trace.
**Blocked-by**: E-1

## Ticket E-3: Remove torso-center re-anchor block

**Source**: BLOB_EXTRACTION_CODE_AUDIT.md finding F1 row 3
**File:line**: `track_runner/residual_motion.py:1295-1323`
**Severity**: HIGH
**Description**: Torso-center re-anchor projects winner offset onto along/cross axes from `local_tangent` and drops cross-track. Direction-aware. Per coordinator 2026-05-28, seed-chord tangent is also not reliable -- seeds are seconds apart, runners curve / change lanes / accelerate. There is NO reliable source of track direction at extraction time.
**Recommendation**: Remove the re-anchor block entirely. Return raw winner-blob centroid as `center_pixel`. Body-to-torso interpretation is a consumer responsibility using LOCAL information only (last few accepted walker frames).
**Suggested follow-on**: push body-to-torso correction down to walker using last few accepted frames.
**Blocked-by**: E-1

## Ticket E-4: Drop local_tangent parameter from extraction API

**Source**: BLOB_EXTRACTION_CODE_AUDIT.md finding F1 row 4
**File:line**: `track_runner/residual_motion.py:928, 1235`
**Severity**: HIGH
**Description**: `local_tangent` parameter is consumed by direction-aware code inside extraction. Both consumers (`walk_walker.py:242` and `dump_step1_inputs.py:98`) pass the axis-aligned fallback `(1,0,0,1)`. The re-anchor silently degrades to drop-y-offset regardless of true runner direction; on diagonal-runner intervals it discards genuine along-track motion as if body noise. Even if tangent were honest it would still violate principle 1.
**Recommendation**: Drop the parameter from the extraction API. No tangent argument anywhere below the API boundary.
**Suggested follow-on**: remove parameter; update call sites in walker and dump tool.
**Blocked-by**: E-2, E-3

## Ticket E-5: Move winner selection to consumer

**Source**: BLOB_EXTRACTION_CODE_AUDIT.md finding F1 row 5
**File:line**: `track_runner/residual_motion.py:1262-1273, 1282-1293`
**Severity**: HIGH
**Description**: Winner selection (`compute_cue_confidence` argmax) inside extraction. Score includes proximity-to-predicted (direction-agnostic but still a runner-prior) and area-vs-predicted-box.
**Recommendation**: Move winner selection to consumer. Extraction returns raw blob list; if a single-blob API shape is required, expose the strongest by `integrated_mag` with no runner-prior scoring.
**Suggested follow-on**: provide `strongest_by_integrated_mag` helper for legacy single-blob callers.
**Blocked-by**: E-1

## Ticket E-6: Delete filter_blobs_to_corridor from extraction module

**Source**: BLOB_EXTRACTION_CODE_AUDIT.md finding F2
**File:line**: `track_runner/residual_motion.py:304-340`
**Severity**: HIGH
**Description**: Function exists at all. Corridor filtering is direction-aware (decomposes via `(tx, ty, nx, ny)` tangent) and assumes a known runner path. Per principle 1, does not belong in extraction module.
**Recommendation**: Move to consumer-side helper (e.g. `tools/blob_walk_v2/walk_corridor.py`). Extraction module exposes only blob detection + features.
**Suggested follow-on**: relocate function to walker-side helper; update imports.
**Blocked-by**: E-2

## Ticket E-7: Strip compute_cue_confidence from extraction

**Source**: BLOB_EXTRACTION_CODE_AUDIT.md finding F3
**File:line**: `track_runner/residual_motion.py:838-894`
**Severity**: HIGH
**Description**: Builds a single scalar from strength + size_score + proximity, all of which bake in runner-path priors (proximity-to-predicted-center, area-vs-predicted-box). Violates principle 1: no scores derived from runner-path priors.
**Recommendation**: Strip the score from extraction. Consumers compute their own confidence with their own context. If a quick strongest-blob lookup is needed, use raw `integrated_mag`.
**Suggested follow-on**: retain position-distance and size-strength components as LOGGED per-blob features per refined boundary; never hard discard.
**Blocked-by**: E-5

## Ticket E-8: Drop tangent fallback from walk_walker

**Source**: BLOB_EXTRACTION_CODE_AUDIT.md finding F8 row 1
**File:line**: `tools/blob_walk_v2/walk_walker.py:242`
**Severity**: HIGH
**Description**: `local_tangent = (1.0, 0.0, 0.0, 1.0)` axis-aligned fallback passed into both bootstrap and per-step `observe_blob_at` calls. Carries no semantic meaning. With the just-landed re-anchor this collapses the projection to drop-y-offset, silently miscorrecting diagonal-runner intervals.
**Recommendation**: After extraction API change (no tangent parameter), this line goes away. Until then, do NOT pass `(1,0,0,1)` as if it were a heading.
**Suggested follow-on**: delete tangent argument once E-4 lands.
**Blocked-by**: E-4

## Ticket E-9: Drop tangent fallback from dump_step1_inputs

**Source**: BLOB_EXTRACTION_CODE_AUDIT.md finding F9 row 1
**File:line**: `tools/blob_walk_v2/dump_step1_inputs.py:98`
**Severity**: HIGH
**Description**: `local_tangent = (1.0, 0.0, 0.0, 1.0)` axis-aligned fallback same as walker. Affects bootstrap (acceptance_box path) and steps 1-3 (production-shape).
**Recommendation**: Same fix as F8: drop after extraction API change.
**Suggested follow-on**: delete tangent argument once E-4 lands; collapse `raw_centroid_x/y` duplicate fields.
**Blocked-by**: E-4

## Secondary section: MEDIUM-severity follow-ups

## Ticket E-1A: Keep bbox test, remove cue_confidence + winner from acceptance branch

**Source**: BLOB_EXTRACTION_CODE_AUDIT.md finding F1 row 6 (MEDIUM)
**File:line**: `track_runner/residual_motion.py:1237-1273`
**Severity**: MEDIUM
**Description**: Acceptance-box branch is a pure geometric ROI test (good) but is immediately followed by `cue_confidence` scoring + winner selection (bad). The bbox test is direction-agnostic and could stay; the scoring + selection that follow must go.
**Recommendation**: Keep only the bbox membership test if any. Remove the `cue_confidence` and winner selection on this branch.
**Suggested follow-on**: retain bbox membership filter; delegate winner choice to consumer.
**Blocked-by**: E-5, E-7

## Ticket E-3A: Stop mutating input blob dict in compute_cue_confidence

**Source**: BLOB_EXTRACTION_CODE_AUDIT.md finding F3 row 2 (MEDIUM)
**File:line**: `track_runner/residual_motion.py:889-892`
**Severity**: MEDIUM
**Description**: Mutates the input blob dict (adds `dist_h`, `size_score`, `proximity_score`, `total_score`). Dual-duty function: returns float, also writes side-effect fields. Hidden output.
**Recommendation**: Return a structured score; let the caller attach it.
**Suggested follow-on**: return a dataclass / named tuple; caller decides whether to attach fields.
**Blocked-by**: E-7

## Ticket E-8A: Update walker to compute body-to-torso correction from local accepted frames

**Source**: BLOB_EXTRACTION_CODE_AUDIT.md finding F8 row 3 (MEDIUM)
**File:line**: `tools/blob_walk_v2/walk_walker.py:407, 419-420, 428`
**Severity**: MEDIUM
**Description**: Reads `obs.center_pixel` as torso-corrected. Once re-anchor is stripped this becomes the raw body centroid; walker will need to apply LOCAL direction context (last few accepts) for any body-to-torso interpretation -- never derived from full-interval seed chord.
**Recommendation**: Update consumer to either (a) accept the body-extent offset in the motion gate, or (b) compute body-to-torso correction from the last few accepted walker frames (per coordinator 2026-05-28 LOCAL-only rule).
**Suggested follow-on**: prototype option (b) with rolling window of accepted frames; gate via motion test.
**Blocked-by**: E-3
