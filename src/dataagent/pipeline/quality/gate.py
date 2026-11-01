"""质检结果先落库再触发下游（B5 / G5 / role-messages 硬性约束 #2）。

`QualityGate` 只做顺序保证：计算 → 落库 → 下游回调。
下游崩溃不丢已判结果，重启后可从库判断该页是否判过。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from dataagent.contracts.messages import PageReport
from dataagent.pipeline.quality.l1_check import L1Checker, L1PageInput

Downstream = Callable[[PageReport], None]


class QualityResultStore(Protocol):
    def save(self, report: PageReport) -> None: ...

    def load(self, doc_id: str, page_no: int) -> PageReport | None: ...


class InMemoryQualityResultStore:
    def __init__(self) -> None:
        self._reports: dict[tuple[str, int], PageReport] = {}

    def save(self, report: PageReport) -> None:
        self._reports[(report.doc_id, report.page_no)] = report

    def load(self, doc_id: str, page_no: int) -> PageReport | None:
        return self._reports.get((doc_id, page_no))


class QualityGate:
    def __init__(self, *, checker: L1Checker, store: QualityResultStore) -> None:
        self._checker = checker
        self._store = store

    def run_page(
        self, page: L1PageInput, *, on_saved: Downstream | None = None
    ) -> PageReport:
        report = self._checker.check(page)
        self._store.save(report)
        if on_saved is not None:
            on_saved(report)
        return report
