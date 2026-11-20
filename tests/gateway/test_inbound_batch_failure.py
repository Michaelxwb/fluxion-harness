"""批次失败语义：多附件不完整不建 Run，并给用户整批重发提示。"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from muad_contracts import AttachmentFailure, AttachmentRef, ChannelEnvelope

from tests.gateway.test_inbound_attachment_flow import (
    BOT_ID,
    _NullDedupe,
    _build_pipeline,
)

pytestmark = pytest.mark.integration


def _ref(name: str = "a.pdf") -> AttachmentRef:
    return AttachmentRef(
        storage_key=f"inbound/{name}",
        kind="DOCUMENT",
        media_type="application/pdf",
        size=1,
        filename=name,
        checksum="sha256:" + "a" * 64,
        source_channel="WECOM",
    )


def _envelope(
    *,
    text: str = "解析附件",
    attachments: tuple[AttachmentRef, ...] = (),
    failures: tuple[AttachmentFailure, ...] = (),
    require_complete: bool = True,
) -> ChannelEnvelope:
    return ChannelEnvelope(
        channel="WECOM",
        bot_id=BOT_ID,
        external_user_id="ext-s07",
        external_conversation_id="conv-s07",
        message_id=f"batch-{uuid4().hex}",
        text=text,
        attachments=list(attachments),
        attachment_failures=list(failures),
        require_complete_attachments=require_complete,
    )


async def _run(tmp_path: Path, envelope: ChannelEnvelope):
    pipeline, console, runtime = _build_pipeline(tmp_path, _NullDedupe())
    adapter = _adapter()
    await pipeline.handle(adapter, envelope)
    return adapter, runtime, console


def _adapter():
    from muad_im_gateway.channels.fake import FakeChannelAdapter

    return FakeChannelAdapter()


async def test_incomplete_batch_does_not_create_run(tmp_path: Path) -> None:
    envelope = _envelope(
        attachments=(_ref(),),
        failures=(AttachmentFailure(key="f1#0", code="ATTACHMENT_FETCH_FAILED"),),
    )
    adapter, runtime, console = await _run(tmp_path, envelope)

    assert runtime.run_requests == []
    texts = tuple(message.text for _route, message in adapter.sent)
    assert any("本次附件未全部收到" in text for text in texts)
    assert any("ATTACHMENT_FETCH_FAILED" in text or "附件" in text for text in texts)
    assert console.audit_calls[-1].outcome == "FAILED"


async def test_count_limit_batch_guides_user_to_split(tmp_path: Path) -> None:
    envelope = _envelope(
        failures=(AttachmentFailure(key="f2#0", code="ATTACHMENT_COUNT_EXCEEDED"),),
    )
    adapter, runtime, _console = await _run(tmp_path, envelope)

    assert runtime.run_requests == []
    texts = tuple(message.text for _route, message in adapter.sent)
    assert any("一次最多接收 5 个附件" in text for text in texts)
    assert any("分批重新发送" in text for text in texts)


async def test_single_frame_partial_failure_keeps_existing_run_behavior(tmp_path: Path) -> None:
    envelope = _envelope(
        text="请只看成功的附件",
        attachments=(_ref("ok.pdf"),),
        failures=(AttachmentFailure(key="bad#0", code="ATTACHMENT_FETCH_FAILED"),),
        require_complete=False,
    )
    _adapter_obj, runtime, _console = await _run(tmp_path, envelope)

    assert len(runtime.run_requests) == 1
    request = runtime.run_requests[0]
    assert request.message.text == "请只看成功的附件"
    assert [ref.filename for ref in request.message.attachments] == ["ok.pdf"]
