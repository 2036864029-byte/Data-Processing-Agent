"""无状态 HTTP 骨架：`/health` + `/status` 只读页。

API 无状态（宪法 VI）：不持有任务状态，所有数据经 StatusProvider 从外部读；
仅 GET 只读端点，其余路由 404 / 405。P0-A 骨架，完整端点清单见 cli-api.md。
"""

from __future__ import annotations

import json
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from dataagent.apps.api.status import StatusProvider, render_status


class _StatusHandler(BaseHTTPRequestHandler):
    def __init__(self, *args: Any, provider: StatusProvider, **kwargs: Any) -> None:
        self._provider = provider
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "service": "dataagent"})
        elif self.path == "/status":
            body = render_status(self._provider.snapshot()).encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)
        else:
            self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        self._send_json(405, {"error": "method_not_allowed"})

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body)

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        """静默默认访问日志（由 A9 可观测模块统一接管）。"""


def create_server(host: str, port: int, provider: StatusProvider) -> ThreadingHTTPServer:
    """绑定并返回 HTTP 服务（不启动 serve_forever；端口 0 表示自动分配）。"""
    handler = partial(_StatusHandler, provider=provider)
    return ThreadingHTTPServer((host, port), handler)
