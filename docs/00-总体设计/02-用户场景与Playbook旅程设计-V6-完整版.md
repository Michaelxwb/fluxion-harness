# MSS 智能服务交付平台——用户场景与 Playbook 旅程设计 V6
## 从用户需求出发的重新构想版

> **V6 关键修订**
>
> V5 保持三类用户、Service/SOP/Skill/Capability 以及设备策略检查黄金旅程不变，重点同步最近几轮已经对齐的运行架构：
>
> - 文档统一使用“旅程/用户旅程/黄金旅程”，不再使用 旅程 英文术语；
> - `platform-api` 明确为 Control Plane，Console 静态资源与 API 合并交付，通常单副本，不进入普通用户实时执行主链；
> - `Execution Service` 明确为共享的确定性 Application Service，是可信执行边界，但不是独立微服务；Agent Runtime 和 platform-api 都可复用；
> - `agent-runtime` 与 `worker` 由 Kubernetes 在部署时创建为长期运行的 Deployment，不按用户动态创建/销毁 Pod；
> - `Channel Gateway` 提升为统一 `Channel Gateway`，通过 Channel Adapter SPI 扩展企业微信、未来业务平台 WebChat 及其他 IM；
> - 第一阶段只实现 `WeCom Adapter`，未来业务平台增加 WebChat 入口时复用同一 ChannelEnvelope、Identity、Memory、Conversation、Service 与 Execution 体系；
> - Channel Gateway 独立部署：连接状态可以本地维护，但 User/Memory/Conversation/Execution/DeliveryRoute 等业务事实必须外置；
> - V1 核心后台镜像收敛为 `platform-api`、`agent-runtime`、`worker`、`channel-gateway` 四个；不再单独交付 `console-web` 镜像；
> - `platform-api` 不调用 Kubernetes API 为用户创建 Agent/Worker Pod，副本扩缩由 Kubernetes/HPA/KEDA 负责；
> - 继续吸收 `muad-openclaw` 的多渠道、身份绑定、平台用户权限、Session、Skill、Browser、Progress 实战经验，以及 `fluxion-harness` 的 stateless、Snapshot、Capability、Memory、Worker 与 Architecture Gate 经验，但不继承旧项目领域模型。

> - 新增“项目平台（ProjectPlatform）”产品对象，并与部署级 `ProjectIntegration` 拆分；只有 Platform Service Capability 关联项目平台；
> - 用户产品授权正式收敛为 `User → AgentAccessGrant → Agent`，而不是要求管理员逐个授权底层 Service/Capability；
> - `/bind` 只负责把企微外部身份映射到平台用户；Bot 路由和 User→Agent 授权独立处理；
> - 企业微信 V1 一个 `bot_id` 只绑定一个 Agent，一个 Channel Gateway 可以维护多个 Bot WebSocket；新 Agent 如需企微入口创建新 Bot；
> - Skill 回归传统 Python 开发体验：SOP 开发者在 PyCharm/VS Code 使用 `fluxion_skill_sdk` 开发、调试、测试，完成后将 Skill Artifact 导入 Console；
> - Skill 可继续承担短时 SOP、数据过滤/转换、if/for/while 和业务算法，但不再自行处理认证、服务发现、自动分页、Retry/Timeout 等共性基础设施；
> - Skill 对 Capability 的依赖来自 `skill.yaml`，Console 详情只读；不建设在线 Skill 编排器和第二套人工能力绑定；
> - Capability 新增自动分页 Data Retrieval Policy，由 Runtime 内部完成 Page/Offset/Cursor 循环和安全限制，Skill/LLM 只发起一次逻辑调用；
> - `Service` 创建时必须绑定一个主 Agent；只有 Service 保留 Draft/Validate/Test/Publish，Agent/Model/Capability 等配置保存后对新请求直接生效；
> - Knowledge 当前进入规划，不在第一阶段冻结外部知识库接入字段、DB 和完整 Provider；
> - Console 只面向 Builder/Admin，End User 不访问；Channel 配置放在 Agent 的 IM 接入页，用户详情复用“IM 身份”页管理绑定码与已绑定身份。

---

# 目录

