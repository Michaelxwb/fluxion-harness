# 71 Commits 关键问题整改需求与设计简报

> **文档编号**：MOD-CRITICAL-FIX-001  
> **文档版本**：v0.3  
> **创建 / 修订日期**：2026-09-06  
> **文档状态**：修订草稿，待设计评审与 TASK 重新对齐  
> **核查基线**：`main` HEAD `796eb0e`

**评审边界**：第 2 章为需求基线，第 3 章为技术设计。本文中的方案和验收均为待实施要求，不代表测试已通过。本次仅修订设计；既有任务文件及 Spec Context 不因此自动更新或完成。

**ID 体系**：FEAT（功能）、S（正常场景）、E（异常 / 边界场景）、NFR（非功能指标）。保留 FEAT-01～07 和原 S-01～07、E-01～03 的编号，修订其语义，新增 FEAT-08/09。

## 1. 文档控制

### 1.1 责任与事实源

开发负责人负责方案和实现，测试负责人负责真实边界证据，架构负责人负责核心 Contract 变更的 ADR。人员待项目分配。

`code-review-analysis.md` 是问题输入；本次逐项代码核查修正其中的排除项与误判。项目 AGENTS.md 的架构规则优先，本文不授权修改核心 Contract。术语固定：AgentDefinition 是逻辑 Agent，RuntimeInstance 是执行进程 / Pod，RuntimeProfile 是配置资源。

### 1.2 修订历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1 | 2026-09-06 | 初始草稿 |
| v0.2 | 2026-09-06 | 按代码实证重写分页、Credential 投影、Memory 装配和 token 方案；补入摘要丢失、流式压缩；修正 HTTP 契约、探针入口、部署测试范围及验收证据要求 |
| v0.3 | 2026-09-06 | S-01 验收策略拆分：S-01a（HTTP 执行拓扑全链，确定性 Provider）为本轮验收；S-01b（真实模型）移 follow-up（需模型凭据环境）。不改任何行为 Contract 与接口 |

### 1.3 问题核查结论

| Review ID | 结论 | 代码证据与边界 | 设计落点 |
|-----------|------|----------------|----------|
| P-01 | 存在 | `api/production_bundle.py` 创建本地 Runtime，Channel 和 Studio 仍可直接执行 | FEAT-01 |
| P-02 | 存在 | Helm 主 Service 只有公共 selector，Runtime Pod 共享这些标签；缺独立 Runtime Service | FEAT-02 |
| P-03 | 存在，原 review 排除错误 | `runtime/memory.py` 写入 summary 并删除被压缩消息；`runtime/agent.py::_model_messages` 只接收 user/assistant，丢弃摘要 | FEAT-08 |
| P-04 | 存在，已可确认 | `AgentRuntime.stream_final_answer` 和 `ExecutionSession.prepare` 不压缩；成功流式分支绕过 `run_step` | FEAT-09 |
| P-05 | 装配缺口存在 | ContextResolver 和 create_dev_bundle 已支持注入，但 production/dev 入口未传入 retriever；B-E-04 显式注入测试不证明默认装配 | FEAT-07 |
| P-06 | 存在，原描述过宽 | httpConsoleApi 五处固定第一页 100 条；部分接口已动态分页，不能概括为全部列表均缺分页 | FEAT-03 |
| P-07 | 存在 | CredentialsPage 发出 2 + N + M 个 HTTP 请求并客户端关联；这是已证实的请求放大，不等于每个后端请求内部也有 N+1 | FEAT-04 |
| P-08 | 覆盖缺口部分成立 | 已有 test_k8s_deployment.py / test_k8s_gate.py；未见独立 API → Runtime 调用链与 Worker 共存拓扑的完整验收 | S-01、S-02、S-05 |
| P-09 | 存在 | runtime-deployment.yaml 无 readiness/liveness probe；API 探针不能覆盖 Runtime | FEAT-05 |
| P-10 | 存在 | Runtime HTTP 入口无调用方身份校验，require_identity=False；优先级低不代表无缺陷 | §2.3、§4.1 |
| P-11 | 部分成立 | execution.step 用 split 计输入；Memory 已补 CJK 字符计数；memory_user_service 的 split 用于 embedding，非 token 计数 | FEAT-06 |

代码路径以上均相对 `backend/src/fluxion/`，测试路径相对 `backend/tests/integration/`，Helm 路径相对 `deploy/helm/fluxion/templates/`。

## 2. 需求分析

### 2.1 目标

修复生产执行边界、列表可见性、请求放大、健康摘流与会话上下文丢失；以真实入口验证 Memory manifest 装配，纠正输入估算口径。Runtime 保持无状态，ExecutionSnapshot 固定版本，SQLite/PostgreSQL 保持同一查询语义。

### 2.2 功能清单

