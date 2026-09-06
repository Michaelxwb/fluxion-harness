# Fluxion 70 Commits 前后端联合整改方案

> **适用基线**：`main` 分支 70 commits  
> **文档定位**：源码问题整改计划 + Console 产品化实施规范  
> **目标**：在不再大规模重画领域架构的前提下，优先收口前后端契约、管理员 Golden Path 和 Console 产品化一致性。  
> **重要约束**：后端领域模型保持完整；前端必须严格按统一的产品交互规范实现，不能继续把 Registry / Schema / Binding 等内部实现直接映射给管理员。

---

# 1. 一句话结论

当前 Fluxion 的核心领域架构已经基本进入可冻结阶段，剩余主要问题集中在两类：

1. **后端跨层契约尚未完全收口**：RuntimeProfile 默认规则、Provider Credential 真相源、ReleaseGate 发布契约、ExecutionSnapshot 完整性等仍存在实现不一致。
2. **前端产品化明显未完成**：虽然菜单和领域页面已经拆分，但多个页面仍缺新增入口、标准列表、搜索过滤、右下分页、只读详情、独立编辑器；能力中心还残留 Generic SchemaForm / 内联创建等旧 Registry UI 思维。

因此下一阶段不建议继续做大范围领域重构，而应转入：

> **Golden Path Closure + Console Productization Closure**

---

# 2. 本轮整改总目标

最终管理员链路必须能够从“空租户”开始，不依赖手工 Seed、直接 API、后台补 Resource 或隐藏 Binding：

```text
新增凭据
   ↓
连接模型服务
   ↓
测试连接
   ↓
发现 / 添加模型
   ↓
创建 Skill / Tool / MCP Server
   ↓
创建 Workflow
   ↓
创建智能体
   ↓
配置 Prompt / Model / Capability / Workflow / Runtime
   ↓
授权用户
   ↓
配置渠道
   ↓
保存 / 发布
   ↓
Web Chat / Channel 使用
   ↓
执行记录 / Trace / Snapshot 排障
```

任何一步都不能要求管理员理解：

- ResourceKind
- Registry
- Binding
- SecretRef
- RuntimeProfile ID
- DBOS Queue
- Worker
- Working Draft 内部实现
- Raw JSON Schema

---

# 3. 整改原则

## 3.1 架构层

继续保留：

- `AgentDefinition`
- `RuntimeProfile`
- `RuntimeInstance / RuntimePool`
- `ExecutionSnapshot`
- Capability Governance
- User Binding / Grant
- TenantPolicy
- ProviderDefinition / ModelDefinition / ModelPolicy
- Approval Gate
- Workflow / DBOS durable execution

但必须修正跨层契约不一致。

## 3.2 产品层

围绕管理员真实 Journey 组织：

```text
准备能力
→ 创建智能体
→ 授权用户
→ 接入渠道
→ 发布
→ 运营
```

## 3.3 前端统一 UI 规则

必须固定为：

> **Table 管理，Modal 创建，SideSheet 查看，Editor 修改。**

并且所有管理型列表页必须遵守以下标准：

> **左上：主要操作按钮**  
> **右上：过滤条件 + 筛选条件 + 模糊搜索**  
> **中间：标准 Semi Table**  
> **右下：总数 + Semi Pagination**

这不是“建议”，而是本轮前端整改的**硬性验收规范**。

---

# 4. 后端问题与整改

## 4.1 P0：RuntimeProfile 默认语义不一致

当前问题：

`AgentDefinition.runtime_profile_ref` 允许为空，并声明应由 Resolver 获取租户默认 RuntimeProfile。

但实际 Resolver 在为空时使用：

```text
RuntimeProfile.id == Agent.id
```

这导致 Agent 创建后如果没有手工准备同名 RuntimeProfile，可能出现：

```text
runtime_profile_not_found
```

### 正确语义

```text
Agent.runtime_profile_ref
        │
        ├── 已配置
        │     ↓
        │ exact RuntimeProfile
        │
        └── 未配置
              ↓
        Tenant Default RuntimeProfile
              ↓
        Platform Default RuntimeProfile
```

