"""文件级体检契约与三态判定（FR-002 / PRD 4.1.8）。

11 项检查全部是确定性规则，**必须在任何解析插件被调用之前完成**。
三态：ok / repairable / rejected；页树深度超限硬拒；
单页尺寸、对象总数、资源嵌套深度只标记可疑（高风险页），不拒收。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Any, Final, Protocol


@unique
class HealthStatus(StrEnum):
    OK = "ok"
    REPAIRABLE = "repairable"
    REJECTED = "rejected"


@unique
class HealthCheck(StrEnum):
    EMPTY_OR_OVERSIZE = "empty_or_oversize"
    MAGIC = "magic"
    HEADER_TRAILER = "header_trailer"
    XREF = "xref"
    PAGE_TREE = "page_tree"
    PAGE_TREE_DEPTH = "page_tree_depth"
    PAGE_SIZE = "page_size"
    OBJECT_COUNT = "object_count"
    RESOURCE_DEPTH = "resource_depth"
    STREAM_RATIO = "stream_ratio"
    ENCRYPTION = "encryption"


@dataclass(frozen=True, slots=True)
class HealthThresholds:
    max_size_bytes: int = 2 * 1024**3
    max_page_tree_depth: int = 50
    max_page_dimension_pt: float = 14400.0
    max_object_count: int = 2_000_000
    max_resource_depth: int = 30
    max_compression_ratio: float = 200.0
    compression_sample_streams: int = 10


DEFAULT_THRESHOLDS: Final[HealthThresholds] = HealthThresholds()


@dataclass(frozen=True, slots=True)
class PdfFacts:
    """受限结构探针结论；None 表示未采样（超大文件跳过深层检查）。"""

    header_ok: bool
    eof_present: bool
    startxref_ok: bool | None
    xref_readable: bool | None
    declared_pages: int | None
    readable_pages: int | None
    page_tree_depth: int | None
    page_dimensions_pt: tuple[float, ...] = ()
    object_count: int | None = None
    resource_depth: int | None = None
    max_compression_ratio: float | None = None
    encrypted: bool = False


@dataclass(frozen=True, slots=True)
class FileFacts:
    size_bytes: int
    extension: str
    magic: bytes
    magic_matches: bool
    pdf: PdfFacts | None = None


@dataclass(frozen=True, slots=True)
class HealthIssue:
    check: HealthCheck
    status: HealthStatus
    detail: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HealthReport:
    status: HealthStatus
    issues: tuple[HealthIssue, ...] = ()
    high_risk_pages: tuple[int, ...] = ()
    high_risk: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "high_risk": self.high_risk,
            "high_risk_pages": list(self.high_risk_pages),
            "issues": [
                {
                    "check": issue.check.value,
                    "status": issue.status.value,
                    "detail": issue.detail,
                    "evidence": dict(issue.evidence),
                }
                for issue in self.issues
            ],
        }


class FileProbe(Protocol):
    def probe(self, path: Any) -> FileFacts: ...


class FileHealthChecker:
    """纯规则判定器：输入探针事实，输出三态报告（无 IO，可重放）。"""

    def __init__(self, thresholds: HealthThresholds = DEFAULT_THRESHOLDS) -> None:
        self._thresholds = thresholds

    def check(self, facts: FileFacts) -> HealthReport:
        thresholds = self._thresholds
        issues: list[HealthIssue] = []
        high_risk_pages: list[int] = []
        high_risk = False

        # 1. 空文件 / 超大文件
        if facts.size_bytes <= 0 or facts.size_bytes > thresholds.max_size_bytes:
            issues.append(
                HealthIssue(
                    HealthCheck.EMPTY_OR_OVERSIZE,
                    HealthStatus.REJECTED,
                    f"文件大小 {facts.size_bytes} 字节不在 (0, {thresholds.max_size_bytes}] 内",
                    {"size_bytes": facts.size_bytes},
                )
            )

        # 2. magic bytes 与后缀声明一致性（不一致交一级判定按内容改判，不拒收）
        if not facts.magic_matches:
            issues.append(
                HealthIssue(
                    HealthCheck.MAGIC,
                    HealthStatus.OK,
                    "魔数与后缀声明不符：交一级格式判定按内容改判家族",
                    {
                        "extension": facts.extension,
                        "magic_hex": facts.magic.hex(),
                    },
                )
            )

        pdf = facts.pdf
        if pdf is not None:
            # 3. 头部 + trailer
            if not pdf.header_ok or not pdf.eof_present:
                issues.append(
                    HealthIssue(
                        HealthCheck.HEADER_TRAILER,
                        HealthStatus.REPAIRABLE,
                        "PDF 头部或 %%EOF 缺失（文件被截断），需重建后重入",
                        {"header_ok": pdf.header_ok, "eof_present": pdf.eof_present},
                    )
                )

            # 4. xref 可解析性
            if pdf.xref_readable is False:
                issues.append(
                    HealthIssue(
                        HealthCheck.XREF,
                        HealthStatus.REPAIRABLE,
                        "xref 不可解析：可重建或顺序扫描后重入",
                    )
                )

            # 5. 页树可遍历：声明页数 = 实际可读页数
            if (
                pdf.declared_pages is not None
                and pdf.readable_pages is not None
                and pdf.declared_pages != pdf.readable_pages
            ):
                issues.append(
                    HealthIssue(
                        HealthCheck.PAGE_TREE,
                        HealthStatus.REPAIRABLE,
                        "声明页数与实际可读页数不一致",
                        {
                            "declared_pages": pdf.declared_pages,
                            "readable_pages": pdf.readable_pages,
                        },
                    )
                )

            # 6. 页树深度（硬拒）
            if (
                pdf.page_tree_depth is not None
                and pdf.page_tree_depth > thresholds.max_page_tree_depth
            ):
                issues.append(
                    HealthIssue(
                        HealthCheck.PAGE_TREE_DEPTH,
                        HealthStatus.REJECTED,
                        "页树深度超限，硬拒（栈溢出风险）",
                        {
                            "depth": pdf.page_tree_depth,
                            "limit": thresholds.max_page_tree_depth,
                        },
                    )
                )

            # 7. 单页尺寸：标记可疑，渲染前降采样（不拒收）
            oversized = [
                index
                for index, dimension in enumerate(pdf.page_dimensions_pt, start=1)
                if dimension > thresholds.max_page_dimension_pt
            ]
            if oversized:
                high_risk_pages.extend(oversized)
                issues.append(
                    HealthIssue(
                        HealthCheck.PAGE_SIZE,
                        HealthStatus.OK,
                        "单页尺寸超限：标记高风险页，渲染前按比例降采样",
                        {
                            "pages": oversized,
                            "limit_pt": thresholds.max_page_dimension_pt,
                        },
                    )
                )

            # 8. 对象总数：标记可疑
            if (
                pdf.object_count is not None
                and pdf.object_count > thresholds.max_object_count
            ):
                high_risk = True
                issues.append(
                    HealthIssue(
                        HealthCheck.OBJECT_COUNT,
                        HealthStatus.OK,
                        "对象总数超限：标记高风险文档",
                        {
                            "object_count": pdf.object_count,
                            "limit": thresholds.max_object_count,
                        },
                    )
                )

            # 9. 资源字典嵌套深度：标记可疑
            if (
                pdf.resource_depth is not None
                and pdf.resource_depth > thresholds.max_resource_depth
            ):
                high_risk = True
                issues.append(
                    HealthIssue(
                        HealthCheck.RESOURCE_DEPTH,
                        HealthStatus.OK,
                        "资源字典嵌套过深：标记高风险文档（递归溢出风险）",
                        {
                            "resource_depth": pdf.resource_depth,
                            "limit": thresholds.max_resource_depth,
                        },
                    )
                )

            # 10. 流解压比：拒收（zip 炸弹）
            if (
                pdf.max_compression_ratio is not None
                and pdf.max_compression_ratio > thresholds.max_compression_ratio
            ):
                issues.append(
                    HealthIssue(
                        HealthCheck.STREAM_RATIO,
                        HealthStatus.REJECTED,
                        "流解压比超限，疑似 zip 炸弹",
                        {
                            "ratio": pdf.max_compression_ratio,
                            "limit": thresholds.max_compression_ratio,
                        },
                    )
                )

            # 11. 加密
            if pdf.encrypted:
                issues.append(
                    HealthIssue(
                        HealthCheck.ENCRYPTION,
                        HealthStatus.REJECTED,
                        "文件已加密且未提供密码，入待确认队列",
                    )
                )

        status = HealthStatus.OK
        if any(issue.status is HealthStatus.REPAIRABLE for issue in issues):
            status = HealthStatus.REPAIRABLE
        if any(issue.status is HealthStatus.REJECTED for issue in issues):
            status = HealthStatus.REJECTED

        return HealthReport(
            status=status,
            issues=tuple(issues),
            high_risk_pages=tuple(high_risk_pages),
            high_risk=high_risk,
        )
