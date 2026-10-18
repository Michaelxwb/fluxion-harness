"""B-253 / B-02：[integration] 上传登记与 Run 独立不可变制品（design §3.3/§3.4/§3.6）。

真实边界（不 mock 存储或租约）：

- **真实 PostgreSQL**：隔离租户的 `runtime.channel_upload` / `artifact` / `run_record` /
  `run_submission` / `canonical_event`（只增删本租户的行）；
- **真实共享文件系统**：临时 `ARTIFACT_ROOT` 下写真实暂存字节、校验真实 sha256、
  原子硬链接发布 Run 自己的键、清理真实删除暂存文件；
- **真实 RunService 受理路径**：幂等重放、暂存 TTL、归属核验与 Run 制品发布都在真实事务里；
  只有模型调用与定义解析是进程内替身（本任务不验证真实模型执行）。

关键断言（契约逐项）：

1. 登记：TTL 来自 `webchat.upload_staging_ttl_sec`、tenant/user/entry 归属受控、大小边界与
   既有附件门控一致；重复登记同键显式冲突；任意路径/坏校验和被拒；登记行不是 artifact 行。
2. 受理：跨租户 `COMMON_NOT_FOUND`、同租户越权 `FORBIDDEN`、过期 `UPLOAD_EXPIRED`、
   数量超限 `ATTACHMENT_COUNT_EXCEEDED`、空正文且无附件 `COMMON_VALIDATION_ERROR`、纯附件放行。
3. **Run 独立不可变制品**：受理为每个 Run 发布自己的 `storage_key`（≠暂存键），内容与
   checksum 不可变；同一上传被多个 Run 引用时各自独立；暂存/另一 Run 的清理不删当前字节。
4. **幂等重放先查已受理结果**：暂存过期（甚至字节已被清理）后重放同键同指纹仍回原 Run；
   只有未受理的新提交才校验暂存 TTL/内容。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.channel_uploads import (
    ChannelUploadLimits,
    cleanup_expired_staging,
    load_owned_upload,
    register_staged_upload,
    resolve_message_uploads,
    run_artifact_storage_key,
    upload_limits_from_settings,
)
from muad_agent_runtime.application.executor import ExecutorFactory
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.application.run_service import RunService, RunStart
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    ChannelUpload,
    RunRecord,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore
from muad_contracts import ChannelContext, MessageInput, RunRequest

from agent_runtime.conftest import CLEANUP_ORDER, FakeResolveClient, TenantContext

pytestmark = pytest.mark.integration

INSTANCE_ID = "instance-b253"
WAIT_TIMEOUT_SEC = 30.0
DOC_BYTES = b"%PDF-1.7 staged webchat upload bytes"
DOC_CHECKSUM = "sha256:" + hashlib.sha256(DOC_BYTES).hexdigest()
#: 清理顺序：暂存登记/制品/入口指针先于 run_record/conversation（同 Owner 物理 FK）。
PURGE_ORDER = ("channel_upload", "artifact", "channel_conversation", *CLEANUP_ORDER)


# --------------------------------------------------------------------------------------
# 隔离租户、真实文件与真实受理驱动
# --------------------------------------------------------------------------------------


@pytest.fixture
async def staging_tenant(
    tenant: TenantContext, tmp_path: Path
) -> AsyncIterator[tuple[TenantContext, Path]]:
    """隔离租户 + 隔离 artifact root（真实文件；不触碰共享 dev 目录）。"""
    root = tmp_path / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield tenant, root
    finally:
        async with get_session_factory()() as session:
            for table in PURGE_ORDER:
                await session.execute(
                    sa.text(f"DELETE FROM runtime.{table} WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant.tenant_id},
                )
            await session.commit()


class _StagingSettings:
    """本套件设置源：足够大的准入容量；webchat 组可注入（TTL/限额边界）。"""

    def __init__(self, *, webchat: dict[str, Any] | None = None) -> None:
        self._webchat = webchat

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        settings: dict[str, Any] = {
            "capacity": {
                "max_open_runs_per_tenant": 100_000,
                "max_open_runs_per_user": 100,
            }
        }
        if self._webchat is not None:
            settings["webchat"] = self._webchat
        return PlatformSettingsSnapshot(revision=0, settings=settings)


def _limits(**overrides: Any) -> ChannelUploadLimits:
    base: dict[str, Any] = {
        "upload_max_bytes": 50 * 1024 * 1024,
        "upload_max_per_message": 5,
        "staging_ttl_sec": 86_400,
    }
    base.update(overrides)
    return ChannelUploadLimits(**base)


def _write_staged_bytes(root: Path, storage_key: str, data: bytes = DOC_BYTES) -> str:
    path = root / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return "sha256:" + hashlib.sha256(data).hexdigest()


async def _register(
    tenant: TenantContext,
    root: Path,
    *,
    data: bytes = DOC_BYTES,
    storage_key: str | None = None,
    entry_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    limits: ChannelUploadLimits | None = None,
    kind: str = "DOCUMENT",
    media_type: str = "application/pdf",
    filename: str | None = "报告.pdf",
    size: int | None = None,
    checksum: str | None = None,
) -> ChannelUpload:
    key = storage_key if storage_key is not None else f"webchat/staging/{uuid.uuid4().hex}"
    actual_checksum = _write_staged_bytes(root, key, data)
    async with get_session_factory()() as session:
        row = await register_staged_upload(
            session,
            tenant_id=tenant.tenant_id,
            user_id=user_id or tenant.platform_user_id,
            entry_id=entry_id,
            storage_key=key,
            kind=kind,
            media_type=media_type,
            checksum=checksum if checksum is not None else actual_checksum,
            filename=filename,
            size=len(data) if size is None else size,
            limits=limits or _limits(),
        )
        await session.commit()
        return row


async def _resolve(
    tenant: TenantContext,
    *,
    entry_id: uuid.UUID,
    upload_ids: Sequence[uuid.UUID],
    text: str = "看看附件",
    user_id: uuid.UUID | None = None,
    limits: ChannelUploadLimits | None = None,
) -> tuple[ChannelUpload, ...]:
    async with get_session_factory()() as session:
        return await resolve_message_uploads(
            session,
            tenant_id=tenant.tenant_id,
            user_id=user_id or tenant.platform_user_id,
            entry_id=entry_id,
            text=text,
            upload_ids=list(upload_ids),
            limits=limits or _limits(),
        )


async def _seed_conversation(
    tenant: TenantContext,
    *,
    entry_id: uuid.UUID | None,
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    conversation_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.conversation"
                " (id, tenant_id, user_id, agent_id, status, last_seq, channel_account_id)"
                " VALUES (:id, :tenant_id, :user_id, :agent_id, 'ACTIVE', 0, :entry_id)"
            ).bindparams(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=user_id or tenant.platform_user_id,
                agent_id=tenant.agent_id,
                entry_id=entry_id,
            )
        )
        await session.commit()
    return conversation_id


def _request(
    tenant: TenantContext,
    *,
    conversation_id: uuid.UUID,
    entry_id: uuid.UUID,
    upload_ids: Sequence[uuid.UUID] = (),
    text: str = "看看附件",
    message_id: str | None = None,
    user_id: uuid.UUID | None = None,
) -> RunRequest:
    return RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=user_id or tenant.platform_user_id,
        conversation_id=conversation_id,
        channel=ChannelContext(type="WEBCHAT", bot_id=str(entry_id), external_user_id="web-1"),
        message=MessageInput(
            id=message_id or f"msg-{uuid.uuid4()}",
            text=text,
            upload_ids=list(upload_ids),
        ),
    )


async def _start_run(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    supervisor: Any,
    request: RunRequest,
    *,
    root: Path,
    settings_client: Any = None,
    key: str | None = None,
) -> RunStart:
    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            INSTANCE_ID,
            settings_client if settings_client is not None else _StagingSettings(),
            executor_factory=executor_factory,
            supervisor=supervisor,
            artifact_root=str(root),
        )
        return await service.start(request, tenant.tenant_id, idempotency_key=key)


async def _drain(*starts: RunStart) -> None:
    for started in starts:
        async for _ in started.events:
            pass


async def _rejects(start_coro: Any) -> AppError:
    with pytest.raises(AppError) as raised:
        await start_coro
    return raised.value


async def _artifact_rows(tenant_id: str) -> list[Artifact]:
    async with get_session_factory()() as session:
        return list(
            (
                await session.execute(
                    sa.select(Artifact).where(Artifact.tenant_id == tenant_id).order_by(Artifact.create_time)
                )
            ).scalars()
        )


async def _run_count(tenant_id: str) -> int:
    async with get_session_factory()() as session:
        count = await session.scalar(
            sa.select(sa.func.count()).select_from(RunRecord).where(RunRecord.tenant_id == tenant_id)
        )
    return int(count or 0)


async def _read_upload(upload_id: uuid.UUID) -> ChannelUpload:
    async with get_session_factory()() as session:
        row = await session.get(ChannelUpload, upload_id)
        assert row is not None
        return row


async def _expire_staging(upload_id: uuid.UUID) -> None:
    """把暂存行推到过期（create_time 一并后退以维持 `expires_at > create_time` 约束）。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.channel_upload"
                " SET create_time = now() - interval '2 hours',"
                "     expires_at = now() - interval '1 hour'"
                " WHERE id = :upload_id"
            ),
            {"upload_id": upload_id},
        )
        await session.commit()