### 整改要求

- 删除 `Agent ID == RuntimeProfile ID` 隐式约定。
- 引入正式的 Tenant Default RuntimeProfile 解析规则。
- 没有租户默认时可回退到明确的 `platform-default`。
- Console 不需要给普通管理员暴露 `runtime_profile_id`。

### 验收

从 Console 新建 Agent，不配置 RuntimeProfile，也能够正常 Resolve。

---

## 4.2 P0：Provider Credential 存在双事实源

当前存在：

```text
ProviderDefinition.credential_ref
```

但 Runtime 实际主要从：

```text
ResourceBinding.credential_ref
```

取 Credential。

这会造成：

```text
Console 配了 credential_ref
Runtime 却仍报 provider binding not found
```

### 推荐最终规则

```text
EffectiveCredential =
    User Binding Credential Override
    ??
    Tenant Binding Credential Override
    ??
    ProviderDefinition.credential_ref
```

Binding 是 Override，不应成为 Provider 可运行的强制前提。

### 整改要求

- ProviderDefinition 作为默认 Credential 真相源。
- User/Tenant Binding 只做 override。
- Snapshot 冻结最终选择的 Credential Ref / Credential Version。
- 发布校验检查 Provider Credential 是否可解析。
- Console 只展示 Credential 选择器，不展示 raw `credential_ref`。

---

## 4.3 P0：Provider Binding 版本规则必须明确

当前 Provider Binding 匹配 Provider 时，对版本 Selector 的严格匹配语义不够明确。

### 必须二选一并写死

方案 A：

```text
Binding 与具体 Provider Version 绑定
```

则必须严格匹配 `provider_version`。

方案 B：

```text
Binding 绑定 Provider Logical ID
跨版本继承
```

则必须明确继承规则及 override 优先级。

### 建议

Credential Override 更适合逻辑 Provider 级继承，但如果出现版本级敏感参数，必须引入 typed override，不允许隐式混用。

---

## 4.4 P0：ReleaseGate 与通用 Resource Publish 耦合过重

当前 ReleaseGate 可能作用于通用 Resource Publish，而 Frontend `publishVersion()` 又可能只发送空 body。

这会导致 Production：

```text
Frontend:
POST publish
{}

Backend:
release_gate_enforced
gate required

→ Publish blocked
```

### 正确边界

ReleaseGate 应作用于真正的 Release Target，而不是所有 Versioned Resource：

```text
AGENT_DEFINITION     ✅
WORKFLOW             视需求
MODEL_DEFINITION     ❌ 默认不需要 Eval Gate
MODEL_PROVIDER       ❌
SKILL                ❌
TOOL                 ❌
MCP_SERVER           ❌
RUNTIME_PROFILE      ❌
```

### 整改要求

- 引入 `ReleaseGatePolicy.applies_to(kind)`。
- Agent Publish 做完整 Release Readiness。
- Frontend 与 Backend Publish Contract 必须统一。
- 不允许 FE 发送 `{}`，BE 却要求 gate。
- 保存与发布职责区分：
  - 保存：基础校验；
  - 发布：完整校验 + 必要 Release Gate。

---

## 4.5 P0：ExecutionSnapshot 还不是完整执行事实

当前设计目标是：

```text
ExecutionRequest
→ ContextResolver
→ ExecutionSnapshot
→ Runtime execute snapshot only
```

但 Credential / Memory / Provider 等执行依赖仍可能在 Runtime 内部再次 Resolve。

### 整改要求

ContextResolver 必须成为唯一执行事实收口点：

```text
AgentDefinition
RuntimeProfile
UserRuntimeState
TenantPolicy
Capabilities
ModelPolicy
Provider
Credential
Memory
Workflow
Channel/User Context
        ↓
ExecutionSnapshot
```

Runtime 不再自行重新决定：

- 使用哪个 Provider
- 使用哪个 Credential
- 使用哪个 Memory source
- 使用哪个 Capability version

