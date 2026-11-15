"""TASK-025 · WeCom 用户确认与 Human 回复分流（S-01、S-06、S-10、E-08、E-09、E-14、E-15）。

真实边界：真实 `InboundPipeline`（生产命令/回复链路、去重占位与受理裁决、`RunRequest` 组装）
+ 真实契约。两个 Owner 面用结构替身：

- Worker（Draft 读 + 确认）：断言 hash 由**服务端**读出、surface/ref 来自真实入站消息，
  同一条消息重投用同一个幂等键；
- Runtime（Human 路由）：断言 Gateway 只把裁决变成用户可见文本，**命中的回复不建 Run**。

覆盖：确认成功/重放/未绑定/无权限/坏 id、单个问题直接回答、多个问题要求指定、零个问题回到
普通聊天、回答被拒、显式会话面不参与聊天分流、渠道私有字段不进入路由请求。
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

import pytest
from fakes import FakeConsoleClient, FakeRuntimeClient, make_envelope, resolved_response
from muad_api import AppError
from muad_api.catalog import MessageCatalog
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    ChannelEnvelope,
    ChannelHumanRouteRequest,
    ChannelHumanRouteResponse,
    ConfirmationSurface,
    HumanRouteCandidate,
    HumanRouteOutcome,
)
from muad_im_gateway.application.inbound import (
    CONFIRM_DONE_WITH_FIRE_TEXT,
    CONFIRM_USAGE_TEXT,
    HUMAN_ANSWER_ACCEPTED_TEXT,
    NO_PERMISSION_TEXT,
    UNBOUND_TEXT,
    InboundPipeline,
)
from muad_im_gateway.application.inbound_schedules import ChatScheduleService
from muad_im_gateway.application.runtime_client import SseEvent
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.infrastructure.dedupe import (
    InMemoryDedupeStore,
    NullDedupeStore,
)

pytestmark = pytest.mark.integration

TENANT = "tenant-sched025"
DRAFT_ID = UUID("40000000-0000-4000-8000-000000000001")
DRAFT_HASH = "sha256:" + "a" * 64
NEXT_FIRE_AT = "2026-11-18T01:00:00+00:00"
ROUTE_FIELDS = {
    "agent_id",
    "platform_user_id",
    "message_id",
    "text",
    "channel",
    "bot_id",
    "external_user_id",
    "external_conversation_id",
}

COMPLETED_STREAM = [
    SseEvent(type="run.created", data={"run_id": "run-1", "resumed": False}),
    SseEvent(type="message.delta", data={"delta": "好的"}),
    SseEvent(type="run.completed", data={"status": "COMPLETED", "final_text": "好的"}),
]


class FakeScheduleOwner:
    """Worker 的 Draft 读面 + 确认动作：记录每一次调用，可按用例注入失败。"""

    def __init__(self) -> None:
        self.drafts: dict[UUID, dict[str, Any]] = {}
        self.get_calls: list[dict[str, Any]] = []
        self.confirm_calls: list[dict[str, Any]] = []
        self.get_error: AppError | None = None
        self.confirm_error: AppError | None = None
        self.confirm_response: dict[str, Any] = {
            "schedule_id": str(uuid.uuid4()),
            "status": "ACTIVE",
            "revision_no": 1,
            "config_version": 1,
            "next_fire_at": NEXT_FIRE_AT,
        }

    async def get_schedule_draft(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls.append(kwargs)
        if self.get_error is not None:
            raise self.get_error
        draft = self.drafts.get(kwargs["draft_id"])
        if draft is None:
            raise AppError(ErrorCode.SCHEDULE_NOT_FOUND)
        return draft

    async def confirm_draft(self, **kwargs: Any) -> dict[str, Any]:
        self.confirm_calls.append(kwargs)
        if self.confirm_error is not None:
            raise self.confirm_error
        return self.confirm_response


class FakeHumanRoutes:
    """Runtime 的路由裁决面：默认 NONE；用例显式给出 ANSWERED/CLARIFY/REJECTED。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = ChannelHumanRouteResponse(outcome=HumanRouteOutcome.NONE)
        self.error: AppError | None = None

    async def resolve_human_route(
        self, request: ChannelHumanRouteRequest, *, tenant_id: str, trace_id: str = ""
    ) -> dict[str, Any]:
        self.calls.append({"request": request, "tenant_id": tenant_id, "trace_id": trace_id})
        if self.error is not None:
            raise self.error
        return self.response.model_dump(mode="json")


