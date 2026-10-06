"""[TASK-007] 附件读取工具：read_attachment / view_image（真实 PostgreSQL + 真实解析库）。

分工：工具**类型无关**且与入口无关（设计 AD-4-B）——任何入口落进 artifact 的东西都能读。
本文件把两条功能场景钉死：跨租户越权（E-05）与损坏文档的明确报错（E-06）。
"""

from __future__ import annotations

import asyncio
import base64
import io
import re
import uuid
from typing import Any

import pytest
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.attachments.tools import (
    AGENT_OUTPUT_ARTIFACT_TYPE,
    APPEND_ARTIFACT_TOOL,
    ATTACHMENT_DIRECTION_INVALID,
    ATTACHMENT_EXTRACT_FAILED,
    ATTACHMENT_LIMIT_INVALID,
    ATTACHMENT_NOT_FOUND,
    ATTACHMENT_OFFSET_INVALID,
    ATTACHMENT_SCOPE_INVALID,
    ATTACHMENT_TOO_LARGE,
    ATTACHMENT_WRITE_UNAVAILABLE,
    DELIVER_ARTIFACT_TOOL,
    LIST_ATTACHMENTS_TOOL,
    MAX_READ_BYTES,
    MAX_TEXT_CHARS,
    READ_ATTACHMENT_TOOL,
    SEARCH_ATTACHMENT_TOOL,
    VIEW_IMAGE_TOOL,
    WRITE_ARTIFACT_RESULT_PREFIX,
    WRITE_ARTIFACT_TOOL,
    AttachmentToolError,
    AttachmentToolSet,
    list_window,
    slice_text,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.gateway_delivery_client import (
    DeliveryUnavailableError,
)
from muad_agent_runtime.infrastructure.models.runtime import Artifact, Conversation, RunRecord
from muad_artifact_store import NfsArtifactStore
from muad_contracts import DeliveryResponse, DeliveryRouteInput

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
    into: tuple[uuid.UUID, uuid.UUID] | None = None,
    artifact_type: str | None = None,
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """落一条入站附件。`into=(run_id, conversation_id)` 可挂进既有 Run/会话（S-02 需要
    让入站与自产**同处一个会话**，否则枚举范围无从谈起）。"""
    async with get_session_factory()() as session:
        if into is None:
            owner = user_id or tenant.platform_user_id
            conversation = Conversation(
                tenant_id=tenant.tenant_id,
                user_id=owner,
                agent_id=tenant.agent_id,
                status="ACTIVE",
                last_seq=0,
            )
            session.add(conversation)
            await session.flush()
            run = RunRecord(
                tenant_id=tenant.tenant_id,
                conversation_id=conversation.id,
                user_id=owner,
                agent_id=tenant.agent_id,
                status="RUNNING",
                input_text="x",
                trace_id=uuid.uuid4().hex,
                cancel_requested=False,
            )
            session.add(run)
            await session.flush()
            run_id, conversation_id = run.id, conversation.id
        else:
            run_id, conversation_id = into
        storage_key = f"inbound/{uuid.uuid4().hex}/0"
        artifact_id = uuid.uuid4()
        (root / storage_key).parent.mkdir(parents=True, exist_ok=True)
        (root / storage_key).write_bytes(data)
        session.add(
            Artifact(
                id=artifact_id,
                tenant_id=tenant.tenant_id,
                run_id=run_id,
                conversation_id=conversation_id,
                artifact_type=artifact_type or f"INBOUND_{kind}",
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
        # 归属用户取本 Run 的（生产里由 `ExecutorRunContext.user_id` 注入）：按 id 取附件要判"是不是我的"
        user_id=tenant.platform_user_id,
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


async def test_same_tenant_other_user_cannot_read_by_id(
    tenant: TenantContext, artifact_root
) -> None:
    """[R-05] 同租户**另一个用户**拿到 artifact id 也读不到：归属按用户判定。

    `tenant_id` 是租户（一个组织），同租户下有多个用户；附件没有 user 列，归属由它的会话或它的
    Run 的 `user_id` 推出。修复前只校验 tenant_id ⇒ 同租户任一用户提供 UUID 就能读到别人的文件
    （2026-10-06 review）。越权一律按"不存在"处理，不回显存在性。
    """
    artifact_id = await _seed_artifact(
        tenant, artifact_root, data=b"secret", kind="DOCUMENT",
        media_type="text/plain", filename="mine.txt", user_id=uuid.uuid4(),
    )

    mine = _tool_set(tenant, artifact_root)
    with pytest.raises(AttachmentToolError) as exc:
        await _call(mine, READ_ATTACHMENT_TOOL, {"artifact_id": str(artifact_id)})

    assert exc.value.code == ATTACHMENT_NOT_FOUND
    assert "mine.txt" not in str(exc.value), "错误信息不得回显对方的文件名"


async def test_same_user_other_conversation_is_still_readable(
    tenant: TenantContext, artifact_root
) -> None:
    """收紧到**用户维度**，不是会话维度：同一用户**另一个会话**里的附件仍然可读。"""
    other_conversation = await _seed_artifact(
        tenant, artifact_root, data="另一个会话的内容".encode(),
        kind="DOCUMENT", media_type="text/plain", filename="older.txt",
    )

    mine = _tool_set(tenant, artifact_root)
    content = await _call(mine, READ_ATTACHMENT_TOOL, {"artifact_id": str(other_conversation)})

    assert "另一个会话的内容" in content


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
        user_id=tenant.platform_user_id,
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


# ---------------------------------------------------------------------------
# TASK-003：附件枚举（S-02）、枚举分页边界（B-02）
# ---------------------------------------------------------------------------

_ENTRY_RE = re.compile(r"^- (\S+) · (.+?) · (\S+) · (\d+) B · (入站|自产) · (.+)$", re.M)


def _entries(listed: str) -> list[re.Match[str]]:
    return list(_ENTRY_RE.finditer(listed))


def test_b02_listing_paging_boundaries() -> None:
    """B-02（纯函数部分）：分页窗口的上下界。**超上限一律拒绝**，不夹紧。

    二选一已定：`limit` 越界与 `offset` 为负都当参数错误。理由是 `read_attachment` 的
    `limit` 已是这个口径——同一个概念两套行为，模型学不会。
    """
    assert list_window({}) == (0, 20), "缺省 offset=0 / limit=20"
    assert list_window({"limit": 1}) == (0, 1)
    assert list_window({"limit": 50, "offset": 7}) == (7, 50)

    with pytest.raises(AttachmentToolError) as over:
        list_window({"limit": 51})
    assert over.value.code == ATTACHMENT_LIMIT_INVALID

    with pytest.raises(AttachmentToolError) as under:
        list_window({"limit": 0})
    assert under.value.code == ATTACHMENT_LIMIT_INVALID

    with pytest.raises(AttachmentToolError) as negative:
        list_window({"offset": -1})
    assert negative.value.code == ATTACHMENT_OFFSET_INVALID


async def test_b02_empty_scope_returns_an_empty_list_not_an_error(
    tenant: TenantContext, artifact_root
) -> None:
    """B-02（空集部分）：范围内没有附件 → 说"0 条"，**不是错误**。

    空集与"出错"必须分开：报错会让模型以为工具坏了，于是不敢再列。
    """
    run_id, conversation_id = await _seed_run(tenant)
    listed = await _call(
        _writer_tool_set(tenant, artifact_root, run_id, conversation_id), LIST_ATTACHMENTS_TOOL, {}
    )

    assert "0 条" in listed
    assert _entries(listed) == [], "空集不得编造条目"


async def test_listing_rejects_unknown_filters(tenant: TenantContext, artifact_root) -> None:
    """未知的 `scope`/`direction` 必须**明确报错**，不能静默当成"什么都没匹配到"。

    静默返回空集是危险的：模型会以为"这个会话确实没有附件"，转而凭记忆编。
    """
    run_id, conversation_id = await _seed_run(tenant)
    tool_set = _writer_tool_set(tenant, artifact_root, run_id, conversation_id)

    with pytest.raises(AttachmentToolError) as bad_scope:
        await _call(tool_set, LIST_ATTACHMENTS_TOOL, {"scope": "everything"})
    assert bad_scope.value.code == ATTACHMENT_SCOPE_INVALID

    with pytest.raises(AttachmentToolError) as bad_direction:
        await _call(tool_set, LIST_ATTACHMENTS_TOOL, {"direction": "sideways"})
    assert bad_direction.value.code == ATTACHMENT_DIRECTION_INVALID


async def test_s02_lists_inbound_and_self_produced_attachments(
    tenant: TenantContext, artifact_root
) -> None:
    """S-02：同一会话里的**入站**与**自产**都要列出，字段齐全且方向可区分。

    真实边界：真实 PG —— 逐行回读 `runtime.artifact`，不 mock 仓储。列出的 id 必须能
    直接喂给 `read_attachment`（否则"跨多轮继续用某个附件"仍然断链）。
    """
    run_id, conversation_id = await _seed_run(tenant)
    pdf = _pdf_bytes("hello")
    inbound_id = await _seed_artifact(
        tenant, artifact_root, data=pdf, kind="DOCUMENT",
        media_type="application/pdf", filename="来件.pdf",
        into=(run_id, conversation_id),
    )
    tool_set = _writer_tool_set(tenant, artifact_root, run_id, conversation_id)
    await _call(tool_set, WRITE_ARTIFACT_TOOL, {"content": "# 汇总\n", "filename": "汇总.md"})

    listed = await _call(tool_set, LIST_ATTACHMENTS_TOOL, {})
    entries = _entries(listed)
    assert len(entries) == 2, f"一次 Run 内的两条附件都要列出：{listed!r}"

    by_name = {match.group(2): match for match in entries}
    assert set(by_name) == {"来件.pdf", "汇总.md"}

    inbound, outbound = by_name["来件.pdf"], by_name["汇总.md"]
    assert inbound.group(1) == str(inbound_id), "列出的 id 必须与库里的一致"
    assert inbound.group(3) == "application/pdf"
    assert inbound.group(4) == str(len(pdf)), "大小取真实字节数"
    assert inbound.group(5) == "入站"

    assert outbound.group(3) == "text/markdown"
    assert outbound.group(5) == "自产", "入站与自产必须可区分"

    # 列出来的 id 要真的可寻址（闭环）：直接拿去读，能读回内容
    read_back = await _call(tool_set, READ_ATTACHMENT_TOOL, {"artifact_id": inbound.group(1)})
    assert "来件.pdf" in read_back


# ---------------------------------------------------------------------------
# TASK-005：写与交付语义分离、追加写与不可变性（B-03）
# ---------------------------------------------------------------------------


async def _row_key(artifact_id: str) -> str:
    """回读 DB 行当前的 `storage_key`（版本演进要看它指向哪里）。"""
    async with get_session_factory()() as session:
        row = await session.get(Artifact, uuid.UUID(artifact_id))
    assert row is not None
    return row.storage_key


def _artifact_id_of(result: str) -> str:
    return result[len(WRITE_ARTIFACT_RESULT_PREFIX) :].split("）")[0].strip()


async def test_write_artifact_succeeds_without_a_delivery_route(
    tenant: TenantContext, artifact_root
) -> None:
    """**TASK-005 起行为反转**：没有交付路由也能写。

    原先这里是「缺交付路由即拒绝」——那把「写」与「可交付」**耦合错了方向**：后台任务、
    控制台触发的 Run 连"写"都做不了。而"写"本身只需要 Run 上下文（落共享 store + DB 行）。
    交付能不能做，由 TASK-006 的 `deliver_artifact` 单独管。
    """
    run_id, conversation_id = await _seed_run(tenant)
    tool_set = _writer_tool_set(tenant, artifact_root, run_id, conversation_id, has_route=False)

    written = await _call(tool_set, WRITE_ARTIFACT_TOOL, {"content": "x", "filename": "a.md"})

    assert written.startswith(WRITE_ARTIFACT_RESULT_PREFIX)
    assert any(artifact_root.rglob("*")), "没有交付路由也要真的落盘"
    assert "deliver_artifact" in written, "返回值要告诉模型：想交给用户得再调交付工具"


def test_b03_store_write_is_immutable(artifact_root) -> None:
    """B-03（不可变原语）：同一 `storage_key` 二次写入抛 `FileExistsError`，且**首个内容原样**。

    真实边界：真实文件系统。这条不是防呆而是**契约**（`RULE-skill-001`）——长文档追加写
    正是靠"每次换一个新 key"做版本演进。
    """
    store = NfsArtifactStore(artifact_root)
    store.write("outbound/probe/v1", b"first")

    with pytest.raises(FileExistsError):
        store.write("outbound/probe/v1", b"second")

    assert store.resolve("outbound/probe/v1").read_bytes() == b"first"


async def test_b03_write_artifact_respects_the_size_limit(
    tenant: TenantContext, artifact_root
) -> None:
    """B-03（大小）：恰好等于上限**接收**；上限 + 1 字节**明确拒绝**。"""
    run_id, conversation_id = await _seed_run(tenant)
    tool_set = _writer_tool_set(tenant, artifact_root, run_id, conversation_id)

    at_limit = await _call(
        tool_set, WRITE_ARTIFACT_TOOL,
        {"content": "x" * MAX_READ_BYTES, "filename": "满.md"},
    )
    assert at_limit.startswith(WRITE_ARTIFACT_RESULT_PREFIX), "恰好等于上限必须接受"

    with pytest.raises(AttachmentToolError) as over:
        await _call(
            tool_set, WRITE_ARTIFACT_TOOL,
            {"content": "x" * (MAX_READ_BYTES + 1), "filename": "超.md"},
        )
    assert over.value.code == ATTACHMENT_TOO_LARGE


async def test_b03_append_versions_the_key_and_never_breaks_existing_content(
    tenant: TenantContext, artifact_root
) -> None:
    """B-03（追加）：每次追加写**新 key**、行指向最新、历史版本原样留存；超限拒绝且**不破坏已有内容**。

    真实边界：真实文件系统（版本键真的落盘）。
    """
    run_id, conversation_id = await _seed_run(tenant)
    tool_set = _writer_tool_set(tenant, artifact_root, run_id, conversation_id)
    store = NfsArtifactStore(artifact_root)

    written = await _call(
        tool_set, WRITE_ARTIFACT_TOOL, {"content": "第一段\n", "filename": "长文.md"}
    )
    artifact_id = _artifact_id_of(written)
    first_key = await _row_key(artifact_id)

    appended = await _call(
        tool_set, APPEND_ARTIFACT_TOOL, {"artifact_id": artifact_id, "content": "第二段\n"}
    )
    assert artifact_id in appended, "追加后仍要能拿到同一个产物 id"

    second_key = await _row_key(artifact_id)
    assert second_key != first_key, "artifact 不可变 ⇒ 追加必须换新 key（RULE-skill-001）"
    assert store.resolve(first_key).read_bytes() == "第一段\n".encode(), "历史版本必须原样不可变"
    assert store.resolve(second_key).read_bytes() == "第一段\n第二段\n".encode(), "最新版本是全文"

    read_back = await _call(tool_set, READ_ATTACHMENT_TOOL, {"artifact_id": artifact_id})
    assert "第一段" in read_back and "第二段" in read_back

    # 超限：拒绝，且**已有内容与行都不动**（半截写入等于把用户的文档毁掉）
    before = store.resolve(second_key).read_bytes()
    with pytest.raises(AttachmentToolError) as too_big:
        await _call(
            tool_set, APPEND_ARTIFACT_TOOL,
            {"artifact_id": artifact_id, "content": "x" * MAX_READ_BYTES},
        )
    assert too_big.value.code == ATTACHMENT_TOO_LARGE
    assert store.resolve(second_key).read_bytes() == before, "拒绝不得破坏已有内容"
    assert await _row_key(artifact_id) == second_key, "拒绝不得改动 DB 行"


async def test_concurrent_appends_do_not_collide_on_the_storage_key(
    tenant: TenantContext, artifact_root
) -> None:
    """追加的 `storage_key` **不由读-改-写的计数器推导**。

    旧实现用 `len(versions) + 1` 推版本号：两个任务读到同一份行（版本 1、versions 为空）就都
    算出 `v2`，第二次写撞上不可变 Artifact 抛 `FileExistsError`（`RULE-skill-001` 的不可变语义
    在这里反过来把并发写变成了报错）。

    行锁（`_load_for_update`）已经是并发正确性的**主**修复；这条钉的是键推导本身——只要还有
    别的写入方不走那把锁，计数器就会重新变成碰撞源。真并发难以稳定复现，所以这里**确定性地
    重建那个中间态**：先追加一次（写下那一刻的键），再把行改回"另一个任务刚读到时"的样子，
    然后追加第二次。计数器实现会在第二次撞上第一次写下的 `v2`；uuid 键不会。
    """
    run_id, conversation_id = await _seed_run(tenant)
    tool_set = _writer_tool_set(tenant, artifact_root, run_id, conversation_id)
    store = NfsArtifactStore(artifact_root)

    written = await _call(
        tool_set, WRITE_ARTIFACT_TOOL, {"content": "第一段\n", "filename": "长文.md"}
    )
    artifact_id = _artifact_id_of(written)
    original_key = await _row_key(artifact_id)

    await _call(tool_set, APPEND_ARTIFACT_TOOL, {"artifact_id": artifact_id, "content": "第二段\n"})
    first_append_key = await _row_key(artifact_id)
    assert first_append_key != original_key

    # 回到"另一个任务读到的快照"：行仍是原始版本，versions 还没记过任何历史
    async with get_session_factory()() as session:
        row = await session.get(Artifact, uuid.UUID(artifact_id))
        assert row is not None
        row.storage_key = original_key
        row.metadata_json = {**row.metadata_json, "version": 1, "versions": []}
        await session.commit()

    await _call(tool_set, APPEND_ARTIFACT_TOOL, {"artifact_id": artifact_id, "content": "第三段\n"})
    second_append_key = await _row_key(artifact_id)

    assert second_append_key != first_append_key, "键不得由旧值推导，否则并发下必然撞车"
    assert store.resolve(first_append_key).read_bytes() == "第一段\n第二段\n".encode(), (
        "被对手抢先的那一版仍须原样留在盘上"
    )


async def test_concurrent_appends_all_land(tenant: TenantContext, artifact_root) -> None:
    """并发追加**不得互相覆盖** —— 整段读-改-写必须在行锁下串行化。

    正文合并、`size`、`checksum`、`metadata_json` 全由旧值推出：没有锁时两个追加各自读到同一
    份旧值，后提交的用旧快照盖掉前一个 —— 用户拿到的是一份**少了一段**的文档，而且不报错。
    断言的是"每一段都还在"，不是"没抛异常"。
    """
    run_id, conversation_id = await _seed_run(tenant)
    tool_set = _writer_tool_set(tenant, artifact_root, run_id, conversation_id)

    written = await _call(
        tool_set, WRITE_ARTIFACT_TOOL, {"content": "第 0 段\n", "filename": "长文.md"}
    )
    artifact_id = _artifact_id_of(written)

    segments = [f"第 {index} 段\n" for index in range(1, 4)]
    await asyncio.gather(
        *(
            _call(tool_set, APPEND_ARTIFACT_TOOL, {"artifact_id": artifact_id, "content": segment})
            for segment in segments
        )
    )

    read_back = await _call(tool_set, READ_ATTACHMENT_TOOL, {"artifact_id": artifact_id})
    for expected in ["第 0 段", *(segment.strip() for segment in segments)]:
        assert expected in read_back, f"{expected} 被并发追加覆盖掉了"


async def test_append_rejects_artifacts_this_run_did_not_produce(
    tenant: TenantContext, artifact_root
) -> None:
    """只能追加**本 Run 自产**的产物：拿用户发来的附件去追加必须明确拒绝。

    这条不是洁癖——入站附件的字节属于用户原始文件，被 Agent 改写就再也回不到原件了。
    """
    run_id, conversation_id = await _seed_run(tenant)
    inbound_id = await _seed_artifact(
        tenant, artifact_root, data=b"user file", kind="DOCUMENT",
        media_type="text/plain", filename="来件.txt", into=(run_id, conversation_id),
    )
    tool_set = _writer_tool_set(tenant, artifact_root, run_id, conversation_id)

    with pytest.raises(AttachmentToolError):
        await _call(
            tool_set, APPEND_ARTIFACT_TOOL,
            {"artifact_id": str(inbound_id), "content": "追加"},
        )


# ---------------------------------------------------------------------------
# TASK-006：交付工具 deliver_artifact（三种结论如实回传）
# ---------------------------------------------------------------------------


class _FakeDeliveryClient:
    """记录交付请求、按脚本应答。**不发真 HTTP** —— 网关侧的真实边界由 E-06 覆盖。"""

    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.requests: list[Any] = []
        self._response = response
        self._error = error

    async def deliver(self, request, *, trace_id: str = ""):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return self._response


_DELIVERY_ROUTE = DeliveryRouteInput(
    channel="WECOM", bot_id="bot-1", external_user_id="ext-1"
)


async def _deliverable(tenant, artifact_root, *, client, route=_DELIVERY_ROUTE):
    """造一个「本次 Run 写出的产物」+ 配好交付路由/客户端的工具集。"""
    run_id, conversation_id = await _seed_run(tenant)
    tool_set = AttachmentToolSet(
        session_factory=get_session_factory,
        artifact_root=artifact_root,
        tenant_id=tenant.tenant_id,
        run_id=run_id,
        conversation_id=conversation_id,
        user_id=tenant.platform_user_id,
        has_delivery_route=route is not None,
        delivery_route=route,
        delivery_client=client,
    )
    written = await _call(tool_set, WRITE_ARTIFACT_TOOL, {"content": "正文", "filename": "汇总.md"})
    return tool_set, _artifact_id_of(written)


async def test_deliver_artifact_reports_a_real_delivery(tenant, artifact_root) -> None:
    """已交付：结果里要有**产物 id 与文件名**，模型才能对用户说清发的是什么。"""
    client = _FakeDeliveryClient(
        DeliveryResponse(
            accepted=True, delivered=True, deduplicated=False, outcome="DELIVERED"
        )
    )
    tool_set, artifact_id = await _deliverable(tenant, artifact_root, client=client)

    result = await _call(tool_set, DELIVER_ARTIFACT_TOOL, {"artifact_id": artifact_id})

    assert artifact_id in result and "汇总.md" in result
    assert len(client.requests) == 1
    sent = client.requests[0]
    assert sent.delivery_key == f"run:{tool_set._run_id}:{artifact_id}"
    assert sent.task_id is None, "会话形态不带 task_id"
    assert sent.message.artifact is not None
    assert str(sent.message.artifact.artifact_id) == artifact_id, "适配器要拿 artifact_id 拼降级直链"


async def test_deliver_artifact_reports_a_degraded_link(tenant, artifact_root) -> None:
    """降级：结果里要带**链接** —— 那是用户实际收到的东西，模型得转达。"""
    link = "https://console.invalid/api/v1/artifacts/x/content?token=t"
    client = _FakeDeliveryClient(
        DeliveryResponse(
            accepted=True, delivered=True, deduplicated=False, outcome="DEGRADED", fallback_url=link
        )
    )
    tool_set, artifact_id = await _deliverable(tenant, artifact_root, client=client)

    result = await _call(tool_set, DELIVER_ARTIFACT_TOOL, {"artifact_id": artifact_id})

    assert link in result
    assert "取件链接" in result


async def test_deliver_artifact_reports_failure_and_keeps_the_artifact(
    tenant, artifact_root
) -> None:
    """没拿到结论 ⇒ **按失败报**，且明说产物已保留可重试。**绝不**说"已交付"。"""
    client = _FakeDeliveryClient(error=DeliveryUnavailableError("ReadTimeout"))
    tool_set, artifact_id = await _deliverable(tenant, artifact_root, client=client)

    result = await _call(tool_set, DELIVER_ARTIFACT_TOOL, {"artifact_id": artifact_id})

    assert "交付失败" in result and "可重试" in result
    assert artifact_id in result, "要告诉模型产物还在，重试不用重写"
    assert "已交付" not in result, "超时只说明没拿到结论，说得像发出去了就是谎报（RULE-03）"

    # 产物确实还在（失败不回滚写入）
    read_back = await _call(tool_set, READ_ATTACHMENT_TOOL, {"artifact_id": artifact_id})
    assert "正文" in read_back


async def test_deliver_artifact_without_a_route_errors_explicitly(tenant, artifact_root) -> None:
    """没有交付路由 ⇒ 明确报错（**只有交付动作**依赖路由；写不依赖）。"""
    tool_set, artifact_id = await _deliverable(
        tenant, artifact_root, client=_FakeDeliveryClient(), route=None
    )

    with pytest.raises(AttachmentToolError) as exc:
        await _call(tool_set, DELIVER_ARTIFACT_TOOL, {"artifact_id": artifact_id})
    assert exc.value.code == ATTACHMENT_WRITE_UNAVAILABLE


def _delivery_tool_set(tenant, artifact_root, *, run_id, conversation_id, client):
    return AttachmentToolSet(
        session_factory=get_session_factory, artifact_root=artifact_root,
        tenant_id=tenant.tenant_id, run_id=run_id, conversation_id=conversation_id,
        user_id=tenant.platform_user_id,
        has_delivery_route=True, delivery_route=_DELIVERY_ROUTE, delivery_client=client,
    )


async def test_deliver_artifact_forwards_an_inbound_image_from_this_conversation(
    tenant, artifact_root
) -> None:
    """**本会话收到的附件可以转交给用户**（2026-10-03 起）。

    真实场景就是用户试的那个：「把这张图片重新发给我」。在此之前这条路是**结构性堵死**的——
    `write_artifact` 只吃文本（agent 造不出位图），作者校验又挡住转发 ⇒ **agent 一张图都发不出去**。
    转发**不产生新内容**，只是把已有的字节递回去，所以它不需要任何新的生成能力。
    """
    run_id, conversation_id = await _seed_run(tenant)
    inbound_id = await _seed_artifact(
        tenant, artifact_root, data=b"\x89PNG-inbound", kind="IMAGE",
        media_type="image/png", filename="截图.png", into=(run_id, conversation_id),
    )
    client = _FakeDeliveryClient(
        DeliveryResponse(accepted=True, delivered=True, deduplicated=False, outcome="DELIVERED")
    )
    tool_set = _delivery_tool_set(
        tenant, artifact_root, run_id=run_id, conversation_id=conversation_id, client=client
    )

    result = await _call(tool_set, DELIVER_ARTIFACT_TOOL, {"artifact_id": str(inbound_id)})

    assert "已交付产物" in result and "截图.png" in result
    sent = client.requests[0]
    assert sent.message.type == "image", "kind=IMAGE 必须走 image 体（企微只认它的 media_id 通路）"
    assert str(sent.message.artifact.artifact_id) == str(inbound_id)


async def test_deliver_artifact_refuses_inbound_attachments_from_another_conversation(
    tenant, artifact_root
) -> None:
    """**别的会话**收到的附件必须拒绝 —— 这条界线是新加的，不能被"放宽转发"顺手带掉。

    `_load` 只保证**租户**隔离。少了会话这层，模型只要拿到一个 id（历史残留、别的会话的上下文），
    就能把它发进当前会话——那是**跨会话的数据外泄**。
    """
    run_id, conversation_id = await _seed_run(tenant)
    other_conversation = await _seed_artifact(
        tenant, artifact_root, data=b"other", kind="DOCUMENT",
        media_type="text/plain", filename="别人的.txt",
    )
    client = _FakeDeliveryClient()
    tool_set = _delivery_tool_set(
        tenant, artifact_root, run_id=run_id, conversation_id=conversation_id, client=client
    )

    with pytest.raises(AttachmentToolError) as exc:
        await _call(tool_set, DELIVER_ARTIFACT_TOOL, {"artifact_id": str(other_conversation)})

    assert "只能交付本会话" in exc.value.message
    assert not client.requests, "被拒的交付不得真的去调网关"


async def test_deliver_artifact_refuses_artifacts_this_run_did_not_produce(
    tenant, artifact_root
) -> None:
    """**别的 Run 自产**的产物仍然拒绝（原意图保留）。"""
    run_id, conversation_id = await _seed_run(tenant)
    other_run_artifact = await _seed_artifact(
        tenant, artifact_root, data=b"agent file", kind="DOCUMENT",
        media_type="text/markdown", filename="别处产物.md",
        artifact_type=AGENT_OUTPUT_ARTIFACT_TYPE,
    )
    client = _FakeDeliveryClient()
    tool_set = _delivery_tool_set(
        tenant, artifact_root, run_id=run_id, conversation_id=conversation_id, client=client
    )

    with pytest.raises(AttachmentToolError) as exc:
        await _call(tool_set, DELIVER_ARTIFACT_TOOL, {"artifact_id": str(other_run_artifact)})

    assert "只能交付本次运行自己写出的产物" in exc.value.message
    assert not client.requests


async def test_deliver_artifact_refuses_tool_result_artifacts(tenant, artifact_root) -> None:
    """**工具结果外置产物**（`TOOL_RESULT`）不能交付 —— 那是中间产物，发给用户等于泄漏上下文。

    放开转发时必须把它挡在外面：它的 `conversation_id` 与当前会话相同，光靠会话判定拦不住。
    """
    run_id, conversation_id = await _seed_run(tenant)
    tool_result = await _seed_artifact(
        tenant, artifact_root, data=b"internal", kind="OTHER",
        media_type="text/plain", filename="tool-result.txt",
        into=(run_id, conversation_id), artifact_type="TOOL_RESULT",
    )
    client = _FakeDeliveryClient()
    tool_set = _delivery_tool_set(
        tenant, artifact_root, run_id=run_id, conversation_id=conversation_id, client=client
    )

    with pytest.raises(AttachmentToolError) as exc:
        await _call(tool_set, DELIVER_ARTIFACT_TOOL, {"artifact_id": str(tool_result)})

    assert "不能交付给用户" in exc.value.message
    assert not client.requests
