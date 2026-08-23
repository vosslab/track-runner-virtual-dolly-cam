# Refactor residue cleanup and analysis-bin retune

Status tracker for the eighteen-milestone plan covering refactor residue left by
the `blob_walk_v2` absorption, the Stage-4 promotion rework, and the schema-15
persistence change, plus the analysis-bin retune that came out of reviewing the
bin table.

Seventeen of eighteen milestones are complete and M17 is partial; see its row
below. The fast pytest lane reports 3,964 passing after the full sequence.

## M17 scope note

WP-T2 named three behaviors: container validation, artifact-path selection, and
the C7 refine-scope refusal. Only container validation was added here, as
`tests/modes/test_mode_entry_guards.py`.

The C7 refusal was already covered before this work, by
`tests/solver/test_tr_refine_mode.py` at the plan level
(`test_guard_detects_full_solve_fallthrough`) and at the mode level
(`test_mode_refine_rejects_full_resolve_without_writing_when_scores_absent`,
which asserts the RuntimeError and that the artifact survives). Adding a third
assertion of the same behavior would duplicate existing coverage.

Artifact-path selection remains genuinely uncovered. It resolves paths through
`tr_paths` inside mode entry points that also open readers and config, so a fast
test of it would assert its own monkeypatching rather than the selection logic.
Left uncovered deliberately rather than covered by a mock of itself.

## Milestone status

| M | Title | Status | Evidence |
| --- | --- | --- | --- |
| M1 | README quick-start repair | done | zero `-i VIDEO.mp4` remain; status line matches `VERSION` |
| M2 | Module docstring accuracy | done | `cli.py` lists `setup` and `analyze`; gate names its two real importers |
| M3 | Area-rule selector | done | selector returns 3, 3, 2, 2, 2 for the table rows |
| M4 | Reader height threading | done | `open_analysis_reader` accepts `source_height`; solve path forwards it |
| M5 | Bin policy test update | done | both policy tables assert the area mapping |
| M6 | Small-target blob harness | done | `tests/tracking/test_small_target_bin_recovery.py` |
| M7 | Bin table republication | done | six documents carry the new mapping |
| M8 | Dead module removal | done | three modules removed, suite green |
| M9 | Seed-anchoring public entry | done | no production module reads a private re-export |
| M10 | Walker host-module finding | done | import direction verified acyclic; host is `walk_engine.py` |
| M11 | Walker facade retirement | done | `walk_walker.py` gone; walker assertion values unchanged |
| M12 | Walker module documentation | done | both layout documents list all seven walker modules |
| M13 | Blob centroid indexing | done | `tests/tracking/test_walk_status_blob_contract.py` |
| M14 | Solver score indexing | done | `tests/output/test_required_score_fields.py`; suite green |
| M15 | Crop default ownership | done | five values defined once in `tr_crop_math.py` |
| M16 | Residual store coverage | done | `tests/source/test_residual_pre_pass.py` |
| M17 | Mode guard coverage | partial | `tests/modes/test_mode_entry_guards.py` covers container validation only; see note below |
| M18 | Count assertion resolution | done | span counts derive from interval bounds |

## M14 scope note

The reason comments were added to the `.get` calls adjacent to the converted
fields: the two optional score lists, and the optional `interval_score` presence
check in `modes/predictions.py`. Pre-existing `.get` calls elsewhere in these
modules that read unrelated keys (video identity, race start, severity-rank and
tier-count lookups) were left untouched, since they sit outside the
required-versus-optional classification this work resolved.

## Integration gate

- `pytest tests/` passes: 3,964 tests.
- `TARGET_DEFAULT_WIDTH_PX` survives only in changelog history and one archived
  evidence record marked as historical.
- `walk_walker.py`, `regime_policies.py`, `ui/actions.py`, and
  `tools_common.py` are absent.
- `interval_solver._` matches only `_endpoint_chord_span_widths`, which
  `interval_solver` defines itself rather than re-exporting.

## Notes carried forward

- The bin retune invalidates camera-motion artifacts for every previously solved
  video, since those artifacts key on analysis bin. A fresh solve regenerates
  them; this is the designed fail-loud path.
- Measured small-target recovery lives in
  [bin_target_table.md](../reports/bin_target_table.md) alongside the mapping.
- `regime_classifier.py` runs 552 lines to serve one reporting path in
  `modes/analyze.py`. Live, correct, and covered; worth a later look.
- Four zero-caller `interval_solver` re-exports were dropped (`BlockBarColumn`,
  `TaskETAColumn`, `measure_canonical_blend_boxes`, `stitch_trajectories`), and
  `PROMOTION_TIERS` stopped being a re-export because the module reads it
  itself; that read now goes to `interval_analytical` directly. The remaining
  public re-exports all have external callers and stay for a later
  per-call-site pass.
- `tests/solver/test_tr_stage4_parity.py` reads
  `interval_solver._endpoint_chord_span_widths`, a module-private that
  `interval_solver` owns. Outside this plan's scope, which covered re-exported
  privates.
