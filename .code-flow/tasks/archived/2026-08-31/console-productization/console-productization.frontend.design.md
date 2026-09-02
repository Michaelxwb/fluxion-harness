# Console 产品化 · 前端 UI 重构 模块需求与设计简报

> **文档编号**: FE-CONSOLE-UI-1.0
> **文档版本**: v0.1（草稿）
> **创建日期**: 2026-08-31
> **文档状态**: 草稿（待评审）

**评审边界**：需求评审第 2 章；设计评审第 3 章。
**来源**：`fluxion-console-productization-remediation-final.md` §4/§18-§28 + `docs/design/06` + `docs/design/08`。

---

## 1. 文档控制

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v0.1 | 2026-08-31 | Codex | 初始草稿 |

---

## 2. 需求分析

### 2.1 需求概述 [必填]

| 项目 | 内容 |
|------|------|
| **模块名称** | Console 产品化 · 前端 UI 重构 |
| **需求类型** | 信息架构重构 + 交互改造 |
| **业务背景** | Console 现以 `ResourcesPage + initialTypeFilter` 万能资源页呈现多领域，无独立 Editor/只读详情，管理员需理解 ResourceKind/Binding/RuntimeProfile 等实现概念 |
| **核心目标** | 围绕管理员旅程（准备能力→创建智能体→授权→渠道→发布→运营）重构 IA，统一 Semi Surface，前端简单、后端严格 |

### 2.2 功能方案 [必填]

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|--------|---------|---------|--------|------|
| FEAT-F01 | 主菜单重构 | 按 Agent-centric IA 重组导航（构建/用户/治理/运营/平台），移除智能体工作台/评测/用户与渠道/Queue/Worker/运行时态/运行设置/运行资产独立菜单 | P1 | US-01 |
| FEAT-F02 | 领域独立列表页 | 删除万能 Resource 页；智能体页只展示 AgentDefinition | P1 | US-01 |
| FEAT-F03 | 领域独立 Create Modal | 每可新增对象独立 Modal（CreateAgentModal/CreateSkillModal/AddMCPServerModal…） | P1 | US-01 |
| FEAT-F04 | Detail SideSheet 只读 | 详情统一右侧 SideSheet，严格只读 | P1 | US-01 |
| FEAT-F05 | 独立 Editor/Studio | 编辑从列表进入专属 Editor，与详情分离 | P1 | US-02 |
| FEAT-F06 | Working Draft 无感 | 删除「创建/编辑草稿」显式 UI，编辑已发布对象自动维护 draft | P1 | US-02 |
| FEAT-F07 | 发布校验呈现 | 发布自动全量校验，失败渲染可操作问题清单 | P1 | US-03 |
| FEAT-F08 | Queue/Worker 退位 | 删除队列/Worker 页面，积压归入执行记录/平台概览 | P1 | US-01 |
| FEAT-F09 | Model 页重构 | 模型页按 Provider → Model 产品语义呈现 | P1 | US-01 |
| FEAT-F10 | 四态完整覆盖 | 列表/详情/Editor 全量 loading/empty/error/success | P2 | US-02 |
| FEAT-F11 | Run Detail Timeline/Trace/Snapshot | 执行记录详情展示 Timeline / Trace / ExecutionSnapshot / Tool·Model Calls | P2 | US-01 |
| FEAT-F12 | Version Diff/History | 版本历史 + 版本 Diff，只读呈现 | P2 | US-02 |

> FEAT-F11/F12 为 P2 体验增强项，组件/数据流细节留待 `cf-task:plan` 拆解时细化。

### 2.3 范围与边界 [必填]

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | Console Web 应用（`frontend/apps/console`）IA/页面/组件/交互 |
| **非范围（Out of Scope）** | Chat Web 应用；后端领域修复（见 backend.design.md）；Policy Center/Plugin SPI（P3） |
| **有意妥协 / 技术债** | 授权规则「高级治理 UI」暂缓（保留现有基础 policy 页）；publish 审批 UI 暂缓 |

### 2.4 验收条件 [必填]

**正常场景**

| 场景ID | 功能ID | 优先级 | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|--------|--------|--------|---------|-------------|---------|-------------|
| S-01 | FEAT-F01 | P1 | E2E | Browser → Router → Nav | 进入 Console | 导航仅含构建/用户/治理/运营/平台；无智能体工作台/评测/Queue/Worker 独立项 |
| S-02 | FEAT-F02 | P1 | E2E | Router → Service → Table | 进入「智能体」 | 列表只含 AgentDefinition，不混入 Model/RuntimeProfile |
| S-03 | FEAT-F03 | P1 | E2E | Modal → Service | 新建智能体 | 弹窗仅收名称/描述/默认模型，无 ResourceKind 下拉、无 timeout/retry/raw JSON |
| S-04 | FEAT-F04 | P1 | E2E | SideSheet 只读 | 查看详情 | 右侧 SideSheet 只读，无 Input/Select/Switch |
| S-05 | FEAT-F05 | P1 | E2E | Router → Editor | 列表点「编辑」 | 进入专属 Editor；详情无法发起编辑 |
| S-06 | FEAT-F07 | P1 | E2E | Editor → Publish → 校验 | 发布含缺失依赖的 Agent | 渲染可操作问题清单，定位到具体缺失项 |
| S-07 | FEAT-F11 | P2 | E2E | Browser → Run Detail | 进入执行记录详情 | Timeline/Trace/Snapshot 分区只读呈现 |
| S-08 | FEAT-F12 | P2 | E2E | Browser → 版本历史 | 查看 Agent 版本历史 | 版本列表 + Diff 只读呈现 |

