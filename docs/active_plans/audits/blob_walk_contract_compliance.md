# Blob walk contract compliance audit

Read-only audit of the relocated windowed walker under
`track_runner/blob_walk/` (M1-M3 landing) against contract clauses C2, C5,
C6, C9, C10 and the "Anti-pattern: chained blob state" rule in
`docs/TRACK_RUNNER_DESIGN.md`. No code was edited. The walker is not yet
wired into Stage 4 (M4 pending); this audit confirms the contracts hold
before integration.

Files audited:

- `track_runner/blob_walk/walk_viterbi.py`
- `track_runner/blob_walk/walk_motion_gate.py`
- `track_runner/blob_walk/walk_walker.py`
- `track_runner/blob_walk/walk_status.py`
- `track_runner/blob_walk/walk_debug_log.py`
- `track_runner/blob_walk/walk_io.py`

## Per-clause verdict

| Clause | Subject | Verdict | Key evidence |
| --- | --- | --- | --- |
| C2 | Spatial thresholds in torso-width units | PASS (one ambiguous) | walk_viterbi.py:72-74, 199-204; walk_motion_gate.py:71-105, 228-245 |
| C5 | Interval-scoped residual cache | PASS | walk_walker.py:1379 |
| C6 | Window buffer scoped to one interval | PASS | walk_walker.py:1388 (local), 1007-1020 |
| C9 | FWD/BWD pass-local state | PASS | walk_walker.py:1267-1283, 1354-1392 |
| C10 | One SCHEMA_VERSION | PASS | walk_debug_log.py:49 |
| Anti-pattern | No chained blob state | PASS | grep clean; walk_walker.py:377-380 |

## C2: spatial constants and unit classification

Every runner-relative spatial threshold in the Viterbi cost terms and the
motion gate is derived from torso width (W), scaled to pixels at the use
site by the per-frame torso_w. Listing each constant:

| Constant | File:line | Unit | Classification |
| --- | --- | --- | --- |
| `MAX_RUNNER_SPEED_W_PER_S = 30.0` | walk_motion_gate.py:71 | W/s | torso-unit, OK |
| `MIN_RUNNER_SPEED_W_PER_S = 7.3` | walk_motion_gate.py:75 | W/s | torso-unit, OK |
| `BOOTSTRAP_UNCERTAINTY_W = 0.30` | walk_motion_gate.py:83 | W | torso-unit, OK |
| `ABSOLUTE_MAX_JUMP_W = 1.5` | walk_motion_gate.py:93 | W | torso-unit, OK |
| `VELOCITY_TOLERANCE = 1.75` | walk_motion_gate.py:97 | unitless ratio | OK (multiplier) |
| `DT_GATE_CAP = 3` | walk_motion_gate.py:100 | frames | OK (time, not space) |
| `MEASUREMENT_ALLOWANCE_W = 0.10` | walk_motion_gate.py:105 | W | torso-unit, OK |
| `WEIGHT_DISPLACEMENT = 1.0` | walk_viterbi.py:32 | cost/W | OK (weights W-unit disp) |
| `WEIGHT_MAG_VAR = 0.5` | walk_viterbi.py:34 | weight | OK (unitless weight) |
| `WEIGHT_ANGLE_VAR = 0.3` | walk_viterbi.py:36 | weight | OK (radians-based) |
| `WEIGHT_EVIDENCE = -0.05` | walk_viterbi.py:38 | weight | OK (weights integrated_mag) |
| `SKIP_COST = 2.0` | walk_viterbi.py:41 | cost | OK (abstract cost) |
| `dog_diameter_override = 0.7 * seed_w` | walk_walker.py:659 | W-derived px | torso-unit, OK |
| acceptance box `0.5 * seed_w`, `0.75 * seed_h` | walk_walker.py:624-627 | W/H-derived px | torso-unit, OK |
| `roi_pad = max(20, seed_w)` | walk_walker.py:640 | px floor + W | ambiguous, see below |

Derived spatial quantities (use sites, all torso-unit):

- Viterbi displacement cap: `max_jump_px = (MAX_RUNNER_SPEED_W_PER_S/fps +
  BOOTSTRAP_UNCERTAINTY_W) * torso_w` at walk_viterbi.py:72-74 and
  226-227. Pure W-units, scaled to px by torso_w. OK.
- Viterbi displacement cost: `disp_w = disp_px / torso_w` at
  walk_viterbi.py:203-204; the cost term is in W-units. OK.
- Motion-gate `per_step_cap = max_runner_jump_per_frame(fps) * torso_w *
  dt_for_gate` (walk_motion_gate.py:238-239), `absolute_cap =
  ABSOLUTE_MAX_JUMP_W * torso_w` (242), `measurement_allowance =
  MEASUREMENT_ALLOWANCE_W * torso_w` (228), `radial_allowance =
  torso_w_drift_frac * torso_w` (231). All W-scaled. OK.

No raw-pixel runner-relative threshold was found in the Viterbi cost terms
or the motion gate. The `+inf` edge prune (walk_viterbi.py:199-200) keys
off the W-scaled `max_jump_px`.

### Ambiguous, needs author confirmation

