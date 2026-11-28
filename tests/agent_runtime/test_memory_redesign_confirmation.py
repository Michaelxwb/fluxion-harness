"""B-15: real Gateway routing, Runtime API/resume, PG and model request capture."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    MemoryCommandReceipt,
    MemoryScope,
    RunInterrupt,
    RunRecord,
    RunSubmission,
    UserMemory,
    UserMemoryHistory,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import memory_hash
from muad_contracts.platform_settings import MemoryPolicySettings

from tests.agent_runtime.memory_confirmation_resume_support import confirmation_stack, rows
from tests.agent_runtime.memory_context_support import memory_text
from tests.agent_runtime.memory_gateway_support import Adapter, envelope, original_submission, pipeline
from tests.agent_runtime.memory_metric_support import metric
from tests.agent_runtime.memory_redesign_write_support import command, commands, context, upsert, writer

pytestmark = pytest.mark.integration


async def test_pending_memory_emits_live_public_confirmation(async_tool_database, monkeypatch):
    from muad_agent_runtime.application.public_events import project_public_stream_event
    from muad_im_gateway.application.sse import SseEvent
    from muad_im_gateway.application.webchat_streams import WebchatPublicProjector

    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        events = sorted(await rows(stack, CanonicalEvent), key=lambda event: event.seq)
        stage = next(event for event in events if event.stream_type == "run.stage" and
                     event.payload_json.get("stage") == "WAITING_INPUT")
        event = next(event for event in events if event.stream_type == "memory.confirmation_required")
        assert stage.seq < event.seq
        public = project_public_stream_event(event, {})
        assert public is not None
        assert public.data.submission_id == event.submission_id
        assert public.data.options[0].value == f"确认 {stack.proposal_id}"
        assert "有效至：" not in public.data.prompt
        assert public.data.expires_at is not None
        assert "有效至：" in event.payload_json["prompt"], "canonical prompt 保留期限供审计/API"
        text_prompt = event.payload_json["text_prompt"]
        assert "有效至：" not in text_prompt, "文本渠道不重复展示期限字段"
        assert "- 谓词：" in text_prompt, "文本渠道按列表逐行展示候选"
        assert "\n\n请回复“确认”或“拒绝”。" in text_prompt, "回复指令单独成行"
        projector = WebchatPublicProjector(run_id=stack.run.id, conversation_id=stack.run.conversation_id)
        projected = projector.project(
            SseEvent(type=event.stream_type, data=event.payload_json, run_id=str(stack.run.id),
                     seq=event.seq, timestamp=event.create_time.isoformat())
        )
        assert projected is not None
        assert projected.data == public.data
        old_payload = {key: value for key, value in event.payload_json.items() if key != "confirmation"}
        assert projector.project(
            SseEvent(type=event.stream_type, data=old_payload, run_id=str(stack.run.id),
                     seq=event.seq, timestamp=event.create_time.isoformat())
        ) is None, "旧事件缺少确认数据，必须能复现被过滤"


async def test_waiting_stream_remains_open_until_confirmation_resumes(async_tool_database, monkeypatch):
    from contextlib import aclosing

    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        events = stack.gateway.open_events(str(stack.run.id), tenant_id=stack.run.tenant_id)
        async with aclosing(events) as stream:
            async for event in stream:
                if event.type == "memory.confirmation_required":
                    break
            next_event = asyncio.create_task(anext(stream))
            try:
                done, _ = await asyncio.wait({next_event}, timeout=0.3)
                assert not done, "waiting for confirmation must not close the stream"
                await stack.submit(f"确认 {stack.proposal_id}")
                event = await asyncio.wait_for(next_event, timeout=10)
                assert event.seq is not None
            finally:
                if not next_event.done():
                    next_event.cancel()
                    await asyncio.gather(next_event, return_exceptions=True)


async def test_b15_gateway_replay_delivers_exact_persisted_prompt_and_confirmation_to_current_reply(
    async_tool_database,
    monkeypatch,
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        gateway, adapter = pipeline(stack), Adapter()
        original = await original_submission(stack)
        assert await gateway.handle(adapter, original)
        reply = adapter.replies[original.message_id]
        shown = next(
            event
            for event in await rows(stack, CanonicalEvent)
            if event.event_type == "MEMORY_CONFIRMATION_REQUIRED"
        )
        assert reply.texts == [shown.payload_json["text_prompt"]]
        assert shown.payload_json["text_prompt"] != shown.payload_json["prompt"]
        assert "- 谓词：" in shown.payload_json["text_prompt"], "企微按列表逐行展示字段"
        assert "有效至：" not in shown.payload_json["text_prompt"], "文本渠道不重复展示期限字段"
        assert "\n\n请回复“确认”或“拒绝”。" in shown.payload_json["text_prompt"], (
            "回复指令必须单独成行"
        )
        displayed = tuple(reply.texts)
        assert reply.finished == 1 and await rows(stack, UserMemory) == []
        incoming = envelope(f"确认 {stack.proposal_id}")
        assert await gateway.handle(adapter, incoming)
        saved = (await rows(stack, UserMemory))[0]
        assert saved.author == "USER" and saved.content_json == {"value": "blue"}
        assert saved.confirmed_count == saved.version == 1
        assert "已保存" in "".join(adapter.replies[incoming.message_id].chunks)
        assert tuple(adapter.replies[original.message_id].texts) == displayed
        assert await gateway.handle(adapter, incoming)
        assert len(await rows(stack, UserMemoryHistory)) == len(await rows(stack, MemoryCommandReceipt)) == 1


async def test_webchat_confirmation_receipt_is_history_only(async_tool_database, monkeypatch):
    """WebChat 的确认回执不进入 SSE 正文：历史刷新负责展示，避免和流式正文瞬时重复。"""
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        async with stack.factory.begin() as session:
            run = await session.get(RunRecord, stack.run.id)
            assert run is not None
            run.channel_json = {**(run.channel_json or {}), "type": "WEBCHAT"}
        await stack.submit(f"确认 {stack.proposal_id}")
        resolved = next(
            event
            for event in await rows(stack, CanonicalEvent)
            if event.event_type == "MEMORY_CONFIRMATION_RESOLVED"
        )
        assert resolved.stream_type is None


@pytest.mark.parametrize("answer", ["1", "是", "确认 invalid", "确认 00000000-0000-0000-0000-000000000000"])
async def test_b15_gateway_invalid_answer_preserves_waiting_and_exact_pending_content(
    async_tool_database,
    monkeypatch,
    answer,
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        gateway, adapter = pipeline(stack), Adapter()
        assert await gateway.handle(adapter, envelope(answer)) is False
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING"
        assert (await rows(stack, RunRecord))[0].status == "WAITING_INPUT"
        assert await rows(stack, UserMemory) == [] and stack.captured == []


@pytest.mark.parametrize(
    "answer,saved", [("确认", True), ("拒绝", False), ("确认。", True), ("拒绝！", False)]
)
async def test_b15_gateway_short_verb_consumes_the_only_pending_confirmation(
    async_tool_database, monkeypatch, answer, saved
):
    """企微等文本渠道只回复“确认/拒绝”即可：恰有一个待确认时不需要抄候选编号。"""
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        gateway, adapter = pipeline(stack), Adapter()
        incoming = envelope(answer)
        assert await gateway.handle(adapter, incoming)
        assert (await rows(stack, RunInterrupt))[0].status == "RESOLVED"
        assert bool(await rows(stack, UserMemory)) is saved
        reply = "".join(adapter.replies[incoming.message_id].chunks)
        assert ("已保存" if saved else "已拒绝") in reply


def test_short_confirmation_without_pending_is_a_normal_message() -> None:
    from muad_agent_runtime.application.memory_confirmation_resume import prepare_confirmation

    assert prepare_confirmation([], "确认") is None
    assert prepare_confirmation([], "拒绝") is None


def _human_input_interrupt(*, options: list[dict[str, str]], status: str = "WAITING"):
    from muad_agent_runtime.infrastructure.models.runtime import RunInterrupt

    return RunInterrupt(
        tenant_id="t",
        run_id=uuid4(),
        conversation_id=uuid4(),
        interrupt_type="HUMAN_INPUT",
        prompt_text="pick one",
        options_json=options,
        status=status,
    )


def test_binding_with_pending_option_human_input_resumes_generically() -> None:
    """带候选的 ask_user 也经网页卡片 resume：binding 放行给 generic 路径，不报记忆过期。"""
    from muad_agent_runtime.application.dto import ConfirmationBinding
    from muad_agent_runtime.application.memory_confirmation_resume import prepare_confirmation

    interrupt = _human_input_interrupt(options=[{"id": "approve", "name": "批准"}])
    binding = ConfirmationBinding(submission_id=uuid4(), source_event_id=uuid4())
    # 没有 MEMORY_CONFIRMATION 待确认，但有带候选的 HUMAN_INPUT → 不报过期，交由 generic 处理。
    assert prepare_confirmation([interrupt], "approve", confirmation=binding) is None


def test_binding_with_pending_free_text_human_input_resumes_generically() -> None:
    """无候选的自由问答（free_text）也是待回答的 HUMAN_INPUT：binding 放行给 generic，不报过期。

    自由问答的 `options_json` 是空数组，但它是网页可消费的 `HUMAN_INPUT`；不能因为候选为空就
    误判为「确认了别的东西」而过期（否则 free_text 卡片提交必然 410）。
    """
    from muad_agent_runtime.application.dto import ConfirmationBinding
    from muad_agent_runtime.application.memory_confirmation_resume import prepare_confirmation

    binding = ConfirmationBinding(submission_id=uuid4(), source_event_id=uuid4())
    interrupt = _human_input_interrupt(options=[])  # 无候选的自由问答
    assert prepare_confirmation([interrupt], "anything", confirmation=binding) is None


def test_binding_without_any_pending_is_still_expired() -> None:
    """真正没有待回答的中断时，带 binding 的 resume 仍显式过期（不静默降级）。"""
    from muad_agent_runtime.application.dto import ConfirmationBinding
    from muad_agent_runtime.application.memory_confirmation_resume import prepare_confirmation
    from muad_api import AppError
    from muad_api.error_codes import ErrorCode

    binding = ConfirmationBinding(submission_id=uuid4(), source_event_id=uuid4())
    # 已 RESOLVED 的 HUMAN_INPUT 不再等待回答，等价于没有 pending。
    interrupt = _human_input_interrupt(options=[], status="RESOLVED")
    with pytest.raises(AppError) as excinfo:
        prepare_confirmation([interrupt], "anything", confirmation=binding)
    assert excinfo.value.code == ErrorCode.MEMORY_CONFIRMATION_EXPIRED


def test_parse_options_validates_and_normalizes() -> None:
    from muad_agent_core.tools import ToolError
    from muad_agent_runtime.application.human_question import _parse_options

    assert _parse_options(None) == []
    assert _parse_options([{"id": "approve", "label": "批准"}]) == [
        {"id": "approve", "label": "批准"}
    ]
    with pytest.raises(ToolError):
        _parse_options([{"id": "dup", "label": "a"}, {"id": "dup", "label": "b"}])
    with pytest.raises(ToolError):
        _parse_options([{"id": "", "label": "a"}])
    with pytest.raises(ToolError):
        _parse_options([])


def test_memory_confirmation_prompt_formats_expiry_in_user_timezone() -> None:
    """文本渠道的期限按冻结时区展示，避免 UTC 时间看起来像已过期。"""
    from datetime import UTC, datetime

    from muad_agent_runtime.application.memory_proposals import _format_expiry

    expires_at = datetime(2026, 11, 5, 7, 11, 25, tzinfo=UTC)
    assert _format_expiry(expires_at, "Asia/Shanghai") == "2026-11-05 15:11:25 +0800"
    assert _format_expiry(expires_at, "Bad/Zone") == "2026-11-05 07:11:25 +0000"


@pytest.mark.parametrize("status", ["RUNNING", "WAITING_TOOL"])
async def test_memory_command_bypasses_gateway_stream_lock_but_runtime_busy_cannot_create_a_run(
    async_tool_database,
    monkeypatch,
    status,
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        from contextlib import suppress

        gateway, adapter = pipeline(stack), Adapter()
        incoming = envelope("/memory remember output_style blue")
        async with stack.factory.begin() as session:
            run = await session.get(RunRecord, stack.run.id)
            run.status = status
        consumer = asyncio.create_task(gateway.consume(adapter))
        try:
            async with gateway._route_lock(incoming):
                await adapter.push(incoming)
                for _ in range(100):
                    if (
                        incoming.message_id in adapter.replies
                        and adapter.replies[incoming.message_id].finished
                    ):
                        break
                    await asyncio.sleep(0.01)
                assert incoming.message_id in adapter.replies
                reply = adapter.replies[incoming.message_id]
                assert reply.finished == 1 and reply.texts
                assert "/stop" in "".join(reply.texts) and "已有任务执行中" in "".join(reply.texts)
        finally:
            consumer.cancel()
            with suppress(asyncio.CancelledError):
                await consumer
        assert len(await rows(stack, RunRecord)) == 1 and await rows(stack, UserMemory) == []
        assert (await rows(stack, RunRecord))[0].status == status


async def test_b15_runtime_only_exact_real_memory_command_can_authorize_candidate_without_prompt(
    async_tool_database,
):
    from tests.agent_runtime.memory_confirmation_support import candidate, service, source

    factory, run, identity, submission = await source(async_tool_database)
    async with factory.begin() as session:
        event = await session.scalar(
            sa.select(CanonicalEvent).where(CanonicalEvent.submission_id == submission)
        )
        event.payload_json = {**event.payload_json, "text": "/memory remember output_style blue"}
    memory = service(factory, identity, submission)
    receipt = await memory.propose(candidate(), call_id="explicit-command")
    assert receipt.saved is True and memory.suspend_request is None
    replay = await memory.propose(candidate(), call_id="another-model-call")
    assert replay == receipt and memory.suspend_request is None
    async with factory() as session:
        saved = await session.scalar(sa.select(UserMemory).where(UserMemory.tenant_id == run.tenant_id))
        assert saved.author == "USER" and saved.confirmation_event_id == event.id
        assert saved.content_json == {"value": "blue"} and saved.source_quote == event.payload_json["text"]
        assert (
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(UserMemoryHistory)
                .where(UserMemoryHistory.tenant_id == run.tenant_id)
            )
            == 1
        )


@pytest.mark.parametrize(
    "change",
    [{"value": "red"}, {"predicate": "language"}, {"content": {"value": "blue", "unit": "pt"}}],
)
async def test_b15_memory_command_cannot_authorize_different_model_parameters(async_tool_database, change):
    from tests.agent_runtime.memory_confirmation_support import candidate, service, source

    factory, run, identity, submission = await source(async_tool_database)
    async with factory.begin() as session:
        event = await session.scalar(
            sa.select(CanonicalEvent).where(CanonicalEvent.submission_id == submission)
        )
        event.payload_json = {**event.payload_json, "text": "/memory remember output_style blue"}
    memory = service(factory, identity, submission)
    receipt = await memory.propose(candidate(**change), call_id="different-candidate")
    assert receipt.saved is False and memory.suspend_request is not None
    async with factory() as session:
        assert (
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(UserMemory)
                .where(UserMemory.tenant_id == run.tenant_id)
            )
            == 0
        )


async def test_local_b15_real_gateway_confirmation_saves_displayed_content_and_new_receipt(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        before = (await rows(stack, RunInterrupt))[0]
        events = await stack.submit(f"确认 {stack.proposal_id}")
        memories, history, receipts = (
            await rows(stack, UserMemory),
            await rows(stack, UserMemoryHistory),
            await rows(stack, MemoryCommandReceipt),
        )
        assert len(memories) == len(history) == len(receipts) == 1
        row = memories[0]
        assert row.author == "USER" and row.content_json == {"value": "blue"}
        assert row.explicit_request and row.confirmed_count == row.version == 1
        assert row.source_quote == "记住蓝色" and "blue" in before.prompt_text
        canonical = await rows(stack, CanonicalEvent)
        user = next(event for event in canonical if event.id == row.confirmation_event_id)
        assert user.event_type == "USER_MESSAGE" and user.payload_json["text"] == f"确认 {stack.proposal_id}"
        assert user.payload_json["source_scope_revision"] == 0
        resolved = next(event for event in canonical if event.event_type == "MEMORY_CONFIRMATION_RESOLVED")
        assert resolved.payload_json["saved"] is True and resolved.payload_json["version"] == row.version
        assert resolved.payload_json["delta"] == "记忆已保存。"
        assert '"saved":true' in resolved.payload_json["text"]
        assert resolved.source_event_id == stack.proposal_id
        assert [event.payload_json["preview"] for event in canonical if event.event_type == "TOOL_CALL"] == [
            '{"saved":false}'
        ]
        assert events[-1].data["status"] == "COMPLETED"
        assert row.memory_key in memory_text(stack.captured[0])
        messages = stack.captured[0]["messages"]
        assert messages[-1]["role"] == "user", (
            "确认结果不得合成 assistant 消息：带预填 assistant 的请求会被供应商拒绝，"
            "最后一条必须是 user"
        )
        assert "记忆已保存" in str(messages[-1]["content"])
        assert not any(
            item["role"] == "assistant" and "记忆已保存" in str(item.get("content"))
            for item in messages
        ), "确认结果是人类可见事实，不是模型说过的话"


async def test_local_b15_reject_consumes_only_memory_interrupt_and_resumes_without_saving(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        async with stack.factory.begin() as session:
            session.add(
                RunInterrupt(
                    tenant_id=stack.run.tenant_id,
                    run_id=stack.run.id,
                    conversation_id=stack.run.conversation_id,
                    interrupt_type="OTHER",
                    prompt_text="other",
                )
            )
        events = await stack.submit(f"拒绝 {stack.proposal_id}")
        assert await rows(stack, UserMemory) == await rows(stack, MemoryCommandReceipt) == []
        interrupts = {row.interrupt_type: row for row in await rows(stack, RunInterrupt)}
        assert interrupts["MEMORY_CONFIRMATION"].status == "RESOLVED"
        assert interrupts["MEMORY_CONFIRMATION"].resolution_json["outcome"] == "REJECTED"
        assert interrupts["OTHER"].status == "WAITING"
        assert events[-1].data["status"] == "COMPLETED" and len(stack.captured) == 1


@pytest.mark.parametrize("text", ["确认 2", "yes", "不要确认 {id}", "确认 {id} 吗", '{"action":"CONFIRM"}'])
async def test_local_b15_invalid_input_keeps_waiting_and_writes_nothing(
    async_tool_database, monkeypatch, text
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        before = len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission))
        with pytest.raises(AppError) as exc:
            await stack.submit(text.format(id=stack.proposal_id) if "{id}" in text else text)
        assert exc.value.code == ErrorCode.MEMORY_CONFIRMATION_INVALID
        assert (await rows(stack, RunRecord))[0].status == "WAITING_INPUT"
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING"
        assert before == (len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission)))
        assert stack.captured == [] and await rows(stack, UserMemory) == []


@pytest.mark.parametrize("stale", ["expiry", "version", "epoch", "hash", "hash_recomputed", "user"])
async def test_local_b15_stale_valid_choice_resumes_without_granting_memory(
    async_tool_database, monkeypatch, stale
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        if stale in ("version", "epoch"):
            ctx = context(tenant=stack.run.tenant_id, user=stack.run.user_id)
            from muad_agent_runtime.application.memory_service import MemoryService

            from tests.agent_runtime.memory_confirmation_support import candidate as proposal_candidate

            if stale == "version":
                await MemoryService(stack.factory).apply_candidate(
                    ctx,
                    proposal_candidate(value="red").normalized(taxonomy_version=1),
                    command_id="stale-version-setup",
                    endpoint="test.setup",
                )
            else:
                key = "unrelated"
                await writer(async_tool_database).execute(ctx, command(upsert(key, "red")))
            if stale == "epoch":
                await writer(async_tool_database).execute(
                    ctx,
                    command(commands().MemoryMutation(action="DELETE", memory_key=key, expected_version=1)),
                )
        async with stack.factory.begin() as session:
            interrupt = await session.get(RunInterrupt, stack.proposal_id)
            if stale == "expiry":
                interrupt.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            elif stale not in ("version", "epoch"):
                proposal = dict(interrupt.proposal_json)
                if stale == "user":
                    proposal["user_id"] = str(uuid4())
                else:
                    proposal["candidate"] = {**proposal["candidate"], "value": "red"}
                    if stale == "hash_recomputed":
                        proposal["candidate_hash"] = memory_hash(proposal["candidate"])
                interrupt.proposal_json = proposal
        events = await stack.submit(f"确认 {stack.proposal_id}")
        assert all(row.author != "USER" for row in await rows(stack, UserMemory))
        interrupt = (await rows(stack, RunInterrupt))[0]
        assert interrupt.status == "RESOLVED" and interrupt.resolution_json["outcome"] == "STALE"
        assert events[-1].data["status"] == "COMPLETED"


async def test_local_b15_expired_absolute_run_deadline_prevents_save_and_resume(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        async with stack.factory.begin() as session:
            run = await session.get(RunRecord, stack.run.id)
            run.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
        with pytest.raises(AppError) as exc:
            await stack.submit(f"确认 {stack.proposal_id}")
        assert exc.value.code == ErrorCode.MEMORY_CONFIRMATION_EXPIRED
        assert (await rows(stack, RunRecord))[0].status == "WAITING_INPUT"
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING"
        assert await rows(stack, UserMemory) == [] and stack.captured == []


async def test_local_b15_bound_resume_without_pending_confirmation_is_expired_not_a_normal_message(
    async_tool_database, monkeypatch
):
    """网页 resume 带原提议身份时，没有可消费中断只能显式过期，不得降级成普通消息执行模型。"""
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        async with stack.factory.begin() as session:
            interrupt = await session.get(RunInterrupt, stack.proposal_id)
            interrupt.status = "RESOLVED"
        response = await stack.explicit(
            f"确认 {stack.proposal_id}", confirmation=(uuid4(), uuid4())
        )
        assert response.status_code == 410
        assert response.json()["code"] == "MEMORY_CONFIRMATION_EXPIRED"
        assert (await rows(stack, RunRecord))[0].status == "WAITING_INPUT"
        assert await rows(stack, UserMemory) == [] and stack.captured == []


async def test_local_b15_other_user_cannot_confirm_in_gateway_or_explicit_resume(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        wrong = uuid4()
        with pytest.raises(AppError):
            await stack.submit(f"确认 {stack.proposal_id}", user=wrong)
        response = await stack.explicit(f"确认 {stack.proposal_id}", user=wrong)
        assert response.status_code == 404
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING"
        assert await rows(stack, UserMemory) == [] and stack.captured == []
        response = await stack.explicit(f"确认 {stack.proposal_id}")
        assert response.status_code == 200 and len(await rows(stack, UserMemory)) == 1


async def test_local_b15_repeated_message_replays_and_changed_payload_is_rejected(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        first = await stack.submit(f"确认 {stack.proposal_id}", message_id="stable-confirm")
        replay = await stack.submit(f"确认 {stack.proposal_id}", message_id="stable-confirm")
        assert first == replay and len(stack.captured) == 1
        assert (await rows(stack, UserMemory))[0].confirmed_count == 1
        assert len(await rows(stack, UserMemoryHistory)) == len(await rows(stack, MemoryCommandReceipt)) == 1
        with pytest.raises(AppError) as exc:
            await stack.submit(f"拒绝 {stack.proposal_id}", message_id="stable-confirm")
        assert exc.value.code == ErrorCode.IDEMPOTENCY_MISMATCH


async def test_local_b15_result_event_failure_rolls_back_fact_history_receipt_and_resume(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        written = metric("memory_write_total", outcome="OK")
        confirmed = metric("memory_confirmation_total", outcome="CONFIRMED")
        failed = metric("memory_confirmation_total", outcome="ERROR")
        async with stack.factory.begin() as session:
            await session.execute(
                sa.text(
                    "CREATE FUNCTION runtime.fail_confirmation_result() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ "
                    "BEGIN IF NEW.event_type='MEMORY_CONFIRMATION_RESOLVED' THEN RAISE EXCEPTION 'fault'; "
                    "END IF; RETURN NEW; END $$"
                )
            )
            await session.execute(
                sa.text(
                    "CREATE TRIGGER fail_confirmation_result BEFORE INSERT ON runtime.canonical_event "
                    "FOR EACH ROW EXECUTE FUNCTION runtime.fail_confirmation_result()"
                )
            )
        before = len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission))
        with pytest.raises(AppError) as exc:
            await stack.submit(f"确认 {stack.proposal_id}")
        assert exc.value.code == ErrorCode.COMMON_INTERNAL_ERROR
        assert metric("memory_write_total", outcome="OK") == written
        assert metric("memory_confirmation_total", outcome="CONFIRMED") == confirmed
        assert metric("memory_confirmation_total", outcome="ERROR") == failed + 1
        assert (
            await rows(stack, UserMemory)
            == await rows(stack, UserMemoryHistory)
            == await rows(stack, MemoryCommandReceipt)
            == []
        )
        assert before == (len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission)))
        assert (await rows(stack, RunRecord))[0].status == "WAITING_INPUT"
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING" and stack.captured == []


async def test_local_b15_frozen_write_disabled_prevents_confirmation(async_tool_database, monkeypatch):
    async with confirmation_stack(
        async_tool_database, monkeypatch, policy=MemoryPolicySettings(write_enabled=False)
    ) as stack:
        with pytest.raises(AppError) as exc:
            await stack.submit(f"确认 {stack.proposal_id}")
        assert exc.value.code == ErrorCode.FORBIDDEN
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING"
        assert await rows(stack, UserMemory) == []


async def test_local_b15_concurrent_same_message_replays_one_durable_confirmation(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        replies = await asyncio.gather(
            stack.submit(f"确认 {stack.proposal_id}", message_id="concurrent-confirm"),
            stack.submit(f"确认 {stack.proposal_id}", message_id="concurrent-confirm"),
        )
        assert replies[0] == replies[1]
        assert len(stack.captured) == 1
        assert (await rows(stack, UserMemory))[0].confirmed_count == 1
        assert len(await rows(stack, MemoryCommandReceipt)) == 1


async def test_local_b15_scope_lock_wait_rechecks_absolute_deadline_and_rolls_back(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        async with stack.factory.begin() as session:
            session.add(MemoryScope(tenant_id=stack.run.tenant_id, user_id=stack.run.user_id))
            await session.execute(
                sa.update(RunRecord)
                .where(RunRecord.id == stack.run.id)
                .values(deadline_at=sa.func.statement_timestamp() + sa.text("interval '5 seconds'"))
            )
        async with stack.factory.begin() as blocker:
            await blocker.scalar(
                sa.select(MemoryScope).where(MemoryScope.tenant_id == stack.run.tenant_id).with_for_update()
            )
            pending = asyncio.create_task(stack.submit(f"确认 {stack.proposal_id}"))
            try:
                for _ in range(400):
                    async with async_tool_database.connect() as probe:
                        waiting = await probe.scalar(
                            sa.text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                "WHERE datname=current_database() "
                                "AND wait_event_type='Lock' AND query LIKE '%memory_scope%')"
                            )
                        )
                    if waiting:
                        break
                    await asyncio.sleep(0.01)
                assert waiting, "resume must reach the real scope row lock"
                # 检测到锁等待后再跨过 5s 绝对期限，验证醒来重检而不是仅凭旧快照继续。
                await asyncio.sleep(5.05)
            finally:
                await blocker.rollback()
            with pytest.raises(AppError) as exc:
                await pending
        assert exc.value.code == ErrorCode.MEMORY_CONFIRMATION_EXPIRED
        assert (await rows(stack, RunRecord))[0].status == "WAITING_INPUT"
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING"
        assert await rows(stack, UserMemory) == await rows(stack, MemoryCommandReceipt) == []
        assert stack.captured == []


async def test_local_b15_quota_failure_keeps_candidate_and_all_inbound_effects_uncommitted(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(
        async_tool_database, monkeypatch, policy=MemoryPolicySettings(max_entries_per_user=1)
    ) as stack:
        ctx = context(tenant=stack.run.tenant_id, user=stack.run.user_id)
        await writer(async_tool_database).execute(ctx, command(upsert("protected", "existing")))
        before = len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission))
        with pytest.raises(AppError) as exc:
            await stack.submit(f"确认 {stack.proposal_id}")
        assert exc.value.code == ErrorCode.MEMORY_QUOTA_EXCEEDED
        assert before == (len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission)))
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING"
        assert (await rows(stack, RunRecord))[0].status == "WAITING_INPUT"
        assert [row.memory_key for row in await rows(stack, UserMemory)] == ["protected"]
        assert len(await rows(stack, MemoryCommandReceipt)) == len(await rows(stack, UserMemoryHistory)) == 1
        assert stack.captured == []


async def test_local_b15_confirmation_reports_and_uses_frozen_ttl(async_tool_database, monkeypatch):
    async with confirmation_stack(
        async_tool_database, monkeypatch, policy=MemoryPolicySettings(ttl_days_user=2)
    ) as stack:
        before = datetime.now(UTC)
        await stack.submit(f"确认 {stack.proposal_id}")
        memory = (await rows(stack, UserMemory))[0]
        assert before + timedelta(days=2) <= memory.expires_at <= datetime.now(UTC) + timedelta(days=2)
        resolved = next(
            row
            for row in await rows(stack, CanonicalEvent)
            if row.event_type == "MEMORY_CONFIRMATION_RESOLVED"
        )
        assert datetime.fromisoformat(resolved.payload_json["expires_at"]) == memory.expires_at


async def test_local_b15_explicit_resume_keeps_real_input_id_distinct_from_transport_key(
    async_tool_database, monkeypatch
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        response = await stack.explicit(
            f"确认 {stack.proposal_id}", message_id="real-input-id", key="transport-key"
        )
        assert response.status_code == 200
        memory = (await rows(stack, UserMemory))[0]
        user = next(
            row for row in await rows(stack, CanonicalEvent) if row.id == memory.confirmation_event_id
        )
        assert user.payload_json["message_id"] == "real-input-id"
        submission = next(row for row in await rows(stack, RunSubmission) if row.id == user.submission_id)
        assert submission.idempotency_key == "transport-key"


@pytest.mark.parametrize("terminal", ["COMPLETED", "FAILED", "CANCELLED"])
async def test_local_b15_terminal_run_takes_priority_over_confirmation(
    async_tool_database, monkeypatch, terminal
):
    async with confirmation_stack(async_tool_database, monkeypatch) as stack:
        async with stack.factory.begin() as session:
            await session.execute(
                sa.update(RunRecord).where(RunRecord.id == stack.run.id).values(status=terminal)
            )
        before = len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission))
        response = await stack.explicit(f"确认 {stack.proposal_id}")
        assert response.status_code == 200 and terminal in response.text
        assert (await rows(stack, RunRecord))[0].status == terminal
        assert (await rows(stack, RunInterrupt))[0].status == "WAITING"
        assert before == (len(await rows(stack, CanonicalEvent)), len(await rows(stack, RunSubmission)))
        assert await rows(stack, UserMemory) == [] and stack.captured == []


async def test_generic_option_human_input_projects_confirmation_card(async_tool_database):
    """带候选的 ask_user（HUMAN_INPUT + options_json）在历史投影里显示为确认卡片。

    无候选的自由问答不投影卡片（仍是纯文本），保证只有「可点选」的问询才出卡片。
    """
    from muad_agent_runtime.application.webchat_history import WebchatHistoryService

    from tests.agent_runtime.memory_confirmation_support import source

    factory, run, _identity, _submission = await source(async_tool_database)
    run.status = "WAITING_INPUT"
    async with factory.begin() as session:
        (await session.get(RunRecord, run.id)).status = "WAITING_INPUT"
        session.add(
            RunInterrupt(
                tenant_id=run.tenant_id,
                run_id=run.id,
                conversation_id=run.conversation_id,
                interrupt_type="HUMAN_INPUT",
                prompt_text="是否执行该审批命令？",
                options_json=[{"id": "approve", "name": "批准"}, {"id": "reject", "name": "拒绝"}],
                status="WAITING",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
    async with factory() as session:
        card = await WebchatHistoryService(session)._confirmation_view(run)
    assert card is not None
    assert card.prompt == "是否执行该审批命令？"
    assert [(opt.value, opt.label) for opt in card.options] == [("approve", "批准"), ("reject", "拒绝")]


async def test_free_form_human_input_has_no_confirmation_card(async_tool_database):
    """无候选的 ask_user 挂起也产生卡片，但为纯自由文本（free_text=True、options 空、无按钮）。"""
    from muad_agent_runtime.application.webchat_history import WebchatHistoryService

    from tests.agent_runtime.memory_confirmation_support import source

    factory, run, _identity, _submission = await source(async_tool_database)
    run.status = "WAITING_INPUT"
    async with factory.begin() as session:
        (await session.get(RunRecord, run.id)).status = "WAITING_INPUT"
        session.add(
            RunInterrupt(
                tenant_id=run.tenant_id,
                run_id=run.id,
                conversation_id=run.conversation_id,
                interrupt_type="HUMAN_INPUT",
                prompt_text="请提供 ticket",
                options_json=[],
                status="WAITING",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
    async with factory() as session:
        card = await WebchatHistoryService(session)._confirmation_view(run)
    assert card is not None
    assert card.free_text is True
    assert card.options == []
    assert card.allow_custom is True


def _option_interrupt(*, allow_custom: bool | None):
    from muad_agent_runtime.infrastructure.models.runtime import RunInterrupt

    return RunInterrupt(
        tenant_id="t",
        run_id=uuid4(),
        conversation_id=uuid4(),
        interrupt_type="HUMAN_INPUT",
        prompt_text="选择客户",
        options_json=[{"id": "c1", "name": "客户一"}, {"id": "c2", "name": "客户二"}],
        proposal_json={"allow_custom": allow_custom} if allow_custom is not None else None,
        status="WAITING",
    )


async def test_resume_rejects_free_text_when_custom_not_allowed(async_tool_database):
    """默认（allow_custom 缺省/false）带选项时只接受候选 id，自定义文本被拒。"""
    from muad_agent_runtime.application.run_resume import resolve_generic_interrupts
    from muad_api import AppError
    from muad_api.error_codes import ErrorCode

    from tests.agent_runtime.memory_confirmation_support import source

    factory, _run, _identity, _submission = await source(async_tool_database)
    for allow_custom in (None, False):
        row = _option_interrupt(allow_custom=allow_custom)
        async with factory() as session:
            with pytest.raises(AppError) as excinfo:
                await resolve_generic_interrupts(session, [row], "客户三")
            assert excinfo.value.code == ErrorCode.COMMON_VALIDATION_ERROR
            # 命中候选 id 仍然可以
            row.status = "WAITING"
            await resolve_generic_interrupts(session, [row], "c1")
            assert row.resolution_json == {"input": "c1"}


async def test_resume_accepts_free_text_when_custom_allowed(async_tool_database):
    """allow_custom=true 时，选项之外的自定义文本被接受并回灌给模型。"""
    from muad_agent_runtime.application.run_resume import resolve_generic_interrupts

    from tests.agent_runtime.memory_confirmation_support import source

    factory, _run, _identity, _submission = await source(async_tool_database)
    row = _option_interrupt(allow_custom=True)
    async with factory() as session:
        await resolve_generic_interrupts(session, [row], "以上都不对，用客户四")
    assert row.status == "RESOLVED"
    assert row.resolution_json == {"input": "以上都不对，用客户四"}


def _multi_interrupt(*, allow_custom: bool = False):
    from muad_agent_runtime.infrastructure.models.runtime import RunInterrupt

    return RunInterrupt(
        tenant_id="t",
        run_id=uuid4(),
        conversation_id=uuid4(),
        interrupt_type="HUMAN_INPUT",
        prompt_text="选择客户（可多选）",
        options_json=[
            {"id": "c1", "name": "客户一"},
            {"id": "c2", "name": "客户二"},
            {"id": "c3", "name": "客户三"},
        ],
        proposal_json={"allow_custom": allow_custom, "multi_select": True},
        status="WAITING",
    )


async def test_resume_multi_select_accepts_multiple_ids(async_tool_database):
    """多选：换行分隔的多个 id 全部命中候选时接受，回灌含 selections。"""
    from muad_agent_runtime.application.run_resume import resolve_generic_interrupts

    from tests.agent_runtime.memory_confirmation_support import source

    factory, _run, _identity, _submission = await source(async_tool_database)
    row = _multi_interrupt()
    async with factory() as session:
        await resolve_generic_interrupts(session, [row], "c1\nc3")
    assert row.status == "RESOLVED"
    assert row.resolution_json == {"input": "c1\nc3", "selections": ["c1", "c3"]}


async def test_resume_multi_select_rejects_unknown_id(async_tool_database):
    """多选：含未知 id 且不允许自定义时拒绝。"""
    from muad_agent_runtime.application.run_resume import resolve_generic_interrupts
    from muad_api import AppError
    from muad_api.error_codes import ErrorCode

    from tests.agent_runtime.memory_confirmation_support import source

    factory, _run, _identity, _submission = await source(async_tool_database)
    row = _multi_interrupt()
    async with factory() as session:
        with pytest.raises(AppError) as excinfo:
            await resolve_generic_interrupts(session, [row], "c1\ncX")
        assert excinfo.value.code == ErrorCode.COMMON_VALIDATION_ERROR


async def test_resume_multi_select_allows_custom_when_enabled(async_tool_database):
    """多选 + allow_custom：混入未知项也接受（自由补充），并保留全部。"""
    from muad_agent_runtime.application.run_resume import resolve_generic_interrupts

    from tests.agent_runtime.memory_confirmation_support import source

    factory, _run, _identity, _submission = await source(async_tool_database)
    row = _multi_interrupt(allow_custom=True)
    async with factory() as session:
        await resolve_generic_interrupts(session, [row], "c1\n客户四")
    assert row.status == "RESOLVED"
    assert row.resolution_json == {"input": "c1\n客户四", "selections": ["c1", "客户四"]}
