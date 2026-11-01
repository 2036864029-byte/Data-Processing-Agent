"""受限 PDF 结构探针（FR-002 / PRD 4.1.8）。

只做结构探测、不解析内容，且必须能在任何解析插件之前运行：
- 头部 / trailer / startxref / xref 可解析性
- 对象数、页树深度（**迭代遍历，不递归**）、页尺寸、资源字典嵌套、流解压比、`/Encrypt`
- 超大文件只读头尾，深层检查标记为未采样；解压有上限，防 zip 炸弹
"""

from __future__ import annotations

import re
import zlib
from pathlib import Path

from dataagent.pipeline.ingest.health import (
    DEFAULT_THRESHOLDS,
    FileFacts,
    HealthThresholds,
    PdfFacts,
)

HEAD_BYTES = 8192
TAIL_BYTES = 8192
SCAN_LIMIT_BYTES = 64 * 1024 * 1024
MAX_DECOMPRESS_BYTES = 4 * 1024 * 1024
_MAX_TREE_NODES = 2_000_000

_OBJ_RE = re.compile(rb"(?m)(\d+)\s+(\d+)\s+obj\b")
_PAGE_TYPE_RE = re.compile(rb"/Type\s*/Page\b")
_PAGES_TYPE_RE = re.compile(rb"/Type\s*/Pages\b")
_COUNT_RE = re.compile(rb"/Count\s+(\d+)")
_KIDS_RE = re.compile(rb"/Kids\s*\[(.*?)\]", re.DOTALL)
_REF_RE = re.compile(rb"(\d+)\s+\d+\s+R")
_MEDIABOX_RE = re.compile(
    rb"/MediaBox\s*\[\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)"
)
_ROOT_RE = re.compile(rb"/Root\s+(\d+)\s+\d+\s+R")
_PAGES_REF_RE = re.compile(rb"/Pages\s+(\d+)\s+\d+\s+R")
_STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
_FLATE_RE = re.compile(rb"/Filter\s*\[?\s*/FlateDecode")

_PDF_MAGIC = b"%PDF-"
_SIGNATURES: dict[str, bytes] = {
    "png": b"\x89PNG\r\n\x1a\n",
    "jpg": b"\xff\xd8\xff",
    "jpeg": b"\xff\xd8\xff",
    "tif": b"II*\x00",
    "webp": b"RIFF",
    "bmp": b"BM",
}
_TEXT_EXTENSIONS = frozenset({"csv", "tsv", "txt", "md", "json", "log"})


def detect_magic_matches(extension: str, head: bytes) -> bool:
    """魔数与后缀声明是否一致；不一致交一级判定按内容改判（不在此拒收）。"""
    ext = extension.lower().lstrip(".")
    if ext == "pdf":
        return head.lstrip().startswith(_PDF_MAGIC)
    if ext in _SIGNATURES:
        if ext == "webp":
            return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
        return head.startswith(_SIGNATURES[ext])
    if ext in _TEXT_EXTENSIONS:
        if head.lstrip().startswith(_PDF_MAGIC):
            return False
        if any(head.startswith(signature) for signature in _SIGNATURES.values()):
            return False
        return b"\x00" not in head
    return True


class PdfStructureProbe:
    """文件 → FileFacts；纯读、无副作用。"""

    def __init__(
        self,
        *,
        thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
        scan_limit_bytes: int = SCAN_LIMIT_BYTES,
    ) -> None:
        self._thresholds = thresholds
        self._scan_limit_bytes = scan_limit_bytes

    def probe(self, path: Path | str) -> FileFacts:
        file_path = Path(path)
        size = file_path.stat().st_size
        head = _read_head(file_path, HEAD_BYTES)
        extension = file_path.suffix.lower().lstrip(".")
        magic_matches = detect_magic_matches(extension, head)

        is_pdf = extension == "pdf" or _PDF_MAGIC in head[:1024]
        if not is_pdf:
            return FileFacts(size, extension, head[:8], magic_matches, None)

        if size <= self._scan_limit_bytes:
            data = file_path.read_bytes()
            pdf = _probe_pdf(data, size, scanned=True, thresholds=self._thresholds)
        else:
            data = head + _read_tail(file_path, TAIL_BYTES)
            pdf = _probe_pdf(data, size, scanned=False, thresholds=self._thresholds)
        return FileFacts(size, extension, head[:8], magic_matches, pdf)


def probe_file(path: Path | str) -> FileFacts:
    return PdfStructureProbe().probe(path)


def _read_head(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        return handle.read(limit)


def _read_tail(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - limit))
        return handle.read(limit)


