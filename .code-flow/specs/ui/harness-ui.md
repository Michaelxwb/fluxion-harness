---
id: harness-ui
description: Console 视觉与列表
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

- [RULE-ui-001] Console 使用 React + TypeScript + Semi Design；列表页采用“左上操作 + 右上搜索筛选 + 列表 + 右下分页”，不重复页签标题/说明块；主展示字段即详情入口；菜单固定十一项：概览/Agent/Skill/MCP/模型/用户/项目平台/后台任务/定时任务/运行审计/系统设置。

## Conventions

统一视觉基调（对齐参考工程 muad-openclaw console 的暗色 Shell，后续模块直接复用，不得各自二次优化）：

- 主题：默认暗色（`index.html` 的 `body[theme-mode="dark"]` + `theme.ts` 持久化切换）；`ConfigProvider(locale=semiLocaleFor(...))` —— Semi 内建文案（Modal/Popconfirm/表格空态等）随界面语言在 zh-CN/en-US 间切换，**不得写死单一 locale**（词条语言由 react-i18next 持有；`tests/frontend/test_ui_style_contract.py` 明断言不得写死）；颜色/圆角仅在 `src/styles/app.css` 覆盖 `--semi-color-*` 与 `--app-*` token。
- 图标：统一 `@douyinfe/semi-icons`；Shell/分页等通用图标不得内联 SVG。
- 页面骨架：`PageHeader`（标题+说明）→ `PageSection`（面板）→ `ModuleToolbar`（左上操作/右上筛选）→ `RemoteTable`（含 `PaginationFooter`：显示区间/每页/翻页，默认每页 10）；详情用 `DetailSideSheet`，表单用 `FormModal`，时间用 `DateTimeText`。
- 模块列表页必须使用 `RemoteTable`（禁止手写 `<Table>` + 分页）；详情 Tab 内的局部清单允许直接用 Semi `Table`。
- 表单字段布局与详情基本信息栅格的完整口径见 `harness-ui-detail.md`（本 spec 不重复定义）。
- 新增/编辑表单统一走 `FormModal`（宽度按表单复杂度取自交互稿，**不设固定枚举**——现网档位为 480/520/560/620/640/720/760，简单表单 520，含代码/结构化配置的按稿加宽；标签置顶；确认文案**由调用方按动作语义显式传 `okText`**，如「授权」「导入」「保存并发现」，组件兜底为 `common.confirm`）；字段补充说明用 `extraText`（如协议、Base URL、API Key、不可改编码），不得用占位符承载说明。
- Shell：侧栏品牌区 + 图标菜单 + 底部用户区（头像/退出）；顶栏放主题切换与语言切换。
- **业务操作按钮统一蓝色**：列表、详情、表单与设置页的操作按钮使用 Semi `type="primary"`（默认类型也为 primary），边框/背景由 `theme` 决定；删除、解绑、撤销、取消任务、清空等确认操作也统一该颜色，悬停不切换成红色。`ConfirmAction`、`FormModal` 的确认/取消操作遵循同一口径，禁用态使用 Semi 内建样式。

  ✅：`<Button type="primary" theme="borderless">...</Button>`；`ConfirmAction` 统一 primary，并保留操作确认。

  ❌：删除常态灰色、悬停红色；详情关系操作或设置页重置/恢复按钮使用 tertiary 灰色。

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

- **`RemoteTable` 只约束「列表页」**：以「左上操作 + 右上搜索筛选 + 列表 + 右下分页」为形态的模块列表页必须用它；**仪表盘/概览类页面**（如 `overview-dashboard` 首页：资源摘要、KPI 与图表，允许时间范围/刷新控制，无列表分页）不受该约束，其指标区块使用共享 PageSection/MetricCards，图表使用主题化 ECharts，但区块内**不得**再用 `RemoteTable`/`ModuleToolbar`。契约测试以「显式声明的非列表页 + 反查其确实不含列表构件」实现，不得靠"不写 RemoteTable"蒙过。仪表盘按可用高度自适应：header/状态/KPI 固定，图表区 flex 填充；桌面 1440×900/1280×800 不得出现 `.app-content` 纵向滚动；ECharts 容器用 ResizeObserver，禁止固定像素画布（✅ 图表随容器收缩；❌ 固定 240px 画布导致一屏溢出；`test_overview_page_contract.py`、overview 组件 S-06）。
- **WebChat 消息渲染只走白名单**：模型正文用白名单 Markdown 解析（标题/列表/引用/表格/代码/行内标记），不执行 HTML/MDX、不写 `dangerouslySetInnerHTML`；链接仅 `http/https/mailto`。✅ `MessageThread` 的 `parseMessageBlocks`/`safeLinkHref`；❌ 直接渲染模型 HTML，或放开相对/`data:` 链接（`test_capacity_webchat_message_history_ui.py`）。
- **WebChat 布局、流式与未读**：消息列/线程/列表/项必须 `flex: 0 0 auto`，Markdown 列表用独立 `chat-message-markdown-list`，不得复用消息列表类；流式正文固定节拍平滑（当前 33ms，不用 rAF）；COMPLETED/FAILED 终态先重读历史再收敛流式，CANCELLED 保留停止态（刷新会移除运行面）；通知面板不再引入，未读只保留侧栏标记，活跃且可见会话自动已读；抽屉开关常驻，桌面默认展开、窄屏默认收起。✅ 长回复不与上一条重叠、流式跟随底部；❌ 用消息列表类渲染 Markdown、Run 结束后只清流式不重读历史（`test_capacity_webchat_webchat_layout.py`、E2E S-103/S-06）。
- **菜单 `adminOnly` 恰有两项**：`config/menu.ts` 里 `/users` 与 `/settings` 带 `adminOnly: true`，所以 ADMIN 见 11 项、非 ADMIN 见 9 项；`AppLayout` 用 `menuItems.filter((item) => !item.adminOnly || account?.role === 'ADMIN')` 过滤。菜单项本身只做可见性，权限边界仍由路由侧 `<RequireRole role="ADMIN">` 兜底。机检：`tests/frontend/test_console_shell_contract.py::test_console_shell_menu_is_the_fixed_eleven_items`。

  ✅：两项 `adminOnly`（`/users`、`/settings`）+ 各自路由守卫

  ```ts
  // config/menu.ts
  { path: '/users', key: 'nav.user', adminOnly: true }
  { path: '/settings', key: 'nav.settings', adminOnly: true }
  ```

  ❌：再加第三项 `adminOnly: true`（机检会红，且"非 ADMIN 可见 9 项"的语义被破坏）

