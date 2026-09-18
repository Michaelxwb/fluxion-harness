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
---

# harness-secret

## Rules

- [RULE-secret-001] 密钥明文存于各 Owner 表（模型 `api_key`、Bot `secret`、MCP/平台 `auth_secret`、用户/共享 `credential_json`），跨表以主键引用，不再使用 `secret_ref`/SecretProvider；密钥不得进入日志、`config_audit_log`、Snapshot、LLM Prompt 或 API 响应（对外以 `*_configured` 表达）。

## Conventions

- 所有密钥明文存于各 Owner 表并以主键引用：模型 `model_definition.api_key`、Bot `bot_account.secret`、MCP `mcp_server.auth_secret`、平台 `project_platform.auth_secret`、用户/共享凭据 `user_credential_ref.credential_json` / `shared_credential_ref.credential_json`。
- 不再有 SecretRef/SecretProvider；跨表引用只使用主键（model_id/bot_account_id/mcp_server_id/platform_id/user_id）。
- 脱敏边界不变：密钥不得进入日志、`config_audit_log`、RuntimeSnapshot、LLM Prompt、IM 消息与 API 响应（对外只回 `*_configured`）。

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

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
