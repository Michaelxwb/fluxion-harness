# TASK-006 Spec Context

- Context-SHA256: `e19da46a57c6c50e224779d993d2e8fc9e728cee59f06a496b213462d18434fb`

## Required Rules
- `harness-project-platform#RULE-platform-001`: ProjectPlatform 只保存实例与寻址配置，认证/Session 由 PlatformAdapter 承担（SPI 无独立 `refresh`，刷新属于 `authenticate` 内部）；凭据明文存于凭据表并由主键引用，`credential_mode` 仅表达选择策略（USER_ONLY/SHARED_ONLY/USER_THEN_SHARED/NONE）；PlatformSession 为可重建 Redis 缓存，键为 `platform_session:{tenant_id}:{platform_id}:{actor_scope}:{credential_version}:{adapter_key}:{adapter_version}` 并以 Set 索引清理；更换 adapter_key 必须使旧凭据与会话失效。
  - rule_sha256=9bfa326bb7da3ce17fab7298a71fd493548b90109dc9850a2645170ba1ce61c1 verifier=harness-project-platform#RULE-platform-001; artifacts=04-project-platform.backend.design.md,04-project-platform.frontend.design.md,04-project-platform.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | integration | Service、DB、Redis | 凭据 `INVALID`；Set 索引键被 `DEL`；响应 `credential_reconfigure_required=true`；审计落库 | tests/console_platform/test_platform_session_invalidation.py（planned） | `uv run pytest -q tests/console_platform/test_platform_session_invalidation.py -k s04` | planned |
