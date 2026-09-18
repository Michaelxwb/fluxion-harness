> **V1.4 详细设计覆盖说明**：本文件是此前总体/Playbook 基线快照。V1.3 已将项目平台认证修正为 PlatformAdapter + 外置 Session 模型，并允许一个 Agent 配置多个 IM 通道账号；V1.4 已将 Agent Worker 纳入 Phase 1、简化 Skill 包格式并固定 Console 菜单。若本基线与 V1.4 详细设计冲突，以 V1.4 00~17 为准；后续总设/Playbook 应同步刷新。

> **用户范围补充口径**：Skill/MCP 不再使用 PUBLIC/PRIVATE 作为核心用户可见性语义；统一使用 `user_scope=ALL/SELECTED`。ALL 表示全部 Agent 授权用户，SELECTED 表示指定用户；两者都不能绕过 AgentAccessGrant 与 Agent Binding。

# 智能服务交付平台——用户场景与 Playbook 旅程设计 V7

> **版本定位**：Skill-first + 自研 Stateless Agent Runtime
>
> **V7 核心变化**：本版不再以 Capability/接口原子化作为 SOP 开发主路径，而是回归“Skill 是业务能力与 SOP 的最小发布单元”。平台保留并强化 OpenClaw 第一版中已经验证有效的用户、Agent、Skill、MCP、Model、项目平台凭据、企业微信绑定等产品模型，替换 OpenClaw Runtime 为自研无状态 Agent Runtime。

---

## 0. V7 关键修订

相对 V6，本版做以下结构性调整：

1. **Phase 1 后台部署单元为 4 个 Image**：`console-platform`、`agent-runtime`、`agent-worker`、`im-gateway`。
2. `console-web + console-api` 合并为 **console-platform**：React/TypeScript/Semi Design 前端静态资源随 Python/FastAPI 后端同镜像交付。
3. `agent-runtime` 使用 **Python + LangGraph**，作为完全无状态执行面；不按用户创建 Pod，不保留用户本地状态。
4. `im-gateway` 统一改为 **Python**，企业微信 V1 直接复用企业微信官方 Python AI Bot SDK，不再依赖 OpenClaw Plugin Runtime。
5. **Phase 1 建设 Agent Worker**：后台任务、定时任务、Parent-Child 并行、lease reclaim 与 Final Delivery 均属 Phase 1 交付范围（见 doc 10），不再是 Phase 2 保留项。
6. **Skill-first**：业务 SOP、接口组合、数据转换、`if/for/while`、业务算法、结果加工优先写在 Skill Python 中。
7. **Capability 不再是 Phase 1 主路径和必选产品对象**。未来只有出现高复用、高风险写、统一大结果治理等真实需求时，再把部分接口固化为 Runtime Tool/治理适配层。
8. 平台统一提供 **Egress Boundary**，但 Phase 1 不独立成 `egress-proxy` Image；它作为 `agent-runtime` 与 `agent-worker` 共享的安全边界，统一完成凭据解析、逻辑地址解析、Egress Allowlist、审计与限流。
9. Skill 不直接持有真实账号、Token、Cookie、Secret；Skill 通过 `ctx.platform / ctx.http / ctx.mcp` 发起逻辑调用。
10. Agent Runtime 引入以下 Harness 级能力：**Skill Lazy Load、统一 ToolRegistry、RuntimeSnapshot、Hook 生命周期、Canonical History 与 LLM Context 分离、Artifact 外置、Model Recovery**。
11. Service 在 Phase 1 不再是 Skill 开发和上线的必经对象。Phase 1 主链路是 `Agent → Skill/MCP → 业务平台`；ServiceExecution 已从设计中删除，正式 Service Release 进入 Phase 2。
12. 保留 `User → AgentAccessGrant → Agent` 的授权模型；不要求普通用户理解或逐个授权 Skill 内部接口。
13. 保留 `user_scope=ALL/SELECTED + 指定用户 grant` 的 Skill/MCP 用户范围机制，支持“快改、快传、指定用户复测、快速回退”。
14. User Memory 仍然是 Phase 1 能力，但仅承载长期用户上下文，不替代实时业务数据。

