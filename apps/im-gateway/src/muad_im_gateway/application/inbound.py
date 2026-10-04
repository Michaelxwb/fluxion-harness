from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing, suppress
from dataclasses import dataclass
from math import ceil
from uuid import UUID

from muad_api import AppError, metrics
from muad_api.catalog import MessageCatalog
from muad_api.context import current_trace_id
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    DEFAULT_PAGE_SIZE,
    AttachmentRef,
    ChannelBindRequest,
    ChannelContext,
    ChannelEnvelope,
    ChannelResolveRequest,
    ChannelResolveResponse,
    ChannelSkillItem,
    DeliveryMessage,
    DeliveryRouteInput,
    InboundAuditOutcome,
    InboundAuditRequest,
    MessageInput,
    RunRequest,
    RunStatus,
)

from ..channels.base import (
    ATTACHMENT_FETCH_FAILED,
    ATTACHMENT_TOO_LARGE,
    AttachmentFetchError,
    AttachmentSource,
    ChannelAdapter,
    ChannelAdapterUnavailable,
    ChannelReplySession,
    FetchedAttachment,
    ReplySessionFactory,
    StreamFinalizer,
)
from ..infrastructure.dedupe import DedupeStore, DedupeStoreError, is_duplicate
from .attachment_gate import (
    ATTACHMENT_COUNT_EXCEEDED,
    ATTACHMENT_RECEIPT_ALL,
    ATTACHMENT_RECEIPT_PARTIAL,
    ATTACHMENT_TYPE_NOT_ALLOWED,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS_PER_MESSAGE,
    AttachmentCandidate,
    evaluate_gate,
    evaluate_precheck,
)
from .console_client import ConsoleClientPort
from .inbound_attachments import InboundAttachmentStore
from .progress import ActivityMessages, ExecutionProgress, iter_with_ticks
from .runtime_client import RuntimeClientPort, SseEvent
from .stream_renderer import (
    BROKEN_STREAM_TEXT,
    DELTA_FLUSH_INTERVAL_SEC,
    FINALIZE_KIND,
    STREAM_KIND,
    TEXT_KIND,
    RenderAction,
    StreamRenderer,
)

logger = logging.getLogger(__name__)

DEDUPE_PREFIX = "im:dedupe"
DEDUPE_TTL_SEC = 600

AUDIT_RECEIVED = "RECEIVED"
AUDIT_REJECTED = "REJECTED"
AUDIT_FAILED = "FAILED"
UNSUPPORTED_MEDIA_CODE = "UNSUPPORTED_MEDIA"

RUN_CREATED_EVENT = "run.created"
MESSAGE_DELTA_EVENT = "message.delta"
TASK_ACCEPTED_EVENT = "task.accepted"
INTERRUPT_REQUIRED_EVENT = "interrupt.required"
RUN_COMPLETED_EVENT = "run.completed"
RUN_FAILED_EVENT = "run.failed"

MAX_CONCURRENT_HANDLERS = 8

# 指标（design §4.2；标签只含类型/错误码/原因，不含 Secret 或消息正文）
MESSAGES_METRIC = "im_messages_total"
DEDUPE_HITS_METRIC = "im_dedupe_hits_total"
RUNTIME_ERRORS_METRIC = "im_runtime_errors_total"
MESSAGE_FAILURES_METRIC = "im_message_failures_total"
STREAM_FIRST_CHUNK_METRIC = "im_stream_first_chunk_ms"
STREAM_LATENCY_METRIC = "im_stream_latency_ms"
RUNTIME_REQUEST_LATENCY_METRIC = "im_runtime_request_latency_ms"
METRIC_HELP = "IM gateway runtime metric"

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


def _elapsed_ms(started_at: float, ended_at: float | None = None) -> float:
    return round(((ended_at or time.monotonic()) - started_at) * 1000, 3)


def _primary_code(codes: Sequence[str]) -> str:
    """多条拒绝原因并存时选一条给用户看：按 `_FEEDBACK_PRIORITY`，未知码排最后。"""
    for candidate in _FEEDBACK_PRIORITY:
        if candidate in codes:
            return candidate
    return codes[0]


