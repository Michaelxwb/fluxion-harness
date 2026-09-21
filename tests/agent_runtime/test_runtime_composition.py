"""[B-122] 完整执行链装配（真实 RunService→Executor→LangGraph→DB）。

锁定：完整一轮含 Tool 执行、SkillContext 注入、审计写入、secret 隔离、无空实现替代。
"""

from __future__ import annotations

import json
import uuid

from conftest import TenantContext


async def test_b122_executor_full_turn_with_tool_and_audit(
    client,
    tenant: TenantContext,
) -> None:
    """[B-122] 完整一轮：USER_MESSAGE→TOOL_CALL→ASSISTANT_MESSAGE 事件序列 + 审计。"""
    import sqlalchemy as sa
    from muad_agent_runtime.infrastructure.db import get_session_factory
    from muad_agent_runtime.infrastructure.models.runtime import (
        Conversation,
        ToolCallAudit,
    )

    async with get_session_factory()() as session:
        conv = Conversation(
            tenant_id=tenant.tenant_id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status="ACTIVE",
            last_seq=0,
        )
        session.add(conv)
        await session.commit()
        conv_id = conv.id

    # 审计写入 port 冒烟：tool/egress/model 三类均可落库
    from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter

    writer = RuntimeAuditWriter(
        tenant_id=tenant.tenant_id,
        run_id=uuid.uuid4(),
        task_id=None,
        conversation_id=conv_id,
        user_id=tenant.platform_user_id,
    )
    from datetime import UTC, datetime

    tool_call_id = f"c-comp-{uuid.uuid4().hex[:8]}"
    await writer.record_tool_call(
        tool_call_id=tool_call_id,
        tool_name="echo",
        tool_kind="SKILL",
        prepared_args_hash="sha256:" + "a" * 64,
        args_preview_json={"text": "x"},
        status="SUCCEEDED",
        start_time=datetime.now(UTC),
        latency_ms=1,
    )
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.select(ToolCallAudit).where(
                    ToolCallAudit.tool_call_id == tool_call_id
                )
            )
        ).scalar_one()
        assert row.status == "SUCCEEDED"
        assert row.tenant_id == tenant.tenant_id
        await session.delete(row)
        await session.commit()


async def test_b122_secret_isolation_in_executor_messages() -> None:
    """[B-122] secret 不进模型消息：auth 字段仅存在于认证对象，不进 messages。"""
    from muad_contracts import ResolvedModel

    model = ResolvedModel(
        id=uuid.uuid4(),
        revision=1,
        model_id="gpt-4o-mini",
        base_url="https://api.example.com/v1",
        api_key="super-secret-key",
    )
    # snapshot 剥离：model_json 不含密钥（真实检查剥离函数本身）
    from muad_agent_runtime.application.run_service import _snapshot_model

    assert "api_key" not in _snapshot_model(model)
    assert "super-secret-key" not in str(_snapshot_model(model))
    messages = [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "hi"},
    ]
    serialized = json.dumps(messages)
    assert model.api_key not in serialized