---

# 1. 项目背景、目标与设计原则

## 1.1 业务背景

MSS 已经积累大量业务平台能力、成熟 SOP 和专家经验，但很多服务交付仍依赖服务人员人工串联：

```text
理解客户需求
→ 查询客户和服务关系
→ 查询设备/资产
→ 进入业务系统
→ 创建任务
→ 查询状态
→ 处理异常
→ 获取结果
→ 分析结果
→ 整理交付内容
```

问题并不是底层能力不存在，而是缺少一个低成本、可复用、可持续升级的 Agent 承载方式。

第一版基于 OpenClaw + Skill 的实践已经验证了三件事情：

- Skill 作为 SOP 载体，开发速度快，业务人员容易理解；
- 企业微信 Bot、绑定码、平台用户、业务凭据等产品链路是可行的；
- OpenClaw 的有状态用户/Pod/Workspace 模型不适合作为生产环境共享 Runtime。

V7 因此不再重做一套 Capability/Workflow 平台，而是在保留 Skill-first 产品体验的前提下，把 OpenClaw Runtime 替换为自研无状态 Agent Runtime。

## 1.2 当前要解决的四个根问题

### 根问题一：标准服务依赖人工串联

目标从：

```text
人工找入口 + 查参数 + 执行 + 跟踪 + 分析
```

变成：

```text
用户表达目标
→ Agent 理解与澄清
→ Skill 完成可自动步骤
→ 只在必要时请求用户判断
→ 聚合真实业务结果
```

### 根问题二：SOP 开发不应退化成“低代码平台开发”

如果把每个接口都先建模为 Capability，再通过 Mapping、Expression、Loop、Condition、Workflow 串起来，最终会把简单的 Python 业务逻辑变成复杂平台配置。

V7 明确：

> **业务人员和 SOP 开发者的最小发布单元是 Skill，不是接口。**

### 根问题三：Skill 不能重新承担所有基础设施问题

Skill 可以写业务逻辑，但不应该每个 Skill 重复处理：

- Secret 保存；
- 用户凭据解析；
- 内部服务真实地址；
- 出网 allowlist；
- 审计；
- 统一 Trace；
- Agent Runtime 生命周期。

这些共性能力由 Runtime / Egress Boundary 统一提供。

### 根问题四：Agent Runtime 必须能真正横向扩展

任意一轮请求都可以进入任意 Runtime 实例：

```text
Turn 1 → runtime-1
Turn 2 → runtime-3
Turn 3 → runtime-2
```

用户、Conversation、Memory、Skill Artifact、配置、凭据等都不能依赖 Runtime Pod 本地保存。

## 1.3 设计目标

1. 普通用户一句话发起真实 MSS 工作。
2. SOP 开发者继续使用 Python、pytest、PyCharm/VS Code 开发业务逻辑。
3. 新接口出现时可以直接在 Skill 中使用，不要求先建 Capability。
4. Skill 修改后分钟级重新导入，指定用户立即验证。
5. Agent Runtime 无状态，可独立扩缩。
6. Skill、MCP、Model、Agent、用户与企微入口统一由 Console 管理。
7. Runtime 具备生产级上下文、Tool、恢复、Trace、安全边界能力。
8. Phase 1 建设 Agent Worker 等真实旅程证明必要的系统；不提前建设 Workflow Designer、通用 Approval Center 等未被真实旅程证明必要的系统。

## 1.4 总体设计原则