- **「系统设置」入口只能来自 `config/menu.ts`**：`/settings` 菜单项必须由 `menuItems` 派生（不在 `AppLayout` 里硬编码第二处导航），路由在 `App.tsx` 受 `<RequireRole role="ADMIN">` 守卫；`AppLayout` 的 `MENU_ICONS` 登记对应 `semi-icons` 图标。机检：`tests/frontend/test_console_shell_contract.py::test_console_shell_has_system_settings_entry`。
- **`AppLayout` 不得内联第二处导航项数组**：Nav items 只能由 `menuItems` 派生（`menuItems.filter(...)` → `visibleItems.map(...)`）；不得出现 `items={[{ itemKey: '/x', ... }]}` 这类内联数组。机检：`tests/frontend/test_console_shell_contract.py::test_layout_renders_menu_items_with_role_filter`。
- **列表页加载必须有请求竞态守卫**：`const requestSeq = useRef(0)`，请求发出前 `++requestSeq.current` 存为局部常量，响应回来先比对 `seq !== requestSeq.current` 即丢弃（不得写入 state）。现网 30 个文件（`agent-management/AgentPage.tsx`、`project-platform/PlatformPage.tsx`、`user-identity/UserPage.tsx`、`skill-management/SkillPage.tsx`、`mcp-management/McpPage.tsx`、`model-management/ModelPage.tsx`、`task-schedule/TaskPage.tsx`、`SchedulePage.tsx`、`SelectedUserTable.tsx`、`PlatformCredentialTab.tsx` 及 `overview-dashboard`/`audit-observability` 的 hooks 等）都已收敛到该形态。
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
- **列表主展示字段即详情入口，复用公共 `EntityLink`**（`components/common/EntityLink.tsx`，`Button theme="borderless"` + `data-testid`），包括 Agent 名称列；不要复制手写同形态按钮。
- **主列表列宽随可用空间分配**：Agent、Skill、MCP、模型、用户、项目平台、后台任务、定时任务、运行审计统一使用 `.app-table-fit`；短 ID、时间和操作列紧凑定宽，其他字段按内容设置最小宽度并分配剩余空间，`scroll.x: '100%'` 与固定表格布局保证桌面铺满。Agent 以 980px 为基准，通过 `RemoteTable.columnGrowth` 与 `DataTable` 已有的容器尺寸观察，按实际可用宽度分配像素列宽；名称/标识/模型共同分配新增空间的 52%，六个计数/状态/版本字段分配其余 48%，避免前面三列独占空白；其他列表的长字段使用 `width: 'auto'`。长文本单行省略，React 渲染的文本复用 `TableText` 或 `CopyableText` 提供全文 `title`；独立时间列、状态标签、复制图标和操作按钮必须完整可见。默认最小表宽 960px，Agent 为 980px；MCP/项目平台为 1280px，后台任务/定时任务为 1440px，空间不足时保留横向滚动与固定操作列。

  ✅：1920/2048px 桌面所有主列表均铺满且无横向滚动；Agent 的名称/标识/模型合计不超过表宽 35%，计数/状态/版本也有充分间距。Agent、Skill、模型、用户、审计在 1280/1440px 也无需滚动，窗口缩放后重新铺满。`e2e/tests/model-list-layout.spec.ts` 与 `e2e/tests/list-layout.spec.ts` 验证长短文本、完整时间/状态/操作、浅色/深色主题、英文表头及窄屏固定操作列。

  ❌：所有短字段列默认分配 176px，导致宽屏也需要滚动才能看到状态、版本和更新时间；隐藏滚动条却让列继续溢出；Agent 只有名称/标识/模型伸展，在宽屏占据一半以上表宽。

- **审计列表以 Trace ID 为首列主展示字段**：列序为 Trace ID / 时间 / 审计类型 / 操作用户 / Agent / 关联对象 / 动作 / 执行结果。用户与 Agent 紧凑定宽，Trace ID、关联对象与动作共同分配剩余宽度；Trace 旁复用 `CopyButton` 复制完整值。Trace ID 标识调用链，可对应多条记录，行标识仍取 `auditId`，点击打开当前行审计详情；Trace 缺失时明确显示真实「审计 ID」并保持详情入口与复制。关联 Run/Task 继续直达对应实体。

  ✅：同一 Trace 的多条审计分别显示、分别打开；宽屏展示完整 UUID，操作用户/Agent 列不占大量空白。`e2e/tests/list-layout.spec.ts` 验证列序/列宽/空 Trace 展示，审计 S-06/S-07 验证真实行身份、复制与详情。

  ❌：把 Trace ID 当行主键，导致同链路记录合并；点击复制图标同时打开详情；无 Trace 时伪造 Trace 值。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
