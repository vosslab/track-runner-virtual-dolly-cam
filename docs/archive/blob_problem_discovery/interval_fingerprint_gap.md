# Interval fingerprint coverage gap

## Summary

The interval fingerprint encodes seed-pair geometry plus a schema-version tag,
but it does NOT encode the numeric gate constants that govern blob-coupled
solve behavior. When those constants change (for example the M3-selected
policy fix to `BLOB_SNAP_ALPHA` or to the `compute_cue_confidence` weights),
previously solved intervals continue to hash to the same key and are silently
reused from the cache. The behavioral change does not take effect until an
unrelated schema bump or seed edit invalidates the entry.

There is a stale comment in
[velocity_model.py](../../../track_runner/velocity_model.py)
that asserts blob-snap constants ARE "baked into the solver fingerprint".
That claim is false today and is the most visible symptom of the gap.

## Background

The cache-reuse contract is implemented in three places.

- The hash itself is built by `interval_fingerprint` in
  [state_io.py](../../../track_runner/state_io.py) at line 770. The
  function reads only `frame_index` plus the rounded `(cx, cy, w, h)` of each
  seed endpoint, then appends an optional `solver_tag` suffix.
- The tag passed for that suffix is built by `build_geometry_tag` in
  [interval_fingerprint.py](../../../track_runner/interval_fingerprint.py)
  at line 47. The tag is `schema_v<N>` where N is the latest
  geometry-affecting schema version from `tr_schema.GEOMETRY_AFFECTING_SCHEMAS`.
- The schema-version authority is `SCHEMA_VERSION` in
  [tr_schema.py](../../../track_runner/tr_schema.py) at line 23,
  with the affecting-set declared at line 49.

Cache reuse happens when both `solve` and `refine` compute the same
fingerprint for an interval. The solver driver in
[interval_solver.py](../../../track_runner/interval_solver.py)
uses `compute_interval_fingerprint` (line 33) as the lookup key into
`prior_solved_intervals`. The same helper is reached from the refine path
through `solve_queue.plan_interval_work`. By design, two intervals with
identical seed endpoints and identical geometry tag are treated as
interchangeable.

## What is in the fingerprint

The fingerprint inputs as of today, per
[state_io.py](../../../track_runner/state_io.py) lines 802 to 812:

- `frame_index` of the start seed (integer)
- `frame_index` of the end seed (integer)
- `(cx, cy, w, h)` of the start seed, each rounded to two decimal places
- `(cx, cy, w, h)` of the end seed, each rounded to two decimal places
- the geometry tag `schema_v<N>` appended after a `||` separator

Notably absent from the fingerprint inputs (intentionally, per
[interval_fingerprint.py](../../../track_runner/interval_fingerprint.py)
lines 56 to 61):

- `bin_factor`; bin-aware computation crosses the source/processed boundary
  inside `camera_motion` and `residual_motion` and upscales back to source
  pixels before the interval solver consumes them.

The video identity, scene-transform digest, and motion-track digest are
checked at a higher level (run-fingerprint walk in
[solve_queue.py](../../../track_runner/solve_queue.py)), not in
the per-interval key.

## What is missing

The following constants govern blob-coupled solve behavior, change the
per-frame solver output when they change, and are NOT inputs to the
fingerprint.

Blob-snap gate constants in
[velocity_model.py](../../../track_runner/velocity_model.py):

- `BLOB_SNAP_ALPHA = 0.6` at line 528; proximity gate cap as a fraction of
  torso height.
- `BLOB_SNAP_PATH_SLACK = 0.5` at line 530; motion-path along-track slack.
- `BLOB_SNAP_PATH_PERP_FRACTION = 0.75` at line 533; motion-path
  perpendicular cap.
- `BLOB_SNAP_VELOCITY_FLOOR = 1.5` at line 537; velocity floor below which
  the direction and motion-path gates are skipped.
- `BLOB_SNAP_ALPHA_MAX = 0.5` at line 540; upper bound on the per-frame
  blob blend weight.
- `BLOB_SNAP_MAX_SHIFT_FRACTION = 0.5` at line 543; per-frame displacement
  clamp.

Direction-gate dot-product threshold in
[velocity_model.py](../../../track_runner/velocity_model.py) at
line 833. The threshold is the bare integer literal `0.0`; the gate is
`(dx * v_pred[0] + dy * v_pred[1]) >= 0.0`. The literal is not a named
constant, which makes it harder to surface in a fingerprint helper than
the `BLOB_SNAP_*` names above.

Cue-confidence weights inside `compute_cue_confidence` in
[residual_motion.py](../../../track_runner/residual_motion.py)
at line 881. The expression `strength * 0.3 + size_score * 0.3 +
proximity * 0.4` carries three weights and the strength normalization
denominator `10000.0` at line 865; none are named constants. The
proximity, size, and strength scoring expressions feeding into this
combination at lines 865 to 879 are also implicitly part of the
contract.

Residual-extraction tunables in
[residual_motion.py](../../../track_runner/residual_motion.py):

- `MIN_BLOB_AREA = 25` at line 51; minimum blob area in pixels before a
  candidate is discarded by `extract_frame_blobs`.
- `DEFAULT_THRESHOLD = 10.0` at line 54; magnitude threshold for blob
  binarization; passed as the default argument to `observe_blob_at` at
  line 929 and therefore reaches the solver path.
- `DEFAULT_HALF_WINDOW = 4` at line 83; residual-motion temporal window
  size. Window-resolution changes already required a schema bump (v9 was
  added to `GEOMETRY_AFFECTING_SCHEMAS` for exactly this reason; see
  [tr_schema.py](../../../track_runner/tr_schema.py) line 39),
  which confirms the underlying tuning belongs in the fingerprint
  contract somehow.
