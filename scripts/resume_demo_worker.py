"""P0-A 续跑演示 worker（T068）。

真实进程被强杀后，从文件版 resume_point 之后继续，已完成批不重做。
仅用于 quickstart 集成演示；生产 worker 在 P0-B 接入 pipeline 与 PG。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from dataagent.contracts.messages import ParseRequest
from dataagent.kernel.state import State
from dataagent.kernel.supervisor import (
    BatchOutcome,
    Supervisor,
    TaskRunContext,
    plan_batches,
)


class JsonProgressRepository:
    """PG 替身：每批 commit 先写临时文件再原子替换，强杀不会留下半截 JSON。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"state": State.PARSING.name, "resume_point": None, "commits": []}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data), encoding="utf-8")
        os.replace(tmp, self._path)

    def state(self, task_id: str) -> State:
        return State(self._data["state"])

    def save_state(self, task_id: str, state: State) -> None:
        self._data["state"] = state.name
        self._save()

    def resume_point(self, task_id: str) -> int | None:
        return self._data["resume_point"]

    def commit_batch(self, task_id: str, batch_index: int) -> None:
        self._data["resume_point"] = batch_index
        self._data["commits"].append(batch_index)
        self._save()


class DemoExecutor:
    def __init__(self, log_path: Path, run_id: str, batch_ms: int) -> None:
        self._log_path = log_path
        self._run_id = run_id
        self._batch_ms = batch_ms

    def execute(self, request: ParseRequest) -> BatchOutcome:
        start = request.page_range[0]
        record = {"event": "executed", "run": self._run_id, "page_range": list(request.page_range)}
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        time.sleep(self._batch_ms / 1000)
        return BatchOutcome(artifact_ids=(f"sha256:{start:04d}",))


class FileArtifacts:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def write(self, artifact_id: str) -> None:
        (self._root / artifact_id.replace(":", "_")).write_text(
            artifact_id, encoding="utf-8"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="P0-A resume demo worker")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--objects", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--total-pages", type=int, default=8)
    parser.add_argument("--batch-pages", type=int, default=1)
    parser.add_argument("--batch-ms", type=int, default=200)
    args = parser.parse_args()

    supervisor = Supervisor(
        executor=DemoExecutor(args.log, args.run_id, args.batch_ms),
        artifacts=FileArtifacts(args.objects),
        repository=JsonProgressRepository(args.state),
    )
    context = TaskRunContext(
        task_id="demo-task",
        doc_id="sha256:demo",
        engine="engine.pdf@1.0.0",
        params_hash="params-hash",
        rulepack_hash="rulepack-hash",
        resource_hint="cpu",
        doc_affinity_key="demo-affinity",
    )
    supervisor.run_batches(
        context, plan_batches(args.total_pages, batch_pages=args.batch_pages)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
