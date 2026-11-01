"""资源预检测试（T039，A6）。

建议值公式 `min(核数×1.25, 可用内存/单进程上限, 32)`（禁写死 cpu_count）；
用户覆盖超限告警并降级；OOM 降批链 32→16→8，最低仍失败则停止并告警。
"""

import os

import pytest

from dataagent.platform.runtime.resources import (
    MAX_BATCH_PAGES,
    OOM_BATCH_LADDER,
    MachineProfile,
    OomBatchDegrader,
    OomExhaustedError,
    recommended_batch_pages,
    recommended_workers,
    resolve_resources,
)


def test_formula_cpu_bound() -> None:
    """CPU 主导：8 核 × 1.25 = 10。"""
    profile = MachineProfile(cpu_cores=8, available_memory_mb=16000)
    assert recommended_batch_pages(profile) == 10
    assert recommended_workers(profile) == 10


def test_formula_memory_bound() -> None:
    """内存主导：400MB / 50MB = 8。"""
    profile = MachineProfile(cpu_cores=16, available_memory_mb=400)
    assert recommended_batch_pages(profile) == 8


def test_formula_capped_at_32() -> None:
    """上限 32：32 核 × 1.25 = 40 → 32。"""
    profile = MachineProfile(cpu_cores=32, available_memory_mb=64000)
    assert recommended_batch_pages(profile) == MAX_BATCH_PAGES == 32


def test_formula_uses_profile_not_hardcoded_cpu_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """禁写死 cpu_count()：即使宿主核数不同，建议值只由传入 profile 决定。"""
    monkeypatch.setattr(os, "cpu_count", lambda: 128)
    profile = MachineProfile(cpu_cores=2, available_memory_mb=1000)
    assert recommended_workers(profile) == 2


def test_override_above_capacity_warns_and_degrades() -> None:
    """用户覆盖超限：告警并降到可行值（不允许改完偷偷 OOM）。"""
    profile = MachineProfile(cpu_cores=8, available_memory_mb=16000)  # 建议 10
    setting = resolve_resources(profile, batch_pages=32, workers=20)

    assert setting.batch_pages == 10
    assert setting.workers == 10
    assert len(setting.warnings) == 2


def test_override_within_capacity_accepted_silently() -> None:
    profile = MachineProfile(cpu_cores=8, available_memory_mb=16000)
    setting = resolve_resources(profile, batch_pages=5, workers=4)

    assert (setting.batch_pages, setting.workers) == (5, 4)
    assert setting.warnings == ()


def test_auto_uses_recommendation() -> None:
    profile = MachineProfile(cpu_cores=4, available_memory_mb=800)
    setting = resolve_resources(profile)

    assert setting.batch_pages == 5  # min(5, 16, 32)
    assert setting.workers == 5
    assert setting.warnings == ()


def test_override_must_be_positive() -> None:
    profile = MachineProfile(cpu_cores=8, available_memory_mb=16000)
    with pytest.raises(ValueError):
        resolve_resources(profile, batch_pages=0)
    with pytest.raises(ValueError):
        resolve_resources(profile, workers=0)


def test_oom_ladder_32_16_8_then_stop_and_alert() -> None:
    """OOM 降批链 32→16→8；最低仍失败则停止并告警。"""
    degrader = OomBatchDegrader(32)

    assert degrader.on_oom() == 16
    assert degrader.on_oom() == 8
    with pytest.raises(OomExhaustedError):
        degrader.on_oom()

    assert degrader.exhausted is True
    assert degrader.batch_pages == 8
    assert OOM_BATCH_LADDER == (32, 16, 8)


def test_oom_ladder_from_lower_initial_batch() -> None:
    degrader = OomBatchDegrader(16)
    assert degrader.on_oom() == 8
    with pytest.raises(OomExhaustedError):
        degrader.on_oom()
