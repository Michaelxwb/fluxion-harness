"""[B-210] 审计可观测性真实验收环境：真实现场栈 → 真实 HTTP / PostgreSQL / Redis。

不得 Mock 的真实边界：真实服务进程（Console/Runtime/Worker + 模型探针）、真实 HTTP、
真实 PostgreSQL（审计种子与租户级清理）、真实 artifact store（导出产物文件）。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from muad_common import SharedSettings
from muad_console_platform.infrastructure.repositories.audit_export_repository import (
    AuditExportRepository,
)
from muad_console_platform.infrastructure.repositories.audit_query_repository import (
    AuditQueryFilters,
    AuditQueryRepository,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.acceptance.audit_observability.environment import (
    CLEANUP_TABLES,
    TENANT,
    AuditStack,
    cleanup_tenant_artifacts,
    clear_engine_caches,
    count_tenant_artifact_files,
    count_tenant_rows,
    purge_tenant,
    require,
    start_audit_stack,
    start_service_without_dependency,
    stop_audit_stack,
    tenant_export_dir,
    wait_ready,
)
from tests.acceptance.task_schedule.environment import run_db
from tests.e2e.seed_audit import AUDIT_ROW_COUNT

SessionFactory = async_sessionmaker[AsyncSession]


def _service_urls(stack: AuditStack) -> dict[str, str]:
    return {
        "llm-probe": stack.llm_url,
        "console": stack.console_url,
        "runtime": stack.runtime_url,
        "worker": stack.worker_url,
    }


def _read_projected_audits(trace_id: str) -> list[dict[str, Any]]:
    """自持 engine 经真实聚合投影（Console 读路径）回读同一 trace 的审计行。"""

    async def read(factory: SessionFactory) -> list[dict[str, Any]]:
        async with factory() as session:
            return await AuditQueryRepository(session).all_rows(
                TENANT, AuditQueryFilters(trace_id=trace_id)
            )

    return list(run_db(read))


def _read_export_job(export_id: uuid.UUID) -> dict[str, Any]:
    """自持 engine 回读导出任务事实（状态/产物引用/行数）。"""

    async def read(factory: SessionFactory) -> dict[str, Any]:
        async with factory() as session:
            job = await AuditExportRepository(session).find_job(TENANT, export_id)
            if job is None:
                raise RuntimeError(f"导出任务 {export_id} 不存在")
            return {
                "status": job.status,
                "artifact_ref": job.artifact_ref,
                "row_count": job.row_count,
                "error_code": job.error_code,
            }

    return dict(run_db(read))


def _probe_payload(response: httpx.Response) -> dict[str, Any]:
    """统一取探针载荷：api-kit 封套（真实服务，`code=0`）与裸 JSON（探针应用）两种形态。"""
    body = response.json()
    if "code" in body and "data" in body:
        assert body["code"] == "0", body
        return dict(body["data"])
    return dict(body)


@pytest.fixture(scope="module", autouse=True)
def isolate_engine_caches() -> Iterator[None]:
    yield
    clear_engine_caches()


@pytest.fixture(scope="module")
def audit_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[AuditStack]:
    settings = SharedSettings()
    require("DATABASE_URL", settings.database_url)
    require("REDIS_URL", settings.redis_url)

    clear_engine_caches()
    root = tmp_path_factory.mktemp("audit-acceptance")
    stack, processes = start_audit_stack(root)
    try:
        yield stack
    finally:
        stop_audit_stack(processes)
        purge_tenant()
        cleanup_tenant_artifacts(stack.artifact_root)
        clear_engine_caches()


async def test_b210_stack_boots_and_services_report_ready(audit_stack: AuditStack) -> None:
    for name, process in audit_stack.processes.items():
        assert process._process is not None, f"{name} 未启动"
        assert process._process.poll() is None, f"{name} 进程已退出"

    urls = _service_urls(audit_stack)
    for name, url in urls.items():
        response = await wait_ready(f"{url}/healthz")
        assert _probe_payload(response)["status"] == "ok", name

    # /readyz 由真实依赖决定：PG 可读写 + artifact 根已挂载
    for name in ("console", "runtime", "worker"):
        ready = await wait_ready(f"{urls[name]}/readyz")
        assert _probe_payload(ready)["status"] == "ready", (name, ready.text)


def test_b210_seed_helpers_produce_expected_rows(audit_stack: AuditStack) -> None:
    seed = audit_stack.seed
    assert audit_stack.tenant_id.startswith("audit-acceptance-")
    assert seed.context.tenant_id == audit_stack.tenant_id

    # 四类审计同 trace 串联：经真实聚合投影回读，字段已归一
    rows = _read_projected_audits(seed.context.trace_id)
    assert sorted(row["audit_type"] for row in rows) == ["CONFIG", "EGRESS", "MODEL", "TOOL"]
    assert {str(row["audit_id"]) for row in rows} == {
        str(seed.rows.config),
        str(seed.rows.tool),
        str(seed.rows.egress),
        str(seed.rows.model),
    }
    assert {row["result_status"] for row in rows} == {"SUCCESS"}
    assert {str(row["agent_id"]) for row in rows if row["agent_id"] is not None} == {
        str(seed.context.agent_id)
    }
    # Actor 名称由真实 JOIN 补齐：CONFIG 取 console_account，其余取 platform_user
    assert {row["actor_name"] for row in rows} == {
        "Audit Acceptance Admin",
        "Audit Acceptance User",
    }

    # 归属主体（同一 run / conversation / 账号）真实落库
    assert count_tenant_rows("runtime.run_record") == 1
    assert count_tenant_rows("runtime.conversation") == 1
    assert count_tenant_rows("control.console_account") == 1

    # 导出任务：SUCCEEDED + 产物落环境自有 artifact 根 + 幂等行已记录
    job = _read_export_job(seed.export.export_id)
    assert job["status"] == "SUCCEEDED"
    assert job["error_code"] is None
    assert job["artifact_ref"] == seed.export.artifact_ref
    assert job["row_count"] == seed.export.row_count == AUDIT_ROW_COUNT
    artifact = audit_stack.artifact_root / seed.export.artifact_ref
    assert artifact.is_file()
    assert len(json.loads(artifact.read_text(encoding="utf-8"))) == AUDIT_ROW_COUNT
    assert count_tenant_artifact_files(audit_stack.artifact_root) == 1
    assert count_tenant_rows("control.audit_export_job") == 1
    assert count_tenant_rows("control.skill_import_idempotency") == 1


@pytest.mark.parametrize(
    ("service", "module"),
    [("console", "muad_console_platform.main"), ("runtime", "muad_agent_runtime.main")],
)
def test_b210_startup_fails_fast_without_artifact_mount(
    tmp_path: Path, service: str, module: str
) -> None:
    """artifact 根未挂载：服务必须在就绪前退出，不得静默降级为可用。"""
    result = start_service_without_dependency(
        name=service,
        module=module,
        log_path=tmp_path / f"{service}-no-artifact.log",
        ARTIFACT_ROOT=str(tmp_path / "missing-artifacts"),
    )
    assert result.healthy is False, f"{service} 在 artifact 未挂载时仍通过健康探测"
    assert result.returncode not in (None, 0), result.log_tail
    assert "artifact storage is not mounted" in result.log_tail


def test_b210_startup_fails_fast_without_reachable_database(tmp_path: Path) -> None:
    """数据库不可达：启动校验真实探测 PG，失败即退出（真实依赖而非静态声明）。"""
    result = start_service_without_dependency(
        name="console",
        module="muad_console_platform.main",
        log_path=tmp_path / "console-no-database.log",
        DATABASE_URL="postgresql+asyncpg://muad:muad@127.0.0.1:1/muad",
    )
    assert result.healthy is False, "PG 不可达时 Console 仍通过健康探测"
    assert result.returncode not in (None, 0), result.log_tail
    assert "cannot read schema revision" in result.log_tail


def test_b210_purge_is_idempotent(audit_stack: AuditStack) -> None:
    """最后执行：连续两次清理后审计相关表与导出幂等行归零、导出产物目录消失。"""
    purge_tenant()
    first = {table: count_tenant_rows(table) for table in CLEANUP_TABLES}
    purge_tenant()
    second = {table: count_tenant_rows(table) for table in CLEANUP_TABLES}
    print(
        "B-210 purge 行数(first -> second): "
        + json.dumps({"first": first, "second": second}, ensure_ascii=False)
    )

    assert set(first.values()) == {0}, first
    assert second == first
    cleanup_tenant_artifacts(audit_stack.artifact_root)
    assert count_tenant_artifact_files(audit_stack.artifact_root) == 0
    assert not tenant_export_dir(audit_stack.artifact_root).exists()
