---
id: harness-api
description: Agent Harness 通用平台规则：api
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-api-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/test_api_i18n.py
    - tests/test_error_catalog.py
    - tests/acceptance/test_foundation_api_envelope.py
    cwd: .
    timeout: 300
---

# harness-api

## Rules

- [RULE-api-001] Console 与内部 API 使用统一封套：外部 `{code,msg,data,trace_id,request_id,timestamp}`；列表统一 `{items,page,page_size,total}`，`page>=1`、`1<=page_size<=100`；业务只抛 error code，`msg`/`http_status` 只来自 `config/api-messages.yaml`。

## Conventions

服务底座统一复用 api-kit 原语，业务代码不得自建同类机制：

| 原语 | 用途 |
|---|---|
| `install_api_foundation(app, ...)` | 上下文中间件 + 统一异常/Envelope + `MessageCatalog` |
| `install_console_security(app, session_verifier, role_resolver)` | 会话校验（401 `UNAUTHORIZED`）与角色依赖（403 `FORBIDDEN`）|
| `install_health_probes(app, readiness_checks)` | `/healthz` 与 `/readyz`（依赖缺失返回 503）|
| `validate_startup(settings, engine, artifact_store, secret_provider)` | 配置/迁移到 head/存储挂载/SecretProvider 可达，失败快速退出 |
| `write_config_audit(session, ...)` | 业务同事务写 `control.config_audit_log`，Secret 键值不落盘 |

✅ 正确（装配共享底座，由 api-kit 统一行为）：

```python
install_api_foundation(app)
install_console_security(app, session_verifier, role_resolver)
install_health_probes(app, {"db": engine_probe})
await validate_startup(settings, engine, artifact_store, secret_provider, migrations_dir=...)
```

❌ 错误（自建探针/校验/审计，绕过 api-kit）：

```python
@app.get("/healthz")
async def healthz():          # 自建探针，缺少依赖态 503 与统一实现
    return {"ok": True}
```

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
