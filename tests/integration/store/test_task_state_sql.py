"""任务状态 PG 仓储集成测试：状态 / 续跑点 / 已完成文档结果。"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from dataagent.kernel.state import State
from dataagent.pipeline.task.factory import create_builtin_runner
from dataagent.platform.store.sql.engine import Database
from dataagent.platform.store.sql.task_state import SqlTaskStateStore

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[3]


def alembic_config(url: str) -> Config:
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


@pytest.fixture
def store(pg_url: str) -> SqlTaskStateStore:
    command.upgrade(alembic_config(pg_url), "head")
    return SqlTaskStateStore(Database(pg_url))


def test_state_and_outcomes_roundtrip(store: SqlTaskStateStore) -> None:
    store.save_state("task-1", State.PARSING)
    store.set_input_signature("task-1", "sig-1")
    store.save_outcome("task-1", 0, {"doc_id": "d0", "status": "processed"})
    store.commit_batch("task-1", 0)

    assert store.state("task-1") is State.PARSING
    assert store.input_signature("task-1") == "sig-1"
    assert store.resume_point("task-1") == 0
    assert store.outcomes("task-1")[0]["status"] == "processed"

    store.reset_outcomes("task-1")
    assert store.outcomes("task-1") == []
    assert store.resume_point("task-1") == -1


def test_runner_resumes_from_pg(
    tmp_path: Path,
    write_pdf,
    pg_url: str,
) -> None:
    store = SqlTaskStateStore(Database(pg_url))
    first_path = write_pdf(
        tmp_path / "a.pdf",
        [
            [
                f"alpha paragraph {index:03d} with supporting text"
                for index in range(60)
            ]
        ],
    )

    first = create_builtin_runner(
        output_dir=tmp_path / "out", state_store=store
    ).run([first_path], task_id="pg-resume")
    removed = Path(first.documents[0].outputs[0])
    removed.unlink()

    restarted = SqlTaskStateStore(Database(pg_url))
    resumed = create_builtin_runner(
        output_dir=tmp_path / "out", state_store=restarted
    ).run([first_path], task_id="pg-resume")

    assert resumed.count("processed") == 1
    assert not removed.exists()  # PG 中记录的已完成文档没有重做