async def _soft_delete_upload(upload_id: uuid.UUID) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            sa.text("UPDATE runtime.channel_upload SET is_deleted = true WHERE id = :upload_id"),
            {"upload_id": upload_id},
        )
        await session.commit()


# --------------------------------------------------------------------------------------
# B-253 断言 1 / B-02：登记（TTL、归属、边界、不可变登记行）
# --------------------------------------------------------------------------------------


async def test_registration_records_attribution_and_ttl_without_artifact_rows(
    staging_tenant: tuple[TenantContext, Path],
) -> None:
    tenant, root = staging_tenant
    entry = uuid.uuid4()

    row = await _register(tenant, root, entry_id=entry)

    assert row.tenant_id == tenant.tenant_id
    assert row.user_id == tenant.platform_user_id
    assert row.entry_id == entry
    assert row.kind == "DOCUMENT"
    assert row.media_type == "application/pdf"
    assert row.checksum == DOC_CHECKSUM
    assert row.size == len(DOC_BYTES)
    assert row.filename == "报告.pdf"
    assert row.is_deleted is False
    ttl = (row.expires_at - row.create_time).total_seconds()
    assert 86_400 <= ttl <= 86_410, "TTL 来自 webchat.upload_staging_ttl_sec（默认 86400 秒）"
    assert (root / row.storage_key).read_bytes() == DOC_BYTES, "暂存字节由渠道写入共享存储"
    assert await _artifact_rows(tenant.tenant_id) == [], "运行前的暂存上传不是 artifact 行"


