"""TASK-025 · WeCom 回复的 Human 分流（S-10、E-08、E-15、E-09 的 Runtime 侧）。

真实边界：真实契约（`ChannelHumanRouteRequest/Response`、`PendingPage`）+ 真实路由逻辑；
Owner 面用结构替身（Worker 的回答 CAS 由 TASK-021 的真实 PG 套件覆盖）。断言只看裁决：

- 零个 OPEN ⇒ `NONE`（调用方继续普通聊天），不碰回答接口；
- 恰好一个 ⇒ 按 `interaction_id` 回答，回答载荷就是这条消息，幂等键是入站消息 id；
- 多个 OPEN ⇒ `CLARIFY` 列出候选，**绝不按新旧挑一个**；
- Owner 拒绝（超时/格式/终态）⇒ `REJECTED`，状态不变也不建 Run；
- 匹配范围是 `user + agent + 这条入站消息的会话`：文本不参与选择回答哪一个。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from muad_agent_runtime.application.human_routing import (
    FREE_TEXT_RESPONSE_KEY,
    HumanRoutingService,
)
from muad_agent_runtime.application.task_client import WorkerTaskClient
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    ChannelHumanRouteRequest,
    HumanRouteOutcome,
    PendingHumanItem,
)

pytestmark = pytest.mark.unit

TENANT = "tenant-sched025"
USER = UUID("aaaaaaaa-0000-4000-8000-00000000000a")
OTHER_USER = UUID("bbbbbbbb-0000-4000-8000-00000000000b")
AGENT = UUID("cccccccc-0000-4000-8000-00000000000c")
OTHER_AGENT = UUID("dddddddd-0000-4000-8000-00000000000d")
SCHEDULE = UUID("10000000-0000-4000-8000-000000000001")
EXECUTION = UUID("20000000-0000-4000-8000-000000000001")
OTHER_SCHEDULE = UUID("10000000-0000-4000-8000-000000000002")
OTHER_EXECUTION = UUID("20000000-0000-4000-8000-000000000002")


@dataclass(frozen=True, slots=True)
class _Seed:
    """替身的过滤维度（`owner_user_id`/`owner_agent`/会话）与契约条目分开存放。"""

    item: dict[str, Any]
    owner_user_id: UUID
    owner_agent: UUID
    external_conversation_id: str


class FakeHumanOwner:
    """Worker 的结构替身：按调用方给的 user/agent/来源过滤，并记录每次回答。"""

    def __init__(self) -> None:
        self.items: list[_Seed] = []
        self.list_calls: list[dict[str, Any]] = []
        self.respond_calls: list[dict[str, Any]] = []
        self.respond_error: AppError | None = None
        self.list_error: AppError | None = None

    async def list_open_human_interactions(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        if self.list_error is not None:
            raise self.list_error
        return {
            "items": [
                seed.item
                for seed in self.items
                if seed.owner_user_id == kwargs["actor_user_id"]
                and seed.owner_agent == kwargs["agent_id"]
                and seed.external_conversation_id == kwargs["external_conversation_id"]
            ],
            "page": 1,
            "page_size": 100,
            "total": 0,
        }

    async def respond_human(self, **kwargs: Any) -> dict[str, Any]:
        self.respond_calls.append(kwargs)
        if self.respond_error is not None:
            raise self.respond_error
        return {
            "interaction_id": str(kwargs["interaction_id"]),
            "status": "RESOLVED",
            "execution_id": str(EXECUTION),
            "execution_status": "QUEUED",
        }


def _item(
    *,
    interaction_id: UUID,
    schedule_id: UUID = SCHEDULE,
    execution_id: UUID = EXECUTION,
    name: str = "A 客户周报",
    question: str = "请确认客户名单",
    owner_user_id: UUID = USER,
    owner_agent: UUID = AGENT,
    external_conversation_id: str = "chat-1",
) -> _Seed:
    item = PendingHumanItem(
        interaction_id=interaction_id,
        schedule_id=schedule_id,
        execution_id=execution_id,
        schedule_name=name,
        question=question,
        deadline_at="2026-11-16T08:00:00+00:00",
    ).model_dump(mode="json")
    return _Seed(
        item=item,
        owner_user_id=owner_user_id,
        owner_agent=owner_agent,
        external_conversation_id=external_conversation_id,
    )


def _request(
    *,
    text: str = "A 客户",
    message_id: str = "msg-1",
    user: UUID = USER,
    agent: UUID = AGENT,
    conversation: str = "chat-1",
) -> ChannelHumanRouteRequest:
    return ChannelHumanRouteRequest(
        agent_id=agent,
        platform_user_id=user,
        message_id=message_id,
        text=text,
        channel="WECOM",
        bot_id="bot-1",
        external_user_id="ext-1",
        external_conversation_id=conversation,
    )


async def _route(owner: FakeHumanOwner, request: ChannelHumanRouteRequest):
    return await HumanRoutingService(owner).route(request, tenant_id=TENANT)


# --------------------------------------------------------------- 0 / 1 / N


async def test_zero_open_questions_falls_back_to_normal_chat() -> None:
    owner = FakeHumanOwner()

    decision = await _route(owner, _request())

    assert decision.outcome is HumanRouteOutcome.NONE
    assert owner.respond_calls == []
    assert decision.candidates == ()


async def test_single_open_question_is_answered_by_interaction_id() -> None:
    interaction_id = uuid.uuid4()
    owner = FakeHumanOwner()
    owner.items = [_item(interaction_id=interaction_id)]

    decision = await _route(owner, _request(text="名单是 A 和 B"))

    assert decision.outcome is HumanRouteOutcome.ANSWERED
    assert decision.interaction_id == interaction_id
    assert len(owner.respond_calls) == 1
    call = owner.respond_calls[0]
    assert call["interaction_id"] == interaction_id
    assert call["response"] == {FREE_TEXT_RESPONSE_KEY: "名单是 A 和 B"}
    # 身份与幂等身份都来自受信请求，不来自回答正文。
    assert call["actor_user_id"] == USER
    assert call["agent_id"] == AGENT
    assert call["idempotency_key"] == "msg-1"


async def test_multiple_open_questions_are_clarified_not_answered() -> None:
    owner = FakeHumanOwner()
    owner.items = [
        _item(
            interaction_id=uuid.uuid4(),
            schedule_id=SCHEDULE,
            execution_id=EXECUTION,
            name="A 客户周报",
            question="客户名单？",
        ),
        _item(
            interaction_id=uuid.uuid4(),
            schedule_id=OTHER_SCHEDULE,
            execution_id=OTHER_EXECUTION,
            name="B 客户日报",
            question="发送时间？",
        ),
    ]

    decision = await _route(owner, _request(text="随便一个答案"))

    assert decision.outcome is HumanRouteOutcome.CLARIFY
    assert decision.interaction_id is None
    assert {item.schedule_name for item in decision.candidates} == {"A 客户周报", "B 客户日报"}
    assert {item.question for item in decision.candidates} == {"客户名单？", "发送时间？"}
    # 绝不按 recency / 文本相似度挑一个：没有任何回答被提交。
    assert owner.respond_calls == []


# ------------------------------------------------------------------ 范围


async def test_scope_is_user_agent_and_the_inbound_conversation() -> None:
    interaction_id = uuid.uuid4()
    owner = FakeHumanOwner()
    owner.items = [
        _item(interaction_id=interaction_id, external_conversation_id="chat-1"),
        _item(
            interaction_id=uuid.uuid4(),
            schedule_id=OTHER_SCHEDULE,
            execution_id=OTHER_EXECUTION,
            external_conversation_id="chat-2",
        ),
        _item(
            interaction_id=uuid.uuid4(),
            schedule_id=OTHER_SCHEDULE,
            execution_id=OTHER_EXECUTION,
            owner_user_id=OTHER_USER,
            external_conversation_id="chat-1",
        ),
        _item(
            interaction_id=uuid.uuid4(),
            schedule_id=OTHER_SCHEDULE,
            execution_id=OTHER_EXECUTION,
            owner_agent=OTHER_AGENT,
            external_conversation_id="chat-1",
        ),
    ]

    decision = await _route(owner, _request(conversation="chat-1"))

    assert decision.outcome is HumanRouteOutcome.ANSWERED
    assert decision.interaction_id == interaction_id
    call = owner.list_calls[0]
    assert call["actor_user_id"] == USER
    assert call["agent_id"] == AGENT
    assert call["channel"] == "WECOM"
    assert call["bot_id"] == "bot-1"
    assert call["external_conversation_id"] == "chat-1"


async def test_another_users_question_is_never_answered() -> None:
    owner = FakeHumanOwner()
    owner.items = [
        _item(interaction_id=uuid.uuid4(), owner_user_id=OTHER_USER),
        _item(interaction_id=uuid.uuid4(), owner_agent=OTHER_AGENT),
    ]

    decision = await _route(owner, _request())

    assert decision.outcome is HumanRouteOutcome.NONE
    assert owner.respond_calls == []
    # 身份按请求透传：问题归属由 Owner 用同一对 (tenant, actor, agent) 再核一次。
    assert owner.list_calls[0]["actor_user_id"] == USER
    assert owner.list_calls[0]["agent_id"] == AGENT


# ------------------------------------------------------------------ 拒绝


@pytest.mark.parametrize(
    ("code", "data", "reason"),
    [
        (ErrorCode.HUMAN_INTERACTION_EXPIRED, None, "HUMAN_INTERACTION_EXPIRED"),
        (ErrorCode.COMMON_VALIDATION_ERROR, {"reason": "HUMAN_RESPONSE_INVALID"}, "HUMAN_RESPONSE_INVALID"),
        (ErrorCode.EXECUTION_NOT_RESUMABLE, None, "EXECUTION_NOT_RESUMABLE"),
    ],
)
async def test_owner_rejection_is_reported_without_changing_state(
    code: ErrorCode, data: dict[str, Any] | None, reason: str
) -> None:
    owner = FakeHumanOwner()
    interaction_id = uuid.uuid4()
    owner.items = [_item(interaction_id=interaction_id)]
    owner.respond_error = AppError(code, data=data)

    decision = await _route(owner, _request())

    assert decision.outcome is HumanRouteOutcome.REJECTED
    assert decision.interaction_id == interaction_id
    assert decision.reason == reason


# ------------------------------------------------------------------ 幂等


async def test_replayed_message_reuses_the_same_idempotency_key() -> None:
    owner = FakeHumanOwner()
    owner.items = [_item(interaction_id=uuid.uuid4())]

    await _route(owner, _request(message_id="msg-replayed"))
    await _route(owner, _request(message_id="msg-replayed"))

    assert [call["idempotency_key"] for call in owner.respond_calls] == [
        "msg-replayed",
        "msg-replayed",
    ]
    assert {call["interaction_id"] for call in owner.respond_calls} == {
        owner.respond_calls[0]["interaction_id"]
    }


async def test_owner_read_failure_is_not_swallowed() -> None:
    owner = FakeHumanOwner()
    owner.list_error = AppError(ErrorCode.COMMON_INTERNAL_ERROR)

    with pytest.raises(AppError) as raised:
        await _route(owner, _request())

    assert raised.value.code == str(ErrorCode.COMMON_INTERNAL_ERROR)
    assert owner.respond_calls == []


async def test_channel_private_fields_are_rejected_at_the_contract() -> None:
    """渠道私有形状没有入口：`reply_id`/媒体令牌这类键在请求边界就是 422。"""
    payload = {
        "agent_id": str(AGENT),
        "platform_user_id": str(USER),
        "message_id": "msg-1",
        "text": "hi",
        "channel": "WECOM",
        "bot_id": "bot-1",
        "reply_id": "req-from-platform",
    }

    with pytest.raises(ValueError):
        ChannelHumanRouteRequest.model_validate(payload)


# ------------------------------------------------------- 真实 HTTP 线形状


class _StubWorker:
    """Worker 内部面的最小 HTTP 替身：只记录请求，不持库。"""

    def __init__(self) -> None:
        self.pending_queries: list[dict[str, str]] = []
        self.respond_headers: list[Any] = []
        self.respond_bodies: list[dict[str, Any]] = []
        self.app = FastAPI()

        @self.app.get("/internal/task-center/pending")
        async def pending(request: Request) -> JSONResponse:  # noqa: ANN202 - FastAPI 端点
            self.pending_queries.append(dict(request.query_params))
            return JSONResponse(
                {
                    "code": "OK",
                    "msg": "ok",
                    "data": {
                        "items": [_item(interaction_id=INTERACTION_ID).item],
                        "page": 1,
                        "page_size": 100,
                        "total": 1,
                    },
                }
            )

        @self.app.post("/internal/human-interactions/{interaction_id}/respond")
        async def respond(interaction_id: uuid.UUID, request: Request) -> JSONResponse:  # noqa: ANN202
            self.respond_headers.append(request.headers)
            self.respond_bodies.append(await request.json())
            return JSONResponse(
                {
                    "code": "OK",
                    "msg": "ok",
                    "data": {
                        "interaction_id": str(interaction_id),
                        "status": "RESOLVED",
                        "execution_id": str(EXECUTION),
                        "execution_status": "QUEUED",
                    },
                }
            )


INTERACTION_ID = UUID("30000000-0000-4000-8000-000000000001")


async def test_worker_client_sends_only_the_neutral_route_and_the_message_idempotency() -> None:
    """线上形状：查询只带来源四元组与主体，回答的幂等键就是入站消息 id。"""
    stub = _StubWorker()
    client = WorkerTaskClient(
        "http://worker", service_token="token", transport=httpx.ASGITransport(app=stub.app)
    )

    decision = await HumanRoutingService(client).route(
        _request(text="A 客户", message_id="msg-42"), tenant_id=TENANT
    )

    assert decision.outcome is HumanRouteOutcome.ANSWERED
    query = stub.pending_queries[0]
    assert query["kind"] == "HUMAN"
    assert query["agent_id"] == str(AGENT)
    assert query["channel"] == "WECOM"
    assert query["bot_id"] == "bot-1"
    assert query["external_user_id"] == "ext-1"
    assert query["external_conversation_id"] == "chat-1"
    # 渠道私有字段在类型上就没有位置。
    assert "reply_id" not in query
    headers = stub.respond_headers[0]
    assert headers["idempotency-key"] == "msg-42"
    assert headers["x-actor-user-id"] == str(USER)
    assert headers["x-agent-id"] == str(AGENT)
    assert stub.respond_bodies[0] == {"response": {FREE_TEXT_RESPONSE_KEY: "A 客户"}}
