"""标题编号契约：中文编号 → 层级，tagged PDF 角色 → 层级。

标题层级规则只在这里定义一次。消费方：
- `pipeline.structure.headings`：文档级层级归一化；
- `pipeline.structure.build`：编号成为标题来源、角色作为兜底信号；
- `pipeline.repair.layout`：Markdown 版面修复的中文编号重建；
- `platform.plugins.adapters.heading_calibration`：注入标题时对齐层级。

契约层叶子模块：无 IO、无第三方依赖，pipeline 与 platform 都可引用
（import-linter 分层：platform 不得依赖 pipeline，故规则必须落在 contracts）。

层级映射（plan-quality-remediation Phase 1）：
`第X章/编/部分` → 1；`第X节` / `一、` → 2；`（一）` → 3；`1.` / `（1）` → 4。
`第X条` 不是标题（仅作段落边界）；`（一）` 与列表编号只提供层级，
不单独建标题（真实语料里它们是条文内的枚举项）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Final

MAX_HEADING_LEVEL: Final[int] = 6
MAX_HEADING_CHARS: Final[int] = 80


@unique
class NumberingKind(StrEnum):
    """中文编号类型（同一类型在同一文档内必须同层）。"""

    CHAPTER = "chapter"
    SECTION = "section"
    TOP_SECTION = "top_section"
    CLAUSE = "clause"
    ENUMERATION = "enumeration"

    @property
    def promotable(self) -> bool:
        """编号本身能否独立成为标题。

        只有结构性编号（章/节/一、）可以。`（一）` 多为条文内的枚举项，
        `1.` 在正文列表里到处都是——二者只提供层级，不单独建标题。
        """
        return self in {
            NumberingKind.CHAPTER,
            NumberingKind.SECTION,
            NumberingKind.TOP_SECTION,
        }


DEFAULT_LEVELS: Final[dict[NumberingKind, int]] = {
    NumberingKind.CHAPTER: 1,
    NumberingKind.SECTION: 2,
    NumberingKind.TOP_SECTION: 2,
    NumberingKind.CLAUSE: 3,
    NumberingKind.ENUMERATION: 4,
}

_CJK_NUMERALS: Final[str] = "一二三四五六七八九十百千零〇"
_CHAPTER_RE: Final[re.Pattern[str]] = re.compile(
    rf"^第[{_CJK_NUMERALS}\d]+(?:章|编|部分)"
)
_SECTION_RE: Final[re.Pattern[str]] = re.compile(
    rf"^第[{_CJK_NUMERALS}\d]+节"
)
_TOP_SECTION_RE: Final[re.Pattern[str]] = re.compile(
    rf"^[{_CJK_NUMERALS}]+、"
)
_CLAUSE_RE: Final[re.Pattern[str]] = re.compile(
    rf"^（[{_CJK_NUMERALS}]+）"
)
_ENUMERATION_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:\d+[.、．](?!\d)|（\d+）)"
)
_STRUCTURE_ROLE_RE: Final[re.Pattern[str]] = re.compile(r"^H([1-6])$")
_LEADING_SPACE_RE: Final[re.Pattern[str]] = re.compile(r"^[\s　]+")

_ORDERED_PATTERNS: Final[tuple[tuple[NumberingKind, re.Pattern[str]], ...]] = (
    (NumberingKind.CHAPTER, _CHAPTER_RE),
    (NumberingKind.SECTION, _SECTION_RE),
    (NumberingKind.CLAUSE, _CLAUSE_RE),
    (NumberingKind.ENUMERATION, _ENUMERATION_RE),
    (NumberingKind.TOP_SECTION, _TOP_SECTION_RE),
)


@dataclass(frozen=True, slots=True)
class HeadingSignal:
    kind: NumberingKind
    level: int


def classify_numbering(text: str) -> HeadingSignal | None:
    """行首中文编号 → 标题信号；无编号或编号后无内容时返回 None。"""
    stripped = _LEADING_SPACE_RE.sub("", text).strip()
    if not stripped:
        return None
    for kind, pattern in _ORDERED_PATTERNS:
        match = pattern.match(stripped)
        if match is None:
            continue
        if not stripped[match.end() :].strip():
            return None
        return HeadingSignal(kind=kind, level=DEFAULT_LEVELS[kind])
    return None


def numbering_level(kind: NumberingKind) -> int:
    return DEFAULT_LEVELS[kind]


def level_from_role(role: str) -> int | None:
    """结构元素角色（H1–H6）→ 层级；其余角色返回 None。"""
    match = _STRUCTURE_ROLE_RE.match(role)
    return int(match.group(1)) if match is not None else None
