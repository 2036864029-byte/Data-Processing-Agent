"""文档级标题层级归一化（Phase 1）。

标题层级有两类信号：Markdown `#` 个数（声明层级）与中文编号语义。
本模块把它们收敛成一条文档级序列，替代原先散落在渲染层的
`min(level, previous_level + 1)` 内联逻辑。

规则：
1. 首个标题保持声明层级——不强制提升为 1；
2. 同一编号类型在同一文档内锁层（首个出现的层级为准）；
3. 只对跳级下调（`previous + 1`），从不无条件提升；
4. 层级限定在 1..MAX_HEADING_LEVEL。

非目标：不在这里决定“哪一行是标题”（那是建块/标定的职责）。
"""

from __future__ import annotations

from dataagent.contracts.headings import (
    MAX_HEADING_LEVEL,
    HeadingSignal,
    NumberingKind,
    classify_numbering,
)
from dataagent.pipeline.events import log_heading_decision


class HeadingLevelNormalizer:
    """按文档顺序归一化标题层级；实例状态跨页累积。"""

    def __init__(self) -> None:
        self._previous: int | None = None
        self._locked: dict[NumberingKind, int] = {}

    def resolve(
        self,
        text: str,
        declared_level: int | None,
        *,
        page_no: int | None = None,
    ) -> int:
        signal = classify_numbering(text)
        candidate = self._candidate(signal, declared_level)
        level = self._bounded(candidate)
        if self._previous is not None:
            level = min(level, self._previous + 1)
        if page_no is not None and (declared_level is None or level != declared_level):
            source, reason = self._decision(signal, declared_level, candidate, level)
            log_heading_decision(
                page_no=page_no,
                text=text,
                source=source,
                level=level,
                reason=reason,
            )
        if signal is not None:
            self._locked[signal.kind] = level
        self._previous = level
        return level

    def _candidate(
        self, signal: HeadingSignal | None, declared_level: int | None
    ) -> int:
        if signal is not None and signal.kind in self._locked:
            return self._locked[signal.kind]
        if declared_level is not None:
            return declared_level
        if signal is not None:
            return signal.level
        return 1

    def _decision(
        self,
        signal: HeadingSignal | None,
        declared_level: int | None,
        candidate: int,
        level: int,
    ) -> tuple[str, str]:
        """（来源, 原因）：只描述“层级不是原样照抄声明值”的情况。"""
        if level != self._bounded(candidate):
            return "clamped", "jump_clamped"
        if signal is None:
            if declared_level is None:
                return "default", "fallback"
            return "declared", "declared"
        if signal.kind in self._locked:
            return "numbering", "locked"
        if declared_level is None:
            return "numbering", "promoted"
        return "declared", "declared"

    @staticmethod
    def _bounded(level: int) -> int:
        return max(1, min(level, MAX_HEADING_LEVEL))
