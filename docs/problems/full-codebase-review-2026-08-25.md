# Fluxion Harness 全量代码评审报告

> 评审日期：2026-08-25
> 范围：backend（~13.4k 行 Python）+ frontend（~4.5k 行 TS）+ shared/contracts
> 方法：5 路子系统深读（registry / api+services / plugins+protocols / runtime+kernel / frontend）+ 端到端链路精读（Channel→Runtime→Resolver→Registry→ToolRuntime）。多处发现由 2–4 个独立来源交叉验证。

## 总体判断

**架构文档质量高于实现完成度。** 契约层（typed events、fail policy、Tool Result 三态、Resource 版本化、publication 事务、bind code、AES-GCM 加密本体）设计克制且符合 baseline；但执行面与契约面严重脱节——三个核心 ADR 承诺（插件加载、scope 过滤、不可信隔离执行）停在「有类型、有测试、无生产调用」的脚手架状态，同一条策略数据在 Console 可见路径与 Runtime 执行路径有两套互相矛盾的解释。当前处于「契约已立、语义未收口、安全边界未闭合」阶段，**不应在多租户环境暴露 `run_command`/`http.get`/`file.*` 工具，亦不应在未补鉴权前接入真实用户**。

下文按严重度排序。每条标注 [确证]/[疑似]、来源 file:line、失败场景、对应架构规则。

---

## P0 — 安全缺陷（多租户暴露前必须修复）

### S1. [确证] Console/Channel API 完全无认证，tenant/actor 取自可伪造 header
- `backend/src/fluxion/api/middleware.py:84-90`：非 dev 模式下 `_identity` 直接信任 `X-Tenant-ID` / `X-Actor-ID`，无 token 校验；缺省落 `"unknown"` 而非 401。
- 违反 console-api-contract §4「tenant_id/actor_id 必须来自可信认证上下文」、RULE-16。
- **失败场景**：设 `X-Tenant-ID: tenant-b` 即可列出/读取 tenant-b 的 resources、bindings、audit、traces、runs、credential 元数据，创建 binding、以任意 actor 身份审批。`X-Actor-ID` 伪造直接击穿审批自审批检查（`console_governance.py:116-117`）与 rollback 角色检查（`console_resources.py:319-322`）。
- 缓解事实：目前 Console 只随 dev bundle 发布（`dev_mode=True` 固定 identity），生产 Console 装配尚不存在——但中间件 non-dev 分支就是未来生产路径，鉴权层完全缺位。
- 交叉验证：registry 层 F10、plugins 层均独立确认。

### S2. [确证] `/api/v1/channels/web/messages` 无鉴权，可冒充任意已绑定用户
- `backend/src/fluxion/api/channel.py:112-129` + `services/channel_app.py:189-201`：`post_message` / `stream_message` 只收 body 里的 `channel_user_id` + 可选 `X-Tenant-ID`，无任何凭据；`resolve_identity` 命中即以该 `platform_user_id` 运行 Runtime，`runtime_profile_id` 由客户端逐条指定。
- `plugins/channel_adapters.py:33-34`：`WebChannelAdapter.normalize_inbound` 验签/解密为空，违反 ADR-011。
- **失败场景**：向 dev bundle POST `{channel_user_id: "user-a", runtime_profile_id: "assistant", content: "..."}` + `X-Tenant-ID: tenant-a`，即可以 user-a 身份执行 Agent、使用其 UserGrant 工具。真实用户前端只用带 Bearer token 的 `/access/messages`，此端点是纯暴露面。

### S3. [确证] 跨租户 Secret IDOR：resolve 无租户校验 + binding 不校验 credential_ref 归属
- `runtime/secrets.py:89-97`：`LocalEncryptedSecretStore.resolve(ref)` 无 tenant 参数。
- `services/console_governance.py:147-176`：`create_binding` 对 `credential_ref` 是自由字符串零校验（不检查 `secret://{tenant_id}/` 前缀）。
- 运行时 `RegistryOpenAIModelProvider._credential`（`runtime/model_providers.py:80-88`）与 `RegistryMCPRuntime._credential`（`runtime/mcp.py:351-360`）都据此成功解密并以该 tenant 身份调用外部系统。
- 违反 RULE-17（Secret 不进入 Resource Spec）、baseline §「tenant scope is mandatory for auth decisions」。
- **失败场景**：tenant A 管理员创建 MCP/Plugin binding 时把 `credential_ref` 填成 `secret://tenant-b/model@1` → 以 tenant B 的凭据调用外部系统。

