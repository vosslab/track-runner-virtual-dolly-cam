# Plan: Analyze-mode diagnostic HTML report (`--plot`)

## Context

Encode-mode crop instability ("zoom pumping," "drift," "bouncing") is hard
to diagnose from a final encoded video. The crop trajectory's input
signals (torso geometry, camera motion, runner ground speed) are computed
during `analyze` but never visualized. The recent
`torso_height_multiple` debugging session
([CHANGELOG.md](../CHANGELOG.md)
2026-05-02) would have been a five-second look at a zoom plot.

This plan ships a single self-contained HTML diagnostic report per
video. Panels are HTML5 canvas charts driven by embedded JSON and a
small inlined vanilla-JavaScript renderer. No external network
dependency, no runtime plotting library, no separate PNG/SVG/JSON
files. The source draft
[ANALYZE_MODE_PLOTS.md](ANALYZE_MODE_PLOTS.md)
proposed PNGs and is superseded; it is replaced in place during M3.

## Design philosophy

- **One artifact per video.** A dozen videos becomes twelve HTML files,
  not forty-eight panel files.
- **Self-contained.** No CDN, no external script tag, no separate JSON
  or SVG files. The HTML opens correctly years later from an archived
  folder, on a plane, in a lab.
- **No runtime plotting dependency.** matplotlib stays dev-only; the
  renderer is hand-written vanilla JS embedded in the HTML.
- **Encode-quality only.** Every panel is chosen to give the user
  insight into the encoded video. Tracker-quality signals (FWD/BWD
  agreement, blob coverage, regime-classifier output) are out of scope
  and would belong in a separate report if ever needed.
- **Output-only feature.** No solver, encoder, or crop-trajectory
  algorithm changes. If a panel reveals a bug, the fix lands in a
  separate plan.
- **Reuse, do not re-derive.** Every panel consumes data already
  present in `_mode_analyze` (trajectory, crop rects, fps) or
  accessible through stable loaders
  (`load_active_camera_motion_or_fail`, `SceneTransform.pixel_to_scene`).
  No duplicate extraction pipeline.
- **Graceful degradation, surfaced at the top.** When camera-motion
  data or scene transform is unavailable, the report's warnings section
  names what was skipped and why. Panels that have data still render.
- **Behavioral tests only.** Tests assert HTML structural shape,
  embedded-JSON schema and array lengths, panel IDs, and warning text.
  Tests never lock in pixel layout, rendered output, or JS runtime
  behavior (which would require a headless browser).
- **Argparse minimalism.** Single binary toggle `--plot`. No format
  flag, no per-panel toggle.

## Objective

Ship an opt-in `-p`/`--plot` flag on the `analyze` subcommand that
writes one self-contained HTML diagnostic report per video, co-located
with `tr_config/<stem>.encode_analysis.yaml`. The HTML embeds:

- a warnings/summary block at the top
- a JSON data block describing the four panels
- four `<canvas>` panel containers
- a small inlined vanilla-JS renderer (line charts, twin axes,
  hover-tooltip, drag-zoom, double-click reset, checkbox series toggle,
  synced x-range across panels, nan -> gaps)

Behavior is covered by unit and integration tests; docs reflect the new
flag and report.

## Scope

- New module `track_runner/analyze_report.py` with one public entry
  point `write_analyze_report(...)` plus private builders
  `_build_analyze_report_data(...)` and `_write_analyze_report_html(...)`,
  per-panel data helpers, projection helper, and smoothing helper.
- New asset file `data/js/analyze_report_renderer.js` (introduces the
  top-level `data/` directory and the `data/js/` subfolder; new
  convention for non-Python build-time assets in this repo). Contains
  the vanilla-JS renderer. Loaded as text by the Python module at
  write time and inlined into the HTML output. The output HTML
  remains fully self-contained: it embeds the JS verbatim and has no
  on-disk dependency on the asset file once written.
- New `-p`/`--plot` flag on the `analyze` subparser in
  `track_runner/cli_args.py`.
- Data plumbing in `track_runner/cli.py` `_mode_analyze` to call the
  new module after the YAML report is written, with try/RuntimeError
  protection around `load_active_camera_motion_or_fail`. Warnings
  accumulated during the call are passed into the report and surfaced
  at its top.
- No change to `pip_requirements.txt`. matplotlib stays in
  `pip_requirements-dev.txt` only (and is no longer needed for the
  report path itself; if other dev tooling uses it, that is unaffected).
- Tests in `tests/test_tr_analyze_report.py` covering happy path, the
  two graceful-degradation paths, the constant-velocity round-trip,
  the JSON schema shape, panel-ID presence in the HTML, and the
  no-external-URL contract.
- Doc updates in `docs/modes/ANALYZE.md` (re-run
  `tools/refresh_mode_docs.py` + a hand-written paragraph),
  `docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md`, `docs/ENCODE_DESIGN.md`
  cross-link, and a `docs/CHANGELOG.md` entry.

## Non-goals

- PNG output. Not in this round; not on a follow-up roadmap.
- SVG output. Not in this round.
- A separate JSON or data sidecar file. The JSON is embedded in the
  HTML.
- An aggregated "dashboard" composite panel. The HTML page is the
  dashboard; panels appear in a clear order with short interpretive
  notes between them.
- Plotly, Chart.js, D3, or any other external charting library.
- Any CDN reference; the HTML must work offline.
- Lasso select, annotations, export buttons, themes, per-panel
  configuration UI.
- Replacing or restructuring the YAML report; the HTML report links
  to it and supplements it.
- Adding `--plot` to `solve`, `refine`, `encode`, or any other mode.

## Current state summary

- `analyze` is a mainline user-facing mode (entry point: `_mode_analyze`
  at `track_runner/cli.py:1843`). It already loads `trajectory`,
  computes `crop_rects` via `tr_crop.direct_center_crop_trajectory`,
  reads `fps` from `diag_data`, and constructs `scene_transform` for
  the regime classifier.
- `MotionTrack` (`track_runner/camera_motion.py:62-81`) exposes per-frame
  `dx`, `dy`, `scale`, `quality` arrays. Loader
  `load_active_camera_motion_or_fail` is at line 1146 and follows the
  active-marker pattern documented in
  [CHANGELOG.md](../CHANGELOG.md)
  2026-05-02.
- `SceneTransform.pixel_to_scene` is at `track_runner/scene_coords.py:43`.
- `matplotlib` is in `pip_requirements-dev.txt` only. No production
  code imports matplotlib. This plan does not change that.
- `tools/refresh_mode_docs.py` regenerates the `<!-- BEGIN AUTO HELP -->`
  block in mode docs.

## Graphs displayed

This is an encode-quality diagnostic. Every panel is chosen to explain
visible encode behavior, not tracker internals. The report writes one
self-contained HTML file at
`tr_config/<stem>.encode_analysis.html`. The HTML contains embedded
JSON data and interactive HTML5 canvas charts driven by an inlined JS
renderer. The user can zoom into a frame range, hover for exact frame
values, and toggle series, all without opening multiple files. Frame
index is the primary x-axis. Seconds appear in hover text when `fps`
is positive.

### Panels in scope

| # | Graph | Diagnostic question | Data source | Always rendered? |
| --- | --- | --- | --- | --- |
| 1 | **Zoom stability (zoom bouncing).** raw `crop_h` + smoothed overlay; `torso_h` and `torso_w` as toggleable series | Where does `crop_h` oscillate frame-to-frame? Is `crop_post_smooth_size_strength` strong enough? Are width-driven and height-driven crop estimates disagreeing? | `crop_rects[i][3]`, `state["h"]`, `state["w"]` from trajectory | Yes |
| 2 | **Zoom multiple (achieved vs configured).** `crop_h / torso_h` with reference at the configured `torso_height_multiple` | Is `torso_height_multiple` honored? Is a hidden floor (e.g. the 2026-05-02 `crop_min_size` story) overriding it? | Trajectory + crop rects; configured value from `tr_config/<stem>.yaml` | Yes |
| 3 | **Camera motion.** `hypot(dx, dy)` + `scale` | Is camera-motion solving stable across the clip? Are anomalous-magnitude or anomalous-scale frames explaining encode-time wobble? | `MotionTrack.dx`, `.dy`, `.scale` from `load_active_camera_motion_or_fail` | Only when camera-motion data is on disk |
| 4 | **Runner ground speed.** raw + 5-frame mean overlay | Is the projected ground velocity sensible? Are there discontinuities indicating tracker confusion? How big is the stride-phase ripple? | Torso centers projected through `SceneTransform.pixel_to_scene`, first difference, nan-aware running mean | Only when both camera-motion data and scene transform are available |

Each graph appears as its own section in the HTML report. There is no
stacked dashboard. The report page itself is the overview.

### Considered and dropped

The following were considered and rejected. They are not deferred --
they are not on the roadmap. If a real encoded-video problem in the
future is not explained by the four panels above, file a separate
plan for the relevant additional graph then.

