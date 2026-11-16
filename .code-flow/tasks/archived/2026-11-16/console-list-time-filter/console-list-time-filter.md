---
{"version":1,"requirement":"console-list-time-filter"}
---

# Tasks: console-list-time-filter

## TASK-001: 沉淀列表时间筛选的统一组件与顺序

- **Status**: done
- **Depends**:
- **Source**:
- **Scenarios**:
- **Spec-Refs**: ui/harness-ui
- **Paths**: .code-flow/specs/ui/harness-ui.md, apps/console-platform/frontend/src/components/common/DateRangeFilter.tsx, apps/console-platform/frontend/src/modules/task-schedule/TaskPage.tsx, apps/console-platform/frontend/src/modules/audit-observability/components/AuditFilterBar.tsx, tests/frontend/test_date_range_filter_contract.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "frontend-list-time-filter",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/frontend/test_date_range_filter_contract.py", "tests/frontend/test_scheduled_v2_console_ui.py", "tests/frontend/test_task_schedule_i18n.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

把已经落地的两条列表约定沉淀进 `ui/harness-ui`：

- 平台所有列表的时间范围筛选只复用 `components/common/DateRangeFilter.tsx`，页面不得再声明 `DatePicker type="dateRange"` 或第二套日期范围控件；
- 筛选顺序固定为「其他筛选 → 时间筛选 → 重置/刷新」。

现有实现与机检已在 `684be1b2`、`ae978728` 提交，本 TASK 只补规范，不改运行代码。

### Checklist

- [x] 在 `ui/harness-ui` 的 Spec Delta 中声明共享时间筛选与顺序规则。
- [x] 归档时同步 `## Conventions` 的 ✅/❌ 示例与机检引用。
- [x] 声明的三组前端契约测试通过。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "ui/harness-ui",
    "rule": "列表时间范围筛选只有一个实现：所有 Console 列表页复用 `components/common/DateRangeFilter.tsx`，页面与模块不得再声明 `DatePicker type=\"dateRange\"`、日期范围输入框或第二套日期范围组件；共享组件的样式、回显与交互调整即全局调整。筛选顺序固定为「其他筛选 → 时间筛选 → 重置/刷新」，时间筛选排在其它筛选之后。机检：`tests/frontend/test_date_range_filter_contract.py`。",
    "evidence": "后台任务与运行审计此前各自维护日期范围控件，样式与回显不一致；本次统一为共享组件并把时间筛选移到其它筛选之后，避免后续优化在多个页面漂移。"
  }
]
```

### Log

- [2026-11-16] created
- [2026-11-16T07:23:09.250Z] started tests=10f32070ae1e

### Verification

- 尚未执行
- 2026-11-16T07:23:50.007Z PASS commands=1/1 executed=1 deduped=0 elapsed=9481ms tests=10f32070ae1e head=ae9787285f3e dirty=0
  - frontend-list-time-filter PASS exit=0
