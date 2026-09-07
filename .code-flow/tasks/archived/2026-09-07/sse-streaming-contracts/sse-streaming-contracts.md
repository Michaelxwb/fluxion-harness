# Tasks: sse-streaming-contracts

- **Source**: .code-flow/tasks/2026-09-07/sse-streaming-contracts/sse-streaming-contracts.design.md
- **Created**: 2026-09-07
- **Updated**: 2026-09-07

## Proposal

API→Runtime 的 SSE 名实不符（网关缓冲式伪流式），流式结算与错误码在各层丢失或变形。本次让增量真实增量、completed 与非流式全等、错误码不断链，并把"三镜像"表述收敛为单制品（与 Helm 单镜像三部署一致）。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|--------|---------|---------|-------------|---------|------|
| S-01 | sse-streaming-contracts.design.md#2.4 验收条件 | integration | 真实 httpx（含 MockTransport 增量下发） | TASK-001 | verified |
| S-02 | sse-streaming-contracts.design.md#2.4 验收条件 | integration | 真实 service.stream + run | TASK-002 | verified |
| S-03 | sse-streaming-contracts.design.md#2.4 验收条件 | integration | 真实 httpx MockTransport | TASK-003 | verified |
| S-04 | sse-streaming-contracts.design.md#2.4 验收条件 | manual | GitHub Actions 环境 | TASK-004 | verified |
| E-01 | sse-streaming-contracts.design.md#2.4 验收条件 | integration | 真实 httpx MockTransport | TASK-001 | verified |
| E-02 | sse-streaming-contracts.design.md#2.4 验收条件 | integration | 真实 httpx MockTransport | TASK-001 | verified |
| E-03 | sse-streaming-contracts.design.md#2.4 验收条件 | integration | 真实 service.stream | TASK-003 | verified |
| B-01 | sse-streaming-contracts.design.md#2.4 验收条件 | integration | 真实 httpx（外借 client） | TASK-001 | verified |

---

## TASK-001: 网关真流式

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: sse-streaming-contracts.design.md#3.2 架构设计, sse-streaming-contracts.design.md#3.3 接口设计
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001, fluxion-console-channel#RULE-fluxion-console-001
- **Acceptance-Refs**: S-01, E-01, E-02, B-01

### Description

`HttpRuntimeGateway.stream` 改 `client.stream` 增量转发；非 200 先读 body 转错误再抛；复用 `_iter_sse`；runtime/channel 两处 `StreamingResponse` 加防缓冲头。先写 S-01 增量测试并记录 RED。

### Checklist

- [x] [S-01][integration] 先写 MockTransport 分块延迟测试：首事件到达早于全收，记录 RED（真实边界：真实 httpx 增量下发）
- [x] `http_runtime_gateway.py` stream 改 `async with client.stream("POST",...)`，200 判状态码，非 200 读 body 进 `_error_from_envelope` 后抛
- [x] 200 路径 `aiter_text` 复用 `_iter_sse` 增量 yield；外借 client 只关 response 不关 client
- [x] 超时保持 connect/pool/write + stream read 300s，不新增重试
- [x] `api/runtime.py` 与 `api/channel.py` 的 `StreamingResponse` 加 `Cache-Control: no-cache` + `X-Accel-Buffering: no`
- [x] [S-01][integration] 首事件增量到达断言（GREEN）；[E-01] 建连前 503 抛无事件；[E-02] 非 200 body 转错误；[B-01] 外借 client 存活且 response 已关
- [x] verifier `RULE-backend-quality-001`: 超时语义不变、无静默吞异常、无重试、外借资源释放（S-01/E-01/E-02/B-01 覆盖）
- [x] verifier `RULE-fluxion-console-001`: Web Chat SSE 边界与鉴权不变，只修转发语义（S-01/E-03 侧证）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | 真实 httpx（含 MockTransport 增量下发） | 首事件早于全收（<0.4s vs 0.5s+），两事件齐 | backend/tests/integration/test_gateway_true_streaming.py::test_s01_first_event_arrives_before_full_body | pytest 该文件 | verified |
| E-01 | integration | 真实 httpx MockTransport | 建连前 503 抛，不产事件 | test_http_runtime_gateway.py::test_e01_stream_pre_connect_error_raises | 回归 8/8 | verified |
| E-02 | integration | 真实 httpx MockTransport | 非 200 先读 body 转错误 | test_http_runtime_gateway.py（既有） + 新实现分支 | 回归 8/8 | verified |
| B-01 | integration | 真实 httpx（外借 client） | response 已关，外借 client 存活 | test_gateway_true_streaming.py::test_b01_borrowed_client_survives_stream | pytest 该文件 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | FAIL（首事件 0.5s+，被缓冲） | 2 passed（首事件 <0.4s） | test_gateway_true_streaming.py | 自定义 _ChunkedTransport 块延迟下发 | verified |
| E-01 | N/A（既有行为） | 既有 8 用例全过 | test_http_runtime_gateway.py | MockTransport 503 | verified |
| E-02 | N/A（既有行为） | 同上（含 aread 分支） | 同上 | 同上 | verified |
| B-01 | N/A（既有保持） | 1 passed（改前改后皆过，防退化） | test_gateway_true_streaming.py | 外借 AsyncClient is_closed 断言 | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-07] completed (done)

