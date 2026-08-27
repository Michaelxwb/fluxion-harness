# Tasks: Fluxion Web Chat 与 Bind

- **Source**: docs/design/fluxion-console-design-v1.6.md
- **Created**: 2026-08-23
- **Updated**: 2026-08-24

## Proposal

实现 PlatformUser Identity、BindCode、Web Chat/SSE，并在此任务完成 Runtime S-R01 的完整 Console+SQLite+Runtime+Web Chat Golden Path，解决 TASK-005 的前向依赖。

### Alignment

- **Scope**: 仅实现本 TASK 的范围，不提前实现后续阶段。
- **Decisions**: 以 Architecture Baseline、Design-Refs 和 active Spec Context 为准。
- **Non-goals**: 不修改任务外核心 Contract；发现冲突使用 `#NOTES` 停止并重新对齐。
- **Acceptance**: Acceptance-Refs、required verifier、NFR Gate 与回归检查全部通过。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-C105 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Channel identity → PlatformUser Store | TASK-103 | verified |
| S-C110 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Browser → Chat → Bind → Runtime | TASK-103 | verified |
| S-C119 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Web/Stub IM Adapter → Channel Adapter Contract → 统一 Channel API → Runtime | TASK-103 | verified |
| E-C108 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | E2E | Unbound Chat → Runtime gate | TASK-103 | verified |
| E-C109 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | integration | Bind Service → Binding Store | TASK-103 | verified |
| S-R01 | docs/design/fluxion-runtime-design-v1.7.md#2.5.2 功能验收场景 | E2E | Console + SQLite Registry + Runtime + Web Chat | TASK-103 | verified |
| B-C106 | docs/design/fluxion-console-design-v1.6.md#2.5.2 功能验收场景 | benchmark | Bind/Chat framework | TASK-103 | verified |

---

## TASK-103: 实现正式 Web Channel，并完成本地产品 Golden Path

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-005, TASK-101, TASK-102
- **Source**: docs/design/fluxion-console-design-v1.6.md#3.2.4, docs/design/fluxion-console-design-v1.6.md#3.2.5, docs/design/fluxion-console-design-v1.6.md#2.5.2, docs/design/fluxion-runtime-design-v1.7.md#2.5.2
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001, fluxion-console-channel#RULE-fluxion-console-001, fluxion-resource-registry#RULE-fluxion-resource-001, fluxion-dfx#RULE-fluxion-dfx-001, fluxion-runtime-core#RULE-fluxion-runtime-001, frontend-directory-structure#RULE-frontend-directory-001, frontend-quality-standards#RULE-frontend-quality-001, frontend-component-specs#RULE-frontend-component-001, frontend-semi-design#RULE-frontend-semi-001
- **Acceptance-Refs**: S-C105, S-C110, S-C119, E-C108, E-C109, S-R01, B-C106

### Description

实现 PlatformUser Identity、BindCode、Web Chat/SSE，并在此任务完成 Runtime S-R01 的完整 Console+SQLite+Runtime+Web Chat Golden Path，解决 TASK-005 的前向依赖。

> **S-C110 与 S-R01 层级区分**：S-C110 是 Chat 前端组件级 E2E（API 可 mock），断言绑定后消息携带 `platform_user_id` 调 Runtime；S-R01 是全栈产品 E2E（真实启动 SQLite+Runtime+Console API+Chat Web）。两者覆盖不同层级，不得写成同一断言的两份拷贝。
>
> **Channel Adapter 边界**：本任务实现统一 Channel Adapter Contract（FEAT-26/S-C119）并以 Web Chat 作为首个实现；具体 IM 通道 Adapter（飞书/QQ/企微）不在 V1 开发，业务接入时仅新增 Adapter 即接入，禁止修改 Runtime 与通道无关核心（见 Architecture Baseline §12 与 ADR-011）。

### Scope

