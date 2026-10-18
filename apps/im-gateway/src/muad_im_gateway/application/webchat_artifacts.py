"""Webchat 附件下载与安全预览（design §3.4 API-10、§3.6 附件与重试生命周期；TASK-055）。

职责与边界：

- **逐请求授权**：每次下载/预览都重新走一条链 —— CHAT 主体（`ChatIdentityDep`，只经 Console
  核验）→ 会话归属（Owner 的 `resolve_webchat_artifact`：本主体名下会话，跨租户/他主体/已失效
  一律 404）→ **当前**入口/Agent 授权（入口快照 + 本次核验的授权集合）。拒绝发生在读取字节
  之前，`storage_key` 由 Owner 解析给出、绝不来自客户端。
- **不泄露存在性**：未授权入口/换绑 Agent 一律 404，授权撤销 403；响应里没有存储键、内部路径
  或主体字段 —— 调用方无法从状态码/响应体分辨"这个 id 存在但你没权限"。
- **安全预览**：只有**显式白名单**的栅格图片可以 `inline` 预览；HTML/SVG 等可执行内容一律
  `attachment` 下载（禁止同源脚本执行），并统一 `no-store` + `nosniff`。媒体类型按严格形状校验，
  坏值降级成 `application/octet-stream` + 下载（仍可下载，但绝不当可执行内容内联）。
- **字节完整性**：行内 `size` 与实际字节不符视同"拿不到"（404）—— 绝不按错误长度声明
  `Content-Length`（那会让客户端读到截断/悬挂的响应）。
- **DB 事务释放后流式**：Owner 解析是短事务；Gateway 只从共享存储流式读字节，不把整包驻留内存，
  也不在流式期间持有任何数据库连接（网关不持库，架构守卫机检）。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG
from typing import Any, Protocol
from urllib.parse import quote
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore
from muad_contracts import AttachmentRef
from muad_contracts.webchat import WebchatConversationScope
from pydantic import BaseModel, ValidationError

from .chat_principal import ChatIdentity
from .webchat_service import WebchatEntrySource, resolve_authorized_entry

logger = logging.getLogger(__name__)

#: 可**内联预览**的媒体类型白名单（栅格图片）：显式枚举，未知/可执行内容一律按附件下载。
SAFE_PREVIEW_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
#: 媒体类型降级值：形状不合法的值可以下载，但绝不当成可执行内容内联。
FALLBACK_MEDIA_TYPE = "application/octet-stream"
ATTACHMENT_DISPOSITION = "attachment"
INLINE_DISPOSITION = "inline"
#: 浏览器不得对下载内容做 MIME 嗅探（HTML/SVG 按附件处理不执行的第二道锁）。
NOSNIFF_HEADER = "nosniff"

#: 媒体类型的严格形状（与 Runtime 登记校验同口径）：坏值不可能进到响应头里。
_MEDIA_TYPE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$"
)


@dataclass(frozen=True, slots=True)
class ArtifactDownload:
    """取件目标：**共享存储路径 + 呈现元信息**（路径只在本层与响应之间流转，绝不进响应）。"""

    path: Path
    stat: os.stat_result
    media_type: str
    filename: str | None
    disposition: str


class WebchatArtifactOwnerPort(Protocol):
    """解析端口：`artifact_id` → 本主体名下会话的归属 + 渠道中立引用（Runtime 是唯一实现）。"""

    async def resolve_webchat_artifact(
        self, artifact_id: UUID, *, tenant_id: str, user_id: UUID
    ) -> dict[str, Any]: ...


class WebchatArtifactService:
    """API-10 应用服务：逐请求授权（会话归属 + 当前入口/Agent）后从共享存储取件。"""

    def __init__(
        self,
        *,
        runtime: WebchatArtifactOwnerPort,
        entries: WebchatEntrySource,
        store: NfsArtifactStore,
        tenant_id: str,
    ) -> None:
        self._runtime = runtime
        self._entries = entries
        self._store = store
        self._tenant_id = tenant_id

    async def open_download(self, identity: ChatIdentity, artifact_id: UUID) -> ArtifactDownload:
        """一次下载/预览的完整授权与取件准备；任一环不成立就显式拒绝（不读字节）。"""
        reference = await self._authorized_reference(identity, artifact_id)
        return self._open(reference)

    async def _authorized_reference(
        self, identity: ChatIdentity, artifact_id: UUID
    ) -> AttachmentRef:
        """Owner 归属解析 + **当前**入口/Agent 授权复核（授权只来自本次 Console 核验）。"""
        data = await self._runtime.resolve_webchat_artifact(
            artifact_id,
            tenant_id=self._tenant_id,
            user_id=identity.principal.platform_user_id,
        )
        scope = _project(WebchatConversationScope, data.get("scope"))
        resolved = resolve_authorized_entry(self._entries, identity, scope.entry_id)
        if resolved.agent_id != scope.agent_id:
            # 入口当前绑定已换 Agent：旧会话的字节不在新 Agent 名下交付（404 不泄露存在性）。
            logger.warning("webchat_artifact_entry_agent_changed")
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        # 授权通过后才解析引用：被拒的请求连存储键都不物化。
        return _project(AttachmentRef, data.get("reference"))

    def _open(self, reference: AttachmentRef) -> ArtifactDownload:
        """渠道中立引用 → 真实字节目标；键越界/字节缺失/长度不符一律"拿不到"（404）。"""
        try:
            path = self._store.resolve(reference.storage_key)
        except ValueError as exc:
            logger.warning("webchat_artifact_storage_key_invalid")
            raise AppError(ErrorCode.COMMON_NOT_FOUND) from exc
        try:
            stat = path.stat()
        except OSError as exc:
            logger.warning("webchat_artifact_bytes_missing error_type=%s", type(exc).__name__)
            raise AppError(ErrorCode.COMMON_NOT_FOUND) from exc
        if not S_ISREG(stat.st_mode) or stat.st_size != reference.size:
            # 行内长度与实际字节不符：不按错误长度声明 Content-Length，宁可说"拿不到"。
            logger.warning("webchat_artifact_size_mismatch")
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        media_type = safe_media_type(reference.media_type)
        return ArtifactDownload(
            path=path,
            stat=stat,
            media_type=media_type,
            filename=reference.filename,
            disposition=disposition_for(media_type),
        )


def safe_media_type(raw: str) -> str:
    """媒体类型 → 可安全放进响应头的值；形状不合法降级为二进制流（绝不内联）。"""
    media_type = raw.strip().lower()
    if not _MEDIA_TYPE_PATTERN.match(media_type):
        logger.warning("webchat_artifact_media_type_invalid")
        return FALLBACK_MEDIA_TYPE
    return media_type


def disposition_for(media_type: str) -> str:
    """呈现方式：白名单栅格图片 `inline` 预览，其余（含 HTML/SVG）一律 `attachment` 下载。"""
    return INLINE_DISPOSITION if media_type in SAFE_PREVIEW_MEDIA_TYPES else ATTACHMENT_DISPOSITION


def content_disposition_header(*, media_type: str, filename: str | None) -> str:
    """`Content-Disposition`：文件名只作元信息（控制字符/引号一律百分号编码，不拼路径）。"""
    disposition = disposition_for(media_type)
    if filename is None:
        return disposition
    encoded = quote(filename, safe="")
    if encoded == filename:
        return f'{disposition}; filename="{filename}"'
    return f"{disposition}; filename*=utf-8''{encoded}"


def _project[ContractT: BaseModel](model: type[ContractT], data: object) -> ContractT:
    """Owner 封套 → 公开契约；字段不符显式失败（不回显内部内容）。"""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        logger.warning("webchat_artifact_owner_projection_failed model=%s", model.__name__)
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
