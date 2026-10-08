"""[E-20] 执行默认接入（agent / memory / artifact / locale 9 叶）。

把平台设置里 Run 用到的 9 个叶子接到既有消费点，取值一律来自**本 Run 冻结的** `policy_json`
（`agent.max_turns`/`max_tool_calls`/`max_model_retries`/`deadline_ms`、`memory.*` 5 叶、
`artifact.max_archive_files`、`locale.default_timezone`）；`artifact.retention_days` /
`cleanup_batch_size` 属 Console 清理，在**当次操作边界**从当前平台设置取（清理不是 Run）。

真实边界（业务路径不 mock）：
- **真实 PostgreSQL**：`control.platform_setting` 版本行（经真实 Console `PlatformSettingsService`
  读写）、`runtime.runtime_snapshot.policy_json` 冻结行、`runtime.user_memory` 存量记忆、
  `runtime.artifact` 清理候选；
- **真实 Runtime 装配**：Run 经真实 `RunService.start` 创建并冻结快照，执行期经真实
  `build_registry`（`MemoryToolSet` / `ArchiveToolSet` / `TimeToolSet`）与
  `DbBackedContextBuilder.load_history` 装配（模型调用不在被测范围，用 no-op 执行器替身）；
- **真实 Console 清理入口**：`muad_console_platform.cli._cleanup_artifacts`（真 CLI 命令协程）

「既有 Run/Task 行与已落库记忆不被改写」由逐列回读断言（`test_e20_..._not_rewritten`）。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.attachments.archive_tools import (
    CREATE_ARCHIVE_TOOL,
    ArchiveToolError,
)
from muad_agent_runtime.application.context_builder import DbBackedContextBuilder
from muad_agent_runtime.application.executor import (
    ExecutorEvent,
    ExecutorRequest,
    RunExecutor,
    build_registry,
)
from muad_agent_runtime.application.memory_service import MemoryService
from muad_agent_runtime.application.memory_tools import RECALL_TOOL, REMEMBER_TOOL
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.application.run_service import (
    RunService,
)
from muad_agent_runtime.application.time_tools import CURRENT_TIME_TOOL
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    CanonicalEvent,
    Conversation,
    RunRecord,
    RuntimeSnapshot,
    UserMemory,
)
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.platform_settings_service import PlatformSettingsService
from muad_contracts import ChannelContext, MessageInput, RunRequest
from muad_contracts.platform_settings import (
    AgentSettings,
    ArtifactSettings,
    LocaleSettings,
    MemoryPolicySettings,
)

from agent_runtime.conftest import FakeResolveClient, TenantContext

# ---- 被改动的 9 个叶子的目标值（刻意都不等于 schema 默认，改动才可见） ----
AGENT_OVERRIDE = AgentSettings(max_turns=7, max_tool_calls=4, deadline_ms=90_000, max_model_retries=1)
MEMORY_OVERRIDE = MemoryPolicySettings(
    write_enabled=False,
    max_injected_memories=2,
    max_injected_bytes=2048,
    max_recall_bytes=4096,
    recall_default_limit=2,
)
ARTIFACT_OVERRIDE = ArtifactSettings(retention_days=5, max_archive_files=2, cleanup_batch_size=1)
LOCALE_OVERRIDE = LocaleSettings(default_timezone="America/New_York")
RECALL_SCENARIO = MemoryPolicySettings(
    write_enabled=True,
    max_injected_memories=2,
    max_injected_bytes=2048,
    max_recall_bytes=200,
    recall_default_limit=10,
)

SOURCE_USER_EXPLICIT = "USER_EXPLICIT"


# ---- 平台设置真源：真实 PG 的 control.platform_setting（经真实 Console 服务） ----


class _PgSettingsClient:
    """Runtime 的 `PlatformSettingsClient` 端口实现：直读真实 PG。

    与真实内部 HTTP 端点同一条产码路径（端点就是 `asdict(read_current().settings)`）——HTTP 客户端
    路径已由 TASK-005 的 `test_platform_settings_source.py` 覆盖，本用例只省掉那层 HTTP。
    """

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        async with get_session_factory()() as session:
            snapshot = await PlatformSettingsService(session).read_current(tenant_id)
            return PlatformSettingsSnapshot(revision=snapshot.revision, settings=asdict(snapshot.settings))


async def _save_settings(tenant_id: str, **groups: Any) -> None:
    """用真实 Console `PlatformSettingsService.save` 写一版设置（真实 PG + 真实校验/审计）。"""
    async with get_session_factory()() as session:
        service = PlatformSettingsService(session)
        current = await service.read_current(tenant_id)
        settings = replace(current.settings, **groups)
        await service.save(tenant_id, AuditActor(account_id=uuid.uuid4()), current.revision, settings)
        await session.commit()


# ---- 真实 Runtime：RunService 创建 Run + 捕获执行期请求 ----


class _DrainingExecutor:
    async def run(self) -> AsyncIterator[ExecutorEvent]:
        yield ExecutorEvent(type="message.delta", data={"delta": "ok"})


class _CapturingExecutorFactory:
    """替身只替换**模型调用**这一环：`_create_run`/`_build_executor` 仍是真实代码路径。"""

    def __init__(self) -> None:
        self.requests: list[ExecutorRequest] = []

    async def __call__(self, request: ExecutorRequest) -> RunExecutor:
        self.requests.append(request)
        return _DrainingExecutor()


def _request(tenant: TenantContext, text: str = "hello defaults") -> RunRequest:
    return RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
        channel=ChannelContext(type="WECOM", bot_id="bot-1"),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text=text),
    )


async def _start_run(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    capture: _CapturingExecutorFactory,
    client: _PgSettingsClient,
) -> uuid.UUID:
    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            "instance-e20",
            executor_factory=capture,
            settings_client=client,
            supervisor=ExecutionSupervisor(),
        )
        started = await service.start(_request(tenant), tenant.tenant_id)
        async for _ in started.events:  # 驱动到终态：真实冻结 + 真实装配请求
            pass
        return started.run_id


async def _policy_json(run_id: uuid.UUID) -> dict[str, Any]:
    async with get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None and run.snapshot_id is not None
        snapshot = await session.get(RuntimeSnapshot, run.snapshot_id)
        assert snapshot is not None and snapshot.run_id == run_id
        return snapshot.policy_json


def _cache(tmp_path: Path) -> SkillArtifactCache:
    return SkillArtifactCache(NfsArtifactStore(tmp_path / "artifacts"), tmp_path / "cache")


# ---- E-20 主场景：新 Run 冻结九个叶子 ----


async def test_e20_new_run_freezes_nine_execution_leaves(
    tenant: TenantContext, fake_resolve: FakeResolveClient
) -> None:
    """改 9 个叶子后新建 Run：`policy_json` 逐键冻结为新值（真实 POST 路径 + 真实 PG）。"""
    await _save_settings(
        tenant.tenant_id,
        agent=AGENT_OVERRIDE,
        memory=MEMORY_OVERRIDE,
        artifact=ARTIFACT_OVERRIDE,
        locale=LOCALE_OVERRIDE,
    )
    run_id = await _start_run(tenant, fake_resolve, _CapturingExecutorFactory(), _PgSettingsClient())
    policy = await _policy_json(run_id)

    assert policy["max_turns"] == AGENT_OVERRIDE.max_turns
    assert policy["max_tool_calls"] == AGENT_OVERRIDE.max_tool_calls
    assert policy["deadline_ms"] == AGENT_OVERRIDE.deadline_ms
    assert policy["max_model_retries"] == AGENT_OVERRIDE.max_model_retries
    assert policy["memory"] == {
        "write_enabled": MEMORY_OVERRIDE.write_enabled,
        "max_injected_memories": MEMORY_OVERRIDE.max_injected_memories,
        "max_injected_bytes": MEMORY_OVERRIDE.max_injected_bytes,
        "max_recall_bytes": MEMORY_OVERRIDE.max_recall_bytes,
        "recall_default_limit": MEMORY_OVERRIDE.recall_default_limit,
    }
    assert policy["artifact"] == {"max_archive_files": ARTIFACT_OVERRIDE.max_archive_files}
    assert policy["locale"] == {"default_timezone": LOCALE_OVERRIDE.default_timezone}
    # `retention_days` / `cleanup_batch_size` 属 Console 清理，**不进 Run 快照**。
    assert "retention_days" not in policy["artifact"]
    assert "cleanup_batch_size" not in policy["artifact"]


async def test_e20_agent_policy_applies_frozen_defaults(
    tenant: TenantContext, fake_resolve: FakeResolveClient
) -> None:
    """冻结的 agent 四叶经 `agent_policy_for`（真实装配）成为执行期 `AgentPolicy` 基值。"""
    from muad_agent_runtime.application.executor import agent_policy_for

    await _save_settings(tenant.tenant_id, agent=AGENT_OVERRIDE)
    capture = _CapturingExecutorFactory()
    await _start_run(tenant, fake_resolve, capture, _PgSettingsClient())

    policy = agent_policy_for(capture.requests[0])
    assert (policy.max_turns, policy.max_tool_calls) == (
        AGENT_OVERRIDE.max_turns,
        AGENT_OVERRIDE.max_tool_calls,
    )
    assert policy.deadline_ms == AGENT_OVERRIDE.deadline_ms
    assert policy.max_model_retries == AGENT_OVERRIDE.max_model_retries


async def test_e20_registry_uses_frozen_artifact_memory_and_locale(
    tenant: TenantContext, fake_resolve: FakeResolveClient, tmp_path: Path
) -> None:
    """真实 `build_registry` 用冻结值装配：归档上限、记忆写开关、时区三处行为可观测。"""
    await _save_settings(
        tenant.tenant_id,
        memory=MEMORY_OVERRIDE,
        artifact=ARTIFACT_OVERRIDE,
        locale=LOCALE_OVERRIDE,
    )
    capture = _CapturingExecutorFactory()
    await _start_run(tenant, fake_resolve, capture, _PgSettingsClient())
    request = capture.requests[0]
    registry = build_registry(request=request, cache=_cache(tmp_path), audit_writer=None, mcp_adapter=None)

    # artifact.max_archive_files：schema 上界与执行期校验同值，且执行期真的拒收超限。
    archive = registry.get(CREATE_ARCHIVE_TOOL)
    assert archive.input_schema["properties"]["files"]["maxItems"] == ARTIFACT_OVERRIDE.max_archive_files
    with pytest.raises(ArchiveToolError) as error:
        await archive.handler(
            {
                "filename": "x.zip",
                "files": [{"path": f"f{i}.txt", "content": "x"} for i in range(3)],
            },
            call_id="c",
        )
    assert error.value.code == "ARCHIVE_FILES_INVALID"

    # memory.write_enabled=false ⇒ `remember` 不注册，`recall` 仍在（读侧不随写开关变化）。
    names = [definition.name for definition in registry.list()]
    assert REMEMBER_TOOL not in names
    assert RECALL_TOOL in names

    # locale.default_timezone ⇒ `current_time` 工具真的按新 IANA 时区换算。
    current_time = await registry.get(CURRENT_TIME_TOOL).handler({}, call_id="c")
    assert f"({LOCALE_OVERRIDE.default_timezone})" in current_time


async def test_e20_recall_uses_frozen_limit_and_byte_cap(
    tenant: TenantContext, fake_resolve: FakeResolveClient, tmp_path: Path
) -> None:
    """`memory.recall_default_limit` 是缺省条数；`memory.max_recall_bytes` 是返回体字节上限。"""
    import json

    await _seed_memories(tenant.tenant_id, tenant.platform_user_id, count=6, value_chars=64)

    # 腿一：缺省条数与 `recall_default_limit` 同值（6 条可用、限 2 ⇒ 只回 2 条）。
    limited = replace(RECALL_SCENARIO, recall_default_limit=2, max_recall_bytes=4096)
    await _save_settings(tenant.tenant_id, memory=limited)
    capture = _CapturingExecutorFactory()
    await _start_run(tenant, fake_resolve, capture, _PgSettingsClient())
    recall = build_registry(
        request=capture.requests[0], cache=_cache(tmp_path), audit_writer=None, mcp_adapter=None
    ).get(RECALL_TOOL)
    assert len(json.loads(await recall.handler({}, call_id="c"))["items"]) == 2

    # 腿二：`max_recall_bytes` 触顶 —— 缺省条数 10 本可带回 6 条，字节上限把它压到 1 条。
    capped = replace(RECALL_SCENARIO, recall_default_limit=10, max_recall_bytes=200)
    await _save_settings(tenant.tenant_id, memory=capped)
    capture2 = _CapturingExecutorFactory()
    await _start_run(tenant, fake_resolve, capture2, _PgSettingsClient())
    recall2 = build_registry(
        request=capture2.requests[0], cache=_cache(tmp_path), audit_writer=None, mcp_adapter=None
    ).get(RECALL_TOOL)
    assert len(json.loads(await recall2.handler({}, call_id="c"))["items"]) == 1


async def test_e20_memory_injection_caps_from_frozen_policy() -> None:
    """`DbBackedContextBuilder.load_history` 用冻结的 `memory.max_injected_memories` 限条数。"""
    tenant, conversation_id, user_id = await _seed_injection_tenant(count=5, value_chars=16)
    try:
        builder = DbBackedContextBuilder(session_factory=get_session_factory)
        history = await builder.load_history(
            tenant_id=tenant,
            conversation_id=conversation_id,
            user_id=user_id,
            budget_messages=10,
            memory_policy=MEMORY_OVERRIDE,
        )
        injected = [m for m in history if str(m.content).startswith("[记忆·")]
        assert len(injected) == MEMORY_OVERRIDE.max_injected_memories
    finally:
        await _purge_tenant(tenant, conversation_id)


# ---- E-20：既有 Run/Task 行与已落库记忆不被改写 ----


async def test_e20_existing_run_and_memories_not_rewritten(
    tenant: TenantContext, fake_resolve: FakeResolveClient
) -> None:
    """配置变更只影响后续新 Run：旧 Run 的快照行逐列不动，已落库记忆一字不改。"""
    capture = _CapturingExecutorFactory()
    client = _PgSettingsClient()
    first = await _start_run(tenant, fake_resolve, capture, client)
    before = await _policy_json(first)
    assert before["max_turns"] == AgentSettings().max_turns  # 无设置行 ⇒ schema 默认

    await _seed_memories(tenant.tenant_id, tenant.platform_user_id, count=1, value_chars=8)
    memory_before = await _memory_row_snapshot(tenant.tenant_id)

    await _save_settings(tenant.tenant_id, agent=AGENT_OVERRIDE, memory=MEMORY_OVERRIDE)

    after = await _policy_json(first)
    assert after == before, "旧 Run 的 policy_json 必须逐键不动"
    assert await _memory_row_snapshot(tenant.tenant_id) == memory_before, "已落库记忆不得被改写"

    second = await _start_run(tenant, fake_resolve, _CapturingExecutorFactory(), client)
    assert (await _policy_json(second))["max_turns"] == AGENT_OVERRIDE.max_turns


# ---- E-20：真实 Console 清理入口按当前平台设置取值 ----


@pytest.fixture(autouse=True)
def _fresh_console_engine() -> AsyncIterator[None]:
    """CLI 走 Console 自己的引擎：清缓存避免跨用例复用到别的事件循环上的连接池。"""
    from muad_console_platform.infrastructure import db as console_db

    console_db.get_engine.cache_clear()
    console_db.get_session_factory.cache_clear()
    yield


async def test_e20_console_cleanup_uses_current_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`cleanup-artifacts` 的保留期与批大小默认值取自**当前**平台设置（CLI 覆盖仍优先）。

    真实 Console CLI 命令协程 + 真实 PG：`artifact.retention_days=5` 让 20 天前的产物进入候选
    （默认 30 天够不到），`artifact.cleanup_batch_size=1` 让本轮**只**清一个。
    """
    from muad_console_platform import cli as console_cli

    tenant_id = f"test-cleanup-{uuid.uuid4()}"
    root = tmp_path / "artifacts"
    old = [await _seed_artifact(root, tenant_id, age=timedelta(days=20)) for _ in range(3)]
    fresh = await _seed_artifact(root, tenant_id, age=timedelta(days=1))

    monkeypatch.setenv("ARTIFACT_ROOT", str(root))
    # retention_days=5 / cleanup_batch_size=1（都不等于 schema 默认 30 / 500）。
    await _save_settings(tenant_id, artifact=ARTIFACT_OVERRIDE)

    exit_code = await console_cli._cleanup_artifacts(
        _cleanup_args(tenant_id=tenant_id, retention_days=None, limit=None)
    )

    assert exit_code == 0
    removed = [i for i in old if not await _artifact_row_exists(i)]
    # 保留期来自设置（4 个里 3 个 20 天前的过期）；批大小来自设置（只处理 1 个）。
    assert len(removed) == ARTIFACT_OVERRIDE.cleanup_batch_size
    assert await _artifact_row_exists(fresh), "未过期的产物不得被清掉"


