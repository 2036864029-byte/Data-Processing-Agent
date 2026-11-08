"""单元测试共享夹具。"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable, Iterator

import pytest

from dataagent.platform.observe.log import configure_logging


@pytest.fixture
def captured_events() -> Iterator[Callable[[], list[dict[str, object]]]]:
    """把 dataagent logger 重定向到内存，返回“读取已发事件”的函数。"""
    logger = logging.getLogger("dataagent")
    handlers = list(logger.handlers)
    level = logger.level
    propagate = logger.propagate
    disabled = logger.disabled
    buffer = io.StringIO()
    configure_logging(stream=buffer)

    def read() -> list[dict[str, object]]:
        return [json.loads(line) for line in buffer.getvalue().splitlines()]

    try:
        yield read
    finally:
        logger.handlers.clear()
        logger.handlers.extend(handlers)
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = disabled
