# Phase 1 Console 产品架构前端设计简报

> **文档编号**: FE-P1-CONSOLE-v0.3
> **文档版本**: v0.3（采纳 roadmap §6 冻结导航；修 F-2 AgentDefinition 契约；按 roadmap 标注各功能落地阶段，Phase 4/5 只锁契约不深做代码）
> **创建日期**: 2026-08-27
> **文档状态**: 草稿
> **配套**: 与同目录 `phase1-product-architecture.backend.design.md` 并行。后端 typed spec model / Product API 契约 / User Domain / agent_id 路由见后端 brief §3.3/§3.4。

**评审边界说明**:
- **需求评审**: 第 2 章 → 锁定需求基线
- **设计评审**: 第 3 章 → 锁定设计基线

**ID 体系**: US（来自 PRD）、FEAT（功能）、CMP（组件）、NFR
场景编号：S-（正常）、E-（异常）、B-（边界）

**填写约定**: 阈值引自 CLAUDE.md 性能基线 / PRD SLO，非示例。框架为 React 19 + Semi Design（CLAUDE.md 前端强制规范）。冻结导航引自 roadmap §6 Phase 4。

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 开发负责人 | Fluxion | 冻结 IA + 全资源接入 + Agent Studio 实现 |
| 设计/交互 | Fluxion | 冻结导航落地 + 术语映射 + schema 驱动表单 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v0.1 | 2026-08-27 | Fluxion | 初始草稿（cf-task:align 产出） |
| v0.2 | 2026-08-27 | Fluxion | 补全全部必备功能；IA 改按资源类型分独立管理页 + Agent Studio 内联新建 |
| v0.3 | 2026-08-27 | Fluxion | 采纳 roadmap §6 冻结导航 `Overview/Build{Agents,Workflows,Capabilities,Eval}/Users/Governance/Operations/Platform`（skill/tool/mcp→Build→Capabilities，model/RuntimeProfile/Secret/Registry→Platform→Advanced，Workspace 独立 app）；修 F-2：Agent Studio picker 对齐 PRD §4.2 AgentDefinition（CapabilityPicker+类型过滤 取代孤立 Skill/Mcp/Tool picker，补 owner/visibility/lifecycle + memory/personalization refs）；按 roadmap 标注各 FEAT 落地阶段，Phase 4/5 功能只锁契约不深做代码 |

---

## 2. 需求分析

### 2.1 需求概述 [必填]

| 项目 | 内容 |
|------|------|
| **模块名称** | Console 产品架构（冻结 IA + 全资源接入 + Agent Studio + Product API 前端） |
| **需求类型** | 重构（开发阶段接受大改，不做兼容修复） |
| **业务背景** | V1 Console 是 Resource-centric 铺表，管理员面对 RuntimeProfile/Binding/Registry 等内部术语和"按 Resource 自动增长的 IA"（codex §4/P-06），"用不明白"（用户原话）。当前前端"插件仅支持新增模型，tool/skill 等都未适配"——必备接入功能缺失。 |
| **核心目标** | 采纳 roadmap §6 冻结导航；每一类必备功能可用：model/tool/skill/mcp 接入、agent runtime 创建（运行设置）、凭据、用户管理（含 360）、授权规则、评估、Workspace shell；Agent Studio 内每个 picker 支持内联新建；schema 驱动表单避免重复造轮子；术语去暴露（普通用户核心页底层术语暴露=0，Phase 4 Gate）。 |

### 2.2 功能方案 [必填]

> 落地阶段引自 roadmap：Phase 1=Domain+Storage Foundations；Phase 4=Product Experience（TASK-C401..C408 / TASK-X401..X408）；Phase 5=Governance+Observability+Eval。本 brief 是综合产品 brief：Phase 4/5 功能**锁契约（IA 位置 + 组件/数据/API 契约）但不深做代码**，由对应 Phase 落地。

