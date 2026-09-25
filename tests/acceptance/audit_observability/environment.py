"""11-audit-observability 真实验收环境：真实多进程栈 + 真实 PG/Redis + 租户级种子与清理。

- 服务都是独立 uvicorn 子进程（真实进程、真实 HTTP），不覆盖任何业务路由；
- 依赖缺失一律 fail（`require`），不允许 skip 后冒充通过；
- 数据统一 `audit-acceptance-<uuid>` 租户（见 `tests/e2e/seed_audit.py`），清理幂等且含导出产物。

栈组成（本模块只需查询面 + 写入面）：Console（API-01..06）+ Runtime（审计写入与内部端点）
+ Worker（多进程栈成员，模块契约要求）+ LLM 探针（种子模型指向真实 HTTP 端点，而非空地址）。
Gateway 与本模块无关，不启动（避免引入与审计无关的渠道依赖）。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import httpx
from muad_common import SharedSettings
from muad_console_platform.application.audit_export_service import EXPORT_ARTIFACT_PREFIX
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.acceptance.task_schedule.environment import (  # 复用 09 真实验收栈原语
    CONTROL_CLEANUP,
    READY_TIMEOUT_SEC,
    RUNTIME_CLEANUP,
    TASK_CLEANUP,
    ServiceProcess,
    clear_engine_caches,
    free_port,
    require,
    run_async,
    run_db,
)
from tests.e2e.seed_audit import TENANT, TRACE_ID, AuditSeed, seed_all

INTERNAL_TOKEN = "e2e-audit-internal-service-token"

# purge 必须清空的租户级表（`count_tenant_rows` 的白名单来源，顺序仅供阅读）。
# console_session / agent_access_grant 不直接计数：二者对父行有 FK，父行
# （console_account / agent_definition）计数归零即证明子行已被先删。
CLEANUP_TABLES = (
    "control.config_audit_log",
    "control.audit_export_job",
    "control.skill_import_idempotency",
    "control.console_account",
    "control.platform_user",
    "control.agent_definition",
    "control.model_definition",
    "runtime.run_record",
    "runtime.conversation",
    "runtime.canonical_event",
    "runtime.run_submission",
    "runtime.tool_call_audit",
    "runtime.egress_audit",
    "runtime.model_invocation_audit",
    "runtime.artifact",
)

__all__ = [
    "CLEANUP_TABLES",
    "AuditStack",
    "INTERNAL_TOKEN",
    "READY_TIMEOUT_SEC",
    "ServiceProcess",
    "ServiceStartupResult",
    "TENANT",
    "TRACE_ID",
    "cleanup_tenant_artifacts",
    "clear_engine_caches",
    "count_tenant_artifact_files",
    "count_tenant_rows",
    "free_port",
    "purge_tenant",
    "require",
    "start_audit_stack",
    "start_service_without_dependency",
    "stop_audit_stack",
    "tenant_export_dir",
    "wait_ready",
]


@dataclass
class AuditStack:
    console_url: str
    runtime_url: str
    worker_url: str
    llm_url: str
    artifact_root: Path
    tenant_id: str
    seed: AuditSeed
    processes: dict[str, ServiceProcess] = field(default_factory=dict)

    def service_headers(self) -> dict[str, str]:
        return {"X-Tenant-Id": self.tenant_id, "X-Internal-Service": INTERNAL_TOKEN}


@dataclass(frozen=True)
class ServiceStartupResult:
    """依赖缺失下的真实启动结果：`healthy=True` 即服务未快启失败（静默降级）。"""

    service: str
    healthy: bool
    returncode: int | None
    log_tail: str


def artifact_root_dir(root: Path) -> Path:
    target = root / "artifacts"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _base_env(database_url: str, redis_url: str, root: Path) -> dict[str, str]:
    """服务进程环境：artifact / skill-cache 根钉在验收临时目录，其余继承真实进程环境。"""
    skill_cache_root = root / "skill-cache"
    skill_cache_root.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
        "ARTIFACT_ROOT": str(artifact_root_dir(root)),
        "SKILL_CACHE_ROOT": str(skill_cache_root),
    }


def _service_env(base: dict[str, str], **overrides: str) -> dict[str, str]:
    env = {**base, **overrides}
    env["DEFAULT_TENANT_ID"] = TENANT
    env["INTERNAL_SERVICE_TOKEN"] = INTERNAL_TOKEN
    return env


def _spawn(
    processes: list[ServiceProcess],
    *,
    logs: Path,
    base_env: dict[str, str],
    name: str,
    module: str,
    **env: str,
) -> ServiceProcess:
    process = ServiceProcess(
        name=name,
        module=module,
        port=free_port(),
        env=_service_env(base_env, **env),
        log_path=logs / f"{name}.log",
    )
    processes.append(process)
    return process


def start_audit_stack(root: Path) -> tuple[AuditStack, list[ServiceProcess]]:
    """启动真实现场栈并写入审计种子（真实 PG + 环境自有 artifact 根，非仓库共享根）。"""
    processes: list[ServiceProcess] = []
    try:
        stack = _boot(root, processes)
    except BaseException:
        # 任何一步失败（含种子写入）都不留孤儿进程
        stop_audit_stack(processes)
        raise
    return stack, processes


def _boot(root: Path, processes: list[ServiceProcess]) -> AuditStack:
    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    redis_url = require("REDIS_URL", settings.redis_url)
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    artifact_root = artifact_root_dir(root)
    base_env = _base_env(database_url, redis_url, root)

    probe = _spawn(
        processes, logs=logs, base_env=base_env, name="llm-probe",
        module="tests.e2e.openai_probe_app",
    )
    probe.start()
    seed = cast(
        AuditSeed,
        run_async(lambda: seed_all(artifact_root, model_base_url=f"{probe.url}/v1")),
    )
    console = _spawn(
        processes, logs=logs, base_env=base_env, name="console",
        module="muad_console_platform.main",
    )
    runtime = _spawn(
        processes, logs=logs, base_env=base_env, name="runtime",
        module="muad_agent_runtime.main", CONSOLE_PLATFORM_URL=console.url,
    )
    worker = _spawn(
        processes, logs=logs, base_env=base_env, name="worker",
        module="muad_agent_worker.main", CONSOLE_PLATFORM_URL=console.url,
        AGENT_RUNTIME_URL=runtime.url,
    )
    for process in (console, runtime, worker):
        process.start()

    return AuditStack(
        console_url=console.url,
        runtime_url=runtime.url,
        worker_url=worker.url,
        llm_url=probe.url,
        artifact_root=artifact_root,
        tenant_id=TENANT,
        seed=seed,
        processes={process.name: process for process in processes},
    )


def stop_audit_stack(processes: list[ServiceProcess]) -> None:
    for process in reversed(processes):
        process.stop()


async def wait_ready(url: str, timeout: float = READY_TIMEOUT_SEC) -> httpx.Response:
    """等待真实 HTTP 探针就绪：200 返回响应体，超时抛错（不允许静默放行）。"""
    deadline = time.monotonic() + timeout
    last_status: int | None = None
    async with httpx.AsyncClient(timeout=5.0) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(url)
            except httpx.HTTPError:
                last_status = None
            else:
                last_status = response.status_code
                if last_status == 200:
                    return response
            await asyncio.sleep(0.2)
    raise RuntimeError(f"{url} 未在 {timeout}s 内就绪（最后状态码: {last_status}）")


def start_service_without_dependency(
    *, name: str, module: str, log_path: Path, **overrides: str
) -> ServiceStartupResult:
    """以缺失依赖/配置启动真实服务，要求其就绪前退出（fail fast，不静默降级）。

    未退出即视为静默降级：先停掉进程再原样上报，绝不留下孤儿进程。
    """
    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    redis_url = require("REDIS_URL", settings.redis_url)
    process = ServiceProcess(
        name=name,
        module=module,
        port=free_port(),
        env=_service_env(
            _base_env(database_url, redis_url, log_path.parent), **overrides
        ),
        log_path=log_path,
    )
    try:
        process.start()
    except RuntimeError:
        healthy = False
    else:
        healthy = True
        process.stop()
    returncode = None if process._process is None else process._process.returncode
    return ServiceStartupResult(
        service=name,
        healthy=healthy,
        returncode=returncode,
        log_tail=log_path.read_text(errors="replace")[-2000:],
    )


def _cleanup_statements() -> tuple[str, ...]:
    """租户级删除语句：先删 FK 子行（console_session / runtime.artifact）再删父行。"""
    return (
        # 审计事实 + 导出任务（无 FK 指向）；共享幂等表按本模块独占租户整表清
        "DELETE FROM control.config_audit_log WHERE tenant_id = :t",
        "DELETE FROM control.audit_export_job WHERE tenant_id = :t",
        "DELETE FROM control.skill_import_idempotency WHERE tenant_id = :t",
        # 会话先于账号（FK console_session.account_id → console_account.id）
        "DELETE FROM control.console_session WHERE account_id IN "
        "(SELECT id FROM control.console_account WHERE tenant_id = :t)",
        "DELETE FROM control.console_account WHERE tenant_id = :t",
        # artifact 先于 run_record（FK runtime.artifact.run_id → runtime.run_record.id）
        "DELETE FROM runtime.artifact WHERE tenant_id = :t",
        *CONTROL_CLEANUP,
        *RUNTIME_CLEANUP,
        *TASK_CLEANUP,
    )


def purge_tenant() -> None:
    """租户级清理（同步入口，幂等）：fixture teardown 与用例内重复调用均可。"""

    async def purge(factory: async_sessionmaker[AsyncSession]) -> None:
        async with factory() as session:
            async with session.begin():
                for statement in _cleanup_statements():
                    await session.execute(text(statement), {"t": TENANT})

    run_db(purge)


def count_tenant_rows(table: str) -> int:
    """按租户统计真实行数（清理断言用）；表名取自白名单，值一律绑定参数。"""
    if table not in CLEANUP_TABLES:
        raise ValueError(f"{table} 不在清理表清单内（禁止任意表名拼接）")

    async def count(factory: async_sessionmaker[AsyncSession]) -> int:
        async with factory() as session:
            result = await session.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": TENANT}
            )
            return int(result.scalar() or 0)

    return int(run_db(count))


def tenant_export_dir(artifact_root: Path) -> Path:
    """本租户导出产物目录（`exports/<tenant>/`，与生产 artifact_ref 前缀同源）。"""
    return artifact_root / EXPORT_ARTIFACT_PREFIX / TENANT


def count_tenant_artifact_files(artifact_root: Path) -> int:
    directory = tenant_export_dir(artifact_root)
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.rglob("*") if path.is_file())


def cleanup_tenant_artifacts(artifact_root: Path) -> None:
    """删除本租户导出产物（一任务一产物，整目录移除，幂等）。"""
    directory = tenant_export_dir(artifact_root)
    if directory.exists():
        shutil.rmtree(directory)
