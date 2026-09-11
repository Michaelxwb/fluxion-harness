# 后端模块详细设计索引 — V1.11 模块分档拆分版

本目录按照 `cf-task:align` 的模板选择规则重新组织：复杂/跨系统/架构模块使用 `design-full.md`；明确规划且不冻结 DB/API 的模块使用 `design-lite.md`。每个一级目录都是一个可独立评审、编码和验收的模块边界。

> **重要原则**：不为了“文档数量”按单表或单 API 过拆。只有职责、数据 Owner、接口 Owner、验收边界能够独立闭合时才成为一个模块。

## 1. 模块分档矩阵

| 模块 | 分档 | Owner DB 表数 | 接口/函数数 | 分档理由 | 详细设计 |
|---|---:|---:|---:|---|---|
| 18-用户与Agent授权 | Full | 2 | 9 | 用户/授权独立领域、DB、运行时权限校验 | `18-用户与Agent授权/design-full.md` |
| 05-Service与Execution | Full | 9 | 14 | Service Release、Execution Snapshot、状态生命周期 | `05-Service与Execution/design-full.md` |
| 11-Conversation与User-Memory | Full | 3 | 9 | 会话/消息/长期 Memory 持久化与隔离 | `11-Conversation与User-Memory/design-full.md` |
| 13-Workspace与Sandbox | Full | 1 | 3 | 受控工作区与代码/脚本执行安全边界 | `13-Workspace与Sandbox/design-full.md` |
| 01-核心领域与发布模型 | Full | 0 | 3 | 核心领域、发布语义、跨模块 Contract | `01-核心领域与发布模型/design-full.md` |
| 14-存储与基础设施适配 | Full | 0 | 5 | PG/Redis/ObjectStore/SecretProvider 跨基础设施适配 | `14-存储与基础设施适配/design-full.md` |
| 04-Agent-Core与Agent-Executor | Full | 4 | 13 | Agent Definition/Executor/Tool/Skill/Proposal 核心链路 | `04-Agent-Core与Agent-Executor/design-full.md` |
| 02-Platform-API与控制面 | Full | 0 | 3 | 控制面聚合、跨领域 HTTP/API 边界 | `02-Platform-API与控制面/design-full.md` |
| 08-Skill-Runtime | Full | 3 | 9 | 制品导入、校验、不可变 Artifact、运行边界 | `08-Skill-Runtime/design-full.md` |
| 03-Agent-Runtime | Full | 0 | 3 | 无状态运行时、上下文解析、横向扩展 | `03-Agent-Runtime/design-full.md` |
| 17-Skill-SDK与离线开发 | Full | 0 | 8 | SDK/CLI/Mock/Dev Gateway/生产 Runtime 多边界 | `17-Skill-SDK与离线开发/design-full.md` |
| 10-Channel-Gateway | Full | 5 | 9 | WeCom WebSocket、身份绑定、路由、主动投递 | `10-Channel-Gateway/design-full.md` |
| 06-Worker-Engine | Full | 0 | 5 | lease/retry/recovery/cancel 可靠执行 | `06-Worker-Engine/design-full.md` |
| 19-模型配置与调用 | Full | 1 | 6 | ModelConfig、Secret、Provider 调用和流式运行 | `19-模型配置与调用/design-full.md` |
| 07-Capability-Runtime | Full | 2 | 8 | Capability Contract、多实现类型、自动分页/大结果 | `07-Capability-Runtime/design-full.md` |
| 16-Knowledge规划 | Lite | 0 | 0 | 明确规划阶段，不冻结生产 DB/API | `16-Knowledge规划/design-lite.md` |
| 15-可观测性与Web公共基础 | Full | 1 | 4 | 拥有 audit_log 与公共 API，属于跨切面架构模块 | `15-可观测性与Web公共基础/design-full.md` |
| 09-Auth与项目平台 | Full | 2 | 10 | ProjectPlatform、用户凭据、Secret/Auth Resolver | `09-Auth与项目平台/design-full.md` |
| 12-Project-Integration与Registry | Full | 0 | 3 | 跨系统集成、Provider Registry、Manifest/Seed | `12-Project-Integration与Registry/design-full.md` |

## 2. DB Owner 硬约束

每张 Framework 自建表只能有一个模块 Owner。其他模块只能引用，不能重复定义 DDL/约束。

| 模块 | Owner 表 |
|---|---|
| 18-用户与Agent授权 | `platform_user`, `agent_access_grant` |
| 05-Service与Execution | `service_definition`, `service_release`, `execution_snapshot`, `service_execution`, `execution_step`, `async_task_run`, `execution_command`, `task_progress_event`, `artifact` |
| 11-Conversation与User-Memory | `conversation`, `message`, `user_memory` |
| 13-Workspace与Sandbox | `workspace` |
| 01-核心领域与发布模型 | 无独立表 |
| 14-存储与基础设施适配 | 无独立表 |
| 04-Agent-Core与Agent-Executor | `agent_definition`, `agent_capability_binding`, `agent_skill_binding`, `agent_service_binding` |
| 02-Platform-API与控制面 | 无独立表 |
| 08-Skill-Runtime | `skill_definition`, `skill_artifact`, `skill_artifact_capability` |
| 03-Agent-Runtime | 无独立表 |
| 17-Skill-SDK与离线开发 | 无独立表 |
| 10-Channel-Gateway | `channel_account`, `channel_identity`, `channel_binding`, `bind_code`, `channel_delivery_route` |
| 06-Worker-Engine | 无独立表 |
| 19-模型配置与调用 | `model_config` |
| 07-Capability-Runtime | `capability_definition`, `capability_implementation` |
| 16-Knowledge规划 | 无独立表 |
| 15-可观测性与Web公共基础 | `audit_log` |
| 09-Auth与项目平台 | `project_platform`, `user_project_credential` |
| 12-Project-Integration与Registry | 无独立表 |

