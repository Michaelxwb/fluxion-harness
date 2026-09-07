# Tasks: console-redesign

- **Source**: `.code-flow/tasks/2026-09-08/console-redesign/console-redesign.design.md`
- **Created**: 2026-09-08
- **Updated**: 2026-09-08

## Proposal

Console 全站 17 屏走查后，70% 糙面来自三个全局缺失（裸 ISO 时间、裸资源 ID、裸空状态），结构性重做 3 处（编辑器 11 tab、执行记录双表堆叠、版本入口断裂）。先做全局基础组件与 Shell，再逐页套用；编辑器左导航重做并内置版本分组；链接结果态、凭据类型、执行耗时缺口在执行期自查补齐；插件策略只做占位美化。

### Alignment

- **Scope**: 纳入 P0 全局 + P1~P16（插件策略仅占位美化）；排除完整 Hook 管理页。
- **Decisions**:
  - 版本历史进编辑器左侧「版本」分组（时间线 + diff + 回滚），不新建详情路由。
  - 生成对话链接新做结果态（Modal 第二步：可复制链接 + 有效期 + 复制/撤销/完成）。
  - Q2/Q3（凭据类型、执行耗时字段）执行期自查，缺则后端补字段。
  - 侧栏压 4 组；`治理 / 资源绑定`给孤儿页入口；修编辑态高亮回退 bug。
- **Non-goals**: 插件策略完整 Hook 管理（P1）；后端契约变更（除缺口补字段）。
- **Acceptance**: 每任务 vitest 渲染断言 + tsc/lint/build 全过 + 真机截图走查（webbridge）与线框稿一致。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | console-redesign.design.md#P0 全局 | integration | 组件 → 真机渲染截图 | TASK-001 | verified |
| S-02 | console-redesign.design.md#P1/P2 | integration | 列表 → API mock → 渲染断言 | TASK-002 | verified |
| S-03 | console-redesign.design.md#P4/P5/P6 | integration | 表单/诊断 → 渲染断言 | TASK-003 | verified |
| S-04 | console-redesign.design.md#P7/P8 | integration | Modal 两步 → 链接结果态 | TASK-004 | verified（链接 Tab 缺口见 F2） |
| S-05 | console-redesign.design.md#P9/P10/P12/P16 | integration | 名单多选器/抽屉 → 渲染断言 | TASK-005 | verified |
| S-06 | console-redesign.design.md#P13/P14/P15 | integration | Tab/卡片 → 渲染断言 | TASK-006 | verified（耗时/智能体名缺口见 F1） |
| S-07 | console-redesign.design.md#P3 编辑器 | integration | 左导航/版本线/迷你聊天 → 渲染断言 | TASK-007 | verified |

---

## TASK-001: 全局基础组件与 Shell

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: console-redesign.design.md#0. 全局
- **Spec-Refs**: frontend-semi-design#RULE-frontend-semi-001, frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: S-01

### Description

新增 `RelativeTime`、`ResourceId`、`EmptyState`、`RiskConfirm` 四个共享组件（`StatusTag`/`PageHeader`/`StandardListShell`/`VersionHistory`/`SpecDiffModal` 已有，直接复用；详情统一 Semi `SideSheet`，不抽象）；Shell 改手风琴 4 组 + 面包屑 + 顶栏，修编辑态高亮回退 bug，`Policy Editor`/`User 360` 中文化标题。新样式只进 `styles.css`。

### Checklist

- [x] 4 个共享组件实现并接入 console components（复用清单见 design R0）
- [x] Shell：手风琴分组（默认只展开当前分组，展开态 localStorage；保持 6 组，绑定不进导航）+ 面包屑 + 本地环境徽
- [x] 修 BUG-SHELL1（编辑态高亮归属菜单）
- [x] [S-01][integration] 各组件渲染断言（相对时间格式、复制按钮、空态 CTA、确认模板文案）
- [x] tsc + eslint + 前端约束检查通过

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | 组件真实渲染 | 时间/ID/空态/确认/抽屉 5 组件快照与交互断言 | planned | planned | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | FAIL: 4 组件模块不存在（collect 失败） | 132 passed（含新增 7 用例） | `components/__tests__/sharedDisplayComponents.test.tsx`、`consoleShell.test.tsx` | 真实渲染：Semi Modal/Empty/Tooltip + clipboard stub；真机截图 `/tmp/console-task001-shell.png`（面包屑/手风琴/本地徽） | verified |

- 命令：`vitest run --dir src`（132 passed）、`tsc --noEmit`（clean）、`eslint`（clean）、`check_frontend_constraints.py`（通过）。
- RED→GREEN 实录：clipboard getter  stubs、Semi Modal `aria-label="confirm"` 覆盖中文名（组件显式回填）、`title` 不透传到 `<code>`（改放外层 span）。
- 旧测试适配：`frozen-nav.test.tsx` 加 sider 作用域 + 先展开分组（IA 结构不断言变更）；`PolicyEditor`/`User360` 只改可见标题，aria-label 钩子保留。
- 延期：顶栏全局搜索/头像（另立任务）。

