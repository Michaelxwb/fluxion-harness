# Tasks: Hook/Plugin 扩展层语义收尾

- **Source**: /Users/jahan/Downloads/fluxion-111-commits-problem-list-with-code-evidence.md（review 文档，基线 main 111 commits，本地已逐条核对代码验证）
- **Created**: 2026-09-08
- **Updated**: 2026-09-08

## Proposal

111 commits 主架构已稳定，剩余问题集中在 Hook/Plugin 扩展层的执行语义、生命周期与边界契约。本批次收口 6 项（4 P0＋2 P1）：Model Hook 移到真实 Provider Attempt 边界、Post/Terminal Hook 强制 FAIL_OPEN、Plugin shutdown 生命周期闭环、删除虚假 ISOLATED、SPI 收敛、sync Hook 收敛为 async-only。完成后冻结底层扩展架构，转入 SDK/示例/观测。

### Alignment

- **Scope**: 纳入 review §3-§8 的 P1-01~04、P2-01~02；排除 P2-03（已验证当前 README L29-31 为 V2 定义，无需再改）；§11 所列三角色/单制品/无状态/外部 PG-Redis 等架构不再动（Non-goals）。
- **Decisions**:
  - P1-04 采用**删除枚举**（用户确认）：从 Contract 删除 `ISOLATED`，不保留 fail-fast 分支。
  - P2-02 采用 **async-only**（用户确认）：`HookRegistration` 只接受 async handler，sync 注册期拒绝；不保留 to_thread 分支。
  - 验收全部可自动化，无 manual 场景（RULE 行的 manual 系 spec 指定 verifier 类型，由 TASK-001 逐项勾选结论）。
- **Non-goals**: API/Runtime/Worker 拆分、单制品＋FLUXION_ROLE、无状态、外部依赖、Snapshot/Finalizer/Scoped Read/Trace 四态/RuntimeProfile V2/SSE 真流式/HookScope 删除方向。
- **Acceptance**: 下表全部场景 verified（RED 先行），ruff＋mypy clean， gate 记账同步后 pass。

---

## Acceptance Coverage

| 场景ID | 来源 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|------|---------|-------------|---------|------|
| S-MH-01 | review §3.6 多轮一致 | integration | AgentRuntime 真实多轮循环＋TypedEventBus＋stub ModelProvider | TASK-001 | verified |
| S-MH-02 | review §3.6 failover 可观测 | integration | `_complete_with_failover` 真实路由切换 | TASK-001 | verified |
| S-MH-03 | review §3.6 流式同语义 | integration | `stream_final_answer` 真实流式调用 | TASK-001 | verified |
| S-MH-04 | review §3.6 零token计一次 | integration | 零 token 流式分支 | TASK-001 | verified |
| E-MH-01 | review §3.6 不改业务语义 | integration | 挂载 hook 前后 AgentLoop 输出/轮次/tool 结果 | TASK-001 | verified |
| RULE-fluxion-runtime-001 | architecture/runtime-core.md（manual verifier） | manual | 无状态/Snapshot/依赖方向/Hook-Plugin Contract 检查清单 | TASK-001 | verified |
| S-FP-01 | review §4.5 注册期拒绝 | integration | 真实注册/loader 路径 | TASK-002 | verified |
| S-FP-02 | review §4.5 after_tool 不改结果 | integration | 真实 dispatch＋service tool 路径 | TASK-002 | verified |
| S-FP-03 | review §4.5 after_execution 不改终态 | integration | 真实 finalize＋dispatch 顺序 | TASK-002 | verified |
| S-FP-04 | review §4.5 error 不覆盖 | integration | 真实异常出口分支 | TASK-002 | verified |
| S-FP-05 | review §4.5 cancelled 不覆盖 | integration | 真实取消出口分支 | TASK-002 | verified |
| E-FP-01 | review §4.5 写 Tool 不诱发重试 | integration | 副作用已发生＋after 失败的对外表现 | TASK-002 | verified |
| S-PL-01 | review §5.4 正常 shutdown | integration | 真实 `RuntimeApplicationService.close`＋测试 Hook 插件 | TASK-003 | verified |
| S-PL-02 | review §5.4 init 失败 rollback | integration | 真实 `initialize` 失败路径 | TASK-003 | verified |
| S-PL-03 | review §5.4 shutdown 幂等 | integration | 重复 close | TASK-003 | verified |
| E-PL-01 | review §5.4 无残留资源 | integration | task/client/socket 可观测关闭 | TASK-003 | verified |
| S-ISO-01 | review §6.4 拒绝假 ISOLATED | unit＋integration | 真实 `PluginLoader.load`＋manifest 构造 | TASK-004 | verified |
| S-ISO-02 | review §6.4 正常组合不受影响 | integration | TRUSTED＋IN_PROCESS 加载 | TASK-004 | verified |
| S-ISO-03 | review §6.4 不可信不进进程 | unit | 真实 `_enforce_trust` | TASK-004 | verified |
| E-ISO-01 | review §6.4 无宣称残留 | static（grep） | 全仓 `ISOLATED`/isolation 引用 | TASK-004 | verified |
| S-SPI-01 | review §7.5 协议仅 register | unit | 协议形状＋loader 接线 | TASK-005 | verified |
| S-SPI-02 | review §7.5 无 type:ignore | static（mypy） | `runtime_app.py` 安装点 | TASK-005 | verified |
| S-SPI-03 | review §7.5 删 scope 注释 | static（review） | `contracts.py` 注释 | TASK-005 | verified |
| E-SPI-01 | review §7.5 排序只留 Kernel | unit | `HookScheduler.ordered` 单测 | TASK-005 | verified |
| S-SYNC-01 | review §8.5 sync 注册拒绝 | unit＋integration | 真实注册路径 | TASK-006 | verified |
| S-SYNC-02 | review §8.5 async 超时取消 | integration | 真实 EventBus＋计时断言 | TASK-006 | verified |
| S-SYNC-03 | review §8.5 无残留 thread | integration | 超时后线程/任务清点 | TASK-006 | verified |
| E-SYNC-01 | review §8.5 语义文档化 | static（review） | 注释＋契约文档 timeout 定义 | TASK-006 | verified |

