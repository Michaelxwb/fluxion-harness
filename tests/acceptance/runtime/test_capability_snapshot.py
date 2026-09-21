"""[S-01][S-02][RULE-auth/mcp/secret/snapshot-001] 授权与 Snapshot 全链路验收。

真实边界：Runtime HTTP → 真实 Console HTTP（resolve-definition/resolve-credentials）→
真实 PostgreSQL；LLM/MCP 均为真实 HTTP 探针；无 dependency_overrides/mock。
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx
import sqlalchemy as sa
from muad_agent_runtime.application.run_service import build_snapshot
from muad_agent_runtime.infrastructure.models.runtime import (
    EgressAudit,
    RunInterrupt,
    RunRecord,
    RuntimeSnapshot,
    ToolCallAudit,
)
from muad_contracts import ResolveDefinitionResponse

from tests.acceptance.runtime.conftest import (
    MODEL_API_KEY,
    ROTATED_API_KEY,
    LiveStack,
    run_db,
)


def _parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        data_lines = [
            line[len("data:") :].lstrip() for line in block.splitlines() if line.startswith("data:")
        ]
        if data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events


def _run_payload(stack: LiveStack, text: str = "hello") -> dict[str, Any]:
    return {
        "agent_id": str(stack.agent_id),
        "platform_user_id": str(stack.platform_user_id),
        "channel": {"type": "WECOM", "bot_id": "bot-acc", "external_conversation_id": "ext-acc"},
        "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": text},
    }


def _resolve_over_console(stack: LiveStack, http: httpx.Client) -> ResolveDefinitionResponse:
    response = http.post(
        f"{stack.console_url}/internal/runtime/resolve-definition",
        json={
            "agent_id": str(stack.agent_id),
            "actor_user_id": str(stack.platform_user_id),
            "channel": "WECOM",
        },
        headers=stack.console_headers(),
    )
    assert response.status_code == 200, response.text
    return ResolveDefinitionResponse.model_validate(response.json()["data"])


def test_s01_effective_capability_zero_leakage_and_real_mcp_call(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    """[S-01] 未授权/禁用能力不进入 resolve/快照/LLM catalog；授权 MCP 经真实 HTTP 执行。"""
    resolved = _resolve_over_console(live_stack, http)
    keys = [skill.key for skill in resolved.skills]
    assert live_stack.skill_key in keys
    assert live_stack.disabled_skill_key not in keys

    definitions = [
        definition for server in resolved.mcp_servers for definition in server.definitions
    ]
    assert [definition["name"] for definition in definitions] == ["probe_tool_1"]

    tool_name = f"mcp::{resolved.mcp_servers[0].key}::probe_tool_1"
    os.environ["OPENAI_PROBE_TOOL_NAME"] = tool_name
    os.environ["MCP_PROBE_REQUIRE_AUTH"] = "mcp-owner-secret"
    try:
        response = http.post(
            f"{live_stack.runtime_url}/v1/runs",
            json=_run_payload(live_stack),
            headers=live_stack.runtime_headers(),
        )
    finally:
        os.environ.pop("OPENAI_PROBE_TOOL_NAME", None)
        os.environ.pop("MCP_PROBE_REQUIRE_AUTH", None)

    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    types = [event["type"] for event in events]
    assert "tool.started" in types
    assert "tool.completed" in types
    assert events[-1]["type"] == "run.completed"
    assert events[-1]["data"]["status"] == "COMPLETED"
    run_id = uuid.UUID(events[0]["run_id"])

    async def inspect(factory: Any) -> tuple[dict[str, Any], list[EgressAudit], list[ToolCallAudit]]:
        async with factory() as session:
            run = await session.get(RunRecord, run_id)
            assert run is not None and run.snapshot_id is not None
            snapshot = await session.get(RuntimeSnapshot, run.snapshot_id)
            assert snapshot is not None
            egress = (
                await session.execute(sa.select(EgressAudit).where(EgressAudit.run_id == run_id))
            ).scalars().all()
            tool_audits = (
                await session.execute(
                    sa.select(ToolCallAudit).where(ToolCallAudit.run_id == run_id)
                )
            ).scalars().all()
            return dict(snapshot.mcp_catalog_json[0]), list(egress), list(tool_audits)

    catalog, egress_rows, tool_audits = run_db(inspect)
    assert catalog["catalog_revision"] == 4
    assert catalog["catalog_hash"].startswith("sha256:")
    assert [definition["name"] for definition in catalog["definitions"]] == ["probe_tool_1"]
    assert all(live_stack.disabled_skill_key not in str(entry) for entry in catalog["definitions"])

    assert any(row.target_type == "MCP" and row.policy_decision == "ALLOW" for row in egress_rows)
    assert any(row.tool_kind == "MCP" and row.status == "OK" for row in tool_audits)


def test_s02_snapshot_freeze_and_api09_credentials_on_resume(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    """[S-02] 快照冻结（无密钥）；resume 走 API-09 读取 Owner 表当前密钥（轮换后仍成功）。"""
    os.environ["OPENAI_PROBE_REQUIRE_AUTH"] = MODEL_API_KEY
    try:
        first = http.post(
            f"{live_stack.runtime_url}/v1/runs",
            json=_run_payload(live_stack, "first"),
            headers=live_stack.runtime_headers(),
        )
    finally:
        os.environ.pop("OPENAI_PROBE_REQUIRE_AUTH", None)
    assert first.status_code == 200, first.text
    events = _parse_sse(first.text)
    assert events[-1]["data"]["status"] == "COMPLETED"
    run_id = uuid.UUID(events[0]["run_id"])
    conversation_id = uuid.UUID(events[0]["data"]["conversation_id"])

    resolved = _resolve_over_console(live_stack, http)

    async def freeze(factory: Any) -> tuple[str, str]:
        async with factory() as session:
            run = await session.get(RunRecord, run_id)
            assert run is not None and run.snapshot_id is not None
            snapshot = await session.get(RuntimeSnapshot, run.snapshot_id)
            assert snapshot is not None
            return snapshot.content_hash, str(snapshot.model_json)

    content_hash, serialized_model = run_db(freeze)
    assert "api_key" not in serialized_model
    assert MODEL_API_KEY not in serialized_model

    async def rotate(factory: Any) -> None:
        async with factory.begin() as connection:
            await connection.execute(
                sa.text("UPDATE control.model_definition SET api_key = :key WHERE id = :id"),
                {"key": ROTATED_API_KEY, "id": live_stack.model_id},
            )

    run_db(rotate)

    async def seed_waiting(factory: Any) -> uuid.UUID:
        waiting_run_id = uuid.uuid4()
        async with factory() as session:
            run = RunRecord(
                id=waiting_run_id,
                tenant_id=live_stack.tenant_id,
                conversation_id=conversation_id,
                user_id=live_stack.platform_user_id,
                agent_id=live_stack.agent_id,
                status="WAITING_INPUT",
                input_text="pending",
                trace_id=uuid.uuid4().hex,
                cancel_requested=False,
            )
            session.add(run)
            snapshot = build_snapshot(
                run_id=waiting_run_id, tenant_id=live_stack.tenant_id, resolved=resolved
            )
            session.add(snapshot)
            await session.flush()
            run.snapshot_id = snapshot.id
            session.add(
                RunInterrupt(
                    tenant_id=live_stack.tenant_id,
                    run_id=waiting_run_id,
                    conversation_id=conversation_id,
                    interrupt_type="CONFIRM",
                    prompt_text="continue?",
                    options_json=["yes", "no"],
                    status="WAITING",
                )
            )
            await session.commit()
        return waiting_run_id

    waiting_run_id = run_db(seed_waiting)
    os.environ["OPENAI_PROBE_REQUIRE_AUTH"] = ROTATED_API_KEY
    try:
        resumed = http.post(
            f"{live_stack.runtime_url}/v1/runs/{waiting_run_id}/resume",
            json={"input": {"id": "reply-1", "type": "text", "text": "yes"}},
            headers=live_stack.runtime_headers(),
        )
    finally:
        os.environ.pop("OPENAI_PROBE_REQUIRE_AUTH", None)
    assert resumed.status_code == 200, resumed.text
    resumed_events = _parse_sse(resumed.text)
    assert resumed_events[0]["type"] == "run.created"
    assert resumed_events[0]["data"]["resumed"] is True
    assert resumed_events[-1]["data"]["status"] == "COMPLETED"

    async def verify_frozen(factory: Any) -> tuple[str, str | None, str]:
        async with factory() as session:
            run = await session.get(RunRecord, run_id)
            assert run is not None and run.snapshot_id is not None
            snapshot = await session.get(RuntimeSnapshot, run.snapshot_id)
            assert snapshot is not None
            interrupt = await session.scalar(
                sa.select(RunInterrupt).where(RunInterrupt.run_id == waiting_run_id)
            )
            assert interrupt is not None
            return snapshot.content_hash, snapshot.model_json.get("api_key"), interrupt.status

    frozen_hash, frozen_key, interrupt_status = run_db(verify_frozen)
    assert (frozen_hash, frozen_key) == (content_hash, None)
    assert interrupt_status == "RESOLVED"
