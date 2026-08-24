# 前端导航地图

> Fluxion 前端统一采用 React 19 + TypeScript + Vite + Semi Design。当前仓库已有 Console、Chat、shared 三个 package 骨架，业务源码尚未落地。

## 技术基线

- Framework：React 19
- Language：TypeScript 5.x
- Build：Vite
- UI：Semi Design（`@douyinfe/semi-ui` + `@douyinfe/semi-icons`）
- Routing：React Router
- State：局部状态优先；跨页面/跨模块状态按实际复杂度选择轻量集中式状态
- API：统一放在 `services/` 或共享 API Client，页面和组件禁止裸 `fetch`
- Styling：Semi Design Token + CSS Modules/共享变量；禁止第二套通用 UI 组件库

## Key Files

| File | Purpose |
|------|---------|
| `package.json` | pnpm workspace 入口脚本 |
| `pnpm-workspace.yaml` | `frontend/apps/*` 与 `frontend/packages/*` 工作区声明 |
| `frontend/apps/console/package.json` | Console Web 依赖与 dev/build/typecheck/lint/test 脚本 |
| `frontend/apps/chat/package.json` | Chat Web 依赖与 dev/build/typecheck/lint/test 脚本 |
| `frontend/packages/shared/package.json` | 前端共享包占位 |
| `scripts/check_frontend_constraints.py` | Semi Design 依赖和 React 19 adapter 约束检查 |

## 应用边界

```text
frontend/
├── apps/
│   ├── console/      # 超管/控制面管理后台
│   └── chat/         # 普通用户 Web 对话 Channel
└── packages/
    └── shared/       # 类型、API Client、Semi 业务基础组件、主题
```

`src/` 目录尚未创建；新增页面、组件、service 时按下方导航规则建立。

## 页面数据流

```text
用户操作
  -> 页面/组件事件
  -> Hook/Store
  -> Service/API Client
  -> Control Plane 或 Runtime API
  -> 状态更新
  -> Semi 组件渲染
```

## 导航规则

- 新页面：放 `src/pages/` 并更新路由。
- 通用业务组件：优先放应用内 `src/components/`；Console/Chat 都复用的放 `frontend/packages/shared/`。
- HTTP/SSE：统一经 `services/`。
- 通用 UI：先查 Semi Design 是否已有组件，再决定是否自定义。
- React 19：入口文件最顶部导入 `@douyinfe/semi-ui/react19-adapter`。
- 前端约束验证：运行 `scripts/check_frontend_constraints.py`。
