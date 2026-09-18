# Frontend Retrieval Map

> AI 导航地图：定位前端代码结构和关键模块。2026-09-10 由 cf-learn 按真实结构校准（无 router.*/stores/utils/types/ 目录，路由在 App.tsx）。

## Purpose

React 18 + Vite 6 + TS 5.7（strict）管理台（`frontend/console`），Semi Design 为唯一 UI 库；Fastify 网关（`apps/channel_gateway`）做渠道接入。骨架期：9 路由中 8 条为占位页。

## Architecture

- Framework: React 18, JSX `react-jsx`, `tsc --noEmit` 门禁（`pnpm typecheck`）
- UI: `@douyinfe/semi-ui` 唯一通用组件库；样式只在 `styles/global.css` 类名
- State: 局部 `useState` + `useXxx` hook，无集中 store
- Routing: `src/App.tsx` 集中注册（无独立 router.*）
- Data: `axios.create` 单例（`baseURL /api/v1`，timeout 15s）+ `services/` 薄封装 + hook 消费
- Envelope: `ApiEnvelope{code,message,data,request_id,timestamp}`，前后端同构
- Gateway: 单文件 `index.ts`（onRequest 注 request-id → onResponse 日志 → setErrorHandler 兜底 500）+ `response.ts` + `logging.ts`（JSON + redact）

## Key Files

| File | Purpose |
|------|---------|
| `frontend/console/src/main.tsx` | 入口挂载 |
| `frontend/console/src/App.tsx` | 路由集中注册（9 条，`*` 重定向 `/agents`） |
| `frontend/console/src/api/client.ts` | axios 单例 + 拦截器（转 ApiError + Toast，带 request_id） |
| `frontend/console/src/services/agentService.ts` | 数据契约（`AgentRow`）+ 纯 HTTP 封装，零 try |
| `frontend/console/src/hooks/useAgentList.ts` | 分页/过滤/重载收口（⚠ 现有 `setLoading` 未定义 bug 待修） |
| `frontend/console/src/pages/agents/AgentsPage.tsx` | 唯一真实页：容器组装 |
| `frontend/console/src/pages/common/PlaceholderListPage.tsx` | 占位页模板 |
| `frontend/console/src/components/common/` | 展示壳：`PageContainer/StandardListPage/ListToolbar/ListFooter` |
| `frontend/console/src/styles/global.css` | 唯一全局样式位置 |
| `apps/channel_gateway/src/index.ts` | 网关：钩子 + `/health` |
| `apps/channel_gateway/src/response.ts` | `ok()/failure()` 信封 |
| `apps/channel_gateway/src/logging.ts` | `logEvent` + `redact()` |

## Module Map

```
frontend/console/src/
├── main.tsx / App.tsx   # 入口 + 路由
├── api/                 # client.ts 单例（唯一 axios 位置）
├── services/            # 数据契约 + 纯 HTTP（agentService.ts）
├── hooks/               # useXxx 状态收口
├── pages/
│   ├── agents/          # 真实页
│   └── common/          # PlaceholderListPage
├── components/common/   # 纯展示壳
└── styles/              # global.css
apps/channel_gateway/src/
├── index.ts             # 钩子 + 路由
├── response.ts
├── contracts.ts
└── logging.ts
```

## Data Flow

```
User Action → 页面容器 → hook（状态） → service（HTTP） → apiClient（拦截器）
            → 后端 Envelope → hook 更新状态 → Re-render
错误：拦截器转 ApiError + Toast（含 request_id），service/hook 不 catch
```

## Navigation Guide

- 新增页面 → `pages/` 加容器 + `App.tsx` 注册路由 + 复用 `PageContainer/StandardListPage`
- 新增数据接口 → `services/` 加纯函数 + 类型契约，hook 消费，禁组件内裸 fetch（有 checks 门禁）
- 新增展示块 → `components/common/` 纯展示壳，样式进 `global.css` 类名
- 状态 → 局部 useState + hook 收口，不建 stores（现状无）
- 网关加路由 → `apps/channel_gateway/src/index.ts`，错误走 `failure()`，日志走 `logEvent`
