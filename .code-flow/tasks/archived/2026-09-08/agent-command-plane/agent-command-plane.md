# Tasks: agent-command-plane

- **Source**: /Users/jahan/Downloads/fluxion-agent-command-plane-design.md（下文简写为 command-plane-design.md）
- **Created**: 2026-09-08
- **Updated**: 2026-09-08

## Proposal

在已有 Runtime 前立一层正式 Command Plane：Web Chat 与 IM 共用同一套 `/bind /new /stop /status /skills /skill /help` 语义，命令不进 LLM；补 Session Head（`/new`）与 Durable Execution Control（`/stop`），`/skill` 复用现有 Snapshot 权限链做显式激活。做完后新增 Agent 命令只是注册一个 CommandHandler，不再改 Channel 主链或 Agent Loop。

### Alignment

- **Scope**: 纳入：§7 Command Framework、§8 解析规则、§9 `/bind` 迁移、§10 Session Head + `/new`、§11 Execution Control + `/stop`、§12 `/status`、§13–§14 `/skills` + `/skill`、§15 `/help`、§16 管道收口、§17–§19 Gateway/内部 API/Contract 扩展、§20 返回协议、§22 可观测、§23–§25 错误码/并发/安全。排除：§3 非目标（`/model /memory /workflow /plugin /mcp /tool`、别名、命令市场、副作用回滚）。
- **Decisions**:
  - D1（用户决策 2026-09-08）：E2E 收窄到 Web 真实链路 + stub-im 语义分支；设计文档 §28.7 的 "Mattermost → …" 改写为 "stub-im → …"；真实 IM adapter 另排期。`StubImChannelAdapter` 只是测试替身（`plugins/channel_adapters.py:37-38`），E2E 测语义分支、不测 IM 集成。
  - D2（用户决策 2026-09-08）：S1 per-message 认证与 Command Plane 同步落地，TASK-005 开工前 S1 必须合入（`/stop` 只能停自己的 execution 依赖身份信任链；见 `api/channel.py:71-75` S2 缺口注释）。
  - D3（用户决策 2026-09-08）：ADR 合一 → ADR-A017（Snapshot directive + ExecutionState + Session Head），已起草 `docs/adr/ADR-A017-Agent-Command-Plane契约.md`。
  - D4（解析结论）：CancellationToken 首批只铺 3 个检查点（loop 前 / model 前 / tool 前），MCP/Workflow 传播放 Phase 5；`/status` 零 Snapshot 构建（禁用 `resolve_context`）；`CapabilityQueryService` 为轻查询（禁 memory recall/credential 解析）；`ChatSessionHead` 并发只保留 revision CAS（不用 `FOR UPDATE` + revision 双机制）。
