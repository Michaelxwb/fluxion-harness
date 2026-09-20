from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class HookEvent(StrEnum):
    USER_PROMPT = "user_prompt"
    PRE_MODEL = "pre_model"
    POST_MODEL = "post_model"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    ON_INTERRUPT = "on_interrupt"
    STOP = "stop"


class UnknownHookEventError(ValueError):
    def __init__(self, event: str) -> None:
        self.event = event
        super().__init__(f"unknown hook event: {event}")


@dataclass(frozen=True, slots=True)
class HookContext:
    event: HookEvent
    payload: Mapping[str, Any]


class HookHandler(Protocol):
    async def __call__(self, context: HookContext) -> HookContext | None: ...


def _coerce_event(event: HookEvent | str) -> HookEvent:
    try:
        return HookEvent(event)
    except ValueError as exc:
        raise UnknownHookEventError(str(event)) from exc


class HookPipeline:
    def __init__(self) -> None:
        self._handlers: dict[HookEvent, list[HookHandler]] = {event: [] for event in HookEvent}

    def register(self, event: HookEvent | str, handler: HookHandler) -> None:
        self._handlers[_coerce_event(event)].append(handler)

    async def run(self, event: HookEvent | str, payload: Mapping[str, Any]) -> HookContext:
        context = HookContext(event=_coerce_event(event), payload=payload)
        for handler in self._handlers[context.event]:
            updated = await handler(context)
            if updated is not None:
                context = updated
        return context
