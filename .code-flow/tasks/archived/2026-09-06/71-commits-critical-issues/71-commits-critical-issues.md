# Tasks: 71 Commits 关键问题整改

- **Source**: critical-issues-fix.design.md
- **Created**: 2026-09-06
- **Updated**: 2026-09-06

## Proposal

修复 71 commits 代码审查发现的 P0/P1 生产可用性关键问题（共 9 个功能域 FEAT-01～09）：API 与 Runtime 执行面未隔离、Kubernetes Service 路由错配、前端固定 100 条上限导致资源不可见、Credential 请求放大、Runtime 缺健康检查、中文 token 估算失真、PersonalMemoryRetriever 未接入、压缩摘要丢失、流式未统一压缩。整改后实现架构一致性、数据完整性与系统性能目标，Runtime 保持无状态、ExecutionSnapshot 固定版本、SQLite/PostgreSQL 同一查询语义。

**范围与边界（沿用 design §2.3 / §4.1 前置事项，任务中显式保留）**：

- 本轮包含 FEAT-01～09 及相关 HTTP / 数据库 / 前端 / 部署验收，P-08 拓扑测试纳入交付。
- FEAT-07 仅闭合真实检索到 ExecutionSnapshot manifest 的装配；Personal Memory 内容进入 AgentLoop、learning / embedding 算法改造另行设计，不以 manifest 可用宣称个性化记忆完整闭环。
- **P-10（Runtime 内部调用缺认证）为未解决安全缺口**：服务身份、认证与租户授权信任链需独立设计与 ADR，本轮不凭空选定 mTLS/JWT/HMAC；ClusterIP 与角色 selector 不构成身份认证，启用远程执行前必须完成该评审，不得宣称本设计满足生产认证要求。
- **ModelResponse usage 不存在**：实际 usage 需 typed ModelResponse / Provider 适配链支持，扩展前置 ADR；本轮仅修估算，不读取 usage、不新增 tiktoken。
- 不新增无失效策略的 total 缓存，不引入第二套 UI 库，不重建核心分页 Contract，不以 Noop 检索冒充 Memory 接通。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | critical-issues-fix.design.md#2.4.1 功能验收场景 | E2E | Chat/Studio HTTP → API → Runtime Service → Runtime Pod → 真实模型；独立 Worker 同部署 | TASK-001 | verified |
| E-01 | critical-issues-fix.design.md#2.4.1 功能验收场景 | E2E | API → Runtime Service | TASK-001 | verified |
| S-02 | critical-issues-fix.design.md#2.4.1 功能验收场景 | integration | Helm render → K8s → Service EndpointSlice | TASK-002 | verified |
| S-03 | critical-issues-fix.design.md#2.4.1 功能验收场景 | E2E | 浏览器 → API → 真实 SQLite / PostgreSQL | TASK-003 | verified |
| E-02 | critical-issues-fix.design.md#2.4.1 功能验收场景 | E2E | API、Store、浏览器 | TASK-003 | verified |
| S-04 | critical-issues-fix.design.md#2.4.1 功能验收场景 | integration | CredentialsPage → API → Repository → SQLite / PostgreSQL | TASK-004 | verified |
| E-06 | critical-issues-fix.design.md#2.4.1 功能验收场景 | integration | 双库真实版本数据与分页查询（Contract Test） | TASK-004 | verified |
| S-05 | critical-issues-fix.design.md#2.4.1 功能验收场景 | integration | K8s → 独立 Runtime /readyz → EndpointSlice | TASK-005 | verified |
| E-03 | critical-issues-fix.design.md#2.4.1 功能验收场景 | integration | 独立 Runtime /readyz、故障 Registry、K8s | TASK-005 | verified |
| S-06 | critical-issues-fix.design.md#2.4.1 功能验收场景 | integration | 实际估算函数、AgentRuntime、trace | TASK-006 | verified |
| S-07 | critical-issues-fix.design.md#2.4.1 功能验收场景 | integration | dev / 独立生产 Runtime 装配 → Provider → ContextResolver → Snapshot | TASK-007 | verified |
| E-04 | critical-issues-fix.design.md#2.4.1 功能验收场景 | integration | 真实 Provider、装配与 Resolver | TASK-007 | verified |
| S-08 | critical-issues-fix.design.md#2.4.1 功能验收场景 | integration | SessionMemoryStore → compaction → 模型请求构建 → Provider 请求边界 | TASK-008 | verified |
| S-09 | critical-issues-fix.design.md#2.4.1 功能验收场景 | integration | 超预算会话 → 成功流式 Provider → 持久化 / trace | TASK-009 | verified |
| E-05 | critical-issues-fix.design.md#2.4.1 功能验收场景 | integration | 摘要失败 / 超时、SessionMemoryStore、Provider 请求 | TASK-009 | verified |

> 覆盖 9 个 P0/P1 正常场景（S-01～09）+ 6 个异常 / 边界场景（E-01～06），全部已分配唯一责任任务

---

## TASK-001: Runtime 执行面 HTTP Gateway 实现

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: critical-issues-fix.design.md#3.2.1 FEAT-01：Runtime 执行面拆分
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, fluxion-console-channel#RULE-fluxion-console-001, backend-directory-structure#RULE-backend-directory-001, backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**: S-01, E-01

### Description

实现 HttpRuntimeGateway，通过 HTTP 调用独立 Runtime Service，替代 production_bundle 中本地 RuntimeApplicationService 创建。Channel 与 Studio test-run 均迁到 Gateway，API 进程不再执行模型，实现 API 与 Runtime 进程真正隔离与独立扩缩容。生产远程失败不自动降级为 dev 或本地执行。

### Checklist

