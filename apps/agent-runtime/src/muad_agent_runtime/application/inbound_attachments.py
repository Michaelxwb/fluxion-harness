"""入站附件的落库与上下文呈现（设计 §3.2.1 ⑥⑦、§3.3）。

**分工**：渠道侧已经把字节写进共享 artifact store 并给出 `storage_key`（设计 AD-1-B），
Runtime 只负责**落 DB 行**与**在上下文里呈现** —— 不搬字节。

**呈现口径（设计 AD-3-B）**：只有**当前消息**的图片内联成图像块；历史轮次的附件一律只留
文本引用。全量重发历史图片会让请求体随轮次线性膨胀，而多数轮次根本用不到旧图。
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from muad_agent_core.model import ImagePart, ModelContent
from muad_contracts import AttachmentRef
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import Artifact

INBOUND_IMAGE = "INBOUND_IMAGE"
INBOUND_DOCUMENT = "INBOUND_DOCUMENT"
INBOUND_OTHER = "INBOUND_OTHER"

_ARTIFACT_TYPE_BY_KIND = {
    "IMAGE": INBOUND_IMAGE,
    "DOCUMENT": INBOUND_DOCUMENT,
    "OTHER": INBOUND_OTHER,
}

#: 单轮内联的图片数上限。门控已限制单消息附件数，这里是防呆的第二道。
MAX_INLINE_IMAGES = 5


@dataclass(frozen=True, slots=True)
class PersistedAttachment:
    artifact_id: uuid.UUID
    kind: str
    media_type: str
    filename: str | None
    size: int
    storage_key: str


def artifact_type_for(kind: str) -> str:
    """未知 kind 归到 `INBOUND_OTHER`，而不是抛错——契约枚举已封闭，这里是防御性兜底。"""
    return _ARTIFACT_TYPE_BY_KIND.get(kind, INBOUND_OTHER)


async def persist_inbound_attachments(
    session: AsyncSession,
    *,
    tenant_id: str,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    refs: Sequence[AttachmentRef],
    source: str | None = None,
) -> tuple[PersistedAttachment, ...]:
    """把入站附件引用落成 `artifact` 行。

    **只 flush 不 commit**：调用方（Run 创建）在同一个事务里写 Run/Snapshot/事件，附件行必须
    与之同生共死——先提交会让"Run 建失败但附件行留下"成为可能。

    `run_id` 非空 ⇒ 既有的 `ck_artifact_run_task_xor`（run/task 恰有其一非空）继续成立，
    **不需要改 schema**（设计 AD-1-B）。
    """
    persisted: list[PersistedAttachment] = []
    for ref in refs:
        artifact_id = uuid.uuid4()
        row = Artifact(
            id=artifact_id,
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=None,
            conversation_id=conversation_id,
            artifact_type=artifact_type_for(ref.kind),
            storage_key=ref.storage_key,
            media_type=ref.media_type,
            size=ref.size,
            checksum=ref.checksum,
            metadata_json={
                "kind": ref.kind,
                "filename": ref.filename,
                "source": source or ref.source_channel,
            },
        )
        session.add(row)
        await session.flush()
        persisted.append(
            PersistedAttachment(
                artifact_id=artifact_id,
                kind=ref.kind,
                media_type=ref.media_type,
                filename=ref.filename,
                size=ref.size,
                storage_key=ref.storage_key,
            )
        )
    return tuple(persisted)


def attachment_payload(attachments: Sequence[PersistedAttachment]) -> list[dict[str, object]]:
    """写进 `USER_MESSAGE` 事件载荷的紧凑摘要。

    历史组装（`context_builder._to_messages`）直接从这里渲染文本引用，**不再回查 artifact 表**
    —— 历史是逐条回放的热路径，为一句引用多打一次库不划算。
    """
    return [
        {
            "artifact_id": str(item.artifact_id),
            "kind": item.kind,
            "media_type": item.media_type,
            "filename": item.filename,
            "size": item.size,
        }
        for item in attachments
    ]


def attachments_from_payload(raw: object) -> tuple[PersistedAttachment, ...]:
    """事件载荷 → 引用结构。载荷不合法（历史数据/手工写入）时返回空，不让回放整条失败。"""
    if not isinstance(raw, list):
        return ()
    items: list[PersistedAttachment] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            items.append(
                PersistedAttachment(
                    artifact_id=uuid.UUID(str(entry["artifact_id"])),
                    kind=str(entry.get("kind") or "OTHER"),
                    media_type=str(entry.get("media_type") or "application/octet-stream"),
                    filename=entry.get("filename") if isinstance(entry.get("filename"), str) else None,
                    size=int(entry.get("size") or 0),
                    storage_key="",
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return tuple(items)


def render_attachment_reference(attachments: Sequence[PersistedAttachment]) -> str:
    """把附件渲染成**一句文本引用**（历史轮次与不可内联时使用）。

    刻意带上 `artifact_id`：模型要"重看"或让工具读它，都得靠这个标识寻址。
    """
    if not attachments:
        return ""
    items = "；".join(
        f"{item.filename or '(未命名)'}（{item.media_type}，{item.size} 字节，附件 ID {item.artifact_id}）"
        for item in attachments
    )
    return f"[用户发来的附件] {items}（内容未直接展示，如需查看请调用附件读取工具）"


def build_current_content(
    text: str,
    *,
    attachments: Sequence[PersistedAttachment] = (),
    read_bytes: Callable[[str], bytes] | None = None,
) -> ModelContent:
    """把**当前消息**渲染成模型内容：图片内联为图像块，其余追加文本引用。

    没有可用的字节读取器（`read_bytes is None`）时**不假装没有附件**——仍保留文本引用，
    让模型知道"用户发过东西"，而不是把它当作纯文本消息。读取失败同样退回引用，
    不让整轮因为一个坏文件失败。
    """
    inline: list[ImagePart] = []
    referenced: list[PersistedAttachment] = []
    for item in attachments:
        if read_bytes is not None and item.kind == "IMAGE" and len(inline) < MAX_INLINE_IMAGES:
            try:
                data = read_bytes(item.storage_key)
            except OSError:
                referenced.append(item)
                continue
            inline.append(
                ImagePart(
                    media_type=item.media_type,
                    data_base64=base64.b64encode(data).decode("ascii"),
                )
            )
        else:
            referenced.append(item)

    reference = render_attachment_reference(referenced)
    head = text.strip()
    if not inline:
        return "\n".join(part for part in (head, reference) if part)

    lead = "\n".join(part for part in (head, reference) if part)
    parts: list[str | ImagePart] = [lead] if lead else []
    parts.extend(inline)
    return tuple(parts)