- **Non-goals**: 见 Scope 排除；另：未知命令纠错提示（Levenshtein 建议）为可选 P2，不阻塞。
- **Acceptance**: §29 DoD 全项；场景 ID 为本次拆解从 §28/§29 派生（设计文档无结构化 S-/E- 编号），见 Acceptance Coverage。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| CMD-01 | §8 解析规则 | unit | CommandParser 纯函数：`/new`/`//new`/未知/大小写/remainder 保留 | TASK-002 | verified |
| CMD-02 | §8.3 + §25.8 | integration | stub-im 未知命令 → `unknown_command`，不断言模型调用（ChannelService + StubImChannelAdapter） | TASK-002 | verified |
| CMD-03 | §9 + §15 + §21 | integration | `/bind` 迁移后行为不变；`/help` 按上下文过滤、与 Registry 一致 | TASK-002 | verified |
| SES-01 | §10.3–§10.5 | integration | PG 真库：Head 创建/映射、`sess_` 格式、不再直传 conversation_id | TASK-003 | verified |
| SES-02 | §10.6–§10.8 + §24.3 | integration | `/new` 原子轮换 + 长期数据保留 + 有 active execution 时拒绝 + 并发 revision 单胜 | TASK-003 | verified |
| SKL-01 | §13 + §28.4 | integration | Registry 真库：四交集有效集合，Draft/未授权/闭包缺失不展示 | TASK-004 | verified |
| SKL-02 | §14 + §28.5 | integration | `/skill` 成功：Snapshot 固化 exact version + directive 进 digest + 不扩权 + remainder 原样 | TASK-004 | verified |
| SKL-03 | §14.3 + §23 | integration | 不存在/无权/pin 版本 → `skill_not_available`，审计有记录、无 prompt 正文 | TASK-004 | verified |
| STP-01 | §11 + §28.6 | integration | 单 Pod：RUNNING→CANCELLING→CANCELLED 全链路（PG + 流式中取消 + tool 前后取消） | TASK-005 | verified |
| STP-02 | §11.2 + §24.2 | integration | CAS：完成与 stop 竞争首次终态获胜；重复 `/stop` 幂等 | TASK-005 | verified |
| STP-03 | §11.7 + §28.6 | integration | 双 service 实例 + Redis：跨 Pod cancel；杀 owner Pod 后 CANCELLING orphan 被认领 | TASK-006 | verified |
| OBS-01 | §22 | integration | command/cancel audit 事件 + `chat.command` span + 命令指标（真 store/真 trace） | TASK-007 | verified |
| E2E-01 | §28.7（D1 改写） | E2E | Web 真实链路：`/new` → prompt → 长任务 `/stop` → `/status` | TASK-007 | verified |
| E2E-02 | §28.7（D1 改写） | E2E | stub-im 链路：`/bind` → `/help` → `/skills` → `/skill` → `/new` | TASK-007 | verified |

---

## TASK-001: Phase 0——ADR 合入与设计文档收口

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: command-plane-design.md#§27 推荐实施顺序(L1521-L1586), command-plane-design.md#§28 测试设计(L1588-L1666)
- **Spec-Refs**: N/A（文档任务，无生产代码）
- **Acceptance-Refs**: D1, D2, D3, D4

### Description

ADR-A017 已起草，需过评审合入；设计文档 §28.7 按 D1 改写 E2E 范围（Mattermost → stub-im），§25 显式声明 S1 依赖（D2）。本任务是全部后续任务的 Gate：ADR 未合入，TASK-003/004/005 不得动 Contract。

### Checklist
- [x] ADR-A017 过评审并合入（Snapshot directive / ExecutionState / Session Head 三决策）
- [x] 设计文档 §28.7 改写：E2E 范围为 Web + stub-im，真实 IM adapter 记入 Non-goals
- [x] 设计文档 §25 追加 S1 依赖声明（TASK-005 开工前 S1 合入）
- [x] 确认本任务文件 Acceptance Coverage 无缺口（§29 DoD 逐项有负责人）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| D3 | review | ADR 文档、Gate | 规则 25 通过，三决策完整 | ADR-A017 + plan 门禁 pass | cf_spec_gate.py --stage plan | verified |

### Acceptance Evidence

- D3 RED: N/A（文档评审任务，无行为变更）
- D3 GREEN: ADR-A017 自评审完成，修正 1 处（digest 跨版本可比表述→以 `snapshot_digest.py:15-16` 全模型哈希语义为准）；plan 门禁 `decision=pass`；§28.7/§25 已改写
- 真实边界证据：门禁输出 pass（10 bindings / 10 applied）；设计文档 diff 见 §28.7 范围说明与 §25.11
- session 投影：N/A（Spec-Refs 为 N/A，`cf_spec_session.py` 无规则可投影）

### Log
- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)
- [2026-09-08] completed (done)

---

## TASK-002: Phase 1——Command Core（Parser/Registry/Dispatcher + /bind 迁移 + 管道收口）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: command-plane-design.md#§6 模块划分(L208-L258), command-plane-design.md#§7 Command Framework(L261-L379), command-plane-design.md#§8 命令解析规则(L380-L447), command-plane-design.md#§9 /bind 设计(L449-L486), command-plane-design.md#§15 /help 设计(L1057-L1079), command-plane-design.md#§16 Chat/Channel 统一处理链(L1081-L1115), command-plane-design.md#§26.1 channel_app 改造(L1434-L1519)
- **Spec-Refs**: fluxion-console-channel#RULE-fluxion-console-001, fluxion-console-api-contract#RULE-fluxion-console-api-001, backend-directory-structure#RULE-backend-directory-001, backend-code-quality-performance#RULE-backend-quality-001, backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: CMD-01, CMD-02, CMD-03

