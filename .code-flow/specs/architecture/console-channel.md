---
id: fluxion-console-channel
description: Fluxion Console/Chat 同仓边界、Web Channel、PlatformUser 与 bind 流程
stages: [design, plan, code, review]
enforcement: required
verifiers:
  - rule: RULE-fluxion-console-001
    type: manual
    config:
      checklist: 检查 Console/Runtime 同仓边界、Web Channel 身份绑定、Bind Code 安全和 Semi Design 前端规范。
      owner: project-owner
---

# Fluxion Console 与 Channel 规范

## Rules

- [RULE-fluxion-console-001] Console 与 Runtime 必须同仓共享 Contract 但可独立部署；Web Chat 是正式 Channel；未绑定身份在映射 PlatformUser 前只能执行 `/bind <code>`。

## Guidance

- Console 创建 RuntimeProfile/Resource，不创建 Agent Runtime Pod；Pod 由本地/Docker/Kubernetes 部署系统负责。
- Console 写 Registry，Runtime 读 Registry；Console 故障不得阻断已发布 Agent 执行。
- `console-web` 是超管/控制面；`chat-web` 是普通用户对话入口。
- Web/Mattermost/企业微信/Internal IAM Identity 映射统一 PlatformUser。
- Bind Code：单次、10 分钟有效、hash 落库、tenant-bound、失败 5 次冻结、禁止完整明文日志。
- 绑定后 Runtime 必须使用 `platform_user_id` 解析共享 Skill/MCP/UserContext。
- Console Web 与 Chat Web 统一使用 Semi Design，不允许 Ant Design 等第二套通用 UI 库。

## 禁止

- 禁止每个 Channel 建一套 User 模型。
- 禁止把测试聊天框当成正式 Web Channel。
- 禁止 Console 直接操作 Runtime 内存 Agent 对象。
