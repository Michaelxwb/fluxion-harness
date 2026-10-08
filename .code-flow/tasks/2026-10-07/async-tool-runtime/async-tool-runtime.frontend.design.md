# 异步工具结果回流与 Run 等待恢复：前端设计

> 文档编号：FE-ASYNC-TOOLS-0.1
>
> 版本：v0.1 · 创建日期：2026-10-07 · 状态：设计评审中
>
> 配套文档：[后端设计](async-tool-runtime.backend.design.md) · [Spec Context](spec-context.yml)

## 1. 文档控制

用户要求将既有分析直接细化为设计文档。本文件采用 Frontend 模板，覆盖 Console 现有 Run 详情与关联 Task 展示；后端文档负责任务执行、结果回流、Gateway 等待及可靠投递。本文件中的组件、词条与场景是待实现设计，不是已经交付的 UI。

| 版本 | 日期 | 作者 | 变更 |
|---|---|---|---|
| v0.1 | 2026-10-07 | Codex | 将等待与接续信息接入现有只读详情，明确交互、契约与浏览器验收 |

## 2. 需求分析

### 2.1 需求概述

用户从运行审计或后台任务打开来源 Run 时，能区分“等待用户确认”和“等待任务结果”，看到待确定提交数、未完成 JOIN 数及关联 Task；任务终态后刷新能看到 Run 接续和终态轮廓。沿用现有只读产品定位。

### 2.2 功能方案

| 功能 | 描述 | 优先级 | 来源 |
|---|---|---|---|
| FEAT-10 | Run 详情展示 WAITING_TOOL、等待起点、截止时间、接续次数与待处理数量 | P0 | 后端 SRC-06；用户“直接细化设计文档” |
| FEAT-11 | 分页展示关联异步操作，已受理任务可跳到既有 Task 详情并返回原 Run | P0 | 后端 SRC-06；后端 FEAT-02、FEAT-09 |
| FEAT-12 | 新事件轮廓的本地化名称、刷新、完整性提示及请求竞态处理 | P0 | 后端 SRC-04、SRC-06 |
| FEAT-13 | 两语言、统一时间/状态映射、四态、键盘交互与真实浏览器验收 | P0 | 后端 SRC-06；现有 Console 规范 |

### 2.3 范围与边界

沿用 `/audits`、`/tasks` 的入口和十一项菜单，使用现有 RunDetailSideSheet。新增关联操作页签和只读指标；不增加 Run 列表、聊天页、主动调度、Run 取消按钮、原文/工具结果明文或调试凭据入口。Task 详情的既有取消功能沿原权限与后端语义执行，不从 Run 轮廓另造一套取消接口。

刷新由用户触发，初次打开和切换对象自动取数；第一版不以轮询动画假造任务进度。后台任务受理前 task_id 可空，显示“提交待确认”；JOIN/DETACH 展示为“本次回答需要结果”/“独立后台任务”，不把技术内部状态露成难懂的裸枚举。

### 2.4 验收条件

所有浏览器场景使用真实构建产物、Chrome、Console HTTP、真实 PG 数据投影；涉及任务状态变化的场景另起真实 Runtime/Worker、SSE/LLM 探针。无业务响应 mock 或 page.route fulfill。全部归属本需求，状态待实现。

