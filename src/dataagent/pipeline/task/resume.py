"""续跑序列化：输入签名与已完成文档结果的编解码。

与编排逻辑解耦，便于单独测试与演进；持久化层只接收/返回普通 dict。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from dataagent.contracts.errors import DefectCode
from dataagent.kernel.supervisor import Supervisor
from dataagent.pipeline.output.delivery import DeliveryLevel
from dataagent.pipeline.repair.fallback import FallbackPath
from dataagent.pipeline.task.context import DocumentContext
from dataagent.pipeline.task.results import DocumentOutcome
from dataagent.platform.store.sql.task_state import TaskStateStore


def input_signature(paths: Sequence[Path]) -> str:
    joined = "\n".join(str(path) for path in paths)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def load_resume_outcomes(
    store: TaskStateStore | None,
    task_id: str,
    signature: str,
    *,
    resume: bool,
    plan_present: bool,
) -> list[dict[str, Any]]:
    """按输入签名决定续跑结果；不匹配则清空并登记新签名。"""
    if store is None:
        return []
    if resume and not plan_present and store.input_signature(task_id) == signature:
        return store.outcomes(task_id)
    store.reset_outcomes(task_id)
    store.set_input_signature(task_id, signature)
    return []


def mark_resumed(contexts: Sequence[DocumentContext], stored: Sequence[dict[str, Any]]) -> int:
    """把已完成文档标记为已处理，返回可续跑数量。"""
    resumed = 0
    for payload in stored[: len(contexts)]:
        if not payload:
            break
        resumed += 1
    for context in contexts[:resumed]:
        context.processed.update(context.universe)
    return resumed


def persist_outcome(
    store: TaskStateStore | None,
    supervisor: Supervisor,
    task_id: str,
    index: int,
    outcome: DocumentOutcome,
) -> None:
    if store is None:
        return
    store.save_outcome(task_id, index, outcome_to_payload(outcome))
    supervisor.commit_batch(task_id, index)


def outcome_to_payload(outcome: DocumentOutcome) -> dict[str, Any]:
    return {
        "doc_id": outcome.doc_id,
        "path": outcome.path,
        "family": outcome.family,
        "status": outcome.status,
        "pages": outcome.pages,
        "gap_pages": outcome.gap_pages,
        "level": None if outcome.level is None else outcome.level.value,
        "defect_codes": [code.value for code in outcome.defect_codes],
        "outputs": list(outcome.outputs),
        "fallback_path": (None if outcome.fallback_path is None else outcome.fallback_path.value),
        "note": outcome.note,
        "avg_extract_quality": outcome.avg_extract_quality,
        "failure_counts": [[code.value, count] for code, count in outcome.failure_counts],
    }


def outcome_from_payload(payload: Mapping[str, Any]) -> DocumentOutcome:
    level = payload.get("level")
    fallback = payload.get("fallback_path")
    return DocumentOutcome(
        doc_id=str(payload["doc_id"]),
        path=str(payload["path"]),
        family=str(payload["family"]),
        status=str(payload["status"]),
        pages=int(payload.get("pages", 0)),
        gap_pages=int(payload.get("gap_pages", 0)),
        level=DeliveryLevel(level) if level else None,
        defect_codes=tuple(DefectCode(value) for value in payload.get("defect_codes", ())),
        outputs=tuple(str(value) for value in payload.get("outputs", ())),
        fallback_path=FallbackPath(fallback) if fallback else None,
        note=str(payload.get("note", "")),
        avg_extract_quality=float(payload.get("avg_extract_quality", 0.0)),
        failure_counts=tuple(
            (DefectCode(code), int(count)) for code, count in payload.get("failure_counts", ())
        ),
    )