async def test_registration_rejects_unsafe_keys_bad_checksum_and_oversize(
    staging_tenant: tuple[TenantContext, Path],
) -> None:
    tenant, root = staging_tenant
    entry = uuid.uuid4()
    limits = _limits(upload_max_bytes=len(DOC_BYTES))

    async def attempt(**overrides: Any) -> AppError:
        data = overrides.pop("data", DOC_BYTES)
        key = overrides.pop("storage_key", f"webchat/staging/{uuid.uuid4().hex}")
        write_file = overrides.pop("write_file", True)
        checksum = overrides.pop("checksum", None)
        if checksum is None:
            checksum = (
                _write_staged_bytes(root, key, data)
                if write_file
                else "sha256:" + hashlib.sha256(data).hexdigest()
            )
        async with get_session_factory()() as session:
            with pytest.raises(AppError) as raised:
                await register_staged_upload(
                    session,
                    tenant_id=tenant.tenant_id,
                    user_id=tenant.platform_user_id,
                    entry_id=entry,
                    storage_key=key,
                    kind=overrides.pop("kind", "DOCUMENT"),
                    media_type=overrides.pop("media_type", "application/pdf"),
                    checksum=checksum,
                    filename=overrides.pop("filename", "报告.pdf"),
                    size=overrides.pop("size", len(data)),
                    limits=limits,
                )
        return raised.value

    assert (await attempt(storage_key="../escape/1")).code == ErrorCode.COMMON_VALIDATION_ERROR
    assert (await attempt(storage_key="/absolute/1", write_file=False)).code == ErrorCode.COMMON_VALIDATION_ERROR
    assert (await attempt(storage_key="", write_file=False)).code == ErrorCode.COMMON_VALIDATION_ERROR
    assert (await attempt(checksum="sha256:NOT-A-DIGEST")).code == ErrorCode.COMMON_VALIDATION_ERROR
    assert (await attempt(kind="VIDEO")).code == ErrorCode.COMMON_VALIDATION_ERROR
    assert (await attempt(media_type="not-a-mime")).code == ErrorCode.COMMON_VALIDATION_ERROR
    assert (await attempt(size=0)).code == ErrorCode.COMMON_VALIDATION_ERROR
    assert (await attempt(size=len(DOC_BYTES) + 1)).code == ErrorCode.ATTACHMENT_TOO_LARGE

    # 恰好等于上限是允许的（边界值语义与既有附件门控一致）
    exact = await _register(
        tenant, root, entry_id=entry, limits=limits, size=len(DOC_BYTES), data=DOC_BYTES
    )
    assert exact.size == len(DOC_BYTES)
    async with get_session_factory()() as session:
        total = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ChannelUpload)
            .where(ChannelUpload.tenant_id == tenant.tenant_id)
        )
    assert int(total or 0) == 1, "拒绝路径不得留下登记行"