| 功能ID | 功能名称 | 功能描述 | 优先级 | 落地阶段 | 来源 |
|--------|---------|---------|--------|---------|------|
| FEAT-F01 | 冻结 Console IA（TASK-C401） | 顶层导航固定为 `Overview / Build{Agents, Workflows, Capabilities, Eval} / Users / Governance / Operations / Platform`；IA 不随 Resource 自动增长（P-06）。skill/tool/mcp→Build→Capabilities；model/RuntimeProfile/Secret/Registry→Platform→Advanced；普通用户不显示 RuntimeProfile/Registry/Binding/Plugin internals（roadmap §6）。 | P0 | Phase 4 | US-01 |
| FEAT-F02 | Overview 仪表盘 | Agent/各资源/Workflow 计数 + 最近活动骨架。 | P1 | Phase 4 | US-04 |
| FEAT-F03 | Agent Studio（TASK-C402） | 建/编辑 Agent：persona(identity + owner/visibility/lifecycle) + model_ref + runtime_profile_ref + **capabilities**（CapabilityPicker，skill/tool/mcp 类型过滤 + 内联新建）+ memory/personalization policy refs + instructions → 预览 → 试跑（对接后端 `/studio/agents/{agent_id}/test-run`）。对齐 PRD §4.2 AgentDefinition。对应 PRD FEAT-22。 | P0 | Phase 1(API契约)/Phase 4(UI) | US-05, FEAT-22 |
| FEAT-F04 | Capabilities 管理（TASK-C404） | Build→Capabilities 单页 + 类型 Tab（skill/tool/mcp）+ schema 驱动 SchemaForm；Agent Studio `CapabilityPicker` 内联新建。tool/skill/mcp 均为 agent-facing capability（CLAUDE.md 规则 12）。对应 PRD FEAT-14。 | P0 | Phase 4 | US-05, FEAT-14 |
| FEAT-F05 | Workflow Studio（TASK-C403） | 工作流列表 + 详情只读；**不做画布编辑器**（Phase 4 之后）。 | P1 | Phase 4 | US-06 |
| FEAT-F06 | Users + User 360（TASK-C405） | 用户列表 + 详情 + bind/授权 + User 360 五区视图（Identity / Profile / Capability / Policy / Activity，PRD US-07）。对应 PRD FEAT-23。 | P0 | Phase 1(bind+PlatformUser 契约)/Phase 4(360) | US-02, US-07, FEAT-23 |
| FEAT-F07 | Governance/Policy（TASK-C406） | Policy 资源管理：列表 + schema 表单；影响 Agent 能调用哪些 capability/tool。 | P1 | Phase 5 | FEAT-23 |
| FEAT-F08 | 评估 Eval（Build→Eval） | Eval set 资源管理：列表 + 详情 + 评估运行骨架（浅做）。 | P2 | Phase 5 | FEAT-23/24 |
| FEAT-F09 | Operations（TASK-C407） | 部署/Pod/指标骨架。 | P2 | Phase 5/6 | - |
| FEAT-F10 | Platform/Advanced（TASK-C408） | RuntimeProfile（运行设置）/ Secret（凭据）/ Registry / **model provider** 资源管理；schema 驱动表单；主流程不暴露，退到 Advanced。架构规则 2/26/27：Console 创建 RuntimeProfile 配置，不创建 Pod。对应 PRD FEAT-15（model）/FEAT-19（secret）。 | P0(RuntimeProfile/model)/P1(Secret deep) | Phase 4(RuntimeProfile/model)/Phase 5(Secret deep) | US-01, FEAT-15/19 |
| FEAT-F11 | Product API BFF services | 前端 services 层调 `/studio/*` `/admin/*` `/platform/*`，**禁止裸 fetch**；类型安全 client；统一 envelope 解包。 | P0 | Phase 1(契约)/Phase 4(UI) | US-05 |
| FEAT-F12 | 术语去暴露 | 主流程 UI 用业务语义（运行设置/授权/资源库/凭据），RuntimeProfile/Binding/Registry/ExecutionSnapshot/Secret 原词退到 Advanced/Platform；普通用户核心页底层术语暴露=0（roadmap Phase 4 Gate）。 | P0 | Phase 4 | US-01 |
| FEAT-F13 | 工作区 shell（普通用户，独立 app） | `frontend/apps/chat/` 演进的普通用户 Workspace 入口（**非 admin Console 路由**）；绑定后映射 PlatformUser；与 Console 共享主题/基础组件（CLAUDE.md 前端规范 7）。不显示 RuntimeProfile/Registry/Binding/Plugin internals。对应 PRD FEAT-21。 | P0 | Phase 4 | FEAT-21 |

> 来源：US-XX 引用 PRD §3.2；FEAT-XX 为 PRD §4 功能编号。model 接入 = FEAT-F10（Platform/Advanced provider 管理）+ FEAT-F03（Agent Studio `ModelSelectSection` picker + 内联新建）+ 场景 S-03 端到端。

### 2.3 范围与边界 [必填]

