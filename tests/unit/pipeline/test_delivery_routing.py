"""任务工厂的交付目标路由接线（PR-4-3）。

`deliver_to` 在任务创建注入点解析为输出根；命中写目标目录，
未命中/不可达回退默认根并写结构化日志说明原因。
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

from dataagent.pipeline.output.targets import DeliverTarget
from dataagent.pipeline.task.factory import create_builtin_runner
from dataagent.platform.observe.log import configure_logging


def test_factory_writes_to_matched_target(tmp_path: Path) -> None:
    target_dir = tmp_path / "nas_finance"
    target_dir.mkdir()
    default_dir = tmp_path / "default"
    (tmp_path / "notes.txt").write_bytes(b"plain text document\n")

    runner = create_builtin_runner(
        output_dir=default_dir,
        deliver_to="存到公司 NAS 财报库",
        deliver_targets=(
            DeliverTarget("nas_finance", target_dir, ("公司 NAS 财报库",)),
        ),
    )
    outcome = runner.run([tmp_path / "notes.txt"], task_id="route-hit")

    document = next(item for item in outcome.documents if item.status == "processed")
    assert document.outputs
    for output in document.outputs:
        assert Path(output).is_file()
        assert target_dir in Path(output).parents
    assert not default_dir.exists() or not any(default_dir.rglob("*"))


def test_factory_falls_back_and_logs_reason(tmp_path: Path) -> None:
    target_dir = tmp_path / "nas_finance"
    target_dir.mkdir()
    default_dir = tmp_path / "default"
    (tmp_path / "notes.txt").write_bytes(b"plain text document\n")

    logger = logging.getLogger("dataagent")
    previous_handlers = list(logger.handlers)
    previous_level = logger.level
    previous_propagate = logger.propagate
    previous_disabled = logger.disabled
    buffer = io.StringIO()
    configure_logging(stream=buffer)
    try:
        runner = create_builtin_runner(
            output_dir=default_dir,
            deliver_to="存到别的地方",
            deliver_targets=(
                DeliverTarget("nas_finance", target_dir, ("公司 NAS 财报库",)),
            ),
        )
        outcome = runner.run([tmp_path / "notes.txt"], task_id="route-miss")
    finally:
        logger.handlers.clear()
        logger.handlers.extend(previous_handlers)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
        logger.disabled = previous_disabled

    document = next(item for item in outcome.documents if item.status == "processed")
    for output in document.outputs:
        assert default_dir in Path(output).parents
    assert not any(target_dir.rglob("*"))

    events = [json.loads(line) for line in buffer.getvalue().splitlines()]
    fallback = [event for event in events if event["event"] == "delivery_target_fallback"]
    assert fallback
    assert fallback[0]["reason"] == "no_match"
    assert fallback[0]["requested"] == "存到别的地方"
    assert fallback[0]["output_dir"] == str(default_dir)


def test_factory_without_deliver_to_keeps_default(tmp_path: Path) -> None:
    default_dir = tmp_path / "default"
    (tmp_path / "notes.txt").write_bytes(b"plain text document\n")

    runner = create_builtin_runner(output_dir=default_dir)
    outcome = runner.run([tmp_path / "notes.txt"], task_id="route-none")

    document = next(item for item in outcome.documents if item.status == "processed")
    for output in document.outputs:
        assert default_dir in Path(output).parents


def test_factory_unreachable_target_falls_back(tmp_path: Path) -> None:
    missing = tmp_path / "not_mounted"
    default_dir = tmp_path / "default"
    (tmp_path / "notes.txt").write_bytes(b"plain text document\n")

    runner = create_builtin_runner(
        output_dir=default_dir,
        deliver_to="存到 NAS",
        deliver_targets=(DeliverTarget("nas", missing, ("NAS",)),),
    )
    outcome = runner.run([tmp_path / "notes.txt"], task_id="route-unreachable")

    document = next(item for item in outcome.documents if item.status == "processed")
    for output in document.outputs:
        assert default_dir in Path(output).parents
    assert not missing.exists()
