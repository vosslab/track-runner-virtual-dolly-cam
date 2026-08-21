# Offline dolly adoption decision

## Decision

`crop_mode: dolly` is the shipped default.

The decision is repository-local. Permanent tests cover the shipped default,
whole-path containment, bounded convergence, and safe smooth fallback using
inline data. They do not rank crop algorithms on one retained trajectory: that
would turn an arbitrary sample into a permanent product gate.

## Scope and provenance

This decision uses no private video, mounted corpus, generated image, manual
review, pixel comparison, or wall-clock threshold. Generated visual samples may
be useful for diagnosis but are temporary and never adoption gates.

Explicit user configuration remains authoritative: `smooth` and
`direct_center` are still supported. A dolly containment solve that does not
converge returns its established `smooth` fallback and records that provenance.