1. **用户旅程优先于平台对象完整性。**
2. **Skill 是 Phase 1 的 SOP 最小发布单元。**
3. **Tool 是 Agent Runtime 内部执行原语，不是业务人员必须维护的产品对象。**
4. **MCP 是标准外部工具扩展方式，不要求内部所有业务 API MCP 化。**
5. **内部业务平台优先通过 Platform SDK / HTTP 调用。**
6. **业务数据处理、条件、循环、聚合允许直接写在 Skill Python。**
7. **Agent Runtime 无状态，权威状态全部外置。**
8. **Console 是控制面，Runtime/Worker 是执行面；同仓库但不同 Image。**
9. **版本以 RuntimeSnapshot 保证一次 Run 内不漂移。**
10. **Canonical History 不等于发给 LLM 的 Context。**
11. **所有 Tool/MCP 最终统一进入 ToolRegistry。**
12. **Skill 默认按需加载正文，Catalog 常驻、正文 Lazy Load。**
13. **凭据不进入 Skill 代码和 Skill 日志。**
14. **Egress Boundary 是安全边界，但 V1 不为边界本身强拆微服务。**
15. **Phase 1 同步优先，并由 Agent Worker 承载后台任务、定时任务与并行批量任务。**
16. **按用户范围（ALL/SELECTED）逐步放开优先于复杂流量放量策略。**
17. **Memory 只保存长期用户上下文，不保存实时业务事实。**
18. **不为了未来可能需要的通用性牺牲当前 SOP 的开发效率。**

---

# 2. 目标用户与总体用户旅程

## 2.1 核心用户

| 用户 | 典型角色 | 核心目标 |
|---|---|---|
| 普通用户 | 服务经理、运营人员、交付人员 | 快速完成一项真实 MSS 工作 |
| 管理员 | 平台管理员、服务管理员 | 管理 Agent、用户、模型、Skill、MCP、凭据和 IM 入口，并保障可运营 |
| SOP / Agent 开发者 | 服务专家、Python 开发人员、Agent 应用开发者 | 把成熟人工 SOP 快速转化为可复用 Skill，并持续迭代 |

## 2.2 三类用户关系

```text
SOP / Agent 开发者
      │
      │ 开发 / 上传 Skill、配置 MCP
      ▼
   Agent 能力
      │
      ▼
    管理员
      │
      ├─ 配置 Model
      ├─ Agent 绑定 Skill/MCP
      ├─ 用户授权
      ├─ 配置企业微信 Bot
      └─ 调整用户范围（ALL/SELECTED）
      │
      ▼
   普通用户
      │
      ▼
 企业微信 / Future WebChat
      │
      ▼
 Agent Runtime
      │
      ▼
 Skill / MCP / Platform
```

## 2.3 Phase 1 黄金旅程

```text
开发者：
人工 SOP
→ Python Skill
→ 本地 Mock / Dev 联调
→ pack
→ Console 导入
→ user_scope=SELECTED
→ 指定测试用户

管理员：
创建 Agent
→ 配 Model
→ 绑定 Skill/MCP
→ 授权用户
→ 配置 WeCom Bot
→ 观察审计
→ 切换 user_scope=ALL

普通用户：
WeCom
→ /bind（首次）
→ “帮 A 客户做一次策略检查”
→ Agent 理解目标
→ Lazy Load 对应 Skill
→ Skill 调业务平台
→ 必要时澄清/确认
→ 返回业务结果与建议
```

---

# 3. 普通用户 Playbook

## 3.1 U01：发起并确认一项服务

### When

用户需要为某个客户完成策略检查、漏洞复测、资产检查、报告分析等标准工作。

### Do-What

用户只表达客户与目标：

> “帮 A 客户做一次策略检查。”

### How-To

```text
WeCom 消息
→ IM Gateway 标准化 ChannelEnvelope
→ 根据 bot_id 路由 Agent
→ 根据 ChannelIdentity 映射 PlatformUser
→ 校验 AgentAccessGrant
→ Agent Runtime 创建 Run + RuntimeSnapshot
→ PromptBuilder 注入 Agent Instructions / User Memory / Skill Catalog
→ LLM 识别需要 policy-check Skill
→ load_skill(policy-check)
→ Skill 获取客户、设备、服务关系
→ 如存在歧义，返回澄清问题
→ 形成确认摘要
→ 用户确认后继续
→ 返回结果
```

