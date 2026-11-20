"""Task-owned 产物写入与不可变版本（design §3.1.5 API-28、§3.4.6、§4.4 item 10；TASK-008）。

**为什么需要它**：`runtime.artifact` 的 owner 是 Runtime，Worker 只能经内部 API 使用
（§3.1.5）。Task-owned 行复用 `artifact.task_id`（值 = `task.task_execution.id`），
`run_id` 必须为 NULL（既有 XOR 约束不变），因此存储键**不能**像 Run 产物那样带 `run_id`，
按 `tasks/{tenant}/{execution_id}/{artifact_id}/v{n}` 生成，也不要求 Conversation。

**归属只在 Owner 复核**：写入请求只携带 `task_execution_id`，不携带 actor/agent。Runtime 只读
同一 PG 里 Worker 拥有的 `task.task_execution` 当前事实（先例：`model_dispatch_store` 只读
`control.model_credential`、`channel_uploads` 只读 `control.bot_account`），拿
`tenant_id/actor_user_id/agent_id` 复核后写进产物行——调用方无法自报归属，跨租户一律 404。

**版本不可变 + 幂等**：同一 Execution 内 `filename` 是产物身份，一次写入产生一个不可变版本
（`version/checksum/name/media_type/size` 落库后不再改变）。`base_version` 是 CAS 而不是提示：
`0` 表示新建，`N` 表示在 `N` 版之上追加 `N+1`。同一调用的重放（同 `filename + base_version +
checksum + media_type + size`）返回**原版本**；内容不同或 base 过期一律
`ARTIFACT_VERSION_CONFLICT`——不静默多出版本，也不拿"最新版"顶替。

**两类产物分开**：本写入方只产出 `TASK_OUTPUT`（用户可下载的结果）。内部留档
（`TOOL_RESULT`/`TRANSCRIPT` 等）即使挂着 `task_id` 也不在可下载白名单里：新增的版本读取
面按类型白名单拒绝，内部原文不会因为"有了下载能力"而变成可下载。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore
from muad_contracts import AttachmentRef, TaskArtifactWriteRequest, TaskArtifactWriteResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.models.runtime import Artifact
from ...metrics import TASK_ARTIFACT_DOWNLOAD_METRIC, record_outcome
from .output_service import MAX_OUTPUT_BYTES
from .reference import attachment_ref

logger = logging.getLogger(__name__)

#: 用户可下载的任务产物类型：与 Run 产物统一为 OUTPUT。
TASK_OUTPUT_ARTIFACT_TYPE = "OUTPUT"
#: 任务产物可下载白名单：内部留档类型不在其中（下载面按它拒绝）。
DOWNLOADABLE_TASK_ARTIFACT_TYPES = frozenset({TASK_OUTPUT_ARTIFACT_TYPE})
#: 存储键前缀：键只由 tenant/execution/artifact/version 拼出，不含 run_id，也不含用户输入。
TASK_ARTIFACT_KEY_PREFIX = "tasks"

TASK_ARTIFACT_SIZE_LIMIT = MAX_OUTPUT_BYTES

_TASK_OWNER_SQL = sa.text(
    "SELECT tenant_id, actor_user_id, agent_id, attempt FROM task.task_execution"
    " WHERE id = :execution_id AND tenant_id = :tenant_id AND is_deleted = false"
)

#: 轻量反射表：只用于在 `runtime.artifact` 查询里做 EXISTS 核属，不引入跨 schema ORM 模型。
_TASK_EXECUTION_TABLE = sa.table(
    "task_execution",
    sa.column("id", sa.Uuid()),
    sa.column("tenant_id", sa.String()),
    sa.column("actor_user_id", sa.Uuid()),
    sa.column("agent_id", sa.Uuid()),
    sa.column("is_deleted", sa.Boolean()),
    schema="task",
)


def task_owner_predicate(
    *,
    tenant_id: str,
    user_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    artifact: type[Artifact] = Artifact,
) -> sa.ColumnElement[bool]:
    """任务产物的唯一归属谓词：task_id + 精确 user/agent + live task_execution。

    读与列必须复用这一份，避免“列得出、读不到”。`agent_id` 为空时返回恒假，绝不放宽。
    """
    if agent_id is None:
        return sa.false()
    live_task = sa.exists(
        sa.select(sa.literal(1)).select_from(_TASK_EXECUTION_TABLE).where(
            _TASK_EXECUTION_TABLE.c.id == artifact.task_id,
            _TASK_EXECUTION_TABLE.c.tenant_id == tenant_id,
            _TASK_EXECUTION_TABLE.c.actor_user_id == user_id,
            _TASK_EXECUTION_TABLE.c.agent_id == agent_id,
            _TASK_EXECUTION_TABLE.c.is_deleted.is_(False),
        )
    )
    return sa.and_(
        artifact.task_id.is_not(None),
        artifact.user_id == user_id,
        artifact.agent_id == agent_id,
        live_task,
    )


@dataclass(frozen=True, slots=True)
class TaskArtifactOwner:
    """可信的 Task owner 事实：从 `task.task_execution` 读，不从请求体取。"""

    execution_id: uuid.UUID
    tenant_id: str
    actor_user_id: uuid.UUID
    agent_id: uuid.UUID
    attempt: int


@dataclass(frozen=True, slots=True)
class CurrentVersion:
    """某个 Task 产物的当前不可变版本（重放/CAS 判定的唯一输入）。"""

    artifact_id: uuid.UUID
    version: int
    checksum: str
    media_type: str
    size: int


def task_artifact_storage_key(
    *, tenant_id: str, execution_id: uuid.UUID, artifact_id: uuid.UUID, version: int
) -> str:
    """系统生成的不可变键：与 Run 无关（`run_id` 为 NULL 时也必须能定位字节）。"""
    return (
        f"{TASK_ARTIFACT_KEY_PREFIX}/{tenant_id}/{execution_id}/{artifact_id}/v{version}"
    )


def plan_task_artifact_version(
    request: TaskArtifactWriteRequest, current: CurrentVersion | None
) -> int | None:
    """CAS 判定：返回要创建的版本号；`None` 表示重放当前版本；其余一律显式冲突。"""
    if request.base_version == 0:
        if current is None:
            return 1
        if current.version == 1 and _same_version(current, request):
            return None
        raise AppError(ErrorCode.ARTIFACT_VERSION_CONFLICT)
    if current is None:
        raise AppError(ErrorCode.TASK_ARTIFACT_NOT_FOUND)
    if current.version == request.base_version:
        return current.version + 1
    if current.version == request.base_version + 1 and _same_version(current, request):
        return None
    raise AppError(ErrorCode.ARTIFACT_VERSION_CONFLICT)


def _same_version(current: CurrentVersion, request: TaskArtifactWriteRequest) -> bool:
    """同一次调用的重放：内容、媒体类型与长度都必须与已落库的不可变版本一致。"""
    return (
        current.checksum == request.checksum
        and current.media_type == request.media_type
        and current.size == request.size
    )


async def write_task_artifact(
    session: AsyncSession,
    *,
    tenant_id: str,
    request: TaskArtifactWriteRequest,
    content: bytes,
    store: NfsArtifactStore,
) -> TaskArtifactWriteResponse:
    """写入/追加一个不可变版本并提交；文件与行同成同败。"""
    _validate_content(request, content)
    artifact_type = _artifact_type_for_purpose(request.purpose)
    await _lock_artifact_identity(
        session,
        tenant_id=tenant_id,
        execution_id=request.task_execution_id,
        filename=request.filename,
        artifact_type=artifact_type,
    )
    owner = await _load_owner(
        session, tenant_id=tenant_id, execution_id=request.task_execution_id
    )
    row = await _load_current(
        session,
        tenant_id=tenant_id,
        execution_id=request.task_execution_id,
        filename=request.filename,
        artifact_type=artifact_type,
        attempt=owner.attempt,
    )
    current = _current_version(row) if row is not None else None
    target = plan_task_artifact_version(request, current)
    if target is None:
        if row is None or current is None:  # plan 已保证两者同时存在；显式失败而不是 `assert`
            raise AppError(ErrorCode.ARTIFACT_VERSION_CONFLICT)
        return TaskArtifactWriteResponse(
            artifact_id=current.artifact_id,
            version=str(current.version),
            checksum=current.checksum,
        )

    # 版本即行：每个版本一个 artifact_id；追加不修改旧行。
    artifact_id = uuid.uuid4()
    logical_file_id = row.logical_file_id if row is not None else uuid.uuid4()
    storage_key = task_artifact_storage_key(
        tenant_id=tenant_id,
        execution_id=owner.execution_id,
        artifact_id=artifact_id,
        version=target,
    )
    # 先落字节再落行：键是「产物+版本」的确定身份，崩溃后重试复用同一份字节（见 _publish）。
    await asyncio.to_thread(_publish, store, storage_key, content, request.checksum)
    try:
        session.add(
            _new_row(
                owner,
                request,
                artifact_id=artifact_id,
                logical_file_id=logical_file_id,
                version=target,
                storage_key=storage_key,
                artifact_type=artifact_type,
                metadata=dict(row.metadata_json or {}) if row is not None else None,
            )
        )
        await session.commit()
    except BaseException:
        # 行没落成，本次写的文件也不留（历史版本字节不受影响）
        await asyncio.to_thread(_discard, store, storage_key)
        raise
    return TaskArtifactWriteResponse(
        artifact_id=artifact_id, version=str(target), checksum=request.checksum
    )


async def resolve_task_artifact_version(
    session: AsyncSession,
    *,
    tenant_id: str,
    artifact_id: uuid.UUID,
    version: str,
    checksum: str,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    store: NfsArtifactStore,
) -> AttachmentRef:
    """解析**指定**不可变版本；不匹配就显式失败，绝不返回最新版冒充。"""
    row = await session.scalar(
        sa.select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.tenant_id == tenant_id,
            Artifact.is_deleted.is_(False),
        )
    )
    if (
        row is None
        or row.task_id is None
        or row.artifact_type not in DOWNLOADABLE_TASK_ARTIFACT_TYPES
    ):
        record_outcome(TASK_ARTIFACT_DOWNLOAD_METRIC, "REJECTED")
        raise AppError(ErrorCode.TASK_ARTIFACT_NOT_FOUND)
    owner = await _load_owner(session, tenant_id=tenant_id, execution_id=row.task_id)
    if owner.actor_user_id != user_id or owner.agent_id != agent_id:
        record_outcome(TASK_ARTIFACT_DOWNLOAD_METRIC, "REJECTED")
        raise AppError(ErrorCode.TASK_ARTIFACT_NOT_FOUND)

    if str(row.version) != version or row.checksum != checksum:
        record_outcome(TASK_ARTIFACT_DOWNLOAD_METRIC, "REJECTED")
        raise AppError(ErrorCode.ARTIFACT_VERSION_CONFLICT)
    path = store.resolve(row.storage_key)
    if not path.is_file() or path.stat().st_size != row.size:
        # 行说这里有这一版、盘上没有（或长度不对）：显式"拿不到"，不拿别的版本顶上
        logger.warning("task_artifact_object_missing artifact_id=%s version=%s", artifact_id, version)
        record_outcome(TASK_ARTIFACT_DOWNLOAD_METRIC, "REJECTED")
        raise AppError(ErrorCode.TASK_ARTIFACT_NOT_FOUND)
    record_outcome(TASK_ARTIFACT_DOWNLOAD_METRIC, "RESOLVED")
    return attachment_ref(row)


def _validate_content(request: TaskArtifactWriteRequest, content: bytes) -> None:
    """声明的 size/checksum 必须与真实字节一致；零字节结果无法被下载引用，直接拒绝。"""
    if request.size == 0:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    if len(content) != request.size:
        raise AppError(ErrorCode.ARTIFACT_VERSION_CONFLICT)
    actual = "sha256:" + hashlib.sha256(content).hexdigest()
    if actual != request.checksum:
        raise AppError(ErrorCode.ARTIFACT_VERSION_CONFLICT)


async def _lock_artifact_identity(
    session: AsyncSession,
    *,
    tenant_id: str,
    execution_id: uuid.UUID,
    filename: str,
    artifact_type: str,
) -> None:
    """同一 (Execution, purpose, filename) 的写入串行化：新建时行还不存在，行锁兜不住。"""
    key = f"task-artifact:{tenant_id}:{execution_id}:{artifact_type}:{filename}"
    await session.execute(
        sa.select(sa.func.pg_advisory_xact_lock(sa.cast(sa.func.hashtext(key), sa.BigInteger)))
    )


async def _load_owner(
    session: AsyncSession, *, tenant_id: str, execution_id: uuid.UUID
) -> TaskArtifactOwner:
    """只读 Worker 拥有的 Execution 当前事实；跨租户/不存在/软删都视同不存在。"""
    row = (
        await session.execute(
            _TASK_OWNER_SQL, {"tenant_id": tenant_id, "execution_id": execution_id}
        )
    ).mappings().first()
    if row is None:
        raise AppError(ErrorCode.TASK_ARTIFACT_NOT_FOUND)
    return TaskArtifactOwner(
        execution_id=execution_id,
        tenant_id=row["tenant_id"],
        actor_user_id=row["actor_user_id"],
        agent_id=row["agent_id"],
        attempt=int(row["attempt"]),
    )


async def _load_current(
    session: AsyncSession,
    *,
    tenant_id: str,
    execution_id: uuid.UUID,
    filename: str,
    artifact_type: str,
    attempt: int,
) -> Artifact | None:
    row = await session.scalar(
        sa.select(Artifact)
        .where(
            Artifact.tenant_id == tenant_id,
            Artifact.task_id == execution_id,
            Artifact.artifact_type == artifact_type,
            Artifact.is_deleted.is_(False),
            Artifact.metadata_json["filename"].astext == filename,
            Artifact.metadata_json["attempt"].astext == str(attempt),
        )
        .order_by(Artifact.version.desc())
        .limit(1)
        .with_for_update()
    )
    return row if isinstance(row, Artifact) else None


def _current_version(row: Artifact) -> CurrentVersion:
    return CurrentVersion(
        artifact_id=row.id,
        version=row.version,
        checksum=row.checksum,
        media_type=row.media_type,
        size=row.size,
    )


def _artifact_type_for_purpose(purpose: str) -> str:
    """OUTPUT 统一为可下载类型；内部用途原样落库，不进入下载白名单。"""
    return TASK_OUTPUT_ARTIFACT_TYPE if purpose == "OUTPUT" else purpose


def _new_row(
    owner: TaskArtifactOwner,
    request: TaskArtifactWriteRequest,
    *,
    artifact_id: uuid.UUID,
    logical_file_id: uuid.UUID,
    version: int,
    storage_key: str,
    artifact_type: str,
    metadata: dict[str, Any] | None = None,
) -> Artifact:
    """Task-owned 版本行：`task_id` = Execution id、`run_id` 为 NULL（XOR）、无 Conversation。"""
    base = {
        "kind": _kind_for(request.media_type),
        "filename": request.filename,
        "source": "task",
        "attempt": owner.attempt,
    }
    if metadata:
        base.update({k: v for k, v in metadata.items() if k in {"relative_path", "kind"}})
    return Artifact(
        id=artifact_id,
        tenant_id=owner.tenant_id,
        run_id=None,
        task_id=owner.execution_id,
        conversation_id=None,
        agent_id=owner.agent_id,
        user_id=owner.actor_user_id,
        artifact_type=artifact_type,
        logical_file_id=logical_file_id,
        version=version,
        storage_key=storage_key,
        media_type=request.media_type,
        size=request.size,
        checksum=request.checksum,
        metadata_json=base,
    )


def _kind_for(media_type: str) -> str:
    """`AttachmentRef.kind` 是封闭枚举；写入方没有 kind 参数，按媒体类型显式归类。"""
    return "IMAGE" if media_type.startswith("image/") else "DOCUMENT"


def _publish(store: NfsArtifactStore, key: str, content: bytes, checksum: str) -> None:
    """不可变发布；同键已有**同一份**字节时视为已发布（崩溃后重试），不同内容即冲突。"""
    try:
        store.write(key, content)
    except FileExistsError:
        if _stored_checksum(store, key) != checksum:
            raise AppError(ErrorCode.ARTIFACT_VERSION_CONFLICT) from None
    except OSError as exc:
        logger.warning("task_artifact_write_failed key=%s error=%s", key, type(exc).__name__)
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc


def _stored_checksum(store: NfsArtifactStore, key: str) -> str | None:
    try:
        with store.resolve(key).open("rb") as handle:
            return "sha256:" + hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError:
        return None


def _discard(store: NfsArtifactStore, key: str) -> None:
    """回滚本次写的文件；删不掉也不抛——清理不能掩盖原始异常。"""
    try:
        store.resolve(key).unlink(missing_ok=True)
    except (OSError, ValueError):
        logger.warning("task_artifact_cleanup_failed key=%s", key)


__all__ = [
    "DOWNLOADABLE_TASK_ARTIFACT_TYPES",
    "TASK_ARTIFACT_SIZE_LIMIT",
    "TASK_OUTPUT_ARTIFACT_TYPE",
    "CurrentVersion",
    "TaskArtifactOwner",
    "plan_task_artifact_version",
    "resolve_task_artifact_version",
    "task_artifact_storage_key",
    "task_owner_predicate",
    "write_task_artifact",
]
