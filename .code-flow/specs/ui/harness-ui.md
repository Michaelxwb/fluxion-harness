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

统一视觉基调（后续模块直接复用，不得各自二次优化）：

- 主题：`main.tsx` 用 `ConfigProvider(locale=zh_CN)`；颜色/圆角/阴影只允许在 `src/styles/app.css` 覆盖 `--semi-color-*` 与 `--app-*` token。
- 页面骨架：`PageCard`（容器）→ `ModuleToolbar`（左上操作/右上筛选）→ `RemoteTable`（受控分页）；详情用 `DetailSideSheet`，表单用 `FormModal`，时间用 `DateTimeText`。
- 品牌：标题走 i18n `app.title`，禁止出现旧品牌字样。

✅ 正确：

```tsx
<PageCard>
  <ModuleToolbar actions={<Button theme="solid">{t('user.add')}</Button>} search={<Input />} />
  <RemoteTable ... />
</PageCard>
```

```css
/* styles/app.css —— 唯一允许写死颜色的位置 */
:root { --semi-color-primary: #2563eb; --app-radius: 10px; }
```

❌ 错误：

```tsx
<Card style={{ background: '#fff', borderColor: '#e5e7eb' }}>  {/* 页面内写死颜色 */}
  <div style={{ display: 'flex', justifyContent: 'space-between' }}>  {/* 自建工具栏 */}
```

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
