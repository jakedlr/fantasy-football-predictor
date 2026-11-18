"""Worker processes (Sprint 8): the one place they are started, for prediction and the Backtests.

- **spawn**, on every platform. Each worker starts from this process's current sys.path, so a
  script's own directory can be found. A forkserver would keep the sys.path it first started with,
  and Windows has no forkserver. fork is unsafe here: a run may still hold the Books' abandoned
  daemon threads, and polars runs a thread pool.
- **Ctrl-C belongs to the main process.** Workers ignore SIGINT, so they print no tracebacks of
  their own. The main process abandons the pool at once: queued tasks are cancelled and the
  workers terminated.
- **Per-worker setup.** `initializer(*initargs)` runs once in each worker, so shared data (the
  Books, the rules, a Backtest's tables) is sent once per worker, not once per task.
"""

import multiprocessing
import os
import signal
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager

START_METHOD = "spawn"


def usable_cpus() -> int:
    """The CPUs this process may run on: its affinity and container limits included, where Python can
    tell (os.process_cpu_count, 3.13+), else every CPU."""
    count = getattr(os, "process_cpu_count", os.cpu_count)()
    return count or 1


def _start(initializer: Callable | None, initargs: tuple) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl-C is the main process's to handle
    if initializer is not None:
        initializer(*initargs)


@contextmanager
def process_pool(workers: int, initializer: Callable | None = None, initargs: tuple = ()) -> Iterator[ProcessPoolExecutor]:
    """`workers` worker processes, set up with `initializer(*initargs)`. On leaving normally, waits for
    them; on any exception (Ctrl-C included), cancels what is queued and terminates them at once."""
    pool = ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context(START_METHOD),
                               initializer=_start, initargs=(initializer, initargs))
    try:
        yield pool
    except BaseException:
        _abandon(pool)
        raise
    pool.shutdown(wait=True)


def in_order(pool: ProcessPoolExecutor, fn: Callable, items) -> Iterator:
    """`fn(item)` for each item, run in `pool`, yielded in item order as each lands. Waits in short
    steps rather than blocking: a signal may be taken by another thread (an abandoned daemon thread),
    and only a waking main thread notices Ctrl-C."""
    from concurrent.futures import TimeoutError as Pending

    futures = [pool.submit(fn, item) for item in items]
    for future in futures:
        while True:
            try:
                yield future.result(timeout=0.1)
                break
            except Pending:
                continue


def _abandon(pool: ProcessPoolExecutor) -> None:
    processes = list((getattr(pool, "_processes", None) or {}).values())
    pool.shutdown(wait=False, cancel_futures=True)
    for process in processes:
        if process.is_alive():
            process.terminate()
