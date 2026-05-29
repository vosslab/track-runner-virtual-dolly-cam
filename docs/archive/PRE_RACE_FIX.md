# Plan: Eliminate diagnostics schema drift (KeyError `interval_score`)

## Objective

Make `*.track_runner.interval_scores.json` schema-coherent end-to-end so that
`target --severity=high` cannot fail with `KeyError: 'interval_score'`, and
prevent the same class of drift from re-emerging when score fields change.

## Context

After `solve -> target(add seeds) -> refine -> target`, the second `target`
crashes in [cli.py](../../track_runner/cli.py)
at `score = iv["interval_score"]`.

The failures happen to cluster in the pre-race region on both test videos
(63 intervals across IMG_3627 and IMG_3629), but there is **no pre-race
specific scoring path**:

- `scoring.score_interval_analytical` always emits `confidence_tier`. There
  is no short-circuit or stationary branch.
- `scoring.score_interval` (the flat-shape producer at
  `scoring.py`)
  is orphaned -- defined but never called by any live path.
- The pre-race region has no dedicated construction in
  `interval_solver.py`, `race_phases.py`, `solver_workers.py`, or
  `solve_queue.py` (verified by grep).

The real pattern is **"seeds the user has not edited since the old
scoring-shape era"**. Pre-race intervals cluster in that pattern only
because users rarely edit stationary pre-race seeds. The same drift can sit
silently in any unedited region of any project:

1. Some past version ran `score_interval` (flat shape) and wrote those
   dicts into the geometry NPZ cache under a fingerprint.
2. Codebase moved to `score_interval_analytical` (nested
   `confidence_tier` shape).
3. `SOLVER_FINGERPRINT_TAG` does NOT include a scoring-schema version,
   so fingerprints unchanged from era 1 still match -- the cache-hit
   branch in [solve_queue.py:168](../../track_runner/solve_queue.py)
   returns the legacy-shape dict verbatim to the pipeline.
4. [state_io.py:860-892](../../track_runner/state_io.py)
   writer has a per-entry `if "confidence_tier" in score: ... else:
   <flat>` branch that faithfully round-trips legacy into a v3-header
   file.
5. [state_io.py:365](../../track_runner/state_io.py)
   reader only migrates flat->nested when `header_val == 2`. Flat
   entries under a v3 header never get migrated.
6. Six reader sites silently hide the mismatch with `.get("interval_score",
   {})` or equivalent (see Silent-default audit below). The KeyError
   finally surfaces at `_validate_diagnostics_confidence` because it is
   the only site that accesses `iv["interval_score"]` with explicit
   indexing.

**Silent-default audit** (every live reader of `interval_score` that uses a
default or a self-fallback):

| Site | Shape |
| --- | --- |
| [cli.py:338](../../track_runner/cli.py) | `iv.get("interval_score", {})` |
| [cli.py:425](../../track_runner/cli.py) | `scored_iv.get("interval_score", {})` |
| [cli.py:429](../../track_runner/cli.py) | `scored_by_key.get(key, {})` |
| [cli.py:528](../../track_runner/cli.py) | `iv.get("interval_score", {})` |
| [cli.py:531](../../track_runner/cli.py) | `prior_scores.get(key, {})` |
| `scoring.py` | `adjacent[0].get("interval_score", adjacent[0])` |
| `scoring.py` | `iv.get("interval_score", iv)` |
| [review.py:530](../../track_runner/review.py) | `iv.get("interval_score", {})` |
| [encode_analysis.py:818, 823](../../track_runner/encode_analysis.py) | `result_item.get("interval_score", {})` |

Every one of these violates the PYTHON_STYLE "do not hide bugs with
defaults" rule for a field the downstream consumer always needs. Most will
need to be tightened so the schema mismatch fails loudly at the reader
rather than silently three hours later.

Intended outcome: drift is eliminated at its source (writer + reader +
cache fingerprint + reader hygiene), the validator can no longer blow up
on stale files, and any future score-schema change auto-invalidates all
cache entries on the next solve.

## Design philosophy

1. **One schema on disk, end to end.** `write_solver_diagnostics` must not
   emit two shapes. If a score dict arrives in a shape the writer does not
   understand, fail loudly at the writer, not three hours later at the
   validator.
2. **Migration fires on shape, not header.** `load_diagnostics` must convert
   any flat-shape entry regardless of file header. Header version should not
   be the gatekeeper for correctness.