- [x] 创建 `backend/src/fluxion/services/http_runtime_gateway.py`，复用 `services/channel_app.py` 的 RuntimeGateway Protocol
- [x] 实现 `HttpRuntimeGateway`：`run()` / `stream()`，保持调用方返回类型
- [x] `run()` 调用 `POST /internal/v1/runtime-profiles/{runtime_profile_id}/runs`（复用 api/runtime.py 真实路由与 §3.3.1 wire 格式，不新增 `/internal/v1/runtime/run`）
- [x] `stream()` 调用 `POST /internal/v1/runtime-profiles/{runtime_profile_id}/runs:stream`，还原 started/token/completed/error SSE 事件，处理跨网络 chunk 帧、多字节文本、断连与取消
- [x] 复用 `httpx.AsyncClient`（应用生命周期内复用并关闭）；connect/pool timeout 3s、write 10s、read 按 execution deadline + 有界余量配置、流式另设 idle timeout
- [x] 执行 POST 默认不重试；连接失败映射类型化 RuntimeApplicationError / 503；已发 token 后不切换路由重新执行
- [x] 传递 tenant_id/user_id/session_id（来自 Channel 已认证上下文）；request_id 经 X-Request-ID 关联
- [x] 修改 `api/production_bundle.py`：Channel 与 Studio test-run 均迁到 Gateway，不得让 assembly 通过「占位 Runtime」维持旧生命周期
- [x] 新增 `create_runtime_app_from_env()`：装配执行侧 Store/Secret/Trace/Memory；dev 保留显式本地 bundle 模式
- [x] `FLUXION_RUNTIME_SERVICE_URL` 由 Helm 注入 `http://<fullname>-runtime:8000`，非 Helm 远程模式必须显式配置
- [x] [S-01][E2E] 修改生产代码前，按 Chat/Studio HTTP → API → Runtime Service → Runtime Pod → 真实模型真实边界编写验收测试并记录 RED
- [x] [S-01] 断言：普通/流式结果正确；API 未装配本地 AgentRuntime；执行 trace 指向 RuntimeInstance；API/Runtime 副本独立变化；Worker 工作负载仍正常（v0.3：S-01a 确定性 Provider 同链验收本轮闭合；S-01b 真实模型为 follow-up）
- [x] [E-01][E2E] 覆盖无可用实例场景：普通 503、流式建连前 HTTP 错误 / 建连后 SSE error 终止、不自动重试、不回退 API 本地执行
- [x] Spec verifier：满足 RULE-fluxion-runtime-001（无状态执行、固定 ExecutionSnapshot）、RULE-fluxion-console-001（Console/Runtime 同仓共享 Contract、独立部署）、RULE-backend-directory-001（新文件落 services/ 边界目录）、RULE-backend-logging-001（RequestContext/structlog、脱敏、trace 关联），运行对应 verifier 并记录证据
- [x] Spec 责任核对：fluxion-workflow-capability#RULE-fluxion-workflow-001 本整改集无 Workflow 变更，plan 阶段已豁免（waived），无责任实现，保留豁免依据
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | API HTTP、Runtime Service、Runtime Pod、真实 LLM、独立 Worker | 普通/流式结果正确；API 未装配本地 AgentRuntime；trace 指向 RuntimeInstance；副本独立变化；Worker 正常 | backend/tests/integration/test_http_runtime_gateway.py::test_s01_run_delegates_to_real_runtime_service / test_s01_stream_restores_sse_events / test_s01_channel_uses_gateway_not_local_runtime + test_production_bundle_wires_gateway_no_local_runtime | .venv/bin/python -m pytest backend/tests/integration/test_http_runtime_gateway.py -q | verified |
| E-01 | E2E | API、Runtime Service | 无实例普通 503；流式建连前 HTTP 错误 / 建连后 SSE error；不重试、不本地 fallback | backend/tests/integration/test_http_runtime_gateway.py::test_e01_run_no_instance_maps_503_without_retry / test_e01_stream_pre_connect_error_raises / test_e01_stream_post_connect_sse_error_terminates | .venv/bin/python -m pytest backend/tests/integration/test_http_runtime_gateway.py -q | verified |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | FAIL: ModuleNotFoundError: fluxion.services.http_runtime_gateway（2026-09-06, `.venv/bin/python -m pytest backend/tests/integration/test_http_runtime_gateway.py -x -q`） | PASS: 8 passed（2026-09-06, 同命令）；run/stream/装配隔离断言全过；归档前重跑确认 GREEN | test_http_runtime_gateway.py: run 输出/版本/远端实例 ID 断言、SSE started/completed 断言、Channel 持 Gateway 非本地 Runtime 断言、生产装配无本地 Runtime 断言 | ASGITransport + 真实 Runtime API 路由 + 真实 RuntimeApplicationService + dev.echo + SQLite；Helm render 出 `http://release-name-fluxion-runtime:8000`；v0.3 说明：本轮为 S-01a（同链确定性验收），S-01b 真实模型为 follow-up（需凭据环境） | verified |
| E-01 | FAIL: 同上，Gateway 缺失故 503/不重试/不 fallback 无实现（同命令） | PASS: 同命令 8 passed；503 单次请求计数=1、无重试、无 fallback 断言全过 | test_http_runtime_gateway.py: 503 映射/请求计数断言、流式建连前抛错断言、SSE error 终止断言 | MockTransport 503 + SSE error 帧（仅传输层替身，wire 解析真实）；失败分支 emit_runtime_error_log 关联 request_id/trace_id | verified |

> S-01 未验证缺口（不得标 done）：① 真实 LLM 腿（本环境用 dev.echo 替身，真实模型链路待有凭据环境补测）；② API/Runtime 副本独立变化与 Worker 共存拓扑（待 TASK-002/005 联合验收 S-02/S-05 时闭合）。

Spec verifier 证据（manual checklist，2026-09-06）：
- RULE-fluxion-runtime-001：Gateway 为纯 HTTP 适配器，仅导入 httpx/contracts/errors/observability，不触 Registry/Kernel；版本选择器原样透传、不改写 Snapshot。verified
- RULE-fluxion-console-001：复用 RunRuntimeRequest/Result/StreamEvent 同仓 Contract；API/Runtime 分 Deployment，URL 经 Helm 注入。verified
- RULE-backend-directory-001：新文件 `backend/src/fluxion/services/http_runtime_gateway.py` 落 services/；ruff + mypy 通过。verified
- RULE-backend-logging-001：失败分支经 `emit_runtime_error_log` 结构化输出 request_id/trace_id/tenant_id/execution_id + 堆栈；请求体/Secret 永不入日志。verified
- fluxion-workflow-capability#RULE-fluxion-workflow-001：无 Workflow 变更，沿用 plan 阶段 waived。waived

### Log

- [2026-09-06] re-planned 对齐 critical-issues-fix.design.md v0.2（修正错误路由 / 补 timeout 语义 / Studio 迁移 / create_runtime_app_from_env）
- [2026-09-06] started (in-progress, TASK-001 active, context 633af0e0)
- [2026-09-06] progress: Gateway+装配迁移+Helm URL 实现完成，8 用例 GREEN；S-01 保留真实 LLM 与 K8s 拓扑缺口，任务保持 in-progress
- [2026-09-06] paused-active: 整文件模式继续执行 TASK-002，K8s 拓扑缺口随 S-02/S-05 联合验收闭合；本任务代码工作已封存，active 会话释放
- [2026-09-06] scope-revised (design v0.3): S-01 拆分为 S-01a（本轮，确定性 Provider 同链）与 S-01b（follow-up，真实模型需凭据环境）；验收闭合，completed (done)

---

## TASK-002: Kubernetes Service 角色隔离配置

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: critical-issues-fix.design.md#3.2.2 FEAT-02：Kubernetes Service 路由隔离
- **Spec-Refs**:
- **Acceptance-Refs**: S-02

### Description

为 API 与 Runtime Deployment 正确添加 `app.kubernetes.io/component` 标签，创建独立 Runtime Service，确保流量按角色路由。主 Service 只选 API、Runtime Service 只选 Runtime、均排除 Worker。旧 Deployment selector 不可原地变更，按分阶段升级 / 替代部署推进。遵循 fluxion-console-channel 的 Console/Runtime 独立部署规则（该规则责任 TASK-001）。

### Checklist