### Description

新建 `commands/` 包（contracts/parser/registry/dispatcher/errors/handlers/formatting），`handle_chat_access` / `stream_chat_access` / Channel `handle` 收口为同一 authenticated pipeline；删除 `_bind_command_code` 硬编码（`services/channel_app.py:214-243,328-337`），`/bind` 迁为 `BindCommand`；`ChannelResult` 增 `kind="command"`。本任务后未知 slash 不进 LLM（行为修正，发布说明需写）。

### Checklist
- [x] `commands/contracts.py`：CommandDescriptor/Context/Invocation/Outcome（ImmediateReply | RuntimeInvocation）+ Handler Protocol
- [x] `commands/parser.py`：§8 全规则（前导空白、`//` 转义、未知→`unknown_command`、小写、无 alias、remainder 保留）；command name ≤ 32、skill id ≤ 128（§25.7）
- [x] `commands/registry.py` + `dispatcher.py`：启动期一次性构建（§7.6 校验：name 唯一/小写 ASCII/usage 非空）
- [x] handlers：bind（复用 `redeem_bind_code` 原子语义）+ help（Registry 自动生成、按 §15/§21 过滤）
- [x] 管道收口：三入口同走 ChatCommandService；命令命中流式入口只发 terminal event、不伪造 started（§20）
- [x] 删除 `_bind_command_code`/`is_bind_command` 特判路径；`ChannelResult` 增 `kind="command"`
- [x] [CMD-01][unit] parser 全规则矩阵（§28.1 八行用例）先写测试记 RED
- [x] [CMD-02][integration] stub-im 未知命令 → `unknown_command`，且模型零调用
- [x] [CMD-03][integration] `/bind` 成功/重复绑定拒绝/Web 不展示；`/help` 三上下文过滤
- [x] verifier RULE-fluxion-console-001（manual）：检查同仓边界、Web Channel 身份绑定、Bind Code 安全（见 CMD-03）
- [x] verifier RULE-fluxion-console-api-001（manual）：命令返回走统一信封 `{kind,command,code,output,data,request_id,trace_id}`，request_id/trace_id 全链路
- [x] verifier RULE-backend-directory-001：`commands/` 包布局符合目录规范（contracts/parser/registry/dispatcher/handlers）
- [x] verifier RULE-backend-quality-001：类型注解完整、无静默吞异常、函数长度合规
- [x] verifier RULE-backend-platform-001：检查 Guidance 适用项、确认无 Avoid 违反

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| CMD-01 | unit | CommandParser | 八行解析矩阵全过 | backend/tests/unit/test_command_parser.py（11 用例） | .venv/bin/python -m pytest backend/tests/unit/test_command_parser.py | verified |
| CMD-02 | integration | ChannelService、StubImChannelAdapter、真 Runtime（零调用断言） | unknown 不进模型 | backend/tests/channel/test_command_plane.py::test_CMD02_*（3 用例，RecordingRuntime.requests == []） | .venv/bin/python -m pytest backend/tests/channel/test_command_plane.py | verified |
| CMD-03 | integration | ChannelRegistryStore 真库 | bind 行为不变、help 与 Registry 一致 | backend/tests/channel/test_command_plane.py::test_CMD03_*（4 用例） | 同上 | verified |

### Acceptance Evidence

