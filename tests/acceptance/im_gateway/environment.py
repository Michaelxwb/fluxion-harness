"""IM Gateway 基础验收环境：真实进程 + 真实依赖（PG / Redis / 模型探针 / WS 探针 / 渠道探针）。

与 09 验收栈同一口径：每个服务是独立 uvicorn 子进程（真实进程、真实 HTTP），
数据库与 Redis 用真实实例，缺依赖即失败（不 skip）。数据统一使用 `e2e-im-*` 租户，
fixture finally 清理；两个 Runtime 实例与真实 Worker 进程由 TASK-030 提供。
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from muad_common import SharedSettings

from tests.acceptance.task_schedule.environment import (  # 复用 09 真实验收栈原语
    CONTROL_CLEANUP,
    RUNTIME_CLEANUP,
    TASK_CLEANUP,
    READY_TIMEOUT_SEC,
    ServiceProcess,
    clear_engine_caches,
    free_port,
    require,
    run_db,
)
from tests.e2e.seed_im_gateway import (
    BOT_ID,
    BOT_SECRET,
    BOUND_EXTERNAL_USER_ID,
    CHAT_ID,
    IM_CONTROL_CLEANUP,
    TENANT,
    UNBOUND_EXTERNAL_USER_ID,
    seed_control,
)

INTERNAL_TOKEN = "e2e-im-internal-service-token"

__all__ = [
    "BOT_ID",
    "BOT_SECRET",
    "BOUND_EXTERNAL_USER_ID",
    "CHAT_ID",
    "INTERNAL_TOKEN",
    "READY_TIMEOUT_SEC",
    "TENANT",
    "UNBOUND_EXTERNAL_USER_ID",
    "GatewayStack",
    "ServiceProcess",
    "clear_engine_caches",
    "cleanup",
    "count_tenant_rows",
    "free_port",
    "purge_tenant",
    "require",
    "restart_process",
    "run_db",
    "start_gateway_stack",
    "stop_gateway_stack",
]


@dataclass
class GatewayStack:
    console_url: str
    runtime_url: str
    runtime2_url: str
    worker_url: str
    gateway_url: str
    llm_url: str
    wecom_ws_url: str
    artifact_root: Path
    tenant_id: str
    agent_id: uuid.UUID
    platform_user_id: uuid.UUID
    model_id: uuid.UUID
    bot_id: str
    bot_secret: str
    bound_external_user_id: str
    unbound_external_user_id: str
    chat_id: str
    processes: dict[str, ServiceProcess] = field(default_factory=dict)
    ws_probe: object | None = None

    def service_headers(self) -> dict[str, str]:
        return {"X-Tenant-Id": self.tenant_id, "X-Internal-Service": INTERNAL_TOKEN}


def _service_env(base: dict[str, str], **overrides: str) -> dict[str, str]:
    env = {**base, **overrides}
    env["DEFAULT_TENANT_ID"] = TENANT
    env["INTERNAL_SERVICE_TOKEN"] = INTERNAL_TOKEN
    return env


def start_gateway_stack(
    root: Path,
    *,
    ws_probe_url: str,
    ws_ca_file: str,
) -> tuple[GatewayStack, list[ServiceProcess]]:
    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    redis_url = require("REDIS_URL", settings.redis_url)

    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    artifact_root = root / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    skill_cache_root = root / "skill-cache"
    skill_cache_root.mkdir(parents=True, exist_ok=True)

    console_port = free_port()
    runtime_port = free_port()
    runtime2_port = free_port()
    worker_port = free_port()
    gateway_port = free_port()
    llm_port = free_port()
    console_url = f"http://127.0.0.1:{console_port}"
    runtime_url = f"http://127.0.0.1:{runtime_port}"
    runtime2_url = f"http://127.0.0.1:{runtime2_port}"
    worker_url = f"http://127.0.0.1:{worker_port}"
    gateway_url = f"http://127.0.0.1:{gateway_port}"
    llm_url = f"http://127.0.0.1:{llm_port}"

    base_env = _service_env(
        {
            **os.environ,
            "DATABASE_URL": database_url,
            "REDIS_URL": redis_url,
            "ARTIFACT_ROOT": str(artifact_root),
            "SKILL_CACHE_ROOT": str(skill_cache_root),
        }
    )

    processes: list[ServiceProcess] = []

    def spawn(name: str, module: str, port: int, **env: str) -> ServiceProcess:
        process = ServiceProcess(
            name=name,
            module=module,
            port=port,
            env={**base_env, **env},
            log_path=logs / f"{name}.log",
        )
        processes.append(process)
        return process

    llm_probe = spawn("llm-probe", "tests.e2e.openai_probe_app", llm_port)
    console = spawn("console", "muad_console_platform.main", console_port)
    runtime = spawn(
        "runtime",
        "muad_agent_runtime.main",
        runtime_port,
        CONSOLE_PLATFORM_URL=console_url,
    )
    # TASK-030：第二 Runtime 实例（同一逻辑 Agent 可被任意实例承载）+ 真实 Worker 进程
    runtime2 = spawn(
        "runtime-2",
        "muad_agent_runtime.main",
        runtime2_port,
        CONSOLE_PLATFORM_URL=console_url,
    )
    worker = spawn(
        "worker",
        "muad_agent_worker.main",
        worker_port,
        CONSOLE_PLATFORM_URL=console_url,
        AGENT_RUNTIME_URL=runtime_url,
        IM_GATEWAY_URL=gateway_url,
        DELIVERY_POLL_INTERVAL_SEC="1",
    )
    gateway = spawn(
        "gateway",
        "muad_im_gateway.main",
        gateway_port,
        CONSOLE_PLATFORM_URL=console_url,
        AGENT_RUNTIME_URL=runtime_url,
        # 注意：不设置 CHANNEL_PROBE_URL —— 该变量会让 Gateway 换用 HTTP 探针适配器，
        # 从而不建立真实 WS 连接；投递链路的渠道探针由 TASK-027 按其边界单独配置。
        WECOM_WS_URL=ws_probe_url,
        WECOM_WS_CA_FILE=ws_ca_file,
    )

    llm_probe.start()
    cleanup(database_url)
    seeded = seed_control(llm_url)
    console.start()
    runtime.start()
    runtime2.start()
    worker.start()
    gateway.start()

    stack = GatewayStack(
        console_url=console_url,
        runtime_url=runtime_url,
        runtime2_url=runtime2_url,
        worker_url=worker_url,
        gateway_url=gateway_url,
        llm_url=llm_url,
        wecom_ws_url=ws_probe_url,
        artifact_root=artifact_root,
        tenant_id=seeded["tenant_id"],
        agent_id=seeded["agent_id"],
        platform_user_id=seeded["platform_user_id"],
        model_id=seeded["model_id"],
        bot_id=seeded["bot_id"],
        bot_secret=seeded["bot_secret"],
        bound_external_user_id=seeded["bound_external_user_id"],
        unbound_external_user_id=seeded["unbound_external_user_id"],
        chat_id=seeded["chat_id"],
        processes={process.name: process for process in processes},
    )
    return stack, processes


def restart_process(process: ServiceProcess) -> None:
    """进程级断线/重启恢复：停止后按原 env/端口重新拉起并等待健康。"""
    process.stop()
    process.start()


def stop_gateway_stack(processes: list[ServiceProcess]) -> None:
    for process in reversed(processes):
        process.stop()


def _cleanup_statements() -> tuple[str, ...]:
    """channel_identity / bind_code 需先于 bot_account、platform_user 删除（FK 顺序）。"""
    return (*IM_CONTROL_CLEANUP, *CONTROL_CLEANUP, *RUNTIME_CLEANUP, *TASK_CLEANUP)


async def _with_own_engine(factory: object) -> object:
    """用独立 engine 访问真实 PG：避免跨事件循环复用被缓存的 engine（验收栈多 loop）。"""
    from sqlalchemy import text  # noqa: F401  (供 factory 使用)
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(SharedSettings().require_database_url())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        return await factory(session_factory)  # type: ignore[operator]
    finally:
        await engine.dispose()


async def purge_tenant() -> None:
    """异步入口：供 async fixture 的 finally 使用（当前事件循环内直接访问真实 PG）。"""
    from sqlalchemy import text

    async def purge(session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            async with session.begin():
                for statement in _cleanup_statements():
                    await session.execute(text(statement), {"t": TENANT})

    await _with_own_engine(purge)


def cleanup(database_url: str) -> None:
    """同步入口（09 原语在独立事件循环访问真实 PG）。"""
    from sqlalchemy import text

    async def purge(factory: object) -> None:
        async with factory() as session:  # type: ignore[operator]
            async with session.begin():
                for statement in _cleanup_statements():
                    await session.execute(text(statement), {"t": TENANT})

    run_db(purge)


async def count_tenant_rows(table: str) -> int:
    """按租户统计真实行数（清理断言用）。"""
    from sqlalchemy import text

    async def count(session_factory: object) -> int:
        async with session_factory() as session:  # type: ignore[operator]
            result = await session.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": TENANT}
            )
            return int(result.scalar() or 0)

    return int(await _with_own_engine(count) or 0)  # type: ignore[arg-type]