| 类别 | 内容 |
|------|------|
| **范围（In Scope，设计覆盖）** | 冻结 IA（7 组导航全设计）；Agent Studio（含 model_ref/runtime_profile_ref/capabilities picker + 内联新建，对齐 §4.2）；Capabilities 管理（skill/tool/mcp，schema 驱动）；Platform/Advanced（RuntimeProfile/Secret/Registry/model provider）；Workflow 列表+详情；用户列表+详情+bind + User 360；授权规则/评估/Operations 锁契约；Workspace shell 入口；Product API services；术语去暴露。**无留位骨架**——凡必备功能均有真实设计（含契约）。 |
| **非范围（Out of Scope，不本 brief 深做代码）** | Workflow 画布编辑器（Phase 4 之后）；评估全生命周期算法深做（Phase 5，本 brief 只锁列表+详情契约）；Operations 部署/Pod/指标深做（Phase 5/6）；Event Bus 扩展（Phase 5/6）；审计详情页（辅助，P2 以后单独 brief）。 |
| **有意妥协 / 技术债** | 评估/Operations 仅锁契约骨架（用户原则 3：辅助降级）；Workspace shell 仅入口+绑定映射，完整用户任务台 Phase 4（TASK-X401..X408）。 |

### 2.4 验收条件 [必填]

> 跨路由、services 请求、状态更新、最终 UI 的用户流程必须 E2E；关键真实边界列不得 mock 的浏览器/路由/services/schema 端点。Matrix 引用的 S-01/S-02/S-04/S-05/S-09/S-13/S-14/B-01 为稳定 ID。

**正常场景**

| 场景ID | 功能ID | 优先级 | 测试层级 | 关键真实边界 | 操作步骤 | 预期 UI 结果 |
|--------|--------|--------|---------|-------------|---------|-------------|
| S-01 | FEAT-F01 | P0 | E2E | Browser → Router → UI | 登录进入 `/` | Overview + 左侧冻结 7 组导航全可见（Overview/Build{Agents,Workflows,Capabilities,Eval}/Users/Governance/Operations/Platform） |
| S-02 | FEAT-F03 | P0 | E2E | Browser → Router → Product API → Runtime → UI | `/build/agents/new` 填表（persona+model_ref+runtime_profile_ref+capabilities+memory refs）→ 预览 → 试跑 | 表单校验通过、预览渲染、试跑流式返回；试跑按 agent_id 路由 |
| S-03 | FEAT-F03,F10 | P0 | E2E | Browser → Router → Product API → Schema endpoint → UI | `/platform/models` 新建 model：schema 表单填 base_url + 内联新建凭据→SecretRef + model_name → 发布；Agent Studio `ModelSelectSection` 内联新建并选中 | Platform 列表出现新 model；Agent Studio picker 选中新建项，不跳离 |
| S-04 | FEAT-F04 | P0 | E2E | Browser → Router → Schema endpoint → UI | `/build/capabilities` 类型 Tab 切 skill/tool/mcp 新建 + Agent Studio `CapabilityPicker` 内联新建 | 三类 capability CRUD + picker 内联新建均可用（共用 SchemaForm + CapabilityPicker） |
| S-05 | FEAT-F04,F10 | P0 | E2E | Browser → Router → Schema endpoint → UI | 各 kind schema 驱动 SchemaForm 渲染 | 一套 SchemaForm 渲染 skill/tool/mcp/model/runtime-profile/secret/policy 全 kind，字段由 `/resources/{kind}/schema` 驱动 |
| S-06 | FEAT-F10 | P0 | E2E | Browser → Router → Product API → UI | `/platform/runtime-profiles` 新建运行设置 → Agent Studio `RuntimeProfileSelect` 引用 | Platform 运行设置列表可见；Agent Studio 可选 |
| S-07 | FEAT-F10 | P0 | E2E | Browser → Router → Service → UI | `/platform/secrets` 独立建凭据 → 列表只见 ref 不见明文 | 凭据列表展示 SecretRef + 用途，无明文 |
| S-08 | FEAT-F05 | P1 | E2E | Browser → Router → Service → UI | 进入 `/build/workflows` 列表 → 详情 | 列表 + 详情只读（无画布） |
| S-09 | FEAT-F06 | P0 | E2E | Browser → Router → Service → UI | Users 列表 → 详情 → bind | 列表 + 详情 + 绑定操作 |
| S-10 | FEAT-F06 | P1 | E2E | Browser → Router → Service → UI | 用户详情 → User 360 视图 | 聚合 Identity/Profile/Capability/Policy/Activity 五区可见 |
| S-11 | FEAT-F07 | P1 | E2E | Browser → Router → Schema endpoint → UI | `/governance/policies` 新建授权规则 | 列表 + schema 表单可用 |
| S-12 | FEAT-F08 | P2 | E2E | Browser → Router → Service → UI | `/build/evals` 列表 → 详情 | 列表 + 详情只读骨架 |
| S-13 | FEAT-F12 | P0 | E2E | Browser → DOM 文本断言 | 遍历主流程页面 | 主流程 UI 文本不出现 `RuntimeProfile`/`Binding`/`Registry`/`ExecutionSnapshot` 原词；Secret 不出现明文；普通用户核心页底层术语暴露=0 |
| S-14 | FEAT-F13 | P0 | E2E | Browser → Router → bind API → UI | 普通用户进 Workspace shell（独立 app）→ `/bind <code>` | 未绑定仅 `/bind`；绑定后映射 PlatformUser，shell 可用；不显示 RuntimeProfile/Registry/Binding |
| S-15 | FEAT-F02 | P1 | E2E | Browser → Router → Service → UI | 进入 `/` Overview | 计数 + 最近活动骨架渲染 |