- The DoG k-factor and minimum target diameter near lines 56 to 68 of the
  same file. The DoG kernel addition was likewise tracked through schema
  v6 (see `GEOMETRY_AFFECTING_SCHEMAS` declaration line 33), again
  confirming that DoG-pipeline tuning is fingerprint-relevant.

The comment block at
[velocity_model.py](../../../track_runner/velocity_model.py)
lines 525 to 527 claims these constants "are baked into the solver
fingerprint in interval_solver.SOLVER_FINGERPRINT_TAG; any numeric change
invalidates the refine cache automatically." This is not what the
fingerprint code does. The `SOLVER_FINGERPRINT_TAG` builder at
[interval_fingerprint.py](../../../track_runner/interval_fingerprint.py)
line 74 returns `<geometry_tag>/schema/<SCHEMA_VERSION>`, and the
key-bearing tag passed into `interval_fingerprint` is the geometry tag
alone, which depends only on `GEOMETRY_AFFECTING_SCHEMAS` membership.
Numeric tuning of a `BLOB_SNAP_*` value does not change either.

## Risk scenario

The counterfactual fix plan (M3 selects a policy, M4 implements it) is
expected to change one or more of the constants enumerated above. The
end-to-end failure mode without a fingerprint update is:

1. M4 lands a change to `BLOB_SNAP_ALPHA` (or to a
   `compute_cue_confidence` weight, or to the dot-product threshold).
   No on-disk schema layout changes.
2. The implementer does not bump `SCHEMA_VERSION` and does not add a new
   value to `GEOMETRY_AFFECTING_SCHEMAS`, because nothing on disk
   changed shape.
3. The user runs `track_runner refine` on an existing project, or
   `track_runner solve` on a project with a populated `tr_config/` cache.
4. The solver driver computes the per-interval fingerprint with the same
   geometry tag as before. Every interval whose seed endpoints are
   unchanged hits the prior-solved partition and is returned from cache
   without invoking the worker.
5. The visual review shows no change. The fix appears not to work. The
   implementer either backs out a correct change or escalates the
   constant further, deepening the bug.

A secondary path arrives at the same place via mixed populations: a
project that solved one Sunday with `ALPHA = 0.6` and is re-solved
Monday with `ALPHA = 1.0`. Intervals untouched by seed edits in between
silently keep the Sunday geometry; intervals with new or moved seeds get
the Monday geometry. The cache becomes a mixture of two policies with
no diagnostic surface that says so.

## Mitigation options

Three approaches; not mutually exclusive.

(a) Add the gate constants to the fingerprint inputs. Introduce a
`gate_params_hash()` helper in
[interval_fingerprint.py](../../../track_runner/interval_fingerprint.py)
that hashes the named gate constants and the implicit literals
(direction-gate threshold, cue-confidence weights, normalization
denominator) into a short tag. Append it to the existing geometry tag
suffix. Any numeric tuning of a registered constant changes the tag and
invalidates the cache. The cost is a single new helper plus the
discipline to add new gate constants to the registry. The benefit is
that the stale comment in `velocity_model.py` becomes accurate.

(b) Continue to invalidate by bumping `SCHEMA_VERSION` and adding the
new version to `GEOMETRY_AFFECTING_SCHEMAS` on every gate-affecting
tune. This is the documented contract today (contract clause C10 in
[TRACK_RUNNER_CONTRACT.md](../../TRACK_RUNNER_CONTRACT.md)
governs the unified schema version). The cost is that every numeric
tune carries a schema bump even when no on-disk layout changes; the
schema-version history grows quickly and the version no longer signals
the semantic event it was intended to signal. The benefit is no new
mechanism.

(c) Add an explicit `--force-resolve` flag to solve and refine that
bypasses the prior-solved partition entirely for one run. Useful as a
testing escape hatch regardless of which of (a) and (b) is chosen, but
not a substitute: it depends on the operator remembering to use it on
every cache-relevant run.

## Recommendation

Adopt option (a) with a centralized `gate_params_hash()` helper, and
keep option (c) available as an operator-facing escape hatch. Option (b)
remains the right mechanism for on-disk layout changes but should not
be used as the routine invalidation lever for gate tuning. The
controlling rationale is contract clause C10: `SCHEMA_VERSION` is the
artifact-version contract, not the algorithm-tuning contract. Using it
as both, as the codebase does today, blurs C10 and makes both
maintainers and the cache less honest.

Recommended next steps for the future plan that consumes this audit:

- Register the named blob-snap constants and the residual-extraction
  defaults in a single module-level tuple inside
  [interval_fingerprint.py](../../../track_runner/interval_fingerprint.py).
- Promote the bare literals (direction-gate threshold, cue-confidence
  weights, strength normalization denominator) to named constants in
  their owning modules and add them to the same registry.
- Update `build_geometry_tag` to append a short, stable hash of the
  registered values to the existing `schema_v<N>` suffix.
- Update the stale comment in
  [velocity_model.py](../../../track_runner/velocity_model.py)
  lines 525 to 527 so the documented behavior matches the code.

## Out of scope

This audit does not propose code changes. It documents a design gap
discovered during the blob-refinement counterfactual fix plan so that
the eventual implementation plan can address it explicitly. It does not
prescribe the hash algorithm, the registry shape, the migration path
for existing cached intervals, or the user-visible diagnostics for a
fingerprint miss. Those decisions belong to a follow-on plan written
against this audit.
