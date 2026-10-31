"""集成测试夹具（T006）：testcontainers 起 PostgreSQL / Redis / MinIO。

仅在有 Docker 的环境使用；相关测试用 @pytest.mark.integration 标记。
Phase 1 无集成用例，这些夹具在 A4 存储抽象阶段起用。
"""

from collections.abc import Iterator

import pytest
from testcontainers.community.minio import MinioContainer
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    with RedisContainer("redis:7-alpine") as redis:
        yield redis


@pytest.fixture(scope="session")
def minio_container() -> Iterator[MinioContainer]:
    with MinioContainer("minio/minio:latest") as minio:
        yield minio