3. **Fingerprint covers schema.** Add a `SCORE_SCHEMA_VERSION` constant to the
   solver fingerprint so a schema bump invalidates every stale cache entry on
   the next solve. This is the durable fix that keeps future changes safe.
4. **No silent fallbacks on required keys.** Per
   [PYTHON_STYLE.md](../PYTHON_STYLE.md)
   "do not hide bugs with defaults": remove the
   `scored_by_key.get(key, {})` shim in `_predictions_from_geometry_cache`
   once the source is fixed.
5. **Contract C3 stays intact.** Pre-race intervals remain independent,
   per-interval, and re-solvable. No inter-interval state is introduced.

## Scope and non-goals

**In scope**

- `track_runner/state_io.py`: `load_diagnostics`, `write_solver_diagnostics`,
  the header + schema constants.
- `track_runner/interval_fingerprint.py` and `state_io.interval_fingerprint`:
  add score-schema version to the solver fingerprint tag.
- `track_runner/cli.py`: `_validate_diagnostics_confidence`,
  `_ensure_target_diagnostics`, `_predictions_from_geometry_cache` (remove
  silent default).
- `track_runner/scoring.py`: delete the unused legacy `score_interval`
  function (dead producer of the bad shape).
- Tests under `tests/` covering the above.
- `docs/CHANGELOG.md` entry.

**Non-goals**

- No changes to scoring math in `score_interval_analytical`.
- No changes to FWD/BWD propagation, blob snap, or per-frame residual motion.
- No changes to the race-phase detector.
- No redesign of the geometry cache NPZ format.
- No attempt to reconstruct the lost v3 scores of affected pre-race intervals
  from first principles: those intervals will re-solve on the first run after
  the fingerprint bump (see Risk R1).

## Current state summary

- Failing validator: [cli.py:190-206](../../track_runner/cli.py).
- Writer (per-entry shape branch): [state_io.py:834-930](../../track_runner/state_io.py).
- Reader (header-gated migration): [state_io.py:317-380](../../track_runner/state_io.py).
- Silent default: [cli.py:418-429](../../track_runner/cli.py).
- Fingerprint: [interval_fingerprint.py:30-60](../../track_runner/interval_fingerprint.py),
  [state_io.py:763-801](../../track_runner/state_io.py).
- Dead legacy producer: `score_interval` at `scoring.py`.
- Contract rules this touches: C3 (intervals independent), C4 (seeds are
  truth), C5 (FWD/BWD independent). No contract rewrite needed.

## Architecture boundaries and ownership

| Component | Durable name | Owner role |
| --- | --- | --- |
| Diagnostics I/O | `state_io` (diagnostics reader + writer) | python-code-review agent |
| Interval cache fingerprint | `interval_fingerprint` | python-code-review agent |
| Target-mode validator | `cli` (`_mode_target` + helpers) | python-code-review agent |
| Scoring producers | `scoring` | python-code-review agent |
| Tests | `tests/` | unit-test-starter agent |

### Mapping

- Milestone M1 -> Workstream WS-A (schema hardening) -> components `state_io`,
  `scoring`. Patches: Patch 1, Patch 2.
- Milestone M1 -> Workstream WS-B (fingerprint + validator resilience) ->
  components `interval_fingerprint`, `cli`. Patches: Patch 3, Patch 4.
- Milestone M1 -> Workstream WS-C (tests + docs) -> components `tests`, `docs`.
  Patches: Patch 5.

All components use durable names. Terms like "milestone", "workstream",
"patch" appear only in this doc, not in code identifiers.

## Milestone plan

One milestone is sufficient: the change set is tightly coupled and must ship
together to avoid a partial state where the fingerprint is bumped but the
writer still emits dual schemas.

### M1. Coherent diagnostics schema

- **Depends on:** none.
- **Entry criteria:** failing `target --severity=high` reproduced locally on
  `TRACK_VIDEOS/IMG_3627.MOV` and `TRACK_VIDEOS/IMG_3629.mkv`.
- **Deliverables:**
  - Single-shape writer (v3 only).
  - Shape-based migration in reader (no header gate).
  - `INTERVAL_SCORE_SCHEMA_VERSION` constant wired into solver fingerprint.
  - Validator raises an actionable `RuntimeError` (not `KeyError`) when a
    stale entry is encountered, suggesting re-solve.
  - `_predictions_from_geometry_cache` no longer defaults missing scores to
    `{}`; it raises or skips with a clear message.
  - Unused `scoring.score_interval` removed.
  - Focused pytest coverage for each of the above.
  - Changelog entry under today's `## 2026-04-23` day block.
