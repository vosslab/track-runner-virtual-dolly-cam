"""Stabilize the per-frame torso h/w as a noisy observation.

Contract clause C5 ("Torso boxes are correct-object, imprecise-boundary
annotations") requires that the crop pipeline must not react directly
to single-frame torso w/h jitter. This module is the robust local
estimator that runs between trajectory load and crop ingest.

Design contract:
- Operates on the in-memory loaded trajectory only. The on-disk schema
  is untouched (C13).
- Position (cx, cy) and size (w, h) are independent signals per C5.
  This module modifies only `w` and `h`; `cx` and `cy` are returned
  unchanged.
- Window-edge clipping; no NaN at the start or end of the stabilized
  series. Where the local window contains only NaN, the original value
  is preserved.
- Operates post-stitching on the already-anchored trajectory. Per C6
  intervals are independent during solve; this module runs after
  solve and is therefore not bound by the interval-isolation rule.

Public API:
- `stabilize_torso_size(coords, method, window) -> dict`

Internal helpers:
- `median_filter_1d(values, window)`: window-local median.
- `hampel_filter_1d(values, window, k=3.0)`: Hampel outlier replacement.
- `mad_gated_filter_1d(values, window, k=3.0)`: MAD-gated median fallback.
"""

# PIP3 modules
import numpy


METHOD_CHOICES = ("none", "median", "hampel", "mad_gated")


#============================================
def median_filter_1d(values: numpy.ndarray, window: int) -> numpy.ndarray:
	"""Window-local median over a 1D array; centered window with edge clipping.

	Args:
		values: 1D float array. NaN entries inside a window are
			ignored when computing the local median.
		window: Odd or even window length; clipped to a minimum of 1.

	Returns:
		A new array of the same length. Where the local window
		contains only NaN, the corresponding output is NaN; the
		caller decides whether to fall back to the input.
	"""
	window = max(int(window), 1)
	if window == 1:
		return values.astype(numpy.float64, copy=True)
	half = window // 2
	n = len(values)
	out = numpy.zeros(n, dtype=numpy.float64)
	for i in range(n):
		start = max(0, i - half)
		end = min(n, i + half + 1)
		slice_vals = values[start:end]
		valid = slice_vals[numpy.isfinite(slice_vals)]
		if len(valid) == 0:
			out[i] = numpy.nan
		else:
			out[i] = float(numpy.median(valid))
	return out


#============================================
def hampel_filter_1d(
	values: numpy.ndarray, window: int, k: float = 3.0,
) -> numpy.ndarray:
	"""Hampel outlier-replacement filter on a 1D array.

	For each frame, compute the local median and the Median Absolute
	Deviation (MAD) over a centered window. If the frame's value is
	more than `k * 1.4826 * MAD` away from the local median, replace
	it with the local median. Otherwise pass through unchanged. This
	preserves real subject-size changes (which agree with the local
	neighborhood) while replacing only single-frame outliers.

	Args:
		values: 1D float array.
		window: Centered window length; clipped to a minimum of 3.
		k: Threshold in MAD units. Default 3.0 (about 3 sigma under
			a normal-distribution assumption).

	Returns:
		A new array of the same length. Edge clipping: short windows
		near the array boundaries fall back to the local median.
	"""
	window = max(int(window), 3)
	half = window // 2
	n = len(values)
	out = values.astype(numpy.float64, copy=True)
	mad_scale = 1.4826
	for i in range(n):
		start = max(0, i - half)
		end = min(n, i + half + 1)
		slice_vals = values[start:end]
		valid = slice_vals[numpy.isfinite(slice_vals)]
		if len(valid) < 3:
			continue
		local_median = float(numpy.median(valid))
		current = float(values[i])
		if not numpy.isfinite(current):
			out[i] = local_median
			continue
		local_mad = float(numpy.median(numpy.abs(valid - local_median)))
		if local_mad <= 0.0:
			# constant neighborhood: any deviation from the local
			# median is structurally an outlier.
			if current != local_median:
				out[i] = local_median
			continue
		threshold = k * mad_scale * local_mad
		if abs(current - local_median) > threshold:
			out[i] = local_median
	return out


#============================================
def mad_gated_filter_1d(
	values: numpy.ndarray, window: int, k: float = 3.0,
) -> numpy.ndarray:
	"""MAD-gated median replacement.

	Variant of Hampel that uses the local median (not the raw value)
	when the gate fires. The difference from `hampel_filter_1d` is in
	how a missing input is treated: `mad_gated_filter_1d` always
	replaces a non-finite input with the local median when one is
	available, even when the gate does not strictly fire.

	Behaviorally similar to Hampel for clean inputs; included so the
	choice between Hampel and a gap-aware variant can be made on evidence.

	Args:
		values: 1D float array.
		window: Centered window length; clipped to a minimum of 3.
		k: Threshold in MAD units. Default 3.0.

	Returns:
		A new array of the same length.
	"""
	window = max(int(window), 3)
	half = window // 2
	n = len(values)
	out = values.astype(numpy.float64, copy=True)
	mad_scale = 1.4826
	for i in range(n):
		start = max(0, i - half)
		end = min(n, i + half + 1)
		slice_vals = values[start:end]
		valid = slice_vals[numpy.isfinite(slice_vals)]
		if len(valid) < 3:
			continue
		local_median = float(numpy.median(valid))
		current = float(values[i]) if numpy.isfinite(values[i]) else float("nan")
		# fill non-finite from local median directly
		if not numpy.isfinite(current):
			out[i] = local_median
			continue
		local_mad = float(numpy.median(numpy.abs(valid - local_median)))
		if local_mad <= 0.0:
			# constant neighborhood: any deviation is an outlier
			if current != local_median:
				out[i] = local_median
			continue
		threshold = k * mad_scale * local_mad
		if abs(current - local_median) > threshold:
			out[i] = local_median
	return out