- **Crop center path** (cx, cy of the crop center over time, or
  `|crop_center - torso_center|` per frame). Could distinguish
  numerical jitter from slow drift from boundary clamping. Visible in
  the encoded video itself, so not first-version-essential. Candidate
  for a second-pass addition if a real drift bug surfaces that the
  four core panels do not explain.
- **Camera-motion quality** (`MotionTrack.quality` per frame). The
  `hypot + scale` panel already surfaces gross failures.
- **Aspect-ratio achieved vs configured.** Speculative; no reported
  bug. The zoom-multiple pattern can be lifted to aspect ratio later
  if needed.
- **Per-interval confidence shading, FWD/BWD agreement, blob
  coverage, regime classifier output.** Tracker-quality, not
  encode-quality. Out of scope for this report.

## Output artifacts

The user-visible contract.

### File set

| Path | Purpose |
| --- | --- |
| `tr_config/<stem>.encode_analysis.html` | Single self-contained diagnostic report; one file per video |

No PNG. No SVG. No standalone JSON sidecar.

### HTML report structure

Plain hand-assembled HTML, ASCII-only, with a small block of inline
CSS, an embedded JSON data block, four `<canvas>` panel containers,
and an inlined JS renderer.

```
<!doctype html>
<html lang="en"><head>
  <meta charset="utf-8">
  <title>analyze report: <video_stem></title>
  <style>/* ~50 lines: warnings box, monospace paths, canvas frame,
            checkbox row, hover tooltip */</style>
</head><body>
  <h1> Analyze diagnostic report: <video_stem>
  <section class="warnings">      only when warnings is non-empty
    <ul>{escaped warning strings, one per <li>}</ul>
  <section class="references">    always rendered
    <ul>
      <li> link to <stem>.encode_analysis.yaml
      <li> link to per-video config (<stem>.yaml) when present
  <section class="panels">        always rendered (or empty when
                                  trajectory is empty)
    <details open><summary>Zoom stability</summary>
      <p> short interpretive note
      <div class="panel-controls"><!-- series checkboxes --></div>
      <canvas id="zoom-stability"></canvas>
    <details open><summary>Zoom multiple</summary>
      ...<canvas id="zoom-multiple"></canvas>
    <details open><summary>Camera motion</summary>      only when motion_track present
      ...<canvas id="camera-motion"></canvas>
    <details open><summary>Runner ground speed</summary>  only when scene_transform and motion_track present
      ...<canvas id="runner-speed"></canvas>
  <script type="application/json" id="encode-analysis-data">
    { ...embedded JSON data, see schema below... }
  </script>
  <script>
    /* contents of data/js/analyze_report_renderer.js,
       inlined verbatim at write time */
  </script>
</body></html>
```

`<details open>` opens expanded by default. The user can collapse a
panel; checkboxes inside each panel toggle individual series. There is
no global "collapse all"/"expand all" because there are only four
panels.

### Embedded JSON schema

The single source of truth for what the JS renders. Top-level keys:

```json
{
  "video_stem": "race_2025_03_01",
  "fps": 29.97,
  "frames": [0, 1, 2, "..."],
  "panels": [
    {
      "id": "zoom-stability",
      "title": "Zoom stability",
      "y_left_label": "crop_h (source-px)",
      "y_right_label": "torso h, w (source-px)",
      "default_visible": ["crop_h", "crop_h_5f_mean"],
      "series": [
        {"name": "crop_h", "axis": "left", "values": [480, 481, 479]},
        {"name": "crop_h_5f_mean", "axis": "left", "values": [480, 480, 480]},
        {"name": "torso_h", "axis": "right", "values": [96, 96, 95]},
        {"name": "torso_w", "axis": "right", "values": [62, 62, 62]}
      ]
    },
    {
      "id": "zoom-multiple",
      "title": "Zoom multiple: achieved vs configured",
      "y_left_label": "crop_h / torso_h",
      "y_right_label": null,
      "default_visible": ["achieved_multiple"],
      "reference_lines": [
        {"axis": "left", "value": 5.0, "label": "configured (5.0)"}
      ],
      "series": [
        {"name": "achieved_multiple", "axis": "left", "values": [5.0, 5.01, 5.0]}
      ]
    },
    {
      "id": "camera-motion",
      "title": "Camera motion",
      "y_left_label": "hypot(dx, dy) (px)",
      "y_right_label": "scale",
      "default_visible": ["camera_dxy", "scale"],
      "series": [
        {"name": "camera_dxy", "axis": "left", "values": [...]},
        {"name": "scale",       "axis": "right", "values": [...]}
      ]
    },
    {
      "id": "runner-speed",
      "title": "Runner ground speed",
      "y_left_label": "speed (scene units / frame)",
      "y_right_label": "speed (scene units / second)",
      "default_visible": ["speed_raw", "speed_5f_mean"],
      "series": [
        {"name": "speed_raw",     "axis": "left", "values": [...]},
        {"name": "speed_5f_mean", "axis": "left", "values": [...]}
      ]
    }
  ],
  "warnings": []
}
```

Schema rules:

- `frames` is the primary x-axis; one entry per source frame; integer.
- `fps` is null when unknown; otherwise a positive float; the JS
  renderer uses it to compute the seconds-portion of hover tooltips and
  the secondary-axis labeling on the runner-speed panel.
- `panels` is an ordered list. The renderer renders panels in the order
  given and matches each panel's `id` to its `<canvas id="...">`.
- Each `series.values` array has the same length as `frames`. Missing
  data is `null` in JSON (not `0`, not `nan`); the JS renders gaps
  where it sees `null`.
- `default_visible` lists the series names that should be checkbox-on
  at first render. Series not in `default_visible` are hidden by
  default but show in the legend so the user can toggle them on
  (e.g. `torso_h` and `torso_w` on the zoom-stability panel).
- `reference_lines` (optional, zoom-multiple only) draws horizontal
  lines without consuming a series slot.
- `warnings` is a list of human-readable strings rendered verbatim
  (after HTML escaping) in the warnings section.

### Renderer UI behavior

The inlined JS renderer implements only these interactions:

- **Hover.** Mouse over any panel -> tooltip shows frame number,
  seconds (when `fps` known), and the value of every visible series at
  that frame.
- **Drag-zoom on x-range.** Click-drag horizontally inside a panel
  zooms all panels to that frame range simultaneously (synced
  x-range).
- **Double-click reset.** Double-click anywhere inside a panel resets
  the x-range across all panels to the full frame range.
- **Series toggle.** Checkbox row above each panel toggles each named
  series on/off. Checkbox state is per-panel (not synced across
  panels).
- **Gap rendering.** Wherever a series value is `null`, the line
  breaks; the JS does not draw zero or interpolate.

Out of scope for the renderer: lasso, annotations, export buttons,
themes, per-panel config, Y-zoom, panning. These are not implemented
and not stubbed for future implementation.

### Degradation matrix

The HTML report always renders. When upstream data is missing, the
corresponding panel is omitted from the JSON and the corresponding
`<details>` block + `<canvas>` are omitted from the HTML body, with a
warning string surfaced at the top.

| Case | Warnings shown | Panels in JSON / HTML |
| --- | --- | --- |
| All inputs present | (none) | zoom-stability, zoom-multiple, camera-motion, runner-speed |
| `motion_track is None` | "Camera-motion data unavailable; camera and speed panels were skipped." | zoom-stability, zoom-multiple |
| `motion_track present, scene_transform is None` | "Scene transform unavailable; runner-speed panel was skipped." | zoom-stability, zoom-multiple, camera-motion |
| Trajectory empty | "Trajectory empty; no panels rendered." | (none) |

### Visual conventions

- ASCII only in titles, axis labels, legend entries, JSON values, and
  HTML body. No greek letters, no degree signs, no curly quotes.
- Inline CSS under ~50 lines: warnings box with a colored left border,
  monospace styling for paths in the references section, light box
  around `<details>`, simple checkbox row above each canvas, basic
  hover tooltip. No external stylesheet.
- Renderer uses two or three colors per panel (defaulting to
  matplotlib-style blue, orange, and grey). Series can be colored by
  index in the `series` list; tests do not assert specific hex codes.

### What is intentionally NOT in the visual contract

- Pixel layout, font metrics, exact canvas dimensions, exact tooltip
  position. Tests must not assert on these.
- Rendered image. Tests do not run a headless browser.
- HTML pretty-print byte form. Tests assert section headings, canvas
  IDs, JSON shape, and warning strings, not whitespace.
- JS runtime correctness. Verified manually in M2's smoke gate.

## Architecture boundaries and ownership

Four durable components. No planning labels (Milestone, Workstream,
Patch) appear in any code identifier, filename, test name, or CLI flag.

