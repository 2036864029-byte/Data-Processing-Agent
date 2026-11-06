"""任务级中断恢复演练子进程（PR-4-4）。

真实 runner + PG 状态存储 + 流式落盘。父进程在首个文档完成并记录后强杀，
重启后已完成文档跳过、未完成文档续跑；任务收尾写 manifest 供对账。
仅用于恢复演练；生产入口仍是 CLI / worker。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dataagent.pipeline.task.config import TaskConfig
from dataagent.pipeline.task.factory import create_builtin_runner
from dataagent.pipeline.task.state_store import SqlTaskStateStore
from dataagent.platform.store.sql.engine import Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="任务级续跑演练")
    parser.add_argument("--pg-url", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args(argv)

    store = SqlTaskStateStore(Database(args.pg_url))
    runner = create_builtin_runner(
        output_dir=args.output_dir,
        state_store=store,
        config=TaskConfig(emit_manifest=True),
    )
    outcome = runner.run(args.inputs, task_id=args.task_id)
    print(
        json.dumps(
            {
                "documents": len(outcome.documents),
                "processed": outcome.count("processed"),
                "state": outcome.machine_state,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