### S4. [确证] 沙箱继承 Runtime 全部环境变量 + 全盘可读
- `runtime/sandbox.py:57-62`：`create_subprocess_exec` 未传 `env=`，沙箱子进程继承 `FLUXION_SECRET_MASTER_KEY`（`secrets.py:67-75` 从 env 读取）、DB 连接串等。模型一句 `run_command ["env"]` 即可把 master key 取进工具结果。
- `runtime/sandbox.py:95`：`(allow file-read*)` 无 subpath 限制，可读 Pod 挂载的 K8s ServiceAccount token、dev 模式 SQLite 文件（跨租户数据）。设计声称「真实隔离文件系统」，实际只隔离了写。
- `runtime/sandbox.py:101-102`：`network_enabled=True` 时未追加 `(allow network*)`，而 `(deny default)` 已拒绝网络——该开关在 macOS 后端永远是 no-op，请求网络的工具静默失败。

### S5. [确证] SSRF：http.get 只校验首跳，重定向 + DNS rebinding 可绕过
- `runtime/builtin_tools.py:121-122`：先 `_assert_public_host`（独立 `getaddrinfo`）再 `urlopen(url)`（第二次独立解析）——两次解析之间 DNS 可变化（DNS rebinding）。
- `builtin_tools.py:122`：`urlopen` 默认跟随 302 重定向，**重定向目标不做任何公网校验**：检查 `example.com` → 302 → `http://169.254.169.254/latest/meta-data/` 即可读云元数据/内网。
- URL 由模型生成，可被 prompt injection 诱导。防护只防「首次直接指向内网」这一最笨形态。
- 交叉验证：plugins 层 + runtime 层独立确认。

### S6. [确证] Tenant policy 的 `denied_tools` 在执行路径被完全丢弃，与 Console 可见路径语义相反
- `runtime/capabilities.py:75-78`：`tenant_policy_tools()` 只返回 `(policy.allowed, policy.configured)`，把 `denied` 集合直接扔掉；`services/runtime_tool_ops.py:140-143` 据此构造执行侧集合。
- 对比 Console 展示路径 `capabilities.py:130-140` `_allowed()` 是 **denied 优先**。
- **失败场景 A（安全洞）**：tenant 配一条 `denied_tools: ["mcp__crm__delete_user"]`、另一条 `allowed_tools` 含同一工具（`_tenant_policy_tools` 对多 policy 做 union）→ Console 隐藏该工具，但执行路径只查 allowed 集合，**tenant 显式 deny 的工具照常被执行**。
- **失败场景 B（锁死）**：deny-only policy（allowed 空）→ `configured=True, policy_allowed=∅` → 执行路径 `tenant_tools=∅` → 该 tenant 所有工具全部被拒，而 Console `visible_tools` 显示可用。同一条策略数据，两条路径给出相反结论。

---

## P0 — 正确性缺陷（生产时间尺度必然发作）

### C1. [确证] 上下文窗口管理整体失效：压缩是死代码 + CJK token 估算系统性低估（最高危产品缺陷）
- `runtime/agent.py:99,143`：`read_session_context` 读取**全量** L1 历史，`_model_messages`（agent.py:368-381）无任何截断/预算控制。
- `runtime/memory.py:132` `compact_context` 在整个 `src/` 中**无任何调用方**（grep 确认）；`read_l2`/`read_summaries` 同样从未被 runtime 读取——L2/summary 两层是纯死代码，只有写入没有消费。
- `memory.py:211-213` `_estimate_tokens = max(1, len(content.split()))`：中文没有空格，一整段中文按 1 个 token 计。flush 阈值基于该估算，中文会话**永远不会触发** flush。
- 违反 FEAT-22/23、DFX-08、性能基线。
- **失败场景**：中文用户连续对话 N 轮后，L1 无限增长，每轮把全部历史重发给 provider → provider 返回 context length exceeded → 不存在任何压缩/截断路径，**该 session 此后每一轮请求都永久失败**，用户只能换 session。对以中文为主要场景的产品（文档全中文），这是必然而非边缘情况。

### C2. [确证] 同步阻塞 builtin 工具直接跑在事件循环里，单个工具调用可冻结整个 Pod
- `runtime/tools.py:143-155`：`_execute` 对同步 executor 直接调用，不进线程池；只有 awaitable 才走 `ensure_future`。
- `builtin_tools.py:110-125` `_http_get` 用同步 `urlopen`；`builtin_tools.py:146` 的 `socket.getaddrinfo` 是**无超时**阻塞 DNS；`timeout_seconds` 取自模型参数且无上限（builtin_tools.py:112-115）。`read_text` 无大小上限、`rglob` 无结果数上限。
- 违反 RULE-18（所有外部调用必须有 timeout）、ADR-010（不可信 Plugin 阻塞 Event Loop）。
- **失败场景**：profile 的 `allowed_tools` 列入 `http.get`（Console 正常配置动作）后，模型调用指向黑洞地址并传 `timeout_seconds=3600` → 事件循环被阻塞最长 1 小时，该 Pod 上**所有并发 execution 全部停摆**；连 agent loop 的 deadline（`agent.py:195-205` 的 `wait_for`）也无法触发，因为 timer 回调在循环被阻塞时根本不会运行。架构规则「所有外部调用必须有 timeout」在形式上有、在机制上无效。
- 对比：hook 的同步 handler（`events.py:183-187`）正确用了 `to_thread`，两处不一致。
- 交叉验证：plugins 层 + runtime 层独立确认。