| S-CL-01 | review-fixes.design.md#3 验收条件 | integration | Service.close → PluginLoader → PG/MCP/插件 | TASK-007 | verified |
| E-CL-01 | review-fixes.design.md#3 验收条件 | integration | Service.initialize → PluginLoader | TASK-007 | verified |
| S-MB-01 | review-fixes.design.md#3 验收条件 | integration | AgentRuntime → Service bridge → TypedEventBus → Provider | TASK-007 | verified |
| S-MB-02 | review-fixes.design.md#3 验收条件 | integration | AgentRuntime → Provider → Service bridge → TypedEventBus | TASK-007 | verified |
| E-MB-01 | review-fixes.design.md#3 验收条件 | integration | AgentRuntime 多轮/流式真实预算 | TASK-007 | verified |
| RULE-backend-quality-001 | review-fixes.design.md#4 Spec Compliance Matrix | integration＋static | 对应规范适用范围与回归证据 | TASK-007 | verified |
| RULE-backend-database-001 | review-fixes.design.md#4 Spec Compliance Matrix | integration＋static | 对应规范适用范围与回归证据 | TASK-007 | verified |
| RULE-backend-directory-001 | review-fixes.design.md#4 Spec Compliance Matrix | integration＋static | 对应规范适用范围与回归证据 | TASK-007 | verified |
| RULE-backend-logging-001 | review-fixes.design.md#4 Spec Compliance Matrix | integration＋static | 对应规范适用范围与回归证据 | TASK-007 | verified |
| RULE-backend-platform-001 | review-fixes.design.md#4 Spec Compliance Matrix | integration＋static | 对应规范适用范围与回归证据 | TASK-007 | verified |
| RULE-fluxion-console-api-001 | review-fixes.design.md#4 Spec Compliance Matrix | integration＋static | 对应规范适用范围与回归证据 | TASK-007 | verified |
| RULE-fluxion-console-001 | review-fixes.design.md#4 Spec Compliance Matrix | integration＋static | 对应规范适用范围与回归证据 | TASK-007 | verified |
| RULE-fluxion-dfx-001 | review-fixes.design.md#4 Spec Compliance Matrix | integration＋static | 对应规范适用范围与回归证据 | TASK-007 | verified |
| RULE-fluxion-resource-001 | review-fixes.design.md#4 Spec Compliance Matrix | integration＋static | 对应规范适用范围与回归证据 | TASK-007 | verified |
| RULE-fluxion-workflow-001 | review-fixes.design.md#4 Spec Compliance Matrix | integration＋static | 对应规范适用范围与回归证据 | TASK-007 | verified |

---

## TASK-001: P1-01 Model Hook 移到真实 Provider Attempt 边界

- **Status**: done
- **Priority**: P0
- **Depends**: （无；可与 TASK-004 并行）
- **Source**: review §3.1 当前代码事实(L81-L335), §3.2 影响(L337-L356), §3.3 根因(L360-L383), §3.4 整改建议(L386-L431), §3.5 建议 Payload(L437-L464), §3.6 验收标准(L466-L474)
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001
- **Acceptance-Refs**: S-MH-01, S-MH-02, S-MH-03, S-MH-04, E-MH-01, RULE-fluxion-runtime-001

### Description

当前 `before/after_model_call` 包在 `run_step()` 外层（`runtime_app.py` L438-448），与真实 Provider Attempt 数量/身份不一致（多轮循环 `agent.py` L328-340、failover L386-416、payload 取 `routes[0]`、纯流式路径 L621-625 无 hook）。改为 AgentRuntime 在真实 Provider Attempt 边界触发（如 ModelCallLifecycle/Interceptor，Service 注入 TypedEventBus），Payload 补 round/attempt/streaming/status/latency_ms，流式与非流式同语义，Plugin 不侵入 AgentLoop 实现。

### Checklist