async def test_duplicate_registration_conflicts_but_other_tenants_reuse_the_key(
    staging_tenant: tuple[TenantContext, Path],
) -> None:
    tenant, root = staging_tenant
    entry = uuid.uuid4()
    key = f"webchat/staging/{uuid.uuid4().hex}"
    await _register(tenant, root, entry_id=entry, storage_key=key)

    async with get_session_factory()() as session:
        with pytest.raises(AppError) as raised:
            await register_staged_upload(
                session,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                entry_id=entry,
                storage_key=key,
                kind="DOCUMENT",
                media_type="application/pdf",
                checksum=DOC_CHECKSUM,
                filename="报告.pdf",
                size=len(DOC_BYTES),
                limits=_limits(),
            )
    assert raised.value.code == ErrorCode.COMMON_CONFLICT, "同租户活跃暂存键重复登记必须显式冲突"

    # 活跃唯一是 (tenant_id, storage_key)：另一个租户的同一键不冲突
    foreign = TenantContext(
        tenant_id=f"test-{uuid.uuid4()}", agent_id=uuid.uuid4(), platform_user_id=uuid.uuid4()
    )
    try:
        foreign_row = await _register(foreign, root, entry_id=uuid.uuid4(), storage_key=key)
        assert foreign_row.tenant_id == foreign.tenant_id
    finally:
        async with get_session_factory()() as session:
            for table in PURGE_ORDER:
                await session.execute(
                    sa.text(f"DELETE FROM runtime.{table} WHERE tenant_id = :tenant_id"),
                    {"tenant_id": foreign.tenant_id},
                )
            await session.commit()


