# Tasks: 中间件纯 ASGI 透传（SSE 真增量直达）

- **Source**: 2026-09-08 对话对齐（TASK-018 缺口前置依赖）；`backend/src/fluxion/api/middleware.py`
- **Created**: 2026-09-08
- **Updated**: 2026-09-08

## Proposal

`RequestContextMiddleware` 继承 Starlette `BaseHTTPMiddleware`，把响应体（含 SSE 流）攒完再发，导致首帧延迟、无真增量。重写为纯 ASGI 中间件：四职责（身份门禁、RequestContext 绑定、access 日志、OTel span）逐行平移，响应体透传。完成后解开归档 TASK-018 两个缺口的前置阻塞。不碰 Redis（无关），不做断线续播（另议）。

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|---|---|---|---|---|---|
| S-MW-01 | 本文件 TASK-001 | integration | 真实 Starlette app＋中间件＋httpx | TASK-001 | verified |
| S-MW-02 | 本文件 TASK-001 | integration | 真实流式端点＋首字节时序断言 | TASK-001 | verified |
| E-MW-01 | 本文件 TASK-001 | integration | 下游抛错路径 | TASK-001 | verified |
| RULE-backend-logging-001 | 本文件 TASK-001 | integration | 关联 ID＋脱敏＋无请求体入日志 | TASK-001 | verified |

---

## TASK-001: 纯 ASGI 重写＋行为一致

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: 本文件 Proposal
- **Spec-Refs**: backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**: S-MW-01, S-MW-02, E-MW-01, RULE-backend-logging-001

### Description

`backend/src/fluxion/api/middleware.py`：`RequestContextMiddleware` 改为纯 ASGI 类（`__call__(scope, receive, send)`，非 http scope 直接透传）；`http.response.start` 消息里改响应头（`X-Request-ID`/`X-Trace-ID`）、读状态码/`X-Biz-Code`/`X-Publish-ID`；body 消息原样透传；异常直接上抛（finally 记日志＋reset context＋默认 500）。身份 401 走 `failure()` 直接发送。不读请求体，不用 BackgroundTasks（全仓无此用法）。

### Checklist
- [x] [S-MW-01][integration] 身份门禁（缺头 401/放行）、头回显、access 日志字段、span 关联，与改前逐项一致
- [x] [S-MW-02][integration] 流式端点首字节在 body 收齐前到达（真增量；改前为 RED）
- [x] [E-MW-01][integration] 下游抛错：500＋access 日志＋context reset，后续请求不受污染
- [x] verifier `RULE-backend-logging-001`：以 S-MW-01/E-MW-01 验证关联 ID＋脱敏＋无请求体入日志
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-MW-01 | integration | 真实 app＋中间件＋httpx | 四职责一致 | backend/tests/integration/test_request_context_middleware.py（新增） | `.venv/bin/python -m pytest -q backend/tests/integration/test_request_context_middleware.py` | verified |
| S-MW-02 | integration | 真实 TCP loopback＋首字节时序 | 首字节早于收齐 | 同上 | 同上 | verified |
| E-MW-01 | integration | 下游抛错路径 | 500＋日志＋无污染 | 同上 | 同上 | verified |
| RULE-backend-logging-001 | integration | 同上 | 关联 ID＋脱敏，由 S-MW-01/E-MW-01 提供行为证据 | 同上 | 同上 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-MW-01 | ——（改前行为， parity 测试一次写对） | 4 passed（含 401/回显） | test_S_MW_01_* | 真实 app＋中间件＋httpx ASGI | verified |
| S-MW-02 | TimeoutError（3s 预算，handler 5s 自放行；另发现 ASGITransport 自身也缓冲，改走真实 TCP loopback 取证） | 4 passed，0.39s | test_S_MW_02_first_byte_before_body_complete | uvicorn loopback＋真 httpx 流；首字节即时 | verified |
| E-MW-01 | —— | 500＋后续请求独立 ID（context 已 reset） | test_E_MW_01_* | raise_app_exceptions=False 真实 500 路径 | verified |
| RULE-backend-logging-001 | —— | access 日志四 ID＋脱敏头＋无 body；ruff+mypy clean | caplog access 事件＋静态检查 | 同上 | verified |

### Log
- [2026-09-08] created (draft)
- [2026-09-08] completed (done)：BaseHTTPMiddleware→纯 ASGI（_ObservingSend 透传 send，只在 start 消息改头/记值）；回归 12＋32＋20 全绿；解开归档 TASK-018 两缺口的前置阻塞（用例本身仍待补）
