"""P0-A 续跑演示（T068）：真实强杀 worker 后从 resume_point 继续。

子进程是真实独立进程；`Popen.kill()` 在 POSIX 为 SIGKILL、Windows 为 TerminateProcess，
模拟“进程被强杀、没有清理钩子”。父进程重启 worker，断言：
- 已提交批不重做（第二轮不执行 resume_point 及之前的批）；
- 最终每个批恰好提交一次，resume_point 到达最后一批。
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER = REPO_ROOT / "scripts" / "resume_demo_worker.py"
TOTAL_PAGES = 8


def worker_env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + (
        os.pathsep + existing if existing else ""
    )
    return env


def start_worker(
    state: Path, objects: Path, log: Path, run_id: str
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            str(WORKER),
            "--state",
            str(state),
            "--objects",
            str(objects),
            "--log",
            str(log),
            "--run-id",
            run_id,
            "--total-pages",
            str(TOTAL_PAGES),
            "--batch-pages",
            "1",
            "--batch-ms",
            "200",
        ],
        env=worker_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def wait_for_progress(state: Path, minimum: int, process: subprocess.Popen[bytes]) -> int:
    deadline = time.time() + 60
    while time.time() < deadline:
        if state.is_file():
            data = json.loads(state.read_text(encoding="utf-8"))
            resume_point = data.get("resume_point")
            if resume_point is not None and resume_point >= minimum:
                return int(resume_point)
        if process.poll() is not None:
            _, stderr = process.communicate()
            pytest.fail(f"worker 在达到初始进度前已退出：{stderr.decode('utf-8', 'replace')}")
        time.sleep(0.02)
    pytest.fail("等待 worker 提交批次超时")


def test_hard_kill_worker_resumes_without_redoing_completed_batches(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    objects = tmp_path / "objects"
    log = tmp_path / "executed.jsonl"

    first = start_worker(state, objects, log, run_id="1")
    try:
        first_resume = wait_for_progress(state, minimum=1, process=first)
    finally:
        first.kill()
        first.wait(timeout=10)

    assert first_resume < TOTAL_PAGES - 1  # 确实在完成前被杀

    second = start_worker(state, objects, log, run_id="2")
    stdout, stderr = second.communicate(timeout=60)
    assert second.returncode == 0, stderr.decode("utf-8", "replace") or stdout

    final = json.loads(state.read_text(encoding="utf-8"))
    assert final["commits"] == list(range(TOTAL_PAGES))
    assert final["resume_point"] == TOTAL_PAGES - 1

    records = [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    second_run = [
        record for record in records if record["event"] == "executed" and record["run"] == "2"
    ]
    assert second_run, "第二轮没有执行剩余批次"
    for record in second_run:
        batch_index = record["page_range"][0] - 1  # batch_pages=1
        assert batch_index > first_resume, (
            f"已完成批 {batch_index} 被重做（resume_point={first_resume}）"
        )
