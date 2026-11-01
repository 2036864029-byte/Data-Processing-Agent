"""`/status` 只读页与 API 无状态测试（T045，A7）。

GET /status 服务端渲染只读页（不改变状态）；GET /health 200；API 进程不持有业务状态，
状态全部来自注入的状态源（生产为 PG）。
"""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from dataagent.apps.api.server import create_server
from dataagent.apps.api.status import ServiceStatus, TaskStatus


class StaticProvider:
    def __init__(self) -> None:
        self.calls = 0

    def snapshot(self) -> ServiceStatus:
        self.calls += 1
        return ServiceStatus(
            tasks=(TaskStatus(task_id="task-1", state="PARSING", progress="3/10"),),
            queue_depth=4,
            alive_workers=2,
        )


@pytest.fixture
def running_api() -> Iterator[tuple[StaticProvider, str]]:
    provider = StaticProvider()
    server = create_server("127.0.0.1", 0, provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield provider, url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def get(url: str) -> tuple[int, str, str]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return (
            response.status,
            response.headers.get("Content-Type", ""),
            response.read().decode(),
        )


def test_health_returns_ok_json(running_api: tuple[StaticProvider, str]) -> None:
    _, url = running_api
    status, content_type, body = get(f"{url}/health")
    assert status == 200
    assert content_type.startswith("application/json")
    assert json.loads(body)["status"] == "ok"


def test_status_renders_read_only_page(running_api: tuple[StaticProvider, str]) -> None:
    provider, url = running_api
    status, content_type, body = get(f"{url}/status")

    assert status == 200
    assert content_type.startswith("text/html")
    assert "task-1" in body
    assert "PARSING" in body
    assert "3/10" in body
    assert 'http-equiv="refresh"' in body  # SSR + 定时刷新，无构建链
    assert provider.calls == 1


def test_status_does_not_change_state(running_api: tuple[StaticProvider, str]) -> None:
    provider, url = running_api
    _, _, first = get(f"{url}/status")
    _, _, second = get(f"{url}/status")

    assert first == second
    assert provider.calls == 2  # 只读：仅查询快照，无写路径


def test_post_status_is_rejected(running_api: tuple[StaticProvider, str]) -> None:
    _, url = running_api
    request = urllib.request.Request(f"{url}/status", data=b"x", method="POST")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=5)
    assert excinfo.value.code == 405


def test_unknown_route_is_404(running_api: tuple[StaticProvider, str]) -> None:
    _, url = running_api
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(f"{url}/nope", timeout=5)
    assert excinfo.value.code == 404


def test_api_is_stateless_across_restart() -> None:
    """API 进程不持有业务状态：重启后同一状态源给出同一页面。"""
    provider = StaticProvider()

    server_a = create_server("127.0.0.1", 0, provider)
    thread_a = threading.Thread(target=server_a.serve_forever, daemon=True)
    thread_a.start()
    url_a = f"http://127.0.0.1:{server_a.server_address[1]}"
    try:
        _, _, first = get(f"{url_a}/status")
    finally:
        server_a.shutdown()
        server_a.server_close()
        thread_a.join(timeout=5)

    server_b = create_server("127.0.0.1", 0, provider)
    thread_b = threading.Thread(target=server_b.serve_forever, daemon=True)
    thread_b.start()
    url_b = f"http://127.0.0.1:{server_b.server_address[1]}"
    try:
        _, _, second = get(f"{url_b}/status")
    finally:
        server_b.shutdown()
        server_b.server_close()
        thread_b.join(timeout=5)

    assert first == second