class _ExplicitConversationAdapter(FakeChannelAdapter):
    """显式会话面（WebChat 同形）：回答按 interaction_id 在任务中心，不参与聊天分流。"""

    conversation_id = UUID("50000000-0000-4000-8000-000000000001")

    def explicit_conversation_id(self, envelope: ChannelEnvelope) -> UUID | None:
        return self.conversation_id


def _pipeline(
    console: FakeConsoleClient,
    runtime: FakeRuntimeClient,
    *,
    catalog: MessageCatalog,
    owner: FakeScheduleOwner | None = None,
    routes: FakeHumanRoutes | None = None,
    dedupe: Any = None,
) -> InboundPipeline:
    return InboundPipeline(
        dedupe=dedupe if dedupe is not None else InMemoryDedupeStore(),
        console=console,
        runtime=runtime,
        catalog=catalog,
        tenant_id=TENANT,
        runtime_reconnect_attempts=0,
        runtime_reconnect_delay_sec=0.0,
        schedules=ChatScheduleService(
            owner=owner or FakeScheduleOwner(),
            routes=routes or FakeHumanRoutes(),
            tenant_id=TENANT,
        ),
    )


def _sent_texts(adapter: FakeChannelAdapter) -> list[str]:
    return [message.text for _route, message in adapter.sent]


def _draft() -> dict[str, Any]:
    return {
        "draft_id": str(DRAFT_ID),
        "draft_hash": DRAFT_HASH,
        "status": "PENDING",
        "preview": {
            "mode": "REMINDER",
            "what": "每天 9 点提醒",
            "when": "CRON 0 1 * * * (Asia/Shanghai)",
            "first_fire_at": NEXT_FIRE_AT,
            "delivery": "PUSH",
        },
        "expires_at": "2026-11-15T13:00:00+00:00",
    }


# ---------------------------------------------------------------- Draft 确认


async def test_confirm_command_uses_the_server_side_hash_and_inbound_reference(
    catalog: MessageCatalog,
) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(COMPLETED_STREAM)
    owner, routes, adapter = FakeScheduleOwner(), FakeHumanRoutes(), FakeChannelAdapter()
    owner.drafts[DRAFT_ID] = _draft()
    pipeline = _pipeline(console, runtime, catalog=catalog, owner=owner, routes=routes)

    accepted = await pipeline.handle(adapter, make_envelope(f"/confirm {DRAFT_ID}"))

    assert accepted is True
    assert runtime.run_requests == []
    assert routes.calls == []
    assert owner.get_calls == [
        {
            "tenant_id": TENANT,
            "user_id": console.resolve_response.platform_user_id,
            "agent_id": console.resolve_response.agent_id,
            "draft_id": DRAFT_ID,
        }
    ]
    confirm = owner.confirm_calls[0]
    # hash 只来自服务端读到的 Draft；用户给的只有 id。
    assert confirm["payload"] == {"draft_hash": DRAFT_HASH}
    assert confirm["surface"] is ConfirmationSurface.WECOM
    # 一次用户动作 = 一个稳定引用 = 同一条入站消息（重投不重复生效）。
    assert confirm["ref"] == "msg-1"
    assert confirm["idempotency_key"] == "msg-1"
    assert _sent_texts(adapter) == [CONFIRM_DONE_WITH_FIRE_TEXT.format(next_fire_at=NEXT_FIRE_AT)]