**异常场景**

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 触发条件 | UI 表现 |
|--------|--------|---------|-------------|---------|---------|
| E-01 | FEAT-F03 | integration | Service → UI | 试跑模型调用失败 | 错误态 + 重试按钮 |
| E-02 | FEAT-F03 | integration | Service → UI | Agent 表单必填缺失（persona/model_ref） | 字段定位 + 校验提示 |
| E-03 | FEAT-F04,F10 | integration | Schema endpoint → UI | schema 表单字段校验失败（任一资源类型） | 字段定位 + 校验提示，不提交 |

**边界场景** [按需]

| 场景ID | 测试层级 | 关键真实边界 | 字段/条件 | 边界值 | 预期行为 |
|--------|---------|-------------|----------|--------|---------|
| B-01 | integration（静态断言） | 源码扫描：tsc strict + ESLint + grep | `services/` 与组件层源码 | 存在裸 `fetch`/`axios`、`any`、`@ts-ignore` | 扫描零命中；tsc strict 通过 |

**非功能指标** [按需]

| 指标ID | 指标名称 | 目标值 | 测量方法 |
|--------|---------|-------|---------|
| NFR-PERF-01 | 资源列表/详情 P95 | ≤300ms | CLAUDE.md 基线 |
| NFR-PERF-02 | Chat 模型调用前框架首字节 P95 | ≤200ms | CLAUDE.md 基线 |

---

## 3. 前端技术设计

### 3.1 技术选型 [必填]

| 类别 | 选型 | 版本 | 选型理由 |
|------|------|------|---------|
| 框架 | React | 19 | CLAUDE.md 前端规范 |
| UI 库 | `@douyinfe/semi-ui` + `@douyinfe/semi-icons` | 2.102.x | 禁止第二套通用库（CLAUDE.md 规则 20/前端规范 5） |
| 适配器 | `@douyinfe/semi-ui/react19-adapter` | - | main.tsx 第一条 UI 导入（规则 21/前端规范 3） |
| 构建 | Vite | 既有 | |
| 路由 | React Router | 既有 | 冻结导航层级 |
| 状态管理 | 局部 state + 共享 store（Zustand 或等价） | - | Agent Studio 表单态 + 列表缓存 |
| 数据请求 | services 封装 + react-query/SWR | - | 禁止裸 fetch（前端规范 6） |
| 表单 | schema 驱动 `SchemaForm`（复用既有 + ADR-012） | - | 一套表单渲染所有资源类型，避免每类手写 |
| 样式方案 | Semi design tokens + CSS Modules | - | token 化，不散落魔法值 |

### 3.2 页面与路由结构 [必填]

> 冻结导航（roadmap §6 Phase 4 TASK-C401）——7 顶层组，IA 不随 Resource 增长（P-06）。Console 在 `frontend/apps/console/`；Workspace shell 在 `frontend/apps/chat/`（独立 app，非 Console 路由）。

