# TODO

This backlog contains only current product ideas. Historical experiments and
retired tooling belong in the changelog or archive, not in the active backlog.

## Product ideas

### Detect race end frame during solve

Explore an optional race-end estimate only when it supports a concrete user
workflow. Any implementation must use current scene-space trajectory data and
remain advisory until a user confirms the result.

### Motion-cue seed recommendation

Use the current residual-motion evidence to suggest review frames in
low-confidence intervals. Suggestions must remain advisory: a human owns seed
identity and all persisted seed geometry.
