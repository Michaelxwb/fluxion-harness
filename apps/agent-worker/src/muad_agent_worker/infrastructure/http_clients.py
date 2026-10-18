"""Worker 出站 HTTP 客户端装配（design §3.7「启动/部署配置」；TASK-095）。

- **预算唯一来源是 `SharedSettings`**：池大小（连接/keepalive）与超时（连接/池/读）都从 typed
  字段读，本模块不复制默认值；非法预算在 `SharedSettings` 构造时显式失败，不做静默回落。
- **按调用类别分池**：控制（Console 平台设置快照、定时解析）与业务出站（Gateway 投递、Runtime
  结果发件/收件箱/产物解析）各自建池、互不共享连接（design §4.1「短控制/…/业务分池」）。
- **只建本进程真实调用的池**（design §3.7「实例只创建实际需要的池」）：Worker 不调用 MCP、
  不上传大文件（Skill 制品经共享盘落盘、附件由 Gateway/Runtime 处理），所以这里只有
  control/egress；MCP/上传/流池的预算仍留在唯一来源里，由真正调用的进程装配。别的池名在这里
  **显式失败**，不会静默按 egress 建出一个没人用的池。
- **期限来自启动设置**：各池读超时是 typed 字段（`*_HTTP_READ_TIMEOUT_SEC`），装配根不重抄；
  业务调用另外还受 Task 冻结的绝对 deadline 约束（TASK-035）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx
from muad_common import SharedSettings

#: 本进程真实调用的出站池（design §3.7 池名；「实例只创建实际需要的池」）。
WORKER_HTTP_POOLS: tuple[str, ...] = ("control", "egress")

HttpPoolName = Literal["control", "egress"]


def _require_worker_pool(pool: str) -> None:
    """本进程不建的池显式失败：不静默按别的池预算造一个没人调用的池（design §3.7）。"""
    if pool not in WORKER_HTTP_POOLS:
        raise ValueError(
            f"worker has no {pool!r} pool: this process makes no {pool!r} calls "
            f"(expected one of {WORKER_HTTP_POOLS})"
        )


def pool_limits(settings: SharedSettings, pool: HttpPoolName) -> httpx.Limits:
    """池连接预算：connections/keepalive 直接取自启动设置（不复制默认值）。"""
    _require_worker_pool(pool)
    if pool == "control":
        return httpx.Limits(
            max_connections=settings.control_http_max_connections,
            max_keepalive_connections=settings.control_http_max_keepalive,
        )
    return httpx.Limits(
        max_connections=settings.egress_http_max_connections,
        max_keepalive_connections=settings.egress_http_max_keepalive,
    )


def pool_timeout(settings: SharedSettings, pool: HttpPoolName) -> httpx.Timeout:
    """池期限预算：连接/池超时全进程统一，读超时按调用类别（§3.7 `*_HTTP_READ_TIMEOUT_SEC`）。"""
    _require_worker_pool(pool)
    read = (
        settings.control_http_read_timeout_sec
        if pool == "control"
        else settings.egress_http_read_timeout_sec
    )
    return httpx.Timeout(
        read, connect=settings.http_connect_timeout_sec, pool=settings.http_pool_timeout_sec
    )


def build_pool_client(settings: SharedSettings, pool: HttpPoolName) -> httpx.AsyncClient:
    """按启动设置建一个池客户端；预算与期限都在这里一次性注入，调用方不再各写一份。"""
    return httpx.AsyncClient(timeout=pool_timeout(settings, pool), limits=pool_limits(settings, pool))


@dataclass(frozen=True, slots=True)
class WorkerHttpPools:
    """Worker 进程的出站池：装配根建、装配根关，调用方只借用（不各自再造一个池）。"""

    control: httpx.AsyncClient
    egress: httpx.AsyncClient

    async def aclose(self) -> None:
        await self.control.aclose()
        await self.egress.aclose()


def build_worker_http_pools(settings: SharedSettings) -> WorkerHttpPools:
    """装配根建池入口：只建 `WORKER_HTTP_POOLS` 声明的池（design §3.7）。"""
    return WorkerHttpPools(
        control=build_pool_client(settings, "control"),
        egress=build_pool_client(settings, "egress"),
    )
