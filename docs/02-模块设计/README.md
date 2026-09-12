# 后端模块详细设计索引 — V1.13

本目录按照 `cf-task:align` 的模板选择规则重新组织：复杂/跨系统/架构模块使用 `design-full.md`；明确规划且不冻结 DB/API 的模块使用 `design-lite.md`。每个一级目录都是一个可独立评审、编码和验收的模块边界。

> **重要原则**：不为了“文档数量”按单表或单 API 过拆。只有职责、数据 Owner、接口 Owner、验收边界能够独立闭合时才成为一个模块。

## 1. 模块分档矩阵

| 模块 | 分档 | Owner DB 表数 | 接口/函数数 | 分档理由 | 详细设计 |
|---|---:|---:|---:|---|---|
| 18-用户与Agent授权 | Full | 2 | 9 | 用户/授权独立领域、DB、运行时权限校验 | `18-用户与Agent授权/design-full.md` |
| 05-Service与Execution | Full | 10 | 20 | Service Release、Execution Snapshot、状态生命周期 | `05-Service与Execution/design-full.md` |
| 11-Conversation与User-Memory | Full | 5 | 8 | 会话/消息/长期 Memory 持久化与隔离 | `11-Conversation与User-Memory/design-full.md` |
| 13-Workspace与Sandbox | Full | 1 | 3 | 受控工作区与代码/脚本执行安全边界 | `13-Workspace与Sandbox/design-full.md` |
| 01-核心领域与发布模型 | Full | 0 | 8 | 核心领域、发布语义、跨模块 Contract 与共享类型 | `01-核心领域与发布模型/design-full.md` |
| 14-存储与基础设施适配 | Full | 0 | 7 | PG/Redis/ObjectStore/SecretProvider 跨基础设施适配 | `14-存储与基础设施适配/design-full.md` |
| 04-Agent-Core与Agent-Executor | Full | 4 | 14 | Agent Definition/Executor/Tool/Skill/Proposal 核心链路 | `04-Agent-Core与Agent-Executor/design-full.md` |
| 02-Platform-API与控制面 | Full | 0 | 3 | 控制面聚合、跨领域 HTTP/API 边界 | `02-Platform-API与控制面/design-full.md` |
| 08-Skill-Runtime | Full | 5 | 9 | 制品导入、校验、不可变 Artifact、运行边界 | `08-Skill-Runtime/design-full.md` |
| 03-Agent-Runtime | Full | 0 | 6 | 无状态运行时、Chat Run 领取、上下文解析、横向扩展 | `03-Agent-Runtime/design-full.md` |
| 17-Skill-SDK与离线开发 | Full | 0 | 8 | SDK/CLI/Mock/Dev Gateway/生产 Runtime 多边界 | `17-Skill-SDK与离线开发/design-full.md` |
| 10-Channel-Gateway | Full | 6 | 15 | WeCom WebSocket、身份绑定、路由、主动投递 | `10-Channel-Gateway/design-full.md` |
| 06-Worker-Engine | Full | 1 | 6 | lease/retry/recovery/cancel/投递 outbox 可靠执行 | `06-Worker-Engine/design-full.md` |
| 19-模型配置与调用 | Full | 1 | 6 | ModelConfig、Secret、Provider 调用和流式运行 | `19-模型配置与调用/design-full.md` |
| 07-Capability-Runtime | Full | 2 | 11 | Capability Contract、多实现类型、自动分页/大结果 | `07-Capability-Runtime/design-full.md` |
| 16-Knowledge规划 | Lite | 0 | 0 | 明确规划阶段，不冻结生产 DB/API | `16-Knowledge规划/design-lite.md` |
| 15-可观测性与Web公共基础 | Full | 1 | 5 | 拥有 audit_log 与公共 API，属于跨切面架构模块 | `15-可观测性与Web公共基础/design-full.md` |
| 09-Auth与项目平台 | Full | 2 | 12 | ProjectPlatform、用户凭据、Secret/Auth Resolver、内部与开发令牌 | `09-Auth与项目平台/design-full.md` |
| 12-Project-Integration与Registry | Full | 0 | 3 | 跨系统集成、Provider Registry、Manifest/Seed | `12-Project-Integration与Registry/design-full.md` |

## 2. DB Owner 硬约束

每张 Framework 自建表只能有一个模块 Owner。其他模块只能引用，不能重复定义 DDL/约束。

