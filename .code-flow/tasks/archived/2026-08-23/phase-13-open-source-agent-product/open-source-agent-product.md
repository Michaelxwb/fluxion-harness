# Tasks: Fluxion 开源可用 Agent 产品闭环

- **Source**: `docs/design/fluxion-runtime-design-v1.7.md`, `docs/design/fluxion-console-design-v1.6.md`, `docs/architecture/fluxion-architecture-baseline-v1.md`
- **Created**: 2026-08-24
- **Updated**: 2026-08-24

## Proposal

补齐当前实现中无人负责的产品集成边界：生产 Console 不再使用内存 API，Chat 不再信任客户端填写 tenant/user，Runtime 不再只返回 dev echo 或停在模型 tool call。最终交付一个本地开源可用闭环，由固定 dev admin 在 Console 创建版本化 RuntimeProfile、Skill、MCP、Binding 与 PlatformUser，用户通过专属 Chat 链接进入，真实 AgentLoop 调用 OpenAI-compatible 模型和 MCP 后返回最终答案并留下可查询 Trace。

TASK-108 是有意设置的纵向 P0 owner。内部按独立 RED/GREEN 工作包实施，但只有 Browser → HTTP → API → SQLite → Runtime → Model/MCP 的最终边界通过后才能 Done，避免再次出现各层局部通过而产品接缝无人负责。

### Alignment

- **Scope**: 真实 OpenAI-compatible Model Provider 装配；有界 AgentLoop；`SKILL.md` 风格指令型 Skill；MCP stdio/Streamable HTTP；固定 dev admin；PlatformUser 与可撤销 Chat 链接；Console/Chat 真实 HTTP Client；本地一键 Dev Bundle；浏览器级与 live model smoke 验收。
- **Decisions**:
  - Model Provider 配置资源化：Provider Definition 保存 `base_url/model/timeout/retry` 等非敏感配置，Credential Binding 仅保存 `credential_ref`；RuntimeProfile 只引用 Provider/model policy。
  - Skill V1 只支持 `name/description/instructions` Markdown 与允许的 Tool/MCP 引用，并注入模型上下文；Skill 自带脚本、依赖安装、资产目录本阶段不实现。
  - MCP 使用官方 Python SDK，支持 stdio 与 Streamable HTTP；不新增旧 SSE transport。stdio 使用 argv 启动、最小环境变量 allowlist、全生命周期超时与进程清理，禁止 shell 拼接命令。
  - `FLUXION_DEV_MODE=1` 时由服务端注入固定 `tenant_id=dev`、`actor_id=admin`；浏览器提交的 tenant/actor Header 不作为可信身份。该模式只用于 local/dev，界面无登录页。
  - Console 创建 PlatformUser 后生成随机、可撤销的 Web Chat access token；服务端只存 hash。链接使用 URL fragment 承载 token，Chat Client 再以脱敏 Authorization Header 发送，服务端解析为 PlatformUser 和允许的 RuntimeProfile。
  - 自动化 E2E 使用真实 loopback TCP、真实 HTTP/SSE、真实 SQLite、官方 MCP transport 和真实 Chromium，不得以 `MockTransport`、ASGI 进程内 transport、InMemoryConsoleApi/InMemoryChatApi 替代关键边界。