- **Exit criteria (done checks):**
  1. `source source_me.sh && python -m pytest tests/test_state_io.py
     tests/test_interval_fingerprint.py tests/test_cli_target.py` passes.
  2. `source source_me.sh && python -m pytest
     tests/test_pyflakes_code_lint.py` passes.
  3. Manual smoke: delete stale diagnostics, run
     `./track_runner/track_runner.py -i TRACK_VIDEOS/IMG_3627.MOV solve`
     followed by `target --severity=high` -- no KeyError, validator
     completes.
  4. Manual smoke: on a diagnostics file preserved from before this fix
     (save a copy first), `target --severity=high` surfaces a clear
     "stale diagnostics, please re-solve" error instead of a KeyError.
  5. Fingerprint tag string in `SOLVER_FINGERPRINT_TAG` now contains a
     `score_schema/N` segment; bumping `N` invalidates all prior entries.

### M2. Pre-race module (deferred; separate design pass)

Status: **deferred, out of scope for the drift fix**. Noted here so it is
not lost and so M1 can be scoped tightly.

- **Depends on:** M1 (so the score schema is stable before we add a new
  producer for it).
- **Problem statement:** Pre-race handling is currently scattered and
  under-specified against contract C2:
  - [TRACK_RUNNER_CONTRACT.md](../TRACK_RUNNER_CONTRACT.md)
    C2 requires that torso-box dimensions in
    `[state_io.py](../../track_runner/state_io.py)
  - `scoring.py`
- **Acceptance criteria:**
  - The `else` branch at `state_io.py:881-892` is removed.
  - Missing `confidence_tier` in an input score dict is written as a v3
    nested entry with `confidence_tier="unsolved"`,
    `failure_reasons=["legacy_schema"]`, numeric fields set to `0.0`, and
    `warning_flags=[]`. `refine` will catch these as fingerprint misses
    on its next run; `target` treats `unsolved` as non-actionable
    severity.
  - `scoring.score_interval` (the orphaned function) is deleted. Any import
    referencing it is removed.
  - `DIAGNOSTICS_HEADER_VALUE` remains `3`; no new file version is needed
    because the on-disk shape was already v3 for all new-code writes.
- **Verification commands:**
  - `source source_me.sh && python -m pyflakes track_runner/state_io.py track_runner/scoring.py`
  - `source source_me.sh && python -m pytest tests/test_state_io.py -k write_solver_diagnostics`
- **Dependencies:** none.

### WP-2. Shape-based load migration

- **Title:** Migrate flat-shape entries regardless of header version.
- **Owner:** python-code-review agent.
- **Touch points:**
  - [state_io.py](../../track_runner/state_io.py)
- **Acceptance criteria:**
  - `load_diagnostics` walks every interval entry; if `"interval_score"` is
    absent, it reconstructs from the flat v2 keys exactly as the existing
    v2 branch does, regardless of `header_val`.
  - If the flat keys are also absent, it raises `RuntimeError` identifying
    the offending interval and suggesting re-solve (do not fabricate
    `confidence_tier`).
  - Loader still accepts `header_val in (2, 3)` with no new rejection.
- **Verification commands:**
  - `source source_me.sh && python -m pytest tests/test_state_io.py -k load_diagnostics`
- **Dependencies:** none.

### WP-3. Add `INTERVAL_SCORE_SCHEMA_VERSION` to solver fingerprint

- **Title:** Auto-invalidate cached interval geometry when score schema
  changes.
- **Owner:** python-code-review agent.
- **Touch points:**
  - [interval_fingerprint.py](../../track_runner/interval_fingerprint.py)
  - `scoring.py`
    (add constant near the analytical scorer).
- **Acceptance criteria:**
  - `scoring.INTERVAL_SCORE_SCHEMA_VERSION = 3` exists as a module-level
    `int` with a comment: "bump when any field in the analytical score
    dict is added, removed, renamed, or semantically changed. Starts at
    3 to match DIAGNOSTICS_HEADER_VALUE today, but the two are
    independent and will diverge when one is bumped without the other."
  - `SOLVER_FINGERPRINT_TAG` in `interval_fingerprint.py` is extended with
    `f"/score_schema/{scoring.INTERVAL_SCORE_SCHEMA_VERSION}"`.
  - Bumping the constant changes the fingerprint string, which is covered
    by a unit test.
- **Verification commands:**
  - `source source_me.sh && python -m pytest tests/test_interval_fingerprint.py`
- **Dependencies:** none.

### WP-4. Actionable validator + remove silent default

- **Title:** Validator raises clear re-solve message; remove
  `.get(key, {})` in cache merge.
- **Owner:** python-code-review agent.
- **Touch points:**
  - [cli.py](../../track_runner/cli.py)
  - [cli.py](../../track_runner/cli.py)
- **Acceptance criteria:**
  - `_validate_diagnostics_confidence` uses explicit key access on
    `iv["interval_score"]` but wraps the whole entry check so that a
    missing key raises `RuntimeError(f"stale diagnostics for interval
    ({start}, {end}); delete {diag_path} and re-solve")` rather than
    `KeyError`.
  - `_ensure_target_diagnostics` converts that `RuntimeError` into the
    existing "re-solve from scratch" control flow if safe, or surfaces it
    unchanged when the caller cannot re-solve in this mode.
  - In `_predictions_from_geometry_cache`, the `scored_by_key.get(key, {})`
    fallback is removed; missing scores raise `RuntimeError` with the
    offending key. This is the PYTHON_STYLE "do not hide bugs with
    defaults" rule.
  - No behavioural change for files that are already in the v3 nested
    shape.
- **Verification commands:**
  - `source source_me.sh && python -m pytest tests/test_cli_target.py`
- **Dependencies:** WP-1, WP-2 (so that any fresh solve produces the shape
  the validator demands).

### WP-5. Regression tests + changelog

- **Title:** Lock down the schema contract in tests and record the change.
- **Owner:** unit-test-starter agent (tests), docset-refresh agent
  (changelog).
- **Touch points:**
  - `tests/test_state_io.py` (new or extended): shape-based load
    migration, writer reject-on-legacy, round-trip.
  - `tests/test_interval_fingerprint.py` (new or extended): fingerprint
    string contains `score_schema/N` and changes when the constant is
    bumped.
  - `tests/test_cli_target.py` (new or extended): stale diagnostics file
    yields the new `RuntimeError` with a clear message (use `tmp_path`
    and a hand-crafted JSON fixture; no video decode).
  - [CHANGELOG.md](../CHANGELOG.md)
    under the existing `## 2026-04-23` day block, in "Fixes and
    Maintenance" and "Behavior or Interface Changes" subsections as
    appropriate.
- **Acceptance criteria:**
  - All three test modules pass via
    `source source_me.sh && python -m pytest <path>`.
  - Tests obey [PYTHON_STYLE.md](../PYTHON_STYLE.md)
    "PYTEST" rules: no brittle assertions on hardcoded constants or
    collection sizes, only behavioural properties.
  - Changelog entry names the plan file and links the touched modules.
- **Verification commands:**
  - `source source_me.sh && python -m pytest tests/test_state_io.py tests/test_interval_fingerprint.py tests/test_cli_target.py`
  - `source source_me.sh && python -m pytest tests/test_pyflakes_code_lint.py`
- **Dependencies:** WP-1, WP-2, WP-3, WP-4.

## Acceptance criteria and gates

A change is accepted when:

1. All per-WP verification commands pass.
2. End-to-end manual flow succeeds on both failing videos:
   `solve -> target -> refine -> target` with no `KeyError`.
3. A deliberately-crafted stale diagnostics file (nested `interval_score`
   missing) now produces a `RuntimeError` whose message tells the user to
   re-solve.
4. `SOLVER_FINGERPRINT_TAG` for a known seed pair changes when
   `INTERVAL_SCORE_SCHEMA_VERSION` is bumped, in a unit test.

## Test and verification strategy

- Unit tests cover the writer, reader, fingerprint, and validator in
  isolation using in-memory dicts and `tmp_path` JSON files. No video
  decode required.
- Pytest `-k` selection lets agents run focused tests during iteration per
  AGENTS.md ("run focused tests on changed code").
- Smoke check the pre-existing `tests/test_pyflakes_code_lint.py` and
  `tests/test_ascii_compliance.py` gates after every patch.
- End-to-end verification is manual and is the final gate because it
  requires the user's local video files.

## Migration and compatibility policy

- Additive: the on-disk header stays at `3`. No new file version.
- Backward compatible reads: flat-shape entries continue to load cleanly
  (per WP-2).
- Forward invalidation: bumping `INTERVAL_SCORE_SCHEMA_VERSION` in the
  future will cause every cached interval's fingerprint to miss, forcing
  a clean re-solve. This is the intended safety valve.
- No migration script is needed; existing stale files either (a) get
  re-solved on the next `solve`/`refine` cycle because the fingerprint
  bump invalidates them, or (b) surface a clear re-solve error on `target`
  if the user manages to call `target` before re-solving.

## Risk register

| ID | Risk | Impact | Trigger | Owner | Mitigation |
| --- | --- | --- | --- | --- | --- |
| R1 | Fingerprint bump forces a full re-solve for every existing project on first run after update | One-time compute cost per video | First run after shipping | release owner | Document in the changelog and README; time the re-solve on the two test videos and quote numbers |
| R2 | Removing the writer `else` branch breaks a producer we failed to find | Solve crashes late | Any code path that passes a legacy score dict | python-code-review agent | WP-1 raises with the offending keys; unit test constructs a legacy dict and asserts the error fires |
| R3 | Removing `.get(key, {})` surfaces other stale entries the user had | `target` fails instead of silently returning nonsense | Stale project files that are not pre-race-only | python-code-review agent | Validator message tells the user to re-solve; single-command recovery |
| R4 | New `RuntimeError` paths in `cli.py` surface as traceback rather than friendly message | Poor UX | User runs `target` with stale diagnostics | python-code-review agent | Catch at `_ensure_target_diagnostics` and print one-line "run `solve` first" hint before raising |
| R5 | Dead `score_interval` in `scoring.py` is imported somewhere we missed | Import error | `from track_runner.scoring import score_interval` at module scope | python-code-review agent | Grep the repo before deletion (already done during planning); pyflakes gate catches the rest |

## Rollout and release checklist

1. Land Patch 1 then Patch 2 (writer + reader): system still works on
   existing good files.
2. Land Patch 3 (fingerprint): expect a one-time re-solve on every
   project; verify on both test videos.
3. Land Patch 4 (validator + silent-default removal): verify that the new
   error message reads cleanly.
4. Land Patch 5 (tests + changelog).
5. Human commits per REPO_STYLE. AI agents do not `git commit`.

## Documentation close-out requirements

- `docs/CHANGELOG.md`: one entry under `## 2026-04-23` describing root
  cause, the four code changes, and the fingerprint bump implication.
- No updates required to `docs/TRACK_RUNNER_CONTRACT.md` or
  `docs/TRACK_RUNNER_DESIGN.md` (no contract clause changes).
- No updates required to `docs/TRACK_RUNNER_V3_SPEC.md` unless the spec
  explicitly documents the on-disk diagnostics shape (if so, note that
  schema is v3-only end-to-end).

## Patch plan and reporting format

- Patch 1: state_io + scoring -- v3-only writer, delete dead legacy
  producer.
- Patch 2: state_io -- shape-based load migration.
- Patch 3: interval_fingerprint + scoring -- add
  `INTERVAL_SCORE_SCHEMA_VERSION` and embed in fingerprint tag.
- Patch 4: cli -- actionable validator, remove silent `.get(key, {})`.
- Patch 5: tests + docs -- regression tests and changelog entry.

## User decisions (locked)

- **Stale-entry handling is mode-dependent.**
  - `refine` mode: automatically re-solve any interval whose stored score
    is stale (missing `interval_score` nested dict, or
    `confidence_tier="unsolved"` per writer policy below). The existing
    fingerprint-miss path already drives this; the validator should not
    block refine.
  - `target` mode: skip stale intervals for severity filtering purposes
    and print one consolidated warning listing how many intervals were
    skipped and the command to run (`refine` or re-`solve`). Do not
    crash. The annotator opens normally on the non-stale subset.
- **Writer policy on legacy-shape input:** tag the entry as an unsolved
  interval rather than raising. The entry is written in v3 nested shape
  with `confidence_tier="unsolved"`, `failure_reasons=["legacy_schema"]`,
  and other numeric fields set to `0.0`. This keeps the on-disk shape
  coherent and lets `refine` pick the interval up as a fingerprint miss
  on its next run.
- **`INTERVAL_SCORE_SCHEMA_VERSION` starts at `3`** to match
  `DIAGNOSTICS_HEADER_VALUE`. A short comment notes that the two numbers
  are independent and will diverge the next time one is bumped without
  the other.

These decisions update the corresponding acceptance criteria in WP-1
(writer tags unsolved, does not raise), WP-3 (constant starts at 3), and
WP-4 (validator is mode-aware: re-solve on refine, warn-and-skip on
target).
