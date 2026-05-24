# CLI argument audit (proposed renames not yet shipped)

Owner: user (sole maintainer). Date: 2026-05-24. Source plan:
`~/.claude/plans/distributed-stirring-church.md`.

This document audits every CLI flag in the track_runner tool across all
eight subcommands (`seed`, `edit`, `target`, `solve`, `refine`, `encode`,
`analyze`, `setup`), identifies inconsistencies, and records the
unified single-letter scheme approved by the user in the 2026-05-23
planning session.

No code in `track_runner/cli_args.py` has been changed by this audit.
Implementation lives in a follow-up rename plan.

## Evidence labeling

Every cell in the inventory and reconciliation tables carries one label:

- `OBSERVED`: present in [../../../track_runner/cli_args.py](../../../track_runner/cli_args.py)
  or [../../../track_runner/ui/base_controller.py](../../../track_runner/ui/base_controller.py)
  at HEAD on 2026-05-24. Line ranges cited inline.
- `PROPOSED`: recommendation in this audit, not yet approved.
- `APPROVED`: confirmed by user in the 2026-05-23 planning session.
  Recorded here as "approved, not yet shipped"; implementation lives
  in the follow-up rename plan.

## Validation artifact

The live argparse surface is dumped to
[cli_argparse_dump.txt](cli_argparse_dump.txt). Regenerate with:

```
source source_me.sh && python3 tools/dump_cli_help.py > docs/active_plans/audits/cli_argparse_dump.txt
```

Cross-check command (every flag in the dump must appear in an OBSERVED
row below; PROPOSED/APPROVED rows must not appear in the dump yet):

```
git ls-files docs/active_plans/audits/cli_argparse_dump.txt | xargs cat | grep -E "^\s+-[a-zA-Z],?\s|^\s+--[a-z]" | sort -u
```

Inventory parity is a doc-review checklist, not a CI gate. Per
[docs/PYTEST_STYLE.md](../../PYTEST_STYLE.md), asserting on
collections of argparse flags is fragile and rots fast.

## Current state inventory (OBSERVED)

Origin: `track_runner/cli_args.py` lines 230-468 (parser construction)
and `track_runner/ui/base_controller.py` lines 40-53 (shared
`-S/--start`).

### Global flags (OBSERVED, cli_args.py:246-279)

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| `-i` | `--input` | input_file | str | required | OBSERVED |
| `-c` | `--config` | config_file | str | None | OBSERVED |
| `-d` | `--debug` | debug | flag | False | OBSERVED |
| `-w` | `--workers` | workers | int | None | OBSERVED; None = half CPU |
| (none) | `--time-range` | time_range | str | None | OBSERVED; "START:END" |

### Shared interactive (base_controller, applied to seed/edit/target/analyze)

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| `-S` | `--start` | start_time | float | None | OBSERVED; uppercase S |

### `seed` mode (OBSERVED, cli_args.py:284-288)

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| `-I` | `--seed-interval` | seed_interval | float | 10.0 | OBSERVED; hardcoded |
| `-S` | `--start` | start_time | float | None | OBSERVED; shared |

### `edit` mode (OBSERVED, cli_args.py:291-295)

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| `-s` | `--severity` | severity | choice(high/medium/low) | None | OBSERVED |
| `-S` | `--start` | start_time | float | None | OBSERVED |

### `target` mode (OBSERVED, cli_args.py:298-320)

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| (none) | `--race-start` | target_race_start | flag | False | OBSERVED; mutex |
| (none) | `--from-analyze` | target_from_analyze | flag | False | OBSERVED; mutex |
| `-s` | `--severity` | severity | choice | None | OBSERVED |
| `-t` | `--top` | top_n | int | None | OBSERVED |
| `-g` | `--gaps` | gap_top_n | int | None | OBSERVED |
| `-I` | `--seed-interval` | seed_interval | float | 10.0 | OBSERVED |
| `-S` | `--start` | start_time | float | None | OBSERVED |