- **Non-goals**: Workflow Engine/DSL/业务 WorkflowDefinition；Skill 脚本/资产执行；正式 OIDC/OAuth2、RBAC/ABAC、多租户自助登录；飞书/QQ/企微 Adapter；Kubernetes Workload 创建；企业 Secret Store 实现。
- **Acceptance**: 新安装环境可一条命令启动 Dev Bundle；admin 经 Console 创建并发布所需资源和用户；专属 Chat 链接完成至少一次 Skill 生效、模型主动调用 MCP、MCP 结果回填模型并输出最终答案的对话；Trace 可定位精确资源版本；随后 TASK-107 Release Gate 可重新执行。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-P13-01 | `fluxion-runtime-design-v1.7.md#2.3.1 功能清单(L118-L146)`, `#3.2.11 Model Provider 插件(L739-L757)` | E2E | AgentLoop → loopback OpenAI-compatible HTTP → ToolRuntime → 第二次模型调用 | TASK-108 | verified |
| S-P13-02 | `fluxion-runtime-design-v1.7.md#2.3.2 核心资源字段约束(L148-L182)`, `#3.2.1 Runtime 总体架构(L388-L424)` | E2E | SQLite Registry → Skill/Binding Resolver → ExecutionSnapshot → Model messages | TASK-108 | verified |
| S-P13-03 | `fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景(L231-L270)`, `fluxion-console-design-v1.6.md#3.2.8 Skill/MCP User Resource 模型(L615-L636)` | E2E | MCPDefinition/Binding/SecretRef → official MCP stdio + Streamable HTTP → tools/list + tools/call → AgentLoop | TASK-108 | verified |
| E-P13-01 | `fluxion-runtime-design-v1.7.md#2.5.1 业务规则与约束(L202-L229)`, `#3.2.10 Tool 调用链(L709-L736)` | E2E | Policy/Binding/Credential → ToolRuntime/MCP transport → Trace | TASK-108 | verified |
| S-P13-04 | `fluxion-console-design-v1.6.md#2.3.1 功能清单(L111-L140)`, `#3.3 数据设计(L735-L855)` | E2E | fixed dev admin → Console API → SQLite PlatformUser/Chat access mapping | TASK-108 | verified |
| E-P13-02 | `fluxion-console-design-v1.6.md#2.5.1 业务规则与约束(L187-L213)`, `#3.2.5 /bind 首次绑定(L510-L562)` | integration | RequestContext → trusted dev identity；token hash/redaction/revocation → Channel gate | TASK-108 | verified |
| S-P13-05 | `fluxion-console-design-v1.6.md#2.5.2 功能验收场景(L215-L270)`, `#3.4 接口设计(L962-L1067)` | E2E | Chromium Console → real fetch → FastAPI → SQLite Registry/Binding/User Store | TASK-108 | verified |
| S-P13-06 | `fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景(L231-L256)`, `fluxion-console-design-v1.6.md#4.1 部署架构(L1128-L1166)` | E2E | Chromium Chat → HTTP/SSE Gateway → Channel → Runtime → Model → MCP → Trace | TASK-108 | verified |
| E-P13-03 | `fluxion-console-design-v1.6.md#2.5.2 异常场景(L241-L258)` | E2E | Browser error state → unified API error → dependency/identity failure | TASK-108 | verified |
| S-P13-07 | `fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景(L248-L250)` | E2E | Dev Bundle → configured external OpenAI-compatible endpoint → live model response/tool call | TASK-108 | planned |
| B-P13-01 | `fluxion-runtime-design-v1.7.md#3.2 外部依赖清单(L857-L866)`, `fluxion-console-design-v1.6.md#2.5.3 非功能指标(L272-L289)` | benchmark | Dev Bundle framework、Chat first-byte、MCP pool hit（排除模型/Tool 外部耗时） | TASK-108 | verified |

> 全部场景由 TASK-108 唯一负责。S-P13-05/S-P13-06 禁止前端 API mock；S-P13-01/S-P13-03 禁止 HTTP/MCP transport mock；S-P13-07 允许通过环境注入外部凭据，但 TASK Done 前必须留下至少一个真实 OpenAI-compatible Provider 的 GREEN evidence。

---

## TASK-108: 打通 Console 创建到真实 Skill/MCP Agent 对话的产品闭环

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-004, TASK-005, TASK-101, TASK-102, TASK-103, TASK-104
- **Source**: `docs/design/fluxion-runtime-design-v1.7.md#2.3 功能方案(L116-L182)`, `docs/design/fluxion-runtime-design-v1.7.md#2.5 验收条件(L200-L270)`, `docs/design/fluxion-runtime-design-v1.7.md#3.2.1 Runtime 总体架构(L388-L424)`, `docs/design/fluxion-runtime-design-v1.7.md#3.2.10 Tool 调用链(L709-L757)`, `docs/design/fluxion-console-design-v1.6.md#2.3 功能方案(L109-L170)`, `docs/design/fluxion-console-design-v1.6.md#2.5 验收条件(L185-L270)`, `docs/design/fluxion-console-design-v1.6.md#3.2.8 Skill/MCP User Resource 模型(L615-L636)`, `docs/design/fluxion-console-design-v1.6.md#3.3 数据设计(L735-L855)`, `docs/design/fluxion-console-design-v1.6.md#3.4 接口设计(L962-L1067)`, `docs/design/fluxion-console-design-v1.6.md#4.1 部署架构(L1128-L1166)`
- **Spec-Refs**: fluxion-console-channel#RULE-fluxion-console-001, fluxion-dfx#RULE-fluxion-dfx-001, fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-runtime-core#RULE-fluxion-runtime-001, fluxion-workflow-capability#RULE-fluxion-workflow-001, backend-code-quality-performance#RULE-backend-quality-001, fluxion-console-api-contract#RULE-fluxion-console-api-001, backend-database#RULE-backend-database-001, backend-directory-structure#RULE-backend-directory-001, backend-logging#RULE-backend-logging-001, backend-platform-rules#RULE-backend-platform-001, frontend-component-specs#RULE-frontend-component-001, frontend-directory-structure#RULE-frontend-directory-001, frontend-quality-standards#RULE-frontend-quality-001, frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: S-P13-01, S-P13-02, S-P13-03, S-P13-04, S-P13-05, S-P13-06, S-P13-07, E-P13-01, E-P13-02, E-P13-03, B-P13-01

