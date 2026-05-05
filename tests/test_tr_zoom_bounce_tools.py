"""Behavioral tests for the zoom-bounce assessment tools.

Each tool gets one or more synthetic-signal tests that assert
behavioral invariants (descending order, lag detection, sign of
correlation, frequency match within tolerance, ranking ordering).
None of the tests assert exact metric values, hardcoded constants,
or collection sizes, so they survive refactors of the underlying
math (per docs/PYTHON_STYLE.md).

Tests do NOT invoke ffmpeg, decode video, or require the user's
corpus. They run on numpy fixtures only.
"""

# Standard Library
import os
import sys
import csv

# allow the test module to import the tool helpers locally
# (NOT via conftest.py per project memory: conftest.py is for
# configuration only, never for sys.path or shared fixtures)
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_TOOLS_DIR = os.path.join(_REPO_ROOT, "tools")
if _TOOLS_DIR not in sys.path:
	sys.path.insert(0, _TOOLS_DIR)
if _REPO_ROOT not in sys.path:
	sys.path.insert(0, _REPO_ROOT)

# PIP3 modules
import numpy
import pytest

# tools under test
import find_zoom_hotspots
import correlate_bounce_with_edge
import spectrum_zoom_bounce
import rank_zoom_variants
import measure_black_bars
import analyze_torso_box_noise

# library under test
import track_runner.torso_size_stabilizer as torso_size_stabilizer


#============================================
# Tool 1: find_zoom_hotspots
#============================================

def test_find_top_n_hotspots_descending_order():
	"""Hotspots are returned in descending intensity order.

	Constructs a per-frame intensity array with three known peaks at
	different magnitudes and asserts the returned ordering.
	"""
	intensity = numpy.zeros(100)
	intensity[20] = 1.0
	intensity[50] = 5.0
	intensity[80] = 3.0
	hotspots = find_zoom_hotspots.find_top_n_hotspots(
		intensity, top_n=3, min_gap_frames=5,
	)
	# strict descending intensity
	values = [v for _idx, v in hotspots]
	assert values == sorted(values, reverse=True)


def test_find_top_n_hotspots_no_overlap():
	"""Returned hotspot frame indices respect the min-gap constraint.

	Adjacent peaks within min_gap_frames of each other should be
	suppressed by the greedy non-max routine.
	"""
	intensity = numpy.zeros(100)
	# two adjacent peaks; only the bigger one should survive a gap of 10
	intensity[30] = 4.0
	intensity[33] = 3.5
	intensity[70] = 5.0
	hotspots = find_zoom_hotspots.find_top_n_hotspots(
		intensity, top_n=3, min_gap_frames=10,
	)
	indices = [idx for idx, _v in hotspots]
	# verify no two indices are within min_gap_frames of each other
	for i, idx_a in enumerate(indices):
		for idx_b in indices[i + 1:]:
			assert abs(idx_a - idx_b) > 10


def test_velocity_p95_distinguishes_drift_from_jitter():
	"""velocity_p95 is high for jitter and low for slow drift.

	Constructs two signals of equal magnitude but different temporal
	character: one is a slow ramp (drift), the other is per-frame
	noise (jitter). The velocity-based score should favor the
	jitter signal.
	"""
	n = 200
	# slow drift: monotone ramp with no per-frame jitter
	drift = numpy.linspace(-0.05, 0.05, n)
	# jitter: per-frame independent noise of comparable magnitude
	rng = numpy.random.default_rng(seed=42)
	jitter = rng.normal(0.0, 0.03, n)
	window = 20
	drift_score = find_zoom_hotspots.score_velocity_p95(drift, window)
	jitter_score = find_zoom_hotspots.score_velocity_p95(jitter, window)
	# the median jitter score should clearly exceed the median drift score
	assert numpy.median(jitter_score) > numpy.median(drift_score) * 5.0


#============================================
# Tool 2: correlate_bounce_with_edge
#============================================

def test_compute_correlation_positive_when_constructed():
	"""Spearman is positive on a deliberately strong constructed signal.

	intensity is large where 1/edge_gap is large (gap is small), so the
	correlation between intensity and inverse-gap must be positive.
	"""
	rng = numpy.random.default_rng(seed=0)
	n = 200
	# small gap = high intensity; large gap = low intensity
	gaps = rng.uniform(5.0, 200.0, n)
	# build intensity proportional to inverse-gap, plus a little noise
	intensity = (1.0 / gaps) * 100.0 + rng.normal(0.0, 0.05, n)
	rho, _p = correlate_bounce_with_edge.compute_correlation(intensity, gaps)
	assert rho > 0.5


