# CLI unification decisions

Owner: user (sole maintainer). Date opened: 2026-05-23. Date recorded:
2026-05-24.

Companion to
[cli_argument_audit.md](cli_argument_audit.md).
All "APPROVED" decisions were confirmed by user in the 2026-05-23
planning session and remain unimplemented as of 2026-05-24; the
follow-up rename plan owns the code edits.

| ID | Decision | Status | Resolution |
| --- | --- | --- | --- |
| D1 | `-s` collision on analyze (`--seed`) vs edit/target (`--severity`) | APPROVED 2026-05-23 | Drop `-s` short on analyze; keep `--seed` long only. |
| D2 | Add short flags broadly on encode mode | APPROVED 2026-05-23 | Add `-m/--mp4`, `-q/--crf`, `-a/--aspect`, `-v/--video-codec`, `-l/--draw-tracking`, `-D/--draw-debug`, `-V/--draw-velocity-arrow`, `-Z/--torso-multiple`. |
| D3 | `--no-filters` vs `-F none` dual spelling | APPROVED 2026-05-23 | Remove `--no-filters`; use `-F none` as the single spelling. |
| D4 | Hardcoded `seed_interval=10.0` | APPROVED 2026-05-23 | Move default to config; flag keeps no code default. |
| D5 | `-d/--debug` rename | APPROVED 2026-05-23 | Rename to `-d/--debug-logs` to disambiguate from `--draw-*`. |
| D6 | Rename deprecation policy | DEFERRED-TO-RENAME-PLAN | User picks "hard-cut" or "one-release stderr-warned alias" at the start of the follow-up rename plan. Default if unspecified: hard-cut. |
| D7 | `-c/--config` short flag | APPROVED 2026-05-23 | Drop `-c` short flag; keep `--config` long form only. User: "I never use --config." |
| D8 | `--torso-multiple` short letter | APPROVED 2026-05-23 | Use `-Z/--torso-multiple` (uppercase Z; avoid `z` which reads as zoom). |
| D9 | `--auto-bin` default-on behavior change | APPROVED 2026-05-23 | `auto_bin_target` defaults to 480 instead of None; `--bin 1` becomes the explicit opt-out. Documented breaking change. |

## Follow-up

The rename plan will land in `~/.claude/plans/<slug>-cli-rename.md`
and resolve D6 in its first work package.