| 功能ID | 名称 | 范围 | 优先级 | 来源 |
|--------|------|------|--------|------|
| FEAT-01 | Runtime 执行面拆分 | 生产 Channel / Studio 执行经 HTTP Gateway，生命周期迁至独立 Runtime | P0 | P-01 |
| FEAT-02 | Service 角色隔离 | API / Runtime 标签、独立 Service、发布升级路径 | P0 | P-02 |
| FEAT-03 | 服务端分页与搜索 | 复用后端分页，补搜索过滤，改造全部受影响调用方 | P0 | P-06 |
| FEAT-04 | Credential Projection API | 基于真实资源结构，固定次数批量读取，替代客户端逐条详情和关联 | P1 | P-07 |
| FEAT-05 | Runtime 健康检查 | 独立 Runtime HTTP 探针、依赖故障摘流 | P0 | P-09 |
| FEAT-06 | 输入 token 估算修复 | 修复 execution.step 估算，保留估算与实际 usage 的区别 | P1 | P-11 |
| FEAT-07 | Memory manifest 装配 | 实际执行入口注入现有 PersonalMemoryRetriever 和真实 Provider | P1 | P-05 |
| FEAT-08 | 摘要进入模型上下文 | 压缩后摘要不被 Prompt 构建过滤，普通与流式一致 | P0 | P-03 |
| FEAT-09 | 流式统一压缩 | 无工具流式在调用模型前执行与非流式一致的压缩准备 | P1 | P-04 |

### 2.3 范围与边界

- 本轮包含 FEAT-01～09、相关 HTTP / 数据库 / 前端 / 部署验收。P-08 的相关拓扑测试纳入交付，不因其属于测试问题而排除。
- FEAT-07 仅闭合真实检索到 ExecutionSnapshot manifest 的装配。Personal Memory 内容进入 AgentLoop、执行后 learning / embedding 算法改造另行设计，不以 manifest 可用宣称个性化记忆完整闭环。
- P-10 保留为真实安全缺口，服务身份、认证与租户授权信任链需要独立设计和 ADR（如涉及核心 Contract）。本轮不凭空选定 mTLS/JWT/HMAC。ClusterIP 和角色 selector 不构成身份认证；启用远程执行前必须完成该信任边界评审，不能宣称本设计已满足生产认证要求。
- Provider 实际 usage 需 typed ModelResponse / Provider 适配链支持，当前 ModelResponse 没有 usage 字段。此扩展以 ADR 和重新对齐 TASK 为前置，不属于本轮直接修改权限。
- 不新增无失效策略的 total 缓存，不引入第二套 UI 库，不重建已有核心分页 Contract，不以 Noop 检索冒充 Memory 接通。
- S-01b（真实模型全链）为 follow-up：需模型凭据环境；本轮以 S-01a（确定性 Provider 同链验收）闭合 FEAT-01，不宣称已验证真实模型腿。

### 2.4 验收条件

#### 2.4.1 功能验收场景

所有场景初始状态均为 planned。编码前记录 RED，完成后记录 GREEN、命令、数据规模、环境与关键断言。需要的外部环境不可用时明确未验证，不用 Mock 结果替代真实边界证据。

