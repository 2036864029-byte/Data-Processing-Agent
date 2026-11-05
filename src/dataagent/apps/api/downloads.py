"""交付访问层：输出根的安全视图 + 按需流式 zip。

所有下载 key 都是相对输出根的正斜杠路径（如 `md/52/<hash>.md`），
解析后（含符号链接）必须仍在输出根内；下载层只读，不触碰任务状态。
zip 是下载动作而非交付格式：边读散文件边压缩边传，不落盘。
"""

from __future__ import annotations

import shutil
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

CHUNK_SIZE = 64 * 1024
MANIFEST_NAME = "manifest.jsonl"

_MEDIA_TYPES = {
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class DownloadKeyError(ValueError):
    """key 非法（路径穿越、绝对路径、控制字符等）。"""


class DownloadNotFound(LookupError):
    """key 合法，但输出根下没有对应的普通文件。"""


def content_type_for(key: str) -> str:
    return _MEDIA_TYPES.get(Path(key).suffix.lower(), "application/octet-stream")


class OutputRoot:
    """输出根目录的安全视图：唯一的 key → 路径解析点。"""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).resolve()

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, key: str) -> Path:
        normalized = _validate_key(key)
        candidate = (self._root / normalized).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError:
            raise DownloadKeyError(key) from None
        return candidate

    def open(self, key: str) -> tuple[Path, BinaryIO]:
        path = self.resolve(key)
        if not path.is_file():
            raise DownloadNotFound(key)
        return path, path.open("rb")

    def manifest_path(self) -> Path | None:
        path = self._root / MANIFEST_NAME
        return path if path.is_file() else None


def _validate_key(key: str) -> str:
    if not key or any(ch in key for ch in "\\:"):
        raise DownloadKeyError(key)
    if any(ord(ch) < 32 or ch == "\x7f" for ch in key):
        raise DownloadKeyError(key)
    # 绝对路径一律拒绝（POSIX 前导斜杠；Windows 盘符已由上面的冒号规则覆盖）
    if key.startswith("/"):
        raise DownloadKeyError(key)
    if any(part in {"", ".", ".."} for part in key.split("/")):
        raise DownloadKeyError(key)
    return key


def write_zip(sink: BinaryIO, entries: Iterable[tuple[str, Path]]) -> None:
    """把 entries 以流式 zip 写入 sink；sink 不可 seek 时 zipfile 用数据描述符。"""
    with zipfile.ZipFile(
        sink, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
    ) as archive:
        for key, path in entries:
            info = zipfile.ZipInfo.from_file(path, arcname=key)
            info.compress_type = zipfile.ZIP_DEFLATED
            with path.open("rb") as source, archive.open(info, "w") as target:
                shutil.copyfileobj(source, target, CHUNK_SIZE)
