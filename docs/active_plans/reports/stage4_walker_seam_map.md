# Stage 4 walker seam map

Turnkey integration map for the future WP-5a/WP-5b coder. This document is
read-only audit output. It MAPS the seam where the windowed walker
(`track_runner/blob_walk/`) attaches as the Stage-4 blob consumer, replacing
the v1 per-frame argmax blob-snap. It does NOT wire it. M4 wiring is held
for external review.

All citations are `file:line` against the repo state at audit time.

## Summary

The v1 blob consumer is `velocity_model._apply_blob_snap`, invoked once per
pass from `propagate_forward_analytical` and `propagate_backward_analytical`,
which are called from `interval_solver.solve_interval_analytical`. Stage 4
re-runs that path with `blob_snap_enabled=True` only on intervals promoted by
`select_promoted_intervals` (low/fair confidence from Stage 3). The walker
entry `walk_walker.walk_one_direction` produces a standalone per-direction
`direction_path`; the future seam replaces the two `snap_pred`-shaped
`forward_path` / `backward_path` lists with walker `direction_path` lists.
Promotion eligibility is computed from Stage-3 scores before any Stage-4
blob work runs, so walker output cannot influence promotion.

## The v1 seam: `_apply_blob_snap`

This is the exact code WP-5 replaces.

| Element | Location |
| --- | --- |
| `_apply_blob_snap` definition | `track_runner/velocity_model.py:607` |
| Per-frame loop | `track_runner/velocity_model.py:700` |
| `residual_motion.observe_blob_at` calls | `track_runner/velocity_model.py:795` and `:807` |
| Gate 1 proximity | `track_runner/velocity_model.py:839` |
| Gate 2 direction | `track_runner/velocity_model.py:845-848` |
| Gate 3 motion-path | `track_runner/velocity_model.py:865-875` (`_motion_path_ok`) |
| Snap blend into raw_pred | `track_runner/velocity_model.py:885-898` |
| `snap_pred` return | `track_runner/velocity_model.py:937` |

State `_apply_blob_snap` reads per frame (all from the frozen `raw` list,
never from prior snap output, per contract C5):

- `raw[i-1]`, `raw[i]`, `raw[i+1]` destructured at `:701`, `:763-764`
  as `raw_prev`/`raw_curr`/`raw_next`.
- local velocity `v_prev`, `v_next`, `v_pred`, `v_pred_mag` computed at
  `:766-769` from raw neighbors only.

Snap math: `delta = blob - raw[t]`, clamped to
`BLOB_SNAP_MAX_SHIFT_FRACTION * h`, then
`snap = raw[t] + alpha_eff * delta` where
`alpha_eff = min(observation.confidence, BLOB_SNAP_ALPHA_MAX)`
(`:887-897`). Accepted only when all three gates pass (`:885`); otherwise the
entry falls through to the pure raw_pred center.

`snap_pred` return shape: a list of state dicts, one per `raw` entry, in raw
order. Each dict has keys `cx`, `cy`, `w`, `h`, `conf`, `source`
(`"propagated"` or `"propagated_with_blob_snap"`), and `blob_gate`
(`"skipped"` / `"absent"` / `"accepted"` / `"rejected"`)
(`:716-724`, `:927-935`).

## How the two passes invoke the seam (FWD/BWD independence)

| Pass | Wrapper | Invocation of `_apply_blob_snap` |
| --- | --- | --- |
| FWD | `propagate_forward_analytical` (`velocity_model.py:941`) | `:974-977`, on `_compute_raw_pred_forward` output (`:973`) |
| BWD | `propagate_backward_analytical` (`velocity_model.py:982`) | `:1016-1019`, on `_compute_raw_pred_backward` output (`:1015`) |

Both wrappers take a required `blob_snap_enabled: bool` and forward `reader`,
`residual_cache`, and `precomputed_store`. Each pass builds its own `raw`
from its own Hermite curve and runs `_apply_blob_snap` on it. No pass reads
the other pass's output. The two snap_pred lists become `forward_path` and
`backward_path` (`interval_solver.py:561-572`, `:581-582`), preserving C9
FWD/BWD independence.

## Run-invariant state available at the seam

`solve_interval_analytical` (`interval_solver.py:466`) is the call site that
owns everything the walker needs. The per-interval blob path already
assembles all of it before calling the two propagators.

| State | Source at the seam (`interval_solver.py`) |
| --- | --- |
| `seed_start`, `seed_end` (interval seed pair) | function args `:467-468` |
| `start_frame`, `end_frame` (frame range) | `:504-505` from the seed pair |
| `scene_transform` | function arg `:469` |
| `all_seeds_scene` | function arg `:470` |
| `fps` | function arg `:471` |
| `reader` (video reader) | function arg `:476` |
| `motion_track` | function arg `:474` |
| `interval_curves` (Hermite fit) | `:518-520` |
| `precomputed_store` (per-interval residual store) | `:546-556` |
| `residual_cache` (per-interval raw residual cache) | `:529` |
| torso/seed geometry (cx,cy,w,h per seed) | inside `seed_start`/`seed_end` dicts |

