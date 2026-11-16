"""TASK-008：Task 产物写入与不可变版本（design §3.1.5 API-28、§3.4.6、§4.4 item 10）。

真实边界：真实 Runtime 应用（内部服务身份门控 + 真实路由）、真实 PostgreSQL（
`task.task_execution` 与 `runtime.artifact` 都是真实行）、真实共享存储（`NfsArtifactStore`
落临时目录，不 mock）。归属只在 Owner 复核：请求体不带 actor/agent，Runtime 只读
Execution 当前事实。

覆盖 checklist：XOR/无 Run 写入、重复版本请求幂等、跨 tenant/actor/agent、checksum
一致性、缺失对象、存储失败、内部留档不可下载、Run 产物回归。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient, Response
from muad_agent_runtime.api.deps import get_artifact_store
from muad_agent_runtime.application.attachments.task_artifacts import write_task_artifact
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    Conversation,
    RunRecord,
)
from muad_agent_runtime.main import app
from muad_artifact_store import NfsArtifactStore
from muad_contracts import TaskArtifactWriteRequest
from sqlalchemy.exc import OperationalError

from agent_runtime.conftest import CLEANUP_ORDER, TenantContext

pytestmark = pytest.mark.integration

#: 产物行先于 Run 相关表清理（同 Owner 物理 FK），再走套件既有顺序。
PURGE_ORDER = ("artifact", *CLEANUP_ORDER)


def _checksum(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


async def _purge(tenant_id: str) -> None:
    async with get_session_factory()() as session:
        for table in PURGE_ORDER:
            await session.execute(
                sa.text(f"DELETE FROM runtime.{table} WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        await session.execute(
            sa.text("DELETE FROM task.task_execution WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await session.commit()


@pytest.fixture()
async def task_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    try:
        yield tenant
    finally:
        await _purge(tenant.tenant_id)


@pytest.fixture()
def artifact_root(tmp_path: Path) -> AsyncIterator[Path]:
    """共享存储的真实根目录：覆盖依赖后写入/解析都落这个临时目录。"""
    app.dependency_overrides[get_artifact_store] = lambda: NfsArtifactStore(tmp_path)
    try:
        yield tmp_path
    finally:
        app.dependency_overrides.pop(get_artifact_store, None)


async def _seed_execution(
    tenant: TenantContext,
    *,
    tenant_id: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
) -> uuid.UUID:
    execution_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO task.task_execution"
                " (id, tenant_id, agent_id, actor_user_id, trigger_type, status, input_json,"
                "  execution_snapshot_json, snapshot_hash, idempotency_key)"
                " VALUES (:id, :tenant_id, :agent_id, :actor_user_id, 'SCHEDULED', 'COMPLETED',"
                "  '{}'::jsonb, '{}'::jsonb, :snapshot_hash, :idempotency_key)"
            ),
            {
                "id": execution_id,
                "tenant_id": tenant_id or tenant.tenant_id,
                "agent_id": agent_id or tenant.agent_id,
                "actor_user_id": actor_user_id or tenant.platform_user_id,
                "snapshot_hash": "sha256:" + "e" * 64,
                "idempotency_key": f"exec-{execution_id}",
            },
        )
        await session.commit()
    return execution_id


def _body(
    execution_id: uuid.UUID,
    *,
    filename: str,
    data: bytes,
    media_type: str = "text/markdown",
    base_version: int = 0,
    checksum: str | None = None,
    size: int | None = None,
) -> TaskArtifactWriteRequest:
    return TaskArtifactWriteRequest(
        task_execution_id=execution_id,
        filename=filename,
        media_type=media_type,
        size=len(data) if size is None else size,
        checksum=checksum or _checksum(data),
        base_version=base_version,
    )


async def _write(
    client: AsyncClient, tenant_id: str, body: TaskArtifactWriteRequest, data: bytes
) -> Response:
    return await client.post(
        "/internal/artifacts",
        data={"metadata": body.model_dump_json()},
        files={"content": ("content", data, "application/octet-stream")},
        headers={"X-Tenant-Id": tenant_id},
    )


async def _read(
    client: AsyncClient,
    tenant: TenantContext,
    artifact_id: uuid.UUID,
    *,
    version: str,
    checksum: str,
    user_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    tenant_id: str | None = None,
) -> Response:
    return await client.get(
        f"/internal/artifacts/{artifact_id}/versions/{version}",
        params={
            "checksum": checksum,
            "user_id": str(user_id or tenant.platform_user_id),
            "agent_id": str(agent_id or tenant.agent_id),
        },
        headers={"X-Tenant-Id": tenant_id or tenant.tenant_id},
    )


async def _rows(tenant_id: str) -> list[Artifact]:
    async with get_session_factory()() as session:
        return list(
            await session.scalars(sa.select(Artifact).where(Artifact.tenant_id == tenant_id))
        )


def _code(response: Response) -> str:
    return str(response.json()["code"])


def _data(response: Response) -> dict[str, Any]:
    payload = response.json()["data"]
    assert isinstance(payload, dict)
    return payload


def _files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()]


async def test_write_registers_task_owned_version_without_run(
    task_tenant: TenantContext, artifact_root: Path, client: AsyncClient
) -> None:
    """无 Run 写入：task_id=Execution.id、run_id=NULL、键按 tenant/Execution 生成。"""
    execution_id = await _seed_execution(task_tenant)
    body = _body(execution_id, filename="report.md", data=b"hello task")
    response = await _write(client, task_tenant.tenant_id, body, b"hello task")

    assert response.status_code == 200, response.text
    data = _data(response)
    assert data["version"] == "1"
    assert data["checksum"] == body.checksum

    rows = await _rows(task_tenant.tenant_id)
    assert len(rows) == 1
    row = rows[0]
    assert str(row.id) == data["artifact_id"]
    assert row.run_id is None, "Task-owned 行不得挂 Run（XOR）"
    assert row.task_id == execution_id
    assert row.conversation_id is None, "任务产物不要求 Conversation"
    assert row.artifact_type == "TASK_OUTPUT"
    assert row.user_id == task_tenant.platform_user_id
    assert row.agent_id == task_tenant.agent_id
    assert row.storage_key == f"tasks/{task_tenant.tenant_id}/{execution_id}/{row.id}/v1"
    assert row.metadata_json["filename"] == "report.md"
    assert row.metadata_json["version"] == 1
    assert (artifact_root / row.storage_key).read_bytes() == b"hello task"


async def test_replayed_write_returns_original_version(
    task_tenant: TenantContext, artifact_root: Path, client: AsyncClient
) -> None:
    """同一调用的重复请求返回原版本：不重复落盘、不多出版本。"""
    execution_id = await _seed_execution(task_tenant)
    body = _body(execution_id, filename="same.md", data=b"same payload")

    first = await _write(client, task_tenant.tenant_id, body, b"same payload")
    second = await _write(client, task_tenant.tenant_id, body, b"same payload")

    assert first.status_code == 200 and second.status_code == 200
    assert _data(second) == _data(first)
    rows = await _rows(task_tenant.tenant_id)
    assert len(rows) == 1
    assert rows[0].metadata_json["versions"] == []
    assert len(_files(artifact_root)) == 1


async def test_same_name_with_other_content_conflicts(
    task_tenant: TenantContext, artifact_root: Path, client: AsyncClient
) -> None:
    """同 filename 的第二次新建（base_version=0）不是"新版本"，显式冲突且不改动旧字节。"""
    execution_id = await _seed_execution(task_tenant)
    first = await _write(
        client, task_tenant.tenant_id, _body(execution_id, filename="r.md", data=b"one"), b"one"
    )
    conflict = await _write(
        client, task_tenant.tenant_id, _body(execution_id, filename="r.md", data=b"two"), b"two"
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert _code(conflict) == "ARTIFACT_VERSION_CONFLICT"
    rows = await _rows(task_tenant.tenant_id)
    assert len(rows) == 1
    assert (artifact_root / rows[0].storage_key).read_bytes() == b"one"
    assert len(_files(artifact_root)) == 1


async def test_append_versions_are_immutable_and_replayable(
    task_tenant: TenantContext, artifact_root: Path, client: AsyncClient
) -> None:
    """追加版本换新键、旧字节不动；重放幂等；过期 base 显式冲突。"""
    execution_id = await _seed_execution(task_tenant)
    v1 = await _write(
        client, task_tenant.tenant_id, _body(execution_id, filename="log.md", data=b"v1"), b"v1"
    )
    artifact_id = _data(v1)["artifact_id"]
    v2_body = _body(execution_id, filename="log.md", data=b"v2", base_version=1)
    v2 = await _write(client, task_tenant.tenant_id, v2_body, b"v2")
    replay = await _write(client, task_tenant.tenant_id, v2_body, b"v2")
    stale = await _write(
        client,
        task_tenant.tenant_id,
        _body(execution_id, filename="log.md", data=b"v3", base_version=1),
        b"v3",
    )

    assert _data(v2)["version"] == "2"
    assert _data(v2)["artifact_id"] == artifact_id
    assert _data(replay) == _data(v2), "同一调用重放必须返回原版本"
    assert stale.status_code == 409 and _code(stale) == "ARTIFACT_VERSION_CONFLICT"

    rows = await _rows(task_tenant.tenant_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.metadata_json["version"] == 2
    history = row.metadata_json["versions"]
    assert [entry["version"] for entry in history] == [1]
    assert history[0]["checksum"] == _checksum(b"v1")
    assert history[0]["size"] == 2
    assert (artifact_root / history[0]["key"]).read_bytes() == b"v1", "历史版本字节不可变"
    assert (artifact_root / row.storage_key).read_bytes() == b"v2"
    assert len(_files(artifact_root)) == 2


async def test_read_requires_the_exact_version_and_checksum(
    task_tenant: TenantContext, artifact_root: Path, client: AsyncClient
) -> None:
    """按 version+checksum 取指定版本；不匹配就冲突，绝不拿最新版冒充。"""
    execution_id = await _seed_execution(task_tenant)
    await _write(
        client, task_tenant.tenant_id, _body(execution_id, filename="doc.md", data=b"first"), b"first"
    )
    second = await _write(
        client,
        task_tenant.tenant_id,
        _body(execution_id, filename="doc.md", data=b"second", base_version=1),
        b"second",
    )
    artifact_id = uuid.UUID(_data(second)["artifact_id"])
    rows = await _rows(task_tenant.tenant_id)
    v1_entry = rows[0].metadata_json["versions"][0]

    v1 = await _read(
        client, task_tenant, artifact_id, version="1", checksum=v1_entry["checksum"]
    )
    assert v1.status_code == 200, v1.text
    reference = _data(v1)
    assert reference["storage_key"] == v1_entry["key"]
    assert reference["checksum"] == v1_entry["checksum"]
    assert reference["size"] == v1_entry["size"]
    assert (artifact_root / reference["storage_key"]).read_bytes() == b"first"

    wrong_checksum = await _read(
        client, task_tenant, artifact_id, version="1", checksum=_checksum(b"second")
    )
    missing_version = await _read(
        client, task_tenant, artifact_id, version="9", checksum=v1_entry["checksum"]
    )
    assert wrong_checksum.status_code == 409
    assert _code(wrong_checksum) == "ARTIFACT_VERSION_CONFLICT"
    assert missing_version.status_code == 409
    assert _code(missing_version) == "ARTIFACT_VERSION_CONFLICT"
    assert "storage_key" not in wrong_checksum.text


async def test_cross_tenant_user_and_agent_are_not_found(
    task_tenant: TenantContext, artifact_root: Path, client: AsyncClient
) -> None:
    """跨租户写/读、他人用户、他人 Agent 一律 404 且不泄露存在性。"""
    execution_id = await _seed_execution(task_tenant)
    written = await _write(
        client, task_tenant.tenant_id, _body(execution_id, filename="a.md", data=b"a"), b"a"
    )
    artifact_id = uuid.UUID(_data(written)["artifact_id"])
    checksum = _checksum(b"a")
    foreign_tenant = TenantContext(
        tenant_id=f"test-{uuid.uuid4()}", agent_id=uuid.uuid4(), platform_user_id=uuid.uuid4()
    )
    try:
        responses = [
            await _write(
                client,
                foreign_tenant.tenant_id,
                _body(execution_id, filename="a.md", data=b"a"),
                b"a",
            ),
            await _read(
                client,
                foreign_tenant,
                artifact_id,
                version="1",
                checksum=checksum,
                user_id=task_tenant.platform_user_id,
                agent_id=task_tenant.agent_id,
            ),
            await _read(
                client, task_tenant, artifact_id, version="1", checksum=checksum, user_id=uuid.uuid4()
            ),
            await _read(
                client, task_tenant, artifact_id, version="1", checksum=checksum, agent_id=uuid.uuid4()
            ),
        ]
    finally:
        await _purge(foreign_tenant.tenant_id)

    for response in responses:
        assert response.status_code == 404, response.text
        assert _code(response) == "TASK_ARTIFACT_NOT_FOUND"
    signatures = {
        (response.status_code, _code(response), response.json()["msg"]) for response in responses
    }
    assert len(signatures) == 1, "拒绝路径不可分辨（不泄露存在性）"
    assert len(await _rows(task_tenant.tenant_id)) == 1
    assert len(_files(artifact_root)) == 1


async def test_missing_or_damaged_object_is_explicit_not_substituted(
    task_tenant: TenantContext, artifact_root: Path, client: AsyncClient
) -> None:
    """行在、字节不在（或长度不符）→ 显式 404；不拿别的版本顶上。"""
    execution_id = await _seed_execution(task_tenant)
    await _write(
        client, task_tenant.tenant_id, _body(execution_id, filename="m.md", data=b"old"), b"old"
    )
    second = await _write(
        client,
        task_tenant.tenant_id,
        _body(execution_id, filename="m.md", data=b"new!", base_version=1),
        b"new!",
    )
    artifact_id = uuid.UUID(_data(second)["artifact_id"])
    rows = await _rows(task_tenant.tenant_id)
    v1_entry = rows[0].metadata_json["versions"][0]
    target = artifact_root / v1_entry["key"]

    target.unlink()
    missing = await _read(
        client, task_tenant, artifact_id, version="1", checksum=v1_entry["checksum"]
    )
    target.write_bytes(b"x")  # 长度与记录不符：不能按错误长度把字节发出去
    damaged = await _read(
        client, task_tenant, artifact_id, version="1", checksum=v1_entry["checksum"]
    )
    current = await _read(
        client, task_tenant, artifact_id, version="2", checksum=_checksum(b"new!")
    )

    assert missing.status_code == 404 and _code(missing) == "TASK_ARTIFACT_NOT_FOUND"
    assert damaged.status_code == 404 and _code(damaged) == "TASK_ARTIFACT_NOT_FOUND"
    assert current.status_code == 200, "缺失的旧版本不影响其它版本"


async def test_internal_archive_artifact_is_not_downloadable(
    task_tenant: TenantContext, artifact_root: Path, client: AsyncClient
) -> None:
    """内部留档（TOOL_RESULT/TRANSCRIPT）即使挂着 task_id 也不在可下载白名单里。"""
    execution_id = await _seed_execution(task_tenant)
    seeded = [
        (await _seed_artifact(task_tenant, execution_id, artifact_root, "TOOL_RESULT"), b"raw tool"),
        (await _seed_artifact(task_tenant, execution_id, artifact_root, "TRANSCRIPT"), b"raw chat"),
    ]
    responses = [
        await _read(client, task_tenant, artifact_id, version="1", checksum=_checksum(data))
        for artifact_id, data in seeded
    ]
    for response in responses:
        assert response.status_code == 404
        assert _code(response) == "TASK_ARTIFACT_NOT_FOUND"
        assert "raw" not in response.text and "storage_key" not in response.text


async def test_run_owned_artifact_is_not_on_the_task_surface(
    task_tenant: TenantContext, artifact_root: Path, client: AsyncClient
) -> None:
    """Run 产物（task_id 为 NULL）不在这条下载面上：两条面互不顶替。"""
    artifact_id = await _seed_run_artifact(task_tenant, artifact_root)
    response = await _read(
        client, task_tenant, artifact_id, version="1", checksum=_checksum(b"run bytes")
    )
    assert response.status_code == 404
    assert _code(response) == "TASK_ARTIFACT_NOT_FOUND"


async def test_write_requires_internal_service_identity(
    task_tenant: TenantContext,
    artifact_root: Path,
    anonymous_client: AsyncClient,
) -> None:
    """受信身份门控：没有内部服务身份不触达写入。"""
    execution_id = await _seed_execution(task_tenant)
    body = _body(execution_id, filename="a.md", data=b"a")
    response = await anonymous_client.post(
        "/internal/artifacts",
        data={"metadata": body.model_dump_json()},
        files={"content": ("content", b"a", "application/octet-stream")},
        headers={"X-Tenant-Id": task_tenant.tenant_id},
    )
    assert response.status_code == 403
    assert await _rows(task_tenant.tenant_id) == []
    assert _files(artifact_root) == []


async def test_write_rejects_declared_checksum_mismatch(
    task_tenant: TenantContext, artifact_root: Path, client: AsyncClient
) -> None:
    """声明的 checksum/size 与真实字节不符：冲突且不留行、不留文件。"""
    execution_id = await _seed_execution(task_tenant)
    mismatched = _body(execution_id, filename="c.md", data=b"real", checksum=_checksum(b"other"))
    wrong_size = _body(execution_id, filename="s.md", data=b"real", size=99)

    responses = [
        await _write(client, task_tenant.tenant_id, mismatched, b"real"),
        await _write(client, task_tenant.tenant_id, wrong_size, b"real"),
    ]
    for response in responses:
        assert response.status_code == 409
        assert _code(response) == "ARTIFACT_VERSION_CONFLICT"
    assert await _rows(task_tenant.tenant_id) == []
    assert _files(artifact_root) == []


async def test_write_rejects_empty_result(task_tenant: TenantContext, client: AsyncClient) -> None:
    """零字节结果无法被下载引用（AttachmentRef.size>0），在写入口显式拒绝。"""
    execution_id = await _seed_execution(task_tenant)
    response = await _write(
        client, task_tenant.tenant_id, _body(execution_id, filename="empty.md", data=b""), b""
    )
    assert response.status_code == 422
    assert _code(response) == "COMMON_VALIDATION_ERROR"


async def test_write_rejects_malformed_metadata(
    task_tenant: TenantContext, client: AsyncClient
) -> None:
    """元信息不是合法契约 JSON：校验失败，不猜字段。"""
    execution_id = await _seed_execution(task_tenant)
    response = await client.post(
        "/internal/artifacts",
        data={"metadata": json.dumps({"task_execution_id": str(execution_id), "filename": "x"})},
        files={"content": ("content", b"x", "application/octet-stream")},
        headers={"X-Tenant-Id": task_tenant.tenant_id},
    )
    assert response.status_code == 422
    assert _code(response) == "COMMON_VALIDATION_ERROR"


async def test_storage_failure_is_explicit(
    task_tenant: TenantContext, tmp_path: Path, client: AsyncClient
) -> None:
    """存储写失败：显式 500，不落行；不把失败伪装成成功。"""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupied", encoding="utf-8")
    app.dependency_overrides[get_artifact_store] = lambda: NfsArtifactStore(blocked)
    try:
        execution_id = await _seed_execution(task_tenant)
        response = await _write(
            client, task_tenant.tenant_id, _body(execution_id, filename="f.md", data=b"x"), b"x"
        )
    finally:
        app.dependency_overrides.pop(get_artifact_store, None)

    assert response.status_code == 500
    assert _code(response) == "COMMON_INTERNAL_ERROR"
    assert await _rows(task_tenant.tenant_id) == []
    assert blocked.read_text(encoding="utf-8") == "occupied"


class _CommitFails:
    """只破坏 commit 的真实会话代理：其余读写都走真实 PG（模拟元数据落库失败）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def commit(self) -> None:
        raise OperationalError("COMMIT", {}, Exception("db down"))


