"""[B-139] 后端真实验收环境核验：真实进程 + PG/Redis/NFS + 清理。

不 mock、不覆盖业务路由；缺依赖 fail 而不是 skip。
"""

from __future__ import annotations

from typing import Any

import hashlib
import uuid
import zipfile
from pathlib import Path

import httpx
import pytest
import redis.asyncio as redis
from sqlalchemy import text

from .environment import LiveStack, cleanup, require, run_db

SERVICE_HEALTH = ("console_url", "runtime_url", "worker_url", "worker2_url", "gateway_url", "channel_url")


def test_b139_all_service_processes_reachable(live_stack: LiveStack, http: httpx.Client) -> None:
    """Console/Runtime/双 Worker/Gateway/渠道探针都是真实独立进程且可访问。"""
    urls = {name: getattr(live_stack, name) for name in SERVICE_HEALTH}
    assert len(set(urls.values())) == len(urls), "每个服务必须独立端口"
    for name, url in urls.items():
        response = http.get(f"{url}/healthz")
        assert response.status_code == 200, f"{name} 不健康: {response.text}"


def test_b139_real_dependencies_present_and_migrated(live_stack: LiveStack) -> None:
    """PG（含本模块迁移产物）/Redis/Artifact 根真实可用，缺失即 fail。"""

    async def inspect(factory: Any) -> dict[str, set[str]]:
        schemas: dict[str, set[str]] = {}
        async with factory() as session:
            for schema in ("task", "runtime", "control"):
                rows = await session.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = :schema"
                    ),
                    {"schema": schema},
                )
                schemas[schema] = {row[0] for row in rows}
        return schemas

    schemas = run_db(inspect)
    assert {"task_submission", "task_schedule", "task_execution", "task_event", "delivery_route"} <= schemas["task"]
    assert {"run_submission", "run_record", "conversation"} <= schemas["runtime"]
    assert {"agent_definition", "skill_artifact", "bot_account"} <= schemas["control"]

    async def redis_round_trip() -> str | None:
        from muad_common import SharedSettings

        client = redis.from_url(  # type: ignore[no-untyped-call]
            SharedSettings().require_redis_url(), decode_responses=True
        )
        try:
            await client.set("e2e:task-schedule:probe", "1")
            value = await client.get("e2e:task-schedule:probe")
            await client.delete("e2e:task-schedule:probe")
            return str(value) if value is not None else None
        finally:
            await client.aclose()

    from .environment import _run_async

    assert _run_async(redis_round_trip) == "1"
    assert live_stack.artifact_root.is_dir()


def test_b139_shared_artifact_mount_and_seeded_skill_package(live_stack: LiveStack) -> None:
    """共享 Artifact 挂载可验证：真实 Skill 包落盘、checksum 一致、可再次读取。"""
    from muad_artifact_store import NfsArtifactStore

    store = NfsArtifactStore(live_stack.artifact_root)
    key = f"probe/{uuid.uuid4().hex}.bin"
    path = store.resolve(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"shared-mount-probe")
    assert store.exists(key)
    assert path.read_bytes() == b"shared-mount-probe"
    path.unlink()

    async def skill_rows(factory: Any) -> list[tuple[str, str]]:
        async with factory() as session:
            rows = await session.execute(
                text(
                    "SELECT storage_key, checksum FROM control.skill_artifact "
                    "WHERE skill_id = :skill_id"
                ),
                {"skill_id": live_stack.skill_id},
            )
            return [(row[0], row[1]) for row in rows]

    rows = run_db(skill_rows)
    assert len(rows) >= 1
    for storage_key, checksum in rows:
        package_path = live_stack.artifact_root / storage_key
        assert package_path.is_file(), storage_key
        assert "sha256:" + hashlib.sha256(package_path.read_bytes()).hexdigest() == checksum
        with zipfile.ZipFile(package_path) as archive:
            assert "scripts/main.py" in archive.namelist()


def test_b139_fixture_does_not_override_business_routes(live_stack: LiveStack) -> None:
    """验收 fixture 不覆盖业务路由：四个应用都没有 dependency_overrides。"""
    from muad_agent_runtime.main import app as runtime_app
    from muad_agent_worker.main import app as worker_app
    from muad_console_platform.main import app as console_app
    from muad_im_gateway.main import app as gateway_app

    for name, app in (
        ("console", console_app),
        ("runtime", runtime_app),
        ("worker", worker_app),
        ("gateway", gateway_app),
    ):
        assert app.dependency_overrides == {}, f"{name} 被覆盖了业务路由"


def test_b139_require_fails_instead_of_skip() -> None:
    """缺依赖必须 fail（不 skip 冒充通过）。"""
    with pytest.raises(pytest.fail.Exception):
        require("DATABASE_URL", None)


def test_b139_cleanup_removes_task_schedule_route_submission_and_probe_data(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    """清理按依赖顺序移除 Task/Event/Schedule/Route/Submission 与探针数据。"""
    headers = live_stack.service_headers()
    schedule_response = http.post(
        f"{live_stack.worker_url}/internal/schedules",
        headers=headers,
        json={
            "name": "e2e cleanup schedule",
            "agent_id": str(live_stack.agent_id),
            "actor_user_id": str(live_stack.platform_user_id),
            "intent_key": "e2e_policy_check",
            "skill_id": str(live_stack.skill_id),
            "input_template": {"customer": "A"},
            "schedule": {"type": "CRON", "cron": "0 9 * * *", "timezone": "Asia/Shanghai"},
            "delivery_route": {
                "channel": "WECOM",
                "bot_id": live_stack.bot_id,
                "external_user_id": "e2e-external-user",
            },
        },
    )
    assert schedule_response.status_code == 200, schedule_response.text

    probe_response = http.post(
        f"{live_stack.channel_url}/probe/deliveries",
        json={"delivery_key": "e2e-probe", "text": "probe"},
    )
    assert probe_response.status_code == 200

    async def counts(factory: Any) -> dict[str, int]:
        result: dict[str, int] = {}
        async with factory() as session:
            for table in (
                "task.task_schedule",
                "task.delivery_route",
                "task.task_submission",
                "task.task_event",
                "task.task_execution",
            ):
                result[table] = int(
                    (
                        await session.execute(
                            text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"),
                            {"t": live_stack.tenant_id},
                        )
                    ).scalar_one()
                )
        return result

    before = run_db(counts)
    assert before["task.task_schedule"] == 1
    assert before["task.delivery_route"] == 1

    from muad_common import SharedSettings

    from .environment import seed_control

    cleanup(SharedSettings().require_database_url(), live_stack.artifact_root)
    # 本用例会清空租户数据：随后重建种子并同步 LiveStack，保证同模块后续用例仍可运行。
    ids = seed_control(
        SharedSettings().require_database_url(), live_stack.llm_url, live_stack.artifact_root
    )
    live_stack.skill_id = ids["skill_id"]
    live_stack.skill_key = ids["skill_key"]
    live_stack.batch_artifact_id = ids["batch_artifact_id"]
    live_stack.batch_storage_key = ids["batch_storage_key"]
    live_stack.batch_checksum = ids["batch_checksum"]

    after = run_db(counts)
    assert after == {
        "task.task_schedule": 0,
        "task.delivery_route": 0,
        "task.task_submission": 0,
        "task.task_event": 0,
        "task.task_execution": 0,
    }, after

    reset = http.post(f"{live_stack.channel_url}/probe/reset")
    assert reset.status_code == 200
    assert http.get(f"{live_stack.channel_url}/probe/deliveries").json()["deliveries"] == []
