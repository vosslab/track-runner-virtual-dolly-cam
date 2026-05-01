"""Unit tests for common_tools/goodbox.py.

Anchors the FFT-friendly box-size predicate and the
largest_goodbox_at_most helper at known sizes.
"""

# PIP3 modules
import pytest

# local repo modules
import common_tools.goodbox as goodbox


#============================================
def test_powers_of_two_are_good():
	# all powers of 2 satisfy both rules trivially
	for n in (4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096):
		assert goodbox.is_good_size(n) is True


#============================================
def test_small_smooth_sizes():
	# small sizes composed only of small primes pass
	# 1 has no factors, m == 1 after the loop, so is_good_size(1) == True
	# 2 = 2^1, 3 = 3^1, etc. -- all under 4 so rule 2 vacuous
	assert goodbox.is_good_size(1) is True
	assert goodbox.is_good_size(2) is True
	assert goodbox.is_good_size(3) is True


#============================================
def test_anchors_from_plan():
	# anchors called out in the plan
	assert goodbox.is_good_size(1024) is True
	assert goodbox.is_good_size(1080) is False
	assert goodbox.is_good_size(1408) is True
	# 1584 = 2^4 * 3^2 * 11; rule 1 passes, but 1584 > 1024 so rule 2
	# requires divisibility by 32. 1584 % 32 == 16, fails.
	assert goodbox.is_good_size(1584) is False


#============================================
def test_largest_goodbox_examples():
	# 540 -> 528 (528 = 2^4 * 3 * 11, divisible by 16, rule 1 ok)
	assert goodbox.largest_goodbox_at_most(540) == 528
	# 1000 -> 960 (960 = 2^6 * 3 * 5, divisible by 16)
	assert goodbox.largest_goodbox_at_most(1000) == 960
	# 500 -> 480 (480 = 2^5 * 3 * 5, divisible by 16)
	assert goodbox.largest_goodbox_at_most(500) == 480
	# 1080 -> 1056 (1056 = 2^5 * 3 * 11, divisible by 32)
	assert goodbox.largest_goodbox_at_most(1080) == 1056


#============================================
def test_largest_goodbox_idempotent():
	# n that already passes the predicate is returned unchanged
	assert goodbox.largest_goodbox_at_most(1024) == 1024
	assert goodbox.largest_goodbox_at_most(256) == 256
	assert goodbox.largest_goodbox_at_most(960) == 960


#============================================
def test_prime_factor_too_large():
	# 13, 17, 19, 23 are primes > 11; rule 1 fails
	assert goodbox.is_good_size(13) is False
	assert goodbox.is_good_size(17) is False
	# 26 = 2 * 13
	assert goodbox.is_good_size(26) is False


#============================================
def test_alignment_thresholds():
	# the plan documents the alignment thresholds:
	# n > 4   -> n % 2 == 0
	# n > 16  -> n % 4 == 0
	# n > 64  -> n % 8 == 0
	# n > 256 -> n % 16 == 0
	# 18 = 2 * 3^2; rule 1 ok, but 18 > 16 -> needs % 4 == 0; 18 % 4 == 2; fail
	assert goodbox.is_good_size(18) is False
	# 20 = 2^2 * 5; 20 > 16 -> needs % 4 == 0; 20 % 4 == 0; pass
	assert goodbox.is_good_size(20) is True
	# 72 = 2^3 * 3^2; 72 > 64 -> needs % 8 == 0; 72 % 8 == 0; pass
	assert goodbox.is_good_size(72) is True
	# 70 = 2 * 5 * 7; 70 > 64 -> needs % 8 == 0; 70 % 8 == 6; fail
	assert goodbox.is_good_size(70) is False


#============================================
def test_is_good_size_rejects_zero():
	with pytest.raises(ValueError):
		goodbox.is_good_size(0)


#============================================
def test_is_good_size_rejects_negative():
	with pytest.raises(ValueError):
		goodbox.is_good_size(-4)


#============================================
def test_largest_goodbox_rejects_small():
	with pytest.raises(ValueError):
		goodbox.largest_goodbox_at_most(3)
	with pytest.raises(ValueError):
		goodbox.largest_goodbox_at_most(0)


#============================================
def test_is_good_size_type_error():
	with pytest.raises(TypeError):
		goodbox.is_good_size(4.0)
