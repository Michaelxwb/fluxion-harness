---
design_template: frontend
---

# Skill 执行结果与对话收尾前端设计

## 1. 文档控制

2026-11-24，设计 v0.1，待复核。前端交付对应 [PRD](./skill-results-conversation-finalization.prd.md)、[后端设计](./skill-results-conversation-finalization.backend.design.md) 及 [人工场景](./skill-results-conversation-finalization.scenarios.md)。

## 2. 需求与范围

| 需求 | 用户可见行为 | 场景 |
| --- | --- | --- |
| FEAT-01、FEAT-05 | Console 和任务中心分别说明本次调用是否执行完成、业务结论和原始退出码 | S-01、S-05、E-01、B-04 |
| FEAT-02、FEAT-04 | 保留聊天中的有效正文、附件与后续答复，技术详情收在独立区域 | S-04、E-02、B-05 |
| FEAT-03 | 对话显示可理解的空结果或客户选择问题。等待中可回答，刷新后仍可定位问题 | S-02、S-03、B-03 |
| FEAT-06 | 准确呈现取消、等待、期限与无法继续。恢复连接动作与业务重执行动作区分 | E-03、B-01、B-02、B-04 |

沿用 WebChat 对话、后台任务中心以及 Console 运行审计/任务详情的现有路由、权限和 SideSheet。真实验证环境需提供授权普通用户、维护者、浏览器及完整服务构建。无权查看详情的用户只能看到对应公开视图及授权产物。

## 3. 前端技术设计

### 3.1 页面、组件及读取路径

| 界面 | 现有落点 | 设计 |
| --- | --- | --- |
| WebChat 对话 | `runState.ts`、`ChatPage.tsx`、`MessageThread.tsx` | 答复正文、部分结果和附件仍在同一条回复。存在后端诊断投影时，消息末尾提供可展开的“技术详情”。有持久答复的失败终态在原回复收口 |
| WebChat 等待输入 | 同一对话的待输入呈现、消息输入框 | 按中断类型展示普通问题，通过原输入框发送真实用户答复。待确认授权继续使用现有确认面。提交失败显示可纠正原因，保持输入和问题状态 |
| Console Run 调用 | `RunDetailSideSheet.tsx`、`RunTimelineOutline.tsx`、`RunEventDetails.tsx`、`useRunEventDetail.ts` | 调用完成节点的摘要显示“执行完成 · 审查未通过”等文字。展开读取执行、业务结论、退出码、受限原因、证据及报告引用。Run 总体状态按终态展示 |
| Console 与用户任务中心 | `TaskDetailSideSheet.tsx`、`TaskBasicDetails.tsx`、`TaskTimeline.tsx`、`BackgroundTaskDetailPanel.tsx` | 原区域分别展示任务生命周期与验证后的 Skill 结果，授权报告复用任务产物入口。具备交互表面时继续沿用后台问题入口 |

API 使用各模块现有服务层和控制器获取详情、会话历史和流式更新。现有按需展开、缓存和 generation 处理继续负责请求生命周期。Run/Task 两端复用同一套执行结果映射及 zh-CN/en-US 词条，依据服务器字段渲染。

### 3.2 状态与展示契约

| 服务端事实 | 对话与 Console 呈现 |
| --- | --- |
| `COMPLETED` + `REVIEW_FINDINGS` | 执行完成、审查未通过。显示已验证 findings 和可访问报告。原始退出码 1 位于详情 |
| `COMPLETED` + `REVIEW_PASSED` | 执行完成、审查通过。保留 warning 摘要及原始退出码 0 |
| `COMPLETED` + `NO_DEVICES` | 显示当前已确认范围内没有设备，并保留查询结论 |
| `COMPLETED` + `NEEDS_INPUT` | 结果详情显示需要补充信息。是否等待用户以持久化中断为准 |
| 工具执行失败且 Agent 继续 | 进度项结束。调用详情记录真实失败，后续用户正文和部分产物正常显示 |
| 本轮失败且有持久回复 | 消息为服务端正文与可见产物。现有失败事件的 `has_reply` 控制通用失败提示，光标已确认后不重复创建消息 |
| 本轮失败且无可读回复 | 基于服务端错误事实展示现有兜底及合适下一步。技术原因按投影呈现 |
| 历史字段缺失 | 对新增状态显示“未记录”。保留来源记录中的退出码及旧状态。未知枚举用安全回退词条 |
| 报告未就绪、无权或已过保留期 | 详情独立提示对应状态。仅已有授权链接可以打开或下载 |

