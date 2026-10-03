"""`artifact` 行 → **渠道中立**的 `AttachmentRef`（设计 AD-6-C）。

**为什么单独一个模块**：这个映射现在有两个调用方——交付工具（会话内）与产物引用解析端点
（后台任务用）。口径必须**只有一处**：两处各写一遍，迟早会在"`kind` 归什么""文件名缺省怎么填"
这些细节上分叉，而分叉的表现是**投递形态不一致**（同一份产物在两条路径上被判成不同类型）。

出站方向 `artifact_id` **必须**带上——降级为签名取件直链时适配器靠它拼链接。
"""

from __future__ import annotations

from muad_contracts import AttachmentRef

from ...infrastructure.models.runtime import Artifact

#: 契约的封闭枚举。未知值归 `OTHER` 而不是抛：产物行可能是历史数据或别的写入方落的。
_KNOWN_KINDS = ("IMAGE", "DOCUMENT", "OTHER")


def artifact_kind(row: Artifact) -> str:
    kind = (row.metadata_json or {}).get("kind")
    return kind if kind in _KNOWN_KINDS else "OTHER"


def attachment_ref(row: Artifact) -> AttachmentRef:
    """产物行 → 渠道中立的引用。

    只有存储键与元信息：**没有**任何渠道私有的发送形状，也没有取件凭据（AD-8）。
    """
    return AttachmentRef(
        storage_key=row.storage_key,
        kind=artifact_kind(row),  # type: ignore[arg-type]
        media_type=row.media_type,
        size=row.size,
        filename=_filename(row),
        checksum=row.checksum,
        artifact_id=row.id,
    )


def _filename(row: Artifact) -> str | None:
    name = (row.metadata_json or {}).get("filename")
    return name if isinstance(name, str) and name else None