`stride` for the residual reader is derived from `fps` via
`residual_motion.resolve_stride(fps)` (`:542`), matching what the walker
needs.

## Walker entry: inputs and return

`walk_walker.walk_one_direction` (`track_runner/blob_walk/walk_walker.py:1267`)
is the per-direction walker. The standalone driver
`walk_driver.run_interval_walk`
(`tools/blob_walk_v2/walk_driver.py:657`) calls it twice: FWD with `sign=1`
(`:764-781`) and BWD with `sign=-1` (`:789-806`), one `DebugLogWriter` per
direction, fully independent (C9).

Walker signature (relevant inputs): `seed`, `neighbor_seed_frame`, `reader`,
`scene_transform`, `fps`, `stride`, `sign`, plus optional
`neighbor_seed_cx/cy/w/h` for diagnostics and per-frame size interpolation
(`walk_walker.py:1267-1283`).

The candidate lattice is NOT a separate input. Inside the walker, each frame
calls `observe_blob_at` and reads `trace.corridor_blobs` as that frame's
candidate list (`walk_walker.py:9-11` docstring; the geometric-ROI corridor
filter has already run during extraction). The 9-frame rolling buffer +
Viterbi DP consumes those per-frame candidate lists. So the walker needs the
same `reader` + `scene_transform` + `residual` plumbing that
`_apply_blob_snap` already has at the seam; it does not need a pre-built
lattice handed in.

Walker return: a `WalkSummary` dataclass (`walk_walker.py:245`,
`:284-299`). The load-bearing field for wiring is
`direction_path`: a list of `{frame_index, cx, cy, w, h, conf}` dicts in
PROCESSED pixels, one per emitted walk frame (`:264-271`, `:298`). `w`/`h`
are seed-derived geometry (the walker solves position, not size); `conf` is
the deterministic distance-from-anchor decay (`conf_from_anchor`,
`:82-98`). The summary also carries per-status counts and
`direction_trace_map` (diagnostics).

## WalkerInputBundle field-to-source map

This is the turnkey table for the WP-5a `WalkerInputBundle`. Each field maps
to a variable already live at the `_apply_blob_snap` seam inside
`solve_interval_analytical`.

| WalkerInputBundle field | walk_one_direction param | Source already at the seam |
| --- | --- | --- |
| seed (pass anchor) | `seed` | FWD: `seed_start`; BWD: `seed_end` (`interval_solver.py:467-468`) |
| frame range (target) | `neighbor_seed_frame` | FWD: `end_frame`; BWD: `start_frame` (`interval_solver.py:504-505`) |
| direction | `sign` | `+1` FWD wrapper, `-1` BWD wrapper (mirrors `propagate_forward/backward_analytical`) |
| reader | `reader` | `reader` arg (`interval_solver.py:476`) |
| scene transform | `scene_transform` | `scene_transform` arg (`interval_solver.py:469`) |
| fps | `fps` | `fps` arg (`interval_solver.py:471`) |
| stride | `stride` | `residual_motion.resolve_stride(fps)` (`interval_solver.py:542`) |
| torso-unit scale | (consumed inside walker via seed w; C2 thresholds in torso-width units) | seed `w`/`h` in `seed_start`/`seed_end` dicts |
| candidate lattice | (not a param) | built inside walker from `observe_blob_at` -> `corridor_blobs`; needs only reader + scene_transform + residual plumbing already present |
| neighbor seed geometry (diag + size interp) | `neighbor_seed_cx/cy/w/h` | the opposite seed dict's cx/cy/w/h |

## Replacing snap_pred with the walker path

| Aspect | v1 `_apply_blob_snap` | Walker `walk_one_direction` |
| --- | --- | --- |
| Returns | `list` of state dicts (`cx,cy,w,h,conf,source,blob_gate`), one per raw frame, raw order | `WalkSummary` whose `direction_path` is a `list` of `{frame_index,cx,cy,w,h,conf}` dicts, one per emitted frame |
| Coordinate space | PROCESSED (then used as pixel path) | PROCESSED (`walk_walker.py:264-271`); source projection is a downstream concern |
| Per-frame selection | per-frame argmax + 3 gates | window-level Viterbi over candidate lattice |

Wiring sketch (held for WP-5b, not done here): replace the two
`velocity_model.propagate_*_analytical` calls
(`interval_solver.py:561-572`) so that, when blob is enabled, FWD and BWD
each produce a walker `direction_path` instead of a snap_pred list. Project
each `direction_path` to the same `forward_path` / `backward_path` shape
(list of state dicts aligned frame-by-frame). Downstream is unchanged:

- `blend_paths(forward_path, backward_path)` (`interval_solver.py:589`,
  defined `:368`) consumes the two paths as-is.
- `scoring.compute_agreement(forward_path, backward_path)`
  (`scoring.py:202`) and `score_interval_analytical`
  (`interval_solver.py:595-602`) consume the two independent paths as-is.