async def test_replayed_confirm_reuses_the_same_key_and_never_creates_a_run(
    catalog: MessageCatalog,
) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(COMPLETED_STREAM)
    owner, adapter = FakeScheduleOwner(), FakeChannelAdapter()
    owner.drafts[DRAFT_ID] = _draft()
    # 模拟去重窗口之后平台重投同一条消息：确认必须还是同一个幂等身份。
    pipeline = _pipeline(
        console, runtime, catalog=catalog, owner=owner, dedupe=NullDedupeStore()
    )
    envelope = make_envelope(f"/confirm {DRAFT_ID}")

    await pipeline.handle(adapter, envelope)
    await pipeline.handle(adapter, envelope)

    assert [call["idempotency_key"] for call in owner.confirm_calls] == ["msg-1", "msg-1"]
    assert [call["ref"] for call in owner.confirm_calls] == ["msg-1", "msg-1"]
    assert runtime.run_requests == []


async def test_confirm_requires_a_bound_and_authorized_user(catalog: MessageCatalog) -> None:
    owner, routes = FakeScheduleOwner(), FakeHumanRoutes()
    owner.drafts[DRAFT_ID] = _draft()
    console = FakeConsoleClient()
    console.resolve_response = resolved_response(bound=False)
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(
        console, FakeRuntimeClient(COMPLETED_STREAM), catalog=catalog, owner=owner, routes=routes
    )

    await pipeline.handle(adapter, make_envelope(f"/confirm {DRAFT_ID}"))

    assert _sent_texts(adapter) == [UNBOUND_TEXT]
    assert owner.get_calls == []

    console.resolve_response = resolved_response(authorized=False)
    await pipeline.handle(adapter, make_envelope(f"/confirm {DRAFT_ID}", message_id="msg-2"))

    assert _sent_texts(adapter) == [UNBOUND_TEXT, NO_PERMISSION_TEXT]
    assert owner.get_calls == []
    assert routes.calls == []


async def test_confirm_rejects_a_malformed_draft_id(catalog: MessageCatalog) -> None:
    owner, routes = FakeScheduleOwner(), FakeHumanRoutes()
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(
        console, FakeRuntimeClient(COMPLETED_STREAM), catalog=catalog, owner=owner, routes=routes
    )

    accepted = await pipeline.handle(adapter, make_envelope("/confirm not-a-uuid"))

    assert accepted is True
    assert _sent_texts(adapter) == [CONFIRM_USAGE_TEXT]
    assert owner.get_calls == []


async def test_confirm_failure_is_reported_and_not_treated_as_accepted(
    catalog: MessageCatalog,
) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(COMPLETED_STREAM)
    owner, adapter = FakeScheduleOwner(), FakeChannelAdapter()
    owner.drafts[DRAFT_ID] = _draft()
    owner.confirm_error = AppError(ErrorCode.SCHEDULE_DRAFT_EXPIRED)
    pipeline = _pipeline(console, runtime, catalog=catalog, owner=owner)

    accepted = await pipeline.handle(adapter, make_envelope(f"/confirm {DRAFT_ID}"))

    # 未受理 ⇒ 平台可重投；确认本身幂等，重试不会重复生效。
    assert accepted is False
    assert runtime.run_requests == []
    assert len(_sent_texts(adapter)) == 1
    assert _sent_texts(adapter)[0] != CONFIRM_USAGE_TEXT


# ---------------------------------------------------------------- Human 回答


def _candidate(name: str, question: str) -> HumanRouteCandidate:
    return HumanRouteCandidate(
        interaction_id=uuid.uuid4(),
        schedule_id=uuid.uuid4(),
        execution_id=uuid.uuid4(),
        schedule_name=name,
        question=question,
        deadline_at="2026-11-16T08:00:00+00:00",
    )