### 验收

- 用户不需要知道底层平台入口；
- 用户不需要录入平台已经能够查询的信息；
- 客户重名、设备为空、权限不足时不得静默猜测；
- 本次 Run 使用固定 RuntimeSnapshot；
- 所有 Skill/MCP/平台调用可审计。

## 3.2 U02：处理短时任务与长任务边界

### Phase 1

短时任务在当前 Run 中执行：

```text
用户确认
→ Skill 发起业务调用
→ 几秒到几十秒内完成
→ 返回结果
```

如果底层平台创建了明显的长任务：

```text
创建 external_task_id
→ 提交后台任务（Agent Worker）
→ 返回“任务已创建，可稍后查询”
→ 用户可离开，当前 Run 结束
→ Worker 继续等待/轮询，有副作用动作按幂等键执行
→ 完成后经 IM Gateway Final Delivery 主动投递
```

跨小时/跨天任务、Crash 后恢复、持久 Timer/Wait、Durable Human Checkpoint 与后台主动完成通知均由 Phase 1 Agent Worker 承载（见 doc 10）。

## 3.3 U03：只在必要时人工介入

### Phase 1

由 Agent/Skill 在会话内完成：

```text
确定性问题
→ 自动处理

业务不确定 / 高风险
→ LangGraph interrupt
→ 向用户解释原因和选择
→ 用户确认
→ resume
```

提示至少包含：

- 发生了什么；
- 当前影响；
- 系统已完成哪些动作；
- 为什么不能自动决定；
- 可选方案；
- 每个方案的影响。

## 3.4 U04：获得可交付业务结果

用户需要的是业务结果，而不是 Tool 成功状态。

Agent 最终回答可以组合：

- 结构化检查结果；
- 异常项；
- 风险说明；
- 不支持项；
- 专家建议；
- 后续动作；
- 大结果摘要与关键结论（完整产物见 Console 运行审计）。

Runtime 对大结果采用：

```text
Tool/Skill 大结果
→ ArtifactManager 写 Artifact Store（NFS-backed RWX PVC）
→ DB 只存 storage_key
→ Context 只保留 preview + metadata + artifact_ref
```

避免把大 JSON 直接长期塞入 LLM Context；IM 消息侧只交付摘要与关键结论，V1.4 不面向 IM 用户提供 Artifact 下载，完整产物可在 Console 运行审计中查看。

## 3.5 U05：首次进入即可使用

```text
管理员预先创建 PlatformUser
→ 授权 Agent
→ 配置项目平台凭据
→ 配置 Bot

用户首次进入 WeCom Bot
→ 未绑定
→ /bind CODE
→ 建立 ChannelIdentity → PlatformUser
→ 开始使用
```

用户不需要安装 Runtime、模型、Skill 或本地开发环境。

## 3.6 U06：跨会话保持 User Memory

### Phase 1 范围

Memory 按 `tenant + platform_user` 持久化，可跨 Conversation 使用。

Memory 适合保存：

- 输出语言偏好；
- 报告模板偏好；
- 常用交付风格；
- 用户明确要求长期记住的上下文。

不保存：

- 客户当前设备；
- 当前扫描状态；
- 最新权限；
- 实时资产；
- 当前业务任务结果。

### WebChat 边界

Phase 1 只实现 WeCom Adapter，因此“真正跨 WeCom/WebChat”作为未来验收；V1 数据模型必须按 PlatformUser 存 Memory，不能按 ChannelIdentity 存，从而保证未来新增 WebChat 时自然复用。

## 3.7 U07：内置命令

V1 固定：

```text
/bind <code>   建立渠道身份映射
/skills        查看当前 Agent 对当前用户可见的 Skill
/new           新建 Conversation
/stop          请求取消当前 Run
```