### C3. [确证] `list_all_resources` 跨 kind 丢资源 —— window partition 缺 `kind`
- `registry/resource_sqlalchemy.py:210-236`：`partition_by=resource_definitions.c.resource_id` 未包含 `kind` 列，`count(func.distinct(resource_id))` 同样跨 kind 去重。
- 已实证复现：tenant t1 下 `skill/shared-name` 与 `mcp/shared-name` 各发布 2 版后，`list_all_resources` 返回 `total=1`，skill 资源从结果中**静默消失**。Console 资源中心主入口 `GET /api/v1/resources`（`api/console.py:118` → `console_resources.py:145`）正是这条路径。
- 契约测试 `tests/contract/test_registry_store.py` 只测了单 kind 的 `list_resources`，`list_all_resources` **零覆盖**——bug 正好藏在测试盲区。
- 修复：`partition_by=[kind, resource_id]`，count 改为对 ranked 子查询计数。

---

## P1 — 架构契约违反

### A1. [确证] 能力交集语义与 baseline §3 公式不符，且三处实现自相矛盾
（4 个独立来源确认：我的 runtime 精读、runtime/kernel agent、plugins agent、registry agent）

baseline §3 / ADR-003 定义 `EffectiveCapability = UserGrant ∩ AgentAllowlist ∩ TenantPolicy`。实现现状：
- `services/runtime_tool_ops.py:139` `user_tools = agent_tools | granted_mcp`（并集）→ 三重交集对内置工具退化为「profile allowlist 即全部」，用户无需 UserGrant 即可用 `http.get` 等内置工具；
- `services/runtime_tool_ops.py:143` 无 tenant policy 时 `tenant_tools = user_tools` → tenant 维度默认全放行（fail-open）；
- `runtime/resolver.py:369-393` Skill 路径是**并集**（注释自认「profile 技能始终保留」「仅由 Binding 授予、profile 未列出的技能也加入」）→ 用户 Binding 能把 profile 未 allowlist 的 Skill 注入任意 profile 的 system prompt；
- `runtime/capabilities.py:130-140` `_allowed`：空 allowlist = 全放行（fail-open）；`runtime/tools.py:129`：三集合硬交集（fail-closed）→ 同一规则两种语义，Console `visible_tools` 展示与 Runtime 实际放行不一致；
- `capabilities.py:108-120` `_required_resource` **不校验 `status == PUBLISHED`**（`resolver.py:144` 校验了）→ binding pin 显式版本时，DRAFT 状态的 policy/MCP 定义可参与生产授权计算。

### A2. [确证] 违反 ADR-005 的执行期漂移：租户策略实时重解析、不进 Snapshot
- `services/runtime_tool_ops.py:128-144`：每次工具调用、每次模型工具列表构建，都新建 `EffectiveCapabilityResolver(self._store)`（:133）按 **latest-published 实时**解析 tenant policy 与用户 binding，而 Snapshot 里的 `policy_version`（`resolver.py:290-305` 解析的 guardrail_policy 版本）根本不参与执行期授权。
- **失败场景**：执行开始后租户发布新 Policy → 本次 execution 后半段的工具授权集合跟随新版本变化，而 trace 里记录的 `snapshot.policy_version` 还是旧版——可复现性/审计失真，这正是 ADR-005 要消灭的 in-execution version drift。附带性能问题：每个 tool call 触发 N 次 `list_bindings` + `get`（N+1 查询），50ms P95 框架开销预算不可达。

### A3. [确证] `ExecutionSnapshot` 并非不可变，且与 resolver 缓存共享可变 dict
- `resources/contracts.py:356`：`ExecutionSnapshot` 是普通 pydantic BaseModel，**没有 `frozen=True`**——任何持有者可以 `snapshot.model_resolution["timeout_ms"] = ...` 直接改写。
- `runtime/resolver.py:220`：`model_resolution = profile.spec_json.get("model_policy")` 是**同一个 dict 对象的引用**；`TenantResourceCache` 返回的 `ResourceDefinition`（contracts.py:289）同样未冻结，`spec_json` 是普通 dict。
- **失败场景**：任何插件/钩子原地修改 `snapshot.model_resolution`，会同时污染本 Pod 缓存中的 profile spec → 该 Pod 后续**所有** execution 解析到被篡改的 model_policy。ADR-005 的「不可变」目前只是编码约定，无机制保障。