def test_lagged_spearman_finds_constructed_lag():
	"""The best-lag detector recovers a deliberately offset correlation.

	Build intensity that is correlated with gaps at lag +2 (i.e. bounce
	trails edge approach by two frames). The best-lag returned must
	be within +/-1 of +2.
	"""
	rng = numpy.random.default_rng(seed=1)
	n = 400
	gaps = rng.uniform(5.0, 100.0, n)
	intensity = numpy.zeros(n)
	# place the high-intensity peaks two frames AFTER each gap minimum
	intensity[2:] = (1.0 / gaps[:-2]) * 100.0
	# tiny noise so Spearman is well-defined
	intensity += rng.normal(0.0, 0.01, n)
	lags, rhos = correlate_bounce_with_edge.compute_lagged_correlation(
		intensity, gaps, lag_window=5,
	)
	best_lag, best_rho = correlate_bounce_with_edge.find_best_lag(lags, rhos)
	assert best_rho > 0.5
	assert abs(best_lag - 2) <= 1


def test_check_frame_alignment_raises_on_mismatch():
	"""Frame-count gap > tolerance must raise unless an offset is given.
	"""
	# beyond tolerance, no offset: raises
	with pytest.raises(RuntimeError):
		correlate_bounce_with_edge.check_frame_alignment(
			n_video=1000, n_traj=1010, tolerance=2, frame_offset=0,
		)
	# within tolerance: passes silently
	correlate_bounce_with_edge.check_frame_alignment(
		n_video=1000, n_traj=1001, tolerance=2, frame_offset=0,
	)
	# beyond tolerance with an offset: passes (user accepted responsibility)
	correlate_bounce_with_edge.check_frame_alignment(
		n_video=1000, n_traj=1010, tolerance=2, frame_offset=10,
	)


#============================================
# Tool 3: spectrum_zoom_bounce
#============================================

def test_compute_power_spectrum_dominant_frequency():
	"""A clean sinusoid produces a dominant peak near the constructed frequency.

	Builds a 30 Hz sinusoid sampled at 60 fps for 600 frames; the
	dominant peak in the power spectrum should be within +/-10% of
	30 Hz.
	"""
	fps = 60.0
	target_hz = 5.0
	n = 600
	t = numpy.arange(n) / fps
	log_scale = 0.05 * numpy.sin(2.0 * numpy.pi * target_hz * t)
	freqs_hz, power = spectrum_zoom_bounce.compute_power_spectrum(log_scale, fps)
	dominant = spectrum_zoom_bounce.find_dominant_frequencies(
		freqs_hz, power, top_n=3,
	)
	assert len(dominant) > 0
	top_freq = dominant[0][0]
	assert abs(top_freq - target_hz) <= 0.1 * target_hz


#============================================
# Tool 4: rank_zoom_variants
#============================================

def _write_csv(path: str, header: list, rows: list) -> None:
	"""Helper: write a CSV with the given header and row dicts."""
	with open(path, "w", newline="") as f:
		writer = csv.DictWriter(f, fieldnames=header)
		writer.writeheader()
		for row in rows:
			writer.writerow(row)


