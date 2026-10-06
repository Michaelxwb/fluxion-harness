from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing, suppress
from dataclasses import dataclass, field
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
from ..infrastructure.dedupe import DEDUPE_VALUE, DedupeStore, DedupeStoreError
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
from .inbound_attachments import AttachmentConflictError, InboundAttachmentStore
from .platform_settings import ReplySettings, resolve_reply_settings
from .ports import NullPlatformSettingsClient, PlatformSettingsClient
from .progress import (
    ActivityMessages,
    ExecutionProgress,
    iter_with_ticks,
)
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
#: 排队积压时的用户可见反馈（RULE-01：不得静默丢）：网关过载时的**显式**拒绝。
CHANNEL_BUSY = "CHANNEL_BUSY"
#: 排队拒绝/设置读取失败发生在**回复设置快照之前**，此时只能用目录的默认语言（zh-CN）。
DEFAULT_REPLY_LOCALE = "zh-CN"

RUN_CREATED_EVENT = "run.created"
MESSAGE_DELTA_EVENT = "message.delta"
TASK_ACCEPTED_EVENT = "task.accepted"
INTERRUPT_REQUIRED_EVENT = "interrupt.required"
RUN_COMPLETED_EVENT = "run.completed"
RUN_FAILED_EVENT = "run.failed"

#: 真正**在跑**的消息处理上限（不含排队等待）。等待不再占用名额——见 `consume`。
MAX_CONCURRENT_HANDLERS = 8
#: 全局排队上限：读循环永不阻塞，但内存必须有界。超出即**可见拒绝**（CHANNEL_BUSY），
#: 不静默丢——丢了用户不知道，只会以为机器人坏了。
MAX_PENDING_HANDLERS = 256
#: 同一路由（bot+用户+会话）的排队上限：一个用户狂发不该把别人挤出去。
MAX_PENDING_PER_ROUTE = 8
#: 命令的排队上限。命令很短，且读取循环必须能一直读到它们（/stop 的可用性由这条保证）。
MAX_PENDING_COMMANDS = 32

#: 本回复生命周期内固定的展示设置（ADR-04）：`handle` 开始时取一次快照、set 一次，
#: 整个回复期间（含 tick）都用它；下一条入站消息重新取。用 ContextVar 而非实例属性——
#: 同一 pipeline 上同 bot 的并发回复各持自己那一份，互不串味。
_REPLY_SETTINGS: contextvars.ContextVar[ReplySettings | None] = contextvars.ContextVar(
    "gateway_reply_settings", default=None
)

#: 本条目消息**自己的**回复会话（2026-10-06 修）。此前只有走 Run 的那条路径开会话，命令回执、
#: 附件回执、错误文案一律走"本路由最新回调"，于是同会话后来的消息会把前面那条消息的回执顶掉
#: （实测：`/bind` 的成功回执发到了后来的那条消息上）。现在 `_send_text` 一律优先用本会话。
_REPLY_SESSION: contextvars.ContextVar[ChannelReplySession | None] = contextvars.ContextVar(
    "gateway_reply_session", default=None
)

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


def _route_key(envelope: ChannelEnvelope) -> tuple[str, str, str]:
    """按路由（渠道 + bot + 用户）串行：同会话的多条消息不交叉。"""
    return (envelope.channel, envelope.bot_id, envelope.external_user_id)


@dataclass(slots=True)
class _Backlog:
    """排队计数：读循环靠它做**有界但不阻塞**的准入判断。

    读循环不能被队列拖住（那正是 /stop 饥饿的成因），所以它只做计数与转交，计数由任务自己
    在收尾时归还。两级上限同时生效：全局一级防内存无界，每路由一级防单个用户挤掉别人。
    """

    pending: int = 0
    commands: int = 0
    by_route: dict[tuple[str, str, str], int] = field(default_factory=dict)

    def admits(self, key: tuple[str, str, str], *, command: bool) -> bool:
        if command:
            return self.commands < MAX_PENDING_COMMANDS
        return (
            self.pending < MAX_PENDING_HANDLERS
            and self.by_route.get(key, 0) < MAX_PENDING_PER_ROUTE
        )

    def take(self, key: tuple[str, str, str], *, command: bool) -> None:
        if command:
            self.commands += 1
            return
        self.pending += 1
        self.by_route[key] = self.by_route.get(key, 0) + 1

    def release(self, key: tuple[str, str, str], *, command: bool) -> None:
        if command:
            self.commands -= 1
            return
        self.pending -= 1
        remaining = self.by_route.get(key, 0) - 1
        if remaining > 0:
            self.by_route[key] = remaining
        else:
            self.by_route.pop(key, None)


