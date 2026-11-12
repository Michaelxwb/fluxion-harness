"""[契约] 附件引用在渠道边界与消息契约上的同型承载。

**为什么需要**：附件必须挂在**渠道边界类型** `ChannelEnvelope` 上，而不是只挂 Runtime 侧契约 ——
否则渠道的媒体类型要穿过适配器边界渗透进核心域（`harness-im#RULE-im-001`），"换通道只写适配器"
就不成立。本文件钉死三件可序列化/可静态判定的事：

- B-05：零附件消息的既有字段序列化结果不变，且**不新增必填字段**（向后兼容）。
- B-07：通道名只在一处声明（`BUILTIN_CHANNEL_NAMES`），契约字段是**开放 string**；
  新增 fake channel 不需要改任何字面量（适配器名即路由身份）。
- S-07：附件引用的结构里**不含任何取件凭据**（url / aes_key / media_id / download_code …）——
  取件方式渠道私有（设计 AD-8），边界只描述"已到手的字节"。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from muad_contracts import (
    ALLOWED_ATTACHMENT_MEDIA_TYPES,
    ARCHIVE_MEDIA_TYPES,
    BUILTIN_CHANNEL_NAMES,
    LOCAL_STREAM_CHANNELS,
    OUTBOUND_CHANNELS,
    SUPPORTED_UPLOAD_FORMATS,
    AttachmentRef,
    ChannelEnvelope,
    MessageInput,
    is_archive_media_type,
    is_text_media_type,
)
from muad_contracts.im import InboundEvent
from muad_im_gateway.channels.base import DuplicateChannelAdapterError, UnknownChannelAdapterError
from muad_im_gateway.channels.fake import FakeChannelAdapter

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = ROOT / "packages" / "contracts" / "src" / "muad_contracts"
CONSOLE_DTO = (
    ROOT
    / "apps"
    / "console-platform"
    / "backend"
    / "src"
    / "muad_console_platform"
    / "application"
    / "dto.py"
)

# 改造前已有的字段集合：零附件时这些字段的取值必须一字不变
BASELINE_ENVELOPE_FIELDS = frozenset(
    {"channel", "bot_id", "external_user_id", "external_conversation_id", "message_id", "text"}
)
BASELINE_MESSAGE_FIELDS = frozenset({"id", "type", "text"})

# 附件引用**允许**持有的字段（AD-8：取件凭据一律不得进入边界类型）
#
# `artifact_id` 于 2026-10-03 **有意**从 `CREDENTIAL_FIELDS` 挪到这里（attachment-round-trip 的
# TASK-006/008），理由不是"用起来方便"，而是它**不满足"取件凭据"的定义**：
#   · 它本身换不到任何东西——取件端点 `GET /api/v1/artifacts/{id}/content` 要么要 Console 会话、
#     要么要**单产物 + 短 TTL + 可撤销**的签名令牌，且每次都用租户做归属校验；
#   · 同一结构里本来就有 `storage_key`（共享存储里的真实路径）——比一个不透明行 UUID 更直接；
#   · 而出站方向**必须**有它：渠道不能直发文件时要拼签名直链，没有 id 就拼不出来。
# 真正要挡的是**取件凭据**（url / aes_key / media_id / token），那几项一个都没放进来，见下面的集合。
ATTACHMENT_REF_FIELDS = frozenset(
    {
        "storage_key",
        "kind",
        "media_type",
        "size",
        "filename",
        "checksum",
        "source_channel",
        "artifact_id",
    }
)
#: **取件凭据**：拿到就能取走字节的东西。附件引用在类型上不得持有其中任何一个。
CREDENTIAL_FIELDS = frozenset(
    {"url", "aes_key", "aeskey", "media_id", "download_code", "access_token", "file_key"}
)


def test_b05_zero_attachment_serialization_stays_backward_compatible() -> None:
    """B-05：零附件时既有字段一字不变，且不新增必填字段。"""
    envelope = ChannelEnvelope(
        channel="WECOM",
        bot_id="bot-1",
        external_user_id="user-1",
        message_id="msg-1",
        text="hi",
    )
    dumped = envelope.model_dump()

    # 只用改造前的字段就能构造成功 ⇒ 没有新增必填字段
    assert BASELINE_ENVELOPE_FIELDS <= set(dumped)
    assert dumped["channel"] == "WECOM"
    assert dumped["bot_id"] == "bot-1"
    assert dumped["external_user_id"] == "user-1"
    assert dumped["external_conversation_id"] is None
    assert dumped["message_id"] == "msg-1"
    assert dumped["text"] == "hi"
    assert dumped["attachments"] == []

    message = MessageInput(id="msg-1", text="hi")
    message_dump = message.model_dump()
    assert BASELINE_MESSAGE_FIELDS <= set(message_dump)
    assert message_dump["type"] == "text", "缺省 type 必须仍是 text（向后兼容）"
    assert message_dump["attachments"] == []

    attachment_message = MessageInput(id="msg-2", type="attachment", text="", attachments=[])
    assert attachment_message.type == "attachment", "type 需能表达带附件的消息"


def test_b07_builtin_channel_names_are_declared_in_exactly_one_place() -> None:
    """B-07：内建通道名只在一处声明；其余契约源码引用别名，不再写枚举字面量。"""
    scanned = sorted(CONTRACT_DIR.glob("*.py")) + [CONSOLE_DTO]
    assert scanned, "未扫描到任何契约源码：路径规则可能已与目录结构脱节"

    assert BUILTIN_CHANNEL_NAMES == ("WECOM", "WEBCHAT"), (
        "内建通道名必须与 enums.py 的唯一声明一致（新增通道**不需要**改这里）"
    )
    literal_text = "Literal[" + ", ".join(f'"{value}"' for value in BUILTIN_CHANNEL_NAMES) + "]"
    hits = {
        path.name: path.read_text(encoding="utf-8").count(literal_text)
        for path in scanned
    }
    offenders = {name: count for name, count in hits.items() if count}
    assert offenders == {}, f"不得再有通道枚举字面量：{offenders}"

    # 别名必须被真正用起来，而不是只定义不引用
    for name in ("channel.py", "runtime.py", "tasks.py", "resolve.py"):
        text = (CONTRACT_DIR / name).read_text(encoding="utf-8")
        assert "ChannelName" in text, f"{name} 未引用 ChannelName 别名"


def test_b07_new_channel_needs_no_literal_change() -> None:
    """B-07（开放）：新通道 = 一个新适配器名；契约与注册表都不需要改字面量。

    这是 S-12「新增 fake channel 不改 ChannelName 字面量」的契约侧断言：
    `ChannelEnvelope` / `InboundEvent` 接受任何非空通道名，注册表按适配器的 `name` 路由。
    """
    envelope = ChannelEnvelope(
        channel="DINGTALK",
        bot_id="bot-x",
        external_user_id="user-x",
        message_id="msg-x",
        text="hi",
    )
    assert envelope.channel == "DINGTALK"
    event = InboundEvent.model_validate(
        {
            "event_id": "6f1b1f2a-7f1f-4f7e-9b0e-1f5d3a2c1b0a",
            "channel": "DINGTALK",
            "tenant_id": "t1",
            "bot_id": "bot-x",
            "external_message_id": "msg-x",
            "external_user_id": "user-x",
            "received_at": "2026-10-25T00:00:00Z",
        }
    )
    assert InboundEvent.model_validate(event.model_dump(mode="json")).channel == "DINGTALK"

    registry = ChannelRegistryForTest()
    registry.register(FakeChannelAdapter(name="DINGTALK"))
    assert registry.get("DINGTALK").name == "DINGTALK"
    with pytest.raises(DuplicateChannelAdapterError):
        registry.register(FakeChannelAdapter(name="DINGTALK"))
    with pytest.raises(UnknownChannelAdapterError):
        registry.get("SLACK")

    # 空名仍然被契约拒绝（开放 ≠ 什么都能进）
    with pytest.raises(ValueError):
        ChannelEnvelope(
            channel="", bot_id="b", external_user_id="u", message_id="m"
        )


def ChannelRegistryForTest():  # noqa: N802 - 小工具，保持与类名一致的显式构造
    from muad_im_gateway.channels.base import ChannelRegistry

    return ChannelRegistry()


async def test_s07_fake_adapter_carries_attachments_without_credentials() -> None:
    """S-07：渠道边界可承载附件引用，且该结构不含任何取件凭据。"""
    credentials_in_type = ATTACHMENT_REF_FIELDS & CREDENTIAL_FIELDS
    assert not credentials_in_type, f"附件引用不得持有取件凭据：{credentials_in_type}"
    assert set(AttachmentRef.model_fields) == set(ATTACHMENT_REF_FIELDS), (
        "AttachmentRef 字段集发生变化：新增字段须先确认它**不是取件凭据**；"
        "若是 DB 标识，还须回答「它本身换不换得到东西」（见文件头对 `artifact_id` 的记录）"
    )

    adapter = FakeChannelAdapter()
    reference = AttachmentRef(
        storage_key="inbound/2026/screenshot.png",
        kind="IMAGE",
        media_type="image/png",
        size=2048,
        filename="截图.png",
        checksum="sha256:" + "a" * 64,
        source_channel="WECOM",
    )
    await adapter.push(
        ChannelEnvelope(
            channel="WECOM",
            bot_id="bot-1",
            external_user_id="user-1",
            message_id="msg-1",
            text="看看这张图",
            attachments=[reference],
        )
    )

    stream = await adapter.iter_events()
    received = await anext(stream)
    await stream.aclose()

    assert len(received.attachments) == 1
    carried = received.attachments[0]
    assert carried.storage_key == "inbound/2026/screenshot.png"
    assert carried.kind == "IMAGE"
    assert carried.media_type == "image/png"
    assert carried.size == 2048
    assert carried.filename == "截图.png"
    assert carried.source_channel == "WECOM"
    assert set(carried.model_dump()) & CREDENTIAL_FIELDS == set()


def test_b07_delivery_capabilities_partition_the_builtin_channels() -> None:
    """交付方式按**入口能力**显式分组：每个内建通道恰属于一组，未知通道不在任何一组。

    产物交付只有两条真实出口（外部推送 / 站内持久流）；两组的补集不是"站内"，因此代码必须
    对未知通道**显式拒绝**，不能把"不在 OUTBOUND 里"当成 WebChat。
    """
    assert OUTBOUND_CHANNELS.isdisjoint(LOCAL_STREAM_CHANNELS)
    assert set(BUILTIN_CHANNEL_NAMES) == set(OUTBOUND_CHANNELS) | set(LOCAL_STREAM_CHANNELS)


def _upload_format_entries():
    return [(item.media_type, item.extensions) for item in SUPPORTED_UPLOAD_FORMATS]


def test_upload_format_list_is_the_single_source_for_the_gate() -> None:
    """准入集合必须从展示清单派生：新增格式只改一处，门控与投影同时生效。

    两边各自维护一份时，放宽某类型只会改到其中一边——用户遇到的是「能传不能读」或
    「页面说支持、上传却被拒」。
    """
    entries = _upload_format_entries()
    media_types = [media_type for media_type, _ in entries]
    assert len(media_types) == len(set(media_types)), "媒体类型不得重复"
    assert ALLOWED_ATTACHMENT_MEDIA_TYPES == set(media_types)
    for media_type, extensions in entries:
        assert extensions, f"{media_type} 缺少展示扩展名"
        for extension in extensions:
            assert extension.startswith(".") and extension == extension.lower(), (
                f"{media_type} 的扩展名必须是含点小写：{extension!r}"
            )


def test_archive_media_types_are_uploadable_and_classified() -> None:
    """压缩包是准入集合的一部分，并按压缩包分类；文本谓词沿用 `text/*` 口径。"""
    assert ARCHIVE_MEDIA_TYPES <= ALLOWED_ATTACHMENT_MEDIA_TYPES
    assert {"application/zip", "application/x-zip-compressed"} <= ARCHIVE_MEDIA_TYPES
    assert is_archive_media_type("application/zip")
    assert is_archive_media_type("  Application/ZIP ")
    assert not is_archive_media_type("application/pdf")
    assert is_text_media_type("text/markdown")
    assert is_text_media_type("TEXT/CSV")
    assert not is_text_media_type("application/zip")
