---
id: harness-secret
description: Agent Harness 通用平台规则：secret
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-secret-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/test_logging_redaction.py
    - tests/acceptance/test_foundation_ops_audit.py
    cwd: .
    timeout: 300
checks:
- id: no-env-model-key
  type: regex
  pattern: MODEL_API_KEY
  files: apps/agent-runtime/**
  message: 运行时禁止用环境变量兜底模型密钥；凭据须经 API-09 从 Owner 表内存读取
---

# harness-secret

## Rules

- [RULE-secret-001] 密钥明文存于各 Owner 表（模型 `api_key`、Bot `secret`、MCP `auth_secret`、用户/共享凭据 `credential_json`；**平台侧无自有密钥列**，见 Conventions 的休眠列说明），跨表以主键引用，不再使用 `secret_ref`/SecretProvider；密钥不得进入日志、`config_audit_log`、Snapshot、LLM Prompt 或 API 响应（对外以 `*_configured` 表达）。

## Conventions

- 所有密钥明文存于各 Owner 表并以主键引用：模型 `model_definition.api_key`、Bot `bot_account.secret`、MCP `mcp_server.auth_secret`、用户/共享凭据 `user_credential_ref.credential_json` / `shared_credential_ref.credential_json`。**平台侧例外**：`project_platform.auth_secret` 是历史遗留的**休眠列**——模型与迁移里确有该列，但 `platform_service.create/update` 从不读写、平台快照不回显、`tests/console_platform/test_platforms_api.py` 断言响应不含它。平台凭据一律走用户/共享凭据表；该列**不得读写，待迁移剔除**。与 `harness-project-platform` 的同名条目对称（两处必须一起改）。
- 不再有 SecretRef/SecretProvider；跨表引用只使用主键（model_id/bot_account_id/mcp_server_id/platform_id/user_id）。
- 脱敏边界不变：密钥不得进入日志、`config_audit_log`、RuntimeSnapshot、LLM Prompt、IM 消息与 API 响应（对外只回 `*_configured`）。
- **禁止面比上述更宽**：密钥同样不得进入 CanonicalEvent、`tool_call_audit`、`egress_audit`、`model_invocation_audit`、IM 出站消息、Skill package 及其 `SKILL.md`。**验收覆盖面以 canary 反查的实际范围为准，勿高估**：`tests/acceptance/im_gateway/test_secrets_and_readiness.py` 的 canary 清单恰为 `runtime.runtime_snapshot` / `runtime.egress_audit` / `runtime.tool_call_audit` / `runtime.model_invocation_audit` 四张表（另加 IM 出站文本与 bot 快照两侧），**不含** `runtime.canonical_event`、`control.config_audit_log`、Skill package 与 `SKILL.md`（`skill_validator.py` 无密钥扫描）。因此「不得进入」是约定，其中一部分**未被机检**——新增落库面时必须自查，不要因为「canary 全绿」就认为已覆盖。
- **唯一受控例外**：内部 bot 快照按最小凭据边界携带 secret，且仅对带 `X-Internal-Service` 的内部调用可见；匿名/越权访问必须 `403 FORBIDDEN`（同上用例断言 canary 存在与不存在两侧）。除该例外，任何"为了联调/调试带上密钥"的做法都属违规。
- **第三条明文出口（受控例外，与 API-08/09 并列）**：`POST /internal/runtime/resolve-definition` 的响应含模型明文 `api_key`，这是**有意契约**（`tests/console_internal/test_resolve_definition_api.py` 断言该字段必在响应里），故只能门控、不能删字段。Console 侧三个 internal 端点（`resolve-definition`/`resolve-credentials`/`resolve-egress-access`）统一由 `require_service_identity` 门控（`apps/console-platform/backend/src/muad_console_platform/api/internal_runtime.py`，2026-09-28 补齐），调用方 Worker/Runtime 必须发 `X-Internal-Service`。
  - ✅ 端点带 `require_service_identity` + 调用方带 `X-Internal-Service` 头。
  - ❌ 裸端点（修复前状态）：无门控却回明文密钥 ⇒ 任意可达该端口的调用方都能取走模型密钥。
- **敏感键清单三处独立维护，无单一来源**：`packages/api-kit/src/muad_api/audit.py`（`SENSITIVE_KEY_MARKERS`，7 键 password/secret/token/api_key/credential/authorization/cookie）、`packages/logging-kit/src/muad_logging/redaction.py`（更宽，另含 `set-cookie`/`id_token`/`passwd`/`private_key`）、`apps/agent-runtime/src/muad_agent_runtime/infrastructure/audit_writer.py`（与 api-kit 同 7 键）。新增敏感键必须三处同改。
  - ✅ 一处新增 → 另两处同步补齐。
  - ❌ 只加 api-kit → 日志与 runtime 三张审计表两侧漏脱敏。
- **两类脱敏语义不同，断言不可混用**：审计侧是**丢弃键**（`packages/api-kit/src/muad_api/audit.py` 的 `sanitize_audit_payload` 直接过滤掉键，故敏感键在 before/after 里**整个消失**；`tests/acceptance/test_secret_consumers.py` 断言 `sanitize_audit_payload({"api_key": ..., "secret": ..., "name": "ok"}) == {"name": "ok"}`）；日志侧是**值置换 `***`**（`packages/logging-kit/src/muad_logging/redaction.py` 的 `REDACTED = "***"`）。
  - ❌ 按 `***` 语义写审计断言：键已被丢弃，永远匹配不到该字面量。
- **`*_configured` 命名有一处例外**：用户凭据读接口回的是 `"configured": True` + `credential_schema_version`/`status`/`last_verified_at`（`apps/console-platform/backend/src/muad_console_platform/application/credential_service.py` 的 `_user_credential_payload`），字段名不是 `*_configured`。该例外只表达「已配置」这一事实，仍不得回明文。

✅ 正确：

```python
model = ModelDefinition(key="gpt-4o", base_url=..., model_id="gpt-4o-mini", api_key=payload.api_key)
```

```python
bot = BotAccount(tenant_id=..., bot_id=..., secret=payload.secret, agent_id=agent_id)  # 明文列
```

❌ 错误：

```python
logger.info("model created api_key=%s", payload.api_key)               # 进入日志
await session.execute(audit_insert, {"after": {"api_key": ...}})      # 进入审计
return {"api_key": model.api_key}                                     # 出现在 API 响应
```

- 运行时凭据解析走内部 API-09（`POST /internal/runtime/resolve-credentials`）：按冻结主键（`model_id`/`mcp_server_ids`）实时读取 Owner 表当前密钥，仅在执行内存使用；模型密钥清空明确 `CREDENTIAL_MISSING`，资源移除明确 `COMMON_NOT_FOUND`，禁止退回环境变量。MCP 鉴权可选，未配置时返回 `auth_secret: null`，客户端不发送 Authorization；已配置时读取当前值，轮换即时生效。新 Run 与 resume/重试均按运行流程实时解析。
  - ✅ 无鉴权 MCP 的 Owner 行存在且 `auth_secret=None` 时正常返回；❌ 把可选 MCP 凭据缺失当成模型密钥缺失。
  - 机检：`tests/console_internal/test_runtime_credentials.py::test_runtime_credentials_support_mcp_without_auth`。

✅ 内存凭据（轮换后 resume 用新值）：

```python
data = await credentials_client.resolve_credentials(tenant_id=t, payload={"execution_ref": {"type": "RUN", "id": run_id}, "model_id": model_id, "mcp_server_ids": ids})
api_key = data["model"]["api_key"]           # 只在内存，不落 Snapshot/日志/审计
```

❌ 环境变量兜底（设计明确禁止）：

```python
api_key = model.api_key or os.environ.get("MODEL_API_KEY")   # 密钥清空时应 CREDENTIAL_MISSING
```

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