| 场景ID | 功能 | 层级 | 不得 Mock 的边界 | 关键断言 |
|--------|------|------|------------------|----------|
| S-01 | FEAT-01 | E2E | Chat / Studio HTTP → API → Runtime Service → Runtime Pod → 真实模型；独立 Worker 同部署 | 普通与流式结果正确；API 未装配本地 AgentRuntime；执行 trace 指向 RuntimeInstance；API / Runtime 副本可独立变化；Worker 工作负载仍正常。v0.3 拆分：S-01a（本轮）以确定性 Provider（dev.echo）走完全相同 HTTP 调用链验收拓扑与隔离；S-01b（真实模型）为 follow-up，需模型凭据环境，不阻塞本轮交付 |
| S-02 | FEAT-02 | integration | Helm render、K8s、Service EndpointSlice | 主 Service 只选 API，Runtime Service 只选 Runtime，均排除 Worker；自定义 release/fullname 下 URL 与名称一致；验证旧版本升级路径 |
| S-03 | FEAT-03 | E2E + contract | 浏览器 → API → 真实 SQLite / PostgreSQL | 150 个逻辑资源且含多版本；每页 20 条时第 6 页出现第 101～120 条，第 8 页为末 10 条；total=150；搜索命中原第一页之外的记录；覆盖 §3.2.3 受影响页面 |
| S-04 | FEAT-04 | integration + UI | CredentialsPage → API → Repository → SQLite / PostgreSQL | 1000 个 Credential、3000 个 Provider 关联；列表一次 HTTP；投影 SQL 次数上限 3，条目增长不增加查询次数；消费者不受原 100 条限制；无 Secret 密文 / 明文泄漏 |
| S-05 | FEAT-05 | integration | K8s → 独立 Runtime /readyz → EndpointSlice | Registry 故障返回 503，Runtime 摘流；恢复后重新接流；liveness 不因单纯依赖故障失败 |
| S-06 | FEAT-06 | unit + integration | 实际估算函数、AgentRuntime、trace | “帮我查询订单”输入估算不再为 1；中英混合、空输入、长输入边界固定；估算标识清楚，不冒充计费 usage；Memory CJK 估算不倒退 |
| S-07 | FEAT-07 | integration | dev / 独立生产 Runtime 实际装配 → Provider → ContextResolver → Snapshot | 预置已知记忆后 manifest 包含相同 entry_id 和内容 hash；同 tenant/user 跨实例一致；保留 B-E-04 凭据真实版本验收 |
| S-08 | FEAT-08 | integration | 实际 SessionMemoryStore → compaction → 模型请求构建 → Provider 请求边界 | 旧事实只存在于已压缩摘要时，普通与流式模型请求均包含该事实；保留最新消息和当前输入且不重复；Snapshot 不被修改 |
| S-09 | FEAT-09 | integration | 超预算会话 → 成功流式 Provider → 持久化 / trace | 无工具流式在模型调用前触发压缩；与非流式得到等价历史；输入和输出各保存一次；覆盖有工具分支与不支持流式的回退 |
| E-01 | FEAT-01 | E2E | API → Runtime Service | 无可用实例时普通调用 503；流式建连前失败按 HTTP 错误返回，建连后错误按 SSE error 终止；不自动重试执行、不回退 API 本地执行 |
| E-02 | FEAT-03/04 | E2E + contract | API、Store、浏览器 | 越界页为空且 total 仍正确；空数据、中文、特殊字符、筛选变化、乱序响应和跨 tenant 请求均正确 |
| E-03 | FEAT-05 | integration | 独立 Runtime /readyz、故障 Registry、K8s | 连接 / 查询超时在预算内返回 503，响应不含 DSN/SQL/Secret；日志可关联；摘流时间满足 NFR |
| E-04 | FEAT-07 | integration | 真实 Provider、装配与 Resolver | 空记忆是成功空 manifest；None / 异常 / 超时为 unavailable 且可观测；tenant/user 隔离；不会以空实现通过 S-07 |
| E-05 | FEAT-08/09 | integration | 摘要失败 / 超时、SessionMemoryStore、Provider 请求 | 沿用有界失败策略；无成功摘要时不删除旧消息；流式失败不重复执行；关闭 / 取消释放资源 |
| E-06 | FEAT-04 | contract | 双库真实版本数据与分页查询 | 同名跨 kind、多版本、draft-only、零消费者、跨租户同 SecretRef、不存在关联均正确；消费者按逻辑 Provider 去重，total 与过滤一致 |

S-08/09 可以使用记录请求的确定性 Provider 测试适配器；不得替换 Memory、压缩调度与 Prompt 构建。断言发出的 ModelRequest 内容，不依赖真实 LLM 偶然回答。S-01 另行验证真实模型链路。

#### 2.4.2 非功能指标

| 指标ID | 指标 | 目标 | 测量与限制 |
|--------|------|------|------------|
| NFR-PERF-01 | Runtime 框架开销 | P95 ≤50ms、P99 ≤100ms | 分离模型 / Tool 外部耗时，跨进程 trace 测量；不预设未经测量的网络增量 |
| NFR-PERF-02 | Credential 投影 / 页面 | API P95 ≤300ms；页面 P95 ≤500ms | 记录 1000 / 3000 数据夹具、硬件、并发、预热、样本；不声称已有 >1s 基线 |
| NFR-PERF-03 | 列表读取 | SQL 含 count P95 ≤200ms；API P95 ≤300ms | 双库分别报告；JSON 搜索 / 索引效果以执行计划及实测为准 |
| NFR-REL-01 | Runtime 故障摘流 | 稳态故障注入到摘流 ≤30s | 包括探测周期、请求超时和 EndpointSlice 传播，不能仅以周期乘阈值证明 |
| NFR-ACC-01 | token 估算 | 中文不退化为单词数；结果可重复、口径可辨识 | 当前不承诺对所有模型误差 ≤10%；真实 usage 扩展后另定义准确率验收 |
| NFR-TEST-01 | 验收覆盖 | P0/P1 场景自动化率 ≥95%；双库 Contract Test 100% 通过 | 跳过项不是通过；保存真实环境证据 |

## 3. 技术设计

### 3.1 技术选型与依赖方向

沿用 Python 3.12+、SQLAlchemy、SQLite / PostgreSQL、httpx、React 19、Semi Design 2.102.x。入口继续先导入 react19-adapter。此次不预定新增 tokenizer 依赖。

HTTP Handler → Application Service → 查询接口 / 领域 Contract → Repository / Provider。SQL 仅在持久化实现层；Runtime 从 Registry 读取配置，不通过 Console API 读事实。新数据对象完整类型注解，使用既有不可变 dataclass / Pydantic 风格。

### 3.2 架构设计

#### 3.2.1 FEAT-01：Runtime 执行面拆分

目标链路：Channel / Studio → RuntimeGateway → HttpRuntimeGateway → Runtime Service → RuntimeApplicationService → AgentRuntime。Console 与 Runtime 共享 Contract，进程与部署独立。