| 顶层组 | 路由 | 布局 | 说明 |
|------|------|------|------|
| Overview | `/` | ConsoleLayout（左侧冻结 7 组导航 + 顶栏） | 仪表盘骨架 |
| Build → Agents | `/build/agents` + `/new` + `/:id` | ConsoleLayout（双栏 for Studio） | Agent Studio（TASK-C402） |
| Build → Workflows | `/build/workflows` + `/:id` | ConsoleLayout | 列表+详情只读（TASK-C403） |
| Build → Capabilities | `/build/capabilities` + `/new` + `/:id`（`?type=skill\|tool\|mcp`） | ConsoleLayout | skill/tool/mcp 统一管理，schema 驱动（TASK-C404） |
| Build → Eval | `/build/eval` + `/:id` | ConsoleLayout | 列表+详情骨架（Phase 5） |
| Users | `/users` + `/:id` | ConsoleLayout | 列表+详情+bind + User 360（TASK-C405） |
| Governance | `/governance` + `/policies` + `/new` + `/:id` | ConsoleLayout | 授权规则（Phase 5） |
| Operations | `/operations` | ConsoleLayout | 部署/Pod/指标骨架（Phase 5/6） |
| Platform → Advanced | `/platform` + `/runtime-profiles` + `/secrets` + `/registry` + `/models` 各 `/new`+`/:id` | ConsoleLayout | RuntimeProfile(运行设置)/Secret(凭据)/Registry/model provider；主流程不暴露（TASK-C408） |
| Workspace shell（独立 app） | `apps/chat/`（非 Console 路由） | WorkspaceLayout | 普通用户入口 + `/bind`（TASK-X401） |

> 目录结构遵守 `frontend/apps/console/`（CLAUDE.md 仓库边界）；Workspace shell 在 `frontend/apps/chat/`；新增页面入口同步导航地图。model provider 资源管理在 Platform→Advanced（infra），非 Build→Capabilities（capability）；Agent 在 Agent Studio `ModelSelectSection` 绑定 model。

### 3.3 组件设计 [必填]

**Agent Studio 组件树**（容器/展示分离；picker 对齐 PRD §4.2 AgentDefinition；每个 picker 支持内联新建）

```
<AgentStudioPage>                # 容器：路由参数 + 数据获取 + 发布状态
├─ <AgentFormPanel>              # 容器：表单 state + 校验 + 提交
│  ├─ <PersonaSection>           # 展示：identity(name/description/system_prompt) + owner/visibility/lifecycle
│  ├─ <ModelSelectSection>       # 展示：model_ref picker（Platform model） + 内联新建（Modal+SchemaForm）
│  ├─ <RuntimeProfileSelect>     # 展示：runtime_profile_ref picker（Platform 运行设置） + 内联新建
│  ├─ <CapabilityPicker>         # 展示：capabilities 绑定 + 类型过滤(skill/tool/mcp) + 内联新建 [取代孤立 Skill/Mcp/Tool picker]
│  ├─ <MemoryPolicySection>      # 展示：memory_policy_ref + personalization_policy_ref
│  ├─ <SecretRefSelect>          # 展示：凭据选择（model/mcp 凭据字段内联新建，不见明文）
│  └─ <InstructionsSection>      # 展示：instructions
├─ <AgentPreviewPanel>           # 展示：拼装后 prompt 预览
└─ <TestRunPanel>                # 容器：test-run SSE（按 agent_id） + 结果流
```

> F-2 修复：v0.2 的孤立 `SkillPicker`/`McpPicker`/`ToolListSection` 已删除，统一为 `CapabilityPicker`（capabilities 是 §4.2 的绑定字段，skill/tool/mcp 是 capability 类型，CLAUDE.md 规则 12）。补 `MemoryPolicySection`（memory/personalization policy refs，§4.2）。`PersonaSection` 扩 owner/visibility/lifecycle（§4.2）。

**共享资源管理组件**（DRY：一套渲染所有资源类型）

```
<ResourceListPage kind={model|skill|tool|mcp|runtime-profile|secret|policy|eval}>
  # 容器：分页列表 + "新建" → SchemaForm（Modal/抽屉）
<ResourceDetailPanel kind>      # 容器：只读详情
<SchemaForm kind schemaRef>     # 展示：由 /resources/{kind}/schema 驱动渲染字段
<ResourcePicker kind>           # 展示：Agent Studio 内复用（model/runtime-profile/secret），支持内联新建
<CapabilityPicker>              # 展示：Agent Studio 内复用（capabilities，skill/tool/mcp 类型过滤 + 内联新建）
```

