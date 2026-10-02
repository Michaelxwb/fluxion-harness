"""入站附件落盘：把取回的字节写进**共享** artifact store，产出渠道中立的 `AttachmentRef`。

职责边界（设计 §3.2.1 的 ⑤）：

- **只写字节，不写 DB**。`artifact` 行由 Runtime 在 Run 建立之后写（AD-1-B），所以这里的产物是
  `AttachmentRef`（**相对** `storage_key` + 元信息），经 `RunRequest.message.attachments` 交给
  Runtime；网关与 Runtime 通过**共享** artifact store（RWX PVC）看到同一批字节。
- **原子且不可变**：临时文件 + `os.replace`；同一 `storage_key` 二次写入抛 `FileExistsError`
  （`harness-skill#RULE-skill-001` 的写入口径）。E-07 的第一道防线是消息去重，这里是第二道。
- **产物键只由系统生成**（`build_storage_key`）：用户给的文件名只作元信息，绝不参与路径拼接。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Literal

from muad_artifact_store import NfsArtifactStore
from muad_contracts import AttachmentRef, ChannelName

from ..channels.base import FetchedAttachment
from .attachment_gate import build_storage_key


def kind_for(media_type: str) -> Literal["IMAGE", "DOCUMENT"]:
    """由 MIME 派生契约的 `kind`。

    只会被**通过门控之后**的媒体调用，因此取值只可能是白名单里的图片或文档——不需要再维护一份
    与门控重复的类型表：白名单本身就在 `attachment_gate`。
    """
    return "IMAGE" if media_type.startswith("image/") else "DOCUMENT"


class InboundAttachmentStore:
    def __init__(self, store: NfsArtifactStore) -> None:
        self._store = store

    def persist(
        self,
        *,
        token: str,
        index: int,
        content: FetchedAttachment,
        source_channel: ChannelName,
    ) -> AttachmentRef:
        storage_key = build_storage_key(token=token, index=index)
        path = self._store.resolve(storage_key)
        if path.exists():
            raise FileExistsError(f"inbound attachment already written: {storage_key}")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_atomically(path, content.data)
        return AttachmentRef(
            storage_key=storage_key,
            kind=kind_for(content.media_type),
            media_type=content.media_type,
            size=len(content.data),
            filename=content.filename,
            checksum=content.checksum,
            source_channel=source_channel,
        )

    @staticmethod
    def _write_atomically(path: Path, data: bytes) -> None:
        """临时文件 + `os.replace`：读方要么看到完整文件，要么什么都看不到（不留半成品）。"""
        tmp = path.with_name(f".tmp-{os.getpid()}-{uuid.uuid4().hex}")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