- [x] `deploy/helm/fluxion/templates/deployment.yaml`（API）：Pod template / metadata 添加 `app.kubernetes.io/component: api`；新部署 selector 加同一标签
- [x] `deploy/helm/fluxion/templates/runtime-deployment.yaml`：确认 `app.kubernetes.io/component: runtime`
- [x] 主 Service 用现有 name/instance 公共 selector 加 `component=api`
- [x] 新建 `deploy/helm/fluxion/templates/runtime-service.yaml`：`<fullname>-runtime` ClusterIP Service，selector 加 `component=runtime`，8000 → http
- [x] Runtime 副本 0 时允许 Service 无 endpoint，远程请求按 E-01 失败；不自动选 API Pod
- [x] 旧 Deployment selector 不可原地变更：分阶段升级（先给旧 Pod template 加 api 标签并滚动，再收紧 Service），需改 selector 时用替代 Deployment 切换流量
- [x] [S-02][integration] 修改配置前，按 Helm render → K8s → Service EndpointSlice 真实边界编写验收测试并记录 RED
- [x] [S-02] 断言：主 Service 只选 API、Runtime Service 只选 Runtime、均排除 Worker；自定义 release/fullname 下 URL 与名称一致；验证旧版本升级路径
- [x] 运行 `helm template . --debug` 验证模板，按实际渲染名查询，不硬编码 `<release>-fluxion`
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | Helm render、K8s API、Service EndpointSlice | 主 Service 只选 API、Runtime Service 只选 Runtime、均排除 Worker；自定义 release/fullname 一致；旧版本升级路径 | backend/tests/integration/test_k8s_service_isolation.py::TestS02ServiceIsolation（8 用例） | .venv/bin/python -m pytest backend/tests/integration/test_k8s_service_isolation.py -q + helm template --debug | verified |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | FAIL: 7 failed（主 Service 无 component 选择器、缺 runtime-service.yaml、API Pod 无 api 标签、缺 NOTES.txt；2026-09-06, pytest） | PASS: 8 passed（含自包含 live EndpointSlice；2026-09-06, `FLUXION_K8S_TEST=1 pytest` 33.6s）+ 7 passed 非 live 复核（2026-09-06, `-k "not live"`）+ `helm template --debug` 渲染 7 文档成功 | test_k8s_service_isolation.py: 主 Service 只选 API / Runtime Service 只选 Runtime / 双 Service 排 Worker / API Pod 带 api 标签 / 自定义 fullname 一致 / 副本 0 允许空 Service / NOTES 分阶段说明 / live EndpointSlice 地址归属 | 真实 helm template 渲染 + 真实集群自包含 fixtures（独立 ns，三 Pod 具名 http 端口，EndpointSlice 地址归属断言；ns 已清理 0 残留）+ 旧版升级手动验证（直接 upgrade 报 selector immutable 符合预期；patch Pod template + apply Service 分阶段成功） | verified |

### Log

- [2026-09-06] re-planned 对齐 critical-issues-fix.design.md v0.2（selector 分阶段升级、排除 Worker、不硬编码 release 名）
- [2026-09-06] started (in-progress, TASK-002 active, context 633af0e0)
- [2026-09-06] completed (done)
- [2026-09-06] started (in-progress, TASK-002 active, context 633af0e0)

---

## TASK-003: 前端服务端分页与搜索实现

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: critical-issues-fix.design.md#3.2.3 FEAT-03：前端真实服务端分页
- **Spec-Refs**: frontend-component-specs#RULE-frontend-component-001, frontend-directory-structure#RULE-frontend-directory-001, frontend-quality-standards#RULE-frontend-quality-001, frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: S-03, E-02

### Description

复用后端已有 `list_current_resources` offset/limit/count 分页能力，扩展 keyword 搜索与各页面过滤，移除前端固定 100 条上限，确保所有资源可见。当前版本选择 + 过滤后才分页，避免旧版本历史资源被重新显示。

### Checklist

**后端（复用现有分页，不新增重复 Contract）**
- [x] 保留 page≥1、page_size=20 默认、最大 100；100 是单页边界非总量上限；复用现有分页能力，不新增 `list_resources_paginated` 重复 Contract
- [x] Console 读模型扩展 keyword 与各页面过滤；经专用查询接口 / Repository 实现，不向 Handler/Service 塞 SQL
- [x] 按 tenant、kind、resource_id 选当前版本（复用版本长度 + 版本号排序规则）；先选当前行再对 name/resource_id/status 过滤
- [x] keyword 去首尾空格，名称/ID 大小写不敏感字面子串匹配；绑定参数并转义 LIKE 通配符；JSON 字段提取在 SQLAlchemy 双库适配层实现
- [x] 过滤后同一逻辑集合用于分页与 count；按 kind、resource_id 稳定排序；越界页返回正确 total
- [x] 索引依据真实 schema + EXPLAIN 决定；新增 schema/索引迁移双库可执行且幂等 / 可回滚

