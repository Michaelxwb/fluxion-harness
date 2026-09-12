# 页面 → API → Domain → DB 追溯矩阵（V1.11）

| Console 页面 / 操作 | API | Domain Owner | DB / 外部事实源 |
|---|---|---|---|
| 概览（统计卡/最近执行） | OPS-API-04 + EXE-API-01 | 可观测性与Web公共基础 | 各领域表 COUNT 聚合 + `service_execution` |
| 服务列表/新增/详情/草稿/发布 | SVC-API-01..09 | Service 与 Execution | `service_definition`, `service_release` |
| 执行列表/详情/终止/重试 | EXE-API-01..04 | Service 与 Execution / Worker | `service_execution`, `execution_step`, `async_task_run`, `execution_command`, `task_progress_event`, `artifact` |
| 执行详情/人工审批（继续/终止） | EXE-API-05 | Service 与 Execution / Worker | `service_execution`（waiting_reason/human_deadline/requested_action） |
| 执行投递诊断 | EXE-API-02 | Channel Gateway / Worker | `channel_delivery`, `channel_delivery_route` |
| 智能体列表/新增/编辑/绑定 | AGENT-API-01..08 | Agent Core | `agent_definition`, `agent_*_binding` |
| 智能体用户授权 | USR-API-07..08 | 用户与 Agent 授权 | `agent_access_grant` |
| 智能体 IM 接入 | CH-API-01..02 | Channel Gateway | `channel_account` + Secret Provider |
| 能力 | CAP-API-01..06 | Capability Runtime | `capability_definition`, `capability_implementation` |
| Skill 导入/详情/新版本 | SKILL-API-01..08 | Skill Runtime | `skill_definition`, `skill_artifact`, `skill_artifact_capability` + Object Store |
| 模型 | MODEL-API-01..05 | 模型配置与调用 | `model_config` + Secret Provider |
| 项目平台 | PLAT-API-01..05 | Auth 与项目平台 | `project_platform` |
| 用户 | USR-API-01..04 | 用户与 Agent 授权 | `platform_user` |
| 用户 / 智能体授权 | USR-API-05..06 | 用户与 Agent 授权 | `agent_access_grant` |
| 用户 / 项目平台认证 | CRED-API-01..04 | Auth 与项目平台 | `user_project_credential` + Secret Provider |
| 用户 / IM 身份与绑定码 | CH-API-03..07 | Channel Gateway | `channel_identity`, `channel_binding`, `bind_code` |
| 用户 / 记忆（Memory）查看/删除 | MEM-API-01/03 | Conversation 与 User Memory | `user_memory` |
| 审计查询 | AUDIT-API-01 | 可观测性与Web公共基础 | `audit_log` |
| 知识库（规划） | 无 | Knowledge规划 | 无冻结 DB/API |

> Skill 的“能力依赖”不是 Console API 写关系；由 Skill 导入解析 `skill.yaml capabilities[]`，落 `skill_artifact_capability` 只读快照。
