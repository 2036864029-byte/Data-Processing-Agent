"""`dataagent up` 的服务编排：自检 → 启动 API / worker → 打印地址。

CLI 是唯一入口：本函数把 API 与 worker 作为同一控制面的两个壳拉起。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer

from dataagent.apps.api.server import create_server
from dataagent.apps.api.status import EmptyStatusProvider, StatusProvider

WorkerTarget = Callable[[threading.Event], None]


@dataclass
class RunningService:
    url: str
    server: ThreadingHTTPServer | None
    stop_event: threading.Event
    worker_thread: threading.Thread | None = None
    _stopped: bool = field(default=False, repr=False)

    def wait(self) -> None:
        """阻塞到 Ctrl+C，然后回收资源。"""
        try:
            while not self.stop_event.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self.stop_event.set()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.worker_thread is not None:
            self.worker_thread.join(timeout=5)


def start_service(
    *,
    host: str,
    port: int,
    role: str = "api",
    provider: StatusProvider | None = None,
    worker_target: WorkerTarget | None = None,
) -> RunningService:
    """启动 API（role=api/all）与可选 worker 线程；返回可停止的运行句柄。"""
    if role not in {"api", "all", "worker"}:
        raise ValueError(f"未知 role：{role}")
    stop_event = threading.Event()

    server: ThreadingHTTPServer | None = None
    url = "worker"
    if role in {"api", "all"}:
        server = create_server(host, port, provider or EmptyStatusProvider())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://{host}:{server.server_address[1]}"
        print(url, flush=True)

    worker_thread: threading.Thread | None = None
    if role in {"worker", "all"} and worker_target is not None:
        worker_thread = threading.Thread(
            target=worker_target, args=(stop_event,), daemon=True
        )
        worker_thread.start()

    return RunningService(
        url=url,
        server=server,
        stop_event=stop_event,
        worker_thread=worker_thread,
    )
