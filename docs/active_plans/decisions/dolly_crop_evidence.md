# Offline dolly adoption decision

## Decision

`crop_mode: dolly` is the shipped default.

The decision is automatic and repository-local. The retained numeric fixture
`tests/fixtures/m7_img3823_crop_trajectory.npz` supplies one trajectory and its
metric target data. `tests/test_tr_crop_dolly.py` runs `direct_center`, `smooth`,
and `dolly` from the same in-memory trajectory, configuration, and video metadata.
The default changes only if dolly:

- converges without its smooth fallback;
- keeps every returned rectangle within the source bounds;
- has no greater absolute center lag than each baseline on both axes; and
- has strictly lower torso-width-normalized p95 center acceleration than each
  baseline.

The current fixture passes all four conditions, so the test requires the
shipped template and missing-mode path to use `dolly`. If any condition fails in
the future, the test reports which rule is false; the deterministic rejection
is to retain `direct_center` as the default until a replacement rule outcome is
committed.

## Scope and provenance

This decision uses no private video, mounted corpus, generated image, manual
review, pixel comparison, or wall-clock threshold. The fixture is numeric and
small enough to run in the normal test suite. Generated visual samples may be
useful for diagnosis but are not adoption gates.

Explicit user configuration remains authoritative: `smooth` and
`direct_center` are still supported. A dolly containment solve that does not
converge returns its established `smooth` fallback and records that provenance.