| 场景 | 功能 | 层级 | 关键真实边界 | 操作与最终可观察结果 |
|---|---|---|---|---|
| S-20 | FEAT-10、FEAT-12 | E2E | Browser→Console→PG；Runtime/Worker/LLM HTTP | 从审计打开真实 JOIN 等待 Run，显示“等待任务结果”、等待时间和数量；结果已收到但尚未 claim 时说明“等待接续”；完成 Task 后点刷新，显示接续/完成且数量归零 |
| S-21 | FEAT-11 | E2E | Browser→Router/面板控制→Console Run/Task API→PG | 点击关联 task_id 打开已有 Task 详情，再点来源 Run 返回同 run_id；反复切换关闭不崩溃，不无限堆叠面板 |
| S-22 | FEAT-10、FEAT-11 | E2E | Browser、真实 Console 分页、PG count | 含 SUBMIT_PENDING 与受理 DETACH 的 Run，前者 task_id 为空无假链接，后者明确独立模式；超过 15 条可翻页，total 与实际记录一致 |
| S-23 | FEAT-13 | E2E | Browser language/timezone、HTTP 请求头、真实 DTO | 开着详情切换 zh-CN/en-US，状态/事件/页签即时切换；UTC 时间按 Asia/Shanghai 显示 YYYY-MM-DD HH:mm:ss，非法值为“-”；请求头复用拦截器 |
| S-24 | FEAT-12、FEAT-13 | E2E | Browser、Console 真实只读投影、PG | 种含原文标记（非执行凭据）的真实 input/result 盘面；详情和网络响应不含原文/结果/凭据，只显示结构；时间线超过 200 条明确提示截断 |
| E-20 | FEAT-10、FEAT-12 | E2E | 故障代理、Browser、真实 Console 重试 | GET 失败显示 ErrorState，恢复后重试得到实际数据；失效会话遵循统一登录重定向，不闪旧对象内容 |
| E-21 | FEAT-11、FEAT-13 | E2E | Browser、账号租户、Console API | 跨租户/不存在 Task 链接请求 404，返回原 Run；伪造 X-Tenant-Id 不改变数据，非 ADMIN 不新增凭据入口 |
| E-22 | FEAT-12 | E2E | 延迟真实响应的代理、Browser、服务请求 | A Run 响应慢，切到 B/关闭后 A 才返回；当前详情不被 A 覆盖，卸载后无状态更新或无效链接 |
| B-20 | FEAT-11、FEAT-12 | E2E | Browser、Console、真实 PG 空列表/分页 | 无异步操作显示空态；15/16 条分页边界正确；删除/取消等状态变化后当前空页回到合法页码，截断提示独立存在 |
| B-21 | FEAT-13 | E2E | Chrome 键盘、实际 Semi SideSheet/Tab/链接 | Tab 键可达刷新、关联 Task、返回和关闭；ESC 只关当前面板，焦点回到来源链接；状态信息不只依赖颜色 |

| 非功能项 | 验收标准 |
|---|---|
| NFR-FE-01 请求卫生 | 打开基本信息一条 detail 请求；首次打开关联页签一条分页请求；组件/hooks 无裸 HTTP，关闭/切对象使过期响应失效 |
| NFR-FE-02 语言与时间 | 无硬编码中文状态；动态键枚举两侧齐全；不缓存译文；DateTimeText 唯一展示时间入口 |
| NFR-FE-03 隐私投影 | API 不取 input_text、任意事件 payload 或 result；只读关联清单无正文/凭据 |

## 3. 前端技术设计

### 3.1 技术选型

沿用 React 18、TypeScript 5.7、Semi Design、React Router、react-i18next、共享 axios 与当前 CSS tokens。数据状态放容器局部 hooks；不为此引入全局状态库、SSE 前端客户端或新的组件库。

### 3.2 页面与路由结构

**UI-01：入口和列表。** 运行审计 `/audits` 的关联 Run 与后台任务 `/tasks` 的来源 Run 共用详情。十一项菜单不变；后台任务列表继续 PageHeader→PageSection→ModuleToolbar→RemoteTable/PaginationFooter，主展示字段通过 EntityLink 打开详情，筛选遵循“选择即时、文本回车”。本需求不另造异步任务列表页。

### 3.3 组件设计

```text
AuditPage / TaskPage（现有来源页面）
└─ RelatedDetailController（同一时刻一个关联对象）
   ├─ RunDetailSideSheet
   │  └─ DetailSideSheet（共用宽度、标题、页签）
   │     ├─ basic：DetailGrid / StatusTag / 等待说明
   │     ├─ operations：RunOperationTable / PaginationFooter
   │     └─ timeline：RunTimelineOutline / 截断提示
   └─ TaskDetailSideSheet（原有 Task、取消和来源 Run 功能）
```

