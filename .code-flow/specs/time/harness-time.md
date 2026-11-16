---
{"description": "时间与时区口径", "next_rule": 1}
---

# harness-time

## Rules

- [RULE-time-001] 数据库时间统一遵循 RULE-data-001；内部 API 输出带时区的 ISO8601，Console 面向前端的时间由共享格式化器输出 offset，前端统一经 DateTimeText 渲染。调度规则与支持的 recurrence 类型由共享调度契约持有，IANA 时区必须显式提供并验证，不得使用服务器本地时区兜底。调度按本地计划槽计算：不存在的墙钟时刻按策略留调度事件，重复墙钟时刻只采用策略允许的映射；不得伪造 UTC Fire。计划槽计数、聚合错过区间的额外次数与结束条件必须一致；已终止的单次计划只能重新创建，不能恢复原 occurrence。

## Conventions

- **「Console 展示统一」要分两个面读，不是一个面**：内部（Worker/Runtime）API 的 `datetime` 出参走 `.isoformat()`（UTC ISO8601，如 `apps/agent-worker/src/muad_agent_worker/api/schedules.py` 的 `run_at`/`next_fire_at`/`create_time`）；Console **面向前端的**接口是 `%Y-%m-%d %H:%M:%S%z`（如 `2026-01-02 03:04:05+00:00`，见下条）。二者不是同一约束，改一侧不影响另一侧。
  - ✅ 新增 Console 面向接口 → 用 `format_console_time`；新增内部 API → 保留 `.isoformat()`。
  - ❌ 把 Console 的 `%Y-%m-%d %H:%M:%S%z` 套到内部 API 上（内部 API 继续用 ISO8601）。
- **Console 面向接口的时间口径收敛到唯一常量**：`CONSOLE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S%z"` 与 `format_console_time()` 定义在 `apps/console-platform/backend/src/muad_console_platform/application/audit_query_service.py`，并被 `overview_query_service.py` 等复用；输出前显式 `astimezone(timezone)`，**默认 timezone=None 按 UTC 格式化并带 offset**（不跟随服务器本地时区，否则 CI/生产服务器 UTC 与用户浏览器时区会整体偏移）；概览通过平台设置时区传入 tzinfo，确保自然日统计与 generated_at 一致。
  - ✅ `format_console_time(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))` → `2026-01-02 03:04:05+00:00`
  - ❌ 输出不带 offset 的裸时间（前端会按浏览器本地再次解释，产生二次偏移）。
- **前端把带 offset 的 Console 时间/UTC ISO8601 转本地时区渲染，非法值回退 `-`**：唯一入口是 `apps/console-platform/frontend/src/components/common/DateTimeText.tsx`（`DATE_TIME_FORMAT = 'YYYY-MM-DD HH:mm:ss'`，用 `new Date(value)` + `getFullYear/getMonth/...` 逐段拼装 ⇒ 浏览器本地时区；`Number.isNaN(date.getTime())` 时返回 `'-'`）。机检 `tests/frontend/test_datetime_contract.py` 钉死三件事：常量字面量、`padStart(2, '0')` 补零、以及 `pages/` 与 `modules/` 下**不得出现 `toLocaleString(`**。
  - ✅ 展示时间一律走 `<DateTimeText value={...} />`。
  - ❌ 页面/模块里手写 `new Date(x).toLocaleString()` ⇒ 契约测试直接红（且各浏览器输出不一致）。
- **调度契约的固定项（v2）**：`Recurrence.kind` 仅 `CRON/ONCE`；`timezone` 必填且由 `ZoneInfo` 校验；CRON 必带 `cron`、ONCE 必带 `run_at`（`packages/contracts/src/muad_contracts/scheduling.py`）。DB 侧 `task.task_schedule` 的 `ck_task_schedule_trigger` 以 `(recurrence_kind='CRON' AND cron_expr IS NOT NULL AND run_at IS NULL) OR (recurrence_kind='ONCE' AND run_at IS NOT NULL AND cron_expr IS NULL)` 同口径兜底。Schedule 终态是 `ENDED`/`CANCELLED`，`ENDED` 的 `end_reason` 区分 COMPLETED/MISSED/EXHAUSTED/ADMISSION_FAILED；ONCE 错过触发是 `ENDED(MISSED)`，不可恢复，只能新建。DST gap/second-fold 只写 SCHEDULE 审计事件，不伪造 Fire。
  - ❌ 给 `timezone` 加默认值（如 `"UTC"`）⇒ 用户不填时静默按错误时区触发。
  - ❌ 把 `ENDED(MISSED)` 当可复活状态，或把 DST gap/second-fold 写成 UTC Fire。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。

## Applied Deltas

- {"source":"2026-11-15/scheduled-task-v2/scheduled-task-v2.md#TASK-004#DELTA-001","op":"update","rule":"RULE-time-001","payload_sha256":"f2cac806f9f6f2b7d0de281c46c3c985e5c9f9570be6ba294851c6732efa49eb","at":"2026-11-16T05:09:15.750Z"}
