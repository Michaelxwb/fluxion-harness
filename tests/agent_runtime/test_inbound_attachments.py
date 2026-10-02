"""[TASK-006] 入站附件的落库与上下文呈现（真实 PostgreSQL）。

分工：渠道侧已把字节写进共享 artifact store 并给出 `storage_key`（设计 AD-1-B），Runtime
只落 DB 行与在上下文里呈现。呈现口径（AD-3-B）：**只有当前消息**的图片内联成图像块，
历史轮次一律只留文本引用。
"""

from __future__ import annotations

import base64
import uuid

from muad_agent_core.model import ImagePart
from muad_agent_runtime.application.inbound_attachments import (
    INBOUND_DOCUMENT,
    INBOUND_IMAGE,
    PersistedAttachment,
    artifact_type_for,
    build_current_content,
    persist_inbound_attachments,
    render_attachment_reference,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Artifact, Conversation, RunRecord
from muad_contracts import AttachmentRef
from sqlalchemy import select

from .conftest import TenantContext


def _ref(
    kind: str = "IMAGE", media_type: str = "image/png", filename: str | None = "截图.png"
) -> AttachmentRef:
    return AttachmentRef(
        storage_key=f"inbound/{uuid.uuid4().hex}/0",
        kind=kind,  # type: ignore[arg-type]
        media_type=media_type,
        size=2048,
        filename=filename,
        checksum="sha256:" + "a" * 64,
        source_channel="WECOM",
    )


def _persisted(**overrides: object) -> PersistedAttachment:
    base: dict[str, object] = {
        "artifact_id": uuid.uuid4(),
        "kind": "DOCUMENT",
        "media_type": "application/pdf",
        "filename": "报告.pdf",
        "size": 4096,
        "storage_key": "inbound/x/0",
    }
    base.update(overrides)
    return PersistedAttachment(**base)  # type: ignore[arg-type]


async def _insert_run(tenant: TenantContext) -> uuid.UUID:
    async with get_session_factory()() as session:
        conversation = Conversation(
            tenant_id=tenant.tenant_id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status="ACTIVE",
            last_seq=0,
        )
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status="RUNNING",
            input_text="看看这张图",
            trace_id=uuid.uuid4().hex,
            cancel_requested=False,
        )
        session.add(run)
        await session.commit()
        return run.id


def test_artifact_type_mapping_covers_known_kinds() -> None:
    assert artifact_type_for("IMAGE") == INBOUND_IMAGE
    assert artifact_type_for("DOCUMENT") == INBOUND_DOCUMENT
    assert artifact_type_for("SOMETHING_NEW") == "INBOUND_OTHER"


async def test_persist_writes_artifact_rows_bound_to_the_run(tenant: TenantContext) -> None:
    """落库：run_id 非空（XOR 约束继续成立）、元信息入 jsonb、字节不由 Runtime 搬。"""
    run_id = await _insert_run(tenant)
    ref = _ref()

    async with get_session_factory()() as session:
        persisted = await persist_inbound_attachments(
            session,
            tenant_id=tenant.tenant_id,
            run_id=run_id,
            conversation_id=None,
            refs=[ref, _ref(kind="DOCUMENT", media_type="application/pdf", filename="报告.pdf")],
        )
        await session.commit()
        rows = (
            await session.execute(
                select(Artifact).where(Artifact.tenant_id == tenant.tenant_id).order_by(Artifact.create_time)
            )
        ).scalars().all()

    assert len(persisted) == 2
    assert len(rows) == 2
    first = rows[0]
    assert first.run_id == run_id
    assert first.task_id is None, "接 task 会让 ck_artifact_run_task_xor 失败"
    assert first.artifact_type == INBOUND_IMAGE
    assert first.storage_key == ref.storage_key, "Runtime 不重写渠道给的存储键（AD-1-B）"
    assert first.media_type == "image/png"
    assert first.size == 2048
    assert first.checksum == ref.checksum
    assert first.metadata_json["filename"] == "截图.png"
    assert first.metadata_json["source"] == "WECOM"
    assert rows[1].artifact_type == INBOUND_DOCUMENT


def test_reference_names_the_artifact_so_the_agent_can_fetch_it() -> None:
    """文本引用必须带可得标识——模型要"重看"或让工具读它，都得靠这个 id 寻址。"""
    item = _persisted()
    reference = render_attachment_reference([item])

    assert str(item.artifact_id) in reference
    assert "报告.pdf" in reference
    assert "application/pdf" in reference
    assert render_attachment_reference([]) == ""


def test_current_content_without_reader_keeps_a_reference_not_silence() -> None:
    """没有字节读取器时**不假装没有附件**：仍留引用，让模型知道用户发过东西。"""
    content = build_current_content("看看这个", attachments=[_persisted()])

    assert isinstance(content, str)
    assert "看看这个" in content
    assert "报告.pdf" in content


def test_current_content_inlines_only_images_from_the_current_message() -> None:
    image = _persisted(kind="IMAGE", media_type="image/png", filename="截图.png", storage_key="k/img")
    document = _persisted()
    content = build_current_content(
        "看看",
        attachments=[image, document],
        read_bytes=lambda key: b"\x89PNG-bytes" if key == "k/img" else b"",
    )

    assert isinstance(content, tuple)
    texts = [part for part in content if isinstance(part, str)]
    images = [part for part in content if isinstance(part, ImagePart)]
    assert len(images) == 1, "当前消息的图片应内联为图像块"
    assert images[0].media_type == "image/png"
    assert images[0].data_base64 == base64.b64encode(b"\x89PNG-bytes").decode("ascii")
    assert any("报告.pdf" in text for text in texts), "文档不内联，只留引用"


def test_current_content_falls_back_to_reference_when_bytes_are_unreadable() -> None:
    """读不到字节时退回引用，而不是让整轮因为一个坏文件失败。"""

    def _boom(key: str) -> bytes:
        raise OSError("missing")

    content = build_current_content("看看", attachments=[_persisted(kind="IMAGE")], read_bytes=_boom)

    assert isinstance(content, str)
    assert "报告.pdf" in content