| 组件 | 类型 | 职责与复用 |
|---|---|---|
| CMP-01 RunDetailSideSheet | 容器 | 详情、页签、刷新和请求代次；复用当前模块 |
| CMP-02 RunOperationTable | 展示 | operation 状态、依赖模式、Task 链接、时间；不自行 HTTP |
| CMP-03 RunTimelineOutline | 展示 | 已有轮廓事件新增本地化名称，保留 seq、时间与有无制品标记 |
| CMP-04 useRunDetail/useRunOperations | 数据 hooks | 请求状态、分页、竞态失效；调用 services/runs.ts |
| CMP-05 RelatedDetailController | 页面局部状态 | RUN/TASK 联合状态互斥切换、返回来源；不在面板内递归挂载另一面板 |

**DETAIL-01：布局。** 公共 DetailSideSheet 宽度 920；标题 run_id、副标题状态/trace 居左，右侧刷新动作与关闭 X 同一行，下方 Tabs。基本信息用分区标题、DetailGrid 双列与 StatusTag；WAITING_TOOL 的 Banner 显示等待原因，WAITING_INPUT 保持人类确认文案。actions 有内容才渲染，children 判空沿公共组件约定。

关联操作页签使用局部 Semi Table，详情表不强行用列表页 RemoteTable。列顺序：调用标识、依赖模式、提交/执行状态、关联任务、提交时间、完成时间、操作（“查看任务”）。任务主字段采用 EntityLink，可点击字段和末列查看复用同回调；task_id 为空时两处均不可点击。不写任意结果文本或裸 snapshot JSON。时间线保留内容边界，只本地化 event_type 标签，不读取原事件 data。

### 3.4 组件接口契约

| 组件/Props | 类型 | 约束 |
|---|---|---|
| RunDetailSideSheet.runId | string 或 null | null 为关闭；切换对象清空旧内容并失效旧请求 |
| RunDetailSideSheet.onCancel | () => void | 仅关闭视图，命名沿原接口，不表示取消 Run |
| RunDetailSideSheet.onOpenTask | (taskId: string, sourceRunId: string) => void，可选 | 有回调且 Task 已受理才呈现详情链接；页面负责替换关联面板 |
| RunOperationTable.rows | readonly RunOperationOutline[] | 无 input/result 或 credential 字段 |
| RunOperationTable.onOpenTask | (taskId: string) => void，可选 | 不导航、不修改 props；上抛点击意图 |
| RunOperationTable.pagination | PageMeta、onPageChange/onPageSizeChange | 默认 15，page_size≤100；变化交容器重取 |
| RunTimelineOutline.events | readonly RunTimelineEvent[] | 只接受现有轮廓及元数据，不收 payload |
| RunTimelineOutline.truncated | boolean | true 必须显示完整性提示 |

RelatedDetailController 状态为 `NONE | {kind:RUN,runId} | {kind:TASK,taskId,sourceRunId}`，来源页面主体保持。Run→Task 时关闭关联 Run 再打开 Task；来源 Run 回调反向替换。最多保留来源详情和一层关联详情，阻止 Run/Task 递归堆叠。返回不重新创建 Run。现有 TaskPage 可复用主 Task 面板，Audit 来源入口按同一控制器接线。

### 3.5 状态与数据流

**FRONT-01：HTTP 出口。** 仅 `modules/run-observability/services/runs.ts` import `src/api/client.ts` 的共享 api 实例：getRun(id)→API-05，listRunOperations(id,{page,page_size})→API-06。组件/hooks 只能调用这两个 service；纯 apiErrorBody 工具可用，但不得调用 api/fetch/axios。请求头、CSRF、401 跳登录和封套 Toast 沿现有拦截器，不改框架。

扩展 RunStatus 联合加入 WAITING_TOOL；RunDetail 添加 waiting_since/deadline_at 可空字符串、waiting_reason（SUBMISSION/TASK_RESULT/RESUME_READY 或 null）、pending_join_count/pending_submission_count/continuation_count 非负整数。RunOperationOutline 使用精确 JOIN/DETACH、SUBMIT_PENDING/SUBMITTED/COMPLETED/FAILED/CANCELLED 联合，error_phase 为 SUBMIT/EXECUTE 或 null、error_code 可空，TaskStatus 复用已有定义且可空。提交失败按错误阶段翻译，不能把 unknown task_status 显示成“任务失败”。分页响应是 Page<RunOperationOutline>，不是裸数组。未知 HTTP/业务失败以已有 ErrorState 与 apiErrorBody 分类处理。

