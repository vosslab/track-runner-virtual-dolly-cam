# CLI argument audit and unification

## Context

The track_runner CLI in [cli_args.py](../../track_runner/cli_args.py) spans **eight** subcommands (`seed`, `edit`, `target`, `solve`, `refine`, `encode`, `analyze`, `setup`) plus global flags. The file has accreted over many feature passes. Some single-letter flags collide in meaning across modes, some long flags lack short forms while peer flags have them, and some defaults live in code that should defer to config per `docs/PYTHON_STYLE.md` argparse minimalism. The user asked for a complete audit of CLI arguments, default settings, and single-letter flags across all modes, with inconsistencies identified and a unified scheme proposed.

Goal: produce a documentation-only audit + reconciliation plan. No code changes in this plan; the follow-up plan executes renames once the user approves the unified scheme.

## Objectives

- Enumerate every CLI flag (global + per-mode) with its short form, long form, dest, type, default, and origin (cli_args.py vs base_controller.py).
- Identify single-letter-flag collisions where the same letter means different things across modes.
- Identify long flags that should have a short flag (peer-consistency gap) and short flags that should be removed (argparse-minimalism violation).
- Identify defaults that hardcode tunables better placed in config.
- Propose one unified single-letter scheme with documented rules (case, reserved letters, scope).
- Produce a migration table: current -> proposed, with deprecation strategy for any flag that must change letter.

## Design philosophy

Two competing pressures shape this audit. Consistency across modes (one letter == one meaning) reduces memorization cost; backward compatibility preserves muscle memory and scripted invocations users have already written. This plan favors consistency for **collisions and missing-short gaps** but leaves correctly-distinct flags alone. Rejected alternative: full ground-up rename to a "every long flag gets a short flag" scheme -- rejected because argparse minimalism (`docs/PYTHON_STYLE.md`) says only frequently-changed flags need short forms, and many encode-mode overrides (`--crf`, `--video-codec`, `--torso-multiple`) are set once and forgotten. Cites `docs/REPO_STYLE.md` "long-term over short-term" -- accept a small migration cost now to remove permanent confusion later.

## Scope

In scope:

- All argparse setup in `track_runner/cli_args.py`.
- Shared interactive args in `track_runner/ui/base_controller.py:add_argparse_args`.
- Validation logic at the bottom of `parse_args()`.

Out of scope:

- Argparse setup for any standalone tool under `tools/` (audited separately if requested).
- Config-file schema (`tr_config/*.yaml`), even where a CLI flag overrides a config key.
- Subcommand semantics, mode names, or any behavior beyond flag spelling and defaults.
- Tests. No committed test changes in this plan or its follow-up rename plan; argparse-inventory pytests would be fragile per [PYTEST_STYLE.md](../PYTEST_STYLE.md). Ad-hoc local tests during rename work are allowed but must not be committed.

## Non-goals

- Do not redesign mode behavior.
- Do not move flags between modes (e.g. promoting an `encode` flag to global) in this plan; that is the user's decision after seeing the audit.
- Do not add new flags. The point is fewer / clearer, not more.

## Evidence labeling convention

Three tiers used throughout this doc to keep observed facts separate from proposals and approvals:

- **OBSERVED**: present in [cli_args.py](../../track_runner/cli_args.py) or [base_controller.py](../../track_runner/ui/base_controller.py) at HEAD on 2026-05-23. Every OBSERVED row cites a line range.
- **PROPOSED**: a recommendation in this plan, not yet approved.
- **APPROVED**: user confirmed in the 2026-05-23 planning session (D1-D5, D7-D9). Implementation is deferred to the follow-up plan; the audit doc records them as "approved, not yet shipped."

Owner for all entries: **user (sole maintainer)**.

## Current state inventory (OBSERVED, cli_args.py lines 230-468; base_controller.py lines 40-53)

### Global flags (OBSERVED, cli_args.py:246-279)

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| `-i` | `--input` | input_file | str | required | |
| `-c` | `--config` | config_file | str | None | |
| `-d` | `--debug` | debug | flag | False | OBSERVED. APPROVED rename to `--debug-logs` per D5; not yet shipped |
| `-w` | `--workers` | workers | int | None | None = half CPU |
| (none) | `--time-range` | time_range | str | None | "START:END" |

### Shared interactive (base_controller, applied to seed/edit/target/analyze)

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| `-S` | `--start` | start_time | float | None | uppercase S |

