# 平台路由修复局部 Plan（2026-11-12）

本文记录前一轮平台路由修复。专用JSON及文件规则分支现已由 [移除文件入口方案](remove-egress-file.plan.md) 取代，运行参数以统一SharedSettings为准。

## 1. 已授权范围与实现

承接用户“直接选择平台，只补充路径，自动拼接平台Base URL”和“做完他”，以及既有TASK-011管理面。保留旧任务历史，但此前TASK-011手动标done/verified不作为本轮终验依据。当前TASK-006的装配修复扩展到平台管理链路，沿用原Context，补入路由器发现的API、前端、UI、国际化、E2E及交付规范。

- Console校验用户输入的相对路径、方法、平台与限制，接口标识由方法和相对路径派生；拒绝完整URL、外部origin覆盖、未知平台及歧义路径。
- 内部只读服务接口 `/internal/v1/egress-policy` 使用当前租户ProjectPlatform配置解析origin和路径前缀，不包含凭据。平台地址变更进入新的投影revision；相同平台和接口保留计数身份。
- Run/Task只冻结是否启用管控和执行累计预算，不冻结公共规则JSON。Worker任务创建及定时触发从可信设置冻结启用选择；恢复不从脚本参数或当前设置重新决定启用状态。
- 受控执行启动从Console获取公共投影并单调发布到租户Redis域。已启用的宿主逐次发送读取已发布的当前公共规则；路由选择携带同一policy，准入时版本变化即拒绝，避免读取两版绕过规则。保存设置本身不宣称已同步发布Redis；后续受控执行装配推进发布。
- 租户首次使用时在PG锁内确认没有历史执行才创建Redis guard；有历史却丢失guard时拒绝重新初始化。不同租户保护域隔离。既有非租户域存量执行不自动迁移或清零。
- 镜像和本地启动提供仅含生命周期参数的默认配置，接口规则为空且public_rules_enabled=false；没有配置限制时不创建计量Redis连接。
- 首次新增滑窗以发布时刻为空历史起点并立即计数；扩大窗口沿用当前仍保留的样本，不阻断整条业务链路。SERVICE_DISCOVERY且没有已解析Base URL的平台不可配置此HTTP规则，界面禁选并解释。

## 2. 验证与边界

| 检查 | 真实边界与结果 |
|---|---|
| tests/console_platform/test_egress_platform_policy.py | 真实PG+ASGI API；租户隔离、非法平台/完整地址拒绝、前缀拼接、地址更新、首次初始化及状态丢失拒绝 |
| tests/sdk/test_console_egress_routing.py | 真实Redis+HTTP socket；窗口拒绝第二次物理发送、版本竞态拒绝、上游429/Retry-After/body保留；另有纯解析测试 |
| tests/agent_runtime/test_egress_root_recovery.py | 真实Run claim/PG registry/Redis/HTTP；新增公共规则独立于执行累计预算的正式宿主装配。Console返回使用typed fixture，不能冒充完整Console→Runtime E2E |
| tests/agent_worker/test_task_service.py | 真实PG任务创建；只取一次可信设置，冻结启用选择且hash匹配 |
| e2e/tests/settings-ui.spec.ts | 真实构建产物+mock API的component测试；选择平台、连续输入路径、只读预览、自动生成接口标识、保存内容及默认空规则 |

本轮定向结果见各测试实际输出。项目级CI与Code Flow终验仍由runner裁决，不从上述局部通过推导全需求通过，不伪造Gate pass。原S-03/E-02重型Linux边界与需求级review未运行者继续保留待验证状态。

## Spec Compliance Matrix

| Spec/Rule | 本次应用 | verifier与证据边界 |
|---|---|---|
| harness-api#RULE-api-002 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-api#RULE-api-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-arch#RULE-arch-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-data#RULE-data-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-e2e#RULE-e2e-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-frontend#RULE-front-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-i18n#RULE-i18n-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-log#RULE-log-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-mcp#RULE-mcp-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-project-platform#RULE-platform-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-rel#RULE-rel-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-secret#RULE-secret-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-skill#RULE-skill-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-snapshot#RULE-snapshot-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-test#RULE-test-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-time#RULE-time-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-ui#RULE-ui-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
| harness-worker#RULE-worker-001 | 保留既有规范；平台路由修复遵循统一封套、平台地址权威、共享SDK、严格类型与生命周期约束 | 对应Spec原verifier；大型验证由项目CI承担，不冒充已通过 |
