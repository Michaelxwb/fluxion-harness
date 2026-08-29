"""TASK-014（Phase 5）Chat Workspace 后端端点（S-15 / FEAT-P5-10）。

真实边界（不 mock 引擎/存储/worker/DB/HTTP）：
- 真实 PG registry（与 DBOS sysdb 同库，S-11 同款装配）承载全部 workspace 数据源
  （AgentDefinition/workflow_run 投影/personal_memory/user_profiles/session_memory）；
- 审批 decide → 真实 DBOSClient send（durable notifications）→ 真实 worker 子进程
  唤醒并跑完 pin-flow（写操作端到端生效）；
- 真实 HTTP（ASGITransport + 统一 envelope）+ Bearer Chat Access Token 鉴权
  （身份来自 token，非 header）。

覆盖：7 组 `/api/v1/workspace/*` 端点读 + 写（decide/correct/delete/
updateProfile/setAutoLearn）、tenant scope（双租户隔离）、鉴权失败 401 envelope。
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert
from tests.workflow_runtime.worker_fixtures import (
    WorkerProcess,
    WorkflowTestClient,
    purge_stale_enqueued,
    purge_stale_workflows,
    worker_db_url,
)

from fluxion.api.workspace import create_app as create_workspace_app
from fluxion.registry.channel_store import ChatAccessRecord, PlatformUserRecord
from fluxion.registry.schema import personal_memory, session_memory
from fluxion.registry.sqlalchemy_store import PostgreSQLRegistryStore
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.runtime.workflow_dbos import DBOS_QUEUE_NAME, workflow_run_id
from fluxion.services.workspace_app import (
    DbosWorkspaceSignalSender,
    WorkspaceApplicationService,
)

REGISTRY_BOOTSTRAP = "tests.workflow_runtime.worker_fixtures:install_registry_worker_bootstrap"
TENANT_A = "tenant-ws-a"
TENANT_B = "tenant-ws-b"
USER_A = "user-ws-a"
USER_B = "user-ws-b"
TOKEN_A = "token-ws-a"
TOKEN_B = "token-ws-b"

AGENT_CS = "agent-cs"
PIN_FLOW_ID = "pin-flow"


def _pin_flow_spec() -> dict[str, object]:
    """pin-flow：prepare(stamp) → review(human_task 挂起) → finalize(stamp)。"""
    return {
        "name": "pin-flow",
        "steps": [
            {
                "id": "prepare",
                "type": "capability",
                "capability_ref": "skill:stamp@1",
                "input": {"seconds": 0.2, "marker": "ws"},
            },
            {
                "id": "review",
                "type": "human_task",
                "depends_on": ["prepare"],
                "assignee": "user:alice",
                "message": "审批",
            },
            {
                "id": "finalize",
                "type": "capability",
                "depends_on": ["review"],
                "capability_ref": "skill:stamp@1",
                "input": {"seconds": 0.2},
            },
        ],
    }


def _agent_spec() -> dict[str, object]:
    return {
        "name": "客服助手",
        "system_prompt": "解答常见问题",
        "owner": "builder-ws",
        "model_ref": {"id": "dev.echo", "version": "1"},
        "description": "解答常见问题、发起任务",
        "capabilities": [
            {"capability_ref": "skill:faq@1", "version_pin": "1", "type": "skill"}
        ],
    }


def _asyncpg_db_url(db_url: str) -> str:
    return db_url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _seed_workspace_data(store: PostgreSQLRegistryStore) -> None:
    """双租户数据：用户/token、Agent 目录、workflow 定义、会话/记忆/画像。"""
    now = datetime.now(UTC)
    for tenant, user, token in (
        (TENANT_A, USER_A, TOKEN_A),
        (TENANT_B, USER_B, TOKEN_B),
    ):
        await store.create_platform_user(
            PlatformUserRecord(
                tenant_id=tenant, platform_user_id=user, display_name=user, created_at=now
            )
        )
        await store.create_chat_access(
            ChatAccessRecord(
                access_id=f"access-{uuid.uuid4().hex[:8]}",
                tenant_id=tenant,
                platform_user_id=user,
                agent_id=AGENT_CS,
                token_hash=_token_hash(token),
                created_at=now,
            )
        )

    # AgentDefinition 产品目录：tenant-a 发布 1 个 + 草稿 1 个（草稿不得出现）
    await store.put(
        ResourceDefinition(
            tenant_id=TENANT_A,
            kind=ResourceKind.AGENT_DEFINITION,
            id=AGENT_CS,
            version="1",
            status=ResourceStatus.DRAFT,
            spec_json=_agent_spec(),
        )
    )
    await store.publish(
        ResourceKind.AGENT_DEFINITION, AGENT_CS, tenant_id=TENANT_A, version="1"
    )
    await store.put(
        ResourceDefinition(
            tenant_id=TENANT_A,
            kind=ResourceKind.AGENT_DEFINITION,
            id="agent-draft",
            version="1",
            status=ResourceStatus.DRAFT,
            spec_json=_agent_spec(),
        )
    )
    # workflow 定义（tenant-a；tenant-b 无）
    await store.put(
        ResourceDefinition(
            tenant_id=TENANT_A,
            kind=ResourceKind.WORKFLOW,
            id=PIN_FLOW_ID,
            version="1",
            status=ResourceStatus.DRAFT,
            spec_json=_pin_flow_spec(),
        )
    )
    await store.publish(
        ResourceKind.WORKFLOW, PIN_FLOW_ID, tenant_id=TENANT_A, version="1"
    )

    # 会话记忆（tenant-a user-a 一段对话；tenant-b 独立会话）
    async with store.engine.begin() as conn:
        await conn.execute(
            insert(session_memory),
            [
                {
                    "tenant_id": TENANT_A,
                    "user_id": USER_A,
                    "session_id": "sess-ws-1",
                    "execution_id": "exec-ws-1",
                    "role": "user",
                    "content": "帮我整理周报",
                    "tokens": 8,
                    "level": "l1",
                    "created_at": now,
                },
                {
                    "tenant_id": TENANT_A,
                    "user_id": USER_A,
                    "session_id": "sess-ws-1",
                    "execution_id": "exec-ws-1",
                    "role": "assistant",
                    "content": "周报已整理完成",
                    "tokens": 10,
                    "level": "l1",
                    "created_at": now,
                },
                {
                    "tenant_id": TENANT_B,
                    "user_id": USER_B,
                    "session_id": "sess-ws-b",
                    "execution_id": "exec-ws-b",
                    "role": "user",
                    "content": "tenant-b 会话",
                    "tokens": 5,
                    "level": "l1",
                    "created_at": now,
                },
            ],
        )
        # personal memory（tenant-a 2 条；tenant-b 1 条）
        await conn.execute(
            insert(personal_memory),
            [
                {
                    "tenant_id": TENANT_A,
                    "user_id": USER_A,
                    "memory_type": "episodic",
                    "content": "用户偏好简洁回复",
                    "embedding": None,
                    "source_session_id": "sess-ws-1",
                    "source_range_hash": None,
                    "learning_enabled": True,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "tenant_id": TENANT_A,
                    "user_id": USER_A,
                    "memory_type": "semantic",
                    "content": "用户所在时区为 UTC+8",
                    "embedding": None,
                    "source_session_id": "sess-ws-1",
                    "source_range_hash": None,
                    "learning_enabled": True,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "tenant_id": TENANT_B,
                    "user_id": USER_B,
                    "memory_type": "semantic",
                    "content": "tenant-b 记忆",
                    "embedding": None,
                    "source_session_id": "sess-ws-b",
                    "source_range_hash": None,
                    "learning_enabled": True,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
    # 用户画像（tenant-a user-a）
    await store.put_user_profile(
        tenant_id=TENANT_A,
        platform_user_id=USER_A,
        profile_json={
            "display_name": "用户A",
            "bio": "",
            "timezone": "Asia/Shanghai",
            "language": "zh-CN",
        },
    )


def _wait_durable_wait_checkpoint(db_url: str, run_id: str, *, timeout: float) -> None:
    """等 `dbos.operation_outputs` 出现 `DBOS.sleep` 行（human_task 挂起已 durable）。"""
    import psycopg

    deadline = time.monotonic() + timeout
    with psycopg.connect(db_url, autocommit=True) as conn:
        while time.monotonic() < deadline:
            row = conn.execute(
                "SELECT 1 FROM dbos.operation_outputs "
                "WHERE workflow_uuid = %s AND function_name = 'DBOS.sleep' LIMIT 1",
                (run_id,),
            ).fetchone()
            if row:
                return
            time.sleep(0.2)
    raise AssertionError(
        f"workflow {run_id} did not reach a durable sleep checkpoint within {timeout}s"
    )


async def _start_blocked_pin_flow(
    store: PostgreSQLRegistryStore, *, tenant_id: str, vmid: str
) -> tuple[WorkerProcess, str]:
    """真实 worker start 一个 pin-flow 并阻塞在 review（返回 worker + run_id）。"""
    del store  # 定义已在 registry（REGISTRY_BOOTSTRAP worker 侧同库解析）
    purge_stale_enqueued(worker_db_url(), DBOS_QUEUE_NAME)
    execution_id = f"{vmid}-{uuid.uuid4().hex[:8]}"
    run_id = workflow_run_id(PIN_FLOW_ID, execution_id)
    worker = WorkerProcess(
        [
            "start",
            "--workflow-id",
            PIN_FLOW_ID,
            "--version",
            "1",
            "--execution-id",
            execution_id,
            "--tenant",
            tenant_id,
            "--await-timeout",
            "180",
        ],
        extra_env={"DBOS__VMID": vmid},
        bootstrap=REGISTRY_BOOTSTRAP,
    )
    worker.wait_for("STARTED", timeout=30.0)
    _wait_durable_wait_checkpoint(worker_db_url(), run_id, timeout=20.0)
    return worker, run_id


@asynccontextmanager
async def _api_stack(
    store: PostgreSQLRegistryStore,
) -> AsyncIterator[tuple[AsyncClient, DbosWorkspaceSignalSender]]:
    """真实 ASGI 栈：workspace app + DBOSClient signal sender（免 launch）。"""
    sender = DbosWorkspaceSignalSender(worker_db_url())
    service = WorkspaceApplicationService(store, signal_sender=sender)
    app = create_workspace_app(service)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        yield client, sender
    finally:
        await client.aclose()
        await sender.close()


@pytest.fixture
async def store() -> AsyncGenerator[PostgreSQLRegistryStore, None]:
    """真实 PG registry（与 DBOS sysdb 同库）：fluxion 表重建。"""
    db_url = worker_db_url()
    st = PostgreSQLRegistryStore(_asyncpg_db_url(db_url), reset_on_initialize=True)
    await st.initialize()
    purge_stale_workflows(db_url)
    try:
        yield st
    finally:
        await st.close()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _start_worker(store: PostgreSQLRegistryStore) -> tuple[WorkerProcess, str]:
    return await _start_blocked_pin_flow(store, tenant_id=TENANT_A, vmid="ws-worker")


# ---------------------------------------------------------------------------
# S-15 主链路：7 组端点读 + envelope + tenant scope + 鉴权
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s15_agents_tasks_history_read(store: PostgreSQLRegistryStore) -> None:
    """S-15 读路径：agents（产品模型无 RuntimeProfile）/tasks（workflow+chat 统一）/
    tasks 详情 /history（统一时间线）；tenant scope + 鉴权 401。"""
    await _seed_workspace_data(store)
    worker, run_id = await _start_worker(store)
    try:
        async with _api_stack(store) as (client, _sender):
            # ---- agents：产品模型目录 ----
            resp = await client.get("/api/v1/workspace/agents", headers=_auth(TOKEN_A))
            assert resp.status_code == 200
            body = resp.json()
            assert body["code"] == 0
            assert "request_id" in body
            agents = body["data"]["items"]
            assert [a["agent_id"] for a in agents] == [AGENT_CS]
            agent = agents[0]
            assert agent["display_name"] == "客服助手"
            assert agent["description"] == "解答常见问题、发起任务"
            assert agent["capabilities"] == ["skill:faq@1"]
            assert agent["available"] is True
            # 产品模型边界：不得出现 RuntimeProfile / mechanics 字段
            assert "runtime_profile_ref" not in agent
            assert "model_ref" not in agent

            # ---- tasks：workflow（运行中）+ chat（会话）统一列表 ----
            resp = await client.get("/api/v1/workspace/tasks", headers=_auth(TOKEN_A))
            assert resp.status_code == 200
            tasks = resp.json()["data"]["items"]
            by_id = {t["task_id"]: t for t in tasks}
            wf_task = by_id[run_id]
            assert wf_task["kind"] == "workflow"
            assert wf_task["status"] == "running"
            assert wf_task["progress"] == 33  # prepare 1/3 节点完成
            chat_task = by_id["sess-ws-1"]
            assert chat_task["kind"] == "chat"
            assert chat_task["status"] == "succeeded"
            assert chat_task["title"] == "帮我整理周报"

            # ---- tasks 详情 ----
            resp = await client.get(
                f"/api/v1/workspace/tasks/{run_id}", headers=_auth(TOKEN_A)
            )
            assert resp.status_code == 200
            detail = resp.json()["data"]
            assert detail["task_id"] == run_id
            assert detail["status"] == "running"

            # ---- history：对话 + 任务统一时间线（倒序）----
            resp = await client.get("/api/v1/workspace/history", headers=_auth(TOKEN_A))
            assert resp.status_code == 200
            entries = resp.json()["data"]["items"]
            kinds = {e["kind"] for e in entries}
            assert kinds == {"chat", "task"}
            ats = [e["at"] for e in entries]
            assert ats == sorted(ats, reverse=True), "时间线必须时间倒序"
            task_entry = next(e for e in entries if e["kind"] == "task")
            assert task_entry["task_id"] == run_id
            assert task_entry["trace_id"], "任务历史必须关联 trace"
            chat_entry = next(e for e in entries if e["kind"] == "chat")
            assert chat_entry["conversation_id"] == "sess-ws-1"

            # ---- tenant scope：tenant-b 不见 tenant-a 数据 ----
            resp = await client.get("/api/v1/workspace/agents", headers=_auth(TOKEN_B))
            assert resp.json()["data"]["items"] == []
            resp = await client.get("/api/v1/workspace/tasks", headers=_auth(TOKEN_B))
            b_tasks = resp.json()["data"]["items"]
            assert {t["task_id"] for t in b_tasks} == {"sess-ws-b"}
            resp = await client.get("/api/v1/workspace/history", headers=_auth(TOKEN_B))
            assert all(e["conversation_id"] != "sess-ws-1" for e in resp.json()["data"]["items"])

            # ---- 鉴权：无 token / 坏 token → 401 envelope ----
            resp = await client.get("/api/v1/workspace/agents")
            assert resp.status_code == 401
            assert resp.json()["code"] == 46_001
            resp = await client.get(
                "/api/v1/workspace/agents", headers=_auth("bad-token")
            )
            assert resp.status_code == 401
            assert resp.json()["code"] == 46_001
    finally:
        if worker.proc.poll() is None:
            worker.stop()


@pytest.mark.asyncio
async def test_s15_approvals_decide_real_worker(store: PostgreSQLRegistryStore) -> None:
    """S-15 审批：挂起 human_task 列表 + decide → 真实 DBOS signal → worker 完成。"""
    await _seed_workspace_data(store)
    worker, run_id = await _start_worker(store)
    client_driver = WorkflowTestClient(worker_db_url())
    try:
        async with _api_stack(store) as (client, _sender):
            # ---- approvals：挂起的 review human_task ----
            resp = await client.get("/api/v1/workspace/approvals", headers=_auth(TOKEN_A))
            assert resp.status_code == 200
            approvals = resp.json()["data"]["items"]
            assert len(approvals) == 1
            approval = approvals[0]
            assert approval["task_id"] == run_id
            assert approval["assignee"] == "user:alice"
            assert approval["message"] == "审批"
            assert approval["status"] == "pending"
            approval_id = approval["approval_id"]

            # tenant scope：tenant-b 看不到 tenant-a 的审批
            resp = await client.get("/api/v1/workspace/approvals", headers=_auth(TOKEN_B))
            assert resp.json()["data"]["items"] == []

            # ---- decide：POST → 真实 DBOS send → worker 唤醒跑完 ----
            resp = await client.post(
                f"/api/v1/workspace/approvals/{approval_id}/decision",
                headers=_auth(TOKEN_A),
                json={"decision": "approve", "comment": "同意"},
            )
            assert resp.status_code == 200
            assert resp.json()["code"] == 0
            worker.wait_for("RUN_RESULT", timeout=60.0)

            # run 终态 succeeded（真实投影由 worker 写回）
            resp = await client.get(
                f"/api/v1/workspace/tasks/{run_id}", headers=_auth(TOKEN_A)
            )
            assert resp.json()["data"]["status"] == "succeeded"
            assert resp.json()["data"]["progress"] == 100

            # 审批队列清空（run 终态 → 不再挂起）
            resp = await client.get("/api/v1/workspace/approvals", headers=_auth(TOKEN_A))
            assert resp.json()["data"]["items"] == []

            # 跨租户 decide：tenant-b 对 tenant-a 审批 → 404
            resp = await client.post(
                f"/api/v1/workspace/approvals/{approval_id}/decision",
                headers=_auth(TOKEN_B),
                json={"decision": "approve"},
            )
            assert resp.status_code == 404
    finally:
        if worker.proc.poll() is None:
            worker.stop()
        client_driver.close()


@pytest.mark.asyncio
async def test_s15_profile_memory_auto_learn_writes(store: PostgreSQLRegistryStore) -> None:
    """S-15 写路径：profile GET/PUT、memory list/correct/delete、auto-learn GET/PUT。"""
    await _seed_workspace_data(store)
    async with _api_stack(store) as (client, _sender):
        # ---- profile：GET → PUT → GET 生效 ----
        resp = await client.get("/api/v1/workspace/profile", headers=_auth(TOKEN_A))
        assert resp.status_code == 200
        profile = resp.json()["data"]
        assert profile["platform_user_id"] == USER_A
        assert profile["display_name"] == "用户A"
        assert profile["timezone"] == "Asia/Shanghai"
        assert profile["locale"] == "zh-CN"

        resp = await client.put(
            "/api/v1/workspace/profile",
            headers=_auth(TOKEN_A),
            json={
                "platform_user_id": USER_A,
                "display_name": "新名字",
                "timezone": "UTC",
                "locale": "en-US",
            },
        )
        assert resp.status_code == 200
        updated = resp.json()["data"]
        assert updated["display_name"] == "新名字"
        resp = await client.get("/api/v1/workspace/profile", headers=_auth(TOKEN_A))
        assert resp.json()["data"]["display_name"] == "新名字"

        # ---- memory：list → correct → delete ----
        resp = await client.get("/api/v1/workspace/memory", headers=_auth(TOKEN_A))
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) == 2
        first = items[0]
        assert first["content"] == "用户偏好简洁回复"
        assert first["source"] == "episodic"

        resp = await client.patch(
            f"/api/v1/workspace/memory/{first['memory_id']}",
            headers=_auth(TOKEN_A),
            json={"content": "纠正后的记忆"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["content"] == "纠正后的记忆"

        resp = await client.delete(
            f"/api/v1/workspace/memory/{items[1]['memory_id']}",
            headers=_auth(TOKEN_A),
        )
        assert resp.status_code == 200
        resp = await client.get("/api/v1/workspace/memory", headers=_auth(TOKEN_A))
        remaining = resp.json()["data"]["items"]
        assert len(remaining) == 1
        assert remaining[0]["content"] == "纠正后的记忆"

        # tenant scope：tenant-b 只见自己的记忆
        resp = await client.get("/api/v1/workspace/memory", headers=_auth(TOKEN_B))
        assert [m["content"] for m in resp.json()["data"]["items"]] == ["tenant-b 记忆"]

        # ---- auto-learn：GET 默认 true → PUT false → GET false + 落库 ----
        resp = await client.get(
            "/api/v1/workspace/memory/auto-learn", headers=_auth(TOKEN_A)
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == {"enabled": True}
        resp = await client.put(
            "/api/v1/workspace/memory/auto-learn",
            headers=_auth(TOKEN_A),
            json={"enabled": False},
        )
        assert resp.status_code == 200
        resp = await client.get(
            "/api/v1/workspace/memory/auto-learn", headers=_auth(TOKEN_A)
        )
        assert resp.json()["data"] == {"enabled": False}
        prefs = await store.get_user_preferences(tenant_id=TENANT_A, platform_user_id=USER_A)
        assert prefs is not None
        assert prefs["preference_json"].get("learning_enabled") is False
