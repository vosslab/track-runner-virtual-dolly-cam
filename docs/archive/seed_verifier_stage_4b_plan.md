# Seed verifier (Stage 4b) landing plan

Source assessment: `INTEGRATION_ASSESSMENT.md`
Plan A. Drafted 2026-05-25 from the blob_walk experiment
(`REPORT.md`,
`ABLATION_REPORT.md`).

## Context

The blob_walk experiment found that seed-local override extraction
(DoG target = 0.7 * seed_w, ROI = tight box around acceptance, acceptance
box = `[seed_x, seed_y, seed_w, 1.5h]`, extending 0.5h downward toward
the runner's thighs where motion residual peaks) recovers the correct
runner blob on 89.3 percent of bootstrap attempts (50/56 across five
videos), up from 12.5 percent for the production default. On the
remaining 6 seeds the acceptance box is empty (no blob); this is itself
a useful signal: either the seed is pre-race or visually occluded, or
the production blob extractor cannot see the runner at all.

This plan lands the per-seed "did the acceptance box contain a blob?"
flag into production as a new Stage 4b "seed verifier" that runs once
per visible post-race seed before Stage 3 begins. The flag is consumed
downstream by Stage 4 promotion and by the UI seed-review tool. The
plan does NOT replace Stage 4's existing Hermite-anchored blob-coupled
re-solve; the walker integration (Plan C in the assessment) is a
separate follow-up plan.

## Design philosophy

Land the most-confirmed walker capability (seed-frame override
extraction) into production as a per-seed quality signal, before
tackling the harder Stage 4-replacement question. The trade-off accepted:
no Stage 4 trajectory change in this plan; the seed verifier output
shapes which intervals get extra evidence, not how that evidence is
spent. Alternative rejected: skip Plan A and go straight to a Stage 4
walker replacement (Plan C). Rejected because the walker's k=6
direction-reversal cliff means most intervals cannot walk long enough
to replace Stage 4 today; Plan B already proved the cliff is the
proximate failure cause, and the walker-replacement design must wait
for that single-gate fix to land first.

## Scope

- New module: `track_runner/seed_verifier.py`.
- New artifact: `tr_config/<basename>.track_runner.seed_verifier.json`
  per video, written by the verifier and read by the solver.
- Extension: `state_io.load_seeds` attaches the per-seed flag in
  memory when the artifact exists.
- Extension: `interval_solver.select_promoted_intervals` promotes
  intervals where either endpoint has `seed_blob_found = False` to
  Stage 4 blob-coupled re-solve.
- Extension: UI seed-review tool surfaces seeds with
  `seed_blob_count_in_box > 1` (crowded scene) as a yellow flag.
- New doc: `docs/SEED_VERIFIER.md`.

## Non-goals

- No Stage 4 replacement.
- No walker integration (per-frame blob walks across an interval).
- No change to `_apply_blob_snap` or the existing Stage 4 gates.
- No mutation of seed `torso_box` data (contract C1).
- No replacement of the existing motion-cue heat-map; the verifier
  uses the same `observe_blob_at` path with the override params.
- No contract amendment (none required for the read-only verifier
  + downstream-consumer pattern).

## Method

### Per-seed verifier output schema

`tr_config/<basename>.track_runner.seed_verifier.json` is a JSON object
with one entry per visible post-race seed. Keyed by `frame_index`
(matching `seeds.json`); the seed itself is never modified.

Per-seed fields:

| field | type | meaning |
| --- | --- | --- |
| `frame_index` | int | matches `seeds.json` |
| `seed_blob_found` | bool | True if a blob was extracted inside the acceptance box |
| `seed_blob_cx`, `seed_blob_cy` | float / null | blob center in source-frame pixels; null when not found |
| `seed_blob_offset_h` | float / null | distance from seed center to blob center, in torso heights; null when not found |
| `seed_blob_area` | int / null | accepted blob area in pixels; null when not found |
| `seed_blob_count_in_box` | int | number of distinct blobs found inside the acceptance box (1 = clean, 2+ = crowded) |
| `acceptance_box` | tuple | `(x_min, y_min, x_max, y_max)` recorded for audit |
| `dog_diameter` | float | DoG target diameter used (0.7 * seed_w) |
| `verifier_version` | int | bumped when the algorithm changes |

The file is regenerated when seeds change. The existing
`SCHEMA_VERSION` bump rule applies (contract C10).

### Stage 4b execution

Stage 4b runs after Stage 2 (race-start identification) and before
Stage 3 (Hermite-only pass). It dispatches one job per visible
post-race seed via the existing `solver_workers` pool (per-seed jobs
are embarrassingly parallel; pool sizing matches the existing Stage 4
worker model).

Inside a worker:
1. Open `VideoReader` and load `SceneTransform` (same init as Stage 4).
2. For the assigned seed, call `residual_motion.observe_blob_at` with
   the override params (DoG diameter = 0.7 * seed_w, ROI = bounding
   rect of acceptance box, acceptance_box = [x, y, x + w, y + 1.5h]).
3. If a blob is returned, set `seed_blob_found = True` and record the
   blob's geometry. Additionally re-run `extract_frame_blobs` on the
   same residual to count how many blobs fall inside the acceptance
   box (`seed_blob_count_in_box`). The 2+ count is the crowded-scene
   signal.
4. If `observe_blob_at` returns None, set `seed_blob_found = False`
   and null the geometry fields. `seed_blob_count_in_box = 0`.
5. Return the per-seed dict to the driver.

The driver aggregates all per-seed dicts and writes the
`seed_verifier.json` artifact.

### Downstream consumers

**Stage 4 promotion** (`interval_solver.select_promoted_intervals`):
extend the existing tier-based promotion logic so intervals with
`seed_blob_found = False` on either endpoint are also promoted, even
if their confidence tier would normally exclude them. Rationale: a
seed without a clean blob signal cannot be solved well with a
Hermite-only Stage 3 pass; Stage 4's blob-coupled re-solve at least
gives the existing gates a chance.

**UI seed-review tool**: extend the seed-list view to render seeds
with `seed_blob_count_in_box > 1` as a yellow flag with the count.
Click-to-confirm or re-anchor as usual. The seed file is unchanged
unless the user edits the box (contract C1).

## Workstreams

### M1 -- per-seed verifier module

Parallel-plan ready: YES (3 work packages, independent).

#### WP-1a: extract `track_runner/seed_verifier.py`

- Depends on: none.
- Body: new module with `compute_seed_blob(seed, reader, scene_transform, fps) -> dict` that mirrors the override extraction in `tools/blob_walk/walk.py` bootstrap. Defaults match the walker override params exactly. Returns the per-seed dict schema defined above.
- Acceptance: pyflakes clean; unit test in `tests/test_seed_verifier.py` runs the function on the existing Glenbrook 1260 fixture and asserts `seed_blob_found = True` with `seed_blob_offset_h < 0.30` (the override observer in `sanity_pack.py` shows -0.06h, +0.26h).

#### WP-1b: dispatch + artifact write

- Depends on: WP-1a.
- Body: extend `interval_solver` (or a new `stage_4b_seed_verifier.py` helper) to (1) collect all visible post-race seeds, (2) dispatch one verifier job per seed via the existing pool initializer, (3) aggregate results and write `tr_config/<basename>.track_runner.seed_verifier.json`. Idempotent: re-running overwrites the file.
- Acceptance: end-to-end test on one video; verify the JSON file is byte-stable across two runs with the same input.

#### WP-1c: `state_io.load_seeds` extension

- Depends on: WP-1b.
- Body: extend `state_io.load_seeds` to look for the sibling `.seed_verifier.json` artifact; when present, attach the per-seed fields to the in-memory seed dict alongside the existing `cx/cy/w/h` derivations. Defaults: `seed_blob_found = None` when the artifact is missing (downstream code must handle None).
- Acceptance: existing `state_io` tests stay green; new test asserts the per-seed fields land in memory when the artifact is present.

### M2 -- downstream consumers

Parallel-plan ready: YES (3 work packages, independent).

#### WP-2a: Stage 4 promotion extension

- Depends on: WP-1c.
- Body: extend `interval_solver.select_promoted_intervals` to promote intervals where either bracketing endpoint has `seed_blob_found = False`. Existing PROMOTION_TIERS logic stays as-is; the new criterion is additive.
- Acceptance: unit test on a synthetic seed-pair set verifies the new criterion fires; existing promotion tests stay green.

#### WP-2b: UI yellow-flag for crowded seeds

- Depends on: WP-1c.
- Body: extend the seed-review UI (location TBD; likely the Qt seed-list panel) to render seeds with `seed_blob_count_in_box > 1` with a yellow background and the count in the tooltip. Click behavior unchanged.
- Acceptance: visual test on a video with at least one crowded seed.

#### WP-2c: `docs/SEED_VERIFIER.md`

- Depends on: WP-1a (sees the function signature).
- Body: doc page covering: what the verifier computes, what the per-seed fields mean, when the artifact is regenerated, how downstream consumers should read the fields.
- Acceptance: links pass `tests/test_markdown_links.py`; ASCII compliance.

### M3 -- validation

Parallel-plan ready: NO (sequential, depends on M2 ship).

#### WP-3a: regression test sweep

- Depends on: M1 + M2 ship.
- Body: rerun the existing track-runner test suite. Verify Stage 4b output is additive (no existing artifacts change).
- Acceptance: 100 percent existing tests pass.

#### WP-3b: cross-validate against blob_walk experiment

- Depends on: WP-3a.
- Body: compare the new `seed_verifier.json` per-seed `seed_blob_found` against the blob_walk experiment's `startup_label` for the same 5 videos (Conant, Lyra-Wheeling, Jason, Glenbrook, Lyra-Hersey). The two should agree on >= 95 percent of seeds; mismatches are flagged for investigation.
- Acceptance: <= 5 percent mismatch rate; named mismatches investigated and recorded in `docs/active_plans/audits/seed_verifier_cross_validation.md`.

## Acceptance criteria (plan-wide)

- `tr_config/*.track_runner.seed_verifier.json` exists for at least the
  5 blob_walk videos.
- `state_io.load_seeds` reads the artifact and attaches per-seed fields
  in memory.
- Stage 4 promotion uses the new criterion (verified via test).
- UI surfaces crowded seeds.
- `docs/SEED_VERIFIER.md` covers the new module.
- All existing tests stay green.
- Cross-validation against `blob_walk/REPORT.md` startup_label data
  shows <= 5 percent mismatch.

## Risk register

| risk | severity | notes |
| --- | --- | --- |
| Verifier rejects 50%+ of seeds on crowded videos | medium | Lyra-Hersey saw 100% in_box; Conant/Jason ~78%. Hononega may be lower (excluded from blob_walk experiment). Cross-validate before promoting verifier output to a hard gate |
| Override extraction `acceptance_box` direction wrong for some scenes | low | user confirmed direction (Cartesian -y = image +y = toward legs) on 2026-05-24 sanity pack; honored in `seed_extract` removal and walker bootstrap. Re-confirm visually on the first new video added |
| Stage 4 promotion-rule change degrades existing solves | low | promotion criterion is additive (existing tiers still fire); only ADDS promotion candidates, never removes |
| `state_io.load_seeds` performance regression | low | the verifier artifact is small; JSON-parsing it adds < 10 ms per video |
| Schema drift between verifier and consumer | low | `SCHEMA_VERSION` already covers this per contract C10 |

## Cross-references

- `INTEGRATION_ASSESSMENT.md` -- source assessment (Plan A).
- `REPORT.md` -- blob_walk per-variant results.
- `ABLATION_REPORT.md` -- gate-ablation finding.
- [TRACK_RUNNER_CONTRACT.md](../TRACK_RUNNER_CONTRACT.md) -- C1 (seeds are truth), C10 (schema versioning).
- [TRACK_RUNNER_DESIGN.md](../TRACK_RUNNER_DESIGN.md) -- 5-stage pipeline.
- [TR_DEVELOPER_GUIDE.md](../TR_DEVELOPER_GUIDE.md) -- existing-primitive routing conventions.
