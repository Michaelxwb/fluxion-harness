"""Global pytest hooks.

只做一件事：用例失败/出错时立刻输出 `FAILED <nodeid>`，并可选追加到
`PYTEST_FAILURE_LOG` 指向的文件。

为什么需要：`pytest -q` 的失败清单只在**整轮结束的 terminal summary** 里；验收/单测被
timeout 或外部 kill 时摘要根本不会写，日志里只剩一串 `F`，事后只能靠点号位置反推用例——
2026-10-24 全量单测被掐断后即出现这种情况。这里在 `pytest_runtest_logreport` 里即时输出，
保证即使进程被杀也留下可定位的用例名。
"""

from __future__ import annotations

import os

import pytest


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if not report.failed:
        return
    line = f"FAILED {report.nodeid} [{report.when}] ({report.duration:.2f}s)"
    print(line, flush=True)
    path = os.environ.get("PYTEST_FAILURE_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        # 日志写入失败不能反过来影响测试结果；stdout 已经留下了同一行。
        pass


@pytest.fixture(autouse=True)
def _reset_event_batcher_cache() -> object:
    """事件 batcher 是事件循环级单例；每个用例前后丢弃，避免测试库/连接串用。"""
    try:
        from muad_agent_runtime.application.event_batcher import reset_event_batcher
    except Exception:  # pragma: no cover - 非 Runtime 测试环境
        yield
        return
    reset_event_batcher()
    try:
        yield
    finally:
        reset_event_batcher()


@pytest.fixture(autouse=True)
def _reset_process_settings_on_env_change(monkeypatch: pytest.MonkeyPatch) -> object:
    """测试里 monkeypatch 环境后立即失效进程级 settings 缓存。

    生产语义是“启动时冻结、改 `.env` 需重启”；测试大量用 `monkeypatch.setenv` 模拟不同
    启动配置，必须在每次 setenv/delenv 后清缓存，否则会读到上一个用例的快照。
    """
    from muad_common import reset_settings_cache

    original_setenv = monkeypatch.setenv
    original_delenv = monkeypatch.delenv

    def setenv(name: str, value: str, prepend: str | None = None) -> None:
        original_setenv(name, value, prepend)
        reset_settings_cache()

    def delenv(name: str, raising: bool = True) -> None:
        original_delenv(name, raising=raising)
        reset_settings_cache()

    monkeypatch.setenv = setenv  # type: ignore[method-assign]
    monkeypatch.delenv = delenv  # type: ignore[method-assign]
    yield


@pytest.fixture(autouse=True)
def _reset_console_process_settings() -> object:
    """进程级 settings 是跨用例缓存；每个用例前后清掉，避免 env/DB 串用。

    Console 与共享的 `process_settings()` 都按进程缓存；生产只在 lifespan/进程启动时
    构造一次，测试里各用例会 monkeypatch `DATABASE_URL`、`INTERNAL_SERVICE_TOKEN`、
    硬上限等，必须让缓存重新读取当前 env。
    """
    from muad_common import reset_settings_cache as reset_shared_settings

    reset_shared_settings()
    try:
        from muad_console_platform.infrastructure.settings import (
            reset_settings_cache as _console_reset_settings,
        )
    except Exception:  # pragma: no cover - 非 Console 测试环境
        _console_reset_settings = None
    if _console_reset_settings is not None:
        _console_reset_settings()
        try:
            from muad_console_platform.main import app as console_app

            if hasattr(console_app.state, "settings"):
                delattr(console_app.state, "settings")
        except Exception:  # pragma: no cover - 非 Console 测试环境
            pass
    try:
        yield
    finally:
        reset_shared_settings()
        if _console_reset_settings is not None:
            _console_reset_settings()


# ---------------------------------------------------------------------------
# Per-test isolated datastore: tests never touch the dev database or dev Redis.
# ---------------------------------------------------------------------------

_ENGINE_MODULES = (
    "muad_agent_runtime.infrastructure.db",
    "muad_console_platform.infrastructure.db",
    "muad_agent_worker.infrastructure.db",
)


def _reset_all_settings() -> None:
    from importlib import import_module

    try:
        from muad_common import reset_settings_cache as reset_shared

        reset_shared()
    except Exception:  # pragma: no cover - 非共享设置环境
        pass
    for module, name in (
        ("muad_console_platform.infrastructure.settings", "reset_settings_cache"),
        ("muad_im_connector.settings", "reset_connector_settings_cache"),
    ):
        try:
            getattr(import_module(module), name)()
        except Exception:  # pragma: no cover - 服务未安装
            pass


async def _dispose_all_engines() -> None:
    from importlib import import_module

    for module in _ENGINE_MODULES:
        try:
            service = import_module(module)
        except Exception:  # pragma: no cover - 服务未安装
            continue
        try:
            await service.dispose_engine()
        except Exception:  # pragma: no cover - 旧 engine 已绑定别的 loop
            try:
                service.get_session_factory.cache_clear()
                service.get_engine.cache_clear()
            except Exception:
                pass


@pytest.fixture(scope="session")
def datastore_template() -> object:
    """One database migrated to head once per session; per-test clones copy it."""

    from tests.acceptance.datastores import create_template, drop_template

    template = create_template()
    try:
        yield template
    finally:
        drop_template(template)


@pytest.fixture
async def isolated_datastore(request: pytest.FixtureRequest, datastore_template, monkeypatch):
    """Clone the template per test and point the process at it.

    `own_datastore`-marked suites create their own databases (acceptance stacks,
    revision-specific migration tests); `no_datastore`/`unit` suites are pure logic.
    """

    if any(
        request.node.get_closest_marker(marker) is not None
        for marker in ("own_datastore", "no_datastore", "unit")
    ):
        yield None
        return
    import asyncio

    from tests.acceptance.datastores import clone_datastore, drop_datastore

    # clone/drop own their event loop; a thread keeps the async fixture legal.
    info = await asyncio.to_thread(clone_datastore, datastore_template)
    await _dispose_all_engines()
    _reset_all_settings()
    monkeypatch.setenv("DATABASE_URL", info["database_url"])
    monkeypatch.setenv("REDIS_URL", info["redis_url"])
    _reset_all_settings()
    try:
        yield info
    finally:
        await _dispose_all_engines()
        _reset_all_settings()
        await asyncio.to_thread(
            drop_datastore, info["name"], info["admin_database_url"], info["redis_url"]
        )


@pytest.fixture(autouse=True)
async def _ensure_isolated_datastore(isolated_datastore: object) -> object:
    """Every test runs against a cloned database unless it owns its datastore.

    The guard fails loudly if a non-opted suite resolves to anything but a
    ``muad_acc_*`` database — the dev database must never be touched by tests.
    """

    if isolated_datastore is not None:
        from muad_common import process_settings

        url = process_settings().require_database_url()
        assert "/muad_acc_" in url, f"test resolved to a non-isolated database: {url}"
    yield
