#!/usr/bin/env python3
"""FFT power spectrum of per-frame zoom-bounce signal in an encoded movie.

Runs Fourier-Mellin per-frame scale estimation (reusing
assess_pixel_zoom helpers via find_zoom_hotspots), takes the FFT of the
per-frame log-scale series, and plots the power spectrum on a log-y
axis. Annotates the EMA cutoff frequency at fps / (2 * pi * tau_frames)
so the eye can see whether residual bounce energy sits above or below
the EMA's expected attenuation band.

Reading the result:
	- Energy peaks below the EMA cutoff: bounce that EMA is not designed
	  to remove (slow drift, sustained ratchet behavior, runner stride
	  if very slow).
	- Energy peaks above the EMA cutoff: residual high-frequency leakage
	  the EMA should have damped; possibly source-content motion or
	  per-frame integer-rounding noise.
	- A clean peak at one frequency is more informative than broadband
	  noise.

Stdout reports the top-3 dominant frequency peaks (in Hz) above the
noise floor.
"""

# Standard Library
import os
import sys
import math
import argparse

# add tools directory to path so we can reuse Tool 1's signal-extraction helper
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS_DIR = os.path.join(_REPO_ROOT, "tools")
if _TOOLS_DIR not in sys.path:
	sys.path.insert(0, _TOOLS_DIR)

# PIP3 modules
import numpy
import scipy.signal
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# local repo modules
import find_zoom_hotspots


#============================================
def parse_args() -> argparse.Namespace:
	"""
	Parse command-line arguments.
	"""
	parser = argparse.ArgumentParser(
		description=(
			"FFT power spectrum of per-frame zoom-bounce signal in an "
			"encoded movie."
		)
	)
	parser.add_argument(
		"-i", "--input", dest="input_file", required=True,
		help="Encoded movie file to analyze",
	)
	parser.add_argument(
		"-o", "--output", dest="output_path", default="",
		help="Output PNG plot path (default: <input>.spectrum.png)",
	)
	parser.add_argument(
		"-t", "--tau-frames", dest="tau_frames", type=float, default=6.0,
		help=(
			"EMA time constant in frames; cutoff frequency annotation "
			"is at fps / (2 * pi * tau). Default 6 matches the current "
			"crop_post_smooth_size_strength=0.15 default."
		),
	)
	parser.add_argument(
		"-W", "--weighting", dest="weighting", default="edge_weighted",
		choices=["full", "edge_weighted", "side_strips"],
		help="Edge masking mode (default: edge_weighted)",
	)
	parser.add_argument(
		"-s", "--start-frame", dest="start_frame", type=int, default=0,
		help=(
			"Skip this many frames at the start of the bounce signal "
			"(useful when source camera has a settling pan). Default 0."
		),
	)
	parser.add_argument(
		"-e", "--end-frame", dest="end_frame", type=int, default=0,
		help=(
			"Stop the spectrum analysis at this frame index "
			"(0 = use all frames). Default 0."
		),
	)
	args = parser.parse_args()
	return args


#============================================
def compute_power_spectrum(
	log_scale: numpy.ndarray,
	fps: float,
) -> tuple:
	"""Compute the one-sided power spectrum of the log_scale signal.

	Uses a Hann window to reduce spectral leakage. Returns frequencies
	in Hz (using fps) and the corresponding power (squared magnitude
	of the FFT).

	Args:
		log_scale: Per-frame log-scale series.
		fps: Source video fps.

	Returns:
		Tuple (freqs_hz, power) of equal length.
	"""
	n = len(log_scale)
	if n < 4:
		raise RuntimeError(f"Spectrum needs >= 4 frames, got {n}")
	window = numpy.hanning(n)
	# detrend (subtract mean) to suppress the DC bin spike from a non-zero
	# mean log_scale; we care about the AC behavior of bounce, not drift
	signal = (log_scale - numpy.mean(log_scale)) * window
	spectrum = numpy.fft.rfft(signal)
	power = numpy.abs(spectrum) ** 2
	freqs_hz = numpy.fft.rfftfreq(n, d=1.0 / fps)
	return (freqs_hz, power)