- [x] [S-MH-01][integration] 改代码前先写测试并记录 RED：stub ModelProvider 经真实 `_run_model_loop` 走 2 轮成功，断言 before/after 各 2 对且 provider_id 为真实 provider
- [x] [S-MH-02][integration] routes[A 抛错→B 成功] 真实 failover，断言 A(error)＋B(success) 两对且 after.provider_id=B
- [x] [S-MH-03][integration] 纯流式 `stream_final_answer` 真实调用产生等价 hook 对（含 streaming 标记）
- [x] [S-MH-04][integration] 零 token 流式只计一次 model call
- [x] [E-MH-01][integration] 挂载 hook 前后 AgentLoop 输出/轮次/tool 结果一致；hook 抛错（FAIL_OPEN）不改变业务
- [x] verifier `RULE-fluxion-runtime-001`：以 S-MH-01~S-MH-04/E-MH-01 行为证据验证无状态＋Snapshot 固定＋Kernel 只依赖 Contract＋Hook 类型化/timeout/fail policy，并记录结论
- [x] 运行 `ruff check`＋`mypy`（backend 范围）clean（ruff 全过；mypy 唯一报错为 `0118ef9` 既有 `store.engine`，diff 证实非本任务引入，不修）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-MH-01 | integration | AgentRuntime 多轮循环、TypedEventBus、stub provider 经 `_complete_once` | hook 对数＝真实调用数，provider_id 真实 | backend/tests/integration/test_model_hook_boundaries.py::test_S_MH_01_multi_round_hook_pairs_match_attempts | `.venv/bin/python -m pytest -q backend/tests/integration/test_model_hook_boundaries.py -p no:cacheprovider` | verified |
| S-MH-02 | integration | `_complete_with_failover` 真实路由切换 | 每个 attempt 一对 hook，after 为成功方 | backend/tests/integration/test_model_hook_boundaries.py::test_S_MH_02_failover_hooks_fire_per_attempt | `.venv/bin/python -m pytest -q backend/tests/integration/test_model_hook_boundaries.py -p no:cacheprovider` | verified |
| S-MH-03 | integration | `stream_final_answer` 真实流式调用 | 流式 hook 对与非流式同语义 | backend/tests/integration/test_model_hook_boundaries.py::test_S_MH_03_streaming_hook_parity | `.venv/bin/python -m pytest -q backend/tests/integration/test_model_hook_boundaries.py -p no:cacheprovider` | verified |
| S-MH-04 | integration | 零 token 流式分支 | 恰好一次 model call | backend/tests/integration/test_model_hook_boundaries.py::test_S_MH_04_zero_token_stream_counts_once | `.venv/bin/python -m pytest -q backend/tests/integration/test_model_hook_boundaries.py -p no:cacheprovider` | verified |
| E-MH-01 | integration | AgentLoop 全路径（hook 开/关对照） | 输出/轮次/tool 结果一致 | backend/tests/integration/test_model_hook_boundaries.py::test_E_MH_01_hooks_do_not_alter_loop_semantics | `.venv/bin/python -m pytest -q backend/tests/integration/test_model_hook_boundaries.py -p no:cacheprovider` | verified |
| RULE-fluxion-runtime-001 | manual | spec 检查清单 | 逐项 pass 结论 | — | — | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-MH-01 | ImportError：`ModelCallAttempt/ModelCallObserver/ModelCallResult` 不存在（seam 缺失，hook 仍包 run_step 外） | 5 passed（本文件全）；2 轮→各 2 对，round=[1,2]，provider 全 stub | test_S_MH_01_multi_round_hook_pairs_match_attempts | 真实 `_run_model_loop`＋`_complete_once`＋_ScriptProvider（complete_calls=2） | verified |
| S-MH-02 | 同上 | before=[(bad,1),(good,2)]，after status=[error,ok] | test_S_MH_02_failover_hooks_fire_per_attempt | 真实 `_complete_with_failover` 路由切换 | verified |
| S-MH-03 | 同上 | 1 对 streaming=True，output_chars=2，stream_calls=1 | test_S_MH_03_streaming_hook_parity | 真实 `stream_final_answer` token 循环 | verified |
| S-MH-04 | 同上 | 1 对 status=ok output_chars=0，stream_calls=1 | test_S_MH_04_zero_token_stream_counts_once | 零 token 流式分支真实走通 | verified |
| E-MH-01 | 同上 | observer 有/无输出与 tool_results 一致，各 1 对 | test_E_MH_01_hooks_do_not_alter_loop_semantics | hook 开/关对照＋调用计数 | verified |
| RULE-fluxion-runtime-001 | —— | 无状态：observer 只作参数/局部传递，AgentRuntime 不存 hook 状态，bridge 每次执行新建；Snapshot：attempt 数据源于执行期真实调用零修改；依赖方向：kernel/events 仅 stdlib，agent 只引 Protocol＋dataclass；Hook 类型化/timeout/fail_policy：payload 保持 frozen，分发仍走 TypedEventBus 既有语义 | agent.py 参数传递链＋kernel import 检查 | kernel 零 fluxion 导入；agent 无 self._observer 存储 | verified |

### Log

- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)：测试文件/用例/命令已填入契约，先写验收测试取 RED
- [2026-09-08] completed (done)：ModelCallObserver 注入＋attempt 边界触发＋bridge 接线，S-MH-01~04/E-MH-01/RULE 全 verified；回归 hooks/流式/中间件 16＋runtime 32 全绿

---

## TASK-002: P1-02 Post/Terminal Hook 强制 FAIL_OPEN

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: review §4.1 代码事实(L480-L605), §4.2 风险示例(L608-L660), §4.3 根因(L662-L686), §4.4 整改建议(L688-L725), §4.5 验收标准(L728-L735)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-FP-01, S-FP-02, S-FP-03, S-FP-04, S-FP-05, E-FP-01

### Description

当前 EventBus 对所有点位统一允许 FAIL_CLOSED（`events.py` L170-179/L197-201），而 `after_tool_call`（`runtime_tool_ops.py` L145，tool 副作用已发生）、`after_execution`（`runtime_app.py` L452，finalize 之后）及 error/cancelled 点位（L475-501 同样在终态之后）再阻断会制造"业务已成功但返回失败"。按 §4.4 矩阵：before_* 可 FAIL_CLOSED，after_*/on_* 强制 FAIL_OPEN——Post/Terminal 注册 FAIL_CLOSED 时在注册/启动期拒绝或自动规范，企业"审计不可用禁写"改走 `before_tool_call`＋FAIL_CLOSED 前置阻断。

### Checklist