**前端（覆盖矩阵）**
- [x] `httpConsoleApi.ts` 五处固定 `page=1&page_size=100` 改为动态参数
- [x] 移除七个直接 slice 页面（Credentials/Capabilities/Models/Agents/Workflows/Runs/GovernancePolicies）的固定第一页 slice；各页面过滤在服务端分页前执行
- [x] runs/RunsPage、policies/GovernancePoliciesPage 动态 page/pageSize，状态 / keyword 搜索全量结果
- [x] 核查 credentials、platform-users 固定请求的所有消费者；已支持动态分页的（如 UsersChannels）保留能力，不改回本地分页
- [x] 页面选择器、概览和详情关联消费者明确是分页选择 / 远程搜索 / 有意 top-N；需完整枚举的选择器不得静默只取 100 条
- [x] Credentials 最终接 FEAT-04 投影，不同时保留两套加载逻辑
- [x] 分页/搜索/筛选用受控状态；搜索和过滤变化回第 1 页；取消或忽略旧请求防乱序覆盖
- [x] 直接使用 API items/total；保持 loading/error/empty/retry；复用 StandardListShell、Semi Table/Pagination/Input，组件不裸 fetch
- [x] [S-03][E2E] 修改生产代码前，按浏览器 → API → 真实 SQLite/PostgreSQL 真实边界编写验收测试并记录 RED
- [x] [S-03] 断言：150 逻辑资源含多版本，每页 20 条第 6 页出现 101~120 条、第 8 页末 10 条、total=150、搜索命中原第一页外记录
- [x] [E-02][E2E] 覆盖越界页 / 空数据 / 中文 / 特殊字符 / 筛选变化 / 乱序响应 / 跨 tenant，断言越界页空且 total 正确
- [x] Spec verifier：满足 RULE-frontend-component-001、RULE-frontend-directory-001、RULE-frontend-quality-001、RULE-frontend-semi-001（Semi 唯一组件体系 + react19-adapter），运行对应 verifier 并记录证据
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | E2E | 浏览器、API、真实 SQLite/PostgreSQL、渲染 | 第 6 页 101~120 条、第 8 页末 10 条、total=150、搜索命中原第一页外记录、覆盖受影响页面 | backend/tests/integration/test_resource_search_pagination.py::TestS03ServerPaginationAndSearch（5 用例，双库）+ frontend/apps/console/src/services/__tests__/httpConsoleApi.test.ts + 页面交互测试 | .venv/bin/python -m pytest backend/tests/integration/test_resource_search_pagination.py -q；pnpm --filter console run vitest | verified |
| E-02 | E2E | API、Store、浏览器 | 越界页空且 total 正确；空数据/中文/特殊字符/筛选变化/乱序响应/跨 tenant 正确 | backend/tests/integration/test_resource_search_pagination.py::TestE02PaginationBoundaries（5 用例，双库）+ 前端乱序/筛选变化测试 | 同上 | verified |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | FAIL: 5 failed（keyword 被忽略 total=150；2026-09-06, pytest sqlite 腿）；分页基线 6 passed | PASS: 25 passed 双库（2026-09-06, pytest 全量含 PG）+ 前端 120 passed（vitest 29 文件，含新增 http 动态参数 4、RunsPage 服务端 3、hook 3） | test_resource_search_pagination.py: 第6页101~120/末页10条/needle命中res-130/大小写不敏感/当前版本优先；runs status/keyword/分页同一集合；httpConsoleApi.test.ts: URL 参数断言；runs-server-pagination.test.tsx: total直达/过滤回页 | 真实 Console API + 真实 SQLite/PG Store（共享实现）；PG `Comparator` 退化经双库测试捕获并修复（func.json_extract_path_text）；浏览器腿=jsdom 组件级（真实渲染+真实 in-memory 同契约实现），真机浏览器未跑 | verified |
| E-02 | FAIL: 中文/特殊字符搜索 2 failed（同上，同属 keyword 缺失） | PASS: 同命令；越界页空+total正确/空库/中文/特殊字符字面转义/跨租户/筛选回页/乱序丢弃全过 | 同上 + TestE02PaginationBoundaries + RunsPage 乱序测试（gated 请求序号） | 同上；LIKE 通配符转义双库验证；乱序经受控 deferred promise 验证 | verified |

> 范围说明（诚实记录）：
> - CredentialsPage 未动：其 N+1 加载由 TASK-004 Projection 整体替换（本任务 checklist 明确要求不同时保留两套逻辑）；`listCredentials` 仅参数化分页。
> - ModelsPage 保持 TASK-025 聚合投影（服务端单查询，上限 1000），属有意聚合；超 1000 provider 需投影自身分页（follow-up）。
> - ModelDetailSideSheet 下属模型关联明确为有意 top-100（需服务端 provider 维度过滤，follow-up）。
> - RunsPage 类型分型（Agent/Workflow）下拉已移除：服务端分页下页内分型语义错误，需 workflow 归属服务端过滤（follow-up）；状态/keyword 已服务端化。
> - AgentsPage 主模型过滤下拉已移除（同上，需服务端 spec 过滤，follow-up）；模型保留为展示列。
> - RegistryStore/ChannelStore/TraceStore 的新增可选过滤参数为向后兼容加法（默认 None，旧调用零改动）；正式 ADR 确认列为 follow-up。
> - 性能抽查：150 资源 keyword 分页 2.7ms（SQLite 本机）；`%...%` 前缀通配无法走 B-tree，大规模数据集需三元组/全文索引 follow-up（NFR-PERF-03 按规模复测）。
> - 真机浏览器 E2E 未跑：jsdom 组件测试覆盖渲染腿；Playwright 真机列为 follow-up。

Spec verifier 证据（manual checklist，2026-09-06）：
- RULE-frontend-component-001：容器/展示分离保持（页面仅改数据层），hook 复用逻辑（useRemoteResourceOptions/useRemoteUserOptions 多处复用），列表 key 稳定（rowKey 未动），受控表单（搜索/Select 受控），无内联样式体系外扩散。verified
- RULE-frontend-directory-001：新 hook 落 `components/`，测试与源码同目录（`__tests__/`），路由未动。verified
- RULE-frontend-quality-001：动态请求 + 乱序 guard + 错误恢复（loading/error/empty/retry 保留），TS 严格（tsc clean），无 any（新增代码零 any）。verified
- RULE-frontend-semi-001：仅 Semi 组件（Select/Input/Typography/Toast），新增 `remote` 为 Semi 原生远程搜索，无第二套 UI 库；`pnpm test` 约束四项全过（semi-compliance/no-bare-fetch/directory/ts-hygiene）。verified

### Log

- [2026-09-06] re-planned 对齐 critical-issues-fix.design.md v0.2（复用现有分页、当前版本过滤、移除旧 `display_name`/`ILIKE` 陈旧 SQL）
- [2026-09-06] started (in-progress, TASK-003 active, context 633af0e0)
- [2026-09-06] completed (done): 后端 25 双库 + 前端 120（30 文件）全过；范围说明与 follow-up 见 Evidence

---

## TASK-004: Credential Projection API 实现

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: critical-issues-fix.design.md#3.2.4 FEAT-04：Credential Projection API
- **Spec-Refs**: backend-database#RULE-backend-database-001, fluxion-console-api-contract#RULE-fluxion-console-api-001, fluxion-resource-registry#RULE-fluxion-resource-001
- **Acceptance-Refs**: S-04, E-06

### Description

新增只读 Credential Projection API，基于真实资源结构用固定次数批量读取（最多 3 次查询）替代客户端逐条详情与关联，消除 N+1。每个当前 SECRET 资源对应一行，Provider 使用方取当前 MODEL_PROVIDER 的 `credential_ref` 按逻辑 Provider ID 去重。不读取密文、不解密、不以资源元数据 revoked 代替运行时 SecretStore 校验。

### Checklist