| Component | Path | Ownership |
| --- | --- | --- |
| `analyze_report` (module) | `track_runner/analyze_report.py` (new) | Public `write_analyze_report`; private `_build_analyze_report_data` and `_write_analyze_report_html`; per-panel data helpers; projection helper; smoothing helper; loader for the JS asset file |
| `analyze_report_renderer` (asset) | `data/js/analyze_report_renderer.js` (new) | Vanilla-JS renderer (canvas line charts, twin axes, hover, drag-zoom, double-click reset, checkbox toggle, synced x-range, nan-gap). Read as text by `analyze_report.py` and inlined into HTML at write time. |
| `analyze_cli` (existing) | `track_runner/cli.py` `_mode_analyze`, `track_runner/cli_args.py` analyze subparser | `--plot` flag declaration, data hand-off to `analyze_report`, graceful-degradation around camera-motion load, accumulating the warning strings list |
| `mode_docs` (existing) | `docs/modes/ANALYZE.md`, `docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md`, `docs/ENCODE_DESIGN.md`, `docs/CHANGELOG.md` | User-facing description of the new flag, the HTML report contents and UI, and the graceful-degradation policy |

### Mapping

| Milestone | Workstreams produce patches against components |
| --- | --- |
| M1 (Foundation) | `analyze_report` skeleton + `analyze_report_renderer` stub + `analyze_cli` flag; tests assert HTML shape, canvas IDs, JSON parse, no external URL strings |
| M2 (Rendering) | `analyze_report` per-panel data builders + HTML assembler with embedded JSON and inlined JS; `analyze_report_renderer` real implementation; behavioral tests for the data builders and structural tests for the HTML |
| M3 (Close-out) | `mode_docs` updates; integration test through `_mode_analyze`; changelog entry; in-repo source-draft replacement |

Patches map to at most two components per
`references/CAPACITY_AND_SIZING.md`.

## Capacity note

This is a small surface (one new Python module, one new JS asset file,
one CLI flag, one HTML output, four doc files). Three milestones, two
to three workstreams each, two to four work packages per workstream --
deliberately below the typical-milestone capacity targets. Reviewer
throughput is the limiting resource at this scale.

## Milestone plan

Milestone numbers are labels. Ordering is defined by `Depends on` and
gate edges below.

### M1: Foundation (skeleton, JS stub, CLI flag)

- **Depends on:** none.
- **Entry criteria:** none.
- **Exit criteria:**
  - `track_runner/analyze_report.py` exists with the documented
    `write_analyze_report(...)` signature, `_build_analyze_report_data`
    and `_write_analyze_report_html` private internals, and a stub body
    that writes a minimal-but-valid HTML file (warnings section + four
    panel placeholders + references + an empty embedded JSON object +
    an inlined no-op JS asset) when called with all-truthy inputs.
  - `data/js/analyze_report_renderer.js` exists as an ASCII text
    file with at least a top-of-file comment block describing the
    schema it consumes; a no-op body is acceptable for M1.
  - `--plot` / `-p` is parseable on the `analyze` subparser; rejected
    on `solve`, `refine`, `encode`, `target`, `setup`.
  - `pip_requirements.txt` is unchanged. matplotlib remains in
    `pip_requirements-dev.txt` only.
  - Tests in `tests/test_tr_analyze_report.py` cover HTML existence,
    canvas-ID presence, JSON-parse roundtrip on the embedded data
    block, the no-external-URL contract, and stub-level
    warnings-section degradation.
- **Deliverables:**
  - Patch 1: `analyze_report` skeleton module.
  - Patch 2: `analyze_report_renderer` JS asset (stub) +
    `analyze_cli` flag declaration.
  - Patch 3: `tests/test_tr_analyze_report.py` covering HTML shape,
    canvas-IDs, JSON parse, no-external-URL, and warnings stub.
- **Done checks:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py`
    passes.
  - `source source_me.sh && python3 -m pyflakes track_runner/analyze_report.py`
    is clean.
  - `source source_me.sh && python3 -m pytest tests/test_pyflakes_code_lint.py
    tests/test_ascii_compliance.py tests/test_import_dot.py
    tests/test_import_requirements.py` passes.
  - `source source_me.sh && python3 track_runner.py analyze --help`
    shows the `-p, --plot` line.
  - `source source_me.sh && python3 track_runner.py solve --plot`
    exits with an argparse error.
  - `grep -F matplotlib pip_requirements.txt` returns no hit.

### M2: Rendering (data builders, JS renderer, HTML assembler)

- **Depends on:** M1 (signatures and asset path must exist before
  panels and renderer fill in).
- **Entry criteria:** M1 exit criteria all pass on the integration
  branch.
- **Exit criteria:**
  - `_build_analyze_report_data` returns a dict that conforms to the
    documented schema for representative inputs.
  - The four per-panel data builders (`_zoom_stability_panel_data`,
    `_zoom_multiple_panel_data`, `_camera_motion_panel_data`,
    `_runner_speed_panel_data`) return panel dicts of the documented
    shape.
  - `_runner_speed_per_frame` returns a NumPy array with `nan` on
    missing frames; constant-velocity synthetic input yields RMS
    deviation < 5% of mean.
  - `_running_mean_nan_aware` returns a same-length array; nan-aware
    centered mean.
  - `_write_analyze_report_html` produces a UTF-8 ASCII HTML document
    with: H1, optional warnings section, references section, a
    `<section class="panels">` with one `<details>` + `<canvas>` per
    panel in JSON order, an embedded JSON `<script>` block whose
    contents parse as JSON and equal the data dict, and an inlined
    `<script>` block with the renderer JS contents.
  - `analyze_report_renderer.js` implements the documented UI behavior
    (hover, drag-zoom, double-click reset, series toggle, synced
    x-range, nan-gap rendering). Verified manually in the M2 smoke
    gate; behavioral correctness is not asserted by unit tests.
- **Deliverables:**
  - Patch 4: `analyze_report` zoom-stability + zoom-multiple +
    camera-motion data builders.
  - Patch 5: `analyze_report` runner-speed data builder + projection
    helper + smoothing helper.
  - Patch 6: `analyze_report` HTML assembler with embedded JSON and
    inlined JS; `analyze_report_renderer.js` real implementation;
    `_mode_analyze` wiring; structural tests for HTML shape and JSON
    schema under each degradation case.
- **Done checks:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py`
    passes including the constant-velocity round-trip and
    structural-shape tests.
  - On a real solved video,
    `source source_me.sh && python3 track_runner.py analyze <video> --plot`
    writes one HTML file that opens in Safari/Chrome/Firefox and
    renders the documented panels with hover/zoom/toggle working
    (manual smoke check; a developer signs off in the patch
    description).
  - On a freshly-solved video that has no camera-motion data on disk
    yet, the same command writes the HTML with a warnings section
    naming the skipped panels and only the two zoom panels rendered,
    and exits 0.

### M3: Close-out (docs, integration, changelog, source-draft replacement)

- **Depends on:** M2 (panels and HTML must render before docs describe
  them and before the integration test can assert on real outputs).
- **Entry criteria:** M2 exit criteria all pass on the integration
  branch.
- **Exit criteria:**
  - `docs/modes/ANALYZE.md` AUTO HELP block is regenerated; a
    hand-written paragraph above it describes the HTML report (one
    file per video, embedded JSON + inlined JS, the four panels,
    interaction model summary, graceful-degradation policy).
  - `docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md` has a "Diagnostic report"
    section interpreting common visible patterns.
  - `docs/ENCODE_DESIGN.md` has a one-line cross-link to the analyze
    HTML report.
  - `docs/CHANGELOG.md` has an entry under today's date in
    "Additions and New Features".
  - `docs/active_plans/ANALYZE_MODE_PLOTS.md` is replaced in place
    with the contents of this plan, retaining the same filename so
    external links resolve. After M3 closes, the file is moved to
    `docs/archive/` per repo convention.
  - Integration test in `tests/test_tr_analyze_report.py` invokes the
    `_mode_analyze` plot call site directly on a synthetic fixture
    and asserts the right HTML structure under each degradation
    case.
- **Deliverables:**
  - Patch 7: `mode_docs` updates.
  - Patch 8: `docs/CHANGELOG.md` entry; in-repo source-draft
    replacement.
  - Patch 9: integration test + synthetic fixture helper.
- **Done checks:**
  - `source source_me.sh && python3 tools/refresh_mode_docs.py` exits
    0; `git diff docs/modes/ANALYZE.md` shows only the AUTO HELP block
    and the hand-written paragraph.
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py
    tests/test_ascii_compliance.py tests/test_pyflakes_code_lint.py`
    passes.
  - `git diff --stat docs/CHANGELOG.md` shows lines added under
    today's `## YYYY-MM-DD` heading in
    "### Additions and New Features".

## Workstream breakdown

### M1 workstreams

#### WS-1A: `analyze_report` Python skeleton

- **Goal:** Land the public entry point, the two private helpers
  named by the user (`_build_analyze_report_data`,
  `_write_analyze_report_html`), and the asset-loading path.