`/skills` 返回的是经过 `Agent binding + user_scope + user grant` 解析后的结果，不泄露用户无权限的 SELECTED Skill。

---

# 4. 管理员 Playbook

## 4.1 A01：创建并配置 Agent

管理员在 Console 中维护：

```text
Agent
├── name / description
├── instructions
├── model
├── skills
├── mcp servers/tools
├── runtime parameters
├── memory policy
└── IM access
```

Agent 保存后对**新 Run**生效；当前 Run 使用 RuntimeSnapshot，不发生中途漂移。

## 4.2 A02：把 Agent 开放给正确的人

主授权模型：

```text
PlatformUser
     ↓
AgentAccessGrant
     ↓
Agent
```

最终业务权限由三层共同决定：

```text
AgentAccessGrant
+ 按 credential_mode 解析项目平台凭据
  USER_ONLY：仅使用用户凭据
  SHARED_ONLY：仅使用平台共享凭据
  USER_THEN_SHARED：优先用户凭据，缺失时回退共享凭据
  NONE：无需凭据
+ 外部业务系统自身 RBAC/ACL
= 最终可执行范围
```

共享凭据每个 ProjectPlatform 最多 1 个。

## 4.3 A03：配置企业微信入口

V1 产品上不建设独立 Channel 管理中心，而是在 Agent 详情配置：

```text
Agent
└── IM 接入
    └── IM 通道账号（0..N）
        ├── bot_id
        └── secret_ref
```

V1 约束：

```text
一个 Agent → 0..N 个 IM 通道账号
一个 bot_id → 唯一归属一个 Agent
一个 IM Gateway → 多个 Bot WebSocket
```

`secret` 存 Secret Provider/安全存储，页面仅展示掩码。

## 4.4 A04：身份、路由、授权三分离

```text
external_user_id → ChannelIdentity → PlatformUser
bot_id           → Agent
PlatformUser     → AgentAccessGrant → Agent
```

`/bind` 只建立第一条关系，不创建 Agent 授权，也不改变 bot_id 路由。

## 4.5 A05：定位失败 Run

Phase 1 不做复杂 Execution Timeline，但必须可回答：

- 谁发起；
- 哪个 Agent；
- 哪个 RuntimeSnapshot；
- 用了哪个 Skill Artifact；
- 调用了哪些 MCP/Platform Tool；
- 失败在哪一步；
- Provider 重试过几次；
- 外部系统返回什么错误；
- Trace ID 是什么。

数据来自：

```text
RunRecord
ToolCallAudit
EgressAudit
ModelInvocationAudit
OpenTelemetry Trace
```

## 4.6 A06：按指定用户验证并放开 Skill / MCP

```text
导入 Skill v2
→ user_scope=SELECTED
→ 指定 skill_user_grant
→ 指定用户下一次 Run 可见
→ 观察 Run/Tool/Egress Audit
→ 修复后重新导入 v3
→ 指定用户复测
→ user_scope=ALL
```

出现异常：

```text
移除 grant / 保持或切回 user_scope=SELECTED
→ 新 Run 立即不可见
→ 已开始的 Run 继续使用自己的 RuntimeSnapshot
```

MCP 使用同样机制。

## 4.7 A07：模型管理

Console 支持：

- Provider；
- Model；
- base_url；
- secret_ref；
- 默认参数；
- enable/disable；
- Agent 绑定。

Runtime 通过 ModelGateway + Provider Adapter 使用；模型 Retry/429/5xx/Timeout/Cancellation 统一由 Runtime RecoveryPolicy 处理，而不是写进 Skill。

---

# 5. SOP / Agent 开发者 Playbook

## 5.1 D01：先判断是否应该做成 Skill

Phase 1 默认：

> 一个成熟 SOP，如果主要是几秒到几十秒的业务查询、数据加工、条件判断和结果整理，就优先实现为 Skill。

适合 Skill：

- 业务规则；
- Python 数据处理；
- if/for/while；
- 多接口组合；
- 短时业务调用；
- Prompt/模板；
- 专家判断；
- MCP 调用。