**异常场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|--------|--------|---------|-------------|---------|---------|
| E-01 | FEAT-F10 | integration | Service → 列表 | 列表接口失败 | ErrorBanner + 重试，非白屏 |
| E-02 | FEAT-F10 | integration | Service → 列表 | 空数据 | Empty 空态 + 新增引导 |
| E-03 | FEAT-F07 | integration | Publish → 校验失败 | 校验返回问题清单 | 问题清单定位到字段，不静默失败 |

**非功能指标** [按需]

| 指标ID | 指标名称 | 目标值 | 测量方法 |
|--------|---------|-------|---------|
| NFR-PERF-01 | 列表首屏 | 待定 | Lighthouse |

---

## 3. 前端技术设计

### 3.1 技术选型 [必填]

| 类别 | 选型 | 版本 | 选型理由 |
|------|------|------|---------|
| 框架 | React | 19 | CLAUDE.md 规则 21 |
| UI | Semi Design | `@douyinfe/semi-ui@2.102.x` | 规则 20，唯一组件体系 |
| 路由 | React Router | - | 现有 HashRouter |
| 状态 | 局部优先 | - | 现有模式 |
| 数据请求 | `services/` 封装 | - | 规则 6，禁裸 fetch |

### 3.2 页面与路由结构 [必填]

| 页面 | 路由 | 布局 | 说明 |
|------|------|------|------|
| 平台概览 | `/overview` | 标准页 | 管理员工作台 |
| 智能体列表 | `/build/agents` | 列表页 | 仅 AgentDefinition |
| 智能体 Studio | `/build/agents/:id/edit` | Editor | 从列表进入，非一级菜单 |
| 工作流 | `/build/workflows` | 列表页 | 独立产品对象 |
| 能力（Skill/Tool/MCP） | `/build/capabilities/:type` | 列表页 | 三 Tab，非万能 form |
| 用户 | `/users` | 列表页 | 渠道并入 Agent |
| 授权规则 | `/governance/policies` | 列表页 | 保留基础页，高级 UI 暂缓 |
| 操作审计 | `/governance/audit` | 列表页 | 保留 |
| 执行记录 | `/operations/runs` | 列表页 | 积压归入 Run Detail |
| 模型 | `/platform/models` | 列表页 | Provider → Model 树/分组 |
| 凭据 | `/platform/credentials` | 列表页 | SecretRef 不暴露明文 |

> 移除路由：`/build/agent-studio`、`/build/eval`、`/operations/queues`、`/operations/workers`、`/operations/runtime-status`、`/platform/runtime-profiles`、`/platform/assets`（分别并入 Agent/概览/Run Detail）。

### 3.3 组件设计 [必填]

**组件树（容器/展示分离）**