### A4. [确证] 工具调用循环终止条件过于激进：任何模型侧失误 = 整个 execution 硬失败
- `runtime/agent.py:393-402` `_remember_tool_call`：同一 `(name, arguments)` 签名出现两次即抛 `AgentLoopDuplicateToolCallError`；`call.call_id` 为空也抛（报错文案还是误导性的 "duplicate"）。
- `plugins/model_provider.py:267`：`call_id=str(value.get("id",""))` —— 部分 OpenAI 兼容服务端不返回 tool call id → **每次 tool 调用必炸**。
- `services/runtime_tool_ops.py:69-73`：模型幻觉出一个不在 `allowed_model_tools` 里的工具名 → 直接 `raise RuntimeApplicationError`，而不是把 `tool_not_allowed` 作为 tool result 喂回模型让它自纠。
- Agent Loop 没有任何「工具错误 → 模型重试/改道」的容错语义，任何一次模型失误都终止整个执行，与 agent loop 基本设计目标相悖。

### A5. [确证] 流式路径：无 deadline、成功时零 Trace、失败时双倍模型调用
- `runtime/agent.py:124-154` `stream_final_answer` 没有 `wait_for` 包裹；`plugins/model_provider.py:118-136` `stream()` 只有 httpx per-read timeout（不是总时长）——慢滴流 SSE 可把 execution 无限期挂住。RULE-18 在流式链路完全缺失。
- `services/runtime_app.py:326-351`：流式**成功**分支从不调用 `_append_trace` → SSE 成功执行没有 TraceRecord，违反 baseline §9 与 DFX-08。
- `runtime_app.py:329-330`：流式异常被静默吞掉后回退 `run(request)` → 同一请求模型被调用两次（双倍成本/副作用）；`runtime_app.py:317-321` 有工具时先 `finish_execution` 再重开全新 execution，第一个 context 的全部 trace 事件被丢弃。
- 交叉验证：我的精读 + api agent + plugins agent + runtime agent 四路确认。前端 `apps/chat/src/App.tsx:96-107` 的 `token` 累加 + `completed` 用完整 output 覆盖，会与后端双倍调用叠加成**用户可见的重复渲染**。

### A6. [确证] publish 乐观锁 CAS 不是原子的，多副本下「同 base 只许一个发布」失效
- `registry/publish_sqlalchemy.py:185-205` 的 `_check_expected_base` 用**无锁 SELECT** 读 latest published，事务内只锁目标 version 行（`_locked_resource:167-178`），不锁 base 行。两个不同版本（v2、v3）基于同一 base v1 并发 publish 时，PostgreSQL READ COMMITTED 下两者都能读到 v1、都通过 CAS、都提交成功——之后 latest 由 `published_at`（**应用时钟**）决定，后提交者可能反而不是 latest。
- 服务层注释自认此问题并以单进程 `asyncio.Lock` 掩盖（`console_resources.py:205-210`），但 store 层修复**未实现**。baseline §2 明确生产形态是多 Pod Console + PostgreSQL。
- 测试 `test_B_C101_concurrent_publish_on_same_base_allows_only_one_success` 只测**同一 version** 双发且经服务层锁串行化，对 store 层 TOCTOU 完全无检出能力。
- 附带 S1[疑似]：`published_at` 并列时 `version DESC` 字符串排序（`"9" > "10"`）选错版本，且不同 Pod 可能取到不同 "latest"，违反 S-R05。

### A7. [确证] Outbox 只有生产端，没有任何已接线的消费端
- `registry/publish_sqlalchemy.py:310-345`：`commit_publication` 事务内写 outbox 行（设计正确）。
- 但全仓 grep 确认 `OutboxWorker` 只在测试中出现，`src/` 内无任何进程启动它；CLI `serve` 两条路径都不启动 worker。
- 后果：生产形态下 Redis 通知链路实际是死的，outbox 表 PENDING 行无限增长；当前 dev 热更新靠 `RevisionAwareResourceResolver` 0.25s 轮询而非 outbox。与 baseline §4「Outbox guarantees reliable event publication」声明不符。

### A8. [确证] 两条发布路径，治理不一致
- Console 的 `commit_publication`（`publish_sqlalchemy.py`）：审计+发布记录+outbox 全事务化，质量很高。
- Runtime 的 `publish_runtime_profile`（`runtime_app.py:179-199`）：走 `store.publish()`，不写 `audit_logs`、不写 `publish_records`、不写 outbox，`bump_revision` 还在 publish 事务之外。由 CLI `run --bootstrap` / SDK `ensure_runtime_profile` 触达。违反契约 §7「Publish 必须进 AuditLog」。

### A9. [确证] 回滚审批单非一次性，可无限重放
- `services/console_resources.py:299-322`：`_verify_rollback_approval` 校验状态/过期/内容匹配后放行，但没有任何「消费」动作；`services/approval_app.py` 的 `ApprovalRecord` 没有 consumed 语义。
- **失败场景**：审批「rollback → v1」通过后，执行 rollback v2→v1；再 publish v2；再次 rollback v2→v1 时复用同一 `approval_id` 仍然成功。高风险回滚（`rollback_safe: false` / deprecated 目标）的审批约束可被单次审批永久绕过。

