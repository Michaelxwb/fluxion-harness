# Console 全页线框设计稿（对齐用，v1）

- 走查基线：`http://localhost:5174/console/`，17 屏截图（` /tmp/console-*.png`）。
- 已定方向：编辑器用左侧二级导航；版本历史进编辑器内部分组；全页线框；孤儿页纳入。
- 约束：Semi Design 2.102.x；Console API 响应/日志/审计封装不动；只改前端展示层 + 文案，不改后端契约（除凭据类型 `-` 缺数据需后端补，见 P10-BUG1）。

---

## 0. 全局（所有页共享，先做）

```
┌────────────────────────────────────────────────────────┐
│ 侧栏                    │ 顶栏：面包屑 / 环境Tenant / 搜索 / 主题 / 头像 │
│ Fluxion 控制台          ├────────────────────────────────────────┤
│ 分组(手风琴,记展开态)    │ 页头：标题 + 描述 + 页级操作(右)          │
│  • 图标+中文,禁用置灰    │ 内容区                                    │
└────────────────────────────────────────────────────────┘
```

- G0-Shell：分组改手风琴（默认只展开当前分组，展开态记 localStorage）；`插件策略`保留置灰 + "规划中" Badge；**分组保持 6 组不变**（`frozen-nav`/`console-router` 测试锁死，不合并）；**资源绑定不进导航**（router 测试明令 Binding 非一级导航，收编方式改为 Policy Editor 内"去绑定"跳转，见 P10）。
- G1-Time：全站时间 → `RelativeTime`（x分钟前）+ hover 完整本地时间。涉及 7 页。
- G2-ResourceId：等宽截断（前 10…后 4）+ 复制按钮 + hover 全文。涉及列表/审计/概览/执行。
- G3-Empty：`EmptyState`（Semi Empty 插画 + 1 句说明 + 主 CTA）。涉及工作流/能力三tab/规则空/绑定空/插件策略。
- G4-Row：名称列 = Avatar/图标 + 主名 + 描述小字；行 hover 露快捷操作；状态 Tag 语义色统一（草稿灰/已发布绿/失败红/运行中蓝）。
- G5-Title：全站页头统一三件套（标题/一句话描述/右上主操作）；中英混杂改中文（`Policy Editor`→`策略编辑`，`User 360`→`用户详情`）。
- G6-Risk：高风险操作统一确认模板（影响范围文本 + 输入资源名二次确认）：发布 / 回滚 / 撤销凭据 / 撤销链接 / 删除绑定。
- G7-Detail：审计/执行/用户详情统一用右侧抽屉（Drawer 宽 560），不跳页。
- BUG-SHELL1：编辑策略/能力/Skill 时侧栏高亮跳到"平台概览"（`selectedKey`回退逻辑），修为高亮归属菜单（取路由前缀最长匹配）。

---

## P1 平台概览（重排）

```
[指标卡×4: 智能体 1 | 工作流 0 | 模型 2 | 近24h执行 2 ]  ← 横排,可点跳
[异常工作台：失败执行 2(红) | 已撤销凭据 0 | 快捷入口]  ← 0 时收成安静条
[最近治理活动 ×5：操作Tag + 对象(G2) + 相对时间(G1)]     ← 去掉截断ID裸文本
[最近活动表：操作Tag着色 + 对象 + 执行者 + 相对时间]      ← publish蓝/secret灰/chat绿
```

- 删"平台对象"巨行条；数字卡点跳转对应列表（带状态筛参）。

## P2 智能体列表（轻改）

- 名称列：Avatar（名首字）+ 名 + 描述小字（能力数·模型名，1 行截断）。
- 主模型列：模型名 + provider 小字（替代 `model-definition_xxx` 裸 ID）。
- 时间 G1；ID 列删（hover 行操作里放"复制 ID"）；行 hover 快捷：测试 / 对话链接 / 发布。
- 空态 G3（新建智能体 CTA）。

