"""reason → defect_code 配置映射（B6/T104 / defect-codes.md 上游原因码接入规则）。

- 映射表放配置、按插件版本分组；上游升级新增 reason 自动落入未知桶。
- 未知 reason 一律落 `plugin_reason_unknown`，`raw_reason` 原样保留，
  不得丢弃、不得凭猜测归入邻近码。
- 单向：`defect_code` 不反推 `reason`（报告两字段并列）。
- `plugin_reason_unknown` 占比进运营指标，>5% 由 A9 AlertEvaluator 告警。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from dataagent.contracts.defects import DEFECT_SPECS, Defect, DefectLocation
from dataagent.contracts.errors import DefectCode

DEFAULT_REASON_MAPPING_PATH: Final[Path] = Path(__file__).with_name(
    "reason_mapping.yaml"
)

ReasonTables = Mapping[str, Mapping[str, Mapping[str, DefectCode]]]


class ReasonMappingError(ValueError):
    """映射配置不合法。"""


class ReasonMapper:
    def __init__(
        self,
        tables: ReasonTables,
        *,
        fallback: DefectCode = DefectCode.PLUGIN_REASON_UNKNOWN,
    ) -> None:
        self._tables = tables
        self._fallback = fallback

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> ReasonMapper:
        tables: dict[str, dict[str, dict[str, DefectCode]]] = {}
        for plugin_id, versions in document.items():
            if not isinstance(versions, Mapping):
                raise ReasonMappingError(f"插件 {plugin_id!r} 的版本分组必须是映射")
            table: dict[str, dict[str, DefectCode]] = {}
            for version, reasons in versions.items():
                if not isinstance(reasons, Mapping):
                    raise ReasonMappingError(
                        f"{plugin_id}@{version} 的 reason 表必须是映射"
                    )
                table[str(version)] = {
                    str(reason): _defect_code(code, plugin_id, version)
                    for reason, code in reasons.items()
                }
            tables[str(plugin_id)] = table
        return cls(tables)

    @classmethod
    def from_yaml(cls, path: str | Path) -> ReasonMapper:
        mapping_path = Path(path)
        try:
            document = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ReasonMappingError(f"无法读取映射配置 {mapping_path}: {exc}") from exc
        if not isinstance(document, Mapping):
            raise ReasonMappingError(f"映射配置必须是映射：{mapping_path}")
        return cls.from_document(document)

    def map(
        self,
        *,
        plugin_id: str,
        plugin_version: str,
        reason: str,
        page_no: int,
    ) -> Defect:
        """单次映射；未知原因原样透传，绝不猜测。"""
        code = (
            self._tables.get(plugin_id, {})
            .get(plugin_version, {})
            .get(reason, self._fallback)
        )
        return Defect(
            code=code,
            location=DefectLocation(page_no=page_no),
            evidence={"plugin_id": plugin_id, "plugin_version": plugin_version},
            severity=0.5,
            suggested_action=DEFECT_SPECS[code].operator,
            raw_reason=reason if code is DefectCode.PLUGIN_REASON_UNKNOWN else None,
        )


@dataclass(frozen=True, slots=True)
class ReasonStats:
    total: int
    unknown: int

    @property
    def ratio(self) -> float:
        return self.unknown / self.total if self.total else 0.0

    def alert_stats(self) -> dict[str, float]:
        return {"plugin_reason_unknown_ratio": self.ratio}

    @classmethod
    def from_defects(cls, defects: Sequence[Defect]) -> ReasonStats:
        return cls(
            total=len(defects),
            unknown=sum(
                1 for defect in defects if defect.code is DefectCode.PLUGIN_REASON_UNKNOWN
            ),
        )


def load_default_reason_mapper() -> ReasonMapper:
    return ReasonMapper.from_yaml(DEFAULT_REASON_MAPPING_PATH)


def _defect_code(code: Any, plugin_id: str, version: str) -> DefectCode:
    try:
        return DefectCode(str(code))
    except ValueError as exc:
        raise ReasonMappingError(
            f"{plugin_id}@{version} 映射到未知缺陷码 {code!r}（缺陷码是封闭集）"
        ) from exc
