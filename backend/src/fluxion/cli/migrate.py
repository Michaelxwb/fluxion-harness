"""fluxion-migrate CLI（Phase 6 TASK-003 / FEAT-P6-03，design §3.4 形态 B）。

- `fluxion-migrate rollover --kind token|channel|data [--tenant]`：
  SurfaceEvidence 判定 → EXTERNAL_ACTIVE/UNKNOWN 双写→校验→切换→删旧；
  RESET_ALLOWED 直接 reset（不建双写）。退出码 0=完成切换 / 非 0=校验失败中止
  或分类门禁拒绝；
- `fluxion-migrate cleanup --kind legacy [--surface ...] [--tenant]`：
  删旧（仅已切换 surface；未切换拒绝）；
- stdout 逐项 `[OK]`/`[FAIL]`，`--json` 机器可读；错误文案中文。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated

import typer
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fluxion.services.migration_rollover import (
    MigrationError,
    MigrationKind,
    RolloverService,
)

_DEFAULT_DSN = "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test"

app = typer.Typer(no_args_is_help=True, help="One-time Migration / Rollover")


@app.callback()
def _callback() -> None:
    """fluxion-migrate：一次性迁移（仅真实外部依赖，FEAT-P6-03）。"""


def _engine(dsn: str) -> AsyncEngine:
    return create_async_engine(dsn)


def _kind(value: str) -> MigrationKind:
    try:
        return MigrationKind(value)
    except ValueError:
        typer.echo(
            f"error: 未知 kind {value!r}（可选：token / channel / data）", err=True
        )
        raise typer.Exit(2) from None


@app.command("rollover")
def rollover_command(
    kind: Annotated[str, typer.Option("--kind")] = "token",
    tenant: Annotated[str, typer.Option("--tenant")] = "default",
    dsn: Annotated[str, typer.Option("--dsn")] = "",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """双写→一致性校验→切换→删旧（RESET_ALLOWED 直接 reset 不建双写）。"""
    registry_dsn = dsn or os.environ.get("FLUXION_POSTGRES_DSN") or _DEFAULT_DSN
    migration_kind = _kind(kind)

    async def _run() -> dict[str, object]:
        engine = _engine(registry_dsn)
        try:
            service = RolloverService(engine=engine, tenant_id=tenant)
            evidence = await service.gather_evidence(migration_kind)
            result = await service.rollover(migration_kind)
            return {
                "kind": result.kind.value,
                "classification": result.classification.value,
                "dual_written": result.dual_written,
                "verified": result.verified,
                "switched": result.switched,
                "legacy_deleted": result.legacy_deleted,
                "shadow_row_count": result.shadow_row_count,
                "checksum": result.checksum,
                "evidence_source": evidence.evidence_source,
            }
        finally:
            await engine.dispose()

    try:
        payload = asyncio.run(_run())
    except MigrationError as error:
        typer.echo(f"[FAIL] rollover 中止: {error}", err=True)
        raise typer.Exit(1) from error

    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return
    typer.echo(f"=== rollover {payload['kind']}（tenant={tenant}）===")
    typer.echo(f"  classification: {payload['classification']}")
    typer.echo(f"  dual_written: {payload['dual_written']}")
    typer.echo(f"  verified: {payload['verified']}")
    typer.echo(f"  switched: {payload['switched']}")
    typer.echo(f"  legacy_deleted: {payload['legacy_deleted']}")
    typer.echo(f"  shadow_row_count: {payload['shadow_row_count']}")
    typer.echo(f"  evidence: {payload['evidence_source']}")


@app.command("cleanup")
def cleanup_command(
    kind: Annotated[str, typer.Option("--kind")] = "legacy",
    surface: Annotated[str, typer.Option("--surface")] = "token",
    tenant: Annotated[str, typer.Option("--tenant")] = "default",
    dsn: Annotated[str, typer.Option("--dsn")] = "",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """删旧（--kind legacy；仅已切换 surface，未切换拒绝）。"""
    if kind != "legacy":
        typer.echo(f"error: 未知 cleanup kind {kind!r}（当前仅 legacy）", err=True)
        raise typer.Exit(2)
    registry_dsn = dsn or os.environ.get("FLUXION_POSTGRES_DSN") or _DEFAULT_DSN
    migration_kind = _kind(surface)

    async def _run() -> dict[str, object]:
        engine = _engine(registry_dsn)
        try:
            service = RolloverService(engine=engine, tenant_id=tenant)
            result = await service.cleanup(migration_kind)
            return {
                "kind": result.kind.value,
                "legacy_deleted": result.legacy_deleted,
                "shadow_row_count": result.shadow_row_count,
            }
        finally:
            await engine.dispose()

    try:
        payload = asyncio.run(_run())
    except MigrationError as error:
        typer.echo(f"[FAIL] cleanup 中止: {error}", err=True)
        raise typer.Exit(1) from error

    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return
    typer.echo(f"=== cleanup legacy（surface={payload['kind']}，tenant={tenant}）===")
    typer.echo(f"  legacy_deleted: {payload['legacy_deleted']}")
    typer.echo(f"  shadow_row_count: {payload['shadow_row_count']}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
