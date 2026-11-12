---
{"version":1,"requirement":"mcp-http-auth"}
---

# Tasks: mcp-http-auth

## TASK-001: 后端认证配置契约与 Console HTTP 客户端

- **Status**: done
- **Depends**:
- **Source**: mcp-http-auth.backend.design.md#33-数据设计, #34-接口设计
- **Scenarios**: S-01, S-02, E-01, E-02, E-03, E-05, B-01
- **Spec-Refs**: mcp/harness-mcp#RULE-mcp-001, secret/harness-secret#RULE-secret-001
- **Paths**: apps/console-platform/backend/src/muad_console_platform/**, tests/console_mcp/**
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "console-mcp-auth",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_mcp/test_mcp_auth_config.py", "tests/console_mcp/test_mcp_client.py"],
    "timeout": 300,
    "parallel": false
  },
  {
    "id": "console-mcp-discover",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_mcp/test_discover_api.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

新增 `McpAuthConfig`（`headers` + `secret_header`）与 `parse_auth_config()`，在服务层把非法配置映射为 `MCP_CONFIG_INVALID`；`McpServerDetail` 返回结构化对象。`McpClient` 支持静态头与密钥头，`test_connection`/`discover_tools` 共用同一构造；`mcp_snapshot` 记录非密 `auth_config`，密钥仍只从 `auth_secret` 读取。补充单测覆盖三态、保留头、CRLF、重复头、敏感名、长度上限与审计脱敏。

### Checklist

- [ ] `McpAuthConfig` 与 `parse_auth_config()` 实现并接入 create/update/detail。
- [ ] `McpClient` 接受 `headers`/`secret_header`，Bearer/无认证行为不变。
- [ ] `test_connection`、`discover_tools` 使用同一头组装。
- [ ] 单测覆盖 S/E/B 场景中的后端断言。
- [ ] `config_audit_log` 不出现密钥值。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "mcp/harness-mcp",
    "rule": "MCP HTTP 认证支持服务级三态：无认证、Authorization Bearer、自定义请求头；自定义头拆分为非密 headers 与单个 auth_secret 的 secret_header，密钥不得进入 auth_config、审计、Snapshot 或 API 响应。",
    "evidence": "真实 MCP 服务需要 X-Api-Key/X-App-Id 等多头认证，且密钥必须留在 Owner 表。"
  }
]
```

### Log

- [2026-11-12] created
- [2026-11-12T07:43:23.698Z] started tests=8f5fcaa6861d

### Verification

- 尚未执行
- 2026-11-12T07:54:51.496Z PASS commands=2/2 executed=2 deduped=0 elapsed=6775ms tests=8f5fcaa6861d head=764ff023bae7 dirty=1
  - console-mcp-auth PASS exit=0
  - console-mcp-discover PASS exit=0

## TASK-002: Runtime 解析与执行透传自定义头

- **Status**: draft
- **Depends**: TASK-001
- **Source**: mcp-http-auth.backend.design.md#32-架构设计, #34-接口设计
- **Scenarios**: S-02, S-04
- **Spec-Refs**: mcp/harness-mcp#RULE-mcp-001, secret/harness-secret#RULE-secret-001, snapshot/harness-snapshot
- **Paths**: packages/contracts/src/muad_contracts/resolve.py, apps/console-platform/backend/src/muad_console_platform/application/agent_mcp_service.py, apps/agent-runtime/src/muad_agent_runtime/application/**, tests/agent_runtime/**, tests/console_platform/**
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "runtime-mcp-headers",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_mcp_headers.py", "tests/agent_runtime/test_mcp_request_correlation.py"],
    "timeout": 300,
    "parallel": false
  },
  {
    "id": "console-mcp-bindings",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_platform/test_agent_mcp_bindings.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

`ResolvedMcpServer` 增加非密 `headers`/`secret_header`；Console 解析定义时从 `auth_config_json` 填入；Runtime `McpServerDefinition` 透传，`_McpSession` 按 Bearer/自定义头/无认证组装请求头。Snapshot 保持只含 ID/目录/工具名，不写 headers 或密钥。补充用例验证 initialize 与 tools/call 头一致、无认证不发 Authorization、`isError` 传播、审计不含密钥。

### Checklist

- [ ] 契约与解析链路透传 headers/secret_header。
- [ ] Runtime 会话三态头组装正确，会话重建后仍正确。
- [ ] Snapshot 与三类审计表不含密钥值。
- [ ] `isError: true` 仍按工具错误传播。

### Spec Delta

```json
[]
```

### Log

- [2026-11-12] created

### Verification

- 尚未执行

## TASK-003: 前端注册表单简化与认证编辑

- **Status**: draft
- **Depends**: TASK-001
- **Source**: mcp-http-auth.frontend.design.md#33-组件设计, #34-组件接口契约
- **Scenarios**: S-01, S-02, S-03, E-04, B-03
- **Spec-Refs**: ui/harness-ui, frontend/harness-frontend, i18n/harness-i18n, mcp/harness-mcp
- **Paths**: apps/console-platform/frontend/src/modules/mcp-management/**, apps/console-platform/frontend/src/locales/**, tests/frontend/**
- **E2E**: ci:e2e
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "mcp-form-contract",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/frontend/test_mcp_form_contract.py", "tests/frontend/test_mcp_auth_form_contract.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

按前端设计重写注册/编辑弹窗：去掉只读传输协议、常显超时/TTL 和原始 JSON；新增认证方式（无/Bearer/自定义头）、请求头增删行、密钥头与密钥值分离、高级设置折叠、`mcpServers` 导入（P1）。编辑时密钥不回显，留空不改；切换无认证显式清空。详情页展示认证方式摘要。更新 `services/mcpServers.ts` 类型与中英文词条。

### Checklist

- [ ] 常显字段收敛为名称、标识、服务地址、认证方式、用户范围、启用状态。
- [ ] 三种认证模式可保存/回显，密钥不回显。
- [ ] 请求头本地校验（重名/保留/敏感名/CRLF）。
- [ ] 高级设置折叠与配置导入入口。
- [ ] 契约测试与词条检查通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-12] created

### Verification

- 尚未执行

## TASK-004: 本地假 MCP 服务

- **Status**: draft
- **Depends**:
- **Source**: mcp-http-auth.backend.design.md#37-本地假-mcp-设计
- **Scenarios**: S-01, S-02
- **Spec-Refs**: mcp/harness-mcp#RULE-mcp-001
- **Paths**: tools/fake-business-platform/**, tests/console_mcp/test_fake_platform_mcp.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "fake-mcp",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_mcp/test_fake_platform_mcp.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

在 `tools/fake-business-platform/server.py` 增加 `POST /mcp` 的 Streamable HTTP JSON-RPC 子集：initialize（下发 session）、notifications/initialized、tools/list、tools/call；支持 none/bearer/headers 三种认证；工具覆盖 READ/WRITE/DESTRUCTIVE 与 `isError`；`GET /mcp` 返回 405；日志脱敏。README 增加两种启动方式与 Console 配置示例。保持零新依赖。

### Checklist

- [ ] CLI 认证参数与启动校验。
- [ ] 会话头、协议版本与请求 ID 正确回显。
- [ ] 工具目录与 effect 归一化符合客户端预期。
- [ ] Bearer/headers 两种模式均可被 `McpClient` 发现。
- [ ] README 示例可复制执行。

### Spec Delta

```json
[]
```

### Log

- [2026-11-12] created
- [2026-11-12T07:32:13.937Z] started tests=82cf59854178

### Verification

- 尚未执行

## TASK-005: 数据迁移、规范与字段文档

- **Status**: draft
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004
- **Source**: mcp-http-auth.backend.design.md#33-数据设计, #36-部署与运维
- **Scenarios**: B-02
- **Spec-Refs**: mcp/harness-mcp#RULE-mcp-001, secret/harness-secret#RULE-secret-001
- **Paths**: migrations/versions/**, .code-flow/specs/mcp/harness-mcp.md, docs/03-Console-Platform详细设计.md, docs/15-Console字段词典与一致性规范.md, tools/fake-business-platform/README.md, tests/migrations/**
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "mcp-auth-migration",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/migrations/test_mcp_auth_config_migration.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

新增 Alembic 数据迁移：`{"header": "X"}` → `{"secret_header": "X"}`，其他未知结构归 `{}`；提供可单测的纯函数。更新 `harness-mcp` 的 Conventions（认证三态、头校验、secret 边界）、Console 字段词典与假 MCP README。补充迁移测试，确保升级后详情可序列化、旧结构不再出现。

### Checklist

- [ ] 迁移可升级且幂等，未知结构归零。
- [ ] 迁移纯函数单测覆盖 header/headers/secret_header/异常结构。
- [ ] `harness-mcp` 约定与字段词典更新。
- [ ] 文档明确 V1 仍不支持 stdio。

### Spec Delta

```json
[]
```

### Log

- [2026-11-12] created

### Verification

- 尚未执行