- [x] [S-FP-01][integration] 改代码前先写测试并记录 RED：after_* 点位注册 FAIL_CLOSED，断言注册/启动期被拒绝或规范为 FAIL_OPEN
- [x] [S-FP-02][integration] after_tool 超时/抛错，断言 tool 成功结果对外不变且 execution 继续
- [x] [S-FP-03][integration] after_execution 抛错，断言终态仍为 COMPLETED 且对外成功
- [x] [S-FP-04][integration] on_execution_error 自身失败不断言覆盖原业务异常（code/message 一致）
- [x] [S-FP-05][integration] on_execution_cancelled 自身失败不断言覆盖 cancellation
- [x] [E-FP-01][integration] 写 Tool 副作用已发生＋after 失败，调用方收到成功且不触发重试语义
- [x] 运行 `ruff check`＋`mypy`（backend 范围）clean
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-FP-01 | integration | 真实注册/loader 路径 | FAIL_CLOSED 被拒或被规范 | backend/tests/integration/test_post_hook_fail_open.py::test_S_FP_01_post_hook_fail_closed_rejected_at_registration | `.venv/bin/python -m pytest -q backend/tests/integration/test_post_hook_fail_open.py -p no:cacheprovider` | verified |
| S-FP-02 | integration | 真实 dispatch＋service tool 路径 | 业务结果不变 | backend/tests/integration/test_post_hook_fail_open.py::test_S_FP_02_after_tool_failure_does_not_change_result | `.venv/bin/python -m pytest -q backend/tests/integration/test_post_hook_fail_open.py -p no:cacheprovider` | verified |
| S-FP-03 | integration | 真实 finalize＋dispatch 顺序 | 终态/对外表现不变 | backend/tests/integration/test_post_hook_fail_open.py::test_S_FP_03_after_execution_failure_keeps_completed | `.venv/bin/python -m pytest -q backend/tests/integration/test_post_hook_fail_open.py -p no:cacheprovider` | verified |
| S-FP-04 | integration | 真实异常出口分支 | 原异常 code/message 不变 | backend/tests/integration/test_post_hook_fail_open.py::test_S_FP_04_error_hook_failure_does_not_mask_business_error | `.venv/bin/python -m pytest -q backend/tests/integration/test_post_hook_fail_open.py -p no:cacheprovider` | verified |
| S-FP-05 | integration | 真实取消出口分支 | cancellation 不变 | backend/tests/integration/test_post_hook_fail_open.py::test_S_FP_05_cancelled_hook_failure_does_not_mask_cancellation | `.venv/bin/python -m pytest -q backend/tests/integration/test_post_hook_fail_open.py -p no:cacheprovider` | verified |
| E-FP-01 | integration | 副作用＋after 失败组合 | 调用方成功、无重试诱因 | backend/tests/integration/test_post_hook_fail_open.py::test_E_FP_01_write_tool_not_retried_on_after_failure | `.venv/bin/python -m pytest -q backend/tests/integration/test_post_hook_fail_open.py -p no:cacheprovider` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-FP-01 | FAIL（DID NOT RAISE）：Post 点位 FAIL_CLOSED 注册被接受（矩阵缺失） | 6 passed（本文件全）；5 post 点位拒收＋3 before 照收＋8 点位完备 | test_S_FP_01_post_hook_fail_closed_rejected_at_registration | 真实 `HookScheduler.register`＋`EventPayload.__subclasses__`（按名去重，slots 重建 quirk 见注） | verified |
| S-FP-02 | pin（FAIL_OPEN 本就继续，无行为变化） | 同文件 GREEN；tool_results 含 time.now | test_S_FP_02_after_tool_failure_does_not_change_result | 真实 service run＋PG | verified |
| S-FP-03 | pin（同上） | 同文件 GREEN；trace.status=completed＋error=None | test_S_FP_03_after_execution_failure_keeps_completed | 真实 finalize＋dispatch 顺序 | verified |
| S-FP-04 | pin（同上） | 同文件 GREEN；code=tool_not_allowed（非 hook_dispatch_failed） | test_S_FP_04_error_hook_failure_does_not_mask_business_error | 真实异常出口分支 | verified |
| S-FP-05 | pin（同上） | 同文件 GREEN；CancelledError 穿透 | test_S_FP_05_cancelled_hook_failure_does_not_mask_cancellation | 真实取消出口分支 | verified |
| E-FP-01 | pin（同上） | 同文件 GREEN；成功＋calls=[time.now] 恰好一次 | test_E_FP_01_write_tool_not_retried_on_after_failure | 调用方视角＋计数 | verified |

> 注：`runtime_helpers.py` 的 ruff I001 在 HEAD 已存在（pre-existing，与本任务无关，不修）；既有 FAIL_CLOSED 注册全为 Before 点位（unit/hook_scheduler/benchmark/agent_loop/trace），矩阵切换零影响。

### Log

- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)：测试文件/用例/命令已填入契约；harness 移入 runtime_helpers 供本批复用
- [2026-09-08] completed (done)：register 拒收＋dispatch 纵深＋点位完备测试，6 全 verified；回归 hooks/model-hook/agent_loop/trace 全绿，ruff/mypy clean（I001 为 HEAD 既有）

---

## TASK-003: P1-03 Runtime 持有 Loader 并闭环 shutdown/rollback

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: review §5.1 代码事实(L741-L835), §5.2 影响(L837-L869), §5.3 整改建议(L872-L909), §5.4 验收标准(L912-L939)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-PL-01, S-PL-02, S-PL-03, E-PL-01

### Description

`PluginLoader.shutdown_all` 已实现（`loader.py` L146-150）但 service 层 loader 是局部变量（`runtime_app.py` L241-243），`close()`（L245-247）不调 shutdown；且 `initialize()` 中插件安装（L218）在 memory 初始化（L221-231）之前，后续失败无 rollback。改为 service 持有 loader：`close()` 调 `shutdown_all()`（顺序：hook plugins → mcp → store），`initialize()` 后续失败时 rollback 已加载插件。

### Checklist

- [x] [S-PL-01][integration] 改代码前先写测试并记录 RED：测试 Hook 插件（setup 建 background task＋client，shutdown 关闭），正常 close 断言 shutdown 恰好一次
- [x] [S-PL-02][integration] initialize 后续阶段失败，断言已加载插件被 rollback shutdown
- [x] [S-PL-03][integration] 重复 close，断言 shutdown 幂等（不二次调用、不抛错）
- [x] [E-PL-01][integration] shutdown 后断言无残留 background task、无未关闭 client/socket
- [x] 运行 `ruff check`＋`mypy`（backend 范围）clean
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-PL-01 | integration | 真实 `close`＋测试 Hook 插件 | shutdown 恰好一次 | backend/tests/integration/test_plugin_lifecycle.py::test_S_PL_01_close_calls_plugin_shutdown_once | `.venv/bin/python -m pytest -q backend/tests/integration/test_plugin_lifecycle.py -p no:cacheprovider` | verified |
| S-PL-02 | integration | 真实 `initialize` 失败路径 | 已加载被 rollback | backend/tests/integration/test_plugin_lifecycle.py::test_S_PL_02a_install_failure_rolls_back, test_S_PL_02b_memory_stage_failure_rolls_back | `.venv/bin/python -m pytest -q backend/tests/integration/test_plugin_lifecycle.py -p no:cacheprovider` | verified |
| S-PL-03 | integration | 重复 close | 幂等 | backend/tests/integration/test_plugin_lifecycle.py::test_S_PL_03_shutdown_is_idempotent | `.venv/bin/python -m pytest -q backend/tests/integration/test_plugin_lifecycle.py -p no:cacheprovider` | verified |
| E-PL-01 | integration | task/client/socket 可观测关闭 | 零残留 | backend/tests/integration/test_plugin_lifecycle.py::test_E_PL_01_no_leftover_resources_after_close | `.venv/bin/python -m pytest -q backend/tests/integration/test_plugin_lifecycle.py -p no:cacheprovider` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-PL-01 | FAIL（assert 0 == 1）：setup 进了但 close 未调 shutdown | 5 passed（本文件全）；shutdown_count=1 | test_S_PL_01_close_calls_plugin_shutdown_once | 真实 `close→_shutdown_hook_plugins→shutdown_all` | verified |
| S-PL-02 | FAIL（同文件 2 用例同因：rollback 缺失） | a：good setup=1/shutdown=1＋loader=None；b：memory 失败同 rollback | test_S_PL_02a/b | 真实安装中途＋memory 阶段双失败路径 | verified |
| S-PL-03 | FAIL（同因） | 两次 close 后 shutdown_count=1 | test_S_PL_03_shutdown_is_idempotent | loader 弹出即幂等 | verified |
| E-PL-01 | FAIL（同因） | task.done＋client.closed＋loader=None | test_E_PL_01_no_leftover_resources_after_close | task/client 可观测关闭 | verified |

