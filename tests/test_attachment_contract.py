"""[契约] 附件引用在渠道边界与消息契约上的同型承载。

**为什么需要**：附件必须挂在**渠道边界类型** `ChannelEnvelope` 上，而不是只挂 Runtime 侧契约 ——
否则渠道的媒体类型要穿过适配器边界渗透进核心域（`harness-im#RULE-im-001`），"换通道只写适配器"
就不成立。本文件钉死三件可序列化/可静态判定的事：

- B-05：零附件消息的既有字段序列化结果不变，且**不新增必填字段**（向后兼容）。
- B-07：通道枚举只在一处定义（`ChannelName`），其余位置引用别名（`Literal["WECOM"]` 字面量仅 1 处）。
- S-07：附件引用的结构里**不含任何取件凭据**（url / aes_key / media_id / download_code …）——
  取件方式渠道私有（设计 AD-8），边界只描述"已到手的字节"。
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

from muad_contracts import (
    AttachmentRef,
    ChannelEnvelope,
    ChannelName,
    MessageInput,
)
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
ATTACHMENT_REF_FIELDS = frozenset(
    {"storage_key", "kind", "media_type", "size", "filename", "checksum", "source_channel"}
)
CREDENTIAL_FIELDS = frozenset(
    {"url", "aes_key", "aeskey", "media_id", "download_code", "access_token", "file_key", "artifact_id"}
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


def test_b07_channel_enum_is_defined_in_exactly_one_place() -> None:
    """B-07：`Literal["WECOM"]` 字面量只允许出现在 ChannelName 定义处。"""
    scanned = sorted(CONTRACT_DIR.glob("*.py")) + [CONSOLE_DTO]
    assert scanned, "未扫描到任何契约源码：路径规则可能已与目录结构脱节"

    hits = {
        path.name: path.read_text(encoding="utf-8").count('Literal["WECOM"]')
        for path in scanned
    }
    offenders = {name: count for name, count in hits.items() if count}
    assert offenders == {"enums.py": 1}, (
        "通道枚举必须只在一处定义（ChannelName），其余位置引用别名；"
        f"实际字面量分布：{offenders}"
    )

    assert get_args(ChannelName) == ("WECOM",), "ChannelName 的取值集合必须是 ('WECOM',)"

    # 别名必须被真正用起来，而不是只定义不引用
    for name in ("channel.py", "runtime.py", "tasks.py", "resolve.py"):
        text = (CONTRACT_DIR / name).read_text(encoding="utf-8")
        assert "ChannelName" in text, f"{name} 未引用 ChannelName 别名"


async def test_s07_fake_adapter_carries_attachments_without_credentials() -> None:
    """S-07：渠道边界可承载附件引用，且该结构不含任何取件凭据。"""
    credentials_in_type = ATTACHMENT_REF_FIELDS & CREDENTIAL_FIELDS
    assert not credentials_in_type, f"附件引用不得持有取件凭据：{credentials_in_type}"
    assert set(AttachmentRef.model_fields) == set(ATTACHMENT_REF_FIELDS), (
        "AttachmentRef 字段集发生变化：新增字段须先确认它既不是取件凭据、也不是 DB 标识"
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