#============================================
def _apply_method(
	values: numpy.ndarray, method: str, window: int,
) -> numpy.ndarray:
	"""Dispatch to the named filter; preserve input on `none`.

	Falls back to the original value where the filter produced NaN
	(local window had no valid samples).
	"""
	if method == "none":
		return values.astype(numpy.float64, copy=True)
	if method == "median":
		filtered = median_filter_1d(values, window)
	elif method == "hampel":
		filtered = hampel_filter_1d(values, window)
	elif method == "mad_gated":
		filtered = mad_gated_filter_1d(values, window)
	else:
		raise ValueError(
			f"Unknown stabilizer method: {method!r}. "
			f"Choices: {METHOD_CHOICES}"
		)
	# fall back to the input where the filter could not estimate
	out = filtered.copy()
	missing = ~numpy.isfinite(out) & numpy.isfinite(values)
	out[missing] = values[missing]
	return out


#============================================
def stabilize_torso_size(
	coords: dict, method: str, window: int,
) -> dict:
	"""Stabilize per-frame torso w/h with a robust local estimator.

	Position (cx, cy) is returned unchanged (size and position are
	independent signals per C5). Width and height are routed through
	the named filter independently.

	Args:
		coords: Dict with per-frame numpy arrays. Required keys:
			"cx", "cy", "w", "h". Additional keys pass through
			unchanged.
		method: One of `none`, `median`, `hampel`, `mad_gated`.
		window: Centered window length; clipped to method-specific
			minimums by the underlying filter.

	Returns:
		A new dict with the same keys; "w" and "h" replaced with
		stabilized arrays when method is non-`none`, otherwise
		copies of the inputs. "cx" and "cy" are byte-equal to
		input.
	"""
	if method not in METHOD_CHOICES:
		raise ValueError(
			f"Unknown stabilizer method: {method!r}. "
			f"Choices: {METHOD_CHOICES}"
		)
	out = dict(coords)
	# size signals: stabilized when method is non-`none`
	for key in ("w", "h"):
		series = numpy.asarray(coords[key], dtype=numpy.float64)
		out[key] = _apply_method(series, method, window)
	# position signals: byte-equal passthrough
	for key in ("cx", "cy"):
		out[key] = numpy.asarray(coords[key]).copy()
	return out


#============================================
def stabilize_trajectory(
	trajectory: list, method: str, window: int,
) -> list:
	"""Stabilize torso w/h on a list-of-state-dicts trajectory.

	Thin wrapper around `stabilize_torso_size` for callers that hold
	the trajectory as a list of per-frame state dicts (the interval
	solver representation). Frames where state is None pass through
	unchanged so trajectory erasure (per the design doc) is preserved.

	Args:
		trajectory: List of length n_frames; each entry is either
			None (erased frame) or a dict with at least "cx", "cy",
			"w", "h" keys.
		method: One of `none`, `median`, `hampel`, `mad_gated`.
		window: Centered window length.

	Returns:
		A new list of the same length. Erased entries are still None.
		Non-erased entries are shallow-copies of the input dict with
		stabilized "w" and "h" values. Other keys are preserved.
	"""
	if method == "none":
		# fast path: no work needed
		return list(trajectory)
	n = len(trajectory)
	if n == 0:
		return []
	# build per-frame arrays; NaN where the frame is None
	w = numpy.full(n, numpy.nan, dtype=numpy.float64)
	h = numpy.full(n, numpy.nan, dtype=numpy.float64)
	for i in range(n):
		state = trajectory[i]
		if state is None:
			continue
		w[i] = float(state["w"])
		h[i] = float(state["h"])
	stabilized_w = _apply_method(w, method, window)
	stabilized_h = _apply_method(h, method, window)
	# write back
	out = []
	for i in range(n):
		state = trajectory[i]
		if state is None:
			out.append(None)
			continue
		new_state = dict(state)
		# guard against NaN (no-op fall-through preserves input)
		if numpy.isfinite(stabilized_w[i]):
			new_state["w"] = float(stabilized_w[i])
		if numpy.isfinite(stabilized_h[i]):
			new_state["h"] = float(stabilized_h[i])
		out.append(new_state)
	return out
