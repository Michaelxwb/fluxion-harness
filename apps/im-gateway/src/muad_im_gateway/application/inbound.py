from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from math import ceil
from typing import Any
from uuid import UUID

from muad_api import AppError
from muad_api.catalog import MessageCatalog
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    DEFAULT_PAGE_SIZE,
    ChannelBindRequest,
    ChannelContext,
    ChannelEnvelope,
    ChannelResolveRequest,
    ChannelResolveResponse,
    ChannelSkillItem,
    DeliveryMessage,
    DeliveryRouteInput,
    MessageInput,
    RunRequest,
    RunStatus,
)

from ..channels.base import ChannelAdapter, ChannelAdapterUnavailable, StreamFinalizer
from ..infrastructure.dedupe import DedupeStore, DedupeStoreError, is_duplicate
from .console_client import ConsoleClientPort
from .stream_renderer import (
    BROKEN_STREAM_TEXT,
    DELTA_FLUSH_INTERVAL_SEC,
    FINALIZE_KIND,
    STREAM_KIND,
    TEXT_KIND,
    StreamRenderer,
    RenderAction,
)
from .runtime_client import RuntimeClientPort, SseEvent

logger = logging.getLogger(__name__)

DEDUPE_PREFIX = "im:dedupe"
DEDUPE_TTL_SEC = 600

RUN_CREATED_EVENT = "run.created"
MESSAGE_DELTA_EVENT = "message.delta"
TASK_ACCEPTED_EVENT = "task.accepted"
INTERRUPT_REQUIRED_EVENT = "interrupt.required"
RUN_COMPLETED_EVENT = "run.completed"
RUN_FAILED_EVENT = "run.failed"

MAX_CONCURRENT_HANDLERS = 8

BIND_COMMAND = "/bind"
NEW_COMMAND = "/new"
STOP_COMMAND = "/stop"
SKILLS_COMMAND = "/skills"

SKILLS_MAX_PAGES = 20
SKILLS_PAGE_INTERVAL_SEC = 0.05  # 页间节流：有界读取不形成紧循环

BIND_SUCCESS_TEXT = "绑定成功"
BIND_USAGE_TEXT = "用法：/bind <绑定码>"
UNBOUND_TEXT = "请先使用 /bind <绑定码> 完成身份绑定"
NO_PERMISSION_TEXT = "当前账号未获得该智能体使用权限"
SKILLS_UNAVAILABLE_TEXT = "技能列表暂不可用"
NO_SKILLS_TEXT = "暂无可用技能"
NEW_CONVERSATION_TEXT = "已创建新会话"
STOP_ACCEPTED_TEXT = "正在停止当前任务…"
STOP_CANCELLED_TEXT = "当前任务已停止"


COMMAND_PREFIXES = ("/bind", "/new", "/stop", "/skills")


def _is_command(text: str) -> bool:
    stripped = text.strip()
    return any(
        stripped == prefix or stripped.startswith(f"{prefix} ") for prefix in COMMAND_PREFIXES
    )


def route_from_envelope(envelope: ChannelEnvelope) -> DeliveryRouteInput:
    return DeliveryRouteInput(
        channel=envelope.channel,
        bot_id=envelope.bot_id,
        external_user_id=envelope.external_user_id,
        external_conversation_id=envelope.external_conversation_id,
    )


def format_skills(skills: Sequence[ChannelSkillItem]) -> str:
    """设计 §3.4.2：只展示 name/platform_label/description，不含 SKILL.md 全文。"""
    if not skills:
        return NO_SKILLS_TEXT
    lines: list[str] = []
    for skill in skills:
        name = skill.name.strip()
        label = (skill.platform_label or "").strip()
        title = f"{label}（{name}）" if label and label != name else name
        description = skill.description.strip()
        lines.append(f"{title}: {description}".strip(": "))
    return "\n".join(lines)


async def fetch_skill_catalog(
    console: ConsoleClientPort,
    agent_id: UUID,
    platform_user_id: UUID,
    tenant_id: str,
) -> tuple[ChannelSkillItem, ...]:
    """API-04 分页契约：按 total 有界读全量 Effective Skill Catalog。

    页间节流且有界（不形成无等待紧循环）；任一页失败（404/坏封套）向上抛出，由调用方
    转成"暂不可用"，不伪装成空目录。
    """
    first = await console.channel_skills(agent_id, platform_user_id, tenant_id)
    items = list(first.items)
    page_size = first.page_size or DEFAULT_PAGE_SIZE
    pages = min(max(ceil(first.total / page_size), 1), SKILLS_MAX_PAGES)
    if first.total > pages * page_size:
        logger.warning("channel_skills_truncated total=%s pages=%s", first.total, pages)
    for page in range(2, pages + 1):
        await asyncio.sleep(SKILLS_PAGE_INTERVAL_SEC)
        nxt = await console.channel_skills(
            agent_id, platform_user_id, tenant_id, page=page, page_size=page_size
        )
        items.extend(nxt.items)
    return tuple(items)


