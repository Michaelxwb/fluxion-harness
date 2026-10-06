from __future__ import annotations

import asyncio
from contextlib import suppress
from uuid import uuid4

from fakes import ConsoleProcess, FakeConsoleClient
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import BotSnapshotItem, BotSnapshotResponse
from muad_im_gateway.application.bot_snapshot import (
    BOT_SNAPSHOT_MAX_PAGES,
    BOT_SNAPSHOT_PAGE_SIZE,
    BotSnapshotCache,
)


async def test_snapshot_starts_empty() -> None:
    cache = BotSnapshotCache(FakeConsoleClient(), tenant_id="t1")
    assert cache.is_ready() is False
    assert cache.revision is None
    assert cache.items == ()
    assert cache.console_reachable is False


async def test_refresh_populates_revision_and_items() -> None:
    console = FakeConsoleClient()
    console.bot_snapshot = BotSnapshotResponse(revision="rev-9")
    cache = BotSnapshotCache(console, tenant_id="t1")

    await cache.refresh()

    assert cache.is_ready() is True
    assert cache.revision == "rev-9"
    assert cache.console_reachable is True
    assert console.bots_calls == 1


async def test_refresh_failure_keeps_previous_revision() -> None:
    console = FakeConsoleClient()
    console.bot_snapshot = BotSnapshotResponse(revision="rev-1")
    cache = BotSnapshotCache(console, tenant_id="t1")
    await cache.refresh()

    console.bots_error = AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    await cache.refresh()

    assert cache.revision == "rev-1"
    assert cache.is_ready() is True
    assert cache.console_reachable is False


async def test_run_forever_polls_until_cancelled() -> None:
    console = FakeConsoleClient()
    console.bot_snapshot = BotSnapshotResponse(revision="rev-1")
    cache = BotSnapshotCache(console, tenant_id="t1", interval_sec=0.01)

    task = asyncio.create_task(cache.run_forever())
    try:
        for _ in range(100):
            if console.bots_calls >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert console.bots_calls >= 2
    assert cache.revision == "rev-1"


def make_item(secret: str = "wecom-bot-1") -> BotSnapshotItem:
    return BotSnapshotItem(
        bot_account_id=uuid4(),
        bot_id="bot-1",
        secret=secret,
        agent_id=uuid4(),
    )


async def test_revision_change_invokes_snapshot_callback() -> None:
    item = make_item()
    console = FakeConsoleClient()
    # total 必须声明：快照"收齐了没有"是按 total 判的，少读一页就等于把那些 bot 当已删除
    console.bot_snapshot = BotSnapshotResponse(revision="r1", items=[item], total=1)
    calls: list[tuple[BotSnapshotItem, ...]] = []

    async def on_change(items: tuple[BotSnapshotItem, ...]) -> None:
        calls.append(items)

    cache = BotSnapshotCache(console, tenant_id="t1", on_snapshot_changed=on_change)
    await cache.refresh()
    assert calls == [(item,)]

    await cache.refresh()
    assert len(calls) == 1

    console.bot_snapshot = BotSnapshotResponse(revision="r2", items=[item], total=1)
    await cache.refresh()
    assert calls == [(item,), (item,)]


async def test_failed_refresh_does_not_invoke_snapshot_callback() -> None:
    console = FakeConsoleClient()
    console.bots_error = AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    calls: list[tuple[BotSnapshotItem, ...]] = []

    async def on_change(items: tuple[BotSnapshotItem, ...]) -> None:
        calls.append(items)

    cache = BotSnapshotCache(console, tenant_id="t1", on_snapshot_changed=on_change)
    await cache.refresh()

    assert calls == []
    assert cache.console_reachable is False


# ---------------------------------------------------------------------------
# B-105: Console snapshot HTTP → 真实 PG bot 配置 → BotSnapshotCache
# ---------------------------------------------------------------------------

import logging  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from muad_console_platform.infrastructure.db import get_session_factory  # noqa: E402
from muad_console_platform.infrastructure.models.channel import BotAccount  # noqa: E402
from muad_console_platform.infrastructure.models.control import (  # noqa: E402
    AgentDefinition,
    ModelDefinition,
)
from muad_im_gateway.application.console_client import ConsoleClient  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

