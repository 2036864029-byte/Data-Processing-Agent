"""冷路径子进程沙箱测试（T035，A5）。

超时 / 非零退出 / 标准输入输出传递；资源上限仅在 POSIX 生效（Windows 跳过）。
"""

import sys

from dataagent.platform.runtime.sandbox import SandboxLimits, SubprocessSandbox


def test_run_captures_stdout_and_returncode() -> None:
    result = SubprocessSandbox().run(
        [sys.executable, "-c", "print('hi')"],
        limits=SandboxLimits(timeout_s=20),
    )
    assert result.ok is True
    assert result.returncode == 0
    assert result.stdout.strip() == b"hi"
    assert result.timed_out is False


def test_run_passes_stdin_through() -> None:
    result = SubprocessSandbox().run(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        limits=SandboxLimits(timeout_s=20),
        stdin=b"payload",
    )
    assert result.ok is True
    assert result.stdout == b"payload"


def test_timeout_kills_process_and_flags_it() -> None:
    result = SubprocessSandbox().run(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        limits=SandboxLimits(timeout_s=1.0),
    )
    assert result.ok is False
    assert result.timed_out is True


def test_nonzero_exit_is_failure() -> None:
    result = SubprocessSandbox().run(
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        limits=SandboxLimits(timeout_s=20),
    )
    assert result.ok is False
    assert result.returncode == 3
    assert result.timed_out is False
