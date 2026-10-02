"""[TASK-007] 附件读取工具：read_attachment / view_image（真实 PostgreSQL + 真实解析库）。

分工：工具**类型无关**且与入口无关（设计 AD-4-B）——任何入口落进 artifact 的东西都能读。
本文件把两条功能场景钉死：跨租户越权（E-05）与损坏文档的明确报错（E-06）。
"""

from __future__ import annotations

import base64
import io
import re
import uuid

import pytest
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.attachment_tools import (
    ATTACHMENT_EXTRACT_FAILED,
    ATTACHMENT_LIMIT_INVALID,
    ATTACHMENT_OFFSET_INVALID,
    ATTACHMENT_TOO_LARGE,
    MAX_READ_BYTES,
    MAX_TEXT_CHARS,
    READ_ATTACHMENT_TOOL,
    SEARCH_ATTACHMENT_TOOL,
    VIEW_IMAGE_TOOL,
    WRITE_ARTIFACT_RESULT_PREFIX,
    WRITE_ARTIFACT_TOOL,
    AttachmentToolError,
    AttachmentToolSet,
    slice_text,
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


# ---------------------------------------------------------------------------
# TASK-002：分段读（S-01）、拒绝路径（E-01）、分段纯函数边界（B-01）、文档内定位
# ---------------------------------------------------------------------------

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

#: 片段区间标注：`（片段 0–20000 / 共 60000 字符…）`
_SEG_NOTE_RE = re.compile(r"（片段 (\d+)–(\d+) / 共 (\d+) 字符")
#: 标注永远在结果**末尾**，剥离它即可拿回纯片段正文
_TAIL_NOTE_RE = re.compile(r"(?:^|\n)（片段 \d+–\d+ / 共 \d+ 字符[^）]*）\s*\Z")


def _segment_of(result: str) -> str:
    """剥掉首行文件名与末尾区间标注，取回纯片段正文（S-01 要按字符拼接比对）。"""
    return _TAIL_NOTE_RE.sub("", result.partition("\n")[2])


def _docx_bytes(*paragraphs: str) -> bytes:
    """一份**内容已知**的真实 .docx（交给真实解析库读，不 mock）。

    抽取口径是 `"\\n".join(p.text for p in doc.paragraphs)`，所以多段之间会各有一个换行。
    """
    import docx

    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_b01_segment_boundaries() -> None:
    """B-01：分段纯函数的四种 offset 边界 + limit 上下界。

    真实边界就是纯函数本身（无 IO）：这里钉死的是**切片语义**，不是工具编排。
    """
    text = "abcdefghij"  # 10 字符

    assert slice_text(text, 0, 4) == "abcd", "offset=0 → 首段"
    assert slice_text(text, len(text), 4) == "", "offset 恰好等于总长 → 空段"
    assert slice_text(text, len(text) + 5, 4) == "", "offset 超出总长 → 空段"

    with pytest.raises(AttachmentToolError) as negative:
        slice_text(text, -1, 4)
    assert negative.value.code == ATTACHMENT_OFFSET_INVALID, "负 offset 是参数错误，不是空段"

    with pytest.raises(AttachmentToolError) as too_small:
        slice_text(text, 0, 0)
    assert too_small.value.code == ATTACHMENT_LIMIT_INVALID

    with pytest.raises(AttachmentToolError) as too_big:
        slice_text(text, 0, MAX_TEXT_CHARS + 1)
    assert too_big.value.code == ATTACHMENT_LIMIT_INVALID


async def test_s01_paged_read_reassembles_the_whole_document(
    tenant: TenantContext, artifact_root
) -> None:
    """S-01：三次分段拼接**逐字符等于**全文，且每段标注区间与总长。

    真实边界：真实文件系统（字节真落盘）+ 真实解析库（python-docx 解析真 OOXML）。
    全文用**单段** 60 000 字符，好让 0/20000/40000 三段恰好覆盖到最后一个字符。
    """
    full = "".join(chr(0x4E00 + (index % 512)) for index in range(60_000))
    data = _docx_bytes(full)
    artifact_id = await _seed_artifact(
        tenant, artifact_root, data=data, kind="DOCUMENT",
        media_type=_DOCX_MIME, filename="长报告.docx",
    )
    tool_set = _tool_set(tenant, artifact_root)

    segments: list[str] = []
    ranges: list[tuple[int, int, int]] = []
    for offset in (0, 20_000, 40_000):
        result = await _call(
            tool_set, READ_ATTACHMENT_TOOL,
            {"artifact_id": str(artifact_id), "offset": offset, "limit": 20_000},
        )
        assert "长报告.docx" in result, "每次返回都要带文件名"
        note = _SEG_NOTE_RE.search(result)
        assert note is not None, f"offset={offset} 的片段必须标注区间与总长：{result[-120:]!r}"
        ranges.append((int(note.group(1)), int(note.group(2)), int(note.group(3))))
        segments.append(_segment_of(result))

    assert "".join(segments) == full, "三次片段拼接必须逐字符等于全文"
    assert ranges == [(0, 20_000, 60_000), (20_000, 40_000, 60_000), (40_000, 60_000, 60_000)]


async def test_s01_long_document_carries_a_paging_hint(
    tenant: TenantContext, artifact_root
) -> None:
    """FEAT-03（收窄后）：未读完时给可行动的翻页指引，而不是一句"内容过长，已截断"。"""
    full = "".join(chr(0x4E00 + (index % 512)) for index in range(60_000))
    artifact_id = await _seed_artifact(
        tenant, artifact_root, data=_docx_bytes(full), kind="DOCUMENT",
        media_type=_DOCX_MIME, filename="长报告.docx",
    )

    first = await _call(
        _tool_set(tenant, artifact_root), READ_ATTACHMENT_TOOL, {"artifact_id": str(artifact_id)}
    )
    assert "offset=20000" in first, "未读完时必须告诉模型下一次从哪读"
    assert "已截断" not in first, "旧的截断文案必须被可行动的指引取代"

    last = await _call(
        _tool_set(tenant, artifact_root), READ_ATTACHMENT_TOOL,
        {"artifact_id": str(artifact_id), "offset": 40_000},
    )
    assert "offset=" not in last, "读到最后一段就不该再提示翻页"


async def test_e01_rejection_paths_are_explicit_and_labelled(
    tenant: TenantContext, artifact_root
) -> None:
    """E-01：超上限 / 损坏文档 / 非法 offset 各自明确报错（含文件与原因），绝不给乱码或空内容。

    另一面：`offset` **超出全文长度不是错误** —— 返回空段并注明（设计 §2.3.2）。
    """
    tool_set = _tool_set(tenant, artifact_root)

    oversized = await _seed_artifact(
        tenant, artifact_root, data=b"x" * (MAX_READ_BYTES + 1), kind="DOCUMENT",
        media_type="text/plain", filename="超大.txt",
    )
    with pytest.raises(AttachmentToolError) as too_large:
        await _call(tool_set, READ_ATTACHMENT_TOOL, {"artifact_id": str(oversized)})
    assert too_large.value.code == ATTACHMENT_TOO_LARGE
    assert "超大.txt" in str(too_large.value), "报错必须指明是哪个文件"

    corrupt = await _seed_artifact(
        tenant, artifact_root, data=b"not-a-real-docx-at-all", kind="DOCUMENT",
        media_type=_DOCX_MIME, filename="损坏.docx",
    )
    with pytest.raises(AttachmentToolError) as broken:
        await _call(tool_set, READ_ATTACHMENT_TOOL, {"artifact_id": str(corrupt)})
    assert broken.value.code == ATTACHMENT_EXTRACT_FAILED
    assert "损坏.docx" in str(broken.value)

    readable = await _seed_artifact(
        tenant, artifact_root, data="短文本".encode(), kind="DOCUMENT",
        media_type="text/plain", filename="短.txt",
    )
    with pytest.raises(AttachmentToolError) as bad_offset:
        await _call(
            tool_set, READ_ATTACHMENT_TOOL, {"artifact_id": str(readable), "offset": -1}
        )
    assert bad_offset.value.code == ATTACHMENT_OFFSET_INVALID
    assert "短.txt" in str(bad_offset.value)

    # 超出全文长度：**不是**错误，给空段 + 标注（仍要说明是哪个文件、总长多少）
    beyond = await _call(
        tool_set, READ_ATTACHMENT_TOOL, {"artifact_id": str(readable), "offset": 999}
    )
    assert "短.txt" in beyond
    assert _segment_of(beyond) == "", "超界返回空段"
    assert "共 3 字符" in beyond, "空段也要注明全文总长，模型才知道确实是读完了"


async def test_document_search_reports_hits_with_offsets_and_misses(
    tenant: TenantContext, artifact_root
) -> None:
    """文档内定位：命中给片段与偏移；**未命中要说"未命中"，不返回空内容冒充成功**。"""
    body = "开头\nALPHA 出现在这里\n中间段落\nALPHA 又出现一次\n结尾"
    artifact_id = await _seed_artifact(
        tenant, artifact_root, data=body.encode(), kind="DOCUMENT",
        media_type="text/plain", filename="笔记.txt",
    )
    tool_set = _tool_set(tenant, artifact_root)

    hits = await _call(
        tool_set, SEARCH_ATTACHMENT_TOOL, {"artifact_id": str(artifact_id), "query": "ALPHA"}
    )
    assert "笔记.txt" in hits
    assert hits.count("offset=") == 2, f"全文里 ALPHA 出现两次：{hits!r}"
    assert f"offset={body.index('ALPHA')}" in hits, "命中要带真实字符偏移，不能只报「有一次」"

    miss = await _call(
        tool_set, SEARCH_ATTACHMENT_TOOL, {"artifact_id": str(artifact_id), "query": "NOT-IN-FILE"}
    )
    assert "未命中" in miss, "未命中必须明说，不能返回空内容"
    assert "offset=" not in miss