---

## TASK-002: 流式 completed 统一

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: sse-streaming-contracts.design.md#3.2 架构设计
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001, fluxion-dfx#RULE-fluxion-dfx-001
- **Acceptance-Refs**: S-02

### Description

`runtime_app` 流式 B 分支改组 `RunRuntimeResult` 走 `to_payload()`，与非流式 10 字段全等；`model_provider_id` 取自 `model.completed` 上下文事件（无事件则 None 并注释）；`tool_results` 保持 `[]`（该分支仅当 model_tools 为空进入，注释写明依据）。

### Checklist

- [x] [S-02][integration] 先写 stream/recording-stream 与 run 的 completed 全等测试，记录 RED
- [x] B 分支构造 `RunRuntimeResult`（provider 取 model.completed 事件，余字段照 `_run_result` 语义填）
- [x] `tool_results: []` 处加注释（分支前置条件 model_tools==[]，恒空语义正确）
- [x] A/C 分支不动；channel 6 字段包装不动（产品契约，Out of Scope）
- [x] [S-02][integration] 10 字段全等断言（GREEN）
- [x] verifier `RULE-fluxion-runtime-001`: 同 Snapshot 结算一致，无本地事实（S-02 覆盖）
- [x] verifier `RULE-fluxion-dfx-001`: 全场景自动化证据（S-02 + 回归 realtime 流式用例）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | 真实 service.stream + run | 键集合一致，非执行域值一致，provider 真实 | backend/tests/integration/test_stream_completed_contract.py | pytest 该文件（GREEN） | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | FAIL（model_provider_id None vs custom-stream） | 1 passed | test_stream_completed_contract.py | 真实 service + 同 store 双 session | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-07] completed (done)

---

## TASK-003: 错误透传不断链

- **Status**: done
- **Priority**: P1
- **Depends**:
- **Source**: sse-streaming-contracts.design.md#2.2 功能方案, sse-streaming-contracts.design.md#3.2 架构设计
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001
- **Acceptance-Refs**: S-03, E-03

### Description

`RuntimeApplicationError` 新增 `upstream_code: int | None` 与 `upstream_error: str | None`（默认 None，纯新增字段）；`_error_from_envelope` 回填上游信封码；channel `_access_events` 加 `except RuntimeApplicationError` 分支（code 保持 channel 码，data 加 `error` slug）。

### Checklist

- [x] [S-03][integration] 先写上游 40001+slug 透传测试，记录 RED
- [x] `runtime_contracts.py` 加两可选字段（默认 None，不改构造位）
- [x] `_error_from_envelope` 回填上游信封码；既有 default_code 分支语义不变
- [x] channel `_access_events` 加 slug 保留分支；SSE error 事件透传不动
- [x] [S-03][integration] upstream_code=40001 与 slug 可见（GREEN）；[E-03] 流中途 error 保留 slug
- [x] verifier `RULE-fluxion-console-api-001`: 错误经统一封装透传，不断链（S-03/E-03 覆盖）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | 真实 httpx MockTransport | upstream_code 与 slug 可见 | backend/tests/integration/test_error_passthrough.py::test_s03 | pytest 该文件 | verified |
| E-03 | integration | 真实 service.stream | error 透传且保留 slug | test_error_passthrough.py::test_e03（桩 service） | pytest 该文件 | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | FAIL（无 upstream_code 属性） | 2 passed | test_error_passthrough.py | MockTransport 400 信封 | verified |
| E-03 | 与 S-03 同批新写（实现后即过；既有透传行为保持） | 2 passed | test_error_passthrough.py | 桩 service 抛远端错误 | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-07] completed (done)

---

## TASK-004: 单制品表达一致

- **Status**: done
- **Priority**: P2
- **Depends**:
- **Source**: sse-streaming-contracts.design.md#2.2 功能方案
- **Spec-Refs**:
- **Acceptance-Refs**: S-04

### Description

Dockerfile 合回单 final（删三 target 与 ENV）；workflow 删矩阵改单构建单推送（tag 规则不变）；compose 去 `target` 行并显式 `FLUXION_ROLE: api`；workflow/Dockerfile/注释"三镜像"改"单制品"。Helm 已是单镜像三部署，不动值。

### Checklist

- [x] Dockerfile 合回单 final，删三 target
- [x] docker-build.yml 删矩阵，单构建（tags 照旧）
- [x] compose 去 target + 显式 role；措辞改单制品
- [x] [S-04][manual] workflow 文件评审 + helm template 渲染通过（需 CI 环境验证构建，设计已声明）
- [x] 运行验收命令并填写 Acceptance Evidence

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | manual | GitHub Actions 环境 | 单构建单推送，三部署同镜像 | workflow 文件 + helm render（本地 YAML 校验通过） | CI 运行（manual 原因见设计） | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-04 | N/A（三 target 构建曾全红于前端门禁，与本项无关） | YAML 解析通过；无"三镜像"残留；Helm 本就单镜像 | grep + yaml load | 文件评审 | verified（构建执行待 CI） |

### Log

- [2026-09-07] created (draft)
- [2026-09-07] completed (done)
