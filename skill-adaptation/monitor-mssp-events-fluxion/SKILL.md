---
name: monitor-mssp-events
description: 监控指定客户在 MSSP 的安全事件，先搜索并确认客户，再按用户指定时间查询；不适用于 MSSW。
platforms: [mssp]
---

# MSSP 事件监控（Fluxion 适配）

先询问用户要查询的客户与时间范围，缺失时继续询问，不自行推断。可询问是否需要企业微信 Webhook；未明确提供则不推送。不要输出或记录 Webhook。

1. 用 `run_skill_script` 调用 `resolve_customer.py`，input 示例：`{"company":"客户简称"}`。展示候选完整名称和数字 company_id，等待用户确认。
2. 确认后调用 `execute_skill`，input 示例：`{"company_ids":["123"],"minutes":60}`。或以 `start` 和 `end` 替换 `minutes`（东八区 `YYYY-MM-DD HH:MM`）。仅使用用户明确确认的数字 ID 与时间。
3. 可选 `notify_no_event`、`no_monitor_report`、`no_c2_hunt` 为布尔值；用户明确要求推送时可提供 `webhook_url`。不要猜测或保存 Webhook。
4. 返回脚本的实际结果；出错时说明失败，不将失败当作零事件。平台登录态由 Fluxion 的 session-manager 和 Redis 管理，业务请求由 Skill 发起，不要求用户提供 Cookie。

监控脚本中的事件内容准备及 C2 交接仍依赖 `mssp-event-push-content` Skill 的本地脚本和原有运行环境。Fluxion 的 Skill 包各自隔离，此部分未经适配与验证；若依赖不可用应明确报告失败，不得宣称已完成研判或推送。