| 模块 | Owner 表 |
|---|---|
| 18-用户与Agent授权 | `platform_user`, `agent_access_grant` |
| 05-Service与Execution | `service_definition`, `service_release`, `execution_snapshot`, `execution_proposal`, `service_execution`, `execution_step`, `async_task_run`, `execution_command`, `task_progress_event`, `artifact` |
| 11-Conversation与User-Memory | `conversation`, `conversation_run`, `message`, `channel_command_receipt`, `user_memory` |
| 13-Workspace与Sandbox | `workspace` |
| 01-核心领域与发布模型 | 无独立表 |
| 14-存储与基础设施适配 | 无独立表 |
| 04-Agent-Core与Agent-Executor | `agent_definition`, `agent_capability_binding`, `agent_skill_binding`, `agent_service_binding` |
| 02-Platform-API与控制面 | 无独立表 |
| 08-Skill-Runtime | `skill_definition`, `skill_artifact`, `skill_artifact_capability`, `skill_import_preview`, `skill_artifact_validation_event` |
| 03-Agent-Runtime | 无独立表 |
| 17-Skill-SDK与离线开发 | 无独立表 |
| 10-Channel-Gateway | `channel_account`, `channel_identity`, `channel_binding`, `bind_code`, `channel_delivery_route`, `channel_delivery` |
| 06-Worker-Engine | `worker_slot_lease`（协调表，非业务事实） |
| 19-模型配置与调用 | `model_config` |
| 07-Capability-Runtime | `capability_definition`, `capability_implementation` |
| 16-Knowledge规划 | 无独立表 |
| 15-可观测性与Web公共基础 | `audit_log` |
| 09-Auth与项目平台 | `project_platform`, `user_project_credential` |
| 12-Project-Integration与Registry | 无独立表 |

> 本表必须与 `01-架构与规范/08-数据库表所有权与字段索引.md` 及模块 §3.3 的物理表集合完全一致（无差集、无幽灵表）；不一致时以模块 Owner 为准并重生成汇总。

## 3. Interface Owner 硬约束

每个 HTTP/Internal/Library/CLI Contract 只在一个模块详细定义；跨模块调用通过 Contract 引用。

**全局鉴权 RULE（ADR-021 / ADR-046 / RULE-API-02 承接）**：
- 所有 `/api/v1/*` 接口必须登录会话（中间件解析身份），唯一豁免为 `POST /api/v1/auth/login`（及 `logout`）；Console 登录的 `role ∈ {ADMIN, BUILDER}` 与 `platform_user.role` 一一对应，`END_USER` 不登录 Console（契约见模块 09 §3.2.3）。
- **写操作**按 ADR-021 页面级权限授予：用户/授权/绑定码/凭据/发布/紧急启停/执行控制/Bot 密钥类接口**仅 Admin**；配置开发类接口 Builder+Admin。**模型与项目平台采用字段级授权（ADR-058）**：新增/编辑对 Builder 开放，但模型 `api_key`、平台 `auth_type`/`auth_schema` **仅 Admin 可写**，非 Admin 携带即 `FIELD_ADMIN_ONLY`(403) 并原子拒绝。
- **读操作**允许 Builder 读取其定义所需的安全只读 DTO（ADR-046）：模型/项目平台列表与详情的 Builder 视图**不含** `base_url`/`default_parameters`/`extra_headers`/`auth_schema`/Secret 引用；平台 `auth_type` **对 Builder 可见**（它是 Provider 机器 key，不含凭据与模板）。
- **执行可见范围（ADR-052）**：Admin = 当前租户全部；Builder = 自己创建的 Service（`service_definition.created_by`）的执行 ∪ 自己被授权 Agent 相关的执行；越界视为不存在（`EXECUTION_NOT_FOUND`）。
- `/internal/*` 一律使用模块 09 `AUTH-LIB-02` 签发的 service-to-service JWT（`audience` + `scope`），身份字段一律来自 token，禁止请求体覆盖。
- 各模块接口表的"认证/授权"列为权威，**且不得为空**（含 Library/Internal 段）。
- 错误码以 `01-架构与规范/10-错误码与错误分类基线.md` 为唯一注册表。

