from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from muad_api import AppError
from muad_api.catalog import MessageCatalog
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    ChannelBindRequest,
    ChannelContext,
    ChannelEnvelope,
    ChannelResolveRequest,
    ChannelResolveResponse,
    DeliveryMessage,
    DeliveryRouteInput,
    MessageInput,
    RunRequest,
)

from ..channels.base import ChannelAdapter, ChannelAdapterUnavailable, StreamFinalizer
from ..infrastructure.dedupe import DedupeStore, DedupeStoreError, is_duplicate
from .console_client import ConsoleClientPort
from .runtime_client import RuntimeClientPort, SseEvent

logger = logging.getLogger(__name__)

DEDUPE_PREFIX = "im:dedupe"
DEDUPE_TTL_SEC = 600
DELTA_FLUSH_CHARS = 40

RUN_CREATED_EVENT = "run.created"
MESSAGE_DELTA_EVENT = "message.delta"
TASK_ACCEPTED_EVENT = "task.accepted"
INTERRUPT_REQUIRED_EVENT = "interrupt.required"
RUN_COMPLETED_EVENT = "run.completed"
RUN_FAILED_EVENT = "run.failed"

BIND_COMMAND = "/bind"
NEW_COMMAND = "/new"
STOP_COMMAND = "/stop"
SKILLS_COMMAND = "/skills"

BIND_SUCCESS_TEXT = "绑定成功"
BIND_USAGE_TEXT = "用法：/bind <绑定码>"
UNBOUND_TEXT = "请先使用 /bind <绑定码> 完成身份绑定"
NO_PERMISSION_TEXT = "当前账号未获得该智能体使用权限"
SKILLS_UNAVAILABLE_TEXT = "技能列表暂不可用"
NO_SKILLS_TEXT = "暂无可用技能"
NEW_CONVERSATION_TEXT = "已创建新会话"
STOP_ACCEPTED_TEXT = "正在停止当前任务…"
BROKEN_STREAM_TEXT = "服务暂时中断，请重发消息"


def route_from_envelope(envelope: ChannelEnvelope) -> DeliveryRouteInput:
    return DeliveryRouteInput(
        channel=envelope.channel,
        bot_id=envelope.bot_id,
        external_user_id=envelope.external_user_id,
        external_conversation_id=envelope.external_conversation_id,
    )


def format_skills(skills: list[dict[str, Any]]) -> str:
    if not skills:
        return NO_SKILLS_TEXT
    lines: list[str] = []
    for skill in skills:
        label = str(skill.get("platform_label") or skill.get("name") or "").strip()
        description = str(skill.get("description") or "").strip()
        lines.append(f"{label}: {description}".strip(": "))
    return "\n".join(lines)


class _RunStreamState:
    def __init__(self) -> None:
        self.run_id: str | None = None
        self.buffer: list[str] = []
        self.awaiting_input = False
        self.terminal = False
        self.has_output = False

    @property
    def buffered_chars(self) -> int:
        return sum(len(chunk) for chunk in self.buffer)