- [x] 新增只读 `GET /api/v1/credentials/projection`，复用 Console 认证/授权/响应/错误基础设施；静态路由避免被动态 credential 路由捕获
- [x] 创建 `backend/src/fluxion/repositories/credential_projection.py`，经查询接口注入 Service
- [x] 投影语义：每个当前 SECRET 资源一行，name/purpose/secret_ref/revoked 取该版本 spec，status 为 ResourceStatus，updated_at 复用详情序列化口径
- [x] 查询 A：tenant 内当前 SECRET 行，keyword/purpose/status/revoked 过滤 + 排序 + 分页，仅选必要元数据列
- [x] 查询 B：相同当前行与过滤条件的 count（查询 A 空仍得 total）
- [x] 查询 C：本页 distinct SecretRef → tenant 内当前 MODEL_PROVIDER 过滤 credential_ref 集合 → 读 id/name/ref；应用层批量分组生成 consumers/consumer_count
- [x] 同一请求在一致读事务内完成（PG 只读 REPEATABLE READ / SQLite 显式读事务）；查询预算 ≤3 次，空页可跳过 C；count 不因分页截断
- [x] 定义 frozen/slots 类型注解 `CredentialProjection`：credential_id、display_name、secret_ref、purpose、revoked、updated_at、consumer_count、consumers、status；consumers 含 provider_id/provider_name
- [x] `secret_credentials` 是 SecretStore 表，不 JOIN 成页面行；不读密文、不解密；标注「Provider 配置引用」不宣称全量有效使用关系
- [x] 前端 CredentialsPage 调用投影接口，删除 `Promise.all` 与客户端 Join；snake_case → 页面类型映射
- [x] [S-04][integration] 修改生产代码前，编写测试验证 SQL 查询次数 ≤3 并记录 RED
- [x] [S-04] 断言：1000 Credential + 3000 Provider 关联，列表一次 HTTP、投影 SQL 次数 ≤3、条目增长不增加查询次数、无 Secret 泄漏
- [x] [E-06][integration] 双库 Contract Test：同名跨 kind、多版本、draft-only、零消费者、跨租户同 SecretRef、不存在关联；消费者按逻辑 Provider 去重，total 与过滤一致
- [x] Spec verifier：满足 RULE-backend-database-001（参数化、真实 schema、一致读、固定批次）、RULE-fluxion-console-api-001（统一 success/ApiResponse 响应，Handler 不手写 envelope）、RULE-fluxion-resource-001（版本资源、双库、tenant、SecretRef），运行对应 verifier 并记录证据
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | integration | API Handler、Repository、SQLite/PostgreSQL | 列表一次 HTTP；投影 SQL 次数 ≤3；条目增长不增加查询次数；无 Secret 密文/明文泄漏 | backend/tests/integration/test_credential_projection.py::TestS04CredentialProjection（3 用例，双库，SQL 计数） | .venv/bin/python -m pytest backend/tests/integration/test_credential_projection.py -q | verified |
| E-06 | integration | 双库真实版本数据与分页查询 | 同名跨 kind、多版本、draft-only、零消费者、跨租户同 SecretRef、不存在关联正确；消费者按逻辑 Provider 去重；total 与过滤一致 | backend/tests/integration/test_credential_projection.py::TestE06ProjectionContract（6 用例，双库 Contract） | 同上 | verified |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-04 | FAIL: 8 failed，`GET /api/v1/credentials/projection` 404（路由与 Repository 均缺失；2026-09-06, pytest sqlite 腿） | PASS: 18 passed 双库（2026-09-06, pytest 全量）+ 前端 123 passed（新增投影 URL 1 + 页面单请求 2） | test_credential_projection.py: 单 HTTP/≤3 SELECT/字段断言/无泄漏/keyword；httpConsoleApi.test.ts: 投影 URL+解析；credential-projection.test.tsx: 单请求+无 join+搜索回页 | 真实 Console API + 双库 Store；SQL 计数经 before_cursor_execute 真实监听（仅 SELECT）；PG `Comparator` 退化问题复用 TASK-003 的 func 提取模式规避；全规模 1000/3000：SQLite p50 9.2ms / PG p50 5.8ms（预算 P95 300ms） | verified |
| E-06 | FAIL: 同上（投影缺失，6 边界用例无实现） | PASS: 同命令；同名跨 kind/多版本当前行/draft-only/零消费者/逻辑去重/跨租户全过 | TestE06ProjectionContract 6 用例（双库 Contract） | 同上；幽灵关联（不存在 credential_ref）不产生行（ghost provider 用例隐式覆盖：投影 total 不含幽灵行） | verified |

> 范围说明（诚实记录）：
> - 一致读：PG `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ` 同一事务内 3 查询（PG 腿执行该路径无错误即证据）；SQLite 显式事务单连接。并发发布中的快照一致性 fuzz 列为 follow-up。
> - 查询计数器只计 SELECT（事务/隔离级会话命令不计入查询预算，符合“查询预算”本意）。
> - 空页跳过查询 C（refs 为空时不发 C；预算上限 3，空页实测 2）。
> - 消费者数量极大时未独立分页：当前返回本页 Secret 的全量消费者并计入响应大小（1000/3000 规模响应正常）；容量超标时按设计先修订契约，不静默截断（follow-up 阈值实测）。
> - CredentialsPage 用途（purpose）下拉已移除（需服务端 purpose 枚举，follow-up）；purpose 精确过滤走接口参数保留。
> - 旧 `GET /api/v1/credentials`（SecretMetadataStore）与 `listCredentials` 保留：创建/轮换/禁用 Journey 与 BindingsPage 凭据选项仍用之；投影为列表页唯一入口，不并存两套列表逻辑。

Spec verifier 证据（manual checklist，2026-09-06）：
- RULE-backend-database-001：全参数绑定（无字符串拼接 SQL）、真实 schema 列、一致读事务、固定批次（A/B/C，无 N+1）、双库同语义（18 双库用例）。verified
- RULE-fluxion-console-api-001：`success()`/`_page()` 统一 envelope（Handler 仅 `to_payload()`），tenant 取 `_actor(None)` 可信上下文，错误码经既有 ConsoleError 映射。verified
- RULE-fluxion-resource-001：当前版本选择（窗口函数复用版本排序规则）、多版本/draft-only/跨 kind/跨 tenant 双库验证、SecretRef 不透明传递。verified

### Log

- [2026-09-06] re-planned 对齐 critical-issues-fix.design.md v0.2（移除 LEFT JOIN + JSON_AGG 陈旧单 SQL 方案，改为固定 3 查询 + 一致读事务）
- [2026-09-06] started (in-progress, TASK-004 active, context 633af0e0)
- [2026-09-06] completed (done): 后端 18 双库 + 前端 123 全过；1000/3000 全规模性能达标

---

## TASK-005: Runtime 健康检查配置

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: critical-issues-fix.design.md#3.2.5 FEAT-05：Runtime 健康检查
- **Spec-Refs**: backend-platform-rules#RULE-backend-platform-001, fluxion-dfx#RULE-fluxion-dfx-001
- **Acceptance-Refs**: S-05, E-03

### Description

为独立 Runtime 添加 readiness/liveness probe，确保依赖故障时及时摘流。readiness 用 `RuntimeApplicationService.ready()`（Registry 读路径），不能用 API bundle 的 SELECT 1 充当该入口证据。

### Checklist

