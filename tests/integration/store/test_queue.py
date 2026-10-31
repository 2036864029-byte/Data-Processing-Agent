"""队列最小语义测试（T023，A4）。

至少一次 + 手动确认 + 死信；不依赖延迟消息与优先级队列
（应用层用 visible_at 实现，research D6）；testcontainers Redis。
"""

import uuid

import pytest
import redis

from dataagent.platform.store.queue.redis import RedisQueue

pytestmark = pytest.mark.integration


@pytest.fixture
def queue(redis_container) -> RedisQueue:
    client = redis.Redis(
        host=redis_container.get_container_host_ip(),
        port=redis_container.get_exposed_port(6379),
    )
    q = RedisQueue(
        client,
        stream=f"q:{uuid.uuid4().hex}",
        group="workers",
        dead_stream=f"q:{uuid.uuid4().hex}:dead",
    )
    yield q
    client.flushdb()


def test_enqueue_receive_ack(queue) -> None:
    """至少一次：入队 → 接收 → 手动确认后不再待确认。"""
    queue.enqueue(b"msg-1")
    msgs = queue.receive(max_count=10)
    assert len(msgs) == 1
    assert msgs[0].payload == b"msg-1"
    assert queue.pending_count() == 1
    queue.ack(msgs[0].id)
    assert queue.pending_count() == 0


def test_at_least_once_unacked_stays_pending(queue) -> None:
    """至少一次：未确认的消息不丢失，保留在待确认列表（崩溃恢复可接管）。"""
    queue.enqueue(b"critical")
    msgs = queue.receive(max_count=10)
    assert len(msgs) == 1
    # 未 ACK → 仍在待确认（PEL），消息不丢失
    assert queue.pending_count() == 1
    # 同一消费者组不再重复派发同一批消息（XREADGROUP > 只读新消息）
    assert queue.receive(max_count=10) == []


def test_ack_unknown_id_is_noop(queue) -> None:
    """手动确认幂等：重复 ACK 无副作用。"""
    queue.enqueue(b"x")
    msgs = queue.receive(max_count=10)
    queue.ack(msgs[0].id)
    queue.ack(msgs[0].id)
    assert queue.pending_count() == 0


def test_dead_letter_moves_to_dlq(queue) -> None:
    """死信：毒丸消息移入死信流，主队列不再派发。"""
    queue.enqueue(b"poison")
    msgs = queue.receive(max_count=10)
    assert len(msgs) == 1
    queue.dead_letter(msgs[0].id)
    assert queue.pending_count() == 0
    assert queue.receive(max_count=10) == []
    assert queue.dead_letter_count() == 1


def test_fifo_order(queue) -> None:
    """FIFO：XADD 顺序 == XREADGROUP 派发顺序（批级任务靠顺序确定性）。"""
    queue.enqueue(b"first")
    queue.enqueue(b"second")
    msgs = queue.receive(max_count=10)
    assert [m.payload for m in msgs] == [b"first", b"second"]


def test_dead_letter_unknown_id_is_noop(queue) -> None:
    """死信不存在的消息：静默跳过（幂等），主队列与死信流均无副作用。"""
    queue.dead_letter("0-0")
    assert queue.pending_count() == 0
    assert queue.dead_letter_count() == 0


def test_pending_count_before_group_is_zero(queue) -> None:
    """消费组未创建前 pending_count == 0（XPENDING NOGROUP 被吞掉）。"""
    assert queue.pending_count() == 0
