"""Contract for the worker-local residual store.

The Stage-4 pre-pass builds a store keyed by (frame_index, roi) that both the
forward and backward passes read. This covers the store behavior those passes
depend on: a hit returns the stored pair, a miss is reported as absent so the
caller falls through to a direct read, and byte accounting bounds growth.
"""

# PIP3 modules
import numpy

# local repo modules
import residual_pre_pass


#============================================
def _residual_pair(size: int) -> tuple:
	"""Build one (residual, validity mask) pair of the given square size."""
	residual = numpy.zeros((size, size), dtype=numpy.float32)
	validity = numpy.full((size, size), 255, dtype=numpy.uint8)
	return (residual, validity)


#============================================
def test_stored_pair_is_returned_for_its_key() -> None:
	"""A stored (frame_index, roi) key returns the pair that was stored."""
	store = residual_pre_pass._ByteBoundedLruStore(max_bytes=10 * 1024 * 1024)
	key = (42, (10, 20, 30, 40))
	pair = _residual_pair(4)
	store.store_result(key, pair)
	assert key in store
	assert store[key] is pair


#============================================
def test_absent_key_reports_missing() -> None:
	"""An unstored key reports absent so the caller reads it directly."""
	store = residual_pre_pass._ByteBoundedLruStore(max_bytes=10 * 1024 * 1024)
	store.store_result((42, (10, 20, 30, 40)), _residual_pair(4))
	assert (43, (10, 20, 30, 40)) not in store


#============================================
def test_roi_is_part_of_the_key() -> None:
	"""The same frame with a different ROI is a separate entry."""
	store = residual_pre_pass._ByteBoundedLruStore(max_bytes=10 * 1024 * 1024)
	store.store_result((7, (0, 0, 10, 10)), _residual_pair(4))
	assert (7, (0, 0, 10, 10)) in store
	assert (7, (5, 5, 10, 10)) not in store


#============================================
def test_store_evicts_to_stay_within_its_byte_budget() -> None:
	"""Storing past the budget evicts earlier entries instead of growing."""
	one_pair_bytes = sum(part.nbytes for part in _residual_pair(8))
	store = residual_pre_pass._ByteBoundedLruStore(max_bytes=2 * one_pair_bytes)
	for frame_index in range(5):
		store.store_result((frame_index, (0, 0, 8, 8)), _residual_pair(8))
	assert store.total_bytes <= store.max_bytes
	assert store.eviction_count > 0


#============================================
def test_oversize_pair_is_declined() -> None:
	"""A pair larger than the whole budget is declined rather than stored."""
	store = residual_pre_pass._ByteBoundedLruStore(max_bytes=16)
	store.store_result((1, (0, 0, 8, 8)), _residual_pair(8))
	assert (1, (0, 0, 8, 8)) not in store
	assert store.oversize_count == 1