### A10. [确证] Runtime API 不走统一 envelope，且缺全局异常处理
- `api/runtime.py:173-184`：`_envelope` 手写响应，`code` 是字符串 `"ok"`，违反契约「`code` 为整数；`0` 表示成功」「Handler 禁止手写字面量」。CLI（`cli/main.py:220-231`）复制了同一套 string-code envelope。
- `api/runtime.py:43-50` 只注册了 `RuntimeApplicationError` handler：请求体校验失败走 FastAPI 默认 422 裸 `{"detail": ...}`，未捕获异常走 Starlette 裸 500 文本——都不在 `{code, message, data, request_id}` 封装内。Console/Channel/Eval 三个 app 都做了统一 handler，唯独 Runtime 缺席。

### A11. [确证] ApprovalStore 只有 InMemory 实现
- `services/console_app.py:53`：默认 `InMemoryApprovalStore()`，仓库无任何 DB-backed `ApprovalStore` 实现。
- 后果：Console 重启后 PENDING 审批无法决策、已 APPROVED 的审批无法通过 `_verify_rollback_approval`（get 返回 None → 403），高风险回滚被永久卡死；多实例 Console 下节点 A 创建的审批在节点 B 上不存在。

### A12. [确证] Binding 变更是通知盲区 + revision bump 非原子
- `registry/sqlalchemy_store.py:271-280,321-332`：`put_binding` / `disable_binding` 先提交 binding 事务，再单独 `bump_revision`；两步之间崩溃则 revision 未变，轮询型 runtime 永远看不到新 binding。
- 且 binding 变更根本不写 outbox 事件——纯 Redis 订阅的 runtime 实例直到下一次 publish 才会失效 binding 缓存。Binding 属于 §3「EffectiveCapability」输入，权限生效/收回延迟。
- 对比 `commit_publication` 内联 upsert bump（`publish_sqlalchemy.py:252-283`）是原子的——同一语义两种实现。

### A13. [确证] schema 双事实源：运行路径 `create_all`，alembic 沦为摆设
- `registry/sqlalchemy_store.py:61-65` 的 `initialize()` 无条件 `metadata.create_all`，且 `console_app.py:67-68`、`runtime_app.py:158-159` 启动即调用——**对 PG 同样生效**。alembic 是并行的另一条建表路径，boot 建库从不写 `alembic_version`。ADR-004 承诺「同一套 Migration 双跑」，实际运行路径不用 migration。今后 `schema.py` 演化时 create_all 新库与 alembic 升级库必然漂移。

### A14. [确证] Microkernel/PluginLoader/Hook 全是未接线的脚手架
- `plugins/loader.py` 在 `src/` 下**零生产调用**（仅 `tests/integration/test_plugin_trust.py`）；实际装配全靠硬编码（`runtime_app.py:126-129`、`runtime_tool_ops.py:44-60` 每个 run 手工 register）。Console 的 plugin 列表来自 `_derive_plugin_summaries`（从 model registry 反推的静态摘要，不是真实加载状态）。
- `HookRegistration` 在 `src/` 下零匹配：生产中 `TypedEventBus` 永远空跑，`before_tool_call` 分发是唯一接线点且必然零 hook。ADR-007 要求覆盖 request/agent_run/llm/skill/mcp/response/retry/error 等生命周期点，实际只有 `BeforeToolCallPayload` 一个 payload 类型。
- Hook 四要素只缺 `scope`：`kernel/events.py:110-120` `ordered()` 只按 `event_type` 过滤，`dispatch()` 从不读取 `registration.scope/scope_id`，任何 hook 会对所有 tenant/agent/user 生效。priority/timeout/fail_policy 落实得对（fail_closed 双触发）。
- Kernel 纯度本身合格：`kernel/` 不依赖 plugins/registry/services，有 AST 测试强制。

### A15. [确证] CLI `fluxion serve`（非 dev）跨 event loop 初始化，首个请求即挂
- `cli/main.py:81-84`：`asyncio.run(service.initialize())` 之后 `uvicorn.run(...)` 创建新 loop。`registry/sqlalchemy_store.py:44-59` engine 在 `__init__` 创建，sqlite 默认 `AsyncAdaptedQueuePool`（无 StaticPool、无 pre_ping），`initialize()` 期间打开的 aiosqlite 连接被回池，而 aiosqlite 连接在 connect 时绑定旧 loop → 新 loop 上首次 DB 访问报 "Future attached to a different loop"。dev 模式经 lifespan 初始化无此问题，恰好只有生产入口踩坑。

### A16. [确证] 不可信插件隔离边界（ADR-010）是纸面承诺
- `plugins/loader.py:90-97` `_enforce_trust` 只拦截 `UNTRUSTED + IN_PROCESS` 组合；声明 `execution_mode=ISOLATED` 的 untrusted 插件**照常以 in-process 对象运行**，没有任何 out-of-process/MCP/RPC/sandbox 通道实现。`PluginExecutionMode.ISOLATED` 是无执行语义的元数据字段。
- `trust_level` 完全来自插件 manifest 自声明（`contracts.py:33`），无签名/来源校验——恶意插件声明 `TRUSTED` 即绕过。
- 缓解：`PluginLoader` 生产零调用，当前没有真实加载不可信代码的路径；但 ADR-010 声称的「Runtime 据 trust_level 决定执行边界」未实现。

