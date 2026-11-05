"""无状态 HTTP 骨架：`/health`、`/status` 与交付访问层。

API 无状态（宪法 VI）：不持有任务状态，所有数据经 StatusProvider 从外部读；
下载端点只读输出根目录。仅 GET，其余方法 405、未知路由 404。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO, cast
from urllib.parse import parse_qs, unquote, urlsplit

from dataagent.apps.api.downloads import (
    CHUNK_SIZE,
    DownloadKeyError,
    DownloadNotFound,
    OutputRoot,
    content_type_for,
    write_zip,
)
from dataagent.apps.api.status import StatusProvider, render_status

_DOWNLOAD_PREFIX = "/download/"
_ZIP_NAME = "dataagent-deliverables.zip"


class _StatusHandler(BaseHTTPRequestHandler):
    def __init__(
        self,
        *args: Any,
        provider: StatusProvider,
        downloads: OutputRoot | None,
        **kwargs: Any,
    ) -> None:
        self._provider = provider
        self._downloads = downloads
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        path = parts.path
        if path == "/health":
            self._send_json(200, {"status": "ok", "service": "dataagent"})
        elif path == "/status":
            body = render_status(self._provider.snapshot()).encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)
        elif path == "/manifest":
            self._send_manifest()
        elif path == "/download/zip":
            self._send_zip(parts.query)
        elif path.startswith(_DOWNLOAD_PREFIX):
            self._send_download(unquote(path[len(_DOWNLOAD_PREFIX) :]))
        else:
            self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        self._send_json(405, {"error": "method_not_allowed"})

    def _send_download(self, key: str) -> None:
        downloads = self._require_downloads()
        if downloads is None:
            return
        try:
            path, handle = downloads.open(key)
        except DownloadKeyError:
            self._send_json(400, {"error": "invalid_key"})
            return
        except DownloadNotFound:
            self._send_json(404, {"error": "not_found"})
            return
        with handle:
            self._send_headers(
                200,
                content_type_for(key),
                os.fstat(handle.fileno()).st_size,
                _attachment(Path(key).name),
            )
            shutil.copyfileobj(handle, self.wfile, CHUNK_SIZE)

    def _send_zip(self, query: str) -> None:
        downloads = self._require_downloads()
        if downloads is None:
            return
        keys = _zip_keys(query)
        if not keys:
            self._send_json(400, {"error": "missing_keys"})
            return

        entries: list[tuple[str, Path]] = []
        for key in keys:
            try:
                path = downloads.resolve(key)
            except DownloadKeyError:
                self._send_json(400, {"error": "invalid_key", "key": key})
                return
            if not path.is_file():
                self._send_json(404, {"error": "not_found", "key": key})
                return
            entries.append((key, path))

        # 流式：无 Content-Length，写完即关连接
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", _attachment(_ZIP_NAME))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            write_zip(cast(BinaryIO, self.wfile), entries)

    def _send_manifest(self) -> None:
        downloads = self._require_downloads()
        if downloads is None:
            return
        path = downloads.manifest_path()
        if path is None:
            self._send_json(404, {"error": "manifest_not_found"})
            return
        with path.open("rb") as handle:
            self._send_headers(
                200,
                "application/x-ndjson; charset=utf-8",
                os.fstat(handle.fileno()).st_size,
                _attachment(path.name),
            )
            shutil.copyfileobj(handle, self.wfile, CHUNK_SIZE)

    def _require_downloads(self) -> OutputRoot | None:
        if self._downloads is None:
            self._send_json(503, {"error": "download_not_configured"})
        return self._downloads

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body)

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self._send_headers(status, content_type, len(body), None)
        self.wfile.write(body)

    def _send_headers(
        self, status: int, content_type: str, length: int, disposition: str | None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        if disposition is not None:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        """静默默认访问日志（由 A9 可观测模块统一接管）。"""


def _zip_keys(query: str) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for value in parse_qs(query).get("keys", []):
        for raw in value.split(","):
            key = raw.strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def _attachment(name: str) -> str:
    safe = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_" for ch in name)
    return f'attachment; filename="{safe}"'


def create_server(
    host: str,
    port: int,
    provider: StatusProvider,
    *,
    output_dir: Path | str | None = None,
) -> ThreadingHTTPServer:
    """绑定并返回 HTTP 服务（不启动 serve_forever；端口 0 表示自动分配）。"""
    downloads = OutputRoot(output_dir) if output_dir is not None else None
    handler = partial(
        _StatusHandler, provider=provider, downloads=downloads
    )
    return ThreadingHTTPServer((host, port), handler)