SECRET_CANARY_A = "b105-secret-canary-a"
SECRET_CANARY_B = "b105-secret-canary-b"
READY_TIMEOUT_SEC = 15.0
REVISION_PATTERN = r"^sha256:[0-9a-f]{64}$"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _UvicornServer:
    """真实进程内 uvicorn（真实 socket）：承载真实 Console app 或脚本化服务。"""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def start(self) -> None:
        port = _free_port()
        config = uvicorn.Config(self._app, host="127.0.0.1", port=port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + READY_TIMEOUT_SEC
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError("console server did not start")
        self.url = f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


class _ScriptedSnapshotServer:
    """脚本化 Console 快照服务：用于 Gateway 侧跨页/失败防御路径。"""

    def __init__(self) -> None:
        self.pages: dict[int, JSONResponse] = {}
        self.fail_with_status: int | None = None
        self.served_pages: list[int] = []
        app = FastAPI()

        @app.get("/internal/channel/bots")
        async def _bots(page: int = 1, page_size: int = 20) -> JSONResponse:
            self.served_pages.append(page)
            if self.fail_with_status is not None:
                return JSONResponse(
                    status_code=self.fail_with_status,
                    content={"code": "COMMON_INTERNAL_ERROR", "msg": "boom"},
                )
            return self.pages[page]

        self._app = app
        self._server = _UvicornServer(app)

    def start(self) -> None:
        self._server.start()
        self.url = self._server.url

    def stop(self) -> None:
        self._server.stop()


def _snapshot_page(
    *,
    revision: str,
    total: int,
    page: int,
    page_size: int,
    bot_suffixes: list[str],
) -> JSONResponse:
    items = [
        {
            "bot_account_id": str(uuid.uuid4()),
            "bot_id": f"bot-{suffix}",
            "secret": f"secret-{suffix}",
            "agent_id": str(uuid.uuid4()),
            "enabled": True,
        }
        for suffix in bot_suffixes
    ]
    return JSONResponse(
        status_code=200,
        content={
            "code": "0",
            "msg": "成功",
            "data": {
                "revision": revision,
                "items": items,
                "page": page,
                "page_size": page_size,
                "total": total,
            },
        },
    )


@pytest.fixture()
async def bot_config() -> AsyncIterator[dict[str, Any]]:
    """真实 PG：2 个启用 bot + 1 个禁用 bot（同一租户）。"""
    tenant_id = f"test-{uuid.uuid4()}"
    seeded: dict[str, Any] = {"tenant_id": tenant_id, "bots": [], "model_id": None, "agent_id": None}
    async with get_session_factory()() as session:
        model = ModelDefinition(
            tenant_id=tenant_id,
            key=f"model-{uuid.uuid4().hex[:8]}",
            name="B105 Model",
            model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1",
        )
        session.add(model)
        await session.flush()
        agent = AgentDefinition(
            tenant_id=tenant_id,
            key=f"agent-{uuid.uuid4().hex[:8]}",
            name="B105 Agent",
            instructions="b105",
            model_id=model.id,
        )
        session.add(agent)
        await session.flush()
        bots = [
            BotAccount(
                tenant_id=tenant_id,
                channel="WECOM",
                name="Bot A",
                bot_id=f"bot-a-{uuid.uuid4().hex[:8]}",
                secret=SECRET_CANARY_A,
                agent_id=agent.id,
                enabled=True,
            ),
            BotAccount(
                tenant_id=tenant_id,
                channel="WECOM",
                name="Bot B",
                bot_id=f"bot-b-{uuid.uuid4().hex[:8]}",
                secret=SECRET_CANARY_B,
                agent_id=agent.id,
                enabled=True,
            ),
            BotAccount(
                tenant_id=tenant_id,
                channel="WECOM",
                name="Bot Disabled",
                bot_id=f"bot-c-{uuid.uuid4().hex[:8]}",
                secret="b105-secret-canary-c",
                agent_id=agent.id,
                enabled=False,
            ),
        ]
        session.add_all(bots)
        await session.commit()
        seeded.update(
            {
                "model_id": model.id,
                "agent_id": agent.id,
                "bots": [
                    {"id": bot.id, "bot_id": bot.bot_id, "enabled": bot.enabled} for bot in bots
                ],
            }
        )
    try:
        yield seeded
    finally:
        async with get_session_factory()() as session:
            await session.execute(
                delete(BotAccount).where(BotAccount.tenant_id == tenant_id)
            )
            await session.execute(
                delete(AgentDefinition).where(AgentDefinition.tenant_id == tenant_id)
            )
            await session.execute(
                delete(ModelDefinition).where(ModelDefinition.tenant_id == tenant_id)
            )
            await session.commit()


async def test_b105_collects_real_console_snapshot_and_reuses_revision(
    bot_config: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    tmp_path: Any,
) -> None:
    # 真实 Console 启动校验需要挂载根目录（与验收栈同一环境口径）
    server = ConsoleProcess()
    server.start(tmp_path)
    calls: list[tuple[BotSnapshotItem, ...]] = []

    async def on_change(items: tuple[BotSnapshotItem, ...]) -> None:
        calls.append(items)

    console = ConsoleClient(server.url)
    cache = BotSnapshotCache(
        console, tenant_id=bot_config["tenant_id"], on_snapshot_changed=on_change
    )
    try:
        with caplog.at_level(logging.DEBUG):
            await cache.refresh()
            first_revision = cache.revision
            assert cache.is_ready() is True
            assert len(cache.items) == 2  # 只包含启用 bot
            assert len(calls) == 1
            assert {item.bot_id for item in cache.items} == {
                bot["bot_id"] for bot in bot_config["bots"] if bot["enabled"]
            }
            assert SECRET_CANARY_A in {item.secret for item in cache.items}

            # revision 不变 → 不重复发布（不触发重连）
            await cache.refresh()
            assert len(calls) == 1
            assert cache.revision == first_revision

            # secret 轮换 → revision 变化并重新发布
            async with get_session_factory()() as session:
                bot = await session.scalar(
                    select(BotAccount).where(
                        BotAccount.tenant_id == bot_config["tenant_id"],
                        BotAccount.bot_id == bot_config["bots"][0]["bot_id"],
                    )
                )
                assert bot is not None
                bot.secret = "b105-rotated-secret"
                await session.commit()
            await cache.refresh()
            assert len(calls) == 2
            assert cache.revision != first_revision
            assert "b105-rotated-secret" in {item.secret for item in cache.items}

        import re

        assert re.match(REVISION_PATTERN, cache.revision or "") is not None
        assert SECRET_CANARY_A not in (cache.revision or "")
        logged = "\n".join(record.getMessage() for record in caplog.records)
        for secret in (SECRET_CANARY_A, SECRET_CANARY_B, "b105-rotated-secret"):
            assert secret not in logged  # secret 只驻内存，不进入日志
    finally:
        await console.aclose()
        server.stop()


async def test_b105_multi_page_collection_is_bounded_and_consistent() -> None:
    server = _ScriptedSnapshotServer()
    server.start()
    calls: list[tuple[BotSnapshotItem, ...]] = []

    async def on_change(items: tuple[BotSnapshotItem, ...]) -> None:
        calls.append(items)

    console = ConsoleClient(server.url)
    cache = BotSnapshotCache(console, tenant_id="t1", on_snapshot_changed=on_change)
    try:
        server.pages = {
            1: _snapshot_page(revision="rev-1", total=3, page=1, page_size=2, bot_suffixes=["a", "b"]),
            2: _snapshot_page(revision="rev-1", total=3, page=2, page_size=2, bot_suffixes=["c"]),
        }
        await cache.refresh()
        assert [item.bot_id for item in cache.items] == ["bot-a", "bot-b", "bot-c"]
        assert len(calls) == 1
        assert server.served_pages == [1, 2]  # 按 total 有界翻页

        # 跨页 revision 不一致 → 丢弃本次读取，保留旧快照，不发布
        server.pages[2] = _snapshot_page(
            revision="rev-2", total=3, page=2, page_size=2, bot_suffixes=["c"]
        )
        await cache.refresh()
        assert len(calls) == 1
        assert cache.revision == "rev-1"
        assert [item.bot_id for item in cache.items] == ["bot-a", "bot-b", "bot-c"]

        # 下一节拍恢复一致 → 发布新快照
        server.pages = {
            1: _snapshot_page(revision="rev-3", total=3, page=1, page_size=2, bot_suffixes=["a", "b"]),
            2: _snapshot_page(revision="rev-3", total=3, page=2, page_size=2, bot_suffixes=["c"]),
        }
        await cache.refresh()
        assert len(calls) == 2
        assert cache.revision == "rev-3"
    finally:
        await console.aclose()
        server.stop()


async def test_b105_console_failure_keeps_previous_snapshot() -> None:
    server = _ScriptedSnapshotServer()
    server.start()
    calls: list[tuple[BotSnapshotItem, ...]] = []

    async def on_change(items: tuple[BotSnapshotItem, ...]) -> None:
        calls.append(items)

    console = ConsoleClient(server.url)
    cache = BotSnapshotCache(console, tenant_id="t1", on_snapshot_changed=on_change)
    try:
        server.pages = {
            1: _snapshot_page(revision="rev-1", total=1, page=1, page_size=20, bot_suffixes=["a"])
        }
        await cache.refresh()
        assert len(calls) == 1
        assert cache.console_reachable is True

        server.fail_with_status = 500
        await cache.refresh()
        assert cache.console_reachable is False
        assert cache.revision == "rev-1"  # 失败不清空
        assert [item.bot_id for item in cache.items] == ["bot-a"]
        assert len(calls) == 1
    finally:
        await console.aclose()
        server.stop()


async def test_failed_publication_is_retried_on_the_next_tick() -> None:
    """适配器应用失败 ⇒ **不认定这个 revision 已生效**，下一轮重试（2026-10-06）。

    原实现先提交 `_revision`/`_items`、再 `await` 回调：回调一抛错，新 revision 已经被记成
    "完成"，下一轮同 revision 直接跳过 ⇒ 连接管理器永远停在旧配置（实测：回调只被调用一次，
    而 `is_ready()` 已经是 True）。
    """
    item = make_item()
    console = FakeConsoleClient()
    console.bot_snapshot = BotSnapshotResponse(revision="r1", items=[item], total=1)
    attempts: list[tuple[BotSnapshotItem, ...]] = []

    async def on_change(items: tuple[BotSnapshotItem, ...]) -> None:
        attempts.append(items)
        if len(attempts) == 1:
            raise RuntimeError("adapter update interrupted")

    cache = BotSnapshotCache(console, tenant_id="t1", on_snapshot_changed=on_change)

    await cache.refresh()

    assert len(attempts) == 1
    assert cache.revision is None, "应用没成功就不算生效"
    assert cache.is_ready() is False, "没生效的快照不得被当成可用"

    await cache.refresh()  # 下一轮必须重试同一个 revision

    assert len(attempts) == 2
    assert cache.revision == "r1"
    assert cache.is_ready() is True
    assert cache.items == (item,)


async def test_snapshot_beyond_the_page_cap_is_not_published() -> None:
    """超过有界读取能力的分页结果**不得**当成完整配置发布（2026-10-06）。

    截断结果交给 `apply_snapshot` 时语义是"这份就是全部" ⇒ 没读到的那批 bot 会被当成已删除
    而**断开连接**（实测：声明 2001 个 bot 时，旧缓存里位于最后一页的那个 bot 被移除）。
    """
    total = BOT_SNAPSHOT_PAGE_SIZE * BOT_SNAPSHOT_MAX_PAGES + 1

    class _OverCapacity(FakeConsoleClient):
        async def bots(self, tenant_id, *, page=1, page_size=BOT_SNAPSHOT_PAGE_SIZE):  # type: ignore[no-untyped-def]
            start = (page - 1) * page_size
            items = [
                BotSnapshotItem(
                    bot_account_id=uuid4(),
                    bot_id=f"bot-{index}",
                    secret="s",
                    agent_id=uuid4(),
                )
                for index in range(start, min(start + page_size, total))
            ]
            return BotSnapshotResponse(
                revision="new", items=items, total=total, page=page, page_size=page_size
            )

    published: list[tuple[BotSnapshotItem, ...]] = []

    async def on_change(items: tuple[BotSnapshotItem, ...]) -> None:
        published.append(items)

    cache = BotSnapshotCache(_OverCapacity(), tenant_id="t1", on_snapshot_changed=on_change)
    old = make_item()
    cache._revision = "old"  # noqa: SLF001 - 预置一份"上一轮完整快照"
    cache._items = (old,)  # noqa: SLF001

    await cache.refresh()

    assert published == [], "截断的分页结果不得发布"
    assert cache.revision == "old"
    assert cache.items == (old,), "保留上一份完整快照（连接不能被误断）"


async def test_snapshot_whose_count_does_not_match_total_is_not_published() -> None:
    """收齐的条目数与声明的 `total` 对不上（后端漂移/空页）⇒ 同样**不发布**。"""
    console = FakeConsoleClient()
    # 声明 3 条、实际只给 1 条：当成"完整配置"就会把另外两个 bot 判成已删除
    console.bot_snapshot = BotSnapshotResponse(
        revision="r1", items=[make_item()], total=3, page=1, page_size=BOT_SNAPSHOT_PAGE_SIZE
    )
    published: list[tuple[BotSnapshotItem, ...]] = []

    async def on_change(items: tuple[BotSnapshotItem, ...]) -> None:
        published.append(items)

    cache = BotSnapshotCache(console, tenant_id="t1", on_snapshot_changed=on_change)

    await cache.refresh()

    assert published == []
    assert cache.is_ready() is False