1. [项目背景、目标与设计原则](#1-项目背景目标与设计原则)
2. [目标用户与总体用户旅程](#2-目标用户与总体用户旅程)
3. [普通用户：完成一次真实服务](#3-普通用户完成一次真实服务)
4. [管理员：让服务可以安全地被使用和运营](#4-管理员让服务可以安全地被使用和运营)
5. [SOP / 服务开发者：把人工服务转化为可复用服务](#5-sop--服务开发者把人工服务转化为可复用服务)
6. [服务、SOP 与执行方式重新定义](#6-服务sop-与执行方式重新定义)
7. [Agent 技术承载与业务旅程映射](#7-agent-技术承载与业务旅程映射)
8. [业务平台能力接入与未来演进](#8-业务平台能力接入与未来演进)
9. [第一阶段范围、黄金旅程 与验收原则](#9-第一阶段范围golden-旅程-与验收原则)

---

# 1. 项目背景、目标与设计原则

## 1.1 业务背景

MSS 已经积累了较完整的安全产品能力、服务流程和专家经验，但当前大量服务仍依赖人工串联。

典型的一次服务交付通常需要服务人员：

```text
理解客户需求
→ 查询客户和服务关系
→ 查询设备 / 资产
→ 进入业务平台
→ 创建任务
→ 持续跟踪状态
→ 处理异常
→ 下载结果或报告
→ 分析问题
→ 整理客户交付内容
```

问题不在于 MSS 没有底层能力。

当前大量真实能力已经存在于业务平台中，并能够通过接口调用，例如：

- 客户查询；
- 设备查询；
- 策略检查任务创建；
- 漏洞扫描任务创建；
- 任务状态查询；
- 报告获取；
- 安全结果查询；
- 其他安全产品能力。

真正缺少的是：

> **如何把已有业务能力、SOP、知识和专家经验，以更低成本组合成可重复、可持续、可规模交付的智能服务。**

原 MSS-Claw 需求中已经明确了几项重要用户价值：

- 一句话发起标准服务；
- 平台自动完成可以自动完成的步骤；
- 长任务不依赖服务人员个人终端；
- 异常或高风险情况下请求人工介入；
- 最终产出可核验、可交付结果；
- 正式服务能力统一发布、统一升级；
- 服务人员不需要维护本地运行环境。

这些用户价值保留。

但原方案中：

```text
成熟 SOP
→ Skill
```

不再作为既定技术结论，而需要重新判断。

---

## 1.2 当前要解决的三个根问题

### 根问题一：标准服务依赖人工串联

大量成熟服务已经有明确 SOP，但执行效率仍与人员数量强相关。

我们希望把：

```text
人工逐步操作
```

变成：

```text
用户表达目标
→ 平台受控执行
→ 用户只处理必要判断
→ 平台直接形成结果
```

---

### 根问题二：开发一个 SOP 很容易退化成开发一个“小业务系统”

过去基于 OpenClaw/Skill/脚本方式实现时，一条 SOP 往往逐渐包含：

```text
Prompt
业务规则
接口调用
参数转换
轮询
重试
异常处理
状态维护
报告整理
```

最终导致：

- Skill 与底层接口强耦合；
- 每个 SOP 重复写胶水代码；
- 执行状态由脚本自己维护；
- 同一种能力被重复实现；
- 接口一升级，大量 SOP 一起修改；
- 很难统一测试、发布和治理。

---

### 根问题三：底层业务能力未来会变化

当前平台主要通过接口暴露能力，但未来可能出现：

```text
HTTP API
Platform Service
MCP
Async Task
Event
其他标准协议
```

如果上层服务直接绑定具体 URL、服务名或 MCP Tool：

> 底层技术变化会持续向所有 SOP 扩散。

因此平台需要一个稳定的业务能力边界。

---

## 1.3 设计目标

本阶段只围绕三个目标设计。

### 目标一：普通用户低门槛完成服务

用户关注：

> “我要完成什么服务任务。”

而不是：

> “应该打开哪个平台、调用哪个接口、运行哪个脚本。”

---

### 目标二：服务开发者低成本把 SOP 产品化

开发者应该主要负责：

- 描述业务目标；
- 描述业务阶段；
- 描述判断规则；
- 复用现有能力；
- 定义异常与人工介入点；
- 验证服务效果。

而不是反复编写基础设施代码。

---

### 目标三：底层业务能力与上层服务解耦

上层尽可能依赖：

```text
customer.get
device.list
policy_check.create
policy_check.status
report.get
```

而不是：

```text
https://xxx/api/v2/...
customer-service-mgr/get_customer
某个 MCP Server 的某个 Tool
```

---

## 1.4 总体设计原则

后续所有需求和技术设计遵循：

1. **用户旅程优先于系统功能。**
2. **Service/SOP 是业务概念，Skill/Workflow/MCP 是实现手段。**
3. **能用简单方式满足 旅程，就不引入更复杂的通用平台能力。**
4. **Agent 是核心技术载体，但不要求所有可靠性问题都由 LLM/Agent 自己解决。**
5. **业务能力优先复用已有平台，不重新建设业务逻辑。**
6. **先跑通代表性 黄金旅程，再扩展平台能力。**
7. **设计可以预留扩展点，但不意味着当前版本必须实现。**
8. **Agent Runtime 必须是无状态计算节点。** 用户、会话、Memory、任务状态、服务定义、Capability、凭据等持久状态全部外置；任意 Runtime 实例都能承接下一次请求。
9. **用户 Memory 是第一阶段用户体验的一部分。** Memory 用于跨会话、跨渠道保持稳定的用户上下文，但不得替代业务平台中的权威业务数据。
10. **Execution Service 是可信执行边界。** LLM/Agent 识别出的业务意图不能直接驱动有副作用的 Worker 操作，必须经过确定性权限、范围、Schema、确认、版本和幂等校验。
11. **Worker Engine 是 Service Execution 的可靠执行底座。** 长任务、等待、重试、幂等、恢复、人工检查点和通知统一由 Worker Engine 管理。
12. **LangGraph 是 Agent 执行框架，不是整个任务平台。** 它用于 Agent reasoning / planning / tool calling / Agent state；业务任务生命周期由 Worker Engine 管理。
13. **用户访问授权以 Agent 为 V1 产品入口。** Admin 授权用户使用 Agent；Agent 下绑定的 Service/Skill/直接 Capability 再按运行时规则解析，外部平台继续做数据权限最终判定。
14. **身份、路由、授权三分离。** ChannelIdentity 识别“是谁”，BotAccount 决定“去哪一个 Agent”，AgentAccessGrant 决定“能不能用”。
15. **Skill 继续由开发人员在线下 IDE 开发。** Console 不承担 Skill 开发/编排职责，只导入、校验、展示和供 Agent 选择。
16. **Capability 是 Skill 的稳定基础设施调用层。** Skill 可以写业务 Python，但不应重复实现认证、分页、服务发现、重试框架。
17. **ProjectPlatform 与 ProjectIntegration 分离。** 前者是用户认证和 Platform Service 的产品对象；后者是代码/部署扩展。
18. **Knowledge 当前后置。** 没有真实外部知识库接入场景前，不为了完整性提前设计错误的通用知识平台。

---

# 2. 目标用户与总体用户旅程

## 2.1 核心用户

当前只有三类核心产品用户。

| 用户 | 典型角色 | 核心目标 |
|---|---|---|
| 普通用户 | 服务经理、运营人员 | 快速完成一项真实 MSS 服务 |
| 管理员 | 服务管理员、平台管理员 | 将正确的服务安全开放给正确的人，并保证可运营 |
| SOP / 服务开发者 | 服务专家、SOP 开发人员、Agent 应用开发者 | 把人工 SOP 和已有业务能力低成本转化为可复用服务 |

“能力接入开发者”属于支撑角色。

他负责：

```text
已有业务 API / 服务
→ 平台可复用业务能力
```

第一阶段可以与 SOP 开发者是同一个人，因此不单独形成第四类产品用户。

---

## 2.2 三类用户之间的关系

```text
SOP / 服务开发者
       │
       │ 定义服务、复用能力
       ▼
   可发布服务
       │
       ▼
     管理员
       │
       ├── 发布
       ├── 授权
       └── 开放渠道
       │
       ▼
    普通用户
       │
       ▼
   发起真实服务
       │
       ▼
平台 / Agent 持续执行
       │
       ▼
    服务结果
```

三类用户实际上共同参与一个服务生命周期：

```text
开发
→ 发布
→ 开放
→ 使用
→ 运营
→ 迭代
```

---

## 2.3 用户旅程的设计粒度

按照用户需求模板，场景按：

```text
When
→ Do-What
→ How-To
```

梳理。

但需要特别注意：

> 产品 Playbook 描述的是用户完成业务目标的旅程，而不是 Console 点哪个按钮、调用哪个 API 的功能级操作说明。

例如：

```text
“完成设备策略检查”
```

是业务 Playbook。

而：

```text
“点击能力菜单 → 新增 Tool → 填 URL”
```

不是本文要描述的产品 Playbook。

---

# 3. 普通用户：完成一次真实服务

## 3.1 用户目标与现状旅程

### When

服务人员需要为客户完成一项标准 MSS 服务时。

例如：

- 设备策略检查；
- 漏洞扫描；
- 漏洞复测；
- 资产发现；
- 周/月报告；
- O365 配置检查；
- 安全基线检查。

### 用户意图

> 我已经知道我要为哪个客户完成什么服务，希望平台帮助我完成可以自动完成的工作，只在真正需要我判断时再找我。

### 当前旅程

```text
确认客户
→ 查询服务关系
→ 找到底层平台
→ 查询设备/资产
→ 配置任务
→ 创建任务
→ 等待
→ 查询状态
→ 异常处理
→ 获取报告
→ 分析结果
→ 整理交付
```

---

## 3.2 核心痛点

| 场景 | 痛点 | 影响 |
|---|---|---|
| 发起任务 | 必须知道对应平台和功能入口 | 学习成本和操作成本高 |
| 参数准备 | 系统已有信息仍需人工查询和录入 | 重复劳动、容易出错 |
| 长任务 | 需要持续刷新和人工跟踪 | 服务人员时间被占用 |
| 异常 | 技术异常和业务决策混在一起 | 不知道何时需要介入 |
| 结果 | 原始结果与客户交付内容分散 | 交付质量依赖个人经验 |
| 本地环境 | 本地运行、Skill 分散安装 | 难以规模推广和统一升级 |

---

## 3.3 用户场景

| When | Do-What | How-To |
|---|---|---|
| 需要完成一项标准服务 | 发起服务 | 用自然语言或统一服务入口表达客户和目标 |
| 平台识别目标后 | 确认执行条件 | 系统补全已有信息，只确认关键范围、时间和风险项 |
| 确认完成后 | 执行任务 | 平台在后台持续完成可自动执行的步骤 |
| 服务执行过程中 | 跟踪状态 | 用业务阶段展示进度，只暴露必要信息 |
| 遇到无法安全自动处理的问题 | 人工介入 | 平台说明问题、影响和可选决策 |
| 服务完成后 | 获取结果 | 聚合报告、结构化结果、建议和交付内容 |

---

## 3.4 Playbook U01：发起并确认一项服务

**核心业务意图**

用户只描述：

> “帮 A 客户做一次策略检查。”

平台负责：

1. 识别服务目标；
2. 识别客户；
3. 获取已有服务关系和设备信息；
4. 对无法唯一判断的内容向用户询问；
5. 执行权限和必要条件检查；
6. 给出业务可理解的确认摘要；
7. 用户确认后创建唯一任务。

**用户价值**

从：

```text
找入口 + 查参数 + 配置底层任务
```

变成：

```text
表达目标 + 确认关键条件
```

**典型异常**

- 客户重名；
- 用户无客户权限；
- 客户无可执行设备；
- 必要能力不可用；
- 关键范围无法确定。

这些情况下不应静默猜测后继续执行。

---

## 3.5 Playbook U02：平台持续执行长任务

**核心业务意图**

策略检查、扫描、报告生成等任务不应该要求用户一直保持页面或个人终端在线。

旅程：

```text
用户确认任务
→ 平台生成任务 ID
→ 平台持续执行
→ 用户可以离开
→ 再次进入
→ 找回同一个任务
→ 查看当前状态或最终结果
```

因此用户需求是：

- 任务一旦正式提交，由平台承载生命周期；
- 页面断开不能使任务自然消失；
- 用户重新进入可以继续查看；
- 重试不能重复产生有副作用的底层任务；
- 失败时保留已经完成的结果。

---

## 3.6 Playbook U03：只在必要时人工介入

用户希望：

```text
技术性可恢复问题
→ 平台自行处理

业务不确定 / 风险问题
→ 找用户确认
```

需要人工介入的典型情况：

- 客户无法唯一确定；
- 执行范围变化；
- 高风险操作；
- 数据冲突；
- 已超出标准服务边界；
- 达到自动恢复上限。

用户得到的提示至少要包含：

```text
发生了什么
影响哪一步
系统已经做了什么
为什么需要我处理
有哪些选择
不同选择意味着什么
```

第一阶段不因为这个场景就建设一个“大而全 Approval Center”。

只需要先满足：

> **当前任务能够暂停、确认、继续或终止。**

---

## 3.7 Playbook U04：获取可以直接交付的服务结果

用户最终要的不是：

```text
HTTP 200
Tool call success
任务状态 completed
```

而是：

```text
真正的服务结果
```

例如策略检查可能包括：

- 检查结果；
- 异常策略；
- 不支持设备；
- 风险解释；
- 优化建议；
- 正式报告；
- 客户沟通内容。

结果应围绕同一个服务任务聚合。

---

## 3.8 Playbook U05：首次进入即可使用

首次用户旅程：

```text
统一入口
→ 身份识别
→ 必要时一次性绑定
→ 获得已授权服务
→ 开始使用
```

用户不应该负责：

- 部署 Agent；
- 安装运行环境；
- 配置公共模型；
- 下载正式 Skill；
- 维护公共依赖；
- 处理服务升级。

后续进入也不应反复初始化。

---

## 3.9 Playbook U06：跨会话、跨渠道保持用户 Memory

### 核心业务意图

用户不希望每次新建会话、切换 Web/企业微信后，都重新告诉 Agent 自己长期稳定的使用背景和偏好。

第一阶段需要明确支持 **User Memory**。

Memory 主要服务于：

- 用户稳定偏好；
- 常用语言与输出格式；
- 服务工作习惯；
- 用户明确要求平台记住的长期上下文；
- 跨会话继续可复用、但不属于某个单独 Task 的信息。

例如：

```text
用户更偏好中文报告
常用交付模板为简版
默认希望先给摘要再给明细
```

这些信息可以在新的 Conversation 中继续使用。

### Memory 不是什么

Memory 不应保存或替代以下权威业务事实：

```text
当前客户有哪些设备
当前策略检查任务状态
客户最新权限
实时资产数据
扫描结果
```

这些信息必须重新通过业务 Capability 查询。

因此：

```text
User Memory
= 长期用户上下文

Conversation
= 当前会话上下文

Task State
= 当前服务任务状态

Business Data
= 业务平台权威数据
```

四者必须区分。

### 第一阶段用户旅程

```text
用户首次使用
→ 正常对话
→ 形成受控 User Memory
→ /new 创建新会话
→ 新会话仍能读取该 User Memory
→ 用户切换到企业微信
→ 身份映射为同一平台用户
→ 仍然获得一致的 User Memory
```

### 第一阶段实现边界

第一阶段 Memory 不需要一开始建设复杂的“全量语义记忆平台”。

至少具备：

- 按 `tenant + user` 隔离；
- 持久化存储；
- 跨 Conversation 使用；
- 跨 Web / IM 渠道使用；
- 有来源、创建时间和更新时间；
- 可控制写入；
- 可查看和清理；
- Runtime 每次执行按需读取；
- Memory 不保存在 Runtime Pod 本地。

语义向量检索、自动总结、多层 Memory、复杂画像推断可以后续按真实场景演进。

---

# 4. 管理员：让服务可以安全地被使用和运营

## 4.1 管理员真正关心的问题

管理员核心目标不是“维护平台中所有技术对象”。

而是：

```text
什么服务可以使用？
谁可以使用？
能服务哪些客户？
从哪里使用？
运行得怎么样？
出了问题怎么定位？
升级以后会不会影响正在运行的任务？
```

因此管理员产品设计应以：

```text
服务
用户
渠道
任务
```

为核心，而不是以 Tool、Hook、Plugin 等技术资源数量为核心。

---

## 4.2 Playbook A01：发布一项正式服务

管理员需要判断：

> 这项服务是否已经达到面向真实用户开放的条件？

发布前最少确认：

- 服务目标和输入明确；
- 关键能力可用；
- 权限边界明确；
- 主流程验证通过；
- 关键异常已覆盖；
- 输出符合交付要求；
- 当前发布版本可追踪。

发布成功以后：

```text
Service Version
→ immutable
```

同一个运行中任务不能突然因为后台修改配置而发生定义漂移。

---

## 4.3 Playbook A02：把智能体开放给正确的人

V1 管理员的主授权产品语义收敛为：

```text
Platform User
      ↓
AgentAccessGrant
      ↓
Agent
```

管理员在“用户 → 智能体授权”或“智能体 → 用户授权”中维护的是同一组关系。

授权 Agent 后，用户可以使用该 Agent 已绑定的 Skill、允许暴露给 LLM 的 Direct Capability，以及该 Agent 承载/可调用的 Service；框架不要求管理员逐个给普通用户授权几十个底层 Capability。

这并不意味着用户获得所有业务数据权限：

```text
Framework AgentAccessGrant
        +
当前用户 × ProjectPlatform Credential
        +
外部业务系统 RBAC/ACL
        =
最终可执行权限
```

典型失败需要区分：

```text
IM 身份未绑定
→ 提示 /bind

身份已绑定但没有 AgentAccessGrant
→ 提示无当前智能体权限

Agent 有权限但外部系统拒绝业务数据访问
→ 返回真实业务授权失败
```

第一阶段不建设复杂 ABAC/用户组策略中心；先满足明确的 User→Agent 授权。

## 4.4 Playbook A03：开放使用渠道

管理员不需要进入一个独立“Channel 管理中心”。V1 产品上在 Agent 详情的“IM 接入”中配置入口：

```text
Agent01
└── 企业微信机器人01
    ├── bot_id
    └── secret
```

V1 约束：

```text
一个 bot_id → 一个 Agent
一个 Agent → 最多一个 WeCom Bot
一个 Channel Gateway → 可以维护多个 Bot WebSocket
```

因此新建 Agent 且需要新的企业微信入口时，应申请/创建新的 bot_id 并绑定该 Agent。Bot 的 WebSocket 长连接建立到统一 Channel Gateway，而不是某个 Agent Runtime Pod。

### 身份绑定、消息路由、授权三分离

```text
external_user_id
→ ChannelIdentity
→ PlatformUser

bot_id
→ Agent

PlatformUser
→ AgentAccessGrant
→ Agent
```

用户第一次尚未建立外部身份映射时：

```text
Admin 在用户详情 / IM 身份生成绑定码
→ 用户在任意可访问 WeCom Bot 输入 /bind CODE
→ Gateway 获取 wotvU***i4GA 等外部身份
→ 建立 ChannelIdentity → PlatformUser
```

`/bind` 不绑定 Agent，不绑定 Bot，也不授予 Agent 权限。

如果企业微信实测同一用户在不同 Bot 下返回稳定一致的外部身份，则用户只需绑定一次；若身份是 Bot 级隔离，允许同一 PlatformUser 绑定多个 ChannelIdentity，但仍不复制 AgentAccessGrant。

### Channel Gateway 是统一通道层

```text
Channel Gateway
└── Channel Adapter SPI
    ├── WeCom Adapter        ← Phase 1
    ├── WebChat Adapter      ← Future
    ├── Mattermost Adapter   ← Future / Dev
    └── Other IM Adapter     ← Future
```

职责：

- 维护渠道协议连接；
- 将原始消息统一为 `ChannelEnvelope`；
- 按 bot/account 路由到 Agent；
- 解析 ChannelIdentity；
- 做消息去重和 ACK/Retry；
- 将 Agent 流式回复转换为渠道协议；
- 使用持久化 DeliveryRoute 做后台主动通知；
- 暴露连接健康状态给运维，而不是 Console 系统设置。

Channel Gateway 不负责 Agent reasoning、User Memory、业务授权、ServiceExecution 和 Capability 调用。

### IM 内置命令

V1 固定命令语义：

```text
/bind <code>  建立外部身份映射
/skills       查看当前 Agent 已绑定且启用的 Skill，按 Skill.platform_label 分组
/new          创建新 Conversation，不改变 Agent/Skill/身份授权
/stop         停止当前 Run/Execution，不删除会话
```

这些命令由统一 Channel/Runtime 实现，不要求每个 Agent 单独维护一套。

### 主动推送与 DeliveryRoute

后台任务可能在用户离开后才完成，必须持久化 DeliveryRoute。`TaskProgressEvent` 表示“Execution 发生了什么”，DeliveryRoute 表示“消息应该送到哪里”，两者继续分离。

Channel Gateway 可以连接有状态，但 User/Memory/Conversation/Execution/DeliveryRoute 等业务事实必须外置。

## 4.5 Playbook A04：定位失败任务

管理员发现任务异常后，希望快速回答：

```text
谁发起的？
哪个客户？
执行的是哪个服务？
服务版本是什么？
执行到哪个业务阶段？
调用了什么底层能力？
失败原因是什么？
重试过几次？
需要谁介入？
能不能恢复？
```

因此第一阶段真正需要的是：

> **任务级 Trace / Execution Timeline**

而不是为了可观测性完整先建设一个复杂的 APM 产品。

---

## 4.6 Playbook A05：升级正式服务

升级目标：

- 新任务使用新版本；
- 正在运行的任务继续使用启动时版本；
- 历史任务能解释当时用了什么；
- 发现问题时可以停止继续使用新版本。

第一阶段重点是：

```text
版本确定性
```

复杂灰度、AB 实验等没有真实用户旅程前暂不需要建设。

---

# 5. SOP / 服务开发者：把人工服务转化为可复用服务

## 5.1 当前开发者旅程与痛点

历史 Skill/Script 模式开发速度快，真正的问题并不是“写 Python”本身，而是一个脚本同时承担了过多平台职责：

```text
人工 SOP
→ Python Skill
   ├── 登录/Token
   ├── URL/服务发现
   ├── 分页 for-loop
   ├── HTTP/MCP 调用
   ├── Retry/Timeout
   ├── 数据处理
   ├── 业务判断
   ├── 长任务轮询
   └── 结果整理
```

随着 SOP 增多，最难维护的是共性基础设施和底层接口变化不断复制，而不是开发者会写 `if/for/while`。

V6 的目标因此调整为：

> **保留传统 Skill 的 Python 开发体验，同时把认证、服务发现、Capability 调用、自动分页、Retry/Timeout、Secret 等共性逻辑收敛到平台。**

---

## 5.2 Playbook D01：先把人工 SOP 建模成 Service

开发者拿到 SOP 时仍先回答业务问题：目标、输入、交付、业务阶段、可靠性边界、人工介入点、可复用能力。

Service 是用户真正使用的业务服务，而且 V1 创建 Service 时必须选择一个**主 Agent**：

```text
Service
└── primary_agent_id → Agent
```

一个 Agent 可以承载多个 Service。

Service 只有 Draft/Validate/Test/Publish 生命周期；Agent、Model、Capability 等配置默认保存后对后续新请求生效。

---

## 5.3 Playbook D02：区分“Skill 内业务逻辑”和“可靠 Service Execution”

V6 不再把 SOP 简单划成“Skill 或 Workflow 二选一”。

### 适合留在 Skill Python 中

- 几秒到几十秒的短生命周期处理；
- `if/for/while`；
- 数据过滤、转换、聚合；
- 业务算法；
- 根据结果决定下一次 Capability 调用；
- Prompt/模板/专家判断；
- 不要求跨进程持久化恢复的柔性步骤。

### 必须进入 Service Execution / Worker 的场景

- 长时间等待；
- 外部 Async Task；
- 有副作用动作不能重复；
- 需要 lease/recovery；
- 需要人工检查点；
- 需要跨小时/跨天恢复；
- 需要明确 Step 状态、取消、补偿或可靠投递。

因此：

```text
Skill
= 传统 Python SOP/方法代码 + Capability 调用

Service Execution
= 可靠任务生命周期
```

复杂真实服务可以同时使用两者。

---

## 5.4 Playbook D03：优先复用已接入 Capability

平台中的 Capability 是 Skill 和 Agent 都可复用的稳定调用边界，例如：

```text
customer.get
device.list
policy_check.create
report.generate
```

开发者关注业务 Contract，而不是 URL/服务名/MCP transport。

如果 Capability 已存在，Skill 直接：

```python
customer = ctx.capability.call("customer.get", {"id": customer_id})
```

不再重复实现认证、HTTP、服务发现和分页。

---

## 5.5 Playbook D04：缺少 Capability 时先接入平台

Capability 仍通过 Console/平台能力接入：

```text
新增 Capability
→ 定义 name / input_schema / output_schema / risk / side_effect
→ 选择 Implementation
→ 测试
→ Skill/Agent 复用
```

V1 Implementation：

```text
Platform Service（注册发现）
HTTP
MCP
Sandbox
```

只有 Platform Service 与 ProjectPlatform 关联。其他类型不挂项目平台；如需认证使用 Implementation 自身统一认证。

列表能力的自动分页在 Capability 中配置，不在 Skill 中写 page for-loop。

---

## 5.6 Playbook D05：在 PyCharm/VS Code 中开发 Skill

Skill 不在 Console 编排。

开发者创建普通 Python 工程：

```text
customer-analysis/
├── SKILL.md
├── skill.yaml
├── src/main.py
├── resources/
└── tests/
```

`skill.yaml` 示例：

```yaml
name: 客户经营分析
key: customer-analysis
platform_label: MSS
sdk_version: "1.x"
entrypoint: src/main.py
capabilities:
  - customer.get
  - order.list
```

Python 示例：

```python
def run(ctx, input):
    customer = ctx.capability.call("customer.get", {"id": input["customer_id"]})
    orders = ctx.capability.call("order.list", {"customer_id": input["customer_id"]})
    active = [x for x in orders if x["status"] == "ACTIVE"]
    return {"customer": customer, "active_orders": active}
```

开发者继续使用熟悉的：

```text
Python
pytest
PyCharm breakpoint
普通函数/模块
数据结构转换
业务算法
```

无需学习新的 Workflow DSL。

---

## 5.7 Playbook D06：Skill SDK 的本地验证模式

`fluxion_skill_sdk` 与 Framework 同仓作为独立 Python package 维护，V1 不要求单独发布 PyPI，但 Skill 禁止 import Runtime/Domain/Repository/Infrastructure 内部模块。

开发者只面向稳定 Public API：

```python
ctx.capability.call(...)
ctx.logger...
ctx.artifact...
ctx.user
ctx.session
```

两种本地模式：

### Mock 单测

```text
Skill
→ MockSkillContext
→ mock capability result
```

用于快速验证 SOP 和数据处理。

### 开发环境真实联调

```text
PyCharm Skill
→ Skill SDK
→ HTTP Dev Capability Gateway
→ Dev Capability Runtime
→ Project Platform / HTTP / MCP / Sandbox
```

这可以验证真实输入 Schema、用户测试账号、自动分页、服务发现和返回结构。

生产导入后 Skill 代码不变：生产 Runtime 注入同一 `SkillContext` 语义，内部可以直接调用 Capability Runtime，不要求再次走 HTTP。

---

## 5.8 Playbook D07：打包并导入 Skill

开发验证完成：

```text
fluxion-skill validate .
→ fluxion-skill test .
→ fluxion-skill pack .
→ xxx.zip
→ Console「导入 Skill」
```

平台导入时至少校验：

- `skill.yaml` Schema；
- SDK 版本兼容性；
- entrypoint 存在；
- Capability 依赖存在；
- 基础 Secret 扫描；
- checksum。

导入产生 immutable Skill Artifact。修改 Skill 需要在线下重新打包后“导入新版本”，不能覆盖旧 Artifact。

Console Skill 详情页全部只读：

```text
基本信息
能力依赖
使用智能体
制品版本
校验结果
```

Capability 依赖来自 `skill.yaml`，不支持 Console 人工绑定/解除。

`platform_label` 只是人类识别标签，用于搜索和 `/skills` 分组，不参与运行时认证和平台路由。

---

## 5.9 Playbook D08：Agent 选择 Skill，Service 选择主 Agent

Skill 导入后，由 Builder 在 Agent 详情中选择可使用的 Skill。

```text
Skill → Capability dependencies
来自 skill.yaml，只读

Agent → Skill
由 Agent 配置维护

Skill → Agent
详情中反向查询，只读
```

Agent 还可以直接绑定 Capability；Skill 内部声明的 Capability 不等于自动把这些 Capability 暴露为 Agent 的 LLM Direct Tool。

创建 Service 时必须绑定一个主 Agent，Service 发布时冻结需要保持一致的 Agent/Skill/Capability 业务逻辑投影。

---

## 5.10 Playbook D09：快速验证 Service 并发布

```text
Draft Service
→ 选择测试用户/测试范围
→ Test Chat / Test Execution
→ 查看 Trace/Execution
→ 调整 Agent/Skill/Service
→ Validate
→ Admin Publish
```

Skill 的快速试错在线下完成；Service 的产品级测试使用同一 Runtime/Execution 模型，不建设第二套简化 Runtime。

只有 Service 需要正式 Publish。Agent/Capability/Model/ProjectPlatform 的修改默认保存后对新请求直接生效，并通过 revision/audit 留痕。

# 6. 服务、SOP 与执行方式重新定义

本章把前面所有场景里的核心业务概念统一起来。

---

## 6.1 Service Definition 是最高层业务对象

Service Definition 表达：

> 用户真正使用和交付的一项业务服务。

例如：

```text
设备策略检查
漏洞复测
月度安全报告
热点威胁自检
```

它至少描述：

```text
服务名称
业务目标
触发场景
输入
业务阶段
关键确认点
异常原则
需要的业务能力
交付结果
完成条件
版本
```

它不直接描述：

```text
HTTP URL
Python 文件
MCP Transport
数据库表
```

---

## 6.2 SOP 是 Service 的业务做法

SOP 仍然是很重要的业务资产。

但：

```text
SOP ≠ Skill
SOP ≠ Workflow
```

SOP 描述：

> 为了完成这项服务，组织希望遵循什么做法、规则和经验。

它可能最终由不同技术方式承载。

---

## 6.3 Skill 的重新定位

V6 将 Skill 定义进一步明确为：

> **使用 Fluxion Skill SDK 离线开发、以不可变制品导入平台、由 Agent Runtime 执行的 Python SOP/方法代码资产。**

Skill 既可以包含 Instructions/Prompt/模板/静态资源，也可以包含普通 Python 数据处理和业务逻辑，并通过：

```python
ctx.capability.call("capability.name", input)
```

调用已接入平台的稳定能力。

因此 Skill 与传统 Skill 的开发逻辑基本一致；变化主要是平台把以下基础设施抽离出去：

```text
认证
Secret
服务发现
HTTP/MCP/Sandbox transport
自动分页
Retry/Timeout
统一错误
```

Skill 不负责可靠长任务生命周期。需要 wait/recovery/human checkpoint 的步骤进入 Service Execution / Worker。

Skill 的“归属平台”是 `platform_label` 文本，用于人类识别和 `/skills` 分组；真实项目平台依赖由它调用的 Platform Service Capability 间接形成。

Console 不提供 Skill 在线开发/编排，也不人工维护 Skill-Capability 依赖。

## 6.4 固定流程的重新定位

固定流程也不等于当前就必须建设一个完整 Workflow Engine。

第一阶段可能只需要：

```text
Durable Task
+
明确的状态转换
```

当出现以下真实需求时，才值得把 Workflow 产品化：

- 多个 Service 反复复用流程片段；
- 大量复杂分支；
- 并行；
- 补偿；
- 人工节点；
- 可视化编排成为开发者刚需。

因此：

```text
固定流程型服务
≠
必须马上建设 Workflow 产品
```

---

## 6.5 三种执行方式最终定义

| 执行方式 | 业务特征 | Agent 角色 | 平台可靠性角色 |
|---|---|---|---|
| Agent 自主型 | 动态判断、知识驱动 | 主执行者 | 提供会话、能力调用、边界控制 |
| 固定流程型 | 顺序确定、长任务、可靠执行 | 可选，用于局部分析 | 主执行控制者 |
| 混合型 | 既有可靠步骤，又有复杂判断 | 负责认知型步骤 | 负责可靠步骤与任务状态 |

这三个只是**执行方式分类**。

不直接对应三个微服务、三个菜单或三个引擎。

---

# 7. Agent 技术承载与业务旅程映射

本章回答一个核心问题：

> **前面定义的用户旅程，哪些由 Agent 直接完成，哪些必须进入可靠后台执行？**

这里不再把“Agent 自主型 / 固定流程型 / 混合型”理解成三个组件。

真正的技术组成只有：

```text
Agent Runtime
Execution Service
Worker Engine
Agent Executor
Capability Runtime
Channel Gateway
```

其中 `LangGraph` 位于 `Agent Executor` 内部。

---

## 7.1 Agent Runtime：面向实时交互的无状态 Agent 执行服务

`Conversational Agent` 不再作为独立微服务。

正确关系是：

```text
Agent Runtime
└── Conversational Agent / LangGraph
```

Agent Runtime 负责：

- 自然语言理解；
- 多轮对话；
- 意图识别；
- 参数抽取；
- 信息澄清；
- User Memory 读取；
- Skill / Knowledge 加载；
- Reasoning / Planning；
- 结构化输出；
- 短时 Capability 调用；
- 实时 Streaming。

它的核心技术栈：

```text
Python
+
LangGraph Library
+
Shared Agent Core
```

---

## 7.2 Shared Agent Core：在线 Agent 和 Worker Agent Step 共用

平台只能有一套 Agent 能力核心。

```text
Shared Agent Core
├── Model Adapter
├── User Memory Port
├── Conversation Context
├── Instructions
├── Skill / Knowledge
├── Capability Catalog
├── Capability Calling
├── Structured Output
└── LangGraph Agent Executor
```

两种场景都复用：

```text
场景 A：在线对话

Agent Runtime
    ↓
Agent Executor
    ↓
LangGraph


场景 B：后台服务中的认知步骤

Worker Engine
    ↓
Agent Step
    ↓
同一个 Agent Executor
    ↓
LangGraph
```

因此不允许未来形成：

```text
Chat Agent 一套 Skill/MCP
Worker Agent 另一套 Skill/MCP
```

---

## 7.3 Chat 与 Service Execution 必须分开

不是每一条用户消息都需要创建后台任务。

### 普通知识问答

例如：

> “策略检查是做什么的？”

直接：

```text
User
 ↓
Agent Runtime
 ↓
Agent
 ↓
Answer
```

### 短时、只读、无副作用 Capability

例如：

> “A 客户有哪些 EDR？”

可以：

```text
Agent Runtime
 ↓
Capability Runtime
 ↓
device.list
 ↓
Answer
```

不需要 Worker。

### 写操作 / 长任务 / 高风险 / 需要可靠执行

例如：

> “帮 A 客户做一次策略检查。”

必须：

```text
Agent Runtime
 ↓
识别 Service Intent
 ↓
补全信息
 ↓
用户确认
 ↓
Execution Service
 ↓
Worker Engine
```

因此固定规则：

```text
Read + Short-lived + No Side Effect
→ Agent 可直接调用

Write / Long-running / Durable / High-risk
→ Execution Service → Worker Engine
```

---

## 7.4 Execution Service：Agent 与 Worker Engine 之间的可信边界

Agent 输出：

```text
intent = policy_check
customer = A
devices = [...]
```

只能表示：

> Agent 建议发起这项服务。

不能直接等价于可信执行命令。

真正创建 Execution 前，由确定性代码执行：

```text
Service 是否存在
Service Version 是否 Published
用户是否有权使用 Service
用户是否有目标客户权限
输入 Schema 是否合法
设备是否属于目标客户
是否完成必要确认
是否重复提交
当前执行范围是否仍然有效
```

通过后创建：

```text
StartServiceExecution
```

最小结构：

```text
ServiceExecution
├── execution_id
├── service_version
├── actor_user_id
├── customer_scope
├── input
├── confirmation_ref
├── delivery_route_ref
├── status
└── execution_snapshot
```

因此：

```text
Agent
= 把模糊用户意图变成明确执行提议

Execution Service
= 把提议变成经过验证的可信 Execution

Worker Engine
= 把 Execution 可靠执行完成
```

---

## 7.5 Worker Engine：Service Execution 的可靠执行底座

Worker Engine 不再定义为“只负责 Checkpointer/长任务”。

它负责完整 Service Execution 生命周期：

```text
Worker Engine
├── Execution Claim
├── Lease / Heartbeat
├── Step State
├── Retry
├── Timeout
├── Wait / Timer
├── Idempotency
├── Crash Recovery
├── Cancellation
├── Human Checkpoint
├── Result Persistence
└── Delivery Trigger
```

它面对的是：

```text
明确、已确认、已授权的 ServiceExecution
```

而不是用户原始 Prompt。

Worker Engine 可以独立部署和扩缩：

```text
ROLE=worker
```

与 Agent Runtime：

```text
ROLE=agent-runtime
```

属于两个运行角色。

代码仍可以在同一个 Python monorepo 中共享领域模型和执行器。

---

## 7.6 Worker Engine 的三类 Step Executor

Worker Engine 自己不应该知道每一种业务实现。

统一按 Step 类型调用 Executor：

```text
Worker Engine
├── Agent Step
│    └── Agent Executor / LangGraph
│
├── Capability Step
│    └── Capability Runtime
│
└── System Step
     ├── Wait / Timer
     ├── Human Checkpoint
     ├── Delivery
     └── Internal Control
```

例如设备策略检查：

```text
理解用户需求
→ Agent Runtime（实时）

用户确认
→ Execution Service

创建策略检查任务
→ Worker / Capability Step

等待检查完成
→ Worker / Wait

查询状态
→ Worker / Capability Step

分析异常策略
→ Worker / Agent Step / LangGraph

生成建议
→ Worker / Agent Step / LangGraph

主动通知
→ Worker / Delivery Step
```

---

## 7.7 三种业务执行方式如何落到同一架构

### Agent 自主型

```text
Worker / Agent Runtime
   ↓
Agent Executor
   ↓
LangGraph
   ↓
Reasoning ↔ Capability
```

Agent 是主要认知执行者。

### 固定流程型

```text
Worker Engine
   ↓
Capability Step
   ↓
Wait
   ↓
Capability Step
   ↓
Result
```

可以完全不需要 LangGraph。

### 混合型

```text
Worker Engine
   ├── Agent Step
   ├── Human Checkpoint
   ├── Capability Step
   ├── Wait
   ├── Capability Step
   ├── Agent Step
   └── Delivery
```

因此：

> Agent 自主型 / 固定流程型 / 混合型只是业务执行方式，不是三个 Engine。

---

## 7.8 LangGraph 的边界

LangGraph 第一阶段定位为：

```text
Agent Executor Framework
```

负责：

- Agent State；
- Reasoning；
- Planning；
- Capability/Tool Selection；
- Agent 内部节点编排；
- Checkpoint；
- Agent 内部 interrupt/resume；
- 认知型 Agent Step。

它不负责整个业务任务平台中的：

```text
全局 Service Execution 生命周期
Worker claim / lease
跨任务调度
业务幂等
后台 timer/wakeup
通用 Delivery
所有业务审批
所有后台 Job
```

前期针对 黄金旅程 的验证已经得到一个明确架构结论：

> **PostgreSQL Checkpointer 能持久化 Graph State，但“状态被保存”不等于“后台任务会在未来自动被调度和唤醒”。**

因此 Worker Engine 仍然需要存在。

当前第一阶段优先采用：

```text
LangGraph Library
```

而不是把：

```text
LangGraph Agent Server
```

作为核心运行依赖。

原因不是 Agent Server 不具备能力，而是未来 Worker Engine 的职责明显大于 Agent Run；如果同时引入完整 Agent Server Queue Worker 和自己的 Worker Engine，会形成两套 Execution Engine。

---

## 7.9 Agent Runtime 无状态是硬架构约束

```text
Agent Runtime
= Stateless Compute
```

Agent Runtime 可以拥有：

- 当前请求临时状态；
- 当前 Turn 的对象；
- 临时模型连接；
- 短生命周期 cache；
- Streaming connection。

但以下权威状态必须全部外置：

| 状态 | 权威位置 |
|---|---|
| User / Identity | User Store |
| User Memory | Memory Store |
| Conversation / Message | Conversation Store |
| Service / Agent Definition | Control Plane Store |
| Skill / Knowledge | Registry / Object Store |
| Capability Definition | Capability Registry |
| Service Execution | Execution Store |
| Worker Step State | Execution Store |
| Result / Artifact | Artifact Store |
| Credential | Secret Provider |
| Channel Binding | Channel Store |
| Delivery Route | Channel/Delivery Store |
| Version / Snapshot | Persistent Store |

必须满足：

```text
Turn 1 → Agent Runtime A
Turn 2 → Agent Runtime B
```

仍然得到一致的：

```text
User
Memory
Conversation
Service / Agent Version
Skill
Capability
Execution
```

不能依赖 sticky session 保证业务正确性。

---

## 7.10 Channel Gateway：统一通道接入层

Channel Gateway 是统一渠道连接/路由/投递服务，和 Agent Runtime 解耦。

### 企业微信 V1

```text
Bot01 ──WebSocket──┐
Bot02 ──WebSocket──┼→ Channel Gateway
Bot03 ──WebSocket──┘
                    │
                    ├→ bot01 → Agent01
                    ├→ bot02 → Agent02
                    └→ bot03 → Agent03
```

一个具体 bot_id V1 只服务一个 Agent，因此普通消息不需要 Router LLM 猜 Agent。

消息处理：

```text
bot_id → Agent
external_user_id → PlatformUser
PlatformUser + Agent → AgentAccessGrant
→ Agent Runtime
```

未绑定身份时提示 `/bind`；身份已绑定但无 Agent 权限时明确返回“无当前智能体权限”。

### `/bind`

`/bind CODE` 只建立 ChannelIdentity → PlatformUser。绑定码在用户详情“IM 身份”页生成，单次、短时有效；不包含 Agent/Bot 授权。

### 独立部署

Channel Gateway 可以保存连接、heartbeat、reconnect 等临时连接状态，但 PlatformUser、Grant、Conversation、Memory、Execution、DeliveryRoute 必须外置。

### 未来 WebChat

未来新增 WebChat 只实现新的 Channel Adapter，复用同一 PlatformUser/Agent/Service/Memory/Execution。V1 不提前冻结 WebChat 配置字段。

## 7.11 核心运行架构

运行主链采用自上而下的分层模型，Control Plane 不位于普通用户实时执行的必经链路。

```text
L1  Channel Layer
    WeCom / Future WebChat / Future IM
                 │
                 ▼
           Channel Gateway
                 │
          ChannelEnvelope
                 ▼

L2  Realtime Agent Layer
           Agent Runtime
             Stateless
                 │
     ┌───────────┴───────────┐
     │                       │
普通问答/短查询           真实业务执行
     │                       │
     ▼                       ▼
Capability Runtime     ExecutionService
                       (shared in-process
                        application service)
                              │
                              ▼

L3  Durable Execution Layer
                       PostgreSQL
                              │
                              ▼
                         Worker Engine
                ┌─────────────┼─────────────┐
                ▼             ▼             ▼
           Agent Step    Capability Step  System Step
                │             │          Wait/Human/
                ▼             ▼           Delivery
             LangGraph   Capability Runtime
                              │
                              ▼

L4  Integration Layer
              Platform API Adapter / MCP / Future
                              │
                              ▼

L5  Existing MSS Business Platform
          Existing RBAC / Customer / Data ACL
```

Control Plane 独立在主链旁路：

```text
Console UI
   │
   ▼
platform-api
   │
   ├── Service / Agent / Skill / Capability
   ├── User / Authorization / Channel Binding
   ├── Publish / Version / Audit
   └── Admin Execution Command
            │
            ▼
   PostgreSQL / Object Store / Secret Provider

Agent Runtime / Worker
→ 读取已发布运行态数据
```

### ExecutionService 不是独立微服务

`ExecutionService` 是共享 Python Application Service：

```text
packages/execution/execution_service.py
```

用户确认时：

```text
Agent Runtime
→ ExecutionService.create(...)
→ PostgreSQL
→ Worker
```

管理员手工创建任务时：

```text
platform-api
→ 同一个 ExecutionService.create(...)
```

这样既保留可信执行边界，又不让 `platform-api` 成为普通用户运行主链的同步依赖。

### Kubernetes 生命周期

`agent-runtime` 和 `worker` 在平台部署时一起以 Deployment 创建：

```text
helm install / upgrade
├── platform-api
├── agent-runtime
├── worker
└── channel-gateway
```

明确禁止：

```text
新用户
→ platform-api 调 Kubernetes API
→ 动态创建专属 Agent Pod / Worker Pod
```

用户与 Pod 不绑定。副本扩缩由 Kubernetes/HPA/KEDA 负责。

---


# 8. 业务平台能力接入与未来演进

当前业务平台主要以接口方式提供能力。

这不是限制，而是第一阶段最有价值的既有资产。

平台不需要先把所有业务系统改造成 Agent/MCP，而是需要建立稳定的业务能力抽象。

---

## 8.1 Capability Contract：隔离 Service 与具体接口

上层 Service / Agent 尽可能依赖：

```text
customer.get
device.list
policy_check.create
policy_check.status
policy_check.result
report.get
```

而不是：

```text
具体 URL
具体 service name
具体 RPC transport
具体 MCP Server
```

Capability Contract 至少定义：

```text
name
description
input_schema
output_schema
side_effect
risk_level
idempotency_semantics
invocation_policy   # 直调结论 DIRECT | EXECUTION_ONLY（V1.13.1 D1 收敛：原 error_semantics/authorization_requirement/execution_characteristic 合并）
```

---

## 8.1A ProjectPlatform：业务平台与 ProjectIntegration 分离

Capability 的业务平台归属不使用 `ProjectIntegration` 表达。

```text
ProjectPlatform
= MSS/CRM/ERP 等 Console/DB 业务对象
= 用户认证模板
= Platform Service Capability 归属

ProjectIntegration
= 代码/部署扩展包
= Manifest / Provider / Adapter
= 不作为普通用户配置
```

V1 每个 ProjectPlatform 定义一套用户认证字段模板；同一用户在同一 ProjectPlatform 只配置一套账号。

只有 Platform Service Capability 必须选择一个 ProjectPlatform；HTTP/MCP/Sandbox 不关联 ProjectPlatform。

## 8.2 Capability Runtime：统一执行入口

真正调用底层业务系统的是：

```text
Capability Runtime
```

而不是 Agent/Worker 自己拼 HTTP。

```text
Agent Runtime
      │
      │ short read/query
      ▼
Capability Runtime
      │
      ▼
Platform API / MCP


Worker Engine
      │
      │ durable/write/long task
      ▼
Capability Runtime
      │
      ▼
Platform API / MCP
```

两边必须共享同一个 Capability Registry / Executor。

---

## 8.3 当前业务平台 API 的接入方式

第一阶段优先支持现有平台接口：

```text
Capability
   ↓
Platform Service / HTTP Adapter
   ↓
现有服务注册发现
   ↓
业务平台 API
```

例如：

```text
customer.get
```

映射：

```text
customer-service-mgr/get_customer
```

上层 Service 和 Agent 只知道：

```text
customer.get
```

---

## 8.3A Capability 自动分页：Skill 不再维护 page for-loop

对于下游一次最多返回 100 条但总量可能 10000+ 的列表接口，Capability Implementation 可以声明自动分页。

支持：

```text
Page/PageSize
Offset/Limit
Cursor
```

典型 Page 配置：

```text
page 参数：page
page_size 参数：page_size
起始页：1
每页数量：100
列表路径：data.items
总数路径：data.total（可选）
```

结束判断：

```text
明确 next_cursor / has_more
→ 优先

total
→ 达到总数结束

len(items) < page_size
→ 下游协议保证时可提前结束

items == []
→ Page/Offset 固定兜底结束
```

所有模式必须受硬保护：

```text
max_pages
max_items
max_duration
duplicate_page_detection
```

因此 Skill 中只写：

```python
items = ctx.capability.call("device.list", params)
```

而不是：

```python
for page in ...:
    requests.get(...)
```

Capability Executor 对上层表现为一次逻辑调用，内部可以发起 N 次请求。超大结果应优先聚合或生成 Artifact，而不是全量塞入 LLM Context。

## 8.4 Agent 如何从意图进入 Worker Engine

以：

> “帮 A 客户做一次策略检查。”

为例。

### Step 1：Agent 理解和补全

```text
Agent Runtime
   ↓
intent = policy_check
customer = A
```

如果需要，可以直接调用只读 Capability：

```text
customer.search
device.list
```

获得可确认信息。

### Step 2：用户确认

Agent 输出：

```text
客户：A
设备：AF × 2、EDR × 1
执行时间：立即

确认执行？
```

用户确认后，Agent 形成：

```text
StartServiceExecution proposal
```

### Step 3：Execution Service 做确定性校验

```text
权限
客户范围
输入 Schema
Published Version
Confirmation
Idempotency
Snapshot
```

校验通过后创建：

```text
ServiceExecution
```

### Step 4：Worker Engine claim Execution

```text
PENDING
→ RUNNING
```

### Step 5：Worker 调 Capability Runtime

```text
policy_check.create
→ Platform API
→ external_task_id
```

持久化后进入：

```text
WAITING
```

后续任意 Worker 实例都能继续执行。

---

## 8.5 MCP 的位置

MCP 是 Capability Implementation 的一种。

```text
Capability Contract
        │
        ├── Platform API
        ├── HTTP
        ├── MCP
        └── Future Adapter
```

例如未来：

```text
threat.search
```

可以由：

```text
MCP Server / search_threat
```

实现。

Agent 仍然只看到：

```text
threat.search
```

而不是把 MCP transport 作为业务语义。

同时 MCP 不得绕过执行治理：

```text
只读、短时、低风险
→ Agent 可直接调用

写入、高风险、长任务
→ Worker Engine
```

---

## 8.6 Async Capability / Async Task

异步是 Capability 的**执行特性**，不是第五种 Capability Implementation 类型。

一个 Platform Service 或 HTTP Capability 都可以采用：

```text
submit
→ status
→ result
→ cancel（底层支持时）
```

长任务进入 Worker/Execution 后，一个 ServiceExecution 可以包含 0..N 个 Async Task Run。Console 不单独提供“异步任务”菜单；Task ID、状态、结果、错误和 Artifact 在 Execution Detail/Timeline 中统一呈现。

`/stop` 停止当前执行时，如果当前 Async Capability 支持 cancel，则 Worker 调下游 cancel；如果不支持，平台停止后续步骤并明确标识远端任务无法取消。

Skill 不自己维护 Async Task 轮询与恢复框架。需要跨进程等待的异步任务必须由 Worker Engine 管理。

## 8.7 未来平台能力演进

未来底层能力可能从：

```text
HTTP polling
```

升级成：

```text
MCP
Event
Callback
Async Message
```

上层设计目标是：

```text
Capability Contract 稳定
Implementation 独立变化
```

即：

> **底层技术升级不等于所有 Service/SOP 一起修改。**


# 9. 第一阶段范围、黄金旅程 与验收原则

## 9.1 第一阶段只验证一条完整服务链

第一阶段不以“平台模块数量”为完成标准。

核心目标：

> **让一条真实 MSS 服务由三类用户完整跑通，并证明架构边界成立。**

推荐继续采用：

```text
设备策略检查
```

作为 黄金旅程。

---

## 9.2 黄金旅程

### SOP / 服务开发者

```text
人工策略检查 SOP
→ 建模 Service Definition（选择主 Agent）
→ 接入/复用 customer.get / device.list / policy_check.* Capability
→ 在 PyCharm 使用 Skill SDK 开发分析/转换 Skill
→ Mock + Dev Capability Gateway 联调
→ pack / 导入 Skill
→ Agent 绑定 Skill
→ Test Service
→ 交给 Admin Publish
```

### 管理员

```text
创建/管理 Platform User
→ 配置用户 × MSS 项目平台认证
→ 给用户授权目标 Agent
→ 在 Agent 的 IM 接入配置独立 WeCom Bot
→ 发布 Service
→ 用户可用
```

### 普通用户

```text
从企业微信进入
→ Channel Gateway 映射统一 PlatformUser
→ 加载 User Memory
→ “帮 A 客户做一次策略检查”
→ Agent 识别 Service
→ 自动查询客户/设备
→ 用户确认
→ Execution Service 创建 ServiceExecution
→ Worker Engine 后台执行
→ 用户可以离开
→ Worker 持续跟踪底层任务
→ Agent Step 分析结果
→ 生成建议
→ WeCom Bot WebSocket 主动推送
→ 用户查看完整结果
```

### 管理员排障

```text
Execution 失败
→ 根据 execution_id / trace_id
→ 查看 Service Version
→ 查看执行阶段
→ 查看 Capability 调用
→ 查看 retry / error
→ 判断恢复方式
```

---

## 9.3 第一阶段核心组件与部署单元

| 组件 | Phase 1 | 职责 |
|---|---:|---|
| Platform API | ✅ | Control Plane、Console API/静态资源、Service/用户/发布/审计 |
| Agent Runtime | ✅ | 无状态实时 Agent、LangGraph、Memory/Conversation、短查询 |
| ExecutionService | ✅ | 共享确定性 Application Service；可信 Execution 创建边界，不独立部署 |
| Worker Engine | ✅ | ServiceExecution 可靠生命周期 |
| Capability Runtime | ✅ | Agent/Worker 共享的业务能力调用 |
| User Memory | ✅ | 跨会话长期用户上下文，并为未来跨 Channel 做好统一 User scope |
| Skill SDK / Dev Capability Gateway | ✅ | 线下 Skill 开发、Mock 与真实 Capability 联调 |
| ProjectPlatform / UserProjectCredential | ✅ | 外部项目平台定义与“用户 × 平台”唯一认证 |
| Channel Gateway | ✅ | 统一 Channel 接入、路由和投递 |
| WeCom Adapter | ✅ | Phase 1 企业微信 Bot WebSocket |
| Future WebChat Adapter | 后续 | 业务平台 WebChat，复用同一 Channel Gateway |
| Console | ✅ | Admin/Builder 页面，静态资源打入 `platform-api` |
| PostgreSQL | ✅ | 权威状态存储 |
| Redis | ✅ | wake-up / cancel / pubsub / cache 等非权威协调 |
| Object Store | ✅ | 报告、Artifact、Skill/Knowledge 文件 |
| Secret Provider | ✅ | Credential / Platform Auth secret |
| OpenTelemetry | ✅ | Trace / Metrics / Logs |
| MCP Adapter | 按真实场景 | Capability Implementation |
| Knowledge / pgvector | 规划 | 外部知识库真实接入方案明确后再设计 |

### Phase 1 最终后台镜像

```text
1. platform-api
2. agent-runtime
3. worker
4. channel-gateway
```

不再单独交付 `console-web` 镜像；React Console 静态资源随 `platform-api` 镜像交付。

默认部署原则：

```text
platform-api
→ Control Plane，通常 1 replica，不按普通用户流量扩容

agent-runtime
→ Kubernetes Deployment，部署时创建，不按用户动态创建 Pod

worker
→ Kubernetes Deployment，部署时创建，不按 Execution 动态创建 Pod

channel-gateway
→ 独立 Deployment，Phase 1 加载 WeCom Adapter
```

未来副本扩缩由 Kubernetes/HPA/KEDA 管理，而不是由 `platform-api` 操作 Kubernetes Pod 生命周期。


---

## 9.4 第一阶段技术栈基线

```text
Control Plane
- Python 3.12+
- FastAPI
- Pydantic v2
- SQLAlchemy 2 Async
- asyncpg
- Alembic
- uv
- React + TypeScript + Vite + Semi Design
- Console static assets packaged into platform-api

Agent Runtime
- Python
- LangGraph Library
- LangChain Core / Model Adapter
- PostgreSQL Checkpointer
- Shared Agent Core: Memory / Skill / Knowledge / Capability

Worker
- Python
- 自研轻量 Worker Engine
- PostgreSQL authoritative execution state
- claim / lease / heartbeat / retry / wait / recovery
- Redis only for non-authoritative wake-up / cancel / coordination

Channel Gateway
- Node.js + TypeScript
- Channel Adapter SPI
- Phase 1: @wecom/aibot-node-sdk
- Future: WebChat / Feishu / DingTalk / Mattermost adapters

Storage / Infra
- PostgreSQL
- Redis
- S3-compatible Object Store
- Secret Provider
- OpenTelemetry
- Docker / Kubernetes / Helm
```

第一阶段不把 LangGraph Agent Server 作为核心依赖。

原因：

```text
Worker Engine 的职责
>
LangGraph Agent Run Worker
```

Worker Engine 还要统一承载：

- Capability Step；
- Wait / Timer；
- Human Checkpoint；
- Delivery；
- Generic background task；
- 未来 schedule/event；
- 不依赖 LLM 的固定流程任务。

因此避免同时维护两套 Execution Engine。


---

## 9.5 第一阶段硬架构约束

### 1. Agent Runtime 无状态

```text
Runtime A 处理 Turn 1
Runtime B 处理 Turn 2
```

必须正确。

不能依赖：

```text
local dict
local file
sticky session
```

保存 User / Memory / Conversation / Service / Execution 等权威状态。

### 2. Worker Engine 可跨实例恢复

必须验证：

```text
Worker A claim
→ 执行部分步骤
→ Worker A crash
→ lease 过期
→ Worker B reclaim
→ 不重复有副作用动作
→ 继续执行
```

### 3. Execution 创建与 Agent 推理分离

任何写操作、长任务和高风险执行都必须：

```text
Agent Proposal
→ Execution Service Validation
→ ServiceExecution
→ Worker
```

不能：

```text
LLM output
→ 直接执行生产写操作
```

### 4. PostgreSQL 是权威事实源

Redis 失效不得导致：

```text
User
Memory
Execution
Service Version
Task State
```

丢失。

### 5. Channel 与业务执行解耦

Channel Gateway/WeCom WebSocket 断开不能导致后台 Execution 丢失。

后台完成后根据持久化 `ChannelDeliveryRoute` 恢复主动通知。未来加入业务平台 WebChat 只新增 Adapter，不改变 Execution。

### 6. 版本必须 Snapshot

Execution 一旦创建，至少固定：

```text
Service Version（service_release）
Agent/Instructions Version（agent release/revision）
Skill Version（artifact checksum）
关键策略/配置（ExecutionSnapshot）
```

Capability Implementation 默认不 pin：执行时解析当前有效 Implementation；授权、Credential、紧急禁用等实时生效（与总设 §4.2 一致）。
运行过程中后台发布 V2，不能使 V1 Execution 的上述已固定内容漂移。

---

### 7. 身份、路由、授权不能混合

```text
external_user_id → PlatformUser
bot_id → Agent
User + Agent → AgentAccessGrant
```

任何一条关系变更都不能隐式修改另外两条。

### 8. Skill 与 Capability 的职责边界

Skill 可以处理业务数据，但不得自行保存用户 Secret、直接依赖生产服务发现、实现通用分页/重试框架。平台能力调用必须通过 Skill SDK / Capability Runtime。

### 9. Capability 自动分页必须可终止

任意分页能力必须有业务结束判断以及最大页数、最大数据量、最大时长等保护；不能只依赖无限 `while True`。

## 9.6 第一阶段暂不完整建设

除非 黄金旅程 实际证明需要，否则暂不建设：

- 完整 Workflow Designer；
- BPMN；
- 独立通用 Workflow Platform；
- LangGraph Agent Server；
- DBOS / Temporal；
- Plugin Marketplace；
- 动态 Hook 平台；
- A2A；
- 完整 Semantic Memory Platform；
- 完整 Knowledge Console / KnowledgeProvider / 外部知识库接入平台；
- 自动画像推断体系；
- Eval Platform；
- 通用 Approval Center；
- Kafka/NATS/EventBus；
- MCP Hosting Platform；
- 高级 Model Policy；
- 通用 Policy DSL。

其中：

```text
Worker Engine
```

不是通用 Workflow 产品。

第一阶段只实现真实 Playbook 所需的最小可靠执行能力。

---

## 9.7 黄金旅程 架构验收

### Agent Runtime

验证：

```text
Turn 1 → Runtime A
Turn 2 → Runtime B
```

User Memory / Conversation / Skill / Capability 保持一致。

### Worker Engine

验证：

```text
创建策略检查
→ external_task_id 持久化
→ Worker crash
→ 另一 Worker 恢复
→ 不重复 create
→ 最终完成
```

### WeCom

验证：

```text
用户在企业微信发起任务
→ 用户离开
→ 后台任务继续
→ 任务完成
→ 根据 DeliveryRoute
→ Bot WebSocket 主动推送用户
```

### Capability

验证：

```text
同一个 customer.get
被在线 Agent 查询复用
+
被后台 Service Execution 复用
```

底层实现细节不泄漏到 Service。

### Memory

Phase 1 验证跨会话持久化：

```text
WeCom Conversation A
→ 形成 User Memory
→ /new
→ WeCom Conversation B
→ 同一 PlatformUser 继续读取该 Memory
```

同时通过 Contract/Integration Test 验证 User Memory 的 key 不绑定具体 Channel。未来业务平台 WebChat Adapter 上线后，不修改 Memory Schema 即可复用同一 PlatformUser Memory。

---

## 9.8 第一阶段最小领域对象

当前建议：

```text
PlatformUser
AgentAccessGrant

ProjectPlatform
UserProjectCredential

ChannelAccount
ChannelIdentity
ChannelBinding
BindCode
ChannelDeliveryRoute

AgentDefinition
ModelConfig

SkillDefinition
SkillArtifact

CapabilityDefinition
CapabilityImplementation
DataRetrievalPolicy

ServiceDefinition（primary_agent_id）
ServiceRelease

Conversation
Message
UserMemory

ServiceExecution
ExecutionStep
ExecutionSnapshot
AsyncTaskRun（如 Step 为异步）

Artifact / Result
Audit / Trace
CredentialRef
```

不为 Knowledge、通用 Workflow Designer、复杂 Policy/Approval 等尚未验证的能力提前增加大量持久化模型。

## 9.9 后续功能进入版本的判断机制

未来提出任何新能力：

```text
Workflow
Memory V2
Plugin
Eval
EventBus
A2A
复杂审批
新执行器
```

必须回答：

1. 服务哪一类用户？
2. 对应哪条真实 旅程？
3. 当前 旅程 哪个步骤无法完成？
4. 有没有更简单的解决方法？
5. 是业务产品能力还是内部实现细节？
6. 是否已有多个真实场景证明值得平台化？

回答不清楚：

```text
不进入当前版本
```

---

## 9.10 当前核心产品与技术模型

最终关系收敛为：

```text
                   用户业务目标
                        │
                        ▼
                 Service Definition
                        │
               Instructions / Skill
                 Knowledge / Rules
                        │
                        ▼
                  Agent Runtime
                   Stateless
                        │
           ┌────────────┴────────────┐
           │                         │
      普通实时交互                真实业务执行
           │                         │
           ▼                         ▼
    Agent Executor             Execution Service
       LangGraph                     │
           │                         ▼
           │                  ServiceExecution
           │                         │
           │                         ▼
           │                    Worker Engine
           │          ┌──────────────┼──────────────┐
           │          │              │              │
           └─────────►Agent Step  Capability Step  System Step
                       │              │              │
                   LangGraph     Capability Runtime  Wait/Timer/
                                      │            Human/Delivery
                               ┌──────┴──────┐
                               ▼             ▼
                         Platform API       MCP
```

核心边界：

```text
Service
= 用户真正使用和交付的业务服务

SOP
= 组织完成 Service 的业务做法

Skill
= 可复用的 Agent 方法 / 知识资源

Agent Runtime
= 无状态的实时认知执行服务

Execution Service
= 将 Agent 提议转换成可信 Execution 的确定性边界

Worker Engine
= Service Execution 的可靠执行底座

LangGraph
= 共享 Agent Executor Framework

Capability
= 稳定业务能力合同

Capability Runtime
= 统一调用 Platform API / MCP 的执行层

Channel Gateway
= WeCom / Future WebChat / 其他 IM 的统一传输适配，不持有核心业务事实
```

到此为止，业务 Playbook 与技术架构已经形成一条完整可追溯链：

```text
用户 旅程
→ Service
→ Execution Mode
→ Agent / Execution Service / Worker
→ Capability
→ 现有业务平台
```



### V6 产品关系补充

```text
ProjectPlatform
    └── Platform Service Capability
            ↑
Skill ──ctx.capability.call()──┘

PlatformUser ──AgentAccessGrant──> Agent
ChannelIdentity ──binding─────────> PlatformUser
ChannelAccount(bot_id) ───────────> Agent

Service ──primary_agent───────────> Agent
```

关键解释：

- Skill 的 `platform_label` 只是文本；运行平台依赖通过 Capability 间接形成；
- User/Agent Grant 与企微身份绑定无关；
- Bot 与 Agent 1:1，Gateway 与多个 Bot 1:N；
- Service 必须有一个主 Agent；
- End User 不访问 Console。

### V6 历史实践吸收原则

新平台重新开发，但以下已经通过历史项目暴露或验证的经验作为架构证据：

```text
muad-openclaw
→ Channel/绑定码/Platform User 权限复用
→ Platform Session 静默登录
→ Skill/Browser/Progress/多用户并发实战
→ 有状态 Pod 导致 User→Pod 耦合的反例

fluxion-harness
→ Stateless Runtime
→ Execution Snapshot
→ Capability Contract / Resolver
→ Memory 边界
→ Worker/Durable Execution 试错
→ Control Plane/Execution Plane
→ Architecture Gate
```

这些经验只能决定“需要避免什么、验证什么”，不能直接把旧项目对象模型复制到新平台。

## 附录：需求来源说明

本文中的业务背景、普通服务人员“一句话发起”、平台统一运行、长任务持续执行、身份与权限、Skill 统一发布、策略检查服务旅程、异常处理和非功能性要求，来自现有 MSS-Claw 用户需求材料。

本文的场景建模方法采用用户需求模板中的：

```text
现状旅程
→ When
→ Do-What
→ How-To
→ 痛点
→ 产品 Playbook
→ 详细需求
```

企业微信 IM 的技术调研参考企业微信团队维护的 `WecomTeam/wecom-openclaw-plugin`；主动进度与通知的数据模型同时参考现有 `muad-openclaw/tools/muad-progress` 的实践。本方案吸收其 Channel 与 Progress 经验，但不继承 OpenClaw Plugin ABI 或“动态创建 Agent”等具体产品模型。企业微信 Bot WebSocket 主动推送能力已通过现有实践验证，V4 将其作为第一阶段确定能力。

以下内容属于本次重新构想后的设计结论，而不是源文档既有结论：

- Service Definition 作为 SOP/服务的上层业务抽象；
- SOP 不默认等于 Skill；
- Agent 自主型 / 固定流程型 / 混合型只是执行方式，不是三个组件；
- Capability Contract 隔离 Service 与 HTTP/MCP/平台接口；
- Agent Runtime 与 Reliable Task 分担认知执行和可靠执行；
- 第一阶段通过 黄金旅程 反推功能范围；
- User Memory 第一阶段即进入范围，但先采用最小可用的持久化模型；
- Agent Runtime 无状态作为第一阶段硬约束；
- WeCom Bot WebSocket 作为第一阶段 IM 主通道，并支持后台主动推送；
- Execution Service 作为 Agent → Worker Engine 的可信执行边界；
- Worker Engine 作为完整 Service Execution 的可靠执行底座；
- LangGraph Library 作为共享 Agent Executor，而非完整 Worker Engine；
- 在线 Agent 与后台 Agent Step 共用 Skill / Knowledge / Capability/MCP 体系。
