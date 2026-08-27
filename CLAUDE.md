# Fluxion Harness 项目开发规范

## 项目定位

- 团队：Fluxion
- 项目：`fluxion-harness`
- 后端：Python 3.12+
- 前端：TypeScript + React 19 + Semi Design
- 架构基线：`docs/architecture/fluxion-architecture-baseline-v1.md`

Fluxion 是一个无状态、插件化的 Agent Harness。Runtime 负责执行，Console/Control Plane 负责管理版本化资源；User/Tenant Binding 拥有用户级能力配置；Kubernetes 调度计算资源，而不是运行态配置。

## 不可违反的架构规则

1. **Runtime 必须无状态**：Pod 不拥有持久 Agent/User/Workflow 事实状态。
2. **RuntimeProfile 不等于 Pod**：创建运行态配置 默认只创建版本化 RuntimeProfile，不创建 Kubernetes Workload。
3. **Everything Configurable is a Resource**：Agent、Skill、MCP、Plugin、Workflow、Policy 都是版本化 Resource。
4. **Definition + Binding**：用户/租户相关配置、Credential 和授权放 Binding，不塞进 RuntimeProfile。
5. **Published Resource 不可原地修改**：修改必须产生新 Draft/Version；回滚选择历史不可变版本。
6. **ExecutionSnapshot**：一次 Execution 从开始到结束固定资源版本。
7. **Dev SQLite / Prod PostgreSQL**：两者实现同一 RegistryStore/Repository Contract，并运行同一套 Contract Test。
8. **YAML 不是事实源**：仅允许 import/export；运行事实存 Registry。
9. **Microkernel + Plugin**：Kernel 只依赖 Contract，不依赖具体 Provider/Plugin 实现。
10. **Hook 必须类型化**：定义 priority、timeout、fail policy、scope。
11. **Everything is a Plugin 不等于所有 Plugin 都 in-process**：不可信/业务扩展使用 MCP/RPC/Sandbox/isolated worker。
12. **Tool 是 Agent-facing Adapter，Capability 才承载业务能力**；Workflow Step 和 Agent Tool 复用 Capability Contract。
13. **Workflow durable state 不进入 Agent Runtime**。
14. **Agent + Console 同仓配套发布、运行边界分离**：共享 Schema/Contract，但进程和 Deployment 可独立扩缩容。
15. **Web Chat 是正式 Channel**：未绑定用户仅允许 `/bind <code>`，绑定后映射 PlatformUser。
16. **Tenant scope 全链路强制**：Resource、Cache、Binding、Session、Trace、Authorization 都必须带 tenant。
17. **Secret 不进入 Resource Spec、日志、Trace**：使用 SecretRef/SecretStore；V1 使用 AES-256-GCM，Master Key 外置。
18. **所有外部调用必须定义 timeout 和失败策略**，禁止无限等待和无限重试。
19. **DFX 必须在编码阶段落实**：可用性、可靠性、扩展性、性能、安全、可维护性、可测试性、可观测性、可部署性、兼容性、可恢复性、可运维性。
20. **前端统一 Semi Design**：Console Web 和 Chat Web 默认使用 `@douyinfe/semi-ui@2.102.x` + `@douyinfe/semi-icons@2.102.x`，禁止再引入 Ant Design 等第二套通用组件库。
21. **React 19 适配**：前端入口必须在任何 Semi 组件之前导入 `@douyinfe/semi-ui/react19-adapter`。
22. **Console API 响应统一封装**：所有 JSON API 使用 `{code, message, data, request_id}`；业务 Handler 禁止手写响应结构。
23. **Console 日志统一封装**：使用 RequestContext + structlog JSON；日志、Audit、Trace 必须关联 request_id/trace_id，敏感字段统一脱敏。
24. **日志不等于 Audit**：Publish、Rollback、权限/Binding/Policy/Bind 等高影响操作必须进入独立 AuditLog。
25. 未经 ADR 且不在当前 TASK 明确范围内，禁止修改核心 Contract 或架构规则。

26. **Agent 语义固定**：Agent 是实际部署/运行的 Runtime Service/Pod 集合；Console 不创建 Agent。
27. **RuntimeProfile 是配置对象**：Console 创建/发布 RuntimeProfile，所有 Runtime Pod 从同一 Registry 读取它。
28. **同一运行态一致性**：相同 `tenant_id + user_id + runtime_profile_id` 在不同 Pod 上必须解析出等价 RuntimeProfile/UserRuntimeState/TenantPolicy，并生成一致的 ExecutionSnapshot。


## 开发语言约定

- 文档、TASK、代码注释、提交说明、用户可见文案：**能用中文时优先中文**。
- 类名、函数名、变量名、协议字段、数据库字段、Resource ID、错误码等代码标识符：使用英文，保持跨语言和工具兼容。
- 技术专有名词如 Agent、Runtime、Plugin、Hook、Skill、MCP、Workflow、Capability、Registry 可以保留英文；首次出现时优先配中文解释。
- 不要为了“中文化”翻译标准协议字段或第三方 API 名称。

## 事实源文档

Codex 只读取当前 active code-flow TASK 引用的文档。全局基线：

