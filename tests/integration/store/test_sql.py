"""SQL 标准子集 + Alembic 迁移测试（T024，A4）。

alembic upgrade head / downgrade base 干净；所有 DDL 走迁移（G9）；
SQLAlchemy Core 无 ORM（禁 declarative mapper）。
"""

import inspect
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from dataagent.platform.store.sql.engine import Database, create_db_engine

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]


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


def test_alembic_upgrade_head_clean(pg_url) -> None:
    """upgrade head 在空库上干净执行，alembic_version 记录基线版本。"""
    cfg = _alembic_config(pg_url)
    command.upgrade(cfg, "head")
    engine = create_db_engine(pg_url)
    with engine.connect() as conn:
        versions = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    assert versions, "upgrade head 后应有 alembic_version 记录基线"


def test_alembic_downgrade_base_rollback(pg_url) -> None:
    """downgrade base 可回滚：alembic_version 无版本记录（回到无迁移状态）。"""
    cfg = _alembic_config(pg_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    engine = create_db_engine(pg_url)
    with engine.connect() as conn:
        versions = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    assert versions == [], "downgrade base 后不应有迁移版本记录"


def test_engine_core_connect_and_execute(pg_url) -> None:
    """SQLAlchemy Core（无 ORM）：引擎连接 + text SQL 执行。"""
    engine = create_db_engine(pg_url)
    with engine.connect() as conn:
        value = conn.execute(text("SELECT 42")).scalar()
    assert value == 42


def test_database_wrapper(pg_url) -> None:
    """Database 封装：engine 属性 + connect() 连接入口。"""
    db = Database(pg_url)
    assert db.engine is not None
    with db.connect() as conn:
        value = conn.execute(text("SELECT 7")).scalar()
    assert value == 7


def test_sql_store_uses_core_not_orm() -> None:
    """禁 ORM：platform/store/sql 不导入 sqlalchemy.orm / declarative。"""
    import ast

    from dataagent.platform.store.sql import engine as sql_engine

    module = ast.parse(inspect.getsource(sql_engine))
    imported: list[str] = []
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(m.startswith("sqlalchemy.orm") for m in imported)
