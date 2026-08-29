"""Chat Workspace 应用服务（Phase 5 TASK-014 / FEAT-P5-10，S-15）。

闭合 phase4 X402-X408 冻结契约：`/api/v1/workspace/*` 7 组端点的领域层。
身份来自 Bearer Chat Access Token（token→tenant+platform_user，rule 16 tenant
scope 全链路）；数据源对齐既有域，不另起存储：

- agents：AgentDefinition 产品模型（发布版目录；不暴露 RuntimeProfile——
  RULE-fluxion-workflow-001 / 术语边界）；
- tasks/history：workflow_run 投影 + session_memory 会话统一视图；
- approvals：运行中 run 的挂起 human_task（node_states 缺位推导）；decide 经
  DBOSClient signal（durable notifications，免 launch——API 进程不抢 recovery）；
- memory：phase2 Memory 域（MemoryUserService 查看/纠正/删除）；
- profile / auto-learn：phase2 用户域（UserDomainService 画像与偏好）。
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Protocol, cast

from sqlalchemy import select

from fluxion.agents.definitions import AgentDefinition
from fluxion.errors.console import (
    WORKSPACE_AUTH_DENIED,
    WORKSPACE_SIGNAL_UNAVAILABLE,
    ConsoleError,
    ConsoleResourceNotFoundError,
    ConsoleValidationError,
)
from fluxion.memory.application.memory_user_service import MemoryUserService
from fluxion.registry.schema import session_memory, workflow_run
from fluxion.registry.sqlalchemy_store import SQLAlchemyRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.workspace_views import (
    chat_task,
    completed_node_ids,
    definition_steps,
    entry_id,
    find_human_task_node,
    iso,
    memory_wire,
    profile_wire,
    run_status_summary,
    truncate,
    workflow_task,
)
from fluxion.users.service import UserDomainService

# 列表/时间线上限（读路径有界，防全表扫描放大）。
_LIST_LIMIT = 50
# 决策 signal 有界（规则 18：外部调用必须带 deadline）。
_SIGNAL_TIMEOUT_SECONDS = 5.0
_SIGNAL_SEND_TIMEOUT_SECONDS = 5.0

class WorkspaceSignalSender(Protocol):
    """human_task 决策 signal 边界（生产实现：DBOSClient send）。"""

    async def send(self, run_id: str, name: str, payload: dict[str, object]) -> None: ...


class DbosWorkspaceSignalSender:
    """DBOSClient 侧 signal sender（免 launch）。

    生产拓扑（design §4.1，rule 13）：API 进程只做 client 侧 signal，不 launch
    DBOS——launched 的 DBOS 无条件消费 internal queue，会与 worker 抢 recovery
    （与 `WorkflowProjectionService` 的只读 client 同一约束）。
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._client: Any | None = None

    async def send(self, run_id: str, name: str, payload: dict[str, object]) -> None:
        client = self._client
        if client is None:
            client = self._create_client()
            self._client = client
        try:
            async with asyncio.timeout(_SIGNAL_SEND_TIMEOUT_SECONDS):
                await asyncio.to_thread(client.send, run_id, payload, f"{name}:{run_id}")
        except Exception as error:  # sysdb 不可达须回统一 envelope（ruff BLE001 不适用于转译再抛）
            raise ConsoleError(
                WORKSPACE_SIGNAL_UNAVAILABLE,
                f"workspace.approval signal 失败: {error}",
                503,
            ) from error

    async def close(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await asyncio.to_thread(client.destroy)

    def _create_client(self) -> Any:
        from dbos import DBOSClient

        from fluxion.runtime.workflow_dbos import DBOS_APP_NAME

        return DBOSClient(
            system_database_url=self._database_url,
            application_name=DBOS_APP_NAME,
            use_listen_notify=False,
        )


class WorkspaceApplicationService:
    """Chat Workspace 门面：token 鉴权 + 7 组读/写用例。"""

    def __init__(
        self,
        store: SQLAlchemyRegistryStore,
        *,
        signal_sender: WorkspaceSignalSender | None = None,
    ) -> None:
        self._store = store
        self._engine = store.engine
        self._signal_sender = signal_sender
        # EngineStore 协议要求 engine 可写（测试替身需要），registry store 的
        # engine 为只读属性——结构不匹配纯属协议形态差异，运行时语义一致。
        self._memory = MemoryUserService(cast(Any, store))
        self._users = UserDomainService(store)

    # ---- 身份（token → tenant + platform_user） ----------------------------

    async def resolve_identity(self, token: str) -> Any:
        """Bearer Chat Access Token → ChatAccessRecord；无效/撤销 → 401。"""
        if not token.strip():
            raise ConsoleError(WORKSPACE_AUTH_DENIED, "缺少 Chat Access Token", 401)
        token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
        record = await self._store.resolve_chat_access(token_hash=token_hash)
        if record is None or record.revoked_at is not None:
            raise ConsoleError(WORKSPACE_AUTH_DENIED, "Chat Access Token 无效或已撤销", 401)
        return record

    # ---- agents（X403：产品模型目录） ---------------------------------------

    async def list_agents(self, *, tenant_id: str) -> dict[str, object]:
        definitions, _total = await self._store.list_resources(
            ResourceKind.AGENT_DEFINITION, tenant_id=tenant_id, offset=0, limit=_LIST_LIMIT
        )
        items: list[dict[str, object]] = []
        for definition in definitions:
            spec = AgentDefinition.model_validate(definition.spec_json)
            items.append(
                {
                    "agent_id": definition.id,
                    "display_name": spec.name,
                    "description": spec.description,
                    "capabilities": [ref.capability_ref for ref in spec.capabilities],
                    "available": True,
                }
            )
        return {"items": items}

    # ---- tasks / history（X402/X404/X406：统一视图） -------------------------

    async def list_tasks(self, *, tenant_id: str, user_id: str) -> dict[str, object]:
        items = [
            workflow_task(row, await self._definition_for_row(tenant_id, row))
            for row in await self._recent_workflow_runs(tenant_id)
        ]
        items.extend(await self._chat_tasks(tenant_id=tenant_id, user_id=user_id))
        items.sort(key=lambda item: str(item["updated_at"]), reverse=True)
        return {"items": items}

    async def get_task(self, *, tenant_id: str, user_id: str, task_id: str) -> dict[str, object]:
        row = await self._store.get_workflow_run(tenant_id=tenant_id, run_id=task_id)
        if row is not None:
            return workflow_task(row, await self._definition_for_row(tenant_id, row))
        chat = await self._chat_task_for_session(
            tenant_id=tenant_id, user_id=user_id, session_id=task_id
        )
        if chat is not None:
            return chat
        raise ConsoleResourceNotFoundError(f"workspace task not found: {task_id}")

    async def list_history(self, *, tenant_id: str, user_id: str) -> dict[str, object]:
        entries: list[dict[str, object]] = []
        for row in await self._recent_workflow_runs(tenant_id):
            definition = await self._definition_for_row(tenant_id, row)
            title = str(definition.get("name") or row["workflow_id"]) if definition else str(row["workflow_id"])
            entries.append(
                {
                    "entry_id": f"run:{row['run_id']}",
                    "kind": "task",
                    "title": title,
                    "summary": run_status_summary(str(row["status"])),
                    "at": iso(row["updated_at"]),
                    "task_id": str(row["run_id"]),
                    "trace_id": str(row["trace_id"]),
                }
            )
        for session in await self._chat_sessions(tenant_id=tenant_id, user_id=user_id):
            entries.append(
                {
                    "entry_id": f"chat:{session['session_id']}",
                    "kind": "chat",
                    "title": truncate(session["first_content"]),
                    "summary": truncate(session["last_content"]),
                    "at": iso(session["last_at"]),
                    "conversation_id": str(session["session_id"]),
                }
            )
        entries.sort(key=lambda entry: str(entry["at"]), reverse=True)
        return {"items": entries[:_LIST_LIMIT]}

    # ---- approvals（X405：human_task 队列 + decide） --------------------------

    async def list_approvals(self, *, tenant_id: str) -> dict[str, object]:
        items: list[dict[str, object]] = []
        for row in await self._recent_workflow_runs(tenant_id, status="running"):
            definition = await self._definition_for_row(tenant_id, row)
            if definition is None:
                continue
            completed = completed_node_ids(row)
            for node in definition_steps(definition):
                if node.get("type") != "human_task":
                    continue
                node_id = str(node.get("id", ""))
                if not node_id or node_id in completed:
                    continue
                items.append(
                    {
                        "approval_id": f"{row['run_id']}::{node_id}",
                        "task_id": str(row["run_id"]),
                        "title": str(definition.get("name") or row["workflow_id"]),
                        "message": str(node.get("message", "")),
                        "assignee": str(node.get("assignee", "")),
                        "created_at": iso(row["created_at"]),
                        "status": "pending",
                    }
                )
        return {"items": items}

    async def decide_approval(
        self,
        *,
        tenant_id: str,
        user_id: str,
        approval_id: str,
        decision: str,
        comment: str | None,
    ) -> None:
        if decision not in ("approve", "reject"):
            raise ConsoleValidationError(f"非法审批决策: {decision}")
        if "::" not in approval_id:
            raise ConsoleResourceNotFoundError(f"审批事项不存在: {approval_id}")
        run_id, _, node_id = approval_id.rpartition("::")
        row = await self._store.get_workflow_run(tenant_id=tenant_id, run_id=run_id)
        if row is None:
            # tenant scope：他租户 run 与不存在同观感（不泄露存在性）。
            raise ConsoleResourceNotFoundError(f"审批事项不存在: {approval_id}")
        definition = await self._definition_for_row(tenant_id, row)
        node = find_human_task_node(definition, node_id) if definition is not None else None
        if node is None or node_id in completed_node_ids(row):
            raise ConsoleResourceNotFoundError(f"审批事项不存在或已处理: {approval_id}")
        if self._signal_sender is None:
            raise ConsoleError(
                WORKSPACE_SIGNAL_UNAVAILABLE, "workflow signal 通道未装配", 503
            )
        payload: dict[str, object] = {"decision": decision, "decided_by": user_id}
        if comment is not None and comment.strip():
            payload["comment"] = comment.strip()
        async with asyncio.timeout(_SIGNAL_TIMEOUT_SECONDS):
            await self._signal_sender.send(run_id, node_id, payload)

    # ---- profile（X407） -----------------------------------------------------

    async def get_profile(self, *, tenant_id: str, user_id: str) -> dict[str, object]:
        profile_json = await self._profile_json(tenant_id=tenant_id, user_id=user_id)
        return profile_wire(user_id, profile_json)

    async def update_profile(
        self,
        *,
        tenant_id: str,
        user_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        display_name = payload.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ConsoleValidationError("display_name 无效")
        spec: dict[str, object] = {"display_name": display_name.strip()}
        if isinstance(payload.get("timezone"), str):
            spec["timezone"] = payload["timezone"]
        if isinstance(payload.get("locale"), str):
            spec["language"] = payload["locale"]
        await self._users.upsert_profile(
            tenant_id=tenant_id,
            platform_user_id=user_id,
            spec=spec,
            actor_id=user_id,
        )
        profile_json = await self._profile_json(tenant_id=tenant_id, user_id=user_id)
        return profile_wire(user_id, profile_json)

    # ---- memory（X407：Personal Memory 查看/纠正/删除） ------------------------

    async def list_memory(self, *, tenant_id: str, user_id: str) -> dict[str, object]:
        entries = await self._memory.list_entries(tenant_id=tenant_id, user_id=user_id)
        return {"items": [memory_wire(entry) for entry in entries]}

    async def correct_memory(
        self, *, tenant_id: str, user_id: str, memory_id: str, content: str
    ) -> dict[str, object]:
        entry = await self._memory.correct(
            tenant_id=tenant_id,
            user_id=user_id,
            entry_id=entry_id(memory_id),
            content=content,
        )
        if entry is None:
            raise ConsoleResourceNotFoundError(f"记忆不存在: {memory_id}")
        return memory_wire(entry)

    async def delete_memory(self, *, tenant_id: str, user_id: str, memory_id: str) -> None:
        entry = next(
            (
                item
                for item in await self._memory.list_entries(tenant_id=tenant_id, user_id=user_id)
                if str(item.id) == memory_id
            ),
            None,
        )
        if entry is None:
            raise ConsoleResourceNotFoundError(f"记忆不存在: {memory_id}")
        deleted = await self._memory.delete(
            tenant_id=tenant_id,
            user_id=user_id,
            entry_id=entry.id,
            memory_type=entry.memory_type,
        )
        if not deleted:
            raise ConsoleResourceNotFoundError(f"记忆不存在: {memory_id}")

    # ---- auto-learn（X407：学习开关） -----------------------------------------

    async def get_auto_learn(self, *, tenant_id: str, user_id: str) -> dict[str, object]:
        return {"enabled": await self._learning_enabled(tenant_id=tenant_id, user_id=user_id)}

    async def set_auto_learn(
        self, *, tenant_id: str, user_id: str, enabled: bool
    ) -> dict[str, object]:
        spec = await self._preference_json(tenant_id=tenant_id, user_id=user_id)
        spec["learning_enabled"] = enabled
        await self._users.set_preferences(
            tenant_id=tenant_id,
            platform_user_id=user_id,
            spec=spec,
            actor_id=user_id,
        )
        return {"enabled": enabled}

    # ---- 私有辅助 -------------------------------------------------------------

    async def _learning_enabled(self, *, tenant_id: str, user_id: str) -> bool:
        preference_json = await self._preference_json(tenant_id=tenant_id, user_id=user_id)
        return bool(preference_json.get("learning_enabled", True))

    async def _preference_json(self, *, tenant_id: str, user_id: str) -> dict[str, object]:
        """get_user_preferences 返回 {preference_json, updated_at} 包装——解包取本体。"""
        record = await self._store.get_user_preferences(
            tenant_id=tenant_id, platform_user_id=user_id
        )
        if isinstance(record, dict) and isinstance(record.get("preference_json"), dict):
            return dict(cast(dict[str, object], record["preference_json"]))
        return {}

    async def _profile_json(self, *, tenant_id: str, user_id: str) -> dict[str, object]:
        record = await self._store.get_latest_user_profile(
            tenant_id=tenant_id, platform_user_id=user_id
        )
        if record is not None and isinstance(record.get("profile_json"), dict):
            return dict(record["profile_json"])
        user = await self._store.get_platform_user(tenant_id=tenant_id, platform_user_id=user_id)
        if user is not None:
            return {"display_name": user.display_name}
        raise ConsoleResourceNotFoundError(f"user not found: {user_id}")

    async def _recent_workflow_runs(
        self, tenant_id: str, *, status: str | None = None
    ) -> list[Any]:
        statement = (
            select(workflow_run)
            .where(workflow_run.c.tenant_id == tenant_id)
            .order_by(workflow_run.c.updated_at.desc())
            .limit(_LIST_LIMIT)
        )
        if status is not None:
            statement = statement.where(workflow_run.c.status == status)
        async with self._engine.connect() as conn:
            return list((await conn.execute(statement)).mappings())

    async def _definition_for_row(self, tenant_id: str, row: Any) -> dict[str, object] | None:
        """按 pinned_refs 精确版本解析 workflow 定义（rule 6：ExecutionSnapshot pin）。"""
        for ref in row["pinned_refs"] or []:
            if not isinstance(ref, dict) or ref.get("kind") != "workflow":
                continue
            definition = await self._store.get(
                ResourceKind.WORKFLOW,
                str(ref.get("id", "")),
                tenant_id=tenant_id,
                version=str(ref.get("version", "")) or None,
            )
            if definition is not None:
                return definition.spec_json
        return None

    async def _chat_tasks(self, *, tenant_id: str, user_id: str) -> list[dict[str, object]]:
        return [
            chat_task(session)
            for session in await self._chat_sessions(tenant_id=tenant_id, user_id=user_id)
        ]

    async def _chat_task_for_session(
        self, *, tenant_id: str, user_id: str, session_id: str
    ) -> dict[str, object] | None:
        for session in await self._chat_sessions(tenant_id=tenant_id, user_id=user_id):
            if str(session["session_id"]) == session_id:
                return chat_task(session)
        return None

    async def _chat_sessions(self, *, tenant_id: str, user_id: str) -> list[dict[str, object]]:
        """session_memory（l1）按 session 聚合：首条用户消息 + 末条消息 + 起止时间。"""
        statement = (
            select(
                session_memory.c.session_id,
                session_memory.c.role,
                session_memory.c.content,
                session_memory.c.created_at,
            )
            .where(
                session_memory.c.tenant_id == tenant_id,
                session_memory.c.user_id == user_id,
                session_memory.c.level == "l1",
            )
            .order_by(session_memory.c.id.asc())
        )
        sessions: dict[str, dict[str, object]] = {}
        async with self._engine.connect() as conn:
            rows = (await conn.execute(statement)).mappings()
            for row in rows:
                session_id = str(row["session_id"])
                session = sessions.setdefault(
                    session_id,
                    {
                        "session_id": session_id,
                        "first_content": "",
                        "last_content": "",
                        "first_at": row["created_at"],
                        "last_at": row["created_at"],
                    },
                )
                if not session["first_content"] and row["role"] == "user":
                    session["first_content"] = str(row["content"])
                session["last_content"] = str(row["content"])
                session["last_at"] = row["created_at"]
        return list(sessions.values())
