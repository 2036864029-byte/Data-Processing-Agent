"""本地盘对象存储实现。

原子写（先写临时文件再 os.replace 落位）；先写内容再写索引——内容寻址下目录项即索引，
内容写全后原子落位，索引（目录项）才出现，崩溃不会出现「索引在、内容半截」；
临时盘预留：临时文件写在独立 tmp 目录（可与数据盘分离，避免写放大）。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from dataagent.platform.store.objectstore.base import ObjectStore


class LocalObjectStore(ObjectStore):
    """本地盘对象存储：根目录下以内容哈希为文件名直接落盘。"""

    def __init__(self, root: Path, tmp_dir: Path | None = None) -> None:
        self._root = root
        self._tmp = tmp_dir if tmp_dir is not None else root / ".tmp"
        self._root.mkdir(parents=True, exist_ok=True)
        self._tmp.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not key or "/" in key or "\\" in key or key.startswith("."):
            raise ValueError(f"非法对象 key：{key!r}（内容寻址 key 应为 SHA-256 十六进制）")
        return self._root / key

    def put(self, key: str, content: bytes) -> None:
        target = self._path(key)
        tmp = self._tmp / f".write-{uuid.uuid4().hex}.tmp"
        try:
            with tmp.open("wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)  # 原子落位：内容先写全，目录项（索引）后出现
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise KeyError(f"对象不存在：{key}")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()

    def list(self, prefix: str = "") -> list[str]:
        return sorted(
            p.name for p in self._root.iterdir() if p.is_file() and p.name.startswith(prefix)
        )
