# 06 Console 与 Channel 详细设计

## 1. Console 定位

Console 是标准 Control Plane/配置入口，与 Runtime 同仓共享 Contract，但可独立部署。Console 故障不能阻断已发布 Agent 执行。

## 2. 信息架构

按管理员任务而非底层 Resource 类型组织菜单（Agent-centric）：

```text
平台概览

构建
├── 智能体
├── 工作流
└── 能力
    ├── Skill
    ├── Tool
    └── MCP

用户
└── 用户

治理
├── 授权规则        [高级治理 UI 暂缓]
├── 插件策略        [暂未开放]
└── 操作审计

运营
└── 执行记录

平台
├── 模型
└── 凭据
```

> 产品 UI 围绕 `AgentDefinition` 组织，但不代表领域对象都从属于它：Workflow、ProviderDefinition、ModelDefinition、Skill、Tool、MCP Server 仍是一等可复用定义。**UI 简化 ≠ Domain 简化**：能力治理（User Grant / Agent Allowlist / TenantPolicy 三维）在 UI 可表现为「配置用户在智能体中的能力」，后端不得退化为二维 checkbox 模型。

## 3. Agent Studio

Agent Studio 编辑 AgentDefinition：persona/instructions、model、runtime profile、capability allowlist、workflow/memory policy refs。禁止在 Agent Studio 保存某个用户的 Credential/config。

## 4. Capability 管理

Capability 页面区分：

- Definition；
- Agent Allowlist；
- User/Tenant Binding/Grant。

UI 必须能明确展示"用户拥有但该 Agent 不允许""Agent 允许但用户未授权"等状态，避免再次把三维权限压成一个 checkbox。

## 5. User 360

展示 Identity、Profile、Preference、Capability Grants、Policy、Memory、Activity。用户事实从 User Domain 读取，不从 AgentDefinition 反推。

## 6. Channel

统一 ChannelAdapter：inbound normalize、signature/decrypt、identity mapping、outbound send。Web Chat 为首个实现；WeCom/Mattermost 等只新增 Adapter。

未绑定 ChannelIdentity 只能进入绑定流程。

## 7. 前端

继续使用 Semi Design。Resource 表单由 typed spec JSON Schema 派生；产品级页面可以有领域专用 UI，但最终提交必须经过同一 typed model validate。

统一 UI Surface：

```text
Table      → 管理
Modal      → 创建
SideSheet  → 查看（只读）
Editor     → 修改
```

- 每种领域对象独立 Create Modal，禁止「通用 Resource 页 + ResourceKind Select」万能表单（`智能体` 页只展示 AgentDefinition，不混入 Model/RuntimeProfile/Plugin 等其他 kind）；
- Detail 统一右侧 SideSheet，严格只读；
- 编辑从列表直接进入专属 Editor / Studio，与 Detail 分离；
- 创建弹窗只采集最小建档信息（Create ≠ Configure）。
