# Progress handoff - 2026-08-20

## Purpose

This is the restart point for the seven goals in
[interaction_shell_and_trajectory_truth.md](../active/interaction_shell_and_trajectory_truth.md).
The plan's goals and functional scope are unchanged. Its completion method is now repository-local
and autonomous: private-video receipts, historical snapshots, manual review, arbitrary timing
thresholds, and Git staging are not gates.

## Operating rules

- Keep Stage 1 estimation behavior and algorithm unchanged and outside scope. The former affine/tail
  experiment was reverted and is not accepted work; annotation-only maintenance is separate.
- Skip Jason permanently. Do not recover, select, diagnose, or run it.
- Keep permanent tests offline, deterministic, and self-contained.
- Use inline/generated inputs, fake readers, deterministic residual fields, `tmp_path`, and small
  retained numeric fixtures.
- Treat local videos and `/private/tmp` receipts as optional diagnostics only.
- Apply exact equality only to exact contracts, especially C3 seed geometry and schema round trips.
- Use semantic properties and relative improvement for output-changing algorithms.
- Do not use arbitrary millisecond thresholds or require manual visual acceptance.
- This plan made no change to `docs/GRAPH_REPORT.md`; leave it outside this plan's work.
- Do not stage, commit, or modify the Git index. All changes remain in the working tree.
- Keep the separate all-functions-typed request independent from the seven product goals.

## Current implementation

### M1 seed truth

- Fresh Hermite and walker results stamp visible/partial seed geometry before persistence.
- Cache reuse applies the same stamp before completion callbacks and stitching.
- Portable cache-reuse tests accept deliberate endpoint disagreement, exact authored geometry, and
  unchanged approximate/not-in-frame behavior.

### M2 confidence ownership

- `track_runner/trajectory_confidence.py` owns confidence and agreement.
- Solve, analyze, encode, crop, and scoring consume the owner boundary.
- Cache reconstruction reapplies owner confidence before anchoring/crop consumers.

### M3 blend commitment

- `track_runner/blend_commitment.py` owns disagreement-run commitment.
- The production canonical residual/DoG/in-box evaluator is shared by selection and diagnostics.
- Transition feasibility is scoped to entry/internal/exit edges touched by committed runs.
- Portable ground-truth tests cover known-runner FWD/BWD ordering, ties, unavailable evidence,
  transition continuity, and debug overlays.
- The rejected private-corpus v5 receipt remains historical only and no longer blocks M3.

### M4 memory and walker cost

- Residual gray buffers use `uint8` and the result store has a 512 MiB byte cap.
- The shared BGR/float32 frame cache enforces its 40-entry limit after every
  neighbor insertion, including a full-gray-cache residual calculation.
- Per-ROI residual/DoG observations now use a separate 512 MiB byte-accounted LRU,
  so interval length cannot grow that retained image cache without bound.
- Startup pre-pass reads only requested neighbor ranges.
- Ordinary Stage-3/4/5 pools report driver/worker RSS and cache counters during normal use.
- Walker geometry defaults fail loudly and cost reuse work is present.
- Solve automatic worker count uses explicit available, parent, worker, and reserve terms through
  a pure tested policy. The worker term is a source-level allocation ledger; the reserve is one
  additional controlled-allocation window, while decoder/runtime RSS remains telemetry. Independent
  review accepted the bounded caches, ledger, and policy without local-video measurements.

### M5 shared tangents

- Tangent support uses frame-distance windows.
- One immutable tangent per shared seed is computed before dispatch.
- FWD/BWD propagation uses one direction-parameterized implementation.
- Generated curves with known derivatives accept the shared-tangent/chord decision; no historical
  comparator or runtime selector is required.

### M6 persistent interaction shell

- `AnnotationSession` owns session/controller state and in-process Seed/Target/Edit switching.
- `FrameSource` owns reader lifetime off the Qt event loop and coalesces newest requests.
- Heat work is asynchronous and stale results are ignored.
- Keymap, render-state, GUI-feedback, Target-label, and typed SOURCE prediction work are present.
- Completion uses offscreen Qt tests with fake readers and generated frames. Historical 4K receipts
  are optional diagnostics and no heartbeat threshold is required.

### M7 offline dolly path

- The whole-path solver and containment integration are present and retain convergence/fallback
  provenance.
