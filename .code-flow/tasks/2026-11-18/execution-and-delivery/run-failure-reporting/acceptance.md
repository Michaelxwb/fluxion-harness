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

执行方式：真实构建产物（Vite dev）、真实浏览器（Kimi WebBridge 控制已登录会话）、真实服务
（Console/Runtime/Worker/Gateway :8000-8003）与真实 PostgreSQL。日期：2026-11-19。

| 场景 | 结论 | 证据 |
|---|---|---|
| S-11 不选模式，生成业务卡片 | pass | WebChat 输入“每天 9 点提醒我喝水，只做站内提醒”；卡片显示“提醒”、`CRON 0 9 * * * (Asia/Shanghai)`、不消费模型预算；确认后 Schedule `65e220c9-4a63-490f-8434-eea845fa01f9` ACTIVE/NONE |
| S-12 真实试跑 | pass（API 真实执行） | 对 SKILL_PLAN Draft `afd9710c-99e0-4ebc-98b4-e22fea5e9bbf` 调 `POST /internal/schedule-drafts/{id}/test-run`；Execution `319e612e-0cc4-4214-a499-3d7c08a5eef9` QUEUED→RUNNING→COMPLETED，真实 async-demo 子进程返回 `{"delayed_sec":3.0,...}`；未创建 Schedule/Fire/Revision |
| S-13 从成功对话提升 | pass（自动化） | `tests/agent_runtime/test_schedule_promotion.py`：复用真实 `execute(target=skill)` 参数并绑定 `{{previous_day}}`；浏览器暂无“提升”按钮 |
| S-14 AGENT_TURN 开放性任务 | pass | WebChat 输入“每天 9 点汇总昨天的定时任务执行情况，按失败原因分类”；卡片显示“智能体”、完整 prompt、`tools_allow=*`、只读记忆、平台预算；确认后 Schedule `dc5d1045-6f8e-4480-a131-ecba66c1508f` ACTIVE/AGENT_TURN |
| S-15 歧义问业务问题 | pass（自动化） | `tests/agent_worker/test_schedule_intent_golden.py` + compiler 澄清用例；不创建 Draft，不暴露 mode |
| S-16 SKILL_PLAN argv/input/时间窗口 | pass（卡片+真实试跑；正式触发待 09:00） | 卡片绑定 `SKILL:async-demo`，首次触发 `2026-11-19 09:00` Asia/Shanghai；试跑冻结 input；argv/input 分离与重试冻结由 `test_scheduled_skill_plan_e2e.py` 证明 |
| S-17 静默结果与文件产物 | pass（自动化） | `test_scheduled_agent_turn_e2e.py`：`record_result(no_change)` 抑制文本、附件 outbox 仍投递、上次 meaningful result 不被覆盖 |
| S-18 预算/工具策略 | pass | AGENT_TURN 卡片显示“系统默认（平台设置）”；Snapshot budget 来自 `PlatformSettings.agent`，`tests/agent_worker/test_schedule_budget_from_settings.py` / `test_schedule_tool_policy.py` |
| S-19 TASK 导出与失败诊断 | pass（自动化） | `test_execution_export_task_events.py`、`test_execution_export_business_fields.py`、`test_agentic_no_progress_message.py` |
| S-20 Web 创建定时任务推企微 | pass（卡片+确认+路由） | SKILL_PLAN 卡片“结果位置 WECOM:jahan-e2e”；确认后 Schedule `206e2cec-8f14-4655-88c7-6bf0395b6f67` ACTIVE/SKILL_PLAN/PUSH，`delivery_route_id=0c0cc4f0-c27c-41bd-b3d7-9507ac9282b1`；未依赖创建入口 |
| E-03 Fake MSSP / 本地 Skill | pass（自动化） | `test_scheduled_skill_plan_e2e.py` 用本地回归 Skill + fake MSSP 完成 Draft→试跑→确认→09:00 Fire；覆盖 401/500、空数据、场景矩阵 |
| E-04 内网真实任务成功 | pending | 需要目标内网部署、真实 `daily-duty-report-mssp` 凭据/企微绑定；仓库内无内网访问 |

### 浏览器截图

- `.data/e2e/skill-plan-push-card.png`：SKILL_PLAN 卡片显示 `WECOM:jahan-e2e`；
- `.data/e2e/agent-turn-card-detail.png`：AGENT_TURN 卡片与确认后状态。

### 验收中发现并修复

- 推送目标列表混入 WebChat pull 路由，且同通道多目标标签相同；已改为只返回
  `OUTBOUND_CHANNELS`，标签带外部用户标识、重名时加不透明短后缀。
- 卡片“结果位置”只显示“当前会话”；已增加 `push_target_label`，PUSH 卡片显示真实安全目标。
- 清理了一条重复的手工 WECOM 路由（与首个草稿 upsert 的 route hash 不同造成重复）。

## 3. 已知边界

- E-04 需要部署修复版本到内网、目标租户的真实 `daily-duty-report-mssp` 凭据与企微路由；
  仓库内只能证明 Fake MSSP / 本地回归 Skill 路径。
- 浏览器验收必须记录任务 id、产物 `artifact_id/checksum`、投递结果；未实际执行的场景不得写成通过。