### A17. [确证] SecretStore 是唯一实现且纯内存
- `runtime/secrets.py:64` `self._records: dict` 无持久化后端；`api/dev_bundle.py:114-117` dev 下无 `FLUXION_SECRET_MASTER_KEY` 时随机生成 key。
- **失败场景**：dev 通过 Console 录入的 credential、seed 的 `FLUXION_MODEL_API_KEY`，重启后全部消失（记录没了；即便记录在，key 也变了）。多 Pod 各持独立 secret 集。违反 ADR-001「Credential → Secret Store 外置」。
- 加密本身正确：`_put_version` 每次 `os.urandom(12)` nonce 无复用、AAD 绑定 ref、32 字节 key 强校验、metadata 不含明文。
- 脱敏缺口：tool 结果原样进 `TraceRecord.tools`（`runtime_utils.py:90-102`）再经 Console trace API 返回，无脱敏——工具取回敏感数据会进 trace 与响应。

### A18. [确证] MCP 每次 tool call 全链路重解析 + 每次调用新建会话 + 连接池并发竞态
- `runtime/mcp.py:269-285`：每次 `call_tool` 执行 list_bindings（SQL）→ store.get（SQL）→ credential resolve（SQL + AES 解密）→ **新建一个 streamable HTTP MCP session**（`terminate_on_close=True`）→ 调用 → 关闭。有状态 MCP server 的会话上下文在调用间全部丢失；stdio transport 每次调用 spawn 一个新子进程。NFR-PERF-04（连接池命中 ≤10ms）不可达。
- `runtime/mcp_pool.py:106-110` `_evict_for_capacity`：`max_clients=20`，多租户共享 Pod 下 key 数量轻易超过 20；淘汰逻辑直接 `aclose()` 最旧条目，**不检查是否有在飞请求持有该 client**。失败场景：execution A 拿到 client 正在 `call_tool`，execution B 触发容量淘汰把该 entry 关掉 → A 的在飞 HTTP 调用抛 closed client → 被误判为 server 故障进入 failover。

### A19. [确证] `shared/contracts/` 是空目录，跨语言契约靠手工双写
- 目录里只有 `.gitkeep`。实际「契约」= 后端 `services/console_payloads.py` 手拼 dict + 前端 `types/console.ts`（180 行）与 `httpChatApi.ts` 手写 interface + 手写运行时校验，无 OpenAPI/JSON Schema 单一事实源，无 schema 同步测试。`tests/contract/test_shared_contracts.py` 只验证「Console 建的资源 Runtime 能跑通」这一次交互，不校验字段级同步。baseline §12 把「共享 Contract / Schema / 版本治理」列为开源 V1 范围，目前未兑现。

### A20. [确证] 治理类操作的 Audit 失败被静默吞掉（fail-open）
- `services/console_app.py:294-305`：`_append_audit` 捕获一切异常只打日志。影响面：`binding.create/disable`、`approval.created/decided`、`platform_user.create`、`chat_access.create/revoke`。
- 对比：Publication 的审计在事务内 fail-closed。契约 §7 要求 Binding 权限变化、Bind 安全事件进 AuditLog 独立持久化——binding 权限变更这一类目前是「尽力而为」。

### A21. [确证] 中断/续跑语义不完整 + 无幂等
- `runtime/agent.py:195-211`：loop deadline 到期时 `wait_for` 取消整个任务，正在执行的 tool 被连坐取消；若该工具已产生外部副作用（HTTP 已发出、子进程已 spawn），trace 中**没有任何该工具的记录**（`tool.completed` 只在成功路径 emit），不可审计、不可重放。
- `agent.py:100`：user message 在模型调用前就写入 L0，异常路径经 `finish_execution` 兜底 flush 进 L1 → 会话留下一条永远没有回复的悬挂 user 轮。
- `runtime_contracts.py:60` + `runtime_utils.py:62`：`execution_id` 可由客户端传入且无幂等去重——超时后带同 id 重试会在 L1 中重复 flush。DFX-02 要求的幂等 execution/tool request key 未实现。

### A22. [确证] Hook 链顺序与设计 §3.2.10 不符
- `services/runtime_tool_ops.py:91-93` `_dispatch_before_tool` 在授权检查（`tools.py:129`）**之前**分发——设计要求 CheckPolicy → CheckGrant → Allowlist → BeforeToolHooks。后果：DLP/安全 hook 会看到用户本无权调用的工具参数；且 fail_closed hook 可在授权结论产生前中断。RiskClassification、Approval、Schema/Semantic Validation、AfterToolHooks、独立 audit 均未实现（仅 trace 事件）。

---