class InboundPipeline:
    def __init__(
        self,
        *,
        dedupe: DedupeStore,
        console: ConsoleClientPort,
        runtime: RuntimeClientPort,
        catalog: MessageCatalog,
        tenant_id: str,
        locale: str = "zh-CN",
    ) -> None:
        self._dedupe = dedupe
        self._console = console
        self._runtime = runtime
        self._catalog = catalog
        self._tenant_id = tenant_id
        self._locale = locale
        self._pending_run_ids: dict[str, str] = {}

    def pending_run_id(self, platform_user_id: UUID | str) -> str | None:
        return self._pending_run_ids.get(str(platform_user_id))

    async def consume(self, adapter: ChannelAdapter) -> None:
        events = await adapter.iter_events()
        async for envelope in events:
            try:
                await self.handle(adapter, envelope)
            except Exception:
                logger.exception(
                    "inbound_message_failed channel=%s message_id=%s",
                    envelope.channel,
                    envelope.message_id,
                )

    async def handle(self, adapter: ChannelAdapter, envelope: ChannelEnvelope) -> None:
        if not await self._mark_seen(envelope):
            return
        text = envelope.text.strip()
        route = route_from_envelope(envelope)
        if text == BIND_COMMAND or text.startswith(f"{BIND_COMMAND} "):
            await self._handle_bind(adapter, route, envelope, text)
            return
        if text == NEW_COMMAND:
            await self._handle_new(adapter, route, envelope)
            return
        if text == STOP_COMMAND:
            await self._handle_stop(adapter, route, envelope)
            return
        if text == SKILLS_COMMAND:
            await self._handle_skills(adapter, route, envelope)
            return
        await self._handle_message(adapter, route, envelope)

    async def _mark_seen(self, envelope: ChannelEnvelope) -> bool:
        key = f"{DEDUPE_PREFIX}:{envelope.channel}:{envelope.message_id}"
        try:
            duplicate = await is_duplicate(self._dedupe, key, DEDUPE_TTL_SEC)
        except DedupeStoreError as exc:
            logger.warning("dedupe_store_failed message_id=%s error=%s", envelope.message_id, exc)
            return True
        if duplicate:
            logger.debug("duplicate_message_ignored message_id=%s", envelope.message_id)
        return not duplicate

    async def _resolve(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        envelope: ChannelEnvelope,
    ) -> ChannelResolveResponse | None:
        request = ChannelResolveRequest(
            channel=envelope.channel,
            bot_id=envelope.bot_id,
            external_user_id=envelope.external_user_id,
        )
        try:
            return await self._console.resolve(request, self._tenant_id)
        except AppError as exc:
            await self._reply_error(adapter, route, exc)
            return None

    async def _handle_bind(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        envelope: ChannelEnvelope,
        text: str,
    ) -> None:
        code = text[len(BIND_COMMAND) :].strip()
        if not code:
            await self._send_text(adapter, route, BIND_USAGE_TEXT)
            return
        request = ChannelBindRequest(
            channel=envelope.channel,
            bot_id=envelope.bot_id,
            external_user_id=envelope.external_user_id,
            bind_code=code,
        )
        try:
            await self._console.bind(request, self._tenant_id)
        except AppError as exc:
            await self._reply_error(adapter, route, exc)
            return
        await self._send_text(adapter, route, BIND_SUCCESS_TEXT)

    async def _handle_new(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        envelope: ChannelEnvelope,
    ) -> None:
        resolved = await self._resolve(adapter, route, envelope)
        if resolved is None:
            return
        if resolved.platform_user_id is None or resolved.agent_id is None:
            await self._send_text(adapter, route, UNBOUND_TEXT)
            return
        if not resolved.authorized:
            await self._send_text(adapter, route, NO_PERMISSION_TEXT)
            return
        try:
            await self._runtime.create_conversation(
                resolved.agent_id,
                resolved.platform_user_id,
                tenant_id=self._tenant_id,
            )
        except AppError as exc:
            await self._reply_error(adapter, route, exc)
            return
        await self._send_text(adapter, route, NEW_CONVERSATION_TEXT)

    async def _handle_stop(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        envelope: ChannelEnvelope,
    ) -> None:
        resolved = await self._resolve(adapter, route, envelope)
        if resolved is None:
            return
        if resolved.platform_user_id is None or resolved.agent_id is None:
            await self._send_text(adapter, route, UNBOUND_TEXT)
            return
        try:
            await self._runtime.cancel_active(
                resolved.agent_id,
                resolved.platform_user_id,
                tenant_id=self._tenant_id,
            )
        except AppError as exc:
            if exc.code == str(ErrorCode.NO_ACTIVE_RUN):
                await self._send_text(
                    adapter,
                    route,
                    self._catalog.message(exc.code, self._locale),
                )
                return
            await self._reply_error(adapter, route, exc)
            return
        await self._send_text(adapter, route, STOP_ACCEPTED_TEXT)

    async def _handle_skills(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        envelope: ChannelEnvelope,
    ) -> None:
        resolved = await self._resolve(adapter, route, envelope)
        if resolved is None:
            return
        if resolved.platform_user_id is None or resolved.agent_id is None:
            await self._send_text(adapter, route, UNBOUND_TEXT)
            return
        if not resolved.authorized:
            await self._send_text(adapter, route, NO_PERMISSION_TEXT)
            return
        try:
            skills = await self._console.channel_skills(
                resolved.agent_id,
                resolved.platform_user_id,
                self._tenant_id,
            )
        except AppError as exc:
            logger.warning("channel_skills_failed code=%s", exc.code)
            await self._send_text(adapter, route, SKILLS_UNAVAILABLE_TEXT)
            return
        await self._send_text(adapter, route, format_skills(skills))

    async def _handle_message(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        envelope: ChannelEnvelope,
    ) -> None:
        resolved = await self._resolve(adapter, route, envelope)
        if resolved is None:
            return
        if resolved.platform_user_id is None or resolved.agent_id is None:
            await self._send_text(adapter, route, UNBOUND_TEXT)
            return
        if not resolved.authorized:
            await self._send_text(adapter, route, NO_PERMISSION_TEXT)
            return
        request = RunRequest(
            agent_id=resolved.agent_id,
            platform_user_id=resolved.platform_user_id,
            channel=ChannelContext(
                type=envelope.channel,
                bot_id=envelope.bot_id,
                external_conversation_id=envelope.external_conversation_id,
            ),
            message=MessageInput(id=envelope.message_id, text=envelope.text),
        )
        await self._consume_run(adapter, route, request, resolved.platform_user_id)

    async def _consume_run(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        request: RunRequest,
        platform_user_id: UUID,
    ) -> None:
        state = _RunStreamState()
        try:
            stream = self._runtime.create_run(request, tenant_id=self._tenant_id)
            async for event in stream:
                await self._apply_run_event(adapter, route, platform_user_id, state, event)
                if state.terminal:
                    break
        except AppError as exc:
            await self._flush(adapter, route, state)
            await self._finalize_stream(adapter, route)
            await self._reply_error(adapter, route, exc)
            return
        await self._flush(adapter, route, state)
        await self._finalize_stream(adapter, route)
        if not state.terminal and not state.awaiting_input:
            await self._send_text(adapter, route, BROKEN_STREAM_TEXT)

    async def _apply_run_event(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        platform_user_id: UUID,
        state: _RunStreamState,
        event: SseEvent,
    ) -> None:
        if event.type == MESSAGE_DELTA_EVENT:
            delta = str(event.data.get("delta") or "")
            if delta:
                state.buffer.append(delta)
                if state.buffered_chars >= DELTA_FLUSH_CHARS:
                    await self._flush(adapter, route, state)
            return
        if event.type == RUN_CREATED_EVENT:
            run_id = event.data.get("run_id")
            if run_id is not None:
                state.run_id = str(run_id)
            return
        if event.type == TASK_ACCEPTED_EVENT:
            await self._flush(adapter, route, state)
            await self._send_text(adapter, route, str(event.data.get("message") or ""))
            state.terminal = True
            return
        if event.type == INTERRUPT_REQUIRED_EVENT:
            await self._flush(adapter, route, state)
            await self._send_text(adapter, route, str(event.data.get("prompt") or ""))
            if state.run_id is not None:
                self._pending_run_ids[str(platform_user_id)] = state.run_id
            state.awaiting_input = True
            return
        if event.type == RUN_COMPLETED_EVENT:
            await self._flush(adapter, route, state)
            final_text = str(event.data.get("final_text") or "")
            if final_text and not state.has_output:
                await self._send_text(adapter, route, final_text)
            state.terminal = True
            return
        if event.type == RUN_FAILED_EVENT:
            await self._flush(adapter, route, state)
            code = str(event.data.get("error_code") or "")
            await self._send_text(adapter, route, self._catalog.message(code, self._locale))
            state.terminal = True

    async def _flush(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        state: _RunStreamState,
    ) -> None:
        if not state.buffer:
            return
        text = "".join(state.buffer)
        state.buffer.clear()
        state.has_output = True
        await self._stream_text(adapter, route, text)

    async def _stream_text(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        text: str,
    ) -> None:
        async def chunks() -> AsyncIterator[str]:
            yield text

        try:
            await adapter.stream(route, chunks())
        except ChannelAdapterUnavailable as exc:
            logger.warning("channel_stream_unavailable channel=%s error=%s", route.channel, exc)

    async def _send_text(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        text: str,
    ) -> None:
        if not text:
            return
        message = DeliveryMessage(text=text)
        try:
            await adapter.send(route, message)
        except ChannelAdapterUnavailable as exc:
            logger.warning("channel_send_unavailable channel=%s error=%s", route.channel, exc)

    async def _finalize_stream(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
    ) -> None:
        if not isinstance(adapter, StreamFinalizer):
            return
        try:
            await adapter.finish_stream(route)
        except ChannelAdapterUnavailable as exc:
            logger.warning("channel_stream_finalize_unavailable channel=%s error=%s", route.channel, exc)

    async def _reply_error(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        exc: AppError,
    ) -> None:
        logger.warning("inbound_dependency_error code=%s", exc.code)
        await self._send_text(adapter, route, self._catalog.message(exc.code, self._locale))