async def test_upload_limits_follow_webchat_settings(
    staging_tenant: tuple[TenantContext, Path],
) -> None:
    _, _ = staging_tenant
    limits = upload_limits_from_settings(
        {"webchat": {"upload_max_bytes": 1024, "upload_max_per_message": 2, "upload_staging_ttl_sec": 120}}
    )
    assert limits == ChannelUploadLimits(
        upload_max_bytes=1024, upload_max_per_message=2, staging_ttl_sec=120
    )
    defaults = upload_limits_from_settings(None)
    assert defaults.upload_max_bytes == 50 * 1024 * 1024
    assert defaults.upload_max_per_message == 5
    assert defaults.staging_ttl_sec == 86_400


# --------------------------------------------------------------------------------------
# B-02：受理核属（未授权/过期/数量/空正文/纯附件）
# --------------------------------------------------------------------------------------


async def test_admission_rejects_cross_tenant_forbidden_and_soft_deleted(
    staging_tenant: tuple[TenantContext, Path],
) -> None:
    tenant, root = staging_tenant
    entry = uuid.uuid4()
    upload = await _register(tenant, root, entry_id=entry)

    # 跨租户：不泄露存在性
    async with get_session_factory()() as session:
        with pytest.raises(AppError) as raised:
            await load_owned_upload(
                session,
                tenant_id=f"test-{uuid.uuid4()}",
                user_id=tenant.platform_user_id,
                entry_id=entry,
                upload_id=upload.id,
            )
    assert raised.value.code == ErrorCode.COMMON_NOT_FOUND

    # 同租户越权用户 / 借其他入口：显式拒绝
    async with get_session_factory()() as session:
        with pytest.raises(AppError) as raised:
            await load_owned_upload(
                session,
                tenant_id=tenant.tenant_id,
                user_id=uuid.uuid4(),
                entry_id=entry,
                upload_id=upload.id,
            )
    assert raised.value.code == ErrorCode.FORBIDDEN
    async with get_session_factory()() as session:
        with pytest.raises(AppError) as raised:
            await load_owned_upload(
                session,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                entry_id=uuid.uuid4(),
                upload_id=upload.id,
            )
    assert raised.value.code == ErrorCode.FORBIDDEN

    # 已软删（清理后）视同不存在
    await _soft_delete_upload(upload.id)
    async with get_session_factory()() as session:
        with pytest.raises(AppError) as raised:
            await load_owned_upload(
                session,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                entry_id=entry,
                upload_id=upload.id,
            )
    assert raised.value.code == ErrorCode.COMMON_NOT_FOUND


async def test_admission_rejects_expired_count_and_empty_content(
    staging_tenant: tuple[TenantContext, Path],
) -> None:
    tenant, root = staging_tenant
    entry = uuid.uuid4()
    limits = _limits(upload_max_per_message=2)

    # 空正文且无附件：显式校验失败；纯附件（无正文）放行
    error = await _rejects(
        _resolve(tenant, entry_id=entry, upload_ids=[], text="   ", limits=limits)
    )
    assert error.code == ErrorCode.COMMON_VALIDATION_ERROR

    # 数量边界：恰好等于上限放行，超一个显式拒绝
    uploads = [
        await _register(tenant, root, entry_id=entry, storage_key=f"webchat/staging/{i}")
        for i in range(3)
    ]
    accepted = await _resolve(
        tenant, entry_id=entry, upload_ids=[u.id for u in uploads[:2]], text="", limits=limits
    )
    assert [row.id for row in accepted] == [u.id for u in uploads[:2]], "纯附件消息放行"
    error = await _rejects(
        _resolve(tenant, entry_id=entry, upload_ids=[u.id for u in uploads], limits=limits)
    )
    assert error.code == ErrorCode.ATTACHMENT_COUNT_EXCEEDED

    # 重复 upload_id 是非法输入（与 API-06 契约一致）
    error = await _rejects(
        _resolve(tenant, entry_id=entry, upload_ids=[uploads[0].id, uploads[0].id], limits=limits)
    )
    assert error.code == ErrorCode.COMMON_VALIDATION_ERROR

    # 过期只阻止新受理
    await _expire_staging(uploads[0].id)
    error = await _rejects(_resolve(tenant, entry_id=entry, upload_ids=[uploads[0].id], limits=limits))
    assert error.code == ErrorCode.UPLOAD_EXPIRED
    fresh = await _resolve(tenant, entry_id=entry, upload_ids=[uploads[1].id], limits=limits)
    assert fresh[0].id == uploads[1].id