## P2 — 前端与边界缺陷

### F1. [确证] Chat 流式 token 累加 + completed 覆盖 → 重复渲染
- `apps/chat/src/App.tsx:96-107`：`token` 事件逐个 `item.content + event.content` 累加；`completed` 事件用 `event.response.output` **整体覆盖** content。若后端先发若干 token 再发 completed（后端 `runtime_app.py:329-355` 流式失败回退正是这个形态），用户先看到 N 个 token 又看到完整 output（含前面 N 个 token），渲染重复。且后端回退路径模型被调用两次，成本/副作用翻倍。

### F2. [确证] ResourceDetailPanel 存在竞态：loadVersions 未受 active 守卫
- `apps/console/src/pages/resources/ResourceDetailPanel.tsx:53-61`：`loadVersions` 内部 `setVersions(await ...)`，未用 `active` flag 守卫。快速切换资源时旧请求的 `setVersions` 会覆盖新资源的版本列表。对比同文件 `getResource`（:30-51）正确用了 `active`。

### F3. [确证] inMemoryConsoleApi.validateDraft 语义与后端分叉
- `apps/console/src/services/inMemoryConsoleApi.ts:150-159`：对所有资源类型都校验 `model` + `timeout_ms` 存在；后端只对 workflow 做专门校验。runtime_profile/skill/mcp 等类型的 spec 不含 `model`/`timeout_ms` 字段，in-memory 实现会恒返回 invalid，而真实 HTTP 后端返回 valid——同方法两个实现行为相反，e2e 与单测可能给出不同结论。

### F4. [确证] 单例 ToolRuntime 跨租户共享，MCP 描述符只注册不注销
- `services/runtime_app.py:105`：整个服务一个 `ToolRuntime`；`runtime/mcp.py:241-252` 每次 execution 的 `prepare` 都向它注册 `mcp__{mcp_id}__{tool}` 描述符，**从不清理**。
- tool_id 无租户命名空间，租户 A/B 各自定义同 id 的 MCP server 会互相覆盖描述符（`credential_ref` 指向最后注册者的 binding）。描述符注册表随（租户 × MCP × 工具）组合无界增长，共享注册表中的元数据（credential_ref、risk_level）来自任意租户的最后一次注册——语义污染暂时被调用期重解析掩盖。

### F5. [确证] SQLite 静默丢弃 `FOR UPDATE`，双库锁语义分叉
- 编译验证：同一条 `with_for_update()` 语句，PG 方言渲染 `FOR UPDATE`，SQLite 方言**完全省略**。`publish_sqlalchemy.py:177`、`channel_sqlalchemy.py:187` 的行锁仅 PG 有效。dev 环境 2 个 worker 并发 publish 同资源可能形成 SHARED/RESERVED 互等，5s busy timeout 后抛裸 `OperationalError("database is locked")` → 500。项目全程未启用 WAL（无 `PRAGMA journal_mode`）。ADR-004 要求「隔离级别以 PG 为准」，此差异无契约测试覆盖。

### F6. [确证] `put()` 可直接插入 `status=PUBLISHED` 行，绕过 publish 治理
- `registry/resource_sqlalchemy.py:26-31` 对非 draft 只要求 `published_at` 非空即可插入——不经审批、不做 CAS、不做 `_validate_definition` 发布校验。`test_tenant_registry.py:64-73` 甚至将该行为固化为契约。当前服务层调用方固定 DRAFT，但未来 YAML import 若走 `put` 即可携带 published 状态直插，是 immutability/governance 旁门。

### F7. [确证] bind code 兑换把一切 `OperationalError` 当 "used"
- `registry/channel_sqlalchemy.py:199-202`：SQLite 任何 `OperationalError`（含并发 publish 的写锁冲突、磁盘满、连接中断）都被翻译成 `BindCodeRejected("used")`。用户拿到「已使用」但 code 实际未消费。应只捕获 lock 错误。

### F8. [确证] audit / bindings 分页不稳定 + 索引不匹配查询
- `registry/sqlalchemy_store.py:167-176` audit 分页 `order_by(created_at.desc())` 无 tiebreak，同一事务批量写入的 audit `created_at` 相同，行会跨页重复/丢失；`idx_audit_target`（`schema.py:82-88`）以 `target_type` 打头，服务不了 tenant-only 排序查询，每次审计分页都是租户内全扫+排序。`list_bindings_page:309` 同缺 tiebreak。

### F9. [确证] 错误码违反命名空间与集中定义
- `api/channel.py:26-29`：`36_001/36_002/36_003` 硬编码在模块内。契约命名空间表规定 **34xxx = Identity/Bind/Channel**，36xxx 是 Workflow/Capability 引用；`errors/console.py` 里根本没有 34xxx 定义。`api/eval.py:24-27` `37xxx` 同样不在命名空间表内。违反「错误码必须集中定义，禁止 Handler 内硬编码」。

