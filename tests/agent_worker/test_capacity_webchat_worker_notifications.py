"""B-268 / TASK-068：Worker 离线结果接入持久用户收件箱（design §3.4 API-11、§3.6）。

真实边界（不 mock 存储、租约或投递）：

- **隔离 PostgreSQL**（`async_tool_datastore`：每用例一个空库 + 迁移到 head，跑完 drop）：真实
  `task.task_execution`/`task.delivery_route`/`task.runtime_result_outbox` 行，真实
  `runtime.conversation`/`run_record`/`tool_operation`/`tool_result_inbox`/`artifact`/
  `channel_notification` 行与完整唯一约束；
- **真实 Worker 监督/claim**：`WorkerLoop.run_once`（真实 `TaskClaimer` 领取 + 任务槽监督 +
  终态事务），不替换存储或租约；
- **真实 Skill 子进程**：`SkillTaskExecutor` 拉起现场脚本（真实 `asyncio` 子进程），脚本每次执行
  追加一行标记——「不重复业务执行」因此是可数的真实事实；
- **外部 HTTP 协议探针**：真实 uvicorn TCP 服务上的 Runtime 路由
  （`/internal/tool-results`、`/internal/webchat/task-results`、`/internal/webchat/notifications`）
  与真实 `require_internal_service` 门控。

关键断言（B-268 契约）：

1. **持久入箱确认后才完成投递**：结果先被 Runtime 受理（`tool_result_inbox`）才成为投递候选
   （受理前不消耗投递尝试、不写通知）；入箱回执 `persisted=true` 才置 `SENT`，2xx 但没有持久
   确认一律按未送达重试；
2. **送达/已读分开**：入箱行 `delivery_state=NOT_REQUIRED`、`read_at IS NULL`；显式已读不改变
   投递状态，送达也不代替已读；
3. **按原来源 conversation 定位**：通知挂在原 Run 的会话/入口/用户上，附件是该 Task 的真实产物；
   没有来源 Run 或没有入口归属的 WEBCHAT 投递**显式失败**，不编造会话；
4. **Worker 重启/重投不重复通知或业务执行**：丢回执后重投命中同一 `delivery_key`
   （`task:{task_id}:final`）的原回执（同 id/seq、feed 水位不变、唯一通知行），Skill 子进程只跑
   一次、终态事件只有一条；非 WEBCHAT 路由仍走 Gateway 出站，不产生 Runtime 结果发件。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from muad_agent_runtime.api.tool_results import router as tool_results_router
from muad_agent_runtime.api.webchat_history import router as webchat_history_router
from muad_agent_runtime.infrastructure.db import get_session as runtime_get_session
from muad_agent_runtime.infrastructure.models.async_tools import ToolOperation, ToolResultInbox
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    ChannelNotification,
    ChannelNotificationFeed,
    Conversation,
    RunRecord,
)
from muad_agent_worker.application.delivery_routes import upsert_delivery_route
from muad_agent_worker.application.ports import PlatformSettingsSnapshot
from muad_agent_worker.delivery.client import (
    HttpDeliveryClient,
    HttpWebchatInboxClient,
)
from muad_agent_worker.delivery.service import WEBCHAT_CHANNEL, DeliveryLoop
from muad_agent_worker.infrastructure import db as worker_db
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.infrastructure.models.runtime_operations import RuntimeResultOutbox
from muad_agent_worker.infrastructure.models.task import TaskEvent, TaskExecution
from muad_agent_worker.results.dispatcher import ResultDispatcher, ResultDispatchPolicy
from muad_agent_worker.worker.executor import SkillTaskExecutor
from muad_agent_worker.worker.service import WorkerLoop
from muad_api import install_api_foundation
from muad_common import SharedSettings
from muad_contracts import (
    BUILTIN_CHANNEL_NAMES,
    ChannelName,
    CompletionMode,
    ControlOutboxStatus,
    DeliveryRouteInput,
    DeliveryStatus,
    OperationStatus,
    RunStatus,
    TaskStatus,
)
from muad_contracts.platform_settings import default_platform_settings, parse_platform_settings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_worker.helpers import DELIVERED_ENVELOPE
from tests.acceptance.runtime.conftest import _Server

pytestmark = pytest.mark.integration

INSTANCE = "b268-worker"
SERVICE_TOKEN = "b268-internal-token"
TENANT_HEADER = "X-Tenant-Id"
HASH = "sha256:" + "a" * 64
SKILL_CHECKSUM = "sha256:" + "b" * 64
ARTIFACT_CHECKSUM = "sha256:" + "c" * 64
TASK_RESULTS_PATH = "/internal/webchat/task-results"
NOTIFICATIONS_PATH = "/internal/webchat/notifications"
TERMINAL_EVENT_TYPES = ("COMPLETED", "FAILED", "CANCELLED", "DEADLINE_EXCEEDED")


# --------------------------------------------------------------------------------------
# 隔离 PostgreSQL / Redis + Worker 进程环境
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IsolatedWorker:
    factory: async_sessionmaker[AsyncSession]
    database_url: str
    redis_url: str
    tmp_path: Path


@pytest.fixture
async def isolated_worker(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[IsolatedWorker]:
    """隔离 PostgreSQL/Redis 号位 + Worker 进程环境；跑完 drop，不触碰共享 dev 库。"""
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", SERVICE_TOKEN)
    await worker_db.dispose_engine()
    try:
        yield IsolatedWorker(
            factory=get_session_factory(),
            database_url=async_tool_datastore["database_url"],
            redis_url=async_tool_datastore["redis_url"],
            tmp_path=tmp_path,
        )
    finally:
        # 退出时清缓存：后续套件按已还原的环境重建 engine，不指向已 drop 的库。
        await worker_db.dispose_engine()


@pytest.fixture
def runtime_probe(isolated_worker: IsolatedWorker) -> Iterator[str]:
    """真实 Runtime HTTP 应用（真实路由 + 真实服务门控）跑在真实 TCP 端口上。"""
    app = FastAPI()
    install_api_foundation(app)
    app.include_router(tool_results_router)
    app.include_router(webchat_history_router)
    database_url = isolated_worker.database_url

    async def sessions() -> AsyncIterator[AsyncSession]:
        # 服务线程有自己的事件循环：engine 必须由那个循环拥有（不能借测试循环的 engine）。
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                yield session
        finally:
            await engine.dispose()

    app.dependency_overrides[runtime_get_session] = sessions
    server = _Server(app)
    url = server.start()
    try:
        yield url
    finally:
        server.stop()


@pytest.fixture
def unacked_probe(runtime_probe: str) -> Iterator[str]:
    """把真实 Runtime 藏在故障代理后面：转发请求，但回执不含持久确认（响应丢失语义）。"""
    proxy = FastAPI()

    @proxy.post(TASK_RESULTS_PATH)
    async def forward(request: Request) -> dict[str, Any]:
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.post(
                runtime_probe + TASK_RESULTS_PATH,
                content=await request.body(),
                headers=dict(request.headers),
            )
        assert response.status_code == 200, response.text
        return {"code": "0", "data": {"persisted": False, "duplicate": False}}

    server = _Server(proxy)
    url = server.start()
    try:
        yield url
    finally:
        server.stop()


@pytest.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(trust_env=False) as client:
        yield client


# --------------------------------------------------------------------------------------
# 真实来源：WEBCHAT 会话/Run/操作/产物 + 可领取 Task + 现场 Skill 脚本
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OfflineTask:
    tenant_id: str
    user_id: uuid.UUID
    agent_id: uuid.UUID
    entry_id: uuid.UUID
    conversation_id: uuid.UUID
    run_id: uuid.UUID
    operation_id: uuid.UUID
    task_id: uuid.UUID
    artifact_id: uuid.UUID
    skill_artifact_id: uuid.UUID
    ready_dir: Path
    marker_file: Path


def _worker_settings() -> SharedSettings:
    return SharedSettings(
        worker_execution_concurrency=1,
        task_heartbeat_sec=1,
        task_cancel_check_sec=1,
    )


def _skill_package(tmp_path: Path, artifact_id: uuid.UUID) -> tuple[Path, Path]:
    """现场 Skill 包：每次执行追加一行标记（可数的真实业务执行事实）。

    脚本按既有结果协议声明 `result_artifact_id`（大结果外置引用）；该引用指向的
    `runtime.artifact` 行由测试现场种下——制品登记是 TASK-053 的契约，本任务只证明
    Worker 提交的是**真实产物引用**、Runtime 核属后投影进通知。
    """
    scripts = tmp_path / "skill" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "skill-runs.txt"
    (scripts / "main.py").write_text(
        "import json, os, sys\n"
        f"with open({str(marker)!r}, 'a', encoding='utf-8') as handle:\n"
        "    handle.write(os.environ['MUAD_TASK_ID'] + '\\n')\n"
        "payload = json.loads(sys.stdin.read() or '{}')\n"
        "print(json.dumps({'summary': '离线结果', 'customers': payload.get('customers', []),"
        f" 'result_artifact_id': {str(artifact_id)!r}"
        "}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    return tmp_path / "skill", marker


def _snapshot(skill_artifact_id: uuid.UUID) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "budget": {"retry_safety": "IDEMPOTENT"},
        "skills": [
            {
                "artifact_id": str(skill_artifact_id),
                "storage_key": "skills/b268/probe.zip",
                "checksum": SKILL_CHECKSUM,
            }
        ],
    }


async def _seed_offline_task(
    isolated: IsolatedWorker,
    *,
    channel: ChannelName = "WEBCHAT",
    with_source_run: bool = True,
    entry_attribution: bool = True,
) -> OfflineTask:
    """真实行种子：入口会话 + 终态 Run + DETACH 操作 + 产物 + 待领取 Task。"""
    factory = isolated.factory
    tenant_id = f"b268-{uuid.uuid4().hex[:12]}"
    user_id, agent_id, entry_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conversation_id, run_id, operation_id, task_id, artifact_id, skill_artifact_id = (
        uuid.uuid4() for _ in range(6)
    )
    now = datetime.now(UTC)
    ready_dir, marker_file = _skill_package(isolated.tmp_path, artifact_id)
    async with factory() as session, session.begin():
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                title="离线任务",
                status="ACTIVE",
                last_seq=0,
                channel_account_id=entry_id if entry_attribution else None,
                external_thread_id="web-thread",
                last_message_at=now,
            )
        )
        await session.flush()
        if with_source_run:
            session.add(
                RunRecord(
                    id=run_id,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    status=RunStatus.COMPLETED,
                    input_text="离线任务",
                    trace_id=f"b268-{uuid.uuid4().hex[:8]}",
                    start_time=now,
                    end_time=now,
                    deadline_at=now + timedelta(hours=1),
                )
            )
            await session.flush()
            session.add(
                ToolOperation(
                    id=operation_id,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    actor_user_id=user_id,
                    source_tool_call_id="call-1",
                    completion_mode=CompletionMode.DETACH,
                    status=OperationStatus.SUBMITTED,
                    task_id=task_id,
                    task_snapshot_hash=HASH,
                    input_hash=HASH,
                    submission_json={},
                )
            )
            session.add(
                Artifact(
                    id=artifact_id,
                    tenant_id=tenant_id,
                    run_id=None,
                    task_id=task_id,
                    conversation_id=conversation_id,
                    artifact_type="TOOL_RESULT",
                    storage_key=f"tool-results/{artifact_id}",
                    media_type="text/plain",
                    size=18,
                    checksum=ARTIFACT_CHECKSUM,
                    metadata_json={"kind": "DOCUMENT", "filename": "result.txt"},
                )
            )
        route_id = await upsert_delivery_route(
            session,
            tenant_id=tenant_id,
            platform_user_id=user_id,
            route=DeliveryRouteInput(
                channel=channel,
                bot_id=str(entry_id),
                external_user_id=str(user_id),
                external_conversation_id=str(conversation_id),
            ),
        )
        session.add(
            TaskExecution(
                id=task_id,
                tenant_id=tenant_id,
                source_run_id=run_id if with_source_run else None,
                source_operation_id=operation_id if with_source_run else None,
                source_tool_call_id="call-1" if with_source_run else None,
                completion_mode=CompletionMode.DETACH if with_source_run else None,
                agent_id=agent_id,
                actor_user_id=user_id,
                intent_key="offline_probe",
                skill_id=uuid.uuid4(),
                skill_artifact_id=skill_artifact_id,
                trigger_type="IMMEDIATE",
                execution_mode="ASYNC",
                task_type="SKILL",
                status=str(TaskStatus.QUEUED),
                input_json={"customers": ["A"]},
                execution_snapshot_schema_version=1,
                execution_snapshot_json=_snapshot(skill_artifact_id),
                snapshot_hash=HASH,
                idempotency_key=f"b268-{task_id}",
                priority=100,
                attempt=0,
                max_attempts=1,
                not_before=now,
                deadline_at=now + timedelta(hours=1),
                delivery_route_id=route_id,
                delivery_mode="FINAL_ONLY",
                delivery_status=str(DeliveryStatus.PENDING),
                delivery_key=f"task:{task_id}:final",
                delivery_attempts=0,
                result_artifact_id=artifact_id if with_source_run else None,
            )
        )
    return OfflineTask(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=agent_id,
        entry_id=entry_id,
        conversation_id=conversation_id,
        run_id=run_id,
        operation_id=operation_id,
        task_id=task_id,
        artifact_id=artifact_id,
        skill_artifact_id=skill_artifact_id,
        ready_dir=ready_dir,
        marker_file=marker_file,
    )


# --------------------------------------------------------------------------------------
# 真实组件装配：Worker / 结果发件 / 投递循环
# --------------------------------------------------------------------------------------


class _ReadyArtifactResolver:
    """Skill 制品缓存接缝：返回测试现场的真实脚本目录（进程边界仍是真实子进程）。"""

    def __init__(self, ready_dir: Path) -> None:
        self._ready_dir = ready_dir

    async def ensure(self, *, artifact_id: str, storage_key: str, checksum: str) -> Path:
        return self._ready_dir


class _ForbiddenGatewayClient:
    """WEBCHAT 投递不得碰 Gateway 出站：一旦被调用即失败（socket 写入不是持久送达）。"""

    async def deliver(self, request: Any) -> Any:
        raise AssertionError("webchat delivery must not call the gateway delivery path")


class _TaskSettingsClient:
    """注入的桩快照源：按 schema 构造一份只覆盖 `task` 分组的平台设置文档。"""

    def __init__(self, **task_overrides: int) -> None:
        self._snapshot = PlatformSettingsSnapshot(
            revision=1,
            settings=asdict(parse_platform_settings({"task": task_overrides})),
        )

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return self._snapshot


async def _run_worker_once(isolated: IsolatedWorker, seed: OfflineTask) -> None:
    """真实领取 + 监督 + 真实 Skill 子进程；终态事务提交后返回。"""
    worker = WorkerLoop(
        isolated.factory,
        _worker_settings(),
        executor=SkillTaskExecutor(_ReadyArtifactResolver(seed.ready_dir)),
        instance_id=INSTANCE,
    )
    try:
        assert await worker.run_once() == seed.task_id
    finally:
        await worker.aclose()


async def _dispatch_result(
    isolated: IsolatedWorker, client: httpx.AsyncClient, runtime_url: str
) -> None:
    """真实结果发件：结果被 Runtime 受理（`tool_result_inbox`）后发件才置 SENT。"""
    dispatcher = ResultDispatcher(
        isolated.factory,
        client,
        runtime_url,
        service_token=SERVICE_TOKEN,
        policy=ResultDispatchPolicy(),
        instance_id=f"b268-dispatch:{uuid.uuid4()}",
    )
    # 发件窗口用进程钟判定；终态写入取 PG 服务端时钟，留一秒余量避开两台时钟的微小偏差。
    assert await dispatcher.run_once(now=datetime.now(UTC) + timedelta(seconds=1)) == 1


def _delivery_loop(
    isolated: IsolatedWorker,
    client: httpx.AsyncClient,
    *,
    gateway: Any | None = None,
    inbox_url: str | None = None,
) -> DeliveryLoop:
    """一条真实投递循环：Gateway 出站默认禁用（WEBCHAT 不得走 socket 写入）。"""
    return DeliveryLoop(
        isolated.factory,
        gateway if gateway is not None else _ForbiddenGatewayClient(),
        _worker_settings(),
        settings_client=_TaskSettingsClient(),
        webchat_inbox=HttpWebchatInboxClient(
            inbox_url or "http://127.0.0.1:9", client, service_token=SERVICE_TOKEN
        ),
    )


# --------------------------------------------------------------------------------------
# 真实行事实的读取（独立连接用于证明「提交后才回执」）
# --------------------------------------------------------------------------------------


async def _task_row(factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID) -> TaskExecution:
    async with factory() as session:
        row = await session.get(TaskExecution, task_id)
    assert row is not None
    return row


async def _publication(
    factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID
) -> RuntimeResultOutbox | None:
    async with factory() as session:
        return await session.scalar(
            sa.select(RuntimeResultOutbox)
            .where(RuntimeResultOutbox.task_id == task_id)
            .order_by(RuntimeResultOutbox.task_event_seq.desc())
            .limit(1)
        )


async def _notification_rows(
    factory: async_sessionmaker[AsyncSession], seed: OfflineTask
) -> list[ChannelNotification]:
    async with factory() as session:
        rows = await session.scalars(
            sa.select(ChannelNotification)
            .where(
                ChannelNotification.tenant_id == seed.tenant_id,
                ChannelNotification.user_id == seed.user_id,
            )
            .order_by(ChannelNotification.seq)
        )
    return list(rows)


async def _feed_last_seq(factory: async_sessionmaker[AsyncSession], seed: OfflineTask) -> int | None:
    async with factory() as session:
        last = await session.scalar(
            sa.select(ChannelNotificationFeed.last_seq).where(
                ChannelNotificationFeed.tenant_id == seed.tenant_id,
                ChannelNotificationFeed.user_id == seed.user_id,
            )
        )
    return None if last is None else int(last)


async def _event_types(
    factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID, event_types: tuple[str, ...]
) -> list[str]:
    async with factory() as session:
        rows = await session.scalars(
            sa.select(TaskEvent.event_type)
            .where(TaskEvent.task_id == task_id, TaskEvent.event_type.in_(event_types))
            .order_by(TaskEvent.seq)
        )
    return list(rows)


async def _committed_notification(database_url: str, seed: OfflineTask) -> Any:
    """独立连接读通知行：响应返回时它必须已经提交（持久入箱后才回 persisted）。"""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT id, seq, kind, task_id, user_id, conversation_id, entry_id,"
                    " delivery_key, delivery_state, read_at, artifact_ids, event_id"
                    " FROM runtime.channel_notification WHERE tenant_id = :tenant AND task_id = :task"
                ),
                {"tenant": seed.tenant_id, "task": seed.task_id},
            )
            return result.one()
    finally:
        await engine.dispose()


async def _inbox_row(
    factory: async_sessionmaker[AsyncSession], seed: OfflineTask
) -> ToolResultInbox:
    async with factory() as session:
        row = await session.scalar(
            sa.select(ToolResultInbox).where(
                ToolResultInbox.tenant_id == seed.tenant_id,
                ToolResultInbox.task_id == seed.task_id,
            )
        )
    assert row is not None
    return row


async def _notification_http(
    client: httpx.AsyncClient, runtime_url: str, seed: OfflineTask, **params: Any
) -> dict[str, Any]:
    response = await client.get(
        runtime_url + NOTIFICATIONS_PATH,
        headers={TENANT_HEADER: seed.tenant_id, "X-Internal-Service": SERVICE_TOKEN},
        params={"user_id": str(seed.user_id), "page_size": 10, **params},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


# --------------------------------------------------------------------------------------
# 1. 持久入箱确认后才完成投递；送达/已读分开；按原来源会话定位
# --------------------------------------------------------------------------------------


async def test_b268_offline_result_enters_the_persistent_inbox_only_after_runtime_acceptance(
    isolated_worker: IsolatedWorker,
    runtime_probe: str,
    http_client: httpx.AsyncClient,
) -> None:
    isolated = isolated_worker
    factory = isolated.factory
    seed = await _seed_offline_task(isolated)

    # 真实 Worker：真实领取 → 真实 Skill 子进程 → 终态事务（结果发件与终态同事务）
    await _run_worker_once(isolated, seed)
    task = await _task_row(factory, seed.task_id)
    assert task.status == str(TaskStatus.COMPLETED)
    assert task.result_json == {"summary": "离线结果", "customers": ["A"]}
    assert seed.marker_file.read_text(encoding="utf-8").splitlines() == [str(seed.task_id)]
    publication = await _publication(factory, seed.task_id)
    assert publication is not None and publication.status is ControlOutboxStatus.PENDING
    assert publication.payload_json["terminal_status"] == str(TaskStatus.COMPLETED)

    # 结果还没被 Runtime 受理：不成为投递候选（不消耗投递尝试、不写通知）
    loop = _delivery_loop(isolated, http_client, inbox_url=runtime_probe)
    assert await loop.run_once() is None
    pending = await _task_row(factory, seed.task_id)
    assert pending.delivery_status == str(DeliveryStatus.PENDING)
    assert pending.delivery_attempts == 0, "结果未受理前不得消耗投递尝试"
    assert await _notification_rows(factory, seed) == []

    # 真实结果发件 → 真实 Runtime HTTP：结果受理（tool_result_inbox）
    await _dispatch_result(isolated, http_client, runtime_probe)
    assert (await _publication(factory, seed.task_id)).status is ControlOutboxStatus.SENT
    inbox = await _inbox_row(factory, seed)
    assert inbox.run_id == seed.run_id and inbox.late is True

    # 受理之后：投递 = 提交进持久用户收件箱；persisted=true 才算送达
    outcome = await loop.run_once()
    assert outcome is not None and outcome.sent is True and outcome.http_status == 200

    delivered = await _task_row(factory, seed.task_id)
    assert delivered.delivery_status == str(DeliveryStatus.SENT)
    assert delivered.delivered_at is not None
    assert delivered.delivery_attempts == 1

    row = await _committed_notification(isolated.database_url, seed)
    assert row.seq == 1 and row.kind == "TASK_RESULT"
    assert row.task_id == seed.task_id
    assert row.user_id == seed.user_id
    assert row.conversation_id == seed.conversation_id, "通知按原来源 conversation 定位"
    assert row.entry_id == seed.entry_id
    assert row.delivery_key == f"task:{seed.task_id}:final"
    assert row.delivery_state == "NOT_REQUIRED", "网页入箱没有出站投递意图"
    assert row.read_at is None, "送达不是已读"
    assert [uuid.UUID(str(value)) for value in row.artifact_ids] == [seed.artifact_id]
    assert row.event_id == inbox.receipt_event_id
    assert await _feed_last_seq(factory, seed) == 1

    page = await _notification_http(http_client, runtime_probe, seed)
    assert page["unread_count"] == 1 and page["next_seq"] == 1
    item = page["items"][0]
    assert item["kind"] == "TASK_RESULT" and item["task_id"] == str(seed.task_id)
    assert item["conversation_id"] == str(seed.conversation_id)
    assert item["read_at"] is None
    assert [attachment["artifact_id"] for attachment in item["attachments"]] == [
        str(seed.artifact_id)
    ]

    # 显式已读是独立事实：不改投递状态，也不被投递状态替代
    read = await http_client.post(
        f"{runtime_probe}{NOTIFICATIONS_PATH}/{item['id']}/read",
        headers={TENANT_HEADER: seed.tenant_id, "X-Internal-Service": SERVICE_TOKEN},
        params={"user_id": str(seed.user_id)},
    )
    assert read.status_code == 200, read.text
    assert read.json()["data"]["read_at"] is not None
    after_read = await _committed_notification(isolated.database_url, seed)
    assert after_read.read_at is not None
    assert after_read.delivery_state == "NOT_REQUIRED"
    assert (await _task_row(factory, seed.task_id)).delivery_status == str(DeliveryStatus.SENT)


# --------------------------------------------------------------------------------------
# 2. 丢回执后重投：命中原回执，不重复通知或业务执行
# --------------------------------------------------------------------------------------


async def test_b268_redelivery_after_a_lost_receipt_hits_the_original_notification_once(
    isolated_worker: IsolatedWorker,
    runtime_probe: str,
    unacked_probe: str,
    http_client: httpx.AsyncClient,
) -> None:
    isolated = isolated_worker
    factory = isolated.factory
    seed = await _seed_offline_task(isolated)
    await _run_worker_once(isolated, seed)
    await _dispatch_result(isolated, http_client, runtime_probe)

    base = datetime.now(UTC)
    first = _delivery_loop(isolated, http_client, inbox_url=unacked_probe)
    outcome = await first.run_once(now=base)
    assert outcome is not None and outcome.sent is False, "2xx 没有持久确认不算送达"
    unacked = await _task_row(factory, seed.task_id)
    assert unacked.delivery_status == str(DeliveryStatus.PENDING)
    assert unacked.delivery_attempts == 1
    assert await _event_types(factory, seed.task_id, ("DELIVERY_RETRY",)) == ["DELIVERY_RETRY"]

    # 代理吞掉了回执：通知其实已经进箱 ⇒ 重投必须命中同一条（不新占 seq、不新增未读）
    written = await _notification_rows(factory, seed)
    assert len(written) == 1
    original = written[0]

    restarted = _delivery_loop(isolated, http_client, inbox_url=runtime_probe)
    replay = await restarted.run_once(now=base + timedelta(seconds=11))
    assert replay is not None and replay.sent is True

    rows = await _notification_rows(factory, seed)
    assert len(rows) == 1, "重投不得新增通知行"
    assert rows[0].id == original.id and rows[0].seq == original.seq
    assert rows[0].read_at is None
    assert await _feed_last_seq(factory, seed) == 1

    task = await _task_row(factory, seed.task_id)
    assert task.delivery_status == str(DeliveryStatus.SENT)
    assert task.delivery_attempts == 2, "只有投递尝试推进，业务执行没有重跑"
    assert task.attempt == 1
    assert seed.marker_file.read_text(encoding="utf-8").splitlines() == [str(seed.task_id)]
    assert await _event_types(factory, seed.task_id, TERMINAL_EVENT_TYPES) == ["COMPLETED"]
    assert await _event_types(factory, seed.task_id, ("CLAIMED",)) == ["CLAIMED"]
    assert await _event_types(factory, seed.task_id, ("DELIVERY_SENT",)) == ["DELIVERY_SENT"]


# --------------------------------------------------------------------------------------
# 3. 缺少来源 Run / 入口归属：显式失败，不编造会话
# --------------------------------------------------------------------------------------


async def test_b268_webchat_delivery_without_source_run_or_entry_fails_explicitly(
    isolated_worker: IsolatedWorker,
    runtime_probe: str,
    http_client: httpx.AsyncClient,
) -> None:
    isolated = isolated_worker
    factory = isolated.factory

    # 没有来源 Run 的 WEBCHAT 投递意图（如计划触发的任务）：无法定位会话，显式终态失败
    orphan = await _seed_offline_task(isolated, with_source_run=False)
    await _run_worker_once(isolated, orphan)
    loop = _delivery_loop(isolated, http_client, inbox_url=runtime_probe)
    outcome = await loop.run_once()
    assert outcome is not None and outcome.sent is False
    failed = await _task_row(factory, orphan.task_id)
    assert failed.delivery_status == str(DeliveryStatus.FAILED)
    assert failed.delivery_attempts == default_platform_settings().task.delivery_max_attempts
    events = await _event_types(factory, orphan.task_id, ("DELIVERY_FAILED",))
    assert events == ["DELIVERY_FAILED"]
    assert await _notification_rows(factory, orphan) == [], "没有来源就不许编造会话通知"

    # 会话没有入口归属：Runtime 核属拒绝（404），投递确定性失败且不落通知行
    unattributed = await _seed_offline_task(isolated, entry_attribution=False)
    await _run_worker_once(isolated, unattributed)
    await _dispatch_result(isolated, http_client, runtime_probe)
    rejected = await loop.run_once()
    assert rejected is not None and rejected.sent is False
    assert rejected.http_status == 404
    assert (await _task_row(factory, unattributed.task_id)).delivery_status == str(
        DeliveryStatus.FAILED
    )
    assert await _notification_rows(factory, unattributed) == []


# --------------------------------------------------------------------------------------
# 4. 非 WEBCHAT 路由：Gateway 出站不变，不产生 Runtime 结果发件
# --------------------------------------------------------------------------------------


async def test_b268_non_webchat_routes_keep_gateway_delivery_without_runtime_publication(
    isolated_worker: IsolatedWorker,
    http_client: httpx.AsyncClient,
) -> None:
    isolated = isolated_worker
    factory = isolated.factory
    seed = await _seed_offline_task(isolated, channel="WECOM")
    await _run_worker_once(isolated, seed)

    assert await _publication(factory, seed.task_id) is None, "企微投递不需要 Runtime 结果发件"

    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=DELIVERED_ENVELOPE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as gateway_http:
        gateway = HttpDeliveryClient("http://im-gateway", gateway_http)
        # 入箱端口指向死地址：若 WEBCHAT 分支被误用，这条投递不可能成功
        loop = _delivery_loop(isolated, http_client, gateway=gateway, inbox_url="http://127.0.0.1:9")
        outcome = await loop.run_once()
    assert outcome is not None and outcome.sent is True
    assert len(calls) == 1
    body = json.loads(calls[0].content)
    assert body["route"]["channel"] == "WECOM"
    assert (await _task_row(factory, seed.task_id)).delivery_status == str(DeliveryStatus.SENT)
    assert await _notification_rows(factory, seed) == []


# --------------------------------------------------------------------------------------
# 5. 真值校验：WEBCHAT 通道名只有一处来源（worker 侧）
# --------------------------------------------------------------------------------------


def test_b268_webchat_channel_constant_matches_the_shared_vocabulary() -> None:
    assert WEBCHAT_CHANNEL == "WEBCHAT"
    # 通道名是开放 string（S-12：新增通道只写适配器）：内建名只在 enums 一处声明，
    # 不再以 `Literal` 封闭（`ChannelName = str`，没有 `__args__`）。
    assert BUILTIN_CHANNEL_NAMES == ("WECOM", "WEBCHAT")