## P3 智能体编辑器（重做信息架构，最大头）

```
页头：← 名称（可改） [草稿v3●未保存]        [保存][发布]   ← 吸顶
┌──────────┬─────────────────────────────────────────────┐
│ 基础     │                                             │
│  基本信息 │  当前分组表单                                │
│  Prompt  │                                             │
│  模型    │  测试分组 = 迷你聊天窗（复用chat组件）        │
│ 编排     │  版本分组 = 时间线 v3(草稿)·v2(已发布·当前)    │
│  能力    │    ·v1：每项 [diff][回滚(G6)][发布时间G1]     │
│  工作流  │                                             │
│ 渠道用户 │                                             │
│  用户授权 │                                             │
│  高级设置 │                                             │
│ 验证     │                                             │
│  测试    │                                             │
│  评测    │                                             │
│ 治理     │                                             │
│  版本 ●2 │                                             │
└──────────┴─────────────────────────────────────────────┘
```

- 11 tab → 左侧 4 分组 11 项；分组可折叠；当前分组路由化（`?tab=`，刷新不丢）。
- 版本分组替代死链 tab：时间线 + diff 抽屉 + 回滚走 G6（"回滚将基于 v2 创建新草稿 v4，原 v3 草稿保留"）。
- 测试分组：输入框 + 运行 → 聊天式气泡（MarkdownMessage/TypingIndicator 现成组件移植）+ 本次 trace 跳执行记录。
- 未保存 dot + 离开守卫（Prompt 大文本防丢）。

## P4 工作流列表（轻改+统一命名）

- 标题统一叫`工作流`（菜单/标题/新建三处一致，现在标题是"流程编排"）。
- 空态 G3 三步说明（定义→校验→发布不可变）+ 新建 CTA；有数据后行结构同 P2。

## P5 Workflow Designer（中改，源码已读）

```
页头：← 名 [草稿] [保存][发布]
[资源条：ID(G2) · 版本 · 状态Tag · 引擎提示]
[表单模式 | JSON模式]
表单：左=节点步骤条（①②③可拖序·点选·＋添加） 右=节点配置表单（能力选择器带搜索）
发布：校验诊断 → 行内错误卡（点一条定位到节点）→ 确认Modal(G6：影响版本说明)
```

- 现在诊断是红字 ul 列表，改成可点击定位的错误卡；节点步骤条加序号+类型图标；JSON 模式加语法错误行内提示（现在静默吞）。

## P6 能力三 tab（中改）

- 新建按钮随 tab 变：新建技能 / 新建工具 / 接入 MCP（现在永远"新建 Skill"）。
- 各 tab 独立空态 G3（一句话讲清 skill/tool/mcp 区别 + 文档链接）。
- 行：名称 + 描述 + 版本 Tag + 被引用数（哪些 Agent 在用，防误删）+ G1。

## P7 用户列表（轻改）

- Avatar + 名 + 状态（链接数 Tag）；「查看 360」改名「用户详情」；时间 G1。
- 表下说明文字收进表头问号 Tooltip。
- 生成链接结果态（待你确认现状，见文末 Q1）：目标态 = Modal 第二步展示可复制链接 + 有效期 + [复制][撤销][完成]。

## P8 用户详情（中改，去重）

```
页头：← Avatar alice [2链接] [生成对话链接]
聚合条：渠道 0 · 授权Agent 1 · 近7天对话 12 · 风险 无      ← 替代重复的身份概要卡
Tab：对话链接（链接表：agent·创建时间G1·[复制][撤销G6]）| 画像 | 能力授权 | 策略 | 活动
```

- 删重复概要卡；链接管理从列表 Modal 搬一半过来（详情页可直接管链接）。

## P9 授权规则列表（轻改）

- 空态 G3：白/黑名单一句话图解 + 新建 CTA；有数据后：规则名 + 白名单 n/黑名单 m 计数 Tag + 生效租户 + G1 + 行操作（编辑/发布/删除 G6）。