### Snapshot Typed Pins

删除类似：

```text
plugin_versions = model_provider_pins
```

改为明确字段：

```text
model_versions
provider_versions
skill_versions
tool_versions
mcp_versions
workflow_versions
policy_versions
credential_versions
binding_versions
```

---

## 4.6 P1：ContextResolver 装配不完整

当前 composition root 中 ContextResolver 的 Credential / Memory 等依赖装配不完整。

### 整改要求

统一从 production/dev composition root 注入：

```text
ContextResolver(
    registry,
    credential_resolver,
    memory_retriever,
    policy_resolver,
    capability_resolver,
    model_policy_resolver,
    ...
)
```

不允许 Runtime 某个子模块单独再建 Resolver。

---

## 4.7 P1：Eval Target 需要从 RuntimeProfile 转向完整执行对象

EvalSet 当前如果主要绑定 RuntimeProfile，会弱化真实 Agent 产品评测。

### 推荐

```text
EvalSet.target:
  kind: agent_definition
  id: ...
  version: ...
```

Workflow 专项：

```text
kind: workflow
```

EvalRun 最终基于：

```text
ExecutionSnapshot
```

做 baseline / candidate 比较。

---

# 5. 前端统一整改规范

这是本轮必须固定的重点。

## 5.1 标准列表布局

所有管理型列表页必须使用统一布局：

```text
页面标题
页面说明

┌─────────────────────────────────────────────────────────────┐
│ [+ 主操作]                     [过滤] [筛选] [模糊搜索]   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│                      Semi Table                             │
│                                                             │
│ 名称      类型      状态      更新时间              操作   │
│ xxx       ...       正常      2026-09-02          编辑 ··· │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                             共 128 条   < 1 2 3 ... >       │
└─────────────────────────────────────────────────────────────┘
```

### 硬性规则

1. 主要新增/连接/添加按钮必须在**列表左上角**。
2. 过滤条件、筛选条件、模糊搜索必须在**列表右上角**。
3. 列表必须使用 Semi `Table`。
4. Pagination 必须在**列表右下角**。
5. 页脚同时展示总数 + Semi `Pagination`。
6. 禁止在列表底部左边显示分页。
7. 禁止重复出现“上一页 / 下一页 + Pagination”两套分页控件。
8. 行操作放最后一列。
9. 高频操作最多直接展示 1～2 个。
10. 其他动作进入 Semi `Dropdown`。
11. 状态使用 `Tag / Badge`。
12. Empty / Loading / Error 使用 Semi 标准组件。

---

# 6. 标准列表样例

以下为**必须参照的样例**。

## 6.1 智能体列表样例

```text
智能体
创建、配置和发布可供用户使用的智能体。

┌───────────────────────────────────────────────────────────────────────────┐
│ [+ 新建智能体]                         [状态 ▼] [模型 ▼] [搜索智能体 🔍] │
├───────────────────────────────────────────────────────────────────────────┤
│ 名称          模型              状态          更新时间            操作     │
│ 客服助手      deepseek-chat     已发布        09-02 20:10       编辑  ··· │
│ IT 助手       gpt-5             有未发布修改  09-02 19:32       编辑  ··· │
│ 财务助手      deepseek-chat     未发布        09-02 18:11       编辑  ··· │
├───────────────────────────────────────────────────────────────────────────┤
│                                      共 36 条      < 1 2 3 4 >            │
└───────────────────────────────────────────────────────────────────────────┘
```

### 行点击 / 操作

- 点击名称：打开右侧 `AgentDetailSideSheet`，纯只读。
- 点击“编辑”：进入 Agent Editor。
- `···`：
  - 查看版本历史
  - 发布
  - 删除
  - 复制等低频操作

---

## 6.2 标准 React/Semi 结构样例