- 复用 `services/channel_app.py` 的 RuntimeGateway；新增 `services/http_runtime_gateway.py`，实现 run / stream，保持调用方返回类型。
- 复用 `api/runtime.py` 的真实路由和 §3.3.1 wire 格式。生产 API 的 Channel 与 Studio test-run 都迁到 Gateway，不能只替换 Channel 就宣称 API 不再执行模型。
- 梳理 production assembly 的 runtime 字段、初始化 / close、outbox worker、Studio 参数依赖。Runtime 生命周期和执行侧 worker 留在独立 Runtime；控制面自身需要的组件按其职责保留。不得让 assembly 继续通过一个“占位 Runtime”维持旧生命周期。
- `create_runtime_app_from_env()` 装配执行侧 Store、Secret、Trace 和 Memory；dev 保留显式本地 bundle 模式。生产远程失败不自动降级为 dev 或本地执行。
- `FLUXION_RUNTIME_SERVICE_URL` 由 Helm 注入 `http://<fullname>-runtime:8000`；非 Helm 远程模式必须显式配置。避免写死 release 名。
- httpx.AsyncClient 按应用生命周期复用、关闭。连接 / pool timeout 默认 3s，write 默认 10s；读取预算按执行 deadline 加有界传输余量配置，流式另设 idle timeout。所有值必须有限，不能固定 30s 截断合法长执行。
- 执行 POST 默认不重试，防止已提交但响应丢失时重复副作用。连接失败映射类型化 RuntimeApplicationError / 503；流式错误和取消关闭 upstream，已发 token 后不能切换路由重新执行。熔断本轮不引入，采用有界超时与快速失败策略。
- tenant/user 从 Channel 已认证上下文映射，request_id 经 X-Request-ID 关联。远程执行返回的 execution_id/trace_id 为本次实际执行标识；不能假设现有 HTTP Payload 已透传调用方全部字段。核心 ID 语义若需变更先 ADR。

#### 3.2.2 FEAT-02：Kubernetes Service 路由隔离

- API Pod template / metadata 添加 `app.kubernetes.io/component: api`；新部署 selector 加同一标签。Runtime 保持 component=runtime，Worker 保持自身角色。
- 主 Service 使用现有 name/instance 公共 selector 加 component=api。新增 `<fullname>-runtime` ClusterIP Service，selector 加 component=runtime，8000 → http。
- Runtime 副本设为 0 时允许 Service 无 endpoint，远程请求按 E-01 失败；不自动选择 API Pod。Helm 配置应明确远程执行依赖 Runtime 副本可用。
- 旧 Deployment 的 selector 不可原地变更：发布计划先为旧 Pod template 添加 api 标签并完成滚动，再收紧 Service；需要更改 Deployment selector 时使用替代 Deployment 切换流量后退出旧对象。不得直接提交不可升级的 Helm patch。
- 验证自定义 release/fullname、API/Runtime/Worker 混部标签与 EndpointSlice；按实际渲染名查询，不硬编码 `<release>-fluxion`。

#### 3.2.3 FEAT-03：前端真实服务端分页

**当前能力与存储**：资源列表入口是 `api/console.py::list_resources`；credentials/runs/policies 等在 `console_routes_read.py`。资源服务已有 `list_current_resources`、offset/limit 和 count。资源存于 `resource_definitions`，显示名取 `spec_json.name`，无独立 display_name 列。

**查询设计**：

1. 保留 page≥1、page_size=20 默认、最大 100。100 是单页边界，不是总量上限。复用现有分页能力，不新增功能重复的核心 list_resources_paginated Contract。
2. Console 读模型扩展 keyword 和各页面已有过滤条件；通过专用查询接口 / Repository 实现，不向 Handler 或 Service 塞 SQL。若实现必须修改 RegistryStore 核心 Contract，先 ADR 和重新对齐任务。
3. 按 tenant、kind、resource_id 选择当前版本：复用现有按版本长度和版本号排序规则。先选当前行，再对 name / resource_id / status 等过滤，避免旧版本命中导致历史资源被重新显示。Runtime latest-published 路径不受影响。
4. keyword 去首尾空格，名称 / ID 按大小写不敏感的字面子串匹配；绑定参数并转义 LIKE 通配符。JSON 字段提取在 SQLAlchemy 双库适配层实现。页面额外字段过滤遵循其展示语义，并在双库测试中固定。
5. 过滤后的同一逻辑集合用于分页和 count；按 kind、resource_id 稳定排序。越界页仍单独返回正确 total。并发写入时普通列表允许下次刷新反映变化；单次投影的行和 count 使用一致读视图。
6. 索引依据实际 schema、数据与 EXPLAIN 决定；不能假定任意包含搜索都能走 B-tree。新增 schema / 索引迁移须双库可执行且可回滚或幂等。

**前端覆盖矩阵**：