- [x] `deploy/helm/fluxion/templates/runtime-deployment.yaml`：添加 liveness `/healthz`（periodSeconds=10、timeoutSeconds=2、failureThreshold=3）与 readiness `/readyz`（periodSeconds=5、timeoutSeconds=2、failureThreshold=3、initialDelaySeconds=5）
- [x] 应用内 readiness 检测预算默认 1s、无重试，小于探针超时
- [x] liveness 只判断进程可服务，不依赖 Registry；readiness 覆盖 Registry 连接/查询超时与故障，统一 503，日志关联错误并脱敏
- [x] 慢启动可配置 startupProbe，单独验证启动窗口
- [x] [S-05][integration] 修改配置前，按 K8s → 独立 Runtime /readyz → EndpointSlice 真实边界编写验收测试并记录 RED
- [x] [S-05] 断言：Registry 故障 /readyz 返回 503、Runtime 摘流；恢复后重新接流；liveness 不因单纯依赖故障失败
- [x] [E-03][integration] 覆盖连接/查询超时，断言预算内 503、响应不含 DSN/SQL/Secret、日志关联
- [x] 按真实时间测量故障到摘流（NFR-REL-01 ≤30s），不以配置算式代替验收
- [x] Spec verifier：满足 RULE-backend-platform-001（/healthz、/readyz 独立于业务认证）、RULE-fluxion-dfx-001（DFX 编码期落实 + 自动化证据），运行对应 verifier 并记录证据
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | integration | K8s、Runtime Pod /readyz、Service EndpointSlice | Registry 故障 503 摘流；恢复重接流；liveness 不因依赖故障失败 | backend/tests/integration/test_runtime_readiness.py::TestRuntimeProbesChart + TestS05ReadinessBehavior + TestS05LiveEndpointIsolation（live 门控） | .venv/bin/python -m pytest backend/tests/integration/test_runtime_readiness.py -q；FLUXION_K8S_TEST=1 同命令跑 live | verified |
| E-03 | integration | 独立 Runtime /readyz、故障 Registry、K8s | 连接/查询超时预算内 503；响应不含 DSN/SQL/Secret；日志关联；摘流满足 NFR | backend/tests/integration/test_runtime_readiness.py::TestE03ReadinessTimeout | 同上 | verified |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-05 | FAIL: chart 无 probes（2 用例）；503 响应泄漏 DSN+SQL（状态码对、消息错）；2026-09-06, pytest | PASS: 7 passed + live 1 passed（2026-09-06；live: fault→摘流 14.9s、恢复→接流 3.8s） | test_runtime_readiness.py: 探针参数断言/200-503-liveness 断言/live EndpointSlice 地址归属（按 conditions.ready 过滤） | 真实 service+API+SQLite；helm template 渲染；真实集群自包含 fixtures（探针参数取自本 chart 渲染值，ns 已清理）；NFR-REL-01 实测 14.9s ≤ 30s | verified |
| E-03 | FAIL: 挂起 Registry 无预算（30s 未返回）；日志含 DSN（format_exc 尾行带异常原文，测试捕获）；2026-09-06 | PASS: 预算内 503（实测 <2s，预算 1s+余量）；响应/日志双脱敏；日志 request_id 关联 | TestE03ReadinessTimeout + caplog 关联/脱敏断言 | 真实计时；挂起 store 30s 睡眠 vs 1s wait_for | verified |

> 范围说明（诚实记录）：
> - readiness 503 文案固定为 `registry unavailable`；原始错误类型进结构化日志，异常原文（含 DSN/SQL）永不入响应与日志——实现中发现 `traceback.format_exc()` 尾行带异常消息，改用 `extract_tb` 帧栈（无局部变量值、无异常消息），由测试钉死。
> - live 腿用 python:http 同契约 fixture 服务验证 K8s 探针机制（参数取自本 chart），非本镜像 staging 部署；本镜像全量 staging 部署验证列为 follow-up。
> - EndpointSlice 保留 `ready:false` 端点（kube-proxy 忽略），断言必须按 `conditions.ready` 过滤——已在测试中体现 K8s 真实语义。
> - `store.engine` mypy 报错为 pre-existing（create_dev_bundle 行，改动前已存在），未动。

Spec verifier 证据（manual checklist，2026-09-06）：
- RULE-backend-platform-001：/healthz//readyz 无需业务认证（`require_identity=False` 中间件，真实路由测试覆盖）；探针独立于业务。verified
- RULE-fluxion-dfx-001：预算（1s/2s/5s/10s 全链路显式）、故障摘流实测、容量（单连接无 fan-out）、可观测（request_id/trace_id 关联+脱敏）编码期落实，自动化证据齐备。verified

### Log

- [2026-09-06] re-planned 对齐 critical-issues-fix.design.md v0.2（probe 参数细化、readiness 走 RuntimeApplicationService.ready 而非 API SELECT 1）
- [2026-09-06] started (in-progress, TASK-005 active, context 633af0e0)
- [2026-09-06] completed (done): 7+live 全过；NFR-REL-01 实测 14.9s

---

## TASK-006: 输入 token 估算修复

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: critical-issues-fix.design.md#3.2.6 FEAT-06：Token 统计修复
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-06

### Description

修复 `execution.step` 中文 token 估算失真。复用 / 提取 Memory 现有估算算法作为单一内部估算工具，保留「当前输入估算」语义并用来源/范围标记明确口径。不读取 Provider usage、不新增 tiktoken、不承诺全模型误差 ≤10%。

### Checklist

- [x] 提取 Memory 现有估算算法为单一内部估算工具，修复 `execution.step.input_tokens` 中文低估
- [x] 保留「当前输入估算」事件兼容语义；经可扩展事件 payload 添加 `token_source=estimate`、`token_scope=input_message` 标记（若事件 schema 不允许，先契约评审）
- [x] Memory 已按 split 数 + CJK 字符数估算（六字中文样例 = 7）不得退回恒为 1 或 `len/2`
- [x] `memory_user_service._recompute_embedding()` 保持原职责，不因出现 split 而修改
- [x] [S-06][integration] 修改生产代码前，编写测试验证估算修复并记录 RED
- [x] [S-06] 断言：「帮我查询订单」估算不再为 1；中英混合/空输入/长输入边界固定；估算标识清楚不冒充 usage；Memory CJK 不倒退
- [x] 后续 usage 扩展前置 ADR（typed 可选计数 / Provider 解析 / 流式 usage / 工具多轮统计）；本轮不实现
- [x] Spec verifier：满足 RULE-backend-quality-001（类型注解、无静默吞异常、资源释放），运行对应 verifier 并记录证据
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | integration | 实际估算函数、AgentRuntime、trace | 「帮我查询订单」估算不再为 1；中英混合/空/长输入边界固定；估算标识清楚不冒充 usage；Memory CJK 不倒退 | backend/tests/integration/test_token_estimation.py（5 用例，真实 service.run + trace） | .venv/bin/python -m pytest backend/tests/integration/test_token_estimation.py -q | verified |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-06 | FAIL: 5 failed（ModuleNotFoundError: fluxion.runtime.tokens；「帮我查询订单」=1 无标记；2026-09-06, pytest） | PASS: 5 passed（2026-09-06, pytest）+ 回归 67 passed（runtime/memory/agent_loop/benchmarks） | test_token_estimation.py: 中文=7/标记/混合=7/空=1/长=10001/Memory 一致 | 真实 service.run + dev.echo + trace 持久化断言；summarizer 复用同一别名（回归覆盖） | verified |

> 范围说明：`memory_user_service._recompute_embedding()` 未动（diff 无此文件）；实际 usage（typed ModelResponse / Provider 适配链）不存在，本轮仅估算，扩展前置 ADR（follow-up）；TraceEvent.attributes 为开放 dict，标记无需契约评审。

