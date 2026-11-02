"""status 数据源集成测试：Redis 队列深度与 worker 存活。"""

from __future__ import annotations

import pytest
import redis

from dataagent.platform.runtime.liveness import RedisWorkerRegistry
from dataagent.platform.store.queue.redis import RedisQueue

pytestmark = pytest.mark.integration


@pytest.fixture
def redis_client(redis_container):
    client = redis.Redis(
        host=redis_container.get_container_host_ip(),
        port=redis_container.get_exposed_port(6379),
    )
    yield client
    client.flushdb()


def test_queue_depth_counts_unacked_messages(redis_client: redis.Redis) -> None:
    queue = RedisQueue(
        redis_client, stream="status:test", group="workers", consumer="w1"
    )
    queue.enqueue(b"first")

    assert queue.depth() == 0  # 消费组尚未创建
    messages = queue.receive()
    assert queue.depth() == 1  # 已投递未确认
    queue.ack(messages[0].id)
    assert queue.depth() == 0


def test_worker_registry_reports_alive(redis_client: redis.Redis) -> None:
    registry = RedisWorkerRegistry(redis_client)

    registry.beat("worker-a", task_id="task-1")

    assert registry.alive() == 1
