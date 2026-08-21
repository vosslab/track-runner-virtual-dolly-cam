"""Emergency controls for ProcessPoolExecutor-owned worker processes."""


#============================================
def force_kill_pool(pool: object) -> None:
	"""Terminate, reap, and cancel a live worker pool exactly once.

	This deliberately uses ProcessPoolExecutor's ``_processes`` ownership map:
	the normal context-manager shutdown waits for running encoders, while an
	interactive quit must return after ending them.

	Args:
		pool: A live concurrent.futures.ProcessPoolExecutor.
	"""
	processes = pool._processes
	if processes is None:
		return
	# Snapshot the executor-owned children before shutdown clears its map.
	children = list(processes.values())
	for process in children:
		if process.is_alive():
			process.terminate()
	# Reap every child now so a later executor cleanup cannot hide a wait.
	for process in children:
		process.join()
	pool.shutdown(wait=False, cancel_futures=True)