- **Owner:** coder.
- **Work packages:** WP-1A-1 (module + signatures + asset loader),
  WP-1A-2 (stub HTML assembler emitting required IDs and an empty
  JSON object).
- **Interfaces:** Provides
  `analyze_report.write_analyze_report`. Reads
  `data/js/analyze_report_renderer.js` from disk at call time.
- **Expected patches:** Patch 1.

#### WS-1B: JS renderer asset (stub) + CLI flag

- **Goal:** Create the JS asset file (stub) so the Python loader has
  something to read; declare the CLI flag.
- **Owner:** coder.
- **Work packages:** WP-1B-1 (analyze subparser flag + rejection on
  other subparsers), WP-1B-2 (`analyze_report_renderer.js` stub with
  schema-comment header and no-op body).
- **Interfaces:** Provides `args.write_plots` boolean to
  `_mode_analyze` (consumed in M2). Provides the JS asset path that
  WS-1A reads.
- **Expected patches:** Patch 2.

#### WS-1C: Skeleton tests

- **Goal:** Prove HTML existence, canvas-ID presence, JSON parse, the
  no-external-URL contract, and stub-level warnings degradation
  before panels are populated.
- **Owner:** tester.
- **Work packages:** WP-1C-1 (HTML structural tests), WP-1C-2 (JSON
  parse + no-external-URL tests), WP-1C-3 (warnings-section stub
  tests).
- **Interfaces:** Needs `analyze_report.write_analyze_report` from
  WS-1A and the JS asset from WS-1B.
- **Expected patches:** Patch 3.

### M2 workstreams

#### WS-2A: Three panel data builders (zoom-stability, zoom-multiple, camera-motion)

- **Goal:** Populate the three data builders whose inputs do not
  require scene projection. The two zoom builders share trajectory +
  crop-rects + per-video config and land in the same patch; the camera
  builder co-lands.
- **Owner:** coder.
- **Work packages:** WP-2A-1 (zoom-stability panel data: raw + 9-frame
  smoothed crop_h, plus torso_h and torso_w), WP-2A-2 (zoom-multiple
  panel data: achieved multiple + reference line from YAML),
  WP-2A-3 (camera-motion panel data: hypot(dx,dy) + scale),
  WP-2A-4 (per-builder behavioral tests).
- **Interfaces:** Needs `analyze_report` skeleton from M1 and
  `_running_mean_nan_aware` from WP-2B-2. Provides three panel dicts
  consumed by WS-2C HTML assembler.
- **Expected patches:** Patch 4.

#### WS-2B: Runner-speed panel data + helpers

- **Goal:** Add the only panel that requires new computation. Isolate
  the projection and smoothing helpers so each is testable
  independent of the rest of the data path.
- **Owner:** coder.
- **Work packages:** WP-2B-1 (private `_runner_speed_per_frame`),
  WP-2B-2 (private `_running_mean_nan_aware`), WP-2B-3
  (`_runner_speed_panel_data`), WP-2B-4 (constant-velocity
  round-trip test).
- **Interfaces:** Needs `analyze_report` skeleton from M1.
- **Expected patches:** Patch 5.

#### WS-2C: JS renderer + HTML assembler + entry-point wiring

- **Goal:** Implement the real JS renderer and complete the HTML
  assembler so the report is end-to-end functional.
- **Owner:** coder + tester.
- **Work packages:** WP-2C-1 (`analyze_report_renderer.js` full
  implementation: line charts, twin axes, hover, drag-zoom,
  double-click reset, series toggle via checkboxes, synced x-range,
  nan-gap rendering), WP-2C-2 (`_write_analyze_report_html`: H1,
  warnings, references, panels, embedded JSON, inlined JS),
  WP-2C-3 (entry-point wiring in `_mode_analyze`),
  WP-2C-4 (HTML structural assertions for each degradation case;
  JSON-schema assertions; no-external-URL assertion).
- **Interfaces:** Needs WS-2A and WS-2B panel data.
- **Expected patches:** Patch 6.

### M3 workstreams

#### WS-3A: Mode docs + cross-references

- **Goal:** Describe the new flag, the HTML report's contents, the
  interaction model, and the graceful-degradation policy in
  user-facing mode docs.
- **Owner:** planner.
- **Work packages:** WP-3A-1 (`docs/modes/ANALYZE.md` hand-written
  paragraph + `tools/refresh_mode_docs.py` run),
  WP-3A-2 (`docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md` "Diagnostic
  report" section), WP-3A-3 (`docs/ENCODE_DESIGN.md` one-line
  cross-link).
- **Interfaces:** Needs M2 merged so docs describe the real feature.
- **Expected patches:** Patch 7.

#### WS-3B: Changelog + source-draft replacement

- **Goal:** Update the changelog and replace the in-repo source draft
  with this canonical plan.
- **Owner:** planner.
- **Work packages:** WP-3B-1 (one entry under today's
  `## YYYY-MM-DD` heading in
  "### Additions and New Features"), WP-3B-2 (replace contents of
  `docs/active_plans/ANALYZE_MODE_PLOTS.md` in place with a copy of
  this plan).
- **Interfaces:** Needs final scope (M2 merged).
- **Expected patches:** Patch 8.

#### WS-3C: Integration test

- **Goal:** Prove the CLI and module agree on the degradation contract
  end-to-end, not just at the helper boundary.
- **Owner:** tester.
- **Work packages:** WP-3C-1 (synthetic fixture providing trajectory,
  motion, scene transform, fps, config), WP-3C-2 (integration test
  walking the three degradation paths and asserting HTML structure
  and JSON schema and warnings text).
- **Interfaces:** Needs M2 merged.
- **Expected patches:** Patch 9.

## Work package specs

Format: title, owner, touch points, acceptance criteria, verification
commands, dependencies. All paths relative to repo root. All `pytest`
and `pyflakes` invocations assume the repo's bootstrap
(`source source_me.sh`).

### WP-1A-1: Module skeleton with public + private helpers

- **Owner:** coder.
- **Touch points:** `track_runner/analyze_report.py` (new).
- **Acceptance criteria:**
  - File starts with no shebang (library module per
    `docs/PYTHON_STYLE.md`).
  - Imports: stdlib first (`json`, `pathlib`, `subprocess`), then
    numpy, then local modules; absolute imports only.
  - Public signature
    `write_analyze_report(*, out_path, video_stem, trajectory,
    crop_rects, motion_track, scene_transform, fps, config, warnings)
    -> pathlib.Path` exists. `out_path` is the full path to the
    `.html` file. `config` is the parsed `tr_config/<stem>.yaml` dict
    (or None). `warnings` is a list[str].
  - Private `_build_analyze_report_data(*, video_stem, trajectory,
    crop_rects, motion_track, scene_transform, fps, config) -> dict`
    exists; returns the JSON-serializable schema dict (panel list may
    be empty in the stub).
  - Private `_write_analyze_report_html(*, out_path, video_stem,
    report_data, warnings) -> pathlib.Path` exists; writes a UTF-8
    ASCII HTML file at `out_path` with parent directories created.
  - Private `_repo_root() -> pathlib.Path` returns the repo root via
    `subprocess.check_output(['git', 'rev-parse', '--show-toplevel'],
    cwd=pathlib.Path(__file__).parent)` per `docs/REPO_STYLE.md`'s
    rule "Determine REPO_ROOT with `git rev-parse --show-toplevel`,
    not by deriving paths from the current working directory." The
    `cwd=__file__.parent` argument anchors the lookup to the
    `track_runner/` package even when the CLI is invoked from
    elsewhere, so the loader works regardless of the user's CWD.
  - Private `_load_renderer_js() -> str` returns
    `(_repo_root() / 'data' / 'js' /
    'analyze_report_renderer.js').read_text(encoding='ascii')`.
  - No try/except blocks in the module body.
- **Verification commands:**
  - `source source_me.sh && python3 -c "import track_runner.analyze_report"`
  - `source source_me.sh && python3 -m pyflakes track_runner/analyze_report.py`
- **Dependencies:** none.

### WP-1A-2: Stub HTML assembler with required structural markers

- **Owner:** coder.
- **Touch points:** `track_runner/analyze_report.py`.
- **Acceptance criteria:**
  - Stub `_write_analyze_report_html` emits all required structural
    markers in document order: `<!doctype html>`, `<h1>` containing
    the video stem, `<section class="warnings">` (only when
    `warnings` is non-empty), `<section class="references">`,
    `<section class="panels">` containing the four
    `<canvas id="...">` markers (`zoom-stability`, `zoom-multiple`,
    `camera-motion`, `runner-speed`),
    `<script type="application/json" id="encode-analysis-data">`
    block whose contents are valid JSON, and `<script>` block whose
    contents equal the loaded renderer JS asset.
  - Stub embedded JSON has shape
    `{"video_stem": "...", "fps": <float|null>, "frames": [...],
    "panels": [], "warnings": [...]}`. Empty `panels` is fine in M1;
    M2 fills it.
  - Stub HTML never contains the substring `http://` or `https://`
    (no external CDN; no external image references). The
    `tr_config/<stem>.yaml` link in the references section uses a
    relative path.