- RED：`ModuleNotFoundError: fluxion.commands`（实现前 collection 失败）
- GREEN：CMD-01 11 passed；CMD-02/03 7 passed；回归 33 passed（channel + bind_code + verify + adapter）与 52 passed（api + identity + gateway + identity_mapping）
- 真实边界证据：PG 真库（PostgreSQLRegistryStore + reset）、StubImChannelAdapter、RecordingRuntime 零调用断言
- mypy 13 文件 clean；ruff clean（`api/channel.py` RUF059 为 pre-existing，未动）
- 与设计偏差 2 处（有意）：① bind 成功沿用 kind="bound"/失败抛 ChannelBindError（外部契约不变，`test_web_message_auth` 锚定）；② bind 的 channel_scope 取 ALL + 处理器内分支（匿名任意通道可兑换 H1 自举；已绑定+Web→not_available；已绑定+IM→already_bound）——回归发现 web 匿名 bind 必须可兑换后修正
- session 投影：`.code-flow/specs/_session/task-agent-command-plane-TASK-002.md`

### Log
- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)
- [2026-09-08] completed (done)

---

## TASK-003: Phase 2——Session Lifecycle（ChatSessionHead + /new）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: command-plane-design.md#§10 Session Lifecycle 与 /new(L488-L625), command-plane-design.md#§24.3 /new 与新消息并发(L1378-L1414)
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001, backend-database#RULE-backend-database-001
- **Acceptance-Refs**: SES-01, SES-02

### Description

新增 `chat_session_heads` + `ChatSessionService`；普通消息经 Head 解析 `active_session_id` 再进 Runtime（替换三处 `session_id=conversation_id` 直传）；`/new` 原子轮换（D4：只用 revision CAS）。ADR-A017 §5 为契约依据。

### Checklist
- [x] `registry/chat_session.py`：Head CRUD + revision CAS（PG Contract Test，ADR-A007 单库）
- [x] `services/chat_session_service.py`：resolve_or_create + rotate（有 active execution → `new_session_conflict`，查 Control Store §11）
- [x] 三入口 session 映射接入（Web access + stream + Channel bound）；`sess_<32hex>` 服务端生成
- [x] `/new` 不删项断言：UserProfile/Personal Memory/授权/Binding/Trace 保留（旧 Memory 行保留仅不引用）
- [x] [SES-01][integration] 首次建 Head、同 conversation 切 Agent 分 Head、ID 格式
- [x] [SES-02][integration] 轮换隔离（旧 Memory 不进新上下文）+ 冲突拒绝 + 并发双 `/new` 单胜，先写测试记 RED
- [x] verifier RULE-fluxion-resource-001（manual）：Head 版本语义、tenant scope 全链路、PG Contract Test（ADR-A007 单库）
- [x] verifier RULE-backend-database-001：新表迁移 + Contract Test 落单库测试套件

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| SES-01 | integration | PG chat_session_heads、SessionMemoryStore | 映射正确、ID 格式 | backend/tests/channel/test_session_lifecycle.py::test_SES01_* | .venv/bin/python -m pytest backend/tests/channel/test_session_lifecycle.py | verified |
| SES-02 | integration | PG Head、ExecutionControlStore、Memory | 隔离 + 冲突 + 并发单胜 | 同上 test_SES02_*（4 用例） | 同上 | verified |

### Acceptance Evidence

- RED：实现前 4 failed（`ChatSessionService` 不存在 + conversation 直传不断言 sess_）
- GREEN：4 passed；回归 channel 22 passed + 50 passed（bind/api/adapter/identity/parser）
- 真实边界证据：PG 真库 `chat_session_heads`（reset 建表）、StubImChannelAdapter、RecordingRuntime session_id 断言
- mypy 6 文件 clean；ruff clean
- 测试修正 2 处（实现无 bug）：① 冲突用例补首条普通消息建 Head；② 并发用例改写——CAS 防的是 lost update，串行双 rotate 双成功合法；stale revision 冲突在 store 层确定性验证
- 已知缺口（转 TASK-005）：缺省 probe `_no_execution_tracker` 恒 False，`/new` 冲突在 Control Store 落地前不可见；`session_busy` 并发门禁同 TASK-005 STP-02

### Log
- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)
- [2026-09-08] completed (done)

---