| 组件ID | 组件名 | 类型 | 复用来源/去向 | 职责 |
|--------|--------|------|--------------|------|
| CMP-01 | ConsoleLayout | 容器 | 复用现有 Layout | 冻结 7 组导航 + 顶栏 |
| CMP-02 | AgentStudioPage | 容器 | 新建 | 路由+状态编排 |
| CMP-03 | AgentFormPanel | 容器 | 新建 | 表单 state + 校验 |
| CMP-04 | PersonaSection | 展示 | 新建 | identity + owner/visibility/lifecycle |
| CMP-05 | ModelSelectSection | 展示 | 新建 | model_ref picker + 内联新建 |
| CMP-06 | RuntimeProfileSelect | 展示 | 新建 | 运行设置 picker + 内联新建 |
| CMP-07 | CapabilityPicker | 展示 | 新建 | capabilities 绑定 + 类型过滤(skill/tool/mcp) + 内联新建 |
| CMP-08 | MemoryPolicySection | 展示 | 新建 | memory/personalization policy refs |
| CMP-09 | SecretRefSelect | 展示 | 新建 | 凭据选择 + 内联新建（不见明文） |
| CMP-10 | AgentPreviewPanel | 展示 | 新建 | prompt 预览 |
| CMP-11 | TestRunPanel | 容器 | 新建 | SSE 试跑（agent_id） |
| CMP-12 | ResourceListPage | 容器 | 新建（所有资源类型共用骨架） | 分页列表 + 新建入口 |
| CMP-13 | ResourceDetailPanel | 容器 | 复用/扩展 | 只读详情 |
| CMP-14 | SchemaForm | 展示 | 复用既有（ADR-012） | schema 驱动表单，所有 kind 共用 |
| CMP-15 | ResourcePicker | 展示 | 新建 | Agent Studio 内复用（model/runtime-profile/secret），内联新建 |
| CMP-16 | User360Panel | 容器 | 新建 | Identity/Profile/Capability/Policy/Activity 五区聚合 |
| CMP-17 | WorkspaceShell | 容器 | 新建（`apps/chat/`） | 普通用户入口 + /bind |

> 分层纪律：容器管数据/状态，展示纯 UI（props in / events out）；复用逻辑提 hook，禁止跨组件复制粘贴。通用组件（Button/Form/Table/Modal/Toast/Select/Tree/Tabs）用 Semi，禁止重复实现（前端规范 4）。`SchemaForm` + `CapabilityPicker` 是"易用 + 不重复造轮子"的核心：每类资源接入表单不手写，由后端 schema 驱动；skill/tool/mcp 统一 CapabilityPicker。

### 3.4 组件接口契约 [必填]

**CMP-03 `<AgentFormPanel>`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| initial | AgentDefinitionDraft | N | 空 | 编辑态初值 |
| onSubmit | (draft) => void | Y | - | 提交上抛 |
| submitting | bool | N | false | |

| Events / 回调 | 载荷类型 | 触发时机 |
|--------------|---------|---------|
| onSubmit | AgentDefinitionDraft | 校验通过 |

**CMP-14 `<SchemaForm>`**（共享，schema 驱动）

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| kind | ResourceKind | Y | - | 资源类型，决定拉取哪份 schema |
| initial | Record | N | 空 | 编辑态初值 |
| onSubmitted | (resource) => void | Y | - | 新建/保存成功上抛（供 picker 选中） |

| Events / 回调 | 载荷类型 | 触发时机 |
|--------------|---------|---------|
| onSubmitted | Resource | 内联新建成功 |

**CMP-15 `<ResourcePicker>`**（Agent Studio 内复用，model/runtime-profile/secret）

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| kind | ResourceKind | Y | - | |
| selected | ResourceRef[] | N | [] | 已选 |
| onChange | (refs) => void | Y | - | 选择变更 |
| allowInlineCreate | bool | N | true | 内联新建开关 |

**CMP-07 `<CapabilityPicker>`**（Agent Studio 内复用，capabilities）

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| selected | CapabilityBinding[] | N | [] | 已绑 capability（含 capability_ref + version_pin） |
| typeFilter | `skill\|tool\|mcp\|all` | N | all | 类型过滤 |
| onChange | (bindings) => void | Y | - | 绑定变更 |
| allowInlineCreate | bool | N | true | 内联新建 capability（走 Build→Capabilities SchemaForm） |

**CMP-11 `<TestRunPanel>`**

| Props | 类型 | 必填 | 默认 | 说明 |
|-------|------|------|------|------|
| agentId | str | Y | - | 按 agent_id 试跑（TASK-A105） |
| input | str | N | - | |

| Events / 回调 | 载荷类型 | 触发时机 |
|--------------|---------|---------|
| onResult | SSE chunk | 流式返回 |
| onError | ApiError | 调用失败 |