服务端 `execution_status`、`business_status`、`exit_code` 和 `report_ref` 来自既有详情接口的投影。结构化诊断只含允许公开的运行标识、错误码与有界脱敏信息。退出码 0 与字段缺失分别展示。结果分类只消费后端提供的字段，SSE、历史和详情以调用或消息身份合并。正文、诊断和报告始终按纯数据渲染。

- **等待。** 显示已保存问题与可操作输入。发送期间防重复提交。断线、刷新和切换界面后查询服务端状态。过期或已取消的旧问题显示结束，新消息通过既有会话入口。
- **终态与重连。** 正常回复和带答复的失败均以服务端持久正文为准。待重连期间保留已显示内容。重连动作只恢复事件订阅。用户显式发送的下一条消息只提交一次，且沿用当前会话。
- **详情四态。** 展开时显示加载。原始字段缺失显示未记录。详情失败提供原请求重试。成功时显示已确认字段和授权报告。切换记录时旧请求响应不能覆盖新记录。
- **样式与可访问性。** 复用 Semi Design 和既有 SideSheet、时间轴、详情字段样式。状态兼用文字和颜色。技术详情与时间轴支持键盘操作和展开状态。窄屏关键字段换行。保持既有焦点、流式占位和任务中心切换行为。

### 3.3 验证

计划在 WebChat 浏览器组件用例覆盖 `has_reply`、历史/SSE 重放、输入等待和失效问题。Console 组件验证执行/业务状态、历史字段、加载失败、键盘展开与 zh-CN/en-US。沿用仓库组件配置并以 `npm --prefix apps/console-platform/frontend run build` 验证类型和构建。API 白名单、授权及恢复由后端契约与集成测试覆盖。真实手工路径见 S-01、S-03、E-02、B-03、B-04。

## 4. 风险与依赖

| 风险 | 设计应对 |
| --- | --- |
| 初审有问题被显示成审查通过，或显示成进程崩溃 | 分开呈现执行、业务与原始退出码 |
| 终态或重连覆盖部分回复，或重复附加终态说明 | 使用服务端持久消息身份和水位，终态事件只负责收口和诊断 |
| 旧请求覆盖新视图或链接扩大访问权限 | 请求代次隔离，以及已授权接口和引用 |
| 用户把技术详情当作需处理的主要内容 | 正文只写业务解释和下一步，诊断在展开区域中查看 |

## Spec Compliance Matrix

| Spec/Rule | 设计落点 | 场景 |
| --- | --- | --- |
| `frontend/harness-frontend#RULE-front-001` | §3.1 模块服务层 | S-05 |
| `frontend/harness-frontend#RULE-front-003`、`frontend/harness-frontend#RULE-004` | §3.1–3.2 保留流式占位，任务中心代次和会话控制器 | S-03、B-04 |
| `ui/harness-ui#RULE-ui-001`、`ui/harness-ui#RULE-002` | §3.1 在已有审计详情和时间轴展示结果 | S-05 |
| `ui-detail/harness-ui-detail#RULE-ui-detail-001` | §3.1–3.2 复用 SideSheet 和按需详情 | S-05、B-04 |
| `i18n/harness-i18n#RULE-i18n-001` | §3.1–3.3 状态、兜底文案双语 | E-02、S-05 |
| `arch/harness-arch#RULE-003`、`arch/harness-arch#RULE-004` | §3.2 技术细节及限流部分结果 | E-02、B-05 |
| `test/harness-test#RULE-test-001` | §3.3 组件、契约和人工验收各自覆盖 | S-03、B-04 |
