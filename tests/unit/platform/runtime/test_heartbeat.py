"""心跳按进展判定测试（T038，A6 / research D7）。

连续 60s 无「进展心跳」判死；按进展而非耗时判定——重复同值心跳不续命，
耗时再长只要进展在推进就存活。
"""

from dataagent.platform.runtime.heartbeat import HeartbeatMonitor


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_alive_within_timeout_and_dead_after() -> None:
    """连续 60s 无心跳判死（阈值处仍存活，超过即死）。"""
    clock = FakeClock()
    monitor = HeartbeatMonitor(clock=clock)
    monitor.beat("task-1", progress=0)

    clock.advance(60)
    assert monitor.is_alive("task-1") is True

    clock.advance(1)
    assert monitor.is_alive("task-1") is False
    assert monitor.dead_tasks() == ("task-1",)


def test_repeated_same_progress_does_not_extend_life() -> None:
    """按进展判定：同值心跳不算进展，不续命。"""
    clock = FakeClock()
    monitor = HeartbeatMonitor(clock=clock)
    monitor.beat("task-1", progress=3)

    clock.advance(30)
    assert monitor.beat("task-1", progress=3) is False
    clock.advance(31)

    assert monitor.is_alive("task-1") is False


def test_advancing_progress_extends_life_regardless_of_elapsed_time() -> None:
    """按进展而非耗时：耗时再长，只要进展持续推进就存活。"""
    clock = FakeClock()
    monitor = HeartbeatMonitor(clock=clock)
    monitor.beat("task-1", progress=0)

    for progress in range(1, 6):
        clock.advance(50)  # 累计 250s，远超 60s
        assert monitor.beat("task-1", progress=progress) is True
        assert monitor.is_alive("task-1") is True


def test_unknown_task_is_not_alive() -> None:
    monitor = HeartbeatMonitor(clock=FakeClock())
    assert monitor.is_alive("ghost") is False
    assert monitor.last_progress("ghost") is None


def test_dead_tasks_lists_only_expired_tasks() -> None:
    clock = FakeClock()
    monitor = HeartbeatMonitor(clock=clock)
    monitor.beat("task-a", progress=0)
    clock.advance(30)
    monitor.beat("task-b", progress=0)
    clock.advance(31)  # task-a 61s 无进展，task-b 31s

    assert monitor.dead_tasks() == ("task-a",)
    assert monitor.is_alive("task-b") is True