### 3.5 状态与数据流 [必填]

**状态划分**

| 状态 | 作用域 | 形状 | 读写方 |
|------|--------|------|--------|
| agentDraft | local（AgentStudioPage） | AgentDefinitionDraft | AgentFormPanel |
| testRunStream | local（TestRunPanel） | {status, chunks[]} | TestRunPanel |
| resourceListCache | shared store | {kind, page, items} | ResourceListPage |
| schemaCache | shared store | {kind, schema} | SchemaForm |

**数据流**

```
用户操作 → 事件处理 → Service(Product API client) → Store/State → 重渲染
```

**数据获取层**：API 调用必须经 `services/` 统一发起，组件/展示层不出现裸 `fetch`/`axios`（前端规范 6）。

| Service 方法 | 对应后端接口 | 调用方组件/hook |
|-------------|-------------|----------------|
| `createAgent(draft)` | `POST /studio/agents` | AgentStudioPage |
| `getAgent(id)` | `GET /studio/agents/{id}` | AgentStudioPage |
| `testRunAgent(agentId, input)` | `POST /studio/agents/{agentId}/test-run` (SSE) | TestRunPanel |
| `listCapabilities(type?)` | `GET /studio/capabilities?type=skill\|tool\|mcp` | ResourceListPage（Build→Capabilities） |
| `listResources(kind)` | `GET /studio/{kind}`（kind ∈ models/runtime-profiles/secrets/policies/evals） | ResourceListPage（Platform/Advanced） |
| `getResource(kind, id)` | `GET /studio/{kind}/{id}` | ResourceDetailPanel |
| `createResource(kind, draft)` | `POST /studio/{kind}` | SchemaForm |
| `getResourceSchema(kind)` | `GET /resources/{kind}/schema` | SchemaForm |
| `listUsers()` / `getUser(id)` | `GET /admin/users` / `/{id}` | UserList / User360 |
| `bindUser(id, code)` | `POST /admin/users/{id}/bind` | UserDetail |
| `getUser360(id)` | `GET /admin/users/{id}/360` | User360Panel |

> 所有响应统一 `{code,message,data,request_id}` envelope（RULE-fluxion-console-api-001）；request_id 透传。`kind` ∈ {agents, models, tools, skills, mcp, runtime-profiles, secrets, policies, evals}。Agent 试跑/引用按 **agent_id** 路由（后端 TASK-A105），不再以 runtime_profile_id 为 Agent 标识（PRD §4.2）。

### 3.6 UI 状态 [必填]

| 视图/交互 | loading | empty | error | success |
|----------|---------|-------|-------|---------|
| Agent 列表 | 骨架表格 | 空态插画+新建 | 错误提示+重试 | 表格 |
| Agent Studio 试跑 | 流式转圈 | - | 错误态+重试（E-01） | 结果流 |
| 表单提交（Agent/资源） | 按钮加载 | - | 字段定位（E-02/E-03） | 成功 Toast |
| 资源管理列表（各 kind） | 骨架 | 空态+新建 | 重试 | 列表 |
| 内联新建 Modal | schema 加载转圈 | - | 校验提示 | 选中新建项+关闭 |
| 凭据列表 | 骨架 | 空态 | 重试 | 仅 SecretRef，无明文 |
| User 360 | 五区骨架 | - | 分区重试 | 五区聚合 |

### 3.7 样式方案 [必填]

| 维度 | 约定 |
|------|------|
| 样式与逻辑分离 | CSS Modules + Semi tokens，不与数据/业务混 |
| 设计 tokens | 间距/颜色/字号引 Semi CSS variables，不散落魔法值 |
| 响应式断点 | Console 桌面优先（≥1280px）；窄屏降级为单栏 |

**术语映射**（FEAT-F12，落点受 RULE-fluxion-console-001 + P-06）

| 内部术语 | 主流程 UI 文案 | 暴露区 |
|---------|--------------|--------|
| RuntimeProfile | 运行设置 | Platform/Advanced |
| Binding | 授权/绑定 | Users 详情 |
| Registry | 资源库 | Platform/Advanced |
| ExecutionSnapshot | （不暴露） | - |
| AgentDefinition | Agent | 全局 |
| Secret | 凭据 | Platform/Advanced（仅 ref） |
| Capability | （按 skill/tool/mcp 具名） | Build→Capabilities |

### 3.8 可访问性与兼容性 [按需]

| 维度 | 要求 |
|------|------|
| 可访问性 | 语义标签、`aria-*`、键盘可达、焦点管理（Semi 内置） |
| 浏览器兼容 | Chrome/Edge 最新两个稳定版 |

