"""[每次运行独立数据存储] 验收基座：本会话独占一个空库 + 一个独立 Redis DB。

**为什么需要**：验收各域共用一份 dev 库时，任务队列的 claim 与投递的选取都**不带租户谓词**
（Worker 是无状态通用工作池，任务行自带 `tenant_id`；`worker/claimer.py:20-27`、
`delivery/service.py:120-139`），而各栈的出站渠道端点又是**栈级 env**（`CHANNEL_PROBE_URL`
让 Gateway 换用 HTTP 探针适配器，`im-gateway/main.py:35-39`）。于是别的套件（或 dev 服务）
留下的可领取/待投递行会被本栈领走、并按**本栈**端点投递 —— 表现为「我的探针里出现别人的投递」，
断言随机红且**单跑绿、串跑红**（2026-10-02 实测：`verify-e2e` 的 14 条 verifier 顺序执行下
task_schedule 的投递用例必红，单独跑全绿）。给每次运行一份空库 + 独立 Redis DB 后，这类
跨套件干扰在结构上不可能发生。

建库 / 迁移 / 清理的**唯一实现**在 `tests/acceptance/datastores.py`：pytest 侧（本文件）与
Playwright 侧（`e2e/support/isolated-datastores.ts` 调其 CLI）共用，避免两份逻辑漂移。

**逃生阀**：`MUAD_ACCEPTANCE_SHARED_DB=1` 时不建库，沿用环境里现有的库（仅用于排查对比）。
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from muad_common import SharedSettings

from tests.acceptance.datastores import (
    DatastoreUnavailableError,
    create_datastore,
    drop_datastore,
)

_MANAGED_ENV = ("DATABASE_URL", "REDIS_URL")


@pytest.fixture(scope="session", autouse=True)
def isolated_datastores() -> Iterator[None]:
    """本会话独占一个空库 + 独立 Redis DB；结束时丢弃。"""
    if os.environ.get("MUAD_ACCEPTANCE_SHARED_DB") == "1":
        yield
        return

    # 没有基准 DATABASE_URL ⇒ 本轮的选取**根本不需要 DB**，不该建库。
    # 实例：`tests/acceptance/dfx/test_dfx_layer_baseline.py` 会用一个**净化过的环境**
    # （显式 pop 掉 DATABASE_URL/REDIS_URL）跑内层 `pytest -m unit tests/acceptance/dfx`，
    # 以证明「单元层可在无 DB 下运行」。本 fixture 是 autouse 且在更上层的 conftest 里，
    # 内层照样会加载它 —— 若无条件建库，就会以
    #   `RuntimeError: DATABASE_URL is required but not configured`
    # 在 session setup 直接报错，把那条例用正要证明的性质打掉（2026-10-02 CI backend 实测）。
    #
    # 这与「静默退回共享库」是两回事：只要基准 DATABASE_URL 存在，就一律建独立库、
    # 绝不退回共享；这里只是「没有库可隔离」时不做无意义的事。
    if not SharedSettings().database_url:
        yield
        return

    saved = {key: os.environ.get(key) for key in _MANAGED_ENV}
    try:
        info = create_datastore()
    except DatastoreUnavailableError as exc:  # pragma: no cover - 环境前提
        pytest.fail(str(exc))

    # 必须同时改 **os.environ**：各域套件的 seed 是进程内直连 DB、走 SharedSettings()，
    # 只注入子进程会出现「seed 写进旧库、服务读新库」的裂脑，每个域都会红。
    os.environ["DATABASE_URL"] = info["database_url"]
    os.environ["REDIS_URL"] = info["redis_url"]
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        drop_datastore(info["name"])
