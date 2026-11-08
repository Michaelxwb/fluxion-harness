# Tasks: 出站请求限流 SDK

- **Source**: egress-rate-sdk.design.md
- **Created**: 2026-11-08
- **Updated**: 2026-11-08

## Proposal

在现有 platform-sdk 和 Runtime/Worker 中提供本地预算；外部 Proxy 承担全局保护。按当前 main 基线实施，保留只读 Session broker；旧 Skill 自行联网不假称已受限。没有新服务、表、管理页面或共享计量存储。

测试骨架位于本需求 planned_tests，避免尚未实现的占位体进入常规测试收集；实施阶段在这些固定 ID 下替换为真实测试，导入可放测试体内。Plan 仅收集，不执行占位体、不伪造 RED/GREEN。预估为计划参考，不是交付承诺。

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout | 优先级 | 执行方式 | 环境 | 输入 | E2E理由 | 触发路径 | 冒烟 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S-01 | egress-rate-sdk.design.md#2.4 验收条件 | unit | 真实 Python SDK 对象与可注入单调时钟 | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/sdk/test_request_budget.py::test_s_01_requests_are_consumed_on_error_and_cancel", "tests/sdk/test_request_budget.py::test_s_01_upstream_limit_still_consumes_a_request"] | . | 300 | P0 | auto | none | packages/contracts/**; packages/platform-sdk/**; apps/agent-runtime/**; apps/agent-worker/**; tests/contracts/test_egress_budget.py | - | - | no |
| S-02 | egress-rate-sdk.design.md#2.4 验收条件 | integration | 真实 Runtime/Worker + 本地 HTTP 探针 + PlatformAdapter，不使用 mock transport | TASK-003 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_s_02"] | . | 300 | P0 | auto | local-api | packages/contracts/**; packages/platform-sdk/**; apps/agent-runtime/**; apps/agent-worker/**; tests/contracts/test_egress_budget.py | - | - | no |
| S-03 | egress-rate-sdk.design.md#2.4 验收条件 | integration | 真实 Runtime + Streamable HTTP MCP 探针 | TASK-004 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_s_03"] | . | 300 | P0 | auto | local-api | packages/contracts/**; packages/platform-sdk/**; apps/agent-runtime/**; apps/agent-worker/**; tests/contracts/test_egress_budget.py | - | - | no |
| S-04 | egress-rate-sdk.design.md#2.4 验收条件 | integration | 真实 Runtime/Worker、Skill 子进程和既有快照/租约边界 | TASK-005 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_s_04"] | . | 300 | P0 | auto | local-api | packages/contracts/**; packages/platform-sdk/**; apps/agent-runtime/**; apps/agent-worker/**; tests/contracts/test_egress_budget.py | - | - | no |
| E-01 | egress-rate-sdk.design.md#2.4 验收条件 | unit | 真实 SDK 对象与 fake send counter，不发送真实网络 | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/sdk/test_request_budget.py::test_e_01_concurrency_releases_only_its_own_lease", "tests/sdk/test_request_budget.py::test_invalid_context_cannot_bypass_target_matching", "tests/sdk/test_request_budget.py::test_invocation_from_other_controller_is_rejected"] | . | 300 | P0 | auto | none | packages/contracts/**; packages/platform-sdk/**; apps/agent-runtime/**; apps/agent-worker/**; tests/contracts/test_egress_budget.py | - | - | no |
| E-02 | egress-rate-sdk.design.md#2.4 验收条件 | integration | 真实 Skill 子进程组、Runtime/Worker 和 HTTP 探针；沿用 start_new_session/killpg | TASK-005 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_e_02"] | . | 300 | P0 | auto | local-api | packages/contracts/**; packages/platform-sdk/**; apps/agent-runtime/**; apps/agent-worker/**; tests/contracts/test_egress_budget.py | - | - | no |
| B-01 | egress-rate-sdk.design.md#2.4 验收条件 | unit | 真实 SDK 对象与可注入时钟 | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/sdk/test_request_budget.py::test_b_01_atomic_parent_and_child_rules", "tests/sdk/test_request_budget.py::test_b_01_concurrent_reservations_do_not_overshoot", "tests/sdk/test_request_budget.py::test_overlapping_target_denial_does_not_debit_parent"] | . | 300 | P0 | auto | none | packages/contracts/**; packages/platform-sdk/**; apps/agent-runtime/**; apps/agent-worker/**; tests/contracts/test_egress_budget.py | - | - | no |
| B-02 | egress-rate-sdk.design.md#2.4 验收条件 | unit | 真实 SDK 装配对象，不连接 Redis/PG | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/sdk/test_request_budget.py::test_b_02_no_limits_does_not_construct_a_controller", "tests/sdk/test_request_budget.py::test_unmatched_rules_do_not_consume_or_call_observer"] | . | 300 | P0 | auto | none | packages/contracts/**; packages/platform-sdk/**; apps/agent-runtime/**; apps/agent-worker/**; tests/contracts/test_egress_budget.py | - | - | no |
| B-03 | egress-rate-sdk.design.md#2.4 验收条件 | integration | 真实慢 HTTP/流式探针与 Runtime 发送器 | TASK-003 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_b_03"] | . | 300 | P0 | auto | local-api | packages/contracts/**; packages/platform-sdk/**; apps/agent-runtime/**; apps/agent-worker/**; tests/contracts/test_egress_budget.py | - | - | no |
| E-03 | egress-rate-sdk.design.md#2.4 验收条件 | integration | 两个独立 Runtime/Worker 进程 + 真实 Proxy HTTP 探针，不使用共享 limiter 存储 | TASK-005 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_e_03"] | . | 300 | P0 | auto | local-api | packages/contracts/**; packages/platform-sdk/**; apps/agent-runtime/**; apps/agent-worker/**; tests/contracts/test_egress_budget.py | - | - | no |
| B-04 | egress-rate-sdk.design.md#2.4 验收条件 | contract | 真实 Python SDK/配置契约对象，可注入时钟和 observer；不代表产品执行证据 | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/contracts/test_egress_budget.py"] | . | 300 | P0 | auto | none | packages/contracts/**; packages/platform-sdk/**; apps/agent-runtime/**; apps/agent-worker/**; tests/contracts/test_egress_budget.py | - | - | no |
| B-05 | egress-rate-sdk.design.md#2.4 验收条件 | unit | 真实 Python SDK/配置契约对象，可注入时钟和 observer；不代表产品执行证据 | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/sdk/test_request_budget.py::test_b_05_matching_and_exact_window_boundary", "tests/sdk/test_request_budget.py::test_b_05_mcp_handshake_does_not_match_a_tool", "tests/sdk/test_request_budget.py::test_b_05_query_and_encoded_identifier_do_not_create_new_buckets"] | . | 300 | P0 | auto | none | packages/contracts/**; packages/platform-sdk/**; apps/agent-runtime/**; apps/agent-worker/**; tests/contracts/test_egress_budget.py | - | - | no |
| E-04 | egress-rate-sdk.design.md#2.4 验收条件 | unit | 真实 Python SDK/配置契约对象，可注入时钟和 observer；不代表产品执行证据 | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/sdk/test_request_budget.py::test_e_04_observer_failure_does_not_resend_or_leak", "tests/sdk/test_request_budget.py::test_observer_cancellation_cleans_the_acquired_lease", "tests/sdk/test_request_budget.py::test_e_04_diagnostic_outcome_cannot_carry_arbitrary_secret_text"] | . | 300 | P0 | auto | none | packages/contracts/**; packages/platform-sdk/**; apps/agent-runtime/**; apps/agent-worker/**; tests/contracts/test_egress_budget.py | - | - | no |