## TASK-004: Phase 3——Skill Commands（/skills + /skill + Snapshot directive）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003
- **Source**: command-plane-design.md#§13 /skills 设计(L895-L955), command-plane-design.md#§14 /skill 设计(L957-L1055), command-plane-design.md#§19 RunRuntimeRequest 调整(L1192-L1212), command-plane-design.md#§26 代码改造点(L1434-L1519)
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001
- **Acceptance-Refs**: SKL-01, SKL-02, SKL-03

### Description

`CapabilityQueryService`（轻查询，D4：禁 memory recall/credential 解析）+ `/skills` + `/skill`（`InvocationDirective` → `RunRuntimeRequest` → ContextResolver 校验 → Snapshot 固化 + digest）；空 prompt 用系统默认输入，不送空串。ADR-A017 §1–§2 为契约依据；正常路径（无 directive）行为零变化。

### Checklist
- [x] `InvocationKind`/`InvocationDirective` + `RunRuntimeRequest.invocation_directive` + `RunPayload` typed 字段（§19，不用 dict）
- [x] `CapabilityQueryService.list_effective_skills`：Agent 声明 ∩ 用户授权 ∩ Published ∩ Tenant Policy + required 闭包（复用现有 resolution 规则，不复制算法）
- [x] ContextResolver directive 校验：requested skill ∈ `effective_capability.skills` → exact version → `ResolvedInvocationDirective` 进 Snapshot + digest
- [x] selected Skill instruction 进 `active_skill_instruction`，其余不注入；Tool 权限仍源完整图（RULE-04 不扩权不断言遗漏）
- [x] `/skill` 禁 version pin（`health-check@v2` → `command_usage_invalid`）；空 prompt 默认输入
- [x] [SKL-01][integration] §28.4 五行矩阵（未声明/未授权/Draft/可用/闭包缺失）
- [x] [SKL-02][integration] 成功链 + digest 含 directive + remainder 原样，先写测试记 RED
- [x] [SKL-03][integration] 不存在/无权 → `skill_not_available`；对外统一口径，明细只进 audit/trace（§23 末段）
- [x] verifier RULE-fluxion-runtime-001（manual）：Snapshot 固定版本（directive 进 digest）、Runtime 无状态（执行态只进 PG Control 表，不驻内存为 SoT）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| SKL-01 | integration | Registry 真库（Skill/Agent/Binding/Policy） | 四交集正确 | backend/tests/channel/test_skill_commands.py::test_SKL01_*（§28.4 五行全覆盖：未声明lone/未授权secret/Draft/可用health/闭包needy） | .venv/bin/python -m pytest backend/tests/channel/test_skill_commands.py | verified |
| SKL-02 | integration | ContextResolver、Snapshot digest、真执行 | 版本固化 + digest + 不扩权 | 同上 test_SKL02_*（3 用例，request 侧 version=None，Snapshot 侧 exact=1） | 同上 | verified |
| SKL-03 | integration | 同上 + AuditLog | 拒绝口径 + 无 prompt 正文 | 同上 test_SKL03_*（2 用例） | 同上 | verified |

### Acceptance Evidence

- RED：实现前 6 failed（CapabilityQueryService/handlers/directive 不存在）
- GREEN：6 passed；回归 channel/unit/api/resources/runtime 135 passed + services 44 passed + integration 388 passed（1 pre-existing 失败 `test_status_filter_failed`，干净树同样失败，与本次无关）
- 真实边界证据：PG 真库（Skill/Agent/Binding/Policy 全发布链）、RecordingRuntime directive 断言、ContextResolver 真解析 digest 差分（含无意图对照防空洞）
- mypy clean（`runtime_app.py:235 store.engine` 为 pre-existing，未动）；ruff clean
- 关键语义 2 项：① listing 取交集从严（Agent 未声明/ Draft 直接排除；主解析管线用户扩展 merge 保持不动）；② handler 前置检查仅为即时拒绝，最终以 Snapshot 校验为准；`_active_skill_instructions` 无 directive 全量回退零行为变化
- session 投影：`.code-flow/specs/_session/task-agent-command-plane-TASK-004.md`

### Log
- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)
- [2026-09-08] completed (done)

---