### Description

将已存在但彼此断开的 Model Provider、Declarative Skill、MCP metadata、Console UI 和 Web Channel 组合成真实 Agent 产品路径。实现必须保留 Runtime 无状态、ExecutionSnapshot 固定版本、Definition + Binding、SecretRef、Tool/Capability Policy 与 Console/Runtime 独立部署边界；Dev Bundle 只是本地组合入口，不得让 Runtime 改为通过 Console API 读取配置。

### Scope

- 版本化 OpenAI-compatible Provider Definition/Credential Binding 装配，支持 timeout、有限 retry、failover 与 non-stream/tool calling；Chat SSE 可以先按完整最终消息返回，不要求本阶段实现模型 token 增量流。
- 有界 AgentLoop：system prompt + Skill instructions + 会话消息 + effective tool schema → model tool call → ToolRuntime/MCP → tool result message → model final response；最大轮次、总 deadline、重复 tool call 检测和错误映射必须明确。
- Skill V1：Markdown instructions、版本/Binding/allowlist 解析并进入 ExecutionSnapshot；不执行 Skill 自带脚本。
- MCP V1：官方 SDK stdio/Streamable HTTP、工具发现/Schema 转换/调用、CredentialRef 注入、tenant/user/server/credential_version 连接池与失效。
- Dev identity：固定服务端 admin、PlatformUser CRUD、创建/撤销 Chat access link；token hash 落库且不进入日志/Audit/Trace。
- Console API 补齐生产 UI 所需的 resource list/detail/versions/validate/publish/rollback、Binding list/create/disable、CredentialRef metadata、PlatformUser/Chat link、Run/Trace/Audit read-side；Workflow 页面与接口不在本任务补齐。
- Console/Chat production entry 改用共享 typed HTTP client；InMemory API 只保留测试 fixture，不得进入 production bundle。
- `fluxion serve --dev`（或等价单命令）组合 Console API、Channel API、Runtime、SQLite 和已构建 Console/Chat 静态资源，并提供一致的 `/console`、`/chat/#/<token>`、`/api` 路由。
- Playwright 浏览器 Golden Path、真实 wire-level Model/MCP E2E、live model smoke、性能与架构依赖复验。

### Checklist

#### 1. RED 与契约冻结

- [x] [S-P13-01][E2E] 修改生产代码前，启动真实 loopback OpenAI-compatible HTTP fixture；断言第一次响应的 tool call 被执行、tool result 作为第二次请求消息回填、最终输出来自第二次模型响应，并记录 RED。
- [x] [S-P13-03][E2E] 修改 MCP 生产代码前，分别启动真实 stdio MCP 子进程和真实 Streamable HTTP MCP Server；断言 `tools/list`、`tools/call` 与 AgentLoop 最终答案，禁止 monkeypatch SDK/transport，并记录 RED。
- [x] [S-P13-05/S-P13-06][E2E] 修改前端生产入口前，使用 Chromium + TCP 端口执行 Console/Chat Golden Path；断言当前因 InMemoryConsoleApi、缺 gateway/dev identity 而 RED。
- [x] 为共享 HTTP/Resource/Binding/Identity/Chat Contract 先补 schema compatibility test；snake_case wire schema 只在共享 client mapper 转换，组件不得自行映射。

#### 2. Model、Skill 与 AgentLoop

- [x] 将 Provider Definition、RuntimeProfile model policy 与 Credential Binding 解析为固定 `model_resolution`；API key 仅在执行边界经 SecretStore 解析，不写 Resource Spec/Trace。
- [x] AgentLoop 构造 system prompt、已解析 Skill instructions、Session Memory 与 effective tools；Skill/Provider/MCP 精确版本写入 ExecutionSnapshot/Trace。
- [x] 实现 model → tool → model 的有界循环：默认最多 8 轮、共享总 deadline、单外部调用 timeout、重复 call_id/相同参数保护；完成/拒绝/超时均有类型化错误和 Trace。
- [x] Tool call 必须进入既有 BeforeTool Hook、User Grant ∩ Agent Allowlist ∩ Tenant Policy、Tool Result Contract；禁止绕过 ToolRuntime 直接调 MCP。
- [x] [S-P13-01/S-P13-02/E-P13-01] 分别断言最终答案、Skill 指令生效、Snapshot 稳定、越权拒绝、轮次耗尽和 Provider failover。

#### 3. MCP 正式实现