> 注：mypy 唯一报错仍为 `0118ef9` 既有 `store.engine`（TASK-001 已记），与本任务无关。

### Log

- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)：测试文件/用例/命令已填入契约；LifecycleHookPlugin 测试插件＋真实 service 路径
- [2026-09-08] completed (done)：持有 loader＋close/rollback 闭环，5 全 verified；回归 hooks/post-hook/model-hook 17 全绿

---

## TASK-004: P1-04 从 Contract 删除虚假 ISOLATED

- **Status**: done
- **Priority**: P0
- **Depends**: （无；可与 TASK-001 并行）
- **Source**: review §6.1 代码事实(L945-L1061), §6.2 问题本质(L1064-L1079), §6.3 整改建议(L1082-L1110), §6.4 验收标准(L1114-L1120)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-ISO-01, S-ISO-02, S-ISO-03, E-ISO-01

### Description

`PluginExecutionMode.ISOLATED` 只是枚举（`contracts.py` L26-28），`load()` 无隔离分支（`loader.py` L129-130 直接 setup），`UNTRUSTED＋ISOLATED` 能通过 trust 检查进当前进程（L152-159）——Trust Boundary 语义错误。按确认决策**删除枚举**（不保留 fail-fast 分支）：V1 固定 TRUSTED＋IN_PROCESS；`PluginManifest.execution_mode` 默认及引用同步清理；`_enforce_trust` 保留不可信拦截。

### Checklist

- [x] [S-ISO-01][unit＋integration] 改代码前先写测试并记录 RED：旧 `ISOLATED` 值构造 manifest / load，断言明确失败
- [x] [S-ISO-02][integration] TRUSTED＋IN_PROCESS 插件加载不受影响（既有 loader 路径回归）
- [x] [S-ISO-03][unit] UNTRUSTED 插件被 `_enforce_trust` 拒绝，不进入进程
- [x] [E-ISO-01][static] 全仓 grep `ISOLATED`/isolation 零残留（历史注记除外）；文档不再宣称隔离能力
- [x] 运行 `ruff check`＋`mypy`（backend 范围）clean
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-ISO-01 | unit＋integration | 真实 `load`＋manifest 构造 | 旧值明确失败 | backend/tests/e2e/test_plugin_trust_isolation_fault.py::test_s04_isolated_mode_no_longer_exists | `.venv/bin/python -m pytest -q backend/tests/e2e/test_plugin_trust_isolation_fault.py -p no:cacheprovider` | verified |
| S-ISO-02 | integration | 真实 loader 路径 | 正常组合加载成功 | backend/tests/e2e/test_plugin_trust_isolation_fault.py::test_s04_trusted_in_process_loads_unaffected | `.venv/bin/python -m pytest -q backend/tests/e2e/test_plugin_trust_isolation_fault.py -p no:cacheprovider` | verified |
| S-ISO-03 | unit | 真实 `_enforce_trust` | 不可信被拒 | backend/tests/e2e/test_plugin_trust_isolation_fault.py::test_s04_untrusted_plugin_is_rejected | `.venv/bin/python -m pytest -q backend/tests/e2e/test_plugin_trust_isolation_fault.py -p no:cacheprovider` | verified |
| E-ISO-01 | static | 全仓引用 | 零残留 | — | `grep -rn "ISOLATED" backend/src backend/tests backend/examples` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-ISO-01 | FAIL：`PluginExecutionMode.ISOLATED` 仍存在（旧契约未删） | 5 passed（本文件全）；成员/值双重缺失＋旧值构造 ValueError | test_s04_isolated_mode_no_longer_exists | 真实枚举＋load 路径 | verified |
| S-ISO-02 | pin（旧代码已支持，无行为变化，不伪造失败） | 同文件 GREEN | test_s04_trusted_in_process_loads_unaffected | 真实 loader 路径 | verified |
| S-ISO-03 | pin（旧代码已拒 untrusted+in_process，无行为变化） | 同文件 GREEN | test_s04_untrusted_plugin_is_rejected | 真实 `_enforce_trust`（PluginTrustError＋loader 为空） | verified |
| E-ISO-01 | —— | 零生产引用；残留 6 处全为删除注记/测试意图（contracts 注释＋本文件 docstring/断言），`PluginExecutionMode.ISOLATED` 构造已不可能 | grep 全仓 | backend/src 仅注释 1 处 | verified |

> 另：本文件断言 3 的 `scope` 子集在改前已于 main 失败（TASK-007 删字段后残留，pre-existing），本次一并修正为真实字段。

### Log

- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)：测试文件/用例/命令已填入契约；先改 e2e 故障测试取 RED（另发现断言 3 scope 残留已在 main 失败，记 pre-existing）
- [2026-09-08] completed (done)：删 ISOLATED 枚举＋e2e 新契约 5 passed＋scope 残留修复；loader/dispatch 回归 25 全绿，ruff/mypy clean

---

## TASK-005: P2-01 HookRegistryProtocol 收敛为纯 register

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-004
- **Source**: review §7.1 代码事实(L1126-L1210), §7.2 注释漂移(L1212-L1223), §7.3 根因(L1226-L1241), §7.4 整改建议(L1244-L1287), §7.5 验收标准(L1290-L1296)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-SPI-01, S-SPI-02, S-SPI-03, E-SPI-01

### Description

