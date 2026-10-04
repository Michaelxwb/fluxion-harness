# Tasks: IM 执行状态与计时

- **Source**: im-execution-progress.design.md
- **Created**: 2026-10-04
- **Updated**: 2026-10-04

## Proposal

使用已实测原生动画、图标和连续计时，展示模型/工具真实阶段；保持渠道差异隔离与有界发送。

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout |
|---|---|---|---|---|---|---|---|---|
| S-401 | im-execution-progress.design.md#2.3 验收条件 | E2E | 真实 WS → Gateway → Runtime/PG → 模型 HTTP 探针 → 原生流回复 | TASK-001 | verified | uv run pytest -q tests/acceptance/im_gateway/test_execution_progress.py | . | 300 |
| S-402 | im-execution-progress.design.md#2.3 验收条件 | integration | 真实 Runner → Executor 模型与工具事件 | TASK-001 | verified | uv run pytest -q tests/agent_runtime/test_execution_activity.py | . | 300 |
| E-401 | im-execution-progress.design.md#2.3 验收条件 | integration | 真实 Pipeline → 回复会话 → 等待/终态/断流清理 | TASK-001 | verified | uv run pytest -q tests/gateway/test_execution_progress.py | . | 300 |
| B-401 | im-execution-progress.design.md#2.3 验收条件 | unit | 进度状态机与有界异步调度 | TASK-001 | verified | uv run pytest -q tests/gateway/test_execution_progress.py | . | 300 |
| B-402 | im-execution-progress.design.md#2.3 验收条件 | integration | 真实适配器 → 本地 TLS WS → 官方 SDK | TASK-001 | verified | uv run pytest -q tests/gateway/test_execution_progress.py | . | 300 |


## TASK-001: 实现执行事件、回复会话和有界进度展示

- **Status**: verified
- **Priority**: P0
- **Depends**:
- **Source**: im-execution-progress.design.md#3.2 架构设计, im-execution-progress.design.md#3.3 接口设计, im-execution-progress.design.md#2.3 验收条件
- **Spec-Refs**: harness-api#RULE-api-002, harness-api#RULE-api-001, harness-im#RULE-im-001, harness-im#RULE-im-002, harness-snapshot#RULE-snapshot-001, harness-test#RULE-test-001, harness-arch#RULE-arch-001, harness-model#RULE-model-001
- **Acceptance-Refs**: S-401, S-402, E-401, B-401, B-402

### Description

实现 FEAT-01..04，既有 snapshot/终态 CAS/幂等链路继续作为唯一执行权威。

### Checklist

- [x] [S-401][E2E] 先登记并编写 tests/acceptance/im_gateway/test_execution_progress.py；真实边界：真实 WS → Gateway → Runtime/PG → 模型 HTTP 探针 → 原生流回复；验证设计约定并登记证据，E2E 延后 verify-e2e
- [x] [S-402][integration] 先登记并编写 tests/agent_runtime/test_execution_activity.py；真实边界：真实 Runner → Executor 模型与工具事件；验证设计约定并登记证据，记录 RED/GREEN
- [x] [E-401][integration] 先登记并编写 tests/gateway/test_execution_progress.py；真实边界：真实 Pipeline → 回复会话 → 等待/终态/断流清理；验证设计约定并登记证据，记录 RED/GREEN
- [x] [B-401][unit] 先登记并编写 tests/gateway/test_execution_progress.py；真实边界：进度状态机与有界异步调度；验证设计约定并登记证据，记录 RED/GREEN
- [x] [B-402][integration] 先登记并编写 tests/gateway/test_execution_progress.py；真实边界：真实适配器 → 本地 TLS WS → 官方 SDK；验证设计约定并登记证据，记录 RED/GREEN
- [x] verifier harness-api#RULE-api-002：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-api#RULE-api-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-im#RULE-im-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-im#RULE-im-002：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-snapshot#RULE-snapshot-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-test#RULE-test-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-arch#RULE-arch-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决
- [x] verifier harness-model#RULE-model-001：执行规范元数据的原始命令，保持规范责任，记录门禁裁决

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-401 | E2E | 真实 WS → Gateway → Runtime/PG → 模型 HTTP 探针 → 原生流回复 | 设计 2.3 对应结果 | tests/acceptance/im_gateway/test_execution_progress.py | uv run pytest -q tests/acceptance/im_gateway/test_execution_progress.py | verified |
| S-402 | integration | 真实 Runner → Executor 模型与工具事件 | 设计 2.3 对应结果 | tests/agent_runtime/test_execution_activity.py | uv run pytest -q tests/agent_runtime/test_execution_activity.py | verified |
| E-401 | integration | 真实 Pipeline → 回复会话 → 等待/终态/断流清理 | 设计 2.3 对应结果 | tests/gateway/test_execution_progress.py | uv run pytest -q tests/gateway/test_execution_progress.py | verified |
| B-401 | unit | 进度状态机与有界异步调度 | 设计 2.3 对应结果 | tests/gateway/test_execution_progress.py | uv run pytest -q tests/gateway/test_execution_progress.py | verified |
| B-402 | integration | 真实适配器 → 本地 TLS WS → 官方 SDK | 设计 2.3 对应结果 | tests/gateway/test_execution_progress.py | uv run pytest -q tests/gateway/test_execution_progress.py | verified |


