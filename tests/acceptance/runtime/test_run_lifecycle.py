"""[S-07][E-02][E-03][E-08][B-01] Run 生命周期 E2E。

真实边界：Runtime HTTP/SSE → 真实 Console HTTP → PostgreSQL/Redis；LLM 为真实 HTTP 探针。
"""

from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import sqlalchemy as sa
from muad_agent_runtime.application.run_service import build_snapshot
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    RunInterrupt,
    RunRecord,
)
from muad_contracts import ResolveDefinitionResponse

from tests.acceptance.runtime.conftest import LiveStack, run_db


def _parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        data_lines = [
            line[len("data:") :].lstrip() for line in block.splitlines() if line.startswith("data:")
        ]
        if data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events


def _headers(stack: LiveStack) -> dict[str, str]:
    return stack.runtime_headers()


def _payload(
    stack: LiveStack,
    text: str = "hello",
    *,
    conversation_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agent_id": str(stack.agent_id),
        "platform_user_id": str(stack.platform_user_id),
        "channel": {"type": "WECOM", "bot_id": "bot-acc", "external_conversation_id": "ext-acc"},
        "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": text},
    }
    if conversation_id is not None:
        payload["conversation_id"] = str(conversation_id)
    return payload


def _fresh_conversation(http: httpx.Client, stack: LiveStack) -> uuid.UUID:
    response = http.post(
        f"{stack.runtime_url}/v1/conversations",
        json={
            "agent_id": str(stack.agent_id),
            "platform_user_id": str(stack.platform_user_id),
        },
        headers=_headers(stack),
    )
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["data"]["conversation_id"])


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