So long as the walker's two `direction_path` outputs are projected into the
existing aligned state-dict shape, `compute_agreement` and `blend_paths`
need no change. The walker's FWD path and BWD path remain independent inputs
to agreement (C9 preserved), exactly as the two snap_pred lists are today.

Note that `run_interval_walk` already demonstrates the projection target: it
returns `solved_entry = {start_frame, end_frame, forward_path,
backward_path, blended_path}` in SOURCE coords
(`walk_driver.py:703-710`). WP-5b should produce the PROCESSED-pixel aligned
path the rest of the analytical solver expects, not the source-projected
driver shape; the driver's projection helper `_project_path_to_source`
(`walk_driver.py:170`) is a render-time concern, not the solver seam.

## Promotion eligibility (Stage-3-first, confirmed)

Promotion is computed entirely from Stage-3 scores before any Stage-4 blob
work, so walker output cannot influence which intervals are promoted.

| Element | Location |
| --- | --- |
| `PROMOTION_TIERS = {"low", "fair"}` | `interval_solver.py:39` |
| `select_promoted_intervals` | `interval_solver.py:43` |
| reads `interval_score["confidence_tier"]` from Stage-3 results | `interval_solver.py:82-85` |
| pre-race intervals excluded (C4) | `interval_solver.py:80-81` |
| Stage-4 dispatch `_dispatch_blob_pass` | `interval_solver.py:~1374` (def), tasks built with `blob_snap_enabled=True` `:1418` |
| in-process Stage-4 call (`blob_snap_enabled=True`) | `interval_solver.py:1456-1464` |
| promotion call site | `interval_solver.py:1746-1751` |

The Stage-3 pass dispatches with `blob_snap_enabled=False`
(`solve_queue.py:781-789`), produces confidence tiers, and only then does
`select_promoted_intervals` choose low/fair intervals for the Stage-4 blob
pass. The walker, as the Stage-4 consumer, runs strictly after promotion is
decided. This ordering is structural and must be preserved by WP-5b.

## Open questions and risks for WP-5a

- Path length alignment. `_apply_blob_snap` returns exactly one entry per
  raw frame (full interval span, endpoints included). The walker emits one
  row per emitted walk frame and may stop early
  (`stop_reason` in `hit_neighbor_seed`/`boundary`/`loop_guard`,
  `walk_walker.py:251`). `blend_paths` and `compute_agreement` use
  `min(len(forward_path), len(backward_path))`
  (`interval_solver.py:392`, `scoring.py:218`), so unequal lengths do not
  crash, but a short walker path silently shrinks the scored span. WP-5a
  must decide whether to pad walker paths to the full interval span (gap
  fill via `interpolated`/`extrapolated` statuses already produced by the
  walker) before projecting to the aligned shape.

- snap_pred consumers beyond blend/score. `_stamp_blob_coverage`
  (`interval_solver.py:610`) reads the `blob_gate` field
  (`"accepted"`/`"rejected"`/`"absent"`/`"skipped"`) that the walker does
  NOT produce; the walker has a five-value status enum instead
  (`accepted`/`interpolated`/`extrapolated`/`soft_miss_no_blob`/
  `soft_miss_no_path`). WP-5a should either map walker statuses onto the
  coverage diagnostic or replace `_stamp_blob_coverage` for the walker
  path. This is a diagnostic field, not a scoring input, so it is lower
  risk, but it will throw a KeyError if left unmapped.

- Driver vs solver entry. `run_interval_walk`
  (`walk_driver.py:657`) is a tile-rendering / CSV-writing standalone
  driver that also writes per-interval debug logs and PNG tiles. The
  solver seam must NOT call `run_interval_walk` directly; it should call
  `walk_walker.walk_one_direction` twice (the way the driver does at
  `:764` and `:789`) without the rendering/CSV side effects. WP-5a should
  define the bundle so the solver path bypasses all I/O the driver does.

- Coordinate space. Walker `direction_path` is PROCESSED
  (`walk_walker.py:264-271`). `_apply_blob_snap` output is also treated as
  PROCESSED-pixel by the solver. Confirm bin_factor handling matches:
  the walker steps in PROCESSED and the snap path comments note
  `source==processed` at `bin_factor==1` (`velocity_model.py:791`).
  At `bin_factor>1` the two must agree on space before
  `blend_paths`/`compute_agreement`. The walker has an explicit
  PROCESSED-seed boundary guard (`walk_walker.py:1325-1343`); the solver
  must feed PROCESSED seeds, which it already has via `scene_transform`.

- precomputed_store / residual_cache reuse. The walker keeps its own
  `residual_cache = {}` scoped to the walk (`walk_walker.py:1379`) and does
  not currently accept the solver's `precomputed_store`
  (`interval_solver.py:546-556`). WP-5a/WP-5b should decide whether to
  thread the existing per-interval `precomputed_store` into the walker to
  preserve the Stage-4 sequential-read optimization, or accept the walker
  building its own cache (potential perf regression on HEVC HDR source).