---

## 4. 风险与依赖 [按需]

| 风险ID | 描述 | 影响 | 应对 | 验证场景 |
|--------|------|------|------|---------|
| RISK-F01 | 后端 AgentDefinition/typed spec model/各 kind schema 未实现 | 资源接入表单无 schema 驱动 | 依赖后端 brief FEAT-B01/B06（typed spec model per kind + `/resources/{kind}/schema`） | S-03, S-04, S-05, S-06 |
| RISK-F02 | 术语映射遗漏 | 内部术语仍泄露 | S-13 DOM 文本断言全主流程；普通用户核心页底层术语暴露=0 | S-13 |
| RISK-F03 | Semi react19-adapter 顺序错 | 运行时样式异常 | main.tsx 首导断言 | S-01 |
| RISK-F04 | 凭据明文泄露到前端 | 违反规则 17 | 凭据列表只渲染 SecretRef + DOM 断言无明文 | S-07, S-13 |
| RISK-F05 | Phase 4/5 功能契约未锁导致后期重设计 | 后期 IA/契约漂移 | 本 brief 锁定冻结导航 + 组件/数据/API 契约，Phase 4/5 落地时不再重设计 | S-01, S-08, S-11, S-12 |

---

## Spec Compliance Matrix

> 零 N/A——本 brief 负责 6 条 required rule（另 1 条 console-channel 与后端共享，FE 为主）；其余 9 条 backend 规则见后端 brief。v0.3 采纳冻结导航 + 修 F-2，落点 section/item 不变，仅刷 artifact_sha256。

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|-----------|-------------|---------|---------|---------|----------------|
| `fluxion-console-channel#RULE-fluxion-console-001` | required | Console/Runtime 同仓共享 Contract；Web Chat 正式 Channel；未绑定仅 `/bind` | §3.2 / item: console-channel-ia + §3.5 / item: bind-flow | S-09 (bind 流程 E2E), S-14 (Workspace shell /bind) | applied |
| `frontend-semi-design#RULE-frontend-semi-001` | required | Semi 强制 + react19-adapter 首导 + 禁第二套库 | §3.1 / item: semi-stack + §3.7 / item: semi-tokens | S-01 (adapter 首导 + 无 antd/MUI) | applied |
| `frontend-component-specs#RULE-frontend-component-001` | required | 容器/展示分离 + 通用组件复用 Semi + SchemaForm/CapabilityPicker DRY | §3.3 / item: agent-studio-components | S-02 (Agent Studio 组件契约), S-05 (SchemaForm 共用), S-04 (CapabilityPicker) | applied |
| `frontend-directory-structure#RULE-frontend-directory-001` | required | 冻结导航目录 + 导航地图同步 | §3.2 / item: persona-ia-routes | S-01 | applied |
| `frontend-quality-standards#RULE-frontend-quality-001` | required | 禁 any / 禁滥用 ts-ignore / 类型安全 services | §3.5 / item: typed-services + §3.6 | B-01 (services 无裸 fetch + 类型安全) | applied |
| `fluxion-dfx#RULE-fluxion-dfx-001` | required | DFX 编码阶段证据（E2E + DOM 断言 + adapter 首导 + schema 驱动） | §3.6 / item: frontend-dfx | S-02, S-13, S-04 (内联新建) | applied |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| 冻结导航 | roadmap §6 Phase 4 TASK-C401 固定的 7 顶层组 Console IA，不随 Resource 增长（P-06） |
| Agent Studio | 构建/编辑/试跑 Agent 的核心工作台（TASK-C402） |
| Product API | 面向前端 BFF 的业务语义 API（`/studio/*` `/admin/*` `/platform/*`） |
| schema 驱动表单 | 由后端 `/resources/{kind}/schema` 驱动渲染的表单（ADR-012），一套组件渲染所有资源类型 |
| CapabilityPicker | Agent Studio 内绑定 capabilities 的组件，skill/tool/mcp 类型过滤 + 内联新建（对齐 §4.2） |
| 内联新建 | Agent Studio picker 内 Modal 弹 SchemaForm，建完即选，不跳离 |
| 创建 agent runtime | Console 创建 RuntimeProfile（运行态配置），不创建 Pod（规则 2/26/27） |
| 术语去暴露 | 内部术语不出现在主流程 UI，退到 Platform/Advanced；普通用户核心页底层术语暴露=0 |

---

*文档结束*
