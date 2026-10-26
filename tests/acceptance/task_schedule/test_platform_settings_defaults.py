"""[S-02] 保存 `task.max_attempts` 后：新建 Task 用新默认，**既有 Task 行不被改写**。

真实边界（全部不 mock）：
- **真实 Console API**：`PUT /api/v1/platform-settings`（真实登录会话 + 真实 CSRF）写一行
  `control.platform_setting` 新版本；
- **真实 PostgreSQL**：`task.task_execution` 的 `max_attempts` / `deadline_at` 逐行回读；
- **真实 Worker 进程**：新 Task 由 Worker 的真实任务创建边界取一次平台设置快照。

与 E-19（`tests/agent_worker/test_task_defaults_from_settings.py`，integration 层）的分工：本用例是
**E2E 层级**——保存走真实 Console API、任务走真实 Worker 进程（真实 lease/claim），不共用进程内夹具。
"""

from __future__ import annotations

import time
import uuid
from typing import Any, cast

import httpx
from muad_agent_runtime.application.task_client import WorkerTaskClient
from sqlalchemy import text

from .environment import (
    CONSOLE_ADMIN_PASSWORD,
    CONSOLE_ADMIN_USERNAME,
    INTERNAL_TOKEN,
    PLATFORM_SETTINGS,
    LiveStack,
    run_async,
    run_db,
)
from .helpers import submission_context

SETTINGS_URL = "/api/v1/platform-settings"
INTERNAL_SETTINGS_URL = "/internal/v1/platform-settings"
SEEDED_REVISION = 1
NEW_MAX_ATTEMPTS = 7
#: 保存同步落库（PUT 返回时已 commit），等待窗口只用来兜住跨进程可见性抖动；
#: 等不到就说明「保存后的快照可见性」本身有缺口，必须显式失败而不是继续用旧默认。
SNAPSHOT_VISIBLE_TIMEOUT_SEC = 30.0


def _await_snapshot_revision(live_stack: LiveStack, revision: int) -> None:
    """等内部快照端点看到刚保存的版本（Worker 取快照走的就是这条通道）。"""
    deadline = time.monotonic() + SNAPSHOT_VISIBLE_TIMEOUT_SEC
    observed: Any = None
    while time.monotonic() < deadline:
        response = httpx.get(
            f"{live_stack.console_url}{INTERNAL_SETTINGS_URL}",
            headers={
                "X-Tenant-Id": live_stack.tenant_id,
                "X-Internal-Service": INTERNAL_TOKEN,
                "X-Caller-Service": "worker",
            },
            timeout=10.0,
        )
        if response.status_code == 200:
            observed = (response.json().get("data") or {}).get("revision")
            if observed == revision:
                return
        time.sleep(0.2)
    raise AssertionError(f"保存后内部快照未收敛到 revision={revision}（最近观测 {observed!r}）")


def _login_admin(http: httpx.Client, live_stack: LiveStack) -> str:
    """真实登录：栈内 Console 下发 `muad_session` + `muad_csrf`；返回 CSRF 供非安全方法带头。"""
    response = http.post(
        f"{live_stack.console_url}/api/v1/auth/login",
        json={"username": CONSOLE_ADMIN_USERNAME, "password": CONSOLE_ADMIN_PASSWORD},
        headers={"X-Tenant-Id": live_stack.tenant_id},
    )
    assert response.status_code == 200, response.text
    csrf = http.cookies.get("muad_csrf")
    assert csrf, "登录必须下发 muad_csrf"
    assert http.cookies.get("muad_session"), "登录必须下发 muad_session"
    return str(csrf)


def _submit_task(live_stack: LiveStack, probe: str) -> str:
    """经真实 Worker 的任务创建边界提交一个新 Task（真实 lease/claim 语义）。"""
    context, resolved = submission_context(live_stack)

    async def submit() -> dict[str, Any]:
        client = WorkerTaskClient(live_stack.worker_url, service_token=INTERNAL_TOKEN)
        try:
            return await client.submit_task(
                context, call_id="standalone-call", skill=resolved["skill"], input_data={"probe": probe}
            )
        finally:
            await client.aclose()

    submitted = run_async(submit)
    assert submitted["status"] == "QUEUED", submitted
    return str(submitted["task_id"])


def _task_fields(live_stack: LiveStack, task_id: str) -> dict[str, Any]:
    """逐行回读 `task.task_execution` 的默认承载列（真实 PostgreSQL）。"""
    tenant = live_stack.tenant_id

    async def query(factory: Any) -> dict[str, Any]:
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT max_attempts, deadline_at FROM task.task_execution "
                        "WHERE tenant_id = :tenant AND id = :task_id"
                    ),
                    {"tenant": tenant, "task_id": uuid.UUID(task_id)},
                )
            ).one()
        return {"max_attempts": int(row[0]), "deadline_at": row[1]}

    return cast(dict[str, Any], run_db(query))


def test_s02_save_attempts_new_task_uses_new_default_existing_row_unchanged(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    seeded = PLATFORM_SETTINGS.task.max_attempts
    assert seeded != NEW_MAX_ATTEMPTS, "本用例需要与种下默认不同的新值"

    # ① 保存前先造一个「既有 Task」，回读它的默认承载列。
    existing_id = _submit_task(live_stack, "before-save")
    existing_before = _task_fields(live_stack, existing_id)
    assert existing_before["max_attempts"] == seeded, existing_before

    # ② 经真实 Console API（真实登录 + CSRF）保存新值 ⇒ 产生新版本（revision 1 → 2）。
    csrf = _login_admin(http, live_stack)
    saved = http.put(
        f"{live_stack.console_url}{SETTINGS_URL}",
        json={"revision": SEEDED_REVISION, "settings": {"task": {"max_attempts": NEW_MAX_ATTEMPTS}}},
        headers={"X-CSRF-Token": csrf},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["revision"] == SEEDED_REVISION + 1, saved.json()
    _await_snapshot_revision(live_stack, SEEDED_REVISION + 1)

    # ③ 保存后新建的 Task 用新默认。
    new_id = _submit_task(live_stack, "after-save")
    new_row = _task_fields(live_stack, new_id)
    assert new_row["max_attempts"] == NEW_MAX_ATTEMPTS, new_row

    # ④ 既有 Task 行逐字段不被改写。
    existing_after = _task_fields(live_stack, existing_id)
    assert existing_after == existing_before, "既有 Task 行不得被改写"