async def test_single_open_question_is_answered_without_creating_a_run(
    catalog: MessageCatalog,
) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(COMPLETED_STREAM)
    routes, adapter = FakeHumanRoutes(), FakeChannelAdapter()
    routes.response = ChannelHumanRouteResponse(
        outcome=HumanRouteOutcome.ANSWERED, interaction_id=uuid.uuid4()
    )
    pipeline = _pipeline(console, runtime, catalog=catalog, routes=routes)

    accepted = await pipeline.handle(adapter, make_envelope("名单是 A 和 B"))

    assert accepted is True
    assert runtime.run_requests == []
    assert _sent_texts(adapter) == [HUMAN_ANSWER_ACCEPTED_TEXT]
    request = routes.calls[0]["request"]
    assert set(request.model_dump()) == ROUTE_FIELDS
    assert request.platform_user_id == console.resolve_response.platform_user_id
    assert request.agent_id == console.resolve_response.agent_id
    assert request.message_id == "msg-1"
    assert request.channel == "WECOM"
    assert request.bot_id == "bot-1"
    assert request.external_conversation_id == "conv-1"
    assert routes.calls[0]["tenant_id"] == TENANT


async def test_multiple_open_questions_are_listed_and_no_run_is_created(
    catalog: MessageCatalog,
) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(COMPLETED_STREAM)
    routes, adapter = FakeHumanRoutes(), FakeChannelAdapter()
    routes.response = ChannelHumanRouteResponse(
        outcome=HumanRouteOutcome.CLARIFY,
        candidates=[_candidate("A 客户周报", "客户名单？"), _candidate("B 客户日报", "发送时间？")],
    )
    pipeline = _pipeline(console, runtime, catalog=catalog, routes=routes)

    accepted = await pipeline.handle(adapter, make_envelope("随便一个答案"))

    assert accepted is True
    assert runtime.run_requests == []
    text = _sent_texts(adapter)[0]
    assert "A 客户周报" in text and "客户名单？" in text
    assert "B 客户日报" in text and "发送时间？" in text


async def test_no_open_question_continues_as_normal_chat(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(COMPLETED_STREAM)
    routes, adapter = FakeHumanRoutes(), FakeChannelAdapter()
    routes.response = ChannelHumanRouteResponse(outcome=HumanRouteOutcome.NONE)
    pipeline = _pipeline(console, runtime, catalog=catalog, routes=routes)

    await pipeline.handle(adapter, make_envelope("随便聊聊"))

    assert len(runtime.run_requests) == 1
    assert runtime.run_requests[0].message.text == "随便聊聊"
    assert routes.calls != []


async def test_rejected_answer_reports_the_reason_without_a_run(
    catalog: MessageCatalog,
) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(COMPLETED_STREAM)
    routes, adapter = FakeHumanRoutes(), FakeChannelAdapter()
    routes.response = ChannelHumanRouteResponse(
        outcome=HumanRouteOutcome.REJECTED,
        interaction_id=uuid.uuid4(),
        reason="HUMAN_INTERACTION_EXPIRED",
    )
    pipeline = _pipeline(console, runtime, catalog=catalog, routes=routes)

    await pipeline.handle(adapter, make_envelope("迟到的回答"))

    assert runtime.run_requests == []
    text = _sent_texts(adapter)[0]
    assert "没有生效" in text
    assert "超时" in text


async def test_route_failure_is_not_treated_as_accepted(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(COMPLETED_STREAM)
    routes, adapter = FakeHumanRoutes(), FakeChannelAdapter()
    routes.error = AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    pipeline = _pipeline(console, runtime, catalog=catalog, routes=routes)

    accepted = await pipeline.handle(adapter, make_envelope("普通消息"))

    assert accepted is False
    assert runtime.run_requests == []
    assert len(_sent_texts(adapter)) == 1


async def test_explicit_conversation_surface_skips_chat_human_routing(
    catalog: MessageCatalog,
) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(COMPLETED_STREAM)
    routes, adapter = FakeHumanRoutes(), _ExplicitConversationAdapter()
    routes.response = ChannelHumanRouteResponse(outcome=HumanRouteOutcome.ANSWERED)
    pipeline = _pipeline(console, runtime, catalog=catalog, routes=routes)

    await pipeline.handle(adapter, make_envelope("网页消息"))

    # 显式会话面按 interaction_id 在任务中心回答：聊天流不代答。
    assert routes.calls == []
    assert len(runtime.run_requests) == 1
    assert runtime.run_requests[0].conversation_id == adapter.conversation_id
