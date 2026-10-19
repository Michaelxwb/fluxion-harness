"""容量验收 fixture：真实四单元独立栈（TASK-107）+ 真实协议探针（TASK-106）。

本目录的验收用例（TASK-110/111/116）从这些 fixture 取**真实组件**：
`capacity_stack` 是四单元独立栈（真实 uvicorn 子进程、共享卷、入口代理、故障进程控制；
库/Redis 复用本会话的验收独立存储 —— 见 `stack.acquire_datastore`），`model_probe` 是
真实 HTTP 模型服务，`wecom_probe` 是真实 TLS/WSS 企微协议服务，`fault_proxy_factory`
按调用方给定的上游起真实 TCP 故障代理。fixture 只做生命周期（启动/回收），行为编排与
断言仍由用例显式驱动；探针故障一律显式计数，不静默吞掉。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.acceptance.capacity.probes import (
    FaultProxy,
    ModelProbeServer,
    WeComProbeServer,
)
from tests.acceptance.capacity.stack import CapacityStack
from tests.capacity.profile import load_profile

#: 容量验收默认机器人（用例可自行构造 `WeComProbeServer` 覆盖）。
DEFAULT_BOTS: dict[str, str] = {"bot-capacity-1": "capacity-probe-secret-1"}
#: 锁定 profile：正式四单元栈的副本数/资源声明由它决定（可用 env 覆盖以跑不同 profile）。
PROFILES_DIR = Path(__file__).resolve().parents[2] / "capacity" / "profiles"
DEFAULT_PROFILE_PATH = PROFILES_DIR / "agent-capacity-webchat.example.yml"
CAPACITY_PROFILE_ENV = "MUAD_CAPACITY_PROFILE"


@pytest.fixture(scope="session")
def capacity_profile_path() -> Path:
    """本轮锁定的容量 profile 文件（`MUAD_CAPACITY_PROFILE` 可覆盖）。"""
    path = Path(os.environ.get(CAPACITY_PROFILE_ENV, str(DEFAULT_PROFILE_PATH)))
    if not path.is_file():
        raise pytest.fail(f"容量验收 profile 不存在：{path}（{CAPACITY_PROFILE_ENV} 可覆盖）")
    return path


@pytest.fixture(scope="session")
def capacity_stack(
    tmp_path_factory: pytest.TempPathFactory, capacity_profile_path: Path
) -> Iterator[CapacityStack]:
    """四单元独立验收栈：真实进程/共享卷/入口代理/故障控制，收尾只清本轮。

    库与 Redis 号位复用本会话的验收独立存储（`tests/acceptance/conftest.py` 已建），
    保证「进程内 seed 与服务子进程看同一个库」；独立运行时由栈按同一机制自建并 drop。
    """
    stack = CapacityStack(
        root=tmp_path_factory.mktemp("capacity-stack"),
        profile=load_profile(capacity_profile_path),
    )
    stack.start()
    try:
        yield stack
    finally:
        stack.teardown()


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
