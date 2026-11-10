"""PG claim/lease -> real artifact cache -> Skill -> broker -> real platform HTTP."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import uuid
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from muad_agent_core.sandbox import DevUnsafeRunner, WorkspaceLayout, WorkspaceStore
from muad_agent_core.skill import SandboxedSkillExecutor
from muad_agent_worker.infrastructure.models.workspace import Workspace, WorkspaceLease
from muad_agent_worker.infrastructure.workspace_store import WorkerWorkspaceMetadataStore
from muad_agent_worker.worker.executor import SkillTaskExecutor
from muad_agent_worker.worker.service import WorkerLoop
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_common import reset_settings_cache
from muad_platform_sdk import MSSAdapter, PlatformAdapterRegistry
from muad_platform_sdk.session.redis_manager import RedisPlatformSessionManager
from muad_platform_sdk.skill_access import SkillPlatformContext
from redis.asyncio import Redis
from sqlalchemy import select

from agent_worker.conftest import TEST_INTERNAL_SERVICE_TOKEN, TenantContext
from agent_worker.helpers import fetch_events, fetch_task, persist_task
from tests.sdk.test_mss_sessions import platform_server as platform_server_fixture
from tests.sdk.test_platform_budget_http import record_requests
from tests.sdk.test_platform_sliding_session import ControlHandler

platform_server = platform_server_fixture
pytestmark = pytest.mark.integration


@pytest.fixture
async def budget_platform(tenant: TenantContext, platform_server) -> AsyncIterator[SkillPlatformContext]:
    _, platform, credential = platform_server
    control = ThreadingHTTPServer(("127.0.0.1", 0), ControlHandler)
    control.token, control.actor = TEST_INTERNAL_SERVICE_TOKEN, str(uuid.uuid4())
    control.platform, control.credential = platform, credential
    control.platform_id = str(uuid.uuid4())
    thread = threading.Thread(target=control.serve_forever, daemon=True)
    thread.start()
    redis = Redis.from_url(tenant.settings.require_redis_url())
    registry = PlatformAdapterRegistry()
    registry.register(MSSAdapter())
    manager = RedisPlatformSessionManager(
        redis, registry, tenant_id=tenant.tenant_id, platform_id=control.platform_id,
    )
    try:
        yield SkillPlatformContext(
            tenant_id=tenant.tenant_id, actor_user_id=control.actor, execution_id=str(uuid.uuid4()),
            execution_type="TASK", platform_keys=("platform_server",),
            console_url=f"http://127.0.0.1:{control.server_port}",
            redis_url=tenant.settings.require_redis_url(), service_token=control.token,
        )
    finally:
        keys = await redis.smembers(manager.index)
        if keys:
            await redis.delete(*keys)
        await redis.delete(manager.index, manager.epoch)
        await redis.aclose()
        await asyncio.to_thread(control.shutdown)
        control.server_close()
        thread.join()


def _frozen_artifact(tmp_path: Path) -> tuple[SkillArtifactCache, uuid.UUID, dict[str, object]]:
    store = NfsArtifactStore(tmp_path / "artifacts")
    path = store.root / "skills/probe.zip"
    path.parent.mkdir(parents=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "SKILL.md", "---\nname: platform_server\ndescription: probe\n"
            "platforms: [platform_server]\n---\nprobe\n",
        )
        archive.writestr(
            "scripts/main.py",
            "import subprocess,time\n"
            "subprocess.run(['session-manager','get-state','--skill-name','platform_server'],capture_output=True)\n"
            "time.sleep(30)\n",
        )
    artifact_id = uuid.uuid4()
    snapshot = {
        "schema_version": 1,
        "skills": [{
            "artifact_id": str(artifact_id), "skill_id": str(uuid.uuid4()), "key": "platform_server",
            "storage_key": "skills/probe.zip",
            "checksum": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            "source": "ADMIN", "platforms": ["platform_server"],
        }],
        "budget": {
            "egress_budget": {"execution": {"max_requests": 2}},
            "execution_timeout_sec": 10,
            "retry_safety": "IDEMPOTENT",
        },
    }
    return SkillArtifactCache(store, tmp_path / "cache"), artifact_id, snapshot


def _workspace_store(tenant: TenantContext, tmp_path: Path) -> WorkspaceStore:
    layout = WorkspaceLayout(
        root=tmp_path / "workspaces", quota_bytes=1024**2, user_quota_bytes=1024**2,
        file_limit=50, data_ttl_seconds=60,
    )
    return WorkspaceStore(
        layout=layout, metadata=WorkerWorkspaceMetadataStore(lambda: tenant.session_factory),
    )


async def _assert_released_workspace(tenant: TenantContext) -> None:
    async with tenant.session_factory() as session:
        leases = list(await session.scalars(
            select(WorkspaceLease)
            .join(Workspace, Workspace.id == WorkspaceLease.workspace_id)
            .where(Workspace.tenant_id == tenant.tenant_id)
        ))
        assert len(leases) == 1 and leases[0].is_deleted


@pytest.mark.parametrize("recovered", [False, True], ids=["new-task", "expired-lease"])
async def test_budget_exhaustion_fails_claimed_task_without_retry_and_releases_leases(
    tmp_path: Path, tenant: TenantContext, platform_server, budget_platform: SkillPlatformContext,
    monkeypatch: pytest.MonkeyPatch, recovered: bool,
) -> None:
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", budget_platform.console_url)
    reset_settings_cache()
    cache, artifact_id, snapshot = _frozen_artifact(tmp_path)
    task = await persist_task(
        tenant, skill_artifact_id=artifact_id, actor_user_id=uuid.UUID(budget_platform.actor_user_id),
        execution_snapshot_json=snapshot, delivery_mode="NONE", max_attempts=3,
        input_json={"egress_budget": {"execution": {"max_requests": 999}}},
        status="RUNNING" if recovered else "QUEUED", attempt=1 if recovered else 0,
        lease_owner="expired-worker" if recovered else None,
        lease_until=datetime.now(UTC) - timedelta(seconds=5) if recovered else None,
    )
    record_requests(platform_server[0])
    executor = SkillTaskExecutor(
        cache, executor=SandboxedSkillExecutor(runner=DevUnsafeRunner(), dev_unsafe=True),
        workspace_store=_workspace_store(tenant, tmp_path),
    )
    loop = WorkerLoop(tenant.session_factory, tenant.settings, executor, instance_id="budget-worker")
    try:
        claim_time = None
        if recovered:
            assert await loop.reclaim_expired() == 1
            queued = await fetch_task(tenant, task.id)
            assert queued.status == "QUEUED"
            claim_time = queued.not_before
        assert await loop.run_once(now=claim_time) == task.id
        result = await fetch_task(tenant, task.id)
        assert result.status == "FAILED" and result.error_code == "LOCAL_BUDGET_EXHAUSTED"
        assert result.attempt == (2 if recovered else 1)
        assert result.lease_owner is None and result.lease_until is None
        assert result.execution_snapshot_json["budget"]["egress_budget"]["execution"]["max_requests"] == 2
        assert await loop.run_once() is None
        events = await fetch_events(tenant, task.id)
        assert [event.event_type for event in events] == (
            ["RECLAIMED", "CLAIMED", "FAILED"] if recovered else ["CLAIMED", "FAILED"]
        )
        assert len(platform_server[0].requests) == 2  # CSRF + login; denied validation never arrived.
        assert len(platform_server[0].sessions) == 1
        await _assert_released_workspace(tenant)
    finally:
        reset_settings_cache()
