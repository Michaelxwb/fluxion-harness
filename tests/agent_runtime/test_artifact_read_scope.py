"""Artifact read scope: Worker asks with execution scope; shared tools reject non-text reads."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from muad_agent_core.tools import AttachmentReadTools, ResolvedAttachment, ToolError
from muad_agent_core.tools.attachments import read_text_segment
from muad_agent_worker.infrastructure.task_artifact_client import TaskArtifactClient


async def test_worker_read_forwards_task_execution_scope() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": {
                    "storage_key": "tenant/a.txt",
                    "kind": "DOCUMENT",
                    "media_type": "text/plain",
                    "size": 3,
                    "filename": "a.txt",
                    "checksum": "sha256:" + "a" * 64,
                    "artifact_id": str(uuid.uuid4()),
                },
            },
        )

    execution_id = uuid.uuid4()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ref = await TaskArtifactClient("http://runtime", client).read(
            tenant_id="tenant-a",
            execution_id=execution_id,
            artifact_id=str(uuid.uuid4()),
        )

    assert seen["task_execution_id"] == str(execution_id)
    assert ref.storage_key == "tenant/a.txt"


async def test_shared_read_tools_reject_non_text_but_view_image_returns_follow_up() -> None:
    async def resolver(
        artifact_id: str, offset: int, limit: int, cursor: Any
    ) -> ResolvedAttachment:
        if artifact_id == "image":
            return ResolvedAttachment("image", "image/png", "x.png", b"png")
        return ResolvedAttachment("bin", "application/pdf", "x.pdf", b"%PDF")

    tools = AttachmentReadTools(resolver)

    with pytest.raises(ToolError) as non_text:
        await tools.read_attachment({"artifact_id": "bin"}, call=_call())
    assert non_text.value.code == "ATTACHMENT_TYPE_UNSUPPORTED"

    result = await tools.view_image({"artifact_id": "image"}, call=_call())
    assert len(result.follow_ups) == 1
    assert "重看附件" in str(result.follow_ups[0].content[0])
    assert "image" in str(result.follow_ups[0].content[1])


async def test_text_read_forwards_the_requested_window_without_loading_the_file() -> None:
    calls: list[tuple[str, int, int]] = []

    async def resolver(
        artifact_id: str, offset: int, limit: int, cursor: Any
    ) -> ResolvedAttachment:
        calls.append((artifact_id, offset, limit))
        return ResolvedAttachment(
            artifact_id, "text/plain", "a.txt", b"segment", total_chars=None
        )

    tools = AttachmentReadTools(resolver)
    result = await tools.read_attachment(
        {"artifact_id": "a", "offset": 5, "limit": 7}, call=_call()
    )

    assert calls == [("a", 5, 7)]
    assert "segment" in result.content


def test_bounded_segment_read_does_not_call_read_bytes(tmp_path, monkeypatch) -> None:
    path = tmp_path / "big.txt"
    path.write_text("a" * 1000 + "B" * 10 + "c" * 1000, encoding="utf-8")

    def fail_read_bytes(self):
        raise AssertionError("must not read the whole file")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

    segment, total, cursor = read_text_segment(path, 1000, 10)

    assert segment.decode() == "B" * 10
    assert total is None
    assert cursor is not None

    next_segment, next_total, next_cursor = read_text_segment(path, 0, 5, cursor)
    assert next_segment.decode() == "c" * 5
    assert next_total is None
    assert next_cursor is not None


def _call():
    from muad_agent_core.tools import ToolCall

    return ToolCall(call_id="c1", name="read_attachment")
