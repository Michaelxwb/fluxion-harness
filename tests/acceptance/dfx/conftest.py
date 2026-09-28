"""TASK-003 DFX 可靠性验收 fixture：真实服务进程 + 真实 PG/Redis，缺失即 fail。

沿用 `tests/acceptance/runtime/conftest.py` 的 `live_stack`（module scope）与
`isolate_engine_caches`（module autouse）口径；环境原语来自 `environment.py`。
收尾在 `finally` 中执行（失败路径同样清理），并核对本租户残留为 0 行。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from .environment import (
    DfxStack,
    cleanup,
    clear_engine_caches,
    require,
    start_dfx_stack,
    stop_dfx_stack,
)


@pytest.fixture(scope="module", autouse=True)
def isolate_engine_caches() -> Iterator[None]:
    """离开本模块前清理绑定服务线程 loop 的 engine 缓存，避免污染后续 async 测试套件。"""
    yield
    clear_engine_caches()


@pytest.fixture(scope="module")
def live_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[DfxStack]:
    """真实 Console/Worker 子进程 + 真实 PG/Redis；租户与产物根钉在系统临时目录。"""
    from muad_common import SharedSettings

    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    redis_url = require("REDIS_URL", settings.redis_url)

    clear_engine_caches()
    root = Path(tmp_path_factory.mktemp("dfx-reliability"))
    stack, processes = start_dfx_stack(root)
    try:
        yield stack
    finally:
        stop_dfx_stack(processes)
        cleanup(database_url, redis_url, stack)
        clear_engine_caches()


@pytest.fixture()
def http() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=30) as client:
        yield client
