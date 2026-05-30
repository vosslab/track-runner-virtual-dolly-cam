"""Bug #101 reproduction (xfail until the typed-coord M2 fix lands).

Bug #101: at bin_factor > 1 the blob_walk_v2 batch entry point
(make_walk_html_v2.process_video) feeds SOURCE-pixel seeds into
walk_driver.run_interval_walk, which builds a SOURCE-scale roi_override and
hands it to residual_motion.observe_blob_at. That source-scale ROI is clamped
against the PROCESSED frame width, collapsing the x-range to zero, and
compute_residual_for_frame raises
  ValueError: degenerate ROI (h=..., w=0) at frame ...; prediction off-frame?

This test drives the same reproducing call as
tests/e2e/e2e_bug_101_degenerate_roi.py (the primary, faster gate). It is
marked xfail because the bug is expected to raise today; the typed-coord
migration (docs/active_plans/active/typed_coordinate_space_plan.md, M2) makes
off-frame an explicit in-bounds soft-miss, at which point this xfail flips to
pass (strict=False so the flip does not fail the suite).

The reproduction needs the real bin>1 source video, so it is skipped when the
fixture is absent. It is slower than the pytest budget by design; the e2e
script is the canonical gate (see docs/E2E_TESTS.md).
"""

# Standard Library
import os
import importlib.util

# PIP3 modules
import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURE_VIDEO = os.path.join(
	_REPO_ROOT, 'TRACK_VIDEOS', 'Jason-3200m-sectionals-IMG_4005.mkv'
)
_E2E_REPRO = os.path.join(
	_REPO_ROOT, 'tests', 'e2e', 'e2e_bug_101_degenerate_roi.py'
)


#============================================
def _load_e2e_reproducer():
	"""Import the e2e reproduction module by file path (it lives under
	tests/e2e/, which pytest does not collect, so a normal import is not
	available). Returns the loaded module.
	"""
	spec = importlib.util.spec_from_file_location('e2e_bug_101_degenerate_roi', _E2E_REPRO)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


#============================================
@pytest.mark.skipif(
	not os.path.isfile(_FIXTURE_VIDEO),
	reason='bug #101 needs the real bin>1 Jason source video (absent here)',
)
def test_bug_101_degenerate_roi_no_longer_raises():
	"""The batch call path no longer raises the degenerate-ROI ValueError.

	Bug #101 (degenerate ROI w=0 at bin>1) is fixed by the typed-coord M2
	work (docs/active_plans/active/typed_coordinate_space_plan.md). Off-frame
	predictions became an explicit in-bounds soft-miss (WS2-C) instead of a
	deep degenerate-ROI crash, and the batch entry point now feeds PROCESSED
	seeds (WS2-D). This drives the original reproducing call path and asserts
	it completes without the degenerate-ROI ValueError. A regression that
	re-introduces the crash flips this to a hard fail.
	"""
	reproducer = _load_e2e_reproducer()
	# Raises ValueError('degenerate ROI ...') if bug #101 regresses; any raise
	# fails the test loudly per the no-try/except philosophy.
	reproducer.reproduce_degenerate_roi()
