"""[B-06] 认证验收环境与租户级种子：真实 Console 进程 + 真实 PostgreSQL。

真实边界：独立 uvicorn 子进程（真实 HTTP）+ 真实 PostgreSQL 逐行回读；种子里的会话
**必须真能带着明文令牌请求成功**，否则后续场景（续期/撤销/越权）都建在沙地上。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.acceptance.console_auth_flow.environment import (
    ADMIN_USERNAME,
    BUILDER_USERNAME,
    DISABLED_USERNAME,
    LOCKED_USERNAME,
    TENANT,
    AuthStack,
    clear_engine_caches,
    count_tenant_rows,
    count_tenant_sessions,
    purge_tenant,
    start_auth_stack,
    stop_auth_stack,
    wait_ready,
)

EXPECTED_ROWS = {"control.console_account": 4}
EXPECTED_SESSIONS = 2


@pytest.fixture(scope="module")
def auth_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[AuthStack]:
    root = tmp_path_factory.mktemp("console-auth-acceptance")
    stack, processes = start_auth_stack(Path(root))
    try:
        yield stack
    finally:
        stop_auth_stack(processes)
        orphans = [
            process.name
            for process in processes
            if process._process is not None and process._process.poll() is None
        ]
        clear_engine_caches()
        purge_tenant()
        assert orphans == [], f"收尾后仍有未退出进程（孤儿）: {orphans}"


async def test_b06_stack_boots_and_dependencies_are_ready(auth_stack: AuthStack) -> None:
    """真实进程 + 真实依赖：`/healthz` 存活、`/readyz` 依赖就绪（探针经 api-kit 封套）。"""
    healthz = await wait_ready(f"{auth_stack.console_url}/healthz")
    assert healthz.json()["data"]["status"] == "ok"
    readyz = await wait_ready(f"{auth_stack.console_url}/readyz")
    assert readyz.json()["data"]["status"] == "ready", readyz.text


def test_b06_seed_produces_expected_rows(auth_stack: AuthStack) -> None:
    """种子逐表回读：共享开发库里还有其它租户数据，能取到**精确**数目即证明租户隔离生效。"""
    for table, expected in EXPECTED_ROWS.items():
        assert count_tenant_rows(table) == expected, f"{table} 行数与种子不符"
    assert count_tenant_sessions() == EXPECTED_SESSIONS, "会话行数与种子不符"


async def test_b06_seeded_session_token_actually_authenticates(auth_stack: AuthStack) -> None:
    """种子会话可用：库内只存 sha256，但拿种子返回的**明文令牌**发请求必须通过。"""
    async with httpx.AsyncClient(
        base_url=auth_stack.console_url,
        timeout=10.0,
        cookies={"muad_session": auth_stack.seed.builder_token},
    ) as client:
        response = await client.get("/api/v1/auth/me", headers={"X-Tenant-Id": TENANT})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["id"] == str(auth_stack.seed.builder_id)
    assert data["username"] == BUILDER_USERNAME


async def test_b06_seeded_edge_accounts_are_usable_bounds(auth_stack: AuthStack) -> None:
    """边界样本齐备且可辨识：禁用账号登录失败、锁定账号返回 423、ADMIN 可登录。"""
    async with httpx.AsyncClient(base_url=auth_stack.console_url, timeout=10.0) as client:
        headers = {"X-Tenant-Id": TENANT}
        disabled = await client.post(
            "/api/v1/auth/login",
            json={"username": DISABLED_USERNAME, "password": auth_stack.seed.disabled_password},
            headers=headers,
        )
        assert disabled.status_code == 401
        assert disabled.json()["code"] == "INVALID_CREDENTIALS"

        locked = await client.post(
            "/api/v1/auth/login",
            json={"username": LOCKED_USERNAME, "password": auth_stack.seed.locked_password},
            headers=headers,
        )
        assert locked.status_code == 423
        assert locked.json()["code"] == "ACCOUNT_LOCKED"

        admin = await client.post(
            "/api/v1/auth/login",
            json={"username": ADMIN_USERNAME, "password": auth_stack.seed.admin_password},
            headers=headers,
        )
        assert admin.status_code == 200
        assert admin.json()["data"]["role"] == "ADMIN"


def test_b06_purge_is_idempotent_and_leaves_no_residue(auth_stack: AuthStack) -> None:
    """清理幂等且清空本模块全部租户级表（含本模块产生的审计行）。"""
    assert count_tenant_rows("control.console_account") > 0, "清理前应仍有种子数据（保证断言灵敏度）"

    purge_tenant()
    purge_tenant()

    for table in (*EXPECTED_ROWS, "control.config_audit_log"):
        assert count_tenant_rows(table) == 0, f"{table} 清理后仍有残留"
    assert count_tenant_sessions() == 0, "control.console_session 清理后仍有残留"