detail/loading/error、operations/page/pageSize/loading/error、activeTab 为容器 local 状态；来源面板状态在页面局部。每条请求发出递增 requestSeq，响应/异常/finally 都核验；关闭、对象切换及卸载也递增，旧请求不能写当前 state。关联页签首次取数，基本页签不开启分页请求。刷新同时使当前对象已加载的数据失效，再批量请求 detail 与可见页签，不逐行 GET Task。detail 与 operations 代次关联，不能把另一个 Run 的清单拼到当前详情。

**I18N-01：词条与状态映射。** `run.status.WAITING_TOOL`：中文“等待任务结果”，英文“Waiting for task results”；新增 run.detail.waitingSince/deadline/pendingJoin/pendingSubmission/continuations、run.detail.operations、run.operation.mode.JOIN/DETACH、run.operation.status.*、run.event.*、run.operation.submissionPending/noTask 等扁平点号词条，两侧键集一致。错误词条以真实后端 code 同名登记并有 fallback。

run-observability/statusOptions.ts 是本模块唯一颜色/词条键映射；标签在本次渲染用 useTranslation 的 t 解析，不在 useState/useMemo 或模块常量缓存译文。WAITING_TOOL 与 WAITING_INPUT 都可用 amber，但文字不同；SUBMIT_PENDING 是提交状态，不能套 Task QUEUED 颜色/名称欺骗用户。事件动态键必须枚举：TOOL_SUBMISSION_PENDING、TOOL_TASK_ACCEPTED、TOOL_RESULT_RECEIVED、BACKGROUND_RESULT、BACKGROUND_RESULT_LATE、RUN_WAITING_TOOL、RUN_RESUMED，以及已展示的现有事件。收到结果与模型使用结果是独立轮廓；前者不能直接显示 Run 已完成。未知事件用“事件”+安全标识兜底，不渲染未知 payload。

### 3.6 UI 状态

| 视图 | loading | empty | error | success |
|---|---|---|---|---|
| Run 基本信息 | 初次 Spin，刷新不把旧对象闪到新对象 | runId=null 关闭；404 显示不可用 | ErrorState 可重试；401 由框架跳登录 | DetailGrid、真实状态、等待数量和时间 |
| 关联操作 | 局部 loading，禁用重复翻页 | EmptyState“暂无异步操作” | 只在该页签 ErrorState，不清除有效基本信息 | 分页清单、Task 链接；无 Task 显示提交待确认 |
| 时间线 | 共享 detail 的 loading | 当前已有 EmptyState | detail 失败时不显示上一 Run 时间线 | 有序事件轮廓、DateTimeText、制品标记、truncated 提示 |
| Run→Task 切换 | Task 详情原有 loading | Task 不存在明确不可用 | 保留返回来源能力 | 当前真实 Task；来源链接回原 Run |

### 3.7 样式方案

复用 app.css 的 --semi-color-* / --app-*、detail-section-title、detail-hint、DetailGrid 与表格样式，不在组件写颜色、内联 SVG 或自定 SideSheet 宽度。长 UUID 用 mono/省略并提供可访问完整名称，时间用 DateTimeText；现有桌面 Console 的面板滚动行为保持，窄窗口关联清单允许局部横向滚动，不临时发明新断点系统。

### 3.8 可访问性与兼容性

EntityLink、刷新和关闭使用现有 Button/公共组件，aria 名称来自当前语言；状态包含可读文字；Tab/ESC/返回焦点由 Semi 与控制器协作，不仅鼠标可用。浏览器验收使用系统 Chrome，语言和 timezoneId=Asia/Shanghai 显式固定，避免开发机与 CI 时区差异。运行前 build，关闭内层关联面板后断言来源页面仍挂载。