### `seed` mode

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| `-I` | `--seed-interval` | seed_interval | float | 10.0 | uppercase I; hardcoded default |
| `-S` | `--start` | start_time | float | None | shared |

### `edit` mode

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| `-s` | `--severity` | severity | choice(high/medium/low) | None | lowercase s |
| `-S` | `--start` | start_time | float | None | |

### `target` mode

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| (none) | `--race-start` | target_race_start | flag | False | mutex |
| (none) | `--from-analyze` | target_from_analyze | flag | False | mutex |
| `-s` | `--severity` | severity | choice | None | |
| `-t` | `--top` | top_n | int | None | |
| `-g` | `--gaps` | gap_top_n | int | None | |
| `-I` | `--seed-interval` | seed_interval | float | 10.0 | |
| `-S` | `--start` | start_time | float | None | |

### `solve` mode

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| `-y` | `--yes` | assume_yes | flag | False | mutex group A |
| (none) | `--keep` | keep_prior | flag | False | mutex group A |
| (none) | `--upgrade` | upgrade | flag | False | mutex group A |
| `-f` | `--full` | full_solve | flag | False | mutex group B (lowercase f) |
| `-H` | `--hermite-only` | hermite_only | flag | False | mutex group B (uppercase H) |
| (none) | `--bin N` | bin_factor | int | 1 | mutex group C |
| (none) | `--auto-bin [H]` | auto_bin_target | int | None (const 480) | mutex group C |

### `refine` mode

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| `-f` | `--full` | full_solve | flag | False | mutex |
| `-H` | `--hermite-only` | hermite_only | flag | False | mutex |
| (none) | `--bin N` | bin_factor | int | 1 | |
| (none) | `--auto-bin [H]` | auto_bin_target | int | None | |

### `encode` mode

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| `-o` | `--output` | output_file | str | None | |
| (none) | `--aspect` | aspect | str | None | |
| (none) | `--keep-temp` | keep_temp | flag | False | |
| `-F` | `--encode-filters` | encode_filters | str | None | uppercase F |
| (none) | `--no-filters` | no_filters | flag | False | mutex with -F |
| (none) | `--mp4` | mp4 | flag | False | |
| (none) | `--allow-offcenter-crop` | allow_offcenter_crop | flag | False | |
| (none) | `--draw-tracking-overlay` | flag | False | overlay mutex |
| (none) | `--draw-debug-overlay` | flag | False | overlay mutex |
| (none) | `--draw-velocity-arrow` | flag | False | requires one of above |
| (none) | `--torso-multiple` | torso_multiple | float | None | |
| `-r` | `--output-resolution` | output_resolution | str | None | "WxH" |
| (none) | `--crf` | crf | int | None | |
| (none) | `--video-codec` | video_codec | str | None | |

### `analyze` mode

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| (none) | `--aspect` | aspect | str | None | |
| `-s` | `--seed` | analyze_seed | flag | False | **COLLISION**: `-s` is severity in edit/target |
| `-t` | `--top` | top_n | int | None | implies `-s` |
| `-g` | `--gaps` | gap_top_n | int | None | |
| `-p` | `--plot` | write_plots | flag | False | |
| `-S` | `--start` | start_time | float | None | |

### `setup` mode

No flags.

## Inconsistencies and findings

### F1. `-s` collision (severity vs seed)

`edit -s` and `target -s` mean `--severity` (filter level). `analyze -s` means `--seed` (open seeding UI after report). Same letter, different semantics, partially-overlapping invocation paths (a user running `target -s high` then `analyze -s` has to context-switch). Severity: high.

### F2. `-f` vs `-F` near-miss

`solve -f` / `refine -f` = `--full`. `encode -F` = `--encode-filters`. Case-distinct, but `-f`/`-F` adjacency invites typos with destructive consequences (the wrong full-solve runs Stage 5 over every interval). Severity: medium.

### F3. Uppercase single-letter inconsistency

Uppercase short flags exist for `-S/--start`, `-I/--seed-interval`, `-F/--encode-filters`, `-H/--hermite-only`. No documented rule for when uppercase is chosen. The pattern appears to be "the lowercase letter was already taken globally or in the same subparser," but `-I` could have been `-N` (interval/Number), `-H` could have been `-q` (quick). Severity: low (documentation gap, not a bug).

### F4. Missing short flags on common encode toggles

