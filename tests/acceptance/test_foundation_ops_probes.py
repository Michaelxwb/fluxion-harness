from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from muad_api import StartupValidationError, install_health_probes

ROOT = Path(__file__).resolve().parents[2]

SERVICE_APP_MODULES = (
    "muad_console_platform.main",
    "muad_agent_runtime.main",
    "muad_agent_worker.main",
    "muad_im_gateway.main",
)

# 各服务的 engine/session factory 是模块级 lru_cache。探针请求会在 TestClient 的 portal
# 线程循环里创建它们，若不清理，后续用例会复用到绑定在已关闭循环上的 engine。
_DB_CACHE_MODULES = {
    "muad_console_platform.main": "muad_console_platform.infrastructure.db",
    "muad_agent_runtime.main": "muad_agent_runtime.infrastructure.db",
    "muad_agent_worker.main": "muad_agent_worker.infrastructure.db",
    "muad_im_gateway.main": None,
}


def _clear_db_caches(module_name: str) -> None:
    target = _DB_CACHE_MODULES[module_name]
    if target is None:
        return
    db = importlib.import_module(target)
    for name in ("get_engine", "get_session_factory"):
        factory = getattr(db, name, None)
        if factory is not None and hasattr(factory, "cache_clear"):
            factory.cache_clear()

FAIL_FAST_SCRIPT = """
import asyncio
from types import SimpleNamespace

from muad_api import StartupValidationError, validate_startup


async def main():
    settings = SimpleNamespace(database_url="postgresql+asyncpg://probe", artifact_root="/missing-mount")
    store = SimpleNamespace(root="/missing-mount")
    try:
        await validate_startup(settings, None, store, migrations_dir=None)
    except StartupValidationError as exc:
        print(f"STARTUP_ABORTED: {exc}")
        raise SystemExit(3)
    raise SystemExit(0)


asyncio.run(main())
"""


def test_s07_probes_reflect_dependency_state(tmp_path: Path) -> None:
    mounted = tmp_path / "artifacts"
    mounted.mkdir()
    failed: set[str] = set()

    def storage() -> bool:
        return "storage" not in failed and mounted.is_dir()

    async def async_probe() -> bool:
        return "async_probe" not in failed and mounted.is_dir()

    def broken_dep() -> bool:
        if "broken_dep" in failed:
            raise RuntimeError("connection refused")
        return True

    app = FastAPI()
    install_health_probes(
        app,
        {"storage": storage, "async_probe": async_probe, "broken_dep": broken_dep},
    )
    client = TestClient(app)

    assert client.get("/healthz").status_code == 200

    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}

    failed.update({"storage", "async_probe", "broken_dep"})
    mounted.rmdir()
    degraded = client.get("/readyz")
    assert degraded.status_code == 503
    assert set(degraded.json()["failed"]) == {"storage", "async_probe", "broken_dep"}


def test_s07_startup_fails_fast_on_missing_dependency() -> None:
    result = subprocess.run(
        [sys.executable, "-c", FAIL_FAST_SCRIPT],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 3, f"expected fail-fast exit, got {result.returncode}: {result.stderr[-400:]}"
    assert "artifact storage is not mounted" in result.stdout


@pytest.mark.parametrize("module_name", SERVICE_APP_MODULES)
def test_s07_every_service_registers_both_probes(module_name: str) -> None:
    """RULE-11：每个服务都必须暴露 /healthz 与 /readyz（真实 app 对象，不是测试内自建 FastAPI）。"""
    app: FastAPI = importlib.import_module(module_name).app
    paths = {getattr(route, "path", None) for route in app.routes}

    assert "/healthz" in paths, f"{module_name} 未注册 /healthz"
    assert "/readyz" in paths, f"{module_name} 未注册 /readyz"

    client = TestClient(app)
    try:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["data"] == {"status": "ok"}

        ready = client.get("/readyz")
        assert ready.status_code in (200, 503)
        body = ready.json()
        assert body["code"] == "0", f"{module_name} /readyz 未走统一封套"
        assert body["data"]["status"] in ("ready", "unavailable")
        if ready.status_code == 503:
            assert body["data"]["failed"], f"{module_name} 503 但未报告失败的依赖"
    finally:
        client.close()
        _clear_db_caches(module_name)


def _clear_console_db_caches() -> None:
    from muad_console_platform.infrastructure.db import get_engine, get_session_factory

    get_engine.cache_clear()
    get_session_factory.cache_clear()


async def test_s07_console_lifespan_runs_startup_validation() -> None:
    """RULE-10：真实服务的 lifespan 必须执行启动校验（配置/迁移到 head/存储挂载）。

    必须是 async 用例：在 pytest-asyncio 的事件循环内跑 lifespan，避免 asyncio.run 关掉 session loop。
    """
    app: FastAPI = importlib.import_module("muad_console_platform.main").app
    _clear_console_db_caches()
    try:
        async with app.router.lifespan_context(app):
            pass
    finally:
        # 别把绑定在本用例事件循环上的 engine/factory 留给后续用例
        _clear_console_db_caches()


async def test_s07_console_startup_fails_fast_on_migration_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app: FastAPI = importlib.import_module("muad_console_platform.main").app
    _clear_console_db_caches()
    # 指向没有迁移文件的目录 → 无法确定 head → 启动即失败（fail fast，不进入就绪）
    monkeypatch.setenv("MIGRATIONS_DIR", str(ROOT / "config"))
    try:
        with pytest.raises(StartupValidationError):
            async with app.router.lifespan_context(app):
                pass
    finally:
        _clear_console_db_caches()