```tsx
<PageHeader
  title="智能体"
  description="创建、配置和发布可供用户使用的智能体。"
/>

<Card>
  <div className="standard-list-toolbar">
    <div className="standard-list-toolbar__left">
      <Button theme="solid" type="primary" onClick={openCreateModal}>
        新建智能体
      </Button>
    </div>

    <div className="standard-list-toolbar__right">
      <Select placeholder="状态" />
      <Select placeholder="模型" />
      <Input
        prefix={<IconSearch />}
        placeholder="搜索智能体"
      />
    </div>
  </div>

  <Table
    columns={columns}
    dataSource={items}
    pagination={false}
  />

  <div className="standard-list-footer">
    <Typography.Text type="tertiary">
      共 {total} 条
    </Typography.Text>

    <Pagination
      currentPage={page}
      pageSize={pageSize}
      total={total}
      onPageChange={setPage}
    />
  </div>
</Card>
```

推荐统一样式：

```css
.standard-list-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.standard-list-toolbar__left,
.standard-list-toolbar__right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.standard-list-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
}
```

注意：

> 可以复用 `StandardListToolbar / StandardListFooter / StandardListCard` 这种布局组件，但不能重新创造 Generic Resource Form。

---

# 7. Create / Detail / Edit 规范

## 7.1 创建必须用独立 Modal

```text
CreateAgentModal
CreateWorkflowModal
CreateSkillModal
CreateToolModal
AddMCPServerModal
CreateUserModal
ConnectModelProviderModal
CreateCredentialModal
```

禁止：

```text
GenericResourceModal
ResourceKind Select
SchemaForm 自动渲染所有字段
Inline Create Form
```

## 7.2 Detail 必须只读 SideSheet

统一：

```text
Table
→ 查看
→ SideSheet
```

Detail 禁止：

- 编辑
- 保存
- 发布
- 添加用户
- 修改权限
- Input
- Select
- Switch
- TextArea

## 7.3 Edit 必须与 Detail 分离

复杂对象：

```text
Agent → Agent Editor
Workflow → Workflow Designer
Skill → Skill Editor
Tool → Tool Editor
MCP Server → MCP Editor
Model / Provider → Model Editor
```

简单对象允许 Edit Modal，但不能在 Detail SideSheet 内直接编辑。

---

# 8. 前端页面缺失矩阵与整改

## 8.1 智能体

### 当前缺口

- 新建按钮位置未完全按列表左上标准。
- 缺搜索。
- 缺状态过滤。
- 缺标准右下 Pagination。
- 行操作不完整。
- Agent 生命周期内部仍缺：
  - 用户授权
  - Channel 配置
  - Test Run
  - Evaluation

### 整改

列表统一为标准 Shell。

Agent Editor 至少包含：

```text
基本信息
Prompt
模型
能力
Workflow
高级运行设置
用户
渠道
测试
评测
版本
```

### 保留

- `CreateAgentModal`
- `AgentDetailSideSheet`
- 独立 Agent Editor
- 无感 Working Draft

---

## 8.2 工作流

### 当前缺口

- 缺“新建工作流”入口。
- 缺 Create Modal。
- 缺搜索 / 过滤 / Pagination。
- 缺操作列。
- Editor 内联铺在列表下方。
- 显式暴露“创建草稿 / 校验”。

### 整改

```text
工作流列表
[+ 新建工作流]      [状态] [搜索]
Table
右下 Pagination
```

Create：

```text
CreateWorkflowModal
→ 名称
→ 描述
→ 创建
→ 独立 Workflow Designer
```

Editor 只保留：

```text
[保存] [发布]
```

底层自动做基础/完整校验。

---

## 8.3 Skill

### 当前缺口

- 新建使用内联 SchemaForm。
- 无独立 Create Modal。
- 无标准搜索 / 过滤 / Pagination。
- 无行操作。
- 无详情 SideSheet。
- 无独立 Editor。

### 整改

```text
[+ 新建 Skill]      [来源] [状态] [搜索]
Table
右下 Pagination
```

`CreateSkillModal`：

- 名称
- 描述
- 在线创建 / 导入 Skill Package

复杂内容进入 Skill Editor。

---

## 8.4 Tool

### 当前缺口

同 Skill，并且缺真正业务类型。

### 整改

`CreateToolModal`：

