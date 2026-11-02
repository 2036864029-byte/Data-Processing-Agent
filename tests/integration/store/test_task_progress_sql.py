"""任务进度表集成测试：迁移建表、upsert、最近任务查询。"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from dataagent.platform.store.sql.engine import Database
from dataagent.platform.store.sql.tasks import SqlTaskProgressStore, TaskProgress

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
def store(pg_url: str) -> SqlTaskProgressStore:
    command.upgrade(alembic_config(pg_url), "head")
    return SqlTaskProgressStore(Database(pg_url))


def test_upsert_and_recent(store: SqlTaskProgressStore) -> None:
    store.upsert(
        TaskProgress("task-a", "running", "1/3", pages_total=3, pages_done=1)
    )
    store.upsert(
        TaskProgress("task-b", "finished", "2/2", pages_total=2, pages_done=2)
    )
    store.upsert(
        TaskProgress(
            "task-a",
            "finished",
            "3/3",
            pages_total=3,
            pages_done=3,
            detail={"gap_pages": 1},
        )
    )

    recent = store.recent()

    assert [item.task_id for item in recent] == ["task-a", "task-b"]
    assert recent[0].state == "finished"
    assert recent[0].pages_done == 3
    assert recent[0].detail["gap_pages"] == 1
    assert recent[0].updated_at
