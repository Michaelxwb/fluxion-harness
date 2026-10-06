"""入站附件落盘：把取回的字节写进**共享** artifact store，产出渠道中立的 `AttachmentRef`。

职责边界（设计 §3.2.1 的 ⑤）：

- **只写字节，不写 DB**。`artifact` 行由 Runtime 在 Run 建立之后写（AD-1-B），所以这里的产物是
  `AttachmentRef`（**相对** `storage_key` + 元信息），经 `RunRequest.message.attachments` 交给
  Runtime；网关与 Runtime 通过**共享** artifact store（RWX PVC）看到同一批字节。
- **原子且不可变**：临时文件 + `os.link`（目标已存在即失败，见 `_publish_new`）。同一
  `storage_key` 重放**相同内容**复用已有产物；**不同内容**抛 `AttachmentConflictError`。
- **产物键只由系统生成**（`build_storage_key`）：用户给的文件名只作元信息，绝不参与路径拼接。

2026-10-06 修的两处（同一段代码）：

1. **重放不能卡死**：此前"存在即 `FileExistsError`"会让消息重投直接失败——Redis 降级（fail-open
   不去重）或去重键过期后，同一条媒体消息永远走不到 Runtime 的幂等提交（实测：Runtime 只收到
   第一次那次失败请求）。
2. **并发写不能互相覆盖**：此前是 `path.exists()` 判断 + `os.replace` 发布，两者之间没有原子性
   —— 两个写者都能通过检查、都能"成功"，后写的静默覆盖先写的，于是两份 `AttachmentRef` 里有
   一份的 checksum 与磁盘内容对不上（实测：两次 persist 都返回成功、内容只剩一份）。
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Literal

from muad_artifact_store import NfsArtifactStore
from muad_contracts import AttachmentRef, ChannelName

from ..channels.base import FetchedAttachment
from .attachment_gate import build_storage_key


class AttachmentConflictError(Exception):
    """同一 `storage_key` 下已经有**不同内容**：既不覆盖别人的字节，也不把那些字节当成自己的。"""

    def __init__(self, storage_key: str) -> None:
        super().__init__(f"inbound attachment conflict: {storage_key}")
        self.storage_key = storage_key


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
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._publish_new(path, content.data)
        except FileExistsError:
            return self._reuse_existing(path, storage_key, content, source_channel)
        return AttachmentRef(
            storage_key=storage_key,
            kind=kind_for(content.media_type),
            media_type=content.media_type,
            size=len(content.data),
            filename=content.filename,
            checksum=content.checksum,
            source_channel=source_channel,
        )

    def _reuse_existing(
        self,
        path: Path,
        storage_key: str,
        content: FetchedAttachment,
        source_channel: ChannelName,
    ) -> AttachmentRef:
        """同键重放：内容一致就**复用已有产物**（重投不再卡死），不一致明确冲突。

        比较的是**磁盘上那份字节**的摘要，不是"文件在不在"——文件在但内容不同是两回事：前者
        是重投，后者说明有人在同一个键上换了东西，两种情况的处置完全相反。
        """
        existing = path.read_bytes()
        if hashlib.sha256(existing).hexdigest() != content.checksum:
            raise AttachmentConflictError(storage_key)
        return AttachmentRef(
            storage_key=storage_key,
            kind=kind_for(content.media_type),
            media_type=content.media_type,
            size=len(existing),
            filename=content.filename,
            checksum=content.checksum,
            source_channel=source_channel,
        )

    @staticmethod
    def _publish_new(path: Path, data: bytes) -> None:
        """**目标不存在才发布**：临时文件 + `os.link`。

        `os.link` 在目标已存在时抛 `FileExistsError`，这是内核级的一次判定 —— 两个并发写者
        只有一个能赢。不能用 `os.replace`：它会**替换**已存在的目标，于是"不可变"只写在注释里。
        临时文件先落盘再建链，所以读方要么看不到文件，要么看到完整内容（不会读到半截）。
        """
        tmp = path.with_name(f".tmp-{os.getpid()}-{uuid.uuid4().hex}")
        try:
            tmp.write_bytes(data)
            os.link(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