## P10 Policy Editor（中改，已走查）

- 标题中文化`策略编辑`；白/黑名单输入改成"能力多选器"（搜已发布 skill/tool/MCP 打勾，已加显示为可删 Tag，现在是裸文本 input 考验记忆力）。
- 生效链路文字（发布→绑定→运行期交集）改成步骤条可视化 + "去绑定"跳转（/governance/bindings）。
- BUG1（需后端）：凭据页"类型 -"同类问题先查 spec 是否缺字段。

## P11 插件策略（占位→可用空态页，中改）

- 置灰保留到 P1 上线；在占位页内给"即将到来"内容：Hook 四要素说明卡（priority/timeout/fail policy/scope）+ 按 Hook 点分的灰态分组（前置/鉴权/后置…）+ 上线后第一步操作预告。现在是一块黑板。

## P12 操作审计（中改）

- 表格保留三筛 + 加时间快捷（近1h/24h/7d）；时间 G1；对象 G2。
- 行点开 G7 抽屉：完整对象/版本/diff 摘要/request_id + [跳执行记录]（trace 关联）。
- 操作列 Tag 着色（publish/rollback 红系高亮，读操作灰）。

## P13 执行记录（重做信息层，中改）

```
Tab：智能体执行 | 工作流运行     ← 替代上下双表堆叠
表：状态Tag · 智能体名(链编辑器) · 耗时 · 失败原因摘要 · 开始G1 · ID(G2)
行点开 G7：Trace 时间线（hook/模型/tool段）+ 输入输出折叠 + 快照版本 + [重试][跳审计]
```

- 现在失败了都不知道为啥；耗时列需后端确认有无字段（没有则前端算 end-start）。

## P14 凭据（轻改+修数据）

- 类型 `-` 修（BUG1）；使用方改 Tag；SecretRef G2；已撤销行灰化 + "已撤销" Tag（与概览入口对应）；撤销走 G6。

## P15 模型（中改，卡片化）

```
[Provider卡 ×n：Avatar首字 grok · endpoint小字 · 连通性[探测] ]
   └ Model标签组：grok-4.6 (v1)[设默认] · + 添加模型
[＋ 连接模型服务]
```

- 一眼看出哪个 Provider 挂了；模型版本收进卡片内，不再占表列。

## P16 资源绑定（Policy Editor 跳转收编，轻改）

- 不给导航入口（旧决策保留）；Policy Editor 生效链路步骤条带"去绑定"跳转（见 P10）。
- 空态 G3（绑定=用户级差异，凭据只展 SecretRef）；`ListPager` 换 `StandardListFooter` 修双分页 bug；类型/主体/资源列加 Tag 着色。

---

## 代码对齐修订（2026-09-08，三路走查后）

> 真机截图没骗人，但源码显示已有复用件。以下覆盖上文对应章节，**以本节为准**。

### R0 已有复用件（TASK-001 瘦身：只新增缺的 4 件）

- 已有：`PageHeader`、`StandardListShell`（Toolbar/Search/Footer/Card/RowActions）、`StatusTag`（语义色已统一）、`ErrorBanner`、`VersionHistory`（版本表 + 键级 Diff 并排，内嵌于 Agent/Model/Credential 详情）、`SpecDiffModal`、`SchemaForm`、`useRemoteResourceOptions`（远程搜索 hook）、`StudioToolbar`、`WorkflowNodeList`、`NodeConfigForm`、`RunsTable`、`User360Header/Tabs`。
- 缺的（仍需新做）：`RelativeTime`、`ResourceId`、`EmptyState`、`RiskConfirm`。详情统一用 Semi `SideSheet`（已有 7 处直调，不再抽象 `DetailDrawer`）。
- 样式：Semi tokens + 单 `styles.css`（187 行），新样式只进此文件；无 CSS Modules/Tailwind。
- 测试模式：`renderConsole({seed})` 注入 in-memory API；构建门禁禁打包 in-memory。

