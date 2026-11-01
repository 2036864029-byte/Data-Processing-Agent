"""文件级体检三态测试（T073，B1）。

11 项检查 → ok / repairable / rejected；页树深度超限硬拒；
单页尺寸 / 对象总数 / 资源嵌套深度只标记可疑（高风险页），不拒收。
"""

import json
import zlib
from pathlib import Path

import pytest

from dataagent.pipeline.ingest.health import (
    DEFAULT_THRESHOLDS,
    FileFacts,
    FileHealthChecker,
    HealthCheck,
    HealthStatus,
    PdfFacts,
)
from dataagent.pipeline.ingest.pdf_probe import PdfStructureProbe, probe_file

GOOD_PDF = PdfFacts(
    header_ok=True,
    eof_present=True,
    startxref_ok=True,
    xref_readable=True,
    declared_pages=1,
    readable_pages=1,
    page_tree_depth=2,
    page_dimensions_pt=(792.0,),
    object_count=3,
    resource_depth=1,
    max_compression_ratio=1.0,
    encrypted=False,
)


def make_facts(
    *,
    size_bytes: int = 1024,
    extension: str = "pdf",
    magic_matches: bool = True,
    **pdf_overrides: object,
) -> FileFacts:
    pdf_fields = {
        "header_ok": GOOD_PDF.header_ok,
        "eof_present": GOOD_PDF.eof_present,
        "startxref_ok": GOOD_PDF.startxref_ok,
        "xref_readable": GOOD_PDF.xref_readable,
        "declared_pages": GOOD_PDF.declared_pages,
        "readable_pages": GOOD_PDF.readable_pages,
        "page_tree_depth": GOOD_PDF.page_tree_depth,
        "page_dimensions_pt": GOOD_PDF.page_dimensions_pt,
        "object_count": GOOD_PDF.object_count,
        "resource_depth": GOOD_PDF.resource_depth,
        "max_compression_ratio": GOOD_PDF.max_compression_ratio,
        "encrypted": GOOD_PDF.encrypted,
    }
    pdf_fields.update(pdf_overrides)
    return FileFacts(
        size_bytes=size_bytes,
        extension=extension,
        magic=b"%PDF-1.4",
        magic_matches=magic_matches,
        pdf=PdfFacts(**pdf_fields),  # type: ignore[arg-type]
    )


def checks(report: object) -> set[HealthCheck]:
    return {issue.check for issue in report.issues}  # type: ignore[attr-defined]


def test_all_good_facts_are_ok() -> None:
    report = FileHealthChecker().check(make_facts())
    assert report.status is HealthStatus.OK
    assert report.issues == ()
    assert report.high_risk is False
    assert report.high_risk_pages == ()


@pytest.mark.parametrize("size", [0, DEFAULT_THRESHOLDS.max_size_bytes + 1])
def test_empty_or_oversized_rejected(size: int) -> None:
    report = FileHealthChecker().check(make_facts(size_bytes=size))
    assert report.status is HealthStatus.REJECTED
    assert HealthCheck.EMPTY_OR_OVERSIZE in checks(report)


def test_magic_mismatch_is_informational_not_blocking() -> None:
    report = FileHealthChecker().check(
        make_facts(extension="csv", magic_matches=False)
    )
    assert report.status is HealthStatus.OK
    assert HealthCheck.MAGIC in checks(report)


def test_header_or_eof_missing_is_repairable() -> None:
    report = FileHealthChecker().check(make_facts(eof_present=False))
    assert report.status is HealthStatus.REPAIRABLE
    assert HealthCheck.HEADER_TRAILER in checks(report)


def test_unreadable_xref_is_repairable() -> None:
    report = FileHealthChecker().check(make_facts(xref_readable=False))
    assert report.status is HealthStatus.REPAIRABLE
    assert HealthCheck.XREF in checks(report)


def test_declared_and_readable_page_mismatch_is_repairable() -> None:
    report = FileHealthChecker().check(make_facts(declared_pages=5, readable_pages=3))
    assert report.status is HealthStatus.REPAIRABLE
    assert HealthCheck.PAGE_TREE in checks(report)


def test_page_tree_depth_over_50_is_hard_rejected() -> None:
    ok_report = FileHealthChecker().check(make_facts(page_tree_depth=50))
    assert HealthStatus.REJECTED not in {ok_report.status}
    rejected = FileHealthChecker().check(make_facts(page_tree_depth=51))
    assert rejected.status is HealthStatus.REJECTED
    assert HealthCheck.PAGE_TREE_DEPTH in checks(rejected)


def test_oversized_page_is_suspicious_and_marked_high_risk() -> None:
    report = FileHealthChecker().check(
        make_facts(page_dimensions_pt=(792.0, 20000.0))
    )
    assert report.status is HealthStatus.OK
    assert report.high_risk_pages == (2,)
    assert HealthCheck.PAGE_SIZE in checks(report)


def test_object_count_and_resource_depth_are_suspicious() -> None:
    too_many_objects = FileHealthChecker().check(
        make_facts(object_count=DEFAULT_THRESHOLDS.max_object_count + 1)
    )
    assert too_many_objects.status is HealthStatus.OK
    assert too_many_objects.high_risk is True
    assert HealthCheck.OBJECT_COUNT in checks(too_many_objects)

    deep_resources = FileHealthChecker().check(
        make_facts(resource_depth=DEFAULT_THRESHOLDS.max_resource_depth + 1)
    )
    assert deep_resources.status is HealthStatus.OK
    assert deep_resources.high_risk is True
    assert HealthCheck.RESOURCE_DEPTH in checks(deep_resources)


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(200.0, HealthStatus.OK), (201.0, HealthStatus.REJECTED)],
)
def test_compression_ratio_threshold(ratio: float, expected: HealthStatus) -> None:
    report = FileHealthChecker().check(make_facts(max_compression_ratio=ratio))
    assert report.status is expected


