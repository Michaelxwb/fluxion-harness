# Tasks: Fluxion Publish / Outbox / Audit

- **Source**: docs/design/fluxion-console-design-v1.6.md
- **Created**: 2026-08-23
- **Updated**: 2026-08-24

## Proposal

把原 TASK-104 的发布可靠性部分独立出来，只负责 Publish/Rollback/Deprecate、Outbox、Redis Streams/SQLite revision 与 Audit 一致性。

### Alignment

- **Scope**: 仅实现本 TASK 的范围，不提前实现后续阶段。
- **Decisions**: 以 Architecture Baseline、Design-Refs 和 active Spec Context 为准。
- **Non-goals**: 不修改任务外核心 Contract；发现冲突使用 `#NOTES` 停止并重新对齐。
- **Acceptance**: Acceptance-Refs、required verifier、NFR Gate 与回归检查全部通过。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-C102 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Console → Registry → Event → Runtime | TASK-104 | verified |
| S-C103 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Console down → Runtime → Registry | TASK-104 | verified |
| S-C106 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Publish/Rollback → Audit Store | TASK-104 | verified |
| S-C113 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Publish → Log + Audit correlation | TASK-104 | verified |
| E-C106 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | integration | Registry → Event Bus failure | TASK-104 | verified |
| E-C107 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | integration | Rollback compatibility | TASK-104 | verified |
| E-C112 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | integration | AuditStore failure | TASK-104 | verified |
| B-C105 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | benchmark | Publish API | TASK-104 | verified |

---

## TASK-104: 实现可靠发布、回滚、Transactional Outbox 与强审计

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-101, TASK-102, TASK-103
- **Source**: docs/design/fluxion-console-design-v1.6.md#3.2.6, docs/design/fluxion-console-design-v1.6.md#3.2.7, docs/design/fluxion-console-design-v1.6.md#2.5.2
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001, fluxion-console-channel#RULE-fluxion-console-001, fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-dfx#RULE-fluxion-dfx-001, backend-logging#RULE-backend-logging-001, backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-C102, S-C103, S-C106, S-C113, E-C106, E-C107, E-C112, B-C105

### Description

把原 TASK-104 的发布可靠性部分独立出来，只负责 Publish/Rollback/Deprecate、Outbox、Redis Streams/SQLite revision 与 Audit 一致性。

### Scope

- Publish/Rollback/Deprecate 状态机。
- PublishRecord + AuditLog + Transactional Outbox 同事务。
- Prod Redis Streams；Dev SQLite Revision Polling。
- Outbox retry/idempotency。
- Publish 性能 benchmark。

### Checklist

- [x] Published/Audit/Outbox 事务边界先写失败测试。
- [x] Event 故障不得出现 UI 伪成功。
- [x] Audit 失败不得让高影响操作伪成功。
- [x] Publish 不等待 Runtime ACK。

### Acceptance Contract

