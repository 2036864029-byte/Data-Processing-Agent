"""冷路径子进程沙箱执行设施：超时 / 资源上限 / 进程隔离（A5 归属，A6 续用）。

隔离边界 = 独立进程 + 不经过 shell + 显式工作目录与可选最小环境；
容器 / 远程沙箱由 manifest 声明，P0 不在此实现（加载器显式拒绝）。
资源上限（RLIMIT_AS / RLIMIT_CPU）仅 POSIX 生效，Windows 仅保证超时与进程隔离。
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    timeout_s: float = 30.0
    memory_mb: int | None = None
    cpu_seconds: int | None = None


DEFAULT_LIMITS: SandboxLimits = SandboxLimits()


@dataclass(frozen=True, slots=True)
class SandboxResult:
    ok: bool
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False


class SubprocessSandbox:
    """以子进程执行外部插件；超时即杀进程并标记 `timed_out`。"""

    def run(
        self,
        argv: Sequence[str],
        *,
        limits: SandboxLimits = DEFAULT_LIMITS,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
    ) -> SandboxResult:
        try:
            completed = subprocess.run(
                list(argv),
                cwd=None if cwd is None else str(cwd),
                env=None if env is None else dict(env),
                input=stdin,
                capture_output=True,
                timeout=limits.timeout_s,
                check=False,
                preexec_fn=_resource_limiter(limits),
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                ok=False,
                returncode=-1,
                stdout=exc.stdout if isinstance(exc.stdout, bytes) else b"",
                stderr=exc.stderr if isinstance(exc.stderr, bytes) else b"",
                timed_out=True,
            )

        return SandboxResult(
            ok=completed.returncode == 0,
            returncode=completed.returncode,
            stdout=completed.stdout if completed.stdout is not None else b"",
            stderr=completed.stderr if completed.stderr is not None else b"",
        )


def _resource_limiter(limits: SandboxLimits) -> Callable[[], None] | None:
    """POSIX 子进程入口钩子：设置地址空间与 CPU 时间上限（Windows 无此机制）。"""
    if sys.platform == "win32" or (
        limits.memory_mb is None and limits.cpu_seconds is None
    ):
        return None

    def _apply() -> None:
        resource = importlib.import_module("resource")
        if limits.memory_mb is not None:
            nbytes = limits.memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (nbytes, nbytes))
        if limits.cpu_seconds is not None:
            resource.setrlimit(
                resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds)
            )

    return _apply
