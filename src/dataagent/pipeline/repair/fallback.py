"""外部增强器降级路径（B6/T102 / FR-013 / PRD 4.5.11）。

- `fallback.enhancer` 是配置项（local_ocr | mineru | none），不得写死；
- 增强器不可用时按 `on_unavailable` 降级，默认 `mark_only`（仅标记不处理）；
- 报告必须显式声明所用降级路径；`mark_only` 结果标注"不可用于自动化下游"。
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum, unique


@unique
class EnhancerKind(StrEnum):
    LOCAL_OCR = "local_ocr"
    MINERU = "mineru"
    NONE = "none"


@unique
class FallbackPath(StrEnum):
    LOCAL_OCR = "local_ocr"
    MINERU = "mineru"
    MARK_ONLY = "mark_only"
    FAIL_DOC = "fail_doc"


@dataclass(frozen=True, slots=True)
class FallbackConfig:
    enhancer: EnhancerKind = EnhancerKind.LOCAL_OCR
    on_unavailable: FallbackPath = FallbackPath.MARK_ONLY

    def __post_init__(self) -> None:
        if self.on_unavailable not in (FallbackPath.MARK_ONLY, FallbackPath.FAIL_DOC):
            raise ValueError(
                "on_unavailable 只能是 mark_only 或 fail_doc"
                f"（收到 {self.on_unavailable.value}）"
            )


@dataclass(frozen=True, slots=True)
class FallbackDecision:
    path: FallbackPath
    reason: str
    auto_downstream_allowed: bool

    def report_fields(self) -> dict[str, str]:
        return {
            "fallback_path": self.path.value,
            "fallback_reason": self.reason,
            "auto_downstream": "true" if self.auto_downstream_allowed else "false",
            "fallback_note": _NOTE.get(self.path, ""),
        }


_NOTE: dict[FallbackPath, str] = {
    FallbackPath.MARK_ONLY: "仅标记不处理；结果不可用于自动化下游",
    FallbackPath.FAIL_DOC: "整份不出（按客户要求）",
}


def resolve_fallback(
    config: FallbackConfig,
    *,
    available_enhancers: Collection[EnhancerKind],
) -> FallbackDecision:
    if config.enhancer is not EnhancerKind.NONE and config.enhancer in available_enhancers:
        return FallbackDecision(
            path=FallbackPath(config.enhancer.value),
            reason=f"enhancer_available:{config.enhancer.value}",
            auto_downstream_allowed=True,
        )
    if config.enhancer is EnhancerKind.NONE:
        reason = "enhancer_disabled"
    else:
        reason = f"enhancer_unavailable:{config.enhancer.value}"
    return FallbackDecision(
        path=config.on_unavailable, reason=reason, auto_downstream_allowed=False
    )
