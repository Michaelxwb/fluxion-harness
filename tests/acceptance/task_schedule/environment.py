"""TASK-039 验收环境：真实 PG/Redis/NFS + Console/Runtime/双 Worker/Gateway/渠道探针。

- 每个服务都是独立 uvicorn 子进程（真实进程、真实 HTTP），不覆盖任何业务路由；
- 依赖缺失一律 fail，不允许 skip 后冒充通过；
- 测试数据统一 `e2e-` 租户前缀，按 FK 依赖顺序清理。
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TENANT = "e2e-task-schedule"
INTERNAL_TOKEN = "e2e-internal-service-token"
MODEL_API_KEY = "e2e-model-key"
SKILL_KEY = "e2e_policy_check"
BOT_ID = "e2e-bot"
BOT_SECRET = "e2e-bot-secret"
READY_TIMEOUT_SEC = 45.0

BATCH_SKILL_SCRIPT = (
    "import json, sys\n"
    "payload = json.loads(sys.stdin.read() or '{}')\n"
    "if 'customers' in payload:\n"
    "    print(json.dumps({'batch': {'items': [{'customer': c} for c in payload['customers']],"
    " 'max_concurrency': 2, 'aggregate_mode': 'ALL'}}))\n"
    "else:\n"
    "    print(json.dumps({'checked': payload.get('customer')}))\n"
)

SKILL_SCRIPT = (
    "import json, sys, pathlib\n"
    "payload = json.loads(sys.stdin.read() or '{}')\n"
    "side_effect = payload.get('side_effect_path')\n"
    "if side_effect:\n"
    "    with pathlib.Path(side_effect).open('a', encoding='utf-8') as stream:\n"
    "        stream.write('executed\\n')\n"
    "print(json.dumps({'checked': payload}, ensure_ascii=False))\n"
)

TASK_CLEANUP = (
    "DELETE FROM task.task_event WHERE tenant_id = :t",
    "DELETE FROM task.task_submission WHERE tenant_id = :t",
    "DELETE FROM task.task_execution WHERE tenant_id = :t",
    "DELETE FROM task.task_schedule WHERE tenant_id = :t",
    "DELETE FROM task.delivery_route WHERE tenant_id = :t",
)

RUNTIME_CLEANUP = (
    "DELETE FROM runtime.canonical_event WHERE tenant_id = :t",
    "DELETE FROM runtime.run_interrupt WHERE tenant_id = :t",
    "DELETE FROM runtime.run_submission WHERE tenant_id = :t",
    "DELETE FROM runtime.runtime_snapshot WHERE tenant_id = :t",
    "DELETE FROM runtime.run_record WHERE tenant_id = :t",
    "DELETE FROM runtime.conversation WHERE tenant_id = :t",
    "DELETE FROM runtime.egress_audit WHERE tenant_id = :t",
    "DELETE FROM runtime.tool_call_audit WHERE tenant_id = :t",
    "DELETE FROM runtime.model_invocation_audit WHERE tenant_id = :t",
    "DELETE FROM runtime.artifact WHERE tenant_id = :t",
)

CONTROL_CLEANUP = (
    "DELETE FROM control.agent_skill_binding WHERE agent_id IN "
    "(SELECT id FROM control.agent_definition WHERE tenant_id = :t)",
    "DELETE FROM control.agent_mcp_binding WHERE agent_id IN "
    "(SELECT id FROM control.agent_definition WHERE tenant_id = :t)",
    "DELETE FROM control.bot_account WHERE tenant_id = :t",
    "DELETE FROM control.skill_artifact WHERE skill_id IN "
    "(SELECT id FROM control.skill WHERE tenant_id = :t)",
    "DELETE FROM control.skill WHERE tenant_id = :t",
    "DELETE FROM control.agent_access_grant WHERE agent_id IN "
    "(SELECT id FROM control.agent_definition WHERE tenant_id = :t)",
    "DELETE FROM control.platform_user WHERE tenant_id = :t",
    "DELETE FROM control.agent_definition WHERE tenant_id = :t",
    "DELETE FROM control.model_definition WHERE tenant_id = :t",
)


def require(name: str, value: str | None) -> str:
    if not value:
        pytest.fail(f"{name} 未配置：验收环境要求真实依赖，不得 skip")
    return value


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_skill_zip(root: Path, storage_key: str, *, body: str | None = None) -> str:
    """把真实 Skill 包写入共享 Artifact 根，返回真实 checksum。"""
    target = root / storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as staging:
        package = Path(staging)
        (package / "scripts").mkdir()
        (package / "scripts" / "main.py").write_text(body or SKILL_SCRIPT, encoding="utf-8")
        with zipfile.ZipFile(target, "w") as archive:
            archive.write(package / "scripts" / "main.py", "scripts/main.py")
    return "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()


@dataclass
class ServiceProcess:
    name: str
    module: str
    port: int
    env: dict[str, str]
    log_path: Path
    _process: subprocess.Popen[bytes] | None = field(default=None, init=False)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        log = self.log_path.open("wb")
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                f"{self.module}:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "error",
            ],
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        self._wait_ready()

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + READY_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError(
                    f"{self.name} exited early (code={self._process.returncode}): "
                    f"{self.log_path.read_text(errors='replace')[-2000:]}"
                )
            try:
                response = httpx.get(f"{self.url}/healthz", timeout=2.0)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError(f"{self.name} not ready in {READY_TIMEOUT_SEC}s: {self.log_path}")

    def stop(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)


@dataclass
class LiveStack:
    console_url: str
    runtime_url: str
    worker_url: str
    worker2_url: str
    gateway_url: str
    channel_url: str
    llm_url: str
    artifact_root: Path
    tenant_id: str
    agent_id: uuid.UUID
    platform_user_id: uuid.UUID
    model_id: uuid.UUID
    skill_id: uuid.UUID
    skill_key: str
    bot_id: str
    batch_artifact_id: uuid.UUID = uuid.UUID(int=0)
    batch_storage_key: str = ""
    batch_checksum: str = ""
    processes: dict[str, ServiceProcess] = field(default_factory=dict)

    def service_headers(self) -> dict[str, str]:
        return {"X-Tenant-Id": self.tenant_id, "X-Internal-Service": INTERNAL_TOKEN}


def _service_env(base: dict[str, str], **overrides: str) -> dict[str, str]:
    env = {**base, **overrides}
    env["DEFAULT_TENANT_ID"] = TENANT
    env["INTERNAL_SERVICE_TOKEN"] = INTERNAL_TOKEN
    return env


def seed_control(
    database_url: str,
    llm_url: str,
    artifact_root: Path,
) -> dict[str, Any]:
    from muad_console_platform.infrastructure.models.channel import BotAccount
    from muad_console_platform.infrastructure.models.control import (
        AgentAccessGrant,
        AgentDefinition,
        AgentSkillBinding,
        ModelDefinition,
        PlatformUser,
        Skill,
        SkillArtifact,
    )

    async def seed() -> dict[str, Any]:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            model = ModelDefinition(
                tenant_id=TENANT,
                key=f"e2e-model-{uuid.uuid4().hex[:8]}",
                name="E2E Model",
                model_id="gpt-4o-mini",
                base_url=f"{llm_url}/v1",
                api_key=MODEL_API_KEY,
                params_json={"temperature": 0.0},
            )
            session.add(model)
            await session.flush()
            agent = AgentDefinition(
                tenant_id=TENANT,
                key=f"e2e-agent-{uuid.uuid4().hex[:8]}",
                name="E2E Agent",
                instructions="You are an e2e agent.",
                model_id=model.id,
                runtime_config={"max_model_retries": 1},
            )
            session.add(agent)
            await session.flush()
            user = PlatformUser(
                tenant_id=TENANT,
                user_code=f"e2e-user-{uuid.uuid4().hex[:8]}",
                display_name="E2E User",
            )
            session.add(user)
            await session.flush()
            session.add(AgentAccessGrant(user_id=user.id, agent_id=agent.id, granted_by=user.id))
            skill = Skill(
                tenant_id=TENANT,
                key=f"{SKILL_KEY}-{uuid.uuid4().hex[:8]}",
                name="E2E Policy Check",
                description="e2e async skill",
                user_scope="ALL",
                enabled=True,
            )
            session.add(skill)
            await session.flush()
            storage_key = f"skills/{skill.id}/v1/skill.zip"
            checksum = build_skill_zip(artifact_root, storage_key)
            artifact = SkillArtifact(
                skill_id=skill.id,
                version="1.0.0",
                checksum=checksum,
                storage_key=storage_key,
                execution_mode="ASYNC",
                instructions="",
                package_size=(artifact_root / storage_key).stat().st_size,
                validation_status="READY",
                created_by=user.id,
            )
            session.add(artifact)
            await session.flush()
            skill.current_artifact_id = artifact.id
            batch_storage_key = f"skills/{skill.id}/batch/skill.zip"
            batch_checksum = build_skill_zip(
                artifact_root, batch_storage_key, body=BATCH_SKILL_SCRIPT
            )
            batch_artifact = SkillArtifact(
                skill_id=skill.id,
                version="batch-1.0.0",
                checksum=batch_checksum,
                storage_key=batch_storage_key,
                execution_mode="ASYNC",
                instructions="",
                package_size=(artifact_root / batch_storage_key).stat().st_size,
                validation_status="READY",
                created_by=user.id,
            )
            session.add(batch_artifact)
            await session.flush()
            session.add(AgentSkillBinding(agent_id=agent.id, skill_id=skill.id, sort_order=0))
            session.add(
                BotAccount(
                    tenant_id=TENANT,
                    channel="WECOM",
                    name="E2E Bot",
                    bot_id=BOT_ID,
                    secret=BOT_SECRET,
                    agent_id=agent.id,
                    enabled=True,
                )
            )
            await session.commit()
            ids: dict[str, Any] = {
                "agent_id": agent.id,
                "user_id": user.id,
                "model_id": model.id,
                "skill_id": skill.id,
                "skill_key": skill.key,
                "batch_artifact_id": batch_artifact.id,
                "batch_storage_key": batch_storage_key,
                "batch_checksum": batch_checksum,
            }
        await engine.dispose()
        return ids

    return cast(dict[str, Any], _run_async(seed))


def cleanup(database_url: str, artifact_root: Path) -> None:
    async def purge() -> None:
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            for statement in (*TASK_CLEANUP, *RUNTIME_CLEANUP, *CONTROL_CLEANUP):
                await connection.execute(text(statement), {"t": TENANT})
        await engine.dispose()

    _run_async(purge)
    if artifact_root.exists():
        for path in sorted(artifact_root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()


def _run_async(factory: Any) -> Any:
    import asyncio

    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            outcome["value"] = asyncio.run(factory())
        except BaseException as exc:  # noqa: BLE001 - 原样抛回
            outcome["error"] = exc

    import threading

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def run_db(coro_factory: Any) -> Any:
    """在独立事件循环访问真实 PostgreSQL。"""
    from muad_common import SharedSettings

    async def runner() -> Any:
        engine = create_async_engine(SharedSettings().require_database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            return await coro_factory(factory)
        finally:
            await engine.dispose()

    return _run_async(runner)


def start_live_stack(root: Path) -> tuple[LiveStack, list[ServiceProcess]]:
    from muad_common import SharedSettings

    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    redis_url = require("REDIS_URL", settings.redis_url)

    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    artifact_root = root / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    skill_cache_root = root / "skill-cache"
    skill_cache_root.mkdir(parents=True, exist_ok=True)

    base_env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
        "ARTIFACT_ROOT": str(artifact_root),
        "SKILL_CACHE_ROOT": str(skill_cache_root),
    }

    console_port = free_port()
    runtime_port = free_port()
    worker_port = free_port()
    worker2_port = free_port()
    gateway_port = free_port()
    channel_port = free_port()
    llm_port = free_port()

    console_url = f"http://127.0.0.1:{console_port}"
    runtime_url = f"http://127.0.0.1:{runtime_port}"
    worker_url = f"http://127.0.0.1:{worker_port}"
    worker2_url = f"http://127.0.0.1:{worker2_port}"
    gateway_url = f"http://127.0.0.1:{gateway_port}"
    channel_url = f"http://127.0.0.1:{channel_port}"
    llm_url = f"http://127.0.0.1:{llm_port}"

    processes: list[ServiceProcess] = []

    def spawn(name: str, module: str, port: int, **env: str) -> ServiceProcess:
        process = ServiceProcess(
            name=name,
            module=module,
            port=port,
            env=_service_env(base_env, **env),
            log_path=logs / f"{name}.log",
        )
        processes.append(process)
        return process

    llm_probe = spawn("llm-probe", "tests.e2e.openai_probe_app", llm_port)
    channel_probe = spawn("channel-probe", "tests.acceptance.task_schedule.channel_probe", channel_port)
    console = spawn(
        "console",
        "muad_console_platform.main",
        console_port,
        AGENT_WORKER_URL=worker_url,
    )
    runtime = spawn(
        "runtime",
        "muad_agent_runtime.main",
        runtime_port,
        CONSOLE_PLATFORM_URL=console_url,
        AGENT_WORKER_URL=worker_url,
    )
    worker = spawn(
        "worker-1",
        "muad_agent_worker.main",
        worker_port,
        CONSOLE_PLATFORM_URL=console_url,
        IM_GATEWAY_URL=gateway_url,
    )
    worker2 = spawn(
        "worker-2",
        "muad_agent_worker.main",
        worker2_port,
        CONSOLE_PLATFORM_URL=console_url,
        IM_GATEWAY_URL=gateway_url,
    )
    gateway = spawn(
        "gateway",
        "muad_im_gateway.main",
        gateway_port,
        CONSOLE_PLATFORM_URL=console_url,
        AGENT_RUNTIME_URL=runtime_url,
        CHANNEL_PROBE_URL=f"{channel_url}/probe/deliveries",
    )

    llm_probe.start()
    channel_probe.start()
    cleanup(database_url, artifact_root)
    ids = seed_control(database_url, llm_url, artifact_root)
    console.start()
    runtime.start()
    worker.start()
    worker2.start()
    gateway.start()

    stack = LiveStack(
        processes={process.name: process for process in processes},
        console_url=console_url,
        runtime_url=runtime_url,
        worker_url=worker_url,
        worker2_url=worker2_url,
        gateway_url=gateway_url,
        channel_url=channel_url,
        llm_url=llm_url,
        artifact_root=artifact_root,
        tenant_id=TENANT,
        agent_id=ids["agent_id"],
        platform_user_id=ids["user_id"],
        model_id=ids["model_id"],
        skill_id=ids["skill_id"],
        skill_key=ids["skill_key"],
        bot_id=BOT_ID,
        batch_artifact_id=ids["batch_artifact_id"],
        batch_storage_key=ids["batch_storage_key"],
        batch_checksum=ids["batch_checksum"],
    )
    return stack, processes


def stop_live_stack(processes: list[ServiceProcess]) -> None:
    for process in reversed(processes):
        process.stop()


def clear_engine_caches() -> None:
    """服务子进程已退出：清理本进程可能缓存过的 engine/session factory。"""
    from muad_agent_runtime.infrastructure import db as runtime_db
    from muad_agent_worker.infrastructure import db as worker_db
    from muad_console_platform.infrastructure import db as console_db

    for module in (runtime_db, worker_db, console_db):
        module.get_engine.cache_clear()
        module.get_session_factory.cache_clear()


run_async = _run_async

__all__ = [
    "BOT_ID",
    "INTERNAL_TOKEN",
    "LiveStack",
    "SKILL_KEY",
    "TENANT",
    "cleanup",
    "clear_engine_caches",
    "free_port",
    "require",
    "run_async",
    "run_db",
    "seed_control",
    "start_live_stack",
    "stop_live_stack",
]