### F10. [确证] 杂项健壮性问题
- `runtime/mcp.py:10`、`mcp_pool.py:8` 直接 `import httpx2`：pyproject 只声明 `httpx`，httpx2 是 uv.lock 中传递依赖——未声明的直接依赖，上游换依赖即断。
- `model_provider.py:143` 每次 attempt 新建 `AsyncClient`（无连接复用）；`_post_with_retry` 把 429/408 归为不可重试 4xx，限流场景无退避重试、无 Retry-After。
- `plugins/channel_adapters.py:29-30` `push_outbound` 只 append 内存 list，ADR-011 的 outbound push 未实现。
- `protocols/a2a.py`：`StubA2APeer` 是唯一 peer，`A2AAdapter` 无生产接线；`A2AAuth.headers()` 返回 `{"type": "bearer", "token": ...}` 非 HTTP 头格式，语义混乱。
- `runtime/mcp_pool.py:119-173` `MCPClientPool` 明文缓存 `credential_value` 且无 TTL（当前死代码，接线时是隐患）。
- `observability/tracing.py:16-49` `configure_tracer` 全仓无调用点——OTel 永远不导出；`InMemoryTraceStore` 无容量上限，长跑进程内存持续增长；`runtime_app.py:113` `_config_events` 同样无限追加。
- `console_resources.py:57,66-71` `_publication_locks` 无淘汰，长生命周期进程内存缓慢增长。

---

## 做得对的部分（避免报告片面）

- **publication 事务**（`publish_sqlalchemy.py:commit_publication`）：状态变更、revision、audit、outbox 收进单事务、方言内 upsert 处理双库差异，是全仓最成熟的一段，且有 fail-closed 测试（`test_audit_failure.py` 证明审计失败回滚整个 publish）。
- **bind code 五要素**：单次使用（guarded UPDATE `consumed_at IS NULL`）、10 分钟过期、SHA-256 存储、tenant 绑定、5 次失败冻结、明文不入库不入审计——全部正确且事务原子。比较方式是 DB 索引等值查找，144-bit 随机值，无时序攻击面（行业标准做法）。
- **AES-256-GCM 加密本体**：nonce 无复用、AAD 绑定 ref、32 字节 key 强校验、metadata 不含明文——可直接进生产的代码。
- **非流式 model provider**：分层超时（agent deadline → provider timeout）、failover 链、4xx 不重试、5xx 指数退避、非 JSON 不重试——错误分类到位。key 只进 Authorization 头不进 URL（安全）。
- **前端 API 契约层**：`httpConsoleApi.ts` 有严谨的手写运行时校验（`requiredString/Number/Boolean/Record`），envelope 解析（`httpClient.ts:108-129`）强制 `{code:number, message, data, request_id}` 结构，无 `any`、无 `@ts-ignore`；类型定义与后端字段命名（snake_case ↔ camelCase）映射清晰。Semi Design 统一使用，未见第二套组件库。
- **租户过滤**：registry 层全部查询/写入均带 tenant 过滤（含 memory_sql、chat access 从 DB 取 tenant），E-R07 有测试；services 层无 ORM 穿透；api 层 handler 均薄封装——问题集中在鉴权层缺位，不在领域逻辑。
- **Workflow Tool Adapter**：`WorkflowAdapter.descriptor` 产出 `ToolDescriptor` 注册进同一 `ToolRuntime`，`local_durable_state_count = 0` 忠实执行 ADR-008。
- **Kernel 纯度**：`kernel/` 不依赖 plugins/registry/services，有 AST 测试（`test_kernel_boundaries.py`）强制。

---

## 修复优先级建议

1. **安全收口**（多租户暴露前）：封 web messages 端点（S2）→ 引入真实认证中间件（S1）→ SecretStore 落库 + resolve 加租户校验 + binding 校验 credential_ref（S3）→ sandbox 显式清空 env + 限制 file-read subpath + network 开关接线（S4）→ http.get 关闭重定向 + 解析后校验 IP（S5）→ denied_tools 进执行路径（S6）。
2. **产品正确性**：compact_context 接线 + token 估算改 tiktoken 或字符数（C1）→ 同步工具进 `to_thread` 且强制 timeout 上限（C2）→ 修 `list_all_resources` partition_by（C3，一行级修复 + 补契约测试）。
3. **语义收口**：能力交集收敛到单一实现点（A1）→ 执行期授权走 Snapshot 而非实时重解析（A2）→ ExecutionSnapshot 加 `frozen=True` 且深拷贝 spec_json（A3）。
4. **产品化收尾**：Runtime API 接统一 envelope（A10）→ ApprovalStore/outbox worker 落库接线（A11/A7）→ CLI serve 跨 loop 修复（A15）→ 流式路径补 deadline/trace（A5）。
5. **测试补缺**：`list_all_resources`、并发 publish 不同 version 同 base（store 层）、SQLite "database is locked"、双 worker claim_outbox——契约套件目前只验证了单线程语义，没有验证它声称的并发不变量。