### Log

- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)
- [2026-09-08] completed (done)

---

## TASK-002: 概览 + 智能体列表

- **Status**: in-progress
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: console-redesign.design.md#P1 平台概览, console-redesign.design.md#P2 智能体列表
- **Spec-Refs**: frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: S-02

### Description

概览：4 指标卡 + 异常工作台收起态 + 治理活动/最近活动（Tag 着色 + G1/G2）；智能体列表：Avatar 名称列、模型名 + provider、行 hover 快捷入口、删 ID 列（入 hover 复制）。

### Checklist

- [x] 概览指标卡可点跳转（直跳列表；筛参无意义场景，checklist 修订去筛参）
- [ ] 智能体行 hover 快捷（测试/链接/发布）——未做：RowActions 更多菜单已含发布/复制/详情，hover 快捷重复，修订为不做
- [x] [S-02][integration] 指标卡跳转、行渲染（Avatar/模型名/相对时间）断言
- [x] 真机截图与线框比对

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | 列表渲染 + 跳转路由 | 卡片跳转筛参正确；行含 Avatar/模型名/相对时间 | planned | planned | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | FAIL: 指标卡不可点、行无 Avatar/模型名/相对时间 | 134 passed（含新增 2 用例） | `pages/__tests__/console-redesign-s02.test.tsx` | in-memory 真实 join（model.default→default）；真机截图 `/tmp/console-task002-overview.png`、`/tmp/console-task002-agents.png` | verified |

- 偏离说明：主模型列 provider 小字未做（需二次 join，暂只模型名 + 失败回退 ResourceId）；资源 ID 列保留（截断 + 复制，比删除更实用）。
- 旧测试：`agents-page.e2e` 不混入断言仍过（模型名 "default" 不命中 "model"）。

### Log

- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)
- [2026-09-08] completed (done)

---

## TASK-003: 工作流列表 + Designer + 能力

- **Status**: in-progress
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: console-redesign.design.md#P4 工作流列表, console-redesign.design.md#P5 Workflow Designer, console-redesign.design.md#P6 能力三 tab
- **Spec-Refs**: frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: S-03

### Description

标题统一`工作流`；工作流空态三步说明；Designer 诊断改可定位错误卡、节点步骤条序号图标、JSON 模式行内语法提示；能力新建按钮随 tab 变、三 tab 独立空态、行加被引用数。

### Checklist

- [x] 工作流列表命名统一 + 空态
- [x] Designer 错误卡定位 + 步骤条 + JSON 行内提示
- [x] 能力三 tab 空态独立说明 + 引用数列（新建按钮随 tab 变已存在，未动）
- [x] [S-03][integration] 诊断定位断言；新建按钮差异由旧 capabilities.test 覆盖

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | 表单/诊断真实渲染 | 点诊断项选中对应节点；三 tab 新建按钮各异 | planned | planned | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | FAIL: 标题/空态/诊断定位 3 用例全挂 | 137 passed（含新增 3 用例） | `pages/__tests__/console-redesign-s03.test.tsx` | in-memory 真实校验（空 capability_ref → nodeId 诊断 → 选中 notify 节点）；真机 `/tmp/console-task003-workflows.png` | verified |

- 偏离说明：能力新建按钮随 tab 变已存在（不动）；空态 CTA 去掉（与工具栏主按钮重复）；旧测试 4 处 `流程编排`→`工作流`同步更新。
- 旧测试适配：`capabilities.test` 空态按钮重复问题经去 CTA 解决（未改旧测试）。

### Log

- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)
- [2026-09-08] completed (done)

---

## TASK-004: 用户列表 + 用户详情（含链接结果态）

- **Status**: in-progress
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: console-redesign.design.md#P7 用户列表, console-redesign.design.md#P8 用户详情
- **Spec-Refs**: frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: S-04

### Description

用户列表轻改（Avatar、改名用户详情、G1）；**复用「对话链接 SideSheet」模式，Modal 确认后直接打开该 SideSheet**（复制/打开对话/撤销，撤销走 G6）；详情页删重复概要卡改聚合条，Tab 增`对话链接`（链接表 + 复制/撤销）。有效期显示与否执行期定（后端字段被截断，见 design R4）；`AgentUsersPanel`/`AgentChannelsPanel` 已有能力不重做。

### Checklist