## 3. Interface Owner 硬约束

每个 HTTP/Internal/Library/CLI Contract 只在一个模块详细定义；跨模块调用通过 Contract 引用。

| 模块 | Owner 接口/函数 |
|---|---|
| 18-用户与Agent授权 | `USR-API-01`, `USR-API-02`, `USR-API-03`, `USR-API-04`, `USR-API-05`, `USR-API-06`, `USR-API-07`, `USR-API-08`, `USR-LIB-01` |
| 05-Service与Execution | `SVC-API-01`, `SVC-API-02`, `SVC-API-03`, `SVC-API-04`, `SVC-API-05`, `SVC-API-06`, `SVC-API-07`, `SVC-API-08`, `SVC-API-09`, `EXE-API-01`, `EXE-API-02`, `EXE-API-03`, `EXE-API-04`, `EXE-LIB-01` |
| 11-Conversation与User-Memory | `CONV-API-01`, `CONV-API-02`, `CONV-API-03`, `CONV-API-04`, `MEM-API-01`, `MEM-API-02`, `MEM-API-03`, `CONV-LIB-01`, `MEM-LIB-01` |
| 13-Workspace与Sandbox | `WS-LIB-01`, `WS-LIB-02`, `WS-LIB-03` |
| 01-核心领域与发布模型 | `CORE-LIB-01`, `CORE-LIB-02`, `CORE-LIB-03` |
| 14-存储与基础设施适配 | `INFRA-LIB-01`, `INFRA-LIB-02`, `INFRA-LIB-03`, `INFRA-LIB-04`, `INFRA-LIB-05` |
| 04-Agent-Core与Agent-Executor | `AGENT-API-01`, `AGENT-API-02`, `AGENT-API-03`, `AGENT-API-04`, `AGENT-API-05`, `AGENT-API-06`, `AGENT-API-07`, `AGENT-API-08`, `AGENT-LIB-01`, `AGCORE-LIB-01`, `AGCORE-LIB-02`, `AGCORE-LIB-03`, `AGCORE-LIB-04` |
| 02-Platform-API与控制面 | `WEB-LIB-01`, `WEB-LIB-02`, `WEB-LIB-03` |
| 08-Skill-Runtime | `SKILL-API-01`, `SKILL-API-02`, `SKILL-API-03`, `SKILL-API-04`, `SKILL-API-05`, `SKILL-API-06`, `SKILL-API-07`, `SKILL-API-08`, `SKILL-LIB-01` |
| 03-Agent-Runtime | `RT-INT-01`, `RT-LIB-01`, `RT-LIB-02` |
| 17-Skill-SDK与离线开发 | `SDK-API-01`, `SDK-API-02`, `SDK-API-03`, `SDK-CLI-01`, `SDK-CLI-02`, `SDK-CLI-03`, `SDK-LIB-01`, `SDK-LIB-02` |
| 10-Channel-Gateway | `CH-API-01`, `CH-API-02`, `CH-API-03`, `CH-API-04`, `CH-API-05`, `CH-API-06`, `CH-API-07`, `CH-INT-01`, `CH-LIB-01` |
| 06-Worker-Engine | `WORK-LIB-01`, `WORK-LIB-02`, `WORK-LIB-03`, `WORK-LIB-04`, `WORK-LIB-05` |
| 19-模型配置与调用 | `MODEL-API-01`, `MODEL-API-02`, `MODEL-API-03`, `MODEL-API-04`, `MODEL-API-05`, `MODEL-LIB-01` |
| 07-Capability-Runtime | `CAP-API-01`, `CAP-API-02`, `CAP-API-03`, `CAP-API-04`, `CAP-API-05`, `CAP-API-06`, `CAP-LIB-01`, `CAP-LIB-02` |
| 16-Knowledge规划 | 无冻结接口 |
| 15-可观测性与Web公共基础 | `OPS-API-01`, `OPS-API-02`, `OPS-API-03`, `AUDIT-API-01` |
| 09-Auth与项目平台 | `PLAT-API-01`, `PLAT-API-02`, `PLAT-API-03`, `PLAT-API-04`, `PLAT-API-05`, `CRED-API-01`, `CRED-API-02`, `CRED-API-03`, `CRED-API-04`, `AUTH-LIB-01` |
| 12-Project-Integration与Registry | `INT-LIB-01`, `INT-LIB-02`, `INT-LIB-03` |

## 4. 分档判定规则

- **Full**：跨系统集成、架构演进、中大型功能、拥有关键 DB/状态机、可靠执行、安全边界、外部依赖或复杂 API Contract。
- **Lite**：简单、局部、规划性模块，且当前不存在需要完整 DB/接口/DFX 设计的实现边界。
- **Frontend**：Console 页面/组件/交互必须单独使用 `design-frontend.md`，见 `../03-前端设计/`。
- 若 Lite 模块后续新增生产 DB/API/跨系统依赖，应升级为 Full，而不是在 Lite 中不断塞入架构内容。