- **Verification commands:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py
    -k structure_markers`
- **Dependencies:** WP-1A-1.

### WP-1B-1: Add `--plot` to analyze subparser

- **Owner:** coder.
- **Touch points:** `track_runner/cli_args.py` (analyze block,
  `cli_args.py:382-401`).
- **Acceptance criteria:**
  - `-p`/`--plot` flag declared with `dest='write_plots'`,
    `action='store_true'`. Help text: "write HTML diagnostic report
    alongside the encode_analysis.yaml".
  - Flag does not appear on `solve`, `refine`, `encode`, `target`,
    `setup` subparsers.
- **Verification commands:**
  - `source source_me.sh && python3 track_runner.py analyze --help | grep -F -- "--plot"`
  - `source source_me.sh && python3 track_runner.py solve --plot 2>&1 | grep -F "unrecognized"`
- **Dependencies:** none.

### WP-1B-2: Renderer JS asset stub

- **Owner:** coder.
- **Touch points:** `data/js/analyze_report_renderer.js` (new).
- **Acceptance criteria:**
  - File exists, ASCII-only, with a top-of-file comment block (10-30
    lines) describing the JSON schema it consumes (the schema
    documented in this plan).
  - Body is a no-op IIFE
    (`(function() { /* M1 stub; real renderer in M2 */ })();`).
  - File loads cleanly via `_load_renderer_js()` (UTF-8/ASCII safe).
- **Verification commands:**
  - `source source_me.sh && python3 -c "from track_runner.analyze_report
    import _load_renderer_js; assert len(_load_renderer_js()) > 0"`
  - `source source_me.sh && python3 -m pytest tests/test_ascii_compliance.py
    -k renderer`
- **Dependencies:** none.

### WP-1C-1: HTML structural tests

- **Owner:** tester.
- **Touch points:** `tests/test_tr_analyze_report.py` (new).
- **Acceptance criteria:**
  - `test_writes_html_at_supplied_out_path`: returned path equals
    `out_path` and the file exists on disk under `tmp_path`.
  - `test_html_contains_required_structural_markers`: HTML body
    contains in document order: `<h1>`,
    `<section class="references">`, four `<canvas id="...">` elements
    with the documented IDs, the JSON `<script>` block, and the
    inlined renderer `<script>` block.
  - `test_html_contains_video_stem_in_h1`: H1 contains the supplied
    stem.
  - Tests use `tmp_path` and synthetic in-memory data; no real video.
- **Verification commands:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py
    -k structure_markers`
- **Dependencies:** WP-1A-2, WP-1B-2.

### WP-1C-2: JSON parse + no-external-URL tests

- **Owner:** tester.
- **Touch points:** `tests/test_tr_analyze_report.py`.
- **Acceptance criteria:**
  - `test_embedded_json_is_valid_json`: extract the contents of the
    `<script type="application/json" id="encode-analysis-data">` block
    from the HTML; `json.loads` succeeds; the parsed object has the
    keys `video_stem`, `fps`, `frames`, `panels`, `warnings`.
  - `test_html_contains_no_external_url`: the HTML body does not
    contain `http://` or `https://` substrings (no CDN, no external
    image, no external link references).
  - `test_html_is_ascii_only`: the HTML body encodes cleanly in
    ASCII.
- **Verification commands:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py
    -k "json_valid or no_external_url or ascii_only"`
- **Dependencies:** WP-1A-2.

### WP-1C-3: Stub-level warnings tests

- **Owner:** tester.
- **Touch points:** `tests/test_tr_analyze_report.py`.
- **Acceptance criteria:**
  - `test_warnings_section_omitted_when_warnings_empty`: warnings
    list `[]` -> `<section class="warnings">` is absent from the HTML
    body.
  - `test_warnings_section_present_when_warnings_supplied`: warnings
    list with one string -> `<section class="warnings">` is present
    and contains the string verbatim as a list item; the same string
    appears in the embedded JSON's `warnings` array.
  - `test_warnings_html_escaped`: a warning string containing `<` and
    `&` is HTML-escaped in the body but unescaped in the JSON
    `warnings` array.
- **Verification commands:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py
    -k warnings`
- **Dependencies:** WP-1A-2.

### WP-2A-1: Zoom-stability panel data builder

- **Owner:** coder.
- **Touch points:** `track_runner/analyze_report.py`
  `_zoom_stability_panel_data`.
- **Acceptance criteria:**
  - Signature
    `_zoom_stability_panel_data(trajectory, crop_rects) -> dict`
    returns a panel dict matching the schema:
    `{"id": "zoom-stability", "title": ..., "y_left_label": ...,
    "y_right_label": ..., "default_visible": ["crop_h",
    "crop_h_5f_mean"], "series": [...]}`.
  - Series in this order: `crop_h` (raw crop heights, source-px),
    `crop_h_5f_mean` (9-frame centered nan-aware running mean of
    crop_h; the panel name says "5f" for legacy consistency with the
    runner-speed panel naming, but the window is 9 to match the
    ~6-7 frame time constant of the 0.15 default
    `crop_post_smooth_size_strength` EMA per the 2026-05-02
    changelog), `torso_h` from `state["h"]`, `torso_w` from
    `state["w"]`. Wait -- name resolution: keep series name
    `crop_h_9f_mean` to be explicit about the actual window.
  - Missing data is `None` (JSON `null`) in every series, not `0` and
    not `nan`.
  - Series values length equals `frames` length (passed in by the
    caller; the panel builder does not own the frame index).
- **Verification commands:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py
    -k zoom_stability`
- **Dependencies:** WP-1A-2, WP-2B-2.

### WP-2A-2: Zoom-multiple panel data builder

- **Owner:** coder.
- **Touch points:** `track_runner/analyze_report.py`
  `_zoom_multiple_panel_data`.
- **Acceptance criteria:**
  - Signature
    `_zoom_multiple_panel_data(trajectory, crop_rects, config) -> dict`.
  - Computes per-frame achieved multiple as `crop_h / torso_h`
    (nan-aware, safe against `torso_h <= 0`); emits as a single
    series named `achieved_multiple` on the left axis.
  - When `config` contains a `torso_height_multiple` key, emits a
    `reference_lines` entry of the form
    `[{"axis": "left", "value": <configured>, "label": "configured (<value>)"}]`.
    When the key is absent, omits the `reference_lines` field.
  - Title independent of the configured value.
- **Verification commands:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py
    -k zoom_multiple`
- **Dependencies:** WP-1A-2.

### WP-2A-3: Camera-motion panel data builder

- **Owner:** coder.
- **Touch points:** `track_runner/analyze_report.py`
  `_camera_motion_panel_data`.
- **Acceptance criteria:**
  - Signature
    `_camera_motion_panel_data(motion_track) -> dict`.
  - Series in this order: `camera_dxy` (left axis, computed as
    `numpy.hypot(motion_track.dx, motion_track.dy)`), `scale` (right
    axis, `motion_track.scale`).
  - Synthetic constant-dx MotionTrack produces a constant
    `camera_dxy` series at `abs(dx)` (within float epsilon).
- **Verification commands:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py
    -k camera_motion`
- **Dependencies:** WP-1A-2.

### WP-2A-4: Behavioral tests for the three panel builders

- **Owner:** tester.
- **Touch points:** `tests/test_tr_analyze_report.py`.
- **Acceptance criteria:**
  - `test_zoom_stability_smoothed_passes_through_when_input_smooth`:
    constant `crop_h` -> the smoothed series equals the raw series
    within float epsilon.
  - `test_zoom_stability_smoothed_attenuates_high_frequency`:
    alternating `crop_h` (a pathological zoom-bounce signal) -> the
    smoothed series sits between the two raw values; the raw-vs-mean
    gap is non-trivial. Confirms the panel surfaces bouncing.
  - `test_zoom_multiple_constant_input_flat`: same-factor scaled
    crop_h and torso_h -> achieved-multiple series is constant within
    float epsilon.
  - `test_camera_motion_constant_dxy_flat`: constant-dx MotionTrack
    -> `camera_dxy` series is constant.
  - Tests assert on the panel-dict series values directly; no HTML
    parsing, no JS execution.
- **Verification commands:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py
    -k "zoom_stability or zoom_multiple or camera_motion"`
- **Dependencies:** WP-2A-1, WP-2A-2, WP-2A-3.

### WP-2B-1: Runner-speed projection helper

- **Owner:** coder.
- **Touch points:** `track_runner/analyze_report.py`
  `_runner_speed_per_frame`.
