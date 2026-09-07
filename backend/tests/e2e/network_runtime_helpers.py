"""TASK-018 真实 TCP 断连 helpers：uvicorn loopback 真服务。

不用 ASGITransport 代替断连（RISK-LIFE-04）：uvicorn 跑在 127.0.0.1 临时端口，
真实 TCP 语义；客户端 httpx 直连。server 生命周期由调用方管理、有界启停。
"""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any, Self

import uvicorn


class TcpAppServer:
    """单个 FastAPI 应用的真实 TCP 服务（async 上下文管理启停）。"""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None
        self.base_url = ""

    async def __aenter__(self) -> Self:
        config = uvicorn.Config(
            self._app, host="127.0.0.1", port=0, log_level="warning"
        )
        server = uvicorn.Server(config)
        self._server = server
        self._task = asyncio.create_task(server.serve())
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started, "uvicorn 真服务启动超时"
        sockets = (server.servers[0].sockets if server.servers else [])
        assert sockets, "uvicorn 无监听 socket"
        port = sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._server is not None and self._task is not None
        self._server.should_exit = True
        try:
            await asyncio.wait_for(self._task, timeout=15)
        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()
