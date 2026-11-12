"""附件格式的单一事实：允许上传的媒体类型、展示扩展名与分类谓词。

**为什么只有这一份**：上传门控（im-gateway）、正文抽取（agent-runtime）与浏览器展示
（客户端配置投影）判断的都是「这个格式收不收」。三处各存一份必然漂移——放宽 zip 时只改了
门控、抽取器没跟上，用户就会遇到「能传不能读」。所以允许集合、扩展名与压缩包/文本谓词都定义
在这里，其余位置只引用。

**扩展名只用于展示与文件选择器过滤**，绝不参与准入判定：准入只认请求里的媒体类型，从文件名
反推类型会引入一整类伪装问题。
"""

from __future__ import annotations

from .tasks import ContractModel


class AttachmentFormat(ContractModel):
    """一个可上传格式的浏览器投影：媒体类型 + 用户可见扩展名（含点）。"""

    media_type: str
    extensions: tuple[str, ...]


SUPPORTED_UPLOAD_FORMATS: tuple[AttachmentFormat, ...] = (
    AttachmentFormat(media_type="image/png", extensions=(".png",)),
    AttachmentFormat(media_type="image/jpeg", extensions=(".jpg", ".jpeg")),
    AttachmentFormat(media_type="image/gif", extensions=(".gif",)),
    AttachmentFormat(media_type="image/webp", extensions=(".webp",)),
    AttachmentFormat(media_type="application/pdf", extensions=(".pdf",)),
    AttachmentFormat(
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        extensions=(".docx",),
    ),
    AttachmentFormat(
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        extensions=(".xlsx",),
    ),
    AttachmentFormat(
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        extensions=(".pptx",),
    ),
    AttachmentFormat(media_type="text/plain", extensions=(".txt",)),
    AttachmentFormat(media_type="text/markdown", extensions=(".md",)),
    AttachmentFormat(media_type="application/zip", extensions=(".zip",)),
    AttachmentFormat(media_type="application/x-zip-compressed", extensions=(".zip",)),
)

#: 准入集合从展示清单派生：新增格式只改一处，门控与投影同时生效。
ALLOWED_ATTACHMENT_MEDIA_TYPES: frozenset[str] = frozenset(
    item.media_type for item in SUPPORTED_UPLOAD_FORMATS
)

#: 按压缩包处理的类型。`x-zip-compressed` 是 Windows 浏览器对 `.zip` 的常见写法。
ARCHIVE_MEDIA_TYPES: frozenset[str] = frozenset(
    {"application/zip", "application/x-zip-compressed"}
)


def is_archive_media_type(media_type: str) -> bool:
    """是否按压缩包读取（大小写不敏感：部件头里的类型不保证小写）。"""
    return media_type.strip().lower() in ARCHIVE_MEDIA_TYPES


def is_text_media_type(media_type: str) -> bool:
    """是否按纯文本读取。沿用既有口径：`text/*` 一律按文本抽取。"""
    return media_type.strip().lower().startswith("text/")