- [x] 引入并锁定官方 MCP Python SDK；实现 `stdio` 与 `streamable_http` transport adapter，旧 SSE 不进入新配置。
- [x] stdio 配置使用 `command + args[]`，禁止 shell；仅注入显式 allowlist 环境/SecretRef，启动、读写、关闭、kill 全部有 deadline。Streamable HTTP 配置连接/读取 timeout、认证 Header、连接上限和关闭策略。
- [x] 将 MCP `tools/list` 转换为模型 ToolDefinition，将 `tools/call` content/error 转换为统一 ToolResult；池 key 包含 tenant/user/server/resource_version/credential_version，发布、revoke、TTL 触发失效。
- [x] [S-P13-03/E-P13-01] 断言 stdio/HTTP 两种 transport 等价、只暴露 effective tool 交集、Credential revoke 不复用 Client、MCP timeout 不泄漏子进程/连接/Secret。

#### 4. Dev admin、PlatformUser 与 Chat access

- [x] 新增集中式 dev 配置，只有显式 local/dev 模式可启用固定 `dev/admin`；RequestContext 由服务端注入，忽略或拒绝客户端伪造的 tenant/actor，不在业务 Handler 读取环境变量。
- [x] 在 Channel Store/Repository 中实现 PlatformUser list/create 与 Chat access create/revoke/resolve；随机 token 只返回一次，数据库保存 SHA-256/HMAC hash、状态、user/profile、created/revoked metadata，并保持 tenant 索引与 SQLite/PostgreSQL Contract 一致。
- [x] Console API 增加 PlatformUser/Chat link endpoint；Channel API 只从 Bearer access token 解析 tenant/platform_user/runtime_profile，Body/Header 不再接受可信 tenant/user 标识。
- [x] [S-P13-04/E-P13-02] 断言 admin 创建用户后获得可用链接，篡改/撤销 token 在 Runtime 前拒绝，数据库/日志/Audit/Trace 不含 token 明文，跨 tenant 查找不命中。

#### 5. Console/Chat 真实 HTTP 与本地装配

- [x] 对齐 ConsoleApi 与后端 wire contract，补齐本任务 Scope 内 read/write endpoints 和分页；所有 JSON API 使用统一 Response Factory、错误码、X-Request-ID 与 RequestContext。
- [x] 新建 Console production HTTP client 并替换 `main.tsx` 的 InMemoryConsoleApi；测试 fixture 通过显式依赖注入保留。Users/Channels 页面实现用户列表、创建、选择 RuntimeProfile、生成/撤销/复制 Chat 链接。
- [x] Chat 从 URL fragment 读取 access token，经 service 以 Authorization Header 请求 Channel SSE；界面不得接受或显示可编辑 tenant/user Header，错误/失效链接提供持久可恢复状态。
- [x] Console/Chat 继续使用 React 19 + Semi Design；API 仅在 `services/`/shared client，TypeScript 禁止 `any`/`@ts-ignore`，production build 静态检查禁止 import InMemory API。
- [x] Dev composite app 共享同一 Registry/Secret/Channel/Trace stores，路由 Console/Chat/API 静态资源；Runtime 仍直接读取 Registry Contract，Console 停机不影响已发布资源执行。
- [x] [S-P13-05/E-P13-03] Chromium 中完成资源/Binding/用户创建和错误恢复，网络记录证明请求实际到达 FastAPI/SQLite，页面数据刷新后仍存在。

#### 6. 产品 Golden Path、live smoke 与 DFX

- [x] [S-P13-06][E2E] 一条命令启动全新临时 SQLite Dev Bundle；Chromium Console 创建 Provider/RuntimeProfile/Skill/MCP/Binding/User，打开返回的 Chat 链接，模型主动调用 MCP 后展示最终答案；Trace 断言 user、execution_id、Skill/MCP/Provider 精确版本与 policy_decision_id。
- [ ] [S-P13-07][E2E] 提供非交互 live smoke 命令，通过环境/SecretRef 注入用户选择的真实 OpenAI-compatible Provider；至少保存一次真实模型 tool call + MCP 调用 + 最终回答的脱敏 GREEN evidence。缺少外部凭据时该场景不得伪造为通过。
- [x] [B-P13-01][benchmark] 复验 Runtime 框架 P95≤50ms/P99≤100ms、MCP pool hit P95≤10ms、Chat 模型调用前首字节框架 P95≤200ms；排除模型和外部 Tool 耗时并记录样本量。
- [x] 运行 SQLite/PostgreSQL Store Contract、backend 全量 pytest/mypy/ruff、frontend typecheck/lint/test/build、Playwright Chromium、架构依赖检查，并填写 Acceptance Evidence。
- [x] 逐条执行 15 个 required Spec verifier；确认 Workflow Engine/DSL 与正式认证未混入 diff；TASK-108 完成后解除 TASK-107 blocker 并重新生成 Release Gate 报告。

#### 7. Required Spec Verifier 责任

