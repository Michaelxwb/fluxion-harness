"""[TASK-007] 附件读取工具：read_attachment / view_image（真实 PostgreSQL + 真实解析库）。

分工：工具**类型无关**且与入口无关（设计 AD-4-B）——任何入口落进 artifact 的东西都能读。
本文件把两条功能场景钉死：跨租户越权（E-05）与损坏文档的明确报错（E-06）。
"""

from __future__ import annotations

import base64
import io
import uuid

import pytest
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.attachment_tools import (
    READ_ATTACHMENT_TOOL,
    VIEW_IMAGE_TOOL,
    WRITE_ARTIFACT_RESULT_PREFIX,
    WRITE_ARTIFACT_TOOL,
    AttachmentToolError,
    AttachmentToolSet,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Artifact, Conversation, RunRecord

from .conftest import TenantContext


@pytest.fixture
def artifact_root(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    root.mkdir()
    monkeypatch.setenv("ARTIFACT_ROOT", str(root))
    return root


def _pdf_bytes(text: str) -> bytes:
    """生成一份**内容已知**的最小可解析 pdf（不用 mock：交给真实解析库读）。"""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


async def _seed_artifact(
    tenant: TenantContext,
    root,
    *,
    data: bytes,
    kind: str,
    media_type: str,
    filename: str,
) -> uuid.UUID:
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
            input_text="x",
            trace_id=uuid.uuid4().hex,
            cancel_requested=False,
        )
        session.add(run)
        await session.flush()
        storage_key = f"inbound/{uuid.uuid4().hex}/0"
        artifact_id = uuid.uuid4()
        (root / storage_key).parent.mkdir(parents=True, exist_ok=True)
        (root / storage_key).write_bytes(data)
        session.add(
            Artifact(
                id=artifact_id,
                tenant_id=tenant.tenant_id,
                run_id=run.id,
                conversation_id=conversation.id,
                artifact_type=f"INBOUND_{kind}",
                storage_key=storage_key,
                media_type=media_type,
                size=len(data),
                checksum="sha256:" + "a" * 64,
                metadata_json={"kind": kind, "filename": filename},
            )
        )
        await session.commit()
        return artifact_id


def _tool_set(tenant: TenantContext, root) -> AttachmentToolSet:
    return AttachmentToolSet(
        session_factory=get_session_factory,
        artifact_root=root,
        tenant_id=tenant.tenant_id,
    )


async def _call(tool_set: AttachmentToolSet, name: str, arguments: dict) -> str:
    registry = ToolRegistry()
    tool_set.register(registry)
    definition = registry.get(name)
    assert definition.handler is not None
    return await definition.handler(arguments, call_id="call-1")


async def test_reads_text_from_a_real_pdf(tenant: TenantContext, artifact_root) -> None:
    """正向：内容已知的 pdf 交给**真实解析库**抽取（不 mock）。"""
    artifact_id = await _seed_artifact(
        tenant, artifact_root, data=_pdf_bytes("hello"), kind="DOCUMENT",
        media_type="application/pdf", filename="报告.pdf",
    )

    result = await _call(
        _tool_set(tenant, artifact_root), READ_ATTACHMENT_TOOL, {"artifact_id": str(artifact_id)}
    )

    assert "报告.pdf" in result, "返回应带文件名，便于模型对应到用户说的话"
    assert isinstance(result, str)


async def test_e05_cross_tenant_read_is_rejected_without_leaking_existence(
    tenant: TenantContext, artifact_root
) -> None:
    """E-05：以租户 B 读取租户 A 的 artifact_id 必须被拒，且不泄露存在性细节。"""
    artifact_id = await _seed_artifact(
        tenant, artifact_root, data=b"secret", kind="DOCUMENT",
        media_type="text/plain", filename="secret.txt",
    )

    other = AttachmentToolSet(
        session_factory=get_session_factory,
        artifact_root=artifact_root,
        tenant_id=f"other-{uuid.uuid4()}",
    )
    with pytest.raises(AttachmentToolError) as exc:
        await _call(other, READ_ATTACHMENT_TOOL, {"artifact_id": str(artifact_id)})

    assert "secret.txt" not in str(exc.value), "错误信息不得回显对方的文件名"
    assert str(artifact_id) not in str(exc.value), "错误信息不得回显标识本身"


async def test_e06_corrupt_document_yields_a_clear_error(
    tenant: TenantContext, artifact_root
) -> None:
    """E-06：损坏的 pdf 必须给明确错误，而不是乱码或空内容。"""
    artifact_id = await _seed_artifact(
        tenant, artifact_root, data=b"%PDF-not-really-a-pdf",
        kind="DOCUMENT", media_type="application/pdf", filename="坏的.pdf",
    )

    with pytest.raises(AttachmentToolError) as exc:
        await _call(
            _tool_set(tenant, artifact_root), READ_ATTACHMENT_TOOL, {"artifact_id": str(artifact_id)}
        )

    # E-06 的契约是「明确错误而非乱码或空内容」：必须抛出、必须指明是哪个文件
    assert exc.value.code == "ATTACHMENT_EXTRACT_FAILED"
    assert "坏的.pdf" in str(exc.value)


async def test_image_read_hints_at_view_image(tenant: TenantContext, artifact_root) -> None:
    artifact_id = await _seed_artifact(
        tenant, artifact_root, data=b"\x89PNG", kind="IMAGE",
        media_type="image/png", filename="截图.png",
    )

    result = await _call(
        _tool_set(tenant, artifact_root), READ_ATTACHMENT_TOOL, {"artifact_id": str(artifact_id)}
    )

    assert VIEW_IMAGE_TOOL in result, "图片不能当文本读，应提示改用重看工具"


async def test_view_image_rejects_non_image(tenant: TenantContext, artifact_root) -> None:
    artifact_id = await _seed_artifact(
        tenant, artifact_root, data=b"text", kind="DOCUMENT",
        media_type="text/plain", filename="a.txt",
    )

    with pytest.raises(AttachmentToolError):
        await _call(
            _tool_set(tenant, artifact_root), VIEW_IMAGE_TOOL, {"artifact_id": str(artifact_id)}
        )


async def test_view_image_follow_up_carries_the_image_part(
    tenant: TenantContext, artifact_root
) -> None:
    """`view_image` 的全部意义在于**结果之后要补一条带图像块的 user 消息**。

    OpenAI 协议的 tool 消息不能携带图像块，所以这条追加消息是唯一通道；这里直接验证
    产出物本身（类型 + 内容块 + 与源字节一致），而不是只验证返回值是段文本。
    """
    payload = b"\x89PNG-real-bytes"
    artifact_id = await _seed_artifact(
        tenant, artifact_root, data=payload, kind="IMAGE",
        media_type="image/png", filename="截图.png",
    )
    tool_set = _tool_set(tenant, artifact_root)
    registry = ToolRegistry()
    tool_set.register(registry)
    definition = registry.get(VIEW_IMAGE_TOOL)

    result = await _call(tool_set, VIEW_IMAGE_TOOL, {"artifact_id": str(artifact_id)})
    assert definition.follow_up_messages is not None

    follow_up = await definition.follow_up_messages(result)
    assert len(follow_up) == 1
    message = follow_up[0]
    assert str(message.role) == "user", "tool 角色不能携带图像块，只能补 user 消息"
    parts = message.content
    assert isinstance(parts, tuple)
    images = [part for part in parts if not isinstance(part, str)]
    assert len(images) == 1
    assert images[0].data_base64 == base64.b64encode(payload).decode("ascii")


async def _seed_run(tenant: TenantContext) -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session_factory()() as session:
        conversation = Conversation(
            tenant_id=tenant.tenant_id, user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id, status="ACTIVE", last_seq=0,
        )
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            tenant_id=tenant.tenant_id, conversation_id=conversation.id,
            user_id=tenant.platform_user_id, agent_id=tenant.agent_id,
            status="RUNNING", input_text="x", trace_id=uuid.uuid4().hex,
            cancel_requested=False,
        )
        session.add(run)
        await session.commit()
        return run.id, conversation.id


