"""[S-10] 授权可见性与多 bot 路由验收（E2E）。

真实边界：真实 PostgreSQL（迁移到 head）+ 真实 Redis + 真实 uvicorn 子进程栈
（Console / Runtime / Worker×2 / IM Gateway）+ 真实 MCP 探针（`tests.e2e.mcp_probe_app`
的真实 ASGI 应用，外面只套一层**调用计数**，不改探针实现、不伪造响应）。断言一律取自
**持久化盘面**（`runtime.runtime_snapshot` 的 `skill_catalog_json` / `mcp_catalog_json` /
`content_hash`、`runtime.egress_audit`、`runtime.tool_call_audit`、`control.*` 授权行），
不以日志或返回值代替。

覆盖（design §2.4.2 S-10）：
- **未授权不进 Catalog/ToolRegistry**：`user_scope=SELECTED` 且无用户授权行的 Skill / MCP
  不进入该 Run 的冻结 Snapshot；对照臂（同一 Agent 上已授权的同类资源）必须**在**其中。
  这两个字段就是进入 Prompt 与 ToolRegistry 的那两个集合
  （`run_service.py::build_snapshot` 由 `resolved.skills` / `resolved.mcp_servers` 直接序列化，
  resume 路径再按 `skill_catalog_json` / `mcp_catalog_json` 反序列化重建）。
- **未授权资源不得被调用**：真实 MCP 探针的**调用计数为 0**（计数取自探针本身，不是「返回体为空」）；
  同一轮对照臂的真实 MCP 调用计数 > 0（证明计数与链路都活着，负例非空）。
- **撤销只影响新 Run**：`UPDATE ... SET is_deleted = true` 后跑新 Run ⇒ 新 Snapshot 不含该资源；
  同时旧 Run 的 `runtime_snapshot.content_hash` **前后各读一次**，断言完全不变（旧 Snapshot 不被改写）。
- **多 bot_id 路由同一 Agent**：同一 Agent 上两个不同 `bot_id` 的 `BotAccount`
  ⇒ `POST /internal/channel/resolve` 两者返回**同一** `agent_id`。

如实登记的边界（不冒充覆盖）：
- **Prompt 文本本身不落库**：Runtime 不持久化拼装好的 prompt/工具列表，本模块以冻结 Snapshot 的
  `skill_catalog_json` / `mcp_catalog_json` 作为持久等价证据（见上），不对 prompt 字符串直接断言。
- **未授权 MCP 的「零调用」窗口**：负例臂的探针在整轮 Run 内零请求；正例臂在同一窗口真实收到
  `tools/call`。两侧用的是同一个探针实现与同一套计数口径。
- 未授权 Skill 的「不得被调用」以「不在 ToolRegistry 可见集合（Snapshot）+ 真实 Run 内没有它的
  任何工具调用/执行事实」表达；LLM 探针只会调用被显式指定的工具名，因此不以「猜模型行为」取证。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import uvicorn
from muad_console_platform.infrastructure.models.control import (
    AgentSkillBinding,
    Skill,
    SkillArtifact,
    SkillUserGrant,
)
from muad_console_platform.infrastructure.models.mcp import (
    AgentMcpBinding,
    McpServer,
    McpUserGrant,
)
from sqlalchemy import text

from tests.acceptance.im_gateway.environment import restart_process
from tests.acceptance.task_schedule.environment import (
    TENANT,
    LiveStack,
    build_skill_zip,
    cleanup,
    clear_engine_caches,
    require,
    run_db,
    start_live_stack,
    stop_live_stack,
)
from tests.e2e.mcp_probe_app import app as mcp_probe_app

pytestmark = pytest.mark.e2e

RUN_TIMEOUT_SEC = 60.0
POLL_INTERVAL_SEC = 0.2
LLM_TOOL_ENV = "OPENAI_PROBE_TOOL_NAME"
MCP_AUTH_ENV = "MCP_PROBE_REQUIRE_AUTH"
MCP_AUTH_SECRET = "s10-owner-secret"
TOOL_NAME = "probe_tool_1"
MCP_CATALOG_HASH = "sha256:" + "c" * 64


# --------------------------------------------------------------------------- #
# 真实探针：MCP 探针（真实 ASGI 应用 + 调用计数）、进程内 uvicorn 服务
# --------------------------------------------------------------------------- #


class _Server:
    """进程内真实 uvicorn HTTP 服务（与 `tests/acceptance/runtime/conftest.py` 同口径）。"""

    def __init__(self, app: object) -> None:
        self._config = uvicorn.Config(app=app, host="127.0.0.1", port=0, log_level="error")
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> str:
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self._server.started:
            raise RuntimeError("probe server failed to start")
        port = self._server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


class CountingMcpProbe:
    """真实 MCP 探针 + 调用计数：只统计，不改探针行为（请求原样转给真实探针应用）。"""

    def __init__(self) -> None:
        self.methods: list[str] = []

    @property
    def call_count(self) -> int:
        return sum(1 for method in self.methods if method == "tools/call")

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") != "/mcp":
            await mcp_probe_app(scope, receive, send)
            return
        body = bytearray()

        async def buffered() -> Any:
            message = await receive()
            if message["type"] == "http.request":
                body.extend(message.get("body", b""))
            return message

        await mcp_probe_app(scope, buffered, send)
        try:
            payload = json.loads(bytes(body) or b"{}")
            self.methods.append(str(payload.get("method")))
        except ValueError:
            self.methods.append("unparsable")


# --------------------------------------------------------------------------- #
# 真实 PostgreSQL 播种与回读
# --------------------------------------------------------------------------- #


def _seed_selected_skill(stack: LiveStack, *, granted: bool) -> str:
    """种一个 `user_scope=SELECTED` 的真实 Skill（可选一条用户授权行）。"""
    key = f"e2e-s10-skill-{uuid.uuid4().hex[:8]}"
    storage_key = f"skills/{key}/1.0.0/skill.zip"
    checksum = build_skill_zip(stack.artifact_root, storage_key)

    async def seed(factory: Any) -> str:
        async with factory() as session:
            skill = Skill(
                tenant_id=stack.tenant_id,
                key=key,
                name="E2E S10 Skill",
                description="s10 selected skill",
                user_scope="SELECTED",
                enabled=True,
            )
            session.add(skill)
            await session.flush()
            artifact = SkillArtifact(
                skill_id=skill.id,
                version="1.0.0",
                checksum=checksum,
                storage_key=storage_key,
                execution_mode="ASYNC",
                instructions="",
                package_size=(stack.artifact_root / storage_key).stat().st_size,
                validation_status="READY",
                created_by=stack.platform_user_id,
            )
            session.add(artifact)
            await session.flush()
            skill.current_artifact_id = artifact.id
            session.add(
                AgentSkillBinding(agent_id=stack.agent_id, skill_id=skill.id, sort_order=1)
            )
            if granted:
                session.add(
                    SkillUserGrant(
                        skill_id=skill.id,
                        user_id=stack.platform_user_id,
                        granted_by=stack.platform_user_id,
                    )
                )
            await session.commit()
        return key

    return cast(str, run_db(seed))


def _seed_mcp_server(
    stack: LiveStack, *, endpoint: str, user_scope: str, granted: bool
) -> dict[str, Any]:
    """种一个绑定到当前 Agent 的真实 MCP Server（可选一条用户授权行）。"""
    key = f"e2e-s10-mcp-{uuid.uuid4().hex[:8]}"

    async def seed(factory: Any) -> dict[str, Any]:
        async with factory() as session:
            server = McpServer(
                tenant_id=stack.tenant_id,
                key=key,
                name="E2E S10 MCP",
                endpoint=endpoint,
                auth_secret=MCP_AUTH_SECRET,
                user_scope=user_scope,
                enabled=True,
                connection_status="CONNECTED",
                tool_catalog_json=[
                    {
                        "name": TOOL_NAME,
                        "description": "S10 probe tool",
                        "input_schema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                        "effect": "READ",
                    }
                ],
                tool_catalog_hash=MCP_CATALOG_HASH,
                tool_catalog_revision=4,
            )
            session.add(server)
            await session.flush()
            session.add(AgentMcpBinding(agent_id=stack.agent_id, mcp_server_id=server.id))
            if granted:
                session.add(
                    McpUserGrant(
                        mcp_server_id=server.id,
                        user_id=stack.platform_user_id,
                        granted_by=stack.platform_user_id,
                    )
                )
            await session.commit()
            return {"id": server.id, "key": key}

    return cast(dict[str, Any], run_db(seed))


def _seed_second_bot(stack: LiveStack, bot_id: str) -> uuid.UUID:
    from muad_console_platform.infrastructure.models.channel import BotAccount

    async def seed(factory: Any) -> uuid.UUID:
        async with factory() as session:
            bot = BotAccount(
                tenant_id=stack.tenant_id,
                channel="WECOM",
                name="E2E S10 Second Bot",
                bot_id=bot_id,
                secret="s10-bot-secret",
                agent_id=stack.agent_id,
                enabled=True,
            )
            session.add(bot)
            await session.commit()
            return bot.id

    return cast(uuid.UUID, run_db(seed))


def _purge_module_rows(tenant_id: str) -> None:
    """清掉本模块新增、而共用清理语句覆盖不到的授权/服务器行（先于 `cleanup` 执行）。

    `tests.acceptance.task_schedule.environment.CONTROL_CLEANUP` 不删 `skill_user_grant` /
    `mcp_user_grant` / `mcp_server`，而它们分别引用 `platform_user`、`skill`、`mcp_server`：
    不清会在统一清理的 `DELETE FROM control.skill ...` / `DELETE FROM control.platform_user ...`
    上撞 FK（`agent_mcp_binding` 就在统一清理里，但那时 `mcp_server` 已由本函数删除）。
    语句顺序按 FK 依赖：用户授权 → MCP 绑定 → MCP 用户授权 → MCP Server。
    """

    async def purge(factory: Any) -> None:
        async with factory() as session:
            for statement in (
                "DELETE FROM control.skill_user_grant WHERE skill_id IN "
                "(SELECT id FROM control.skill WHERE tenant_id = :t)",
                "DELETE FROM control.agent_mcp_binding WHERE agent_id IN "
                "(SELECT id FROM control.agent_definition WHERE tenant_id = :t)",
                "DELETE FROM control.mcp_user_grant WHERE mcp_server_id IN "
                "(SELECT id FROM control.mcp_server WHERE tenant_id = :t)",
                "DELETE FROM control.mcp_server WHERE tenant_id = :t",
            ):
                await session.execute(text(statement), {"t": tenant_id})
            await session.commit()

    run_db(purge)


def _revoke_skill_grant(skill_key: str, tenant_id: str) -> int:
    """撤销用户级 Skill 授权（真实列写入，按设计只影响后续 Run）。"""

    async def revoke(factory: Any) -> int:
        async with factory() as session:
            result = await session.execute(
                text(
                    "UPDATE control.skill_user_grant SET is_deleted = true WHERE skill_id IN "
                    "(SELECT id FROM control.skill WHERE tenant_id = :t AND key = :key)"
                ),
                {"t": tenant_id, "key": skill_key},
            )
            await session.commit()
            return int(result.rowcount or 0)

    return cast(int, run_db(revoke))


def _revoke_mcp_grant(mcp_key: str, tenant_id: str) -> int:
    async def revoke(factory: Any) -> int:
        async with factory() as session:
            result = await session.execute(
                text(
                    "UPDATE control.mcp_user_grant SET is_deleted = true WHERE mcp_server_id IN "
                    "(SELECT id FROM control.mcp_server WHERE tenant_id = :t AND key = :key)"
                ),
                {"t": tenant_id, "key": mcp_key},
            )
            await session.commit()
            return int(result.rowcount or 0)

    return cast(int, run_db(revoke))


def _snapshot_of(run_id: uuid.UUID) -> dict[str, Any]:
    """Run 的冻结 Snapshot 盘面（授权可见性的唯一持久判据）。"""

    async def query(factory: Any) -> dict[str, Any] | None:
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT s.content_hash, s.skill_catalog_json, s.mcp_catalog_json"
                        " FROM runtime.runtime_snapshot s"
                        " JOIN runtime.run_record r ON r.snapshot_id = s.id"
                        " WHERE r.id = :id"
                    ),
                    {"id": run_id},
                )
            ).one_or_none()
        if row is None:
            return None
        return {
            "content_hash": row[0],
            "skill_keys": [item["key"] for item in row[1]],
            "mcp_keys": [item["key"] for item in row[2]],
            "skill_catalog": row[1],
            "mcp_catalog": row[2],
        }

    snapshot = cast("dict[str, Any] | None", run_db(query))
    assert snapshot is not None, f"Run {run_id} 没有冻结 Snapshot"
    return snapshot


def _run_status(run_id: uuid.UUID) -> str:
    async def query(factory: Any) -> str:
        async with factory() as session:
            status = await session.scalar(
                text("SELECT status FROM runtime.run_record WHERE id = :id"), {"id": run_id}
            )
        return str(status)

    return cast(str, run_db(query))


def _egress_targets(run_id: uuid.UUID) -> list[dict[str, Any]]:
    async def query(factory: Any) -> list[dict[str, Any]]:
        async with factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT target_type, target, operation, policy_decision, result_status"
                        " FROM runtime.egress_audit WHERE run_id = :id ORDER BY create_time"
                    ),
                    {"id": run_id},
                )
            ).all()
        return [
            {
                "target_type": row[0],
                "target": row[1],
                "operation": row[2],
                "policy_decision": row[3],
                "result_status": row[4],
            }
            for row in rows
        ]

    return cast(list[dict[str, Any]], run_db(query))


def _new_conversation(stack: LiveStack) -> uuid.UUID:
    """新会话：不带 conversation_id 时 Runtime 会复用最近会话（历史会影响本轮行为）。"""
    conversation_id = uuid.uuid4()

    async def seed(factory: Any) -> uuid.UUID:
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO runtime.conversation"
                    " (id, tenant_id, user_id, agent_id, status, last_seq)"
                    " VALUES (:id, :t, :u, :a, 'ACTIVE', 0)"
                ),
                {
                    "id": conversation_id,
                    "t": stack.tenant_id,
                    "u": stack.platform_user_id,
                    "a": stack.agent_id,
                },
            )
            await session.commit()
        return conversation_id

    return cast(uuid.UUID, run_db(seed))


# --------------------------------------------------------------------------- #
# Run 驱动
# --------------------------------------------------------------------------- #


def _parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        data = [
            line[len("data:") :].lstrip() for line in block.splitlines() if line.startswith("data:")
        ]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


@contextmanager
def _llm_tool(stack: LiveStack, tool_name: str | None) -> Iterator[None]:
    """让真实 LLM 探针在首轮返回指定工具调用（改进程 env 后重启探针，用例结束还原）。"""
    process = stack.processes["llm-probe"]
    original = process.env.get(LLM_TOOL_ENV)
    if tool_name is None:
        process.env.pop(LLM_TOOL_ENV, None)
    else:
        process.env[LLM_TOOL_ENV] = tool_name
    restart_process(process)
    try:
        yield
    finally:
        if original is None:
            process.env.pop(LLM_TOOL_ENV, None)
        else:
            process.env[LLM_TOOL_ENV] = original
        restart_process(process)


def _start_run(stack: LiveStack, http: httpx.Client, text_body: str) -> uuid.UUID:
    response = http.post(
        f"{stack.runtime_url}/v1/runs",
        json={
            "agent_id": str(stack.agent_id),
            "platform_user_id": str(stack.platform_user_id),
            "conversation_id": str(_new_conversation(stack)),
            "channel": {
                "type": "WECOM",
                "bot_id": stack.bot_id,
                "external_conversation_id": f"s10-conv-{uuid.uuid4().hex[:8]}",
            },
            "message": {"id": f"msg-{uuid.uuid4().hex[:10]}", "type": "text", "text": text_body},
        },
        headers={"X-Tenant-Id": stack.tenant_id},
        timeout=RUN_TIMEOUT_SEC,
    )
    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    assert events and events[0]["type"] == "run.created", events[:1]
    assert events[-1]["type"] == "run.completed", events[-3:]
    return uuid.UUID(str(events[0]["run_id"]))


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module", autouse=True)
def isolate_engine_caches() -> Iterator[None]:
    yield
    clear_engine_caches()


@pytest.fixture(scope="module")
def live_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveStack]:
    from muad_common import SharedSettings

    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    require("REDIS_URL", settings.redis_url)

    clear_engine_caches()
    # 起栈前先清授权/服务器残留：上一次异常中断留下的 `skill_user_grant` 等行会让
    # `start_live_stack` 内部的统一清理撞 FK（栈会因此在起探针之后中途失败）。
    _purge_module_rows(TENANT)
    root = tmp_path_factory.mktemp("dfx-authorization-scope")
    stack, processes = start_live_stack(Path(root))
    try:
        yield stack
    finally:
        stop_live_stack(processes)
        _purge_module_rows(stack.tenant_id)
        cleanup(database_url, stack.artifact_root)
        clear_engine_caches()


@pytest.fixture(scope="module")
def probes() -> Iterator[tuple[CountingMcpProbe, str, CountingMcpProbe, str]]:
    """两个真实 MCP 探针（授权臂 / 未授权臂各自一个），真实 HTTP、真实调用计数。"""
    authorized, unauthorized = CountingMcpProbe(), CountingMcpProbe()
    authorized_server, unauthorized_server = _Server(authorized), _Server(unauthorized)
    original_auth = os.environ.get(MCP_AUTH_ENV)
    os.environ[MCP_AUTH_ENV] = MCP_AUTH_SECRET
    authorized_url = authorized_server.start()
    unauthorized_url = unauthorized_server.start()
    try:
        yield authorized, authorized_url, unauthorized, unauthorized_url
    finally:
        unauthorized_server.stop()
        authorized_server.stop()
        if original_auth is None:
            os.environ.pop(MCP_AUTH_ENV, None)
        else:
            os.environ[MCP_AUTH_ENV] = original_auth


@pytest.fixture()
def http() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=RUN_TIMEOUT_SEC) as client:
        yield client


# --------------------------------------------------------------------------- #
# S-10：授权可见性
# --------------------------------------------------------------------------- #


def test_s10_unauthorized_skill_and_mcp_absent_from_catalog_and_never_called(
    live_stack: LiveStack,
    http: httpx.Client,
    probes: tuple[CountingMcpProbe, str, CountingMcpProbe, str],
) -> None:
    """[S-10] 未授权 Skill/MCP 不进冻结 Catalog；未授权 MCP 探针调用计数为 0。"""
    authorized, authorized_url, unauthorized, unauthorized_url = probes
    hidden_skill_key = _seed_selected_skill(live_stack, granted=False)
    authorized_mcp = _seed_mcp_server(
        live_stack, endpoint=f"{authorized_url}/mcp", user_scope="SELECTED", granted=True
    )
    unauthorized_mcp = _seed_mcp_server(
        live_stack, endpoint=f"{unauthorized_url}/mcp", user_scope="SELECTED", granted=False
    )
    authorized.methods.clear()
    unauthorized.methods.clear()
    tool_name = f"mcp::{authorized_mcp['key']}::{TOOL_NAME}"

    with _llm_tool(live_stack, tool_name):
        run_id = _start_run(live_stack, http, "s10 authorized mcp call")

    snapshot = _snapshot_of(run_id)
    assert live_stack.skill_key in snapshot["skill_keys"], snapshot["skill_keys"]
    assert hidden_skill_key not in snapshot["skill_keys"], (
        "未授权（SELECTED 且无用户授权行）的 Skill 进了冻结 Catalog"
    )
    assert authorized_mcp["key"] in snapshot["mcp_keys"], snapshot["mcp_keys"]
    assert unauthorized_mcp["key"] not in snapshot["mcp_keys"], (
        "未授权（SELECTED 且无用户授权行）的 MCP 进了冻结 Catalog"
    )

    # 正例臂：同一窗口内真实 MCP 调用确实发生（探针计数 > 0，链路与计数非空）。
    assert authorized.call_count >= 1, f"授权 MCP 未被真实调用：{authorized.methods}"
    assert _run_status(run_id) == "COMPLETED"

    # 负例臂：未授权 MCP 探针在同一窗口内零请求（计数取自探针本身，不是返回体为空）。
    assert unauthorized.methods == [], f"未授权 MCP 探针收到了请求：{unauthorized.methods}"

    # 持久化盘面复核：Egress 审计里只有授权 MCP 的目标，未授权 MCP 从未出现。
    egress = _egress_targets(run_id)
    mcp_rows = [row for row in egress if row["target_type"] == "MCP"]
    assert mcp_rows, f"授权 MCP 调用没有 Egress 审计行：{egress}"
    assert all(row["policy_decision"] == "ALLOW" for row in mcp_rows), mcp_rows
    assert any(
        str(row["target"]) == f"mcp://{authorized_mcp['key']}/{TOOL_NAME}" for row in mcp_rows
    ), mcp_rows
    assert all(unauthorized_mcp["key"] not in str(row["target"]) for row in egress), egress


def test_s10_revocation_only_affects_new_run_snapshot(
    live_stack: LiveStack,
    http: httpx.Client,
    probes: tuple[CountingMcpProbe, str, CountingMcpProbe, str],
) -> None:
    """[S-10] 撤销授权后：新 Run 的快照不含该资源，旧 Run 的 Snapshot 与 hash 完全不变。"""
    _, authorized_url, _, _ = probes
    granted_skill_key = _seed_selected_skill(live_stack, granted=True)
    granted_mcp = _seed_mcp_server(
        live_stack, endpoint=f"{authorized_url}/mcp", user_scope="SELECTED", granted=True
    )

    with _llm_tool(live_stack, None):
        first_run = _start_run(live_stack, http, "s10 before revocation")
    before = _snapshot_of(first_run)
    assert granted_skill_key in before["skill_keys"], before["skill_keys"]
    assert granted_mcp["key"] in before["mcp_keys"], before["mcp_keys"]

    assert _revoke_skill_grant(granted_skill_key, live_stack.tenant_id) == 1, "未撤销到 Skill 授权行"
    assert _revoke_mcp_grant(granted_mcp["key"], live_stack.tenant_id) == 1, "未撤销到 MCP 授权行"

    second_run = _start_run(live_stack, http, "s10 after revocation")
    after = _snapshot_of(second_run)
    assert granted_skill_key not in after["skill_keys"], (
        f"撤销后新 Run 仍能看到该 Skill：{after['skill_keys']}"
    )
    assert granted_mcp["key"] not in after["mcp_keys"], (
        f"撤销后新 Run 仍能看到该 MCP：{after['mcp_keys']}"
    )

    # 旧 Snapshot 不得被后续变更改写：content_hash 前后各读一次，必须完全相等。
    frozen_again = _snapshot_of(first_run)
    assert frozen_again["content_hash"] == before["content_hash"], (
        f"旧 Run 的 Snapshot 被改写了：{before['content_hash']} → {frozen_again['content_hash']}"
    )
    assert frozen_again["skill_keys"] == before["skill_keys"]
    assert frozen_again["mcp_keys"] == before["mcp_keys"]
    assert granted_skill_key in frozen_again["skill_keys"]
    assert granted_mcp["key"] in frozen_again["mcp_keys"]


def test_s10_multiple_bot_ids_route_to_same_agent(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    """[S-10] 同一 Agent 的两个 bot_id：`/internal/channel/resolve` 返回同一 agent_id。"""
    second_bot_id = f"e2e-s10-bot-{uuid.uuid4().hex[:8]}"
    _seed_second_bot(live_stack, second_bot_id)
    headers = {**live_stack.service_headers()}

    resolved: dict[str, Any] = {}
    for bot_id in (live_stack.bot_id, second_bot_id):
        response = http.post(
            f"{live_stack.console_url}/internal/channel/resolve",
            json={
                "channel": "WECOM",
                "bot_id": bot_id,
                "external_user_id": f"s10-external-{uuid.uuid4().hex[:8]}",
            },
            headers=headers,
        )
        assert response.status_code == 200, f"resolve({bot_id}) 失败：{response.text}"
        body = cast(dict[str, Any], response.json()["data"])
        assert body["bound"] is False and body["platform_user_id"] is None, body
        resolved[bot_id] = body["agent_id"]

    assert set(resolved.values()) == {str(live_stack.agent_id)}, (
        f"多 bot_id 未路由到同一 Agent：{resolved}"
    )