不适合在当前 Run 内同步完成：

- 数小时持续执行；
- crash 后继续；
- 可靠 Timer；
- Durable Human Checkpoint；
- 跨天任务。

这些由 `execution: async/auto` 提交 Phase 1 Agent Worker，以 durable 后台任务承载。

## 5.2 D02：在 IDE 中开发 Skill

标准工程：

```text
policy-check/
├── SKILL.md
├── scripts/
│   └── main.py          # 可选
├── references/          # 可选
├── assets/              # 可选
└── tests/               # 可选
```

`SKILL.md` 正文描述：

- Skill 解决什么问题；
- 什么时候应该使用；
- 输入/输出约定；
- 业务限制；
- 调用原则；
- 必要确认点。

`SKILL.md` frontmatter 描述最小机器可读元数据，例如：

```markdown
---
name: policy-check
description: 为指定客户执行设备策略检查；当用户要求策略检查、基线检查或检查异常策略时使用。
execution: async
platform_label: MSS
---
```

`execution: sync|async|auto` 与 `platform_label` 均为可选；版本、checksum、`user_scope` 与指定用户授权属于平台控制面元数据，不写入 SKILL.md。

## 5.3 D03：Skill 内直接组织业务逻辑

示例：

```python
def run(ctx, input):
    customer = ctx.platform.call(
        service="customer-service-mgr",
        operation="get_customer",
        payload={"customer_id": input["customer_id"]},
    )

    devices = ctx.platform.call(
        service="asset-service",
        operation="list_devices",
        payload={"customer_id": customer["id"]},
    )

    supported = [x for x in devices if x["status"] == "ONLINE"]
    if not supported:
        return {"status": "NEED_USER", "reason": "没有可执行设备"}

    return normalize(customer, supported)
```

开发者不需要先在 Console 建 `customer.get`、`device.list` 等 Capability。

## 5.4 D04：Egress Boundary 对 Skill 的约束

Skill 可以表达“要访问什么”，但不能自行管理真实凭据。

推荐：

```python
ctx.platform.call(...)
ctx.http.get(...)
ctx.mcp.call(...)
```

Runtime Egress Boundary 统一完成：

```text
CredentialResolver
ServiceResolver
Allowlist
Audit
RateLimit
HTTP/MCP Client
```

禁止把用户 Secret 写入：

- Skill package；
- SKILL.md；
- 日志；
- LLM Prompt。

## 5.5 D05：本地 Mock 测试

```text
Skill
→ MockSkillContext
→ fake platform / fake mcp / fake artifact
→ pytest
```

目标是让 SOP 数据处理和业务规则可以在没有整个平台时快速测试。

## 5.6 D06：开发环境真实联调

平台提供 Dev SDK / Developer Token：

```text
本地 Skill
→ Skill SDK
→ Dev Gateway/API
→ 测试用户身份
→ Dev/Test 业务平台
```

Skill 代码与生产运行时保持同一 `SkillContext` 语义。

## 5.7 D07：打包和导入

```text
skill validate
→ skill test
→ skill pack
→ xxx.zip
→ Console 导入
```

导入校验：

- 包结构（必需 `SKILL.md`，路径穿越检查）；
- frontmatter（`name/description` 有效，`execution/platform_label` 可选）；
- Secret 基础扫描；
- checksum；
- package size。

导入产生 immutable Artifact。新修改生成新 Artifact，不覆盖历史版本。

## 5.8 D08：Agent 绑定 Skill

```text
Skill Artifact
       ↑
Agent ─┘
```

Console 中 Agent 选择哪些 Skill 可供 Runtime 使用；Skill 详情反查哪些 Agent 使用它。

Runtime 不会把所有 Skill 正文一次性注入 Prompt，而是：

```text
Skill Catalog(name + description)
→ 模型判断
→ load_skill(name)
→ 加载 SKILL.md 正文
```