def _writes(actions: Sequence[RenderAction]) -> bool:
    """这组动作里有没有**真的要写到会话上**的内容。

    空动作（增量还在 renderer 缓冲里、没到 flush 时机）与空文本都不占用发送者——
    在飞的计时状态可以继续跑，读取循环照常往下走。
    """
    return any(
        action.kind in (STREAM_KIND, FINALIZE_KIND) or (action.kind == TEXT_KIND and action.text)
        for action in actions
    )


def route_from_envelope(envelope: ChannelEnvelope) -> DeliveryRouteInput:
    return DeliveryRouteInput(
        channel=envelope.channel,
        bot_id=envelope.bot_id,
        external_user_id=envelope.external_user_id,
        external_conversation_id=envelope.external_conversation_id,
    )


def _attachment_count(adapter: ChannelAdapter, envelope: ChannelEnvelope) -> int:
    """适配器侧的**待取件**数量；不支持取件的渠道恒为 0（取件是可选能力，AD-8）。"""
    return adapter.attachment_count(envelope) if isinstance(adapter, AttachmentSource) else 0


def _carries_no_payload(adapter: ChannelAdapter, envelope: ChannelEnvelope) -> bool:
    """信封里既无文本、也无附件、也无"渠道说有、尚未取件"的载荷 ⇒ 没有可运行的内容。

    不能只看 `attachments`：它按定义只描述"**已经拿到手的**字节"（AD-8），所以纯图片/文件消息
    在取件之前必然是空的——只看它会把媒体消息在入口就拦掉，永远走不到取件。
    """
    if envelope.text.strip() or envelope.attachments or envelope.unsupported_media is not None:
        return False
    return _attachment_count(adapter, envelope) == 0


def format_skills(skills: Sequence[ChannelSkillItem]) -> str:
    """设计 §3.4.2：只展示 name/platform_label/description，不含 SKILL.md 全文。

    排版（2026-10-01 改善）：先给条数，再**一条一段** —— 名称单独一行加粗，描述缩进在
    下一行，段间空行。原先是 `名称: 描述` 单行拼接，条目一多就糊成一整块、名称与描述
    分不清。回复走 `reply_text` 的 stream 体，markdown 会被渲染（模型回复里的 `**粗体**`
    已实测渲染成加粗），故用 `**` 做标题。
    """
    if not skills:
        return NO_SKILLS_TEXT
    blocks = [f"**可用技能（{len(skills)}）**"]
    for skill in skills:
        name = skill.name.strip()
        label = (skill.platform_label or "").strip()
        title = f"{label}（{name}）" if label and label != name else name
        description = skill.description.strip()
        blocks.append(f"**{title}**\n{description}" if description else f"**{title}**")
    return "\n\n".join(blocks)


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
        self.progress = ExecutionProgress()
        self.reply: ChannelReplySession | None = None
        #: 在飞的**计时状态**发送（见 `_update_progress`）：读取循环不等它，但它与正文
        #: 共用同一条会话流，所以正文/收尾写入前必须让它落地（否则两个写者并发写同一 stream）。
        self.status_task: asyncio.Task[None] | None = None
        self.run_id: str | None = None
        self.awaiting_input = False
        self.terminal = False
        self.started_at = time.monotonic()
        self.first_event_at: float | None = None
        self.first_chunk_at: float | None = None


@dataclass(frozen=True, slots=True)
class _CollectedAttachments:
    """一条消息的附件链路结论。`audit_outcome` 为 None ⇒ 无需审计（纯文本消息）。"""

    refs: tuple[AttachmentRef, ...] = ()
    audit_outcome: InboundAuditOutcome | None = None
    reason_code: str = ""


#: 反馈原因码的优先级：取件失败先说（E-03），其次体积（E-02），再次类型，最后数量。
_FEEDBACK_PRIORITY = (
    ATTACHMENT_TOO_LARGE,
    ATTACHMENT_TYPE_NOT_ALLOWED,
    ATTACHMENT_COUNT_EXCEEDED,
)


