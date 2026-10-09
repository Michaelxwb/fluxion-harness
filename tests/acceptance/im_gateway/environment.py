"""IM Gateway 基础验收环境：真实进程 + 真实依赖（PG / Redis / 模型探针 / WS 探针 / 渠道探针）。

与 09 验收栈同一口径：每个服务是独立 uvicorn 子进程（真实进程、真实 HTTP），
数据库与 Redis 用真实实例，缺依赖即失败（不 skip）。数据统一使用 `e2e-im-*` 租户，
fixture finally 清理；两个 Runtime 实例与真实 Worker 进程由 TASK-030 提供。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from muad_common import SharedSettings
from muad_contracts.platform_settings import PlatformSettings, parse_platform_settings

from tests.acceptance.datastores import seed_platform_settings
from tests.acceptance.task_schedule.environment import (  # 复用 09 真实验收栈原语
    CONTROL_CLEANUP,
    READY_TIMEOUT_SEC,
    RUNTIME_CLEANUP,
    TASK_CLEANUP,
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

# 本栈按租户种下的平台设置（与生产同构）：原先靠子进程 env 注入的业务默认已迁到
# `control.platform_setting`（env 注入静默失效），改为 `start_gateway_stack` 启动时按本栈租户写一行
# revision=1。只列**需要非默认**的键，其余由 schema 默认补全（部分文档语义，见
# `tests.acceptance.datastores.seed_platform_settings`）。
PLATFORM_SETTINGS_OVERRIDES: dict[str, Any] = {
    # 展示节拍：生产默认 5s（客户端每帧整帧重排 + 滚动到底）；验收里压到 1s（schema 下界）——
    # 一次几秒的运行在 5s 节拍下压根不会产生 tick 帧，那条路径（`iter_with_ticks` + tick 分支 +
    # 令牌预算）在 E2E 里就没人走。生产默认值由毫秒级单测钉住。
    "im": {"progress_interval_sec": 1},
    # 投递退避 base：生产默认 5；压到 1（下界）省真实等待 —— 本栈没有任何用例断言窗口值
    # （`test_b127` 只断言「耗尽后 FAILED 且任务不被吞掉」），仍在投递轮询粒度（1s）的 16 倍。
    "task": {"delivery_backoff_base_sec": 1},
}
PLATFORM_SETTINGS: PlatformSettings = parse_platform_settings(PLATFORM_SETTINGS_OVERRIDES)

__all__ = [
    "BOT_ID",
    "BOT_SECRET",
    "BOUND_EXTERNAL_USER_ID",
    "CHAT_ID",
    "INTERNAL_TOKEN",
    "PLATFORM_SETTINGS",
    "PLATFORM_SETTINGS_OVERRIDES",
    "READY_TIMEOUT_SEC",
    "TENANT",
    "UNBOUND_EXTERNAL_USER_ID",
    "GatewayStack",
    "ServiceProcess",
    "clear_engine_caches",
    "cleanup",
    "count_tenant_rows",
    "free_port",
    "latest_run_id",
    "purge_tenant",
    "require",
    "restart_process",
    "run_db",
    "start_gateway_stack",
    "wait_for_new_run_terminal",
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
    # Console 自己的两个下游地址，都必须显式指到本栈的进程上——否则会**安静地**走 `.env`
    # 里的默认端口（8000/8001），表现为莫名奇妙的 502 或"所有产物都取不到"：
    #   · `AGENT_RUNTIME_URL`：产物取件的归属校验经 runtime 的 `/internal/artifacts/{id}`
    #     （解析单点）。这是 console → runtime 的第一条调用边，在此之前从没被真正用到过。
    #   · `CONSOLE_PLATFORM_URL`：Console 用它拼**取件直链的基址**（终端用户点的那个 URL）。
    console = spawn(
        "console",
        "muad_console_platform.main",
        console_port,
        AGENT_RUNTIME_URL=runtime_url,
        CONSOLE_PLATFORM_URL=console_url,
    )
    # `IM_GATEWAY_URL` 必须显式指到**本栈**的 gateway。不设的话 runtime 会回落到 `.env` 里的
    # `http://127.0.0.1:8003` —— 那是**开发者本机的 dev 网关**，于是：
    #   ① 会话内交付打到了另一个实例（跨出了本栈的隔离库边界）；
    #   ② 那个实例跑的是它自己启动时的旧代码，回一个与本次改动无关的 422；
    #   ③ 排查方向全错——错误码看着像契约不匹配，实际是"打错了机器"。
    # 2026-10-03 实测踩到（TASK-010 的 S-06），worker 那条线一直是显式设的，runtime 这两条漏了。
    runtime = spawn(
        "runtime",
        "muad_agent_runtime.main",
        runtime_port,
        CONSOLE_PLATFORM_URL=console_url,
        IM_GATEWAY_URL=gateway_url,
    )
    # TASK-030：第二 Runtime 实例（同一逻辑 Agent 可被任意实例承载）+ 真实 Worker 进程
    runtime2 = spawn(
        "runtime-2",
        "muad_agent_runtime.main",
        runtime2_port,
        CONSOLE_PLATFORM_URL=console_url,
        IM_GATEWAY_URL=gateway_url,
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
    # 与生产同构：把原先 env 注入的业务默认按本栈租户种一行平台设置（服务在此之后才起）。
    seed_platform_settings(database_url, TENANT, PLATFORM_SETTINGS_OVERRIDES)
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
    """异步入口：供 async fixture 的 finally 使用（当前事件循环内直接访问真实 PG）。

    `test_recovery` 会在真实栈**仍存活**时主动 purge（验证"清理不留残留"），此时
    Runtime/Gateway 可能并发写入 run_record，落在 run_record 与 conversation 两条
    DELETE 之间 ⇒ 外键违例。整个事务重试即可收敛（下一次 run_record DELETE 会带走
    竞态写入的行）；固定 3 次仍失败则照常抛错，不吞异常。
    """
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    async def purge(session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            async with session.begin():
                for statement in _cleanup_statements():
                    await session.execute(text(statement), {"t": TENANT})

    for attempt in range(3):
        try:
            await _with_own_engine(purge)
            return
        except IntegrityError:
            if attempt == 2:
                raise
            await asyncio.sleep(0.5)


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


RUN_TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")


async def latest_run_id() -> str | None:
    """最新一次 Run 的 id（按 `create_time` 取最后一行）。"""
    from sqlalchemy import text

    async def fetch(session_factory: object) -> str | None:
        async with session_factory() as session:  # type: ignore[operator]
            result = await session.execute(
                text(
                    "SELECT id::text FROM runtime.run_record WHERE tenant_id = :t "
                    "ORDER BY create_time DESC LIMIT 1"
                ),
                {"t": TENANT},
            )
            value = result.scalar()
            return None if value is None else str(value)

    return await _with_own_engine(fetch)  # type: ignore[return-value]


async def wait_for_new_run_terminal(*, previous_run_id: str | None, timeout: float = 120.0) -> str:
    """等**这次推送新建的** Run（id ≠ `previous_run_id`）跑到终态，返回状态。

    同步点**不能**写成「收到任意一条回复」：进度占位帧（`🔵 准备中`）在 Run 建立**之前**就发
    出来了（设计里的「提交准备」阶段），于是"收到回复"完全可能早于 Run 建立/跑完 —— 拿它当真，
    读到的要么是只有 `run.created` 的中间盘面，要么是**上一条用例留下的旧 Run**（假绿）。
    2026-10-04 CI：S-03 因此挂掉；本地机器快，0.3s 轮询窗口里 Run 早已跑完，所以只有 CI 会撞。
    """
    deadline = time.monotonic() + timeout
    status = "未知（未观察到新 Run）"
    while time.monotonic() < deadline:
        run_id = await latest_run_id()
        if run_id is not None and run_id != previous_run_id:
            status = await _latest_status_of(run_id)
            if status in RUN_TERMINAL_STATUSES:
                return status
        await asyncio.sleep(0.3)
    raise AssertionError(
        f"新 Run（≠{previous_run_id}）未在 {timeout}s 内到达终态，最后状态 {status!r}"
    )


async def _latest_status_of(run_id: str) -> str:
    from sqlalchemy import text

    async def fetch(session_factory: object) -> str:
        async with session_factory() as session:  # type: ignore[operator]
            result = await session.execute(
                text("SELECT status FROM runtime.run_record WHERE id = :r"), {"r": uuid.UUID(run_id)}
            )
            return str(result.scalar() or "")

    return await _with_own_engine(fetch)  # type: ignore[return-value]