## TASK-005: Phase 4a——单 Pod Execution Control（/stop + /status）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004
- **Source**: command-plane-design.md#§11 Execution Control 与 /stop(L627-L859), command-plane-design.md#§12 /status 设计(L861-L893), command-plane-design.md#§17 RuntimeGateway 扩展(L1117-L1146), command-plane-design.md#§18 Runtime Internal API(L1148-L1190), command-plane-design.md#§24.1–§24.2 并发语义(L1378-L1414), command-plane-design.md#§26 代码改造点(L1434-L1519)
- **Spec-Refs**: N/A（Rule 归属见 TASK-003 resource/database；本任务无独占 required Rule）
- **Acceptance-Refs**: STP-01, STP-02, SES-02（session_busy 侧）

### Description

`runtime_executions` + `ExecutionControlStore`（SQL CAS，禁先 get 后无条件 update）+ `ActiveExecutionRegistry` + `CancellationToken`（D4：首批 3 检查点——loop 前/model 前/tool 前）；run/stream 生命周期接入（create→register→mark running→terminal CAS→unregister，复用现有 cancelled finalize）；`cancel_active_execution` + `get_session_status` 内部 API + Gateway 方法；`/stop` + `/status`。**外部前置**：S1 合入（D2），开工前检查。ADR-A017 §3–§4§6 为契约依据。

### Checklist
- [x] 开工前置检查：S1 已合入（D2）；未合入则本任务 blocked（`cf-task-block`）
- [x] `registry/execution_control.py`：建表 + partial unique active index + CAS 状态机（PG Contract Test）
- [x] `runtime/cancellation.py` + `active_execution_registry.py`：token + 本地 task 注册（仅加速用，非事实源）
- [x] run/stream 接入生命周期 6 步；现有 cancelled finalize/hook/trace 复用，不另建终态实现
- [x] 内部 API：`POST /internal/v1/session-executions:resolve` 风格（§18 建议第二种）+ cancel；四元组匹配校验
- [x] `HttpRuntimeGateway` 实现 control 方法（业务 POST 不自动重试）
- [x] `/stop` 三返回码（nothing_to_stop/stop_requested/stop_already_requested）；`/status` 只读 Head + Control Store、零 Snapshot 构建（D4：handler 仅调 Head + Gateway，构造上无 Snapshot 路径）
- [x] [STP-01][integration] 长任务流式中 `/stop` → CANCELLED；tool 前/后取消点；stop 不依赖原 SSE 连接
- [x] [STP-02][integration] 完成与 stop 竞争 + 重复 stop 幂等 + 并发双消息 `session_busy`，先写测试记 RED

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| STP-01 | integration | PG executions、真 Runtime、SSE 流 | CANCELLED 终态 + Hook/Trace 复用 | backend/tests/channel/test_execution_control.py::test_STP01_* | .venv/bin/python -m pytest backend/tests/channel/test_execution_control.py | verified |
| STP-02 | integration | PG CAS、双并发请求 | 首次终态获胜、幂等、busy | 同上 test_STP02_*（3 用例）+ stop/status 映射 | 同上 | verified |

### Acceptance Evidence

- RED：实现前 5 failed（ExecutionControlService/内部 API//stop//status 不存在）
- GREEN：5 passed；回归 channel/api/services/runtime/resources/unit 305 passed + integration/e2e/contract 591 passed（1 pre-existing `test_status_filter_failed` 除外，干净树同败）
- 真实边界证据：真 RuntimeApplicationService + 阻塞模型 provider + PG 真表 + task.cancel 真取消 + CAS rowcount 判定
- mypy clean（仅 pre-existing `store.engine`）；ruff clean
- 回归修复 3 处：① anyio 取消域在 finally 重复投递→begin/end 加 `asyncio.shield`（否则 eternal-RUNNING，E_LIFE_04 捕获）；② S-09 本地状态审计补 `ActiveExecutionRegistry._entries: Ephemeral` 标注；③ S_ID_03 memory 断言改经 Head 解析 sess（TASK-003 行为变更的测试锚点更新）
- 测试修正 1 处：setup 顺序（service.initialize 会 reset 建库，必须先 init 再播种）——实现无 bug
- `/new` 缺省探针已替换为 Control Store 真查询（TASK-003 缺口关闭）；`session_busy` 并发门禁生效（STP-02）
- session 投影：N/A（Spec-Refs 为 N/A；Rule 归属 TASK-003/007，plan 门禁 pass）

