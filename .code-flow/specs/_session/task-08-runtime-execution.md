# TASK-025 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules
- `harness-api#RULE-api-001`: Console 与内部 API 使用统一封套：外部 `{code,msg,data,trace_id,request_id,timestamp}`；列表统一 `{items,page,page_size,total}`，`page>=1`、`1<=page_size<=100`；业务只抛 error code，`msg`/`http_status` 只来自 `config/api-messages.yaml`。
  - rule_sha256=615724c773399b4ce032ae7934787e55501ac643ed0a9d2648e135e5003c8ab6 verifier=harness-api#RULE-api-001; artifacts=08-runtime-execution.backend.design.md,08-runtime-execution.md
- `harness-api#RULE-api-002`: 创建/上传类 POST（导入、可重试提交）支持 `Idempotency-Key` Header：DB 幂等表 partial unique `(tenant_id, idempotency_key, endpoint)` 记录首次响应；请求指纹 = endpoint|关键参数|内容 checksum；同 key 同指纹重放返回首次结果（200 原响应），同 key 不同指纹返回 `COMMON_CONFLICT`。
  - rule_sha256=aa78d9fb6c38b64c15bb312974e019de2044461d3fc2c9ae84969d84022b445a verifier=harness-api#RULE-api-002; artifacts=08-runtime-execution.backend.design.md,08-runtime-execution.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | E2E | 真实 Gateway→Runtime SSE→PostgreSQL | WAITING_INPUT 普通回复自动恢复原 Run；首事件 run.created/resumed=true；seq 延续 | tests/acceptance/runtime/test_run_lifecycle.py -k s07（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","s07"] | planned |
| E-02 | E2E | 真实 Gateway→cancel-active→PostgreSQL/SSE | WAITING_INPUT CAS CANCELLED；interrupt CANCELLED；CANCEL 事件与 run.completed(status=CANCELLED) | tests/acceptance/runtime/test_run_lifecycle.py -k e02（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","e02"] | planned |
| E-03 | E2E | 真实 Gateway→Runtime→PostgreSQL partial unique | 并发不同消息仅一活跃 Run；409 RUN_BUSY；已有状态/lease 不变；双语标准 Envelope | tests/acceptance/runtime/test_run_lifecycle.py -k e03（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","e03"] | planned |
| E-08 | E2E | 真实 Gateway→cancel-active→PostgreSQL/Redis→执行者 | CREATED/RUNNING 响应 CANCELLING；DB cancel_requested 权威；Redis 故障仍协作 CANCELLED；无活跃 404 NO_ACTIVE_RUN | tests/acceptance/runtime/test_run_lifecycle.py -k e08（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_run_lifecycle.py","-k","e08"] | planned |
| B-01 | E2E | 真实 Gateway HTTP/SSE→Runtime 幂等表→PostgreSQL/Tool | 新增：同 key 同指纹 200 重放原提交结果、不二次执行；异指纹 COMMON_CONFLICT；并发/重启后仍幂等 | tests/acceptance/runtime/test_idempotency.py -k b01（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_idempotency.py","-k","b01"] | planned |
| RULE-api-001 | E2E | 真实 Gateway→Runtime→PostgreSQL partial unique；真实 Gateway→cancel-active→PostgreSQL/Redis→执行者＋原 verifier 边界 | 并发不同消息仅一活跃 Run；409 RUN_BUSY；已有状态/lease 不变；双语标准 Envelope；CREATED/RUNNING 响应 CANCELLING；DB cancel_requested 权威；Redis 故障仍协作 CANCELLED；无活跃 404 NO_ACTIVE_RUN；原 verifier 全部通过 | tests/test_api_i18n.py, tests/test_error_catalog.py, tests/acceptance/test_foundation_api_envelope.py＋tests/acceptance/runtime/test_run_lifecycle.py（planned） | ["bash","-lc","'uv' 'run' 'pytest' '-q' 'tests/test_api_i18n.py' 'tests/test_error_catalog.py' 'tests/acceptance/test_foundation_api_envelope.py' && uv run pytest -q tests/acceptance/runtime/test_run_lifecycle.py"] | planned |
| RULE-api-002 | E2E | 真实 Gateway HTTP/SSE→Runtime 幂等表→PostgreSQL/Tool＋原 verifier 边界 | 新增：同 key 同指纹 200 重放原提交结果、不二次执行；异指纹 COMMON_CONFLICT；并发/重启后仍幂等；原 verifier 全部通过 | tests/console_skill/test_import_idempotency.py＋tests/acceptance/runtime/test_idempotency.py（planned） | ["bash","-lc","'uv' 'run' 'pytest' '-q' 'tests/console_skill/test_import_idempotency.py' && uv run pytest -q tests/acceptance/runtime/test_idempotency.py"] | planned |
