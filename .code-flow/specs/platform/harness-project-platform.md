---
id: harness-project-platform
description: Agent Harness 通用平台规则：platform
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-platform-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q tests -k schema_parity
    cwd: .
    timeout: 300
---

# harness-project-platform

## Rules

- [RULE-platform-001] ProjectPlatform 只保存实例与寻址配置，认证/Session 由 PlatformAdapter 承担（SPI 无独立 `refresh`，刷新属于 `authenticate` 内部）；凭据明文存于凭据表并由主键引用，`credential_mode` 仅表达选择策略（USER_ONLY/SHARED_ONLY/USER_THEN_SHARED/NONE）；PlatformSession 为可重建 Redis 缓存，键为 `platform_session:{tenant_id}:{platform_id}:{actor_scope}:{credential_version}:{adapter_key}:{adapter_version}` 并以 Set 索引清理；更换 adapter_key 必须使旧凭据与会话失效。

## Conventions

凭据 HTTP API 的访问、出网与密钥面：

- **凭据类 HTTP API 仅 ADMIN，且租户取自登录账号**：`credentials_router` 挂在 `admin` 组（`api/router.py:42-46`，非 ADMIN → `403 FORBIDDEN`），路由内 `TenantId = AccountTenantId`（`api/credentials.py:15-16`）——修复前是「已登录即可」× 请求头派生租户，任意已登录账号改一个头即可跨租户读写凭据。
  - ✅ `TenantId = AccountTenantId`；❌ `TenantId = Annotated[str, Depends(get_tenant_id)]`
  - 机检：`tests/console_auth/test_rbac.py`、`tests/console_platform/test_credentials_api.py::test_credentials_tenant_comes_from_account_not_header`
- **删除 user/shared 凭据必须同时清空该平台的 Session 索引**：凭据与 Session 生命周期绑定，只软删凭据行会让旧 Session 继续拿失效凭据出网（`application/credential_service.py:146-163,212-229` 两条删除路径都调 `self._sessions.clear_platform(...)`）。
  - ✅ 软删凭据行 → `clear_platform`；❌ 只软删凭据行
  - 机检：`tests/console_platform/test_platform_session_invalidation.py:181-216`
- **`POST /{platform_id}/test` 只做连通性探测**：返回 `connectivity` 与 `credential_ref_status`（`ACTIVE`/`MISSING`/`NOT_CHECKED`），不得读取或回显凭据明文（`api/platform_test.py:28-47`；`application/platform_test_service.py:60-95`）。
  - ❌ 响应体里出现 `credential_json` / token 明文
- **出网目标双重校验**：Console 侧 HTTP target 的 host 必须落在平台 `resolver_config.base_url` 或 `adapter_config.allowlist` 内，且 method 限于 `GET/POST/PUT/DELETE/PATCH`（`application/resolve_egress_service.py:104-123`）；SDK 侧再经 `EgressBoundary`（host allowlist、禁跳转、`max_bytes` 默认 5MiB、必填 `timeout_sec`，拒绝时请求不发出）（`packages/platform-sdk/src/muad_platform_sdk/egress_boundary.py:1,10-26`）。
- **适配器注册表键唯一且带版本**：重复 key 直接抛 `PlatformAdapterAlreadyRegistered`（`packages/platform-sdk/src/muad_platform_sdk/adapter/registry.py:22-25`）；`generic-http` 版本 `"1"`，额外注册的 key 版本 `"2"`（`application/platform_adapter_service.py:8-15`；`packages/platform-sdk/src/muad_platform_sdk/adapter/generic_http.py:53`）。更换 adapter_key 时刷新 `adapter_schema_version` 并使旧凭据/Session 失效（`application/platform_service.py:203-205`）。
- **密钥不回显、不入审计、不入日志**：凭据保存审计的 `after` 只含 `credential_schema_version`/`status`（`application/credential_service.py:125-138,192-204`）；platform 快照不含 `auth_secret`（`tests/console_platform/test_platforms_api.py:145`）；`PlatformSession` 的 token/cookie 字段 `repr=False`（`packages/platform-sdk/src/muad_platform_sdk/types.py:41-46`）。
- **平台认证配置的唯一合法载体是凭据表**：平台级凭据一律走 `shared_credential_ref.credential_json`、用户级走 `user_credential_ref.credential_json`（`credential_mode` 只是选择策略）。`project_platform.auth_secret` 是历史遗留的**休眠列**——模型与迁移里有列（`infrastructure/models/control.py:424`、`migrations/versions/0004_secret_plaintext_expand.py:18-22`），但 `platform_service.create/update` 从不读写、快照不回显、有测试断言响应不含它（`tests/console_platform/test_platforms_api.py:145`）。**该列不得读取、不得写入**，待迁移剔除；新增代码不得以它为平台密钥载体。
  - ✅ 平台密钥写 `shared_credential_ref`；❌ 往 `project_platform.auth_secret` 写值或从它读值
- 登记缺口（实现待落地）：`PlatformSession` 的「可重建 Redis 缓存」目前只有键构造器（`packages/platform-sdk/src/muad_platform_sdk/session/key.py:22-28`）、Set 索引失效器（`session/invalidator.py:9-15`）与端口 `Protocol`（`session/manager.py:18-23`），**无生产侧 manager 实现**，`credential_version` 也没有生产赋值点（仅测试传字面量 `"v1"`）——当前按契约约定对待。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