- **Acceptance criteria:**
  - Signature
    `_runner_speed_per_frame(trajectory, scene_transform) -> numpy.ndarray`.
  - Calls `scene_transform.pixel_to_scene(frame_index, cx, cy)` for
    each non-missing frame.
  - First difference produces speed in scene units per frame; missing
    frames -> `numpy.nan` in the output array.
  - Output length equals input trajectory length so frame index
    aligns with other panels.
- **Verification commands:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py
    -k speed_helper`
- **Dependencies:** WP-1A-2.

### WP-2B-2: nan-aware running-mean helper

- **Owner:** coder.
- **Touch points:** `track_runner/analyze_report.py`
  `_running_mean_nan_aware`.
- **Acceptance criteria:**
  - Pure function:
    `_running_mean_nan_aware(values: numpy.ndarray, window: int) -> numpy.ndarray`
    returning an array of the same length as `values`.
  - Centered window. A single nan inside a window does NOT blank the
    whole window output; the mean is computed over the non-nan
    elements only. A window with zero non-nan elements yields nan.
  - Helper is independent of `_runner_speed_per_frame` and of any
    panel builder. WP-2A-1 (zoom-stability) and WP-2B-3
    (runner-speed) both consume it.
  - Docstring states the WHY (zoom bouncing visualization,
    stride-phase ripple) per `docs/PYTHON_STYLE.md` "comment when WHY
    is non-obvious".
- **Verification commands:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py
    -k running_mean`
- **Dependencies:** none (pure helper; can land in parallel with
  WP-2B-1).

### WP-2B-3: Runner-speed panel data builder

- **Owner:** coder.
- **Touch points:** `track_runner/analyze_report.py`
  `_runner_speed_panel_data`.
- **Acceptance criteria:**
  - Signature
    `_runner_speed_panel_data(trajectory, scene_transform, fps) -> dict`.
  - Series in this order: `speed_raw` (raw per-frame speed in scene
    units / frame, left axis), `speed_5f_mean` (5-frame centered
    nan-aware running mean of `speed_raw`, left axis).
  - When `fps` is positive, sets `y_right_label` to "speed (scene
    units / second)" so the renderer emits a secondary axis. When
    `fps` is None or non-positive, omits the right-axis label.
  - Every input frame produces a value (`null` for missing); never
    sub-samples.
- **Verification commands:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py
    -k speed_panel`
- **Dependencies:** WP-2B-1, WP-2B-2.

### WP-2B-4: Constant-velocity round-trip test

- **Owner:** tester.
- **Touch points:** `tests/test_tr_analyze_report.py`.
- **Acceptance criteria:**
  - `test_constant_velocity_input_produces_constant_speed`: synthetic
    trajectory with constant scene velocity plus an identity
    `SceneTransform`-like stub yields a per-frame raw speed array
    whose RMS deviation about its mean is < 5% of the mean (the raw
    helper is the unit under test, not the smoothed overlay).
  - Test does not assert the absolute speed value, only constancy.
- **Verification commands:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py
    -k constant_velocity`
- **Dependencies:** WP-2B-1.

### WP-2C-1: JS renderer implementation

- **Owner:** coder.
- **Touch points:** `data/js/analyze_report_renderer.js`.
- **Acceptance criteria:**
  - File is ASCII-only and self-contained: no `import`, no `require`,
    no `<script src="...">`, no fetch/XHR. Reads its data from
    `document.getElementById('encode-analysis-data').textContent`
    and `JSON.parse`s it.
  - Renders one line chart per panel into the `<canvas id="...">`
    matched by `panel.id`. Supports twin axes when both
    `y_left_label` and `y_right_label` are non-null. Reference lines
    drawn as dashed horizontal lines on the named axis.
  - Implements: hover tooltip (frame, seconds when fps known, value
    of every visible series at that frame), drag-zoom on x-range
    (synced across all panels), double-click reset (synced),
    per-panel checkbox row that toggles series visibility, nan/null
    -> gap (do not draw a line through null).
  - Does NOT implement: lasso, annotations, export buttons, themes,
    Y-zoom, panning, per-panel config UI.
  - File length budget: aim for under 600 lines of vanilla JS.
- **Verification commands:**
  - Manual: open the M2 sample HTML in Safari, Chrome, and Firefox;
    confirm hover/zoom/toggle/reset/sync all work; record observed
    file size and any browser-specific rendering note in the patch
    description.
  - `source source_me.sh && python3 -m pytest tests/test_ascii_compliance.py
    -k renderer`
- **Dependencies:** none (the renderer consumes the documented JSON
  schema, not the Python builders).

### WP-2C-2: HTML assembler with embedded JSON and inlined JS

- **Owner:** coder.
- **Touch points:** `track_runner/analyze_report.py`
  `_write_analyze_report_html`.
- **Acceptance criteria:**
  - Emits in document order: `<!doctype html>`, `<head>` with
    `<title>` containing the video stem and a `<style>` block with
    inline CSS under ~50 lines, `<body>`, `<h1>`, optional
    `<section class="warnings">` (only when `warnings` is non-empty),
    `<section class="references">`, `<section class="panels">`
    containing one `<details open><summary>...</summary>...</details>`
    block per panel in JSON `panels` order with the documented
    `<canvas id="...">`,
    `<script type="application/json" id="encode-analysis-data">`
    block whose contents equal `json.dumps(report_data, indent=2,
    ensure_ascii=True)`, and a final `<script>` block with the
    `_load_renderer_js()` contents inlined verbatim.
  - Warning strings, video stem, and any other user-supplied text are
    HTML-escaped (`<`, `>`, `&`, `"`, `'`) before being placed in
    body content.
  - `tr_config/<stem>.yaml` link in the references section uses a
    relative path (the same directory as the HTML file).
  - Output never contains `http://` or `https://` substrings.
- **Verification commands:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py
    -k assemble_html`
- **Dependencies:** WP-1A-2, WP-2A-1, WP-2A-2, WP-2A-3, WP-2B-3,
  WP-2C-1.

### WP-2C-3: Wire `_mode_analyze` to call the entry point

- **Owner:** coder.
- **Touch points:** `track_runner/cli.py` `_mode_analyze` (after the
  YAML report is written, around line 1953+).
- **Acceptance criteria:**
  - When `args.write_plots` is true, the function builds an empty
    `warnings` list, attempts
    `motion_track = camera_motion.load_active_camera_motion_or_fail(args.input_file)`
    inside try/RuntimeError; on RuntimeError, appends the documented
    "Camera-motion data unavailable; camera and speed panels were
    skipped." string to `warnings`, sets `motion_track = None`, and
    prints the same string to stderr.
  - Loads the per-video config dict
    (`tr_config/<stem>.yaml`) when present, passing `None` otherwise.
  - Calls
    `analyze_report.write_analyze_report(out_path=<derived>.encode_analysis.html,
    video_stem=stem, trajectory=trajectory, crop_rects=crop_rects,
    motion_track=motion_track, scene_transform=scene_transform,
    fps=fps, config=config, warnings=warnings)` and prints one info
    line to stdout with the returned path.
- **Verification commands:**
  - `source source_me.sh && python3 -m pyflakes track_runner/cli.py`
- **Dependencies:** WP-2C-2, WP-1B-1.

### WP-2C-4: HTML and JSON structural assertions for each degradation case

- **Owner:** tester.
- **Touch points:** `tests/test_tr_analyze_report.py`.
- **Acceptance criteria:**
  - `test_full_html_when_all_inputs_present`: HTML contains four
    `<canvas id="...">` elements (`zoom-stability`, `zoom-multiple`,
    `camera-motion`, `runner-speed`); embedded JSON's `panels` list
    has length 4 with matching `id` order; no warnings section.
  - `test_html_when_motion_missing`: two canvases (`zoom-stability`,
    `zoom-multiple`); JSON `panels` length 2 with matching IDs;
    warnings section present and contains the documented
    "Camera-motion data unavailable" string; same string appears in
    JSON `warnings`.
  - `test_html_when_scene_transform_missing`: three canvases
    (`zoom-stability`, `zoom-multiple`, `camera-motion`); JSON
    `panels` length 3; warnings contain "Scene transform unavailable".
  - `test_html_contains_no_external_url`: HTML never contains
    `http://` or `https://` regardless of degradation case.
  - `test_embedded_json_series_lengths_match_frames`: every series
    `values` array has the same length as the top-level `frames`
    array.
  - Tests parse the JSON via standard `json.loads`; tests do not
    parse the HTML beyond regex-extracting the JSON `<script>` block
    contents.
- **Verification commands:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py
    -k "full_html or motion_missing or scene_transform_missing or
    no_external or series_lengths"`
- **Dependencies:** WP-2C-2, WP-2C-3.

### WP-3A-1: Update `docs/modes/ANALYZE.md`

