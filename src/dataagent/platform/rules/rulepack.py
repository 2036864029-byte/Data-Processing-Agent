"""RulePack：声明式规则包（B8/T117 / FR-014 / PRD 4.6.2）。

YAML 纯数据；schema 校验（pydantic，未知字段拒绝）；版本化发布 / 冲突阻断 / 回滚 /
dry-run。每条规则必须携带原始需求文本（可追溯）。NL→RulePack 编译链路属 P1。

字段提取的声明式锚点层见 `extract.py`；表达式求值见 `expr.py`（同一受限引擎）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dataagent.platform.rules.expr import (
    ExpressionError,
    FieldWhitelist,
    RestrictedExpressionEngine,
)

DOCUMENT_FIELDS: frozenset[str] = frozenset(
    {
        "page_count",
        "extract_quality",
        "char_density",
        "glyph_coverage",
        "gap_ratio",
        "stp_rate",
    }
)
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@unique
class FieldType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    MONEY = "money"
    DATE = "date"


@unique
class CheckType(StrEnum):
    EXPRESSION = "expression"
    SEQUENCE = "sequence"


class RuleField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    type: FieldType
    anchors: list[str] = Field(min_length=1)
    required: bool = False
    pattern: str | None = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError(f"字段 id 必须形如 snake_case：{value!r}")
        return value

    @field_validator("pattern")
    @classmethod
    def _validate_pattern(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"正则不合法：{value!r}（{exc}）") from exc
        return value


class RuleCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: CheckType
    expr: dict[str, Any] | None = None
    tolerance: float | None = Field(default=None, ge=0.0, le=1.0)
    requirement: str = Field(min_length=1, description="原始需求文本（可追溯）")

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError(f"规则 id 必须形如 snake_case：{value!r}")
        return value

    @model_validator(mode="after")
    def _expr_matches_type(self) -> RuleCheck:
        if self.type is CheckType.EXPRESSION and self.expr is None:
            raise ValueError("expression 检查必须提供 expr")
        if self.type is CheckType.SEQUENCE and self.expr is not None:
            raise ValueError("sequence 检查不提供 expr（P0 仅记录）")
        return self


class RulePack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rulepack: str
    version: str = "1.0.0"
    extends: str | None = None
    fields: list[RuleField] = Field(default_factory=list)
    checks: list[RuleCheck] = Field(default_factory=list)
    thresholds: dict[str, float] = Field(default_factory=dict)

    @field_validator("rulepack")
    @classmethod
    def _validate_rulepack(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError(f"规则包 id 必须形如 snake_case：{value!r}")
        return value

    @model_validator(mode="after")
    def _unique_ids(self) -> RulePack:
        field_ids = [field.id for field in self.fields]
        if len(field_ids) != len(set(field_ids)):
            raise ValueError("字段 id 不得重复")
        check_ids = [check.id for check in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("规则 id 不得重复")
        return self

    @model_validator(mode="after")
    def _validate_expressions(self) -> RulePack:
        engine = RestrictedExpressionEngine(whitelist=_whitelist_for(self))
        for check in self.checks:
            if check.expr is not None:
                try:
                    engine.validate(check.expr)
                except ExpressionError as exc:
                    raise ValueError(f"规则 {check.id} 表达式不合法：{exc}") from exc
        return self


class RulePackConflictError(Exception):
    """冲突发布被阻断（版本不递增等）。"""


class RulePackRegistry:
    """版本化注册表：发布冻结、禁回退覆盖、任意历史版本回滚。"""

    def __init__(self) -> None:
        self._history: dict[str, list[RulePack]] = {}
        self._active: dict[str, RulePack] = {}

    def publish(self, pack: RulePack) -> RulePack:
        history = self._history.get(pack.rulepack, [])
        active = self._active.get(pack.rulepack)
        if active is not None and _version_tuple(pack.version) <= _version_tuple(
            active.version
        ):
            raise RulePackConflictError(
                f"{pack.rulepack} 已激活版本 {active.version}，"
                f"新版本必须递增（收到 {pack.version}）"
            )
        history.append(pack)
        self._history[pack.rulepack] = history
        self._active[pack.rulepack] = pack
        return pack

    def active(self, rulepack_id: str) -> RulePack:
        try:
            return self._active[rulepack_id]
        except KeyError as exc:
            raise KeyError(f"规则包未发布：{rulepack_id}") from exc

    def versions(self, rulepack_id: str) -> list[str]:
        return [pack.version for pack in self._history.get(rulepack_id, [])]

    def rollback(self, rulepack_id: str, version: str) -> RulePack:
        for pack in self._history.get(rulepack_id, []):
            if pack.version == version:
                self._active[rulepack_id] = pack
                return pack
        raise KeyError(f"规则包 {rulepack_id} 无版本 {version}")


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_id: str
    requirement: str
    passed: bool | None
    value: Any = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class SampleRun:
    index: int
    results: tuple[CheckResult, ...]


@dataclass(frozen=True, slots=True)
class DryRunReport:
    rulepack_id: str
    version: str
    samples: tuple[SampleRun, ...]

    @property
    def results(self) -> tuple[CheckResult, ...]:
        return tuple(result for sample in self.samples for result in sample.results)

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed is True)

    @property
    def failed(self) -> int:
        return sum(1 for result in self.results if result.passed is False)

    @property
    def skipped(self) -> int:
        return sum(1 for result in self.results if result.passed is None)


def load_rulepack(path: str | Path) -> RulePack:
    rulepack_path = Path(path)
    document = yaml.safe_load(rulepack_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"规则包必须是映射：{rulepack_path}")
    return RulePack.model_validate(document)


def dry_run(
    pack: RulePack, samples: Sequence[dict[str, Any]]
) -> DryRunReport:
    engine = RestrictedExpressionEngine(whitelist=_whitelist_for(pack))
    runs: list[SampleRun] = []
    for index, sample in enumerate(samples):
        results: list[CheckResult] = []
        for check in pack.checks:
            if check.type is CheckType.SEQUENCE or check.expr is None:
                results.append(
                    CheckResult(
                        check.id,
                        check.requirement,
                        None,
                        note="sequence 检查在 P0 dry-run 中仅记录",
                    )
                )
                continue
            value = engine.evaluate(check.expr, sample)
            results.append(
                CheckResult(
                    check.id,
                    check.requirement,
                    _passed(check, value),
                    value=value,
                )
            )
        runs.append(SampleRun(index=index, results=tuple(results)))
    return DryRunReport(
        rulepack_id=pack.rulepack, version=pack.version, samples=tuple(runs)
    )


def _whitelist_for(pack: RulePack) -> FieldWhitelist:
    allowed = frozenset(
        {field.id for field in pack.fields}
        | set(pack.thresholds)
        | DOCUMENT_FIELDS
    )
    return FieldWhitelist(allowed_groups=None, allowed_paths=allowed)


def _passed(check: RuleCheck, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if check.tolerance is not None:
            return abs(value) <= check.tolerance
        return value != 0
    return bool(value)


def _version_tuple(text: str) -> tuple[int, ...]:
    core = re.split(r"[-+]", text, maxsplit=1)[0]
    parts: list[int] = []
    for chunk in core.split("."):
        digits = re.match(r"\d+", chunk)
        if digits is None:
            break
        parts.append(int(digits.group()))
    return tuple(parts) or (0,)


__all__ = [
    "CheckResult",
    "CheckType",
    "DryRunReport",
    "FieldType",
    "RuleCheck",
    "RuleField",
    "RulePack",
    "RulePackConflictError",
    "RulePackRegistry",
    "SampleRun",
    "dry_run",
    "load_rulepack",
]
