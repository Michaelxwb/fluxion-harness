"""Runtime 验收真实环境：真实 HTTP 进程（Console/Runtime/LLM 探针/MCP 探针）+ PG/Redis/Artifact。

不使用 dependency_overrides/mock 业务服务：Runtime 经真实 HTTP 调 Console（resolve-definition/
resolve-credentials）与 LLM/MCP 探针；数据落真实 PostgreSQL。
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TENANT = f"acc-{uuid.uuid4()}"
INTERNAL_TOKEN = "acceptance-internal-token"
MODEL_API_KEY = "sk-owner-table-key"
ROTATED_API_KEY = "sk-rotated-owner-key"
MCP_SECRET = "mcp-owner-secret"


@dataclass(frozen=True)
class LiveStack:
    runtime_url: str
    console_url: str
    llm_url: str
    mcp_url: str
    artifact_root: Path
    tenant_id: str
    agent_id: uuid.UUID
    platform_user_id: uuid.UUID
    model_id: uuid.UUID
    mcp_server_id: uuid.UUID
    skill_key: str
    disabled_skill_key: str

    def runtime_headers(self) -> dict[str, str]:
        return {"X-Tenant-Id": self.tenant_id}

    def console_headers(self) -> dict[str, str]:
        return {
            "X-Tenant-Id": self.tenant_id,
            "X-Internal-Service": INTERNAL_TOKEN,
        }


def _require(name: str, value: str | None) -> str:
    if not value:
        pytest.fail(f"{name} 未配置：验收环境要求真实依赖，不得 skip")
    return value


class _Server:
    def __init__(self, app: object) -> None:
        self._config = uvicorn.Config(app=app, host="127.0.0.1", port=0, log_level="error")
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> str:
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self._server.started:
            raise RuntimeError("server failed to start")
        port = self._server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def _seed_console(database_url: str, llm_url: str, mcp_url: str, artifact_root: Path) -> dict[str, object]:
    from muad_console_platform.infrastructure.models.control import (
        AgentAccessGrant,
        AgentDefinition,
        AgentSkillBinding,
        ModelDefinition,
        PlatformUser,
        Skill,
        SkillArtifact,
    )
    from muad_console_platform.infrastructure.models.mcp import AgentMcpBinding, McpServer
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async def seed() -> dict[str, object]:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        ids: dict[str, object] = {}
        async with factory() as session:
            model = ModelDefinition(
                tenant_id=TENANT,
                key=f"acc-model-{uuid.uuid4().hex[:8]}",
                name="Acceptance Model",
                model_id="gpt-4o-mini",
                base_url=f"{llm_url}/v1",
                api_key=MODEL_API_KEY,
                params_json={"temperature": 0.0},
            )
            session.add(model)
            await session.flush()
            agent = AgentDefinition(
                tenant_id=TENANT,
                key=f"acc-agent-{uuid.uuid4().hex[:8]}",
                name="Acceptance Agent",
                instructions="You are an acceptance agent.",
                model_id=model.id,
                runtime_config={"max_model_retries": 1},
            )
            session.add(agent)
            await session.flush()
            user = PlatformUser(
                tenant_id=TENANT,
                user_code=f"acc-user-{uuid.uuid4().hex[:8]}",
                display_name="Acceptance User",
            )
            session.add(user)
            await session.flush()
            session.add(AgentAccessGrant(user_id=user.id, agent_id=agent.id, granted_by=user.id))

            enabled_skill = Skill(
                tenant_id=TENANT,
                key=f"acc-skill-{uuid.uuid4().hex[:8]}",
                name="Enabled Skill",
                description="enabled acceptance skill",
                user_scope="ALL",
                enabled=True,
            )
            disabled_skill = Skill(
                tenant_id=TENANT,
                key=f"acc-skill-disabled-{uuid.uuid4().hex[:8]}",
                name="Disabled Skill",
                description="disabled acceptance skill",
                user_scope="ALL",
                enabled=False,
            )
            session.add_all([enabled_skill, disabled_skill])
            await session.flush()
            artifact = SkillArtifact(
                skill_id=enabled_skill.id,
                version="1.0.0",
                checksum="sha256:" + "a" * 64,
                storage_key=f"skills/{enabled_skill.id}/acc.zip",
                execution_mode="SYNC",
                package_size=128,
                validation_status="VALID",
                created_by=user.id,
            )
            session.add(artifact)
            await session.flush()
            enabled_skill.current_artifact_id = artifact.id
            session.add(AgentSkillBinding(agent_id=agent.id, skill_id=enabled_skill.id, sort_order=0))

            mcp = McpServer(
                tenant_id=TENANT,
                key=f"acc-mcp-{uuid.uuid4().hex[:8]}",
                name="Acceptance MCP",
                endpoint=f"{mcp_url}/mcp",
                auth_secret=MCP_SECRET,
                user_scope="ALL",
                enabled=True,
                connection_status="CONNECTED",
                tool_catalog_json=[
                    {
                        "name": "probe_tool_1",
                        "description": "Probe tool 1",
                        "input_schema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                        "effect": "READ",
                    }
                ],
                tool_catalog_hash="sha256:" + "b" * 64,
                tool_catalog_revision=4,
            )
            session.add(mcp)
            await session.flush()
            session.add(AgentMcpBinding(agent_id=agent.id, mcp_server_id=mcp.id))
            await session.commit()
            ids = {
                "agent_id": agent.id,
                "user_id": user.id,
                "model_id": model.id,
                "mcp_server_id": mcp.id,
                "skill_key": enabled_skill.key,
                "disabled_skill_key": disabled_skill.key,
            }
        await engine.dispose()
        return ids

    return run_async(seed)


def _cleanup(database_url: str, ids: dict[str, object], artifact_root: Path) -> None:
    async def cleanup() -> None:
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM runtime.canonical_event WHERE tenant_id = :t"
                ),
                {"t": TENANT},
            )
            await connection.execute(
                text("DELETE FROM runtime.run_interrupt WHERE tenant_id = :t"), {"t": TENANT}
            )
            await connection.execute(
                text("DELETE FROM runtime.run_submission WHERE tenant_id = :t"), {"t": TENANT}
            )
            await connection.execute(
                text("DELETE FROM runtime.runtime_snapshot WHERE tenant_id = :t"), {"t": TENANT}
            )
            await connection.execute(
                text("DELETE FROM runtime.run_record WHERE tenant_id = :t"), {"t": TENANT}
            )
            await connection.execute(
                text("DELETE FROM runtime.conversation WHERE tenant_id = :t"), {"t": TENANT}
            )
            for table in ("egress_audit", "tool_call_audit", "model_invocation_audit", "artifact"):
                await connection.execute(
                    text(f"DELETE FROM runtime.{table} WHERE tenant_id = :t"), {"t": TENANT}
                )
            await connection.execute(
                text(
                    "DELETE FROM control.agent_skill_binding WHERE skill_id IN "
                    "(SELECT id FROM control.skill WHERE tenant_id = :t)"
                ),
                {"t": TENANT},
            )
            await connection.execute(
                text("DELETE FROM control.agent_mcp_binding WHERE agent_id IN "
                     "(SELECT id FROM control.agent_definition WHERE tenant_id = :t)"),
                {"t": TENANT},
            )
            await connection.execute(
                text("DELETE FROM control.skill_artifact WHERE skill_id IN "
                     "(SELECT id FROM control.skill WHERE tenant_id = :t)"),
                {"t": TENANT},
            )
            await connection.execute(
                text("DELETE FROM control.skill WHERE tenant_id = :t"), {"t": TENANT}
            )
            await connection.execute(
                text("DELETE FROM control.mcp_server WHERE tenant_id = :t"), {"t": TENANT}
            )
            await connection.execute(
                text("DELETE FROM control.agent_access_grant WHERE agent_id IN "
                     "(SELECT id FROM control.agent_definition WHERE tenant_id = :t)"),
                {"t": TENANT},
            )
            await connection.execute(
                text("DELETE FROM control.platform_user WHERE tenant_id = :t"), {"t": TENANT}
            )
            await connection.execute(
                text("DELETE FROM control.agent_definition WHERE tenant_id = :t"), {"t": TENANT}
            )
            await connection.execute(
                text("DELETE FROM control.model_definition WHERE tenant_id = :t"), {"t": TENANT}
            )
        await engine.dispose()

    run_async(cleanup)
    for path in sorted(artifact_root.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            path.rmdir()


@pytest.fixture(scope="module", autouse=True)
def isolate_engine_caches() -> Iterator[None]:
    """离开本模块前清理绑定服务线程 loop 的 engine 缓存，避免污染后续 async 测试套件。"""
    yield
    _clear_engine_caches()


@pytest.fixture(scope="module")
def live_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveStack]:
    settings = SharedSettings()
    database_url = _require("DATABASE_URL", settings.database_url)
    _require("REDIS_URL", settings.redis_url)

    _clear_engine_caches()
    artifact_root = tmp_path_factory.mktemp("acceptance-artifacts")
    skill_cache_root = tmp_path_factory.mktemp("acceptance-skill-cache")
    os.environ["ARTIFACT_ROOT"] = str(artifact_root)
    os.environ["SKILL_CACHE_ROOT"] = str(skill_cache_root)
    os.environ["INTERNAL_SERVICE_TOKEN"] = INTERNAL_TOKEN
    os.environ["RUN_LEASE_SEC"] = "5"
    os.environ["RUN_HEARTBEAT_SEC"] = "1"
    os.environ["RUN_REAPER_INTERVAL_SEC"] = "1"

    from tests.e2e.mcp_probe_app import app as mcp_app
    from tests.e2e.openai_probe_app import app as llm_app

    llm_server = _Server(llm_app)
    mcp_server = _Server(mcp_app)
    console_server = _Server(_console_app())
    runtime_server = _Server(_runtime_app())
    llm_url = llm_server.start()
    mcp_url = mcp_server.start()
    ids = _seed_console(database_url, llm_url, mcp_url, artifact_root)
    console_url = console_server.start()
    os.environ["CONSOLE_PLATFORM_URL"] = console_url
    runtime_url = runtime_server.start()
    try:
        yield LiveStack(
            runtime_url=runtime_url,
            console_url=console_url,
            llm_url=llm_url,
            mcp_url=mcp_url,
            artifact_root=artifact_root,
            tenant_id=TENANT,
            agent_id=ids["agent_id"],  # type: ignore[arg-type]
            platform_user_id=ids["user_id"],  # type: ignore[arg-type]
            model_id=ids["model_id"],  # type: ignore[arg-type]
            mcp_server_id=ids["mcp_server_id"],  # type: ignore[arg-type]
            skill_key=str(ids["skill_key"]),
            disabled_skill_key=str(ids["disabled_skill_key"]),
        )
    finally:
        runtime_server.stop()
        console_server.stop()
        mcp_server.stop()
        llm_server.stop()
        _cleanup(database_url, ids, artifact_root)
        _clear_engine_caches()


def _clear_engine_caches() -> None:
    """服务线程的 loop 已结束：清理 lru_cache，避免后续测试复用绑定旧 loop 的 engine。"""
    from muad_agent_runtime.infrastructure import db as runtime_db
    from muad_console_platform.infrastructure import db as console_db

    for module in (runtime_db, console_db):
        module.get_engine.cache_clear()
        module.get_session_factory.cache_clear()


def _console_app() -> object:
    from muad_console_platform.main import app

    return app


def _runtime_app() -> object:
    from muad_agent_runtime.main import app

    return app


@pytest.fixture()
def http() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=30) as client:
        yield client


def run_async(factory: Any) -> Any:
    """在独立线程/事件循环执行 async 工作，避免污染 pytest-asyncio 的主循环。"""
    import asyncio

    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            outcome["value"] = asyncio.run(factory())
        except BaseException as exc:  # noqa: BLE001 - 跨线程原样抛回
            outcome["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def run_db(coro_factory: Any) -> Any:
    """在独立事件循环中访问真实 PostgreSQL（避免复用 Runtime 线程的 engine/loop）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async def runner() -> Any:
        engine = create_async_engine(SharedSettings().require_database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            return await coro_factory(factory)
        finally:
            await engine.dispose()

    return run_async(runner)
