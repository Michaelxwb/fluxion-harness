---
id: harness-time
description: Agent Harness 通用平台规则：time
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-time-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q
      tests -k schema_parity
    cwd: .
    timeout: 300
---

# harness-time

## Rules

- [RULE-time-001] 数据库统一 `timestamptz` 存储；Console 展示统一 `YYYY-MM-DD HH:mm:ss`；调度时区使用 IANA 时区（如 `Asia/Shanghai`）。

## Conventions

- **「Console 展示统一」要分两个面读，不是一个面**：内部（Worker/Runtime）API 的 `datetime` 出参走 `.isoformat()`（UTC ISO8601，如 `apps/agent-worker/src/muad_agent_worker/api/schedules.py` 的 `run_at`/`next_fire_at`/`create_time`）；Console **面向前端的**接口才是 `%Y-%m-%d %H:%M:%S`（见下条）。二者不是同一约束，改一侧不影响另一侧。
  - ✅ 新增 Console 面向接口 → 用 `format_console_time`；新增内部 API → 保留 `.isoformat()`。
  - ❌ 把 Console 的 `%Y-%m-%d %H:%M:%S` 套到内部 API 上（会丢掉时区信息，Runtime/Worker 无法可靠比对）。
- **Console 面向接口的时间口径收敛到唯一常量**：`CONSOLE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"` 与 `format_console_time()` 定义在 `apps/console-platform/backend/src/muad_console_platform/application/audit_query_service.py`，并被 `overview_query_service.py` 等复用；输出前显式 `astimezone()`（DB 绝对时刻 → 服务器本地时区）。
  - ❌ 各 service 里自行 `strftime("%Y-%m-%d %H:%M:%S")` ⇒ 口径分叉。
- **前端把 UTC ISO8601 转本地时区渲染，非法值回退 `-`**：唯一入口是 `apps/console-platform/frontend/src/components/common/DateTimeText.tsx`（`DATE_TIME_FORMAT = 'YYYY-MM-DD HH:mm:ss'`，用 `new Date(value)` + `getFullYear/getMonth/...` 逐段拼装 ⇒ 浏览器本地时区；`Number.isNaN(date.getTime())` 时返回 `'-'`）。机检 `tests/frontend/test_datetime_contract.py` 钉死三件事：常量字面量、`padStart(2, '0')` 补零、以及 `pages/` 与 `modules/` 下**不得出现 `toLocaleString(`**。
  - ✅ 展示时间一律走 `<DateTimeText value={...} />`。
  - ❌ 页面/模块里手写 `new Date(x).toLocaleString()` ⇒ 契约测试直接红（且各浏览器输出不一致）。
- **调度模型的固定项**（契约 + DB 同口径）：`type: Literal["CRON","ONCE"]`；`timezone` **必填且无默认值**，值必须是合法 IANA 时区（`ZoneInfo` 校验，非法即 `ValueError`）；`CRON` 必带 `cron`、`ONCE` 必带 `run_at`（`packages/contracts/src/muad_contracts/tasks.py`）；DB 侧 `ck_task_schedule_trigger` 以 `(schedule_type='CRON' AND cron_expr IS NOT NULL) OR (schedule_type='ONCE' AND run_at IS NOT NULL)` 同口径兜底（`migrations/versions/0002_initial_schema.py`）。`ScheduleStatus` 含终态 `MISSED`——`ONCE` 错过触发时间后 `completed_at`/`next_fire_at` 均为 NULL 且**不可恢复**（`packages/contracts/src/muad_contracts/enums.py`）。
  - ❌ 给 `timezone` 加默认值（如 `"UTC"`）⇒ 用户不填时静默按错误时区触发。
  - ❌ 把 `MISSED` 当可复活状态处理（语义是终态，不可恢复）。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