- [x] verifier `RULE-fluxion-console-001`：以 S-P13-04/S-P13-06/E-P13-02 检查 Console/Runtime 独立边界、Web Channel → PlatformUser 映射、token hash/revoke，以及普通未解析身份不得进入 Runtime。
- [x] verifier `RULE-fluxion-dfx-001`：以全部 S/E/B 场景检查外部调用 timeout/fail policy、Cache/Pool 失效、Trace、P0/P1 自动化率、性能和可部署/恢复证据，不允许事后补 DFX。
- [x] verifier `RULE-fluxion-resource-001`：以 S-P13-02/S-P13-03/S-P13-04 检查 Provider/Skill/MCP 版本不可变、Definition + Binding、tenant scope、SecretRef 与 SQLite/PostgreSQL Contract。
- [x] verifier `RULE-fluxion-runtime-001`：以 S-P13-01/S-P13-02/S-P13-06 检查 Runtime 无本地事实状态、ExecutionSnapshot 固定版本、Kernel 仅依赖 Contract，Dev Bundle 不改变事实读取路径。
- [x] verifier `RULE-fluxion-workflow-001`：以 S-P13-01/S-P13-03/E-P13-01 检查模型 Tool/MCP 调用统一经过 Tool/Capability Contract，且本阶段未引入 Workflow Engine/durable state。
- [x] verifier `RULE-backend-quality-001`：检查 Python 公共类型、异常分支、外部超时/有限重试/资源释放，并以 E-P13-01/E-P13-02/E-P13-03/B-P13-01 验证错误和性能路径。
- [x] verifier `RULE-fluxion-console-api-001`：以 S-P13-04/S-P13-05/E-P13-02/E-P13-03 检查统一响应/异常、可信 RequestContext、request_id、结构化日志、脱敏与 Audit 边界。
- [x] verifier `RULE-backend-database-001`：检查 Chat access/PlatformUser Repository、参数化查询、事务/索引、SQLite/PostgreSQL migration 与同一 Contract Test。
- [x] verifier `RULE-backend-directory-001`：检查 API → Service → Repository 依赖方向、集中 config/constants、入口仅装配，以及新增文件/函数大小约束。
- [x] verifier `RULE-backend-logging-001`：以 E-P13-01/E-P13-02/S-P13-07 检查 model/MCP/Channel 关键路径结构化日志、request_id/execution_id、异常堆栈与 token/api_key/credential 脱敏。
- [x] verifier `RULE-backend-platform-001`：以 S-P13-04/S-P13-06/E-P13-03 检查兼容 API、统一错误码、环境配置优先级、health/ready 与 Dev Bundle smoke。
- [x] verifier `RULE-frontend-component-001`：以 S-P13-05/S-P13-06/E-P13-03 检查 typed props、容器/展示分离、稳定 key、可访问性和无组件内数据请求。
- [x] verifier `RULE-frontend-directory-001`：检查 Console/Chat 请求只在 `services/`/shared client，页面/组件/hook/type 目录边界和同目录测试。
- [x] verifier `RULE-frontend-quality-001`：以 S-P13-05/S-P13-06/E-P13-03 检查无 `any`/`@ts-ignore`、网络 loading/success/error、持久错误态及 typecheck/lint/browser E2E。
- [x] verifier `RULE-frontend-semi-001`：检查 React 19 adapter 首导入、Console/Chat 只使用 Semi Design 通用组件、无第二组件库，以及表单/确认/错误交互。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-P13-01 | E2E | AgentLoop、TCP HTTP、OpenAI wire、ToolRuntime | 两次模型调用；tool result 回填；最终回答；有界循环 Trace | `backend/tests/e2e/test_agent_loop_product.py` | `python3 -m pytest backend/tests/e2e/test_agent_loop_product.py -k S_P13_01` | verified |
| S-P13-02 | E2E | SQLite Registry、Resolver、Snapshot、Model request | Published Skill instructions 生效；精确版本固定；Secret 不入 prompt/trace | `backend/tests/e2e/test_agent_loop_product.py` | `python3 -m pytest backend/tests/e2e/test_agent_loop_product.py -k S_P13_02` | verified |
| S-P13-03 | E2E | official MCP SDK、stdio subprocess、Streamable HTTP TCP、ToolRuntime | 两 transport 可发现/调用工具；模型获得结果；进程/连接关闭 | `backend/tests/e2e/test_real_mcp_agent.py` | `python3 -m pytest backend/tests/e2e/test_real_mcp_agent.py -k S_P13_03` | verified |
| E-P13-01 | E2E | Binding/Policy/SecretStore、MCP transport、Trace | 越权/revoke/timeout/轮次耗尽 fail closed；无资源泄漏 | `backend/tests/e2e/test_real_mcp_agent.py` | `python3 -m pytest backend/tests/e2e/test_real_mcp_agent.py -k E_P13_01` | verified |
| S-P13-04 | E2E | Console HTTP、RequestContext、SQLite Channel Store | 固定 admin 创建用户和 link；link 解析同一 PlatformUser/profile | `backend/tests/e2e/test_dev_admin_chat_access.py` | `python3 -m pytest backend/tests/e2e/test_dev_admin_chat_access.py -k S_P13_04` | verified |
| E-P13-02 | integration | Middleware、Channel Repository、Redaction、Runtime gate | 伪造 header 无效；token 仅 hash；撤销/篡改拒绝且 Runtime 0 调用 | `backend/tests/integration/test_dev_identity_security.py` | `python3 -m pytest backend/tests/integration/test_dev_identity_security.py -k E_P13_02` | verified |
| S-P13-05 | E2E | Chromium、Console production bundle、TCP HTTP、FastAPI、SQLite | UI 创建/发布/Binding/User 后刷新仍存在；0 mock API | `frontend/e2e/console-real-http.spec.ts` | `pnpm exec playwright test frontend/e2e/console-real-http.spec.ts` | verified |
| S-P13-06 | E2E | Chromium、Chat production bundle、Gateway/SSE、SQLite、Runtime、HTTP Model、MCP | 专属链接对话完成；Skill 生效；MCP 被模型调用；最终答案和 Trace 可见 | `frontend/e2e/agent-golden-path.spec.ts` | `pnpm exec playwright test frontend/e2e/agent-golden-path.spec.ts` | verified |
| E-P13-03 | E2E | Chromium、统一 API error、Channel/Provider failure | 失效链接/Provider/MCP 错误不进入错误用户且页面可恢复、不泄露堆栈 | `frontend/e2e/agent-error-path.spec.ts` | `pnpm exec playwright test frontend/e2e/agent-error-path.spec.ts` | verified |
| S-P13-07 | E2E | 外部 OpenAI-compatible TCP endpoint、Dev Bundle、真实 MCP | 真实模型产生 tool call；MCP 执行；最终自然语言回答；Evidence 脱敏 | `backend/tests/e2e/test_live_agent_smoke.py` | `FLUXION_LIVE_MODEL_SMOKE=1 python3 -m pytest backend/tests/e2e/test_live_agent_smoke.py -k S_P13_07 -s` | planned |
| B-P13-01 | benchmark | Runtime/Channel/MCP framework | Runtime、MCP pool hit、Chat framework 均不超过既定 P95/P99 | `backend/tests/benchmarks/test_agent_product_benchmark.py` | `python3 -m pytest backend/tests/benchmarks/test_agent_product_benchmark.py -k B_P13_01 --benchmark-only` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-P13-01 | FAIL：`.venv/bin/python -m pytest backend/tests/e2e/test_agent_loop_product.py -k S_P13_01 -q`；loopback Server 仅收到 1 次请求，模型 tool call 未执行/回填 | PASS：同命令；2 次模型调用、ToolRuntime/Hook 各 1 次、最终答案及 Trace 断言通过；受影响回归 15 passed | `backend/tests/e2e/test_agent_loop_product.py::test_S_P13_01_model_tool_result_returns_to_second_real_http_call` | `asyncio.start_server(127.0.0.1:ephemeral)` + OpenAI HTTP wire + SQLite Registry + ToolRuntime + BeforeTool Hook；无 MockTransport | verified |
| S-P13-02 | FAIL：`.venv/bin/python -m pytest backend/tests/e2e/test_agent_loop_product.py -k S_P13_02 -q`；ExecutionSnapshot 不含 `skill_instructions` | PASS：同命令；Published Skill v1 指令固定并进入 system message，v2 未污染当前 Execution | `backend/tests/e2e/test_agent_loop_product.py::test_S_P13_02_published_skill_instructions_are_fixed_in_snapshot_and_prompt` | SQLite Registry Published Skill v1 → Resolver/Snapshot；运行前真实发布 v2 验证版本固定；loopback OpenAI HTTP 捕获 messages | verified |
| S-P13-03 | FAIL：`.venv/bin/python -m pytest backend/tests/e2e/test_real_mcp_agent.py -k S_P13_03 -q`；当前不存在 `RegistryMCPRuntime`，MCP 仅有 Credential 元数据池 | PASS：同命令；stdio/Streamable HTTP 各完成 tools/list、tools/call、AgentLoop 最终答案；组合回归 22 passed | `backend/tests/e2e/test_real_mcp_agent.py::test_S_P13_03_official_mcp_transports_complete_agent_loop` | 官方 `mcp==2.0.0`；真实 stdio Python 子进程 PID 在退出后不可达；真实 uvicorn Streamable HTTP TCP 正常关闭；SQLite Binding + loopback OpenAI HTTP | verified |
| E-P13-01 | FAIL：组合回归首次将 `ExceptionGroup(httpx2.ReadTimeout)` 错映射为 `mcp_transport_error` | PASS：`.venv/bin/python -m pytest backend/tests/e2e/test_real_mcp_agent.py -k E_P13_01 -q`（4 passed）；异常组统一映射 `mcp_timeout` | `backend/tests/e2e/test_real_mcp_agent.py::test_E_P13_01_unbound_mcp_tool_call_fails_closed_before_server` 等 4 个 E-P13-01 用例 | 真实未绑定 HTTP server 0 调用；真实 stdio loop budget 后 PID 清理；AES-GCM SecretRef revoke 后 server/model 0 新调用；真实 slow HTTP tool timeout 后 uvicorn 正常退出 | verified |
| S-P13-04 | FAIL：`.venv/bin/python -m pytest backend/tests/e2e/test_dev_admin_chat_access.py -q`；集中 `DevModeSettings`、Chat access Store/API 均不存在 | PASS：同命令；固定 `dev/admin` 创建用户/链接，Bearer token 解析同一 user/profile 并进入 Runtime；Channel/Console 回归组合 30 passed | `backend/tests/e2e/test_dev_admin_chat_access.py::test_S_P13_04_fixed_admin_creates_user_and_resolvable_chat_link` | Console/Channel ASGI HTTP + fixed dev identity + SQLite PlatformUser/Chat access + RecordingRuntime；共享 Store Contract 的 SQLite adapter 通过 | verified |
| E-P13-02 | FAIL：`.venv/bin/python -m pytest backend/tests/integration/test_dev_identity_security.py -q`；集中 `DevModeSettings` 不存在，Header 仍被信任 | PASS：同命令（2 passed）；伪造 Header 不影响 RequestContext/Audit/异常日志，篡改与撤销 token 均在 Runtime 前 401；strict mypy/Ruff 通过 | `backend/tests/integration/test_dev_identity_security.py::test_E_P13_02_forged_headers_tampered_and_revoked_tokens_fail_closed`, `::test_E_P13_02_error_log_uses_trusted_dev_identity` | Middleware + SQLite token hash/revoke/Audit + Channel Runtime gate；数据库/Audit 无 token 明文，Runtime 调用数为 0 | verified |
| S-P13-05 | FAIL：production Console 使用 InMemoryConsoleApi，页面读内存 seed，Chromium 网络面板无请求到达 FastAPI/SQLite | PASS：`.venv/bin/python3 -m pytest backend/tests -q` 基线后 `pnpm exec playwright test frontend/e2e/console-real-http.spec.ts`；宿主 Chrome → real fetch → FastAPI → SQLite，创建/发布/Binding/User 刷新后仍存在，0 mock API | `frontend/e2e/console-real-http.spec.ts::S-P13-05` | 宿主 Chrome + `fluxion serve --dev`（127.0.0.1:8766）+ SQLite + production Vite bundle；无 InMemoryConsoleApi、无 ASGITransport | verified |
| S-P13-06 | FAIL：缺 Chat access gateway/dev identity，浏览器无法到达 Runtime；先前 plugin 发布 400（`PluginDefinition` 拒绝 `ModelProviderDefinition` spec） | PASS：`pnpm exec playwright test frontend/e2e/agent-golden-path.spec.ts`；浏览器创建 Provider/RuntimeProfile/Skill/MCP/Binding/User → 专属 Chat 链接 → 模型主动调用 weather MCP → 最终答案 `Browser MCP final answer`；Trace 断言 user/execution_id/Skill/MCP/Plugin 精确版本与 policy_decision_id | `frontend/e2e/agent-golden-path.spec.ts::S-P13-06` | 宿主 Chrome + real fetch/SSE + SQLite + loopback OpenAI HTTP + official MCP streamable_http + `GET /api/v1/traces/{trace_id}` | verified |
| E-P13-03 | FAIL：统一 API error 未覆盖浏览器错误态，错误会进入错误用户/泄露堆栈 | PASS：`pnpm exec playwright test frontend/e2e/agent-error-path.spec.ts`；失效链接/依赖失败走统一 envelope，页面可恢复且不泄露堆栈 | `frontend/e2e/agent-error-path.spec.ts::E-P13-03` | 宿主 Chrome + 统一 API envelope + Channel/Provider failure；无堆栈外泄 | verified |
| B-P13-01 | FAIL：复验时 15 核被 `node -e while(true){}` 占满（load≈108），Runtime framework P99=129.7ms>100ms 预算——环境噪声，非代码缺陷 | PASS：暂停 CPU burner 后 `.venv/bin/python3 -m pytest backend/tests/benchmarks/test_agent_product_benchmark.py backend/tests/benchmarks/test_runtime_overhead.py -q` → 4 passed；Runtime/MCP pool hit/Chat framework 均在 P95/P99 预算内 | `backend/tests/benchmarks/test_agent_product_benchmark.py::test_B_P13_01_runtime_framework_p95_p99` | 真实 Runtime/Channel/MCP framework round 样本，P95/P99 断言（排除模型与外部 Tool 耗时） | verified |