def test_encrypted_is_rejected() -> None:
    report = FileHealthChecker().check(make_facts(encrypted=True))
    assert report.status is HealthStatus.REJECTED
    assert HealthCheck.ENCRYPTION in checks(report)


def test_rejected_wins_over_repairable_and_report_is_serializable() -> None:
    report = FileHealthChecker().check(
        make_facts(eof_present=False, page_tree_depth=60)
    )
    assert report.status is HealthStatus.REJECTED
    assert json.dumps(report.as_dict())


def test_non_pdf_text_file_passes_basic_checks() -> None:
    facts = FileFacts(
        size_bytes=20,
        extension="txt",
        magic=b"hello dat",
        magic_matches=True,
        pdf=None,
    )
    assert FileHealthChecker().check(facts).status is HealthStatus.OK


# ── 真实探针：用合成 PDF 验证结构检查 ──────────────────────────────


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


def simple_pdf(*, media_box: bytes = b"0 0 612 792") -> bytes:
    return build_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox ["
            + media_box
            + b"] /Resources << >> >>",
        ]
    )


def nested_pages_pdf(depth: int) -> bytes:
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    for level in range(1, depth + 1):
        objects.append(
            f"<< /Type /Pages /Kids [{level + 2} 0 R] /Count 1 >>".encode()
        )
    objects.append(
        f"<< /Type /Page /Parent {depth + 1} 0 R /MediaBox [0 0 612 792] >>".encode()
    )
    return build_pdf(objects)


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_probe_valid_pdf(tmp_path: Path) -> None:
    facts = probe_file(write(tmp_path, "ok.pdf", simple_pdf()))
    assert facts.pdf is not None
    assert facts.pdf.header_ok is True
    assert facts.pdf.eof_present is True
    assert facts.pdf.startxref_ok is True
    assert facts.pdf.readable_pages == 1
    assert facts.pdf.declared_pages == 1
    assert facts.pdf.page_tree_depth == 2
    assert facts.pdf.page_dimensions_pt == (792.0,)
    assert FileHealthChecker().check(facts).status is HealthStatus.OK


def test_probe_truncated_pdf_is_repairable(tmp_path: Path) -> None:
    data = simple_pdf()
    facts = probe_file(write(tmp_path, "cut.pdf", data[: len(data) // 3]))
    assert facts.pdf is not None
    assert facts.pdf.eof_present is False
    assert FileHealthChecker().check(facts).status is HealthStatus.REPAIRABLE


def test_probe_deep_page_tree_is_rejected(tmp_path: Path) -> None:
    facts = probe_file(write(tmp_path, "deep.pdf", nested_pages_pdf(60)))
    assert facts.pdf is not None
    assert facts.pdf.page_tree_depth is not None
    assert facts.pdf.page_tree_depth > 50
    assert FileHealthChecker().check(facts).status is HealthStatus.REJECTED


def test_probe_oversized_page_marked_high_risk(tmp_path: Path) -> None:
    facts = probe_file(
        write(tmp_path, "big.pdf", simple_pdf(media_box=b"0 0 20000 20000"))
    )
    report = FileHealthChecker().check(facts)
    assert report.status is HealthStatus.OK
    assert report.high_risk_pages == (1,)


def test_probe_compression_bomb_rejected(tmp_path: Path) -> None:
    compressed = zlib.compress(b"\x00" * 500_000)
    stream_object = (
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(compressed)
        + compressed
        + b"\nendstream"
    )
    data = build_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
            stream_object,
        ]
    )
    facts = probe_file(write(tmp_path, "bomb.pdf", data))
    assert facts.pdf is not None
    assert facts.pdf.max_compression_ratio is not None
    assert facts.pdf.max_compression_ratio > 200
    assert FileHealthChecker().check(facts).status is HealthStatus.REJECTED


def test_probe_encrypted_pdf_rejected(tmp_path: Path) -> None:
    data = build_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
            b"<< /Filter /Standard >>",
        ],
        trailer_extra=b" /Encrypt 4 0 R",
    )
    facts = probe_file(write(tmp_path, "enc.pdf", data))
    assert facts.pdf is not None
    assert facts.pdf.encrypted is True
    assert FileHealthChecker().check(facts).status is HealthStatus.REJECTED


def test_probe_extension_mismatch_not_blocking(tmp_path: Path) -> None:
    facts = probe_file(write(tmp_path, "renamed.csv", simple_pdf()))
    assert facts.magic_matches is False
    report = FileHealthChecker().check(facts)
    assert report.status is HealthStatus.OK
    assert HealthCheck.MAGIC in checks(report)


def test_probe_empty_file_rejected(tmp_path: Path) -> None:
    facts = probe_file(write(tmp_path, "empty.txt", b""))
    assert FileHealthChecker().check(facts).status is HealthStatus.REJECTED


def test_probe_custom_scan_limit_skips_deep_checks(tmp_path: Path) -> None:
    path = write(tmp_path, "ok.pdf", simple_pdf())
    facts = PdfStructureProbe(scan_limit_bytes=10).probe(path)
    assert facts.pdf is not None
    assert facts.pdf.page_tree_depth is None
    assert facts.pdf.header_ok is True
