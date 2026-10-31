"""Redis 队列异常分支单元测试（T028 补充）。

用注入的假 redis client 覆盖不易在真实 Redis 上触发的分支
（消费组创建的非 BUSYGROUP 错误原样抛出、消费组不存在时 pending_count 为 0）。
"""

import pytest
import redis.exceptions

from dataagent.platform.store.queue.redis import RedisQueue


class FakeRedis:
    """可注入逐方法错误的假 redis client。"""

    def __init__(
        self,
        xgroup_error: redis.exceptions.ResponseError | None = None,
        xpending_error: redis.exceptions.ResponseError | None = None,
    ) -> None:
        self._xgroup_error = xgroup_error
        self._xpending_error = xpending_error

    def xgroup_create(self, *args, **kwargs) -> None:
        if self._xgroup_error is not None:
            raise self._xgroup_error

    def xreadgroup(self, *args, **kwargs) -> None:
        return None

    def xadd(self, *args, **kwargs) -> str:
        return "1-0"

    def xack(self, *args, **kwargs) -> None:
        return None

    def xdel(self, *args, **kwargs) -> None:
        return None

    def xrange(self, *args, **kwargs) -> list:
        return []

    def xlen(self, *args, **kwargs) -> int:
        return 0

    def xpending(self, *args, **kwargs) -> dict:
        if self._xpending_error is not None:
            raise self._xpending_error
        return {"pending": 0, "min": None, "max": None, "consumers": []}


def test_ensure_group_re_raises_non_busygroup() -> None:
    """消费组创建的非 BUSYGROUP 错误原样抛出（不吞掉真正的故障）。"""
    queue = RedisQueue(
        FakeRedis(xgroup_error=redis.exceptions.ResponseError("PERMISSION DENIED")),
        stream="s",
    )
    with pytest.raises(redis.exceptions.ResponseError):
        queue.receive()


def test_pending_count_zero_when_group_missing() -> None:
    """消费组不存在（NOGROUP）时 pending_count 返回 0，不抛错。"""
    queue = RedisQueue(
        FakeRedis(xpending_error=redis.exceptions.ResponseError("NOGROUP No such consumer group")),
        stream="s",
    )
    assert queue.pending_count() == 0


def test_pending_count_empty_summary_returns_zero() -> None:
    """xpending 返回空摘要时 pending_count 返回 0（防御分支）。"""

    class EmptySummaryRedis(FakeRedis):
        def xpending(self, *args, **kwargs) -> None:
            return None

    queue = RedisQueue(EmptySummaryRedis(), stream="s")
    assert queue.pending_count() == 0
