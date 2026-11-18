"""DeliveryMessage 的消息附件契约：多附件一条消息，text/attachments 至少一个非空。"""

from __future__ import annotations

import pytest
from muad_contracts import DeliveryMessage
from muad_contracts.channel import AttachmentRef
from pydantic import ValidationError


def _ref() -> AttachmentRef:
    return AttachmentRef(
        storage_key="tenant/a/file",
        kind="DOCUMENT",
        media_type="text/markdown",
        size=3,
        filename="report.md",
        checksum="sha256:" + "a" * 64,
        artifact_id="00000000-0000-0000-0000-000000000001",
    )


def test_message_accepts_text_and_multiple_attachments() -> None:
    message = DeliveryMessage(type="message", text="报告已生成", attachments=[_ref(), _ref()])
    assert message.type == "message"
    assert len(message.attachments) == 2


def test_message_accepts_attachments_without_text() -> None:
    message = DeliveryMessage(type="message", attachments=[_ref()])
    assert message.text == ""
    assert len(message.attachments) == 1


def test_message_requires_text_or_attachment() -> None:
    with pytest.raises(ValidationError):
        DeliveryMessage(type="message")


def test_terminal_keeps_status_and_can_carry_attachments() -> None:
    message = DeliveryMessage(type="terminal", status="COMPLETED", attachments=[_ref()])
    assert message.status == "COMPLETED"
    assert len(message.attachments) == 1
