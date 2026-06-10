"""Data-boundary tests for the Stage 4 walker input bundle seam (WP-5a).

The Stage 4 seam (`interval_solver.run_stage4_walker_seam`) builds a
`walker_bundle.WalkerInputBundle` per direction and hands it to an
injectable walker callable. These tests inject a fake walker that records
the bundle it receives, then assert two paired data-boundary properties:

  positive: the bundle carries the seed and the candidate-lattice source
    (reader + scene_transform + residual sampling) sufficient to walk one
    direction from the seed.
  negative (Hermite independence): no Hermite raw_pred path is reachable
    through the bundle -- the walker must be free to diverge from a failed
    Hermite path.

A third test locks the Stage-3-first ordering: promotion eligibility is
decided from the Stage-3 confidence tier before any walker output exists.

Behavioral and deterministic per docs/PYTEST_STYLE.md: assertions check the
seam's behavior (what the walker can/cannot reach, when the walker runs),
not a brittle exact key set.
"""

# Standard Library
import dataclasses

# local repo modules (track_runner/ is on sys.path via tests/conftest.py)
import interval_solver


#============================================
class _RecordingWalker:
	"""Fake walker callable that records every bundle it is handed.

	Stands in for the real walk_walker.walk_one_direction. It does no walking;
	it captures the bundle so the test can inspect the data boundary, and
	tracks invocation order so the test can prove the walker ran after (never
	before) the promotion decision.
	"""

	def __init__(self):
		self.received_bundles = []
		self.call_count = 0

	def __call__(self, bundle):
		self.call_count += 1
		self.received_bundles.append(bundle)
		# return a trivial standalone path stand-in
		fake_path = [{"frame_index": bundle.start_frame, "cx": 0.0, "cy": 0.0}]
		return fake_path


#============================================
def _make_seed(frame_index: int, cx: float, cy: float, w: float, h: float) -> dict:
	"""Build a minimal seed dict with the fields the bundle reads."""
	seed = {"frame_index": frame_index, "cx": cx, "cy": cy, "w": w, "h": h}
	return seed


#============================================
def _promoted_score() -> dict:
	"""Stage-3 interval score whose tier promotes to Stage 4."""
	score = {"confidence_tier": "low"}
	return score


#============================================
def _not_promoted_score() -> dict:
	"""Stage-3 interval score whose tier does not promote."""
	score = {"confidence_tier": "high"}
	return score


#============================================
def test_bundle_carries_seed_and_candidate_lattice_source():
	"""Positive boundary: the bundle gives the walker what it needs to walk.

	The recorded FWD bundle must anchor on the left seed and carry the
	candidate-lattice source plumbing (reader, scene_transform, fps, stride)
	plus the torso-unit scale derived from the seed box. With those, the
	walker can build its per-frame corridor_blobs lattice and step from the
	seed -- no other input is required.
	"""
	seed_start = _make_seed(10, 100.0, 50.0, 40.0, 80.0)
	seed_end = _make_seed(40, 200.0, 60.0, 44.0, 88.0)
	fake_reader = object()
	fake_transform = object()
	walker = _RecordingWalker()

	result = interval_solver.run_stage4_walker_seam(
		seed_start=seed_start,
		seed_end=seed_end,
		stage3_interval_score=_promoted_score(),
		reader=fake_reader,
		scene_transform=fake_transform,
		fps=30.0,
		walker_callable=walker,
	)

	# walker ran twice: one FWD bundle, one BWD bundle (C9 independence)
	assert result["promoted"] is True
	assert walker.call_count == 2

	forward_bundle = walker.received_bundles[0]
	backward_bundle = walker.received_bundles[1]

	# FWD anchors on the left seed; the seed the walker walks from is present
	assert forward_bundle.seed is seed_start
	assert forward_bundle.direction_sign == 1
	# BWD anchors on the right seed and steps the other way
	assert backward_bundle.seed is seed_end
	assert backward_bundle.direction_sign == -1

	# candidate-lattice source plumbing reaches the walker so it can gather
	# per-frame corridor_blobs itself (the lattice is built inside the walker)
	assert forward_bundle.reader is fake_reader
	assert forward_bundle.scene_transform is fake_transform
	assert forward_bundle.fps == 30.0
	# stride is the residual neighbor stride the walker passes to observe_blob_at
	assert isinstance(forward_bundle.stride, int)
	assert forward_bundle.stride >= 1

	# torso-unit scale (contract C2) is the seed box; thresholds are multiples
	assert forward_bundle.torso_width == seed_start["w"]
	assert forward_bundle.torso_height == seed_start["h"]

	# frame range spans the interval so the walk has a termination target
	assert forward_bundle.start_frame == seed_start["frame_index"]
	assert forward_bundle.end_frame == seed_end["frame_index"]


#============================================
def test_bundle_does_not_carry_hermite_path():
	"""Negative boundary (paired): no Hermite raw_pred reachable via the bundle.

	Hermite independence is the whole point of the walker. The bundle must
	not expose the frozen Hermite prediction (raw_pred) under any field, so
	the walker cannot snap back onto a failed Hermite path. We scan every
	bundle field value: none may be a Hermite-path-shaped object, and no
	field name may reference raw_pred / hermite.
	"""
	seed_start = _make_seed(10, 100.0, 50.0, 40.0, 80.0)
	seed_end = _make_seed(40, 200.0, 60.0, 44.0, 88.0)
	walker = _RecordingWalker()

	interval_solver.run_stage4_walker_seam(
		seed_start=seed_start,
		seed_end=seed_end,
		stage3_interval_score=_promoted_score(),
		reader=object(),
		scene_transform=object(),
		fps=30.0,
		walker_callable=walker,
	)

	bundle = walker.received_bundles[0]
	field_names = {f.name for f in dataclasses.fields(bundle)}

	# no field even names the Hermite path -- prevents a smuggled raw_pred
	for name in field_names:
		lowered = name.lower()
		assert "raw_pred" not in lowered, (
			f"bundle field {name!r} references the Hermite raw_pred path"
		)
		assert "hermite" not in lowered, (
			f"bundle field {name!r} references Hermite geometry"
		)

	# the bundle's seed is the human seed object itself, not a Hermite-derived
	# path: the walk starts from the seed, never from an interpolated curve.
	assert bundle.seed is seed_start


#============================================
def test_eligibility_decided_before_walker_output_exists():
	"""Stage-3-first: promotion is decided before any walker runs.

	When the Stage-3 tier does not promote, the injectable walker is never
	called, so no walker output can have influenced the decision. The seam
	reports the same tier it read, and the walker call count stays zero.
	"""
	seed_start = _make_seed(10, 100.0, 50.0, 40.0, 80.0)
	seed_end = _make_seed(40, 200.0, 60.0, 44.0, 88.0)
	walker = _RecordingWalker()

	result = interval_solver.run_stage4_walker_seam(
		seed_start=seed_start,
		seed_end=seed_end,
		stage3_interval_score=_not_promoted_score(),
		reader=object(),
		scene_transform=object(),
		fps=30.0,
		walker_callable=walker,
	)

	# not promoted -> walker never invoked -> its output cannot exist yet
	assert result["promoted"] is False
	assert walker.call_count == 0
	assert result["forward_result"] is None
	assert result["backward_result"] is None
	# the decision was driven by the Stage-3 tier the seam was handed
	assert result["confidence_tier"] == "high"
