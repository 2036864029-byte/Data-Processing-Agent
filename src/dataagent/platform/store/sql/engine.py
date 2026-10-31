"""SQL 标准子集：SQLAlchemy Core（禁 ORM）引擎 / 连接工厂。

P0 不做运行时切换（G10 单一配置）：单进程一份引擎，由 config.postgres_url 配置一次。
所有 DDL 走 Alembic 迁移（G9）；业务经 SQLAlchemy Core 的 text() / Core 表定义访问，
禁止 ORM（declarative mapper）。
"""

from __future__ import annotations

from sqlalchemy import Connection, Engine, create_engine


def create_db_engine(url: str, pool_pre_ping: bool = True) -> Engine:
    """创建 SQLAlchemy Core 引擎（pool_pre_ping 保证连接健康，长任务可续跑）。"""
    return create_engine(url, pool_pre_ping=pool_pre_ping)


class Database:
    """单进程数据库连接工厂：引擎 + connect() 上下文入口。"""

    def __init__(self, url: str) -> None:
        self._engine = create_db_engine(url, pool_pre_ping=True)

    @property
    def engine(self) -> Engine:
        return self._engine

    def connect(self) -> Connection:
        return self._engine.connect()
