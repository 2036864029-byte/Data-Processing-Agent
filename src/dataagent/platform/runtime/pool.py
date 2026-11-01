"""CPU 密集路径进程池（宪法 VI）。

吞吐来自进程数而非事件循环；禁用线程池（Rust 扩展段错误会杀整个进程，
try/except 与线程池都接不住）。子进程崩溃由 `BrokenProcessPool` 上报，主进程存活。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor
from types import TracebackType
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


class ProcessPool:
    """`ProcessPoolExecutor` 的薄封装；用 with 语句保证回收。"""

    def __init__(self, max_workers: int) -> None:
        if max_workers < 1:
            raise ValueError("max_workers 必须 ≥ 1")
        self._executor = ProcessPoolExecutor(max_workers=max_workers)

    @property
    def executor(self) -> ProcessPoolExecutor:
        return self._executor

    def map(self, fn: Callable[[T], R], items: Iterable[T]) -> list[R]:
        """按序返回结果；子进程崩溃时抛 BrokenProcessPool。"""
        return list(self._executor.map(fn, items))

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)

    def __enter__(self) -> ProcessPool:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.shutdown()