`HookRegistryProtocol` 暴露 `ordered(event_type: str)`（`contracts.py` L296-299），与 Kernel `HookScheduler.ordered(event_type: type)`（`events.py` L143-153）漂移，迫使 `runtime_app.py` L241 用 `type: ignore`；注释 L290-293 仍写已删除的 scope。按 §7.4 收敛为纯注册接收端（`register` only，Plugin 层只提交 Registration，排序/调度/执行留 Kernel）；loader 的 `hook_registry` 注解同步对齐，去掉 ignore。

### Checklist

- [x] [S-SPI-01][unit] 改代码前先记录现状 RED（协议含 ordered＋str）：收敛后断言协议仅 `register`
- [x] [S-SPI-02][static] 去掉 `runtime_app.py` L241 的 `type: ignore` 后 `mypy` clean
- [x] [S-SPI-03][static] `contracts.py` scope 残留注释删除（review 确认）
- [x] [E-SPI-01][unit] `HookScheduler.ordered` 排序语义单测保留在 Kernel 侧
- [x] 运行 `ruff check`＋`mypy`（backend 范围）clean
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-SPI-01 | unit | 协议形状＋loader 接线 | 仅 register | backend/tests/e2e/test_plugin_trust_isolation_fault.py::test_s04_hook_sink_carries_timeout_fail_policy | `.venv/bin/python -m pytest -q backend/tests/e2e/test_plugin_trust_isolation_fault.py -p no:cacheprovider` | verified |
| S-SPI-02 | static | 安装点类型检查 | mypy clean、无 ignore | — | backend mypy（runtime_app/loader） | verified |
| S-SPI-03 | static | 注释文本 | 无 scope 残留 | — | review 确认 | verified |
| E-SPI-01 | unit | `HookScheduler.ordered` | 排序语义单测绿 | backend/tests/unit/test_hook_scheduler.py | `.venv/bin/python -m pytest -q backend/tests/unit/test_hook_scheduler.py -p no:cacheprovider` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-SPI-01 | ERROR（collection ImportError）：`HookRegistrationSinkProtocol` 不存在 | 5 passed（本文件全）；有 register、无 ordered | test_s04_hook_sink_carries_timeout_fail_policy | 真实协议形状 | verified |
| S-SPI-02 | ——（静态，随实现验证） | mypy clean（除 `0118ef9` 既有 store.engine）；零 `type: ignore`（显式 `_HookRegistrationSink` 适配，泛型结构不兼容故不用隐式匹配） | 安装点 L275 附近 | mypy 输出＋grep ignore | verified |
| S-SPI-03 | ——（静态，随实现验证） | scope/ordered 残留注释已删改；SPI-06 注释写明纯接收端 | contracts.py SPI-06＋loader 注释 | review | verified |
| E-SPI-01 | ——（保持绿，随实现回归） | scheduler 单测绿（含 24 passed 批次） | test_hook_scheduler.py | Kernel 侧排序保留 | verified |

### Log

- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)：测试文件/用例/命令已填入契约；e2e 断言先行取 RED
- [2026-09-08] completed (done)：改名 HookRegistrationSinkProtocol＋显式适配去 ignore，S-SPI 全 verified；回归 24＋25 全绿

---

## TASK-006: P2-02 Hook Handler 收敛为 async-only

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-002
- **Source**: review §8.1 代码事实(L1302-L1343), §8.2 实际行为(L1345-L1378), §8.3 风险(L1380-L1394), §8.4 整改建议(L1397-L1428), §8.5 验收标准(L1431-L1439)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-SYNC-01, S-SYNC-02, S-SYNC-03, E-SYNC-01

### Description

同步 handler 经 `to_thread`＋`wait_for`（`events.py` L213-222），timeout 只能停止等待、不能终止后台线程，注释 L216"真正约束执行时间"描述不准确。按确认决策收敛为 **async-only**：`HookHandler` 只接受 async callable，sync 注册期明确失败，删 to_thread 分支；timeout 语义以 async cancellation 为准；修正注释并在契约文档明确 timeout＝Runtime 等待上限；检查全仓既有 handler（示例 `backend/examples/audit_hook/`、测试）全为 async。

### Checklist

- [x] [S-SYNC-01][unit＋integration] 改代码前先写测试并记录 RED：sync handler 注册，断言明确失败
- [x] [S-SYNC-02][integration] async hook 超时，断言任务被取消、主流程按 fail_policy 继续（与 TASK-002 矩阵一致）
- [x] [S-SYNC-03][integration] 超时后断言无残留后台 thread/task
- [x] [E-SYNC-01][static] L216 错误注释修正；契约文档写明 timeout＝等待上限、非强终止
- [x] 全仓既有 handler（示例/测试）确认为 async，无残留 sync 注册
- [x] 运行 `ruff check`＋`mypy`（backend 范围）clean
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-SYNC-01 | unit＋integration | 真实注册路径 | sync 明确失败 | backend/tests/integration/test_hook_async_only.py::test_S_SYNC_01_sync_handler_rejected_at_registration | `.venv/bin/python -m pytest -q backend/tests/integration/test_hook_async_only.py -p no:cacheprovider` | verified |
| S-SYNC-02 | integration | 真实 EventBus＋计时 | 超时取消＋主流程继续 | backend/tests/integration/test_hook_async_only.py::test_S_SYNC_02_async_timeout_cancels_handler | `.venv/bin/python -m pytest -q backend/tests/integration/test_hook_async_only.py -p no:cacheprovider` | verified |
| S-SYNC-03 | integration | 线程/任务清点 | 零残留 | backend/tests/integration/test_hook_async_only.py::test_S_SYNC_03_no_thread_spawned_for_hooks | `.venv/bin/python -m pytest -q backend/tests/integration/test_hook_async_only.py -p no:cacheprovider` | verified |
| E-SYNC-01 | static | 注释＋契约文档 | 语义准确 | — | 注释放置＋`HookHandler` 别名 review 确认 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-SYNC-01 | FAIL（DID NOT RAISE）×2：sync 注册被接受（新文件＋旧 E-R06 改写） | 3 passed（新文件全）＋旧 E-R06 改写通过 | test_S_SYNC_01 / test_E_R06_sync_handler_is_rejected | 真实 `HookRegistration.__post_init__` | verified |
| S-SYNC-02 | pin（async 超时取消本就成立） | 同批 GREEN；TIMEOUT＋cancelled=[True] | test_S_SYNC_02_async_timeout_cancels_handler | 真实 dispatch＋CancelledError 可观测 | verified |
| S-SYNC-03 | pin（线程清点本就成立） | 同批 GREEN；线程集合无新增 | test_S_SYNC_03_no_thread_spawned_for_hooks | threading 前后比较 | verified |
| E-SYNC-01 | —— | "真正约束"旧注释已删；新注释写明等待上限＋cooperative＋无线程；`HookHandler` 别名仅 `Awaitable[None]`；全仓 handler（audit_hook/测试）确认为 async | events.py `_run_with_timeout`＋别名定义 | grep＋review | verified |

