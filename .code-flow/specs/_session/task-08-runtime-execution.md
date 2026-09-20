# TASK-015 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules
- `harness-project-platform#RULE-platform-001`: ProjectPlatform 只保存实例与寻址配置，认证/Session 由 PlatformAdapter 承担（SPI 无独立 `refresh`，刷新属于 `authenticate` 内部）；凭据明文存于凭据表并由主键引用，`credential_mode` 仅表达选择策略（USER_ONLY/SHARED_ONLY/USER_THEN_SHARED/NONE）；PlatformSession 为可重建 Redis 缓存，键为 `platform_session:{tenant_id}:{platform_id}:{actor_scope}:{credential_version}:{adapter_key}:{adapter_version}` 并以 Set 索引清理；更换 adapter_key 必须使旧凭据与会话失效。
  - rule_sha256=9bfa326bb7da3ce17fab7298a71fd493548b90109dc9850a2645170ba1ce61c1 verifier=harness-project-platform#RULE-platform-001; artifacts=08-runtime-execution.backend.design.md,08-runtime-execution.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-05 | integration | 真实 Skill→Egress Boundary→HTTP 探针/PostgreSQL | 拒绝 host 不发出请求，FORBIDDEN；DENY/HTTP 审计；timeout/5 MiB/redirect 边界；平台与 MCP 同一策略 | tests/agent_runtime/test_egress_boundary.py -k e05（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_egress_boundary.py","-k","e05"] | planned |
| RULE-platform-001 | integration | 真实 Skill→Egress Boundary→HTTP 探针/PostgreSQL＋原 verifier 边界 | 拒绝 host 不发出请求，FORBIDDEN；DENY/HTTP 审计；timeout/5 MiB/redirect 边界；平台与 MCP 同一策略；原 verifier 全部通过 | 原 verifier＋tests/agent_runtime/test_egress_boundary.py, tests/sdk/test_runtime_platform_session.py（planned） | ["bash","-lc","uv run pytest -q tests -k schema_parity && uv run pytest -q tests/agent_runtime/test_egress_boundary.py tests/sdk/test_runtime_platform_session.py"] | planned |