def _cleanup_args(*, tenant_id: str, retention_days: int | None, limit: int | None) -> Any:
    import argparse

    return argparse.Namespace(
        tenant=tenant_id,
        retention_days=retention_days,
        limit=limit,
        grace_seconds=0.0,
        dry_run=False,
    )


async def _seed_artifact(root: Path, tenant_id: str, *, age: timedelta) -> uuid.UUID:
    artifact_id = uuid.uuid4()
    key = f"cleanup/{uuid.uuid4().hex}.txt"
    path = NfsArtifactStore(root).resolve(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"bytes\n")
    stamp = (datetime.now(UTC) - age).timestamp()
    os.utime(path, (stamp, stamp))
    async with get_session_factory()() as session:
        session.add(
            Artifact(
                id=artifact_id,
                tenant_id=tenant_id,
                task_id=uuid.uuid4(),  # run_id/task_id 恰填一个（表上的 XOR 约束）
                artifact_type="AGENT_OUTPUT",
                storage_key=key,
                media_type="text/plain",
                size=6,
                checksum="sha256:" + "0" * 64,
                metadata_json={},
                create_time=datetime.now(UTC) - age,
                update_time=datetime.now(UTC) - age,
            )
        )
        await session.commit()
    return artifact_id


async def _artifact_row_exists(artifact_id: uuid.UUID) -> bool:
    async with get_session_factory()() as session:
        return bool(
            await session.scalar(
                sa.select(sa.func.count()).select_from(Artifact).where(Artifact.id == artifact_id)
            )
        )


