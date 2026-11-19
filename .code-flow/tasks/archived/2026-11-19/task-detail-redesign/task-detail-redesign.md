---
{"version":1,"requirement":"task-detail-redesign"}
---

# Tasks: task-detail-redesign

<!--
一个需求一份任务文档。设计/场景来源：
- task-detail-redesign.frontend.design.md
- task-detail-redesign.backend.design.md
- task-detail-redesign.scenarios.md
-->

## TASK-001: 扩展 Fire/LastFire 的运行摘要字段

- **Status**: done
- **Depends**:
- **Source**: task-detail-redesign.backend.design.md#3.4, #3.5
- **Scenarios**: task-detail-redesign:S-01, task-detail-redesign:S-02, task-detail-redesign:E-03, task-detail-redesign:B-03, task-detail-redesign:B-06
- **Spec-Refs**: api/harness-api#RULE-api-001, worker/harness-worker#RULE-worker-001, time/harness-time#RULE-time-001, test/harness-test#RULE-test-001
- **Paths**: packages/contracts/src/muad_contracts/scheduling.py, apps/agent-worker/src/muad_agent_worker/application/schedule_read.py, tests/agent_worker/test_scheduled_v2_read_api.py, tests/contracts/test_scheduled_v2_contracts.py, tests/gateway/test_scheduled_v2_task_center.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "run-summary-backend",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_worker/test_scheduled_v2_read_api.py", "tests/contracts/test_scheduled_v2_contracts.py", "tests/gateway/test_scheduled_v2_task_center.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

在现有 Worker 任务中心读投影上增加运行摘要，不改数据库、不新增端点：

- `FireItem` 增加 `duration_ms`、`artifact_count`；
- `LastFireSummary` 增加 `execution_id`、`duration_ms`、`artifact_count`；
- 一页 6 条 Fire 用批量查询取执行时间、最后投递、产物数，禁止 N+1；
- 端到端耗时优先取最后一个 `runtime.message_delivery.delivered_at`，回退 `task_execution.finished_at`；无结束时间为 null；
- 产物数按 `runtime.artifact` 的 `OUTPUT` 与 `logical_file_id` 去重；
- 所有跨表查询带 `tenant_id` 与软删条件。

### Checklist

- [x] `FireItem` / `LastFireSummary` 契约字段与前端类型一致。
- [x] `schedule_read.fire_history()` 批量返回 `duration_ms` / `artifact_count`。
- [x] `schedule_read._last_fires()` 返回 `execution_id` / `duration_ms` / `artifact_count`。
- [x] 耗时回退、运行中为 null、跳过/准入失败为 null 的边界已覆盖。
- [x] 产物数按 `OUTPUT` + `logical_file_id` 去重，多版本不重复。
- [x] 一页 6 条不产生 N+1；查询带 tenant/soft-delete。
- [x] Worker/contracts/gateway 测试通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-19] created
- [2026-11-19T11:25:31.129Z] started tests=5564e75a598e

### Verification

- 尚未执行
- 2026-11-19T11:33:40.010Z PASS commands=1/1 executed=1 deduped=0 elapsed=37106ms tests=5564e75a598e head=5b5ae4343e77 dirty=1
  - run-summary-backend PASS exit=0
## TASK-002: 重构定时任务详情为等高两栏 + 统一分页

- **Status**: done
- **Depends**: TASK-001
- **Source**: task-detail-redesign.frontend.design.md#3.2, #3.3, #3.4, #3.5, #3.6, #3.7
- **Scenarios**: task-detail-redesign:S-01, task-detail-redesign:S-02, task-detail-redesign:S-03, task-detail-redesign:E-01, task-detail-redesign:E-02, task-detail-redesign:E-03, task-detail-redesign:B-01, task-detail-redesign:B-02, task-detail-redesign:B-03, task-detail-redesign:B-06
- **Spec-Refs**: frontend/harness-frontend#RULE-front-001, frontend/harness-frontend#RULE-004, i18n/harness-i18n#RULE-i18n-001, ui/harness-ui#RULE-ui-001, time/harness-time#RULE-time-001
- **Paths**: apps/console-platform/frontend/src/modules/webchat/types.ts, apps/console-platform/frontend/src/modules/webchat/task-center/ScheduleDetailPanel.tsx, apps/console-platform/frontend/src/modules/webchat/task-center/labels.tsx, apps/console-platform/frontend/src/modules/webchat/task-center/taskCenterState.ts, apps/console-platform/frontend/src/modules/webchat/task-center.css, apps/console-platform/frontend/src/locales/zh-CN.json, apps/console-platform/frontend/src/locales/en-US.json, tests/frontend/test_scheduled_v2_task_center_ui.py, tests/frontend/test_capacity_webchat_webchat_i18n.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "schedule-detail-ui",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/frontend/test_scheduled_v2_task_center_ui.py", "tests/frontend/test_capacity_webchat_webchat_i18n.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

把定时任务详情从「摘要 + 触发历史 + 底部执行详情 + 底部技术卡」改为：

