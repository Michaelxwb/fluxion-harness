# 高 QPS 架构整改前端设计

> 文档编号：FE-HIGH-QPS-REMEDIATION
> 文档版本：v0.2
> 创建日期：2026-10-24
> 文档状态：设计评审中
> 来源：`docs/operations/high-qps-remediation-plan.md` + 代码审计
> 后端契约：同目录 `high-qps-remediation.backend.design.md`
> 场景源：同目录 `high-qps-remediation.scenarios.md`

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|---|---|---|
| 前端负责人 | Agent（设计草案） | 组件/状态/构建 |
| 后端接口人 | 待指定 | 通知/设置契约 |
| 测试负责人 | 待指定 | component/E2E 场景 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|---|---|---|---|
| v0.1 | 2026-10-24 | Agent | 初始设计：通知请求 page_size/轮询、code splitting、可选静态 overlay |
| v0.2 | 2026-10-25 | Agent | 按 review 修正：FEAT-F04 提升为 P1（启用时安全边界）、S-10 验收、API-19 明确 |

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| 模块名称 | WebChat 通知轮询与前端静态交付优化 |
| 需求类型 | 性能优化 / 交付方式优化 |
| 业务背景 | 1000 个在线用户每 5s 轮询通知；每次轮询触发 Console 认证 + Runtime 设置回源；前端入口 bundle 4.2MB 且聊天首屏静态引入全部 admin 页面，1000 次冷加载会压 Console Python 进程 |
| 核心目标 | 让空闲轮询不再放大 Console；让聊天首屏更小；静态层可选独立，不改变同源 Cookie/CSRF |

### 2.2 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-F01 | 通知请求带 page_size | `useNotifications` 从 client-config 投影取 `history_page_size`，随 `/notifications` 请求发送 | P0 | US-01/02 |
| FEAT-F02 | 轮询间隔配置化 | 默认推荐 15s；继续使用 API-19 投影的 `notification_poll_sec`，带 ±20% 抖动；不硬编码 | P0 | US-01/02 |
| FEAT-F03 | 聊天入口 code splitting | 12 个 admin 页面改为 lazy；聊天入口不再静态引入全部管理页 | P1 | US-05 |
| FEAT-F04 | 可选静态 overlay | `console-web` nginx/CDN 只服务 `/assets`、`/chat`、`/`；同源路径分流；默认不启用。**启用时同源 Cookie/CSRF 是安全边界，因此按 P1 验收** | P1 | US-05 |

### 2.3 范围与边界

| 类别 | 内容 |
|---|---|
| 范围（In Scope） | `useNotifications` 查询/轮询；`App.tsx` 路由 lazy；可选静态 overlay 的前端构建/Ingress 契约；相关 component/E2E 验证 |
| 非范围（Out of Scope） | 新 UI 页面/组件/交互；通知中心视觉改版；WebSocket/长轮询通知；跨域 CDN；管理页功能变更 |
| 有意妥协 / 技术债 | 默认仍由 Console 镜像提供静态资源；静态 overlay 是 opt-in；轮询仍保留 HTTP（不做推送） |

---

## 2.4 验收条件

> 完整场景见 `high-qps-remediation.scenarios.md`；本节仅列前端设计影响。

| 场景ID | 前端设计影响 / 备注 |
|---|---|
| S-02 | 1000 用户空闲轮询；请求带 page_size；Console 不再回源 settings |
| S-06 | 多 Gateway/SSE 重连；前端仍只经 service 层 |
| S-10 | admin 路由 lazy/深链/聊天首屏不加载 admin chunk |
| S-09 | component：请求 query/轮询间隔/服务层调用 |
| E-01 | 设置源故障时，已带 page_size 的通知轮询不依赖 settings |
| B-04 | 可选静态 overlay 的同源 Cookie/CSRF/SSE 路由；启用 overlay 时按 P1 安全边界验收 |

#### 2.4.1 验证环境假设

| 维度 | 假设 | 验证方式 / 参数 | 不满足时 |
|---|---|---|---|
| 容量 | 1000 个在线会话；15s 轮询；±20% 抖动 | acceptance 浏览器同形驱动 / component | 标记 incomplete |
| 硬件 | 目标浏览器（Chrome/Edge 最新两个大版本） | Playwright projects | env_blocked |
| 依赖 | 真实 Console/Gateway/Runtime；client-config 投影 | preflight + 真实栈 | env_blocked |
| profile | component 单测 + S-02 负载 + B-04 浏览器 | `npm --prefix ... test` / Playwright | 显式 incomplete，不降级 |

---

## 3. 前端技术设计

### 3.1 技术选型

| 类别 | 选型 | 版本 | 选型理由 |
|---|---|---|---|
| 框架 | React + TypeScript | 现有 | 不引入新框架 |
| 状态管理 | 现有 controllers + `useSyncExternalStore` | 现有 | 不新增全局库 |
| 路由 | 现有 React Router | 现有 | 仅 lazy 化 |
| 样式 | 现有 Semi/CSS | 现有 | 不改视觉 |
| 数据请求 | 现有 `api/client.ts` + `modules/webchat/services/webchatApi.ts` | 现有 | 遵守 service 层规则；client-config 走既有 API-19 `GET /api/v1/webchat/client-config` |
| 构建 | Vite | 现有 | 复用现有 build stage |

### 3.2 页面与路由结构

| 页面 | 路由 | 布局 | 说明 |
|---|---|---|---|
| 聊天页 | `/chat/{entryId}` | 现有 | 不变；`ChatPage` 已 lazy |
| 管理页 ×12 | `/agents`、`/skills`、... | 现有 | 改为 `React.lazy` + Suspense，减聊天首屏 |
| 静态资源 | `/assets/*` | — | 可选 `console-web`/CDN；hash immutable |

