"""B-251：历史和 SSE 共用安全公开投影（TASK-051）。

真实边界：真实 Runtime 应用（真实路由 + `X-Internal-Service` 门控）、真实
`WebchatHistoryService`、真实 `public_events` 投影函数、隔离租户的真实 PostgreSQL
（conversation/run_record/canonical_event/artifact 都是真实行，canonical `seq` 由真实
`EventWriter` 在会话行锁内分配）；不 mock 存储、不触模型、不触租约。

关键断言（design §3.6 公开流与持久写入；B-251）：

1. **单一共用函数**：历史查询与 SSE 流两条路径复用同一个 `project_public_fact`
   （模块属性同一，不是两份投影）；同一 canonical 行 → 同一公开事实、同一词表、同一过滤。
2. **白名单**：USER/ASSISTANT 可见消息（含安全附件，失效附件 `available=false`）、正文 delta、
   业务阶段、终态、安全错误、附件引用与真实用户确认可公开；工具参数/结果、模型请求、SYSTEM、
   原始推理、记忆/未知内部事件与存储键一律不公开（未知事件默认不公开）。
3. **合法跳号**：过滤后 canonical seq 可以不连续；投影不补号、不重排（历史只出消息事实，
   SSE 接缝只出公开流事件）。
4. **错误显式**：已知公开事件类型的坏载荷显式失败，不静默当过滤；内部确认载荷/TRANSCRIPT
   留档不公开、不提供下载。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_runtime.application import public_events
from muad_agent_runtime.application import webchat_history as webchat_history_module
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.infrastructure.db import get_engine, get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    CanonicalEvent,
    Conversation,
    RunRecord,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts.enums import RunStatus
from muad_contracts.webchat import WebchatExecutionStatus, WebchatHistoryMessage, WebchatMessagePage
from muad_contracts.webchat_events import (
    WebchatArtifactEvent,
    WebchatConfirmationEvent,
    WebchatDeltaEvent,
    WebchatErrorEvent,
    WebchatStageEvent,
    WebchatTerminalEvent,
)
from sqlalchemy import event

from agent_runtime.conftest import CLEANUP_ORDER, TenantContext

pytestmark = pytest.mark.integration

#: 本套件的入口（WEBCHAT 稳定入口账号；真实核属维度之一，不建 Console 行）。
ENTRY = uuid.uuid4()
#: 清理顺序：制品先于 run_record/conversation（同 Owner 物理 FK），再走套件既有顺序。
PURGE_ORDER = ("artifact", *CLEANUP_ORDER)
BASE_TIME = datetime(2026, 10, 18, 8, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# 真实 PG 读写（只增删本租户的行）
# --------------------------------------------------------------------------------------


async def _purge_tenant(tenant_id: str) -> None:
    async with get_session_factory()() as session:
        for table in PURGE_ORDER:
            await session.execute(
                sa.text(f"DELETE FROM runtime.{table} WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        await session.commit()


@pytest.fixture
async def projection_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    try:
        yield tenant
    finally:
        await _purge_tenant(tenant.tenant_id)


async def _seed_conversation(tenant: TenantContext) -> uuid.UUID:
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        title="投影会话",
        status="ACTIVE",
        last_seq=0,
        channel_account_id=ENTRY,
        last_message_at=BASE_TIME,
        create_time=BASE_TIME,
        update_time=BASE_TIME,
        is_deleted=False,
    )
    async with get_session_factory()() as session:
        session.add(conversation)
        await session.commit()
    return conversation.id


async def _seed_run(tenant: TenantContext, conversation_id: uuid.UUID) -> uuid.UUID:
    """真实 Run 行（终态）+ 会话 `last_run_id` 指针（artifact 行 FK 需要真实 run_record）。"""
    run_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                status=RunStatus.COMPLETED,
                input_text="seed",
                trace_id="seed-trace",
                start_time=BASE_TIME,
                end_time=BASE_TIME,
                create_time=BASE_TIME,
                update_time=BASE_TIME,
            )
        )
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        conversation.last_run_id = run_id
        await session.commit()
    return run_id


async def _seed_artifact(
    tenant: TenantContext,
    conversation_id: uuid.UUID,
    *,
    run_id: uuid.UUID,
    artifact_type: str = "INBOUND_DOCUMENT",
    metadata_json: dict[str, Any] | None = None,
    filename: str | None = "note.txt",
    is_deleted: bool = False,
) -> uuid.UUID:
    artifact = Artifact(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        run_id=run_id,
        task_id=None,
        conversation_id=conversation_id,
        artifact_type=artifact_type,
        storage_key=f"tenant/{tenant.tenant_id}/artifact-{uuid.uuid4().hex}",
        media_type="text/plain",
        size=3,
        checksum="sha256:" + "a" * 64,
        metadata_json=(
            metadata_json
            if metadata_json is not None
            else {"kind": "DOCUMENT", "filename": filename}
        ),
        is_deleted=is_deleted,
        create_time=BASE_TIME,
        update_time=BASE_TIME,
    )
    async with get_session_factory()() as session:
        session.add(artifact)
        await session.commit()
    return artifact.id


async def _append_event(
    tenant_id: str,
    conversation_id: uuid.UUID,
    *,
    run_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
    stream_type: str | None = None,
    artifact_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """真实 `EventWriter` 追加 canonical 行（`seq` 由会话行锁分配，不补号）。"""
    event_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await EventWriter(session).append(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            stream_type=stream_type,
            artifact_id=artifact_id,
            event_id=event_id,
        )
        await session.commit()
    return event_id


async def _load_rows(tenant_id: str, conversation_id: uuid.UUID) -> list[CanonicalEvent]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            sa.select(CanonicalEvent)
            .where(
                CanonicalEvent.tenant_id == tenant_id,
                CanonicalEvent.conversation_id == conversation_id,
            )
            .order_by(CanonicalEvent.seq)
        )
        return list(rows.all())


async def _load_artifacts(
    tenant_id: str, artifact_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Artifact]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            sa.select(Artifact).where(
                Artifact.tenant_id == tenant_id, Artifact.id.in_(artifact_ids)
            )
        )
        return {row.id: row for row in rows}


# --------------------------------------------------------------------------------------
# HTTP 驱动（真实应用 + 真实服务身份门控）
# --------------------------------------------------------------------------------------


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant.tenant_id,
        "X-Internal-Service": SharedSettings().internal_service_token or "",
    }


def _read_scope(tenant: TenantContext) -> dict[str, str]:
    return {
        "user_id": str(tenant.platform_user_id),
        "agent_id": str(tenant.agent_id),
        "entry_id": str(ENTRY),
    }


def _messages_path(conversation_id: uuid.UUID) -> str:
    return f"/internal/webchat/conversations/{conversation_id}/messages"


def _page_data(response: Any, contract: Any) -> Any:
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["code"] == "0"
    return contract.model_validate(payload["data"])


def _attachment_wire(attachment: Any) -> tuple[uuid.UUID, bool, str | None]:
    return (attachment.artifact_id, attachment.available, attachment.filename)


def _message_wire(message: WebchatHistoryMessage) -> tuple[int, str, str, list[Any]]:
    return (
        message.seq,
        message.role,
        message.text,
        [_attachment_wire(attachment) for attachment in message.attachments],
    )


def _safe_wire(values: list[Any]) -> str:
    return json.dumps([value.model_dump(mode="json") for value in values], ensure_ascii=False)


# --------------------------------------------------------------------------------------
# B-251：单一共用投影（历史 + SSE 同函数同结果）
# --------------------------------------------------------------------------------------


async def test_b251_history_and_stream_share_one_projection_over_the_same_rows(
    client: AsyncClient, projection_tenant: TenantContext
) -> None:
    tenant = projection_tenant
    conversation_id = await _seed_conversation(tenant)
    run_id = await _seed_run(tenant, conversation_id)
    available = await _seed_artifact(tenant, conversation_id, run_id=run_id, filename="note.txt")
    removed = await _seed_artifact(
        tenant, conversation_id, run_id=run_id, filename="gone.txt", is_deleted=True
    )

    await _append_event(
        tenant.tenant_id,
        conversation_id,
        run_id=run_id,
        event_type="USER_MESSAGE",
        payload={
            "text": "用户问题",
            "attachments": [
                {
                    "artifact_id": str(available),
                    "kind": "DOCUMENT",
                    "media_type": "text/plain",
                    "filename": None,
                    "size": 3,
                },
                {
                    "artifact_id": str(removed),
                    "kind": "DOCUMENT",
                    "media_type": "text/plain",
                    "filename": None,
                    "size": 3,
                },
            ],
        },
    )
    await _append_event(
        tenant.tenant_id,
        conversation_id,
        run_id=run_id,
        event_type="ASSISTANT_DELTA",
        stream_type="message.delta",
        payload={"delta": "增量"},
    )
    await _append_event(
        tenant.tenant_id,
        conversation_id,
        run_id=run_id,
        event_type="TOOL_CALL",
        stream_type="tool.completed",
        payload={
            "tool_call_id": "t1",
            "tool_name": "secret-tool",
            "arguments": {"canary": "CANARY_TOOL"},
        },
    )
    await _append_event(
        tenant.tenant_id,
        conversation_id,
        run_id=run_id,
        event_type="RUN_STAGE",
        stream_type="run.stage",
        payload=WebchatExecutionStatus(stage="RUNNING", started_at=BASE_TIME).model_dump(mode="json"),
    )
    await _append_event(
        tenant.tenant_id,
        conversation_id,
        run_id=run_id,
        event_type="ASSISTANT_MESSAGE",
        payload={"text": "助手正文"},
    )
    await _append_event(
        tenant.tenant_id,
        conversation_id,
        run_id=run_id,
        event_type="SYSTEM_PROMPT",
        payload={"content": "CANARY_SYSTEM"},
    )
    await _append_event(
        tenant.tenant_id,
        conversation_id,
        run_id=run_id,
        event_type="FUTURE_INTERNAL",
        stream_type="future.private.event",
        payload={"secret": "CANARY_UNKNOWN"},
    )
    await _append_event(
        tenant.tenant_id,
        conversation_id,
        run_id=run_id,
        event_type="RUN_COMPLETED",
        stream_type="run.completed",
        payload={"status": "COMPLETED", "final_text": "CANARY_FINAL"},
    )

    rows = await _load_rows(tenant.tenant_id, conversation_id)
    assert [row.seq for row in rows] == list(range(1, 9))
    artifacts = await _load_artifacts(tenant.tenant_id, [available, removed])

    facts = [public_events.project_public_fact(row, artifacts) for row in rows]
    messages = [fact for fact in facts if isinstance(fact, WebchatHistoryMessage)]
    stream_events = [
        stream_event
        for row in rows
        if (stream_event := public_events.project_public_stream_event(row, artifacts)) is not None
    ]

    # 单一实现：历史路径与 SSE 接缝复用同一函数（模块属性同一，不是两份投影）。
    assert webchat_history_module.project_public_fact is public_events.project_public_fact

    # 历史事实：只出 USER/ASSISTANT 两条可见消息；附件安全投影（失效附件 available=false）。
    assert [message.seq for message in messages] == [1, 5]
    assert [message.role for message in messages] == ["USER", "ASSISTANT"]
    assert [message.text for message in messages] == ["用户问题", "助手正文"]
    assert messages[1].attachments == []
    assert [_attachment_wire(attachment) for attachment in messages[0].attachments] == [
        (available, True, "note.txt"),
        (removed, False, "gone.txt"),
    ]

    # SSE 事实：同一过滤；过滤后 canonical seq 合法跳号（1/3/5/6/7 不公开，不补号不重排）。
    assert [type(stream_event).__name__ for stream_event in stream_events] == [
        "WebchatDeltaEvent",
        "WebchatStageEvent",
        "WebchatTerminalEvent",
    ]
    assert [stream_event.seq for stream_event in stream_events] == [2, 4, 8]
    assert all(
        stream_event.conversation_id == conversation_id and stream_event.run_id == run_id
        for stream_event in stream_events
    )
    delta, stage, terminal = stream_events
    assert isinstance(delta, WebchatDeltaEvent)
    assert delta.data.text == "增量" and delta.data.reply_generation == 0
    assert isinstance(stage, WebchatStageEvent)
    assert stage.data.stage == "RUNNING"
    assert isinstance(terminal, WebchatTerminalEvent)
    assert terminal.data.status == "COMPLETED"

    # 真实 HTTP 历史端点（真实服务身份门控）与同一函数输出逐字段一致：同函数同结果。
    response = await client.get(
        _messages_path(conversation_id),
        headers=_headers(tenant),
        params={**_read_scope(tenant), "page": 1, "page_size": 50},
    )
    page = _page_data(response, WebchatMessagePage)
    assert [_message_wire(message) for message in page.items] == [
        _message_wire(message) for message in messages
    ]

    # 内部 canary 与存储键绝不进入任何公开投影输出。
    wire = _safe_wire([fact for fact in facts if fact is not None]) + _safe_wire(stream_events)
    for canary in (
        "CANARY_TOOL",
        "CANARY_SYSTEM",
        "CANARY_UNKNOWN",
        "CANARY_FINAL",
        "secret-tool",
        "storage_key",
    ):
        assert canary not in wire, canary


async def test_b251_history_projection_is_not_n_plus_one_and_matches_shared_facts(
    client: AsyncClient, projection_tenant: TenantContext
) -> None:
    """历史分页仍按页一次批量取附件；输出仍是共用函数的同一事实。"""
    tenant = projection_tenant
    conversation_id = await _seed_conversation(tenant)
    run_id = await _seed_run(tenant, conversation_id)
    artifacts = [
        await _seed_artifact(
            tenant, conversation_id, run_id=run_id, filename=f"note-{index}.txt"
        )
        for index in range(3)
    ]
    for index, artifact_id in enumerate(artifacts):
        await _append_event(
            tenant.tenant_id,
            conversation_id,
            run_id=run_id,
            event_type="USER_MESSAGE",
            payload={
                "text": f"问题 {index}",
                "attachments": [
                    {
                        "artifact_id": str(artifact_id),
                        "kind": "DOCUMENT",
                        "media_type": "text/plain",
                        "filename": None,
                        "size": 3,
                    }
                ],
            },
        )

    statements: list[str] = []
    engine = get_engine()

    def capture(*args: Any) -> None:
        statements.append(str(args[2]))

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        response = await client.get(
            _messages_path(conversation_id),
            headers=_headers(tenant),
            params={**_read_scope(tenant), "page": 1, "page_size": 50},
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    page = _page_data(response, WebchatMessagePage)

    rows = await _load_rows(tenant.tenant_id, conversation_id)
    artifact_map = await _load_artifacts(tenant.tenant_id, artifacts)
    messages: list[WebchatHistoryMessage] = []
    for row in rows:
        fact = public_events.project_public_fact(row, artifact_map)
        assert fact is None or isinstance(fact, WebchatHistoryMessage)
        if fact is not None:
            messages.append(fact)
    assert [_message_wire(message) for message in page.items] == [
        _message_wire(message) for message in messages
    ]
    assert [message.text for message in messages] == ["问题 0", "问题 1", "问题 2"]

    # 附件元信息按页恰一次批量查询（不逐条回查，无 N+1）。
    artifact_statements = [statement for statement in statements if "runtime.artifact" in statement]
    assert len(artifact_statements) == 1


# --------------------------------------------------------------------------------------
# B-251：SSE 接缝的白名单分支与合法跳号
# --------------------------------------------------------------------------------------


async def test_b251_stream_seam_exposes_only_whitelisted_events(
    projection_tenant: TenantContext,
) -> None:
    tenant = projection_tenant
    conversation_id = await _seed_conversation(tenant)
    run_id = await _seed_run(tenant, conversation_id)
    available = await _seed_artifact(tenant, conversation_id, run_id=run_id, filename="out.txt")
    removed = await _seed_artifact(
        tenant, conversation_id, run_id=run_id, filename="gone.txt", is_deleted=True
    )
    transcript = await _seed_artifact(
        tenant,
        conversation_id,
        run_id=run_id,
        artifact_type="TRANSCRIPT",
        metadata_json={"message_count": 3},
    )

    await _append_event(
        tenant.tenant_id,
        conversation_id,
        run_id=run_id,
        event_type="ARTIFACT_CREATED",
        stream_type="artifact.created",
        payload={"artifact_id": str(available)},
        artifact_id=available,
    )
    await _append_event(
        tenant.tenant_id,
        conversation_id,
        run_id=run_id,
        event_type="ARTIFACT_CREATED",
        stream_type="artifact.created",
        payload={"artifact_id": str(removed)},
        artifact_id=removed,
    )
    # 内部压缩留档不是可下载的公开附件（不提供 TRANSCRIPT 下载）。
    await _append_event(
        tenant.tenant_id,
        conversation_id,
        run_id=run_id,
        event_type="ARTIFACT_CREATED",
        stream_type="artifact.created",
        payload={"artifact_id": str(transcript)},
        artifact_id=transcript,
    )
    await _append_event(
        tenant.tenant_id,
        conversation_id,
        run_id=run_id,
        event_type="RUN_FAILED",
        stream_type="run.failed",
        payload={"status": "FAILED", "error_code": "MODEL_UNAVAILABLE", "retryable": True},
    )

    rows = await _load_rows(tenant.tenant_id, conversation_id)
    artifacts = await _load_artifacts(tenant.tenant_id, [available, removed, transcript])
    stream_events = [
        stream_event
        for row in rows
        if (stream_event := public_events.project_public_stream_event(row, artifacts)) is not None
    ]

    assert [type(stream_event).__name__ for stream_event in stream_events] == [
        "WebchatArtifactEvent",
        "WebchatArtifactEvent",
        "WebchatErrorEvent",
    ]
    assert [stream_event.seq for stream_event in stream_events] == [1, 2, 4]  # 3 合法跳号
    first, second, error = stream_events
    assert isinstance(first, WebchatArtifactEvent)
    assert first.data.artifact_id == available and first.data.available is True
    assert first.data.filename == "out.txt" and first.data.checksum == "sha256:" + "a" * 64
    assert isinstance(second, WebchatArtifactEvent)
    assert second.data.artifact_id == removed and second.data.available is False
    assert isinstance(error, WebchatErrorEvent)
    assert error.data.code == "MODEL_UNAVAILABLE" and error.data.retryable is True

    wire = _safe_wire(stream_events)
    assert str(transcript) not in wire
    assert "storage_key" not in wire and "TRANSCRIPT" not in wire


async def test_b251_stream_seam_filters_unknown_and_internal_events_by_default(
    projection_tenant: TenantContext,
) -> None:
    tenant = projection_tenant
    conversation_id = await _seed_conversation(tenant)
    run_id = await _seed_run(tenant, conversation_id)
    internal = [
        ("MODEL_CALL_STARTED", "model.started", {"model": "gpt-x", "api_key": "CANARY_KEY"}),
        ("TOOL_CALL", "tool.completed", {"tool_name": "secret-tool", "result": "CANARY_RESULT"}),
        ("ASSISTANT_TURN", "assistant.turn", {"reasoning_content": "CANARY_REASONING"}),
        ("SYSTEM_PROMPT", None, {"content": "CANARY_SYSTEM"}),
        ("MEMORY_CONSOLIDATED", None, {"summary": "CANARY_MEMORY"}),
        ("FUTURE_INTERNAL", "future.private.event", {"secret": "CANARY_UNKNOWN"}),
        ("RUN_CREATED", "run.created", {"input": "CANARY_INPUT"}),
        ("USER_MESSAGE", None, {"text": "CANARY_USER"}),
        ("ASSISTANT_MESSAGE", None, {"text": "CANARY_ASSISTANT"}),
    ]
    for event_type, stream_type, payload in internal:
        await _append_event(
            tenant.tenant_id,
            conversation_id,
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            stream_type=stream_type,
        )

    rows = await _load_rows(tenant.tenant_id, conversation_id)
    stream_events = [
        public_events.project_public_stream_event(row, {}) for row in rows
    ]
    assert stream_events == [None] * len(internal)
    # 未知/内部事件默认不公开：真实行确实带 canary，但公开投影没有任何出口。
    wire = json.dumps([row.payload_json for row in rows], ensure_ascii=False)
    for canary in ("CANARY_KEY", "CANARY_RESULT", "CANARY_REASONING", "CANARY_SYSTEM",
                   "CANARY_MEMORY", "CANARY_UNKNOWN", "CANARY_INPUT"):
        assert canary in wire, canary


async def test_b251_stream_seam_projects_the_complete_safe_confirmation_only(
    projection_tenant: TenantContext,
) -> None:
    tenant = projection_tenant
    conversation_id = await _seed_conversation(tenant)
    run_id = await _seed_run(tenant, conversation_id)
    submission_id = uuid.uuid4()
    source_event_id = uuid.uuid4()
    expires_at = BASE_TIME + timedelta(minutes=5)
    await _append_event(
        tenant.tenant_id,
        conversation_id,
        run_id=run_id,
        event_type="MEMORY_CONFIRMATION_REQUIRED",
        stream_type="memory.confirmation_required",
        payload={
            "submission_id": str(submission_id),
            "source_event_id": str(source_event_id),
            "prompt": "请选择",
            "options": [{"value": "17", "label": "原始选项"}],
            "expires_at": expires_at.isoformat(),
        },
    )
    # 记忆确认的内部载荷（proposal_id/proposal_hash/…）：不合成、不编造选项，不公开内部键。
    await _append_event(
        tenant.tenant_id,
        conversation_id,
        run_id=run_id,
        event_type="MEMORY_CONFIRMATION_REQUIRED",
        stream_type="memory.confirmation_required",
        payload={
            "proposal_id": str(uuid.uuid4()),
            "prompt": "内部提示",
            "expires_at": expires_at.isoformat(),
            "proposal_hash": "CANARY_PROPOSAL_HASH",
        },
    )

    rows = await _load_rows(tenant.tenant_id, conversation_id)
    stream_events = [
        stream_event
        for row in rows
        if (stream_event := public_events.project_public_stream_event(row, {})) is not None
    ]
    assert [type(stream_event).__name__ for stream_event in stream_events] == [
        "WebchatConfirmationEvent"
    ]
    confirmation = stream_events[0]
    assert isinstance(confirmation, WebchatConfirmationEvent)
    assert confirmation.seq == 1
    assert confirmation.data.submission_id == submission_id
    assert confirmation.data.source_event_id == source_event_id
    assert confirmation.data.prompt == "请选择"
    assert [(option.value, option.label) for option in confirmation.data.options] == [("17", "原始选项")]
    wire = _safe_wire(stream_events)
    assert "CANARY_PROPOSAL_HASH" not in wire and "proposal_hash" not in wire


# --------------------------------------------------------------------------------------
# B-251：已知公开事件的坏载荷显式失败
# --------------------------------------------------------------------------------------


async def test_b251_known_public_events_fail_explicitly_on_malformed_payloads(
    projection_tenant: TenantContext,
) -> None:
    tenant = projection_tenant
    conversation_id = await _seed_conversation(tenant)
    run_id = await _seed_run(tenant, conversation_id)
    bad_payloads = [
        ("ASSISTANT_DELTA", "message.delta", {"delta": 123}),
        ("RUN_STAGE", "run.stage", {"stage": "NOT_A_STAGE"}),
        ("RUN_COMPLETED", "run.completed", {"status": "RUNNING"}),
        ("RUN_FAILED", "run.failed", {"status": "FAILED", "error_code": "provider secret canary"}),
    ]
    for event_type, stream_type, payload in bad_payloads:
        await _append_event(
            tenant.tenant_id,
            conversation_id,
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            stream_type=stream_type,
        )

    rows = await _load_rows(tenant.tenant_id, conversation_id)
    for row in rows:
        with pytest.raises(AppError) as failure:
            public_events.project_public_stream_event(row, {})
        assert failure.value.code == ErrorCode.COMMON_INTERNAL_ERROR
        assert "secret" not in str(failure.value)  # 原始坏载荷不回显
