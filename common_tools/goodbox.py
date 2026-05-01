"""FFT-friendly box-size predicate (cryo-EM goodbox rule).

The cv2.phaseCorrelate FFT cost is dramatically lower on box sizes
whose prime factors are small and whose 2-alignment grows with size.
This module provides the canonical "goodbox" predicate and a helper
to snap a dimension down to the largest goodbox not exceeding it.

Rule (canonical):
	1. All prime factors of n are <= 11.
	2. Let x be the largest integer such that 4^x < n. Then
	   n % 2^x == 0.

Equivalent alignment summary: n > 4 -> n % 2 == 0;
n > 16 -> n % 4 == 0; n > 64 -> n % 8 == 0;
n > 256 -> n % 16 == 0; n > 1024 -> n % 32 == 0;
n > 4096 -> n % 64 == 0.
"""


#============================================
def is_good_size(n: int) -> bool:
	"""Return True if n satisfies the goodbox predicate.

	Args:
		n: candidate box size, must be a positive integer.

	Returns:
		True if all prime factors of n are <= 11 and n meets the
		size-dependent 2-alignment requirement; False otherwise.

	Raises:
		ValueError: if n < 1.
	"""
	if not isinstance(n, int):
		raise TypeError(f"is_good_size requires int, got {type(n).__name__}")
	if n < 1:
		raise ValueError(f"is_good_size requires n >= 1, got {n}")
	# rule 1: all prime factors must be <= 11
	m = n
	for p in (2, 3, 5, 7, 11):
		while m % p == 0:
			m //= p
	if m != 1:
		return False
	# rule 2: alignment grows with size
	# x is the largest integer such that 4^(x+1) <= n, equivalently 4^x < n
	x = 0
	while 4 ** (x + 1) < n:
		x += 1
	if x > 0 and n % (2 ** x) != 0:
		return False
	return True


#============================================
def largest_goodbox_at_most(n: int) -> int:
	"""Return the largest goodbox not exceeding n.

	Walks downward from n until the predicate holds. For n <= 3
	the predicate may not hold; this helper raises in that case
	rather than returning a degenerate size.

	Args:
		n: upper bound on the returned goodbox size.

	Returns:
		Largest k such that k <= n and is_good_size(k) is True.

	Raises:
		ValueError: if n < 4 (no useful goodbox exists below 4).
	"""
	if not isinstance(n, int):
		raise TypeError(f"largest_goodbox_at_most requires int, got {type(n).__name__}")
	if n < 4:
		raise ValueError(f"largest_goodbox_at_most requires n >= 4, got {n}")
	# walk downward; small primes guarantee a hit within a few steps
	k = n
	while k >= 4 and not is_good_size(k):
		k -= 1
	return k
