"""接入流程集成测试（T074，B1）。

folder + upload 两种接入；损坏 / 加密文件在解析能力被调用前拦截进待确认队列；
重复文件（同内容哈希）直接复用已有结果，不重复体检、不重复计费。
"""

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from dataagent.pipeline.ingest.ingestor import Ingestor, IngestOutcome
from dataagent.pipeline.ingest.pdf_probe import PdfStructureProbe
from dataagent.pipeline.ingest.sql import SqlDocumentStore, SqlPendingQueue
from dataagent.platform.store.sql.engine import Database

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[4]


def alembic_config(url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def build_pdf(objects: list[bytes], *, trailer_extra: bytes = b"") -> bytes:
    header = b"%PDF-1.4\n"
    body = b""
    offsets: list[int] = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(header) + len(body))
        body += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(header) + len(body)
    xref = (
        f"xref\n0 {len(objects) + 1}\n".encode()
        + b"0000000000 65535 f \n"
        + b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    )
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R".encode()
        + trailer_extra
        + b" >>\n"
    )
    return header + body + xref + trailer + f"startxref\n{xref_offset}\n%%EOF\n".encode()


def valid_pdf() -> bytes:
    return build_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
        ]
    )


def encrypted_pdf() -> bytes:
    return build_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
            b"<< /Filter /Standard >>",
        ],
        trailer_extra=b" /Encrypt 4 0 R",
    )


class CountingProbe:
    def __init__(self) -> None:
        self._inner = PdfStructureProbe()
        self.calls = 0

    def probe(self, path: Path) -> object:
        self.calls += 1
        return self._inner.probe(path)


@pytest.fixture
def pg_url(pg_container) -> str:
    host = pg_container.get_container_host_ip()
    port = pg_container.get_exposed_port(pg_container.port)
    return (
        f"postgresql+psycopg://{pg_container.username}:{pg_container.password}"
        f"@{host}:{port}/{pg_container.dbname}"
    )


@pytest.fixture
def ingest_env(pg_url: str):
    command.upgrade(alembic_config(pg_url), "head")
    database = Database(pg_url)
    with database.connect() as connection:
        connection.execute(text("DELETE FROM pending_documents"))
        connection.execute(text("DELETE FROM documents"))
        connection.commit()

    documents = SqlDocumentStore(database)
    pending = SqlPendingQueue(database)
    accepted: list[str] = []
    probe = CountingProbe()
    ingestor = Ingestor(
        documents=documents,
        pending=pending,
        probe=probe,  # type: ignore[arg-type]
        on_accepted=lambda record: accepted.append(record.path),
    )
    try:
        yield ingestor, database, pending, accepted, probe
    finally:
        with database.connect() as connection:
            connection.execute(text("DELETE FROM pending_documents"))
            connection.execute(text("DELETE FROM documents"))
            connection.commit()


def write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_folder_ingest_intercepts_before_downstream_and_dedupes(
    ingest_env, tmp_path: Path
) -> None:
    ingestor, database, pending, accepted, probe = ingest_env
    source = tmp_path / "inbox"
    source.mkdir()
    write(source / "ok.pdf", valid_pdf())
    truncated = valid_pdf()
    write(source / "truncated.pdf", truncated[: len(truncated) // 3])
    write(source / "encrypted.pdf", encrypted_pdf())
    write(source / "empty.txt", b"")
    write(source / "zz-duplicate.pdf", valid_pdf())

    results = ingestor.ingest_folder(source)

    # 文件按路径排序：empty.txt / encrypted.pdf / ok.pdf / truncated.pdf / zz-duplicate.pdf
    assert [result.outcome for result in results] == [
        IngestOutcome.REJECTED,
        IngestOutcome.REJECTED,
        IngestOutcome.ACCEPTED,
        IngestOutcome.REPAIRABLE,
        IngestOutcome.DUPLICATE,
    ]

    # 体检在派发前完成：rejected 不进入下游；duplicate 不重新体检
    assert sorted(Path(path).name for path in accepted) == ["ok.pdf", "truncated.pdf"]
    assert probe.calls == 4

    # 待确认队列带诊断信息，任何文件不静默丢弃
    pending_records = pending.all()
    assert {Path(record.path).name for record in pending_records} == {
        "encrypted.pdf",
        "empty.txt",
    }
    encrypted = next(
        record for record in pending_records if record.path.endswith("encrypted.pdf")
    )
    assert encrypted.reason == "health_rejected"
    issue_checks = {issue["check"] for issue in encrypted.diagnostics["issues"]}
    assert "encryption" in issue_checks
    assert json.dumps(dict(encrypted.diagnostics))

    with database.connect() as connection:
        document_count = connection.execute(
            text("SELECT count(*) FROM documents")
        ).scalar()
        healths = {
            row[0]
            for row in connection.execute(text("SELECT health FROM documents"))
        }
    assert document_count == 4  # 重复文件不重复入库
    assert healths == {"ok", "repairable", "rejected"}


def test_upload_ingest_uses_content_hash_and_source(
    ingest_env, tmp_path: Path
) -> None:
    ingestor, database, _, accepted, _ = ingest_env
    result = ingestor.ingest_upload(
        "report.txt", b"hello dataagent\n", tmp_path / "uploads"
    )

    assert result.outcome is IngestOutcome.ACCEPTED
    assert result.document.source == "upload"
    assert accepted == [result.document.path]
    with database.connect() as connection:
        row = connection.execute(
            text("SELECT source, health FROM documents WHERE doc_id = :doc_id"),
            {"doc_id": result.document.doc_id},
        ).first()
    assert row == ("upload", "ok")


def test_duplicate_upload_reuses_existing_result(
    ingest_env, tmp_path: Path
) -> None:
    ingestor, _, _, accepted, probe = ingest_env
    first = ingestor.ingest_upload("a.txt", b"same content", tmp_path / "u1")
    second = ingestor.ingest_upload("b.txt", b"same content", tmp_path / "u2")

    assert first.outcome is IngestOutcome.ACCEPTED
    assert second.outcome is IngestOutcome.DUPLICATE
    assert second.document.doc_id == first.document.doc_id
    assert len(accepted) == 1
    assert probe.calls == 1
