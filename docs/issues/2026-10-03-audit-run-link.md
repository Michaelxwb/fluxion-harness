# 运行审计关联 Run 错误跳转到后台任务列表

- 记录日期：2026-10-03
- 状态：**部分修复**（关联 Task 与两个深链参数已闭环；关联 Run 仍待设计确认）
- 涉及模块：运行审计、后台任务、定时任务
- 来源：用户截图与代码检查

## 修复进展（2026-10-04）

**已闭环**

- `TaskPage` 现在读取 `?taskId=` 并直接打开该任务详情（含列表照常加载；目标已失效时给错误态，
  不静默落回空列表）。这同时修好了**概览「最近后台任务」**的同一处跳转。
- `SchedulePage` 现在读取 `?scheduleId=` 并直接打开该定时任务详情（修好概览「下一批定时触发」）。
- 后台任务详情新增「定时任务 ID」反向链接（**交互稿 `docs/智能服务交付平台-V1.4-交互稿.html`
  里本来就有这一行，此前实现漏做**），点击叠加打开定时任务详情。
- 新增 `/tasks?scheduleId=`，供定时任务详情的历史页签「在任务列表中查看」使用，
  并以可关闭标签显式呈现该筛选。
- 键名配对机检：`tests/frontend/test_task_schedule_deep_link_contract.py`；
  浏览器回归：`e2e/tests/task-schedule/{task-list,task-detail,schedule-history}.spec.ts`。

**为什么此前没被发现**：当时的 e2e 只断言 `toHaveURL(/\/tasks\?taskId=/)` 与「页面非空白」，
**不断言记录真的打开了**——发出方写对了键、接收方没读，两边都过。现在这三条用例都断言
「该记录可见 + 无关记录不可见」。

**仍未闭环**

- 关联 **Run** 没有承载页：普通对话的模型或工具调用可以产生运行审计而不产生后台 Task，
  用任务列表承载任意 Run 是错的（现网表现即用户截图里的空列表）。具体承载页面待设计确认。
  在定案前 `runId` 参数保持现状（跳转到 `/tasks?runId=`，任务页不识别它）。

## 现象与复现步骤

1. 打开 Console「运行审计」，查看模型调用或工具调用的详情。
2. 切换到「关联」页签，点击「关联 Run」中的 Run ID。
3. 页面跳转到「后台任务」列表，未展示关联运行详情；用户截图中的列表显示「暂无数据」。

截图示例：

- Agent：demo01
- 模型调用审计 ID：`44aaaa29-649f-4280-a840-84f1a7872628`
- 关联 Run ID：`6eec951d-d67c-4234-91b4-10015e8405a1`
- 审计时间：2026-10-03 19:52:46（页面显示时间）

## 已确认的问题

- `apps/console-platform/frontend/src/modules/audit-observability/components/AuditDetailSideSheet.tsx` 的 `handleOpenRelated` 将 Run 和 Task 都跳转至 `/tasks`，分别携带 `runId`、`taskId` 查询参数。
- `apps/console-platform/frontend/src/modules/task-schedule/TaskPage.tsx` 未读取上述查询参数，点击关联链接只会打开普通后台任务列表，无法定位关联记录或打开详情。
- `apps/console-platform/frontend/src/modules/task-schedule/services/tasks.ts` 的 `TaskListParams` 没有 Run ID 筛选参数；Console 后端 `api/tasks.py` 的列表接口也没有对应参数。
- Run 与后台 Task 是不同实体。普通对话中的模型或工具调用可以产生运行审计，而未必产生后台任务，因此不能直接以后台任务列表承载任意 Run 的查看入口。

## 待核实项

- 示例 Run 是否实际创建过后台 Task，需查询运行与任务数据确认。
- 截图中的空列表是否符合当前租户的实际任务数据，尚未核实；不能据此判断运行记录丢失或后台任务创建失败。

## 建议修复方向

- 关联 Run 提供能够查看该 Run 的入口，例如运行详情或按 Run ID 定位的审计记录；具体承载页面待设计确认。
- 关联 Task 读取 `taskId` 并打开对应后台任务详情。
- 关联不存在或无权限时，展示明确状态，避免静默跳转到不相关的空列表。

## 修复验收与回归测试要求

- [ ] 点击普通对话的关联 Run，能查看对应运行信息或该 Run 的审计记录，即使没有后台 Task。**（未闭环，待设计）**
- [x] 点击关联 Task，打开对应任务详情，而非普通列表。`e2e/tests/task-schedule/task-list.spec.ts` S-FE-07
- [x] 带关联参数的 URL 可直接访问并刷新，仍能定位同一记录。S-FE-07（含 `page.reload()`）
- [x] 关联不存在、不可读及无关联记录的状态有明确反馈。E-FE-04；来源已删的定时任务见 S-FE-06
- [x] 增加覆盖上述关联跳转行为的测试。上述三条 + 键名配对契约 `tests/frontend/test_task_schedule_deep_link_contract.py`