`--aspect`, `--mp4`, `--crf`, `--no-filters` are all flipped during routine encode iteration. Peer flags `-o`, `-F`, `-r` have shorts. Argparse minimalism (`docs/PYTHON_STYLE.md`) says "Only add flags users frequently need to change between runs"; these qualify. Severity: medium.

### F5. Asymmetric short-flag coverage in solve prompt mutex

`-y/--yes` has a short flag; `--keep` and `--upgrade` do not. All three are equally scriptable. Severity: medium.

### F6. Hardcoded `seed_interval=10.0` default

`-I/--seed-interval` defaults to `10.0` in code. Per `docs/PYTHON_STYLE.md` "Hardcode instead" rule for argparse, that defaults that vary per-video belong in config (`tr_config/*.yaml`); if always 10 s, hardcode without a flag. Currently both. Severity: low (works correctly; design clarity issue).

### F7. `--bin` / `--auto-bin` lack short flags despite being a common solve/refine toggle

These appear in scripted batch runs. Severity: low.

### F8. `--time-range` has no short flag and is global

Common enough during diagnostic work to warrant `-T` or `-r` (but `-r` is taken by encode). Severity: low.

### F9. `-F none` aliases `--no-filters`

Documented dual spelling. Adds parser complexity and a validation rule (`--no-filters cannot be combined with -F`). One spelling should win. Severity: low.

### F10. `-r` overloaded across modes

`-r/--output-resolution` is encode-only today. If `refine` ever wants a short flag (e.g., `-r` for "refine target"), collision. No active bug, just future fragility. Severity: trivial.

### F11. `-d/--debug` conflated with `--draw-*`

`-d` is the debug log toggle. The three `--draw-*` flags burn overlays into the output. Easy to confuse on first read. Help text already addresses this; no rename needed but a `--debug-logs` long form would disambiguate. Severity: trivial.

### F12. Mutex-group convention undocumented

Solve has three separate mutex groups (prompt, stage, bin). Refine has two. Encode has overlay. Target has submode. No doc captures the rule "stage flags are mutex; container flags are mutex; etc." Future contributors will guess. Severity: documentation gap.

## Proposed unified scheme (PROPOSED + APPROVED, not yet shipped)

All rows in this section are PROPOSED unless flagged APPROVED. APPROVED rows are confirmed by the user in the 2026-05-23 planning session; they remain unimplemented and are not present in `cli_args.py` at HEAD.

### Rules

