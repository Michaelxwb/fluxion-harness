"""TASK-039 验收环境 fixture：真实服务进程 + 真实依赖，缺失即 fail。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from .environment import (
    LiveStack,
    clear_engine_caches,
    cleanup,
    require,
    start_live_stack,
    stop_live_stack,
)


@pytest.fixture(scope="module", autouse=True)
def isolate_engine_caches() -> Iterator[None]:
    yield
    clear_engine_caches()


@pytest.fixture(scope="module")
def live_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveStack]:
    from muad_common import SharedSettings

    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    require("REDIS_URL", settings.redis_url)

    clear_engine_caches()
    root = tmp_path_factory.mktemp("task-schedule-acceptance")
    stack, processes = start_live_stack(Path(root))
    try:
        yield stack
    finally:
        stop_live_stack(processes)
        cleanup(database_url, stack.artifact_root)
        clear_engine_caches()


@pytest.fixture()
def http() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=30) as client:
        yield client