| S-05 | egress-rate-sdk.design.md#2.4 验收条件 | contract | 真实 Runtime build_snapshot/execution_defaults_of 与 contracts build_task_snapshot；只验证构建读回契约，不代表 DB/子进程生命周期 | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/contracts/test_egress_budget.py::test_snapshot_builders_freeze_normalized_policy"] | . | 300 | P0 | auto | none | tests/contracts/test_egress_budget.py | - | - | no |

## TASK-001: 配置解析与冻结快照

- **Status**: done
- **Priority**: P0
- **Depends**: -
- **Source**: egress-rate-sdk.design.md#3.5 配置契约与优先级（本轮技术建议）
- **Spec-Refs**: harness-snapshot#RULE-snapshot-001
- **Rule-Owner-Refs**: harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: S-05, B-04, harness-snapshot#RULE-snapshot-001
- **Estimate**: 1.5d
- **Capability**: 可从 Agent JSON 得到有效预算，Run/Task 恢复沿用阈值且明确新段计数

### Description

核对当前 contracts/source/sandbox 和预算字段，冻结 typed 配置；覆盖默认合并、null、非法输入；不新增表或配置页面。

### Checklist

- [x] 先将 Plan 骨架替换为真实断言，功能缺失时取 RED，实施后取 GREEN；不得把 pytest.fail 占位失败作为功能验证
- [x] 核对当前 contracts/source/sandbox 和预算字段，冻结 typed 配置；覆盖默认合并、null、非法输入；不新增表或配置页面
- [x] [B-04][contract] 真实 Python SDK/配置契约对象，可注入时钟和 observer；不代表产品执行证据；断言：Skill 按字段覆盖默认，父预算继续约束；空有效规则旁路；非法预算在发送前失败，不能回退为无限制
- [x] [RULE-snapshot-001] verifier: ["bash", "-lc", "uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]；cwd=.；timeout=600；由 TASK-001 负责，不以收集代执行

- [x] [S-05][contract] 真实快照构建与读回，断言旧值冻结、新值新 hash、Task budget 归属

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-04 | contract | 真实 Python SDK/配置契约对象，可注入时钟和 observer；不代表产品执行证据 | Skill 按字段覆盖默认，父预算继续约束；空有效规则旁路；非法预算在发送前失败，不能回退为无限制 | tests/contracts/test_egress_budget.py | ["uv", "run", "pytest", "-q", "tests/contracts/test_egress_budget.py"] | verified |
| harness-snapshot#RULE-snapshot-001 | integration | Spec 所声明 verifier 的真实组件边界；不得用 SDK mock 替代 | 每个新 Run/Task 在执行前冻结 RuntimeSnapshot/execution snapshot（Agent/Model/Skill/MCP 版本、Prompt 模板版本、catalog revision/hash；**预算只属 execution snapshot 的 `budget` 键**——Run 侧的 `RuntimeSnapshot` 无 budget 列，等价载体是 `policy_json`，默认含 `max_model_retries`，预算与压缩策略等执行期策略一并冻结在此键）；配置或授权变更只影响后续新 Run/Task；终态写入必须 CAS。 | harness-snapshot#RULE-snapshot-001 | ["bash", "-lc", "uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] | verified |

| S-05 | contract | 真实 Runtime build_snapshot/execution_defaults_of 与 contracts build_task_snapshot；只验证构建读回契约，不代表 DB/子进程生命周期 | 两类快照携带相同有效预算；Task 预算只在 budget.egress_budget，Run 在 policy_json.egress_budget；旧快照独立于修改后的 Agent 配置，新快照采用新值并改变 hash；读回不重新 resolve 或读取控制面 | tests/contracts/test_egress_budget.py::test_snapshot_builders_freeze_normalized_policy | ["uv", "run", "pytest", "-q", "tests/contracts/test_egress_budget.py::test_snapshot_builders_freeze_normalized_policy"] | verified |

### Acceptance Evidence

| 范围 | RED | GREEN | 断言位置/真实边界 | 状态 |
|---|---|---|---|---|
| B-04 配置契约 | 真实断言 14 failed：muad_contracts.egress_budget 模块缺失 | uv run pytest -q tests/contracts/test_egress_budget.py：16 passed（包含快照构建验证） | defaults/override/null/非法输入/窗口字段继承均在测试文件显式断言；无网络替身 | 定向通过，尚未替换 manifest 原测试入口 |
| Run/Task 快照构建 | 解析模块缺失 RED 已记录；快照附加断言未单独取 RED | 同套件 test_snapshot_builders_freeze_normalized_policy 通过 | 真实 Runtime build_snapshot、contracts build_task_snapshot 与 execution_defaults_of；未启动服务、DB 或 Skill 子进程 | 构建/读回证据，不能替代 S-04 integration |
| S-04 | 未执行 | 未执行 | 所需 controller/真实进程生命周期接线属后续能力；原验收层级保留 | pending |

实现文件：contracts/egress_budget.py；contracts/snapshot.py；Runtime run_service.py/executor.py。没有新增表/服务/前端，SDK 核心与发送接线尚未实现。mypy 新模块通过，5 个改动 Python 文件 Ruff 通过。

计划缺口：S-04 的完整计数/进程断言依赖 TASK-002/005，但目前被分配为 TASK-001 完成门槛。不能把快照构建单测代替该 integration；TASK-001 保留 in-progress，未运行 finish，不声明 Done Gate 通过。下一步须修正任务责任依赖（保留 S-04 完整边界和最终验收）再推进后续实现。
- B-04: verified — automated command passed; run_id=aa3d57e6470b4444a2302a91727c35b9 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=aa3d57e6470b4444a2302a91727c35b9 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=7437290979024480bbb006991a5a90f6 (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=7437290979024480bbb006991a5a90f6 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=7615cbf8cfd64b3c878232f584cb588e (confirmed_by: runner)
- S-05: verified — automated command passed; run_id=7615cbf8cfd64b3c878232f584cb588e (confirmed_by: runner)

### Log

- [2026-11-08] created (draft)
- [2026-11-08] started
- [2026-11-08] completed (done)



计划修正记录：用户要求继续后，将原 S-04 完整移交 TASK-005；新增 S-05，验证配置与快照构建。此前的依赖倒置已解除，原 S-04 历史 pending 证据保留，不冒充通过。


最新验证证据（2026-11-08）：

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-04 | 初始模块缺失 14 failed；追加目标歧义/重复 ID 测试 2 failed、24 passed | 26 passed；acceptance runner 无缓存复跑 | tests/contracts/test_egress_budget.py 的配置、窗口继承、协议互斥、重复规则断言 | 真实 Pydantic 模型，无传输替身 | verified |
| S-05 | 负向对照移除 Task budget.egress_budget 键后真实快照测试失败；逐字节还原代码 | 真实快照契约和 acceptance runner 复跑通过 | test_snapshot_builders_freeze_normalized_policy | Runtime build_snapshot/execution_defaults_of + contracts build_task_snapshot | verified |
| harness-snapshot#RULE-snapshot-001 | 现有 verifier 回归无需制造功能 RED；原环境 CREATEDB 不足已定位并隔离处理 | 原 verifier 命令完整执行：第一组 4 passed，第二组 39 passed、1176 deselected | tests/agent_runtime/test_snapshot_freeze.py、test_run_reaper.py 和 executor/resolve 选择集 | 临时 PostgreSQL 容器，schema 迁移到 head；Redis 独占号位，无共享开发库替代 | verified |

轻量 validators：7 项通过。临时数据库将仅用于本任务 Done Gate，随后清理；不改开发账号权限。旧 pending 描述是历史执行记录，以本节和 runner 最新状态为准；S-04 仍由 TASK-005 承担完整集成验收。

## TASK-002: 本地预算控制器与观测

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: egress-rate-sdk.design.md#3.7 原子计数与发送租约
- **Spec-Refs**: harness-log#RULE-log-001, harness-secret#RULE-secret-001
- **Rule-Owner-Refs**: harness-log#RULE-log-001, harness-secret#RULE-secret-001
- **Acceptance-Refs**: S-01, E-01, B-01, B-02, B-05, E-04, harness-log#RULE-log-001, harness-secret#RULE-secret-001
- **Estimate**: 1.5d
- **Capability**: 进程内原子控制累计/窗口/并发，无预算旁路，observer 失败可见

### Description

单 controller 管父子规则；单调窗口；幂等同步释放；安全错误及 observer，限制检查无外部 IO。

### Checklist

- [x] 先将 Plan 骨架替换为真实断言，功能缺失时取 RED，实施后取 GREEN；不得把 pytest.fail 占位失败作为功能验证
- [x] 单 controller 管父子规则；单调窗口；幂等同步释放；安全错误及 observer，限制检查无外部 IO
- [x] [S-01][unit] 真实 Python SDK 对象与可注入单调时钟；断言：每次 acquire 代表一次真实发送尝试，失败/取消/Proxy 429 不返还次数；正常关闭释放 inflight；错误和 callback 只有 scope/target/limit 元数据，不含 header/body/token/cookie
- [x] [E-01][unit] 真实 SDK 对象与 fake send counter，不发送真实网络；断言：返回结构化 LocalRateLimitError，底层发送调用数为零；规则失败不部分扣减；并发释放后可再次获取；拒绝不会增加请求次数
- [x] [B-01][unit] 真实 SDK 对象与可注入时钟；断言：任一规则不足时 lease 不成立，其他计数/占用保持不变；全部满足才一次提交；相同 scope 的并发调用不超过本地规则
- [x] [B-02][unit] 真实 SDK 装配对象，不连接 Redis/PG；断言：请求走原有发送路径，不创建计数结构、不访问外部计数依赖、不增加默认限制；Proxy 返回结果仍由原客户端处理
- [x] [B-05][unit] 真实 Python SDK/配置契约对象，可注入时钟和 observer；不代表产品执行证据；断言：query 不新建桶，所有命中规则共同生效，握手不冒充 Tool；窗口开左闭右，不受墙钟回拨影响，拒绝不部分扣减
- [x] [E-04][unit] 真实 Python SDK/配置契约对象，可注入时钟和 observer；不代表产品执行证据；断言：observer 失败可见，不重复发送业务请求或将拒绝变放行；lease 正确释放，日志不含头/正文/凭据
- [x] [RULE-log-001] verifier: ["uv", "run", "pytest", "-q", "tests/test_logging.py", "tests/test_logging_redaction.py", "tests/acceptance/test_foundation_logging.py"]；cwd=.；timeout=300；由 TASK-002 负责，不以收集代执行
- [x] [RULE-secret-001] verifier: ["uv", "run", "pytest", "-q", "tests/test_logging_redaction.py", "tests/acceptance/test_foundation_ops_audit.py"]；cwd=.；timeout=300；由 TASK-002 负责，不以收集代执行

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | unit | 真实 Python SDK 对象与可注入单调时钟 | 每次 acquire 代表一次真实发送尝试，失败/取消/Proxy 429 不返还次数；正常关闭释放 inflight；错误和 callback 只有 scope/target/limit 元数据，不含 header/body/token/cookie | tests/sdk/test_request_budget.py | ["uv", "run", "pytest", "-q", "tests/sdk/test_request_budget.py::test_s_01_requests_are_consumed_on_error_and_cancel", "tests/sdk/test_request_budget.py::test_s_01_upstream_limit_still_consumes_a_request"] | verified |
| E-01 | unit | 真实 SDK 对象与 fake send counter，不发送真实网络 | 返回结构化 LocalRateLimitError，底层发送调用数为零；规则失败不部分扣减；并发释放后可再次获取；拒绝不会增加请求次数 | tests/sdk/test_request_budget.py | ["uv", "run", "pytest", "-q", "tests/sdk/test_request_budget.py::test_e_01_concurrency_releases_only_its_own_lease", "tests/sdk/test_request_budget.py::test_invalid_context_cannot_bypass_target_matching", "tests/sdk/test_request_budget.py::test_invocation_from_other_controller_is_rejected"] | verified |
| B-01 | unit | 真实 SDK 对象与可注入时钟 | 任一规则不足时 lease 不成立，其他计数/占用保持不变；全部满足才一次提交；相同 scope 的并发调用不超过本地规则 | tests/sdk/test_request_budget.py | ["uv", "run", "pytest", "-q", "tests/sdk/test_request_budget.py::test_b_01_atomic_parent_and_child_rules", "tests/sdk/test_request_budget.py::test_b_01_concurrent_reservations_do_not_overshoot", "tests/sdk/test_request_budget.py::test_overlapping_target_denial_does_not_debit_parent"] | verified |
| B-02 | unit | 真实 SDK 装配对象，不连接 Redis/PG | 请求走原有发送路径，不创建计数结构、不访问外部计数依赖、不增加默认限制；Proxy 返回结果仍由原客户端处理 | tests/sdk/test_request_budget.py | ["uv", "run", "pytest", "-q", "tests/sdk/test_request_budget.py::test_b_02_no_limits_does_not_construct_a_controller", "tests/sdk/test_request_budget.py::test_unmatched_rules_do_not_consume_or_call_observer"] | verified |
| B-05 | unit | 真实 Python SDK/配置契约对象，可注入时钟和 observer；不代表产品执行证据 | query 不新建桶，所有命中规则共同生效，握手不冒充 Tool；窗口开左闭右，不受墙钟回拨影响，拒绝不部分扣减 | tests/sdk/test_request_budget.py | ["uv", "run", "pytest", "-q", "tests/sdk/test_request_budget.py::test_b_05_matching_and_exact_window_boundary", "tests/sdk/test_request_budget.py::test_b_05_mcp_handshake_does_not_match_a_tool", "tests/sdk/test_request_budget.py::test_b_05_query_and_encoded_identifier_do_not_create_new_buckets"] | verified |
| E-04 | unit | 真实 Python SDK/配置契约对象，可注入时钟和 observer；不代表产品执行证据 | observer 失败可见，不重复发送业务请求或将拒绝变放行；lease 正确释放，日志不含头/正文/凭据 | tests/sdk/test_request_budget.py | ["uv", "run", "pytest", "-q", "tests/sdk/test_request_budget.py::test_e_04_observer_failure_does_not_resend_or_leak", "tests/sdk/test_request_budget.py::test_observer_cancellation_cleans_the_acquired_lease", "tests/sdk/test_request_budget.py::test_e_04_diagnostic_outcome_cannot_carry_arbitrary_secret_text"] | verified |
| harness-log#RULE-log-001 | integration | Spec 所声明 verifier 的真实组件边界；不得用 SDK mock 替代 | 所有服务统一使用 logging-kit，仅配置 `LOG_DIR`（另读 `LOG_LEVEL`，缺省 `INFO`；非法级别直接 `raise ValueError` 而非静默回退；`console=True` 为默认值，会额外挂一个 stdout handler）；日志按 `service/YYYY-MM-DD.log` 输出**单行文本**：`YYYY-MM-DD HH:mm:ss.SSS ±ZZZZ [LEVEL] service logger message / key=value ...`，自动携带 `trace_id/request_id/tenant_id` 等关联字段（空串不打印），并对 Authorization/Cookie/api_key/access_token/refresh_token/secret/password 等敏感字段脱敏。 | harness-log#RULE-log-001 | ["uv", "run", "pytest", "-q", "tests/test_logging.py", "tests/test_logging_redaction.py", "tests/acceptance/test_foundation_logging.py"] | verified |
| harness-secret#RULE-secret-001 | integration | Spec 所声明 verifier 的真实组件边界；不得用 SDK mock 替代 | 密钥明文存于各 Owner 表（模型 `control.model_credential.api_key`、Bot `secret`、MCP `auth_secret`、用户/共享凭据 `credential_json`；平台侧只有历史休眠列 `project_platform.auth_secret`，不得读写），跨表以主键引用，不再使用 `secret_ref`/SecretProvider；密钥不得进入日志、`config_audit_log`、Snapshot、LLM Prompt 或 API 响应（对外以 `*_configured` 表达）。 | harness-secret#RULE-secret-001 | ["uv", "run", "pytest", "-q", "tests/test_logging_redaction.py", "tests/acceptance/test_foundation_ops_audit.py"] | verified |

### Acceptance Evidence

本阶段仅登记计划，无实现/验收证据。Start 后记录真实 RED/GREEN、断言位置与真实边界；占位体必须移除。
- S-01: verified — automated command passed; run_id=ac841a12f3134c4fba231974c9b586d8 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=ac841a12f3134c4fba231974c9b586d8 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=ac841a12f3134c4fba231974c9b586d8 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=ac841a12f3134c4fba231974c9b586d8 (confirmed_by: runner)
- B-05: verified — automated command passed; run_id=ac841a12f3134c4fba231974c9b586d8 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=ac841a12f3134c4fba231974c9b586d8 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=6b9f513112a949d98687302bd5ba7eb3 (confirmed_by: runner)
- E-01: verified — automated command passed; run_id=6b9f513112a949d98687302bd5ba7eb3 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=6b9f513112a949d98687302bd5ba7eb3 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=6b9f513112a949d98687302bd5ba7eb3 (confirmed_by: runner)
- B-05: verified — automated command passed; run_id=6b9f513112a949d98687302bd5ba7eb3 (confirmed_by: runner)
- E-04: verified — automated command passed; run_id=6b9f513112a949d98687302bd5ba7eb3 (confirmed_by: runner)

### Log

- [2026-11-08] created (draft)
- [2026-11-08] started
- [2026-11-08] completed (done)


最新验证证据（2026-11-08）：

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| S-01 | 初始真实测试因 request_budget 缺失失败 | runner passed；直接发送失败/取消/上游限流不返还次数断言通过 | tests/sdk/test_request_budget.py::test_s_01_requests_are_consumed_on_error_and_cancel、test_s_01_upstream_limit_still_consumes_a_request | 真实 SDK/事件回调，不发网络 | verified |
| E-01 | 初始缺模块；追加并发释放发现总占用未同步失败，已修复 | runner passed | test_e_01_concurrency_releases_only_its_own_lease、test_invalid_context_cannot_bypass_target_matching、test_invocation_from_other_controller_is_rejected | 真实 lease、可信 scope 与错误对象 | verified |
| B-01 | 初始缺模块；负向移除 check 后 20 个请求全放行、configured=3 的断言失败，逐字节还原后通过 | runner passed | 两个 test_b_01 和 test_overlapping_target_denial_does_not_debit_parent | asyncio 并发真实控制器，无存储替身 | verified |
| B-02 | 初始缺模块 | runner passed | test_b_02_no_limits_does_not_construct_a_controller、test_unmatched_rules_do_not_consume_or_call_observer | 无预算返回 None、未命中不计数也不调用 observer | verified |
| B-05 | 初始缺模块 | runner passed | 三个 test_b_05 | 注入单调时钟、query/编码路径/动态段、MCP 握手匹配 | verified |
| E-04 | 回调取消时 lease 泄漏测试真实失败，已修复 | runner passed | test_e_04_observer_failure_does_not_resend_or_leak、test_observer_cancellation_cleans_the_acquired_lease、test_e_04_diagnostic_outcome_cannot_carry_arbitrary_secret_text | 日志捕获无原始异常/路径秘密；本地 observer_failures 记录失败 | verified |
| harness-log#RULE-log-001 | 新观测约定沿用既有日志，无需伪造旧功能 RED | 合并回归 17 passed，包含原 verifier 全部用例；finish 独立执行原命令 | tests/test_logging.py、test_logging_redaction.py、tests/acceptance/test_foundation_logging.py | 既有 logging-kit/真实验收基础设施 | verified |
| harness-secret#RULE-secret-001 | 新事件安全字段已取真实功能 RED | 同组合回归通过，包含原 verifier 全部用例；finish 独立执行原命令 | tests/test_logging_redaction.py、tests/acceptance/test_foundation_ops_audit.py | 既有脱敏与审计真实边界 | verified |

核心/配置定向套件总计 42 passed；Ruff/mypy 与轻量 validators 通过。observer 失败在核心暴露本地 observer_failures 计数与 warning，应用 metrics 的装配仍由后续发送接线任务负责。此任务没有修改平台/MCP/legacy Skill 发送路径，不能据此宣称业务出站已全部限流。

## TASK-003: 平台发送与 SDK 客户端接线

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: egress-rate-sdk.design.md#3.8 接线点与停止信号
- **Spec-Refs**: harness-project-platform#RULE-platform-001
- **Rule-Owner-Refs**: harness-project-platform#RULE-platform-001
- **Acceptance-Refs**: S-02, B-03, harness-project-platform#RULE-platform-001
- **Estimate**: 2d
- **Capability**: 认证/校验/重试和显式 SDK 业务发送受控，直连旧脚本边界可验证

### Description

保持只读 Session broker；Adapter send 显式传上下文；受控 SDK 请求逐次计数；不恢复业务 IPC，不修改任意第三方 HTTP 库。

### Checklist

- [ ] 先将 Plan 骨架替换为真实断言，功能缺失时取 RED，实施后取 GREEN；不得把 pytest.fail 占位失败作为功能验证
- [ ] 保持只读 Session broker；Adapter send 显式传上下文；受控 SDK 请求逐次计数；不恢复业务 IPC，不修改任意第三方 HTTP 库
- [ ] [S-02][integration] 真实 Runtime/Worker + 本地 HTTP 探针 + PlatformAdapter，不使用 mock transport；断言：登录、校验、业务和重试都在实际 send 前经过 gate；超限时对应请求未发出；每次真实 HTTP 尝试只计一次，既有认证/Session 语义仍成立
- [ ] [B-03][integration] 真实慢 HTTP/流式探针与 Runtime 发送器；断言：流关闭前不释放；取消、异常、正常关闭均释放一次且重复 close 不误释放其他 lease
- [ ] [RULE-platform-001] verifier: ["bash", "-lc", "uv run pytest -q tests -k schema_parity"]；cwd=.；timeout=300；由 TASK-003 负责，不以收集代执行

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | integration | 真实 Runtime/Worker + 本地 HTTP 探针 + PlatformAdapter，不使用 mock transport | 登录、校验、业务和重试都在实际 send 前经过 gate；超限时对应请求未发出；每次真实 HTTP 尝试只计一次，既有认证/Session 语义仍成立 | .code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_s_02 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_s_02"] | planned |
| B-03 | integration | 真实慢 HTTP/流式探针与 Runtime 发送器 | 流关闭前不释放；取消、异常、正常关闭均释放一次且重复 close 不误释放其他 lease | .code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_b_03 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_b_03"] | planned |
| harness-project-platform#RULE-platform-001 | integration | Spec 所声明 verifier 的真实组件边界；不得用 SDK mock 替代 | ProjectPlatform 只保存实例与寻址配置，认证/Session 由 PlatformAdapter 承担（SPI 无独立 `refresh`，刷新属于 `authenticate` 内部）；凭据明文存于凭据表并由主键引用，`credential_mode` 仅表达选择策略（USER_ONLY/SHARED_ONLY/USER_THEN_SHARED/NONE）；PlatformSession 为可重建 Redis 缓存，键为 `platform_session:{tenant_id:platform_id}:{actor_scope}:{credential_version}:{adapter_key}:{adapter_version}`（`{tenant_id:platform_id}` 是字面 hash tag，session/index/generation/lock 同 slot，保证 4-key Lua 在 Redis Cluster 可执行），索引 `platform_sessions:{tenant_id:platform_id}` 以 Set 清理；更换 adapter_key 必须使旧凭据与会话失效。 | harness-project-platform#RULE-platform-001 | ["bash", "-lc", "uv run pytest -q tests -k schema_parity"] | planned |

### Acceptance Evidence

本阶段仅登记计划，无实现/验收证据。Start 后记录真实 RED/GREEN、断言位置与真实边界；占位体必须移除。

### Log

- [2026-11-08] created (draft)

## TASK-004: MCP 与上游错误处理

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: egress-rate-sdk.design.md#3.9 本地与上游错误契约
- **Spec-Refs**: harness-mcp#RULE-mcp-001
- **Rule-Owner-Refs**: harness-mcp#RULE-mcp-001
- **Acceptance-Refs**: S-03, harness-mcp#RULE-mcp-001
- **Estimate**: 1d
- **Capability**: MCP 握手/Tool 每次发送受控，安全保留上游 429 与重试头

### Description

保留 singleflight/请求 ID/会话清理；HTTP status 与 Retry-After 结构化携带；不新增自动重试。

### Checklist

- [ ] 先将 Plan 骨架替换为真实断言，功能缺失时取 RED，实施后取 GREEN；不得把 pytest.fail 占位失败作为功能验证
- [ ] 保留 singleflight/请求 ID/会话清理；HTTP status 与 Retry-After 结构化携带；不新增自动重试
- [ ] [S-03][integration] 真实 Runtime + Streamable HTTP MCP 探针；断言：每个实际 MCP HTTP 发送分别计次，本地超限不外发，现有授权保持；上游 HTTP 状态和 Retry-After 通过安全结构化字段保留，不误标本地超限，不暴露原始响应正文/秘密
- [ ] [RULE-mcp-001] verifier: ["uv", "run", "pytest", "-q", "tests/console_mcp/test_mcp_rules.py"]；cwd=.；timeout=300；由 TASK-004 负责，不以收集代执行

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | integration | 真实 Runtime + Streamable HTTP MCP 探针 | 每个实际 MCP HTTP 发送分别计次，本地超限不外发，现有授权保持；上游 HTTP 状态和 Retry-After 通过安全结构化字段保留，不误标本地超限，不暴露原始响应正文/秘密 | .code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_s_03 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_s_03"] | planned |
| harness-mcp#RULE-mcp-001 | integration | Spec 所声明 verifier 的真实组件边界；不得用 SDK mock 替代 | V1 仅支持 Streamable HTTP；Tool Catalog 由 `discover-tools` 唯一维护并落 PostgreSQL；用户范围仅 Server 级（ALL/SELECTED），不做 Tool 级启停或授权；MCP Tool 必须进入统一 ToolRegistry。 | harness-mcp#RULE-mcp-001 | ["uv", "run", "pytest", "-q", "tests/console_mcp/test_mcp_rules.py"] | planned |

### Acceptance Evidence

本阶段仅登记计划，无实现/验收证据。Start 后记录真实 RED/GREEN、断言位置与真实边界；占位体必须移除。

### Log

- [2026-11-08] created (draft)

## TASK-005: 脚本停止、生命周期与需求验收

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004
- **Source**: egress-rate-sdk.design.md#3.6 作用域、匹配与恢复
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-skill#RULE-skill-001, harness-test#RULE-test-001, harness-worker#RULE-worker-001
- **Rule-Owner-Refs**: harness-arch#RULE-arch-001, harness-skill#RULE-skill-001, harness-test#RULE-test-001, harness-worker#RULE-worker-001
- **Acceptance-Refs**: E-02, E-03, S-04, harness-arch#RULE-arch-001, harness-skill#RULE-skill-001, harness-test#RULE-test-001, harness-worker#RULE-worker-001
- **Estimate**: 2d
- **Capability**: 超额受控请求通知父进程停止脚本，真实进程/恢复链和规范门禁完成闭合

### Description

第 N 次完成，第 N+1 次阻止并回收进程组；Task 确定性失败不自动重试；跨段新计数标识；收口 inventory/负向对照/CI 验收。

### Checklist

- [ ] [S-04][integration] 真实 Runtime/Worker、Skill 子进程和既有快照/租约边界；断言：策略始终沿用冻结值；同段脚本共用 execution 计数，各 invocation 有独立子计数；retry/resume 建立新段和新本地计数，并记录 segment 身份；不声称跨段累计硬上限；脚本输入不能改变可信预算/身份；已知预算耗尽不自动重试

- [ ] 先将 Plan 骨架替换为真实断言，功能缺失时取 RED，实施后取 GREEN；不得把 pytest.fail 占位失败作为功能验证
- [ ] 第 N 次完成，第 N+1 次阻止并回收进程组；Task 确定性失败不自动重试；跨段新计数标识；收口 inventory/负向对照/CI 验收
- [ ] [E-02][integration] 真实 Skill 子进程组、Runtime/Worker 和 HTTP 探针；沿用 start_new_session/killpg；断言：第 N 次正常完成；第 N+1 次被拦截，通知父进程回收 invocation 整个进程组及未完成请求，不自动重试；已有外部操作保留，Run 可汇报部分结果，Task 按确定性错误收尾
- [ ] [E-03][integration] 两个独立 Runtime/Worker 进程 + 真实 Proxy HTTP 探针，不使用共享 limiter 存储；断言：新进程不宣称恢复旧进程计数，上游 429 可被上层识别；两个进程各有本地计数；只验证上游响应处理，不宣称验证 Proxy 全局算法
- [ ] [RULE-arch-001] verifier: ["uv", "run", "pytest", "-q", "tests/architecture"]；cwd=.；timeout=300；由 TASK-005 负责，不以收集代执行
- [ ] [RULE-skill-001] verifier: ["uv", "run", "pytest", "-q", "tests/test_skill_artifact_cache.py"]；cwd=.；timeout=300；由 TASK-005 负责，不以收集代执行
- [ ] [RULE-test-001] verifier: ["bash", "-lc", "uv run python scripts/ci_validation_cache.py --validator \"Pytest (acceptance)\" --json && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]；cwd=.；timeout=2700；由 TASK-005 负责，不以收集代执行
- [ ] [RULE-worker-001] verifier: ["bash", "-lc", "uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"]；cwd=.；timeout=1800；由 TASK-005 负责，不以收集代执行
- [ ] 建立 tests/egress_rate_sdk_inventory.py，校验全部 13 场景、9 条 Spec Rule、manifest/owner/执行证据与实际用例；至少四类扰动负向取证后还原
- [ ] 根据实际改动补配置 inventory/CI 分片登记；按项目隔离规范完成需求级验收及 review，先读取 finish 实际裁决再写提交结论

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-02 | integration | 真实 Skill 子进程组、Runtime/Worker 和 HTTP 探针；沿用 start_new_session/killpg | 第 N 次正常完成；第 N+1 次被拦截，通知父进程回收 invocation 整个进程组及未完成请求，不自动重试；已有外部操作保留，Run 可汇报部分结果，Task 按确定性错误收尾 | .code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_e_02 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_e_02"] | planned |
| E-03 | integration | 两个独立 Runtime/Worker 进程 + 真实 Proxy HTTP 探针，不使用共享 limiter 存储 | 新进程不宣称恢复旧进程计数，上游 429 可被上层识别；两个进程各有本地计数；只验证上游响应处理，不宣称验证 Proxy 全局算法 | .code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_e_03 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_e_03"] | planned |
| harness-arch#RULE-arch-001 | integration | Spec 所声明 verifier 的真实组件边界；不得用 SDK mock 替代 | 固定四个**逻辑业务部署单元**（console-platform / agent-runtime / agent-worker / im-gateway）；`im-gateway` 可拆为无状态 web 与有状态 connector 两个运行时角色，connector 不是第五个业务微服务。Runtime 与 Worker 无状态、可横向扩展，不绑定 Pod、bot_id 或用户；同一会话可被任意 Pod 执行；Connector 只持有 bot 传输所有权与回复上下文，不建立 agent→Pod 映射。 | harness-arch#RULE-arch-001 | ["uv", "run", "pytest", "-q", "tests/architecture"] | planned |
| harness-skill#RULE-skill-001 | integration | Spec 所声明 verifier 的真实组件边界；不得用 SDK mock 替代 | Artifact Store 为 **RWX PVC**（硬事实是 `accessModes: ReadWriteMany`，`deploy/k8s/base/pvc-artifacts.yaml`；是否落在 NFS 由部署侧 StorageClass 决定，清单里 `storageClassName` 并未固定；V1 不建 MinIO/S3）；数据库只保存相对 `storage_key`；Runtime/Worker 使用 emptyDir 本地缓存并在校验 checksum 后原子切换 READY；禁止直接从 NFS 目录执行 Python Skill；同一 checksum 并发首次加载必须 singleflight（**限每进程内**：跨 Pod 的正确性由 `os.replace` 幂等与 DB CAS 承载，不得当作跨进程互斥）；Artifact 写入不可变（**目标不存在才发布**：临时文件 + `os.link`，同 `storage_key` 二次写入抛 `FileExistsError`）；导入 DB 事务失败同步删除已写文件，进程级孤儿由 `cleanup-skill-orphans` CLI 宽限期扫描清理（宽限期保护进行中事务）。 | harness-skill#RULE-skill-001 | ["uv", "run", "pytest", "-q", "tests/test_skill_artifact_cache.py"] | planned |
| harness-test#RULE-test-001 | integration | Spec 所声明 verifier 的真实组件边界；不得用 SDK mock 替代 | 跨 API/DB/Runtime/Browser 的关键流程必须 E2E 且明确“不得 mock 的真实边界”（真实 PostgreSQL、真实 Redis 行为、真实 HTTP、真实浏览器渲染）；单元测试覆盖纯逻辑与状态机，契约测试覆盖枚举/错误码/迁移一致性。 | harness-test#RULE-test-001 | ["bash", "-lc", "uv run python scripts/ci_validation_cache.py --validator \"Pytest (acceptance)\" --json && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | planned |
| harness-worker#RULE-worker-001 | integration | Spec 所声明 verifier 的真实组件边界；不得用 SDK mock 替代 | PostgreSQL 是 Task/Schedule/lease 的唯一权威源；Redis 仅作 wake-up/cancel hint；`task_type` 仅 `SKILL/BATCH`（外部等待用 WAITING 表达）；claim 使用 `FOR UPDATE SKIP LOCKED`，进入 WAITING 释放 lease，deadline 到期由 Scheduler sweep 置 `FAILED(TASK_DEADLINE_EXCEEDED)`。**deadline 是领取与收尾两侧的硬条件**：claim 排除 `deadline_at <= now` 的行（过期任务绝不执行），执行跨过 deadline 也不得写 `COMPLETED`，同样以 `FAILED(TASK_DEADLINE_EXCEEDED)` 收尾（`worker/claimer.py` 的 `_claimable_conditions`、`worker/service.py` 的终态守卫）。 | harness-worker#RULE-worker-001 | ["bash", "-lc", "uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"] | planned |

| S-04 | integration | 真实 Runtime/Worker、Skill 子进程和既有快照/租约边界 | 策略始终沿用冻结值；同段脚本共用 execution 计数，各 invocation 有独立子计数；retry/resume 建立新段和新本地计数，并记录 segment 身份；不声称跨段累计硬上限；脚本输入不能改变可信预算/身份；已知预算耗尽不自动重试 | .code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_s_04 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-06/egress-rate-sdk/planned_tests/test_egress_rate_sdk_acceptance.py::test_s_04"] | planned |

### Acceptance Evidence

本阶段仅登记计划，无实现/验收证据。Start 后记录真实 RED/GREEN、断言位置与真实边界；占位体必须移除。

### Log

- [2026-11-08] created (draft)
