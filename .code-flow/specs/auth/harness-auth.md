---
id: harness-auth
description: Agent Harness 通用平台规则：auth
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-auth-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q tests/console_platform/test_user_side_relations.py
      -k s04 && uv run pytest -q tests -k schema_parity
    cwd: .
    timeout: 600
---

# harness-auth

## Rules

- [RULE-auth-001] 授权为三层关系：User→Agent（AgentAccessGrant）、Agent→Skill/MCP（Binding）、SELECTED 资源再叠加 SkillUserGrant/McpUserGrant；Effective Capability 公式必须含 `is_deleted=false` 与资源/Agent `enabled`；绑定无启停开关、授权无到期时间；不建三元授权、不做 MCP Tool 级授权；未授权资源不得进入 Prompt/ToolRegistry/Skill Catalog。

## Conventions

Console 身份、会话与凭据管理（每条末附机器检查）：

- **仅两级角色，凭据类路由必须挂 `admin` 组**：角色只有 `ADMIN`/`BUILDER`（`infrastructure/models/auth.py:9-10`），创建账号按白名单校验 role（`application/auth_service.py:21,149-150`）。`accounts`/`users`/`credentials` 三组路由挂 `admin` 组（`require_admin`，非 ADMIN → `403 FORBIDDEN`），其余配置类路由挂 `authenticated`（`api/router.py:27-46`）。凭据管理的 ADMIN 门控是**前端隐藏 + 后端 403 兜底**的双层要求，缺一不可。
  - ✅ `admin.include_router(credentials_router)`
  - ❌ 把 `credentials_router` 挂回 `authenticated`（越权修复前状态：任意已登录账号可读写凭据）
  - 机检：`tests/console_auth/test_rbac.py::test_builder_cannot_access_credentials_routes`
- **租户是主体属性，不是请求属性**（2026-09-28 全量收敛）：用户态/管理态路由的租户**只能**取自登录账号——`deps.TenantId` 的默认实现就是 `AccountTenantId`（`account.tenant_id`）。请求头 `X-Tenant-Id` 由中间件写入 contextvar（`packages/api-kit/src/muad_api/middleware.py:36,45`），**可被客户端任意改写**，故只允许两处显式 opt-in：内部服务路由（`/internal/*`，另有服务身份门控）与公开登录（`/auth/login`，无会话可依、只能靠头按租户定位账号）——它们用显式命名 `HeaderTenantId`。`get_tenant_id` 已从 deps 删除，残留调用会 import 失败而非静默降级。
  - 现状：`accounts`/`agents`/`audits`/`auth`(已认证端点)/`credentials`/`mcp_servers`/`models`/`overview`/`platform_test`/`platforms`/`schedules`/`skills`/`tasks`/`users` 共 14 处均为账号租户；`internal_runtime`/`internal_channel` 用 `HeaderTenantId`
  - ✅ `TenantId = AccountTenantId`（默认即安全）；内部/公开入口显式 `TenantId = HeaderTenantId`
  - ❌ 已认证路由上用 `HeaderTenantId`：各 service 的 `row.tenant_id != tenant_id` 只校验「这行属不属于你**要**的租户」，拦不住「你要的租户是不是**你的**」——伪造头即可读写他租户数据
  - 机检：`tests/console_auth/test_rbac.py::test_authenticated_tenant_comes_from_account_not_header`（带伪造头时列表仍须含本账号租户的数据）
  - 登记缺口（实现待收敛）：`accounts`/`users`/`agents`/`models`/`skills`/`platforms`/`mcp_servers`/`overview`/`audits` 等已认证路由仍用 `get_tenant_id()` —— 未认证的公开登录入口按头收窄用户名查找（`api/auth.py:33`）不在此约束内。
  - 机检：`tests/console_platform/test_credentials_api.py::test_credentials_tenant_comes_from_account_not_header`
