# 工程映射（能力 → 代码落点）

> 本文件是研发导航，不是设计基线。设计以 `docs/01`、`docs/基础文档/`、`docs/基础模块/`、`docs/核心功能/` 为准；代码移动/重构不要求与本文件一一对应。

## 1. 使用规则

- 每个核心功能有**单一主 owner**；其他模块通过契约参与，不复制实现。
- 基础模块是部署单元/共享库；核心功能是业务能力切片，不新增部署单元。
- 跨 app 只上移纯契约、DTO、错误码、共享执行规则；I/O、事务、编排留在 app。
- 代码变更先改设计/Spec，再改实现；本文件只记录落点，不作为授权依据。
- 进行中的执行契约统一以 `agent-execution-contracts` 任务为准，完成后回填本文件。

---

## 2. 基础模块

| 模块 | 主要代码落点 | Owner |
| ---- | ------------ | ----- |
| Console Platform | `apps/console-platform/backend`（api/application/infrastructure/domain）；前端 `apps/console-platform/frontend` | Console |
| Agent Runtime | `apps/agent-runtime/src/muad_agent_runtime`（api/application/infrastructure） | Runtime |
| Agent Worker | `apps/agent-worker/src/muad_agent_worker`（api/application/scheduler/worker/delivery/infrastructure） | Worker |
| IM Gateway | `apps/im-gateway/src/muad_im_gateway`（api/application/channels/infrastructure） | Gateway |
| IM Connector | `apps/im-connector/src/muad_im_connector`（channels/runtime/outbound/lease） | Connector |
| 执行内核 | `packages/agent-core` | 执行内核 |
| Skill SDK/运行时 | `packages/skill-sdk` + Runtime/Worker 的 Skill 执行落点 | 执行内核 |
| 平台接入/Session | `packages/platform-sdk` | 平台接入 |
| 消息内核 | `packages/im-core` | IM |
| 契约/公共 | `packages/contracts`、`packages/api-kit`、`packages/common` | 平台公共 |
| 制品存储 | `packages/artifact-store` | 制品 |
| 日志/指标 | `packages/logging-kit` + 各角色 `metrics.py` | 平台公共 |

---

## 3. 核心功能

### 20 Agent、授权与 Skill

| 能力 | 主落点 | 参与方 |
| ---- | ------ | ------ |
| Agent 定义/绑定/授权 | Console `application/agent_service.py`、`agent_mcp_service.py`、`grant_service.py`、`resolve_service.py` | contracts |
| Skill 导入/版本/审批/范围 | Console `application/skill_service.py`、`skill_draft_service.py`、`infrastructure/skill_artifact_store.py`；Runtime `application/draft_import.py`、`draft_tools.py` | artifact-store、agent-core |
| Skill 执行 | Runtime `application/skill_tools.py`；Worker `worker/agentic_skills.py`、`worker/executor.py` | agent-core、skill-sdk |
| Effective 解析 | Console `application/resolve_service.py`；Runtime/Worker Resolve client | contracts |

**边界**：Console 定义/审批；Runtime/Worker 只执行；执行内核不定义业务规则。

### 21 MCP 与模型能力

| 能力 | 主落点 | 参与方 |
| ---- | ------ | ------ |
| MCP 目录/绑定/范围 | Console `application/mcp_service.py`、`agent_mcp_service.py`、`resolve_service.py` | contracts |
| MCP 调用 | Runtime `application/mcp_runtime_adapter.py`；Worker `application/agentic/mcp.py` | platform-sdk（MCP client/审计/限额） |
| 模型定义/凭据/额度 | Console `application/model_service.py`、`model_credential_service.py`、`model_quota_service.py`、`model_deployment_service.py`、`model_probe_service.py` | contracts |
| 模型选择/预占/调用 | Runtime `application/model_gateway.py`、`model_allocator.py`、`application/capacity_metrics.py` | 外部模型网关 |

**已知收口点**：Runtime/Worker MCP 双实现由执行契约统一需求收口；不得新增第三套。