- `docs/architecture/fluxion-architecture-baseline-v1.md`
- `docs/problems/design-drivers.md`
- `docs/design/fluxion-runtime-design-v1.7.md`
- `docs/design/fluxion-console-design-v1.6.md`
- `.code-flow/specs/architecture/`
- `.code-flow/specs/frontend/semi-design.md`

## 仓库边界

- `backend/src/fluxion/kernel/`：最小 Microkernel。
- `backend/src/fluxion/resources/`：Resource Contract 和 Resolver。
- `backend/src/fluxion/registry/`：Store 抽象和 SQLite/PostgreSQL Adapter。
- `backend/src/fluxion/plugins/`：Plugin 实现/Adapter。
- `backend/src/fluxion/runtime/`：Execution 编排。
- `backend/src/fluxion/protocols/`：MCP/A2A/Workflow 边界协议。
- `backend/src/fluxion/api/`：HTTP 入口，不写领域逻辑。
- `backend/src/fluxion/services/`：Application Service / Use Case。
- `backend/src/fluxion/repositories/`：Repository 实现和查询。
- `backend/src/fluxion/models/`：持久化 Model。
- `frontend/apps/console/`：超管/Control Plane Web。
- `frontend/apps/chat/`：用户 Web Chat Channel。
- `frontend/packages/shared/`：前端共享类型、Semi 业务基础组件、API Client、主题。
- `shared/contracts/`：语言无关 Schema、OpenAPI、Event Contract。

## 依赖方向

允许：

```text
api / cli / sdk
    -> services
    -> domain contracts
    -> repositories / providers
```

禁止：

- `kernel -> concrete plugin/provider`
- `services -> ORM model query`
- `console -> runtime internal implementation`
- `frontend -> database`
- `runtime -> console API as source of truth`

## 质量与 DFX 硬约束

- 所有变更必须有测试。
- Python 公共函数/类必须有类型注解。
- 禁止静默吞异常。
- TypeScript 禁止 `any`、滥用 `@ts-ignore`。
- 单函数原则上不超过 50 行；确有必要必须在评审中说明。
- 单文件原则上不超过 500 行，按职责拆分。
- 禁止硬编码 Secret、非参数化 SQL、循环内无界网络调用。
- SQLite/PostgreSQL 必须共享 Contract Test。
- P0/P1 验收场景自动化率 ≥95%，无法自动化需说明原因。
- 新外部依赖必须明确 timeout、retry、circuit breaker/fail policy。
- 新 Cache 必须明确 key scope、TTL、invalidation、stale 行为。
- 新 Plugin/Hook 必须明确 trust、timeout、fail policy、observability。
- 关键路径必须有 trace_id / execution_id。

## 性能基线

- Runtime 框架额外开销：P95 ≤ 50ms，P99 ≤ 100ms，不含模型和外部 Tool。
- Resource Resolver L1 命中：P95 ≤ 5ms。
- ExecutionSnapshot 构建：P95 ≤ 20ms。
- Hook 调度框架开销：P95 ≤ 10ms，不含 Hook 外部 I/O。
- Console Resource 列表/详情：P95 ≤ 300ms。
- Publish API：P95 ≤ 500ms。
- `/bind` API：P95 ≤ 300ms。
- Web Chat 模型调用前框架首字节开销：P95 ≤ 200ms。

## 前端强制规范

1. Console/Chat 使用 React 19 + TypeScript + Vite + Semi Design。
2. 必须使用 `@douyinfe/semi-ui`、`@douyinfe/semi-icons`。
3. `main.tsx` 第一条 UI 相关导入必须是 `@douyinfe/semi-ui/react19-adapter`。
4. Button/Form/Table/Modal/Toast/Notification/Tabs/Select 等通用组件禁止重复实现。
5. 禁止引入 `antd`、`@ant-design/icons`、MUI 等第二套通用 UI 库。
6. API 调用只能进入 `services/` 或共享 API Client，组件禁止裸 `fetch`。
7. Console 管理 UI 与 Chat 用户 UI 是两个应用，但共享主题和基础组件。
8. 发布、回滚、权限、删除等高风险操作必须有明确确认和影响说明。

## Codex / code-flow 工作方式

Fluxion 不维护第二套任务激活脚本，任务状态、依赖、Spec Context、RED/GREEN、验收证据和完成状态全部交给 code-flow 原生命令。

常用命令：

```text
cf-task:status
cf-task:status fluxion-runtime
cf-task:start fluxion-runtime TASK-001
cf-task:start fluxion-console TASK-101
cf-task:note <file> <TASK-ID>
cf-task:block <file> <TASK-ID>
cf-task:archive <file>
cf-validate
```

原则：

1. 先使用 `cf-task:status` 查看当前状态。
2. 只通过 `cf-task:start` 启动/继续任务；不要手工创建 `.active-task.json`。
3. `cf-task:start` 自己负责 refresh Context、Start Gate、Active Marker、Session Spec、验收测试 RED/GREEN 和完成检查。
4. 当前 TASK 的 Source / Spec-Refs / Acceptance-Refs 是编码范围，禁止自行扩展到其他任务。
5. 如果任务与 Architecture Baseline 冲突，记录 `#NOTES` 并停止，不自行改 Contract。
6. 需要修改核心 Contract 时先建立 ADR，再重新对齐设计与 TASK。
7. 所有说明、任务记录、代码注释和用户可见文本能用中文时优先中文；代码标识符和协议字段保持英文。