- **CSRF 双提交 + Cookie 属性**：非安全方法（`GET/HEAD/OPTIONS/TRACE` 之外）必须比对 `muad_csrf` Cookie 与 `X-CSRF-Token` 头（`hmac.compare_digest`，失败 `403`）；会话 Cookie `muad_session` 为 `httponly`、CSRF Cookie 必须可读、两者 `samesite=strict`、`secure` 仅非 dev 打开。`authenticated` 与 `admin` 两组都挂 `require_csrf`（`api/security.py:9-13,20-47,52-58`；`api/router.py:27,42`；写 Cookie 见 `api/auth.py:50`）。
- **会话唯一权威源是 PG `control.console_session`**：令牌只存 `sha256` 摘要、库里无明文列；登录按当前平台设置 `auth.session_ttl_hours`（默认 12h）签发，**滑动阈值是派生值**——本会话签发窗口（`expires_at - issued_at`）的一半，剩余低于一半时续期到 `now + 当前平台 TTL`；登出置 `revoked_at` 且幂等（重复登出不报错）。无状态 JWT 会绕过吊销与滑动续期，禁止引入（`application/auth_service.py:119-137`；`infrastructure/models/auth.py:49-80`）。
  - ✅ 库里只有 sha256 摘要；❌ 存明文或改用无状态 JWT
  - 机检：`tests/console_auth/test_login.py:99-102`
- **已认证会话的解析不得依赖设置可读（ADR-12）**：会话解析发生在**每个**请求上，若它也读平台设置，一条 schema 坏掉的 `platform_setting` 行就会让该租户**全部**已认证请求失败——把「一次保存写坏」放大成「整个租户锁死」。因此 `RULE-06`「设置源不可读即明确失败」只约束**业务操作**与**登录/续期这两个策略生效点**，**不适用于普通请求的会话解析**：解析用本会话签发时冻结的 TTL（`expires_at - issued_at`）判滑动阈值，**只在真要续期时**才读当前 `auth.session_ttl_hours`（`application/auth_service.py:127-133`）。这样既满足「之后签发/续期的会话按新值」，又不动已签发会话，还让普通请求零额外设置读取（同守 NFR-PERF-01）。
- **密码与失败锁定**：`argon2id`（`$argon2id$` 前缀）、最短 12 字符；连续 5 次失败锁 15 分钟且**锁定时把计数清零**（恢复后重新计 5 次）；未知用户与禁用账号都走 dummy hash 等化时序并统一回 `INVALID_CREDENTIALS`，不泄露账号是否存在（`application/auth_service.py:22-30,61-75,105-111`）。
  - ✅ 未知用户先 `verify_password(_DUMMY_HASH, password)` 再报错；❌ 提前 `return` 造成响应时间差
  - 机检：`tests/console_auth/test_login.py:106-112`；`tests/acceptance/console_auth_flow/test_auth_acceptance.py:220-239`
- **账号创建与登录成功必须写 `control.config_audit_log`**：审计与业务变更**共用同一 session（同一事务）**，actor 为创建者（创建）或账号本人（登录），载荷不含密码哈希（登录只记 username/role）；幂等重放没有产生新变更，**不写新审计**（`api/accounts.py:64-79`；`api/auth.py:39-49`；`application/audit_service.py:57-78`）。
- **`resolve-definition` 与 API-08/09 同服务身份门控**：响应含模型明文 `api_key`，必须要求 `X-Internal-Service`（`require_service_identity`），三个 internal 端点一致；Console 侧调用方（Worker `scheduler/client.py:57`、Runtime `infrastructure/console_client.py:50,100`）必须发该头。
  - ✅ 门控 + 调用方带头；❌ 裸端点（越权修复前状态：无头即可读到明文密钥）
  - 机检：`tests/console_internal/test_runtime_credentials.py::test_resolve_definition_requires_service_identity`
- **`POST /api/v1/accounts` 的 `Idempotency-Key` 复用既有共享幂等表**：落在 `control.skill_import_idempotency`（不建第二张表），指纹 = 规范化 JSON（`sort_keys` + 紧凑分隔符, `ensure_ascii=False`）的 `sha256:`，含 endpoint/tenant/username/display_name/role；同 key 异指纹 → `IDEMPOTENCY_MISMATCH`；并发由 `pg_advisory_xact_lock`（按 `(tenant, key, endpoint)` 摘要）串行化（`api/accounts.py:16-17,55`；`application/auth_service.py:181-212,249-267,270-276`）。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