```text
名称
描述
工具类型：
  HTTP API
  Platform Service
```

创建后进入 Tool Editor 配置：

- URL / Method / Headers
- 或 service_name / operation
- Credential
- Input Schema
- Output Schema
- Test Call

---

## 8.5 MCP

### 当前缺口

- 当前按 MCP Resource Schema 创建。
- 缺“添加 MCP Server”产品语义。
- 缺 Test Connection。
- 缺 Discover / Refresh Tools。
- 缺 Credential Select。
- 缺标准列表。

### 整改

```text
[+ 添加 MCP Server]       [Transport] [状态] [搜索]
```

`AddMCPServerModal`：

- 名称
- Transport
- URL / Command
- Credential
- 测试连接

成功：

```text
MCP Server
→ Discover
→ MCP Tools
```

MCP Tool 不允许手工创建。

---

## 8.6 凭据

### 当前缺口

这是 Golden Path blocker。

缺：

- 新增入口
- Create Modal
- 搜索
- 类型过滤
- 状态过滤
- Pagination
- 行操作
- 只读详情
- 编辑 / 轮换 / 禁用
- 主列表仍可能展示 SecretRef 等内部字段

### 整改

```text
凭据

[+ 新增凭据]       [类型 ▼] [状态 ▼] [搜索凭据]

Table:
名称
类型
状态
使用方
更新时间
操作

                           共 N 条   Pagination
```

`CreateCredentialModal`：

- 名称
- 类型
- Secret
- 描述

保存后禁止明文回显。

---

## 8.7 模型

### 当前缺口

同样是 Golden Path blocker。

缺：

- “连接模型服务”入口
- Provider Create Modal
- Credential Select
- Test Connection
- Discover Models
- Refresh Models
- Add Model
- 搜索
- 过滤
- Pagination
- Detail SideSheet

### 整改

标准入口：

```text
[+ 连接模型服务]
```

`ConnectModelProviderModal`：

- Provider 类型
- 名称
- Endpoint
- Credential Select
- `+ 新增凭据`
- Test Connection

成功后：

```text
Discover Models
→ deepseek-chat
→ deepseek-reasoner
```

Provider 不支持发现时：

```text
+ 手工添加模型
```

Credential 禁止填写 raw `credential_ref`。

---

## 8.8 用户

### 当前缺口

是当前最接近标准的一页，但仍需：

- 补模糊搜索。
- 补状态过滤。
- 统一右下 Pagination。
- 删除重复上一页/下一页 + Pagination。
- Agent Select 不应混淆为列表过滤。
- Agent-scoped Capability/Channel 权限 Journey 尚未完整。

### 整改

标准用户列表：

```text
[+ 添加用户]        [状态] [渠道] [搜索用户]

Table

                           共 N 条 Pagination
```

Agent 授权放入 Agent → 用户，不塞回用户创建流程。

---

## 8.9 执行记录

### 当前缺口

- Run / Workflow Run / Queue / Worker 混在一个页面。
- Run Detail 内联铺在列表下方。
- 缺搜索 / 完整过滤 / 标准分页。

### 整改

仅保留：

```text
执行记录

[状态] [类型] [Agent] [Workflow] [时间] [搜索]
Table
右下 Pagination
```

点击 Run：

```text
RunDetailSideSheet
├── Summary
├── Timeline
├── Model Calls
├── Tool Calls
├── Workflow
├── Trace
└── ExecutionSnapshot
```

删除产品页面中的：

- Queue Summary
- Worker Summary

---

## 8.10 操作审计

### 当前缺口

- 缺操作类型过滤。
- 缺操作者过滤。
- 缺对象类型过滤。
- 缺时间范围。
- 缺模糊搜索。
- Pagination 未严格右对齐。
- 缺 Detail SideSheet。

### 整改

```text
操作审计

                       [操作] [操作者] [对象类型] [时间] [搜索]

Table

                              共 N 条 Pagination
```

审计详情用只读 SideSheet。

---

## 8.11 授权规则 / Policy

### 当前问题

