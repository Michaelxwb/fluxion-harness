"""DbosWorkflowEngine：`WorkflowEngine` Protocol 的 DBOS 生产实现（ADR-013）。

归属 runtime 包（与 Protocol 同包，镜像 RegistryStore adapter 同包模式；design
§3.1「DbosWorkflowEngine 归属」）。本模块只承载 client 侧 start/signal/cancel/
status——引擎执行（`_run_graph` 解释器 + 节点 executor + queue 监听）由独立
`fluxion-workflow-worker` 进程承载（design §4.1 部署模型）。

关键机制（PoC `tests/workflow_poc/dbos_app.py` 已验证，DBOS 2.31）：
- DBOS 进程内全局单例（`_dbos_global_instance`）：本模块不 import 时序破坏它，
  `configure_dbos` 幂等创建，已存在则复用（同进程不得混用不同 database_url）；
- 查询/信号类 DBOS 客户端 API 统一 `asyncio.to_thread`（DBOS `*_async` 客户端
  方法绑定首个 event loop，RISK-P3-04）；
- DBOS 对不可达 backend 的 launch/客户端调用会内部无限重试（实测 sys_db 重连
  循环 60s backoff）——所有成员以 `asyncio.wait_for` 有界封装（规则 18 非 hang），
  连接类失败映射 `WorkflowBackendUnavailableError` 交由 `ResilientWorkflowEngine`
  熔断（E-01）；
- start 用 `SetWorkflowID` 幂等（同 execution 二次 start 返回既有 run，S-05）。

禁 double retry（RULE-P3-04）：本层不做 step 级重试，step 级 durable retry 归
DBOS；本层 fail policy 只覆盖 backend 调用（launch/查询/信号）。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import Any

from dbos import DBOS, DBOSClient, Queue, SetWorkflowID
from dbos._dbos_config import DBOSConfig

from fluxion.config.workflow import WorkflowBackendSettings
from fluxion.errors.workflow import (
    WORKFLOW_ENGINE_FAILURE,
    WorkflowBackendUnavailableError,
    WorkflowEngineError,
    WorkflowRunNotFoundError,
)
from fluxion.observability.logging import emit_workflow_event_log
from fluxion.registry.store import RegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime.workflow import (
    WorkflowExecutionHistory,
    WorkflowRunStatus,
    WorkflowStartRequest,
    WorkflowStartResult,
    WorkflowStepRecord,
)

DBOS_APP_NAME = "fluxion-workflow"
DEFAULT_DBOS_DATABASE_URL = "postgresql://mmuser:mmuser@localhost:5432/fluxion_workflow"

# DBOS 终态集合（TASK-007 terminal GC）：达到终态的 run 不再 acquire active refs
# （幂等二次 start / S-05），worker 观察到终态后释放引用。与 cli.workflow_worker 共享。
TERMINAL_STATUSES = frozenset(
    {"SUCCESS", "ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"}
)

# DBOS 状态（大写）→ 投影 status（design §3.3 词表 running/succeeded/failed/
# cancelled/paused，VARCHAR(16) 内）——worker/recover 写投影必须走此映射，
# 禁止 `.lower()`（"max_recovery_attempts_exceeded" 31 字符超列宽 + 词表外，P1-11）。
# MappingProxyType：只读常量映射（非可变运行态，RULE-P3-01 模块级可变容器守护）。
PROJECTION_STATUS_BY_DBOS = MappingProxyType(
    {
        "SUCCESS": "succeeded",
        "ERROR": "failed",
        "CANCELLED": "cancelled",
        "MAX_RECOVERY_ATTEMPTS_EXCEEDED": "failed",
    }
)

# 队列部署（design §4.1）：`fluxion-workflow-worker` Deployment 监听 `DBOS_QUEUE_NAME`；
# `worker_concurrency` 有界（PoC 4，实测 8 任务 4/4 分摊）防单 worker 全认领。
DBOS_QUEUE_NAME = "fluxion-workflow"
DEFAULT_WORKER_CONCURRENCY = 4
# database_backed_queue=True 的 Queue 不进内存 registry（`_queue.py` 早退），
# 构造不依赖已 launch 的 DBOS 实例，模块级安全；真正生效需 `register_workflow_queue`
# 持久化到 `dbos.queues` 后由 worker 的 queue_thread 轮询。
WORKFLOW_QUEUE = Queue(
    DBOS_QUEUE_NAME,
    database_backed_queue=True,
    worker_concurrency=DEFAULT_WORKER_CONCURRENCY,
)

# 成员级有界等待（规则 18）：覆盖 DBOS 内部重试挂起；client 侧查询/信号正常耗时毫秒级。
DEFAULT_OP_TIMEOUT_SECONDS = 10.0
# launch（含 sysdb 迁移 + startup recovery）有界等待；backend 不可达时 DBOS 内部
# 重试循环不会自行失败，由该上限兜底（E-01 非 hang）。
DEFAULT_LAUNCH_TIMEOUT_SECONDS = 30.0

# 连接类异常（backend 不可达/超时）——统一映射 WorkflowBackendUnavailableError。
_INFRA_ERROR_TYPES: tuple[type[BaseException], ...] = ()
try:  # sqlalchemy 为 dbos 传递依赖；engine 侧捕获其连接池异常
    from sqlalchemy.exc import OperationalError as _SqlaOperationalError

    _INFRA_ERROR_TYPES = (*_INFRA_ERROR_TYPES, _SqlaOperationalError)
except ImportError:  # pragma: no cover - dbos 声明即保证 sqlalchemy 存在
    pass
try:
    from psycopg import OperationalError as _PsycopgOperationalError

    _INFRA_ERROR_TYPES = (*_INFRA_ERROR_TYPES, _PsycopgOperationalError)
except ImportError:  # pragma: no cover - psycopg 为声明依赖
    pass


def resolve_database_url(settings: WorkflowBackendSettings | None = None) -> str:
    """env（DBOS_DATABASE_URL）> 配置文件 > 默认本地库（backend-platform 优先级）。"""
    resolved = settings or WorkflowBackendSettings.resolve()
    return resolved.dbos_database_url or DEFAULT_DBOS_DATABASE_URL


_configure_lock = threading.Lock()
_configured_url: str | None = None


def configure_dbos(
    database_url: str,
    *,
    app_name: str = DBOS_APP_NAME,
) -> str:
    """幂等创建进程级 DBOS 全局实例（sysdb 与业务库同库，design §3.3）。

    返回实际生效的 database_url。已配置过则复用（DBOS 单例语义）；URL 不一致
    视为进程装配错误，快速失败而非静默串库。
    """
    global _configured_url
    with _configure_lock:
        if _configured_url is not None:
            if _configured_url != database_url:
                raise WorkflowEngineError(
                    "DBOS global instance already configured for a different database; "
                    "one database_url per process",
                    code=WORKFLOW_ENGINE_FAILURE,
                )
            return _configured_url
        DBOS(
            config=DBOSConfig(
                name=app_name,
                database_url=database_url,
                system_database_url=database_url,
                run_admin_server=False,
            )
        )
        _configured_url = database_url
        return database_url


def dbos_launched() -> bool:
    """DBOS 全局实例是否已进入 launch（含 startup recovery）。"""
    from dbos._dbos import _dbos_global_instance

    instance = _dbos_global_instance
    return instance is not None and getattr(instance, "_launched", False) is True


def register_workflow_queue(
    *,
    database_url: str,
    worker_concurrency: int = DEFAULT_WORKER_CONCURRENCY,
    polling_interval_sec: float = 1.0,
) -> Queue:
    """worker 进程装配 queue：持久化到 `dbos.queues`（database_backed_queue 不入
    内存 registry，须 register_queue 才会被 queue_thread 轮询）。

    `register_queue` 要求非 async 上下文（check_async 硬 raise）→ 调用方须置于
    后台线程（`fluxion.cli.workflow_worker` 已封装，PoC 已验证 ≤1s 被接管）。
    """
    configure_dbos(database_url)
    DBOS.register_queue(
        name=DBOS_QUEUE_NAME,
        worker_concurrency=worker_concurrency,
        polling_interval_sec=polling_interval_sec,
    )
    return WORKFLOW_QUEUE


class DbosWorkflowEngine:
    """`WorkflowEngine` Protocol 的 DBOS 生产实现（client 侧 7 成员）。

    - `start`：`SetWorkflowID`（run_id=`{workflow_id}:{execution_id}`）幂等启动
      `_run_graph` 解释器 workflow，返回前回查证明同步持久化（SLO-WF-01）；
    - 查询/信号类统一 `asyncio.to_thread` + `asyncio.wait_for` 有界；
    - launch 由后台线程承载（DBOS 对不可达 backend 无限重试），成员等待有界，
      超时/连接失败映射 `WorkflowBackendUnavailableError`（E-01 非 hang）。
    """

    def __init__(
        self,
        *,
        database_url: str | None = None,
        op_timeout_seconds: float = DEFAULT_OP_TIMEOUT_SECONDS,
        launch_timeout_seconds: float = DEFAULT_LAUNCH_TIMEOUT_SECONDS,
        auto_launch: bool = True,
        listen_queues: list[str] | None = None,
        enqueue_start: bool = False,
    ) -> None:
        self._database_url = configure_dbos(database_url or resolve_database_url())
        self._op_timeout_seconds = op_timeout_seconds
        self._launch_timeout_seconds = launch_timeout_seconds
        self._launch_event = threading.Event()
        self._launch_error: BaseException | None = None
        self._enqueue_start = enqueue_start
        # 只读 client（execution history）：免 launch 直连 sysdb，API/Console 进程
        # 读路径不触发 DBOS.launch，避免与运行中 worker 抢 recovery/queue（S-11）。
        self._read_client_instance: DBOSClient | None = None
        if auto_launch:
            self._start_launch_thread(listen_queues)

    # ---- WorkflowEngine Protocol 成员 ----

    async def start(self, request: WorkflowStartRequest) -> WorkflowStartResult:
        await self._ensure_launched()
        run_id = workflow_run_id(request.workflow_id, request.execution_id)
        # TASK-007：对 `pinned_refs` 逐项 acquire（ref_type=workflow）。已存在的
        # 终态 run（幂等二次 start，S-05）不再 acquire——引用已由首次 run 的
        # terminal release 释放，重 acquire 会留孤儿行。
        existing = await self._call("start", lambda: DBOS.get_workflow_status(run_id))
        is_new = existing is None
        acquired = False
        if is_new or _map_status(existing) not in TERMINAL_STATUSES:
            await self._acquire_references(request, run_id)
            acquired = True
        try:
            definition = await self._resolve_definition(request)
            launch = (
                lambda: self._enqueue_graph(run_id, definition, request)
                if self._enqueue_start
                else self._start_graph(run_id, definition, request)
            )
            handle = await self._call("start", launch)
            del handle  # start 返回 handle；durable 证明走下方回查
            durable = await self._call(
                "start",
                lambda: DBOS.get_workflow_status(run_id),
            )
        except Exception:
            # 新 run start 失败（resolve/launch/durable 回查任一环节）→ 回滚引用；
            # 既有 run 失败不回滚（其引用仍被存活 run 持有）。
            if acquired:
                await self._release_if_not_live(run_id, request.tenant_id)
            raise
        if durable is None:
            if acquired:
                await self._release_if_not_live(run_id, request.tenant_id)
            raise WorkflowBackendUnavailableError(
                f"workflow {run_id} not durable immediately after start"
            )
        emit_workflow_event_log(
            event="workflow.started",
            run_id=run_id,
            tenant_id=request.tenant_id,
            trace_id=request.trace_id,
            execution_id=request.execution_id,
        )
        return WorkflowStartResult(run_id=run_id, status="started")

    async def resume(self, run_id: str) -> WorkflowRunStatus:
        status = await self._status_or_raise(run_id)
        return WorkflowRunStatus(run_id=run_id, status=_map_status(status))

    async def signal(self, run_id: str, name: str, payload: object) -> None:
        await self._ensure_launched()
        await self._call("signal", lambda: DBOS.send(run_id, payload, f"{name}:{run_id}"))

    async def cancel(self, run_id: str, *, timeout: float) -> None:
        await self._ensure_launched()
        await asyncio.wait_for(
            asyncio.to_thread(DBOS.cancel_workflow, run_id), timeout=timeout
        )

    async def get_status(self, run_id: str) -> WorkflowRunStatus:
        status = await self._status_or_raise(run_id)
        return WorkflowRunStatus(run_id=run_id, status=_map_status(status))

    async def await_result(self, run_id: str, *, timeout: float) -> object:
        await self._ensure_launched()
        result = await asyncio.wait_for(
            asyncio.to_thread(DBOS.get_result, run_id), timeout=timeout
        )
        return result

    async def get_execution_history(self, run_id: str) -> WorkflowExecutionHistory:
        # 只读路径（API/Console，S-11）：DBOSClient 直连 sysdb 免 launch，不参与
        # 恢复/队列消费——API 进程查运行中 run 不会抢运行中 worker 的 recovery。
        client = self._read_client()
        handle = await self._call(
            "get_execution_history",
            lambda: client.retrieve_workflow(run_id),
        )
        status = _map_status(handle.get_status())
        steps = await self._call(
            "get_execution_history",
            lambda: client.list_workflow_steps(run_id),
        )
        records = tuple(
            WorkflowStepRecord(
                # DBOS StepInfo（TypedDict）：function_name 是 DBOS 侧 step 函数名
                # （`_run_node` / `DBOS.sleep` / `DBOS.recv` 等），非 workflow 节点
                # ID；节点 ID 级状态在投影 `node_states`（TASK-008）。function_id
                # 是 step 在 workflow 内的调用序号，一并编码便于回溯。
                node_id=str(step.get("function_name") or f"step-{index}"),
                status="SUCCESS" if step.get("error") is None else "ERROR",
                output=step.get("output"),
                error=(
                    str(step["error"])
                    if step.get("error") is not None
                    else None
                ),
            )
            for index, step in enumerate(steps)
        )
        return WorkflowExecutionHistory(
            run_id=run_id, status=status, steps=records
        )

    def _read_client(self) -> DBOSClient:
        if self._read_client_instance is None:
            self._read_client_instance = DBOSClient(
                system_database_url=self._database_url,
                application_name=DBOS_APP_NAME,
                use_listen_notify=False,
            )
        return self._read_client_instance

    # ---- TASK-007：terminal release（worker 观察到终态后调用，按 run_id 释放）----

    async def release_run_references(self, run_id: str, *, tenant_id: str) -> None:
        """run 到达终态（succeeded/failed/cancelled）后释放其全部 active refs。

        由 `fluxion-workflow-worker`（start 结果路径 / recover 终态路径）调用；
        按 ref_id（run_id）精确解绑，无需重放 run_meta。未装配 releaser 时 no-op。
        releaser 是 sync psycopg 路径（解释器在 DBOS 独立 event loop，P0-2 统一）。
        """
        releaser = get_reference_releaser()
        if releaser is None:
            return
        releaser(tenant_id=tenant_id, ref_type="workflow", ref_id=run_id)

    # ---- 内部：launch / 有界调用 / 错误映射 ----

    def _start_launch_thread(self, listen_queues: list[str] | None) -> None:
        if dbos_launched():
            self._launch_event.set()
            return

        def _launch() -> None:
            try:
                if listen_queues is not None:
                    DBOS.listen_queues(listen_queues)
                DBOS.launch()
            except BaseException as error:  # noqa: BLE001 — 线程边界：记录后由等待方映射
                self._launch_error = error
            finally:
                self._launch_event.set()

        threading.Thread(target=_launch, name="dbos-launch", daemon=True).start()

    async def _ensure_launched(self) -> None:
        if not self._launch_event.wait(timeout=self._launch_timeout_seconds):
            raise WorkflowBackendUnavailableError(
                "DBOS launch did not complete within "
                f"{self._launch_timeout_seconds}s (backend unavailable?)"
            )
        if self._launch_error is not None:
            raise WorkflowBackendUnavailableError(
                f"DBOS launch failed: {self._launch_error}"
            )

    async def _call(self, operation: str, call: Callable[[], Any]) -> Any:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(call), timeout=self._op_timeout_seconds
            )
        except WorkflowEngineError:
            raise
        except _INFRA_ERROR_TYPES as error:
            raise WorkflowBackendUnavailableError(
                f"workflow backend call {operation} failed: {error}"
            ) from error
        except TimeoutError as error:
            raise WorkflowBackendUnavailableError(
                f"workflow backend call {operation} timed out after "
                f"{self._op_timeout_seconds}s"
            ) from error
        except Exception as error:
            from dbos._error import DBOSNonExistentWorkflowError

            if isinstance(error, DBOSNonExistentWorkflowError):
                raise WorkflowRunNotFoundError(f"workflow run not found: {error}") from error
            raise WorkflowEngineError(
                f"workflow backend call {operation} failed: {error}",
                code=WORKFLOW_ENGINE_FAILURE,
            ) from error

    def _start_graph(
        self, run_id: str, definition: Mapping[str, object], request: WorkflowStartRequest
    ) -> Any:
        from fluxion.runtime.workflow_graph import run_graph_workflow

        with SetWorkflowID(run_id):
            return DBOS.start_workflow(
                run_graph_workflow,
                dict(definition),
                dict(request.arguments),
                self._run_meta(run_id, request),
            )

    def _enqueue_graph(
        self, run_id: str, definition: Mapping[str, object], request: WorkflowStartRequest
    ) -> Any:
        """队列式 start：enqueue 到 `fluxion-workflow` queue（S-06 database-backed
        queue）。`SetWorkflowID` 下的 enqueue 走同一 deduplication_id，同 execution
        二次 enqueue 幂等（Probe 实测：返回既有 run，input 保留首启）。
        """
        from fluxion.runtime.workflow_graph import run_graph_workflow

        with SetWorkflowID(run_id):
            return WORKFLOW_QUEUE.enqueue(
                run_graph_workflow,
                dict(definition),
                dict(request.arguments),
                self._run_meta(run_id, request),
            )

    def _run_meta(
        self, run_id: str, request: WorkflowStartRequest
    ) -> dict[str, object]:
        return {
            "run_id": run_id,
            "tenant_id": request.tenant_id,
            "user_id": request.user_id,
            "execution_id": request.execution_id,
            "trace_id": request.trace_id,
            "pinned": [
                {"kind": ref.kind, "id": ref.id, "version": ref.version}
                for ref in request.pinned
            ],
        }

    async def _acquire_references(
        self, request: WorkflowStartRequest, run_id: str
    ) -> None:
        """start 时对 `request.pinned` 逐项 acquire active reference（ref_type=workflow）。

        引用坐标 = ExecutionSnapshot pinned 版本（RULE-P3-03）；未装配 store 时
        no-op（既有 worker 测试无 Registry 接线）。重复 acquire 由 add 的
        ON CONFLICT DO NOTHING 幂等兜底。
        """
        store = get_reference_store()
        if store is None:
            return
        acquired: list[object] = []
        try:
            for ref in request.pinned:
                await store.add_active_reference(
                    tenant_id=request.tenant_id,
                    kind=ResourceKind(ref.kind),
                    resource_id=ref.id,
                    version=ref.version,
                    ref_type="workflow",
                    ref_id=run_id,
                )
                acquired.append(ref)
        except Exception:
            # P1-4：acquire 循环中途失败 → 回滚已 acquire 的部分引用（不留孤儿行）
            for ref in acquired:
                try:
                    await store.release_active_reference(
                        tenant_id=request.tenant_id,
                        kind=ResourceKind(ref.kind),
                        resource_id=ref.id,
                        version=ref.version,
                        ref_type="workflow",
                        ref_id=run_id,
                    )
                except Exception:  # noqa: BLE001 — 回滚最佳努力，不掩盖原始失败
                    continue
            raise

    async def _release_run_refs(self, tenant_id: str, run_id: str) -> None:
        # review P1-1 修复：原代码引用未定义名 `_reference_releaser`（自 phase3
        # 存在）——start 失败回滚路径触发 NameError，acquired 引用残留且原始
        # 错误被掩盖。releaser 是 sync callable（P0-2 注解修正后两处调用点均
        # 不 await），此处直接调用。
        releaser = get_reference_releaser()
        if releaser is None:
            return
        releaser(tenant_id=tenant_id, ref_type="workflow", ref_id=run_id)

    async def _release_if_not_live(self, run_id: str, tenant_id: str) -> None:
        """start 失败回滚引用，但仅当 run 确认不存活（不存在/已终态）才释放。

        P1-5 竞态：start 超时/失败时 DBOS 后台线程仍可能最终把 run 拉起 → 此时
        释放会让"活 run 零引用"、pinned 版本可被 mid-run hard-delete。状态查询
        失败视为未知 → 保守保留引用（宁可孤儿，不可移除存活 run 的引用）。
        """
        try:
            status = await self._call("start", lambda: DBOS.get_workflow_status(run_id))
        except Exception:  # noqa: BLE001 — 状态未知：不释放
            return
        if status is None or _map_status(status) in TERMINAL_STATUSES:
            await self._release_run_refs(tenant_id, run_id)

    async def _resolve_definition(
        self, request: WorkflowStartRequest
    ) -> Mapping[str, object]:
        workflow_ref = next(
            (ref for ref in request.pinned if ref.kind == "workflow"), None
        )
        if workflow_ref is None:
            raise WorkflowEngineError(
                "WorkflowStartRequest.pinned must contain the workflow version ref",
                code=WORKFLOW_ENGINE_FAILURE,
            )
        provider = _definition_provider()
        if provider is None:
            raise WorkflowEngineError(
                "workflow definition provider not configured", code=WORKFLOW_ENGINE_FAILURE
            )
        return await provider(request.tenant_id, workflow_ref.id, workflow_ref.version)

    async def _status_or_raise(self, run_id: str) -> Any:
        await self._ensure_launched()
        status = await self._call("get_status", lambda: DBOS.get_workflow_status(run_id))
        if status is None:
            raise WorkflowRunNotFoundError(f"workflow run {run_id} not found")
        return status


# ---------------------------------------------------------------------------
# definition provider：tenant + workflow_id + version → spec dict（Registry 读路径）
# ---------------------------------------------------------------------------

DefinitionProvider = Callable[[str, str, str], Awaitable[Mapping[str, object]]]
_definition_provider_instance: DefinitionProvider | None = None
_provider_lock = threading.Lock()


def set_definition_provider(provider: DefinitionProvider | None) -> None:
    """装配进程级 definition provider（worker/API 启动时注入 Registry 读路径）。"""
    global _definition_provider_instance
    with _provider_lock:
        _definition_provider_instance = provider


def _definition_provider() -> DefinitionProvider | None:
    return _definition_provider_instance


def get_definition_provider() -> DefinitionProvider | None:
    """进程级 definition provider（引擎 start 路径，主 loop 上 async 调用）。"""
    return _definition_provider_instance


# subworkflow 节点在 DBOS workflow 函数内解析子定义——DBOS 在独立 event loop，
# 不能调 async SQLAlchemy engine（"Future attached to a different loop"），须走
# sync psycopg resolver（P0-1）。与 async provider 平行装配。
SyncDefinitionResolver = Callable[[str, str, str], Mapping[str, object]]
_sync_definition_resolver_instance: SyncDefinitionResolver | None = None
_sync_definition_resolver_lock = threading.Lock()


def set_sync_definition_resolver(resolver: SyncDefinitionResolver | None) -> None:
    """装配 sync definition resolver（worker 启动时注入；解释器 subworkflow 用）。"""
    global _sync_definition_resolver_instance
    with _sync_definition_resolver_lock:
        _sync_definition_resolver_instance = resolver


def get_sync_definition_resolver() -> SyncDefinitionResolver | None:
    """进程级 sync resolver（未装配返回 None）。"""
    with _sync_definition_resolver_lock:
        return _sync_definition_resolver_instance


# ---------------------------------------------------------------------------
# active reference store / releaser：start acquire / terminal release（TASK-007）
# ---------------------------------------------------------------------------

# acquire 走 RegistryStore.add_active_reference（Protocol）；terminal release 按
# ref_id（run_id）释放，非 Protocol 成员，以进程级回调注入——镜像
# set_definition_provider 装配模式，不扩展 RegistryStore 核心 Contract（rule 25）。
_reference_store_instance: RegistryStore | None = None
_reference_releaser_instance: Callable[..., None] | None = None


def set_reference_store(store: RegistryStore | None) -> None:
    """装配进程级 active-ref store（start acquire 用；worker/API 启动时注入）。"""
    global _reference_store_instance
    _reference_store_instance = store


def get_reference_store() -> RegistryStore | None:
    """进程级 active-ref store（未装配返回 None，acquire 为 no-op）。"""
    return _reference_store_instance


def set_reference_releaser(releaser: Callable[..., None] | None) -> None:
    """装配进程级 terminal releaser：`(tenant_id, ref_type, ref_id) -> None`。"""
    global _reference_releaser_instance
    _reference_releaser_instance = releaser


def get_reference_releaser() -> Callable[..., None] | None:
    """进程级 terminal releaser（未装配返回 None，release 为 no-op）。"""
    return _reference_releaser_instance


def workflow_run_id(workflow_id: str, execution_id: str) -> str:
    """run_id = `{workflow_id}:{execution_id}`（同 execution 重放 → 同 run，S-05）。"""
    return f"{workflow_id}:{execution_id}"


def _map_status(dbos_status: Any) -> str:
    """DBOS 2.31 `WorkflowStatus` 是 dataclass（`.status` 为 str 字面量，非 `.value` enum）。

    `.status` 缺省时回退 `.value`，最后兜底 `str()`；最终统一大写映射到
    `WorkflowRunStatus.status`（PENDING/SUCCESS/ERROR/CANCELLED/...）。
    """
    for attribute in ("status", "value"):
        candidate = getattr(dbos_status, attribute, None)
        if isinstance(candidate, str) and candidate:
            return candidate.upper()
    return str(dbos_status).upper()
