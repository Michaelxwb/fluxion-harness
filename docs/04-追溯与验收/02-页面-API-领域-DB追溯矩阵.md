# 页面 → API → Domain → DB 追溯矩阵（V1.13）

> 事实源：页面/字段以 `03-前端设计/90-Console交互规格.md` 与各 `design-frontend.md` 为准；API/DTO 与 DB 以模块 Owner 文档为准；索引以 `01-架构与规范/08`、`09` 为准。

| Console 页面 / 操作 | API | Domain Owner | DB / 外部事实源 |
|---|---|---|---|
| 概览（统计卡/最近执行） | OPS-API-04 + EXE-API-01 | 可观测性与Web公共基础 | 各领域表 COUNT 聚合 + `service_execution` |
| 服务列表/新增/详情 | SVC-API-01..03 | Service 与 Execution | `service_definition` |
| 服务 Draft 编辑/校验/测试/发布/紧急启停 | SVC-API-04..07, SVC-API-10 | Service 与 Execution | `service_definition`（draft_payload 步骤含 `human_policy`，ADR-055；draft_payload/draft_revision/enabled）、`service_release`、`execution_snapshot`（source=TEST）、`service_execution`（execution_source=TEST） |
| 服务 Release 历史/详情 | SVC-API-08..09 | Service 与 Execution | `service_release` |
| 执行列表/详情/取消/重试/重新投递 | EXE-API-01..04、EXE-API-07 | Service 与 Execution / Worker（租约经 CORE-LIB-08） | `service_execution`（含 `current_step_name`；Builder 范围依赖 `service_definition.created_by`，ADR-052）, `execution_step`, `async_task_run`, `execution_command`, `task_progress_event`, `artifact` |
| 执行详情 / 人工审批（继续/终止） | EXE-API-05 | Service 与 Execution / Worker | `service_execution`（waiting_reason/context_summary/human_deadline/requested_action）、`execution_command` |
| 执行结果产物下载 | EXE-API-06 | Service 与 Execution / 存储与基础设施适配 | `artifact`（`artifact_id` 为对外身份；`object_ref` 不外泄；清理经 INFRA-LIB-06） |
| 执行投递诊断 | EXE-API-02 | Channel Gateway / Worker | `channel_delivery`, `channel_delivery_route`（预建经 CH-LIB-02，含 `:redeliver:<n>`） |
| 智能体列表/新增/编辑/绑定 | AGENT-API-01..08 | Agent Core | `agent_definition`, `agent_capability_binding`, `agent_skill_binding`, `agent_service_binding` |
| 智能体用户授权 | USR-API-07..08 | 用户与 Agent 授权 | `agent_access_grant` |
| 智能体 IM 接入（Bot 密钥，仅 Admin） | CH-API-01..02 | Channel Gateway | `channel_account` + Secret Provider（经 CH-DATA-01 下发 SecretLease） |
| 能力 | CAP-API-01..06 | Capability Runtime | `capability_definition`（含 side_effect 四值 / risk_level / `invocation_policy` / 派生 `direct_invocation`，ADR-057）, `capability_implementation`（`auth_mode` = 凭据来源唯一声明） |
| Skill 导入（两阶段）/详情/新版本 | SKILL-API-01..08 | Skill Runtime | `skill_definition`, `skill_artifact`, `skill_artifact_capability`, `skill_import_preview` + Object Store |
| 模型 | MODEL-API-01..05 | 模型配置与调用 | `model_config`（`request_timeout_seconds`/`extra_headers`） + Secret Provider |
| 项目平台 | PLAT-API-01..05 | Auth 与项目平台 | `project_platform`（`auth_type` = 已注册 AUTH Provider key，或 `UNCONFIGURED`） |
| 用户 | USR-API-01..04 | 用户与 Agent 授权 | `platform_user` |
| 用户 / 智能体授权 | USR-API-05..06 | 用户与 Agent 授权 | `agent_access_grant`（集合替换幂等，无独立 revision） |
| 用户 / 项目平台认证 | CRED-API-01..04 | Auth 与项目平台 | `user_project_credential`（credential/session 到期分离） + Secret Provider（撤销补偿经 INFRA-LIB-07） |
| 用户 / IM 身份与绑定码 | CH-API-03..07 | Channel Gateway | `channel_identity`, `channel_binding`, `bind_code` |
| 用户 / 记忆（Memory）查看/删除 | MEM-API-01/03 | Conversation 与 User Memory | `user_memory` |
| 审计查询 | AUDIT-API-01 | 可观测性与Web公共基础 | `audit_log` |
| 知识库（规划） | 无 | Knowledge规划 | 无冻结 DB/API |

## 说明

1. **Console 不提供会话管理**（ADR-045）：不存在 `/api/v1/conversations*`；会话由 IM `/new`（`CONV-LIB-02`）与 Runtime（`RT-LIB-03` 领取/恢复）驱动，落 `conversation` / `conversation_run` / `message`。
2. **IM 命令**不走 Console API：`CH-INT-02`（`/internal/v1/commands`）调用各领域 Application，落 `channel_command_receipt`。
3. **Skill 能力依赖**不是 Console 写关系：由导入解析 `skill.yaml capabilities[]` 落 `skill_artifact_capability`（只读快照）。
4. `MEM-API-02` 是 Admin 治理入口，V1 Console 不提供写表单（ADR-037）。
5. 本矩阵必须与 `02-模块设计/README.md` §2/§3 及 `01-架构与规范/08`、`09` 的机械比对结果一致（见 `03-设计验收Gate.md` 的机械比对 Gate）。
