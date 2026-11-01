"""接入器：folder watch + upload（FR-001），内容哈希去重，体检先于任何解析。

- `doc_id` = 文件内容 SHA-256（主键铁律），同内容重复提交直接复用已有结果。
- 体检（FR-002）在任何下游处理之前执行；rejected 入待确认队列，其余文件继续。
- 任何文件不得静默丢弃（SC-005）：rejected 记录同时落 documents + pending_documents。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import Any, Protocol

from dataagent.pipeline.ingest.health import (
    FileHealthChecker,
    HealthReport,
    HealthStatus,
)
from dataagent.pipeline.ingest.pdf_probe import PdfStructureProbe


@unique
class IngestOutcome(StrEnum):
    ACCEPTED = "accepted"
    REPAIRABLE = "repairable"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    doc_id: str
    source: str
    path: str
    size_bytes: int
    health: HealthStatus
    family: str | None = None


@dataclass(frozen=True, slots=True)
class PendingRecord:
    doc_id: str
    source: str
    path: str
    reason: str
    diagnostics: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class IngestResult:
    outcome: IngestOutcome
    document: DocumentRecord
    health: HealthReport | None = None


class DocumentStore(Protocol):
    def find(self, doc_id: str) -> DocumentRecord | None: ...

    def save(self, record: DocumentRecord) -> None: ...


class PendingQueue(Protocol):
    def enqueue(self, record: PendingRecord) -> None: ...

    def all(self) -> list[PendingRecord]: ...


class InMemoryDocumentStore:
    def __init__(self) -> None:
        self.records: dict[str, DocumentRecord] = {}

    def find(self, doc_id: str) -> DocumentRecord | None:
        return self.records.get(doc_id)

    def save(self, record: DocumentRecord) -> None:
        self.records[record.doc_id] = record


class InMemoryPendingQueue:
    def __init__(self) -> None:
        self.records: list[PendingRecord] = []

    def enqueue(self, record: PendingRecord) -> None:
        self.records.append(record)

    def all(self) -> list[PendingRecord]:
        return list(self.records)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


class Ingestor:
    """folder 扫描 / upload 写入统一走 ingest_path；重复内容不重复体检、不重复计费。"""

    def __init__(
        self,
        *,
        documents: DocumentStore,
        pending: PendingQueue,
        checker: FileHealthChecker | None = None,
        probe: PdfStructureProbe | None = None,
        on_accepted: Callable[[DocumentRecord], None] | None = None,
    ) -> None:
        self._documents = documents
        self._pending = pending
        self._checker = checker if checker is not None else FileHealthChecker()
        self._probe = probe if probe is not None else PdfStructureProbe()
        self._on_accepted = on_accepted

    def ingest_path(self, path: Path | str, *, source: str = "folder") -> IngestResult:
        file_path = Path(path)
        doc_id = sha256_file(file_path)
        existing = self._documents.find(doc_id)
        if existing is not None:
            return IngestResult(IngestOutcome.DUPLICATE, existing)

        facts = self._probe.probe(file_path)
        report = self._checker.check(facts)
        record = DocumentRecord(
            doc_id=doc_id,
            source=source,
            path=str(file_path),
            size_bytes=facts.size_bytes,
            health=report.status,
        )
        self._documents.save(record)

        if report.status is HealthStatus.REJECTED:
            self._pending.enqueue(
                PendingRecord(
                    doc_id=doc_id,
                    source=source,
                    path=str(file_path),
                    reason="health_rejected",
                    diagnostics=report.as_dict(),
                )
            )
            return IngestResult(IngestOutcome.REJECTED, record, report)

        if self._on_accepted is not None:
            self._on_accepted(record)
        outcome = (
            IngestOutcome.REPAIRABLE
            if report.status is HealthStatus.REPAIRABLE
            else IngestOutcome.ACCEPTED
        )
        return IngestResult(outcome, record, report)

    def ingest_folder(self, root: Path | str) -> list[IngestResult]:
        folder = Path(root)
        if not folder.is_dir():
            raise ValueError(f"接入目录不存在：{folder}")
        return [
            self.ingest_path(path)
            for path in sorted(folder.rglob("*"))
            if path.is_file()
        ]

    def ingest_upload(
        self, filename: str, data: bytes, upload_dir: Path | str
    ) -> IngestResult:
        if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
            raise ValueError(f"非法上传文件名：{filename!r}")
        target_dir = Path(upload_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / filename
        path.write_bytes(data)
        return self.ingest_path(path, source="upload")