### 22 对话、Run、任务与调度

| 能力 | 主落点 | 参与方 |
| ---- | ------ | ------ |
| 对话入口/SSE | Gateway `application/inbound.py`、`webchat_core.py`、`webchat_service.py`、`stream_renderer.py`、`sse.py`、`runtime_client.py` | Connector |
| Run 生命周期 | Runtime `application/run_service.py`、`executor.py`、`async_tools/supervisor.py`、`continuation_pump.py`、`event_tail_hub.py` | contracts |
| 取消/恢复 | Runtime `run_service.py`、`async_tools/*`；Gateway `cancel-active` 调用 | Console 解析 |
| Task/Schedule | Worker `application/task_service.py`、`scheduler/service.py`、`worker/executor.py`、`api/tasks.py`、`api/schedules.py` | Runtime task client |
| 批量 | Worker `application/batch_fanout.py`、`batch_fanin.py` | contracts |
| 任务中心 | Gateway `application/task_center.py`；Console `infrastructure/repositories/run_query_repository.py` 等投影 | 数据层 |

### 23 结果与附件投递

| 能力 | 主落点 | 参与方 |
| ---- | ------ | ------ |
| 投递策略/状态 | Worker `delivery/service.py`、`delivery/records.py`、`application/task_message_outbox.py`、`terminal_tasks.py` | contracts |
| 投递 facade/去重 | Gateway `application/channel_delivery.py`、`connector_outbound.py`、`webchat_artifacts.py` | Connector |
| 通道发送/媒体 | Connector `outbound.py`、`channels/*` | 通道 SDK |
| 产物解析 | Runtime `/internal/artifacts/{id}`；Gateway/Worker `artifact_client` | artifact-store |

**边界**：Worker 拥有投递状态/策略；Gateway 只做 facade；Connector 只做通道发送。

### 24 记忆、经验、审计与可观测

| 能力 | 主落点 | 参与方 |
| ---- | ------ | ------ |
| 记忆写入/检索 | Runtime `application/memory_*`（write/context/commands/cache/resume/consolidation） | contracts |
| 经验/学习 | Runtime `application/learning_*`、`experience_candidates.py`；Console `application/learning_service.py` | 数据层 |
| 审计 | Runtime `infrastructure/audit_writer.py`、`application/egress_observability*`；Worker task/delivery 审计；Console `application/audit_*` | logging-kit |
| 可观测 | 各角色 `metrics.py`、`main.py` probes、api-kit metrics | logging-kit |

**边界**：Runtime 是 Memory 唯一 writer；Console 只查询/治理；Worker 不直接写 Memory。

---

## 4. 已知代码归位项（小步，不做大重构）

1. **MCP 运行时双实现**：Runtime/Worker 共享契约与端口，删隐藏/重复路径；以 `agent-execution-contracts` 为准。
2. **投递策略归位**：PUSH 默认/附件/去重策略集中到 Worker 投递策略；Gateway/Connector 只保留机制。
3. **执行契约共享**：Tool/Result/Recovery/Context 契约上移 `packages/contracts` / `agent-core`，两宿主 parity。
4. **Console 只读投影**：overview/audit/run/task 查询统一带 tenant/软删/归属谓词，不写别的数据域。
5. **平台接入**：Adapter/Session/Credential 只在 `platform-sdk`；Runtime/Worker 不直连供应商。

不改：部署单元、对外 API 形状、数据库 schema、执行/连接/管理三面分离。

---

## 5. 变更流程

1. 设计：更新 `docs/` 对应模块/核心功能文档。
2. 规范：需要时通过 Task Spec Delta 更新 `.code-flow/specs/`。
3. 实现：按主 owner 落点修改；跨 app 只走契约。
4. 验证：单元/集成/契约/关键 E2E；全量在 CI/归档后执行。
5. 回填：代码落点变化时更新本文件；设计语义变化先改设计文档。

---

*文档结束*