#============================================
def find_dominant_frequencies(
	freqs_hz: numpy.ndarray,
	power: numpy.ndarray,
	top_n: int,
) -> list:
	"""Locate the top-N power peaks above a median-based noise floor.

	Skips the DC bin (frequency 0). Uses scipy.signal.find_peaks to
	avoid reporting a single broad peak as multiple adjacent bins.
	The peak prominence threshold is set to twice the median power
	above the floor.

	Args:
		freqs_hz: Frequency axis in Hz.
		power: Power spectrum aligned to freqs_hz.
		top_n: Number of dominant peaks to return.

	Returns:
		List of (frequency_hz, power) tuples sorted by power descending.
	"""
	if len(freqs_hz) < 4 or top_n <= 0:
		return []
	# skip the DC bin (index 0); freqs_hz[0] == 0
	power_no_dc = power[1:]
	freqs_no_dc = freqs_hz[1:]
	noise_floor = float(numpy.median(power_no_dc))
	# prominence threshold: peaks must clearly stand above the median
	prominence = max(2.0 * noise_floor, 1e-12)
	peaks, _ = scipy.signal.find_peaks(power_no_dc, prominence=prominence)
	if len(peaks) == 0:
		# fall back to argmax-style ranking when find_peaks finds nothing
		# (very smooth spectrum); take the top-N bins by power
		peaks = numpy.argsort(power_no_dc)[::-1][:top_n]
	# sort by power descending and clip to top_n
	peak_powers = power_no_dc[peaks]
	order = numpy.argsort(peak_powers)[::-1][:top_n]
	dominant = [
		(float(freqs_no_dc[peaks[i]]), float(peak_powers[i]))
		for i in order
	]
	return dominant


#============================================
def render_spectrum_plot(
	freqs_hz: numpy.ndarray,
	power: numpy.ndarray,
	cutoff_hz: float,
	dominant: list,
	output_path: str,
	input_name: str,
) -> None:
	"""Render the power spectrum on log-y axis with EMA-cutoff annotation.

	Args:
		freqs_hz: Frequency axis (Hz).
		power: Power spectrum.
		cutoff_hz: EMA cutoff annotation frequency.
		dominant: List of (freq_hz, power) tuples to annotate as markers.
		output_path: PNG output path.
		input_name: Source video basename, embedded in title.
	"""
	fig, ax = plt.subplots(figsize=(9, 5))
	ax.semilogy(freqs_hz, power, color="#1f77b4", linewidth=1.0)
	# EMA cutoff annotation
	ax.axvline(
		cutoff_hz, color="#d62728", linestyle="--", linewidth=1.2,
		label=f"EMA cutoff ~ {cutoff_hz:.2f} Hz",
	)
	# annotate dominant peaks with a small marker
	for freq, pwr in dominant:
		ax.plot(freq, pwr, marker="o", color="#2ca02c", markersize=8)
		ax.annotate(
			f"{freq:.2f} Hz",
			xy=(freq, pwr),
			xytext=(5, 5), textcoords="offset points",
			color="#2ca02c", fontsize=9,
		)
	ax.set_xlabel("Frequency (Hz)")
	ax.set_ylabel("Power (log scale)")
	ax.set_title(f"Zoom-bounce power spectrum: {input_name}")
	ax.grid(True, which="both", alpha=0.3)
	ax.legend(loc="upper right")
	fig.tight_layout()
	fig.savefig(output_path, dpi=120)
	plt.close(fig)


#============================================
def main() -> None:
	"""
	Main entry point.
	"""
	args = parse_args()

	if not os.path.isfile(args.input_file):
		raise RuntimeError(f"Input file not found: {args.input_file}")

	print(f"Analyzing zoom spectrum in: {args.input_file}")
	log_scale, fps, total_frames = (
		find_zoom_hotspots.compute_log_scale_series(
			args.input_file, args.weighting,
		)
	)
	print(f"  fps={fps:.2f}, frames={total_frames}")

	# apply optional frame range
	start_frame = max(0, int(args.start_frame))
	end_frame = int(args.end_frame) if args.end_frame > 0 else len(log_scale)
	end_frame = min(end_frame, len(log_scale))
	if start_frame >= end_frame:
		raise RuntimeError(
			f"Empty range: start={start_frame} end={end_frame}"
		)
	signal = log_scale[start_frame:end_frame]
	print(f"  spectrum range: frames [{start_frame}, {end_frame})")

	freqs_hz, power = compute_power_spectrum(signal, fps)
	dominant = find_dominant_frequencies(freqs_hz, power, top_n=3)

	# EMA cutoff annotation: f_c = fps / (2 * pi * tau_frames)
	tau_frames = max(args.tau_frames, 1e-3)
	cutoff_hz = fps / (2.0 * math.pi * tau_frames)
	print(f"  EMA cutoff annotation: tau={tau_frames:.2f} frames -> {cutoff_hz:.3f} Hz")

	# stdout summary of dominant peaks
	print("Top-3 dominant peaks:")
	for rank, (freq, pwr) in enumerate(dominant, start=1):
		below_or_above = "below" if freq < cutoff_hz else "above"
		print(f"  {rank}: {freq:.3f} Hz ({below_or_above} EMA cutoff)  power={pwr:.4g}")

	# write PNG
	base = os.path.splitext(args.input_file)[0]
	output_png = args.output_path or (base + ".spectrum.png")
	render_spectrum_plot(
		freqs_hz, power, cutoff_hz, dominant, output_png,
		os.path.basename(args.input_file),
	)
	print(f"  wrote: {output_png}")


#============================================

if __name__ == "__main__":
	main()
