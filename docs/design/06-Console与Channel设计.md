# 06 Console 与 Channel 详细设计

## 1. Console 定位

Console 是标准 Control Plane/配置入口，与 Runtime 同仓共享 Contract，但可独立部署。Console 故障不能阻断已发布 Agent 执行。

## 2. 信息架构

建议按用户任务而非底层 Resource 类型堆菜单：

### Build

- Agents
- Capabilities
- Workflows
- Test / Trace / Eval
- Publish

### Admin

- Users / User 360
- Governance / Policy / Approval
- Channels / Identity
- Operations
- Platform / Runtime health

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