### `solve` mode (OBSERVED, cli_args.py:323-368)

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| `-y` | `--yes` | assume_yes | flag | False | OBSERVED; mutex prompt |
| (none) | `--keep` | keep_prior | flag | False | OBSERVED; mutex prompt |
| (none) | `--upgrade` | upgrade | flag | False | OBSERVED; mutex prompt |
| `-f` | `--full` | full_solve | flag | False | OBSERVED; mutex stage |
| `-H` | `--hermite-only` | hermite_only | flag | False | OBSERVED; mutex stage |
| (none) | `--bin N` | bin_factor | int | 1 | OBSERVED; mutex bin |
| (none) | `--auto-bin [H]` | auto_bin_target | int | None (const 480) | OBSERVED; mutex bin |

### `refine` mode (OBSERVED, cli_args.py:371-385)

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| `-f` | `--full` | full_solve | flag | False | OBSERVED; mutex |
| `-H` | `--hermite-only` | hermite_only | flag | False | OBSERVED; mutex |
| (none) | `--bin N` | bin_factor | int | 1 | OBSERVED |
| (none) | `--auto-bin [H]` | auto_bin_target | int | None | OBSERVED |

### `encode` mode (OBSERVED, cli_args.py:116-226 via `_add_encode_args`)

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| `-o` | `--output` | output_file | str | None | OBSERVED |
| (none) | `--aspect` | aspect | str | None | OBSERVED |
| (none) | `--keep-temp` | keep_temp | flag | False | OBSERVED |
| `-F` | `--encode-filters` | encode_filters | str | None | OBSERVED |
| (none) | `--no-filters` | no_filters | flag | False | OBSERVED; mutex with -F |
| (none) | `--mp4` | mp4 | flag | False | OBSERVED |
| (none) | `--allow-offcenter-crop` | allow_offcenter_crop | flag | False | OBSERVED |
| (none) | `--draw-tracking-overlay` | draw_tracking_overlay | flag | False | OBSERVED; overlay mutex |
| (none) | `--draw-debug-overlay` | draw_debug_overlay | flag | False | OBSERVED; overlay mutex |
| (none) | `--draw-velocity-arrow` | draw_velocity_arrow | flag | False | OBSERVED; needs tier |
| (none) | `--torso-multiple` | torso_multiple | float | None | OBSERVED |
| `-r` | `--output-resolution` | output_resolution | str | None | OBSERVED; "WxH" |
| (none) | `--crf` | crf | int | None | OBSERVED |
| (none) | `--video-codec` | video_codec | str | None | OBSERVED |

### `analyze` mode (OBSERVED, cli_args.py:401-424)

| Short | Long | Dest | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| (none) | `--aspect` | aspect | str | None | OBSERVED |
| `-s` | `--seed` | analyze_seed | flag | False | OBSERVED; **COLLISION** with edit/target -s |
| `-t` | `--top` | top_n | int | None | OBSERVED; implies `-s` |
| `-g` | `--gaps` | gap_top_n | int | None | OBSERVED |
| `-p` | `--plot` | write_plots | flag | False | OBSERVED |
| `-S` | `--start` | start_time | float | None | OBSERVED |

### `setup` mode (OBSERVED, cli_args.py:427-429)

No flags.

## Inconsistencies and findings

| ID | Finding | Severity |
| --- | --- | --- |
| F1 | `-s` collision: severity (edit/target) vs seed (analyze) | high |
| F2 | `-f` (solve/refine `--full`) vs `-F` (encode `--encode-filters`): case-distinct, typo-prone | medium |
| F3 | Uppercase short flags chosen ad-hoc (`-S`, `-I`, `-F`, `-H`); no rule documented | low |
| F4 | Missing short flags on common encode toggles (`--aspect`, `--mp4`, `--crf`, `--no-filters`) | medium |
| F5 | Asymmetric short-flag coverage in solve prompt mutex (`-y` has short, `--keep`/`--upgrade` do not) | medium |
| F6 | Hardcoded `seed_interval=10.0` default belongs in config | low |
| F7 | `--bin` / `--auto-bin` lack short flags despite frequent use in batch scripts | low |
| F8 | `--time-range` global flag has no short form | low |
| F9 | `-F none` aliases `--no-filters`; one spelling should win | low |
| F10 | `-r` encode-only; potential future collision if refine wants a short | trivial |
| F11 | `-d/--debug` (logs) confusable with `--draw-*` (overlays) | trivial |
| F12 | Mutex-group convention undocumented | doc gap |

## Unified scheme (APPROVED, not yet shipped)