- PlatformUserIdentity/BindCode Store 与 Service。
- 10 分钟、单次、hash、tenant-bound、失败 5 次冻结。
- Web Chat + SSE + IdentityResolver。
- Channel Adapter Contract（入站规范化/出站推送/身份映射钩子）定义，Web Chat 作为首个实现。
- 未绑定普通消息在 Runtime 前阻断。
- 完整 Local Product Bundle Golden Path。

### Checklist

- [x] 先写 Bind 正常/异常/未绑定拦截和 S-R01 Golden Path。
- [x] BindCode 明文不得进入 DB/log/audit/trace。
- [x] Golden Path 必须真实启动 SQLite Registry + Runtime + Console API + Web Chat。
- [x] Channel Adapter Contract 以 Web Chat + Stub IM Adapter 双实现验证；新增 IM 通道仅新增 Adapter，不修改 Runtime 核心。

### Acceptance Contract

| 场景ID | 测试层级 | 测试文件 | 单独执行命令 | 核心断言 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-C105 | E2E | `backend/tests/e2e/test_identity_mapping.py` | `python3 -m pytest backend/tests/e2e/test_identity_mapping.py -k S_C105` | Channel Identity 得到统一 PlatformUser | verified |
| S-C110 | E2E | `frontend/apps/chat/src/__tests__/bind-chat.e2e.test.tsx` | `pnpm --filter @fluxion/chat test -- -t S-C110` | bind 后以 platform_user_id 调用 Runtime | verified |
| S-C119 | E2E | `backend/tests/e2e/test_channel_adapter.py` | `python3 -m pytest backend/tests/e2e/test_channel_adapter.py -k S_C119` | Web 与 Stub IM Adapter 共用统一契约进入 Runtime；切换不修改核心 | verified |
| E-C108 | E2E | `frontend/apps/chat/src/__tests__/bind-chat.e2e.test.tsx` | `pnpm --filter @fluxion/chat test -- -t E-C108` | 未绑定普通消息不进入 Runtime | verified |
| E-C109 | integration | `backend/tests/integration/test_bind_code.py` | `python3 -m pytest backend/tests/integration/test_bind_code.py -k E_C109` | 过期/已用/错 tenant code 拒绝 | verified |
| S-R01 | E2E | `backend/tests/e2e/test_local_product_golden_path.py` | `python3 -m pytest backend/tests/e2e/test_local_product_golden_path.py -k S_R01` | Console 创建发布 RuntimeProfile→bind→Chat→Runtime 完整成功 | verified |
| B-C106 | benchmark | `backend/tests/benchmarks/test_chat_bind_benchmark.py` | `python3 -m pytest backend/tests/benchmarks/test_chat_bind_benchmark.py -k B_C106 --benchmark-only` | Bind P95≤300ms；Chat 框架 P95≤200ms | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-C105 | FAIL: `python3 -m pytest backend/tests/e2e/test_identity_mapping.py -k S_C105` 缺少 `fluxion.plugins.channel_adapters` | PASS: 同命令 | `backend/tests/e2e/test_identity_mapping.py:35` | 真实 SQLite Store 持久化并解析 Channel identity 到 PlatformUser，断言 tenant 与 user 一致 | verified |
| S-C110 | FAIL: `pnpm --filter @fluxion/chat test -- -t S-C110` 无法解析 `src/App`，Chat/Bind UI 未实现 | PASS: 同命令 | `frontend/apps/chat/src/__tests__/bind-chat.e2e.test.tsx:28` | React 19 + Semi Chat 经 service 完成 bind，随后仅以 `platform_user_id=user-a` 调 Runtime | verified |
| S-C119 | FAIL: `python3 -m pytest backend/tests/e2e/test_channel_adapter.py -k S_C119` 缺少统一 Channel Adapter 实现 | PASS: 同命令 | `backend/tests/e2e/test_channel_adapter.py:31` | Web/Stub IM 两个 Adapter 通过同一 Contract/Service 进入同一 Runtime，输出与请求一致 | verified |
| E-C108 | FAIL: `pnpm --filter @fluxion/chat test -- -t E-C108` 无法解析 `src/App`，未绑定拦截 UI 未实现 | PASS: 同命令 | `frontend/apps/chat/src/__tests__/bind-chat.e2e.test.tsx:13` | 未绑定普通消息显示 bind gate，Runtime 调用数严格为 0 | verified |
| E-C109 | FAIL: `python3 -m pytest backend/tests/integration/test_bind_code.py -k E_C109` 缺少 Bind Service/Store | PASS: 同命令 | `backend/tests/integration/test_bind_code.py:56` | 真实 SQLite 事务拒绝过期/已用/错 tenant/frozen；5 次失败冻结；DB 与 Audit 均无 code 明文 | verified |
| S-R01 | FAIL: `python3 -m pytest backend/tests/e2e/test_local_product_golden_path.py -k S_R01` 缺少 `fluxion.api.channel` | PASS: 同命令 | `backend/tests/e2e/test_local_product_golden_path.py:66` | 临时文件 SQLite + Console ASGI API + Runtime + Channel ASGI/SSE 完整链路，断言发布、绑定及模型输出 | verified |
| B-C106 | FAIL: `python3 -m pytest backend/tests/benchmarks/test_chat_bind_benchmark.py -k B_C106 --benchmark-only` 缺少 Channel/Bind 实现 | PASS: 同命令（100 rounds） | `backend/tests/benchmarks/test_chat_bind_benchmark.py:65` | 真实 SQLite Bind/Chat 框架计时，断言 Bind P95≤300ms、Chat P95≤200ms | verified |

