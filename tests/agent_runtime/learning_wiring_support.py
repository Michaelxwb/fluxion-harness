"""Shared fixtures for learning pipeline wiring tests."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import sqlalchemy as sa
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.application.run_service import build_snapshot
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    EvalCase,
    EvalReport,
    MemoryCommandReceipt,
    MemoryScope,
    RunRecord,
    RuntimeSnapshot,
    UserMemory,
    UserMemoryHistory,
)
from muad_contracts import ResolvedAgent, ResolveDefinitionResponse, ResolvedModel, RunStatus

BASE_URL = "http://replay.test"
REFERENCE = '{"name":"A","amount":10}'
FAMILY = "skill:demo"


class Settings:
    def __init__(self, tenant_id: str, enabled: bool = True) -> None:
        self._tenant, self._enabled = tenant_id, enabled

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        enabled = self._enabled and tenant_id == self._tenant
        # 租户策略控制回放样本门槛；测试只备了少量样本，显式降到 1。
        return PlatformSettingsSnapshot(
            revision=1,
            settings={"learning": {"learning_enabled": enabled, "eval_min_cases": 1}},
        )


def transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system = " ".join(
            item.get("content", "") for item in body["messages"] if item.get("role") == "system"
        )
        text = REFERENCE if "参考经验" in system else "已整理"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    return httpx.MockTransport(handler)


async def seed_runs(tenant: str, user: uuid.UUID, count: int = 3) -> list[uuid.UUID]:
    conversation_id = uuid.uuid4()
    run_ids: list[uuid.UUID] = []
    async with get_session_factory()() as session:
        session.add(Conversation(id=conversation_id, tenant_id=tenant, user_id=user, agent_id=uuid.uuid4()))
        await session.flush()
        seq = 0
        for _ in range(count):
            run_id = uuid.uuid4()
            run_ids.append(run_id)
            run = RunRecord(
                id=run_id,
                tenant_id=tenant,
                conversation_id=conversation_id,
                user_id=user,
                agent_id=uuid.uuid4(),
                status=RunStatus.COMPLETED,
                input_text="请输出客户 A 的 JSON",
                trace_id=f"trace-{run_id}",
                end_time=datetime.now(UTC),
                task_family=FAMILY,
            )
            session.add(run)
            await session.flush()
            resolved = ResolveDefinitionResponse(
                agent=ResolvedAgent(
                    id=run.agent_id, key="demo", revision=1, instructions="你是助手", runtime_config={}
                ),
                model=ResolvedModel(
                    id=uuid.uuid4(), revision=1, model_id="probe", base_url=BASE_URL
                ),
            )
            snapshot = build_snapshot(run_id=run.id, tenant_id=tenant, resolved=resolved)
            session.add(snapshot)
            await session.flush()
            run.snapshot_id = snapshot.id
            events = [
                ("USER_MESSAGE", {"text": "请输出客户 A 的 JSON"}),
                (
                    "ASSISTANT_TURN",
                    {
                        "tool_calls": [
                            {
                                "id": f"c{run_id}",
                                "name": "execute_skill",
                                "arguments": {"skill_key": "demo"},
                            }
                        ]
                    },
                ),
                ("TOOL_CALL", {"tool_call_id": f"c{run_id}", "tool_name": "execute_skill", "status": "OK"}),
                ("ASSISTANT_MESSAGE", {"text": REFERENCE}),
            ]
            for event_type, payload in events:
                seq += 1
                session.add(
                    CanonicalEvent(
                        tenant_id=tenant,
                        conversation_id=conversation_id,
                        run_id=run_id,
                        seq=seq,
                        event_type=event_type,
                        payload_json=payload,
                    )
                )
        await session.execute(
            sa.update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(last_seq=seq)
        )
        await session.commit()
    return run_ids


async def seed_experience(
    tenant: str,
    user: uuid.UUID,
    key: str,
    *,
    state: str = "PROPOSED",
    content: str = "输出 JSON，包含 name 与 amount 字段",
    authority: str = "AGENT_INFERRED",
    author: str = "AGENT",
) -> None:
    async with get_session_factory()() as session:
        session.add(
            UserMemory(
                tenant_id=tenant,
                user_id=user,
                memory_key=key,
                category="PROCEDURE",
                content_json={"value": content},
                author=author,
                authority=authority,
                confidence=2,
                explicit_request=author == "USER",
                confirmation_event_id=uuid.uuid4() if author == "USER" else None,
                last_confirmed_at=datetime.now(UTC) if author == "USER" else None,
                confirmed_count=1 if author == "USER" else 0,
                expires_at=(
                    datetime.now(UTC) + timedelta(days=365) if author == "AGENT" else None
                ),
                activation_state=state,
                task_signature=FAMILY,
                trigger_text="客户 JSON",
            )
        )
        await session.commit()


async def seed_report(
    tenant: str,
    key: str,
    status: str = "PASSED",
    *,
    base: str = "cli",
    user_id: uuid.UUID | None = None,
) -> None:
    async with get_session_factory()() as session:
        session.add(
            EvalReport(
                tenant_id=tenant,
                task_family=FAMILY,
                experience_key=key,
                experience_user_id=user_id,
                base_config_hash=base,
                metrics_json={},
                decision_json={"status": status},
                report_json={},
                content_hash=f"hash-{uuid.uuid4()}",
                version=1,
            )
        )
        await session.commit()


async def seed_credential(tenant: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO control.model_credential"
                " (tenant_id,key,provider_key,base_url,api_key,api_key_digest,max_inflight)"
                " VALUES (:t,'probe','dev',:u,'fixture-key',:digest,1)"
            ),
            {"t": tenant, "u": BASE_URL, "digest": "0" * 64},
        )
        await session.commit()


async def cleanup(tenant: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(sa.delete(EvalReport).where(EvalReport.tenant_id == tenant))
        await session.execute(sa.delete(EvalCase).where(EvalCase.tenant_id == tenant))
        await session.execute(sa.delete(UserMemoryHistory).where(UserMemoryHistory.tenant_id == tenant))
        await session.execute(sa.delete(MemoryCommandReceipt).where(MemoryCommandReceipt.tenant_id == tenant))
        await session.execute(sa.delete(UserMemory).where(UserMemory.tenant_id == tenant))
        await session.execute(sa.delete(MemoryScope).where(MemoryScope.tenant_id == tenant))
        await session.execute(sa.delete(CanonicalEvent).where(CanonicalEvent.tenant_id == tenant))
        run_ids = list(
            (await session.scalars(sa.select(RunRecord.id).where(RunRecord.tenant_id == tenant))).all()
        )
        if run_ids:
            await session.execute(sa.delete(RuntimeSnapshot).where(RuntimeSnapshot.run_id.in_(run_ids)))
        await session.execute(sa.delete(RunRecord).where(RunRecord.tenant_id == tenant))
        await session.execute(sa.delete(Conversation).where(Conversation.tenant_id == tenant))
        await session.execute(
            sa.text("DELETE FROM control.model_credential WHERE tenant_id=:t"), {"t": tenant}
        )
        await session.execute(
            sa.text("DELETE FROM control.config_audit_log WHERE tenant_id=:t"), {"t": tenant}
        )
        await session.commit()
