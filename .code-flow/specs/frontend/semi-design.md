---
id: frontend-semi-design
description: Fluxion 前端 UI 开发强制使用 Semi Design，约束组件库、React 19 适配、样式与交互一致性
stages: [design, plan, code, review]
enforcement: required
verifiers:
  - rule: RULE-frontend-semi-001
    type: manual
    config:
      checklist: 确认页面优先使用 Semi Design 组件，未引入 Ant Design 等第二套通用组件库，并完成 React 19 adapter、可访问性和主题规范检查。
      owner: project-owner
---

# Semi Design 前端规范

## Rules

- [RULE-frontend-semi-001] Fluxion Console Web 与 Chat Web 的通用 UI 组件必须以 Semi Design 为唯一默认组件体系，并遵守本规范。

## 技术基线

- React 19。
- Semi Design：`@douyinfe/semi-ui`。
- 图标：`@douyinfe/semi-icons`。
- React 19 应用入口必须在任何 Semi 组件导入之前执行：

```ts
import '@douyinfe/semi-ui/react19-adapter';
```

- 官方参考：
  - `https://semi.design/zh-CN/start/getting-started`
  - `https://semi.design/zh-CN/ecosystem/react19`
  - 用户提供的 D2C/研发参考：`https://semi.design/code/zh-CN/start/quick-start`

## 强制要求

1. Button、Form、Table、Modal、Toast、Notification、Tabs、Select、Dropdown、Tag、Badge、Tooltip、Pagination、Navigation 等通用能力优先直接使用 Semi 组件。
2. 禁止再引入 Ant Design、Material UI、Element 等第二套通用 UI 组件库，避免视觉、交互和包体重复。
3. 自定义组件必须建立在 Semi 组件和 Design Token 之上；仅当 Semi 不提供能力或业务交互明显特殊时才允许自行实现。
4. 禁止复制 Semi 内部样式源码或通过大量 CSS 覆盖模拟另一套设计语言。
5. 业务页面不得直接写死品牌色、状态色和通用间距；统一通过主题 Token、CSS Variable 或共享样式变量。
6. 表单统一使用 Semi Form 体系；校验信息必须定位到字段，异步提交必须提供 loading/success/error 状态。
7. 列表和管理页优先使用 Semi Table、Pagination、Tag、Tooltip 等标准组件，避免重复造轮子。
8. 删除、回滚、发布、权限变更等高风险动作必须使用 Semi Modal/Popconfirm 类确认交互，并明确展示影响对象与版本。
9. Toast/Notification 只用于短时反馈；关键错误必须在页面内保留可恢复信息，不能只弹 Toast。
10. Console Web 与 Chat Web 可以拥有不同页面布局，但必须共享 Semi Design 主题、基础组件封装和可访问性规范。
11. React 19 adapter 必须在 `main.tsx` 最顶部导入，并由自动化测试/静态检查覆盖。
12. 组件不得通过 DOM class 名等非公开方式依赖 Semi 内部实现。

## 推荐封装

`frontend/packages/shared/` 中允许封装少量 Fluxion 业务基础组件，例如：

```text
shared/
├── ui/
│   ├── ResourceStatusTag
│   ├── VersionBadge
│   ├── PageHeader
│   ├── ConfirmAction
│   └── EmptyState
├── theme/
└── api/
```

这些组件应组合 Semi，而不是替代 Semi。

## Avoid

- 禁止 `antd`、`@ant-design/icons`。
- 禁止页面级重复实现 Button/Form/Table/Modal/Toast。
- 禁止为了快速还原页面而大量内联样式。
- 禁止绕过 Semi 的受控表单模型自行维护重复表单状态。
- 禁止在没有设计评审的情况下改写全局 Semi 主题语义。
