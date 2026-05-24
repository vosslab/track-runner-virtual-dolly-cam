#!/usr/bin/env bash
# Validation Pass 2: rerun visualize_blob_gates on held_out-sample intervals
# and capture new verdicts.csv files for post-fix comparison.

set -euo pipefail

#============================================

REPO_ROOT=$(git rev-parse --show-toplevel)
SENTINEL_FILE="${REPO_ROOT}/output_smoke/blob_refine_fix/2026-05-23/M4_FIX_LANDED"
BASELINE_CSV="${REPO_ROOT}/output_smoke/blob_refine_fix/2026-05-23/m2_sweep/per_video_selection_rebucketed.csv"
OUTPUT_DIR="${REPO_ROOT}/output_smoke/blob_refine_fix/2026-05-23/validation/pass2"
SWEEP_LOG="${OUTPUT_DIR}/sweep.log"

#============================================

# Check sentinel file
if [[ ! -f "${SENTINEL_FILE}" ]]; then
	echo "REFUSED: M4 production fix has not landed (sentinel file missing). Do not run validation yet."
	exit 1
fi

# Verify baseline CSV exists
if [[ ! -f "${BASELINE_CSV}" ]]; then
	echo "ERROR: Baseline CSV not found: ${BASELINE_CSV}"
	exit 1
fi

# Create output directories
mkdir -p "${OUTPUT_DIR}"

#============================================

# Initialize log
cat > "${SWEEP_LOG}" <<'EOF'
Pass 2 validation run: held_out-sample intervals
==================================================
EOF

TOTAL=0
SUCCEEDED=0
FAILED=0

# Read CSV and process held_out-sample rows
{
	read -r header_line  # Skip header
	while IFS=',' read -r rng_seed sample video interval_start interval_end bucket tier agreement blob_accept_fwd blob_accept_bwd actual_accept_fwd actual_accept_bwd rebucket_note; do
		# Skip empty or whitespace-only lines
		[[ -z "${sample// }" ]] && continue

		# Filter to held_out sample only
		[[ "${sample}" != "held_out" ]] && continue

		TOTAL=$((TOTAL + 1))

		# Construct video path - try multiple extensions
		VIDEO_PATH=""
		for ext in mkv mp4 mov; do
			candidate="${REPO_ROOT}/TRACK_VIDEOS/${video}.${ext}"
			if [[ -f "${candidate}" ]]; then
				VIDEO_PATH="${candidate}"
				break
			fi
		done

		if [[ -z "${VIDEO_PATH}" ]]; then
			echo "WARNING: Video not found for ${video}, skipping interval ${interval_start}-${interval_end}"
			{
				echo "[${TOTAL}] SKIP (video not found): ${video} [${interval_start}-${interval_end}]"
			} | tee -a "${SWEEP_LOG}"
			FAILED=$((FAILED + 1))
			continue
		fi

		# Construct output path
		INTERVAL_OUTPUT="${OUTPUT_DIR}/${video}/interval_${interval_start}_${interval_end}"
		mkdir -p "${INTERVAL_OUTPUT}"

		# Run visualizer through safe_experiment_wrapper
		# Pass 2: always --no-png (no Jason PNG exception in held-out)
		if python3 "${REPO_ROOT}/tools/safe_experiment_wrapper.py" -- python3 "${REPO_ROOT}/tools/visualize_blob_gates.py" \
			-v "${VIDEO_PATH}" \
			-i "${interval_start}" "${interval_end}" \
			--write-corridor-blob-list \
			--no-png \
			-o "${INTERVAL_OUTPUT}" 2>&1; then

			{
				echo "[${TOTAL}] OK: ${video} [${interval_start}-${interval_end}]"
			} | tee -a "${SWEEP_LOG}"
			SUCCEEDED=$((SUCCEEDED + 1))
		else
			{
				echo "[${TOTAL}] FAIL: ${video} [${interval_start}-${interval_end}]"
			} | tee -a "${SWEEP_LOG}"
			FAILED=$((FAILED + 1))
		fi
	done
} < "${BASELINE_CSV}"

#============================================

# Final summary
{
	echo ""
	echo "Pass 2 Summary"
	echo "============="
	echo "Total held_out-sample intervals: ${TOTAL}"
	echo "Succeeded: ${SUCCEEDED}"
	echo "Failed: ${FAILED}"
} | tee -a "${SWEEP_LOG}"

echo "Log written to: ${SWEEP_LOG}"
exit 0
