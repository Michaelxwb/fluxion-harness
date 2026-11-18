# unified-artifact-delivery 验收记录

日期：2026-11-19  
关联需求：`execution-and-delivery`（与 `run-failure-reporting` 同批发布）

## 1. 自动化证据

| 范围 | 命令 | 结果 |
|---|---|---|
| 消息投递状态机 | `pytest -q tests/agent_runtime/test_message_delivery.py tests/gateway/test_delivery_protocol.py` | 13 passed |
| Task outbox/任务中心 | `pytest -q tests/agent_worker/test_task_message_outbox.py tests/console_platform/test_task_center_deliveries.py` | 17 passed |
| Artifact 版本/终端附件 | `pytest -q tests/scheduled_v2_task_artifacts.py` 等（TASK-001/002 声明集） | passed |
| Schedule 目标独立 | `pytest -q tests/agent_worker/test_schedule_delivery_target.py tests/console_platform/test_schedule_delivery_targets.py tests/agent_worker/test_scheduled_v2_wecom_route.py tests/agent_worker/test_scheduled_v2_drafts.py` | passed |
| MEDIA 兼容 | `pytest -q tests/agent_runtime/test_media_compat.py` | 6 passed |
| `execute` target / workspace | `pytest -q tests/agent_runtime/test_skill_tools.py tests/agent_runtime/test_workspace_root_absolute.py` | passed |

## 2. 人工浏览器验收

执行日期：2026-11-19；真实浏览器（Kimi WebBridge）+ 真实 Console/Runtime/Worker/Gateway。

| 场景 | 结论 | 证据 |
|---|---|---|
| S-03 企微创建任务 Web 可读 | pending | 需要企微 Connector 真实入站；本地未启动 Connector |
| S-04 Web 创建任务选企微 push | pass（卡片/确认/路由） | WebChat 创建的 SKILL_PLAN 卡片显示“结果位置 `WECOM:jahan-e2e`”；确认后 Schedule `206e2cec-8f14-4655-88c7-6bf0395b6f67` 为 `PUSH`，冻结 `delivery_route_id=0c0cc4f0-c27c-41bd-b3d7-9507ac9282b1` |
| S-06 无企微绑定只 Web | pass（NONE 路径） | REMINDER 卡片显示“不主动投递，请到定时任务中心查看”；Schedule `65e220c9-4a63-490f-8434-eea845fa01f9` 为 `NONE` |
| S-07 无 Web 入口只企微 push | pending | 需要企微 Connector 入站 |
| S-08 多文件一条消息 | pass（自动化） | `tests/agent_runtime/test_message_delivery.py`、`tests/agent_worker/test_task_message_outbox.py` 多附件形状 |
| S-09 显式再发一次 | pass（自动化） | 新 `message_id` 创建新消息；旧消息不变（message_delivery 测试） |
| E-01 重试/重放不重复 | pass（自动化） | `test_message_delivery.py`、`test_delivery_protocol.py`、`test_task_message_outbox.py` |
| E-04 失败/取消不挂半成品 | pass（自动化） | `test_finalization_funnel.py`、`test_scheduled_v2_task_artifacts.py` |
| E-07 `execute` 漏 target | pass（自动化） | `test_skill_tools.py` 可行动错误 |
| E-08 `WORKSPACE_ROOT` 相对路径 | pass（自动化） | `test_workspace_root_absolute.py` |
| E-09 `MEDIA:` 兼容 | pass（自动化） | `test_media_compat.py`（6 passed） |

### 验收中发现并修复

- 目标列表只返回真实出站通道；同通道多目标使用带外部用户标识的唯一标签。
- 任务中心/卡片 PUSH 目标显示 `push_target_label`，不暴露内部 route id。
- 多附件交付在 Gateway 幂等指纹中包含每一条附件，避免同键换附件被当成重试。

## 3. 边界

- 企微真实发送需要可用机器人绑定与测试会话；本地 Fake MSSP 只证明业务 Skill 与 Web pull，不证明企微网络送达。
- 未实际执行的场景不得写成通过；浏览器验收必须记录任务/Execution id、artifact checksum 与投递状态。
