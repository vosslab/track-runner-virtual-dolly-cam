#!/usr/bin/env bash
#
# Golden-output baseline gate for the blob_walk_v2 windowed walker.
#
# This is a non-browser E2E gate (see docs/E2E_TESTS.md). It walks EXACTLY 4
# fixed seed-to-seed intervals (2 per video across two videos) and proves a
# refactor did not change walker output. It must run after every refactor
# patch, so it is built around 4 small fixed intervals only -- a full walk of
# every interval takes 25+ minutes and is FORBIDDEN here.
#
# Modes:
#   (default)            Walk fresh into a scratch dir, then compare vs the
#                        committed snapshot. Exits non-zero on any diff.
#   --update-baseline    Walk and write/overwrite the snapshot, then exit 0.
#
# Usage:
#   bash tests/e2e/e2e_blob_walk_baseline.sh
#   bash tests/e2e/e2e_blob_walk_baseline.sh --update-baseline
#
# Exit codes: 0 = pass (or baseline updated), non-zero = diff or error.

# Note: -u (nounset) is intentionally omitted. source_me.sh sources the user
# profile (bash_completion) which references unbound vars like PS1; -u would
# abort the gate before the walker ever runs.
set -eo pipefail

# Resolve repo root from this script's location (tests/e2e/ -> repo root).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

HARNESS_PY="${SCRIPT_DIR}/e2e_blob_walk_baseline.py"
BASELINE_DIR="${SCRIPT_DIR}/baseline_blob_walk"
SCRATCH_DIR="/tmp/e2e_blob_walk_fresh"

# Bring the repo Python environment online (Python 3.12, PYTHONPATH, etc.).
# shellcheck disable=SC1091
source "${REPO_ROOT}/source_me.sh"

UPDATE_BASELINE=0
if [ "${1:-}" = "--update-baseline" ]; then
	UPDATE_BASELINE=1
fi

if [ "${UPDATE_BASELINE}" -eq 1 ]; then
	echo "[gate] --update-baseline: walking 4 fixed intervals into snapshot"
	echo "[gate] snapshot dir: ${BASELINE_DIR}"
	rm -rf "${BASELINE_DIR}"
	python3 "${HARNESS_PY}" walk --output-dir "${BASELINE_DIR}"
	echo "[gate] row counts in snapshot:"
	python3 "${HARNESS_PY}" rows --dir "${BASELINE_DIR}"
	echo "[gate] baseline snapshot updated: ${BASELINE_DIR}"
	exit 0
fi

if [ ! -d "${BASELINE_DIR}" ]; then
	echo "[gate] ERROR: no baseline snapshot at ${BASELINE_DIR}" >&2
	echo "[gate] run: bash tests/e2e/e2e_blob_walk_baseline.sh --update-baseline" >&2
	exit 2
fi

echo "[gate] walking 4 fixed intervals into scratch: ${SCRATCH_DIR}"
rm -rf "${SCRATCH_DIR}"
python3 "${HARNESS_PY}" walk --output-dir "${SCRATCH_DIR}"

echo "[gate] row counts in fresh walk:"
python3 "${HARNESS_PY}" rows --dir "${SCRATCH_DIR}"

echo "[gate] comparing fresh walk vs baseline snapshot"
python3 "${HARNESS_PY}" compare --fresh-dir "${SCRATCH_DIR}" --baseline-dir "${BASELINE_DIR}"

echo "[gate] PASS: walker output unchanged vs baseline"
