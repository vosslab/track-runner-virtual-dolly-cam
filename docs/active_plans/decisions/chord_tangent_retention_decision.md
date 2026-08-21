# WP-N3 far-endpoint tangent decision

Date: 2026-08-20

## Decision

Use the shared far-endpoint tangent in production.
`velocity_model.FAR_ENDPOINT_TANGENT_SHARED` is the internal default for
`fit_interval_curves()`. The retained chord arm exists only to keep this
repository-owned decision testable; it is not a CLI or configuration choice.

## Method

The focused velocity-model test generates smooth, non-linear center and
log-size curves at irregular seed frames. It supplies their exact local
derivatives as the immutable `SeedTangentField`, then compares both arms at
the two far Hermite endpoints of every interval:

- **chord:** the retained alternative uses the interval chord at the far end.
- **shared:** uses the generated shared tangent at both ends.

For each endpoint, the error is the absolute difference between the Hermite
derivative and the known curve derivative. The production choice is the arm
with the smaller total error. If errors tie, the shared rule wins because it
uses the one field already required by C6 and adds no special far-endpoint
rule.

The generated non-linear cases select shared directly: its total derivative
error is lower than chord's. This is a semantic derivative comparison, not a
pixel, byte, timing, corpus, or visual-review gate.

## Contract impact

Each interval still receives its immutable seed-only tangent field before
either pass runs. FWD and BWD remain separate directional evaluations and no
pass reads the other's result. Their matching shared endpoints improve the
stitched C6 derivative continuity; scoring still consumes independently built
pass outputs under C9.

## Residual risk

The generated curves prove the far-endpoint Hermite geometry, not runner
identity in a particular video. Residual evidence remains the responsibility
of the later independent solve/scoring stages. No private corpus is required
to keep this decision reproducible.
