"""容量验收探针 fixture（TASK-106）：真实模型 HTTP / 企微 SDK 协议 / 故障代理。

本目录的验收用例（TASK-107/110/111/116）从这些 fixture 取**真实 socket 探针**：
`model_probe` 是真实 HTTP 模型服务，`wecom_probe` 是真实 TLS/WSS 企微协议服务，
`fault_proxy_factory` 按调用方给定的上游起真实 TCP 故障代理。fixture 只做生命周期
（启动/回收），行为编排与断言仍由用例显式驱动；探针故障一律显式计数，不静默吞掉。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import pytest

from tests.acceptance.capacity.probes import (
    FaultProxy,
    ModelProbeServer,
    WeComProbeServer,
)

#: 容量验收默认机器人（用例可自行构造 `WeComProbeServer` 覆盖）。
DEFAULT_BOTS: dict[str, str] = {"bot-capacity-1": "capacity-probe-secret-1"}


@pytest.fixture()
def model_probe() -> Iterator[ModelProbeServer]:
    """真实 HTTP 模型探针：可编程延迟/增量/usage/429/5xx/断流/应答丢失。"""
    with ModelProbeServer() as server:
        yield server


@pytest.fixture()
async def wecom_probe() -> AsyncIterator[WeComProbeServer]:
    """真实 WSS 企微协议探针：官方 SDK 按真实协议连接，故障注入可观测。"""
    async with WeComProbeServer(expected_bots=dict(DEFAULT_BOTS)) as server:
        yield server


@pytest.fixture()
def fault_proxy_factory() -> Iterator[Callable[..., FaultProxy]]:
    """真实 TCP 故障代理工厂：按上游起代理，用例结束时统一回收。"""
    created: list[FaultProxy] = []

    def make(upstream_host: str, upstream_port: int, **kwargs: Any) -> FaultProxy:
        proxy = FaultProxy(upstream_host, upstream_port, **kwargs)
        proxy.start()
        created.append(proxy)
        return proxy

    try:
        yield make
    finally:
        for proxy in reversed(created):
            proxy.stop()
