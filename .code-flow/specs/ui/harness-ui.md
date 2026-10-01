---
id: harness-ui
description: Agent Harness 通用平台规则：ui
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-ui-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py
      && npm --prefix apps/console-platform/frontend run build
    cwd: .
    timeout: 900
---

# harness-ui

## Rules

- [RULE-ui-001] Console 使用 React + TypeScript + Semi Design；列表页采用“左上操作 + 右上搜索筛选 + 列表 + 右下分页”，不重复页签标题/说明块；主展示字段即详情入口；菜单固定十项：概览/Agent/Skill/MCP/模型/用户/项目平台/后台任务/定时任务/运行审计。

## Conventions

统一视觉基调（对齐参考工程 muad-openclaw console 的暗色 Shell，后续模块直接复用，不得各自二次优化）：

- 主题：默认暗色（`index.html` 的 `body[theme-mode="dark"]` + `theme.ts` 持久化切换）；`ConfigProvider(locale=semiLocaleFor(...))` —— Semi 内建文案（Modal/Popconfirm/表格空态等）随界面语言在 zh-CN/en-US 间切换，**不得写死单一 locale**（词条语言由 react-i18next 持有；`tests/frontend/test_ui_style_contract.py` 明断言不得写死）；颜色/圆角仅在 `src/styles/app.css` 覆盖 `--semi-color-*` 与 `--app-*` token。
- 图标：统一 `@douyinfe/semi-icons`；Shell/分页等通用图标不得内联 SVG。
- 页面骨架：`PageHeader`（标题+说明）→ `PageSection`（面板）→ `ModuleToolbar`（左上操作/右上筛选）→ `RemoteTable`（含 `PaginationFooter`：显示区间/每页/翻页，默认每页 10）；详情用 `DetailSideSheet`，表单用 `FormModal`，时间用 `DateTimeText`。
- 模块列表页必须使用 `RemoteTable`（禁止手写 `<Table>` + 分页）；详情 Tab 内的局部清单允许直接用 Semi `Table`。
- 表单字段布局以交互稿为准：双列栅格 `.form-grid`、控件同宽、按稿顺序成对、动态字段分区说明；详情基本信息用 `DetailGrid`。
- 新增/编辑表单统一走 `FormModal`（宽度按表单复杂度取自交互稿，**不设固定枚举**——现网档位为 480/520/560/620/640/720/800，简单表单 520，含代码/结构化配置的按稿加宽；标签置顶；确认文案**由调用方按动作语义显式传 `okText`**，如「授权」「导入」「保存并发现」，组件兜底为 `common.confirm`）；字段补充说明用 `extraText`（如协议、Base URL、API Key、不可改编码），不得用占位符承载说明。
- Shell：侧栏品牌区 + 图标菜单 + 底部用户区（头像/退出）；顶栏放主题切换与语言切换。

✅ 正确：

```tsx
<>
  <PageHeader title={t('user.title')} description={t('user.subtitle')} />
  <PageSection>
    <ModuleToolbar actions={<Button theme="solid">{t('user.add')}</Button>} search={<Input />} />
    <RemoteTable ... onPageSizeChange={...} />
  </PageSection>
</>
```

```css
/* styles/app.css —— 唯一允许写死颜色的位置 */
body[theme-mode='dark'] { --semi-color-primary: #4d8dff; }
```

❌ 错误：

```tsx
<Card style={{ background: '#fff' }}>                  {/* 页面内写死颜色 */}
  <div style={{ display: 'flex', justifyContent: 'space-between' }}>  {/* 自建工具栏/面板 */}
```

- **`RemoteTable` 只约束「列表页」**：以「左上操作 + 右上搜索筛选 + 列表 + 右下分页」为形态的模块列表页必须用它；**仪表盘/概览类页面**（如 `overview-dashboard` 首页：KPI 卡片 + 若干 ≤5 行预览块，无工具栏/筛选/分页）不受该约束，其区块可用 Semi `Table` 并 `pagination={false}`，但区块内**不得**再用 `RemoteTable`/`ModuleToolbar`。契约测试以「显式声明的非列表页 + 反查其确实不含列表构件」实现，不得靠"不写 RemoteTable"蒙过。
- **菜单 `adminOnly` 恰有一项**：`config/menu.ts` 里只有 `/users` 带 `adminOnly: true`，所以 ADMIN 见 10 项、非 ADMIN 见 9 项；`AppLayout` 用 `menuItems.filter((item) => !item.adminOnly || account?.role === 'ADMIN')` 过滤。菜单项本身只做可见性，权限边界仍由路由侧 `<RequireRole role="ADMIN">` 兜底。机检：`tests/frontend/test_console_shell_contract.py:31-38`。

  ✅：唯一一项 `adminOnly` + 路由守卫

  ```ts
  // config/menu.ts
  { path: '/users', key: 'nav.user', adminOnly: true }
  ```

  ❌：再加第二个 `adminOnly: true`（机检会红，且"非 ADMIN 可见 9 项"的语义被破坏）