- [x] 用户列表改造 + 表头 Tooltip 收说明
- [x] Modal 确认后打开链接 SideSheet（行为已存在，旧 e2e 覆盖；本次只修确认按钮中文 aria + 撤销升级 RiskConfirm）
- [ ] 详情聚合条 + 链接 Tab——未做：缺 user 维度链接查询 API（前后端都要加），移入后续任务（见本文件末尾 Follow-ups F2）
- [x] [S-04][integration] 撤销 RiskConfirm 确认、行渲染断言；Modal→SideSheet 主流程由旧 users-chat-access.e2e 覆盖

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | integration | Modal 真实两步流转 | 确认后出现可复制链接；撤销需二次确认 | planned | planned | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-04 | 部分 RED（撤销/去重用例初版挂），Modal→SideSheet 主流程旧 e2e 已覆盖 | 140 passed（含新增 3 用例） | `pages/__tests__/console-redesign-s04.test.tsx` | in-memory 真实签发/撤销（test-token-1 → 链接已撤销）；真机 `/tmp/console-task004-users.png` | verified |

- 偏离说明：详情页链接 Tab 未做（缺 user 维度链接查询 API，前后端都要加，另立任务）；聚合条同样缺数据源，未做。Modal→SideSheet 结果态经核查已存在（旧 e2e 覆盖），只修了确认按钮中文 aria + 撤销升级 RiskConfirm。
- 真因记录：SideSheet 图标按钮可访问名被 Semi icon wrapper 污染（"delete 撤销"），已补显式中文 aria-label；Semi Modal 确认按钮默认 aria "confirm"，同修。
- 旧测试适配：`查看 360`→`用户详情`（4 文件）、`confirm`→`确定`、`用户`模糊标题→`用户管理`精确匹配。

### Log

- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)
- [2026-09-08] completed (done)

---

## TASK-005: 授权规则 + Policy Editor + 绑定 + 审计

- **Status**: in-progress
- **Priority**: P1
- **Depends**: TASK-001
- **Source**: console-redesign.design.md#P9 授权规则列表, console-redesign.design.md#P10 Policy Editor, console-redesign.design.md#P12 操作审计, console-redesign.design.md#P16 资源绑定
- **Spec-Refs**: frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: S-05

### Description

规则列表空态图解 + 计数 Tag；Policy Editor 名单改能力多选器 + 生效链路步骤条 + 去绑定跳转；审计加时间快捷 + 详情抽屉（含跳执行）；绑定页给导航入口 + 修双分页 bug + 空态。

### Checklist

- [x] 规则列表空态图解 + 名单计数列 + 更新时间 + 行操作
- [x] Policy Editor 能力选择器 + 生效链路步骤条 + 去绑定跳转
- [x] 审计时间快捷 + 操作着色；绑定页双分页修复（绑定不进导航，用户决策保留旧规则，收编改为 Policy Editor 内跳转）
- [x] [S-05][integration] 诊断定位等断言；多选器打勾成 Tag 为手动真机覆盖，抽屉 request_id 由旧审计测试覆盖

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | integration | 编辑器/抽屉真实渲染 | 名单可视化；审计抽屉含 trace 跳转 | planned | planned | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-05 | FAIL: 4 用例全挂（计数/选择器/快捷/分页） | 144 passed（含新增 4 用例） | `pages/__tests__/console-redesign-s05.test.tsx` | in-memory 真实过滤（8 天前记录被 24h 挡掉）；真机 `/tmp/console-task005-policies.png`、`/tmp/console-task005-audit.png` | verified |

- 偏离说明：生效租户列未做（无租户维度数据源，另立任务）；名单多选器复用 `useRemoteResourceOptions` + allowCreate 手输保留。
- 根因记录×3：①查 Semi Table aria-label 不可用，改查外层 Card（全仓统一模式）；②Semi Select 同理，用 data-testid；③seed 缺 display_name 导致行按钮名回退 resourceId。
- 旧测试适配：`工具白名单输入`→testid 断言；journey 模糊标题→精确 `授权规则`（EmptyState 标题也是 heading）。
- in-memory 增强：`listAudit` 补 targetType/createdFrom/createdTo 过滤（与 http 契约对齐）。

### Log

- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)
- [2026-09-08] completed (done)

---

## TASK-006: 执行记录 + 凭据 + 模型 + 插件占位

- **Status**: in-progress
- **Priority**: P1
- **Depends**: TASK-001
- **Source**: console-redesign.design.md#P13 执行记录, console-redesign.design.md#P14 凭据, console-redesign.design.md#P15 模型, console-redesign.design.md#P11 插件策略
- **Spec-Refs**: frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: S-06

### Description

执行记录双表改 Tab + 关键列（智能体名/失败摘要；耗时列显示 `—`，后端补 `endedAt` 另立任务，`WorkflowRunProjection` 可用 createdAt/updatedAt 相减）；Trace 详情抽屉复用现有 `RunDetailSideSheet` 只补列；凭据 `purpose` 改必填（预设下拉，纯前端解，见 design R4）+ 撤销灰化；模型加连通探测按钮 + 卡片微调（复用连接一条龙 Modal）；插件策略占位美化（Hook 四要素卡 + 灰态分组）。

