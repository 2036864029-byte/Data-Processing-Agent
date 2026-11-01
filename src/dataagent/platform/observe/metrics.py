"""指标采集：口径定义 + 采集器 + sink（FR-025 / PRD 4.9.2）。

八项核心指标（逐项口径见 METRIC_SPECS）：
处理量 / STP 率 / 误放率 / 平均单页成本 / 档位分布 / T1 命中率 / p50·p95 时延 / 复杂页占比。
指标不依赖外部服务；sink 抽象负责落库或暴露，P0 提供 JSON 日志 sink。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Any, Final, Protocol

from dataagent.contracts.policies import Tier
from dataagent.platform.observe.log import log_event


@unique
class MetricName(StrEnum):
    PROCESSED_PAGES = "processed_pages"
    PROCESSED_DOCS = "processed_docs"
    STP_RATE = "stp_rate"
    MISDROP_RATE = "misdrop_rate"
    COST_PER_PAGE = "cost_per_page"
    TIER_DISTRIBUTION = "tier_distribution"
    POSTPROCESS_HIT_RATE = "postprocess_hit_rate"
    LATENCY_P50_MS = "latency_p50_ms"
    LATENCY_P95_MS = "latency_p95_ms"
    COMPLEX_PAGE_RATIO = "complex_page_ratio"


@dataclass(frozen=True, slots=True)
class MetricSpec:
    name: MetricName
    unit: str
    definition: str


METRIC_SPECS: Final[dict[MetricName, MetricSpec]] = {
    MetricName.PROCESSED_PAGES: MetricSpec(
        MetricName.PROCESSED_PAGES, "页", "已处理页数（累计；按日聚合得到页/日）"
    ),
    MetricName.PROCESSED_DOCS: MetricSpec(
        MetricName.PROCESSED_DOCS, "文档", "已处理文档数（累计；按日聚合得到文档/日）"
    ),
    MetricName.STP_RATE: MetricSpec(
        MetricName.STP_RATE, "比例", "一次通过文档数 / 总文档数（无人工介入）"
    ),
    MetricName.MISDROP_RATE: MetricSpec(
        MetricName.MISDROP_RATE,
        "比例",
        "人工复核判不合格但系统判 pass 的样本数 / 人工判不合格样本数",
    ),
    MetricName.COST_PER_PAGE: MetricSpec(
        MetricName.COST_PER_PAGE, "元/页", "总成本 / 总页数"
    ),
    MetricName.TIER_DISTRIBUTION: MetricSpec(
        MetricName.TIER_DISTRIBUTION,
        "比例",
        "T0/T1/T2/T3 各档页数 / 总页数（T3 占比是成本预警核心指标）",
    ),
    MetricName.POSTPROCESS_HIT_RATE: MetricSpec(
        MetricName.POSTPROCESS_HIT_RATE,
        "比例",
        "走 T1 且修复成功的页数 / 走 T1 的页数",
    ),
    MetricName.LATENCY_P50_MS: MetricSpec(
        MetricName.LATENCY_P50_MS, "ms", "端到端时延 p50（页级样本最近秩法）"
    ),
    MetricName.LATENCY_P95_MS: MetricSpec(
        MetricName.LATENCY_P95_MS, "ms", "端到端时延 p95（页级样本最近秩法）"
    ),
    MetricName.COMPLEX_PAGE_RATIO: MetricSpec(
        MetricName.COMPLEX_PAGE_RATIO, "比例", "is_complex 页 / 总页数"
    ),
}


def percentile(values: Sequence[float], p: float) -> float:
    """最近秩法百分位（确定性；空样本返回 0.0）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100.0 * len(ordered)))
    return float(ordered[min(rank, len(ordered)) - 1])


class MetricsSink(Protocol):
    def emit(self, task_id: str, snapshot: Mapping[str, Any]) -> None: ...


class LoggingMetricsSink:
    """P0 sink：把指标快照写入结构化日志（落库 sink 在 B 阶段接入）。"""

    def emit(self, task_id: str, snapshot: Mapping[str, Any]) -> None:
        log_event("metrics_snapshot", task_id=task_id, **snapshot)


@dataclass
class MetricsCollector:
    """任务级指标累加器：只保存计数与样本，快照时统一按口径计算比率。"""

    processed_docs: int = 0
    stp_docs: int = 0
    processed_pages: int = 0
    complex_pages: int = 0
    total_cost: float = 0.0
    tier_counts: dict[Tier, int] = field(
        default_factory=lambda: dict.fromkeys(Tier, 0)
    )
    t1_pages: int = 0
    t1_success_pages: int = 0
    reviewed_fail_samples: int = 0
    misdropped_samples: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def record_document(self, *, stp: bool) -> None:
        """STP 以文档为单位：一次通过（无人工介入）计为 stp=True。"""
        self.processed_docs += 1
        if stp:
            self.stp_docs += 1

    def record_page(
        self,
        *,
        tier: Tier,
        cost: float,
        latency_ms: float,
        is_complex: bool = False,
    ) -> None:
        self.processed_pages += 1
        self.total_cost += cost
        self.latencies_ms.append(latency_ms)
        self.tier_counts[tier] += 1
        if is_complex:
            self.complex_pages += 1

    def record_postprocess(self, *, hit: bool) -> None:
        """走 T1 的页：hit 表示修复成功（采纳）。"""
        self.t1_pages += 1
        if hit:
            self.t1_success_pages += 1

    def record_review(self, *, system_pass: bool, human_pass: bool) -> None:
        """人工复核样本：误放率分母只含人工判不合格的样本。"""
        if human_pass:
            return
        self.reviewed_fail_samples += 1
        if system_pass:
            self.misdropped_samples += 1

    def snapshot(self) -> dict[str, Any]:
        pages = self.processed_pages
        return {
            "processed_pages": pages,
            "processed_docs": self.processed_docs,
            "stp_rate": self._ratio(self.stp_docs, self.processed_docs),
            "misdrop_rate": self._ratio(
                self.misdropped_samples, self.reviewed_fail_samples
            ),
            "cost_per_page": self.total_cost / pages if pages else 0.0,
            "tier_distribution": {
                tier.value: self._ratio(self.tier_counts[tier], pages)
                for tier in Tier
            },
            "postprocess_hit_rate": self._ratio(self.t1_success_pages, self.t1_pages),
            "latency_p50_ms": percentile(self.latencies_ms, 50),
            "latency_p95_ms": percentile(self.latencies_ms, 95),
            "complex_page_ratio": self._ratio(self.complex_pages, pages),
        }

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 0.0
