"""Restate PoC worker 子进程入口（TASK-004；真实进程边界测试用）。

Restate 是 server 模型：journal 归 Restate server，Python worker 只 serve handler 端点。
本进程 = SDK app（hypercorn/HTTP2）+ 注册 deployment；Restate server 驱动 handler 执行
并持久化 journal。S-02/P-CRASH 杀 worker 后，新 worker 注册即被 server 续跑（恢复在
server 侧，无需专门 recover 模式）。

用法（`python -m tests.workflow_poc.restate_worker <mode> ...`，cwd=backend）：
- `serve <index>`：起端点 + 注册 + 打印 `READY-<index>` → 常驻直到被杀
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import threading
import time

import httpx
from hypercorn.asyncio import serve
from hypercorn.config import Config

from tests.workflow_poc.restate_app import RESTATE_ADMIN, build_app


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Restate PoC worker")
    sub = parser.add_subparsers(dest="mode", required=True)
    serve_cmd = sub.add_parser("serve", help="serve SDK endpoint + 注册 deployment")
    serve_cmd.add_argument("--index", type=int, default=0)
    serve_cmd.add_argument("--idle-seconds", type=float, default=300.0)
    serve_cmd.add_argument(
        "--port", type=int, default=0, help="固定端口（S-02 恢复：同端口重启 = 更新同一 deployment）"
    )
    return parser


def _register_deployment(uri: str) -> None:
    """admin API 注册 deployment（HTTP2 端点，无需 --use-http1.1）。"""
    res = httpx.post(
        f"{RESTATE_ADMIN}/deployments",
        json={"uri": uri},
        timeout=10.0,
    )
    if res.status_code >= 400 and res.status_code != 409:  # 409 = 已注册（幂等容忍）
        raise RuntimeError(f"register {uri} -> {res.status_code}: {res.text[:200]}")


async def _mode_serve(args: argparse.Namespace) -> int:
    port = args.port or _find_free_port()
    app = build_app()
    uri = f"http://host.docker.internal:{port}"

    stop_event = asyncio.Event()

    async def run_hypercorn() -> None:
        config = Config()
        config.bind = [f"0.0.0.0:{port}"]
        config.h2_max_concurrent_streams = 2147483647
        config.keep_alive_max_requests = 2147483647
        config.keep_alive_timeout = 2147483647
        await serve(app, config=config, mode="asgi", shutdown_trigger=stop_event.wait)

    thread = threading.Thread(target=lambda: asyncio.run(run_hypercorn()), daemon=True)
    thread.start()
    # 等端点起来再注册
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            httpx.get(f"http://localhost:{port}/discover", timeout=2.0)
            break
        except Exception:  # noqa: BLE001 — 端点尚未就绪，重试
            time.sleep(0.2)
    _register_deployment(uri)
    print(f"READY-{args.index} {uri}", flush=True)
    await asyncio.sleep(args.idle_seconds)
    stop_event.set()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.mode == "serve":
        return asyncio.run(_mode_serve(args))
    raise SystemExit(f"unknown mode: {args.mode}")


if __name__ == "__main__":
    sys.exit(main())