1. **Lowercase short letter = one global meaning.** A given lowercase letter, if assigned, means the same thing in every mode that uses it.
2. **Uppercase short letter = same letter, "more of" or "alternate form".** Example: `-s` severity, `-S` start; `-f` full, `-F` filters. This is the de-facto rule; document it.
3. **Reserved global lowercase letters (cannot be reused in subparsers):** `i, c, d, w, h` (`-h` is argparse's help). Currently held by `-i/--input`, `-c/--config`, `-d/--debug`, `-w/--workers`.
4. **Long flag may exist without a short flag** if the option is rarely toggled (argparse minimalism). Adding a short flag requires justification ("flipped often enough that typing `--mp4` is friction").
5. **Mutex groups documented inline** with a one-line comment naming the group: `# mutex: stage`, `# mutex: bin`, `# mutex: overlay-tier`.

### Reconciliation table (revised per user feedback 2026-05-23)

Letters chosen for collision-free coverage. Long flags unchanged unless noted.

| Mode | Current | Proposed | Rationale |
| --- | --- | --- | --- |
| global | `-c/--config` | drop `-c`; keep `--config` long only | user: "never use --config, low priority for a single letter" -- frees `c` for future use |
| global | `--time-range` | `-T/--time-range` | F8 |
| edit | `-s/--severity high\|medium\|low` | add `--high`, `--medium`, `--low` aliases via `action="store_const"` | user request: typing `edit --high` reads better than `edit -s high`; keep `-s/--severity` |
| analyze | `-s/--seed` | drop `-s` short on analyze; use `--seed` long only | F1 collision with edit/target severity |
| solve | `--keep` (no short) | `-k/--keep` | F5 |
| solve | `--upgrade` (no short) | `-u/--upgrade` | F5 |
| solve/refine | `--bin N` | `-b/--bin N` | F7; user explicit |
| solve/refine | `--auto-bin` | **default-on**: `auto_bin_target` defaults to 480 instead of None; `--bin 1` becomes the explicit opt-out for "no binning" | user request: auto-bin on by default; documented breaking change |
| seed/target | `-I/--seed-interval` default 10.0 | move default to config; keep flag without code default | F6 |
| encode | `--mp4` (no short) | `-m/--mp4` | F4; user "encode needs more single-letter options" |
| encode | `--crf` (no short) | `-q/--crf` | F4; `q` for "quality" |
| encode | `--aspect` (no short) | `-a/--aspect` | F4 |
| encode | `--video-codec` (no short) | `-v/--video-codec` | F4 |
| encode | `--torso-multiple` (no short) | `-Z/--torso-multiple` | F4; uppercase `Z` per user choice (distances from `z`-for-zoom) |
| encode | `--keep-temp` | **remove flag entirely** | user: rarely used, not worth a short flag; temp cleanup default-on is correct |
| encode | `--draw-tracking-overlay` | `-l/--draw-tracking` (review-tier overlay) | F4; `l` for "layer" (avoid `-t` which is `--top` elsewhere) |
| encode | `--draw-debug-overlay` | `-D/--draw-debug` | F4; uppercase D distinct from global `-d/--debug-logs` |
| encode | `--draw-velocity-arrow` | `-V/--draw-velocity-arrow` | F4 |
| encode | `--no-filters` | remove; require `-F none` | F9 single spelling |
| encode | `--allow-offcenter-crop` (no short) | leave long-only | rare flag; argparse minimalism |

### Single-letter flag summary table (proposed final)

Letter columns: `g`=global, `s`=seed, `e`=edit, `t`=target, `S`=solve, `R`=refine, `E`=encode, `a`=analyze. Cell = long flag in that mode. `-` = unused. Same letter = same meaning across columns.

| Letter | g | s | e | t | S | R | E | a |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `-i` | --input | - | - | - | - | - | - | - |
| `-w` | --workers | - | - | - | - | - | - | - |
| `-d` | --debug-logs | - | - | - | - | - | - | - |
| `-T` | --time-range | - | - | - | - | - | - | - |
| `-S` | - | --start | --start | --start | - | - | - | --start |
| `-I` | - | --seed-interval | - | --seed-interval | - | - | - | - |
| `-s` | - | - | --severity | --severity | - | - | - | - |
| `-t` | - | - | - | --top | - | - | - | --top |
| `-g` | - | - | - | --gaps | - | - | - | --gaps |
| `-p` | - | - | - | - | - | - | - | --plot |
| `-y` | - | - | - | - | --yes | - | - | - |
| `-k` | - | - | - | - | --keep | - | - | - |
| `-u` | - | - | - | - | --upgrade | - | - | - |
| `-f` | - | - | - | - | --full | --full | - | - |
| `-H` | - | - | - | - | --hermite-only | --hermite-only | - | - |
| `-b` | - | - | - | - | --bin | --bin | - | - |
| `-B` | - | - | - | - | --auto-bin | --auto-bin | - | - |
| `-o` | - | - | - | - | - | - | --output | - |
| `-a` | - | - | - | - | - | - | --aspect | - |
| `-m` | - | - | - | - | - | - | --mp4 | - |
| `-q` | - | - | - | - | - | - | --crf | - |
| `-v` | - | - | - | - | - | - | --video-codec | - |
| `-r` | - | - | - | - | - | - | --output-resolution | - |
| `-F` | - | - | - | - | - | - | --encode-filters | - |
| `-l` | - | - | - | - | - | - | --draw-tracking | - |
| `-D` | - | - | - | - | - | - | --draw-debug | - |
| `-V` | - | - | - | - | - | - | --draw-velocity-arrow | - |
| `-Z` | - | - | - | - | - | - | --torso-multiple | - |

Reserved / unused: `-c` (dropped), `-h` (argparse help), `-n`, `-x`, `-j`, `-A`, `-C`, `-G`, `-J`, `-L`, `-M`, `-N`, `-O`, `-P`, `-Q`, `-U`, `-W`, `-X`, `-Y`. `-e` not used; reserve for future. `-h` is argparse help across all subparsers.

Edit-mode severity alias flags (no short letter, store_const equivalents): `--high`, `--medium`, `--low` -- all map to `dest=severity`.

### Letters that stay as-is

`-i, -c, -d, -w, -s, -S, -t, -g, -p, -y, -f, -F, -H, -I, -o, -r` -- already used and not in collision once F1 is fixed.

### Letters reserved (do not reuse)

`-h` argparse help. `-V` (proposed) reserved for future `--version`.

## Architecture boundaries

This plan is documentation-only. No production code change in this plan. The audit table and reconciliation table are the deliverable. A follow-up plan (out of scope here) executes the renames in `cli_args.py` and adds the deprecation shims.

Component mapping:

- **Audit doc**: lives under `docs/active_plans/audits/cli_argument_audit.md`.
- **Reconciliation table**: section of the audit doc; sourced from this plan.
- **Migration patch (follow-up plan only)**: touches `track_runner/cli_args.py` and `track_runner/ui/base_controller.py` only.

## Milestones

### Milestone 1 -- publish the audit doc

Owner: **user** (sole maintainer).
Workstream IDs: M1-W1 (single doer; doc-only).

Deliverable: `docs/active_plans/audits/cli_argument_audit.md` containing all eight subcommands, the OBSERVED inventory tables, findings F1-F12, and the PROPOSED/APPROVED reconciliation table. Each table cell labeled OBSERVED / PROPOSED / APPROVED per the convention above.

Validation artifact (required, not optional): generate `docs/active_plans/audits/cli_argparse_dump.txt` by running every subparser's `--help` and concatenating the output. A small helper script (~30 lines) under `tools/dump_cli_help.py` is the recommended path:

```
source source_me.sh && python3 tools/dump_cli_help.py > docs/active_plans/audits/cli_argparse_dump.txt
```

The script imports `track_runner.cli_args.parse_args`'s parser construction (refactor `parse_args` to expose a `_build_parser()` helper, OR call the existing parser by running `track_runner.py <mode> --help` for each mode and capturing stdout). The audit doc cross-checks every flag in `cli_argparse_dump.txt` against its OBSERVED inventory row; any flag in the dump that is not in the inventory is a closure-blocking miss.

Exit criteria:

- File exists and renders on GitHub.
- All **eight** subcommands covered (`seed`, `edit`, `target`, `solve`, `refine`, `encode`, `analyze`, `setup`).
- `cli_argparse_dump.txt` checked in; every flag in the dump appears in the OBSERVED inventory; reviewer can grep both artifacts and diff.
- `docs/CHANGELOG.md` entry under today's date noting the audit doc. Owner: user.
- Obvious follow-on: link the new doc from `docs/REPO_STYLE.md` if a CLI-style section is added; not required to close this milestone.

Parallel-plan ready: no -- single workstream; doc is one coherent artifact.

### Milestone 2 -- record decisions and spawn the rename plan

Owner: **user** (sole maintainer).
Workstream IDs: none (decision gate + handoff).

Deliverable A: `docs/active_plans/decisions/cli_unification_decisions.md` capturing D1-D9. D1-D5 and D7-D9 are recorded APPROVED with the 2026-05-23 timestamp. D6 (deprecation policy) has its own row with an explicit resolution path: user picks "hard-cut" or "one-release stderr-warned alias" at the start of the follow-up rename plan, not in this plan. Default if unspecified at rename time: hard-cut.

Deliverable B: open the follow-up plan `~/.claude/plans/<slug>-cli-rename.md` (slug to be assigned by the planning skill at creation time). That plan owns the actual `cli_args.py` edits, deprecation shims, test updates, and `docs/CHANGELOG.md` entries for each rename.

Exit criteria:

- D1-D9 each have an explicit row in `cli_unification_decisions.md` with status APPROVED or DEFERRED-TO-RENAME-PLAN.
- D6 has a documented resolution path even if unresolved at audit close.
- Follow-up rename plan exists at `~/.claude/plans/<slug>-cli-rename.md` with M1-W1 work packages for each rename group.
- Obvious follow-on: link the rename plan from `cli_unification_decisions.md`.

Parallel-plan ready: no -- gating decision.

## Decisions made (2026-05-23)

- D1 RESOLVED: drop `-s` short on `analyze`; keep `--seed` long only.
- D2 RESOLVED: add short flags broadly on encode (user said "encode needs more single-letter options"). See revised table.
- D3 RESOLVED: keep `-F none`; remove `--no-filters`.
- D4 RESOLVED: move `seed_interval=10.0` to config.
- D5 OPEN: `-d/--debug` -> `-d/--debug-logs` rename. Recommendation: leave alone; help text already explains.
- D6 OPEN: deprecation policy on rename. Recommendation: hard-cut.

## Decisions made (2026-05-23, second round)

- D5 RESOLVED: rename `-d/--debug` -> `-d/--debug-logs`.
- D7 RESOLVED: drop `-c` short flag; keep `--config` long form.
- D8 RESOLVED: use `-Z/--torso-multiple` (uppercase).
- D9 RESOLVED: auto-bin on by default (target 480); `--bin 1` opts out. Behavior change accepted.

## Open decisions (still need user input)

D6. **Rename deprecation policy.** When renaming a flag (e.g. `--debug` -> `--debug-logs`), support old spelling for one release with stderr warning, or hard-cut? Recommendation: hard-cut.

## Risk register

| Risk | Impact | Trigger | Owner | Mitigation |
| --- | --- | --- | --- | --- |
| Renaming flags breaks the user's shell history / saved scripts | medium | flag letter changes | user | publish reconciliation table BEFORE any code change; D6 governs deprecation |
| Audit doc drifts from live `cli_args.py` after future edits | low | new flag added without doc update | user | regenerate `cli_argparse_dump.txt` on every `cli_args.py` change; not enforced by test in this plan |
| User rejects the unified scheme | none | normal review | user | this is a decision plan; rejection just keeps current state |
| Reader confuses PROPOSED/APPROVED rows with current shipped behavior | medium | row labels missing or stale | user | every table cell in the audit doc carries an OBSERVED/PROPOSED/APPROVED label; doc title says "Audit (proposed renames not yet shipped)"; PROPOSED-vs-shipped grep returns zero false positives |
| Validation artifact (`cli_argparse_dump.txt`) drifts from the inventory table | medium | file regenerated but doc not updated | user | regenerate dump and re-diff before closing every CLI-touching PR |

## Documentation execution

- Patch 1: create `docs/active_plans/audits/cli_argument_audit.md` (Milestone 1).
- Patch 2: add `docs/CHANGELOG.md` entry under `## 2026-05-23` (today) in the **Decisions and Failures** section: "Published CLI argument audit; reconciliation pending user decision (D1-D6)."

No code patches in this plan. Code patches deferred to a follow-up plan written after Milestone 2.

## Verification

This plan ships documentation only. Verification artifacts and commands:

1. Audit doc exists at `docs/active_plans/audits/cli_argument_audit.md`.
2. Argparse dump artifact exists at `docs/active_plans/audits/cli_argparse_dump.txt`. Regeneration command:

   ```
   source source_me.sh && python3 tools/dump_cli_help.py > docs/active_plans/audits/cli_argparse_dump.txt
   ```

   The helper script either (a) imports the parser via a refactored `track_runner.cli_args._build_parser()` and calls `parser.format_help()` for each subparser, or (b) shells out to `track_runner.py <mode> --help` for each of the eight modes plus the global parser. Option (a) is preferred (no subprocess; deterministic; survives missing video-file requirement).

3. Cross-check command: every flag in `cli_argparse_dump.txt` must appear in an OBSERVED row of the audit doc. A one-line follow-up check:

   ```
   git ls-files docs/active_plans/audits/cli_argparse_dump.txt | xargs cat | grep -E "^\s+-[a-zA-Z],?\s|^\s+--[a-z]" | sort -u
   ```

   Compare manually to the OBSERVED rows. Ad-hoc dev-time pytest is fine during the rename plan (run, observe, discard), but **do not commit a permanent inventory pytest**: per [PYTEST_STYLE.md](../PYTEST_STYLE.md), assertions on collections of argparse flags and required-key lists are fragile and rot fast. Inventory parity stays a doc-review checklist, not a CI gate.

4. `docs/CHANGELOG.md` carries the new entry under `## 2026-05-23` in **Additions and New Features** (audit doc) and **Decisions and Failures** (D1-D5, D7-D9 approvals; D6 deferred).

5. No production code, no test, no `tr_config/*.yaml` modified in this plan. Confirm with `git status` after Milestone 1.

End-to-end: not applicable; documentation-only plan.

## Completion criteria

- Milestone 1 exit criteria met (audit doc published + changelog entry).
- Milestone 2 exit criteria met (user decision recorded in `docs/active_plans/decisions/cli_unification_decisions.md`).
- D1-D6 each have an explicit user answer.
- Follow-up rename plan either spawned (if approved) or recorded as rejected.
