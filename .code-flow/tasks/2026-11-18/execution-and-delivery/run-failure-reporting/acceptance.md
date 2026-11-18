# run-failure-reporting 验收记录

日期：2026-11-19  
需求：`execution-and-delivery`（`run-failure-reporting` + `unified-artifact-delivery` v2.0）  
执行人：AI agent（本地自动化）+ 人工浏览器验收（待执行）

## 1. 自动化证据

| 范围 | 命令 | 结果 |
|---|---|---|
| 失败结果与收尾 | `pytest -q tests/agent_core/test_outcome_parser.py tests/agent_worker/test_execution_outcomes.py tests/agent_runtime/test_tool_audit_business.py tests/agent_core/test_termination_snapshot.py tests/agent_runtime/test_finalization_funnel.py tests/agent_runtime/test_finalization_facts.py tests/agent_runtime/test_finalization_render.py` | 51 passed |
| 导出/诊断 | `pytest -q tests/console_platform/test_execution_export_business_fields.py tests/console_platform/test_execution_diagnosis.py` | 11 passed |
| Job/payload | `pytest -q tests/contracts/test_scheduled_job_payload.py tests/contracts/test_scheduled_v2_contracts.py tests/agent_worker/test_scheduled_v2_fire.py` | 93 passed |
| Authoring | `pytest -q tests/agent_runtime/test_schedule_authoring_compiler.py tests/contracts/test_schedule_plan.py tests/agent_worker/test_schedule_intent_golden.py` | 56 passed |
| 试跑/提升 | `pytest -q tests/agent_worker/test_schedule_test_run.py tests/agent_runtime/test_schedule_promotion.py tests/frontend/test_schedule_preview_contract.py` | 16 passed |
| SKILL_PLAN 运行时 | `pytest -q tests/agent_worker/test_scheduled_skill_plan.py tests/agent_worker/test_skill_argv.py tests/contracts/test_scheduled_v2_contracts.py` | 72 passed |
| AGENT_TURN 运行时 | `pytest -q tests/agent_worker/test_scheduled_agent_turn.py tests/agent_worker/test_agentic_skill_argv.py tests/agent_core/test_prompt_unattended_contract.py` | 22 passed |
| 预算/工具策略 | `pytest -q tests/agent_worker/test_schedule_budget_from_settings.py tests/agent_worker/test_schedule_tool_policy.py tests/console_platform/test_schedule_view_budget.py` | 8 passed |
| Fake MSSP + 本地回归 Skill | `pytest -q tests/tools/test_fake_mssp_platform.py tests/tools/test_fake_mssp_scenarios.py tests/agent_worker/test_local_report_skill.py` | 45 passed |
| 草稿诊断 | `pytest -q tests/console_platform/test_skill_draft_diagnostics.py tests/agent_runtime/test_draft_tools.py` | 30 passed |
| Task 导出 | `pytest -q tests/console_platform/test_execution_export_task_events.py tests/console_platform/test_execution_export_repository.py` | 4 passed |

## 2. 人工浏览器验收

执行方式：真实构建产物、真实浏览器、真实服务；浏览器由 `kimi-webbridge` 控制。

| 场景 | 结论 | 证据 |
|---|---|---|
| S-11 不选模式，生成业务卡片 | pending | |
| S-12 真实试跑 | pending | |
| S-13 从成功对话提升 | pending | |
| S-14 AGENT_TURN 开放性任务 | pending | |
| S-15 歧义问业务问题 | pending | |
| S-16 SKILL_PLAN argv/input/时间窗口 | pending | |
| S-17 静默结果与文件产物 | pending | |
| S-18 预算/工具策略 | pending | |
| S-19 TASK 导出与失败诊断 | pending | |
| E-03 Fake MSSP / 本地 Skill 回归 | pending | |
| E-04 内网真实任务成功 | pending（需目标环境与用户账号） | |

## 3. 已知边界

- E-04 需要部署修复版本到内网、目标租户的真实 `daily-duty-report-mssp` 凭据与企微路由；
  仓库内只能证明 Fake MSSP / 本地回归 Skill 路径。
- 浏览器验收必须记录任务 id、产物 `artifact_id/checksum`、投递结果；未实际执行的场景不得写成通过。
