"""B-258 / TASK-058：Runtime 暂存上传登记的受信端口（design §3.3 数据设计 / §3.4 接口设计）。

真实边界（不 mock 存储或租约）：

- **真实 Runtime HTTP 应用**：真实路由与 `require_internal_service` 服务身份门控（`api/webchat_history.py`
  的 `/internal/webchat/uploads`，与 API-09/14/15 历史端点同一路由面共存）；
- **真实 PostgreSQL**：隔离租户的 `runtime.channel_upload` / `artifact` / `run_record` /
  `conversation` 行，以及 Console 拥有的 `control.bot_account` / `control.agent_definition` /
  `control.model_definition` 行（入口/Agent 核属读同一 PG 的当前事实）；只增删本租户的行；
- **真实共享文件系统**：隔离 `ARTIFACT_ROOT` 下真实暂存字节与 Run 制品硬链接；
- **真实 `RunService` 受理路径**（`POST /v1/runs`）：核属/TTL/发布走真实代码，不替换存储或租约；
  只有模型调用与定义解析是进程内替身。

关键断言（B-258 契约逐项）：

1. **仅经服务门控登记**：缺失/错误 `X-Internal-Service` 一律 403 且不落任何行；`user_id`/`agent_id`/
   `entry_id` 必须由受信调用方显式携带——浏览器自填身份没有位置；
2. **核当前用户/入口/Agent**：入口必须是本租户**启用中**、绑定该 Agent 的 `control.bot_account`
   行；跨租户/不存在/已删除 ⇒ `COMMON_NOT_FOUND`（不泄露存在性），绑定其他 Agent/已停用 ⇒ `FORBIDDEN`；
3. **不可变存储键**：系统生成的相对键形状受控（空串/绝对路径/反斜杠/`.`/`..` 段一律拒绝）；
   同租户活跃键重复登记 ⇒ `COMMON_CONFLICT` 且原登记行逐字段不变；登记只落 `channel_upload` 行，
   **不预造** artifact 行，响应只回权威元信息与 `upload_id`（无 storage_key、无 artifact 引用位置）；
4. **动态限额**：单文件上限与暂存 TTL 来自 `webchat` 平台设置（Console 权威），恰等于上限放行；
5. **登记后可被真实受理路径使用**：归属不符 `FORBIDDEN`、跨租户/伪造 upload_id `COMMON_NOT_FOUND`、
   过期 `UPLOAD_EXPIRED`；成功受理发布 Run 自己的不可变键（≠暂存键）。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_runtime.api.deps import get_platform_settings_client
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Artifact, ChannelUpload, Conversation
from muad_common import SharedSettings
from muad_contracts.webchat import WebchatUpload

from agent_runtime.conftest import CLEANUP_ORDER, TenantContext

pytestmark = pytest.mark.integration

UPLOADS_PATH = "/internal/webchat/uploads"
RUNS_PATH = "/v1/runs"
DOC_BYTES = b"%PDF-1.7 staged upload registered through the trusted runtime port"
DOC_CHECKSUM = "sha256:" + hashlib.sha256(DOC_BYTES).hexdigest()
#: 清理顺序：暂存登记/制品/入口指针先于 run_record/conversation（同 Owner 物理 FK）。
RUNTIME_PURGE_ORDER = ("channel_upload", "artifact", "channel_conversation", *CLEANUP_ORDER)
#: Console 拥有的行（入口核属的真实事实）：bot_account → agent_definition → model_definition。
CONTROL_PURGE_ORDER = ("bot_account", "agent_definition", "model_definition")
#: 登记行不可变的字段（重复登记/受理都不得改写）。
IMMUTABLE_UPLOAD_FIELDS = (
    "tenant_id",
    "user_id",
    "entry_id",
    "storage_key",
    "kind",
    "media_type",
    "checksum",
    "filename",
    "size",
    "expires_at",
    "create_time",
    "is_deleted",
)


# --------------------------------------------------------------------------------------
# 隔离租户 + 隔离 artifact root + 真实 Console 行种子
# --------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_artifact_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """真实文件系统但隔离根目录：生产装配（`SharedSettings.artifact_root`）在请求内取用。"""
    root = tmp_path / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACT_ROOT", str(root))
    return root


async def _purge_tenant(tenant_id: str) -> None:
    async with get_session_factory()() as session:
        for table in RUNTIME_PURGE_ORDER:
            await session.execute(
                sa.text(f"DELETE FROM runtime.{table} WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        for table in CONTROL_PURGE_ORDER:
            await session.execute(
                sa.text(f"DELETE FROM control.{table} WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        await session.commit()


@pytest.fixture
async def upload_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    try:
        yield tenant
    finally:
        await _purge_tenant(tenant.tenant_id)


async def _ensure_agent(tenant_id: str, agent_id: uuid.UUID) -> None:
    """真实 Agent 定义链（`agent_definition.model_id` 是物理 FK，先建模型定义）；幂等。"""
    async with get_session_factory()() as session:
        exists = await session.scalar(
            sa.text(
                "SELECT 1 FROM control.agent_definition"
                " WHERE id = :agent_id AND tenant_id = :tenant_id AND is_deleted = false"
            ),
            {"agent_id": agent_id, "tenant_id": tenant_id},
        )
        if exists is not None:
            return
        model_id = uuid.uuid4()
        await session.execute(
            sa.text(
                "INSERT INTO control.model_definition (id, tenant_id, key, name, model_id)"
                " VALUES (:model_id, :tenant_id, :key, '模型', 'gpt-4o-mini')"
            ),
            {"model_id": model_id, "tenant_id": tenant_id, "key": f"model-{uuid.uuid4().hex[:12]}"},
        )
        await session.execute(
            sa.text(
                "INSERT INTO control.agent_definition"
                " (id, tenant_id, key, name, instructions, model_id)"
                " VALUES (:agent_id, :tenant_id, :key, 'Agent', 'help the user', :model_id)"
            ),
            {
                "agent_id": agent_id,
                "tenant_id": tenant_id,
                "key": f"agent-{uuid.uuid4().hex[:12]}",
                "model_id": model_id,
            },
        )
        await session.commit()


async def _seed_entry(
    tenant_id: str,
    *,
    agent_id: uuid.UUID,
    enabled: bool = True,
    is_deleted: bool = False,
) -> uuid.UUID:
    """真实 WEBCHAT 入口（`control.bot_account`）：入口键全局唯一，按租户/Agent 归属。"""
    await _ensure_agent(tenant_id, agent_id)
    entry_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO control.bot_account"
                " (id, tenant_id, channel, name, bot_id, agent_id, enabled, is_deleted)"
                " VALUES (:entry_id, :tenant_id, 'WEBCHAT', :name, :bot_id, :agent_id, :enabled,"
                " :is_deleted)"
            ),
            {
                "entry_id": entry_id,
                "tenant_id": tenant_id,
                "name": "网页入口",
                "bot_id": f"webchat-{uuid.uuid4().hex}",
                "agent_id": agent_id,
                "enabled": enabled,
                "is_deleted": is_deleted,
            },
        )
        await session.commit()
    return entry_id


async def _seed_conversation(
    tenant: TenantContext, *, entry_id: uuid.UUID, user_id: uuid.UUID | None = None
) -> uuid.UUID:
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        user_id=user_id or tenant.platform_user_id,
        agent_id=tenant.agent_id,
        status="ACTIVE",
        last_seq=0,
        channel_account_id=entry_id,
        last_message_at=sa.func.now(),
    )
    async with get_session_factory()() as session:
        session.add(conversation)
        await session.commit()
    return conversation.id


def _write_staged_bytes(root: Path, storage_key: str, data: bytes = DOC_BYTES) -> str:
    path = root / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return "sha256:" + hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------------------
# HTTP 驱动（真实应用 + 真实服务身份门控）
# --------------------------------------------------------------------------------------


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant.tenant_id,
        "X-Internal-Service": SharedSettings().internal_service_token or "",
    }


_MISSING_HEADER = object()


def _gate_headers(tenant: TenantContext, token: str | None | object = _MISSING_HEADER) -> dict[str, str]:
    """受信身份头：`token` 传字符串即覆盖，传 `None` 即**完全省略**该头（门控拒绝路径）。"""
    headers = {"X-Tenant-Id": tenant.tenant_id}
    if token is None:
        return headers
    headers["X-Internal-Service"] = (
        token if isinstance(token, str) else (SharedSettings().internal_service_token or "")
    )
    return headers


def _upload_body(
    tenant: TenantContext,
    *,
    entry_id: uuid.UUID,
    storage_key: str,
    agent_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "user_id": str(user_id or tenant.platform_user_id),
        "agent_id": str(agent_id or tenant.agent_id),
        "entry_id": str(entry_id),
        "storage_key": storage_key,
        "kind": "DOCUMENT",
        "media_type": "application/pdf",
        "checksum": DOC_CHECKSUM,
        "filename": "报告.pdf",
        "size": len(DOC_BYTES),
    }
    body.update(overrides)
    return body


async def _post_upload(
    client: AsyncClient,
    tenant: TenantContext,
    body: dict[str, Any],
    *,
    token: str | None | object = _MISSING_HEADER,
) -> httpx.Response:
    return await client.post(UPLOADS_PATH, headers=_gate_headers(tenant, token), json=body)


async def _register(
    client: AsyncClient,
    tenant: TenantContext,
    root: Path,
    *,
    entry_id: uuid.UUID,
    storage_key: str | None = None,
    data: bytes = DOC_BYTES,
    **overrides: Any,
) -> uuid.UUID:
    """真实流程：先写共享字节，再经受信端口登记，返回 `upload_id`。"""
    key = storage_key or f"webchat/staging/{uuid.uuid4().hex}"
    checksum = _write_staged_bytes(root, key, data)
    body = _upload_body(tenant, entry_id=entry_id, storage_key=key, checksum=checksum, **overrides)
    response = await _post_upload(client, tenant, body)
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["data"]["upload_id"])


class _UploadSettings:
    """平台设置源（Console 权威）：容量足够大 + 可注入 `webchat` 分组（限额/TTL 边界）。"""

    def __init__(self, webchat: dict[str, Any] | None = None) -> None:
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


def _install_settings(**webchat: Any) -> None:
    from muad_agent_runtime.main import app

    app.dependency_overrides[get_platform_settings_client] = lambda: _UploadSettings(webchat or None)


# --------------------------------------------------------------------------------------
# 真实 PG 读取（只读本租户的行）
# --------------------------------------------------------------------------------------


async def _upload_row(upload_id: uuid.UUID) -> ChannelUpload:
    async with get_session_factory()() as session:
        row = await session.get(ChannelUpload, upload_id)
        assert row is not None
        return row


async def _upload_count(tenant_id: str) -> int:
    async with get_session_factory()() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ChannelUpload)
            .where(ChannelUpload.tenant_id == tenant_id)
        )
    return int(count or 0)


async def _artifact_rows(tenant_id: str) -> list[Artifact]:
    async with get_session_factory()() as session:
        return list(
            (
                await session.execute(
                    sa.select(Artifact).where(Artifact.tenant_id == tenant_id)
                )
            ).scalars()
        )


async def _expire_upload(upload_id: uuid.UUID) -> None:
    """把暂存行推到过期（`create_time` 一并后退以维持 `expires_at > create_time` 约束）。"""
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


async def _snapshot(row: ChannelUpload) -> dict[str, Any]:
    return {field: getattr(row, field) for field in IMMUTABLE_UPLOAD_FIELDS}


# --------------------------------------------------------------------------------------
# B-258：仅经服务门控登记
# --------------------------------------------------------------------------------------


async def test_b258_registration_is_only_reachable_through_the_service_gate(
    client: AsyncClient, upload_tenant: TenantContext, isolated_artifact_root: Path
) -> None:
    tenant = upload_tenant
    entry = await _seed_entry(tenant.tenant_id, agent_id=tenant.agent_id)
    key = f"webchat/staging/{uuid.uuid4().hex}"
    _write_staged_bytes(isolated_artifact_root, key)
    body = _upload_body(tenant, entry_id=entry, storage_key=key)

    for token in (None, "", "wrong-token"):
        denied = await _post_upload(client, tenant, body, token=token)
        assert denied.status_code == 403, denied.text
        assert denied.json()["code"] == "FORBIDDEN"
    assert await _upload_count(tenant.tenant_id) == 0, "门控拒绝不得留下登记行"
    assert await _artifact_rows(tenant.tenant_id) == []

    accepted = await _post_upload(client, tenant, body)
    assert accepted.status_code == 200, accepted.text
    assert await _upload_count(tenant.tenant_id) == 1


async def test_b258_registration_requires_the_trusted_identity_explicitly(
    client: AsyncClient, upload_tenant: TenantContext, isolated_artifact_root: Path
) -> None:
    tenant = upload_tenant
    entry = await _seed_entry(tenant.tenant_id, agent_id=tenant.agent_id)
    key = f"webchat/staging/{uuid.uuid4().hex}"
    _write_staged_bytes(isolated_artifact_root, key)
    base = _upload_body(tenant, entry_id=entry, storage_key=key)

    for missing in ("user_id", "agent_id", "entry_id", "storage_key", "size"):
        incomplete = {name: value for name, value in base.items() if name != missing}
        denied = await _post_upload(client, tenant, incomplete)
        assert denied.status_code == 422, (missing, denied.text)
        assert denied.json()["code"] == "COMMON_VALIDATION_ERROR"

    # 预造制品引用没有位置：登记端口只认暂存登记元信息，不认 artifact/upload 引用。
    for injected in ("artifact_id", "upload_id"):
        denied = await _post_upload(client, tenant, {**base, injected: str(uuid.uuid4())})
        assert denied.status_code == 422, (injected, denied.text)
    assert await _upload_count(tenant.tenant_id) == 0
    assert await _artifact_rows(tenant.tenant_id) == []


# --------------------------------------------------------------------------------------
# B-258：核当前用户/入口/Agent
# --------------------------------------------------------------------------------------


async def test_b258_registration_verifies_the_current_entry_and_agent(
    client: AsyncClient, upload_tenant: TenantContext, isolated_artifact_root: Path
) -> None:
    tenant = upload_tenant
    bound_entry = await _seed_entry(tenant.tenant_id, agent_id=tenant.agent_id)
    other_agent = uuid.uuid4()
    foreign_entry = await _seed_entry(tenant.tenant_id, agent_id=other_agent)
    disabled_entry = await _seed_entry(tenant.tenant_id, agent_id=tenant.agent_id, enabled=False)
    deleted_entry = await _seed_entry(tenant.tenant_id, agent_id=tenant.agent_id, is_deleted=True)
    unknown_entry = uuid.uuid4()
    foreign = TenantContext(
        tenant_id=f"test-{uuid.uuid4()}", agent_id=uuid.uuid4(), platform_user_id=uuid.uuid4()
    )
    cross_tenant_entry = await _seed_entry(foreign.tenant_id, agent_id=foreign.agent_id)

    try:
        # 入口绑定别的 Agent / 入口已停用：显式越权。
        for entry in (foreign_entry, disabled_entry):
            key = f"webchat/staging/{uuid.uuid4().hex}"
            _write_staged_bytes(isolated_artifact_root, key)
            denied = await _post_upload(
                client, tenant, _upload_body(tenant, entry_id=entry, storage_key=key)
            )
            assert denied.status_code == 403, denied.text
            assert denied.json()["code"] == "FORBIDDEN"

        # 未知入口 / 已删除入口 / 跨租户入口：不泄露存在性。
        for entry in (unknown_entry, deleted_entry, cross_tenant_entry):
            key = f"webchat/staging/{uuid.uuid4().hex}"
            _write_staged_bytes(isolated_artifact_root, key)
            denied = await _post_upload(
                client, tenant, _upload_body(tenant, entry_id=entry, storage_key=key)
            )
            assert denied.status_code == 404, denied.text
            assert denied.json()["code"] == "COMMON_NOT_FOUND"

        # 身份字段与入口实际绑定不一致（自报别的 Agent）同样是越权。
        key = f"webchat/staging/{uuid.uuid4().hex}"
        _write_staged_bytes(isolated_artifact_root, key)
        denied = await _post_upload(
            client,
            tenant,
            _upload_body(tenant, entry_id=bound_entry, storage_key=key, agent_id=other_agent),
        )
        assert denied.status_code == 403
        assert await _upload_count(tenant.tenant_id) == 0, "拒绝路径不得留下登记行"

        accepted = await _register(
            client, tenant, isolated_artifact_root, entry_id=bound_entry
        )
        row = await _upload_row(accepted)
        assert row.tenant_id == tenant.tenant_id
        assert row.user_id == tenant.platform_user_id
        assert row.entry_id == bound_entry
    finally:
        await _purge_tenant(foreign.tenant_id)


# --------------------------------------------------------------------------------------
# B-258：登记落权威元信息、不预造制品、不可变存储键
# --------------------------------------------------------------------------------------


async def test_b258_registration_records_authoritative_metadata_without_artifacts(
    client: AsyncClient, upload_tenant: TenantContext, isolated_artifact_root: Path
) -> None:
    tenant = upload_tenant
    entry = await _seed_entry(tenant.tenant_id, agent_id=tenant.agent_id)
    _install_settings(upload_staging_ttl_sec=120)
    key = f"webchat/staging/{uuid.uuid4().hex}"
    checksum = _write_staged_bytes(isolated_artifact_root, key)

    response = await _post_upload(
        client, tenant, _upload_body(tenant, entry_id=entry, storage_key=key, checksum=checksum)
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["code"] == "0"
    upload = WebchatUpload.model_validate(payload["data"])
    assert upload.kind == "DOCUMENT"
    assert upload.media_type == "application/pdf"
    assert upload.size == len(DOC_BYTES)
    assert upload.filename == "报告.pdf"
    assert upload.checksum == checksum
    assert set(payload["data"]) == {
        "upload_id",
        "kind",
        "media_type",
        "size",
        "filename",
        "checksum",
        "expires_at",
    }
    # 响应只回权威元信息与 upload_id：暂存存储键与制品引用都不进公开响应。
    assert "storage_key" not in response.text
    assert "artifact_id" not in response.text

    row = await _upload_row(upload.upload_id)
    assert row.tenant_id == tenant.tenant_id
    assert row.user_id == tenant.platform_user_id
    assert row.entry_id == entry
    assert row.storage_key == key
    assert row.checksum == checksum
    assert row.is_deleted is False
    ttl = (row.expires_at - row.create_time).total_seconds()
    assert 120 <= ttl <= 130, "TTL 来自 webchat.upload_staging_ttl_sec（本用例显式声明 120 秒）"
    assert upload.expires_at == row.expires_at
    assert (isolated_artifact_root / key).read_bytes() == DOC_BYTES, "暂存字节留在共享存储"
    assert await _artifact_rows(tenant.tenant_id) == [], "登记不是 artifact 行（不预造制品）"


async def test_b258_registration_rejects_arbitrary_paths_and_duplicate_keys(
    client: AsyncClient, upload_tenant: TenantContext, isolated_artifact_root: Path
) -> None:
    tenant = upload_tenant
    entry = await _seed_entry(tenant.tenant_id, agent_id=tenant.agent_id)

    for bad_key in ("", "/absolute/path", "../escape", "webchat/../escape", "webchat\\staging"):
        denied = await _post_upload(
            client, tenant, _upload_body(tenant, entry_id=entry, storage_key=bad_key)
        )
        assert denied.status_code == 422, (bad_key, denied.text)
        assert denied.json()["code"] == "COMMON_VALIDATION_ERROR"
    assert await _upload_count(tenant.tenant_id) == 0, "任意路径不得留下登记行"

    key = f"webchat/staging/{uuid.uuid4().hex}"
    upload_id = await _register(client, tenant, isolated_artifact_root, entry_id=entry, storage_key=key)
    before = await _snapshot(await _upload_row(upload_id))

    # 同租户活跃暂存键重复登记：显式冲突，且原登记行逐字段不变（不可变键）。
    _write_staged_bytes(isolated_artifact_root, key, b"tampered bytes")
    duplicated = await _post_upload(
        client,
        tenant,
        _upload_body(
            tenant,
            entry_id=entry,
            storage_key=key,
            checksum="sha256:" + hashlib.sha256(b"tampered bytes").hexdigest(),
            size=len(b"tampered bytes"),
        ),
    )
    assert duplicated.status_code == 409, duplicated.text
    assert duplicated.json()["code"] == "COMMON_CONFLICT"
    assert await _upload_count(tenant.tenant_id) == 1
    assert await _snapshot(await _upload_row(upload_id)) == before, "登记行是不可变的权威元信息"
    assert await _artifact_rows(tenant.tenant_id) == []


async def test_b258_registration_enforces_the_configured_limits(
    client: AsyncClient, upload_tenant: TenantContext, isolated_artifact_root: Path
) -> None:
    tenant = upload_tenant
    entry = await _seed_entry(tenant.tenant_id, agent_id=tenant.agent_id)
    _install_settings(upload_max_bytes=len(DOC_BYTES), upload_max_per_message=2)

    def _key() -> str:
        key = f"webchat/staging/{uuid.uuid4().hex}"
        _write_staged_bytes(isolated_artifact_root, key)
        return key

    rejections: list[tuple[dict[str, Any], int, str]] = [
        ({"size": len(DOC_BYTES) + 1}, 400, "ATTACHMENT_TOO_LARGE"),
        ({"size": 0}, 422, "COMMON_VALIDATION_ERROR"),
        ({"kind": "VIDEO"}, 422, "COMMON_VALIDATION_ERROR"),
        ({"media_type": "not-a-mime"}, 422, "COMMON_VALIDATION_ERROR"),
        ({"checksum": "sha256:NOT-A-DIGEST"}, 422, "COMMON_VALIDATION_ERROR"),
        ({"filename": ""}, 422, "COMMON_VALIDATION_ERROR"),
    ]
    for overrides, status, code in rejections:
        denied = await _post_upload(
            client, tenant, _upload_body(tenant, entry_id=entry, storage_key=_key(), **overrides)
        )
        assert denied.status_code == status, (overrides, denied.text)
        assert denied.json()["code"] == code, overrides
    assert await _upload_count(tenant.tenant_id) == 0, "拒绝路径不得留下登记行"

    # 恰等于上限是允许的（边界值与既有附件门控一致）。
    exact = await _post_upload(
        client,
        tenant,
        _upload_body(tenant, entry_id=entry, storage_key=_key(), size=len(DOC_BYTES)),
    )
    assert exact.status_code == 200, exact.text
    assert await _upload_count(tenant.tenant_id) == 1


# --------------------------------------------------------------------------------------
# B-258：登记后的真实受理（核属/TTL/发布）
# --------------------------------------------------------------------------------------


def _run_payload(
    tenant: TenantContext,
    *,
    conversation_id: uuid.UUID,
    entry_id: uuid.UUID,
    upload_ids: Sequence[uuid.UUID] = (),
    user_id: uuid.UUID | None = None,
    text: str = "看看附件",
) -> dict[str, Any]:
    return {
        "agent_id": str(tenant.agent_id),
        "platform_user_id": str(user_id or tenant.platform_user_id),
        "conversation_id": str(conversation_id),
        "channel": {"type": "WEBCHAT", "bot_id": str(entry_id), "external_user_id": "web-1"},
        "message": {
            "id": f"msg-{uuid.uuid4()}",
            "text": text,
            "upload_ids": [str(upload_id) for upload_id in upload_ids],
        },
    }


async def _submit_run(
    client: AsyncClient, tenant: TenantContext, payload: dict[str, Any]
) -> httpx.Response:
    return await client.post(RUNS_PATH, headers=_headers(tenant), json=payload)


async def test_b258_registered_upload_is_admitted_only_for_its_owner(
    client: AsyncClient, upload_tenant: TenantContext, isolated_artifact_root: Path
) -> None:
    tenant = upload_tenant
    entry = await _seed_entry(tenant.tenant_id, agent_id=tenant.agent_id)
    upload_id = await _register(client, tenant, isolated_artifact_root, entry_id=entry)
    registered = await _snapshot(await _upload_row(upload_id))
    conversation_id = await _seed_conversation(tenant, entry_id=entry)

    accepted = await _submit_run(
        client,
        tenant,
        _run_payload(tenant, conversation_id=conversation_id, entry_id=entry, upload_ids=[upload_id]),
    )
    assert accepted.status_code == 200, accepted.text
    assert "run_id" in accepted.text

    # 受理发布 Run 自己的不可变键（≠暂存键），字节来自真实共享存储。
    artifacts = await _artifact_rows(tenant.tenant_id)
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.storage_key.startswith(f"runs/{artifact.run_id}/attachments/{upload_id}")
    assert artifact.storage_key != (await _upload_row(upload_id)).storage_key
    assert artifact.checksum == DOC_CHECKSUM
    assert (isolated_artifact_root / artifact.storage_key).read_bytes() == DOC_BYTES
    # 受理不改写登记行：上传引用不可变，同消息重投可重复验证。
    assert await _snapshot(await _upload_row(upload_id)) == registered

    # 越权：另一个用户借同一入口提交本上传 ⇒ 显式 FORBIDDEN，不建 Run。
    stranger = uuid.uuid4()
    stranger_conversation = await _seed_conversation(tenant, entry_id=entry, user_id=stranger)
    denied = await _submit_run(
        client,
        tenant,
        _run_payload(
            tenant,
            conversation_id=stranger_conversation,
            entry_id=entry,
            upload_ids=[upload_id],
            user_id=stranger,
        ),
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "FORBIDDEN"

    # 借其他入口提交同一上传 ⇒ 显式 FORBIDDEN。
    other_entry = await _seed_entry(tenant.tenant_id, agent_id=tenant.agent_id)
    other_conversation = await _seed_conversation(tenant, entry_id=other_entry)
    denied = await _submit_run(
        client,
        tenant,
        _run_payload(
            tenant, conversation_id=other_conversation, entry_id=other_entry, upload_ids=[upload_id]
        ),
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "FORBIDDEN"

    # 跨租户与伪造 upload_id：不泄露存在性。
    foreign = TenantContext(
        tenant_id=f"test-{uuid.uuid4()}", agent_id=uuid.uuid4(), platform_user_id=uuid.uuid4()
    )
    try:
        foreign_entry = await _seed_entry(foreign.tenant_id, agent_id=foreign.agent_id)
        foreign_conversation = await _seed_conversation(foreign, entry_id=foreign_entry)
        denied = await _submit_run(
            client,
            foreign,
            _run_payload(
                foreign,
                conversation_id=foreign_conversation,
                entry_id=foreign_entry,
                upload_ids=[upload_id],
            ),
        )
        assert denied.status_code == 404, denied.text
        assert denied.json()["code"] == "COMMON_NOT_FOUND"
    finally:
        await _purge_tenant(foreign.tenant_id)

    fabricated = uuid.uuid4()
    denied = await _submit_run(
        client,
        tenant,
        _run_payload(
            tenant,
            conversation_id=await _seed_conversation(tenant, entry_id=entry),
            entry_id=entry,
            upload_ids=[fabricated],
        ),
    )
    assert denied.status_code == 404
    assert denied.json()["code"] == "COMMON_NOT_FOUND"
    assert len(await _artifact_rows(tenant.tenant_id)) == 1, "拒绝路径不得留下制品"


async def test_b258_expired_registration_blocks_new_admission_only(
    client: AsyncClient, upload_tenant: TenantContext, isolated_artifact_root: Path
) -> None:
    tenant = upload_tenant
    entry = await _seed_entry(tenant.tenant_id, agent_id=tenant.agent_id)
    stale = await _register(client, tenant, isolated_artifact_root, entry_id=entry)
    fresh = await _register(client, tenant, isolated_artifact_root, entry_id=entry)

    await _expire_upload(stale)
    expired_conversation = await _seed_conversation(tenant, entry_id=entry)
    denied = await _submit_run(
        client,
        tenant,
        _run_payload(
            tenant, conversation_id=expired_conversation, entry_id=entry, upload_ids=[stale]
        ),
    )
    assert denied.status_code == 410, denied.text
    assert denied.json()["code"] == "UPLOAD_EXPIRED"
    assert await _artifact_rows(tenant.tenant_id) == []

    # 过期只挡新受理：同一入口下未过期的上传照常受理并发布自己的制品。
    accepted = await _submit_run(
        client,
        tenant,
        _run_payload(
            tenant,
            conversation_id=await _seed_conversation(tenant, entry_id=entry),
            entry_id=entry,
            upload_ids=[fresh],
        ),
    )
    assert accepted.status_code == 200, accepted.text
    artifacts = await _artifact_rows(tenant.tenant_id)
    assert len(artifacts) == 1
    assert artifacts[0].storage_key.endswith(f"/attachments/{fresh}")
    assert (await _upload_row(stale)).expires_at <= (await _upload_row(fresh)).expires_at