> 连带迁移：`test_agent_loop_product.py` 的 sync lambda hook 按新契约改为 async（改动炸到即修）；全量后端套件 953 passed，剩余 5 项与本批无关（DOD-09 Playwright 端口占用系环境；`status_filter_failed` 经 stash 对照在干净 HEAD 同样失败；3×scale 为 V1 幽灵字段种子 vs V2，均为 pre-existing）。

### Log

- [2026-09-08] created (draft)
- [2026-09-08] started (in-progress)：测试文件/用例/命令已填入契约；旧 E-R06 改写＋新文件取 RED
- [2026-09-08] completed (done)：async-only（构造期拒绝＋to_thread 删除＋注释修正），新文件 3＋旧 E-R06 改写全 verified；回归 19 全绿


---

## TASK-007: Review 修复关闭故障、模型 Hook 预算与取消收尾

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006
- **Source**: review-fixes.design.md#2 技术设计, review-fixes.design.md#3 验收条件, review-fixes.design.md#4 Spec Compliance Matrix
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001, backend-database#RULE-backend-database-001, backend-directory-structure#RULE-backend-directory-001, backend-logging#RULE-backend-logging-001, backend-platform-rules#RULE-backend-platform-001, fluxion-console-api-contract#RULE-fluxion-console-api-001, fluxion-console-channel#RULE-fluxion-console-001, fluxion-dfx#RULE-fluxion-dfx-001, fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-workflow-capability#RULE-fluxion-workflow-001
- **Acceptance-Refs**: S-CL-01, E-CL-01, S-MB-01, S-MB-02, E-MB-01, RULE-backend-quality-001, RULE-backend-database-001, RULE-backend-directory-001, RULE-backend-logging-001, RULE-backend-platform-001, RULE-fluxion-console-api-001, RULE-fluxion-console-001, RULE-fluxion-dfx-001, RULE-fluxion-resource-001, RULE-fluxion-workflow-001

### Description

修复本会话 review 已复现的三个缺陷；本任务同时承接自动 scope refresh 新增的 required Spec。规范具体适用范围见局部设计矩阵，不变更核心 Contract。原运行态规范仍由 TASK-001 唯一负责。

已确认决策（review 结论落字）：
- `close()` 尝试完 Hook/MCP/Store 全部清理后，仍有失败则抛聚合错（`PluginShutdownError`），不吞；
- 单插件 shutdown 默认 bound 5000ms（与 FEAT-07 启动预算同量级），loader 构造可覆盖；
- 单次取消/超时/流关闭必成对上报，双取消仅 best-effort；
- 预算耗尽时连 before 也不触发（不制造未开始调用的 after）。

### Checklist

