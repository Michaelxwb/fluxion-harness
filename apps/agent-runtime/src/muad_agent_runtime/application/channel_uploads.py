"""网页暂存上传的登记、受理核属与 Run 独立不可变制品原语（design §3.3/§3.4/§3.6）。

分工与边界：

- **入口/Agent 核属**（`assert_entry_agent_binding`）：受信登记端口在落行**之前**核当前入口是
  本租户启用中、绑定该 Agent 的真实 `control.bot_account` 行——自报入口/Agent 或预造入口引用
  一律显式拒绝（跨租户/不存在不泄露存在性）。用户活性与入口授权由 CHAT 认证面在 Gateway 侧核验。
- **登记**（`register_staged_upload`）：Gateway 把字节流式写进共享 artifact store 后，经
  受信端口登记**权威元信息**并拿到 `upload_id`。登记行是 `channel_upload`，**不是** artifact
  行；`storage_key` 由系统生成（相对路径），大小/类型/校验和在这里做第一道显式拒绝。
- **受理核属**（`resolve_message_uploads`）：只有**未受理的新提交**才校验暂存 TTL 与归属；
  跨租户一律 `COMMON_NOT_FOUND`（不泄露存在性），同租户越权/借入口 `FORBIDDEN`，过期
  `UPLOAD_EXPIRED`，数量超限 `ATTACHMENT_COUNT_EXCEEDED`。幂等重放由 Run 受理路径在调用本
  模块**之前**完成（已受理提交不重新依赖暂存状态）。
- **Run 独立不可变制品**（`publish_run_artifact`）：受理时按上传行发布 Run 自己的
  `storage_key`（≠暂存键）：先校验真实字节的 sha256 与登记一致，再用 `os.link` 原子硬链接
  发布——目标已存在即 `FileExistsError`（同键二次写入绝不覆盖，`harness-skill#RULE-skill-001`）。
  暂存键与各 Run 的键是**不同目录项**：清理暂存或某一 Run 的制品都不会删掉其他引用的字节。
- **清理**（`cleanup_expired_staging`）：只软删过期且未归属的暂存登记并删除其暂存文件；
  已受理制品的字节在 Run 自己的键上，不因暂存清理消失。活跃引用与孤儿宽限的完整保护
  属 TASK-069 的清理任务，本模块只提供暂存侧的最小正确语义。

时间一律取 PG 服务端时钟（`database_now`）：TTL 判定跨实例一致，不用进程墙钟。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore
from muad_contracts.platform_settings import parse_platform_settings
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import ChannelUpload

logger = logging.getLogger(__name__)

#: 暂存上传的 kind 词表（与 `ck_channel_upload_kind` 同形）。
CHANNEL_UPLOAD_KINDS = frozenset({"IMAGE", "DOCUMENT", "OTHER"})
#: 暂存键长度上限（DB 是 text，这里给路径语义一个明确边界）。
STAGING_KEY_MAX_LENGTH = 512
#: Run 自己的制品键前缀：`runs/<run_id>/attachments/<upload_id>`（全部系统生成，不含用户输入）。
RUN_ARTIFACT_KEY_PREFIX = "runs"
#: 单次清理扫描上限（有界前进，不一次扫全表）。
CLEANUP_BATCH_SIZE = 100

_CHECKSUM_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_MEDIA_TYPE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$"
)
#: 入口/Agent 核属：只读 Console 拥有的 `control.bot_account` 当前事实（同一 PG，先例见
#: `infrastructure/model_dispatch_store`）；跨 Owner 不落副本、不双写。
_ENTRY_BINDING_SQL = sa.text(
    "SELECT agent_id, enabled FROM control.bot_account"
    " WHERE id = :entry_id AND tenant_id = :tenant_id AND is_deleted = false"
)


@dataclass(frozen=True, slots=True)
class ChannelUploadLimits:
    """暂存上传的门控（来自 `webchat` 设置组；默认值与既有附件门控一致）。"""

    upload_max_bytes: int
    upload_max_per_message: int
    staging_ttl_sec: int

    def __post_init__(self) -> None:
        if self.upload_max_bytes <= 0 or self.upload_max_per_message <= 0:
            raise ValueError("channel upload limits must be positive")
        if self.staging_ttl_sec <= 0:
            raise ValueError("staging TTL must be positive")


def upload_limits_from_settings(settings: Mapping[str, Any] | None) -> ChannelUploadLimits:
    """平台设置快照 → 暂存门控；缺省分组回落 schema 默认（唯一来源在 contracts）。"""
    webchat = parse_platform_settings(settings).webchat
    return ChannelUploadLimits(
        upload_max_bytes=webchat.upload_max_bytes,
        upload_max_per_message=webchat.upload_max_per_message,
        staging_ttl_sec=webchat.upload_staging_ttl_sec,
    )


def validate_storage_key(storage_key: str) -> None:
    """暂存键必须是系统生成的相对路径：空串/绝对路径/反斜杠/`.`/`..` 段一律显式拒绝。"""
    if not storage_key or len(storage_key) > STAGING_KEY_MAX_LENGTH:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    if storage_key.startswith("/") or "\\" in storage_key:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    if any(part in ("", ".", "..") for part in storage_key.split("/")):
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)


def validate_upload_metadata(
    *,
    kind: str,
    media_type: str,
    checksum: str,
    filename: str | None,
    size: int,
    limits: ChannelUploadLimits,
) -> None:
    """登记元信息的结构与边界：坏形状显式校验失败；超单文件上限用附件门控词表的稳定码。"""
    if kind not in CHANNEL_UPLOAD_KINDS:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    if len(media_type) > 128 or not _MEDIA_TYPE_PATTERN.match(media_type):
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    if not _CHECKSUM_PATTERN.match(checksum):
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    if filename is not None and (not filename or len(filename) > 255):
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    if size <= 0:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    if size > limits.upload_max_bytes:
        raise AppError(ErrorCode.ATTACHMENT_TOO_LARGE)


def run_artifact_storage_key(*, run_id: uuid.UUID, upload_id: uuid.UUID) -> str:
    """Run 自己的制品键：只由系统 UUID 拼出，**与暂存键必然不同**（不同目录项）。"""
    return f"{RUN_ARTIFACT_KEY_PREFIX}/{run_id}/attachments/{upload_id}"


def publish_run_artifact(
    *, store: NfsArtifactStore, upload: ChannelUpload, storage_key: str
) -> None:
    """把暂存字节发布为 Run 自己的不可变键：校验 checksum 后原子硬链接（目标存在即失败）。

    读不到暂存字节 ⇒ `UPLOAD_EXPIRED`（对用户就是"重新上传"）；字节与登记校验和不一致 ⇒
    `COMMON_CONFLICT`（登记内容不可信，拒绝受理）。发布用 `os.link`：内核原子判定"目标不存在"，
    两个写者不可能都成功、也绝无覆盖；暂存文件与 Run 键共享 inode，删任一路径都不影响另一路径
    的字节（这正是"暂存/其他 Run 清理不删当前字节"的机制）。
    """
    staged = _resolve_staged_path(store, upload.storage_key)
    if _staged_checksum(staged) != upload.checksum:
        raise AppError(ErrorCode.COMMON_CONFLICT)
    target = store.resolve(storage_key)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(staged, target)
    except FileExistsError as exc:
        raise AppError(ErrorCode.COMMON_CONFLICT) from exc
    except OSError as exc:
        logger.warning(
            "run_artifact_publish_failed",
            extra={"upload_id": str(upload.id), "error_type": type(exc).__name__},
        )
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc


def assert_upload_not_expired(upload: ChannelUpload, now: datetime) -> None:
    """过期只阻止**新受理**：已受理提交的幂等重放不经过这里（设计 §3.4）。"""
    if upload.expires_at <= now:
        raise AppError(ErrorCode.UPLOAD_EXPIRED)


async def _assert_no_active_staging_key(
    session: AsyncSession, *, tenant_id: str, storage_key: str
) -> None:
    """活跃暂存键预检：同租户同键已有活跃登记即显式冲突（唯一索引仍是并发兜底）。"""
    existing = await session.scalar(
        sa.select(ChannelUpload.id).where(
            ChannelUpload.tenant_id == tenant_id,
            ChannelUpload.storage_key == storage_key,
            ChannelUpload.is_deleted.is_(False),
        )
    )
    if existing is not None:
        raise AppError(ErrorCode.COMMON_CONFLICT)


async def assert_entry_agent_binding(
    session: AsyncSession, *, tenant_id: str, entry_id: uuid.UUID, agent_id: uuid.UUID
) -> None:
    """受信登记端口的入口/Agent 核属：入口必须是本租户**启用中**、绑定该 Agent 的真实入口账号。

    - 跨租户/不存在/已删除 ⇒ `COMMON_NOT_FOUND`（不泄露存在性）；
    - 同租户入口绑定了别的 Agent 或已停用 ⇒ `FORBIDDEN`（显式越权）。

    用户活性与入口授权由 CHAT 认证面（Console API-12 主体）在 Gateway 侧实时核验；本函数只核
    Runtime 能权威判定的入口身份事实——自报入口/Agent 没有位置，也不编造未登记的入口引用。
    """
    row = (
        await session.execute(_ENTRY_BINDING_SQL, {"entry_id": entry_id, "tenant_id": tenant_id})
    ).first()
    if row is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    if row.agent_id != agent_id or not row.enabled:
        raise AppError(ErrorCode.FORBIDDEN)


async def register_staged_upload(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: uuid.UUID,
    entry_id: uuid.UUID,
    storage_key: str,
    kind: str,
    media_type: str,
    checksum: str,
    filename: str | None,
    size: int,
    limits: ChannelUploadLimits,
) -> ChannelUpload:
    """登记暂存上传（返回 `upload_id` 载体）：元信息/键形状先校验，TTL 从 PG 时钟起算。"""
    validate_storage_key(storage_key)
    validate_upload_metadata(
        kind=kind,
        media_type=media_type,
        checksum=checksum,
        filename=filename,
        size=size,
        limits=limits,
    )
    await _assert_no_active_staging_key(
        session, tenant_id=tenant_id, storage_key=storage_key
    )
    now = await database_now(session)
    row = ChannelUpload(
        tenant_id=tenant_id,
        user_id=user_id,
        entry_id=entry_id,
        storage_key=storage_key,
        kind=kind,
        media_type=media_type,
        checksum=checksum,
        filename=filename,
        size=size,
        expires_at=now + timedelta(seconds=limits.staging_ttl_sec),
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        # 并发登记同键：活跃唯一索引是兜底，落败者同样得到显式冲突。
        raise AppError(ErrorCode.COMMON_CONFLICT) from exc
    return row


async def load_owned_upload(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: uuid.UUID,
    entry_id: uuid.UUID,
    upload_id: uuid.UUID,
) -> ChannelUpload:
    """按受验证主体取登记行：跨租户/不存在/已清理 ⇒ `COMMON_NOT_FOUND`；越权 ⇒ `FORBIDDEN`。"""
    row = await session.scalar(
        sa.select(ChannelUpload).where(
            ChannelUpload.id == upload_id,
            ChannelUpload.tenant_id == tenant_id,
            ChannelUpload.is_deleted.is_(False),
        )
    )
    if row is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    if row.user_id != user_id or row.entry_id != entry_id:
        raise AppError(ErrorCode.FORBIDDEN)
    return row


async def resolve_message_uploads(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: uuid.UUID,
    entry_id: uuid.UUID,
    text: str,
    upload_ids: list[uuid.UUID],
    limits: ChannelUploadLimits,
) -> tuple[ChannelUpload, ...]:
    """新消息受理前的暂存核属：内容/数量门控 + tenant/user/entry 核属 + TTL 判定。

    只在**未受理的新提交**路径调用；返回的登记行是发布 Run 制品的权威输入
    （调用方不得再信客户端给过的任何元信息）。
    """
    if not text.strip() and not upload_ids:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    if len(set(upload_ids)) != len(upload_ids):
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    if len(upload_ids) > limits.upload_max_per_message:
        raise AppError(ErrorCode.ATTACHMENT_COUNT_EXCEEDED)
    now = await database_now(session)
    uploads: list[ChannelUpload] = []
    for upload_id in upload_ids:
        upload = await load_owned_upload(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            entry_id=entry_id,
            upload_id=upload_id,
        )
        assert_upload_not_expired(upload, now)
        uploads.append(upload)
    return tuple(uploads)


async def cleanup_expired_staging(
    session: AsyncSession,
    *,
    artifact_root: str | Path,
    batch_size: int = CLEANUP_BATCH_SIZE,
) -> int:
    """清理过期未归属的暂存登记：软删行 + 删除暂存文件；已受理的 Run 制品不受影响。

    行锁 + `SKIP LOCKED` 让并发清理者各自前进（不互相等待）；只处理 `expires_at <= now`
    的活跃行，清理本身幂等。返回本次软删的行数。
    """
    if isinstance(batch_size, bool) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    store = NfsArtifactStore(artifact_root)
    now = await database_now(session)
    rows = list(
        (
            await session.scalars(
                sa.select(ChannelUpload)
                .where(
                    ChannelUpload.is_deleted.is_(False),
                    ChannelUpload.expires_at <= now,
                )
                .order_by(ChannelUpload.expires_at)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for row in rows:
        row.is_deleted = True
        row.update_time = sa.func.now()
        _remove_staged_file(store, row)
    await session.flush()
    return len(rows)


def _resolve_staged_path(store: NfsArtifactStore, storage_key: str) -> Path:
    path = store.resolve(storage_key)
    if not path.is_file():
        raise AppError(ErrorCode.UPLOAD_EXPIRED)
    return path


def _staged_checksum(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
    except FileNotFoundError as exc:
        raise AppError(ErrorCode.UPLOAD_EXPIRED) from exc
    except OSError as exc:
        logger.warning("staged_upload_read_failed", extra={"error_type": type(exc).__name__})
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
    return f"sha256:{digest}"


def _remove_staged_file(store: NfsArtifactStore, row: ChannelUpload) -> None:
    """删暂存文件（行已软删）：文件缺失是幂等清理的正常情形；其余 IO 失败留可观测告警。"""
    try:
        store.resolve(row.storage_key).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "staged_upload_unlink_failed",
            extra={"upload_id": str(row.id), "error_type": type(exc).__name__},
        )