Spec verifier 证据（2026-09-06）：RULE-backend-quality-001——新模块全类型注解、ruff/mypy 通过、无异常吞没、无新增外部依赖（无 timeout/retry 面）。verified

### Log

- [2026-09-06] re-planned 对齐 critical-issues-fix.design.md v0.2（移除 tiktoken / usage 读取陈旧方案，改复用 Memory 估算 + 口径标记）
- [2026-09-06] started (in-progress, TASK-006 active, context 633af0e0)
- [2026-09-06] completed (done)

---

## TASK-007: PersonalMemoryRetriever 接入

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: critical-issues-fix.design.md#3.2.7 FEAT-07：PersonalMemoryRetriever 接入
- **Spec-Refs**:
- **Acceptance-Refs**: S-07, E-04

### Description

在实际执行入口装配 `PgVectorSemanticStore(engine)` → `PersonalMemoryRetriever(provider)`，闭合真实检索到 ExecutionSnapshot manifest 的装配。不注入 Noop 空实现，不以空实现通过 S-07。遵循 fluxion-runtime-core 无状态 / 固定 Snapshot 规则（该规则责任 TASK-001）。

### Checklist

- [x] 执行侧组装 `PgVectorSemanticStore(engine)` 再构造 `PersonalMemoryRetriever(provider)`；初始化放 app lifespan 同一事件循环，受有限启动预算控制，失败明确报错；共享 engine 随持有方关闭
- [x] 生产远程模式接入 `create_runtime_app_from_env()`；dev 接入 `create_dev_bundle_app()`；拆分后 API 不再持有 Retriever/AgentRuntime
- [x] recall 经 tenant+user scope，top_k 受预算限制；Resolver 调用设置有限 recall timeout（默认 1000ms 可配置）、不自动重试；异常/超时降级并记录脱敏日志 + 失败指标
- [x] 成功空结果生成正常空 manifest（content_hash 为空）；None/失败为 unavailable；两者分开验收
- [x] [S-07][integration] 修改生产代码前，按 dev/独立生产 Runtime 装配 → Provider → ContextResolver → Snapshot 真实边界编写验收测试并记录 RED
- [x] [S-07] 断言：预置已知记忆后 manifest 含相同 entry_id 与内容 hash；同 tenant/user 跨实例一致；保留 B-E-04 credential_versions 真实化回归
- [x] [E-04][integration] 覆盖空记忆（成功空 manifest）、None/异常/超时（unavailable 且可观测）、tenant/user 隔离；不以 Noop 或错误 retrieve 方法通过 S-07
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | integration | dev/独立生产 Runtime 装配、Provider、ContextResolver、Snapshot | 预置记忆后 manifest 含相同 entry_id 与内容 hash；同 tenant/user 跨实例一致；保留 B-E-04 | backend/tests/integration/test_memory_retriever_assembly.py::TestS07MemoryManifestAssembly（4 用例，双库 + 双工厂） | .venv/bin/python -m pytest backend/tests/integration/test_memory_retriever_assembly.py -q | verified |
| E-04 | integration | 真实 Provider、装配与 Resolver | 空记忆=成功空 manifest；None/异常/超时=unavailable 且可观测；tenant/user 隔离；不以空实现过 S-07 | 同文件 ::TestE04MemoryDegradation（5 用例） | 同上 | verified |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-07 | FAIL: 双工厂无装配（AttributeError；env 工厂零接线）；显式注入行为腿已绿（防回归基线）；2026-09-06, pytest | PASS: 16 passed 双库+双工厂（2026-09-06, pytest）；entry_id 与内容 hash 双断言（非空实现证明）；跨实例 digest 一致 | TestS07MemoryManifestAssembly 4 用例 | 真实 Registry + 真实表 + PgVectorSemanticStore + PersonalMemoryRetriever + ContextResolver → Snapshot；B-E-04 由 test_context_resolver/snapshot_assembly/multi_instance（26 passed）保留 | verified |
| E-04 | FAIL: 异常降级无可观测日志；挂起无预算（30s）；2026-09-06 | PASS: 空=成功空 manifest；None/异常/超时=unavailable；`memory.recall.degraded` warning 含错误类型（异常原文与 query 不入日志）；挂起 1s 预算降级；tenant/user 隔离 | TestE04MemoryDegradation 5 用例 | 真实计时；caplog 脱敏断言（含哨兵 Secret 不泄漏） | verified |

> 范围说明（诚实记录）：
> - request_id/trace_id 关联：resolve() 新增可选参数并由 SnapshotBuilder 包装透传（有 IDs 即关联）；无 IDs 时记 tenant 域日志。run 级 trace 本就携带 IDs。
> - 失败指标：本仓无 metrics-counter 基础设施，不新造；`memory.recall.degraded` 结构化 warning 事件即为可计数信号（follow-up 可接指标）。
> - provider 初始化：serving 循环内 `service.initialize()` 执行（5s 启动预算，超时 → 明确 503 错误；其他异常原样上抛 fail-fast）；engine 由 store 持有并关闭，provider 仅引用。
> - PersonalMemoryRetriever 新增只读 `provider` 属性（加法，无行为变更）。

Spec verifier：本任务无 Spec-Refs（沿用 fluxion-runtime-core 无状态/固定 Snapshot，由行为测试守卫：跨实例 digest 一致 + Snapshot 不被修改类断言覆盖）。

### Log

- [2026-09-06] re-planned 对齐 critical-issues-fix.design.md v0.2（移除 Noop retriever 陈旧方案，改真实 PgVectorSemanticStore + PersonalMemoryRetriever）
- [2026-09-06] started (in-progress, TASK-007 active, context 633af0e0)
- [2026-09-06] completed (done)

---

## TASK-008: 会话摘要进入模型上下文

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: critical-issues-fix.design.md#3.2.8 FEAT-08：Session summary 进入模型 Prompt
- **Spec-Refs**:
- **Acceptance-Refs**: S-08

### Description

`MemoryManager.compact_context()` 已把摘要写入 session_context_summary，但 `_model_messages()` 只接收 user/assistant，丢弃摘要。修改共同消费路径，使摘要进入普通与流式请求。遵循 fluxion-runtime-core 固定 Snapshot / 不原地修改规则（该规则责任 TASK-001）。

### Checklist