- [x] [S-CL-01][integration] 先写 RED 再修复：Service.close → PluginLoader → PG/MCP/插件，断言关闭失败后继续清理、可重试，且全部尝试后抛聚合错
- [x] [E-CL-01][integration] 先写 RED 再修复：Service.initialize → PluginLoader，断言 rollback 保留原异常（notes 附带清理错）与单插件关闭 bound
- [x] [S-MB-01][integration] 先写 RED 再修复：AgentRuntime → Service bridge → TypedEventBus → Provider，断言慢 post hook 不消耗模型预算
- [x] [S-MB-02][integration] 先写 RED 再修复：AgentRuntime → Provider → Service bridge → TypedEventBus，断言单次 deadline/取消/流关闭成对上报（双取消 best-effort）
- [x] [E-MB-01][integration] 先写 RED 再修复：AgentRuntime 多轮/流式真实预算，断言累计业务 deadline 不重置、per-attempt 超时语义不变
- [x] verifier `RULE-backend-quality-001`：逐项核查 review-fixes.design.md#4 对应适用范围，运行关联集成/架构回归并记录证据
- [x] verifier `RULE-backend-database-001`：逐项核查 review-fixes.design.md#4 对应适用范围，运行关联集成/架构回归并记录证据
- [x] verifier `RULE-backend-directory-001`：逐项核查 review-fixes.design.md#4 对应适用范围，运行关联集成/架构回归并记录证据
- [x] verifier `RULE-backend-logging-001`：逐项核查 review-fixes.design.md#4 对应适用范围，运行关联集成/架构回归并记录证据
- [x] verifier `RULE-backend-platform-001`：逐项核查 review-fixes.design.md#4 对应适用范围，运行关联集成/架构回归并记录证据
- [x] verifier `RULE-fluxion-console-api-001`：逐项核查 review-fixes.design.md#4 对应适用范围，运行关联集成/架构回归并记录证据
- [x] verifier `RULE-fluxion-console-001`：逐项核查 review-fixes.design.md#4 对应适用范围，运行关联集成/架构回归并记录证据
- [x] verifier `RULE-fluxion-dfx-001`：逐项核查 review-fixes.design.md#4 对应适用范围，运行关联集成/架构回归并记录证据
- [x] verifier `RULE-fluxion-resource-001`：逐项核查 review-fixes.design.md#4 对应适用范围，运行关联集成/架构回归并记录证据
- [x] verifier `RULE-fluxion-workflow-001`：逐项核查 review-fixes.design.md#4 对应适用范围，运行关联集成/架构回归并记录证据
- [x] 执行 cf-validate，记录 RED/GREEN 与既有失败，完成 code/review Gate

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-CL-01 | integration | Service.close → PluginLoader → PG/MCP/插件 | 关闭失败后继续清理、可重试、抛聚合错 | backend/tests/integration/test_hook_cleanup_failures.py::test_S_CL_01 | `.venv/bin/python -m pytest -q backend/tests/integration/test_hook_cleanup_failures.py -p no:cacheprovider` | verified |
| E-CL-01 | integration | Service.initialize → PluginLoader | rollback 保留原异常与有界清理 | backend/tests/integration/test_hook_cleanup_failures.py::test_E_CL_01 | `.venv/bin/python -m pytest -q backend/tests/integration/test_hook_cleanup_failures.py -p no:cacheprovider` | verified |
| S-MB-01 | integration | AgentRuntime → Service bridge → TypedEventBus → Provider | 慢 post hook 不消耗模型预算 | backend/tests/integration/test_model_hook_deadlines.py::test_S_MB_01 | `.venv/bin/python -m pytest -q backend/tests/integration/test_model_hook_deadlines.py -p no:cacheprovider` | verified |
| S-MB-02 | integration | AgentRuntime → Provider → Service bridge → TypedEventBus | 单次 deadline/取消/流关闭成对 | backend/tests/integration/test_model_hook_deadlines.py::test_S_MB_02 | `.venv/bin/python -m pytest -q backend/tests/integration/test_model_hook_deadlines.py -p no:cacheprovider` | verified |
| E-MB-01 | integration | AgentRuntime 多轮/流式真实预算 | 累计业务 deadline 不重置 | backend/tests/integration/test_model_hook_deadlines.py::test_E_MB_01 | `.venv/bin/python -m pytest -q backend/tests/integration/test_model_hook_deadlines.py -p no:cacheprovider` | verified |
| RULE-backend-quality-001 | integration＋static | 新模块/loader/service/agent 全改动面 | 类型完整＋有界＋聚合不吞＋ruff/mypy | 本任务 5 场景＋全量回归 956 | ruff clean；mypy 仅既有 store.engine | verified |
| RULE-backend-database-001 | integration | TEST_POSTGRES_DSN 真实 PG | 无新增 SQL/事务，Store 抽象调用 | test_hook_cleanup_failures 全（PG harness） | PG 集成回归绿 | verified |
| RULE-backend-directory-001 | static | runtime/attempt_budget 新模块＋loader/service 归属 | 预算放 runtime，生命周期放 loader/service | backend/tests/architecture/ 12 passed | AST 架构回归绿 | verified |
| RULE-backend-logging-001 | integration | context.emit＋hook trace sink | 复用既有留痕，原异常传播，无 Secret | test_S_MB_02（取消/超时成对＋原错保持） | 留痕断言绿 | verified |
| RULE-backend-platform-001 | static | 零 HTTP/部署变更 | dev/prod 共用逻辑 | git diff：零 frontend/deploy/api 文件 | diff 审查 | verified |
| RULE-fluxion-console-api-001 | static | 零新 Handler/字段 | 异常经既有基础设施返回 | git diff：零 api 文件＋既有 E-02 绿 | diff 审查＋回归 | verified |
| RULE-fluxion-console-001 | static | 仅 Runtime 执行边界 | 不碰 Channel/部署边界 | git diff：仅 backend runtime/loader/service | diff 审查 | verified |
| RULE-fluxion-dfx-001 | integration | RED 先行＋5 场景＋全回归 | 关闭恢复＋预算＋成对 Trace | 本任务 RED 记录＋956 passed | 全量回归绿 | verified |
| RULE-fluxion-resource-001 | integration | Snapshot/Registry 零改动 | Contract 不变 | e2e snapshot 回归绿（含全量） | 回归绿 | verified |
| RULE-fluxion-workflow-001 | integration | Tool 经既有 handler | 无 durable state 引入 | test_agent_loop_product 绿（含全量） | 回归绿 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-CL-01 | ERROR（collection ImportError）：`PluginShutdownError` 不存在 | 2 passed（本文件）；失败插件保留＋健康关完＋重试只跑失败项＋抛聚合 | test_S_CL_01 | 真实 close→shutdown_all（聚合＋保留） | verified |
| E-CL-01 | 同上（同文件导入阻塞） | notes 含插件 id＋loader 保留＋close 重试成功；5ms 超时后健康仍关完 | test_E_CL_01 | 真实 rollback notes＋loader 超时 | verified |
| S-MB-01 | FAIL：慢 hook（50ms）吃掉 20ms deadline → AgentLoopTimeoutError | 成功输出 ok（hook 耗时已扣除） | test_S_MB_01 | 真实 bridge＋bus＋AttemptBudget | verified |
| S-MB-02 | FAIL：取消/timeout/流关闭的 attempt 缺 after 对 | 三段各 1 对：timeout/timeout、cancel/error、aclose/error，原错保持 | test_S_MB_02 | 真实 failover/取消/aclose 路径 | verified |
| E-MB-01 | FAIL：afters 只有 ["error"]，缺 timeout 对（外层 wait_for 直接取消） | afters=[error, timeout]，累计 90ms 抛 deadline | test_E_MB_01 | attempt 级 wait_for＋预算耗尽分流 | verified |

> 实现注记：① 外层整轮 wait_for 已删（它会取消合法 Hook 等待），deadline 改 attempt 级＋tool 段累计强制，挂起 tool 仍有 wait_for 兜底；② 重构中途漏 `continue` 致失败分支掉进成功上报，被 S-MH-02/E-MB-01 当场抓住，已修；③ S-PL-02a/b 旧断言（loader=None）在新保留语义下依然通过（全成功回滚即清空释放），无需改 verified 测试；④ 双取消 best-effort（设计已定）；⑤ mypy 唯一报错仍为既有 store.engine。

### Log

- [2026-09-08] created (draft)：用户授权修复 review 三项缺陷，局部 Plan 补齐 required Spec。
- [2026-09-08] started (in-progress)：Plan Gate pass，原生 start 绑定 TASK-007，开始 RED 回归。
- [2026-09-08] completed (done)：场景 ID 命名空间化＋三决策落字；loader 聚合/保留/5000ms＋service 保持引用/全试后抛/notes；attempt_budget 模块＋attempt 级累计强制＋tool 段兜底；5 场景＋10 Rule 全 verified；cf-validate 等价全过（仅 2 处 HEAD 既有）；全量 956 passed（5 项已知无关）。