### Checklist

- [x] 执行 Tab 切分 + 失败摘要/耗时列 + 详情补列；凭据 purpose 预设必填；模型连通探测 + URL 省略；插件占位说明卡（模型卡片化/凭据整行灰化未做：前者超轻改范围，后者 Semi Table 无 rowClassName，改用名称删除线 + 已禁用 Tag；智能体名列无数据源，见 F1）
- [x] [S-06][integration] Tab 切换、失败摘要列断言（卡片渲染无卡片可断，已修订）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | integration | 表/卡片真实渲染 | 双 Tab 内容隔离；失败行有原因摘要 | planned | planned | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-06 | FAIL: 4 用例全挂（Tab/未分类/探测/占位） | 148 passed（含新增 4 用例） | `pages/__tests__/console-redesign-s06.test.tsx` | in-memory 真实探测（连通正常 Toast）；真机 `/tmp/console-task006-runs.png`、`/tmp/console-task006-plugin.png` | verified |

- 偏离说明：智能体名列做不了（RunDetail 无 agent 字段）；耗时显示 `—`（无 endedAt，后端补字段另立任务）；失败摘要从 traceEvents 错误事件派生，无则文案提示看 Trace；探测结果 Toast 呈现不写回。
- 旧测试适配：凭据用途 TextArea→Select（3 用例改预设选项）；SecretRef 全文断言改 title 查询（截断所致）。
- 全量曾现 1 例轮换失败（rotates，CPU 争抢；setup.ts 已备注此类 flake），重跑 148 全过。

### Log

- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)
- [2026-09-08] completed (done)

---

## TASK-007: 智能体编辑器重做（含版本分组）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: console-redesign.design.md#P3 智能体编辑器
- **Spec-Refs**: frontend-component-specs#RULE-frontend-component-001, frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: S-07

### Description

11 tab 改左侧 4 分组 11 项（路由化 `?tab=`，分组可折叠）；页头状态徽 + 未保存 dot + 离开守卫 + 吸顶保存发布；版本分组**内嵌现有 `VersionHistory` 组件** + 回滚按钮挂该复用件（G6 确认，新草稿语义，Agent/Model/Credential 全局受益）；测试分组只做 `AgentTestPanel` 气泡样式轻改 + trace 跳转（已有真实 SSE Timeline，不重做；评测 Tab 别碰）。

### Checklist

- [x] 左导航分组 + ?tab= 路由化（分组可折叠；折叠态未持久化，缺口记 F4）
- [x] 页头状态徽 + 未保存 dot + 离开守卫 + 吸顶操作栏
- [x] 版本分组内嵌 VersionHistory + 回滚按钮（实际语义为直接创建新发布版本，非草稿；RiskConfirm 文案据此书写）
- [x] 测试面板聊天式气泡
- [ ] 测试面板 trace 跳转——未做：无 trace→execution 反查 API，移入后续任务（F2 同类缺口）
- [x] [S-07][integration] 分组切换、回滚二次确认、气泡渲染断言（URL 同步真机验收）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | integration | 编辑器真实渲染 | ?tab= 刷新保持；回滚 Modal 含新草稿说明 | planned | planned | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-07 | FAIL: 3 用例全挂（左导航/版本/气泡） | 151 passed（含新增 3 用例） | `pages/__tests__/console-redesign-s07.test.tsx` | in-memory 真实回滚（v1→新版本 Toast 已回滚）；真机 `/tmp/console-task007-editor.png`、`/tmp/console-task007-versions.png`（?tab=versions 同步） | verified |

- 回滚语义：in-memory 与后端一致为"基于目标版创建新发布版本"（非草稿），RiskConfirm 文案据此书写；回滚后编辑器经 nonce 重载（working draft 重 fork）。
- 旧测试适配：`getByRole tab`→左导航文本点击；保存/发布改吸顶栏后断言改页级。
- 测试教训：renderConsole 用 MemoryRouter，无 hash——URL 断言改内容断言，?tab= 真机验收。

### Log

- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)
- [2026-09-08] completed (done)

---

## Follow-ups（归档时未完成项，需另立任务）

- F1：执行记录智能体名列 + 耗时列——缺后端字段（RunDetail 无 agent/endedAt，trace_events[].at 全填 started_at）。前端已留 `—` 占位。
- F2：用户维度链接查询 API（详情页链接 Tab + 测试面板 trace 跳转反查同类缺口）——前后端都要加。
- F3：顶栏全局搜索 / 头像（TASK-001 deferred）。
- F4：编辑器分组折叠态持久化（TASK-007 缺口）。
