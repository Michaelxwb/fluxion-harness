"""[E-07][RULE-secret-001][RULE-log-001] 安全验收：Egress 拒绝、Secret 全链路与日志脱敏（integration）。

真实边界：**真实 uvicorn 子进程栈**（Console / Runtime / Worker×2 / IM Gateway / 渠道探针 / LLM 探针）
+ **真实 PostgreSQL**（审计与 Owner 表一律逐行回读）+ **真实本地 HTTP 探针**（Egress 出网目标与
MCP 端点的真实承载，只统计/承载请求，不改写响应）。本层不 mock 任何边界。

覆盖（E-07 + TASK-007 契约表）：
1. `ctx.http` 未命中 allowlist → 真实 `EgressBoundary` **不发出请求**（同 URL 的放行臂证明探针可达、
   计数非空）并按 `target_type=HTTP` 落 `runtime.egress_audit` 的 DENY 行（真实 PG 回读）。
2. `ctx.http` 响应 > 5 MiB → 显式 `ResponseTooLargeError`（不静默降级为成功）且同样落 DENY 审计。
3. 三处 internal 端点（`resolve-definition` / `resolve-credentials` / `resolve-egress-access`）：
   匿名与伪造服务身份一律 403 `FORBIDDEN` 且响应体无明文；带 `X-Internal-Service` 的调用方可见明文
   （两侧都断言）。
4. canary 全链路反查：canary 同时写进三张 Owner 表（模型 `api_key` / bot `secret` / MCP `auth_secret`），
   驱动一次真实 Run（真实 MCP 工具调用）后反查 `runtime.runtime_snapshot` / `runtime.egress_audit` /
   `runtime.tool_call_audit` / `runtime.model_invocation_audit` 四表、IM 出站文本与 bot 快照两侧。
5. 两类脱敏语义**不可混用**：审计侧 `sanitize_audit_payload` 是**丢键**（键在 before/after 中整个消失），
   日志侧 `RedactionFilter` 是**值置换 `***`**（键仍在、值被替换）。

显式边界（不修，只登记，不得以「canary 全绿」代替覆盖结论）：
- 本文件的 canary 机检范围**恰为**四表 + IM 出站文本 + bot 快照两侧，沿用配对 verifier
  `tests/acceptance/im_gateway/test_secrets_and_readiness.py` 的口径；**不含** `runtime.canonical_event`、
  `control.config_audit_log`、Skill package 与其 `SKILL.md`（`skill_validator.py` 无密钥扫描）。
  「密钥不得进入」是约定而非全覆盖，新增落库面必须自查。
- 生产里 `EgressBoundary`（`packages/platform-sdk`，`target_type=HTTP`）是 `ctx.http` 的实现体，
  但**尚未被 Run 内的 SkillContext 接线**（`SkillContext.http` 全仓无构造点）；Run 内唯一真实出网面是
  MCP（`target_type=MCP`，S-10 已覆盖）。本文件因此直接驱动**生产类 `EgressBoundary`**（真实本地 HTTP
  探针 + 真实 PG 审计写入），而不是伪造一个 Run 内的 `ctx.http` 调用。
- 「no jitter / compaction」等模型恢复面归 TASK-009；RBAC/CSRF 归 TASK-008（E-08），本文件不重复断言。

收尾清理：canary 值一次性（每次运行随机生成）；模块收尾停掉栈、清本租户全部行与产物根，并复核
库内**不再出现**该明文。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_api.audit import sanitize_audit_payload, write_config_audit
from muad_common import SharedSettings
from muad_contracts import ResolveDefinitionResponse, build_task_snapshot, snapshot_hash
from muad_platform_sdk.egress_boundary import (
    MAX_BYTES_DEFAULT,
    EgressBoundary,
    EgressPolicy,
    ForbiddenEgressError,
    ResponseTooLargeError,
)
from sqlalchemy import text

from tests.acceptance.im_gateway.environment import restart_process
from tests.acceptance.task_schedule.environment import (
    TENANT,
    LiveStack,
    cleanup,
    clear_engine_caches,
    require,
    run_db,
    start_live_stack,
    stop_live_stack,
)
from tests.e2e.mcp_probe_app import app as mcp_probe_app

pytestmark = pytest.mark.integration

# 一次性 canary：随机后缀，运行后不得在库/日志/产物里留下该明文。
CANARY = f"e07-canary-{uuid.uuid4().hex}-do-not-leak"
# 四张运行事实表（配对 verifier 的同口径清单）。
AUDIT_TABLES = (
    "runtime.runtime_snapshot",
    "runtime.egress_audit",
    "runtime.tool_call_audit",
    "runtime.model_invocation_audit",
)
BOTS_PATH = "/internal/channel/bots"
RESOLVE_DEFINITION_PATH = "/internal/runtime/resolve-definition"
RESOLVE_CREDENTIALS_PATH = "/internal/runtime/resolve-credentials"
RESOLVE_EGRESS_PATH = "/internal/runtime/resolve-egress-access"
FORBIDDEN = "FORBIDDEN"
RUN_TIMEOUT_SEC = 60.0
DELIVERY_TIMEOUT_SEC = 90.0
POLL_INTERVAL_SEC = 0.3
LLM_TOOL_ENV = "OPENAI_PROBE_TOOL_NAME"
MCP_AUTH_ENV = "MCP_PROBE_REQUIRE_AUTH"
TOOL_NAME = "probe_tool_1"
MCP_CATALOG_HASH = "sha256:" + "c" * 64
DELIVERY_BOT_ID = "e2e-bot"
DELIVERY_CONVERSATION_ID = "e07-canary-conversation"
RESIDUE_TABLES = (
    "control.model_definition",
    "control.bot_account",
    "control.mcp_server",
    "control.project_platform",
    "control.shared_credential_ref",
    "control.config_audit_log",
    "control.agent_definition",
    *AUDIT_TABLES,
    "task.task_execution",
)


# --------------------------------------------------------------------------- #
# 真实探针：进程内 uvicorn 服务（与 `tests/acceptance/runtime/conftest.py` 同口径）
# --------------------------------------------------------------------------- #


class _Server:
    """进程内真实 uvicorn HTTP 服务（真实 TCP、真实 HTTP，不是 ASGI 传输替身）。"""

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


class _EgressProbe:
    """Egress 出网目标的真实 HTTP 端点：只统计请求路径，不改写任何响应。

    `/oversize` 返回 > 5 MiB 的真实响应体（用真实字节流触发 `ResponseTooLargeError`）。
    """

    def __init__(self) -> None:
        self.paths: list[str] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        assert scope["type"] == "http", scope["type"]
        self.paths.append(str(scope["path"]))
        body = b"x" * (MAX_BYTES_DEFAULT + 1) if scope["path"] == "/oversize" else b'{"ok": true}'
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _CountingMcpProbe:
    """真实 MCP 探针 + 调用计数：只统计方法名，不改探针行为（请求原样转给真实探针应用）。"""

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
# 真实 PostgreSQL 读取与模块自建行清理
# --------------------------------------------------------------------------- #


def _scalar(statement: str, params: dict[str, Any]) -> Any:
    async def query(factory: Any) -> Any:
        async with factory() as session:
            return await session.scalar(text(statement), params)

    return run_db(query)


def _rows(statement: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    async def query(factory: Any) -> list[dict[str, Any]]:
        async with factory() as session:
            result = (await session.execute(text(statement), params)).mappings().all()
        return [dict(row) for row in result]

    return cast(list[dict[str, Any]], run_db(query))


def _canary_hits(table: str, tenant_id: str) -> int:
    """本租户在给定表里出现 canary 明文的行数（`to_jsonb(row)::text` 全列反查）。"""

    async def query(factory: Any) -> int:
        async with factory() as session:
            return int(
                await session.scalar(
                    text(
                        f"SELECT count(*) FROM {table} AS row_source"  # noqa: S608 - 表名来自本文件常量
                        " WHERE row_source.tenant_id = :t"
                        " AND to_jsonb(row_source.*)::text ILIKE :c"
                    ),
                    {"t": tenant_id, "c": f"%{CANARY}%"},
                )
                or 0
            )

    return cast(int, run_db(query))


def _purge_module_rows(tenant_id: str) -> None:
    """清本模块自建、而共用清理语句覆盖不到的行（FK 顺序：授权 → 绑定 → 服务器/凭据 → 平台）。

    `tests.acceptance.task_schedule.environment.CONTROL_CLEANUP` 不删 `mcp_server` /
    `agent_mcp_binding` 之外的 `project_platform` / `shared_credential_ref` / `config_audit_log`；
    不清会在统一清理的 `DELETE FROM control.platform_user ...` 上撞 FK（上一次异常中断的残留同样如此）。
    """

    async def purge(factory: Any) -> None:
        async with factory() as session:
            for statement in (
                "DELETE FROM control.agent_mcp_binding WHERE agent_id IN "
                "(SELECT id FROM control.agent_definition WHERE tenant_id = :t)",
                "DELETE FROM control.mcp_user_grant WHERE mcp_server_id IN "
                "(SELECT id FROM control.mcp_server WHERE tenant_id = :t)",
                "DELETE FROM control.skill_user_grant WHERE skill_id IN "
                "(SELECT id FROM control.skill WHERE tenant_id = :t)",
                "DELETE FROM control.mcp_server WHERE tenant_id = :t",
                "DELETE FROM control.user_credential_ref WHERE tenant_id = :t",
                "DELETE FROM control.shared_credential_ref WHERE tenant_id = :t",
                "DELETE FROM control.project_platform WHERE tenant_id = :t",
                "DELETE FROM control.config_audit_log WHERE tenant_id = :t",
            ):
                await session.execute(text(statement), {"t": tenant_id})
            await session.commit()

    run_db(purge)


def _set_owner_secrets(stack: LiveStack) -> None:
    """把 canary 写进 Owner 表（模型 `api_key`、bot `secret`）——真实列写入，运行期实时读取。"""

    async def update(factory: Any) -> None:
        async with factory() as session:
            await session.execute(
                text(
                    "UPDATE control.model_definition SET api_key = :k"
                    " WHERE tenant_id = :t AND id = :id"
                ),
                {"k": CANARY, "t": stack.tenant_id, "id": stack.model_id},
            )
            await session.execute(
                text(
                    "UPDATE control.bot_account SET secret = :k"
                    " WHERE tenant_id = :t AND bot_id = :b"
                ),
                {"k": CANARY, "t": stack.tenant_id, "b": DELIVERY_BOT_ID},
            )
            await session.commit()

    run_db(update)


def _seed_mcp_server(stack: LiveStack, endpoint: str) -> dict[str, Any]:
    """绑一个真实 MCP Server 到当前 Agent，`auth_secret` 即 canary（真实 Owner 表行）。"""
    from muad_console_platform.infrastructure.models.mcp import AgentMcpBinding, McpServer

    key = f"e07-mcp-{uuid.uuid4().hex[:8]}"

    async def seed(factory: Any) -> dict[str, Any]:
        async with factory() as session:
            server = McpServer(
                tenant_id=stack.tenant_id,
                key=key,
                name="E07 Canary MCP",
                endpoint=endpoint,
                auth_secret=CANARY,
                user_scope="ALL",
                enabled=True,
                connection_status="CONNECTED",
                tool_catalog_json=[
                    {
                        "name": TOOL_NAME,
                        "description": "E07 probe tool",
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
            await session.commit()
            return {"id": server.id, "key": key}

    return cast(dict[str, Any], run_db(seed))


def _seed_platform_with_credential(stack: LiveStack) -> str:
    """真实平台 + 共享凭据：`credential_json` 即 canary（受控出口的明文来源）。"""
    from muad_console_platform.infrastructure.models.control import (
        ProjectPlatform,
        SharedCredentialRef,
    )

    key = f"e07-platform-{uuid.uuid4().hex[:8]}"

    async def seed(factory: Any) -> str:
        async with factory() as session:
            platform = ProjectPlatform(
                tenant_id=stack.tenant_id,
                key=key,
                name="E07 Canary Platform",
                resolver_type="HTTP",
                resolver_config_json={"base_url": "https://canary.example.test/"},
                adapter_key="generic-http",
                adapter_config_json={"allowlist": []},
                adapter_schema_version="1",
                credential_mode="SHARED_ONLY",
                enabled=True,
            )
            session.add(platform)
            await session.flush()
            session.add(
                SharedCredentialRef(
                    tenant_id=stack.tenant_id,
                    platform_id=platform.id,
                    credential_json={"token": CANARY},
                    credential_schema_version="1",
                    status="ACTIVE",
                )
            )
            await session.commit()
            return key

    return cast(str, run_db(seed))


# --------------------------------------------------------------------------- #
# Run / Task 驱动（真实 HTTP）
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
                "bot_id": DELIVERY_BOT_ID,
                "external_conversation_id": f"e07-conv-{uuid.uuid4().hex[:8]}",
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


def _submit_final_delivery(stack: LiveStack, http: httpx.Client) -> str:
    """真实 Console 冻结定义 → 契约快照 → Worker `POST /internal/tasks`（`FINAL_ONLY` 投递）。"""
    response = http.post(
        f"{stack.console_url}{RESOLVE_DEFINITION_PATH}",
        json={
            "agent_id": str(stack.agent_id),
            "actor_user_id": str(stack.platform_user_id),
            "channel": "WECOM",
        },
        headers=stack.service_headers(),
    )
    assert response.status_code == 200, response.text
    resolved = ResolveDefinitionResponse.model_validate(response.json()["data"])
    skill = next(item for item in resolved.skills if item.key == stack.skill_key)
    snapshot = build_task_snapshot(agent=resolved.agent, model=resolved.model, skill=skill)
    submitted = http.post(
        f"{stack.worker_url}/internal/tasks",
        json={
            "tenant_id": stack.tenant_id,
            "agent_id": str(stack.agent_id),
            "actor_user_id": str(stack.platform_user_id),
            "intent_key": "e07_security_probe",
            "skill_id": str(skill.skill_id),
            "skill_artifact_id": str(skill.artifact_id),
            "input": {},
            "execution_snapshot": snapshot,
            "snapshot_hash": snapshot_hash(snapshot),
            "idempotency_key": f"e07-{uuid.uuid4().hex}",
            "delivery_mode": "FINAL_ONLY",
            "delivery_route": {
                "channel": "WECOM",
                "bot_id": DELIVERY_BOT_ID,
                "external_user_id": "e07-canary-user",
                "external_conversation_id": DELIVERY_CONVERSATION_ID,
            },
        },
        headers=stack.service_headers(),
    )
    assert submitted.status_code == 200, submitted.text
    return str(submitted.json()["data"]["task_id"])


def _await_outbound_texts(http: httpx.Client, probe_url: str) -> list[str]:
    """等真实渠道探针收到本用例 bot 的投递，返回出站文本（真实 HTTP + 真实投递链路）。"""
    deadline = time.monotonic() + DELIVERY_TIMEOUT_SEC
    texts: list[str] = []
    while time.monotonic() < deadline:
        body = http.get(f"{probe_url}/probe/deliveries", timeout=10.0).json()
        texts = [
            str(record.get("text"))
            for record in body["deliveries"]
            if record.get("bot_id") == DELIVERY_BOT_ID
        ]
        if texts:
            return texts
        time.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(f"{DELIVERY_TIMEOUT_SEC}s 内真实探针未收到 IM 出站投递")


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def security_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveStack]:
    """真实子进程栈（Console/Runtime/Worker×2/IM Gateway/渠道探针/LLM 探针）+ 真实 PG/Redis。

    起栈前先清本模块自建、共用清理语句覆盖不到的行：上一次异常中断留下的 `mcp_server` /
    `project_platform` 等会让 `start_live_stack` 内部的统一清理撞 FK。
    """
    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    require("REDIS_URL", settings.redis_url)

    clear_engine_caches()
    _purge_module_rows(TENANT)
    root = tmp_path_factory.mktemp("dfx-security")
    stack, processes = start_live_stack(Path(root))
    try:
        yield stack
    finally:
        stop_live_stack(processes)
        _purge_module_rows(stack.tenant_id)
        cleanup(database_url, stack.artifact_root)
        clear_engine_caches()
        leaked = {table: _canary_hits(table, stack.tenant_id) for table in RESIDUE_TABLES}
        assert not {key: value for key, value in leaked.items() if value}, (
            f"收尾后库内仍有 canary 明文：{leaked}"
        )
        # 日志目录：真实服务子进程的 stdout/日志文件同样不得留下该明文。
        log_dir = stack.processes["console"].log_path.parent
        log_files = sorted(log_dir.glob("*.log"))
        assert log_files, f"服务日志目录为空，日志侧断言会是空转：{log_dir}"
        tainted = [
            path.name for path in log_files if CANARY in path.read_text(errors="replace")
        ]
        assert tainted == [], f"收尾后日志目录仍有 canary 明文：{tainted}"


@pytest.fixture(scope="module")
def mcp_probe() -> Iterator[tuple[_CountingMcpProbe, str]]:
    """真实 MCP 探针（进程内 uvicorn），并要求 Authorization（canary 走真实出网链路）。"""
    probe = _CountingMcpProbe()
    server = _Server(probe)
    original = os.environ.get(MCP_AUTH_ENV)
    os.environ[MCP_AUTH_ENV] = CANARY
    url = server.start()
    try:
        yield probe, url
    finally:
        server.stop()
        if original is None:
            os.environ.pop(MCP_AUTH_ENV, None)
        else:
            os.environ[MCP_AUTH_ENV] = original


@pytest.fixture()
def http() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=RUN_TIMEOUT_SEC) as client:
        yield client


# --------------------------------------------------------------------------- #
# E-07 ① ③：Egress 拒绝与响应上限（真实 EgressBoundary + 真实 HTTP 探针 + 真实 PG 审计）
# --------------------------------------------------------------------------- #


def _boundary(
    factory: Any, tenant_id: str, policy: EgressPolicy, run_id: uuid.UUID
) -> EgressBoundary:
    """生产 `EgressBoundary` + 生产 `RuntimeAuditWriter`（写真实 `runtime.egress_audit`）。"""
    return EgressBoundary(
        policy=policy,
        audit_writer=RuntimeAuditWriter(
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=None,
            conversation_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            session_factory=lambda: factory,
        ),
    )


async def _egress_arm(
    factory: Any, tenant_id: str, probe: _EgressProbe, target: str, run_id: uuid.UUID
) -> dict[str, Any]:
    """拒绝臂先跑（记录探针计数），随后用**同一 URL** 的放行臂证明探针可达、计数非空。"""
    denied = _boundary(factory, tenant_id, EgressPolicy(allowed_hosts=("allowed.example.test",)), run_id)
    try:
        with pytest.raises(ForbiddenEgressError):
            await denied.http_get(target)
    finally:
        await denied.aclose()
    hits_after_deny = list(probe.paths)

    allowed = _boundary(factory, tenant_id, EgressPolicy(allowed_hosts=("127.0.0.1",)), run_id)
    try:
        allowed_status = (await allowed.http_get(target)).status_code
    finally:
        await allowed.aclose()

    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT target, target_type, policy_decision, status_code"
                    " FROM runtime.egress_audit WHERE tenant_id = :t AND run_id = :r"
                    " ORDER BY create_time"
                ),
                {"t": tenant_id, "r": run_id},
            )
        ).mappings().all()
    return {
        "hits_after_deny": hits_after_deny,
        "allowed_status": allowed_status,
        "hits_after_allow": list(probe.paths),
        "rows": [dict(row) for row in rows],
    }


def test_e07_egress_denied_host_is_not_sent_and_deny_is_audited(security_stack: LiveStack) -> None:
    """[E-07] 未命中 allowlist 的 `ctx.http`：请求**不发出** + `target_type=HTTP` 的 DENY 审计。"""
    probe = _EgressProbe()
    server = _Server(probe)
    base = server.start()
    run_id = uuid.uuid4()
    try:
        target = f"{base}/probe"
        outcome = run_db(
            lambda factory: _egress_arm(factory, security_stack.tenant_id, probe, target, run_id)
        )
    finally:
        server.stop()

    # ① 调用不发出：拒绝臂之后探针零请求（它就在同一 URL 上，放行臂证明其可达且会计数）。
    assert outcome["hits_after_deny"] == [], f"被拒绝的 host 仍然发出了请求：{outcome}"
    # 正对照：同一 URL 在 allowlist 内必须真实到达探针（否则上面的「零请求」是空断言）。
    assert outcome["allowed_status"] == 200
    assert outcome["hits_after_allow"] == ["/probe"], outcome

    # ② DENY 审计落在真实 PG：target_type=HTTP、target 为该 URL、decision=DENY。
    denied_rows = [row for row in outcome["rows"] if row["policy_decision"] == "DENY"]
    assert len(denied_rows) == 1, outcome["rows"]
    denied = denied_rows[0]
    assert denied["target_type"] == "HTTP"
    assert denied["target"] == target
    assert denied["status_code"] is None, "拒绝路径没有响应，不得记 status_code"
    allowed_rows = [row for row in outcome["rows"] if row["policy_decision"] == "ALLOW"]
    assert len(allowed_rows) == 1 and allowed_rows[0]["status_code"] == 200, outcome["rows"]


async def _oversize_arm(
    factory: Any, tenant_id: str, target: str, run_id: uuid.UUID
) -> list[dict[str, Any]]:
    boundary = _boundary(factory, tenant_id, EgressPolicy(allowed_hosts=("127.0.0.1",)), run_id)
    try:
        with pytest.raises(ResponseTooLargeError):
            await boundary.http_get(target)
    finally:
        await boundary.aclose()
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT target, target_type, policy_decision, status_code, error_code"
                    " FROM runtime.egress_audit WHERE tenant_id = :t AND run_id = :r"
                    " ORDER BY create_time"
                ),
                {"t": tenant_id, "r": run_id},
            )
        ).mappings().all()
    return [dict(row) for row in rows]


def test_e07_egress_response_over_five_mib_is_rejected_and_audited(
    security_stack: LiveStack,
) -> None:
    """[E-07] `ctx.http` 响应 > 5 MiB：显式失败（不返回假成功）且落 DENY 审计。"""
    probe = _EgressProbe()
    server = _Server(probe)
    base = server.start()
    run_id = uuid.uuid4()
    try:
        target = f"{base}/oversize"
        rows = run_db(lambda factory: _oversize_arm(factory, security_stack.tenant_id, target, run_id))
    finally:
        server.stop()

    # 请求确实发出并收到真实大响应体（探针命中 —— 不是「因为没连上所以失败」）。
    assert probe.paths == ["/oversize"], probe.paths
    # 显式失败：不得静默降级为成功（调用方拿到的是异常，不是截断的响应）。
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["target_type"] == "HTTP"
    assert row["target"] == target
    assert row["policy_decision"] == "DENY", "超限必须记为拒绝"
    assert row["error_code"] == "RESPONSE_TOO_LARGE", row
    assert row["status_code"] == 200, "响应本身收到了，拒绝的是响应体大小"


# --------------------------------------------------------------------------- #
# E-07：三处 internal 端点的服务身份门控（匿名/越权 403，受信调用方可见明文）
# --------------------------------------------------------------------------- #


def _internal_payloads(stack: LiveStack, platform_key: str) -> dict[str, dict[str, Any]]:
    execution_ref = {"type": "RUN", "id": str(uuid.uuid4())}
    return {
        RESOLVE_DEFINITION_PATH: {
            "agent_id": str(stack.agent_id),
            "actor_user_id": str(stack.platform_user_id),
            "channel": "WECOM",
        },
        RESOLVE_CREDENTIALS_PATH: {
            "execution_ref": execution_ref,
            "model_id": str(stack.model_id),
            "mcp_server_ids": [],
        },
        RESOLVE_EGRESS_PATH: {
            "actor_user_id": str(stack.platform_user_id),
            "execution_ref": execution_ref,
            "platform_key": platform_key,
            "target": {
                "type": "PLATFORM_SERVICE",
                "service": "customer-service-mgr",
                "operation": "get_customer",
            },
        },
    }


def test_e07_internal_endpoints_gate_plaintext_behind_service_identity(
    security_stack: LiveStack, http: httpx.Client
) -> None:
    """[E-07] 三处受控明文出口：匿名/伪造服务身份 403 且无明文；受信调用方可见明文。"""
    stack = security_stack
    _set_owner_secrets(stack)
    platform_key = _seed_platform_with_credential(stack)
    payloads = _internal_payloads(stack, platform_key)
    forged = {"X-Tenant-Id": stack.tenant_id, "X-Internal-Service": "forged-token"}

    for path, payload in payloads.items():
        anonymous = http.post(f"{stack.console_url}{path}", json=payload)
        assert anonymous.status_code == 403, f"{path}: {anonymous.text}"
        assert anonymous.json()["code"] == FORBIDDEN, path
        assert CANARY not in anonymous.text, f"{path} 匿名响应泄露明文"

        impersonated = http.post(f"{stack.console_url}{path}", json=payload, headers=forged)
        assert impersonated.status_code == 403, f"{path}: {impersonated.text}"
        assert impersonated.json()["code"] == FORBIDDEN, path
        assert CANARY not in impersonated.text, f"{path} 伪造服务身份响应泄露明文"

        authorized = http.post(
            f"{stack.console_url}{path}", json=payload, headers=stack.service_headers()
        )
        assert authorized.status_code == 200, f"{path}: {authorized.text}"
        # 受控例外：带服务身份的调用方**必须**能看到明文（否则 Runtime 无法取到凭据）。
        assert CANARY in authorized.text, f"{path} 受信调用方拿不到明文"


# --------------------------------------------------------------------------- #
# E-07 ②：canary 全链路反查（四表 + IM 出站 + bot 快照两侧）
# --------------------------------------------------------------------------- #


def test_e07_canary_never_reaches_run_audit_tables(
    security_stack: LiveStack, http: httpx.Client, mcp_probe: tuple[_CountingMcpProbe, str]
) -> None:
    """[E-07] 载荷含 canary（模型 key / MCP auth_secret）时，真实 Run 的四张事实表均无明文。"""
    stack = security_stack
    probe, mcp_url = mcp_probe
    _set_owner_secrets(stack)
    seeded = _seed_mcp_server(stack, f"{mcp_url}/mcp")
    probe.methods.clear()
    tool_name = f"mcp::{seeded['key']}::{TOOL_NAME}"

    with _llm_tool(stack, tool_name):
        run_id = _start_run(stack, http, "e07 canary run")

    # 负例非空：真实 MCP 调用确实发生（canary 作为 auth_secret 真实走过 Egress 链路）。
    assert probe.call_count >= 1, f"授权 MCP 未被真实调用：{probe.methods}"

    counts = {
        table: int(
            _scalar(
                f"SELECT count(*) FROM {table} WHERE tenant_id = :t AND run_id = :r",
                {"t": stack.tenant_id, "r": run_id},
            )
            or 0
        )
        for table in AUDIT_TABLES
    }
    assert all(value >= 1 for value in counts.values()), f"本次 Run 未写全四张表：{counts}"

    leaked = {table: _canary_hits(table, stack.tenant_id) for table in AUDIT_TABLES}
    assert not {key: value for key, value in leaked.items() if value}, f"canary 进入运行事实表：{leaked}"
    # 阳性对照：同一反查口径在两处 Owner 表上**必须**命中（明文按设计存这里）——
    # 证明上面的「四表零命中」不是扫描器失效造成的空断言。
    assert _canary_hits("control.mcp_server", stack.tenant_id) >= 1
    assert _canary_hits("control.model_definition", stack.tenant_id) >= 1


def test_e07_bot_snapshot_is_gated_and_im_outbound_carries_no_plaintext(
    security_stack: LiveStack, http: httpx.Client
) -> None:
    """[E-07] bot 快照两侧（受信可见明文 / 匿名 403 无明文）+ IM 出站文本无 canary。"""
    stack = security_stack
    _set_owner_secrets(stack)

    internal = http.get(
        f"{stack.console_url}{BOTS_PATH}",
        params={"page": 1, "page_size": 50},
        headers=stack.service_headers(),
    )
    assert internal.status_code == 200, internal.text
    assert CANARY in internal.text, "内部快照应按最小凭据边界携带 secret"

    anonymous = http.get(f"{stack.console_url}{BOTS_PATH}", params={"page": 1, "page_size": 50})
    assert anonymous.status_code == 403, anonymous.text
    assert anonymous.json()["code"] == FORBIDDEN, anonymous.text
    assert CANARY not in anonymous.text

    _submit_final_delivery(stack, http)
    texts = _await_outbound_texts(http, stack.channel_url)
    assert texts, "真实探针未收到投递（Gateway→探针链路未打通）"
    assert all(CANARY not in text for text in texts), "IM 出站文本携带 canary"
    gateway_log = (stack.processes["gateway"].log_path).read_text(errors="replace")
    assert CANARY not in gateway_log, "canary 进入 Gateway 进程日志"


# --------------------------------------------------------------------------- #
# E-07：两类脱敏语义不可混用（审计丢键 vs 日志 `***`）
# --------------------------------------------------------------------------- #

_LOG_CHILD = """
import logging
from muad_logging import configure_logging
service, log_dir, message = __import__("sys").argv[1:4]
configure_logging(service, log_dir=log_dir, console=False)
logging.getLogger("e07.security").info(message)
logging.shutdown()
"""


def test_e07_audit_drops_keys_while_logs_replace_values(
    security_stack: LiveStack, tmp_path: Path
) -> None:
    """[RULE-log-001] 审计侧**丢键**（键整个消失）与日志侧**值置换 `***`** 语义不同，不可混用。"""
    resource_id = uuid.uuid4()
    assert sanitize_audit_payload({"api_key": CANARY, "secret": CANARY, "name": "ok"}) == {"name": "ok"}

    async def write(factory: Any) -> None:
        async with factory() as session:
            await write_config_audit(
                session,
                actor_user_id=uuid.uuid4(),
                resource_type="MODEL_DEFINITION",
                resource_id=resource_id,
                action="UPDATE",
                before={"api_key": CANARY, "secret": CANARY, "name": "ok"},
                after={"api_key": CANARY, "secret": CANARY, "name": "ok"},
                tenant_id=security_stack.tenant_id,
            )
            await session.commit()

    try:
        run_db(write)
        row = _rows(
            "SELECT before_json, after_json FROM control.config_audit_log"
            " WHERE tenant_id = :t AND resource_id = :id",
            {"t": security_stack.tenant_id, "id": resource_id},
        )
        assert len(row) == 1, row
        assert row[0]["after_json"] == {"name": "ok"}, "审计侧必须**丢键**（不是把值改成 ***）"
        assert row[0]["before_json"] == {"name": "ok"}
        assert CANARY not in json.dumps(row[0], ensure_ascii=False, default=str)
    finally:
        run_db(
            lambda factory: _delete_audit(factory, security_stack.tenant_id, resource_id)
        )

    log_dir = tmp_path / "logs"
    subprocess.run(
        [sys.executable, "-c", _LOG_CHILD, "e07-svc", str(log_dir), f"api_key={CANARY}"],
        check=True,
        capture_output=True,
        text=True,
    )
    log_file = next((log_dir / "e07-svc").glob("*.log"))
    payload = json.loads(log_file.read_text(encoding="utf-8").splitlines()[-1])
    assert "api_key=***" in payload["message"], payload["message"]
    assert CANARY not in payload["message"], "日志侧必须把值换成 ***，不得留明文"
    assert "api_key" in payload["message"], "日志侧是**值置换**：键必须仍在（与审计丢键相反）"


async def _delete_audit(factory: Any, tenant_id: str, resource_id: uuid.UUID) -> None:
    async with factory() as session:
        await session.execute(
            text("DELETE FROM control.config_audit_log WHERE tenant_id = :t AND resource_id = :id"),
            {"t": tenant_id, "id": resource_id},
        )
        await session.commit()
