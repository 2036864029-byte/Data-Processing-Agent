"""声明式字段提取（B8/T119 / PRD 4.6.6 第①层）。

字段提取三层的第一层（P0 唯一一层）：锚点词 → 定位 → 抽值 → 类型校验。
模板按坐标（②）与模型兜底（③）属后续阶段，不在 P0 范围。
缺失必填字段显式标记（`missing=True`），不得静默。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dataagent.platform.rules.rulepack import FieldType, RuleField, RulePack

_SEPARATORS = re.compile(r"^[\s:：=＝]+")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_DATE = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")


@dataclass(frozen=True, slots=True)
class ExtractedField:
    field_id: str
    value: float | str | None
    raw: str | None = None
    anchor: str | None = None
    missing: bool = False
    source: str = "declarative"


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    fields: tuple[ExtractedField, ...]

    @property
    def missing_required(self) -> tuple[str, ...]:
        return tuple(field.field_id for field in self.fields if field.missing)

    def get(self, field_id: str) -> ExtractedField | None:
        return next(
            (field for field in self.fields if field.field_id == field_id), None
        )


def extract_declarative_fields(pack: RulePack, text: str) -> ExtractionOutcome:
    lines = text.splitlines()
    return ExtractionOutcome(
        fields=tuple(_extract_field(field, lines) for field in pack.fields)
    )


def _extract_field(field: RuleField, lines: list[str]) -> ExtractedField:
    for anchor in field.anchors:
        index = next(
            (position for position, line in enumerate(lines) if anchor in line),
            None,
        )
        if index is None:
            continue
        candidate = _value_after_anchor(lines[index], anchor)
        if not candidate:
            candidate = next(
                (line.strip() for line in lines[index + 1 :] if line.strip()), ""
            )
        if field.pattern is not None:
            match = re.search(field.pattern, candidate)
            if match is None:
                continue
            candidate = match.group(1) if match.groups() else match.group(0)
        value = _coerce(candidate, field.type)
        if value is None:
            continue
        return ExtractedField(
            field_id=field.id, value=value, raw=candidate, anchor=anchor
        )
    return ExtractedField(field_id=field.id, value=None, missing=True)


def _value_after_anchor(line: str, anchor: str) -> str:
    remainder = line.split(anchor, maxsplit=1)[1]
    return _SEPARATORS.sub("", remainder).strip()


def _coerce(candidate: str, field_type: FieldType) -> float | str | None:
    if field_type is FieldType.TEXT:
        return candidate or None
    if field_type is FieldType.DATE:
        match = _DATE.search(candidate)
        return match.group(0) if match is not None else None
    cleaned = candidate.replace(",", "").replace("，", "")
    match = _NUMBER.search(cleaned)
    return float(match.group(0)) if match is not None else None