class InboundPipeline:
    def __init__(
        self,
        *,
        dedupe: DedupeStore,
        console: ConsoleClientPort,
        runtime: RuntimeClientPort,
        catalog: MessageCatalog,
        attachment_store: InboundAttachmentStore | None = None,
        tenant_id: str,
        locale: str = "zh-CN",
        delta_flush_interval_sec: float = DELTA_FLUSH_INTERVAL_SEC,
        progress_interval_sec: float = 1.0,
    ) -> None:
        if progress_interval_sec <= 0:
            raise ValueError("progress interval must be positive")
        self._progress_interval_sec = progress_interval_sec
        self._activity_messages: ActivityMessages | None = None
        self._delta_flush_interval_sec = delta_flush_interval_sec
        self._dedupe = dedupe
        self._console = console
        self._runtime = runtime
        self._catalog = catalog
        # 未配置时**不静默丢附件**：真收到媒体会记 ERROR（见 `_collect_attachments`）。
        self._attachment_store = attachment_store
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
            metrics.inc_counter(
                MESSAGE_FAILURES_METRIC, 1, {"reason": "unexpected"}, help="IM message failures"
            )
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
        if _carries_no_payload(adapter, envelope):
            # 既无文本也无附件、渠道也说没有待取件的内容 ⇒ 没有可运行的东西：**不**建 Run、
            # **不**回复（用户没发任何可回应的内容）。带载荷的消息不会走到这里：它的取件、
            # 反馈与审计在 `_collect_attachments` 里闭环。
            logger.warning(
                "inbound_envelope_without_payload message_id=%s channel=%s",
                envelope.message_id,
                envelope.channel,
            )
            return
        text = envelope.text.strip()
        metrics.inc_counter(
            MESSAGES_METRIC,
            1,
            {"type": "command" if _is_command(text) else "text"},
            help="Inbound IM messages by type",
        )
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
            metrics.inc_counter(
                MESSAGE_FAILURES_METRIC, 1, {"reason": "dedupe_unavailable"}, help="IM message failures"
            )
            logger.warning("dedupe_store_failed message_id=%s error=%s", envelope.message_id, exc)
            return True
        if duplicate:
            metrics.inc_counter(DEDUPE_HITS_METRIC, 1, help="Inbound dedupe hits")
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
        # 附件链路放在**授权之后**：未绑定/无权限那两条早退路径不会取件，"能收才去拉"，
        # 把 AD-1-B 下（字节先于 Run 落盘）的无主产物压到最小（设计 §3.2.1）。
        collected = await self._collect_attachments(adapter, route, envelope)
        if not collected.refs and not envelope.text.strip():
            # 没有可跑的内容（附件全部被拒/取件失败，且文本为空）：反馈与审计已由
            # `_collect_attachments` 闭环，这里只是**不**把空消息当用户输入发给模型。
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
            message=MessageInput(
                id=envelope.message_id,
                type="attachment" if collected.refs else "text",
                text=envelope.text,
                attachments=list(collected.refs),
            ),
        )
        await self._consume_run(adapter, route, request, resolved.platform_user_id)

    async def _collect_attachments(
        self, adapter: ChannelAdapter, route: DeliveryRouteInput, envelope: ChannelEnvelope
    ) -> _CollectedAttachments:
        """② 预检 → ③ 取件 → ④ 实检 → ⑤ 落盘（设计 §3.2.1）。

        **没有静默路径**：每一种"收不了"都在这里同时给出用户可见回复与审计结局；纯文本消息直接
        返回——不取件、也不写审计（审计只针对带载荷的消息，否则每句闲聊一行会把审计面淹掉）。
        """
        if envelope.unsupported_media is not None:
            return await self._reject_payload(adapter, route, envelope, UNSUPPORTED_MEDIA_CODE, 0)
        source = adapter if isinstance(adapter, AttachmentSource) else None
        count = _attachment_count(adapter, envelope)
        if count == 0:
            if envelope.text.strip():
                # 纯文本消息：不取件，也不写审计（否则每句闲聊一行会把审计面淹掉）
                return _CollectedAttachments()
            # 入口护栏放行过（当时确有引用），此刻引用没了（授权往返期间过期或被驱逐）。
            # 仍然不能静默：给反馈 + 审计，而不是把空文本当用户输入发出去。
            return await self._reject_payload(adapter, route, envelope, ATTACHMENT_FETCH_FAILED, 0)
        if self._attachment_store is None:
            # 部署漏配（缺 ARTIFACT_ROOT）：响亮地失败，而不是把附件悄悄丢掉
            logger.error(
                "inbound_attachments_unavailable message_id=%s count=%s", envelope.message_id, count
            )
            return await self._reject_payload(adapter, route, envelope, ATTACHMENT_FETCH_FAILED, count)
        assert source is not None  # count > 0 只可能来自实现了取件的适配器
        return await self._materialize(adapter, route, envelope, source, count)

    async def _materialize(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        envelope: ChannelEnvelope,
        source: AttachmentSource,
        count: int,
    ) -> _CollectedAttachments:
        store = self._attachment_store
        assert store is not None  # 调用方已判过
        precheck = evaluate_precheck(count)
        fetched: list[tuple[int, FetchedAttachment]] = []
        failures: list[str] = []
        for index in range(precheck.accepted):
            try:
                fetched.append(
                    (
                        index,
                        await source.fetch_attachment(
                            envelope, index, max_bytes=MAX_ATTACHMENT_BYTES
                        ),
                    )
                )
            except AttachmentFetchError as exc:
                failures.append(exc.code)

        graded = [
            (
                index,
                AttachmentCandidate(
                    media_type=item.media_type, size=len(item.data), filename=item.filename
                ),
                item,
            )
            for index, item in fetched
        ]
        decision = evaluate_gate([candidate for _, candidate, _ in graded])
        rejected_positions = {rejection.index for rejection in decision.rejected}

        refs: list[AttachmentRef] = []
        total_bytes = 0
        for position, (index, _candidate, content) in enumerate(graded):
            if position in rejected_positions:
                continue
            refs.append(
                store.persist(
                    token=envelope.message_id,
                    index=index,
                    content=content,
                    source_channel=envelope.channel,
                )
            )
            total_bytes += len(content.data)

        codes = [*failures, *(rejection.code for rejection in precheck.rejected)]
        codes.extend(rejection.code for rejection in decision.rejected)
        # RULE-04：同一条入站消息**至多一条**附件相关反馈。全收下也要回一条收据（否则用户
        # 不知道东西到底到没到），部分接收把原因**并进同一条**——绝不拆成"回执 + 拒绝说明"两条。
        if refs or codes:
            await self._send_text(adapter, route, self._receipt_text(len(refs), codes))
        if failures:
            outcome: InboundAuditOutcome = "FAILED"
        elif refs:
            outcome = "RECEIVED"
        else:
            outcome = "REJECTED"
        await self._audit(
            envelope,
            outcome=outcome,
            count=count,
            accepted=len(refs),
            total_bytes=total_bytes,
            reason_code=_primary_code(codes) if codes else "",
        )
        return _CollectedAttachments(
            refs=tuple(refs), audit_outcome=outcome, reason_code=_primary_code(codes) if codes else ""
        )

    async def _reject_payload(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        envelope: ChannelEnvelope,
        code: str,
        count: int,
    ) -> _CollectedAttachments:
        """整条消息不可收：明确回复 + 审计（E-01 走的就是这条路）。"""
        await self._send_text(adapter, route, self._feedback_text(code))
        await self._audit(
            envelope, outcome="REJECTED", count=count, accepted=0, total_bytes=0, reason_code=code
        )
        return _CollectedAttachments(audit_outcome="REJECTED", reason_code=code)

    def _feedback_text(self, code: str) -> str:
        """用户可见文案一律经消息目录取（RULE-i18n-001）；**数值只有一个来源**：门控常量。"""
        args: dict[str, object] = {}
        if code == ATTACHMENT_TOO_LARGE:
            args["limit"] = MAX_ATTACHMENT_BYTES
        elif code == ATTACHMENT_COUNT_EXCEEDED:
            args["limit"] = MAX_ATTACHMENTS_PER_MESSAGE
        return self._catalog.message(code, self._locale, args or None)

    def _receipt_text(self, accepted: int, codes: Sequence[str]) -> str:
        """一条回执，**至多一条**（RULE-04）。三种形态共用这一个出口：

        - **全部接收** → 只报数目（原先这里是静默的，用户不知道东西到没到）；
        - **部分接收** → 「收到几个 + 几个没收 + 原因」并成一条，原因复用拒绝码自己的文案；
        - **全部拒绝** → 只给拒绝说明，**不叠加**回执（叠加就成了第二条消息）。

        文案一律经消息目录（RULE-i18n-001），数值仍只有一处来源——原因是**取**来的，不是另写的。
        """
        rejected = len(codes)
        if rejected and accepted:
            return self._catalog.message(
                ATTACHMENT_RECEIPT_PARTIAL,
                self._locale,
                {
                    "accepted": accepted,
                    "rejected": rejected,
                    "reason": self._feedback_text(_primary_code(codes)),
                },
            )
        if rejected:
            return self._feedback_text(_primary_code(codes))
        return self._catalog.message(ATTACHMENT_RECEIPT_ALL, self._locale, {"count": accepted})

    async def _audit(
        self,
        envelope: ChannelEnvelope,
        *,
        outcome: InboundAuditOutcome,
        count: int,
        accepted: int,
        total_bytes: int,
        reason_code: str,
    ) -> None:
        """写一条入站审计（TASK-012 的 `POST /internal/channel/audit`）。

        审计面抖动**不阻断用户请求**：不能因为审计写不进去就让用户收不到回答；但失败必须留
        ERROR 日志（"不吞"指的是有痕迹，不是指放弃用户请求）。
        """
        try:
            await self._console.audit(
                InboundAuditRequest(
                    channel=envelope.channel,
                    bot_id=envelope.bot_id,
                    external_message_id=envelope.message_id,
                    external_user_id=envelope.external_user_id,
                    outcome=outcome,
                    reason_code=reason_code,
                    attachment_count=count,
                    accepted_count=accepted,
                    total_bytes=total_bytes,
                    trace_id=current_trace_id() or None,
                ),
                self._tenant_id,
            )
        except AppError as exc:
            logger.error(
                "inbound_audit_write_failed message_id=%s error=%s",
                envelope.message_id,
                type(exc).__name__,
            )

    async def _consume_run(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        request: RunRequest,
        platform_user_id: UUID,
    ) -> None:
        state = _RunStreamState(self._build_renderer())
        if isinstance(adapter, ReplySessionFactory):
            try:
                state.reply = adapter.open_reply(route, request.message.id)
                if self._activity_messages is None:
                    self._activity_messages = ActivityMessages(self._catalog.file_path, self._locale)
            except ChannelAdapterUnavailable:
                logger.warning("channel_reply_session_unavailable channel=%s", route.channel)
        finalized = False
        try:
            await self._update_progress(route, state, force=True)
            stream = self._runtime.create_run(request, tenant_id=self._tenant_id)
            async with aclosing(iter_with_ticks(stream, interval=self._progress_interval_sec)) as events:
                async for event in events:
                    if event is None:
                        await self._update_progress(route, state)
                        continue
                    await self._apply_run_event(adapter, route, platform_user_id, state, event)
                    if state.terminal:
                        break
            if not state.terminal and not state.awaiting_input:
                state.progress.apply(SseEvent("run.failed", {}))
                await self._update_progress(route, state, force=True)
                await self._send_run_text(adapter, route, state, BROKEN_STREAM_TEXT)
        except AppError as exc:
            state.progress.apply(SseEvent("run.failed", {}))
            await self._update_progress(route, state, force=True)
            if state.reply is None:
                # 非状态能力渠道**保留原回复路径**（设计 §3.2）：正文尾段先落，错误文案在后。
                # 有回复会话的渠道相反——错误要并进那条还没收尾的流里，所以先发再 finalize。
                await self._run_actions(adapter, route, state, state.renderer.finalize())
                finalized = True
            await self._reply_error(adapter, route, exc, reply=state.reply)
            return
        finally:
            if not finalized:
                await self._run_actions(adapter, route, state, state.renderer.finalize())
        metrics.set_gauge(
            STREAM_LATENCY_METRIC,
            _elapsed_ms(state.started_at),
            help="Whole-stream latency until finalize (ms)",
        )

    async def _update_progress(
        self,
        route: DeliveryRouteInput,
        state: _RunStreamState,
        *,
        force: bool = False,
    ) -> None:
        """把当前阶段送到会话上（设计 §3.4 / PROGRESS-01）。

        **计时 tick 与阶段变化走两条路**：

        - `force=True`（阶段变化、终态、准备中）在主路径上**按序**发送：先等掉在飞的 tick，
          再发本条——同一条会话流只有一个写者，顺序不能被 tick 打乱。
        - `force=False`（1 秒 tick）**不等**：发送者忙就整条丢掉（不排队、不积压），下一拍用
          当时的最新阶段重算。读取循环因此不会被计时拖住，正文与终态可以随时插进来。
        """
        if state.reply is None or (not force and not state.progress.active):
            return
        if not state.progress.visible:
            return
        if force:
            await self._drain_status(state)
            await self._send_status(route, state)
            return
        if state.status_task is None or state.status_task.done():
            state.status_task = asyncio.create_task(self._send_status(route, state))

    async def _send_status(self, route: DeliveryRouteInput, state: _RunStreamState) -> None:
        reply = state.reply
        if reply is None:  # 会话在任务起跑前被回收（收尾/停机）
            return
        assert self._activity_messages is not None
        try:
            await reply.update_status(self._activity_messages.render(state.progress))
        except ChannelAdapterUnavailable:
            logger.warning("channel_status_update_failed channel=%s", route.channel, exc_info=True)

    async def _drain_status(self, state: _RunStreamState) -> None:
        """等掉在飞的 tick，保证随后写入的是**唯一写者**。

        只等不取消：状态帧与正文帧同走一条官方流，中途取消可能留下半个 WS 帧；而两者共用
        同一条传输，等它的代价也就是这条传输本来要花的代价。上限由会话自己的状态超时兜底。
        """
        task, state.status_task = state.status_task, None
        if task is None:
            return
        with suppress(asyncio.CancelledError):
            await task

    async def _send_run_text(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        state: _RunStreamState,
        text: str,
    ) -> None:
        if state.reply is None:
            await self._send_text(adapter, route, text)
            return
        try:
            await state.reply.send(text)
        except ChannelAdapterUnavailable:
            logger.warning("channel_reply_failed channel=%s", route.channel, exc_info=True)

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
        changed = state.progress.apply(event)
        if changed:
            await self._update_progress(route, state, force=True)
        del platform_user_id  # resume 由 Runtime 决定（Gateway 无状态）
        if state.first_event_at is None:
            state.first_event_at = time.monotonic()
            metrics.set_gauge(
                RUNTIME_REQUEST_LATENCY_METRIC,
                _elapsed_ms(state.started_at, state.first_event_at),
                help="Runtime run request latency until first SSE event (ms)",
            )
        if event.type == RUN_CREATED_EVENT:
            run_id = event.run_id or (event.data or {}).get("run_id")
            if run_id is not None:
                state.run_id = str(run_id)
                if state.reply is not None:
                    state.reply.bind_run(state.run_id)
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
        if state.reply is not None:
            actions = tuple(action for action in actions if action.kind != FINALIZE_KIND) + tuple(
                action for action in actions if action.kind == FINALIZE_KIND
            )
            if _writes(actions):
                # 真要写了才等在飞的 tick。force 路径**挡不住这一帧**：正文一出现状态即转为
                # 不可见，此后的 force 调用都在可见性检查处直接返回，只有这里能兜住。
                await self._drain_status(state)
        for action in actions:
            if action.kind == STREAM_KIND:
                if state.first_chunk_at is None:
                    state.first_chunk_at = time.monotonic()
                    metrics.set_gauge(
                        STREAM_FIRST_CHUNK_METRIC,
                        _elapsed_ms(state.started_at, state.first_chunk_at),
                        help="Time to first streamed chunk (ms)",
                    )
                if state.reply is None:
                    await self._stream_text(adapter, route, action.text)
                else:
                    try:
                        await state.reply.stream(action.text)
                    except ChannelAdapterUnavailable:
                        logger.warning("channel_reply_stream_failed channel=%s", route.channel, exc_info=True)
            elif action.kind == TEXT_KIND and action.text:
                await self._send_run_text(adapter, route, state, action.text)
            elif action.kind == FINALIZE_KIND:
                if state.reply is None:
                    await self._finalize_stream(adapter, route)
                else:
                    try:
                        await state.reply.finish()
                    except ChannelAdapterUnavailable:
                        logger.warning(
                            "channel_reply_finalize_failed channel=%s", route.channel, exc_info=True
                        )

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
        *,
        reply: ChannelReplySession | None = None,
    ) -> None:
        metrics.inc_counter(RUNTIME_ERRORS_METRIC, 1, {"code": exc.code}, help="Runtime error codes observed")
        metrics.inc_counter(MESSAGE_FAILURES_METRIC, 1, {"reason": exc.code}, help="IM message failures")
        logger.warning("inbound_dependency_error code=%s", exc.code)
        text = self._catalog.message(exc.code, self._locale)
        if reply is None:
            await self._send_text(adapter, route, text)
        else:
            try:
                await reply.send(text)
            except ChannelAdapterUnavailable:
                logger.warning("channel_error_reply_failed channel=%s", route.channel, exc_info=True)