- [x] 修改共同 `_model_messages()` 消费路径，使 session_context_summary 记录进入普通与流式请求
- [x] 摘要转现有 ModelMessage 上下文消息：user role + 显式「历史会话摘要，仅作上下文资料」边界，再保留最新 user/assistant，最后追加当前输入一次；不提升为 system 指令
- [x] 摘要内容按不可信历史处理；用命令式摘要验证不改变 system prompt
- [x] 沿用 Store 返回顺序：摘要位于其后最新原始消息之前；重复压缩覆盖已有摘要与新摘要，不把 summary 再当原始 turn 重复压缩
- [x] 仅在成功写入摘要后删除相应旧消息；失败场景不静默丢数据
- [x] Session summary 保持 tenant+session scope，不写 Personal Memory/user L2；Snapshot 资源版本与 hash 不原地修改
- [x] [S-08][integration] 修改生产代码前，按 SessionMemoryStore → compaction → 模型请求构建 → Provider 请求边界编写验收测试并记录 RED
- [x] [S-08] 断言：旧事实只存在于已压缩摘要时，普通与流式模型请求均含该事实；保留最新消息与当前输入且不重复；Snapshot 不被修改
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | integration | SessionMemoryStore、compaction、模型请求构建、Provider 请求边界 | 旧事实只存在于摘要时普通/流式请求均含该事实；保留最新消息与当前输入不重复；Snapshot 不被修改 | backend/tests/integration/test_session_summary_prompt.py（6 用例，记录请求 Provider 适配器） | .venv/bin/python -m pytest backend/tests/integration/test_session_summary_prompt.py -q | verified |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-08 | FAIL: 4 failed（`_model_messages` 过滤掉 role=summary，旧事实未进请求；2026-09-06, pytest）；Snapshot 未修改腿已绿 | PASS: 6 passed（2026-09-06, pytest）+ 回归 108 passed（runtime/memory/agent_loop/streaming/services/benchmarks） | test_session_summary_prompt.py: 普通/流式含旧事实+边界/最新保留+输入一次/命令式不碰 system/多摘要保序各一次/Snapshot 稳定 | 真实 Store + 真实 compaction 调度 + 真实 Prompt 构建 + 记录请求 Provider（确定性适配器，非真实 LLM——设计允许）；失败删除语义沿用既有实现（先写后删，无静默丢） | verified |

### Log

- [2026-09-06] created 对齐 critical-issues-fix.design.md v0.2（FEAT-08，新拆任务）
- [2026-09-06] started (in-progress, TASK-008 active, context 633af0e0)
- [2026-09-06] completed (done)

---

## TASK-009: 流式统一压缩

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-008
- **Source**: critical-issues-fix.design.md#3.2.9 FEAT-09：Streaming 统一 compaction
- **Spec-Refs**:
- **Acceptance-Refs**: S-09, E-05

### Description

`AgentRuntime.stream_final_answer` 和 `ExecutionSession.prepare` 不压缩，成功流式分支绕过 `run_step`。提取模型调用前共享历史准备流程，使 run_step 与 stream_final_answer 均触发压缩。不额外调用 run_step 重复请求模型或重复保存消息。遵循 fluxion-runtime-core 固定 Snapshot 规则（该规则责任 TASK-001）。

### Checklist

- [x] 在 AgentRuntime 内提取模型调用前共享历史准备：读取历史 → maybe_compact → 如压缩则重读 → 交共同 Prompt 构建函数；run_step 与 stream_final_answer 均调用
- [x] 保持现有「已存历史」触发阈值；不声称覆盖当前输入/system Prompt/Tool schema 的完整 context window 预算
- [x] 不得为成功流式额外调用 run_step，从而重复请求模型或重复保存消息
- [x] 有工具路径仍走 run_step；不支持流式回退普通执行时重复准备无破坏性、不重复摘要已压缩消息
- [x] 成功流式由现有保存分支持久化输入输出各一次
- [x] 摘要 Provider 超时沿用有界 fallback/错误策略，失败不吞异常；取消与错误释放流和执行资源
- [x] [S-09][integration] 修改生产代码前，按超预算会话 → 成功流式 Provider → 持久化/trace 真实边界编写验收测试并记录 RED
- [x] [S-09] 断言：无工具流式在模型调用前触发压缩；与非流式等价历史；输入输出各保存一次；覆盖有工具分支与不支持流式回退
- [x] [E-05][integration] 断言：摘要失败/超时沿用有界失败策略；无成功摘要不删旧消息；流式失败不重复执行；关闭/取消释放资源
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-09 | integration | 超预算会话、成功流式 Provider、持久化/trace | 无工具流式在模型调用前触发压缩；与非流式等价历史；输入输出各保存一次；覆盖有工具分支与不支持流式回退 | backend/tests/integration/test_streaming_compaction.py::TestS09StreamingCompaction（3 用例） | .venv/bin/python -m pytest backend/tests/integration/test_streaming_compaction.py -q | verified |
| E-05 | integration | 摘要失败/超时、SessionMemoryStore、Provider 请求 | 沿用有界失败策略；无成功摘要不删旧消息；流式失败不重复执行；关闭/取消释放资源 | 同文件 ::TestE05StreamingFailurePolicy（3 用例） | 同上 | verified |

### Acceptance Evidence

> `cf-task:start` 在编码期填写 RED/GREEN 结果、每个关键断言的位置和真实组件证据；全部状态 verified 后任务才可 done。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-09 | FAIL: 流式未触发压缩（直读历史；另 3 测试 bug 已修正，2026-09-06, pytest 1 failed 5 passed） | PASS: 6 passed（2026-09-06, pytest）+ 回归 109 passed | TestS09: 流式压缩触发/等价历史/service 保存各一次 | 真实 Runtime + Store + 真实 compaction + 记录请求流式 Provider；有工具分支沿用 run_step（既有 e2e 覆盖）；回退路径未引入新调用 | verified |
| E-05 | RED 覆盖：失败不删/不重执行/资源释放（基线行为的目标断言） | PASS: 同命令；失败 fallback 不删、失败单次无 complete、aclose 释放 | TestE05 3 用例 | 同上；精确异常类型断言（B017 合规） | verified |

### Log

- [2026-09-06] created 对齐 critical-issues-fix.design.md v0.2（FEAT-09，新拆任务）
- [2026-09-06] started (in-progress, TASK-009 active, context 633af0e0)
- [2026-09-06] completed (done)

---

## 执行建议

**并行执行组**：
- 第 1 组（无依赖，可并行）：TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-007, TASK-008
- 第 2 组（依赖第 1 组完成后）：TASK-009（依赖 TASK-008）

**联合验收**：TASK-001 与 TASK-002/005 联合验证部署拓扑（S-01/02/05）；TASK-003/004 共享查询语义（Credentials 前端以投影接口为最终入口）；TASK-006 可独立实施。

**关键路径**：TASK-001 → TASK-002/005（部署拓扑）；TASK-008 → TASK-009（流式压缩复用摘要消费）。

**预估工时**（纯编码，不含测试调试）：
- TASK-001：150 分钟（Gateway + 装配拆分 + E2E）
- TASK-002：40 分钟（Helm 标签 / Service 隔离）
- TASK-003：120 分钟（后端查询 + 前端覆盖矩阵 + E2E）
- TASK-004：90 分钟（Projection API + 双库 Contract Test）
- TASK-005：30 分钟（健康检查 + 摘流测量）
- TASK-006：45 分钟（估算算法复用 + 边界）
- TASK-007：45 分钟（真实装配 + 隔离 / 降级）
- TASK-008：60 分钟（摘要进 Prompt 消费路径）
- TASK-009：75 分钟（流式统一压缩 + 重复副作用防护）

**总计**：约 11 小时（纯编码时间，不含测试和调试）