| 模块 | Owner 接口/函数 |
|---|---|
| 18-用户与Agent授权 | `USR-API-01`, `USR-API-02`, `USR-API-03`, `USR-API-04`, `USR-API-05`, `USR-API-06`, `USR-API-07`, `USR-API-08`, `USR-LIB-01` |
| 05-Service与Execution | `SVC-API-01`, `SVC-API-02`, `SVC-API-03`, `SVC-API-04`, `SVC-API-05`, `SVC-API-06`, `SVC-API-07`, `SVC-API-08`, `SVC-API-09`, `SVC-API-10`, `EXE-API-01`, `EXE-API-02`, `EXE-API-03`, `EXE-API-04`, `EXE-API-05`, `EXE-API-06`, `EXE-API-07`, `EXE-LIB-01`, `EXE-LIB-02`, `EXE-LIB-03` |
| 11-Conversation与User-Memory | `MEM-API-01`, `MEM-API-02`, `MEM-API-03`, `CONV-LIB-01`, `CONV-LIB-02`, `CONV-LIB-03`, `MEM-LIB-01`, `MEM-LIB-02` |
| 13-Workspace与Sandbox | `WS-LIB-01`, `WS-LIB-02`, `WS-LIB-03` |
| 01-核心领域与发布模型 | `CORE-LIB-01`, `CORE-LIB-02`, `CORE-LIB-03`, `CORE-LIB-04`, `CORE-LIB-05`, `CORE-LIB-06`, `CORE-LIB-07`, `CORE-LIB-08` |
| 14-存储与基础设施适配 | `INFRA-LIB-01`, `INFRA-LIB-02`, `INFRA-LIB-03`, `INFRA-LIB-04`, `INFRA-LIB-05`, `INFRA-LIB-06`, `INFRA-LIB-07` |
| 04-Agent-Core与Agent-Executor | `AGENT-API-01`, `AGENT-API-02`, `AGENT-API-03`, `AGENT-API-04`, `AGENT-API-05`, `AGENT-API-06`, `AGENT-API-07`, `AGENT-API-08`, `AGENT-LIB-01`, `AGCORE-LIB-01`, `AGCORE-LIB-02`, `AGCORE-LIB-03`, `AGCORE-LIB-04`, `AGCORE-LIB-05` |
| 02-Platform-API与控制面 | `WEB-LIB-01`, `WEB-LIB-02`, `WEB-LIB-03` |
| 08-Skill-Runtime | `SKILL-API-01`, `SKILL-API-02`, `SKILL-API-03`, `SKILL-API-04`, `SKILL-API-05`, `SKILL-API-06`, `SKILL-API-07`, `SKILL-API-08`, `SKILL-LIB-01` |
| 03-Agent-Runtime | `RT-INT-01`, `RT-INT-02`, `RT-INT-03`, `RT-LIB-01`, `RT-LIB-02`, `RT-LIB-03` |
| 17-Skill-SDK与离线开发 | `SDK-API-01`, `SDK-API-02`, `SDK-API-03`, `SDK-CLI-01`, `SDK-CLI-02`, `SDK-CLI-03`, `SDK-LIB-01`, `SDK-LIB-02` |
| 10-Channel-Gateway | `CH-API-01`, `CH-API-02`, `CH-API-03`, `CH-API-04`, `CH-API-05`, `CH-API-06`, `CH-API-07`, `CH-INT-01`, `CH-INT-02`, `CH-DATA-01`, `CH-DATA-02`, `CH-DATA-03`, `CH-DATA-04`, `CH-LIB-01`, `CH-LIB-02` |
| 06-Worker-Engine | `WORK-LIB-01`, `WORK-LIB-02`, `WORK-LIB-03`, `WORK-LIB-04`, `WORK-LIB-05`, `WORK-LIB-06` |
| 19-模型配置与调用 | `MODEL-API-01`, `MODEL-API-02`, `MODEL-API-03`, `MODEL-API-04`, `MODEL-API-05`, `MODEL-LIB-01` |
| 07-Capability-Runtime | `CAP-API-01`, `CAP-API-02`, `CAP-API-03`, `CAP-API-04`, `CAP-API-05`, `CAP-API-06`, `CAP-LIB-01`, `CAP-LIB-02`, `CAP-LIB-03`, `CAP-LIB-04`, `CAP-LIB-05` |
| 16-Knowledge规划 | 无冻结接口 |
| 15-可观测性与Web公共基础 | `OPS-API-01`, `OPS-API-02`, `OPS-API-03`, `OPS-API-04`, `AUDIT-API-01` |
| 09-Auth与项目平台 | `PLAT-API-01`, `PLAT-API-02`, `PLAT-API-03`, `PLAT-API-04`, `PLAT-API-05`, `CRED-API-01`, `CRED-API-02`, `CRED-API-03`, `CRED-API-04`, `AUTH-LIB-01`, `AUTH-LIB-02`, `AUTH-LIB-03` |
| 12-Project-Integration与Registry | `INT-LIB-01`, `INT-LIB-02`, `INT-LIB-03` |

> **登记纪律**：本表与 `01-架构与规范/09-接口所有权与详细设计索引.md` 均由模块 §3.4.1 的接口清单**重生成**。新增接口时必须同步三处（模块 §3.4.1、本表、09-索引），且 `/internal/*` 与 `/api/v1/*` 的"认证/授权"列不得为空。

## 4. 分档判定规则

- **Full**：跨系统集成、架构演进、中大型功能、拥有关键 DB/状态机、可靠执行、安全边界、外部依赖或复杂 API Contract。
- **Lite**：简单、局部、规划性模块，且当前不存在需要完整 DB/接口/DFX 设计的实现边界。
- **Frontend**：Console 页面/组件/交互必须单独使用 `design-frontend.md`，见 `../03-前端设计/`。
- 若 Lite 模块后续新增生产 DB/API/跨系统依赖，应升级为 Full，而不是在 Lite 中不断塞入架构内容。
