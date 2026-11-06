"""交付目标注册表与自然语言路由（PR-4-3）。

交付目标是「已配置的备用输出根」：NAS 等先由 OS 挂载，`path` 指向挂载点，
凭证属于挂载层而非本模块。P0 路由是确定性规则：名称与别名（忽略大小写与
空白）子串匹配，最长匹配优先；未指定、未命中、歧义、不可达都回退默认输出根
并给出原因，绝不静默改写用户的交付位置。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class RouteReason(StrEnum):
    MATCHED = "matched"
    NOT_SPECIFIED = "not_specified"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True, slots=True)
class DeliverTarget:
    name: str
    path: Path
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DeliveryRoute:
    output_dir: Path
    target: DeliverTarget | None
    reason: RouteReason
    requested: str | None = None
    detail: str = ""

    @property
    def routed(self) -> bool:
        return self.reason is RouteReason.MATCHED

    def as_fields(self) -> dict[str, str | None]:
        return {
            "requested": self.requested,
            "target": self.target.name if self.target is not None else None,
            "reason": self.reason.value,
            "output_dir": str(self.output_dir),
            "detail": self.detail or None,
        }


class TargetConfigLike(Protocol):
    """配置 schema 中单个目标的最小视图（pipeline 不依赖配置模块）。"""

    path: Path
    aliases: Sequence[str]


def targets_from_config(
    config: Mapping[str, TargetConfigLike],
) -> tuple[DeliverTarget, ...]:
    return tuple(
        DeliverTarget(
            name=name,
            path=Path(item.path),
            aliases=tuple(item.aliases),
        )
        for name, item in config.items()
    )


class DeliverTargetRouter:
    def __init__(
        self,
        targets: Iterable[DeliverTarget] = (),
        default_dir: Path | str = "var/output",
    ) -> None:
        self._targets = tuple(targets)
        self._default = Path(default_dir)

    @property
    def targets(self) -> tuple[DeliverTarget, ...]:
        return self._targets

    @property
    def default_dir(self) -> Path:
        return self._default

    def route(self, text: str | None) -> DeliveryRoute:
        requested = (text or "").strip()
        if not requested:
            return DeliveryRoute(self._default, None, RouteReason.NOT_SPECIFIED)

        normalized = _normalize(requested)
        matches = [
            (length, target)
            for target in self._targets
            if (length := _match_length(target, normalized)) > 0
        ]
        if not matches:
            return DeliveryRoute(
                self._default,
                None,
                RouteReason.NO_MATCH,
                requested,
                "未匹配到已配置的交付目标",
            )

        best = max(length for length, _ in matches)
        winners = [target for length, target in matches if length == best]
        if len(winners) > 1:
            names = "、".join(sorted(target.name for target in winners))
            return DeliveryRoute(
                self._default,
                None,
                RouteReason.AMBIGUOUS,
                requested,
                f"命中多个交付目标：{names}",
            )

        target = winners[0]
        problem = _unreachable_reason(target.path)
        if problem is not None:
            return DeliveryRoute(
                self._default,
                None,
                RouteReason.UNREACHABLE,
                requested,
                f"目标 {target.name} 不可用：{problem}",
            )
        return DeliveryRoute(target.path, target, RouteReason.MATCHED, requested)


def _normalize(value: str) -> str:
    return "".join(value.casefold().split())


def _match_length(target: DeliverTarget, normalized_text: str) -> int:
    lengths = [
        len(needle)
        for raw in (target.name, *target.aliases)
        if (needle := _normalize(raw)) and needle in normalized_text
    ]
    return max(lengths, default=0)


def _unreachable_reason(path: Path) -> str | None:
    try:
        if not path.exists():
            return f"路径不存在或未挂载（{path}）"
        if not path.is_dir():
            return f"路径不是目录（{path}）"
        if not os.access(path, os.W_OK):
            return f"路径不可写（{path}）"
    except OSError as exc:
        return f"无法访问 {path}：{exc}"
    return None