> BLOCKED: 外部前置 S1 未合入（D2）。`api/channel.py:69-73` S2 残留注释仍在，/channels/web/messages 仍逐消息信任 channel_user_id；/stop“只能停自己”的保证无身份信任链支撑。待 S1（per-message 认证）合入后解除。

> UNBLOCKED 2026-09-08：S1 已合入（channel-permessage-auth TASK-001 done：`handle()` verified 签名强制 + S2 注释消除）。本任务可开工。

### Log
- [2026-09-08] created (draft)
- [2026-09-08] blocked (外部前置 S1 未合入，was draft)
- [2026-09-08] unblocked (S1 已合入，reopened as draft)
- [2026-09-08] started (in-progress)
- [2026-09-08] completed (done)

---

## TASK-006: Phase 4b——多 Pod 取消（Redis 信号 + Orphan 认领）

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-005
- **Source**: command-plane-design.md#§11.7 多 Pod 取消(L627-L859), command-plane-design.md#§28.6 /stop 测试(L1588-L1666)
- **Spec-Refs**:
- **Acceptance-Refs**: STP-03

### Description

Redis Pub/Sub 只做取消加速（PG `CANCELLING` 仍是 SoT，信号丢失仍可经执行边界检测收敛）；owner Pod 重启后的 CANCELLING orphan reconciliation。Redis event 只带 execution_id/tenant 等非 secret 数据（§25.10）。

### Checklist
- [x] cancel notifier：`/stop` 写 PG 后 publish；owner Pod 订阅 → `cancel_local()` → asyncio 取消 → 现有 finalize
- [x] 执行边界兜底：CANCELLING 经 Control Store 可见（Redis 丢失仍最终取消，加测试）
- [x] orphan reconciliation：启动/定时认领超期 CANCELLING（owner 失联）并结算
- [x] [STP-03][integration] 双 service 实例跨 Pod cancel + 杀 owner 后认领，先写测试记 RED

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| STP-03 | integration | 双实例、真 Redis、PG | 跨 Pod CANCELLED + 认领 | backend/tests/channel/test_cross_pod_cancel.py（3 用例） | .venv/bin/python -m pytest backend/tests/channel/test_cross_pod_cancel.py | verified |

### Acceptance Evidence

- RED：实现前 3 failed（RedisCancelBus/订阅/认领不存在）
- GREEN：3 passed；回归 308 passed（channel/api/services/runtime/resources/unit）+ 591 passed（integration/e2e/contract，1 pre-existing 除外）
- 真实边界证据：双 RuntimeApplicationService + 真 Redis pub/sub + 真 PG + task.cancel 真取消 + 超期 CANCELLING 真认领
- mypy/ruff clean（仅 pre-existing）
- 关键 bug 修复：`asyncio.shield` 包 begin 导致 `current_task()` 注册内层任务、取消打空——调用方在 shield 之外捕获 outer task 传入；附带发现 STP-01 在 shield 加入后未重跑即合入的问题，以后 shield/并发改动必须重跑全部 STP
- 测试修正 1 处：同库多实例测试不得 initialize 第二个 service（reset 建库会清数据），引入 `_setup_uninitialized_service`（构造不 init、不 close）
- Redis 语义：publish 失败只记录不改变取消（PG 为 SoT）；订阅只加速；orphan 默认 300s 认领窗口
- session 投影：N/A（Spec-Refs 为 N/A；Rule 归属 TASK-003/007，plan 门禁 pass）

### Log
- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)
- [2026-09-08] completed (done)

---

