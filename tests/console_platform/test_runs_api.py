"""API-18 `GET /api/v1/runs/{run_id}`：Console 侧的 Run 只读投影。

真实边界：真实 Console HTTP/session → 真实 PostgreSQL（`runtime.run_record` +
`runtime.canonical_event`）。本视图**不转调 Runtime**（它没有对外读接口），走的是
`harness-data.md` 允许的跨 Owner Schema 只读聚合投影。

两条硬约束在本文件里各有用例钉住：**租户谓词**（跨租户与不存在同码）与
**只投影结构、不投影内容**（`input_text` 与事件 payload 都不外传）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_engine, get_session_factory
from muad_console_platform.infrastructure.repositories.run_query_repository import TIMELINE_LIMIT
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from console_platform.conftest import (
    TenantContext,  # noqa: F401  (fixture re-export)
    )

#: 刻意取一个不可能出现在元数据里的串，用来证明它没被投影出去。
SECRET_INPUT = "用户原话-do-not-leak-9f2c"
SECRET_PAYLOAD = "payload-do-not-leak-71ab"

_INSERT_AGENT = text(
    """
    INSERT INTO control.agent_definition
        (id, tenant_id, key, name, description, instructions, model_id,
         runtime_config_json, revision, enabled)
    VALUES (:agent_id, :tenant_id, :key, :agent_name, NULL, '', :model_id,
            '{}'::jsonb, 1, true)
    """
)
_INSERT_PLATFORM_USER = text(
    """
    INSERT INTO control.platform_user (id, tenant_id, user_code, display_name, status)
    VALUES (:user_id, :tenant_id, :user_code, :user_name, 'ACTIVE')
    """
)
_INSERT_CONVERSATION = text(
    """
    INSERT INTO runtime.conversation (id, tenant_id, user_id, agent_id)
    VALUES (:conversation_id, :tenant_id, :user_id, :agent_id)
    """
)
_INSERT_RUN = text(
    """
    INSERT INTO runtime.run_record
        (id, tenant_id, conversation_id, user_id, agent_id, status, input_text,
         trace_id, start_time, end_time, cancel_requested, error_code, error_message)
    VALUES (:run_id, :tenant_id, :conversation_id, :user_id, :agent_id, :status,
            :input_text, :trace_id, now(), now(), false, :error_code, :error_message)
    """
)
_INSERT_EVENT = text(
    """
    INSERT INTO runtime.canonical_event
        (id, tenant_id, conversation_id, run_id, seq, stream_type, event_type, payload_json)
    VALUES (:event_id, :tenant_id, :conversation_id, :run_id, :seq, :stream_type,
            :event_type, cast(:payload AS jsonb))
    """
)

_CLEANUP = tuple(
    statement.replace("tenant_id = :tenant_id", "tenant_id LIKE :tenant_prefix")
    for statement in (
        "DELETE FROM runtime.canonical_event WHERE tenant_id = :tenant_id",
        "DELETE FROM runtime.run_record WHERE tenant_id = :tenant_id",
        "DELETE FROM runtime.conversation WHERE tenant_id = :tenant_id",
        "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
        "DELETE FROM control.agent_definition WHERE tenant_id = :tenant_id",
    )
)


@dataclass(frozen=True)
class RunSeed:
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    agent_name: str
    user_name: str
    trace_id: str


@pytest.fixture(autouse=True)
async def cleanup(tenant: TenantContext) -> AsyncIterator[None]:
    """按 `tenant_id` 前缀清干净：跨租户用例会再落一份 `<本租户>-other` 的行。

    `test-<uuid>` 里没有 LIKE 通配符，前缀匹配是本文件自己造的租户的精确边界。
    """
    yield
    session_factory = get_session_factory()
    async with session_factory() as session:
        for statement in _CLEANUP:
            await session.execute(text(statement), {"tenant_prefix": f"{tenant.tenant_id}%"})
        await session.commit()


async def _seed_run(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    tenant_id: str | None = None,
    status: str = "COMPLETED",
    error_code: str | None = None,
    error_message: str | None = None,
) -> RunSeed:
    owner = tenant_id or tenant.tenant_id
    agent_id, user_id, conversation_id, run_id = (uuid.uuid4() for _ in range(4))
    seed = RunSeed(
        run_id=run_id,
        conversation_id=conversation_id,
        agent_name=f"Run Agent {run_id.hex[:6]}",
        user_name=f"Run User {run_id.hex[:6]}",
        trace_id=f"trace-{run_id.hex[:12]}",
    )
    await session.execute(
        _INSERT_AGENT,
        {
            "agent_id": agent_id,
            "tenant_id": owner,
            "key": f"run-agent-{agent_id.hex[:8]}",
            "agent_name": seed.agent_name,
            "model_id": tenant.model_id,
        },
    )
    await session.execute(
        _INSERT_PLATFORM_USER,
        {
            "user_id": user_id,
            "tenant_id": owner,
            "user_code": f"run-user-{user_id.hex[:8]}",
            "user_name": seed.user_name,
        },
    )
    await session.execute(
        _INSERT_CONVERSATION,
        {
            "conversation_id": conversation_id,
            "tenant_id": owner,
            "user_id": user_id,
            "agent_id": agent_id,
        },
    )
    await session.execute(
        _INSERT_RUN,
        {
            "run_id": run_id,
            "tenant_id": owner,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "status": status,
            "input_text": SECRET_INPUT,
            "trace_id": seed.trace_id,
            "error_code": error_code,
            "error_message": error_message,
        },
    )
    return seed


async def _seed_event(
    session: AsyncSession,
    tenant: TenantContext,
    seed: RunSeed,
    *,
    seq: int,
    event_type: str,
    stream_type: str | None = None,
) -> None:
    await session.execute(
        _INSERT_EVENT,
        {
            "event_id": uuid.uuid4(),
            "tenant_id": tenant.tenant_id,
            "conversation_id": seed.conversation_id,
            "run_id": seed.run_id,
            "seq": seq,
            "stream_type": stream_type,
            "event_type": event_type,
            "payload": f'{{"text": "{SECRET_PAYLOAD}"}}',
        },
    )


async def _run(tenant: TenantContext, **kwargs: object) -> RunSeed:
    async with get_session_factory()() as session:
        async with session.begin():
            return await _seed_run(session, tenant, **kwargs)  # type: ignore[arg-type]


async def test_run_detail_projects_metadata_and_structural_outline(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seed = await _run(tenant)
    async with get_session_factory()() as session:
        async with session.begin():
            for seq, (event_type, stream_type) in enumerate(
                (
                    ("run.created", None),
                    ("message.delta", "message.delta"),
                    ("tool.started", "tool.started"),
                    ("message.delta", "message.delta"),
                    ("run.completed", None),
                ),
                start=1,
            ):
                await _seed_event(
                    session, tenant, seed, seq=seq, event_type=event_type, stream_type=stream_type
                )

    response = await client.get(f"/api/v1/runs/{seed.run_id}")
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["run_id"] == str(seed.run_id)
    assert data["status"] == "COMPLETED"
    # 名称在同一 SQL 内 JOIN 补齐（前端不该拿裸 UUID 当标签）
    assert data["agent_name"] == seed.agent_name
    assert data["actor_name"] == seed.user_name
    assert data["trace_id"] == seed.trace_id
    assert data["timeline_truncated"] is False

    # 轮廓剔除流式增量（token 级，行数可达数千），其余按 seq 升序
    assert [item["event_type"] for item in data["timeline"]] == [
        "run.created",
        "tool.started",
        "run.completed",
    ]
    assert [item["seq"] for item in data["timeline"]] == [1, 3, 5]


async def test_run_detail_does_not_leak_input_text_or_event_payload(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """Console 至今零暴露对话原文；这条把「不回退」钉住，而不是靠约定。"""
    seed = await _run(tenant)
    async with get_session_factory()() as session:
        async with session.begin():
            await _seed_event(
                session, tenant, seed, seq=1, event_type="run.created", stream_type=None
            )

    response = await client.get(f"/api/v1/runs/{seed.run_id}")
    assert response.status_code == 200
    assert SECRET_INPUT not in response.text
    assert SECRET_PAYLOAD not in response.text
    assert "input_text" not in response.text


async def test_run_detail_is_tenant_scoped(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """跨租户与不存在同码：不泄漏「这个 id 在别的租户里存在」。

    租户取自**登录账号**（`AccountTenantId`），改 `X-Tenant-Id` 请求头不起作用——所以这里
    把 Run 落成另一个租户的行，再以本租户的账号去查，这才真正走到租户谓词。
    """
    foreign = await _run(tenant, tenant_id=f"{tenant.tenant_id}-other")
    missing = await _run(tenant)

    other = await client.get(f"/api/v1/runs/{foreign.run_id}")
    assert other.status_code == 404
    assert other.json()["code"] == "COMMON_NOT_FOUND"
    # 同时确认：同一个请求打自己的 Run 是通的（否则上面的 404 可能只是别的原因）
    own = await client.get(f"/api/v1/runs/{missing.run_id}")
    assert own.status_code == 200, own.text

    absent = await client.get(f"/api/v1/runs/{uuid.uuid4()}")
    assert absent.status_code == 404
    assert absent.json()["code"] == "COMMON_NOT_FOUND"


async def test_soft_deleted_run_is_not_found(client: AsyncClient, tenant: TenantContext) -> None:
    seed = await _run(tenant)
    async with get_session_factory()() as session:
        async with session.begin():
            await session.execute(
                text("UPDATE runtime.run_record SET is_deleted = true WHERE id = :run_id"),
                {"run_id": seed.run_id},
            )

    response = await client.get(f"/api/v1/runs/{seed.run_id}")
    assert response.status_code == 404


async def test_run_outline_reports_truncation(client: AsyncClient, tenant: TenantContext) -> None:
    """超出上限时如实置 `truncated`，不假装完整。"""
    seed = await _run(tenant)
    async with get_session_factory()() as session:
        async with session.begin():
            for seq in range(1, TIMELINE_LIMIT + 6):
                await _seed_event(
                    session, tenant, seed, seq=seq, event_type="tool.completed", stream_type=None
                )

    response = await client.get(f"/api/v1/runs/{seed.run_id}")
    data = response.json()["data"]
    assert data["timeline_truncated"] is True
    assert len(data["timeline"]) == TIMELINE_LIMIT


async def test_run_detail_issues_a_bounded_number_of_queries(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """详情 + 轮廓 = 两条往返；名称靠 JOIN 补齐，不得逐行回查（N+1 守卫）。"""
    seed = await _run(tenant)
    async with get_session_factory()() as session:
        async with session.begin():
            for seq in range(1, 6):
                await _seed_event(
                    session, tenant, seed, seq=seq, event_type="tool.completed", stream_type=None
                )

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", _record)
    try:
        response = await client.get(f"/api/v1/runs/{seed.run_id}")
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert response.status_code == 200
    touching = [s for s in statements if "runtime.run_record" in s or "canonical_event" in s]
    assert len(touching) == 2, statements
