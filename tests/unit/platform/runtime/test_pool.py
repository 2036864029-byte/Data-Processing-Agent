"""进程池隔离测试（T040，A6 / 宪法 VI）。

CPU 密集路径用进程池（禁线程池）；子进程崩溃（模拟 Rust 扩展崩溃）不杀主进程。
"""

import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import pytest

from dataagent.platform.runtime.pool import ProcessPool


def pid_worker(_: int) -> int:
    return os.getpid()


def crash_worker(_: int) -> int:
    os._exit(1)  # 模拟 Rust 扩展段错误：进程直接死亡，try/except 接不住


def test_workers_run_in_separate_processes() -> None:
    with ProcessPool(max_workers=2) as pool:
        pids = pool.map(pid_worker, [1, 2, 3])

    assert len(pids) == 3
    assert all(pid != os.getpid() for pid in pids)


def test_uses_process_pool_not_thread_pool() -> None:
    with ProcessPool(max_workers=1) as pool:
        assert isinstance(pool.executor, ProcessPoolExecutor)
        assert not isinstance(pool.executor, ThreadPoolExecutor)


def test_child_crash_does_not_kill_parent() -> None:
    parent_pid = os.getpid()
    with ProcessPool(max_workers=1) as pool, pytest.raises(BrokenProcessPool):
        pool.map(crash_worker, [1])

    assert os.getpid() == parent_pid


def test_invalid_worker_count_rejected() -> None:
    with pytest.raises(ValueError):
        ProcessPool(max_workers=0)