- `roi_pad = max(20, seed_w)` (walk_walker.py:640). The `seed_w` term is
  torso-unit. The literal `20` is a raw-pixel floor on the ROI padding.
  This is the pixel extraction window handed to `observe_blob_at`, not a
  runner-relative acceptance or gating threshold (acceptance uses
  `0.5*seed_w` / `0.75*seed_h`, line 624-627, and the ROI is clamped to
  frame bounds at 641-644). It reads as a "low-level raster window about
  the image" quantity, which C2 permits, but a `20`-px floor on a search
  window is borderline: at the ~10 px-tall extreme of the runner range
  noted in C2, a 20 px pad is ~2 torso widths of extraction margin. Flag
  for the author: confirm whether the `20` floor is intended as an
  image-raster minimum (allowed) or should be expressed as a torso
  multiple. Not a FAIL because it does not gate any runner-geometry
  decision; it only sizes the residual ROI.

## C5: per-interval residual pre-pass / cache

The residual cache is created fresh inside `walk_one_direction` at
walk_walker.py:1379 (`residual_cache = {}`) with the explicit comment
"scoped to this walk; not shared across intervals per C6". It is passed
down to `_compute_roi_and_observe` (param at 576, used at 668) and never
returned, module-global, or shared between calls. The cache holds only
image-derived observe results, not accepted blobs or gate outcomes.
PASS.

## C6: interval independence of the window buffer

`window_buffer = collections.deque()` is a local in `walk_one_direction`
(walk_walker.py:1388), created per call. A single `walk_one_direction`
call walks one seed-to-neighbor span: the loop in `_run_windowed_steps`
advances `frame_f = seed_frame + sign * step * dt` and terminates when
`frame_f == neighbor_seed_frame` (walk_walker.py:1007-1020). No buffer
entry crosses an interval boundary: the deque holds only frames between
the pass's seed and its neighbor seed, and is destroyed when the call
returns. The window length is bounded by `WALKER_WINDOW_FRAMES = 9`
(walk_walker.py:59, drained at 1056). PASS.

## C9: FWD/BWD pass-local state

`walk_one_direction` (walk_walker.py:1267-1283) takes a `sign` parameter
(+1 FWD, -1 BWD). Each invocation owns its own `accepts`,
`visited_frames`, `direction_path`, `direction_trace_map`,
`residual_cache`, `last_accepted_cx/cy`, and `window_buffer`, all
declared as locals at lines 1354-1392. Nothing reads a sibling pass's
trajectory: the only positional anchor, `last_accepted_cx/cy`, is seeded
from this pass's own seed (1391-1392) and updated only from this pass's
Viterbi-selected path (walk_walker.py:377-380). The confidence helper
`conf_from_anchor` decays from the pass's own seed (82-96). There is no
parameter or global by which the forward call could read backward state
or vice versa, and the blended/stitched output is not consumed here.
PASS.

## Anti-pattern: chained blob state

`grep -rnE "last_blob|prev_accepted_blob|miss_count|_chain_|chain_break"`
over `track_runner/blob_walk/` returns only the two comment lines in
walk_walker.py:30-31 that forbid such variables. No such variable is
defined.

The only cross-frame state is:

- the bounded 9-frame `window_buffer` of raw candidate lists
  (walk_walker.py:1388, 59), which is image-derived raw data, the
  explicitly allowed exception; and
- `last_accepted_cx/cy`, a single ROI-anchor coordinate (not a blob dict,
  not a filtered-blob list, not a gate outcome). It is updated from the
  Viterbi path position at walk_walker.py:377-380 and used only to place
  the next ROI (walk_walker.py:1031-1032, `pred_cx = last_accepted_cx`).
  It does not enter the Viterbi cost: `walk_viterbi.select_path` /
  `transition_cost` read only candidate `centroid_x`, `centroid_y`, and
  `integrated_mag` from the window lists (walk_viterbi.py:95-127,
  194-208). Viterbi output therefore depends only on the raw window
  candidates, not on accumulated accept memory.

No accepted-blob, filtered-blob-list, gate-outcome, or snap_pred memory
accumulates across frames. The motion gate (`walk_motion_gate.evaluate`)
is pure per-call math and is not even invoked in the v13 windowed loop
(only its W-unit constants are imported by walk_viterbi.py:24). PASS.

## C10: unified SCHEMA_VERSION

`walk_debug_log.py:49` sets `SCHEMA_VERSION = tr_schema.SCHEMA_VERSION`
(imported at line 38). A repo grep for `SCHEMA_VERSION` and any
`*_VERSION` constant across `track_runner/blob_walk/` finds no second
schema constant: the only assignment is the unified read at line 49; the
remaining hits are docstring/comment references to the v11/v12/v13
column-meaning history (walk_debug_log.py:11-30, 42-48) and the
`WALKER_WINDOW_FRAMES` window count, which is not a schema constant. The
verdict-CSV `HEADER` tuple (walk_debug_log.py:57-119) is the column
layout, not a version integer. PASS.

## Verdict summary

All five audited clauses PASS, plus the chained-blob-state anti-pattern
PASS. One C2 item is flagged ambiguous and needs author confirmation:
the raw `20`-px floor in `roi_pad = max(20, seed_w)`
(walk_walker.py:640). It does not gate any runner-geometry decision (it
only sizes the residual extraction ROI), so it is not a FAIL, but the
author should confirm whether the floor belongs in the C2 "allowed
image-raster" bucket or should be expressed as a torso multiple.
