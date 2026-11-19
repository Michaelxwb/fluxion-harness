---
{"version":1,"requirement":"task-detail-refinement"}
---

# Tasks: task-detail-refinement

<!--
需求来源：2026-11-19 用户对 task-detail-redesign 的两张截图反馈。
设计口径：
- 定时任务最近运行每页 10 条；保留「运行结果」卡片并支持一键复制；移除重复的「执行结果」折叠块，原始 JSON 收进技术信息弹层。
- 后台任务改为左子任务 / 右选中子任务：左侧列表保留字段，右侧内联加载选中子任务的结果与文件；默认展示父任务文件，不再跳转子任务详情页。
-->

## TASK-001: 定时任务详情：每页 10 条、结果去重与一键复制

- **Status**: done
- **Depends**:
- **Source**: 用户截图反馈 2026-11-19（Image 1）
- **Scenarios**: task-detail-refinement:S-01, S-02, S-03
- **Spec-Refs**: frontend/harness-frontend#RULE-front-001, frontend/harness-frontend#RULE-004, i18n/harness-i18n#RULE-i18n-001, ui/harness-ui#RULE-ui-001
- **Paths**: apps/console-platform/frontend/src/modules/webchat/task-center/taskCenterState.ts, apps/console-platform/frontend/src/modules/webchat/task-center/ScheduleDetailPanel.tsx, apps/console-platform/frontend/src/modules/webchat/task-center.css, apps/console-platform/frontend/src/locales/zh-CN.json, apps/console-platform/frontend/src/locales/en-US.json, tests/frontend/test_scheduled_v2_task_center_ui.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "schedule-detail-refinement",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/frontend/test_scheduled_v2_task_center_ui.py", "tests/frontend/test_scheduled_v2_history_artifacts_ui.py", "tests/frontend/test_capacity_webchat_webchat_i18n.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 最近运行列表 `page_size` 从 6 改为 10，提示文案同步。
- 保留绿色「运行结果」卡片；移除重复的「执行结果」折叠块；原始结果 JSON 移入任务名旁的技术信息弹层。
- 「运行结果」增加一键复制，复制完整结果正文（非截断文本），显示复制成功/失败。
- 分页、选中、错误重试行为不变。

### Checklist

- [x] 首屏运行列表每页 10 条，20 条运行显示 1/2 页。
- [x] 「运行结果」仍展示可读结果；页面首屏不再出现第二个结果文本块。
- [x] 原始结果 JSON 只在技术信息弹层中，默认关闭。
- [x] 复制按钮复制完整结果，成功/失败有可见反馈。
- [x] 中英文词条齐全；相关前端测试通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-19] created

### Verification

- 尚未执行
- 2026-11-19T13:13:21.588Z PASS commands=1/1 executed=1 deduped=0 elapsed=21834ms tests=9aeab1ac0fec head=5e88668182ca dirty=1
  - schedule-detail-refinement PASS exit=0
## TASK-002: 后台任务详情：左子任务、右选中子任务结果

- **Status**: done
- **Depends**: TASK-001
- **Source**: 用户截图反馈 2026-11-19（Image 2）
- **Scenarios**: task-detail-refinement:S-04, S-05, S-06, E-07
- **Spec-Refs**: frontend/harness-frontend#RULE-front-001, frontend/harness-frontend#RULE-004, i18n/harness-i18n#RULE-i18n-001, ui/harness-ui#RULE-ui-001
- **Paths**: apps/console-platform/frontend/src/modules/webchat/task-center/backgroundState.ts, apps/console-platform/frontend/src/modules/webchat/task-center/BackgroundTaskDetailPanel.tsx, apps/console-platform/frontend/src/modules/webchat/task-center/ChildTaskTable.tsx, apps/console-platform/frontend/src/modules/webchat/task-center/TaskCenterPage.tsx, apps/console-platform/frontend/src/modules/webchat/task-center/TaskResultSection.tsx, apps/console-platform/frontend/src/modules/webchat/task-center/taskResult.ts, apps/console-platform/frontend/src/modules/webchat/task-center.css, apps/console-platform/frontend/src/locales/zh-CN.json, apps/console-platform/frontend/src/locales/en-US.json, tests/frontend/test_async_skill_background_task_center_ui.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "background-inline-child",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/frontend/test_async_skill_background_task_center_ui.py", "tests/frontend/test_capacity_webchat_webchat_i18n.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 有子任务时改为左右两栏：左侧子任务列表（保留标签、状态、错误摘要、产物数、结束时间），右侧结果面板。
- 右侧默认展示父任务文件；点击子任务行只在右侧内联加载该子任务的结果与文件，URL 不变，不再进入子任务详情页。
- 右侧提供「父任务文件」返回父任务文件；子任务加载中/失败有骨架、错误与重试。
- 父任务文件区域从左侧大面板改为右侧窄面板，文件卡列宽随之收窄。
- 无子任务时仍保持单列文件布局；无子任务且无产物时不渲染空文件卡。
- 子任务分页、失败行高亮、错误摘要保持。

### Checklist

- [x] 有子任务：左侧列表、右侧结果/文件，两栏等高。
- [x] 点击子任务行不改变 URL，右侧显示该子任务结果与文件；再点「父任务文件」恢复父任务文件。
- [x] 子任务加载失败显示错误与重试；加载中显示骨架。
- [x] 父任务文件区域收窄，文件卡不溢出；窄屏单列、无整页横向滚动。
- [x] 无子任务/无产物布局不回归。
- [x] 中英文词条齐全；后台任务与 i18n 测试通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-19] created
- [2026-11-19T13:16:48.657Z] reopen: 补充子任务内联选择控制器测试

### Verification

- 尚未执行
- 2026-11-19T13:13:53.656Z PASS commands=1/1 executed=1 deduped=0 elapsed=18678ms tests=4021cf1a3703 head=5e88668182ca dirty=1
  - background-inline-child PASS exit=0
- 2026-11-19T13:17:07.665Z PASS commands=1/1 executed=1 deduped=0 elapsed=18907ms tests=4021cf1a3703 head=5e88668182ca dirty=1
  - background-inline-child PASS exit=0
