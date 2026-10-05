# 平台系统设置 前端模块需求与设计简报

> 后端设计见同目录 `platform-settings.backend.design.md`（API-01..API-06 的定义在那里）。
> 本需求 = `context-compaction` 划出的**需求二（Console 系统设置页）** ∪ 2026-10-04 全仓配置盘点。

## 目录

- [1. 文档控制](#1-文档控制)
- [2. 需求分析](#2-需求分析)
  - [2.1 需求概述](#21-需求概述-必填)
  - [2.2 功能方案](#22-功能方案-必填)
  - [2.3 范围与边界](#23-范围与边界-必填)
  - [2.4 验收条件](#24-验收条件-必填)
- [3. 前端技术设计](#3-前端技术设计)
  - [3.1 技术选型](#31-技术选型-必填)
  - [3.2 页面与路由结构](#32-页面与路由结构-必填)
  - [3.3 组件设计](#33-组件设计-必填)
  - [3.4 组件接口契约](#34-组件接口契约-必填)
  - [3.5 状态与数据流](#35-状态与数据流-必填)
  - [3.6 UI 状态](#36-ui-状态-必填)
  - [3.7 样式方案](#37-样式方案-必填)
  - [3.8 可访问性与兼容性](#38-可访问性与兼容性-按需)
- [4. 风险与依赖](#4-风险与依赖-按需)
- [Spec Compliance Matrix](#spec-compliance-matrix)
- [附录：术语表](#附录术语表)

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 产品经理 | fluxion-harness | 需求定义、交互验收 |
| 开发负责人 | fluxion-harness | 前端方案确认 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v0.1 | 2026-10-04 | fluxion-harness | 初始草稿：系统设置页 + 菜单/路由/契约测试同步 |
| v0.2 | 2026-10-05 | fluxion-harness | 布局重构：折叠手风琴改为「左分组导航 + 右单分组面板」，撤双横幅与只读说明卡片，字段行收成单行且说明与标签同行、压缩行高（§3.2/3.3/3.4/3.6/3.7 同步改写） |

---

## 2. 需求分析

### 2.1 需求概述 [必填]

| 项目 | 内容 |
|------|------|
| **模块名称** | 平台系统设置（Console 系统设置页） |
| **需求类型** | 新页面（含菜单/路由新增与既有契约测试改写） |
| **业务背景** | Console 目前**没有**系统设置的菜单、路由与页面：`src/config/menu.ts:7-18` 固定十项、`tests/frontend/test_console_shell_contract.py:41-46` 明确断言"不含系统设置入口"。平台业务默认只能改代码或逐 Agent 配置。 |
| **核心目标** | 让管理员在一个页面上看清「哪些能改、改了什么时候生效、资源是否已覆盖」，并在保存后立即对**新**操作生效。 |

---

### 2.2 功能方案 [必填]

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|--------|---------|---------|--------|------|
| FEAT-04 | Console 系统设置页 | 分组表单页：展示平台默认/资源覆盖/生效方式，保存带版本乐观并发，可看历史并回滚 | P0 | US-01, US-02, US-03 |
| FEAT-08 | 菜单/路由/契约测试同步 | 菜单由固定十项改为十一项、新增路由与角色守卫、`shell` 契约测试按新事实改写 | P1 | US-01 |

---

### 2.3 范围与边界 [必填]

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | ① 菜单新增「系统设置」（`adminOnly`）；② 路由 `/settings` + `RequireRole role="ADMIN"`；③ 分组设置表单（含每项的默认值/覆盖情况/生效方式提示）；④ 保存（版本乐观并发 + 字段级错误定位）；⑤ 版本历史列表与回滚；⑥ 只读项与"不在这里改"的项的归属说明；⑦ 中英词条；⑧ 既有 `shell` 契约测试改写为十一项 + 新增入口的正向断言 |
| **非范围（Out of Scope）** | ① 前端持有任何校验规则或默认值副本（全部来自 API）；② 富文本/自由 JSON 编辑框；③ 修改 Agent/模型/MCP/项目平台资源的配置表单（仍在各自页面）；④ 部署参数（`.env`/Secret/k8s）的编辑入口；⑤ 上下文膨胀可视化看板；⑥ 把前端构建期 `VITE_*` 变量运行时化 |
| **有意妥协 / 技术债** | ① **整份保存**：提交的是改动后的整份设置文档（后端设计 ADR-09），并发编辑同一份设置时后提交者收到版本冲突并需重新加载——不做字段级合并；② 不做"未保存即离开"的拦截弹窗（本次只做提示条），列入后续；③ 非敏感限额（`API-05`）本次只被 Skill 导入处消费，其余页面沿用其服务端返回，不在本页展示 |

---

### 2.4 验收条件 [必填]

> 场景 ID 与后端设计共用同一编号空间（后端定义 S-01..S-03、E-01..E-10、E-17、B-01..B-04、B-07）。本页只列与前端交互相关的场景，并新增 E-11..E-14 / B-05 / B-06；S-01 与 S-03 是与后端共用的同一条场景，最终负责人写在前端任务上。

**正常场景**

| 场景ID | 功能ID | 优先级 | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|--------|--------|--------|---------|-------------|---------|-------------|
| S-01 | FEAT-04 | P0 | E2E | 真实浏览器（Playwright，成功路径**无路由拦截**）→ 真实 Console API → 真实 PostgreSQL → 真实 Runtime → 真实模型探针 | 1. 以 ADMIN 登录并进入系统设置页；2. 展开「上下文压缩」分组；3. 修改 `snip.max_groups`；4. 点击保存；5. 在 IM/运行侧触发一条新消息产生新 Run | 保存后页面显示新版本号与"已保存"提示；后续新 Run 使用新值（由后端 E2E 断言）；保存前的执行中 Run 不受影响（页面提示"执行中的任务不受影响"） |
| S-03 | FEAT-08 | P0 | E2E | 真实浏览器 + 真实登录会话与角色（无路由拦截） | 1. 以 ADMIN 登录 → 观察菜单；2. 以 BUILDER 登录 → 观察菜单与直接访问 `/settings`；3. 以另一租户 ADMIN 登录 | ADMIN 菜单出现「系统设置」且在**运行审计之后**；BUILDER 菜单**不出现**该入口且直接访问被拒（跳转/无权限页）；另一租户 ADMIN 看不到本租户的设置值 |

**异常场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|--------|--------|---------|-------------|---------|---------|
| E-11 | FEAT-04 | integration | 真实路由 + 真实 HTTP 服务层（后端校验真实生效） | 提交破坏联动的组合（如 `snip.max_groups` 小于保留头尾组数之和 + 1） | 保存失败；错误**定位到具体字段行**（红框 + 字段级文案），当前值不被覆盖；页面保留用户输入便于修正 |
| E-12 | FEAT-04 | integration | 真实路由 + 真实 HTTP 服务层（真实 409） | 保存时后端返回版本冲突 | 明确提示"设置已被他人修改"，提供「重新加载」按钮；**不**静默重试、不覆盖 |
| E-13 | FEAT-04 | integration | 真实路由 + 真实 HTTP 服务层（真实 5xx/超时） | 读取设置接口失败 | 页面进入错误态：错误文案 + 「重试」按钮；不显示任何默认值（避免把"没读到"伪装成"当前值"） |
| E-14 | FEAT-04 | integration | 真实路由 + 真实 HTTP 服务层（真实 403） | BUILDER 会话直接访问 `/settings` | 跳转到无权限页/被路由守卫拦截，不发设置请求 |

**边界场景**

| 场景ID | 测试层级 | 关键真实边界 | 字段/条件 | 边界值 | 预期行为 |
|--------|---------|-------------|----------|--------|---------|
| B-05 | unit | 表单状态 hook（纯逻辑） | 有无未保存改动 | 未改动后再次点击保存 / 改动后路由离开 | 无改动时保存按钮禁用；`updated_by` 与版本号显示当前版本而非乐观值 |
| B-06 | unit | 生效方式标签映射（纯函数） | `applies_to` 取值 | `new_run`/`new_task`/`next_operation`/`restart_required`/`code` | 分别渲染为「新会话生效」/「新任务生效」/「下一步操作生效」/「需重启服务」/「随代码发布」，无未知值兜底文案 |

**非功能指标** [按需]

| 指标ID | 指标名称 | 目标值 | 测量方法 |
|--------|---------|-------|---------|
| NFR-PERF-03 | 设置页首屏 | 单次 `GET /api/v1/platform-settings` 后渲染完成（无 N+1 请求） | 网络面板断言：进入页面只发 1 个设置读取请求 |

---

## 3. 前端技术设计

### 3.1 技术选型 [必填]

| 类别 | 选型 | 版本 | 选型理由 |
|------|------|------|---------|
| 框架 | React + TypeScript | 18 / 5.7 | 与 Console 现有栈一致（`apps/console-platform/frontend/package.json`） |
| UI 库 | `@douyinfe/semi-ui` + `semi-icons` | 既有版本 | 复用 `Form`/`Switch`/`InputNumber`/`Select`/`SideSheet`/`Banner`/`Toast`，不引入新组件库 |
| 状态管理 | 无全局库；页面局部状态 + 自定义 hook | — | 仓库既无 redux/zustand/react-query，状态仅 `AuthContext`；设置页是单页自洽表单，不值得引入全局 store |
| 路由 | `react-router-dom` v6 | 既有版本 | 与 `src/App.tsx` 现有路由层级一致 |
| 样式方案 | Semi Design 组件 + 既有全局样式 | — | 不新增设计 token（`harness-ui` 既有约定） |
| 数据请求 | 共享 axios 实例（`src/api/client.ts`）+ `modules/settings/services/*` | — | 强约束：组件禁止裸用 axios/fetch（`RULE-front-001`） |
| i18n | `i18next` / `react-i18next` | 既有版本 | 词条进 `src/locales/{zh-CN,en-US}.json`（`RULE-i18n-001`） |

---

### 3.2 页面与路由结构 [必填]

| 页面 | 路由 | 布局 | 说明 |
|------|------|------|------|
| 系统设置 | `/settings` | `AppLayout` 主内容区（左分组导航 + 右单分组面板，非列表页） | 新增；`src/App.tsx` 中包 `RequireRole role="ADMIN"` |

**菜单变更（`src/config/menu.ts`）**

| 变更 | 内容 |
|------|------|
| 顺序 | 追加为第 **11** 项，排在「运行审计」之后 |
| 条目 | `{ path: "/settings", key: "nav.settings", adminOnly: true }` |
| 图标 | 在 `src/layout/AppLayout.tsx` 的 `MENU_ICONS` 中登记（`semi-icons` 既有图标） |
| 规范同步 | `RULE-ui-001` 的"菜单固定十项"必须**如实改写为十一项**并在枚举中列出「系统设置」（不留旧文案） |

**契约测试改写（`tests/frontend/test_console_shell_contract.py`）**

| 既有断言 | 新事实 |
|----------|--------|
| `EXPECTED_KEYS` 十项（`:9-20`） | 十一项，末项 `nav.settings` |
| `test_console_shell_menu_is_the_fixed_ten_items`（`:27-38`） | 更名并断言十一项固定顺序；`adminOnly` 项由 1 个（`/users`）变为 **2 个**（`/users`、`/settings`） |
| `test_console_shell_has_no_system_settings_entry`（`:41-46`） | **删除**该反向断言，替换为正向断言：菜单与 `AppLayout` 必须含系统设置入口，且路由受 `RequireRole role="ADMIN"` 守卫 |

> 事实性文档同步（`docs/00-详细设计索引与设计基线.md:210`、`docs/README.md:24` 现写着"不提供系统设置菜单"）在 FEAT-08 内一并改写。

---

### 3.3 组件设计 [必填]

**组件树**（容器/展示分离；v0.2 起为单面板布局）

```
SettingsPage                       # 容器：取数 + 保存 + 错误定位 + 当前分组切换
├─ SettingsHeader                  # 展示：当前版本 / 内置默认提示 / 保存者 / 保存时间 / 保存与重置按钮
├─ SettingsNotice                  # 展示：生效语义一行浅色说明（非横幅）
├─ SettingsGroupNav                # 展示：左侧分组导航（页面内联组件，分组来自元数据）
├─ SettingsGroupPanel（当前分组）   # 展示：分组标题 + 生效方式标签 + 覆盖提示 + 字段列表
│  └─ SettingsFieldRow[]           # 展示：单个设置项（单行：标签+参考信息 | 控件）
│     └─ OverrideBadge             # 展示：「已被 N 个资源覆盖」（仅在偏离分组/有覆盖时）
├─ ReadonlyNotesPanel              # 展示：页头帮助气泡内容（不在此页管理的项 + 归属说明）
└─ VersionHistoryPanel             # 容器：历史分页 + 回滚确认（SideSheet）
   └─ VersionRow[]                 # 展示：版本号 / 时间 / 操作者 / 变更键
```

> **为什么是单面板**：九个分组的手风琴把页面撑成一列「只占地方不显内容」的折叠条，加上两条
> 横幅与只读说明卡片，纵向极深而信息密度极低。左导航 + 单面板让页面深度恒定（只随当前分组
> 的字段数变化），保存失败的字段错误若落在非当前分组，容器自动切到第一个出错分组保证可见。

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|--------|--------|------|--------------|------|
| CMP-01 | `SettingsPage` | 容器 | 新建 | 取数、表单状态、保存、错误映射到字段、版本冲突处理、当前分组切换（错误自动切组） |
| CMP-02 | `SettingsHeader` | 展示 | 新建 | 版本/内置默认提示/保存者/时间展示；保存/重置/查看历史按钮（事件上抛） |
| CMP-03 | `SettingsNotice` | 展示 | 新建 | 生效语义一行浅色说明（非横幅） |
| CMP-04 | `SettingsGroupPanel` | 展示 | 新建 | 右侧单分组面板：分组标题、生效方式标签、组级覆盖提示、字段列表 |
| CMP-05 | `SettingsFieldRow` | 展示 | 新建 | 单行布局：标签与参考信息（默认值/单位/范围）同行在左，控件在右；生效方式仅在偏离分组时标注 |
| CMP-06 | `OverrideBadge` | 展示 | 新建 | 「已被 N 个资源覆盖」提示；无覆盖渲染 null（常态不通告） |
| CMP-07 | `ReadonlyNotesPanel` | 展示 | 新建 | 页头帮助气泡内容：非本页项及其归属（`.env`+重启 / 资源页面 / 代码） |
| CMP-08 | `VersionHistoryPanel` | 容器 | 新建 | 历史分页读取、选中版本、回滚二次确认 |
| CMP-09 | `VersionRow` | 展示 | 新建 | 单条历史行 |

**分层纪律**：只有 CMP-01 与 CMP-08 触达服务层；其余组件纯 props in / events out。字段渲染的取值与范围来自 API 返回的元数据，**组件内不得写默认值字面量**（避免前端成为第二套默认源）。

---

### 3.4 组件接口契约 [必填]

**CMP-04 `SettingsGroupPanel`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `group` | `SettingsGroupMeta` | Y | — | `{ key, labelKey, appliesTo, readonly?, fields[] }` |
| `values` | `Record<string, unknown>` | Y | — | 当前表单值（按字段路径扁平化） |
| `errors` | `Record<string, string>` | Y | — | 字段路径 → 错误文案 |
| `disabled` | `boolean` | N | `false` | 无写权限/保存中 |

| Events / 回调 | 载荷类型 | 触发时机 |
|--------------|---------|---------|
| `onFieldChange` | `(path: string, value: unknown)` | 任一字段变更 |

**CMP-05 `SettingsFieldRow`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `meta` | `SettingsFieldMeta` | Y | — | `{ path, labelKey, type, defaultValue, min?, max?, enum?, unitKey? }` |
| `groupAppliesTo` | `AppliesTo` | Y | — | 所在分组的生效方式；字段级标签仅在 `meta.appliesTo` 偏离分组时展示 |
| `value` | `unknown` | Y | — | 当前值 |
| `error` | `string \| undefined` | N | — | 字段级错误（来自 `VALIDATION_FAILED` 的 `details[].path`） |

| Events / 回调 | 载荷类型 | 触发时机 |
|--------------|---------|---------|
| `onChange` | `(value: unknown)` | 控件值变更 |

**CMP-08 `VersionHistoryPanel`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| `currentRevision` | `number` | Y | — | 当前版本 |
| `visible` | `boolean` | Y | — | SideSheet 显隐 |

| Events / 回调 | 载荷类型 | 触发时机 |
|--------------|---------|---------|
| `onRestore` | `(revision: number) => void` | 用户在二次确认后确认回滚 |
| `onClose` | `() => void` | 关闭 |

> 组件内**禁止直接修改 props**；改值一律通过事件上抛（`RULES`：容器持有状态）。

---

### 3.5 状态与数据流 [必填]

**状态划分**

| 状态 | 作用域 | 形状（shape） | 读写方 |
|------|--------|--------------|--------|
| `meta`（服务端返回的分组/字段/默认/范围/覆盖数/只读说明） | local（`SettingsPage`） | `{ revision, groups, readonlyNotes }` | 读取 hook 写、展示组件读 |
| `formValues`（用户编辑中的值，按字段路径扁平化） | local | `Record<string, unknown>` | 字段变更写、字段行读 |
| `baseValues`（加载时的基准值，用于脏判断与重置） | local | 同 `formValues` | 加载时写、重置/脏判断读 |
| `fieldErrors` | local | `Record<string, string>` | 保存失败写、字段行读 |
| `saving` / `conflict` | local | `boolean` | 保存流程读写 |

不引入全局 store；不把设置值放进 `AuthContext` 之类的既有上下文。

**数据流**

```
进入页面 → usePlatformSettings() → GET /api/v1/platform-settings
   → meta（含每项默认/范围/覆盖数）
   → formValues ← baseValues
用户改字段 → onFieldChange → formValues（局部重渲染 + 脏标记）
点击保存 → PUT /api/v1/platform-settings {revision, settings}
   ├─ 成功 → 更新 meta.revision，baseValues = formValues，Toast「已保存，新操作立即生效」
   ├─ 400 VALIDATION_FAILED → fieldErrors（按 details[].path）
   ├─ 409 VERSION_CONFLICT → conflict 提示 + 「重新加载」
   └─ 403/5xx → 错误提示 + 重试（不修改 formValues）
查看历史 → useSettingsRevisions() → GET /api/v1/platform-settings/revisions（分页）
回滚 → POST /api/v1/platform-settings/revisions/{revision}/restore → 成功后重新加载 meta
```

**数据获取层**：全部经 `modules/settings/services/`，组件与 hook 不出现裸 `fetch`/`axios`。

| Service 方法 | 对应后端接口 | 调用方组件/hook |
|-------------|-------------|----------------|
| `getPlatformSettings()` | `GET /api/v1/platform-settings` | `usePlatformSettings` → CMP-01 |
| `savePlatformSettings({revision, settings})` | `PUT /api/v1/platform-settings` | `useSavePlatformSettings` → CMP-01 |
| `listSettingsRevisions({page, page_size})` | `GET /api/v1/platform-settings/revisions` | `useSettingsRevisions` → CMP-08 |
| `restoreSettingsRevision(revision)` | `POST /api/v1/platform-settings/revisions/{revision}/restore` | `useRestoreSettingsRevision` → CMP-08 |

> 返回的列表按统一封套 `{items,page,page_size,total}` 解析（`RULE-api-001`）。

---

### 3.6 UI 状态 [必填]

| 视图/交互 | loading | empty | error | success |
|----------|---------|-------|-------|---------|
| 设置页主体 | 分组骨架屏 | 不适用（字段来自 schema 元数据，恒有内容）；`revision=0` 时页头出现琥珀色「当前使用平台内置默认值，尚未保存过」小标签 | 「加载失败」+ 重试按钮；**不渲染任何值** | 左导航 + 当前分组面板 + 页头版本信息 |
| 保存按钮 | 按钮内转圈并禁用 | — | 字段级红字（错误落在非当前分组时容器自动切组）+ 冲突横幅 | Toast「已保存」+ 版本号更新 |
| 版本历史 | 列表骨架 | 「暂无历史版本」 | 错误文案 + 重试 | 分页列表 + 回滚入口 |
| 覆盖提示（CMP-06） | — | 无覆盖**不渲染**（常态不通告） | — | 分组头部显示「已被 N 个资源覆盖」 |
| 只读说明（CMP-07） | — | — | — | 页头帮助气泡（点击展开，不常驻占位） |
| 路由守卫 | 会话校验中骨架 | — | 无权限页 | 正常渲染 |

---

### 3.7 样式方案 [必填]

- **设计 token**：不新增；沿用 Semi Design 主题变量与既有全局样式（settings 样式段在 `styles/app.css`，全部走 `--semi-color-*` / `--app-radius-*` 变量）。
- **布局**：单卡片内的「左分组导航（176px）+ 右单分组面板」；字段行单行排布——标签与浅色参考信息（默认值/单位/范围）同行在左、控件在右，参考信息过长时自然换行；分组主体限宽 860px 保持可读行长；窄屏（≤768px）导航转横向换行、字段行纵向堆叠。
- **样式与逻辑分离**：不写内联魔法值；必要的布局类走既有样式文件约定。
- **文案**：全部走 i18n 词条（`nav.settings`、`settings.*`、含 B-06 的五个生效方式标签），zh-CN/en-US 同步新增（`RULE-i18n-001`）。
- **列表页规范不适用**：本页不是列表页，`harness-ui` 的「左上操作 + 右上搜索筛选 + 列表 + 右下分页」不套用；版本历史作为 SideSheet 内的小列表使用分页组件。

---

### 3.8 可访问性与兼容性 [按需]

- 每个字段控件有可关联的 `label`（Semi `Form` 的 `label` + 字段路径 `name`），错误文案与字段用 `aria-describedby` 关联。
- Switch/Switch 类布尔项键盘可达；保存/回滚按钮有明确 `loading`/`disabled` 状态。
- 回滚是破坏性操作：二次确认对话（Semi `Modal.confirm`），文案说明"会生成新版本，历史不删除"。
- 浏览器兼容：沿用 Console 既有基线（Chromium/Firefox/Safari 最近两个大版本）。

---

## 4. 风险与依赖 [按需]

| 风险ID | 描述 | 影响 | 缓解方案 |
|--------|------|------|---------|
| RISK-FE-01 | 前端复制一份默认值/范围 | 出现第二套默认源，页面与服务端漂移 | 默认值、范围、枚举全部来自 API；组件禁止写默认值字面量；B-06 只做标签映射 |
| RISK-FE-02 | 表格/表单把"读取失败"渲染成默认值 | 管理员误以为当前就是默认值 | E-13 明确要求错误态**不渲染值** |
| RISK-FE-03 | 版本冲突被静默重试覆盖他人改动 | 变更丢失、不可信 | E-12：冲突即失败 + 重新加载，绝不自动重试 |
| RISK-FE-04 | 既有 shell 契约测试写死"十项且无系统设置" | 新页面一加就红，容易被绕过（删测试） | 按新事实施改：断言十一项 + 正向断言入口存在 + 路由守卫；不保留反向断言 |
| RISK-FE-05 | `RULE-ui-001` 规范未同步 | 规范与代码事实不一致 | FEAT-08 内改写规范并登记为规范变更 |

---

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景/verifier | 状态 |
|---|---|---|---|---|---|
| harness-frontend#RULE-front-001 | required | 设置页所有 HTTP 调用只经 `modules/settings/services/*`（import 共享 `src/api/client.ts` 实例）；组件与 hook 不裸用 axios/fetch | 3.5 数据获取层 | S-03、E-11；verifier `harness-frontend#RULE-front-001` | applied |
| harness-ui#RULE-ui-001 | required | 菜单由固定十项改为**十一项**并如实改写该规则；本页不是列表页故不套用列表页布局；详情/历史用 SideSheet | 3.2 菜单变更、3.7 样式方案 | S-03；verifier `harness-ui#RULE-ui-001` | applied |
| harness-i18n#RULE-i18n-001 | required | 页面与错误文案全部走 zh-CN/en-US 词条，不改框架代码；`locale.default_locale` 为业务设置项 | 3.7 样式方案（文案段） | B-06、E-09 | applied |
| harness-api#RULE-api-001 | required | 前端按统一封套解析设置读取与版本列表（列表走 `{items,page,page_size,total}`）；错误提示文案取自服务端错误码语义 | 3.5 数据流、3.6 UI 状态 | E-11、E-12、E-13 | applied |
| harness-auth#RULE-auth-001 | required | 路由与菜单按 ADMIN 角色守卫；页面不展示任何未授权的资源/凭据信息 | 3.2 路由结构、3.6 UI 状态（路由守卫态） | S-03、E-14 | applied |
| harness-test#RULE-test-001 | required | 前端分层：纯逻辑 hook/映射用 unit，服务层与错误映射用 integration，跨路由与服务请求的用户流程用真实浏览器 E2E | 2.4 验收条件 | S-01、S-03、E-11、E-12、E-13、E-14、B-05、B-06 | applied |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| 平台默认 | 平台级业务设置取值，作用于所有未显式覆盖的资源 |
| 资源覆盖 | Agent/模型/MCP/项目平台/Schedule 上显式保存的配置，优先于平台默认 |
| 生效方式（`applies_to`） | 本项改动何时生效：新会话 / 新任务 / 下一步操作 / 需重启服务 / 随代码发布 |
| revision | 平台设置版本号，按租户单调递增 |
| 统一封套 | `{code,msg,data,trace_id,request_id,timestamp}`；列表为 `{items,page,page_size,total}` |

---

*文档结束*
