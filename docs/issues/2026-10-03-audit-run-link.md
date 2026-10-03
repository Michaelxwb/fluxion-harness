# 运行审计关联 Run 错误跳转到后台任务列表

- 记录日期：2026-10-03
- 状态：**已修复**（关联 Task 与关联 Run 两条都闭环；「独立的 Run 列表页」是另一个产品问题，未做，见文末）
- 涉及模块：运行审计、后台任务、定时任务、Run 详情
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
- **关联 Run 改为就地叠加打开 Run 详情**（不再跳 `/tasks?runId=`——那只会打开一个无关的空列表）。
  规则：关联目标**本身就是列表页实体**（Task/Schedule）才跳列表；目标是**别的域、Console 没有它
  的列表页**时（Run）就地叠加。口径写进详设 §11.6，Run 视图本身见 §11.7。
- 后台任务详情新增「来源运行」（`task_execution.source_run_id`，**字段此前一路传到前端 DTO 却从没
  渲染过**）→ 打开同一个 Run 详情。
- 键名配对机检：`tests/frontend/test_task_schedule_deep_link_contract.py` 与
  `tests/frontend/test_run_observability_contract.py`；后端 `tests/console_platform/test_runs_api.py`；
  浏览器回归见 `e2e/tests/`。

**为什么此前没被发现**：当年的 e2e 只断言 `toHaveURL(/\/tasks\?taskId=/)` 与「页面非空白」，
**不断言记录真的打开了**——发出方写对了键、接收方没读，两边都过。现在这些用例都断言
「该记录可见 + 无关记录不可见」，audit 那条也从「断言跳转到 /tasks?runId=」改成「断言驻留原页 +
Run 详情打开」。配套还清掉了一条**把错误行为写进契约**的前端契约用例
（`test_audit_detail_contract.py` 原先要求「关联须落到任务列表并携带 id」）。

**仍未闭环（属另一个产品问题，不在本条）**

- **独立的 Run 列表页**：本条只做到「从一条审计/任务跳进它那次运行」。要能**浏览**所有 Run
  （按 Agent/用户/时间筛）就得开页面：新增导航项、`App.tsx` 路由、`sidebar.spec.ts` 的导航计数
  与详设 §3 页面结构都要跟着改。
- 更关键的是：**Run 详情要不要展示用户输入原文**。Console 至今零暴露对话原文，独立列表页等于把
  所有人的对话输入摊在 Console 里——这是新的隐私姿态，应由产品单独定。
  **本条刻意不展示**：`GET /api/v1/runs/{id}` 就不返回输入原文与事件负载（`test_runs_api.py`
  有专门用例钉住「不回退」），因为**先不加易、后撤难**。

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

- [x] 点击普通对话的关联 Run，能查看对应运行信息或该 Run 的审计记录，即使没有后台 Task。
      Run 详情（状态/Agent/执行用户/trace/起止/失败原因 + 事件轮廓）**就地叠加打开**，不需要有后台 Task。
- [x] 点击关联 Task，打开对应任务详情，而非普通列表。`e2e/tests/task-schedule/task-list.spec.ts` S-FE-07
- [x] 带关联参数的 URL 可直接访问并刷新，仍能定位同一记录。S-FE-07（含 `page.reload()`）
      ——**`taskId` / `scheduleId` 适用**；Run 走就地叠加、**没有 URL**（见文末"仍未闭环"）
- [x] 关联不存在、不可读及无关联记录的状态有明确反馈。E-FE-04；来源已删的定时任务见 S-FE-06；
      运行不存在见 `tests/console_platform/test_runs_api.py`（跨租户与不存在同码）
- [x] 增加覆盖上述关联跳转行为的测试。上述三条 + 键名配对契约 `tests/frontend/test_task_schedule_deep_link_contract.py`
      与 `tests/frontend/test_run_observability_contract.py` + 后端 `tests/console_platform/test_runs_api.py`