仍然通过 SchemaForm 内联新建，属于半成品。

### 建议

当前二选一：

A. 高级治理 UI 暂缓：
- 菜单置灰/隐藏；
- Runtime TenantPolicy / Approval Gate 保留。

B. 真正开放：
- `CreatePolicyModal`
- Policy Editor
- Detail SideSheet
- Search / Filter / Pagination

禁止继续保留 Generic SchemaForm 半成品。

---

## 8.12 平台概览

不套标准列表模板。

当前需逐步从“统计卡片”升级成管理员工作台：

- 模型连接异常
- Credential 异常
- Agent 发布失败
- Workflow 执行失败
- 高风险审批
- 最近异常运行
- 最近活动

支持点击跳转具体对象。

---

# 9. Agent 内部缺失功能

当前删除了部分旧一级菜单后，相关能力没有完全接回 Agent。

必须补：

## 9.1 Agent → 用户

- 添加用户
- 查看 Agent Access
- User Binding / Grant 产品投影
- 用户级 Capability 差异

## 9.2 Agent → 渠道

- 添加 Web Chat
- 企业微信
- Mattermost
- 微信
- Channel status
- Channel test / verify

## 9.3 Agent → 测试

调用已有 Test Run 能力：

```text
输入测试 Prompt
→ 执行
→ 展示 Model/Tool/Workflow Timeline
```

## 9.4 Agent → 评测

已有 Eval API 应接入 Agent 生命周期，而不是重新做一级 Eval 菜单。

---

# 10. 前端代码级约束

为了防止再次回流 Generic UI，建议增加架构检查：

普通产品页面：

```text
frontend/apps/console/src/pages/**
```

禁止直接 import：

```text
SchemaForm
GenericResourceForm
GenericResourceEditor
```

仅允许在：

```text
pages/internal/**
pages/debug/**
```

使用。

同时禁止：

- 前端生成 Resource ID。
- 前端写死 Version `1`。
- 产品页面直接编辑 SecretRef。
- 产品页面要求用户选择 ResourceKind。
- 产品页面内联铺开 Create Form。

---

# 11. API 产品化建议

Console 不应长期依赖通用：

```text
POST /resources
```

完成所有业务动作。

建议提供产品化 Application API：

```text
POST /agents
POST /workflows
POST /skills
POST /tools
POST /mcp-servers

POST /credentials

POST /model-providers
POST /model-providers/{id}/test
POST /model-providers/{id}/discover-models
POST /model-providers/{id}/models
```

内部仍可映射到 Resource Registry。

这样：

> Registry 是基础设施，Product API 是管理员语义。

---

# 12. E2E 必须重写为真实 UI Golden Path

现有 E2E 若大量通过直接 HTTP Seed Resource，会绕过前端缺陷。

本轮必须新增真正浏览器 Journey：

```text
EMPTY TENANT

UI:
新增 Credential
→ UI 创建成功

UI:
连接 Model Provider
→ 选择 Credential
→ Test Connection
→ Discover Models

UI:
创建 Tool
UI:
创建 Skill
UI:
添加 MCP Server

UI:
创建 Agent
→ 选择 Model
→ 添加 Capability
→ 保存
→ 发布

UI:
添加用户
→ Agent 授权

UI:
Web Chat
→ 对话
→ Runtime Execution
→ 执行记录可查看
```

### 禁止测试偷偷做

```text
page.request.post() 创建产品资源
seed same-id RuntimeProfile
seed Provider Binding
使用 dev.echo 代替真正 Provider
直接手写 Registry Resource
```

允许 Test Fixture 创建的只能是：

- 基础租户
- 测试管理员账号
- 外部依赖模拟器

产品对象必须通过 UI 建立。

---

# 13. 前端页面验收矩阵

所有以下列必须逐项 GREEN：

