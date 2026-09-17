import pytest
from muad_agent_core.hooks import (
    HookContext,
    HookEvent,
    HookPipeline,
    UnknownHookEventError,
)


async def _noop(context: HookContext) -> None:
    return None


def test_hook_event_members_match_design_doc() -> None:
    assert {member.value for member in HookEvent} == {
        "user_prompt",
        "pre_tool_use",
        "post_tool_use",
        "stop",
    }


async def test_handlers_execute_in_registration_order() -> None:
    calls: list[str] = []

    async def first(context: HookContext) -> None:
        calls.append("first")

    async def second(context: HookContext) -> HookContext:
        calls.append("second")
        return HookContext(event=context.event, payload={**context.payload, "second": True})

    pipeline = HookPipeline()
    pipeline.register(HookEvent.PRE_TOOL_USE, first)
    pipeline.register(HookEvent.PRE_TOOL_USE, second)

    result = await pipeline.run(HookEvent.PRE_TOOL_USE, {"a": 1})

    assert calls == ["first", "second"]
    assert result.event is HookEvent.PRE_TOOL_USE
    assert result.payload == {"a": 1, "second": True}


async def test_handler_exception_propagates() -> None:
    calls: list[str] = []

    async def ok(context: HookContext) -> None:
        calls.append("ok")

    async def boom(context: HookContext) -> None:
        raise RuntimeError("hook failed")

    pipeline = HookPipeline()
    pipeline.register(HookEvent.STOP, ok)
    pipeline.register(HookEvent.STOP, boom)

    with pytest.raises(RuntimeError, match="hook failed"):
        await pipeline.run(HookEvent.STOP, {"a": 1})

    assert calls == ["ok"]


async def test_unknown_event_is_rejected_at_run_time() -> None:
    pipeline = HookPipeline()

    with pytest.raises(UnknownHookEventError) as error:
        await pipeline.run("not_an_event", {})

    assert error.value.event == "not_an_event"
    assert isinstance(error.value, ValueError)


def test_unknown_event_is_rejected_at_registration_time() -> None:
    pipeline = HookPipeline()

    with pytest.raises(UnknownHookEventError):
        pipeline.register("not_an_event", _noop)


async def test_event_without_handlers_keeps_payload_unchanged() -> None:
    pipeline = HookPipeline()

    result = await pipeline.run(HookEvent.USER_PROMPT, {"a": 1})

    assert result.event is HookEvent.USER_PROMPT
    assert result.payload == {"a": 1}


async def test_handlers_are_scoped_per_event() -> None:
    calls: list[str] = []

    async def handler(context: HookContext) -> None:
        calls.append(context.event.value)

    pipeline = HookPipeline()
    pipeline.register(HookEvent.POST_TOOL_USE, handler)

    await pipeline.run(HookEvent.PRE_TOOL_USE, {})
    assert calls == []

    await pipeline.run(HookEvent.POST_TOOL_USE, {})
    assert calls == ["post_tool_use"]