# --------------------------------------------------------------------------------------
# B-253：受理创建 Run 自己的不可变 storage_key（真实 RunService）
# --------------------------------------------------------------------------------------


async def test_run_admission_publishes_its_own_immutable_artifact(
    staging_tenant: tuple[TenantContext, Path],
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    execution_supervisor_boundary: Any,
) -> None:
    tenant, root = staging_tenant
    entry = uuid.uuid4()
    conversation_id = await _seed_conversation(tenant, entry_id=entry)
    upload = await _register(tenant, root, entry_id=entry)

    started = await _start_run(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(tenant, conversation_id=conversation_id, entry_id=entry, upload_ids=[upload.id]),
        root=root,
    )
    await _drain(started)

    rows = await _artifact_rows(tenant.tenant_id)
    assert len(rows) == 1
    artifact = rows[0]
    run_key = run_artifact_storage_key(run_id=started.run_id, upload_id=upload.id)
    assert artifact.run_id == started.run_id
    assert artifact.storage_key == run_key, "受理创建 Run 自己的 storage_key，而不是复用暂存键"
    assert artifact.storage_key != upload.storage_key
    assert artifact.checksum == DOC_CHECKSUM
    assert artifact.artifact_type == "INBOUND_DOCUMENT"
    assert artifact.media_type == "application/pdf"
    assert artifact.size == len(DOC_BYTES)
    assert artifact.metadata_json["upload_id"] == str(upload.id)
    assert artifact.metadata_json["filename"] == "报告.pdf"
    assert artifact.metadata_json["source"] == "WEBCHAT"

    # 内容与校验和不可变：Run 键的字节 == 暂存原始字节；同键二次写入被拒（FileExistsError）
    run_path = root / run_key
    assert run_path.read_bytes() == DOC_BYTES
    assert "sha256:" + hashlib.sha256(run_path.read_bytes()).hexdigest() == artifact.checksum
    with pytest.raises(FileExistsError):
        NfsArtifactStore(root).write(run_key, b"tampered")

    # 登记行在受理后不被改写（上传引用不可变）
    reloaded = await _read_upload(upload.id)
    assert reloaded.storage_key == upload.storage_key
    assert reloaded.checksum == upload.checksum
    assert reloaded.user_id == upload.user_id
    assert reloaded.entry_id == upload.entry_id
    assert reloaded.expires_at == upload.expires_at

    # 事件载荷只带安全摘要（含 artifact_id），不带存储键
    async with get_session_factory()() as session:
        payload = await session.scalar(
            sa.text(
                "SELECT payload_json FROM runtime.canonical_event"
                " WHERE tenant_id = :tenant_id AND run_id = :run_id AND event_type = 'USER_MESSAGE'"
            ),
            {"tenant_id": tenant.tenant_id, "run_id": started.run_id},
        )
    assert payload is not None
    attachments = payload["attachments"]
    assert [item["artifact_id"] for item in attachments] == [str(artifact.id)]
    assert all("storage_key" not in item for item in attachments)