| 请求 / 页面 | 改造要求 |
|-------------|----------|
| resources；Credentials、Capabilities、Models、Agents、Workflows | 移除固定第一页和七个页面中的对应 slice；各页面过滤在服务端分页前执行 |
| runs；RunsPage | 动态 page/pageSize，状态 / keyword 搜索全量结果 |
| policies；GovernancePoliciesPage | 动态分页和全量搜索过滤 |
| credentials、platform-users 固定请求的调用方 | 核查 httpConsoleApi 所有消费者；已支持动态分页的 UsersChannels 等保留能力，不能改回本地分页 |
| 页面选择器、概览和详情关联消费者 | 明确是分页选择、远程搜索还是有意 top-N；需要完整枚举的选择器不得静默只取 100 条 |

这里七个直接 slice 页面是 Credentials、Capabilities、Models、Agents、Workflows、Runs、GovernancePolicies。接口变更同步 ConsoleApi 类型、HTTP 实现、测试实现及调用方；Credentials 最终接 FEAT-04 投影，不同时保留两套加载逻辑。

分页 / 搜索 / 筛选用受控状态，搜索和过滤变化回第 1 页，取消或忽略旧请求防乱序覆盖。直接使用 API items/total；保持 loading/error/empty/retry，越界空页也保留回页能力。复用 StandardListShell、Semi Table / Pagination / Input，组件不裸 fetch。

#### 3.2.4 FEAT-04：Credential Projection API

新增只读 `GET /api/v1/credentials/projection`，复用 Console 认证、授权、响应与错误基础设施。该静态路由需避免被动态 credential 路由捕获。

**投影语义**：本轮保持 CredentialsPage 的资源视角。每个当前 SECRET 资源对应一行，名称、purpose、secret_ref、revoked 来自该版本 spec，status 为 ResourceStatus，updated_at 复用现有详情序列化口径。Provider 使用方取当前 MODEL_PROVIDER 资源的 `spec_json.credential_ref`，按逻辑 Provider ID 去重。

`secret_credentials` 是 SecretStore 的持久化表，包含 nonce/ciphertext 等；它不是 Console SECRET 资源的替代表，也不能把跨版本密钥行直接 JOIN 成页面行。本投影不读取密文，不解密 Secret，不以资源元数据 revoked 代替执行时真实 SecretStore 校验。Binding 覆盖的有效凭据 / 使用方分析属于后续 effective usage 设计；当前字段应明确标注“Provider 配置引用”，不能宣称全量有效使用关系。

**固定次数查询**（`repositories/credential_projection.py`，经查询接口注入 Service）：

1. 查询 A：tenant 内当前 SECRET 行，应用 keyword / purpose / status / revoked 过滤、排序和分页，仅选择必要元数据。
2. 查询 B：相同当前行选择与过滤条件的 count；即使查询 A 为空仍得到 total。
3. 查询 C：针对本页 distinct SecretRef，在 tenant 内先选当前 MODEL_PROVIDER，再过滤 credential_ref 集合，读取 ID / name / ref。应用层对批量结果分组，生成 consumers 和 consumer_count，不逐 Credential 查询、不先把 Provider 列表限制到 100 条。

同一请求在适配器提供的一致读事务内完成（PostgreSQL 只读 REPEATABLE READ / SQLite 显式读事务），查询预算最多 3 次，空页可跳过 C。count 不因分页而截断。单个 Secret 的消费者数量可能很大，记录关联行数和响应大小；若容量测试超标，先修订为消费者独立分页契约，不静默截断。

结构沿用 `credential_id`（对应 SECRET resource_id）、display_name、secret_ref、purpose、revoked、updated_at、consumer_count、consumers、status；消费者包含 provider_id/provider_name。数据对象 frozen/slots、完整类型注解。以 SQL 次数计数器证明无 N+1；EXPLAIN 用于评估各查询计划，不替代次数统计，也不强制 JOIN/JSON 聚合为单条 SQL。

#### 3.2.5 FEAT-05：Runtime 健康检查

独立 Runtime 的路由在 `api/runtime.py`，由 `create_runtime_app_from_env()` 启动。`RuntimeApplicationService.ready()` 当前通过 Registry `get()` 检测读路径，不能拿 API bundle 的 SELECT 1 作为该入口验收证据。

Runtime Deployment 添加：liveness `/healthz`、periodSeconds=10、timeoutSeconds=2、failureThreshold=3；readiness `/readyz`、periodSeconds=5、timeoutSeconds=2、failureThreshold=3，initialDelaySeconds=5。应用内 readiness 检测预算默认 1s、无重试，小于探针超时。慢启动可配置 startupProbe，单独验证启动窗口。

liveness 只判断进程可服务，不依赖 Registry；readiness 覆盖 Registry 连接 / 查询超时及故障，统一 503，日志保留关联错误并脱敏。故障恢复后重新 ready。按真实时间测量故障到摘流，不以配置算式代替验收；发布时记录探针与应用预算。

#### 3.2.6 FEAT-06：Token 统计修复

