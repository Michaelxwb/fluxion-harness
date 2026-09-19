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

- 主题：默认暗色（`index.html` 的 `body[theme-mode="dark"]` + `theme.ts` 持久化切换）；`ConfigProvider(locale=zh_CN)`；颜色/圆角仅在 `src/styles/app.css` 覆盖 `--semi-color-*` 与 `--app-*` token。
- 图标：统一 `@douyinfe/semi-icons`；Shell/分页等通用图标不得内联 SVG。
- 页面骨架：`PageHeader`（标题+说明）→ `PageSection`（面板）→ `ModuleToolbar`（左上操作/右上筛选）→ `RemoteTable`（含 `PaginationFooter`：显示区间/每页/翻页，默认每页 10）；详情用 `DetailSideSheet`，表单用 `FormModal`，时间用 `DateTimeText`。
- 模块列表页必须使用 `RemoteTable`（禁止手写 `<Table>` + 分页）；详情 Tab 内的局部清单允许直接用 Semi `Table`。
- 表单字段布局以交互稿为准：双列栅格 `.form-grid`、控件同宽、按稿顺序成对、动态字段分区说明；详情基本信息用 `DetailGrid`。
- 新增/编辑表单统一走 `FormModal`（宽 520、标签置顶、确认文案「保存」）；字段补充说明用 `extraText`（如协议、Base URL、API Key、不可改编码），不得用占位符承载说明。
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

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
