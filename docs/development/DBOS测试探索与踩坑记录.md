# DBOS 测试探索与踩坑记录

> 适用：Phase 3 Workflow Platform（`backend/tests/workflow_runtime/`、`backend/tests/integration/test_workflow_*`）。
> 本文件记录真实 DBOS 2.31 + 本地 PG（`fluxion_workflow` 库）测试中实测踩过的坑与验证过的模式，
> 避免下次重踩。每条均有真实运行证据（非理论推断）。

## 1. DBOS 运行模型（测试前必读）

### 1.1 PENDING 不是"挂起检查点"
`dbos.workflow_status.status='PENDING'` 是 DBOS 初始/在飞状态：workflow_status 建立即 PENDING，
全执行期保持，**不能**用于判断 workflow 是否 durable 阻塞。

判断 durable 挂起（kill 前必须等待的信号）：
```sql
SELECT 1 FROM dbos.operation_outputs
WHERE workflow_uuid = %s AND function_name = 'DBOS.sleep' LIMIT 1
```
`DBOS.sleep` 操作行落库 ⇒ 前序 step 已 durable 提交、workflow 阻塞在 recv/sleep。
测试辅助 `_wait_durable_wait_checkpoint`（S-03/S-07/S-08 实证）。

### 1.2 DBOS workflow 函数在独立 event loop 运行
DBOS workflow 函数（`run_graph_workflow`）跑在 DBOS 内部 event loop，**不能**在其中调用 async
SQLAlchemy engine：
```
RuntimeError: Task ... got Future ... attached to a different loop
```
解释器内一切 DB 访问必须走 **sync psycopg**（连接按次建立、自动提交）：
- 投影 writer（`runtime/workflow_projection.py`）
- active ref releaser（`release_workflow_active_references`）
- subworkflow 子定义解析（`set_sync_definition_resolver`）

引擎 `start`（acquire refs、解析定义）在主 loop 上，async store 可用——只有"DBOS workflow 函数
内部"受限。

### 1.3 startup recovery 会恢复残留 PENDING run
任何新 worker `DBOS.launch()`（含 startup recovery）会把 sysdb 里**残留 PENDING** 的 workflow
重新执行（memo-skip 已完 step）。共享 PG 下，上个测试留下的 PENDING run 会被下个测试的 worker
恢复 → 重写投影/active refs → 精确计数断言误报（B-02 实测多出行）。

修复（测试隔离）：
1. setup 清残留：`DELETE FROM dbos.workflow_status WHERE status IN ('PENDING','ENQUEUED')`
   （`purge_stale_workflows`）；
2. cleanup 把 run signal 到终态再停 worker（不留 PENDING）。
> 注意：该 DELETE 无 tenant 过滤，仅适合顺序测试；并发测试须更精细（按 app 名/时间窗过滤）。

### 1.4 launched 的 DBOS 无条件消费 `_dbos_internal_queue`
`DBOS.launch()` 后的进程会消费 DBOS 内部 recovery queue。驱动/API 测试进程若 launch，会与
recover worker **抢 recovery/dequeue**，破坏 kill/recover 语义（实测 S-09 计时被破坏）。

→ 只读/信号路径用 `WorkflowTestClient`（纯 `DBOSClient`，不 launch），保证 recover worker 是
唯一可恢复方。API/Console 读 execution history 同理（`DbosWorkflowEngine.get_execution_history`
用 DBOSClient 免 launch）。

### 1.5 DBOS 对不可达 backend 内部无限重试
DBOS 对不可达 backend 的 launch/客户端调用会内部无限重试（实测 sys_db 重连循环）。所有成员
必须 `asyncio.wait_for` 有界封装（`_call`），超时映射 `WorkflowBackendUnavailableError`（非 hang，
E-01）。

### 1.6 客户端 API 绑定 event loop（RISK-P3-04）
DBOS 阻塞式客户端方法（`get_workflow_status`/`list_workflow_steps`/`send` 等）绑定首个 event loop，
必须 `asyncio.to_thread` + `asyncio.wait_for`。

### 1.7 queue 装配顺序
- `DBOS.listen_queues([...])` 必须在 `DBOS.launch()` 之前调用；
- `register_workflow_queue`（database_backed_queue）要求非 async 上下文（`check_async` 硬 raise）
  → 放后台线程；
- `worker_concurrency` 有界（默认 4）防单 worker 全认领（S-06 双 worker 分摊）。

### 1.8 `recv_async` 的 `timeout_seconds` 必填
DBOS `recv_async` 要求 timeout 必填（None 会 TypeError）。human_task 未设超时用模型上界兜底
（30 天 ≈ 无限等待）。**recv 返回 None = 超时/被取消 → 必须报错，不能静默当成功**（P1-8，审批
可能被空 payload 放行）。

### 1.9 `sleep_async` 按原始 deadline 触发
wait 节点 kill + 重启后按**原始 deadline** 触发（不重算 sleep），S-09 实测 elapsed 落窗口内。

### 1.10 start 幂等
`SetWorkflowID(run_id)` 下同 run 二次 start 返回既有 run（S-05）。`existing is None` 判 is_new；
终态 run 不重 acquire refs（避免孤儿行）。

### 1.11 状态词表：DBOS 大写 vs 投影小写
DBOS 状态大写（`SUCCESS`/`ERROR`/`PENDING`/`CANCELLED`）；投影 `workflow_run.status` 是小写词表
（`running/succeeded/failed/cancelled/paused`）。写投影必须走 `PROJECTION_STATUS_BY_DBOS` 映射，
**禁止 `.lower()`**（`max_recovery_attempts_exceeded` 31 字符 > VARCHAR(16) + 词表外，P1-11）。

