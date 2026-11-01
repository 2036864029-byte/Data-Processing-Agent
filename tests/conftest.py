"""全局测试夹具（T006）：确定性随机种子、临时目录、确定性样本文件。

宪法 I：同一输入必须产生同一产物，可复现是验收前提——测试同样必须确定性。
"""

import os
import random
from collections.abc import Callable
from pathlib import Path

import pytest

FIXED_SEED = int(os.environ.get("DATAAGENT_TEST_SEED", "20261031"))


@pytest.fixture(autouse=True)
def _deterministic_seed() -> None:
    """每个测试开始前重设随机种子，避免用例间状态污染。"""
    random.seed(FIXED_SEED)
    yield


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """独立临时数据目录，测试结束自动清理。"""
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def sample_files(data_dir: Path) -> dict[str, Path]:
    """固定内容的确定性样本文件（内容哈希可复现，供后续阶段复用）。"""
    samples: dict[str, Path] = {}
    for name, content in {
        "tiny.txt": "hello dataagent\n",
        "simple.csv": "a,b,c\n1,2,3\n4,5,6\n",
        "empty.txt": "",
    }.items():
        p = data_dir / name
        p.write_text(content, encoding="utf-8")
        samples[name] = p
    return samples


PAGE_WIDTH = 612
PAGE_HEIGHT = 792


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf_bytes(pages: list[list[str]], *, font_size: float = 6.0) -> bytes:
    """最小合法 PDF（Helvetica 文本页），供编排器 / 基准测试复用。"""
    objects: list[str] = []
    count = len(pages)
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(count))
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {count} >>")
    font_id = 3 + 2 * count
    for index, lines in enumerate(pages):
        parts = [
            f"BT /F1 {font_size} Tf 72 {720 - row * 10} Td"
            f" ({_escape_pdf_text(line)}) Tj ET"
            for row, line in enumerate(lines)
        ]
        stream = "\n".join(parts)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {4 + index * 2} 0 R >>"
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    document = "%PDF-1.4\n"
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document += f"{number} 0 obj\n{body}\nendobj\n"
    xref = len(document)
    document += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    document += "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    document += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    )
    return document.encode("latin-1")


@pytest.fixture
def write_pdf() -> Callable[[Path, list[list[str]]], Path]:
    def _write(path: Path, pages: list[list[str]]) -> Path:
        path.write_bytes(build_pdf_bytes(pages))
        return path

    return _write
