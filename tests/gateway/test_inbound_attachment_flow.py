"""[TASK-004] 附件编排的**渠道中立性**（S-07）与**重投幂等**（E-07）。

**S-07 的验法**：同一批"内容等价"的输入，分别喂给两条适配器——

- `FakeChannelAdapter`：内存字节，**没有 url、没有 aes_key**（另一条完全不同的取件路径）；
- 生产 `WeComAdapter`：真实 HTTP + 真实 AES-256-CBC 密文（企微路径）。

然后断言门控结论、契约产出、用户可见反馈、审计记录与落盘字节**逐项相同**。这正是 AD-8/RULE-07
要的性质：换通道只写适配器，预检/取件/实检/落盘/反馈/审计这一整段一行都不用改。若哪天核心域里
长出 `if channel == "WECOM"` 这样的分支，两条路径的结论就会开始分叉，本用例会先红。

**E-07 的验法**：幂等键用**真实 Redis** 承载。同一 `msgid` 重投不产生第二次 Run、不产生第二份
产物（第二道防线是落盘的不可变写入，见 `test_inbound_attachment_store.py`）。
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fakes import FakeConsoleClient, FakeRuntimeClient
from muad_api.catalog import MessageCatalog
from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings
from muad_contracts import AttachmentRef, BotSnapshotItem, ChannelEnvelope, InboundAuditRequest
from muad_im_gateway.application.attachment_gate import (
    ATTACHMENT_COUNT_EXCEEDED,
    ATTACHMENT_TYPE_NOT_ALLOWED,
    MAX_ATTACHMENTS_PER_MESSAGE,
)
from muad_im_gateway.application.inbound import DEDUPE_TTL_SEC, InboundPipeline
from muad_im_gateway.application.inbound_attachments import InboundAttachmentStore
from muad_im_gateway.application.runtime_client import SseEvent
from muad_im_gateway.channels.fake import FakeBlob, FakeChannelAdapter
from muad_im_gateway.channels.wecom.adapter import WeComAdapter, _AibotClientPort
from muad_im_gateway.infrastructure.dedupe import RedisDedupeStore, build_dedupe_store

from tests.e2e.wecom_media_server import AES_KEY, MediaServer, wecom_ciphertext

BOT_ID = "bot-s07"
TENANT_ID = "tenant-attachment-flow"
PNG = b"\x89PNG\r\n\x1a\n" + b"s07-pixels" * 4
TEXT_FILE = b"# harness\nattachment flow\n"
ZIP_FILE = b"PK\x03\x04" + b"not-whitelisted" * 2
CATALOG_PATH = Path(__file__).resolve().parents[2] / "config" / "api-messages.yaml"


def resolved_response() -> Any:
    """已绑定且已授权的解析结果（两条路径共用一份口径）。"""
    return FakeConsoleClient().resolve_response.model_copy(
        update={"bound": True, "authorized": True, "agent_id": uuid4(), "platform_user_id": uuid4()}
    )


class _NullDedupe:
    """不干扰的幂等键：S-07 比的是编排行为，不是去重（E-07 用真实 Redis 单独验）。"""

    async def set_if_absent(self, key: str, ttl_sec: int) -> bool:
        return True

    async def exists(self, key: str) -> bool:
        return False

    async def mark(self, key: str, ttl_sec: int) -> None: ...

    async def reserve(self, key: str, ttl_sec: int) -> bool:
        return True

    async def get_value(self, key: str) -> str | None:
        return None

    async def release(self, key: str) -> None: ...

    async def aclose(self) -> None: ...


# --------------------------------------------------------------- 企微侧的取件凭据壳


class _StubRawClient:
    """承载 `_AibotClientPort` 的原始 SDK 客户端替身：只登记回调，取件不走它。

    取件由 `_AibotClientPort.download_media` 直接走本仓的 `download_media`（真实 HTTP + 真实解密），
    与 SDK 的 `download_file` 无关——所以这里的替身不需要任何下载能力。
    """

    def __init__(self) -> None:
        self.handlers: dict[str, Callable[..., Any]] = {}
        #: 出站文本（口径与 `FakeChannelAdapter.sent` 一致：一次完整回复一条）
        self.sent: list[str] = []

    @property
    def is_connected(self) -> bool:
        return True

    def on(self, event: str, handler: Callable[..., Any]) -> object:
        self.handlers[event] = handler
        return self

    async def connect(self) -> object:
        # 认证回调是"进 CONNECTED"的唯一开关（`_BotConnection._handle_authenticated`）：
        # 不触发它，取件会因连接未就绪而失败——那不是本用例要验的东西。
        handler = self.handlers.get("authenticated")
        if handler is not None:
            handler()
        return self

    def disconnect(self) -> None: ...

    async def send_message(self, chatid: str, body: dict[str, object]) -> object:
        self._record(str((body.get("markdown") or {}).get("content") or ""))
        return {}

    async def reply_stream(
        self, frame: dict[str, object], stream_id: str, content: str, finish: bool = False
    ) -> object:
        if finish:  # 收尾帧才算一条完整回复（流式中间帧与空收尾不计）
            self._record(content)
        return {}

    def _record(self, text: str) -> None:
        if text:
            self.sent.append(text)


def _wecom_frame(message_id: str, items: list[tuple[bytes, str, str]], server: MediaServer) -> dict[str, Any]:
    """`mixed` 帧：每个媒体项一条真实 HTTP URL（密文由 `MediaServer` 真实承载）。"""
    msg_item: list[dict[str, Any]] = []
    for position, (plaintext, media_type, filename) in enumerate(items):
        url = server.serve(f"/{message_id}/{position}", wecom_ciphertext(plaintext), filename=filename)
        msg_type = "image" if media_type.startswith("image/") else "file"
        msg_item.append({"msgtype": msg_type, msg_type: {"url": url, "aeskey": AES_KEY}})
    return {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": f"req-{message_id}"},
        "body": {
            "msgid": message_id,
            "chatid": "conv-s07",
            "from": {"userid": "ext-s07"},
            "msgtype": "mixed",
            "mixed": {"msg_item": msg_item},
        },
    }


# --------------------------------------------------------------------------- 驱动


@dataclass(frozen=True, slots=True)
class _Outcome:
    """一次编排的全部可观察产出（两条路径比的就是这些）。"""

    refs: tuple[AttachmentRef, ...]
    #: 出站文本，**附件链路的反馈在前、Run 的回复在后**（取件/门控先于建 Run）
    feedback: tuple[str, ...]
    audits: tuple[InboundAuditRequest, ...]
    run_message: tuple[str, tuple[AttachmentRef, ...]] | None
    stored: dict[str, bytes]


def _build_pipeline(root: Path, dedupe: Any) -> tuple[InboundPipeline, FakeConsoleClient, FakeRuntimeClient]:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    # 正常的 Run 流：附件链路的结论要与"Run 正常跑完"并存，否则反馈里会混进 BROKEN_STREAM 文案
    runtime = FakeRuntimeClient(
        [
            SseEvent(type="run.created", data={"run_id": "run-1", "resumed": False}),
            SseEvent(type="run.completed", data={"status": "COMPLETED", "final_text": "收到"}),
        ]
    )
    pipeline = InboundPipeline(
        dedupe=dedupe,
        console=console,
        runtime=runtime,
        catalog=MessageCatalog(CATALOG_PATH),
        attachment_store=InboundAttachmentStore(NfsArtifactStore(root)),
        tenant_id=TENANT_ID,
        locale="zh-CN",
    )
    return pipeline, console, runtime


async def _drive(
    *,
    adapter: Any,
    envelope: ChannelEnvelope,
    root: Path,
    dedupe: Any,
    outbound: Callable[[], tuple[str, ...]],
) -> _Outcome:
    pipeline, console, runtime = _build_pipeline(root, dedupe)
    await pipeline.handle(adapter, envelope)
    stored = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    return _Outcome(
        refs=tuple(ref for request in runtime.run_requests for ref in request.message.attachments),
        feedback=outbound(),
        audits=tuple(console.audit_calls),
        run_message=(
            (runtime.run_requests[0].message.type, tuple(runtime.run_requests[0].message.attachments))
            if runtime.run_requests
            else None
        ),
        stored=stored,
    )


def _fake_outbound(adapter: FakeChannelAdapter) -> tuple[str, ...]:
    return tuple(message.text for _route, message in adapter.sent)


def _media_only_envelope(message_id: str) -> ChannelEnvelope:
    """纯媒体消息：**文本为空**——取件前 `attachments` 也必然是空的（AD-8）。"""
    return ChannelEnvelope(
        channel="WECOM",
        bot_id=BOT_ID,
        external_user_id="ext-s07",
        external_conversation_id="conv-s07",
        message_id=message_id,
        text="",
    )


#: 一条消息里同时踩到三种结局：接收 / 类型拒绝 / 数量超限。
#: 两条路径给的输入必须逐位对应，否则"结论相同"就无从比起。
CASE: tuple[tuple[bytes, str, str], ...] = (
    (PNG, "image/png", "shot.png"),
    (TEXT_FILE, "text/plain", "notes.txt"),
    (ZIP_FILE, "application/zip", "bundle.zip"),  # 不在白名单 → 实检拒绝
    (PNG + b"-second", "image/png", "shot-2.png"),
    (PNG + b"-third", "image/png", "shot-3.png"),
    (PNG + b"-sixth", "image/png", "shot-6.png"),  # 第 6 个 → 预检按位置丢弃
)


async def _fake_outcome(root: Path, message_id: str) -> _Outcome:
    """伪渠道路径：字节直接来自内存，取件完全不经过 url/aes_key。"""
    adapter = FakeChannelAdapter()
    envelope = _media_only_envelope(message_id)
    await adapter.push(
        envelope,
        blobs=[FakeBlob(data=data, media_type=mime, filename=name) for data, mime, name in CASE],
    )
    return await _drive(
        adapter=adapter,
        envelope=envelope,
        root=root,
        dedupe=_NullDedupe(),
        outbound=lambda: _fake_outbound(adapter),
    )


async def _wecom_outcome(root: Path, server: MediaServer, message_id: str) -> _Outcome:
    """企微路径：真实 HTTP 下载 + 真实解密，帧经适配器分流。"""
    raw = _StubRawClient()
    bot = BotSnapshotItem(
        bot_account_id=uuid4(), bot_id=BOT_ID, secret="s", agent_id=uuid4(), enabled=True
    )
    adapter = WeComAdapter(
        sdk_factory=lambda _bot_id, _secret: _AibotClientPort(raw, bot_id=BOT_ID), bots=(bot,)
    )
    await adapter.start()
    try:
        raw.handlers["message"](_wecom_frame(message_id, list(CASE), server))
        envelope = await asyncio.wait_for((await adapter.iter_events()).__anext__(), timeout=5)
        return await _drive(
            adapter=adapter,
            envelope=envelope,
            root=root,
            dedupe=_NullDedupe(),
            outbound=lambda: tuple(raw.sent),
        )
    finally:
        await adapter.stop()


@pytest.fixture()
def media_server() -> AsyncIterator[MediaServer]:
    server = MediaServer()
    try:
        yield server
    finally:
        server.close()


# ------------------------------------------------------------------ S-07 行为一致


@pytest.mark.integration
async def test_s07_two_fetch_paths_produce_identical_outcomes(
    tmp_path: Path, media_server: MediaServer
) -> None:
    """S-07：换一条取件路径（内存字节 vs 真实 HTTP+解密），门控/契约/落盘结论逐项相同。"""
    fake_root = tmp_path / "fake"
    wecom_root = tmp_path / "wecom"
    fake_root.mkdir()
    wecom_root.mkdir()

    # 同一个 msgid 喂两条路径：产物键、审计里的 external_message_id 才能逐字段对得上
    message_id = f"msg-s07-{time.time_ns()}"
    fake = await _fake_outcome(fake_root, message_id)
    wecom = await _wecom_outcome(wecom_root, media_server, message_id)

    # ① 契约产出完全一致（storage_key/kind/media_type/size/filename/checksum 逐字段）
    assert fake.refs == wecom.refs
    assert len(fake.refs) == 4
    assert [ref.filename for ref in fake.refs] == ["shot.png", "notes.txt", "shot-2.png", "shot-3.png"]
    for ref in fake.refs:
        assert ref.storage_key.startswith("inbound/") and not ref.storage_key.startswith("/")
        assert ref.checksum and ref.size > 0

    # ② 用户可见反馈一致，文案取自消息目录、原因码取自门控（类型拒绝优先于数量超限）
    assert fake.feedback == wecom.feedback
    assert fake.feedback[0] == MessageCatalog(CATALOG_PATH).message(
        ATTACHMENT_TYPE_NOT_ALLOWED, "zh-CN"
    )

    # ③ 审计一致：带载荷的消息一行，计数与结局相同
    assert [audit.model_dump() for audit in fake.audits] == [
        audit.model_dump() for audit in wecom.audits
    ]
    assert len(fake.audits) == 1
    assert fake.audits[0].outcome == "RECEIVED"  # 有附件落盘成功 ⇒ 不是整条被拒
    assert fake.audits[0].attachment_count == 6 and fake.audits[0].accepted_count == 4
    assert fake.audits[0].total_bytes == sum(ref.size for ref in fake.refs)

    # ④ 落盘字节一致：同一份明文，两种取件方式必须得到同一份字节
    assert sorted(fake.stored.values()) == sorted(wecom.stored.values())
    assert sorted(fake.stored.values()) == sorted({PNG, TEXT_FILE, PNG + b"-second", PNG + b"-third"})
    assert len(fake.stored) == 4

    # ⑤ 进 Run 的消息带上了附件（纯媒体消息也走得到这里），且 type 被置为 attachment
    assert fake.run_message is not None and wecom.run_message is not None
    assert fake.run_message == wecom.run_message
    assert fake.run_message[0] == "attachment"
    assert fake.run_message[1] == fake.refs


@pytest.mark.integration
async def test_s07_media_only_envelope_is_not_dropped_at_the_entry(tmp_path: Path) -> None:
    """入口护栏只看 `attachments` 会拦掉纯媒体消息（取件前它必然为空）——本用例钉住这一点。"""
    adapter = FakeChannelAdapter()
    envelope = _media_only_envelope(f"msg-only-{time.time_ns()}")
    await adapter.push(envelope, blobs=[FakeBlob(data=PNG, media_type="image/png", filename="a.png")])

    outcome = await _drive(
        adapter=adapter,
        envelope=envelope,
        root=tmp_path,
        dedupe=_NullDedupe(),
        outbound=lambda: _fake_outbound(adapter),
    )

    assert len(outcome.refs) == 1
    assert outcome.run_message is not None and outcome.run_message[0] == "attachment"
    assert outcome.audits[0].accepted_count == 1


@pytest.mark.integration
async def test_s07_count_limit_is_reported_with_the_gate_number(tmp_path: Path) -> None:
    """数量超限：只丢超出的那些，文案里的数值直接来自门控常量（只有一处定义）。"""
    adapter = FakeChannelAdapter()
    envelope = _media_only_envelope(f"msg-count-{time.time_ns()}")
    await adapter.push(
        envelope,
        blobs=[
            FakeBlob(data=PNG + bytes([index]), media_type="image/png", filename=f"{index}.png")
            for index in range(MAX_ATTACHMENTS_PER_MESSAGE + 1)
        ],
    )

    outcome = await _drive(
        adapter=adapter,
        envelope=envelope,
        root=tmp_path,
        dedupe=_NullDedupe(),
        outbound=lambda: _fake_outbound(adapter),
    )

    assert len(outcome.refs) == MAX_ATTACHMENTS_PER_MESSAGE
    expected = MessageCatalog(CATALOG_PATH).message(
        ATTACHMENT_COUNT_EXCEEDED, "zh-CN", {"limit": MAX_ATTACHMENTS_PER_MESSAGE}
    )
    assert outcome.feedback[0] == expected
    assert str(MAX_ATTACHMENTS_PER_MESSAGE) in outcome.feedback[0]
    assert outcome.audits[0].reason_code == ATTACHMENT_COUNT_EXCEEDED
    assert outcome.audits[0].total_bytes == sum(ref.size for ref in outcome.refs)


@pytest.mark.integration
async def test_s07_take_failure_is_reported_and_audited_not_silently_dropped(tmp_path: Path) -> None:
    """取件失败（引用越界/已驱逐）：明确反馈 + 审计，**不**把空消息当用户输入发出去。"""
    adapter = FakeChannelAdapter()
    envelope = _media_only_envelope(f"msg-e03-{time.time_ns()}")
    blob = FakeBlob(data=PNG, media_type="image/png", filename="a.png")
    await adapter.push(envelope, blobs=[blob])

    pipeline, console, runtime = _build_pipeline(tmp_path, _NullDedupe())
    granted = console.resolve_response

    async def _resolve_then_evict(request: Any, tenant_id: str) -> Any:
        """授权往返期间引用过期/被驱逐：入口护栏看到过引用，取件时它已经不在了。"""
        adapter._pending.clear()  # noqa: SLF001 - 直接制造"引用已不在"这一事实
        return granted

    console.resolve = _resolve_then_evict  # type: ignore[method-assign]
    await pipeline.handle(adapter, envelope)

    feedback = tuple(message.text for _route, message in adapter.sent)
    assert runtime.run_requests == [], "取件失败不得建 Run（空文本不是用户输入）"
    assert feedback == (MessageCatalog(CATALOG_PATH).message("ATTACHMENT_FETCH_FAILED", "zh-CN"),)
    assert console.audit_calls[0].outcome == "REJECTED"
    assert console.audit_calls[0].reason_code == "ATTACHMENT_FETCH_FAILED"
    assert _digests(tmp_path) == {}


# ------------------------------------------------------------------ E-07 重投幂等


@pytest.fixture()
async def redis_store() -> AsyncIterator[RedisDedupeStore]:
    """真实 Redis：幂等键必须在真依赖上验（降级成 Null store 会让本用例变成恒真）。"""
    settings = SharedSettings()
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL 未配置：重投幂等验收需要真实 Redis（不得 skip）")
    store = await build_dedupe_store(settings.redis_url)
    if not isinstance(store, RedisDedupeStore):
        raise RuntimeError("Redis 不可达，build_dedupe_store 降级为 Null store：需要真实 Redis")
    try:
        yield store
    finally:
        await store.aclose()


def _digests(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.integration
async def test_e07_redelivery_creates_no_second_run_and_no_duplicate_artifact(
    tmp_path: Path, redis_store: RedisDedupeStore
) -> None:
    """E-07：同一 `msgid` 重投 → 一次 Run、一份产物、一行审计（企微会重投）。"""
    adapter = FakeChannelAdapter()
    envelope = _media_only_envelope(f"msg-e07-{time.time_ns()}")
    blob = FakeBlob(data=PNG, media_type="image/png", filename="a.png")
    await adapter.push(envelope, blobs=[blob])

    pipeline, console, runtime = _build_pipeline(tmp_path, redis_store)
    key = f"im:dedupe:WECOM:{envelope.message_id}"
    try:
        await pipeline.handle(adapter, envelope)
        first = _digests(tmp_path)
        assert len(runtime.run_requests) == 1 and len(first) == 1

        await adapter.push(envelope, blobs=[blob])  # 重投：同 msgid、同引用
        await pipeline.handle(adapter, envelope)

        assert len(runtime.run_requests) == 1, "重投不得再建 Run"
        assert _digests(tmp_path) == first, "重投不得新增或覆盖产物"
        assert len(console.audit_calls) == 1, "重投在去重处即返回：不写第二条审计"
        assert await redis_store.get_value(key) == "1"
        ttl = await redis_store._client.ttl(key)  # noqa: SLF001 - 断言真实 Redis TTL
        assert 0 < ttl <= DEDUPE_TTL_SEC
    finally:
        await redis_store.release(key)