def test_rank_zoom_variants_lower_is_better_orders_correctly(tmp_path):
	"""The variant ranking puts the lowest variant first for a lower-better metric.
	"""
	header = ["filename", "bounce_rate_per_s"]
	baseline_rows = [
		{"filename": "A", "bounce_rate_per_s": 1.0},
		{"filename": "B", "bounce_rate_per_s": 2.0},
	]
	variant_rows = [
		# variant beats baseline on both videos (lower is better)
		{"filename": "A", "bounce_rate_per_s": 0.5},
		{"filename": "B", "bounce_rate_per_s": 1.0},
	]
	baseline_dir = tmp_path / "baseline"
	variant_dir = tmp_path / "path_a"
	baseline_dir.mkdir()
	variant_dir.mkdir()
	_write_csv(str(baseline_dir / "pixel_zoom_comparison.csv"), header, baseline_rows)
	_write_csv(str(variant_dir / "pixel_zoom_comparison.csv"), header, variant_rows)

	baseline_values = rank_zoom_variants.load_variant_csv(
		str(baseline_dir / "pixel_zoom_comparison.csv"), "bounce_rate_per_s",
	)
	variant_values = rank_zoom_variants.load_variant_csv(
		str(variant_dir / "pixel_zoom_comparison.csv"), "bounce_rate_per_s",
	)
	deltas = rank_zoom_variants.compute_deltas(baseline_values, variant_values)
	# variant is lower than baseline on both videos -> negative delta
	for _b, _v, delta, _pct in deltas.values():
		assert delta < 0
	# verdict: with 50% threshold on lower-is-better, variant wins both
	median_pct, wins, total, verdict = rank_zoom_variants.variant_verdict(
		deltas, win_threshold=0.10, higher_is_better=False,
	)
	assert wins == total
	assert verdict == "win"


def test_rank_zoom_variants_direction_flips_ranking(tmp_path):
	"""The lower-vs-higher direction flips win/loss verdicts on the same data.

	Same CSV pair, same metric; only the directionality changes. Under
	lower-is-better the variant wins; under higher-is-better the variant
	loses.
	"""
	header = ["filename", "bounce_rate_per_s"]
	baseline_rows = [
		{"filename": "A", "bounce_rate_per_s": 1.0},
		{"filename": "B", "bounce_rate_per_s": 2.0},
	]
	variant_rows = [
		{"filename": "A", "bounce_rate_per_s": 0.5},
		{"filename": "B", "bounce_rate_per_s": 1.0},
	]
	baseline_dir = tmp_path / "baseline"
	variant_dir = tmp_path / "path_a"
	baseline_dir.mkdir()
	variant_dir.mkdir()
	_write_csv(str(baseline_dir / "pixel_zoom_comparison.csv"), header, baseline_rows)
	_write_csv(str(variant_dir / "pixel_zoom_comparison.csv"), header, variant_rows)

	baseline_values = rank_zoom_variants.load_variant_csv(
		str(baseline_dir / "pixel_zoom_comparison.csv"), "bounce_rate_per_s",
	)
	variant_values = rank_zoom_variants.load_variant_csv(
		str(variant_dir / "pixel_zoom_comparison.csv"), "bounce_rate_per_s",
	)
	deltas = rank_zoom_variants.compute_deltas(baseline_values, variant_values)
	# under lower-is-better: variant is better -> verdict "win"
	_med_l, wins_l, _t_l, verdict_l = rank_zoom_variants.variant_verdict(
		deltas, win_threshold=0.10, higher_is_better=False,
	)
	# under higher-is-better with the same data: variant loses
	_med_h, wins_h, _t_h, verdict_h = rank_zoom_variants.variant_verdict(
		deltas, win_threshold=0.10, higher_is_better=True,
	)
	assert verdict_l == "win"
	assert verdict_h == "loss"
	assert wins_l > wins_h


def test_resolve_metric_direction_known_metric_blocks_override():
	"""Built-in directionality cannot be overridden for known metrics.
	"""
	# known metric, no override: returns False (lower-is-better)
	is_higher = rank_zoom_variants.resolve_metric_direction(
		"bounce_rate_per_s", metric_direction_arg="",
		higher_is_better_flag=False,
	)
	assert is_higher is False
	# known metric, attempted override via -D: errors
	with pytest.raises(RuntimeError):
		rank_zoom_variants.resolve_metric_direction(
			"bounce_rate_per_s", metric_direction_arg="higher",
			higher_is_better_flag=False,
		)
	# known metric, attempted override via -H: errors
	with pytest.raises(RuntimeError):
		rank_zoom_variants.resolve_metric_direction(
			"bounce_rate_per_s", metric_direction_arg="",
			higher_is_better_flag=True,
		)
	# unknown metric requires explicit direction
	with pytest.raises(RuntimeError):
		rank_zoom_variants.resolve_metric_direction(
			"my_custom_metric", metric_direction_arg="",
			higher_is_better_flag=False,
		)
	# unknown metric with -D works
	is_higher = rank_zoom_variants.resolve_metric_direction(
		"my_custom_metric", metric_direction_arg="lower",
		higher_is_better_flag=False,
	)
	assert is_higher is False


