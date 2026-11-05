"""交付清单（manifest）：一行一文档，供程序批量遍历与下载对账。

默认关闭（`TaskConfig.emit_manifest`）；文件位于输出根目录 `manifest.jsonl`。
产物 key 使用相对输出根的正斜杠路径（如 `md/52/<hash>.md`）。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dataagent.pipeline.task.results import DocumentOutcome

MANIFEST_NAME = "manifest.jsonl"


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