当前 `execution.step.input_tokens` 在模型调用前记录当前用户文本的 split 估算。它既不是完整 Prompt 大小，也不是 Provider 计费 usage。ModelResponse 只有 provider_id/content/tool_calls，无 usage。

本轮复用 / 提取 Memory 的现有估算算法作为单一内部估算工具，修复 execution.step 中文低估；保留该事件“当前输入估算”的兼容语义，并通过现有可扩展事件 payload 的来源标记 `token_source=estimate`、范围标记 `token_scope=input_message` 明确口径。若事件 schema 不允许新增元数据，先进行契约评审，不静默改变字段含义。空输入保持现有估算下限，测试固定边界。

Memory 已按 split 数加 CJK 字符数估算：六字中文样例现值为 7；不得退回恒为 1 或简单长度除二并声称更准确。`memory_user_service._recompute_embedding()` 保持原职责，不因出现 split 而修改。

后续实际 usage 扩展需 ADR 定义 typed 可选计数、Provider 解析、流式 usage、工具多轮 / 重试统计及兼容策略。完整 prompt_tokens 不能覆盖单条输入估算；未知 usage 不填 0 冒充真实消耗。本轮无新增 tiktoken、无全模型 ≤10% 误差承诺、无未经测量的 CPU 开销结论。

#### 3.2.7 FEAT-07：PersonalMemoryRetriever 接入

已有 `PersonalMemoryRetriever(provider: SemanticStoreProvider)`，方法是 `recall(tenant_id, user_id, query, top_k, timeout_ms)`；ContextResolver 和 RuntimeApplicationService 已接受并透传它。

- 在实际执行侧组装 `PgVectorSemanticStore(engine)`，再构造 `PersonalMemoryRetriever(provider)`。初始化放应用 lifespan 的同一事件循环，受有限启动预算控制，初始化失败明确报错；共享 engine 随持有方关闭。
- 生产远程模式接入独立 `create_runtime_app_from_env()`，dev 接入 `create_dev_bundle_app()`。若拆分前测试生产本地 bundle，也应覆盖其注入；拆分后的 API 不再持有 Retriever / AgentRuntime。
- 沿用 Provider 双库能力，不以名字推断 SQLite 不可用。查询经 tenant+user scope，top_k 受预算限制；本轮 Resolver 调用设置有限 recall timeout（默认 1000ms，可配置），不自动重试，异常 / 超时降级并记录 request_id/trace_id 的脱敏日志和失败指标。
- 成功空结果生成正常空 manifest（content_hash 为空）；None 或失败为 unavailable。这两者需要分开验收。现有“hash 不等于 unavailable”只能证明未走失败分支，不能证明召回了记忆。
- 预置已知记录，断言 entry_refs、内容 hash、预算截断和隔离；保留 B-E-04 credential_versions 真实化回归，不用 Noop 或错误的 retrieve 方法通过验收。

#### 3.2.8 FEAT-08：Session summary 进入模型 Prompt

`MemoryManager.compact_context()` 已把摘要作为 role=summary 写入 session_context_summary；`read_session_context()` 可读回。修改共同的 `_model_messages()` 消费路径，使该记录进入普通与流式请求。

摘要转换为现有 ModelMessage 支持的上下文消息：使用 user role、显式“历史会话摘要，仅作上下文资料”边界，再保留最新 user/assistant 历史，最后追加当前输入一次。不得提升为 system 指令或混入系统策略。摘要内容按不可信历史处理；测试中使用命令式摘要验证不会改变 system prompt。

沿用 Store 返回顺序及现有摘要存储语义，确保摘要位于其后的最新原始消息之前；重复压缩测试覆盖已有摘要与新摘要，不把 summary 再当原始 turn 重复压缩。仅在成功写入摘要后删除相应旧消息；失败场景不得静默丢数据。Session summary 保持 tenant+session scope，不写 Personal Memory / user L2，Snapshot 的资源版本与 hash 不原地修改。

#### 3.2.9 FEAT-09：Streaming 统一 compaction

在 AgentRuntime 内提取模型调用前的共享历史准备流程：读取历史 → maybe_compact → 如发生压缩重读 → 交由共同 Prompt 构建函数。run_step 和 stream_final_answer 均调用它；不把压缩塞进仅部分调用方会执行的上层流程。

本轮保持现有“已存历史”触发阈值，不声称已覆盖当前输入、系统 Prompt、Tool schema 的完整 context window 预算；完整预算策略另行设计。不得为成功流式额外调用 run_step，从而重复请求模型或重复保存消息。

有工具路径仍走 run_step；不支持流式的路径回退普通执行时，重复准备必须无破坏性、不能重复摘要已压缩消息；成功流式由现有保存分支持久化输入输出各一次。摘要 Provider 超时沿用有界 fallback / 错误策略，失败不吞异常；日志、compaction trace 与实际执行关联，取消与错误都释放流和执行资源。

### 3.3 接口设计

#### 3.3.1 HttpRuntimeGateway 内部 HTTP 接口