### Acceptance Evidence

functional 尚未执行；S-401 在编码后登记 e2e_deferred，并由 verify-e2e 实跑。
- S-401: e2e_deferred — automated command e2e_deferred; run_id=f66779417d5f4aaba101885d74344aa6 (confirmed_by: runner)
- S-402: failed — automated command failed; run_id=f66779417d5f4aaba101885d74344aa6 (confirmed_by: runner)
- E-401: verified — automated command passed; run_id=f66779417d5f4aaba101885d74344aa6 (confirmed_by: runner)
- B-401: verified — automated command passed; run_id=f66779417d5f4aaba101885d74344aa6 (confirmed_by: runner)
- B-402: verified — automated command passed; run_id=f66779417d5f4aaba101885d74344aa6 (confirmed_by: runner)
- S-401: e2e_deferred — automated command e2e_deferred; run_id=d5b87f3593d54e2bbd8c55629c20bd49 (confirmed_by: runner)
- S-402: verified — automated command passed; run_id=d5b87f3593d54e2bbd8c55629c20bd49 (confirmed_by: runner)
- E-401: verified — automated command passed; run_id=d5b87f3593d54e2bbd8c55629c20bd49 (confirmed_by: runner)
- B-401: verified — automated command passed; run_id=d5b87f3593d54e2bbd8c55629c20bd49 (confirmed_by: runner)
- B-402: verified — automated command passed; run_id=d5b87f3593d54e2bbd8c55629c20bd49 (confirmed_by: runner)
- S-401: e2e_deferred — automated command e2e_deferred; run_id=bd719fb1d0e94520a57a2ab8e56bfced (confirmed_by: runner)
- S-402: verified — automated command passed; run_id=bd719fb1d0e94520a57a2ab8e56bfced (confirmed_by: runner)
- E-401: verified — automated command passed; run_id=bd719fb1d0e94520a57a2ab8e56bfced (confirmed_by: runner)
- B-401: verified — automated command passed; run_id=bd719fb1d0e94520a57a2ab8e56bfced (confirmed_by: runner)
- B-402: verified — automated command passed; run_id=bd719fb1d0e94520a57a2ab8e56bfced (confirmed_by: runner)
- S-401: e2e_deferred — automated command e2e_deferred; run_id=84579c343fe74a1e9aff5aa2b0903f6d (confirmed_by: runner)
- S-402: verified — automated command passed; run_id=84579c343fe74a1e9aff5aa2b0903f6d (confirmed_by: runner)
- E-401: verified — automated command passed; run_id=84579c343fe74a1e9aff5aa2b0903f6d (confirmed_by: runner)
- B-401: verified — automated command passed; run_id=84579c343fe74a1e9aff5aa2b0903f6d (confirmed_by: runner)
- B-402: verified — automated command passed; run_id=84579c343fe74a1e9aff5aa2b0903f6d (confirmed_by: runner)
- S-401: verified — automated command passed; run_id=cb5ed45adf4949bcbe8fa0b4eedf0d66 (confirmed_by: runner)
- S-401: verified — automated command passed; run_id=23a57453a88142a9967d2881f9c08595 (confirmed_by: runner)
- S-402: verified — automated command passed; run_id=23a57453a88142a9967d2881f9c08595 (confirmed_by: runner)
- E-401: verified — automated command passed; run_id=23a57453a88142a9967d2881f9c08595 (confirmed_by: runner)
- B-401: verified — automated command passed; run_id=23a57453a88142a9967d2881f9c08595 (confirmed_by: runner)
- B-402: verified — automated command passed; run_id=23a57453a88142a9967d2881f9c08595 (confirmed_by: runner)

### Log

- [2026-10-04] created (draft)；用户已在本会话授权开始编码。
- [2026-10-04] started
- [2026-10-04] code review 收口（9 条）：①产物交付按交付键里的 Run 找回**起该 Run 的那条消息**的回调（会话 `bind_run` + 交付契约传 `run_id`）；②收尾只发正文——空正文就发空帧抹掉占位，不再把状态串当回答；③非状态能力渠道保持「正文尾段先、错误文案后」的原顺序；④状态发送加有界超时（5s）并记日志；⑤上游 SSE 流按能力 `aclose`；⑥控制面探针脚本 `delay_ms` 当场校验；⑦收尾后退化路径写明并加测试。
- [2026-10-04] 复查后追加：⑧计时 tick 移出读取主路径（后台单飞、发送者忙则整条丢弃；正文/收尾写入前先让在飞的 tick 落地，保持单写者）；⑨事件落库业务名补 `MODEL_CALL_STARTED/COMPLETED`，Console Run 轮廓改按业务名过滤（token 增量剔除、模型调用边界与工具调用对称保留）；⑩补 `tests/e2e` 探针脚本契约用例，并修同名测试文件导致 `pytest tests/` 收集失败（`tests/acceptance/im_gateway/` 补包标记）。
- [2026-10-04] completed (done)
- [2026-10-04] archived：终验 verify-e2e 全 pass（S-401 走真实 WS/Gateway/Runtime/PG/探针；8 条 code 延后 verifier + 8 条 review 层各执行一次），code/review 门禁 pass，验收契约五条场景全部 verified；Checklist 与 Rule 行在归档时一并闭环。