@dataclass(frozen=True, slots=True)
class _SeenReservation:
    """入站去重的占位句柄。

    `owner` 为 `None` = 去重存储不可用（fail-open）：照常处理，但没有可升级/可释放的键
    —— 丢消息比重复消息更严重，不能因 Redis 抖动把用户消息吞掉。
    """

    key: str
    owner: str | None


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
        #: 本回复的状态文案目录（locale 随平台设置快照逐条固定，故归本条回复而非 pipeline）。
        self.activity_messages: ActivityMessages | None = None
        #: 在飞的**计时状态**发送（见 `_update_progress`）：读取循环不等它，但它与正文
        #: 共用同一条会话流，所以正文/收尾写入前必须让它落地（否则两个写者并发写同一 stream）。
        self.status_task: asyncio.Task[None] | None = None
        self.run_id: str | None = None
        self.awaiting_input = False
        self.terminal = False
        self.started_at = time.monotonic()
        #: 上一次**真的**发了计时帧的时刻（读取循环醒得比计时节拍快，靠它节流）。
        self.last_progress_at = 0.0
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
        settings_client: PlatformSettingsClient | None = None,
        delta_flush_interval_sec: float = DELTA_FLUSH_INTERVAL_SEC,
    ) -> None:
        self._activity_catalogs: dict[str, ActivityMessages] = {}
        self._delta_flush_interval_sec = delta_flush_interval_sec
        self._dedupe = dedupe
        self._console = console
        self._runtime = runtime
        self._catalog = catalog
        # 未配置时**不静默丢附件**：真收到媒体会记 ERROR（见 `_collect_attachments`）。
        self._attachment_store = attachment_store
        self._tenant_id = tenant_id
        # 生产装配注入真实 HTTP client；直构调用点默认「该租户无记录」⇒ schema 默认。
        self._settings_client: PlatformSettingsClient = settings_client or NullPlatformSettingsClient()
        self._route_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        #: **在飞**处理的并发闸（排队等锁的不算）：封顶同时跑的模型流数量。
        self._handlers = asyncio.Semaphore(MAX_CONCURRENT_HANDLERS)

    @property
    def _reply(self) -> ReplySettings:
        """本回复生命周期内固定的设置；只应在 `handle` 内访问（否则是漏取了快照）。"""
        reply = _REPLY_SETTINGS.get()
        if reply is None:
            raise RuntimeError("reply settings accessed outside an inbound reply lifecycle")
        return reply

    @property
    def _locale(self) -> str:
        return self._reply.locale

    @property
    def _progress_interval_sec(self) -> float:
        return self._reply.progress_interval_sec

    async def consume(self, adapter: ChannelAdapter) -> None:
        """有界并发消费入站事件：一个 Run 的长流不阻塞同 bot 的后续消息（含 /stop）。

        **读循环永不阻塞**（2026-10-06 修）。原口径是「活跃任务满 8 个就在 `asyncio.wait` 上
        等下一个完成」，而同路由**排队等锁**的消息同样是"活跃任务"——7 条排队消息就能占满 8 个
        槽，读取循环于是再也读不到 `/stop`（实测：长流 + 7 条同路由消息之后，取消接口的调用
        次数恒为 0，直到人工放行长流才开始处理）。

        现在等待发生在**任务内部**，读循环只做计数与转交：

        - 非命令消息按 route 串行（同 route 的流不交叉），在飞处理由 `_handlers` 信号量封顶；
        - 命令（/bind /new /stop /skills）**不排队等锁**，长流期间仍即时处理；
        - 排队**有界**（全局 + 每路由），队满给用户一条明确反馈并留痕，不静默丢；
        - Runtime 决定 RUN_BUSY/resume：Gateway 不缓存活跃 Run 事实。
        """
        events = await adapter.iter_events()
        backlog = _Backlog()
        active: set[asyncio.Task[None]] = set()
        try:
            async for envelope in events:
                key = _route_key(envelope)
                command = _is_command(envelope.text)
                if not backlog.admits(key, command=command):
                    await self._refuse_overloaded(adapter, envelope)
                    continue
                backlog.take(key, command=command)
                task = asyncio.create_task(self._consume_one(adapter, envelope, backlog, key))
                active.add(task)
                task.add_done_callback(active.discard)
        finally:
            for task in tuple(active):
                task.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)

    async def _consume_one(
        self,
        adapter: ChannelAdapter,
        envelope: ChannelEnvelope,
        backlog: _Backlog,
        key: tuple[str, str, str],
    ) -> None:
        """一条消息一个任务；**排队等锁在任务里**，不占读循环。"""
        try:
            if _is_command(envelope.text):
                await self.handle(adapter, envelope)
                return
            async with self._route_lock(envelope), self._handlers:
                await self.handle(adapter, envelope)
        except Exception:
            self._record_unexpected(envelope)
        finally:
            backlog.release(key, command=_is_command(envelope.text))

    def _record_unexpected(self, envelope: ChannelEnvelope) -> None:
        metrics.inc_counter(
            MESSAGE_FAILURES_METRIC, 1, {"reason": "unexpected"}, help="IM message failures"
        )
        logger.exception(
            "inbound_message_failed channel=%s message_id=%s",
            envelope.channel,
            envelope.message_id,
        )

    async def _refuse_overloaded(
        self, adapter: ChannelAdapter, envelope: ChannelEnvelope
    ) -> None:
        """排队积压：**可见**拒绝（指标 + 日志 + 用户反馈），不静默丢。

        静默丢最坏：用户以为机器人坏了，而我们连一条痕迹都没有。给一条"稍后再试"至少让用户
        知道该重发；留指标与日志让"网关被打爆"在监控上看得见。
        """
        metrics.inc_counter(
            MESSAGE_FAILURES_METRIC, 1, {"reason": "overloaded"}, help="IM message failures"
        )
        logger.warning(
            "inbound_backlog_full channel=%s message_id=%s", envelope.channel, envelope.message_id
        )
        await self._send_text(
            adapter,
            route_from_envelope(envelope),
            self._catalog.message(CHANNEL_BUSY, DEFAULT_REPLY_LOCALE),
        )

    def _route_lock(self, envelope: ChannelEnvelope) -> asyncio.Lock:
        key = _route_key(envelope)
        lock = self._route_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._route_locks[key] = lock
        return lock

    async def handle(self, adapter: ChannelAdapter, envelope: ChannelEnvelope) -> bool:
        """处理一条入站消息，返回**是否已受理**。

        **去重的时机**（2026-10-06 修）：占位在入口占，终值只在**受理之后**写。此前是"看一眼
        就写 10 分钟去重键"，于是受理前的故障（平台设置拉取失败、授权解析失败、附件落盘失败）
        会把这条消息永久标成"处理过"：平台重投被去重跳过，用户既没有 Run、也收不到任何反馈
        （实测 `runtime_submissions=0, user_feedback=0`，键的 TTL 还剩 600 秒）。

        未受理即释放占位，让重投能真正重试；受理之后的重复由下游幂等键兜底（`message_id` 作为
        `Idempotency-Key` 沿链路透传到 Console 与 Runtime）。
        """
        seen = await self._reserve_seen(envelope)
        if seen is None:
            return True
        session = self._open_reply_session(adapter, envelope)
        session_token = _REPLY_SESSION.set(session)
        try:
            accepted = await self._dispatch(adapter, envelope)
        except BaseException:
            await self._release_seen(seen, envelope)
            raise
        finally:
            _REPLY_SESSION.reset(session_token)
            await self._close_reply_session(session, envelope)
        if accepted:
            await self._mark_accepted(seen, envelope)
        else:
            await self._release_seen(seen, envelope)
        return accepted

    async def _dispatch(self, adapter: ChannelAdapter, envelope: ChannelEnvelope) -> bool:
        if _carries_no_payload(adapter, envelope):
            # 既无文本也无附件、渠道也说没有待取件的内容 ⇒ 没有可运行的东西：**不**建 Run、
            # **不**回复（用户没发任何可回应的内容）。带载荷的消息不会走到这里：它的取件、
            # 反馈与审计在 `_collect_attachments` 里闭环。
            logger.warning(
                "inbound_envelope_without_payload message_id=%s channel=%s",
                envelope.message_id,
                envelope.channel,
            )
            return True
        text = envelope.text.strip()
        metrics.inc_counter(
            MESSAGES_METRIC,
            1,
            {"type": "command" if _is_command(text) else "text"},
            help="Inbound IM messages by type",
        )
        route = route_from_envelope(envelope)
        # 回复生命周期开始时**只此一处**取一次快照并固定（ADR-04）：整个回复期间（含 tick）
        # 都用它，下一条入站消息重新取。
        try:
            settings = await self._resolve_reply_settings()
        except AppError as exc:
            # 设置读取失败也要**让用户看见**：此前它发生在业务异常处理之外，只落一条 unexpected
            # 日志（用户既不建 Run 也收不到任何提示），而消息却已被去重键标记成处理过。
            await self._reply_error(adapter, route, exc, locale=DEFAULT_REPLY_LOCALE)
            return False
        token = _REPLY_SETTINGS.set(settings)
        try:
            if text == BIND_COMMAND or text.startswith(f"{BIND_COMMAND} "):
                return await self._handle_bind(adapter, route, envelope, text)
            if text == NEW_COMMAND:
                return await self._handle_new(adapter, route, envelope)
            if text == STOP_COMMAND:
                return await self._handle_stop(adapter, route, envelope)
            if text == SKILLS_COMMAND:
                return await self._handle_skills(adapter, route, envelope)
            return await self._handle_message(adapter, route, envelope)
        finally:
            _REPLY_SETTINGS.reset(token)

    def _open_reply_session(
        self, adapter: ChannelAdapter, envelope: ChannelEnvelope
    ) -> ChannelReplySession | None:
        """固定**本条消息自己的**回复会话：命令回执、附件回执、错误文案都回到它自己的回调上。

        此前只有走 Run 的路径开会话，其余一律走"本路由最新回调"，于是同会话后来的消息会把前
        面那条消息的回执顶掉（实测：`/bind` 的成功回执发到了后来的那条消息上）。

        打不开就退回适配器默认路径（那是"主动投递"）：渠道没有会话内回复能力、或该回调已过期
        （TTL 5 分钟）都会走到这里，此时退回本路由最新回调是唯一还能把话说出去的路径。
        """
        if not isinstance(adapter, ReplySessionFactory):
            return None
        try:
            return adapter.open_reply(route_from_envelope(envelope), envelope.message_id)
        except ChannelAdapterUnavailable:
            logger.warning("channel_reply_session_unavailable channel=%s", envelope.channel)
            return None

    async def _close_reply_session(
        self, session: ChannelReplySession | None, envelope: ChannelEnvelope
    ) -> None:
        """收尾并**释放**本会话（`finish` 幂等；Run 路径已经在 finalize 里收过尾）。

        不释放会让会话一直挂在适配器的活跃集合里：命令回执这类一次性文本没有别的收尾点。
        """
        if session is None:
            return
        try:
            await session.finish()
        except ChannelAdapterUnavailable:
            logger.warning("channel_reply_close_failed channel=%s", envelope.channel, exc_info=True)

    async def _resolve_reply_settings(self) -> ReplySettings:
        """取该租户当前平台设置快照并解析出这份回复的 locale 与节拍。

        源不可读 ⇒ 明确失败（`AppError`，由 `_dispatch` 转成用户可见反馈并放行重投），
        绝不回退过期默认值。
        """
        snapshot = await self._settings_client.fetch_snapshot(
            tenant_id=self._tenant_id, trace_id=current_trace_id()
        )
        return resolve_reply_settings(snapshot)

    def _activity_messages(self, locale: str) -> ActivityMessages:
        """按 locale 缓存状态文案目录（同 locale 只读一次 YAML；locale 逐条回复可能不同）。"""
        catalog = self._activity_catalogs.get(locale)
        if catalog is None:
            catalog = ActivityMessages(self._catalog.file_path, locale)
            self._activity_catalogs[locale] = catalog
        return catalog

    async def _reserve_seen(self, envelope: ChannelEnvelope) -> _SeenReservation | None:
        """占位；`None` = **已在处理/已受理**（重复投递，丢弃）。

        只占位、不写终值：终值（"已受理"）由 `_mark_accepted` 在真正受理之后写。见 `handle`。
        """
        key = f"{DEDUPE_PREFIX}:{envelope.channel}:{envelope.message_id}"
        try:
            owner = await self._dedupe.reserve(key, DEDUPE_TTL_SEC)
        except DedupeStoreError as exc:
            metrics.inc_counter(
                MESSAGE_FAILURES_METRIC, 1, {"reason": "dedupe_unavailable"}, help="IM message failures"
            )
            logger.warning("dedupe_store_failed message_id=%s error=%s", envelope.message_id, exc)
            # fail-open：照常处理。丢消息比重复消息更严重（RULE-13 的同一条口径）。
            return _SeenReservation(key=key, owner=None)
        if owner is None:
            metrics.inc_counter(DEDUPE_HITS_METRIC, 1, help="Inbound dedupe hits")
            logger.debug("duplicate_message_ignored message_id=%s", envelope.message_id)
            return None
        return _SeenReservation(key=key, owner=owner)

    async def _mark_accepted(self, seen: _SeenReservation, envelope: ChannelEnvelope) -> None:
        """把占位升级成"已受理"（TTL 600s）。此后同 `message_id` 的重投不再重复处理。"""
        if seen.owner is None:  # 去重存储不可用：没有可升级的键
            return
        try:
            await self._dedupe.mark(seen.key, seen.owner, DEDUPE_VALUE, DEDUPE_TTL_SEC)
        except DedupeStoreError as exc:
            logger.warning("dedupe_mark_failed message_id=%s error=%s", envelope.message_id, exc)

    async def _release_seen(self, seen: _SeenReservation, envelope: ChannelEnvelope) -> None:
        """**未受理就释放占位**，让平台的重投能真正重试（CAS：只释放属于自己的占位）。"""
        if seen.owner is None:
            return
        try:
            await self._dedupe.release(seen.key, seen.owner)
        except DedupeStoreError as exc:
            logger.warning("dedupe_release_failed message_id=%s error=%s", envelope.message_id, exc)

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
    ) -> bool:
        code = text[len(BIND_COMMAND) :].strip()
        if not code:
            await self._send_text(adapter, route, BIND_USAGE_TEXT)
            return True
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
            # 依赖调用没成功 ⇒ **未受理**：平台重投时重跑（Console 侧幂等键=message_id，不会重复绑定）
            return False
        await self._send_text(adapter, route, BIND_SUCCESS_TEXT)
        return True

    async def _handle_new(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        envelope: ChannelEnvelope,
    ) -> bool:
        resolved = await self._resolve(adapter, route, envelope)
        if resolved is None:
            return False
        if resolved.platform_user_id is None or resolved.agent_id is None:
            await self._send_text(adapter, route, UNBOUND_TEXT)
            return True
        if not resolved.authorized:
            await self._send_text(adapter, route, NO_PERMISSION_TEXT)
            return True
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
            return False
        await self._send_text(adapter, route, NEW_CONVERSATION_TEXT)
        return True

    async def _handle_stop(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        envelope: ChannelEnvelope,
    ) -> bool:
        resolved = await self._resolve(adapter, route, envelope)
        if resolved is None:
            return False
        if resolved.platform_user_id is None or resolved.agent_id is None:
            await self._send_text(adapter, route, UNBOUND_TEXT)
            return True
        if not resolved.authorized:
            await self._send_text(adapter, route, NO_PERMISSION_TEXT)
            return True
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
                return True
            await self._reply_error(adapter, route, exc)
            return False
        # 设计 §3.4.2：WAITING_INPUT 已被 Runtime 直接 CAS 为 CANCELLED（立即"已停止"），
        # CREATED/RUNNING 只受理；Gateway 不猜测也不缓存活跃 Run
        await self._send_text(
            adapter,
            route,
            STOP_CANCELLED_TEXT
            if str(result.get("status") or "") == str(RunStatus.CANCELLED)
            else STOP_ACCEPTED_TEXT,
        )
        return True

    async def _handle_skills(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        envelope: ChannelEnvelope,
    ) -> bool:
        resolved = await self._resolve(adapter, route, envelope)
        if resolved is None:
            return False
        if resolved.platform_user_id is None or resolved.agent_id is None:
            await self._send_text(adapter, route, UNBOUND_TEXT)
            return True
        if not resolved.authorized:
            await self._send_text(adapter, route, NO_PERMISSION_TEXT)
            return True
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
            return False
        await self._send_text(adapter, route, format_skills(catalog))
        return True

    async def _handle_message(
        self,
        adapter: ChannelAdapter,
        route: DeliveryRouteInput,
        envelope: ChannelEnvelope,
    ) -> bool:
        resolved = await self._resolve(adapter, route, envelope)
        if resolved is None:
            return False
        if resolved.platform_user_id is None or resolved.agent_id is None:
            await self._send_text(adapter, route, UNBOUND_TEXT)
            return True
        if not resolved.authorized:
            await self._send_text(adapter, route, NO_PERMISSION_TEXT)
            return True
        # 附件链路放在**授权之后**：未绑定/无权限那两条早退路径不会取件，"能收才去拉"，
        # 把 AD-1-B 下（字节先于 Run 落盘）的无主产物压到最小（设计 §3.2.1）。
        collected = await self._collect_attachments(adapter, route, envelope)
        if not collected.refs and not envelope.text.strip():
            # 没有可跑的内容（附件全部被拒/取件失败，且文本为空）：反馈与审计已由
            # `_collect_attachments` 闭环，这里只是**不**把空消息当用户输入发给模型。
            return True
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
        return await self._consume_run(adapter, route, request, resolved.platform_user_id)

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
        codes = [*failures, *(rejection.code for rejection in precheck.rejected)]
        codes.extend(rejection.code for rejection in decision.rejected)
        for position, (index, _candidate, content) in enumerate(graded):
            if position in rejected_positions:
                continue
            try:
                ref = store.persist(
                    token=envelope.message_id,
                    index=index,
                    content=content,
                    source_channel=envelope.channel,
                )
            except AttachmentConflictError:
                # 同一个键上已经有**别的**字节：既不覆盖也不当成自己的，这一份按未收下处理。
                logger.error(
                    "inbound_attachment_conflict message_id=%s index=%s",
                    envelope.message_id,
                    index,
                )
                codes.append(ATTACHMENT_FETCH_FAILED)
                continue
            except OSError as exc:
                # 落盘是本地副作用：一份写不进去只影响这一份，不该把整条消息（含有效文本）丢掉
                logger.error(
                    "inbound_attachment_write_failed message_id=%s index=%s error=%s",
                    envelope.message_id,
                    index,
                    type(exc).__name__,
                )
                codes.append(ATTACHMENT_FETCH_FAILED)
                continue
            refs.append(ref)
            total_bytes += ref.size

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
    ) -> bool:
        """跑完一次 Run 的 SSE 流；返回 **Run 是否已被 Runtime 受理**（见 `handle` 的去重口径）。

        "受理"的判据是**提交没被拒**：`create_run` 抛 `AppError`（连不上、4xx/5xx）才算没受理，
        此时放行平台重投去真正重试；流开出来之后无论跑成什么样，Runtime 那边都已经有这条 Run
        （幂等键就是 `message_id`），重投只会被它按幂等重放——所以那时该去重，不该重来一遍。
        """
        state = _RunStreamState(self._build_renderer())
        # 会话由 `handle` 在入站那一刻固定（`_REPLY_SESSION`）：本条消息的所有回复——回执、错误、
        # 状态、正文——都回到**它自己的**回调上，不受同会话后续消息影响。
        state.reply = _REPLY_SESSION.get()
        if state.reply is not None:
            state.activity_messages = self._activity_messages(self._locale)
        finalized = False
        try:
            await self._update_progress(route, state, force=True)
            stream = self._runtime.create_run(request, tenant_id=self._tenant_id)
            async with aclosing(iter_with_ticks(stream, interval=self._tick_interval_sec)) as events:
                async for event in events:
                    if event is None:
                        # 计时拍：**同一拍里也要把到点的正文缓冲发出去**。正文此前只在下一条
                        # 增量到来时才可能被冲出去，模型一思考/一跑工具就整段挂在缓冲里
                        # （实测：只收一个 delta 后静默 1.2 秒，客户端一个正文帧都没收到）。
                        await self._flush(adapter, route, state)
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
            return False
        finally:
            if not finalized:
                await self._run_actions(adapter, route, state, state.renderer.finalize())
        metrics.set_gauge(
            STREAM_LATENCY_METRIC,
            _elapsed_ms(state.started_at),
            help="Whole-stream latency until finalize (ms)",
        )
        return True

    @property
    def _tick_interval_sec(self) -> float:
        """SSE 读取循环的醒来的节拍：**计时与正文刷新取更小的那个**。

        正文档节拍（5s）只决定"计时状态多久更新一次"，不该顺带决定正文多久能出去一次；
        正文的合并窗口是 `delta_flush_interval_sec`（0.5s）。计时仍按 `_progress_interval_sec`
        节流（见 `_update_progress`），所以客户端看到的计时粒度不变。
        """
        flush = self._delta_flush_interval_sec
        if flush <= 0:
            # 直构的调用方把合并窗口设成 0（每条增量立即 flush）：那正文不需要定时器，
            # 读取循环按计时节拍醒来即可 —— 但**必须为正**（`iter_with_ticks` 不接受 0）。
            return self._progress_interval_sec
        return min(self._progress_interval_sec, flush)

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
            state.last_progress_at = time.monotonic()
            return
        # 读取循环的醒来节拍（0.5s，为了及时冲正文）**不等于**计时节拍（5s）：正文要快，
        # 计时帧要少（每帧都让客户端整帧重排）。这里按 `im.progress_interval_sec` 节流。
        now = time.monotonic()
        if now - state.last_progress_at < self._progress_interval_sec:
            return
        if state.status_task is None or state.status_task.done():
            state.status_task = asyncio.create_task(self._send_status(route, state))
            state.last_progress_at = now

    async def _send_status(self, route: DeliveryRouteInput, state: _RunStreamState) -> None:
        reply = state.reply
        activity_messages = state.activity_messages
        if reply is None or activity_messages is None:  # 会话在任务起跑前被回收（收尾/停机）
            return
        try:
            await reply.update_status(activity_messages.render(state.progress))
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
        """一条独立文本（命令回执 / 附件回执 / 错误文案）。

        **优先走本消息自己的回复会话**（`_REPLY_SESSION`）：回执必须落在**发起它的那条消息**上。
        此前这里一律走 `adapter.send` → 适配器的"本路由最新回调"，于是同会话后来的消息会把前面
        那条的回执顶掉（实测：`/bind` 的成功回执发到了后来的那条消息上）。

        没有会话（渠道不支持会话内回复、或本消息的回调已过期）才退回 `adapter.send` —— 那是
        适配器里的"主动投递"路径，与"回到某条消息"是两回事。
        """
        if not text:
            return
        session = _REPLY_SESSION.get()
        if session is not None:
            try:
                await session.reply_once(text)
            except ChannelAdapterUnavailable as exc:
                logger.warning("channel_reply_unavailable channel=%s error=%s", route.channel, exc)
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
        locale: str | None = None,
    ) -> None:
        """把依赖错误转成用户可见文案。

        `locale` 显式传入只用于**回复设置本身取不到**的那条路径：那时 `_REPLY_SETTINGS` 还没
        建立，只能退回目录默认语言——但**不能因此不回复**（用户看不见错误等于消息被吞了）。
        """
        metrics.inc_counter(RUNTIME_ERRORS_METRIC, 1, {"code": exc.code}, help="Runtime error codes observed")
        metrics.inc_counter(MESSAGE_FAILURES_METRIC, 1, {"reason": exc.code}, help="IM message failures")
        logger.warning("inbound_dependency_error code=%s", exc.code)
        text = self._catalog.message(exc.code, locale or self._locale)
        if reply is None:
            await self._send_text(adapter, route, text)
        else:
            try:
                await reply.send(text)
            except ChannelAdapterUnavailable:
                logger.warning("channel_error_reply_failed channel=%s", route.channel, exc_info=True)