- **Owner:** planner.
- **Touch points:** `docs/modes/ANALYZE.md`.
- **Acceptance criteria:**
  - Hand-written paragraph above the AUTO HELP block describing the
    HTML report (one self-contained file per video, embedded JSON +
    inlined vanilla JS, the four panels, the interaction model
    summary -- hover, drag-zoom, double-click reset, series toggle --
    and the graceful-degradation policy).
  - AUTO HELP block regenerated by `tools/refresh_mode_docs.py`.
  - Markdown style matches `docs/MARKDOWN_STYLE.md`: ASCII only,
    sentence-case headings.
- **Verification commands:**
  - `source source_me.sh && python3 tools/refresh_mode_docs.py`
  - `git diff docs/modes/ANALYZE.md`
  - `source source_me.sh && python3 -m pytest tests/test_ascii_compliance.py
    -k ANALYZE`
- **Dependencies:** WP-2C-3.

### WP-3A-2: Add "Diagnostic report" section to architecture doc

- **Owner:** planner.
- **Touch points:** `docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md`.
- **Acceptance criteria:**
  - New `## Diagnostic report` (or appropriately nested) section
    describing each panel, its data source, its diagnostic
    interpretation, and the report's interaction model.
  - Cross-links to `docs/modes/ANALYZE.md` and
    `docs/ENCODE_DESIGN.md`.
- **Verification commands:**
  - `git diff docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md`
  - `source source_me.sh && python3 -m pytest tests/test_ascii_compliance.py
    -k TRACK_RUNNER_ANALYZE_AND_ENCODE`
- **Dependencies:** WP-2C-3.

### WP-3A-3: ENCODE_DESIGN.md cross-link

- **Owner:** planner.
- **Touch points:** `docs/ENCODE_DESIGN.md`.
- **Acceptance criteria:**
  - One sentence cross-linking the analyze HTML report as the
    canonical first-look diagnostic for encode-quality issues.
- **Verification commands:**
  - `git diff docs/ENCODE_DESIGN.md`
- **Dependencies:** WP-3A-2.

### WP-3B-1: Changelog entry

- **Owner:** planner.
- **Touch points:** `docs/CHANGELOG.md`.
- **Acceptance criteria:**
  - Entry under today's `## YYYY-MM-DD` heading in
    "### Additions and New Features".
  - Entry mentions the flag, the HTML report (one file per video,
    embedded JSON + inlined vanilla JS, the four panels, the
    interaction model summary), the graceful-degradation policy, and
    the explicit non-promotion of matplotlib (the new path is
    runtime-dependency-free).
- **Verification commands:**
  - `git diff docs/CHANGELOG.md`
- **Dependencies:** WP-3A-1, WP-3A-2, WP-3A-3.

### WP-3B-2: Replace in-repo source draft with this plan

- **Owner:** planner.
- **Touch points:** `docs/active_plans/ANALYZE_MODE_PLOTS.md`.
- **Acceptance criteria:**
  - File contents replaced in place with a copy of this plan,
    retaining the filename so existing links continue to resolve.
- **Verification commands:**
  - `git diff docs/active_plans/ANALYZE_MODE_PLOTS.md`
- **Dependencies:** none beyond M2 merge.

### WP-3C-1: Synthetic fixture

- **Owner:** tester.
- **Touch points:** `tests/_analyze_report_fixtures.py` (new helper
  module under `tests/`; `conftest.py` is for configuration only per
  repo memory; shared helpers go in named helper modules).
- **Acceptance criteria:**
  - Returns a deterministic trajectory, a `MotionTrack`-shaped
    namedtuple/object, a stub `SceneTransform`-shaped object exposing
    `pixel_to_scene`, an `fps` value, and a stub config dict.
  - Lives in `tests/`, not in `track_runner/`.
- **Verification commands:**
  - `source source_me.sh && python3 -c "import tests._analyze_report_fixtures"`
- **Dependencies:** WP-2C-3.

### WP-3C-2: End-to-end integration test

- **Owner:** tester.
- **Touch points:** `tests/test_tr_analyze_report.py`.
- **Acceptance criteria:**
  - Test invokes the plot path that `_mode_analyze` calls (via direct
    function call, not subprocess) using the WP-3C-1 fixture and
    asserts HTML structure + JSON schema for each of the three
    degradation paths (full data, motion missing, scene transform
    missing).
  - Test exits in under 10 seconds on a developer laptop.
- **Verification commands:**
  - `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py`
- **Dependencies:** WP-3C-1, WP-2C-3.

## Patch plan and reporting

Reporting format follows `references/CAPACITY_AND_SIZING.md` and
`references/plan_quality_standard.md` Section 7. Each patch touches at
most two components.

- **Patch 1: analyze_report skeleton.** Public entry point, private
  builders (`_build_analyze_report_data`, `_write_analyze_report_html`),
  asset loader (`_load_renderer_js`), stub HTML assembler with
  required structural markers and an empty embedded JSON object.
- **Patch 2: analyze_report_renderer (stub) + analyze_cli flag.** JS
  asset file with schema-comment header and no-op body; `--plot`
  declared on analyze subparser.
- **Patch 3: tests, M1 stub coverage.** HTML existence,
  structural-marker presence, JSON-parse roundtrip, no-external-URL,
  ASCII-only, warnings-section degradation.
- **Patch 4: analyze_report zoom-stability + zoom-multiple +
  camera-motion data builders.**
- **Patch 5: analyze_report runner-speed data builder + projection
  helper + smoothing helper.**
- **Patch 6: analyze_report HTML assembler with embedded JSON +
  inlined JS + entry-point wiring + analyze_report_renderer real
  implementation + structural assertions.** Two components:
  `analyze_report` and `analyze_report_renderer`. (`analyze_cli` is
  touched but only at the wiring call site, which is part of the
  `analyze_report` entry-point contract; this stays within the
  two-component limit.)
- **Patch 7: tests, migration, docs.** ANALYZE.md hand-written
  paragraph + AUTO HELP refresh; TRACK_RUNNER_ANALYZE_AND_ENCODE.md
  "Diagnostic report" section; ENCODE_DESIGN.md cross-link.
- **Patch 8: tests, migration, docs.** `docs/CHANGELOG.md` entry plus
  in-repo source-draft replacement at
  `docs/active_plans/ANALYZE_MODE_PLOTS.md`.
- **Patch 9: tests, migration, docs.** End-to-end integration test +
  fixture helper.

## Acceptance criteria and gates

- **Unit gate:**
  `source source_me.sh && python3 -m pytest tests/test_tr_analyze_report.py`
  passes after each M1/M2 patch that adds tests.
- **Lint gate:**
  `source source_me.sh && python3 -m pytest
  tests/test_pyflakes_code_lint.py tests/test_ascii_compliance.py
  tests/test_import_dot.py tests/test_import_requirements.py` passes
  after every patch.
- **Integration gate (closes M2):** Running
  `source source_me.sh && python3 track_runner.py analyze <stem> --plot`
  against a real solved video writes the HTML; the file opens in
  Safari, Chrome, and Firefox; hover, drag-zoom, double-click reset,
  and series-toggle all work; observed file size and any
  browser-specific note recorded in the patch description.
- **Regression gate:** Existing `tests/test_tr_encode_mode.py` and
  `tests/test_tr_velocity_model.py` still pass; analyze-mode behavior
  without `--plot` is byte-identical to current main.
- **Release gate (closes M3):** `pip install -r pip_requirements.txt`
  on a fresh checkout produces a working `analyze --plot` (no new
  runtime dependency to install).

## Test strategy

- **Unit checks:** `tests/test_tr_analyze_report.py` with the WP-3C-1
  fixture. Behavioral assertions only: HTML structural markers, JSON
  parse and schema, panel-skip rules per degradation case,
  constant-velocity round-trip RMS bound on the raw speed helper,
  panel-builder data correctness.
- **Integration checks:** WP-3C-2 walks the `_mode_analyze` plot
  call-site via direct function invocation (no subprocess), asserting
  HTML and JSON structure under each degradation case.
- **Smoke checks:** Manual run on a known-solved sample video before
  closing M2 and again before closing M3, with the report opened in
  three browsers (Safari, Chrome, Firefox) to verify the JS UI works.
- **Regression gate:** Repo pytest run against
  `tests/test_pyflakes_code_lint.py`, `tests/test_ascii_compliance.py`,
  `tests/test_import_dot.py`, `tests/test_import_requirements.py`,
  `tests/test_tr_encode_mode.py`, `tests/test_tr_velocity_model.py`,
  `tests/test_shebangs.py`.
- **Failure semantics:** Unit-gate failure blocks the patch.
  Integration-gate failure blocks the milestone close. Regression-gate
  failure blocks merge to main.

## Migration and compatibility

- **Additive only.** `analyze` without `--plot` is byte-identical to
  current main. No existing CLI flag is renamed or repurposed.
- **No dependency change.** matplotlib remains in
  `pip_requirements-dev.txt` only and is not promoted. The
  report path uses no external Python or JavaScript library at
  runtime.