### 3.3 组件设计

```text
<App>
├─ <ChatPage>                 # 已 lazy；聊天首屏
└─ <AdminRoutes>              # 改为 lazy（12 个页面）
   ├─ OverviewPage
   ├─ AgentsPage
   └─ ...
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|---|---|---|---|---|
| CMP-F01 | `useNotifications` | hook/controller | 现有 | 请求 page_size、轮询间隔、single-flight、失败保留 |
| CMP-F02 | `App` 路由表 | 容器 | 现有 | admin 路由 lazy/Suspense；不改变菜单 |
| CMP-F03 | `console-web`（可选） | 静态层 | 新增构建目标 | nginx 服务静态 + SPA fallback；不进入业务组件 |

> 不新增展示组件；不改列表/详情/菜单结构。

### 3.4 组件接口契约

> API-19 指既有 `GET /api/v1/webchat/client-config`（本设计不改响应结构）；page_size/轮询间隔来自该投影。

**CMP-F01 `useNotifications`**

| 输入/依赖 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `config.limits().history_page_size` | number | 是 | 来自 API-19 | 通知页大小；不再由后端回源 |
| `config.limits().notification_poll_sec` | number | 是 | 来自 API-19 | 轮询间隔 |
| `client.notifications(query)` | `(query, options) => Promise<Page>` | 是 | service | 只经 service 层 |

| 行为 | 载荷 | 触发时机 |
|---|---|---|
| `syncOnce` | `{page:1, page_size, after_seq}` | 首次/定时/前台恢复 |
| `markRead` | `notificationId` | 用户显式已读 |

**CMP-F02 `App`**

| 行为 | 说明 |
|---|---|
| lazy import | admin 页面动态 import；失败显示现有错误边界 |
| 菜单 | 保持 11 项与顺序不变 |

### 3.5 状态与数据流

```text
定时器/前台恢复
  -> useNotifications.syncOnce()
  -> client.notifications({page, page_size, after_seq})
  -> webchatApi.notifications()
  -> api/client.ts
  -> store/cursor/items/unread
  -> useSyncExternalStore 重渲染
```

| 状态 | 作用域 | 形状 | 读写方 |
|---|---|---|---|
| cursor/items/unread/error | controller shared | 现有 `NotificationSnapshot` | hook/组件 |
| config limits | shared | `history_page_size`/`notification_poll_sec` | client-config |
| 轮询定时器 | controller | timeout handle | hook |

**Service 方法**

| Service 方法 | 对应后端接口 | 调用方 |
|---|---|---|
| `notifications({page,page_size,after_seq})` | `GET /api/v1/webchat/notifications` | `useNotifications` |
| `read(id)` | `POST /api/v1/webchat/notifications/{id}/read` | NotificationPanel |

### 3.6 UI 状态

| 视图/交互 | loading | empty | error | success |
|---|---|---|---|---|
| 通知面板 | 现有骨架/转圈 | 现有空态 | 现有错误+重试 | 现有列表 |
| 管理页 lazy | Suspense fallback | 现有空态 | 现有错误边界 | 现有页面 |

> 不新增视觉状态；只改变加载时机。

### 3.7 样式方案

| 维度 | 约定 |
|---|---|
| 样式与逻辑分离 | 继续 CSS/Semi；不在 hook 中写样式 |
| 设计 tokens | 不新增魔法值 |
| 字段布局 | 不改列表/详情布局 |
| 响应式 | 不改断点 |

### 3.8 可访问性与兼容性

| 维度 | 要求 |
|---|---|
| 可访问性 | 不改变现有语义/aria/键盘路径；lazy fallback 不抢焦点 |
| 浏览器/设备 | 现有支持矩阵；静态 overlay 不改变同源 Cookie |

---

## 4. 风险与依赖

| 风险ID | 描述 | 影响 | 应对 | 验证场景 |
|---|---|---|---|---|
| RISK-F01 | 已保存的平台设置仍是 `notification_poll_sec=5.0` | 只改默认不生效 | 通过平台设置/运维文档把推荐值设为 15s；前端只消费投影 | S-02、S-09 |
| RISK-F02 | page_size 超出后端分页上限 | 422/截断 | 取 `history_page_size` 且 ≤100；契约测试 | S-09 |
| RISK-F03 | lazy 路由破坏菜单/深链 | 404/白屏 | 保留路由表/Suspense/错误边界；component + 深链检查 | S-10、B-04 |
| RISK-F04 | 静态 overlay 换域名导致 Cookie/CORS | 登录失败 | 同源路径分流；禁止跨域 CDN | B-04 |
| RISK-F05 | 多 worker 指标/静态缓存 | 观测/缓存不一致 | 静态层独立；指标按后端设计 | B-03、B-04 |

---

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| `harness-frontend#RULE-front-001` | required | 通知请求只经 `webchatApi`/`api/client.ts`；组件/hook 不裸 fetch/axios；无新增文案键 | §3.4、§3.5 | S-09、S-02 | applied |
| `harness-ui#RULE-ui-001` | required | 不改菜单/列表/详情结构；仅 lazy 与静态 overlay | §3.2、§3.3 | S-10、B-04 | applied |

---

## 附录：术语表

| 术语 | 定义 |
|---|---|
| page_size | 通知分页大小；来自 client-config 投影 |
| code splitting | 按路由动态加载，减小聊天首屏 bundle |
| console-web | 可选静态层（nginx/CDN），不执行业务 API |

---

*文档结束*