## 5.9 D09：MCP 接入

MCP Server 由 Console 注册并绑定 Agent；Runtime 启动 Run 时发现允许的 MCP Tool，并转换为统一 `ToolDefinition` 注册进 ToolRegistry。

```text
MCP Tool
→ MCP Adapter
→ ToolRegistry
→ Hook / Policy / Audit
→ Execute
```

MCP 不绕过 Runtime 的 Tool 管理。

## 5.10 D10：脚本快改快传

```text
发现字段处理错误
→ IDE 改一行 Python
→ pytest
→ pack
→ 导入新 Artifact
→ user_scope=SELECTED
→ 指定用户下一次 Run 使用新 Artifact
→ 验证
→ user_scope=ALL
```

目标：分钟级迭代，不要求先改 Capability、Workflow、Mapping、Service Release。

---

# 6. Agent Runtime 运行模型

## 6.1 一次 Run 的核心流程

```text
RunRequest
  ↓
ResolveIdentity / Authorization
  ↓
Create RuntimeSnapshot
  ↓
Load Conversation + UserMemory
  ↓
Build Skill Catalog + Tool Registry
  ↓
PromptBuilder
  ↓
LangGraph Agent Loop
  ├─ model
  ├─ load_skill
  ├─ tool/mcp
  ├─ skill
  └─ interrupt/resume
  ↓
Persist Canonical Events
  ↓
Stream Result
```

## 6.2 RuntimeSnapshot

至少固定：

```text
run_id
agent_id + revision
instructions revision
model provider/model/config revision
skill artifact ids + checksum
mcp server/tool revisions
tool policy revision
memory policy revision
runtime config
```

当前 Run 内不漂移；新配置只影响新 Run。

## 6.3 Canonical History 与 LLM Context 分离

```text
Canonical Event/History
        │
        └── ContextBuilder
              ├─ recent messages
              ├─ summary
              ├─ user memory
              ├─ loaded skill
              ├─ artifact preview
              └─ tool results preview
                    ↓
                 LLM Context
```

压缩的是“请求上下文”，不是历史事实。

## 6.4 ToolRegistry

Runtime 的所有可执行工具统一注册：

```text
Built-in Tool
Skill Loader Tool
MCP Tool
Platform Tool
Future Browser/File Tool
```

统一经过：

```text
schema validation
→ pre_tool hook
→ permission/policy
→ immutable PreparedToolCall
→ execute
→ post_tool hook
→ audit
```

## 6.5 Hook 最小生命周期

V1 代码层提供：

```text
on_user_message
pre_tool_use
post_tool_use
on_stop
```

暂不做通用 Hook Console。

## 6.6 Model Recovery

统一处理：

- 429；
- 5xx；
- Retry-After；
- backoff + jitter；
- deadline；
- cancellation；
- provider timeout；
- prompt too large；
- fallback（若 Agent 配置允许）。

---

# 7. Console 产品模型

Phase 1 Console 菜单固定为：

```text
概览
Agent
Skill
MCP
模型
用户
项目平台
后台任务
定时任务
运行审计
```

不提供“系统设置/中间件状态”菜单；Channel 不单独做顶层菜单，在 Agent 的“IM 接入”中维护。

### Agent

- 基本信息；
- Instructions；
- Model；
- Skill；
- MCP；
- 用户授权；
- IM 接入；
- 运行记录。

### Skill

- 导入；
- 版本；
- checksum；
- 用户范围（ALL/SELECTED）；
- 指定用户；
- 使用 Agent；
- SKILL.md frontmatter（name/description/execution/platform_label）；
- 校验结果。

### MCP

- server endpoint；
- transport（V1 仅 streamable-http）；
- auth；
- 用户范围（ALL/SELECTED）；
- 指定用户；
- Agent binding；
- tool discovery 状态。

### Model

- Provider；
- Model；
- base URL；
- secret_ref；
- 参数；
- enable/disable。

### 用户