- **Backward compatibility.** No on-disk artifacts are renamed. The
  new HTML path is in a previously-unused namespace
  (`*.encode_analysis.html`) so it cannot collide with existing
  files.
- **Rollback strategy.** Plan reverts cleanly via `git revert` of the
  patches in reverse order.
- **Deletion criteria for legacy paths.** None; nothing is being
  deleted in this round. The source draft is replaced in place
  (preserves filename); after merge, archival follows the standard
  active-to-archive pattern.

## Risks and mitigations

| Risk | Impact | Trigger | Mitigation | Owner |
| --- | --- | --- | --- | --- |
| JS renderer bug breaks the report in one browser | A panel renders blank or wrong | First user opens the report | M2 manual smoke check across Safari, Chrome, Firefox is mandatory; document any browser-specific note in `docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md`; keep the renderer small (under ~600 lines) and stick to widely-supported canvas APIs | reviewer |
| JS renderer bug only surfaces on a long clip | Hover/zoom regress when frame count is high | First user runs report on a real (long) video | Smoke check uses a long real clip in addition to the synthetic fixture; record observed file size and render time in the patch description | coder |
| Embedded JSON balloons HTML file size on long clips | HTML is slow to load | Long event clips | No sub-sampling: completeness is the contract. Revisit only if a real clip produces an HTML > 25 MB or a load time > 3 s in a browser. Document the empirical worst case observed in M2 close-out. | coder |
| `_runner_speed_per_frame` projection produces nonsense when scene transform is degenerate | Misleading speed panel | Scene transform from a single-frame video or stationary clip | Test exercises a stub `SceneTransform` returning `(0, 0)` for every input; assertion is "raw speed array is all zeros and finite" | coder |
| Stride-phase ripple in speed panel looks alarming | Confused user | Any sprinting clip | Plot raw and 5-frame mean overlay on the same axes; title labels both series. Do not hide the raw data. | coder |
| HTML escaping bug lets a config string break the report | Broken output | Pathological config string with `<` or `&` | `_write_analyze_report_html` HTML-escapes warnings, video stem, and any other user-supplied text in body content (WP-2C-2 acceptance) | reviewer |
| User adds `--plot` to `solve` or `refine` | Confusing error | First user trying the flag | Flag declared on `analyze` only; rejection verified by WP-1B-1 | reviewer |
| Plan-vs-implementation drift on the public function signature | Re-touch of `_mode_analyze` after M2 panels land | Coders implementing M2 want to add a kwarg | Lock the signature in M1 (WP-1A-1); add helper kwargs internally | architect |
| Camera-motion data is stale | Camera panel data is stale | User runs `analyze --plot` between `solve` and a config change | The active-marker resolver in `load_active_camera_motion_or_fail` validates this; falling back to "skip camera and speed panels" with a warning is the right default | coder |
| JSON schema drift between Python builder and JS renderer | Renderer breaks silently | Python builder adds a key the renderer does not handle | Schema is documented in this plan and in the renderer JS file's top-of-file comment; both are updated in the same patch when the schema changes; WP-1C-1 / WP-2C-4 tests assert key presence | reviewer |
| Reviewer becomes the bottleneck on a 9-patch chain | Slow merge | Review backlog | Patches are independently reviewable; M1 patches can merge before M2 begins | orchestrator |

## Rollout and release checklist

- [ ] M1 patches merged; `analyze --help` shows `--plot`; stub HTML
  lands under `tmp_path` in tests; embedded JSON parses; HTML contains
  no external URL strings.
- [ ] M2 patches merged; `analyze --plot` produces a real HTML on a
  sample solved video that renders in Safari, Chrome, and Firefox;
  hover, drag-zoom, double-click reset, and series-toggle all
  verified manually; behavioral and structural tests pass.
- [ ] M3 patches merged; mode docs reflect the new flag; changelog
  entry present; in-repo source draft replaced with this plan;
  integration test exercises all three degradation paths.
- [ ] Fresh-venv smoke check:
  `python3 -m venv /tmp/_plot_venv && source /tmp/_plot_venv/bin/activate
  && pip install -r pip_requirements.txt && python3 track_runner.py
  analyze <stem> --plot` produces the HTML and it opens in a browser
  (no matplotlib install required).
- [ ] Repo-wide test gate green:
  `source source_me.sh && python3 -m pytest tests/`.

## Documentation close-out requirements

- `docs/modes/ANALYZE.md`: hand-written paragraph above AUTO HELP
  block; AUTO HELP regenerated. Owner: WP-3A-1 (planner).
- `docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md`: new "Diagnostic report"
  section. Owner: WP-3A-2 (planner).
- `docs/ENCODE_DESIGN.md`: one-line cross-link. Owner: WP-3A-3
  (planner).
- `docs/CHANGELOG.md`: entry under today's date in
  "### Additions and New Features". Owner: WP-3B-1 (planner).
- `docs/active_plans/ANALYZE_MODE_PLOTS.md`: replaced in place with
  this plan during M3 (WP-3B-2). After M3 merges, the file is moved
  to `docs/archive/` per repo convention. Owner: maintainer
  (post-merge).
- The canonical plan file is now
  `docs/active_plans/ANALYZE_MODE_PLOTS.md` (this file). The
  session-local working draft at
  `~/.claude/plans/groovy-singing-neumann.md` is superseded.

## Decisions resolved during planning

These were open in the source draft and were closed by Neil during
plan construction. Recording here so M2 implementers do not
re-litigate them.

1. **Output format.** One self-contained HTML file per video. No PNG,
   no SVG, no separate JSON sidecar by default.
2. **Render path.** Embedded JSON + inlined vanilla-JS canvas
   renderer. No CDN, no external library, no template engine. The
   report opens correctly years later from an archived folder.
3. **No runtime plotting dependency.** matplotlib stays dev-only and
   is not promoted to runtime. The report path is
   runtime-dependency-free.
4. **No dashboard.** The HTML page itself is the dashboard. The four
   panels appear in document order with short interpretive notes.
5. **Smoothing presentation.** The runner-speed panel and the
   zoom-stability panel both show raw and a centered running-mean
   overlay. The raw series is never hidden.
6. **Sub-sampling.** None. Plot every frame. Completeness is the
   contract.
7. **`torso_height_multiple` source.** Read from
   `tr_config/<stem>.yaml` at write time, used only for the reference
   line on the zoom-multiple panel. The panel always renders even
   when the YAML is missing the key.
8. **Camera-motion data terminology.** It is data on disk in `.npz`
   files, not a cache. No use of "cache" in user-facing language.
9. **Source-draft handling.** Replace
   `docs/active_plans/ANALYZE_MODE_PLOTS.md` in place with this plan
   during M3 close-out (WP-3B-2). After merge, archive normally.
10. **CLI flag name.** `--plot` retained for minimal CLI surface.
    Help text updated to "write HTML diagnostic report".
11. **Encode-quality scope.** This report exists to give the user
    insight into the encoded video. Tracker-quality signals are out
    of scope.
12. **Zoom-bouncing visualization.** A dedicated zoom-stability panel
    plots raw `crop_h` and a 9-frame centered running mean on the
    same axis; the gap is the bouncing magnitude. The 9-frame window
    matches the ~6-7 frame time constant of the 0.15 default
    `crop_post_smooth_size_strength` EMA per the 2026-05-02
    changelog.
13. **Crop-center panel deferred (and dropped, not on roadmap).** The
    four core panels are enough for the first version. If a real
    drift bug surfaces later that they do not explain, file a separate
    plan adding a crop-center panel then.
14. **Renderer interaction set.** Hover, drag-zoom on x-range,
    double-click reset, per-panel checkbox toggles, synced x-range,
    nan -> gap. Lasso, annotations, export buttons, themes, Y-zoom,
    panning, per-panel config UI are explicitly out of scope.
15. **Renderer JS asset location.** `data/js/analyze_report_renderer.js`
    at repo root. Introduces a new top-level `data/` directory and a
    `data/js/` subfolder for non-Python build-time assets. This is a
    new convention in this repo (existing data files like
    `track_runner/track_runner.config.yaml` live inside the package).
    Rationale: the renderer is a build-time asset, not part of the
    `track_runner` package surface; a clearly-named central data
    location signals "this is data, not code." The Python loader uses
    `git rev-parse --show-toplevel` (per `docs/REPO_STYLE.md`) to
    locate the repo root and read the file. The output HTML inlines
    the JS verbatim, so the asset file is needed at write time only;
    distributed HTML reports remain fully self-contained.
16. **`_running_mean_nan_aware` location.** Keep it private to
    `track_runner/analyze_report.py`. Promote to a shared numeric
    utility module only when a second caller appears.
17. **Default `<details>` state.** All four panels open by default
    (`<details open>`). The user opens the report intending to see
    the panels; collapsing is opt-in per session.

## Open questions and decisions needed

None. All planning-stage decisions are recorded in the section above.