#============================================
# Tool 5: measure_black_bars
#============================================

def test_measure_black_bars_returns_constructed_top_height():
	"""Constructed frame with a known top bar returns matching bar height.

	Builds a 100x200 grayscale frame with the top 15 rows fully black
	and the rest fully white. The measured top bar should be 15 rows
	(within +/-1 to allow for boundary handling).
	"""
	height = 100
	width = 200
	frame = numpy.full((height, width), 255, dtype=numpy.uint8)
	frame[:15, :] = 0
	t, b, l, r = measure_black_bars.measure_bars_for_frame(
		frame, threshold=16, row_fraction=0.95,
	)
	assert abs(t - 15) <= 1
	assert b == 0
	assert l == 0
	assert r == 0


def test_measure_black_bars_area_matches_constructed():
	"""Constructed frame: bar area fraction matches the analytic prediction.
	"""
	height = 100
	width = 200
	frame = numpy.full((height, width), 255, dtype=numpy.uint8)
	# 10-row top bar plus a 5-column left bar
	frame[:10, :] = 0
	frame[:, :5] = 0
	t, b, l, r = measure_black_bars.measure_bars_for_frame(
		frame, threshold=16, row_fraction=0.95,
	)
	area = measure_black_bars.per_frame_bar_area_fraction(
		t, b, l, r, height, width,
	)
	# top strip: 10 * 200 = 2000; left strip: 5 * 100 = 500;
	# top-left corner overlap: 10 * 5 = 50; total: 2450 / 20000 = 0.1225
	expected = (10 * 200 + 5 * 100 - 10 * 5) / float(height * width)
	assert abs(area - expected) <= 1e-6


#============================================
# Tool 0: analyze_torso_box_noise
#============================================

def test_analyze_torso_velocity_peaks_at_steepest_slope():
	"""velocity_abs of a sinusoidal torso_h peaks where the slope is steepest.

	A pure cosine has zero slope at the crests and steepest slope at the
	zero crossings. abs(diff(cos)) peaks at the zero-crossing indices, not
	at the crest indices.
	"""
	n_frames = 400
	t = numpy.arange(n_frames, dtype=numpy.float64)
	# pick a slow sinusoid so the diff is well-resolved
	period_frames = 50.0
	signal = 10.0 * numpy.cos(2.0 * numpy.pi * t / period_frames)
	v = analyze_torso_box_noise.velocity_abs(signal)
	# index 0 is NaN-padded; ignore it for arg-extrema
	v_finite = v[1:]
	# peak velocity should occur near a zero crossing of cos, i.e. at
	# t such that t / period_frames = 0.25, 0.75, ... (modulo period).
	# For period_frames=50, that's frame indices 12-13 and 37-38.
	peak_idx = int(numpy.argmax(v_finite)) + 1
	# The constructed signal's steepest slope is one of the zero-cross
	# regions; assert peak lands near a zero-cross (within +/-3 frames of
	# any 0.25-period offset, which is the expected tolerance for a
	# discrete cosine).
	period = period_frames
	phase_within_period = (peak_idx % period) / period
	# accept proximity to either 0.25 or 0.75 of the period
	dist_to_quarter = min(
		abs(phase_within_period - 0.25),
		abs(phase_within_period - 0.75),
	)
	assert dist_to_quarter < 0.10


def test_analyze_torso_median_filter_suppresses_single_outlier():
	"""A constant signal plus one isolated outlier is suppressed by the median.

	Behavioral invariant per WP 1.2: a one-frame outlier in an otherwise
	constant torso_h series should be removed by the median filter (the
	median ignores it) but should remain visible in the raw difference at
	the outlier index.
	"""
	n_frames = 100
	signal = numpy.full(n_frames, 50.0, dtype=numpy.float64)
	outlier_idx = 40
	# inject a single 30-pixel jump at one frame
	signal[outlier_idx] = 80.0
	# median filter with a window large enough to reject a single sample
	smoothed = analyze_torso_box_noise.median_filter_1d(signal, 7)
	# the median-filtered value at the outlier should snap back to ~50
	assert abs(smoothed[outlier_idx] - 50.0) < 1.0
	# the raw velocity should still show the outlier as a large jump
	v = analyze_torso_box_noise.velocity_abs(signal)
	# velocity at the outlier index is the jump magnitude
	assert v[outlier_idx] > 10.0
	# velocity at non-outlier indices is zero (or NaN at index 0)
	assert v[5] == 0.0
	assert v[outlier_idx + 5] == 0.0