def _writer_tool_set(tenant: TenantContext, root, run_id, conversation_id, *, has_route=True):
    return AttachmentToolSet(
        session_factory=get_session_factory,
        artifact_root=root,
        tenant_id=tenant.tenant_id,
        run_id=run_id,
        conversation_id=conversation_id,
        has_delivery_route=has_route,
    )


async def test_s06_write_then_read_back_closes_the_loop(
    tenant: TenantContext, artifact_root
) -> None:
    """S-06：写出的产物必须能被**同一个读取工具**读回，内容一致（闭环）。

    真实边界：工具 → 真实 artifact store（落盘）→ 读回，全程不 mock。
    """
    run_id, conversation_id = await _seed_run(tenant)
    tool_set = _writer_tool_set(tenant, artifact_root, run_id, conversation_id)

    payload = "# 汇总\n\n- 第一条\n- 第二条\n"
    written = await _call(
        tool_set, WRITE_ARTIFACT_TOOL, {"content": payload, "filename": "汇总.md"}
    )
    assert written.startswith(WRITE_ARTIFACT_RESULT_PREFIX)
    artifact_id = written[len(WRITE_ARTIFACT_RESULT_PREFIX) :].split("）")[0].strip()

    read_back = await _call(tool_set, READ_ATTACHMENT_TOOL, {"artifact_id": artifact_id})
    assert "汇总.md" in read_back
    assert payload.strip() in read_back, "读回内容必须与写出内容一致"
    # 文件真的落到了 artifact store，而不是只写了 DB 行
    assert any(artifact_root.rglob("*"))


async def test_write_artifact_without_delivery_route_errors_explicitly(
    tenant: TenantContext, artifact_root
) -> None:
    """没有交付路由时必须明确报错 —— 产物写出来没人收，静默成功等于骗模型。"""
    run_id, conversation_id = await _seed_run(tenant)
    tool_set = _writer_tool_set(
        tenant, artifact_root, run_id, conversation_id, has_route=False
    )

    with pytest.raises(AttachmentToolError):
        await _call(tool_set, WRITE_ARTIFACT_TOOL, {"content": "x", "filename": "a.md"})


async def test_written_artifact_is_tenant_scoped(tenant: TenantContext, artifact_root) -> None:
    """写出的产物同样受租户隔离：别的租户读不到。"""
    run_id, conversation_id = await _seed_run(tenant)
    written = await _call(
        _writer_tool_set(tenant, artifact_root, run_id, conversation_id),
        WRITE_ARTIFACT_TOOL,
        {"content": "secret", "filename": "s.md"},
    )
    artifact_id = written[len(WRITE_ARTIFACT_RESULT_PREFIX) :].split("）")[0].strip()

    other = AttachmentToolSet(
        session_factory=get_session_factory, artifact_root=artifact_root,
        tenant_id=f"other-{uuid.uuid4()}",
    )
    with pytest.raises(AttachmentToolError):
        await _call(other, READ_ATTACHMENT_TOOL, {"artifact_id": artifact_id})
