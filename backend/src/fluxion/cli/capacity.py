"""fluxion-capacity CLI（Phase 6 TASK-001 / FEAT-P6-01，design §3.4 形态 B）。

`fluxion-capacity verify --profile v1`：运行 V1 契约满负载 scale-test 复核 SLO。

- 退出码：0=SLO 达标 / 非 0=未达标（Release 门禁可消费）；
- stdout 逐项 `[OK]`/`[FAIL]`，`--json` 机器可读；
- 真实边界：真实 PostgreSQL（--dsn / FLUXION_POSTGRES_DSN / 默认 fluxion_test）+
  真实 Runtime（dev.echo 本地模型）；
- 满负载参数 = V1 契约（50 tenant × 100 sessions = 5,000），可 --tenants/
  --sessions 缩样（验收以满负载为准）。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated

import typer

from fluxion.services.capacity_verify import (
    V1_PROFILE,
    run_capacity_verification,
)

_DEFAULT_DSN = "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test"

app = typer.Typer(no_args_is_help=True, help="Capacity Profile scale-test 复核")


@app.callback()
def _callback() -> None:
    """fluxion-capacity：Capacity Profile scale-test 复核（FEAT-P6-01）。"""


@app.command("verify")
def verify_command(
    profile: Annotated[str, typer.Option("--profile")] = "v1",
    dsn: Annotated[str, typer.Option("--dsn")] = "",
    tenants: Annotated[int, typer.Option("--tenants")] = 0,
    sessions: Annotated[int, typer.Option("--sessions")] = 0,
    concurrency: Annotated[int, typer.Option("--concurrency")] = 100,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """运行 capacity scale-test 复核 V1 契约 SLO（退出码 0=达标）。"""
    if profile != "v1":
        typer.echo(f"error: 未知 profile {profile!r}（当前仅 v1）", err=True)
        raise typer.Exit(2)

    registry_dsn = dsn or os.environ.get("FLUXION_POSTGRES_DSN") or _DEFAULT_DSN
    # 缺省 = V1 契约满负载（RULE-P6-01：验收以契约值为准）
    tenant_count = tenants or int(V1_PROFILE["tenants"])
    session_count = sessions or int(V1_PROFILE["concurrent_sessions"]) // int(
        V1_PROFILE["tenants"]
    )

    try:
        report = asyncio.run(
            run_capacity_verification(
                registry_dsn=registry_dsn,
                tenants=tenant_count,
                sessions_per_tenant=session_count,
                concurrency=concurrency,
                run_tag=f"capacity-cli-{os.getpid()}",
            )
        )
    except Exception as error:
        typer.echo(f"[FAIL] scale-test 无法执行: {error}", err=True)
        raise typer.Exit(1) from error

    checks: list[tuple[str, bool, str]] = [
        (
            "executions_success",
            report.success_count == report.total_executions,
            f"{report.success_count}/{report.total_executions}",
        ),
        (
            "p95_execution_ms",
            report.p95_execution_ms <= float(V1_PROFILE["p95_execution_ms"]),
            f"{report.p95_execution_ms:.1f}ms ≤ {V1_PROFILE['p95_execution_ms']:.0f}ms",
        ),
        (
            "digest_consistency",
            report.digest_consistency_rate == float(V1_PROFILE["digest_consistency_rate"]),
            f"{report.digest_consistency_rate:.0%}",
        ),
        (
            "capability_equivalence",
            report.equivalence_rate == float(V1_PROFILE["equivalence_rate"]),
            f"{report.equivalence_rate:.0%}",
        ),
    ]
    passed = all(ok for _, ok, _ in checks)

    if json_output:
        payload = {
            "profile": "v1",
            "passed": passed,
            "tenants": report.tenants,
            "sessions_per_tenant": report.sessions_per_tenant,
            "total_executions": report.total_executions,
            "success_count": report.success_count,
            "p50_execution_ms": round(report.p50_execution_ms, 2),
            "p95_execution_ms": round(report.p95_execution_ms, 2),
            "p99_execution_ms": round(report.p99_execution_ms, 2),
            "duration_seconds": round(report.duration_seconds, 2),
            "throughput_per_sec": round(report.throughput_per_sec, 2),
            "digest_consistency_rate": report.digest_consistency_rate,
            "equivalence_rate": report.equivalence_rate,
            "errors": report.errors,
        }
        typer.echo(json.dumps(payload, ensure_ascii=False))
    else:
        typer.echo(f"=== Capacity Profile {profile} scale-test（{report.tenants} tenant × "
                   f"{report.sessions_per_tenant} sessions）===")
        for line in report.summary_lines():
            typer.echo(f"  {line}")
        typer.echo("=== SLO 判定 ===")
        for name, ok, detail in checks:
            typer.echo(f"  [{'OK' if ok else 'FAIL'}] {name}: {detail}")
        for error_detail in report.errors:
            typer.echo(f"  [FAIL] error: {error_detail}", err=True)

    if not passed:
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