async def test_two_runs_get_independent_immutable_copies_of_the_same_upload(
    staging_tenant: tuple[TenantContext, Path],
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    execution_supervisor_boundary: Any,
) -> None:
    tenant, root = staging_tenant
    entry = uuid.uuid4()
    conversation_id = await _seed_conversation(tenant, entry_id=entry)
    upload = await _register(tenant, root, entry_id=entry)

    first = await _start_run(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(tenant, conversation_id=conversation_id, entry_id=entry, upload_ids=[upload.id]),
        root=root,
    )
    await _drain(first)
    second = await _start_run(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(tenant, conversation_id=conversation_id, entry_id=entry, upload_ids=[upload.id]),
        root=root,
    )
    await _drain(second)

    rows = await _artifact_rows(tenant.tenant_id)
    assert len(rows) == 2
    keys = {row.storage_key for row in rows}
    assert len(keys) == 2, "同一上传被多个 Run 引用时各自独立"
    first_key = run_artifact_storage_key(run_id=first.run_id, upload_id=upload.id)
    second_key = run_artifact_storage_key(run_id=second.run_id, upload_id=upload.id)
    assert keys == {first_key, second_key}
    assert {row.checksum for row in rows} == {DOC_CHECKSUM}
    assert (root / first_key).read_bytes() == DOC_BYTES
    assert (root / second_key).read_bytes() == DOC_BYTES

    # 某一 Run 的制品被清理不删其他 Run 的字节
    (root / first_key).unlink()
    assert not (root / first_key).exists()
    assert (root / second_key).read_bytes() == DOC_BYTES


async def test_staging_cleanup_never_deletes_accepted_run_artifacts(
    staging_tenant: tuple[TenantContext, Path],
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    execution_supervisor_boundary: Any,
) -> None:
    tenant, root = staging_tenant
    entry = uuid.uuid4()
    conversation_id = await _seed_conversation(tenant, entry_id=entry)
    accepted = await _register(tenant, root, entry_id=entry)
    unclaimed = await _register(
        tenant, root, entry_id=entry, storage_key=f"webchat/staging/{uuid.uuid4().hex}"
    )
    started = await _start_run(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(
            tenant, conversation_id=conversation_id, entry_id=entry, upload_ids=[accepted.id]
        ),
        root=root,
    )
    await _drain(started)
    run_key = run_artifact_storage_key(run_id=started.run_id, upload_id=accepted.id)

    # 两个暂存行都过期：已受理引用的与未归属的
    await _expire_staging(accepted.id)
    await _expire_staging(unclaimed.id)
    async with get_session_factory()() as session:
        removed = await cleanup_expired_staging(session, artifact_root=root)
        await session.commit()
    assert removed == 2

    # 暂存行软删、暂存文件删除；已受理 Run 制品字节完好
    assert (await _read_upload(accepted.id)).is_deleted is True
    assert (await _read_upload(unclaimed.id)).is_deleted is True
    assert not (root / accepted.storage_key).exists()
    assert not (root / unclaimed.storage_key).exists()
    assert (root / run_key).read_bytes() == DOC_BYTES, "暂存清理不能删除已受理制品"
    async with get_session_factory()() as session:
        again = await cleanup_expired_staging(session, artifact_root=root)
        await session.commit()
    assert again == 0, "清理幂等：已软删的行不再被重复计数"