All entries below are APPROVED by user in the 2026-05-23 planning
session and are not present in `cli_args.py` at HEAD on 2026-05-24.
The follow-up rename plan owns implementation.

### Rules

1. Lowercase short letter == one global meaning across modes.
2. Uppercase short letter == related-but-distinct counterpart of the
   lowercase form. Examples: `-s` severity vs `-S` start; `-f` full
   vs `-F` filters.
3. Reserved global lowercase letters: `i`, `d`, `w`, `h` (`h` is
   argparse help). `-c` is dropped; `c` becomes available.
4. Long flags may exist without a short flag. Adding a short requires
   evidence of frequent toggle use (`docs/PYTHON_STYLE.md` argparse
   minimalism).
5. Mutex groups documented inline: `# mutex: stage`, `# mutex: bin`,
   `# mutex: overlay-tier`, etc.

### Reconciliation table

| Mode | Current (OBSERVED) | Proposed (APPROVED) | Rationale |
| --- | --- | --- | --- |
| global | `-c/--config` | drop `-c`; keep `--config` long only | D7: user rarely uses `--config`; frees `c` |
| global | `-d/--debug` | `-d/--debug-logs` | D5: disambiguates from `--draw-*` overlays |
| global | `--time-range` | `-T/--time-range` | F8 |
| edit | `-s/--severity high\|medium\|low` | add `--high`, `--medium`, `--low` aliases | reads better than `-s high`; keep `-s/--severity` |
| analyze | `-s/--seed` | drop `-s` short; keep `--seed` long only | D1; resolves F1 |
| solve | `--keep` | `-k/--keep` | F5 |
| solve | `--upgrade` | `-u/--upgrade` | F5 |
| solve/refine | `--bin N` | `-b/--bin N` | F7 |
| solve/refine | `--auto-bin` | `-B/--auto-bin`; **default-on at target 480** (D9 behavior change) | user request; `--bin 1` opts out |
| seed/target | `-I/--seed-interval` default 10.0 | move default to config; flag has no code default | D4; F6 |
| encode | `--mp4` | `-m/--mp4` | F4 |
| encode | `--crf` | `-q/--crf` | F4; `q` for quality |
| encode | `--aspect` | `-a/--aspect` | F4 |
| encode | `--video-codec` | `-v/--video-codec` | F4 |
| encode | `--torso-multiple` | `-Z/--torso-multiple` | D8; uppercase Z (avoid `z` which reads as zoom) |
| encode | `--keep-temp` | remove entirely | rarely used; temp cleanup default-on is correct |
| encode | `--draw-tracking-overlay` | `-l/--draw-tracking` | F4; `l` for layer |
| encode | `--draw-debug-overlay` | `-D/--draw-debug` | F4; uppercase D, distinct from `-d` |
| encode | `--draw-velocity-arrow` | `-V/--draw-velocity-arrow` | F4 |
| encode | `--no-filters` | remove; use `-F none` | D3; F9 |
| encode | `--allow-offcenter-crop` | long-only | rare flag |

### Single-letter flag summary (PROPOSED/APPROVED final state)

Columns: g=global, s=seed, e=edit, t=target, S=solve, R=refine, E=encode,
a=analyze. Cell shows long flag in that mode. `-` means unused. Identical
letter across columns means identical meaning.

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

Edit-mode severity alias flags (no short letter, `action="store_const"`):
`--high`, `--medium`, `--low` all map to `dest=severity`.

Reserved / unused letters: `-c` (dropped), `-h` (argparse help), `-n`,
`-e`, `-x`, `-j`, `-A`, `-C`, `-E`, `-G`, `-J`, `-K`, `-L`, `-M`, `-N`,
`-O`, `-P`, `-Q`, `-R`, `-U`, `-W`, `-X`, `-Y`.

## Decisions

Decision records live in
[../decisions/cli_unification_decisions.md](../decisions/cli_unification_decisions.md).
D1-D5, D7-D9 APPROVED on 2026-05-23. D6 (rename deprecation policy)
DEFERRED to the follow-up rename plan; default if unspecified at
rename time is **hard-cut**.

## Follow-up

The rename plan will land in `~/.claude/plans/<slug>-cli-rename.md`.
That plan owns the actual edits to
[../../../track_runner/cli_args.py](../../../track_runner/cli_args.py),
the changelog entries for each rename group, and the validation
re-dump.