### 1.12 StepInfo 是 TypedDict，字段名易错
`DBOS.list_workflow_steps` 返回 `StepInfo`（TypedDict）：字段是 `function_name`/`output`/`error`，
**没有** `func_name`/`status`（TASK-008 曾用错键 → 全是 step-N + 空 status）。
且 steps 的 `function_name` 是 DBOS 函数名（`_run_node`/`DBOS.sleep`），**不含 workflow 节点 ID**
——节点 ID 级状态在投影 `node_states`，不是 execution history。

## 2. 测试隔离与共享 PG

### 2.1 registry store 用 asyncpg，DBOS sysdb 用 psycopg v3
- `PostgreSQLRegistryStore("postgresql://...")` 默认 psycopg2 dialect（**非本项目依赖**）
  → 必须 `postgresql+asyncpg://`；
- DBOS sysdb（`--database-url`）走 psycopg v3 plain `postgresql://`；
- 同库跨驱动连接（asyncpg store ↔ psycopg worker/writer），S-07/S-11 实证。

### 2.2 `reset_on_initialize=True` 重建 fluxion 表
`store.initialize()`（reset）做 `metadata.drop_all + create_all`：重建 fluxion 表（含 workflow_run），
**不动 `dbos.*` schema**。每个测试开头 `_fresh_registry_store` 保证干净投影表。

### 2.3 worker 子进程 distinct `DBOS__VMID`
多 worker 子进程必须 distinct `DBOS__VMID`（`s03-worker`/`s11-ok`/`worker-0`…），否则 DBOS
进程身份混淆。

### 2.4 `purge_stale_enqueued` 只清 ENQUEUED
`purge_stale_enqueued` 按 `queue_name` + `status='ENQUEUED'` 过滤；**PENDING 残留不在此列**，
需 `purge_stale_workflows`（见 1.3）。

## 3. 投影 / execution history

### 3.1 终态接线下沉解释器（serve 覆盖）
`run_graph_workflow` 的 except/else 统一写 failed/succeeded 投影 + 释放 active refs → serve
（生产 Deployment）/start/recover 三模式一致，不依赖 CLI 接线（P0-2）。serve 测试用
`install_registry_worker_bootstrap` + `WorkerProcess(["serve", ...])` + `enqueue_start=True` 驱动。

### 3.2 解释器写投影必须 sync psycopg（见 1.2）
### 3.3 读路径 DBOSClient 免 launch（见 1.4）

## 4. subworkflow

### 4.1 复用父 run_meta 会打穿父投影
`_execute_subworkflow` 把 `dict(run_meta)`（同 run_id）传给子 `run_graph_workflow` → 子流程
`upsert` 清空父 node_states、`finish` 把父提前写成 succeeded（P0-1）。必须派生独立 run_id
（`{child}:{parent}:sub:{node}`）+ `parent_run_id` 血缘 + `SetWorkflowID`（子 DBOS id 可寻址，
嵌套 human_task 的 signal 按子 run_id 投递）。

### 4.2 子定义解析不能调 async provider（见 1.2）
子流程在 DBOS loop 内解析子定义 → sync resolver（psycopg，语义同 `recall_pinned` 拒绝 DRAFT）。

### 4.3 有界 get_result + 嵌套深度上限
`handle.get_result()` 无上界 → 子流程卡死父 run 永久 PENDING（规则 18）。按节点 `timeout_ms`
有界；嵌套深度上限 `_MAX_SUBWORKFLOW_DEPTH=5`（防 A 含 A 无限嵌套）。

## 5. 验证过的模式（复用）

| 模式 | 位置 | 用途 |
|------|------|------|
| `_wait_durable_wait_checkpoint` | test_workflow_gate_s03_s07 / s11 | 用 `DBOS.sleep` 操作行判断 durable 挂起（替代 PENDING 误判） |
| `WorkflowTestClient`（纯 DBOSClient） | worker_fixtures | 驱动进程 signal/status，不 launch 不抢 recovery |
| `WorkerProcess`（`--bootstrap` 装配） | worker_fixtures | 真实 worker 子进程 + SIGKILL 崩溃恢复 |
| `install_registry_worker_bootstrap` | worker_fixtures | registry-backed provider + sync resolver + ref store/releaser + 投影 writer 一站装配 |
| `purge_stale_workflows` + 清理 signal | S-11 / review_fixes | 共享 PG 测试隔离（1.3） |
| 投影 writer 错误隔离 | workflow_graph | 投影写失败只记日志，不影响业务执行结果 |
| review 回归 | test_workflow_review_fixes | P0-1 subworkflow 独立投影 / P0-2 failed 投影+释放 / serve 终态 |

## 6. 相关文档 / 链接

- `.code-flow/tasks/2026-08-28/phase3-workflow-platform/phase3-workflow-platform.design.md`（DBOS 生产化设计；ADR-013 选型 DBOS，PoC 证据 `evidence/dbos.json` 11/11）
- `backend/tests/workflow_poc/`（选型 PoC 证据，DBOS 2.31）
- `docs/adr/ADR-A005-Durable-Workflow边界.md`（Durable Workflow 边界）
- Restate 单节点局限（worker 崩溃不恢复、2 deployment 不分摊）→ DBOS 对照占优（memory: `restate-single-node-limits`）