```
<AgentsPage>                     # 容器：listAgents + 状态
├─ <ListToolbar>                 # 展示：[+新增] [过滤] [搜索]
├─ <AgentsTable>                 # 展示：Semi Table，行末操作
│  └─ <RowActions>               # 展示：编辑/···Dropdown
├─ <ResourceDetailSideSheet>     # 展示：只读 Descriptions/Tabs/Timeline
└─ <CreateAgentModal>            # 展示：最小建档表单
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|--------|--------|------|--------------|------|
| CMP-01 | PageShell / TableShell | 展示 | 复用（公共壳） | 标题+工具栏+分页布局 |
| CMP-02 | ListToolbar | 展示 | 复用 | 左新增右过滤搜索 |
| CMP-03 | ResourceDetailSideSheet | 展示 | 复用 | 只读详情 |
| CMP-04 | CreateAgentModal | 展示 | 新建 | 最小建档 |
| CMP-05 | StatusTag | 展示 | 复用现有 | 状态 Tag |

> **分层纪律**：`TableShell`/`PageShell` 是公共壳，但**业务页面独立**，禁止复用成 `GenericResourceTable + ResourceKind Select`。

### 3.4 组件接口契约 [必填]

**CMP-03 `<ResourceDetailSideSheet>`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| open | boolean | Y | false | 是否展开 |
| resource | ResourceSummary | Y | - | 只读数据源 |
| onClose | () => void | Y | - | 关闭回调 |

| Events | 载荷类型 | 触发时机 |
|--------|---------|---------|
| onClose | void | 点击关闭/遮罩 |

> 组件内禁止直接修改 props；SideSheet 内不渲染任何可写表单组件（Input/Select/Switch/TextArea）。

### 3.5 状态与数据流 [必填]

**状态划分**

| 状态 | 作用域 | 形状 | 读写方 |
|------|--------|------|--------|
| 列表数据 | local | `readonly ResourceSummary[]` | 页面容器 |
| 加载/错误态 | local | `loading / error` | 页面容器 |
| 当前导航 | Router | `location.pathname` | React Router |

**数据获取层**：统一经 `services/`（现有 `httpConsoleApi`），组件通过 hook 消费，禁裸 fetch。

| Service 方法 | 对应后端接口 | 调用方 |
|-------------|-------------|--------|
| `listAgents()` | `GET /api/v1/agents` | AgentsPage |
| `listModelProviders()` | `GET /api/v1/model-providers` | ModelsPage |
| `validatePublish()` | `POST /api/v1/resources/{kind}/{id}:validate-publish` | Editor |

### 3.6 UI 状态 [必填]

| 视图/交互 | loading | empty | error | success |
|----------|---------|-------|-------|---------|
| 列表页 | Skeleton/Spin | Empty + 新增引导 | ErrorBanner + 重试 | Table + 分页 |
| 详情 SideSheet | Spin | — | ErrorBanner | Descriptions |
| Create Modal | 提交 Spin | — | Toast 错误 | Toast 成功 + 关闭 |
| 发布 | 校验 Spin | — | 问题清单定位 | Toast 成功 |

### 3.7 样式方案 [必填]

| 维度 | 约定 |
|------|------|
| 样式与逻辑分离 | Semi Design token + 现有 `styles.css`，不混数据逻辑 |
| 设计 tokens | 间距 4 倍数，强调色 ≤2 种 |
| 响应式 | Sider 导航 + 内容自适应（沿用现有 Layout） |

---

## 4. 风险与依赖

| 风险ID | 描述 | 影响 | 应对 | 验证场景 |
|--------|------|------|------|---------|
| RISK-F01 | 现有 `ResourcesPage` 被 5 个路由复用，拆分回归面大 | 中 | 逐页迁移 + 保留旧页过渡 | S-02 |
| RISK-F02 | Semi 受控组件动画坑 | 中 | 沿用现有 jsdom 补发 animationEnd 模式 | S-03 |

---

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|-----------|-------------|---------|---------|---------|----------------|
| frontend-semi-design#RULE-frontend-semi-001 | required | 唯一 Semi 组件体系 + Surface | §3.1 / §3.3 | S-01 | applied |
| frontend-component-specs#RULE-frontend-component-001 | required | 容器/展示分离 | §3.3 | S-02 | applied |
| frontend-directory-structure#RULE-frontend-directory-001 | required | 页面/路由/组件归位 | §3.2 | S-01 | applied |
| frontend-quality-standards#RULE-frontend-quality-001 | required | 类型/lint/四态/集中请求 | §3.5 / §3.6 | E-01 | applied |
| fluxion-console-channel#RULE-fluxion-console-001 | required | 渠道归属 Agent，普通用户不登录 Console | §3.2 移除用户与渠道 | S-01 | applied |
| fluxion-resource-registry#RULE-fluxion-resource-001 | required | —（后端域） | 见 backend.design.md | — | 后端设计覆盖 |
| fluxion-runtime-core#RULE-fluxion-runtime-001 | required | —（后端域） | 见 backend.design.md | — | 后端设计覆盖 |
| fluxion-workflow-capability#RULE-fluxion-workflow-001 | required | —（后端域） | 见 backend.design.md | — | 后端设计覆盖 |
| fluxion-dfx#RULE-fluxion-dfx-001 | required | —（后端域） | 见 backend.design.md | — | 后端设计覆盖 |
| fluxion-console-api-contract#RULE-fluxion-console-api-001 | required | —（后端域） | 见 backend.design.md | — | 后端设计覆盖 |
| backend-code-quality-performance#RULE-backend-quality-001 | required | —（后端域） | 见 backend.design.md | — | 后端设计覆盖 |
| backend-database#RULE-backend-database-001 | required | —（后端域） | 见 backend.design.md | — | 后端设计覆盖 |
| backend-directory-structure#RULE-backend-directory-001 | required | —（后端域） | 见 backend.design.md | — | 后端设计覆盖 |
| backend-logging#RULE-backend-logging-001 | required | —（后端域） | 见 backend.design.md | — | 后端设计覆盖 |
| backend-platform-rules#RULE-backend-platform-001 | required | —（后端域） | 见 backend.design.md | — | 后端设计覆盖 |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| IA | Information Architecture，信息架构 |
| Surface | Modal 创建 / SideSheet 查看 / Editor 修改 / Table 管理 |
| 容器/展示组件 | 数据状态 / 纯 UI（props in / events out） |

*文档结束*
