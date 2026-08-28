"""durable graph 解释器（design §3.1 D4 / §3.4；TASK-004/006）。

单注册 DBOS workflow `run_graph_workflow`：数据驱动遍历 WorkflowDefinition V2
图（免按定义 codegen/动态注册）。遍历语义（确定性，DBOS replay 安全——已完
step 结果按 args memoize）：

1. 节点按 `depends_on` 拓扑波次执行；同波就绪节点 `asyncio.gather` 并发
   （NFR-PERF-03）。
2. 节点就绪 = 全部 `depends_on` 目标已有输出；节点被跳过 = 任一依赖被跳过
   （分支剪枝的传播规则）。
3. 路由节点（condition/switch）经 `_run_node` step 求值，输出 `next` 后继集；
   依赖该路由节点但不在 `next` 集内的后继被跳过（剪枝）。
4. parallel 节点为控制节点：其 branch 成员隐式依赖该 parallel（先启动后汇聚）；
   `join_policy=all` 全部成员完成才汇聚，`any` 首个完成分支即汇聚（其余分支
   仍执行完以保确定性）。下游依赖 parallel 的节点等汇聚后就绪。
5. wait → `DBOS.sleep_async`（durable timer）；human_task → `DBOS.recv_async`
   （durable signal，topic=`{node_id}:{run_id}`，与 `DbosWorkflowEngine.signal`
   的 `send(topic=f"{name}:{run_id}")` 对齐：signal name = human_task 节点 ID）；
   subworkflow → 嵌套 `run_graph_workflow`。三者是 workflow 上下文操作
   （DBOS 语义要求），不经过 `_run_node` step。

Retry 边界（RULE-P3-04 禁 double retry）：`_run_node` step 声明
`retries_allowed=True`（DBOS step 级 durable retry，副作用幂等性归 step 实现）；
解释器/executor 内不得再套 Fluxion 层重试。节点 `timeout_ms` 在 step 内以
`asyncio.timeout` 有界（每次重试获得全新 timeout），超时转节点 ERROR
（S-04，禁无限等待）。

capability/agent 节点经进程级 executor hook 执行（worker 启动时装配真实
Capability Contract / AgentRuntime 执行路径；未装配快速失败）——Tool 是
Adapter、业务在 Capability（RULE-fluxion-workflow-001），解释器不感知具体
Provider。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from dbos import DBOS, SetWorkflowID

from fluxion.errors.workflow import WORKFLOW_ENGINE_FAILURE, WorkflowEngineError
from fluxion.observability.logging import emit_workflow_event_log
from fluxion.runtime.workflow_expressions import (
    WorkflowExpressionError,
    evaluate_expression,
    render_template,
)
from fluxion.runtime.workflow_projection import get_projection_writer

_STEP_MAX_ATTEMPTS = 3
_STEP_RETRY_INTERVAL_SECONDS = 0.2
# subworkflow 嵌套深度上界（P0-1：A 含 A 自引用 → 运行时无限嵌套）。
_MAX_SUBWORKFLOW_DEPTH = 5
# human_task 未设 `timeout_seconds`（无超时，审批可挂多日）时 DBOS `recv_async` 的
# 兜底超时。DBOS 要求 timeout_seconds 必填（`record_sleep` 内部 `time.time()+seconds`，
# None 会 TypeError）；取 HumanTaskNode 模型上界 30 天 ≈ 无限等待。
_HUMAN_TASK_NO_TIMEOUT_SECONDS = 2_592_000.0


# ---------------------------------------------------------------------------
# executor hooks（worker bootstrap 装配；解释器只依赖 hook 契约）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilityNodeRequest:
    """capability 节点执行请求（按 `skill|tool|mcp` 前缀 dispatch）。"""

    prefix: str
    capability_ref: str
    tenant_id: str
    user_id: str
    run_id: str
    node_id: str
    input: dict[str, object]


@dataclass(frozen=True, slots=True)
class AgentNodeRequest:
    """agent 节点执行请求（agent_ref → ContextResolver → pinned ExecutionSnapshot）。"""

    agent_ref: str
    prompt: str
    max_turns: int | None
    tenant_id: str
    user_id: str
    run_id: str
    node_id: str
    input: dict[str, object]


CapabilityExecutor = Callable[[CapabilityNodeRequest], Awaitable[object]]
AgentExecutor = Callable[[AgentNodeRequest], Awaitable[object]]

_capability_executors: dict[str, CapabilityExecutor] = {}
_agent_executor: AgentExecutor | None = None


def set_capability_executor(prefix: str, executor: CapabilityExecutor) -> None:
    """装配某前缀（skill/tool/mcp/plugin）的 capability executor。"""
    _capability_executors[prefix] = executor


def clear_capability_executors() -> None:
    _capability_executors.clear()


def set_agent_executor(executor: AgentExecutor | None) -> None:
    global _agent_executor
    _agent_executor = executor


# ---------------------------------------------------------------------------
# _run_node：@DBOS.step 节点 executor（capability/agent/condition/switch/transform）
# ---------------------------------------------------------------------------


@DBOS.step(
    retries_allowed=True,
    interval_seconds=_STEP_RETRY_INTERVAL_SECONDS,
    max_attempts=_STEP_MAX_ATTEMPTS,
)
async def _run_node(
    kind: str,
    node_def: dict[str, object],
    scope: dict[str, object],
    run_meta: dict[str, object],
) -> object:
    """节点级 durable executor：step 级 retry 归 DBOS；timeout_ms 有界。"""
    node_id = str(node_def.get("id", ""))
    timeout_ms = node_def.get("timeout_ms")
    timeout_seconds = float(timeout_ms) / 1000.0 if timeout_ms is not None else None
    try:
        async with asyncio.timeout(timeout_seconds):
            return await _dispatch_node(kind, node_def, scope, run_meta)
    except TimeoutError as error:
        raise WorkflowEngineError(
            f"node {node_id} timed out after {timeout_ms}ms",
            code=WORKFLOW_ENGINE_FAILURE,
        ) from error


async def _dispatch_node(
    kind: str,
    node_def: dict[str, object],
    scope: dict[str, object],
    run_meta: dict[str, object],
) -> object:
    node_id = str(node_def.get("id", ""))
    if kind == "capability":
        return await _execute_capability(node_def, scope, run_meta)
    if kind == "agent":
        return await _execute_agent(node_def, scope, run_meta)
    if kind == "transform":
        template = str(node_def.get("transform", ""))
        return render_template(template, scope)
    if kind == "condition":
        expression = str(node_def.get("expression", ""))
        condition = bool(evaluate_expression(expression, scope))
        next_ids = list(node_def.get("then", []) if condition else node_def.get("else", []))
        return {"condition": condition, "next": next_ids}
    if kind == "switch":
        return _route_switch(node_def, scope)
    raise WorkflowEngineError(
        f"node {node_id} has unsupported executor kind {kind!r}",
        code=WORKFLOW_ENGINE_FAILURE,
    )


def _route_switch(node_def: dict[str, object], scope: dict[str, object]) -> object:
    expression = str(node_def.get("expression", ""))
    value = evaluate_expression(expression, scope)
    cases = list(node_def.get("cases", []))
    for case in cases:
        if not isinstance(case, dict):
            continue
        if str(case.get("value", "")) == _case_key(value):
            return {"value": value, "next": list(case.get("node_ids", []))}
    return {"value": value, "next": list(node_def.get("default", []))}


def _case_key(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


async def _execute_capability(
    node_def: dict[str, object],
    scope: dict[str, object],
    run_meta: dict[str, object],
) -> object:
    capability_ref = str(node_def.get("capability_ref", ""))
    prefix = capability_ref.split(":", 1)[0] if ":" in capability_ref else ""
    executor = _capability_executors.get(prefix)
    if executor is None:
        raise WorkflowEngineError(
            f"capability executor not configured for prefix {prefix!r}",
            code=WORKFLOW_ENGINE_FAILURE,
        )
    request = CapabilityNodeRequest(
        prefix=prefix,
        capability_ref=capability_ref,
        tenant_id=str(run_meta.get("tenant_id", "")),
        user_id=str(run_meta.get("user_id", "")),
        run_id=str(run_meta.get("run_id", "")),
        node_id=str(node_def.get("id", "")),
        input=_render_input(node_def.get("input"), scope),
    )
    return await executor(request)


async def _execute_agent(
    node_def: dict[str, object],
    scope: dict[str, object],
    run_meta: dict[str, object],
) -> object:
    if _agent_executor is None:
        raise WorkflowEngineError(
            "agent executor not configured", code=WORKFLOW_ENGINE_FAILURE
        )
    request = AgentNodeRequest(
        agent_ref=str(node_def.get("agent_ref", "")),
        prompt=str(node_def.get("prompt", "")),
        max_turns=node_def.get("max_turns"),  # type: ignore[arg-type]
        tenant_id=str(run_meta.get("tenant_id", "")),
        user_id=str(run_meta.get("user_id", "")),
        run_id=str(run_meta.get("run_id", "")),
        node_id=str(node_def.get("id", "")),
        input=_render_input(node_def.get("input"), scope),
    )
    return await _agent_executor(request)


def _render_input(
    raw: object, scope: Mapping[str, object]
) -> dict[str, object]:
    """节点静态 input 的字符串值做 `{{ }}` 引用插值（非字符串值原样）。"""
    if not isinstance(raw, Mapping):
        return {}
    rendered: dict[str, object] = {}
    for key, value in raw.items():
        rendered[str(key)] = render_template(str(value), scope) if isinstance(value, str) else value
    return rendered


# ---------------------------------------------------------------------------
# workflow_run 投影写路径（TASK-008 / FEAT-P3-06）：解释器分批写 node_states
# ---------------------------------------------------------------------------


def _structured_node_states(
    run_id: str,
    outputs: Mapping[str, object],
    states: Mapping[str, str],
    skipped: set[str],
) -> dict[str, dict[str, object]]:
    """`states`(str) + `outputs` → design §3.3 `{node_id: {status, output_ref, error}}`。

    `output_ref` 是输出定位引用（`run:{run_id}:node:{node_id}:output`），不内联
    Secret 类输出（rule 17）；实际输出在 DBOS step 结果 / execution history。
    """
    result: dict[str, dict[str, object]] = {}
    for node_id, status in states.items():
        result[node_id] = {
            "status": status,
            "output_ref": (
                f"run:{run_id}:node:{node_id}:output" if node_id in outputs else None
            ),
            "error": None,
        }
    for node_id in skipped:
        result[node_id] = {"status": "skipped", "output_ref": None, "error": None}
    return result


def _projection_upsert_run(run_meta: Mapping[str, object]) -> None:
    writer = get_projection_writer()
    if writer is None:
        return
    try:
        writer.upsert_run(run_meta)
    except Exception as error:  # noqa: BLE001 — 投影写错误隔离：可观测性不影响业务执行
        emit_workflow_event_log(
            event="workflow.projection.upsert_failed",
            level="warning",
            run_id=str(run_meta.get("run_id", "")),
            tenant_id=str(run_meta.get("tenant_id", "")) or None,
            trace_id=str(run_meta.get("trace_id", "")) or None,
            detail=f"{type(error).__name__}: {error}",
        )


def _projection_write_states(
    run_meta: Mapping[str, object],
    outputs: Mapping[str, object],
    states: Mapping[str, str],
    skipped: set[str],
) -> None:
    writer = get_projection_writer()
    if writer is None:
        return
    try:
        writer.update_node_states(
            tenant_id=str(run_meta.get("tenant_id", "")),
            run_id=str(run_meta.get("run_id", "")),
            node_states=_structured_node_states(
                str(run_meta.get("run_id", "")), outputs, states, skipped
            ),
        )
    except Exception as error:  # noqa: BLE001 — 投影写错误隔离
        emit_workflow_event_log(
            event="workflow.projection.write_states_failed",
            level="warning",
            run_id=str(run_meta.get("run_id", "")),
            tenant_id=str(run_meta.get("tenant_id", "")) or None,
            trace_id=str(run_meta.get("trace_id", "")) or None,
            detail=f"{type(error).__name__}: {error}",
        )


def _projection_finish(
    run_meta: Mapping[str, object],
    status: str,
    outputs: Mapping[str, object],
    states: Mapping[str, str],
    skipped: set[str],
) -> None:
    writer = get_projection_writer()
    if writer is None:
        return
    try:
        writer.finish_run(
            tenant_id=str(run_meta.get("tenant_id", "")),
            run_id=str(run_meta.get("run_id", "")),
            status=status,
            node_states=_structured_node_states(
                str(run_meta.get("run_id", "")), outputs, states, skipped
            ),
        )
    except Exception as error:  # noqa: BLE001 — 投影写错误隔离
        emit_workflow_event_log(
            event="workflow.projection.finish_failed",
            level="warning",
            run_id=str(run_meta.get("run_id", "")),
            tenant_id=str(run_meta.get("tenant_id", "")) or None,
            trace_id=str(run_meta.get("trace_id", "")) or None,
            detail=f"{type(error).__name__}: {error}",
        )


def _release_run_refs(run_meta: Mapping[str, object]) -> None:
    """terminal 释放 active refs（P0-2：serve 模式无终态接线）。releaser 未装配 no-op。

    由解释器在 run 到达终态（succeeded/failed）时调用，使 start/serve/recover 三
    种 worker 形态统一走同一终态路径；幂等（重复释放 no-op）。releaser 是 sync
    psycopg 路径（DBOS workflow 在独立 event loop，不能调 async engine）；错误
    隔离——释放失败只记日志，不掩盖 workflow 结果。
    """
    from fluxion.runtime.workflow_dbos import get_reference_releaser

    releaser = get_reference_releaser()
    if releaser is None:
        return
    try:
        releaser(
            tenant_id=str(run_meta.get("tenant_id", "")),
            ref_type="workflow",
            ref_id=str(run_meta.get("run_id", "")),
        )
    except Exception as error:  # noqa: BLE001 — GC 错误隔离
        emit_workflow_event_log(
            event="workflow.release_refs_failed",
            level="warning",
            run_id=str(run_meta.get("run_id", "")),
            tenant_id=str(run_meta.get("tenant_id", "")) or None,
            trace_id=str(run_meta.get("trace_id", "")) or None,
            detail=f"{type(error).__name__}: {error}",
        )


# ---------------------------------------------------------------------------
# run_graph_workflow：单注册 DBOS workflow（数据驱动图遍历）
# ---------------------------------------------------------------------------


@DBOS.workflow()
async def run_graph_workflow(
    definition: dict[str, object],
    input: dict[str, object],
    run_meta: dict[str, object],
) -> dict[str, object]:
    """遍历 V2 定义图；返回 `{outputs, node_states}`（design §3.4）。"""
    nodes = {
        str(node.get("id", "")): node
        for node in definition.get("steps", [])
        if isinstance(node, dict)
    }
    if not nodes:
        raise WorkflowEngineError(
            "workflow definition has no nodes", code=WORKFLOW_ENGINE_FAILURE
        )
    outputs: dict[str, object] = {}
    states: dict[str, str] = {}
    skipped: set[str] = set()
    started_parallel: set[str] = set()
    member_of = _parallel_member_map(nodes)
    run_id = str(run_meta.get("run_id", ""))

    # TASK-008：run 启动 → 投影行（status=running + pinned_refs 版本快照）。
    # writer 未装配（非 worker 进程）no-op；upsert 幂等（replay 安全）。
    _projection_upsert_run(run_meta)

    try:
        while True:
            _propagate_skips(nodes, outputs, skipped, states)
            _settle_parallel_joins(nodes, outputs, states, skipped, started_parallel)
            ready = [
                node_id
                for node_id in nodes
                if node_id not in outputs
                and node_id not in skipped
                and _deps_ready(nodes[node_id], outputs)
                and _parallel_gate_open(node_id, member_of, started_parallel)
            ]
            # parallel 控制节点：立即启动（成员随后就绪），无自身执行体
            parallel_before = set(started_parallel)
            for node_id in ready:
                if nodes[node_id].get("type") == "parallel":
                    started_parallel.add(node_id)
                    states[node_id] = "running"
            newly_started_parallel = started_parallel - parallel_before
            executable = [
                node_id for node_id in ready if nodes[node_id].get("type") != "parallel"
            ]
            if not executable:
                if all(node_id in outputs or node_id in skipped for node_id in nodes):
                    break
                if newly_started_parallel:
                    # 本轮仅启动了 parallel 控制节点（无执行体），成员下一轮才就绪
                    # （成员还有自身依赖时）——不是死锁，继续下一波。
                    continue
                raise WorkflowEngineError(
                    "workflow graph deadlock: no executable node but graph unfinished",
                    code=WORKFLOW_ENGINE_FAILURE,
                )
            scope = {**outputs, "input": input}
            tasks = [
                asyncio.create_task(_execute_graph_node(nodes[node_id], scope, run_meta))
                for node_id in executable
            ]
            try:
                results = await asyncio.gather(*tasks)
            except Exception:
                # P1-10：单节点失败 → cancel 兄弟节点并等全部 settle（防副作用在
                # run 终态后落库成孤儿任务），再抛给外层终态处理。
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            for node_id, result in zip(executable, results):
                outputs[node_id] = result
                states[node_id] = "succeeded"
                emit_workflow_event_log(
                    event=f"workflow.node.{node_id}.succeeded",
                    run_id=run_id,
                    tenant_id=str(run_meta.get("tenant_id", "")) or None,
                    trace_id=str(run_meta.get("trace_id", "")) or None,
                )
                _apply_router_pruning(nodes, node_id, nodes[node_id], result, skipped)
            # TASK-008：一波节点完成 → 分批写 node_states（单行 UPDATE，PATTERN-backend-003）
            _projection_write_states(run_meta, outputs, states, skipped)
    except Exception:
        # P0-2：终态 failed 投影 + 释放 active refs——解释器统一承载终态路径，
        # serve（生产 Deployment）/start/recover 三模式一致，不再依赖 CLI 接线。
        _projection_finish(run_meta, "failed", outputs, states, skipped)
        _release_run_refs(run_meta)
        raise
    else:
        # P0-2：终态 succeeded + 释放 active refs
        _projection_finish(run_meta, "succeeded", outputs, states, skipped)
        _release_run_refs(run_meta)
    return {
        "outputs": outputs,
        "node_states": {**states, **{node_id: "skipped" for node_id in skipped}},
    }


async def _execute_graph_node(
    node: dict[str, object],
    scope: dict[str, object],
    run_meta: dict[str, object],
) -> object:
    """workflow 上下文节点：wait / human_task / subworkflow；其余走 `_run_node` step。"""
    kind = str(node.get("type", ""))
    node_id = str(node.get("id", ""))
    run_id = str(run_meta.get("run_id", ""))
    if kind == "wait":
        duration = float(node.get("duration_seconds", 0))
        await DBOS.sleep_async(duration)
        return {"waited_seconds": duration}
    if kind == "human_task":
        timeout_seconds = node.get("timeout_seconds")
        # DBOS `recv_async` 的 timeout_seconds 必填（见 `_HUMAN_TASK_NO_TIMEOUT_SECONDS`）；
        # 未设超时用模型上界兜底，近似无限等待。
        recv_timeout = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else _HUMAN_TASK_NO_TIMEOUT_SECONDS
        )
        payload = await DBOS.recv_async(
            topic=f"{node_id}:{run_id}",
            timeout_seconds=recv_timeout,
        )
        # P1-8：recv 返回 None（超时/被取消）→ 明确 ERROR，禁止把"无 signal"静默当
        # 成功放行（审批可能被空 payload 直接通过）。未设 timeout 时兜底 30 天上界
        # 到期同样视为超时，而非成功。
        if payload is None:
            raise WorkflowEngineError(
                f"human_task {node_id} not signaled before timeout",
                code=WORKFLOW_ENGINE_FAILURE,
            )
        return {"assignee": str(node.get("assignee", "")), "payload": payload}
    if kind == "subworkflow":
        return await _execute_subworkflow(node, scope, run_meta)
    return await _run_node(kind, node, scope, run_meta)


async def _execute_subworkflow(
    node: dict[str, object],
    scope: dict[str, object],
    run_meta: dict[str, object],
) -> object:
    from fluxion.runtime.workflow_dbos import get_sync_definition_resolver

    workflow_ref = str(node.get("workflow_ref", ""))
    resource_id = workflow_ref.removeprefix("workflow:").rsplit("@", 1)[0]
    version = workflow_ref.rsplit("@", 1)[1]
    node_id = str(node.get("id", ""))
    parent_run_id = str(run_meta.get("run_id", ""))
    depth = int(run_meta.get("subworkflow_depth", 0))
    if depth >= _MAX_SUBWORKFLOW_DEPTH:
        raise WorkflowEngineError(
            f"subworkflow nesting exceeds max depth {_MAX_SUBWORKFLOW_DEPTH} at {node_id}",
            code=WORKFLOW_ENGINE_FAILURE,
        )
    # P0-1：子定义解析必须走 sync resolver——解释器在 DBOS 独立 event loop，async
    # SQLAlchemy engine 不可用（"different loop"）；未装配则明确失败（非静默）。
    resolver = get_sync_definition_resolver()
    if resolver is None:
        raise WorkflowEngineError(
            "subworkflow definition sync resolver not configured",
            code=WORKFLOW_ENGINE_FAILURE,
        )
    sub_definition = resolver(str(run_meta.get("tenant_id", "")), resource_id, version)
    # P0-1：子流程派生独立 run_id（`{resource}:{parent_run_id}:sub:{node_id}`）+ parent
    # 血缘——子流程写自己的投影行，不复用父 run_id（否则 upsert 清空父 node_states、
    # finish 把父 run 提前写成 succeeded）。SetWorkflowID 使子 DBOS id 可寻址（嵌套
    # human_task 的 signal 按子 run_id 投递）。
    child_run_id = f"{resource_id}:{parent_run_id}:sub:{node_id}"
    child_meta = {
        **dict(run_meta),
        "run_id": child_run_id,
        "parent_run_id": parent_run_id,
        "subworkflow_depth": depth + 1,
    }
    with SetWorkflowID(child_run_id):
        handle = await DBOS.start_workflow_async(
            run_graph_workflow,
            dict(sub_definition),
            _render_input(node.get("input"), scope),
            child_meta,
        )
    # 有界等待（P1-9，规则 18）：子流程 get_result 无上界 → 子流程卡死父 run 永久
    # PENDING。`timeout_ms`（节点公共字段）即节点级执行上界；未设则无界（与
    # human_task 无超时语义一致，审批可挂多日）。
    timeout_ms = node.get("timeout_ms")
    if timeout_ms is not None:
        try:
            async with asyncio.timeout(float(timeout_ms) / 1000.0):
                return await handle.get_result()
        except TimeoutError as error:
            raise WorkflowEngineError(
                f"subworkflow node {node_id} timed out after {timeout_ms}ms",
                code=WORKFLOW_ENGINE_FAILURE,
            ) from error
    return await handle.get_result()


def _apply_router_pruning(
    nodes: Mapping[str, dict[str, object]],
    router_id: str,
    router_node: dict[str, object],
    result: object,
    skipped: set[str],
) -> None:
    """condition/switch 剪枝：路由候选中被选中分支以外的后继 → skipped。

    只剪路由节点**引用到的候选**（condition 的 then/else、switch 的
    cases/default）：依赖路由但未被路由引用的节点是"延续节点"（如并行汇聚
    下游），无论分支走向都必须执行，不得剪枝（S-10 fanout 回归）。
    """
    kind = str(router_node.get("type", ""))
    if kind not in {"condition", "switch"} or not isinstance(result, dict):
        return
    chosen = set(result["next"])
    referenced = _router_referenced(router_node)
    for node_id, node in nodes.items():
        if (
            router_id in (node.get("depends_on") or [])
            and node_id in referenced
            and node_id not in chosen
        ):
            skipped.add(node_id)


def _router_referenced(router_node: Mapping[str, object]) -> set[str]:
    """路由节点全部候选后继（condition.then/else；switch.cases[*].node_ids/default）。"""
    kind = str(router_node.get("type", ""))
    referenced: set[str] = set()
    if kind == "condition":
        referenced.update(router_node.get("then") or [])  # type: ignore[arg-type]
        referenced.update(router_node.get("else") or [])  # type: ignore[arg-type]
    elif kind == "switch":
        for case in router_node.get("cases") or []:
            if isinstance(case, dict):
                referenced.update(case.get("node_ids") or [])  # type: ignore[arg-type]
        referenced.update(router_node.get("default") or [])  # type: ignore[arg-type]
    return referenced


def _propagate_skips(
    nodes: Mapping[str, dict[str, object]],
    outputs: dict[str, object],
    skipped: set[str],
    states: dict[str, str],
) -> None:
    changed = True
    while changed:
        changed = False
        for node_id, node in nodes.items():
            if node_id in outputs or node_id in skipped:
                continue
            if any(dep in skipped for dep in (node.get("depends_on") or [])):
                skipped.add(node_id)
                states.pop(node_id, None)
                changed = True


def _settle_parallel_joins(
    nodes: Mapping[str, dict[str, object]],
    outputs: dict[str, object],
    states: dict[str, str],
    skipped: set[str],
    started_parallel: set[str],
) -> None:
    for node_id, node in nodes.items():
        if node_id in outputs or node.get("type") != "parallel":
            continue
        if node_id not in started_parallel:
            continue
        branches = [b for b in node.get("branches", []) if isinstance(b, dict)]
        settled = [
            branch
            for branch in branches
            if all(
                member in outputs or member in skipped
                for member in branch.get("node_ids", [])
            )
        ]
        if not settled:
            continue
        if str(node.get("join_policy", "all")) == "any" or len(settled) == len(branches):
            outputs[node_id] = {
                "join_policy": node.get("join_policy", "all"),
                "branches": [
                    {
                        "branch_id": branch.get("branch_id"),
                        "outputs": [
                            outputs.get(str(member)) for member in branch.get("node_ids", [])
                        ],
                    }
                    for branch in (settled if str(node.get("join_policy")) == "any" else branches)
                ],
            }
            states[node_id] = "succeeded"


def _parallel_member_map(nodes: Mapping[str, dict[str, object]]) -> dict[str, str]:
    member_map: dict[str, str] = {}
    for node_id, node in nodes.items():
        if node.get("type") != "parallel":
            continue
        for branch in node.get("branches", []):
            if not isinstance(branch, dict):
                continue
            for member in branch.get("node_ids", []):
                member_map[str(member)] = node_id
    return member_map


def _deps_ready(node: Mapping[str, object], outputs: Mapping[str, object]) -> bool:
    return all(dep in outputs for dep in (node.get("depends_on") or []))


def _parallel_gate_open(
    node_id: str, member_of: Mapping[str, str], started_parallel: set[str]
) -> bool:
    parallel_id = member_of.get(node_id)
    return parallel_id is None or parallel_id in started_parallel


__all__ = [
    "AgentExecutor",
    "AgentNodeRequest",
    "CapabilityExecutor",
    "CapabilityNodeRequest",
    "WorkflowExpressionError",
    "clear_capability_executors",
    "run_graph_workflow",
    "set_agent_executor",
    "set_capability_executor",
]