复用已有路由：

- `POST /internal/v1/runtime-profiles/{runtime_profile_id}/runs`
- `POST /internal/v1/runtime-profiles/{runtime_profile_id}/runs:stream`

路径参数来自 RunRuntimeRequest.runtime_profile_id 并正确 URL 编码。请求通过 X-Request-ID 关联；JSON 请求示意：

```json
{
  "tenant_id": "tenant-a",
  "user_id": "user-a",
  "session_id": "session-a",
  "input": "帮我查询订单",
  "runtime_profile_version_selector": "latest-published",
  "agent_definition_id": "assistant",
  "tool_calls": []
}
```

`input` 使用 RunPayload 的序列化别名。不得新增未经实现的 `/internal/v1/runtime/run` 路由。普通响应由统一 success 包装，data 按 RunRuntimeResult.to_payload 解析：request_id、trace_id、execution_id、service_instance_id、runtime_profile_id、runtime_profile_version、output、latency_ms、model_provider_id、tool_results。Gateway 检查 HTTP 状态和业务 code，错误不伪装成成功结果。

流式使用既有 `started`、`token`、`completed` 及 API 层 `error` 事件。token.data 含 content；completed 载荷按实际分支契约处理，不假定流式拥有普通结果的所有字段。Gateway 还原 RuntimeStreamEvent，处理跨网络 chunk 的帧、多字节文本、断连与取消；不改名为 data/done，不缓冲完整响应后伪装流式。

当前 RunPayload 未承载调用方全部 trace_id/execution_id 字段，身份与 ID 完整传播属于 §3.2.1 的契约评审点，不能把“请求转发”当作其已解决的证据。

#### 3.3.2 Credential Projection API

`GET /api/v1/credentials/projection` 参数：page（默认 1）、page_size（默认 20，最大 100）、keyword、purpose、status、revoked（后四者可选）。参数组合为 AND；status 使用已有 ResourceStatus，revoked 单独表示凭据资源元数据。tenant 从可信认证上下文获得，不接受用户通过查询参数切换 tenant。