- The repository-owned numeric trajectory ran `direct_center`, `smooth`, and `dolly` from the same
  in-memory inputs. Dolly converged without fallback, remained contained, had no greater lag on
  either axis, and had lower p95 center acceleration than both baselines.
- The automated rule therefore selected `dolly` as the shipped default. No private video, manual
  review, or user decision was involved.

### M8 ownership cleanup

- Mode bodies live under `track_runner/modes/`; CLI parsing and dispatch remain in `cli.py`.
- Dead confidence/occlusion/propagator code exposed by the plan is removed.
- Direct real-parser and `cli.main()` dispatch tests accept the routing boundary; help behavior is
  tested semantically, not as byte-identical output.

### Separate typing maintenance

- The explicit request to type all existing functions has been implemented across the repository
  in the working tree.
- A Git-independent AST scan of the whole working tree reports 238 Python files, zero missing
  argument/return annotations, and zero `typing` imports. This receipt does not depend on the
  tracked-file count.
- It remains independent maintenance, not a semantic gate for M1-M8, and does not justify
  unrelated refactors.

## Completion state

All seven original product goals are accepted by portable implementation, focused tests, and fresh
independent review. M1 covers cache reuse; M2, M3, and M6 pass the portable audit; M4 accepts the
source-ledger worker policy; M5 accepts the generated known-derivative decision; M7 selects the
`dolly` default; and M8 accepts real parser plus `cli.main()` dispatch.

No plan work remains. Keep Stage 1 estimation behavior and algorithm unchanged and outside scope,
keep Jason permanently skipped, and leave all files unstaged.
Do not reopen this work for private-media/manual/network/timing/pixel/byte/Git-index evidence.

Import-requirements discovery now recognizes repo-local working-tree modules/packages without
staging, retains tracked scan scope, and still rejects undeclared external imports. Its independent
review accepts 603 focused checks, including symlink/outside-root containment. Markdown-link
discovery now accepts live untracked local targets while retaining Git-scoped source collection and
rejecting external file/directory symlink escapes. Its independent review accepts 156 Markdown
tests plus 400 typing/pyflakes checks. Neither hygiene repair affects accepted product behavior or
requires staging.

The pre-audit completion snapshot passed 272 focused product tests and 1,332 hygiene checks. The
later user-promoted source-file ownership maintenance work is complete: a direct
`rg`/`wc` scan finds every worktree Python file below the exclusive 1,000-line
limit, with the largest at 994 lines, and the portable suite passes 4,559 tests.
Extractions separate interval progress/analysis/seed anchoring, residual
frames/blob traces, walker engine/observer/summary, torso-box I/O, crop
math/direct/controller, Encode reports/audio/pool control, UI heat/edit/status
support, mode video/seed support, and walk-report tools. They make no output
or performance claim. `camera_motion_artifact` owns artifacts/cache only, so
Stage 1 estimation algorithms remain unchanged. Review also found and corrected
canonical approximate/legacy-obstructed seed eligibility with a real NPZ
identity round-trip, and parallel Encode quit now terminates and reaps its
pool. Refreshed Graphify reports 2,306 nodes, 3,450 edges, and 164 communities,
with a 44.6x average token reduction benchmark. Jason, private-video, manual,
and staging gates were not used.

## Historical diagnostics

Earlier local receipts and reports remain available for debugging, including the M2 attribution,
M3 pilot/overlays, M4 bin-1 memory run, and 4K UI probes. They can inform a diagnosis but cannot
determine milestone status because a normal repository clone cannot reproduce them.

The six quarantined Stage-1 experimental motion artifacts remain under
`/private/tmp/track-runner-reverted-stage1-artifacts-20260820/` for forensic recovery. Their
canonical paths are absent, restored production code treats the experimental labels as stale, and
this plan does not use them.

## Restart checks

Run only the checks relevant to the next package:

```bash
source source_me.sh
python3 -m pytest <focused test files>
python3 -m pytest tests/test_function_typing.py tests/test_pyflakes_code_lint.py
python3 -m pytest tests/test_ascii_compliance.py tests/test_indentation.py
python3 -m pytest tests/test_markdown_links.py
```

`pytest tests/` is a health report, not a product-milestone gate. The completed
portable run passes 4,559 tests, including the source-line-limit checks.
Version-control preparation belongs to the human.