- 页头：任务名 + 技术信息图标 + 单一状态 + 频率/下次运行 + 操作；
- 四指标：下次运行、最近一次、结果发送到、运行次数；
- 左右等高两栏：最近运行（每页 6 条，`PaginationFooter`） + 选中运行的详情（结果、文件、发送结果）；
- 技术信息移入任务名旁的弹层，所有用户可见；
- Fire + Execution 合成单一用户状态；
- 选中运行与 Execution 详情联动；跳过/准入失败不请求 Execution；
- 保留现有 controller 的 generation/epoch 守卫，任务中心隐藏时停止刷新。

### Checklist

- [x] 页头/指标/运行列表/详情/技术信息弹层布局完成。
- [x] 运行列表 `page_size=6`，使用 `PaginationFooter`，翻页与选中规则正确。
- [x] 左右两栏桌面端等高；窄屏堆叠；无横向滚动。
- [x] 单一状态映射覆盖成功/进行中/等待中/失败/已跳过/未运行。
- [x] 跳过/准入失败显示原因且不发 Execution 请求。
- [x] 技术信息默认关闭，点击外部/Esc 关闭，所有用户可见。
- [x] 首屏不渲染 Revision/Snapshot/sha256/MIME/UUID/原始 JSON。
- [x] 加载/空/错误态完整，错误重试不清空列表。
- [x] 中英文词条齐全；组件/契约测试通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-19] created
- [2026-11-19T11:34:07.773Z] started tests=e49e53312a48

### Verification

- 尚未执行
- 2026-11-19T11:46:25.717Z PASS commands=1/1 executed=1 deduped=0 elapsed=16443ms tests=e49e53312a48 head=a0a0f9bad773 dirty=1
  - schedule-detail-ui PASS exit=0
## TASK-003: 重构后台任务详情为结论摘要 + 条件子任务布局

- **Status**: done
- **Depends**: TASK-002
- **Source**: task-detail-redesign.frontend.design.md#3.3, #3.4, #3.6, #3.7
- **Scenarios**: task-detail-redesign:S-04, task-detail-redesign:S-05, task-detail-redesign:S-06, task-detail-redesign:E-04, task-detail-redesign:E-05, task-detail-redesign:E-06, task-detail-redesign:B-04, task-detail-redesign:B-05, task-detail-redesign:B-07
- **Spec-Refs**: frontend/harness-frontend#RULE-front-001, frontend/harness-frontend#RULE-004, i18n/harness-i18n#RULE-i18n-001, ui/harness-ui#RULE-ui-001, time/harness-time#RULE-time-001
- **Paths**: apps/console-platform/frontend/src/modules/webchat/task-center/BackgroundTaskDetailPanel.tsx, apps/console-platform/frontend/src/modules/webchat/task-center/TaskResultSection.tsx, apps/console-platform/frontend/src/modules/webchat/task-center/TaskArtifactList.tsx, apps/console-platform/frontend/src/modules/webchat/task-center/ChildTaskTable.tsx, apps/console-platform/frontend/src/modules/webchat/task-center.css, apps/console-platform/frontend/src/locales/zh-CN.json, apps/console-platform/frontend/src/locales/en-US.json, tests/frontend/test_async_skill_background_task_center_ui.py, tests/frontend/test_scheduled_v2_history_artifacts_ui.py, tests/frontend/test_capacity_webchat_webchat_i18n.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "background-detail-ui",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/frontend/test_async_skill_background_task_center_ui.py", "tests/frontend/test_scheduled_v2_history_artifacts_ui.py", "tests/frontend/test_capacity_webchat_webchat_i18n.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

重构后台任务详情：

- 有子任务：环形图 + 进度条 + 失败项摘要 + 文件双列 + 子任务卡；
- 无子任务：隐藏子任务卡、文件全宽、单状态环，不显示 0 项计数；
- 文件卡只保留文件名、类型、大小、下载；版本/校验值/MIME 移入技术信息或文件详情；
- 原始 JSON 移入任务名旁的技术信息弹层；
- 子任务保留下钻与分页；失败原因摘要可见；
- 下载失败、无产物、无子任务、多机器人部分送达均有明确状态。

### Checklist

- [x] 有子任务摘要：环形图、进度条、图例、失败项摘要。
- [x] 无子任务：隐藏子任务卡、文件全宽、单状态环、无 0 项计数。
- [x] 文件网格/列表两种布局；不显示 sha256/MIME/版本/未请求推送。
- [x] 原始结果 JSON 只在技术信息弹层中。
- [x] 子任务仅 `children.total > 0` 渲染，分页/下钻正常。
- [x] 纯文本运行显示「推送到会话」，不伪造机器人数量。
- [x] 下载失败、部分送达、无产物状态明确。
- [x] 技术信息入口与定时任务一致，所有用户可见。
- [x] 中英文词条齐全；现有产物版本/校验值测试已按新位置更新；组件/契约测试通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-19] created
- [2026-11-19T11:46:52.467Z] started tests=7ee9377282fe

### Verification

- 尚未执行
- 2026-11-19T11:55:38.722Z PASS commands=1/1 executed=1 deduped=0 elapsed=24364ms tests=7ee9377282fe head=4c048d5456d5 dirty=1
  - background-detail-ui PASS exit=0
