"""ffp.workers: the one place worker processes are started (Sprint 8 review)."""

import os
import signal
import threading
import time

import pytest

from ffp import workers


def _sigint_handler(_):
    return signal.getsignal(signal.SIGINT) == signal.SIG_IGN


def _sleep(seconds):
    time.sleep(seconds)
    return seconds


def _setup_value(_):
    return workers_seen


workers_seen = None


def _remember(value):
    global workers_seen
    workers_seen = value


def test_workers_leave_ctrl_c_to_the_main_process():
    # Review finding 4: every worker printed its own KeyboardInterrupt traceback.
    with workers.process_pool(2) as pool:
        assert all(pool.map(_sigint_handler, range(2)))


def test_each_worker_is_set_up_once_with_its_data():
    with workers.process_pool(2, _remember, ("books",)) as pool:
        assert list(pool.map(_setup_value, range(4))) == ["books"] * 4


def test_ctrl_c_abandons_the_workers_at_once():
    # Another thread may take the signal (here a blocked daemon thread, as a run's abandoned Book threads
    # are): the main thread must wait in short steps to notice it.
    threading.Thread(target=threading.Event().wait, daemon=True).start()
    began = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        with workers.process_pool(2) as pool:
            # A real SIGINT, as Ctrl-C sends (interrupt_main only sets a flag a blocking wait never sees).
            threading.Timer(1.0, os.kill, (os.getpid(), signal.SIGINT)).start()
            list(workers.in_order(pool, _sleep, [30, 30]))
    assert time.monotonic() - began < 5


def test_results_come_back_in_order():
    with workers.process_pool(3) as pool:
        assert list(workers.in_order(pool, _sleep, [0.3, 0.0, 0.1])) == [0.3, 0.0, 0.1]


def test_workers_start_with_spawn_which_every_platform_has_and_which_reads_sys_path_now():
    # Review findings 2 and 5: forkserver kept the sys.path of its first start, and Windows has none.
    assert workers.START_METHOD == "spawn"


def test_cpus_counts_only_those_this_process_may_use(monkeypatch):
    # Review finding 7: os.cpu_count() ignores affinity and container limits.
    monkeypatch.setattr(os, "process_cpu_count", lambda: 3, raising=False)
    assert workers.usable_cpus() == 3
