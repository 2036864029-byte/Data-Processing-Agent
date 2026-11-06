"""任务级强杀续跑演练（PR-4-4）。

真实独立进程 + PG 状态存储 + 流式落盘：首个文档已完成并记录后被强杀，
重启后已完成文档跳过（产物不被重写）、未完成文档续跑，收尾 manifest
与落盘产物对账通过。`Popen.kill()` 在 POSIX 为 SIGKILL、Windows 为 TerminateProcess。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from dataagent.pipeline.output.manifest import verify_manifest
from dataagent.pipeline.task.state_store import SqlTaskStateStore
from dataagent.platform.store.sql.engine import Database

pytestmark = [pytest.mark.integration, pytest.mark.fault]

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "resume_demo_task.py"
TASK_ID = "task-kill-drill"
DOC_COUNT = 8


def _alembic_config(url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture
def pg_url(pg_container) -> str:
    host = pg_container.get_container_host_ip()
    port = pg_container.get_exposed_port(pg_container.port)
    return (
        f"postgresql+psycopg://{pg_container.username}:{pg_container.password}"
        f"@{host}:{port}/{pg_container.dbname}"
    )


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + (
        os.pathsep + existing if existing else ""
    )
    return env


def _start_child(
    pg_url: str, output_dir: Path, inputs: list[Path]
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            str(SCRIPT),
            "--pg-url",
            pg_url,
            "--task-id",
            TASK_ID,
            "--output-dir",
            str(output_dir),
            *[str(path) for path in inputs],
        ],
        env=_child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _completed_documents(
    store: SqlTaskStateStore, task_id: str
) -> list[dict[str, object]]:
    return [payload for payload in store.outcomes(task_id) if payload]


def _wait_for_documents(
    store: SqlTaskStateStore,
    process: subprocess.Popen[bytes],
    minimum: int,
    *,
    timeout: float = 120,
) -> list[dict[str, object]]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        completed = _completed_documents(store, TASK_ID)
        if len(completed) >= minimum:
            return completed
        if process.poll() is not None:
            _, stderr = process.communicate()
            pytest.fail(
                "任务在达到初始进度前已退出："
                + stderr.decode("utf-8", "replace")
            )
        time.sleep(0.01)
    pytest.fail("等待任务写入已完成文档超时")


def test_hard_kill_mid_task_resumes_without_redoing_completed_documents(
    tmp_path: Path,
    write_pdf,
    pg_url: str,
) -> None:
    command.upgrade(_alembic_config(pg_url), "head")
    inputs = [
        write_pdf(
            tmp_path / f"doc_{index:02d}.pdf",
            [
                [
                    f"doc{index} page1 paragraph {row:03d} supporting text"
                    for row in range(40)
                ],
                [
                    f"doc{index} page2 paragraph {row:03d} supporting text"
                    for row in range(40)
                ],
                [
                    f"doc{index} page3 paragraph {row:03d} supporting text"
                    for row in range(40)
                ],
            ],
        )
        for index in range(DOC_COUNT)
    ]
    output_dir = tmp_path / "out"
    store = SqlTaskStateStore(Database(pg_url))

    first = _start_child(pg_url, output_dir, inputs)
    try:
        completed = _wait_for_documents(store, first, minimum=1)
    finally:
        first.kill()
        first.wait(timeout=10)

    assert 0 < len(completed) < DOC_COUNT  # 确实在任务完成前被杀
    checkpoint = completed[0]
    md_path = next(
        Path(str(path))
        for path in checkpoint["outputs"]  # type: ignore[union-attr]
        if str(path).endswith(".md")
    )
    md_path.write_bytes(md_path.read_bytes() + b"\nSENTINEL\n")

    second = _start_child(pg_url, output_dir, inputs)
    stdout, stderr = second.communicate(timeout=180)
    assert second.returncode == 0, stderr.decode("utf-8", "replace") or stdout.decode(
        "utf-8", "replace"
    )

    # 已完成文档没有重做：哨兵内容仍在（重写产物会覆盖它）
    assert "SENTINEL" in md_path.read_text(encoding="utf-8")
    final = _completed_documents(store, TASK_ID)
    assert len(final) == DOC_COUNT

    report = verify_manifest(output_dir)
    assert report.ok
    assert report.documents == DOC_COUNT