| 场景ID | 测试层级 | 测试文件 | 单独执行命令 | 核心断言 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-C102 | E2E | `backend/tests/e2e/test_publish_hot_reload.py` | `python3 -m pytest backend/tests/e2e/test_publish_hot_reload.py -k S_C102` | 发布后新执行使用新版本 | verified |
| S-C103 | E2E | `backend/tests/e2e/test_console_independence.py` | `python3 -m pytest backend/tests/e2e/test_console_independence.py -k S_C103` | Console 停机不影响已发布执行 | verified |
| S-C106 | E2E | `backend/tests/e2e/test_audit.py` | `python3 -m pytest backend/tests/e2e/test_audit.py -k S_C106` | 发布/回滚 Audit 完整 | verified |
| S-C113 | E2E | `backend/tests/e2e/test_audit.py` | `python3 -m pytest backend/tests/e2e/test_audit.py -k S_C113` | Log/Audit 可关联且职责独立 | verified |
| E-C106 | integration | `backend/tests/integration/test_outbox.py` | `python3 -m pytest backend/tests/integration/test_outbox.py -k E_C106` | Event 故障进入 pending 并可重试 | verified |
| E-C107 | integration | `backend/tests/integration/test_rollback.py` | `python3 -m pytest backend/tests/integration/test_rollback.py -k E_C107` | 不兼容回滚阻止/强审批 | verified |
| E-C112 | integration | `backend/tests/integration/test_audit_failure.py` | `python3 -m pytest backend/tests/integration/test_audit_failure.py -k E_C112` | Audit 失败不伪成功 | verified |
| B-C105 | benchmark | `backend/tests/benchmarks/test_publish_benchmark.py` | `python3 -m pytest backend/tests/benchmarks/test_publish_benchmark.py -k B_C105 --benchmark-only` | Publish P95≤500ms | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-C102 | FAIL: 缺少 `fluxion.services.outbox` 与 Outbox→Runtime 事件链路 | PASS: 同命令 | `backend/tests/e2e/test_publish_hot_reload.py:59` | Console API 原子写 Registry/Outbox，worker 投递 config.changed 并立即使 Runtime 新执行使用 v2 | verified |
| S-C103 | FAIL: 缺少 `publish_records`，发布仍无独立事实记录 | PASS: 同命令 | `backend/tests/e2e/test_console_independence.py:45` | 文件 SQLite 由 Console 发布并关闭后，独立 Runtime Store 仍读取 Published Version 与 PublishRecord | verified |
| S-C106 | FAIL: Rollback API 返回 405，无法形成回滚 Audit | PASS: 同命令 | `backend/tests/e2e/test_audit.py:50` | Publish/Publish/Rollback 真实 API 与 Audit Store，断言 actor/resource/version/action/time/request/publish 关联 | verified |
| S-C113 | FAIL: Access Log 缺少与 Audit 共用的 `publish_id` | PASS: 同命令 | `backend/tests/e2e/test_audit.py:90` | structlog JSON Access Log 与独立 Audit 表共享 request_id/publish_id，且 event/action 职责不同 | verified |
| E-C106 | FAIL: 缺少 Outbox worker，事件失败无法保留 pending/retry | PASS: 同命令 | `backend/tests/integration/test_outbox.py:56` | 故障 Publisher 首次失败后 Outbox 保持 pending/attempt=1，第二次恢复且仅发布一次；Redis adapter 另测 ID 幂等与 timeout | verified |
| E-C107 | FAIL: Deprecate/Rollback API 返回 405，无强审批 Gate | PASS: 同命令 | `backend/tests/integration/test_rollback.py:57` | deprecated 历史版本普通回滚 409，必须 force + approval_id；批准后重新激活且生成 pending event | verified |
| E-C112 | FAIL: 缺少 Outbox/PublishRecord 原子边界，无法注入 Audit 事务失败 | PASS: 同命令 | `backend/tests/integration/test_audit_failure.py:62` | Audit writer 故障注入后 HTTP 500，Resource 保持 Draft，PublishRecord/Audit/Outbox/revision 全部回滚 | verified |
| B-C105 | FAIL: Publish API 仍返回 `event_status=published`，未表达异步 pending | PASS: 同命令（100 rounds，mean 15.65ms，max 102.49ms） | `backend/tests/benchmarks/test_publish_benchmark.py:54` | 真实 ASGI Publish API 只等待 DB 事务，不等待 Runtime ACK，断言 P95≤500ms | verified |

### Definition of Done

- Publish/Audit/Outbox 可恢复且可追溯。
- Publish P95≤500ms。
- 测试/故障注入/Stop Gate 全部通过。

### Log

- [2026-08-23] DeepSeek 评审修订：补依赖图、验收覆盖与任务内聚性。
- [2026-08-24T03:33:32Z] started (in-progress, context-sha256=1bbd57843d37a97b3399bcbbf2718e8145ddcf161bb13a0144b92698193f26be)
- [2026-08-24T03:59:55Z] RED: TASK-104 八个 Acceptance Contract 单独命令均失败；缺失 Transactional Outbox/PublishRecord、Rollback/Deprecate API、强审计事务与 publish_id 日志关联。
- [2026-08-24T04:21:19Z] GREEN: 八个 Acceptance Contract 单独命令全部 PASS；Publication/Audit/Outbox/revision 同事务，Outbox 有界重试/租约/幂等，Redis Streams 与 SQLite revision 路径均有自动化证据。
- [2026-08-24T04:21:19Z] Validation: Python syntax/mypy/ruff PASS；后端 124 passed；Registry SQLite+PostgreSQL Contract 18 passed；前端 typecheck/lint/test 与 Semi 约束 PASS。
- [2026-08-24T04:21:19Z] completed (done, context-sha256=1bbd57843d37a97b3399bcbbf2718e8145ddcf161bb13a0144b92698193f26be)
- [2026-08-24T12:26:49Z] REVIEW+FIX: (1) CRITICAL：rollback 审批 gate 是 presence-only——`approval_id` 传任意字符串即通过（`approval.py` gate 仅被测试使用）。已修复：新增 `services/approval_app.py`（ApprovalStore/InMemoryApprovalStore，PENDING/APPROVED/REJECTED，expires_at），`console_app.create_approval/decide_approval/_verify_rollback_approval`，`api/console.py` 增加 `POST /api/v1/approvals` 与 `POST /api/v1/approvals/{id}:decide`；伪造 approval_id → 403，审批人不能执行回滚，仅 requester 可执行，过期/已决/内容不匹配均拒绝。`test_E_C107` 已改为真实审批流（create→decide→rollback + 伪造 403 负例）。(2) publish/rollback/deprecate 增加 per-resource 进程内锁，消除 expected_base_version check-then-commit 竞态（多实例需 DB 级串行化，已注释）。`_check_expected_base` 不加锁读取的 TOCTOU 因此被服务层锁覆盖（单进程），多进程残留记录为已知 gap。(3) rollback 到当前已发布版本现拒绝为 no-op（避免无意义 revision bump/audit 污染）。(4) 已知 gap（记录，未半实现）：OutboxWorker 仅测试中启动，生产单实例由 `poll_revision`（0.25s）覆盖，跨实例通知缺失。