| 页面 | 左上主操作 | 右上过滤 | 右上搜索 | Semi Table | 右下分页 | 行操作 | Create Modal | Detail SideSheet | Editor | 禁止 Generic SchemaForm |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Agent | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| Workflow | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| Skill | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| Tool | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| MCP | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| User | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | N/A |
| Credential | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | N/A |
| Model | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | N/A |
| Audit | N/A | ☐ | ☐ | ☐ | ☐ | N/A | N/A | ☐ | N/A | N/A |
| Runs | N/A | ☐ | ☐ | ☐ | ☐ | ☐ | N/A | ☐ | N/A | N/A |

只有整张矩阵达到目标，才能宣布：

```text
Console Productization DONE
```

---

# 14. 整改优先级

## P0：前后端 Golden Path Blocker

1. RuntimeProfile default 语义修复。
2. Provider Credential 真相源统一。
3. ReleaseGate / Publish contract 修复。
4. ExecutionSnapshot 收口。
5. 凭据新增完整 Journey。
6. 模型服务连接完整 Journey。
7. 从空租户 Browser E2E。

## P1：前端产品化

1. 建立统一 Standard List Shell。
2. 所有管理列表迁移：
   - 左上主按钮；
   - 右上过滤 + 筛选 + 搜索；
   - 右下总数 + Pagination。
3. Skill / Tool / MCP 拆独立 Create Modal。
4. Workflow 重构为 Create Modal + 独立 Designer。
5. 所有 Detail 改 SideSheet。
6. Agent 补用户 / 渠道 / 测试 / 评测。
7. Runs 去 Queue / Worker 化。
8. Credential / Model 补 Detail / Edit / 操作列。
9. 删除产品页面 Generic SchemaForm。

## P2：体验和性能

1. Provider / MCP / Tool Test Connection。
2. Capability Dependency Planning。
3. 发布问题定位。
4. Version Diff。
5. Run Timeline / Trace / Snapshot。
6. Console Projection API。
7. 避免 Model 页 O(N) 请求。
8. 完整 Empty / Loading / Error / Permission 状态。

---

# 15. Definition of Done

本轮整改完成必须同时满足：

1. 管理员从空租户可完全通过 UI 创建第一个可运行 Agent。
2. Credential 有新增入口。
3. Model 有“连接模型服务”入口。
4. Skill / Tool / MCP 创建不再内联 SchemaForm。
5. Workflow 不再内联 Editor。
6. 所有管理型列表：
   - 左上主操作；
   - 右上过滤/筛选/搜索；
   - 中间 Semi Table；
   - 右下总数 + Pagination。
7. 所有详情 SideSheet 只读。
8. Edit 与 Detail 完全分离。
9. 前端不生成 Resource ID / Version。
10. 前端不展示 raw SecretRef。
11. 发布链路前后端契约一致。
12. RuntimeProfile 不再依赖 Agent 同名 Profile。
13. ProviderDefinition Credential 默认可直接运行。
14. Binding 只承担明确 override。
15. ExecutionSnapshot 是 Runtime 唯一执行事实。
16. Product E2E 不允许通过 API 偷偷 Seed Product Resource。
17. Queue / Worker 不再作为 Console 核心产品页面。
18. Agent 内补齐用户、渠道、测试、评测生命周期。

---

# 16. 最终结论

70 commits 当前已经不是“架构方向错误”的阶段。

下一步应停止继续重画大架构，集中处理：

```text
后端：
跨层契约一致性
ExecutionSnapshot 收口
Publish / Credential / RuntimeProfile Golden Path

前端：
统一标准列表
补齐新增入口
独立 Create Modal
SideSheet 只读详情
独立 Editor
打通 Credential → Model → Agent → User → Chat 全链路
```

前端本轮最重要的硬约束必须固定为：

> **列表左上放主要操作按钮；列表右上放过滤、筛选与模糊搜索；中间使用标准 Semi Table；列表页脚统一放在右下方，展示总数与 Semi Pagination。**

并继续遵守：

> **Table 管理，Modal 创建，SideSheet 查看，Editor 修改。**

本轮只有在真实 Browser Golden Path 从空租户跑通、前端页面验收矩阵全部收口后，才能认为 Fluxion Console 产品化整改真正完成。