def _probe_pdf(
    data: bytes, size: int, *, scanned: bool, thresholds: HealthThresholds
) -> PdfFacts:
    header_ok = _PDF_MAGIC in data[:1024]
    tail = data[-2048:]
    eof_present = b"%%EOF" in tail
    startxref_ok = _startxref_ok(data, size, scanned=scanned)
    encrypted = b"/Encrypt" in data

    if not scanned:
        return PdfFacts(
            header_ok=header_ok,
            eof_present=eof_present,
            startxref_ok=startxref_ok,
            xref_readable=None if startxref_ok is None else True,
            declared_pages=None,
            readable_pages=None,
            page_tree_depth=None,
            encrypted=encrypted,
        )

    xref_readable = bool(startxref_ok) or (
        b"\nxref" in data
        or b"/Type/XRef" in data
        or b"/Type /XRef" in data
    )

    object_numbers: list[int] = []
    object_bodies: dict[int, bytes] = {}
    matches = list(_OBJ_RE.finditer(data))
    for index, match in enumerate(matches):
        number = int(match.group(1))
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(data)
        object_numbers.append(number)
        object_bodies[number] = data[match.end() : body_end]

    declared_counts: list[int] = []
    page_bodies: list[bytes] = []
    pages_kids: dict[int, list[int]] = {}
    for number in object_numbers:
        body = object_bodies[number]
        if _PAGES_TYPE_RE.search(body):
            count_match = _COUNT_RE.search(body)
            if count_match is not None:
                declared_counts.append(int(count_match.group(1)))
            kids_match = _KIDS_RE.search(body)
            kids = (
                [int(ref.group(1)) for ref in _REF_RE.finditer(kids_match.group(1))]
                if kids_match is not None
                else []
            )
            pages_kids[number] = kids
        elif _PAGE_TYPE_RE.search(body):
            page_bodies.append(body)

    declared_pages = max(declared_counts) if declared_counts else None
    readable_pages = len(page_bodies)
    page_tree_depth = _page_tree_depth(data, object_bodies, pages_kids)
    page_dimensions = _page_dimensions(page_bodies)
    resource_depth = _resource_depth(object_bodies)
    compression_ratio = _max_compression_ratio(
        data, thresholds.compression_sample_streams
    )

    return PdfFacts(
        header_ok=header_ok,
        eof_present=eof_present,
        startxref_ok=startxref_ok,
        xref_readable=xref_readable,
        declared_pages=declared_pages,
        readable_pages=readable_pages,
        page_tree_depth=page_tree_depth,
        page_dimensions_pt=page_dimensions,
        object_count=len(object_numbers),
        resource_depth=resource_depth,
        max_compression_ratio=compression_ratio,
        encrypted=encrypted,
    )


def _startxref_ok(data: bytes, size: int, *, scanned: bool) -> bool | None:
    matches = list(re.finditer(rb"startxref\s+(\d+)", data[-2048:]))
    if not matches:
        return False if scanned else None
    offset = int(matches[-1].group(1))
    if offset < 0 or offset >= size:
        return False
    if not scanned:
        return True
    snippet = data[offset : offset + 16].lstrip()
    return snippet.startswith(b"xref") or re.match(rb"\d+\s+\d+\s+obj", snippet) is not None


def _page_tree_depth(
    data: bytes, object_bodies: dict[int, bytes], pages_kids: dict[int, list[int]]
) -> int | None:
    if not pages_kids:
        return None
    root_candidates: list[int] = []
    root_match = _ROOT_RE.search(data)
    if root_match is not None:
        catalog = object_bodies.get(int(root_match.group(1)))
        if catalog is not None:
            pages_ref = _PAGES_REF_RE.search(catalog)
            if pages_ref is not None:
                root_candidates.append(int(pages_ref.group(1)))
    if not root_candidates:
        referenced = {kid for kids in pages_kids.values() for kid in kids}
        root_candidates = [number for number in pages_kids if number not in referenced]
    if not root_candidates:
        root_candidates = list(pages_kids)[:1]

    max_depth = 0
    visited: set[int] = set()
    stack = [(number, 1) for number in root_candidates]
    while stack:
        number, depth = stack.pop()
        if number in visited:
            # 页树出现环 / 共享节点：结构异常，按超深处理（硬拒）
            max_depth = max(max_depth, _MAX_TREE_NODES)
            continue
        visited.add(number)
        max_depth = max(max_depth, depth)
        for kid in pages_kids.get(number, []):
            if kid in pages_kids:
                if len(visited) < _MAX_TREE_NODES:
                    stack.append((kid, depth + 1))
            else:
                # 叶子页节点也计入深度：根 Pages(1) + Page(2) = 2
                max_depth = max(max_depth, depth + 1)
    return max_depth


def _page_dimensions(page_bodies: list[bytes]) -> tuple[float, ...]:
    dimensions: list[float] = []
    for body in page_bodies:
        match = _MEDIABOX_RE.search(body)
        if match is None:
            continue
        x1, y1, x2, y2 = (float(match.group(index)) for index in range(1, 5))
        dimensions.append(max(abs(x2 - x1), abs(y2 - y1)))
    return tuple(dimensions)


def _resource_depth(object_bodies: dict[int, bytes]) -> int | None:
    maximum: int | None = None
    for body in object_bodies.values():
        start = body.find(b"/Resources")
        if start < 0:
            continue
        snippet = body[start : start + 4096]
        depth = 0
        local_max = 0
        index = 0
        while index < len(snippet) - 1:
            pair = snippet[index : index + 2]
            if pair == b"<<":
                depth += 1
                local_max = max(local_max, depth)
                index += 2
            elif pair == b">>":
                depth = max(0, depth - 1)
                index += 2
            else:
                index += 1
        maximum = local_max if maximum is None else max(maximum, local_max)
    return maximum


def _max_compression_ratio(data: bytes, sample_streams: int) -> float | None:
    ratios: list[float] = []
    for match in _STREAM_RE.finditer(data):
        lookback = data[max(0, match.start() - 512) : match.start()]
        if _FLATE_RE.search(lookback) is None:
            continue
        raw = match.group(1)
        if not raw:
            continue
        ratios.append(_bounded_decompress_ratio(raw))
        if len(ratios) >= sample_streams:
            break
    return max(ratios) if ratios else None


def _bounded_decompress_ratio(raw: bytes) -> float:
    limit = min(MAX_DECOMPRESS_BYTES, max(4096, len(raw) * 201))
    try:
        decompressor = zlib.decompressobj()
        output = decompressor.decompress(raw, limit)
    except zlib.error:
        return 0.0
    if output and (not decompressor.eof or decompressor.unconsumed_tail):
        return limit / len(raw)
    return len(output) / len(raw) if output else 0.0
