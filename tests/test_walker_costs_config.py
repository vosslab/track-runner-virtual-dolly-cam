"""Config-propagation tests for the walker_costs Viterbi cost weights.

Covers the WP-COST-1 config flow that is NOT exercised by the cost-model unit
tests (tests/test_walk_cost_model.py, owned separately):

- set_cost_weights installs config-driven weights that _active_weights resolves
  (the path solver_workers._worker_init takes once per process).
- tr_config.resolve_config reproduces the old cli per-video/default if/else
  branch for both the default and per-video cases.
- a partial walker_costs section fails loudly at validation.
- ExecutionContext carries walker_costs through to make_pool (F1 plumbing check).

Assertions target behavior (which weights are in force, which branch resolved,
that a partial section raises), not hardcoded default magnitudes, per the repo
pytest style guide.
"""

# Standard Library
import copy

# PIP3 modules
import pytest

# local repo modules
import yaml
import tr_paths
import tr_config
import interval_solver
import solve_queue
import solver_workers
import blob_walk.walk_viterbi as walk_viterbi


#============================================
@pytest.fixture(autouse=True)
def _isolate_cost_weights():
	"""Clear the module weight cache before and after each test."""
	walk_viterbi.reset_cost_weights_for_tests(None)
	yield
	walk_viterbi.reset_cost_weights_for_tests(None)


#============================================
def _non_default_walker_costs() -> dict:
	"""A walker_costs dict whose values differ from the module defaults.

	Each value is offset from the live default so the test detects whether the
	installed weight, not the default, is in force -- without hardcoding the
	default magnitude itself.
	"""
	defaults = walk_viterbi._active_weights()
	bumped = {name: defaults[name] + 10.0 for name in walk_viterbi.COST_WEIGHT_NAMES}
	return bumped


#============================================
def test_set_cost_weights_overrides_active_weights():
	# Behavior: after install, every weight the DP reads equals the injected
	# value, not the module default. This is the _worker_init install path.
	injected = _non_default_walker_costs()
	walk_viterbi.set_cost_weights(injected)
	active = walk_viterbi._active_weights()
	for name in walk_viterbi.COST_WEIGHT_NAMES:
		assert active[name] == injected[name]


#============================================
def test_reset_restores_module_defaults():
	# Behavior: clearing the cache returns the DP to its module-constant
	# defaults (the value before any install). Compare to a fresh-cleared read.
	walk_viterbi.reset_cost_weights_for_tests(None)
	defaults = copy.deepcopy(walk_viterbi._active_weights())
	walk_viterbi.set_cost_weights(_non_default_walker_costs())
	walk_viterbi.reset_cost_weights_for_tests(None)
	assert walk_viterbi._active_weights() == defaults


#============================================
def test_resolve_config_default_matches_old_branch(tmp_path):
	# resolve_config with a nonexistent per-video config must take the default
	# branch -- byte-equivalent to the old cli `else: read_default_config()`.
	# Use tmp_path so the path does not exist but is not a literal /tmp string
	# (avoids a B108 bandit false-positive on the plain /tmp/ form).
	missing_input = str(tmp_path / "this_video_has_no_config_walkercosts.mkv")
	resolved, had_config = tr_config.resolve_config(missing_input)
	old_branch = tr_config.read_default_config()
	assert had_config is False
	assert resolved == old_branch


#============================================
def test_resolve_config_per_video_matches_old_branch(tmp_path):
	# resolve_config with an existing per-video config must take the load branch
	# -- equivalent to the old cli `load_config(path, auto_save_migration=True)`.
	default_cfg = tr_config.read_default_config()
	per_video = copy.deepcopy(default_cfg)
	# perturb a walker weight so the per-video file is distinguishable from the
	# default and we can confirm the file (not the default) was loaded.
	per_video["walker_costs"]["SKIP_COST"] = default_cfg["walker_costs"]["SKIP_COST"] + 1.0
	config_path = tmp_path / "myvideo.track_runner.config.yaml"
	with open(config_path, "w") as fh:
		yaml.dump(per_video, fh, default_flow_style=False, sort_keys=False)

	resolved, had_config = tr_config.resolve_config(
		"unused_input.mkv", config_path=str(config_path),
	)
	old_branch = tr_config.load_config(str(config_path), auto_save_migration=True)
	assert had_config is True
	assert resolved == old_branch
	# and the loaded per-video weight, not the default, is what came back.
	assert resolved["walker_costs"]["SKIP_COST"] == per_video["walker_costs"]["SKIP_COST"]


#============================================
def test_resolve_config_then_install_uses_per_video_weights(tmp_path):
	# End-to-end config flow: resolve a per-video config, install its weights,
	# and confirm the DP reads the per-video values.
	default_cfg = tr_config.read_default_config()
	per_video = copy.deepcopy(default_cfg)
	bumped = _non_default_walker_costs()
	per_video["walker_costs"] = bumped
	config_path = tmp_path / "myvideo.track_runner.config.yaml"
	with open(config_path, "w") as fh:
		yaml.dump(per_video, fh, default_flow_style=False, sort_keys=False)

	resolved, _had = tr_config.resolve_config(
		"unused.mkv", config_path=str(config_path),
	)
	tr_config.validate_config(resolved)
	walk_viterbi.set_cost_weights(resolved["walker_costs"])
	active = walk_viterbi._active_weights()
	for name in walk_viterbi.COST_WEIGHT_NAMES:
		assert active[name] == bumped[name]