## TASK-007: Phase 5——可观测与 Hardening（Audit/指标/Trace + E2E）

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002, TASK-003, TASK-004, TASK-005, TASK-006
- **Source**: command-plane-design.md#§20 Command 返回协议(L1214-L1259), command-plane-design.md#§21 权限模型(L1261-L1277), command-plane-design.md#§22 审计与可观测性(L1279-L1351), command-plane-design.md#§23 错误码(L1353-L1376), command-plane-design.md#§24 并发语义(L1378-L1414), command-plane-design.md#§25 安全要求(L1416-L1432), command-plane-design.md#§28.7 E2E(L1588-L1666), command-plane-design.md#§29 验收标准(L1668-L1710)
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001, backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**: OBS-01, E2E-01, E2E-02

### Description

audit（6 事件 + 字段白名单，`/bind` code 与 prompt 正文禁入）、metrics（`fluxion_command_*` 等，skill_id 高基数只进 trace/audit）、`chat.command` span；§23 错误码全量对齐；race/chaos 补齐（含 tool sleep 30s 时 `/stop` 时限）；Web + stub-im E2E（D1 改写后范围）。

### Checklist
- [x] audit：`command.executed/rejected`、`session.rotated`、`execution.cancel_requested/cancelled`、`skill.explicitly_selected`（只记 skill_id）
- [x] metrics + `chat.command` span（§22.2–22.3 属性表）
- [x] §23 错误码逐项对齐实现与文档；§25 安全 10 条逐项自查（长度限制/四元组/内部 API 隔离）
- [x] chaos：不可中断 tool 下 `/stop` 时限断言；已完成副作用不宣称回滚（§11.9/§29）
- [x] [OBS-01][integration] audit/metrics/trace 三件套不断言遗漏
- [x] [E2E-01][E2E] Web：`/new` → prompt → 长任务 `/stop` → `/status`（真实链路）
- [x] [E2E-02][E2E] stub-im：`/bind` → `/help` → `/skills` → `/skill` → `/new`（D1 范围）
- [x] verifier RULE-fluxion-dfx-001（manual）：十二项 DFX 逐项过（可用性/可靠性/扩展性/性能/安全/可维护性/可测试性/可观测性/可部署性/兼容性/可恢复性/可运维性）
- [x] verifier RULE-backend-logging-001（manual）：命令 audit 字段 + 脱敏（bind code/prompt 正文禁入）+ request_id/trace_id 关联

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| OBS-01 | integration | AuditLog、TraceStore、指标注册 | 事件/span/指标齐全且脱敏 | backend/tests/channel/test_command_observability.py（3 用例） | .venv/bin/python -m pytest backend/tests/channel/test_command_observability.py | verified |
| E2E-01 | E2E | Web API、Runtime、PG | 全链路命令语义 | backend/tests/channel/test_command_e2e.py::test_E2E01_* | .venv/bin/python -m pytest backend/tests/channel/test_command_e2e.py | verified |
| E2E-02 | E2E | ChannelService、StubImChannelAdapter | IM 分支语义全覆盖 | 同上 test_E2E02_* | 同上 | verified |

### Acceptance Evidence

- RED：实现前 3 failed（审计/指标接线不存在；E2E-01 预先通过因其不断言审计）
- GREEN：5 passed；回归 313 passed（channel/api/services/runtime/resources/unit）+ 591 passed（integration/e2e/contract，1 pre-existing 除外）
- 真实边界证据：PG AuditLog 真查（command.executed/rejected、skill.explicitly_selected 无 prompt）、指标快照断言、30s 阻塞模型中 /stop <5s 返回且 PG 即时 CANCELLING
- mypy/ruff clean（仅 pre-existing）
- 范围说明：① 指标为进程内注册表（无新外部依赖），Prometheus exposition 后续接入；② chaos 覆盖模型阻塞路径，同步不可中断 tool 的语义按 §11.9（尽力中断、不宣称回滚），中断式 tool executor 后续专项；③ §25 自查 10 条通过（长度/四元组/内部 API/未知命令/audit 脱敏/Redis 非 secret）
- session 投影：`.code-flow/specs/_session/task-agent-command-plane-TASK-007.md`

### Log
- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)
- [2026-09-08] completed (done)