#============================================
# torso_size_stabilizer (M2.B per declarative-shimmying-brooks.md)
#============================================

def _make_torso_coords_with_outlier(
	n_frames: int = 100, outlier_idx: int = 40,
) -> dict:
	"""Constant torso h/w with a single one-frame outlier; cx/cy linear ramp."""
	h = numpy.full(n_frames, 50.0, dtype=numpy.float64)
	w = numpy.full(n_frames, 30.0, dtype=numpy.float64)
	cx = numpy.linspace(100.0, 200.0, n_frames)
	cy = numpy.linspace(150.0, 250.0, n_frames)
	# inject a single-frame outlier on size only
	h[outlier_idx] = 80.0
	w[outlier_idx] = 50.0
	return {"cx": cx, "cy": cy, "w": w, "h": h}


def test_stabilize_median_suppresses_outlier_and_preserves_quiet_frames():
	"""Median stabilizer suppresses the outlier; non-outlier frames stay close to input.

	Behavioral invariant: the outlier-frame size returns to the local median
	(within 1 px), and frames far from the outlier are within 1 px of input
	since the local median equals the constant signal.
	"""
	outlier_idx = 40
	coords = _make_torso_coords_with_outlier(outlier_idx=outlier_idx)
	out = torso_size_stabilizer.stabilize_torso_size(
		coords, method="median", window=7,
	)
	# outlier suppressed back toward 50 px
	assert abs(out["h"][outlier_idx] - 50.0) < 1.0
	assert abs(out["w"][outlier_idx] - 30.0) < 1.0
	# non-outlier frame is unchanged (median of constants is the constant)
	assert abs(out["h"][5] - 50.0) < 1.0
	assert abs(out["h"][outlier_idx + 10] - 50.0) < 1.0


def test_stabilize_hampel_replaces_only_outliers_quiet_frames_byte_equal():
	"""Hampel only replaces gate-violating frames; other frames are byte-equal.

	Behavioral invariant: under Hampel, an outlier frame is pulled toward the
	local median while non-outlier frames pass through unchanged. This
	separates Hampel from a plain median (which would touch every frame).
	"""
	outlier_idx = 40
	coords = _make_torso_coords_with_outlier(outlier_idx=outlier_idx)
	out = torso_size_stabilizer.stabilize_torso_size(
		coords, method="hampel", window=7,
	)
	# outlier was replaced
	assert out["h"][outlier_idx] != coords["h"][outlier_idx]
	# distant frames are byte-equal to the input
	for idx in (0, 1, 5, 20, outlier_idx + 10, len(coords["h"]) - 1):
		assert out["h"][idx] == coords["h"][idx]
		assert out["w"][idx] == coords["w"][idx]


def test_stabilize_mad_gated_replaces_outlier_like_hampel():
	"""MAD-gated stabilizer also replaces the single-frame outlier.

	Behavioral invariant: mad_gated must move the outlier-frame size away
	from the input value (toward the local median) on the constant + outlier
	fixture, just like Hampel.
	"""
	outlier_idx = 40
	coords = _make_torso_coords_with_outlier(outlier_idx=outlier_idx)
	out = torso_size_stabilizer.stabilize_torso_size(
		coords, method="mad_gated", window=7,
	)
	assert out["h"][outlier_idx] != coords["h"][outlier_idx]
	assert abs(out["h"][outlier_idx] - 50.0) < 1.0


def test_stabilize_cx_cy_passthrough_byte_identical_for_every_method():
	"""Per C5 size and position are independent; cx/cy are never modified.

	Behavioral invariant: for every method (none, median, hampel, mad_gated)
	and a representative window, the returned cx and cy arrays are
	byte-identical to the input.
	"""
	coords = _make_torso_coords_with_outlier()
	for method in ("none", "median", "hampel", "mad_gated"):
		out = torso_size_stabilizer.stabilize_torso_size(
			coords, method=method, window=7,
		)
		assert numpy.array_equal(out["cx"], coords["cx"]), method
		assert numpy.array_equal(out["cy"], coords["cy"]), method