### Definition of Done

- 完整本地产品 Golden Path GREEN。
- Bind/Chat 性能 Gate 达标。
- 前后端测试与 Stop Gate 全部通过。

### Log

- [2026-08-23] DeepSeek 评审修订：补依赖图、验收覆盖与任务内聚性。
- [2026-08-24T01:39:03Z] started (in-progress, context-sha256=2e713ffa9396bf8e7e90f518f7c473456c3bd583dc529defd30bcab17a823d1f)
- [2026-08-24T01:45:57Z] RED: TASK-103 七个场景测试已写入；Python 3.12 后端测试因 Channel Contract/Store/API 缺失失败，Chat E2E 因 `src/App` 与 Chat service 缺失失败。
- [2026-08-24T02:45:40Z] GREEN: 七个 Acceptance Contract 单独命令全部 PASS；BindCode 仅 hash 入库且错误审计不含明文，Web/Stub IM 共享 Channel Contract，本地 SQLite+Console+Runtime+Chat/SSE Golden Path 完成。
- [2026-08-24T02:45:40Z] Validation: Python syntax/mypy/ruff PASS；后端 114 passed；Registry SQLite+PostgreSQL Contract 16 passed；前端 typecheck/lint/test 与 Semi 约束 PASS。
- [2026-08-24T02:45:40Z] completed (done, context-sha256=2e713ffa9396bf8e7e90f518f7c473456c3bd583dc529defd30bcab17a823d1f)
- [2026-08-24T12:26:49Z] REVIEW+FIX: (1) chat_access token 无 TTL，仅显式 revoke 回收——记录为已知 gap（phase-08 验收仅覆盖 bind code 过期，无 chat token TTL contract）；(2) `issue_chat_access` 不校验 runtime_profile 已发布——记录为已知 gap（dev 模式契约 test_S_P13_04 依赖运行时按需解析，强制校验破坏契约）；(3) channel API 缺少 catch-all Exception handler，未捕获异常会破坏 envelope 并截断 SSE——已修复（`api/channel.py` 增加 generic handler）；(4) SSE error 帧缺 request_id/trace_id——已修复（`_events`/`_access_events` 携带请求上下文）；(5) SQLite 并发兑换 BindCode 触发 OperationalError 而非干净拒绝——已修复（`channel_sqlalchemy.py` 捕获 → `BindCodeRejected("used")`）；(6) 审批 gate 为 presence-only（任意字符串 approval_id 可通过）——已修复为真实 service-layer ApprovalStore + routes（见 phase-09）。