- **Shell 不得出现「系统设置」入口**：`config/menu.ts` 与 `layout/AppLayout.tsx` 里连 `setting` 字样都不允许出现（含大小写变体）——防止在菜单来源之外硬编码第二处导航。机检：`tests/frontend/test_console_shell_contract.py:41-46`。
- **`AppLayout` 不得内联第二处导航项数组**：Nav items 只能由 `menuItems` 派生（`menuItems.filter(...)` → `visibleItems.map(...)`）；不得出现 `items={[{ itemKey: '/x', ... }]}` 这类内联数组。机检：`tests/frontend/test_console_shell_contract.py:58-64`。
- **列表页加载必须有请求竞态守卫**：`const requestSeq = useRef(0)`，请求发出前 `++requestSeq.current` 存为局部常量，响应回来先比对 `seq !== requestSeq.current` 即丢弃（不得写入 state）。现网 19 个文件（`agent-management/AgentPage.tsx:30,42-44`、`project-platform/PlatformPage.tsx`、`user-identity/UserPage.tsx`、`skill-management/SkillPage.tsx`、`mcp-management/McpPage.tsx`、`model-management/ModelPage.tsx`、`task-schedule/TaskPage.tsx`、`SchedulePage.tsx`、`SelectedUserTable.tsx`、`PlatformCredentialTab.tsx` 及 `overview-dashboard`/`audit-observability` 的 hooks 等）都已收敛到该形态。
- **列表筛选的生效时机统一为「选择即时、文本回车」**：下拉/日期类筛选**选中即**写入查询参数；关键字文本是**草稿**，只有**回车**才写入查询参数；两者都把页码复位到第 1 页。列表页**没有**独立搜索按钮——筛选控件本身就是搜索入口（对应设计 §3.3.1 的按钮表只有「搜索/筛选」「重置」「刷新」三行）。8 个模块列表页同款：`agent-management/AgentPage.tsx`、`model-management/ModelPage.tsx`、`user-identity/UserPage.tsx`、`skill-management/SkillPage.tsx`、`mcp-management/McpPage.tsx`、`project-platform/PlatformPage.tsx`、`audit-observability/components/AuditFilterBar.tsx`、`task-schedule/{TaskPage,SchedulePage}.tsx`。

  ✅：关键字只改草稿、回车才提交；下拉直接改查询参数

  ```tsx
  const [keywordInput, setKeywordInput] = useState('');

  /** 回车提交：复位页码；值未变则不改 params，避免无谓重查。 */
  const commitKeyword = (): void => {
    setParams((prev) =>
      prev.keyword === keywordInput ? prev : { ...prev, keyword: keywordInput, page: 1 }
    );
  };

  <Input
    value={keywordInput}
    onChange={setKeywordInput}      // 只改草稿，不触发请求
    onEnterPress={commitKeyword}    // 回车才写入查询参数
  />
  <Select onChange={(value) => setParams((prev) => ({ ...prev, enabled: String(value), page: 1 }))} />
  ```

  ❌：`onChange` 里逐键查询（或 300ms 防抖查询）；下拉只改草稿、要再点独立搜索按钮才生效

  ```tsx
  <Input onChange={(text) => setParams((prev) => ({ ...prev, keyword: text, page: 1 }))} />
  <Select onChange={(value) => setFilters((prev) => ({ ...prev, enabled: String(value) }))} />
  <ListActionButton action="search" onClick={applyFilters} />
  ```

  机检：`tests/frontend/test_agent_module_contract.py`、`test_mcp_module_contract.py`、`test_skill_module_contract.py`、`test_platform_page_contract.py::test_page_applies_keyword_on_enter` 断言列表页含 `keywordInput` + `onEnterPress`；`test_audit_gap_contract.py` 断言审计关键字控件绑定草稿并在回车时上抛。`components/common/ListActionButton.tsx` 的 `Action` 类型**不含** `'search'`（放回搜索按钮会直接 typecheck 失败）。
- **列表主展示字段即详情入口，优先复用公共 `EntityLink`**（`components/common/EntityLink.tsx`，`Button theme="borderless"` + `data-testid`）。已复用 8 处；`AgentPage` 的 Agent 名列仍手写同形态 `Button`，属待收敛的技术债——新代码一律优先用 `EntityLink`，不要复制手写版本。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