def _seed_waiting(
    stack: LiveStack,
    http: httpx.Client,
    *,
    conversation_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    resolved = _resolve_over_console(stack, http)
    run_id, conv_id = uuid.uuid4(), conversation_id or uuid.uuid4()

    async def seed(factory: Any) -> None:
        async with factory() as session:
            from muad_agent_runtime.infrastructure.models.runtime import Conversation

            session.add(
                Conversation(
                    id=conv_id,
                    tenant_id=stack.tenant_id,
                    user_id=stack.platform_user_id,
                    agent_id=stack.agent_id,
                    status="ACTIVE",
                    last_seq=0,
                )
            )
            run = RunRecord(
                id=run_id,
                tenant_id=stack.tenant_id,
                conversation_id=conv_id,
                user_id=stack.platform_user_id,
                agent_id=stack.agent_id,
                status="WAITING_INPUT",
                input_text="pending",
                trace_id=uuid.uuid4().hex,
                cancel_requested=False,
            )
            session.add(run)
            snapshot = build_snapshot(run_id=run_id, tenant_id=stack.tenant_id, resolved=resolved)
            session.add(snapshot)
            await session.flush()
            run.snapshot_id = snapshot.id
            session.add(
                RunInterrupt(
                    tenant_id=stack.tenant_id,
                    run_id=run_id,
                    conversation_id=conv_id,
                    interrupt_type="CONFIRM",
                    prompt_text="continue?",
                    options_json=["yes", "no"],
                    status="WAITING",
                )
            )
            await session.commit()

    run_db(seed)
    return run_id, conv_id


def _stream_events(http: httpx.Client, response: httpx.Response) -> list[dict[str, Any]]:
    return _parse_sse(response.read().decode())


def test_s07_waiting_input_auto_resume_continues_seq(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    """[S-07] WAITING_INPUT 普通回复自动恢复原 Run：resumed=true、seq 延续、interrupt RESOLVED。"""
    run_id, conv_id = _seed_waiting(live_stack, http)

    async def seed_prior_event(factory: Any) -> None:
        async with factory() as session:
            from muad_agent_runtime.infrastructure.models.runtime import Conversation

            session.add(
                CanonicalEvent(
                    tenant_id=live_stack.tenant_id,
                    conversation_id=conv_id,
                    run_id=run_id,
                    seq=1,
                    event_type="USER_MESSAGE",
                    payload_json={"text": "prior"},
                )
            )
            await session.execute(
                sa.update(Conversation)
                .where(Conversation.id == conv_id)
                .values(last_seq=1)
            )
            await session.commit()

    run_db(seed_prior_event)

    response = http.post(
        f"{live_stack.runtime_url}/v1/runs",
        json=_payload(live_stack, "choose device 1", conversation_id=conv_id),
        headers=_headers(live_stack),
    )
    assert response.status_code == 200, response.text
    events = _stream_events(http, response)
    assert events[0]["type"] == "run.created"
    assert events[0]["data"]["resumed"] is True
    assert events[0]["run_id"] == str(run_id)
    assert events[0]["seq"] > 1  # seq 延续而非从 1 重排
    assert events[-1]["type"] == "run.completed"
    assert events[-1]["data"]["status"] == "COMPLETED"

    async def verify(factory: Any) -> tuple[str, str]:
        async with factory() as session:
            run = await session.get(RunRecord, run_id)
            assert run is not None
            interrupt = await session.scalar(
                sa.select(RunInterrupt).where(RunInterrupt.run_id == run_id)
            )
            assert interrupt is not None
            return run.status, interrupt.status

    assert run_db(verify) == ("COMPLETED", "RESOLVED")


def test_s07_terminal_resume_is_idempotent(live_stack: LiveStack, http: httpx.Client) -> None:
    created = http.post(
        f"{live_stack.runtime_url}/v1/runs",
        json=_payload(live_stack, conversation_id=_fresh_conversation(http, live_stack)),
        headers=_headers(live_stack),
    )
    events = _stream_events(http, created)
    assert events[-1]["data"]["status"] == "COMPLETED"
    run_id = events[0]["run_id"]

    resumed = http.post(
        f"{live_stack.runtime_url}/v1/runs/{run_id}/resume",
        json={"input": {"id": "terminal-replay", "type": "text", "text": "again"}},
        headers=_headers(live_stack),
    )
    assert resumed.status_code == 200
    replay = _stream_events(http, resumed)
    assert replay[-1]["type"] == "run.completed"
    assert replay[-1]["data"]["status"] == "COMPLETED"


def test_e02_cancel_requested_waiting_input_writes_events(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    """[E-02] WAITING_INPUT 取消：CAS CANCELLED + interrupt CANCELLED + CANCEL 事件 + run.completed。"""
    run_id, conv_id = _seed_waiting(live_stack, http)

    response = http.post(
        f"{live_stack.runtime_url}/v1/runs/cancel-active",
        json={
            "agent_id": str(live_stack.agent_id),
            "platform_user_id": str(live_stack.platform_user_id),
        },
        headers=_headers(live_stack),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"run_id": str(run_id), "status": "CANCELLED"}

    async def verify(factory: Any) -> tuple[str, str, list[str], list[str]]:
        async with factory() as session:
            run = await session.get(RunRecord, run_id)
            assert run is not None
            interrupt = await session.scalar(
                sa.select(RunInterrupt).where(RunInterrupt.run_id == run_id)
            )
            assert interrupt is not None
            rows = (
                await session.execute(
                    sa.select(CanonicalEvent)
                    .where(CanonicalEvent.conversation_id == conv_id)
                    .order_by(CanonicalEvent.seq)
                )
            ).scalars().all()
            business = [row.event_type for row in rows if row.stream_type is None]
            streamed = [str(row.stream_type) for row in rows if row.stream_type is not None]
            return run.status, interrupt.status, business, streamed

    status, interrupt_status, business, streamed = run_db(verify)
    assert status == "CANCELLED"
    assert interrupt_status == "CANCELLED"
    assert "CANCEL" in business
    assert "run.completed" in streamed

    got = http.get(f"{live_stack.runtime_url}/v1/runs/{run_id}", headers=_headers(live_stack))
    assert got.status_code == 200
    assert got.json()["data"]["status"] == "CANCELLED"
    assert got.json()["data"]["cancel_requested"] is False


def test_e03_concurrent_create_run_one_busy(live_stack: LiveStack, http: httpx.Client) -> None:
    """[E-03] 并发不同消息仅一活跃 Run：409 RUN_BUSY（真实 HTTP + partial unique）。"""
    conversation_id = _fresh_conversation(http, live_stack)

    os.environ["OPENAI_PROBE_DELAY_MS"] = "1500"
    try:
        def create(text: str) -> httpx.Response:
            with httpx.Client(timeout=30) as client:
                return client.post(
                    f"{live_stack.runtime_url}/v1/runs",
                    json=_payload(live_stack, text, conversation_id=conversation_id),
                    headers=_headers(live_stack),
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(create, ["first", "second"]))
    finally:
        os.environ.pop("OPENAI_PROBE_DELAY_MS", None)

    codes = sorted(response.status_code for response in responses)
    assert codes == [200, 409]
    busy = next(response for response in responses if response.status_code == 409)
    assert busy.json()["code"] == "RUN_BUSY"


def test_e08_cancel_running_run_cooperatively(live_stack: LiveStack, http: httpx.Client) -> None:
    """[E-08] RUNNING 取消：cancel-active 返回 CANCELLING；执行者协作写 CANCEL/终态；Redis hint。"""
    import redis

    conversation_id = _fresh_conversation(http, live_stack)

    os.environ["OPENAI_PROBE_DELAY_MS"] = "2000"
    try:
        streamed: list[dict[str, Any]] = []
        with http.stream(
            "POST",
            f"{live_stack.runtime_url}/v1/runs",
            json=_payload(live_stack, "long task", conversation_id=conversation_id),
            headers=_headers(live_stack),
        ) as stream:
            for line in stream.iter_lines():
                if not line.startswith("data:"):
                    continue
                streamed.append(json.loads(line[len("data:") :].strip()))
                if len(streamed) == 1:
                    assert streamed[0]["type"] == "run.created"
                    run_id = streamed[0]["run_id"]
                    cancelled = http.post(
                        f"{live_stack.runtime_url}/v1/runs/cancel-active",
                        json={
                            "agent_id": str(live_stack.agent_id),
                            "platform_user_id": str(live_stack.platform_user_id),
                        },
                        headers=_headers(live_stack),
                    )
                    assert cancelled.status_code == 200
                    assert cancelled.json()["data"]["status"] == "CANCELLING"
    finally:
        os.environ.pop("OPENAI_PROBE_DELAY_MS", None)

    assert streamed[-1]["type"] == "run.completed"
    assert streamed[-1]["data"]["status"] == "CANCELLED"

    async def verify(factory: Any) -> tuple[str, bool, list[str]]:
        async with factory() as session:
            run = await session.get(RunRecord, uuid.UUID(run_id))
            assert run is not None
            rows = (
                await session.execute(
                    sa.select(CanonicalEvent)
                    .where(CanonicalEvent.run_id == uuid.UUID(run_id))
                    .order_by(CanonicalEvent.seq)
                )
            ).scalars().all()
            return (
                run.status,
                run.cancel_requested,
                [row.event_type for row in rows if row.stream_type is None],
            )

    status, cancel_requested, business = run_db(verify)
    assert status == "CANCELLED"
    assert cancel_requested is True
    assert "CANCEL" in business

    from muad_common import SharedSettings

    redis_client = redis.from_url(SharedSettings().require_redis_url(), decode_responses=True)
    try:
        assert redis_client.exists(f"run:cancel:{run_id}") == 1
    finally:
        redis_client.close()


def test_b01_idempotency_replay_over_http(live_stack: LiveStack, http: httpx.Client) -> None:
    """[B-01] 同 key 同指纹 200 重放持久化事件（seq/payload 一致）；异指纹 409；不二次执行。"""
    payload = _payload(live_stack, conversation_id=_fresh_conversation(http, live_stack))
    headers = {**_headers(live_stack), "Idempotency-Key": f"b01-{uuid.uuid4()}"}

    first = http.post(f"{live_stack.runtime_url}/v1/runs", json=payload, headers=headers)
    assert first.status_code == 200
    first_events = _stream_events(http, first)

    second = http.post(f"{live_stack.runtime_url}/v1/runs", json=payload, headers=headers)
    assert second.status_code == 200
    second_events = _stream_events(http, second)
    assert [(event["seq"], event["type"]) for event in second_events] == [
        (event["seq"], event["type"]) for event in first_events
    ]
    assert [event["data"] for event in second_events] == [
        event["data"] for event in first_events
    ]

    changed = {**payload, "message": {**payload["message"], "text": "different"}}
    conflict = http.post(f"{live_stack.runtime_url}/v1/runs", json=changed, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_MISMATCH"

    run_id = first_events[0]["run_id"]

    async def verify(factory: Any) -> int:
        async with factory() as session:
            count = await session.scalar(
                sa.select(sa.func.count())
                .select_from(RunRecord)
                .where(RunRecord.id == uuid.UUID(run_id))
            )
            rows = (
                await session.execute(
                    sa.select(CanonicalEvent).where(CanonicalEvent.run_id == uuid.UUID(run_id))
                )
            ).scalars().all()
            assert all(row.submission_id is not None for row in rows)
            return int(count or 0)

    assert run_db(verify) == 1


def test_b01_concurrent_same_key_single_run(live_stack: LiveStack, http: httpx.Client) -> None:
    """[B-01] 并发同 key：唯一约束落败者读取首次提交结果，只创建一个 Run。"""
    payload = _payload(live_stack, conversation_id=_fresh_conversation(http, live_stack))
    headers = {**_headers(live_stack), "Idempotency-Key": f"b01c-{uuid.uuid4()}"}

    os.environ["OPENAI_PROBE_DELAY_MS"] = "800"
    try:
        def create(_: int) -> httpx.Response:
            with httpx.Client(timeout=30) as client:
                return client.post(
                    f"{live_stack.runtime_url}/v1/runs", json=payload, headers=headers
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(create, [1, 2]))
    finally:
        os.environ.pop("OPENAI_PROBE_DELAY_MS", None)

    assert all(response.status_code == 200 for response in responses)
    run_ids = {_stream_events(http, response)[0]["run_id"] for response in responses}
    assert len(run_ids) == 1


def test_get_run_snapshot_summary(live_stack: LiveStack, http: httpx.Client) -> None:
    created = http.post(
        f"{live_stack.runtime_url}/v1/runs",
        json=_payload(live_stack, conversation_id=_fresh_conversation(http, live_stack)),
        headers=_headers(live_stack),
    )
    events = _stream_events(http, created)
    run_id = events[0]["run_id"]
    got = http.get(f"{live_stack.runtime_url}/v1/runs/{run_id}", headers=_headers(live_stack))
    assert got.status_code == 200
    data = got.json()["data"]
    assert data["status"] == "COMPLETED"
    assert data["snapshot"]["mcp_catalog_revision"] == 4
    assert data["snapshot"]["mcp_catalog_hash"].startswith("sha256:")
    serialized = json.dumps(data)
    assert "api_key" not in serialized
    assert "sk-owner-table-key" not in serialized
