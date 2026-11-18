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

| 场景 | 结论 | 证据 |
|---|---|---|
| S-03 企微创建任务 Web 可读 | pending | |
| S-04 Web 创建任务选企微 push | pending | |
| S-06 无企微绑定只 Web | pending | |
| S-08 多文件一条消息 | pending | |
| S-09 显式再发一次 | pending | |
| E-01 重试/重放不重复 | pending | |
| E-04 失败/取消不挂半成品 | pending | |
| E-07 `execute` 漏 target | pending | |
| E-08 `WORKSPACE_ROOT` 相对路径 | pending | |
| E-09 `MEDIA:` 兼容 | pending | |

## 3. 边界

- 企微真实发送需要可用机器人绑定与测试会话；本地 Fake MSSP 只证明业务 Skill 与 Web pull，不证明企微网络送达。
- 未实际执行的场景不得写成通过；浏览器验收必须记录任务/Execution id、artifact checksum 与投递状态。