async def test_metadata_persist_failure_discards_written_file(
    task_tenant: TenantContext, artifact_root: Path
) -> None:
    """行没落成时删除本次写的文件：不留孤儿，也不留指向不存在文件的引用。"""
    execution_id = await _seed_execution(task_tenant)
    body = _body(execution_id, filename="orphan.md", data=b"payload")
    async with get_session_factory()() as session:
        with pytest.raises(OperationalError):
            await write_task_artifact(
                _CommitFails(session),
                tenant_id=task_tenant.tenant_id,
                request=body,
                content=b"payload",
                store=NfsArtifactStore(artifact_root),
            )

    assert _files(artifact_root) == []
    assert await _rows(task_tenant.tenant_id) == []


async def _seed_artifact(
    tenant: TenantContext,
    execution_id: uuid.UUID,
    root: Path,
    artifact_type: str,
    data: bytes = b"seed",
) -> uuid.UUID:
    """直接落一行非本写入方的产物（内部留档），验证下载面的类型白名单。"""
    artifact_id = uuid.uuid4()
    key = f"tools/{tenant.tenant_id}/{execution_id}/{artifact_id}/result.bin"
    target = root / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    async with get_session_factory()() as session:
        session.add(
            Artifact(
                id=artifact_id,
                tenant_id=tenant.tenant_id,
                run_id=None,
                task_id=execution_id,
                conversation_id=None,
                agent_id=tenant.agent_id,
                user_id=tenant.platform_user_id,
                artifact_type=artifact_type,
                storage_key=key,
                media_type="text/plain",
                size=len(data),
                checksum=_checksum(data),
                metadata_json={"filename": "seed.txt", "version": 1, "versions": []},
            )
        )
        await session.commit()
    return artifact_id


async def _seed_run_artifact(tenant: TenantContext, root: Path) -> uuid.UUID:
    """Run 产物（task_id 为 NULL）：证明 Run/Task 两条下载面互不顶替。"""
    data = b"run bytes"
    artifact_id = uuid.uuid4()
    key = f"outbound/{artifact_id}/v1"
    target = root / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    conversation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                status="ACTIVE",
                last_seq=0,
            )
        )
        await session.flush()
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                status="COMPLETED",
                input_text="seed",
                trace_id=f"trace-{run_id}",
            )
        )
        await session.flush()
        session.add(
            Artifact(
                id=artifact_id,
                tenant_id=tenant.tenant_id,
                run_id=run_id,
                task_id=None,
                conversation_id=None,
                agent_id=tenant.agent_id,
                user_id=tenant.platform_user_id,
                artifact_type="TASK_OUTPUT",
                storage_key=key,
                media_type="text/plain",
                size=len(data),
                checksum=_checksum(data),
                metadata_json={"filename": "run.txt", "version": 1, "versions": []},
            )
        )
        await session.commit()
    return artifact_id
