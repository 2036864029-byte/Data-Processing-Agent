"""一级格式判定器（FR-003 / PRD 4.2.5）。

纯规则、不依赖任何解析插件；只读前 256KB，单文件 ≤50ms。
输出 5 类家族（pdf / tabular / text / image / unknown）+ 三态
（confident / ambiguous / unclassified）。后缀只作提示，不作判据。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import Any, Final

from dataagent.platform.observe.log import log_event

SAMPLE_LIMIT_BYTES: Final[int] = 256 * 1024
SAMPLE_LINES: Final[int] = 100
AMBIGUOUS_LINES: Final[int] = 50


@unique
class Family(StrEnum):
    PDF = "pdf"
    TABULAR = "tabular"
    TEXT = "text"
    IMAGE = "image"
    UNKNOWN = "unknown"


@unique
class SniffState(StrEnum):
    CONFIDENT = "confident"
    AMBIGUOUS = "ambiguous"
    UNCLASSIFIED = "unclassified"


EXTENSION_FAMILIES: Final[Mapping[str, Family]] = {
    "pdf": Family.PDF,
    "csv": Family.TABULAR,
    "tsv": Family.TABULAR,
    "xlsx": Family.TABULAR,
    "txt": Family.TEXT,
    "md": Family.TEXT,
    "json": Family.TEXT,
    "log": Family.TEXT,
    "png": Family.IMAGE,
    "jpg": Family.IMAGE,
    "jpeg": Family.IMAGE,
    "tif": Family.IMAGE,
    "tiff": Family.IMAGE,
    "webp": Family.IMAGE,
    "bmp": Family.IMAGE,
}

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_IMAGE_SIGNATURES: Final[tuple[bytes, ...]] = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"II*\x00",
    b"MM\x00*",
    b"BM",
)
_DELIMITERS: Final[tuple[str, ...]] = (",", "\t", ";", "|")


@dataclass(frozen=True, slots=True)
class DelimiterStats:
    delimiter: str | None
    consistency: float
    spread: int
    fields: int


@dataclass(frozen=True, slots=True)
class SniffResult:
    family: Family
    state: SniffState
    reasons: tuple[str, ...] = ()
    candidates: tuple[Family, ...] = ()
    extension_mismatch: bool = False
    encoding: str | None = None
    bytes_read: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "family": self.family.value,
            "state": self.state.value,
            "reasons": list(self.reasons),
            "candidates": [family.value for family in self.candidates],
            "extension_mismatch": self.extension_mismatch,
            "encoding": self.encoding,
            "bytes_read": self.bytes_read,
        }


def read_sample(path: Path | str, limit: int = SAMPLE_LIMIT_BYTES) -> tuple[bytes, int]:
    with Path(path).open("rb") as handle:
        sample = handle.read(limit)
    return sample, len(sample)


def sniff_file(path: Path | str, *, limit: int = SAMPLE_LIMIT_BYTES) -> SniffResult:
    file_path = Path(path)
    sample, _ = read_sample(file_path, limit)
    return sniff_sample(
        sample,
        extension=file_path.suffix.lower().lstrip("."),
        size=file_path.stat().st_size,
        path=file_path,
    )


def sniff_sample(
    sample: bytes,
    *,
    extension: str,
    size: int,
    path: Path | None = None,
) -> SniffResult:
    reasons: list[str] = []
    encoding: str | None = None
    candidates: tuple[Family, ...] = ()

    if not sample:
        result = SniffResult(
            Family.UNKNOWN,
            SniffState.UNCLASSIFIED,
            reasons=("empty_sample",),
            bytes_read=0,
        )
        return _with_extension_mismatch(result, extension=extension)

    magic_family = _magic_family(sample[:16])
    if magic_family is Family.PDF:
        result = SniffResult(
            Family.PDF,
            SniffState.CONFIDENT,
            reasons=("pdf_magic",),
            bytes_read=len(sample),
        )
    elif magic_family is Family.IMAGE:
        result = SniffResult(
            Family.IMAGE,
            SniffState.CONFIDENT,
            reasons=("image_magic",),
            bytes_read=len(sample),
        )
    else:
        if sample.startswith(_ZIP_MAGIC):
            reasons.append("zip_container_unsupported")
        text, encoding = _decode_text(sample)
        if text is None:
            reasons = [*reasons, "no_magic", "binary_or_unsupported_encoding"]
            result = SniffResult(
                Family.UNKNOWN,
                SniffState.UNCLASSIFIED,
                reasons=tuple(reasons),
                bytes_read=len(sample),
            )
        else:
            stats = _delimiter_stats(text)
            if stats.delimiter is not None and stats.consistency > 0.8 and stats.spread <= 1:
                reasons.extend(
                    [
                        f"delimiter:{_delimiter_name(stats.delimiter)}",
                        f"encoding:{encoding}",
                    ]
                )
                result = SniffResult(
                    Family.TABULAR,
                    SniffState.CONFIDENT,
                    reasons=tuple(reasons),
                    encoding=encoding,
                    bytes_read=len(sample),
                )
            elif (
                stats.delimiter is not None
                and stats.consistency > 0.5
                and stats.spread <= 2
            ):
                reasons.append(
                    f"weak_delimiter:{_delimiter_name(stats.delimiter)}"
                    f"({stats.consistency:.2f})"
                )
                candidates = (Family.TABULAR, Family.TEXT)
                result = SniffResult(
                    Family.TEXT,
                    SniffState.AMBIGUOUS,
                    reasons=tuple(reasons),
                    candidates=candidates,
                    encoding=encoding,
                    bytes_read=len(sample),
                )
            else:
                reasons.append(f"encoding:{encoding}")
                result = SniffResult(
                    Family.TEXT,
                    SniffState.CONFIDENT,
                    reasons=tuple(reasons),
                    encoding=encoding,
                    bytes_read=len(sample),
                )

    result = _with_extension_mismatch(result, extension=extension)
    if path is not None and result.extension_mismatch:
        declared = EXTENSION_FAMILIES.get(extension)
        log_event(
            "extension_mismatch",
            path=str(path),
            declared=None if declared is None else declared.value,
            detected=result.family.value,
            state=result.state.value,
        )
    return result


def _magic_family(head: bytes) -> Family | None:
    if head.lstrip().startswith(_PDF_MAGIC):
        return Family.PDF
    if any(head.startswith(signature) for signature in _IMAGE_SIGNATURES):
        return Family.IMAGE
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return Family.IMAGE
    return None


def _decode_text(sample: bytes) -> tuple[str | None, str | None]:
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return _printable(sample.decode("utf-16", errors="replace"), "utf-16")
    if b"\x00" in sample:
        return None, None
    for encoding in ("utf-8", "gb18030"):
        try:
            text = sample.decode(encoding)
        except UnicodeDecodeError:
            continue
        decoded, used = _printable(text, encoding)
        if decoded is not None:
            return decoded, used
    return None, None


def _printable(text: str, encoding: str) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    printable = sum(
        1 for char in text if char.isprintable() or char in {"\n", "\r", "\t"}
    )
    if printable / len(text) >= 0.95:
        return text, encoding
    return None, None


def _delimiter_stats(text: str) -> DelimiterStats:
    lines = [line for line in text.splitlines()[:SAMPLE_LINES] if line.strip()]
    if len(lines) < 2:
        return DelimiterStats(None, 0.0, 0, 0)

    best: DelimiterStats | None = None
    for delimiter in _DELIMITERS:
        counts = [line.count(delimiter) + 1 for line in lines]
        modal_fields, modal_count = Counter(counts).most_common(1)[0]
        if modal_fields < 2:
            continue
        stats = DelimiterStats(
            delimiter=delimiter,
            consistency=modal_count / len(lines),
            spread=max(counts) - min(counts),
            fields=modal_fields,
        )
        if best is None or stats.consistency > best.consistency:
            best = stats
    return best if best is not None else DelimiterStats(None, 0.0, 0, 0)


def _delimiter_name(delimiter: str) -> str:
    return {"\t": "tab", ",": "comma", ";": "semicolon", "|": "pipe"}.get(
        delimiter, delimiter
    )


def _with_extension_mismatch(result: SniffResult, *, extension: str) -> SniffResult:
    declared = EXTENSION_FAMILIES.get(extension)
    if declared is None:
        return result
    detected = result.candidates if result.candidates else (result.family,)
    if declared in detected:
        return result
    return SniffResult(
        family=result.family,
        state=result.state,
        reasons=result.reasons,
        candidates=result.candidates,
        extension_mismatch=True,
        encoding=result.encoding,
        bytes_read=result.bytes_read,
    )
