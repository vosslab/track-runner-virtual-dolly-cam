"""Behavioral tests for the Stage 4 walker adapter (WP-5b).

The adapter walker_bundle.walk_bundle_to_path bridges a WalkerInputBundle to
the relocated core walker walk_one_direction, then projects the walker's
standalone direction_path into the full-span, chronological, aligned
state-dict list the analytical solver's blend/score path expects.

These tests inject a fake walk_one_direction (via monkeypatch) that returns a
controlled direction_path, so they exercise the projection logic
deterministically without a real video reader:

  - a complete walk produces a full-span FWD path anchored on the seeds;
  - a short walk (early stop) is padded to the full interval span so FWD and
    BWD cover the same frames.

The companion test_walker_flag covers OFF-vs-ON path selection in
solve_interval_analytical.
"""

# Standard Library
import types

# local repo modules (track_runner/ is on sys.path via tests/conftest.py)
import walker_bundle


#============================================
def _seed(frame_index, cx, cy, w, h):
	"""Minimal seed dict with the fields the bundle and adapter read."""
	return {"frame_index": frame_index, "cx": cx, "cy": cy, "w": w, "h": h}


#============================================
def _bundle(direction_sign, start_frame, end_frame, seed, neighbor):
	"""Build a WalkerInputBundle directly (reader/transform are unused fakes)."""
	bundle = walker_bundle.WalkerInputBundle(
		seed=seed,
		neighbor_seed=neighbor,
		start_frame=start_frame,
		end_frame=end_frame,
		direction_sign=direction_sign,
		torso_width=float(seed["w"]),
		torso_height=float(seed["h"]),
		reader=object(),
		scene_transform=object(),
		fps=30.0,
		stride=1,
	)
	return bundle


#============================================
def _fake_summary(direction_path):
	"""Stand-in for WalkSummary: the adapter only reads .direction_path."""
	return types.SimpleNamespace(direction_path=direction_path)


#============================================
def _patch_walker(monkeypatch, direction_path):
	"""Patch walk_one_direction to return a fixed direction_path."""
	def fake_walk(**kwargs):
		return _fake_summary(direction_path)
	monkeypatch.setattr(
		walker_bundle.walk_walker, "walk_one_direction", fake_walk,
	)


#============================================
def test_complete_walk_produces_full_span_anchored_path(monkeypatch):
	"""A complete FWD walk yields one state per frame, endpoints on the seeds.

	The walker emits every frame 10..13. The adapter must return four states
	(frames 10, 11, 12, 13) with the endpoint boxes pinned to the seed boxes,
	so blend_paths sees an interval-length path anchored at both seeds.
	"""
	seed_start = _seed(10, 100.0, 50.0, 40.0, 80.0)
	seed_end = _seed(13, 130.0, 50.0, 40.0, 80.0)
	direction_path = [
		{"frame_index": 10, "cx": 100.0, "cy": 50.0, "w": 40.0, "h": 80.0,
			"conf": 1.0, "status": "accepted"},
		{"frame_index": 11, "cx": 111.0, "cy": 51.0, "w": 40.0, "h": 80.0,
			"conf": 0.9, "status": "accepted"},
		{"frame_index": 12, "cx": 122.0, "cy": 52.0, "w": 40.0, "h": 80.0,
			"conf": 0.8, "status": "accepted"},
		{"frame_index": 13, "cx": 199.0, "cy": 99.0, "w": 40.0, "h": 80.0,
			"conf": 0.7, "status": "accepted"},
	]
	_patch_walker(monkeypatch, direction_path)

	bundle = _bundle(1, 10, 13, seed_start, seed_end)
	path = walker_bundle.walk_bundle_to_path(bundle)

	# full interval span: frames 10..13 inclusive
	assert len(path) == 4
	# start endpoint pinned to the left seed box, not the walker's frame-10 row
	assert path[0]["cx"] == seed_start["cx"]
	# end endpoint pinned to the right seed box, overriding the walker's row
	assert path[-1]["cx"] == seed_end["cx"]
	# interior accepted frame keeps the walker geometry
	assert path[1]["cx"] == 111.0


#============================================
def test_short_walk_is_padded_to_full_interval_span(monkeypatch):
	"""An early-stop walk is padded so the path covers the whole interval.

	The walker emits only frames 0, 1, 2 of a 0..10 interval (it stopped
	early). The adapter must still return 11 states (0..10), filling the gap
	frames so a short FWD path does not silently shrink the scored span when
	blended against a full-length BWD path.
	"""
	seed_start = _seed(0, 0.0, 0.0, 20.0, 40.0)
	seed_end = _seed(10, 100.0, 0.0, 20.0, 40.0)
	direction_path = [
		{"frame_index": 0, "cx": 0.0, "cy": 0.0, "w": 20.0, "h": 40.0,
			"conf": 1.0, "status": "accepted"},
		{"frame_index": 1, "cx": 10.0, "cy": 0.0, "w": 20.0, "h": 40.0,
			"conf": 0.9, "status": "accepted"},
		{"frame_index": 2, "cx": 20.0, "cy": 0.0, "w": 20.0, "h": 40.0,
			"conf": 0.8, "status": "accepted"},
	]
	_patch_walker(monkeypatch, direction_path)

	bundle = _bundle(1, 0, 10, seed_start, seed_end)
	path = walker_bundle.walk_bundle_to_path(bundle)

	# padded to the full 0..10 span (11 frames)
	assert len(path) == 11
	# every padded frame carries a usable box (filled, not None)
	assert all(isinstance(state["cx"], float) for state in path)
	# the filled gap interpolates toward the end seed, staying inside the span
	assert seed_start["cx"] <= path[6]["cx"] <= seed_end["cx"]