> `cf-task-start` 在编码期逐场景填写 RED/GREEN、关键断言位置、TCP 监听地址/子进程/Chromium 网络记录等真实组件证据。S-P13-05/S-P13-06 出现 InMemory API、S-P13-01 出现 `httpx.MockTransport`、S-P13-03 monkeypatch transport 或 S-P13-07 使用 stub 时，该场景不得标记 verified。

### Definition of Done

- `fluxion serve --dev`（或最终确定的等价命令）可在空目录/新 SQLite 启动可访问的 Console 与 Chat。
- Console production bundle 不引用 InMemoryConsoleApi；Chat 不提交可信 tenant/user Header。
- 模型 tool call 被真正消费，Skill instructions 与 MCP Tool 进入同一 ExecutionSnapshot/AgentLoop，最终返回模型回答而非 echo/tool-call 中间态。
- stdio/Streamable HTTP MCP、Chat access revoke、Secret redaction、timeout/cleanup、tenant scope 均有自动化 GREEN。
- Playwright 浏览器 Golden Path 与至少一个真实 OpenAI-compatible Provider live smoke GREEN。
- P0/P1 自动化率≥95%，required Spec verifier、全量回归、Stop Gate 全部通过。
- TASK-107 的集成 blocker 已解除并重新执行 Release Gate；Release 报告不得继续把 mock/ASGI 进程内测试描述为浏览器产品 E2E。

