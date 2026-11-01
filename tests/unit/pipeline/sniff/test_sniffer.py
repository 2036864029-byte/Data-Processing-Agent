"""一级格式判定测试（T078，B2）。

输出 pdf / tabular / text / image / unknown 五类家族；
判定三态 confident / ambiguous / unclassified；magic / 编码 / 分隔符覆盖正常与损坏样本。
"""

import json
from pathlib import Path

from dataagent.pipeline.sniff.sniffer import (
    Family,
    SniffState,
    sniff_file,
    sniff_sample,
)

CSV = "name,amount,date\n" + "".join(
    f"row{index},{index * 10},2026-01-{index:02d}\n" for index in range(1, 20)
)
WEAK_DELIMITER = "a,b,c\n1,2,3\n4,5,6\n7,8\n9,10\n"
PLAIN_TEXT = "第一行普通文本\n第二行没有稳定分隔符\n第三行只是文字\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\nendobj\ntrailer\n%%EOF\n"


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_pdf_magic_is_confident_pdf(tmp_path: Path) -> None:
    result = sniff_file(write(tmp_path, "doc.pdf", PDF))
    assert result.family is Family.PDF
    assert result.state is SniffState.CONFIDENT
    assert "pdf_magic" in result.reasons


def test_image_magic_is_confident_image(tmp_path: Path) -> None:
    result = sniff_file(write(tmp_path, "image.png", PNG))
    assert result.family is Family.IMAGE
    assert result.state is SniffState.CONFIDENT


def test_csv_is_confident_tabular_by_delimiter(tmp_path: Path) -> None:
    result = sniff_file(write(tmp_path, "data.csv", CSV.encode()))
    assert result.family is Family.TABULAR
    assert result.state is SniffState.CONFIDENT
    assert result.encoding == "utf-8"


def test_tsv_is_confident_tabular(tmp_path: Path) -> None:
    tsv = "a\tb\tc\n1\t2\t3\n4\t5\t6\n"
    result = sniff_file(write(tmp_path, "data.tsv", tsv.encode()))
    assert result.family is Family.TABULAR
    assert result.state is SniffState.CONFIDENT


def test_plain_text_is_confident_text(tmp_path: Path) -> None:
    result = sniff_file(write(tmp_path, "notes.txt", PLAIN_TEXT.encode()))
    assert result.family is Family.TEXT
    assert result.state is SniffState.CONFIDENT


def test_gb18030_text_is_detected(tmp_path: Path) -> None:
    result = sniff_file(write(tmp_path, "gbk.txt", PLAIN_TEXT.encode("gb18030")))
    assert result.family is Family.TEXT
    assert result.state is SniffState.CONFIDENT
    assert result.encoding == "gb18030"


def test_utf16_text_is_detected(tmp_path: Path) -> None:
    result = sniff_file(
        write(tmp_path, "utf16.txt", "hello\nworld\nanother line\n".encode("utf-16"))
    )
    assert result.family is Family.TEXT
    assert result.state is SniffState.CONFIDENT
    assert result.encoding is not None and result.encoding.startswith("utf-16")


def test_weak_delimiter_is_ambiguous_and_does_not_guess(tmp_path: Path) -> None:
    result = sniff_file(write(tmp_path, "weak.csv", WEAK_DELIMITER.encode()))
    assert result.state is SniffState.AMBIGUOUS
    assert set(result.candidates) == {Family.TABULAR, Family.TEXT}


def test_binary_without_magic_is_unclassified(tmp_path: Path) -> None:
    result = sniff_file(write(tmp_path, "blob.bin", bytes(range(256)) * 8))
    assert result.family is Family.UNKNOWN
    assert result.state is SniffState.UNCLASSIFIED


def test_empty_sample_is_unclassified() -> None:
    result = sniff_sample(b"", extension="txt", size=0)
    assert result.family is Family.UNKNOWN
    assert result.state is SniffState.UNCLASSIFIED


def test_result_is_serializable() -> None:
    result = sniff_sample(CSV.encode(), extension="csv", size=len(CSV))
    payload = json.loads(json.dumps(result.as_dict()))
    assert payload["family"] == "tabular"
    assert payload["state"] == "confident"
