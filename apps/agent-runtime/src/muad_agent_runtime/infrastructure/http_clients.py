"""Runtime 出站 HTTP 客户端装配（design §3.7「启动/部署配置」；TASK-094）。

- **预算唯一来源是 `SharedSettings`**：池大小（连接/keepalive）与超时（连接/池/读）都从 typed
  字段读，本模块不复制默认值；非法预算在 `SharedSettings` 构造时显式失败，不做静默回落。
- **按调用类别分池**：控制（Console 短控制/认证/设置、Worker 控制命令）、模型（供应商调用，
  含摘要与记忆提炼）、MCP（工具调用）、业务出站（Gateway 交付、Worker 任务提交）各自建池、
  互不共享连接（design §3.7「短控制/…/模型/MCP/业务分池」、§4.1「短控制/SSE/模型/上传/MCP/
  业务分池」）。
- **只建本进程真实调用的池**（design §3.7「实例只创建实际需要的池」）：Runtime 是 SSE 的
  **产出方**（不做出站 stream 调用）、上传的**接收方**（不把整文件外发），所以这里只有
  control/model/mcp/egress；stream/upload 池名在这里**显式失败**，不会静默按别的池预算造一个
  没人用的池（它们的预算仍留在唯一来源里，由真正调用的进程装配）。
- **内部服务调用不走环境代理**：control/egress 打的是 Console/Worker/Gateway 的内部端点，
  `trust_env` 会把部署里的 `HTTP_PROXY` 变成内部调用的隐形路由（实测把 127.0.0.1 的交付变成
  读超时）；model/mcp 是外部调用，保持 httpx 默认（与既有各客户端同口径）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx
from muad_common import SharedSettings

#: 本进程真实调用的出站池（design §3.7 池名；「实例只创建实际需要的池」）。
RUNTIME_HTTP_POOLS: tuple[str, ...] = ("control", "model", "mcp", "egress")

HttpPoolName = Literal["control", "model", "mcp", "egress"]

#: 内部服务池：不打部署环境的出站代理（见模块 docstring）。
_INTERNAL_POOLS: tuple[str, ...] = ("control", "egress")


def _require_runtime_pool(pool: str) -> None:
    """本进程不建的池显式失败：不静默按别的池预算造一个没人调用的池（design §3.7）。"""
    if pool not in RUNTIME_HTTP_POOLS:
        raise ValueError(
            f"runtime has no {pool!r} pool: this process makes no {pool!r} calls "
            f"(expected one of {RUNTIME_HTTP_POOLS})"
        )


def pool_limits(settings: SharedSettings, pool: HttpPoolName) -> httpx.Limits:
    """池连接预算：connections/keepalive 直接取自启动设置（不复制默认值）。"""
    _require_runtime_pool(pool)
    if pool == "control":
        return httpx.Limits(
            max_connections=settings.control_http_max_connections,
            max_keepalive_connections=settings.control_http_max_keepalive,
        )
    if pool == "model":
        return httpx.Limits(
            max_connections=settings.model_http_max_connections,
            max_keepalive_connections=settings.model_http_max_keepalive,
        )
    if pool == "mcp":
        return httpx.Limits(
            max_connections=settings.mcp_http_max_connections,
            max_keepalive_connections=settings.mcp_http_max_keepalive,
        )
    return httpx.Limits(
        max_connections=settings.egress_http_max_connections,
        max_keepalive_connections=settings.egress_http_max_keepalive,
    )


def pool_timeout(settings: SharedSettings, pool: HttpPoolName) -> httpx.Timeout:
    """池期限预算：连接/池超时全进程统一，读超时按调用类别（§3.7 `*_HTTP_READ_TIMEOUT_SEC`）。"""
    _require_runtime_pool(pool)
    if pool == "control":
        read = settings.control_http_read_timeout_sec
    elif pool == "model":
        read = settings.model_http_read_timeout_sec
    elif pool == "mcp":
        read = settings.mcp_http_read_timeout_sec
    else:
        read = settings.egress_http_read_timeout_sec
    return httpx.Timeout(
        read, connect=settings.http_connect_timeout_sec, pool=settings.http_pool_timeout_sec
    )


def build_pool_client(settings: SharedSettings, pool: HttpPoolName) -> httpx.AsyncClient:
    """按启动设置建一个池客户端；预算与期限都在这里一次性注入，调用方不再各写一份。"""
    return httpx.AsyncClient(
        timeout=pool_timeout(settings, pool),
        limits=pool_limits(settings, pool),
        trust_env=pool not in _INTERNAL_POOLS,
    )


@dataclass(frozen=True, slots=True)
class RuntimeHttpPools:
    """Runtime 进程的出站池：装配根建、装配根关，调用方只借用（不各自再造一个池）。"""

    control: httpx.AsyncClient
    model: httpx.AsyncClient
    mcp: httpx.AsyncClient
    egress: httpx.AsyncClient

    async def aclose(self) -> None:
        await self.control.aclose()
        await self.model.aclose()
        await self.mcp.aclose()
        await self.egress.aclose()


def build_runtime_http_pools(settings: SharedSettings) -> RuntimeHttpPools:
    """装配根建池入口：只建 `RUNTIME_HTTP_POOLS` 声明的池（design §3.7）。"""
    return RuntimeHttpPools(
        control=build_pool_client(settings, "control"),
        model=build_pool_client(settings, "model"),
        mcp=build_pool_client(settings, "mcp"),
        egress=build_pool_client(settings, "egress"),
    )