响应由统一 `success()` / `ApiResponse[PageData[CredentialProjection]]` 基础设施生成，Handler 不手写 envelope。数据示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [{
      "credential_id": "cred-a",
      "display_name": "模型凭据",
      "secret_ref": "opaque-secret-ref",
      "purpose": "model",
      "revoked": false,
      "updated_at": "2026-09-06T10:00:00Z",
      "consumer_count": 1,
      "consumers": [{"provider_id": "provider-a", "provider_name": "模型服务"}],
      "status": "published"
    }],
    "page": 1,
    "page_size": 20,
    "total": 1
  },
  "request_id": "req_example"
}
```

SecretRef 为不透明引用，不重新拼接或泄漏密钥；updated_at 类型与已有详情一致。前端适配器负责 snake_case 到既有页面类型的映射。现有 credential 写接口、轮换 / 禁用确认与独立 AuditLog 继续沿用。

### 3.4 性能、容量与可观测性

- HTTP 连接池复用，记录请求延迟、错误率、取消及 RuntimeInstance；从分布式 trace 分离传输和模型 / Tool 时间。CPU / 内存“无变化”不能证明 API 未执行模型。
- SQL 显式选择元数据列，参数化、tenant scope、当前版本选择与稳定排序统一。先建立真实双库基线再确定索引；COUNT OVER 不保证免扫描，也不能在空页提供 total，不以其代替正确的 count。
- 本轮不加 total 缓存，不宣称查询复杂度为 O(log n)，不把 JSON_AGG 当性能保证。查询次数、返回行数 / 字节、P95 都进入验收。
- Token 估算是文本扫描，split 不因中文只有一个结果就变成 O(1)。执行估算开销实测，不把估算工具当全模型 tokenizer。
- Memory recall 和 summarizer 必须有 timeout、fail policy、trace；容量测试同时记录用户记忆规模，Provider 的 Python cosine 降级不能因有 top_k 就被视为扫描有界。
- 错误响应不暴露 DSN、SQL、Secret；日志使用统一 RequestContext / structlog 和脱敏，区分健康空结果与降级，避免静默吞异常。

## 4. 风险与依赖

### 4.1 风险及门槛

| 风险 | 应对措施 / 完成条件 |
|------|--------------------|
| Runtime 内部调用缺认证 | 记录 P-10 为未解决风险；远程生产启用前完成服务身份与 tenant 授权信任边界设计评审。本文不宣称已解决 |
| API 拆分遗漏 Studio / assembly 生命周期 | S-01 同时覆盖 Channel 和 Studio；静态装配检查与运行 trace 均证明模型在 Runtime 执行 |
| Gateway POST 重试重复副作用 | 默认不重试；断连不本地 fallback；幂等扩展需独立契约 |
| Deployment selector 不可变 | 按 §3.2.2 分阶段升级 / 替代部署，测试回滚与流量切换，不能只验证全新安装 |
| 当前版本查询 / 关联导致重复行 | 双库相同 fixture 验证多版本、draft-only、跨 kind / tenant；计数与分页使用一致集合 |
| 消费者 / 记忆规模过大 | 记录响应大小和扫描规模，超预算先修订容量 / 分页契约，不静默截断 |
| 摘要丢失、权限提升或重复持久化 | S-08/09、E-05 验证 Provider 实际请求、原始记录保留和写入次数 |
| 核心 Contract 变更 | Provider usage、RegistryStore 扩展、身份传播若触及核心 Contract，先 ADR；禁止借本设计绕过项目规则 |

### 4.2 依赖与执行顺序

| 依赖 | 当前状态 | 使用要求 |
|------|----------|----------|
| RuntimeGateway / Runtime API | 已有 | 使用实际路径、Payload、SSE 事件，补 Gateway 适配测试 |
| httpx / SQLAlchemy / React / Semi | 既有技术栈 | 实际版本以依赖锁文件为准；不额外引入 UI 库 |
| SQLite / PostgreSQL | 既有 Adapter | 分页和投影跑共享 Contract Test；事务语义分别验证 |
| PersonalMemoryRetriever / PgVectorSemanticStore | 已有 | 复用装配和双库能力；初始化、超时及生命周期需接通 |
| ModelResponse usage | 不存在 | 本轮仅修估算；扩展前置 ADR，不将依赖标记为已就绪 |
| K8s 部署测试 | 已有部分覆盖 | 扩展独立 API / Runtime 与 Worker 共存场景，环境和运行结果待实施记录 |

执行依赖：FEAT-01 与 FEAT-02/05 联合验收，FEAT-07 随执行侧装配调整；FEAT-03/04 共享查询语义，Credentials 前端以投影接口为最终入口；FEAT-08 提供统一 Prompt 消费，FEAT-09 复用并补流式验收；FEAT-06 可独立实施。不设置“分页必须等待 HTTP Gateway”的虚假依赖。

### 4.3 TASK 与验收同步要求

现有 `71-commits-critical-issues.md` 仍为 v0.1 拆解，不可直接执行其中的旧 SQL、Noop、usage 读取和路由示例。后续规划需：

1. 更新 TASK-001～007 的 Source / Checklist / Acceptance Contract，使其与本文一致。
2. 为 FEAT-08/09 分配任务并覆盖 S-08/09、E-05；将新增异常场景分配到对应任务。
3. 用 code-flow 原生流程刷新 Spec Context、Start Gate 和 RED/GREEN，不手写 active marker，也不将本次文档修订当代码验收完成。
4. §2.3 与 §4.1 的核心 Contract / 认证边界前置事项在任务中显式保留；未解决时不得标记相关生产交付完成。

## Spec Compliance Matrix

以下是设计映射，状态均为“待实现验证”，不以设计文字替代测试或架构评审。SESSION 文档中的旧任务编号可能属于其他任务集，不能仅因 TASK-ID 相同就复用其验收证据。

| Rule / 约束 | 设计落点 | 验证方式 | 状态 |
|-------------|----------|----------|------|
| RULE-fluxion-runtime-001 | §3.2.1、§3.2.7～9：独立无状态执行、固定 Snapshot | S-01、S-07～09、E-05 | 待验证 |
| RULE-fluxion-resource-001 | §3.2.3/4：版本资源、双库、tenant、SecretRef | S-03/04、E-02/06、共享 Contract Test | 待验证 |
| RULE-fluxion-console-001 | §3.2.1/2：Channel / Studio 执行边界 | S-01/02、E-01；认证风险单独评审 | 待验证 |
| RULE-fluxion-dfx-001 | §2.4、§3.4、§4.1：预算、故障、容量、部署升级 | S-01～09、E-01～06、性能报告 | 待验证 |
| RULE-fluxion-console-api-001 | §3.3、§3.4：统一响应、可信上下文、日志与 Audit 边界 | API / 日志 / 授权回归；P-10 仍开放 | 待验证 |
| RULE-backend-database-001 | §3.2.3/4：参数化、真实 schema、一致读、固定批次 | 双库 SQL 计数、EXPLAIN、版本与隔离测试 | 待验证 |
| RULE-backend-quality-001 | §3.1/2/4：类型、资源释放、timeout/fail policy | lint/typecheck、错误 / 取消测试 | 待验证 |
| RULE-frontend-quality-001 | §3.2.3：动态请求、乱序处理、错误恢复 | S-03、E-02、前端类型与组件测试 | 待验证 |
| RULE-frontend-semi-001 | §3.1、§3.2.3：Semi 与 React19 adapter | 依赖与入口检查、页面交互测试 | 待验证 |
| AGENTS.md 核心 Contract / ADR 约束 | §2.3、§3.2.6、§4.1/3 | ADR 与 TASK 范围核对 | 前置事项未闭合 |

文档结构自检只验证编号、追溯与陈旧方案清理；实现状态、性能与生产安全结论均以之后的验收证据为准。