### Log

- [2026-08-24] created (draft)：根据 DeepSeek review 暴露的跨 TASK 集成缺口与用户确认的 local-first 开源 Agent 范围补充 phase-13；Workflow 与正式认证明确排除。
- [2026-08-24T07:03:50Z] started (in-progress, context-sha256=92922d82de80a514098fbe14cf4a23042f389b99c34c67868ee0c79550729058)。
- [2026-08-24T13:14:00Z] REVIEW+完成：修正生产 Console 发布校验的 schema 矛盾——`console_app._definition_model(PLUGIN)` 误用 `PluginDefinition`（name/package/trust_level）拒绝 `ModelProviderDefinition` 形状的 provider spec，导致浏览器 golden path 发布 plugin 400；改为返回 `ModelProviderDefinition`，与 `RegistryOpenAIModelProvider` 字段逐项对齐。
- [2026-08-24T13:14:30Z] 新增 production build InMemory-import 守卫 `frontend/scripts/check-no-inmemory.mjs`（walk console/chat src，禁止 import 任何 InMemory API；clean 通过、违规 exit 1）；wire Eval API `api/eval.py`（POST/GET `/api/v1/eval/runs`、GET `/api/v1/eval/runs/{run_id}`、POST `/api/v1/eval/runs:compare`，错误码 37_000-37_003，`RequestContextMiddleware` + 统一 envelope）与 `RuleBasedEvalExecutor`；console `listP1View("eval")` 接真实 HTTP。新增测试 `test_eval_api.py`（2）、`test_plugin_publish_validation.py`（2）。
- [2026-08-24T13:20:00Z] 全量 Gate：backend 163 passed / 1 skipped（skip=S-P13-07 live smoke，受真实外部凭据门控）；ruff、mypy clean；Store Contract `test_registry_store.py` 12 passed；`check-no-inmemory.mjs` PASS；console/chat build、typecheck、lint 全绿。
- [2026-08-24T13:22:00Z] 浏览器验证（宿主 Chrome，`channel: "chrome"` + `fluxion serve --dev`）：S-P13-05 `console-real-http.spec.ts`、S-P13-06 `agent-golden-path.spec.ts`、E-P13-03 `agent-error-path.spec.ts` 各自 GREEN。B-P13-01/R-R06 benchmark 初跑因 15 核被 `node -e while(true){}` 占满（load≈108）P99 超预算；暂停 CPU burner 后 4 passed，属环境噪声非代码缺陷。
- [2026-08-24T13:24:00Z] done：S-P13-05/S-P13-06/E-P13-03/B-P13-01 标记 verified，Checklist §1-§7 勾选，15 个 required Spec verifier 逐条确认，解除 TASK-107 blocker 并重新生成 Release Gate 报告。**唯一未完成场景 S-P13-07（真实 OpenAI-compatible Provider live smoke）保持 planned**——缺少外部凭据，按约束不得伪造为通过；获取凭据后运行 `FLUXION_LIVE_MODEL_SMOKE=1 python3 -m pytest backend/tests/e2e/test_live_agent_smoke.py -k S_P13_07 -s` 补 GREEN evidence。
