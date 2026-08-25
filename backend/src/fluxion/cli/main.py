from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
from pathlib import Path
from typing import Annotated, Protocol, cast

import typer

from fluxion.api.dev_bundle import create_dev_bundle_app
from fluxion.api.runtime import create_app
from fluxion.registry import SQLiteRegistryStore
from fluxion.services.runtime_app import (
    RunRuntimeRequest,
    RuntimeApplicationError,
    RuntimeApplicationService,
    default_runtime_profile_request,
)

DEFAULT_REGISTRY_DSN = "sqlite+aiosqlite:///./fluxion-dev.db"


class UvicornModule(Protocol):
    def run(self, app: object, *, host: str, port: int) -> object: ...


app = typer.Typer(no_args_is_help=True)
plugins_app = typer.Typer(no_args_is_help=True)
app.add_typer(plugins_app, name="plugins")


@app.command("run")
def run_command(
    agent: Annotated[str, typer.Option("--agent")] = "assistant",
    input_message: Annotated[str, typer.Option("--input")] = "",
    tenant: Annotated[str, typer.Option("--tenant")] = "dev-tenant",
    user: Annotated[str, typer.Option("--user")] = "dev-user",
    session: Annotated[str, typer.Option("--session")] = "dev-session",
    registry_dsn: Annotated[str, typer.Option("--registry-dsn")] = "",
    bootstrap: Annotated[bool, typer.Option("--bootstrap")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    payload = asyncio.run(
        _run(
            agent=agent,
            input_message=input_message,
            tenant=tenant,
            user=user,
            session=session,
            registry_dsn=_registry_dsn(registry_dsn),
            bootstrap=bootstrap,
        )
    )
    _emit(payload, json_output)


@app.command("serve")
def serve_command(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8000,
    registry_dsn: Annotated[str, typer.Option("--registry-dsn")] = "",
    dev: Annotated[bool, typer.Option("--dev")] = False,
) -> None:
    dsn = _registry_dsn(registry_dsn)
    uvicorn = _load_uvicorn()
    if dev:
        console_dist, chat_dist = _ensure_frontend_builds()
        uvicorn.run(
            create_dev_bundle_app(
                registry_dsn=dsn,
                console_dist=console_dist,
                chat_dist=chat_dist,
            ),
            host=host,
            port=port,
        )
        return
    service = _create_service(dsn)
    asyncio.run(service.initialize())
    uvicorn_run = uvicorn.run
    uvicorn_run(create_app(service), host=host, port=port)


@app.command("validate")
def validate_command(
    path: Annotated[Path, typer.Argument()],
    registry_dsn: Annotated[str, typer.Option("--registry-dsn")] = DEFAULT_REGISTRY_DSN,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    payload = asyncio.run(_validate(path, registry_dsn))
    _emit(payload, json_output)


@plugins_app.command("list")
def plugins_list_command(
    registry_dsn: Annotated[str, typer.Option("--registry-dsn")] = DEFAULT_REGISTRY_DSN,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    service = _create_service(registry_dsn)
    data = [summary.to_payload() for summary in service.list_plugins()]
    _emit(_envelope("ok", "ok", data, "plugins-list"), json_output)


async def _run(
    *,
    agent: str,
    input_message: str,
    tenant: str,
    user: str,
    session: str,
    registry_dsn: str,
    bootstrap: bool,
) -> dict[str, object]:
    service = _create_service(registry_dsn)
    await service.initialize()
    try:
        if bootstrap:
            await service.ensure_runtime_profile(
                default_runtime_profile_request(
                    tenant_id=tenant,
                    runtime_profile_id=agent,
                )
            )
        result = await service.run(
            service_run_request(
                tenant=tenant,
                user=user,
                agent=agent,
                session=session,
                input_message=input_message,
            )
        )
        return _envelope("ok", "ok", result.to_payload(), result.request_id)
    except RuntimeApplicationError as exc:
        return _envelope(exc.code, str(exc), None, "runtime-error")
    finally:
        await service.close()


async def _validate(path: Path, registry_dsn: str) -> dict[str, object]:
    service = _create_service(registry_dsn)
    await service.initialize()
    try:
        data = await service.validate_resource_file(path)
        return _envelope("ok", "ok", data, "validate")
    except RuntimeApplicationError as exc:
        return _envelope(exc.code, str(exc), None, "validate")
    finally:
        await service.close()


def service_run_request(
    *,
    tenant: str,
    user: str,
    agent: str,
    session: str,
    input_message: str,
) -> RunRuntimeRequest:
    return RunRuntimeRequest(
        tenant_id=tenant,
        user_id=user,
        runtime_profile_id=agent,
        session_id=session,
        input_message=input_message,
    )


def _create_service(registry_dsn: str) -> RuntimeApplicationService:
    return RuntimeApplicationService.create_dev_bundle(SQLiteRegistryStore(registry_dsn))


def _ensure_frontend_builds() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[4]
    console_dist = root / "frontend" / "apps" / "console" / "dist"
    chat_dist = root / "frontend" / "apps" / "chat" / "dist"
    if not (console_dist / "index.html").exists() or not (chat_dist / "index.html").exists():
        try:
            subprocess.run(
                ["pnpm", "run", "build"],
                cwd=root,
                check=True,
                timeout=180,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise typer.BadParameter(f"frontend build failed: {exc}") from exc
    return console_dist, chat_dist


def _load_uvicorn() -> UvicornModule:
    try:
        return cast(UvicornModule, importlib.import_module("uvicorn"))
    except ModuleNotFoundError as exc:
        raise typer.BadParameter("serve requires uvicorn to be installed") from exc


def _registry_dsn(explicit: str) -> str:
    """解析 Registry DSN：显式 --registry-dsn 优先，否则读 FLUXION_DATABASE_URL。"""
    if explicit:
        return explicit
    return os.environ.get("FLUXION_DATABASE_URL", DEFAULT_REGISTRY_DSN)


def _emit(payload: dict[str, object], json_output: bool) -> None:
    if payload["code"] != "ok":
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=False), err=True)
        else:
            typer.echo(f"error: {payload['message']}", err=True)
        raise typer.Exit(1)
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False))
    else:
        typer.echo(payload["data"])


def _envelope(
    code: str,
    message: str,
    data: object,
    request_id: str,
) -> dict[str, object]:
    return {
        "code": code,
        "message": message,
        "data": data,
        "request_id": request_id,
    }