## 4. 风险与依赖

| 风险 | 影响 | 应对 | 场景 |
|---|---|---|---|
| RISK-FE-01 等待被显示成完成或确认 | 高：用户误判执行状态 | 状态/模式唯一映射、精确 DTO 与本地化标签 | S-20、S-22、S-23 |
| RISK-FE-02 过期响应覆盖新对象、嵌套面板崩溃 | 高：错看任务或整页消失 | 请求代次失效、互斥关联控制器、公共 children 安全约定 | S-21、E-22 |
| RISK-FE-03 扩投影时带出结果或跨租户数据 | 高：泄密 | 后端只读字段白名单、账号租户、真实 HTTP 反查 | S-24、E-21 |
| RISK-FE-04 译文缓存、时间和分页漂移 | 中：中英文混用、数据不完整 | 当前渲染翻译、DateTimeText、分页 count 同谓词 | S-22、S-23、B-20 |

依赖后端 API-05/06 与 WAITING_TOOL 同版本可用。前端不添加适配旧 DTO 的双路径。页面 E2E 优先扩展现有 audit-observability/task-schedule 域；真实执行链放后端 runtime live stack，浏览器跨链用例由 Plan 固定一个独立数据库的完整栈，不让两套 Worker 共享验收库抢任务。

实现检查：TypeScript typecheck/build、check_frontend_api_usage.py、check_frontend_i18n.py、run-observability 源码/词条契约及上述真实 Playwright 场景。Plan 登记可收集命令和证据；本设计不把这些尚未实现的检查标成通过。

## 5. 需求追溯矩阵

| 来源 | 功能 | 组件 / 后端接口 | 场景 | 状态 |
|---|---|---|---|---|
| SRC-06 | FEAT-10 | CMP-01、API-05 | S-20、S-22、E-20 | 待实现 |
| SRC-06、FEAT-02 | FEAT-11 | CMP-02/05、API-06 | S-21、S-22、E-21、B-20 | 待实现 |
| SRC-04、SRC-06 | FEAT-12 | CMP-03/04、API-05/06 | S-20、S-24、E-20、E-22、B-20 | 待实现 |
| SRC-06 | FEAT-13 | 所有组件、前端词条/DateTimeText | S-23、S-24、E-21、B-21 | 待实现 |

## Spec Compliance Matrix

本文件承载 Context 中 4 条前端 required Rule；其余 16 条见后端 Matrix。applied 表示设计落点已明确，功能测试待实现。

| Spec/Rule | enforcement | 设计影响 | 具体落点 | 验证场景 / 现有 verifier | 状态 |
|---|---|---|---|---|---|
| harness-frontend#RULE-front-001 | required | 模块 services、共享拦截器与精确类型 | §3.5 / FRONT-01 | S-20、E-20、E-22；test_api_client_contract/API usage/typecheck | applied |
| harness-i18n#RULE-i18n-001 | required | 双语、枚举齐全、翻译不缓存 | §3.5 / I18N-01 | S-23；check_frontend_i18n/test_foundation_i18n | applied |
| harness-ui#RULE-ui-001 | required | 固定菜单与统一列表、详情入口 | §3.2 / UI-01 | S-21、S-22；test_console_shell_contract/test_ui_style_contract/build | applied |
| harness-ui-detail#RULE-ui-detail-001 | required | 公共 SideSheet、标题操作、Tabs、双列详情 | §3.3 / DETAIL-01 | S-20、S-21、B-21；test_detail_sidesheet_contract/test_form_layout_contract/typecheck | applied |

## 附录：状态用语

WAITING_TOOL 是 Run 暂停执行并等依赖结果；Worker Task 的 WAITING 是任务自身等待外部条件，两者不能共用“正在执行”文案。SUBMISSION_PENDING 是 Runtime 已持久化提交意图、Worker 尚未确认受理；SUBMITTED 是受理成功；COMPLETED/FAILED/CANCELLED 才是任务终态。Run 详情是观察入口，关闭视图不取消执行。
