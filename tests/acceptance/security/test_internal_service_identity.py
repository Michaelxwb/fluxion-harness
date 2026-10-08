"""E-11：内部服务身份、结果回流绑定与 Console 租户投影（真实 HTTP + PG，无业务 mock）。

关键断言（backend design §2.5.2 / RULE-09）：
- 无/错服务身份：Runtime 结果回流、Runtime Admin Run、Console 内部 resolve 一律 403，无数据；
- actor/run/operation/task/snapshot hash 不匹配与跨租户：403 绑定错误、不写 inbox / 不写回执事件，
  且“另一租户存在”与“不存在”同码同形（不泄露存在性）；
- Console 租户只取登录账号：伪造 `X-Tenant-Id` 指向另一租户也读不到该租户的 Run/operations。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.acceptance.security.conftest import (
    OTHER_TENANT,
    SeededOperation,
    seed_accepted_operation,
)
from tests.acceptance.task_schedule.environment import (
    CONSOLE_ADMIN_PASSWORD,
    CONSOLE_ADMIN_USERNAME,
    INTERNAL_TOKEN,
    TENANT,
    LiveStack,
    run_db,
)

pytestmark = pytest.mark.e2e

TOOL_RESULTS_PATH = "/internal/tool-results"
ADMIN_RUNS_PATH = "/internal/admin/runs"
RESOLVE_DEFINITION_PATH = "/internal/runtime/resolve-definition"


def _service_headers(tenant_id: str) -> dict[str, str]:
    return {"X-Tenant-Id": tenant_id, "X-Internal-Service": INTERNAL_TOKEN}


def _result_body(seeded: SeededOperation, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": 1,
        "event_id": str(uuid.uuid4()),
        "operation_id": str(seeded.operation_id),
        "task_id": str(seeded.task_id),
        "task_event_seq": 1,
        "source_run_id": str(seeded.run_id),
        "actor_user_id": str(seeded.actor_user_id),
        "task_snapshot_hash": seeded.task_snapshot_hash,
        "terminal_status": "COMPLETED",
        "completed_at": datetime.now(UTC).isoformat(),
        "result": {"value": "security-ok"},
    }
    body.update(overrides)
    return body


def test_e11_internal_endpoints_require_service_identity(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    """[E-11] 无服务身份或伪造身份：Runtime/Console 内部端点一律 403 FORBIDDEN。"""
    probe_id = str(uuid.uuid4())
    body = {
        "schema_version": 1,
        "event_id": probe_id,
        "operation_id": probe_id,
        "task_id": probe_id,
        "task_event_seq": 1,
        "source_run_id": probe_id,
        "actor_user_id": probe_id,
        "task_snapshot_hash": "sha256:" + "a" * 64,
        "terminal_status": "COMPLETED",
        "completed_at": datetime.now(UTC).isoformat(),
        "result": {"value": "probe"},
    }
    identities = ({"X-Tenant-Id": TENANT}, {"X-Tenant-Id": TENANT, "X-Internal-Service": "forged"})
    for headers in identities:
        denied = http.post(live_stack.runtime_url + TOOL_RESULTS_PATH, json=body, headers=headers)
        assert denied.status_code == 403, denied.text
        assert denied.json()["code"] == "FORBIDDEN"
        assert denied.json().get("data") is None

        denied = http.get(live_stack.runtime_url + ADMIN_RUNS_PATH, headers=headers)
        assert denied.status_code == 403, denied.text
        assert denied.json()["code"] == "FORBIDDEN"

        denied = http.post(
            live_stack.console_url + RESOLVE_DEFINITION_PATH,
            json={"agent_id": str(uuid.uuid4()), "actor_user_id": str(uuid.uuid4())},
            headers=headers,
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["code"] == "FORBIDDEN"


def test_e11_result_binding_mismatches_are_rejected_without_inbox(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    """[E-11] 绑定不匹配/跨租户拒绝且零写入；不存在的 operation 与另一租户的存在同码同形。"""
    seeded = run_db(lambda factory: seed_accepted_operation(factory, tenant=TENANT))
    other = run_db(lambda factory: seed_accepted_operation(factory, tenant=OTHER_TENANT))

    def post(body: dict[str, object], tenant_id: str) -> httpx.Response:
        return http.post(
            live_stack.runtime_url + TOOL_RESULTS_PATH, json=body, headers=_service_headers(tenant_id)
        )

    attacks = {
        "actor": _result_body(seeded, actor_user_id=str(uuid.uuid4())),
        "run": _result_body(seeded, source_run_id=str(uuid.uuid4())),
        "task": _result_body(seeded, task_id=str(uuid.uuid4())),
        "hash": _result_body(seeded, task_snapshot_hash="sha256:" + "c" * 64),
    }
    for name, attack in attacks.items():
        rejected = post(attack, TENANT)
        assert rejected.status_code == 403, f"{name}: {rejected.text}"
        assert rejected.json()["code"] == "TOOL_RESULT_BINDING_MISMATCH", name

    absent = post(_result_body(seeded, operation_id=str(uuid.uuid4())), TENANT)
    cross_tenant = post(_result_body(other), TENANT)
    assert absent.status_code == cross_tenant.status_code == 403
    assert absent.json()["code"] == cross_tenant.json()["code"] == "TOOL_RESULT_BINDING_MISMATCH"
    assert absent.json().get("data") is None and cross_tenant.json().get("data") is None

    async def side_effects(factory: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
        from muad_agent_runtime.infrastructure.models.async_tools import ToolResultInbox
        from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent

        async with factory() as session:
            inbox = await session.scalar(
                select(func.count())
                .select_from(ToolResultInbox)
                .where(ToolResultInbox.operation_id.in_([seeded.operation_id, other.operation_id]))
            )
            events = await session.scalar(
                select(func.count())
                .select_from(CanonicalEvent)
                .where(CanonicalEvent.run_id.in_([seeded.run_id, other.run_id]))
            )
        return int(inbox or 0), int(events or 0)

    assert run_db(side_effects) == (0, 0), "被拒绝的请求不得落 inbox 或回执事件"

    # 正面对照：完全匹配的请求必须被受理——证明上面的 403 不是端点整体不可用造成的假象。
    accepted = post(_result_body(seeded), TENANT)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["persisted"] is True
    assert run_db(side_effects) == (1, 1)


def test_e11_console_tenant_comes_from_login_account_not_header(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    """[E-11] 伪造租户头不改变 Console 读取面：租户只取登录账号。"""
    own = run_db(lambda factory: seed_accepted_operation(factory, tenant=TENANT))
    foreign = run_db(lambda factory: seed_accepted_operation(factory, tenant=OTHER_TENANT))

    login = http.post(
        live_stack.console_url + "/api/v1/auth/login",
        json={"username": CONSOLE_ADMIN_USERNAME, "password": CONSOLE_ADMIN_PASSWORD},
        headers={"X-Tenant-Id": TENANT},
    )
    assert login.status_code == 200, login.text

    spoofed = {"X-Tenant-Id": OTHER_TENANT}
    foreign_detail = http.get(
        live_stack.console_url + f"/api/v1/runs/{foreign.run_id}", headers=spoofed
    )
    assert foreign_detail.status_code == 404, foreign_detail.text
    assert foreign_detail.json()["code"] == "COMMON_NOT_FOUND"
    assert foreign.run_trace_id not in foreign_detail.text

    own_detail = http.get(live_stack.console_url + f"/api/v1/runs/{own.run_id}", headers=spoofed)
    assert own_detail.status_code == 200, own_detail.text
    assert own_detail.json()["data"]["run_id"] == str(own.run_id)
    assert "security-canary-input" not in own_detail.text
    assert "input_text" not in own_detail.text

    foreign_ops = http.get(
        live_stack.console_url + f"/api/v1/runs/{foreign.run_id}/operations", headers=spoofed
    )
    assert foreign_ops.status_code == 404, foreign_ops.text

    own_ops = http.get(
        live_stack.console_url + f"/api/v1/runs/{own.run_id}/operations", headers=spoofed
    )
    assert own_ops.status_code == 200, own_ops.text
    data = own_ops.json()["data"]
    assert data["total"] == 1
    assert [item["operation_id"] for item in data["items"]] == [str(own.operation_id)]
