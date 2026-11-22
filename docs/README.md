# 智能服务交付平台——详细设计 V1.4｜总体设计 V1.5

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
7. Console 不提供“中间件状态”菜单：PostgreSQL、Redis、NFS/PVC、Runtime/Worker Pod 状态属于运维体系，不进入业务 Console。菜单固定十一项，末项「系统设置」（`adminOnly`）只管理平台级业务默认值，不承载中间件状态。
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

完整分类、目标目录与迁移状态见 `00-详细设计索引与设计基线.md`。

能力 → 代码落点与 owner 见 `operations/engineering-map.md`（研发导航，非设计基线）。

### 项目基础文档

- `00-详细设计索引与设计基线.md`
- `01-总体设计说明书.md`
- `01-总体设计图/`
- `基础文档/02-核心领域与数据库.md`
- `基础文档/07-跨模块接口与协议.md`
- `基础文档/08-关键流程时序与状态机.md`
- `基础文档/09-DFX安全可靠性与测试.md`
- `基础文档/14-授权模型.md`
- `基础文档/15-Console字段词典.md`
- `configuration-inventory.md`

### 基础模块文档（框架层）

- `基础模块/03-Console-Platform.md`
- `基础模块/04-Agent-Runtime.md`
- `基础模块/05-IM-Gateway与Connector.md`
- `基础模块/06-执行内核与Skill运行时.md`
- `基础模块/10-Agent-Worker与异步任务.md`
- `基础模块/12-平台接入与Session.md`

### 核心功能文档（业务层）

- 20 Agent、授权与 Skill（待建）
- 21 MCP 与模型能力（待建）
- 22 对话、Run、任务与调度（待建）
- 23 结果与附件投递（待建）
- 24 记忆、经验、审计与可观测（待建）

### 历史与规划

- `历史/11-V1.2评审修订说明.md`
- `历史/13-V1.3评审修订说明.md`
- `历史/16-数据库接口与Artifact-Store复审修订说明.md`
- `历史/17-V1.4评审修订说明.md`
- `规划/18-浏览器引擎方案.md`

## V1.3 字段一致性复核

已完成 Agent / Skill / MCP / Model / User / ProjectPlatform / Task / Schedule / Audit 全模块字段收敛，统一词典见 `基础文档/15-Console字段词典.md`。