class _RunStreamState:
    """一次 Run 的流状态：run_id/终态由本类持有，文本缓冲与节流归 StreamRenderer。"""

    def __init__(self, renderer: StreamRenderer) -> None:
        self.renderer = renderer
        self.run_id: str | None = None
        self.awaiting_input = False
        self.terminal = False


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
        delta_flush_interval_sec: float = DELTA_FLUSH_INTERVAL_SEC,
    ) -> None:
        self._delta_flush_interval_sec = delta_flush_interval_sec
        self._dedupe = dedupe
        self._console = console
        self._runtime = runtime
        self._catalog = catalog
        self._tenant_id = tenant_id
        self._locale = locale
        self._route_locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    async def consume(self, adapter: ChannelAdapter) -> None:
        """有界并发消费入站事件：一个 Run 的长流不阻塞同 bot 的后续消息（含 /stop）。

        - 非命令消息按 route 串行（同 route 的流不交叉）；
        - 命令（/bind /new /stop /skills）不加 route 锁，长流期间仍可即时处理；
        - Runtime 决定 RUN_BUSY/resume：Gateway 不缓存活跃 Run 事实。
        """
        events = await adapter.iter_events()
        active: set[asyncio.Task[None]] = set()
        try:
            async for envelope in events:
                if len(active) >= MAX_CONCURRENT_HANDLERS:
                    _done, active = await asyncio.wait(
                        active, return_when=asyncio.FIRST_COMPLETED
                    )
                task = asyncio.create_task(self._consume_one(adapter, envelope))
                active.add(task)
                task.add_done_callback(active.discard)
        finally:
            for task in tuple(active):
                task.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)

    async def _consume_one(self, adapter: ChannelAdapter, envelope: ChannelEnvelope) -> None:
        try:
            if _is_command(envelope.text):
                await self.handle(adapter, envelope)
                return
            async with self._route_lock(envelope):
                await self.handle(adapter, envelope)
        except Exception:
            logger.exception(
                "inbound_message_failed channel=%s message_id=%s",
                envelope.channel,
                envelope.message_id,
            )

    def _route_lock(self, envelope: ChannelEnvelope) -> asyncio.Lock:
        key = (envelope.channel, envelope.bot_id, envelope.external_user_id)
        lock = self._route_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._route_locks[key] = lock
        return lock

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
            external_conversation_id=envelope.external_conversation_id,
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
            await self._console.bind(
                request,
                self._tenant_id,
                idempotency_key=envelope.message_id,
            )
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
                # 稳定幂等键：同命令重试由 Runtime Owner 持久重放，不创建第二会话
                idempotency_key=envelope.message_id,
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
        if not resolved.authorized:
            await self._send_text(adapter, route, NO_PERMISSION_TEXT)
            return
        try:
            result = await self._runtime.cancel_active(
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
        # 设计 §3.4.2：WAITING_INPUT 已被 Runtime 直接 CAS 为 CANCELLED（立即"已停止"），
        # CREATED/RUNNING 只受理；Gateway 不猜测也不缓存活跃 Run
        await self._send_text(
            adapter,
            route,
            STOP_CANCELLED_TEXT
            if str(result.get("status") or "") == str(RunStatus.CANCELLED)
            else STOP_ACCEPTED_TEXT,
        )

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
            catalog = await fetch_skill_catalog(
                self._console,
                resolved.agent_id,
                resolved.platform_user_id,
                self._tenant_id,
            )
        except AppError as exc:
            logger.warning("channel_skills_failed code=%s", exc.code)
            await self._send_text(adapter, route, SKILLS_UNAVAILABLE_TEXT)
            return
        await self._send_text(adapter, route, format_skills(catalog))

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
                external_user_id=envelope.external_user_id,
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
        state = _RunStreamState(self._build_renderer())
        try:
            stream = self._runtime.create_run(request, tenant_id=self._tenant_id)
            async for event in stream:
                await self._apply_run_event(adapter, route, platform_user_id, state, event)
                if state.terminal:
                    break
        except AppError as exc:
            await self._run_actions(adapter, route, state, state.renderer.finalize())
            await self._reply_error(adapter, route, exc)
            return
        await self._run_actions(adapter, route, state, state.renderer.finalize())
        if not state.terminal and not state.awaiting_input:
            await self._send_text(adapter, route, BROKEN_STREAM_TEXT)

    def _build_renderer(self) -> StreamRenderer:
        return StreamRenderer(
            flush_interval_sec=self._delta_flush_interval_sec,
            message_for=lambda code: self._catalog.message(code, self._locale),
        )

    async def _apply_run_event(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        platform_user_id: UUID,
        state: _RunStreamState,
        event: SseEvent,
    ) -> None:
        del platform_user_id  # resume 由 Runtime 决定（Gateway 无状态）
        if event.type == RUN_CREATED_EVENT:
            run_id = event.run_id or (event.data or {}).get("run_id")
            if run_id is not None:
                state.run_id = str(run_id)
            return
        await self._run_actions(adapter, route, state, state.renderer.apply(event))
        if event.type == INTERRUPT_REQUIRED_EVENT:
            state.awaiting_input = True
        if event.type in (TASK_ACCEPTED_EVENT, RUN_COMPLETED_EVENT, RUN_FAILED_EVENT):
            state.terminal = True

    async def _run_actions(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        state: _RunStreamState,
        actions: Sequence[RenderAction],
    ) -> None:
        for action in actions:
            if action.kind == STREAM_KIND:
                await self._stream_text(adapter, route, action.text)
            elif action.kind == TEXT_KIND and action.text:
                await self._send_text(adapter, route, action.text)
            elif action.kind == FINALIZE_KIND:
                await self._finalize_stream(adapter, route)

    async def _flush(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        state: _RunStreamState,
    ) -> None:
        await self._run_actions(adapter, route, state, state.renderer.flush())

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
