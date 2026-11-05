"""任务级配置：一次执行的输入参数（非 AI 模型配置）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from dataagent.kernel.plan import FrozenPlan
from dataagent.pipeline.quality.l2_sample import ProfileSpeed
from dataagent.pipeline.repair.fallback import EnhancerKind, FallbackConfig

DEFAULT_BATCH_PAGES: Final[int] = 32
# P0 字符密度口径：A4 版心近似面积（cm²）；P1 由真实 bbox 替换
DEFAULT_TEXT_AREA_CM2: Final[float] = 468.0
DEFAULT_FALLBACK: Final[FallbackConfig] = FallbackConfig()


@dataclass(frozen=True, slots=True)
class TaskConfig:
    output_formats: tuple[str, ...] = ("json", "markdown")
    emit_tables_json: bool = False
    batch_pages: int = DEFAULT_BATCH_PAGES
    text_area_cm2: float = DEFAULT_TEXT_AREA_CM2
    gap_tolerance: float = 0.05
    fallback: FallbackConfig = DEFAULT_FALLBACK
    available_enhancers: frozenset[EnhancerKind] = frozenset()
    profile_speed: ProfileSpeed = ProfileSpeed.BALANCED
    plan: FrozenPlan | None = None