- PlatformUser；
- AgentAccessGrant；
- 项目平台凭据；
- IM 身份与绑定码；
- Memory 管理。

---

# 8. Phase 1 / Phase 2 / Future

## 8.1 Phase 1 必须完成

- `console-platform`；
- `agent-runtime`；
- `agent-worker`；
- `im-gateway`；
- WeCom Bot WebSocket；
- Agent/Skill/MCP/Model/User；
- AgentAccessGrant；
- ProjectPlatform / Credential；
- Skill Artifact；
- Skill Lazy Load；
- ToolRegistry；
- RuntimeSnapshot；
- Conversation；
- User Memory；
- Runtime Checkpoint；
- Egress Boundary；
- Streaming；
- 后台任务 / 定时任务 / Parent-Child 并行 / lease reclaim / Final Delivery；
- Run/Tool/Egress Audit；
- OTel；
- ALL/SELECTED + 指定用户 grant；
- `/bind /skills /new /stop`。

## 8.2 Phase 2

由真实长任务场景驱动：

- ServiceDefinition/ServiceRelease；
- 完整 Execution Timeline；
- 长任务 Artifact Delivery。

## 8.3 暂不建设

- Workflow Designer；
- BPMN；
- Capability Center；
- 通用 Approval Center；
- A2A；
- Multi-Agent Team；
- EventBus；
- Plugin Marketplace；
- 全量语义 Memory 平台；
- 完整 Knowledge Platform；
- 通用 Policy DSL。

---

# 9. 黄金旅程验收

## 9.1 Skill-first 迭代验收

```text
改 Skill Python
→ 新 Artifact
→ user_scope=SELECTED
→ 指定用户下一次 Run 生效
→ 未授权用户不可见
→ 出问题收回 grant
```

## 9.2 Runtime 无状态验收

```text
Turn 1 → runtime-A
Turn 2 → runtime-B
```

仍满足：

- Conversation 连续；
- User Memory 一致；
- Agent 授权一致；
- Skill/MCP 解析一致；
- 无本地 sticky session 依赖。

## 9.3 RuntimeSnapshot 验收

```text
Run 开始使用 Skill v1
→ 管理员导入 v2 并切换
→ 当前 Run 仍为 v1
→ 新 Run 使用 v2
```

## 9.4 Egress 安全验收

- Skill 无明文 Secret；
- 非 allowlist 目的地址拒绝；
- Credential 按 actor 解析；
- 日志不打印 Credential Value；
- 每次出网可追踪 `run_id / user / skill / target / result / latency`。

## 9.5 企业微信验收

- 一个 Gateway 连接多个 Bot；
- bot_id 正确路由 Agent；
- `/bind` 只绑定身份；
- Streaming 正常；
- Gateway 重启后业务事实不丢失；
- Future WebChat 不要求修改 Agent Runtime 领域模型。

---

# 10. 最终用户旅程摘要

## SOP 开发者

```text
人工 SOP
→ Python Skill
→ Mock/Dev Test
→ pack
→ Console 导入
→ user_scope=SELECTED
→ 指定用户验证
→ 修复重传
→ user_scope=ALL
```

## 管理员

```text
配置 Model / ProjectPlatform
→ 创建 Agent
→ 绑定 Skill/MCP
→ 授权用户
→ 配置 WeCom Bot
→ 观察 Run/Tool/Egress Audit
→ 放开用户范围/快速回退
```

## 普通用户

```text
WeCom
→ /bind（首次）
→ 表达业务目标
→ Agent 理解与澄清
→ Skill 完成业务接口组合与数据处理
→ 必要时确认
→ 获得业务结果
```

---

# 11. 一句话架构原则

> **Console 管定义，Runtime/Worker 管执行，Gateway 管渠道；Skill 承载 SOP，Tool/MCP 是 Runtime 执行能力；所有状态外置，所有业务出网经过统一安全边界，Phase 1 只做真实旅程需要的四套服务。**