### R1 版本/回滚（修正 P3）

- `AgentDetailSideSheet` **就是智能体详情**（只读 + VersionHistory + Diff，注释明确不从详情发起编辑）。断的只是编辑器版本 tab 的死链。
- 改法：编辑器「版本」分组**内嵌现有 `VersionHistory` 组件**，不新建时间线；回滚按钮是新增，但**挂在 `VersionHistory` 复用件上**（Agent/Model/Credential 全局受益），走 G6 确认 + 后端已有 `rollbackVersion` 接口。

### R2 编辑器测试/评测/用户/渠道（修正 P3/P7 相关）

- `AgentTestPanel` 已是真实 SSE Timeline 测试 → 只做气泡样式轻改，不做迷你聊天窗重做；`AgentEvalPanel` 有内容，别碰。
- `CapabilityPicker` 已是远程搜索多选 → P10 名单多选器复用其模式，不新做。
- `AgentUsersPanel`（授权 + 能力交集 + 签发/撤销）与 `AgentChannelsPanel`（开通/验证/撤销）已存在 → P7 结果态改为：**复用 `UsersChannelsPage` 已有的「对话链接 SideSheet（复制/打开对话/撤销）」模式，Modal 确认后直接打开该 SideSheet**，不新做 Modal 第二步。

### R3 列表页多为轻改（修正 P1/P12/P13/P15）

- P1 概览已是异常工作台 + 带参跳转 → 只做指标卡化 + 着色。
- P12 审计已有详情 SideSheet（request/trace/before-after）→ 只加时间快捷 + 操作着色。
- P13 执行已有 `RunDetailSideSheet`（5 分区 + Snapshot）→ 只做 Tab 切双表 + 补列（智能体名/失败摘要）。
- P15 模型已有 Provider 分组 + 连接一条龙 Modal → 只加连通探测按钮 + 卡片微调。
- P16 绑定页用了遗留 `ListPager` → 修法明确为换 `StandardListFooter`。

### R4 Q2/Q3 答案（代码已给出结论）

- Q2 凭据类型：后端 `SecretDefinition` 无 type，只有可空 `purpose`（空即 `-`）。**纯前端可解**：创建/编辑表单 `purpose` 改必填（预设下拉 api-key/token/password/certificate + 自定义），存量 `-` 随编辑消除。不需后端。
- Q3 执行耗时：run 无 `endedAt`，且 `trace_events[].at` 全填 `started_at`（后端占位），**前端算不出**。本批耗时列显示 `—`；后端补 `ended_at` 另立任务（不阻塞本批）。`WorkflowRunProjection` 有 createdAt/updatedAt，可前端相减显示。
- 链接有效期：后端 `chat_access_tokens` 有 `expires_at/revoked_at` 但前端契约截断。结果态第一版不显示有效期；要显示则后端补 `console_payloads.issued_chat_access_payload` + `httpConsoleParsers` 字段（TASK-004 执行期定）。

## 对齐结论（2026-09-08，用户确认）

- Q1：生成链接确认后**无结果展示**（现状缺）→ P7/P8 新做结果态（Modal 第二步：可复制链接 + 有效期 + [复制][撤销][完成]；详情页链接 Tab 同步）。
- Q2/Q3：执行时自查，缺字段后端补（凭据类型、执行耗时）。
- Q4：插件策略 **B（占位美化，不做完整 Hook 管理）**。

## 待你确认（3 个）

- Q1：生成对话链接确认后的结果态现在长什么样？（截图或一句话）→ 定 P7/P8 链接管理做到哪。
- Q2：凭据"类型 -"：spec 里本来就没 type，还是前端没取？（我动手时先查，没有就提后端补字段。）
- Q3：执行耗时后端有没有现成字段？（没有我就前端 end-start 算。）
- Q4：插件策略 P1 到底做不做进这批？（A 做：完整 Hook 管理页；B 不做：只做 P11 占位美化。）默认 B。