#============================================
def test_partial_walker_costs_section_fails_loudly():
	# A walker_costs section missing one required weight must raise at
	# validation, not silently fall back to a default for the missing key.
	cfg = tr_config.read_default_config()
	del cfg["walker_costs"][walk_viterbi.COST_WEIGHT_NAMES[0]]
	with pytest.raises(RuntimeError):
		tr_config.validate_config(cfg)


#============================================
def test_absent_walker_costs_section_validates():
	# The section is optional: a config without it validates (callers keep the
	# walk_viterbi module defaults). This guards the optional-section contract.
	cfg = tr_config.read_default_config()
	del cfg["walker_costs"]
	# Should not raise.
	tr_config.validate_config(cfg)


#============================================
def test_default_config_path_round_trips_resolution(tmp_path, monkeypatch):
	# resolve_config(input_file) with no explicit path must look up the
	# per-video file under the tr_paths data dir. Point the data dir at tmp and
	# confirm the file there is loaded.
	monkeypatch.setattr(tr_paths, "DATA_DIR", str(tmp_path))
	default_cfg = tr_config.read_default_config()
	per_video = copy.deepcopy(default_cfg)
	per_video["walker_costs"]["WEIGHT_DISPLACEMENT"] = (
		default_cfg["walker_costs"]["WEIGHT_DISPLACEMENT"] + 5.0
	)
	# default_config_path strips the extension: stem == "clip".
	target = tmp_path / "clip.track_runner.config.yaml"
	with open(target, "w") as fh:
		yaml.dump(per_video, fh, default_flow_style=False, sort_keys=False)

	resolved, had_config = tr_config.resolve_config("clip.mkv")
	assert had_config is True
	assert resolved["walker_costs"]["WEIGHT_DISPLACEMENT"] == (
		per_video["walker_costs"]["WEIGHT_DISPLACEMENT"]
	)


#============================================
def test_execution_context_carries_walker_costs_to_make_pool(monkeypatch):
	"""F1 plumbing: _dispatch_blob_pass passes context.walker_costs to make_pool.

	Builds a minimal but sufficient setup to trigger the pool path inside
	_dispatch_blob_pass (requires num_workers >= 2 and len(tasks) >= 4), then
	asserts the make_pool call receives the exact walker_costs dict from the
	ExecutionContext. Mocks at the make_pool boundary; no real subprocess is started.
	"""
	# Build a non-default walker_costs dict (values offset from module defaults).
	defaults = walk_viterbi._active_weights()
	injected_costs = {name: defaults[name] + 7.0 for name in walk_viterbi.COST_WEIGHT_NAMES}

	# Five seeds to support four consecutive intervals (pair_idx 0-3).
	# _dispatch_blob_pass calls filter_usable_seeds_sorted(seeds) which requires
	# a "status" field; "visible" is the normal usable-seed status.
	seeds = [
		{
			"frame_index": i * 10,
			"cx": float(i),
			"cy": 0.0,
			"w": 1.0,
			"h": 1.0,
			"status": "visible",
		}
		for i in range(5)
	]

	context = solve_queue.ExecutionContext(
		reader=None,
		scene_transform=object(),
		motion_track=None,
		all_seeds=seeds,
		all_seeds_scene=[
			(s["frame_index"], s["cx"], 0.0, 1.0, 1.0) for s in seeds
		],
		fps=30.0,
		num_workers=2,
		video_path="/fake/video.mkv",
		video_frame_count=100,
		debug=False,
		walker_costs=injected_costs,
	)

	# Capture make_pool kwargs and return a minimal context-manager stand-in.
	captured = {}

	class _FakePool:
		"""Minimal context manager; raises on submit to stop execution cleanly."""
		def __enter__(self):
			return self
		def __exit__(self, *a):
			return False
		def submit(self, *a, **k):
			raise StopIteration("stop after pool entry -- no real solve needed")

	def fake_make_pool(**kwargs):
		captured.update(kwargs)
		return _FakePool()

	monkeypatch.setattr(solver_workers, "make_pool", fake_make_pool)

	# Four dummy interval results (pairs 0-3) so all four tasks get built and
	# len(tasks) >= 4 triggers the pool path.
	dummy_result = {"source": "hermite", "confidence_tier": "low"}
	interval_results = [dict(dummy_result) for _ in range(4)]

	# _dispatch_blob_pass enters the pool context then calls pool.submit, which
	# raises StopIteration to cut short without running real workers. The pool
	# path is confirmed when make_pool is called (captured dict is populated).
	import contextlib
	with contextlib.suppress(StopIteration, RuntimeError):
		interval_solver._dispatch_blob_pass(
			"Stage 4: blob promotion pass",
			list(range(4)),
			interval_results,
			context.all_seeds,
			context,
			num_workers=2,
			debug=False,
			run_control=None,
			on_interval_solved=None,
			video_path="/fake/video.mkv",
			blob_pass=True,
		)

	# Core assertion: make_pool was reached and received the injected walker_costs.
	assert "walker_costs" in captured, (
		"make_pool was not called -- _dispatch_blob_pass did not reach the pool path; "
		"check that len(tasks) >= 4 and num_workers >= 2 to trigger the pool branch"
	)
	assert captured["walker_costs"] is injected_costs
