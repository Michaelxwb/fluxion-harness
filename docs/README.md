# 智能服务交付平台——详细设计 V1.4

## 1. 本版修订重点

V1.4 完成一轮全量评审修复：统一授权判定公式（含 `is_deleted`/`enabled` 谓词）、收敛 Run/Task 并发与取消语义、补齐 Task deadline 与 Final Delivery 幂等、保留 `ctx.http` 出网面、清理 V1.2 残留并刷新两份上游基线。完整问题清单与决策记录见 `17-V1.4评审修订说明.md`。

V1.3 修订重点（历史）：

V1.3 在 V1.2 基础上进一步吸收旧项目 `muad-openclaw/tools/session-manager` 已验证的经验，重点修正“项目平台认证”的抽象，同时同步收敛 Console/IM 交互口径。

本版新增/调整：

1. `ProjectPlatform` 不再以固定 `Bearer / Basic / OAuth2 / API Key` 认证枚举为核心，而改为 **PlatformAdapter 插件机制**。
2. 新增 `PlatformAdapterRegistry / PlatformSessionManager / PlatformSessionStore`：
   - 每个平台选择一个 `adapter_key`；
   - Adapter 自己实现登录、签名、Session 刷新/校验、请求认证；
   - Adapter 声明 `platform_config_schema` 与 `credential_schema`，Console 动态生成表单；
   - 新增平台如果可复用已有 Adapter，不写新代码；
   - 只有出现新的登录/签名/Session 协议时才开发新的 Adapter。
3. Session 权威凭据为各 Owner 表的明文列；Session 是可重建缓存，外置到 Redis，Runtime/Worker Pod 不保存权威 Session。
4. `credential_mode` 只表达“用户凭据/共享凭据如何选择”，不再与认证协议混在一个字段中。
5. 一个逻辑 Agent 支持配置 **0..N 个 IM 通道账号**；每个 `bot_id` 仍只路由到一个 Agent；`bot_id` 与 Runtime/Worker Pod 永远无绑定。
6. `control.bot_account` 移除 `(agent_id, channel)` 唯一约束，允许一个 Agent 配置多个同类 IM Bot。
7. Console 删除“系统设置/中间件状态”菜单；PostgreSQL、Redis、NFS/PVC、Runtime/Worker Pod 状态属于运维体系，不进入业务 Console。
8. Console 列表页不再重复展示页签标题/说明块，直接采用“左上操作 + 右上筛选 + 列表 + 右下分页”。
9. Console 字段名统一中文；领域名词 `Agent / Skill / MCP / bot_id` 等保留。
10. Background/Cron/Worker、单一意图与 ExecutionRouter 等 V1.2 结论保持不变。
11. Skill/MCP 用户范围统一为 `ALL/SELECTED`：用户先获得 AgentAccessGrant，Agent 再绑定 Skill/MCP，SELECTED 资源额外要求 SkillUserGrant/McpUserGrant；Runtime 在 Prompt/ToolRegistry 前完成交集过滤。

## 2. 当前部署单元

```text
muad-console-platform
muad-agent-runtime
muad-agent-worker
muad-im-gateway
```

外部基础设施：

```text
PostgreSQL
Redis
Artifact Store（NFS-backed RWX PVC）
OpenTelemetry Backend
```

## 3. 项目平台调用基线

```text
Skill
  -> ctx.platform.call(...)
  -> PlatformClient
  -> Egress Boundary
  -> ProjectPlatform Resolver
  -> PlatformAdapterRegistry
  -> CredentialResolver
  -> PlatformSessionManager
  -> PlatformAdapter
       - authenticate / refresh
       - validate
       - prepare_request
  -> Business Platform
```

其中：

- ProjectPlatform = “平台实例配置”；
- PlatformAdapter = “某一类平台认证/Session 协议的代码实现”；
- CredentialRef = “用户或共享凭据引用”；
- PlatformSession = “由凭据派生、可失效重建的 Session 缓存”。

## 4. 文档结构

- `00-详细设计索引与设计基线.md`
- `01-总体架构与部署详细设计.md`
- `02-核心领域与数据库详细设计.md`
- `03-Console-Platform详细设计.md`
- `04-Agent-Runtime详细设计.md`
- `05-IM-Gateway详细设计.md`
- `06-Skill-SDK与Egress-Boundary详细设计.md`
- `07-跨模块接口与协议详细设计.md`
- `08-关键流程时序与状态机详细设计.md`
- `09-DFX安全可靠性测试验收详细设计.md`
- `10-Agent-Worker与异步任务详细设计.md`
- `11-V1.2评审修订说明.md`（历史）
- `12-项目平台适配与Session详细设计.md`
- `13-V1.3评审修订说明.md`
- `14-用户范围与能力授权模型详细说明.md`
- `15-Console字段词典与一致性规范.md`
- `16-数据库接口与Artifact-Store复审修订说明.md`
- `17-V1.4评审修订说明.md`


## V1.3 字段一致性复核

已完成 Agent / Skill / MCP / Model / User / ProjectPlatform / Task / Schedule / Audit 全模块字段收敛，统一词典见 `15-Console字段词典与一致性规范.md`。