async def test_replay_after_staging_expiry_returns_the_original_run(
    staging_tenant: tuple[TenantContext, Path],
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    execution_supervisor_boundary: Any,
) -> None:
    tenant, root = staging_tenant
    entry = uuid.uuid4()
    conversation_id = await _seed_conversation(tenant, entry_id=entry)
    upload = await _register(tenant, root, entry_id=entry)
    message_id = f"msg-{uuid.uuid4()}"

    first = await _start_run(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(
            tenant,
            conversation_id=conversation_id,
            entry_id=entry,
            upload_ids=[upload.id],
            message_id=message_id,
        ),
        root=root,
        key="idem-b253-replay",
    )
    await _drain(first)

    # 暂存过期且字节已被清理：已受理提交的重放不重新依赖暂存
    await _expire_staging(upload.id)
    (root / upload.storage_key).unlink()
    replay = await _start_run(
        tenant,
        fake_resolve,
        executor_factory,
        execution_supervisor_boundary,
        _request(
            tenant,
            conversation_id=conversation_id,
            entry_id=entry,
            upload_ids=[upload.id],
            message_id=message_id,
        ),
        root=root,
        key="idem-b253-replay",
    )
    await _drain(replay)
    assert replay.run_id == first.run_id, "同键同指纹重放必须返回原 Run"
    assert await _run_count(tenant.tenant_id) == 1
    assert len(await _artifact_rows(tenant.tenant_id)) == 1

    # 未受理的新提交才校验暂存 TTL：过期上传明确拒绝且不留 Run/制品
    error = await _rejects(
        _start_run(
            tenant,
            fake_resolve,
            executor_factory,
            execution_supervisor_boundary,
            _request(
                tenant,
                conversation_id=conversation_id,
                entry_id=entry,
                upload_ids=[upload.id],
                message_id=f"msg-{uuid.uuid4()}",
            ),
            root=root,
            key="idem-b253-new",
        )
    )
    assert error.code == ErrorCode.UPLOAD_EXPIRED
    assert await _run_count(tenant.tenant_id) == 1
    assert len(await _artifact_rows(tenant.tenant_id)) == 1


async def test_missing_or_tampered_staged_bytes_fail_explicitly_without_side_effects(
    staging_tenant: tuple[TenantContext, Path],
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    execution_supervisor_boundary: Any,
) -> None:
    tenant, root = staging_tenant
    entry = uuid.uuid4()
    conversation_id = await _seed_conversation(tenant, entry_id=entry)

    missing = await _register(tenant, root, entry_id=entry)
    (root / missing.storage_key).unlink()
    error = await _rejects(
        _start_run(
            tenant,
            fake_resolve,
            executor_factory,
            execution_supervisor_boundary,
            _request(
                tenant, conversation_id=conversation_id, entry_id=entry, upload_ids=[missing.id]
            ),
            root=root,
        )
    )
    assert error.code == ErrorCode.UPLOAD_EXPIRED

    tampered = await _register(tenant, root, entry_id=entry)
    (root / tampered.storage_key).write_bytes(b"different bytes than registered")
    error = await _rejects(
        _start_run(
            tenant,
            fake_resolve,
            executor_factory,
            execution_supervisor_boundary,
            _request(
                tenant, conversation_id=conversation_id, entry_id=entry, upload_ids=[tampered.id]
            ),
            root=root,
        )
    )
    assert error.code == ErrorCode.COMMON_CONFLICT

    assert await _run_count(tenant.tenant_id) == 0, "失败路径不得留下 Run"
    assert await _artifact_rows(tenant.tenant_id) == []
    assert not (root / "runs").exists() or not any((root / "runs").rglob("*")), (
        "失败路径不得留下 Run 制品文件"
    )


async def test_unattributable_entry_cannot_admit_staged_uploads(
    staging_tenant: tuple[TenantContext, Path],
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    execution_supervisor_boundary: Any,
) -> None:
    """入口不可归属（bot_id 非 UUID 形状）时不能承载暂存上传：显式校验失败，不静默丢内容。"""
    tenant, root = staging_tenant
    entry = uuid.uuid4()
    conversation_id = await _seed_conversation(tenant, entry_id=None)
    upload = await _register(tenant, root, entry_id=entry)
    request = _request(
        tenant, conversation_id=conversation_id, entry_id=entry, upload_ids=[upload.id]
    )
    request = request.model_copy(
        update={"channel": request.channel.model_copy(update={"bot_id": "bot-wecom"})}
    )
    error = await _rejects(
        _start_run(
            tenant,
            fake_resolve,
            executor_factory,
            execution_supervisor_boundary,
            request,
            root=root,
        )
    )
    assert error.code == ErrorCode.COMMON_VALIDATION_ERROR
    assert await _run_count(tenant.tenant_id) == 0
