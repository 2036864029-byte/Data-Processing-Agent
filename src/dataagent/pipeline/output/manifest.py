"""交付清单（manifest）：一行一文档，供程序批量遍历与下载对账。

默认关闭（`TaskConfig.emit_manifest`）；文件位于输出根目录 `manifest.jsonl`。
产物 key 使用相对输出根的正斜杠路径（如 `md/52/<hash>.md`）。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from dataagent.pipeline.task.results import DocumentOutcome

MANIFEST_NAME = "manifest.jsonl"

_DELIVERABLE_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    ("md", "*.md"),
    ("json", "*.json"),
    ("tables", "*.tables.json"),
)


@dataclass(frozen=True, slots=True)
class ManifestReport:
    """manifest 与落盘产物的对账结果（双向）。"""

    documents: int
    keys_checked: int
    missing: tuple[str, ...] = ()
    orphans: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing and not self.orphans

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "documents": self.documents,
            "keys_checked": self.keys_checked,
            "missing": list(self.missing),
            "orphans": list(self.orphans),
        }


def manifest_entry(
    outcome: DocumentOutcome, output_dir: Path | str
) -> dict[str, Any]:
    root = Path(output_dir)
    keys: dict[str, str | None] = {"md": None, "json": None, "tables": None}
    for output in outcome.outputs:
        if output.endswith(".tables.json"):
            keys["tables"] = _relative_key(output, root)
        elif output.endswith(".md"):
            keys["md"] = _relative_key(output, root)
        elif output.endswith(".json"):
            keys["json"] = _relative_key(output, root)
    return {
        "doc_id": outcome.doc_id,
        "status": outcome.status,
        "delivery_level": outcome.level.value if outcome.level else None,
        "pages": outcome.pages,
        "gap_pages": outcome.gap_pages,
        "md": keys["md"],
        "json": keys["json"],
        "tables": keys["tables"],
        "path": outcome.path,
        "note": outcome.note or None,
    }


def write_manifest(
    outcomes: Sequence[DocumentOutcome], output_dir: Path | str
) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / MANIFEST_NAME
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for outcome in outcomes:
            handle.write(
                json.dumps(
                    manifest_entry(outcome, root), ensure_ascii=False
                )
                + "\n"
            )
    return path


def _relative_key(path: str, root: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def verify_manifest(output_dir: Path | str) -> ManifestReport:
    """核对 manifest 的每个 key 都有文件，且交付目录下没有未登记的产物。"""
    root = Path(output_dir).resolve()
    manifest = root / MANIFEST_NAME
    if not manifest.is_file():
        raise FileNotFoundError(manifest)

    documents = 0
    referenced: set[str] = set()
    missing: list[str] = []
    with manifest.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            documents += 1
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"manifest 第 {number} 行不是合法 JSON：{exc}"
                ) from exc
            if not isinstance(entry, dict):
                raise ValueError(f"manifest 第 {number} 行不是 JSON 对象")
            for field in ("md", "json", "tables"):
                raw = entry.get(field)
                if not raw:
                    continue
                key = str(raw)
                if key in referenced:
                    continue
                referenced.add(key)
                if not _is_landed(root, key):
                    missing.append(key)

    orphans = sorted(
        path.relative_to(root).as_posix()
        for directory, pattern in _DELIVERABLE_PATTERNS
        for path in (root / directory).rglob(pattern)
        if path.is_file()
        and path.relative_to(root).as_posix() not in referenced
    )
    return ManifestReport(
        documents=documents,
        keys_checked=len(referenced),
        missing=tuple(missing),
        orphans=tuple(orphans),
    )


def _is_landed(root: Path, key: str) -> bool:
    try:
        candidate = (root / key).resolve()
    except OSError:
        return False
    return candidate.is_relative_to(root) and candidate.is_file()