# ---- 记忆/会话种子（真实 PG） ----


async def _seed_memories(tenant_id: str, user_id: uuid.UUID, *, count: int, value_chars: int) -> None:
    service = MemoryService()
    for index in range(count):
        await service.upsert(
            tenant_id=tenant_id,
            user_id=user_id,
            category="PREFERENCE",
            memory_key=f"seed.{index:02d}",
            content_json={"value": "v" * value_chars},
            source_type=SOURCE_USER_EXPLICIT,
        )


async def _memory_row_snapshot(tenant_id: str) -> list[tuple[Any, ...]]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(
                    UserMemory.memory_key,
                    UserMemory.content_json,
                    UserMemory.version,
                    UserMemory.update_time,
                    UserMemory.enabled,
                )
                .where(UserMemory.tenant_id == tenant_id)
                .order_by(UserMemory.memory_key)
            )
        ).all()
        return [tuple(row) for row in rows]


async def _seed_injection_tenant(*, count: int, value_chars: int) -> tuple[str, uuid.UUID, uuid.UUID]:
    """种一个隔离租户：1 会话 + 一段历史 + N 条 `USER_EXPLICIT` 记忆（真实 PG）。"""
    tenant = f"e20-{uuid.uuid4()}"
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()
    base = datetime.now(UTC) - timedelta(days=1)
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant,
                user_id=user_id,
                agent_id=uuid.uuid4(),
                status="ACTIVE",
                last_seq=1,
            )
        )
        for index in range(count):
            session.add(
                UserMemory(
                    tenant_id=tenant,
                    user_id=user_id,
                    memory_key=f"inj.{index:02d}",
                    category="PREFERENCE",
                    content_json={"value": "v" * value_chars},
                    source_type=SOURCE_USER_EXPLICIT,
                    enabled=True,
                    update_time=base + timedelta(minutes=index),
                )
            )
        # 历史给够：比例上限（0.2 × 历史字节）不先触顶，条数上限才是本用例的约束。
        session.add(
            CanonicalEvent(
                tenant_id=tenant,
                conversation_id=conversation_id,
                seq=1,
                event_type="USER_MESSAGE",
                payload_json={"text": "历史" * 2500},
            )
        )
        await session.commit()
    return tenant, conversation_id, user_id


async def _purge_tenant(tenant: str, conversation_id: uuid.UUID) -> None:
    async with get_session_factory()() as session:
        for model in (UserMemory, CanonicalEvent, Conversation):
            await session.execute(model.__table__.delete().where(model.tenant_id == tenant))
        await session.commit()
    assert conversation_id  # 会话随之删除；参数保留以表明清理范围
