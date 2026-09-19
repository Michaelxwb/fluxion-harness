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
- rule: RULE-api-002
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/console_skill/test_import_idempotency.py
    cwd: .
    timeout: 300
---

# harness-api

## Rules

- [RULE-api-002] 创建/上传类 POST（导入、可重试提交）支持 `Idempotency-Key` Header：DB 幂等表 partial unique `(tenant_id, idempotency_key, endpoint)` 记录首次响应；请求指纹 = endpoint|关键参数|内容 checksum；同 key 同指纹重放返回首次结果（200 原响应），同 key 不同指纹返回 `COMMON_CONFLICT`。
- [RULE-api-001] Console 与内部 API 使用统一封套：外部 `{code,msg,data,trace_id,request_id,timestamp}`；列表统一 `{items,page,page_size,total}`，`page>=1`、`1<=page_size<=100`；业务只抛 error code，`msg`/`http_status` 只来自 `config/api-messages.yaml`。

## Conventions

列表封套适用于**所有**列表端点，包括子资源列表（`/{parent}/{id}/items`）；返回裸数组会导致前端 Page 解析失败（`page.items` 为 undefined、表格恒空——05 指定用户 Tab 真实事故）。

✅ 子资源列表分页：

```python
items, total = await service.list_grants(tenant_id, skill_id, page, page_size)
return ok(catalog, paginate(items=[...], page=page, page_size=page_size, total=total))
```

❌ 裸数组：

```python
return ok(catalog, [item.model_dump() for item in items])   # 前端 Page<T> 解析为 undefined
```

## Conventions

服务底座统一复用 api-kit 原语，业务代码不得自建同类机制：

| 原语 | 用途 |
|---|---|
| `install_api_foundation(app, ...)` | 上下文中间件 + 统一异常/Envelope + `MessageCatalog` |
| `install_console_security(app, session_verifier, role_resolver)` | 会话校验（401 `UNAUTHORIZED`）与角色依赖（403 `FORBIDDEN`）|
| `install_health_probes(app, readiness_checks)` | `/healthz` 与 `/readyz`（依赖缺失返回 503）|
| `validate_startup(settings, engine, artifact_store)` | 配置/迁移到 head/存储挂载校验，失败快速退出 |
| `write_config_audit(session, ...)` | 业务同事务写 `control.config_audit_log`，Secret 键值不落盘 |

✅ 正确（装配共享底座，由 api-kit 统一行为）：

```python
install_api_foundation(app)
install_console_security(app, session_verifier, role_resolver)
install_health_probes(app, {"db": engine_probe})
await validate_startup(settings, engine, artifact_store, migrations_dir=...)
```

❌ 错误（自建探针/校验/审计，绕过 api-kit）：

```python
@app.get("/healthz")
async def healthz():          # 自建探针，缺少依赖态 503 与统一实现
    return {"ok": True}
```

## Conventions

- `message_args` 只有在 YAML 模板包含对应 `{placeholder}` 时才会渲染；通用错误码（如 `COMMON_CONFLICT`）不得承载需要展示给用户的细节。需要展示细节（引用数、字段名等）时必须登记专用错误码与中英文模板。

✅ 专用错误码 + 占位符：

```yaml
MODEL_IN_USE:
  http_status: 409
  messages:
    zh-CN: "模型被 {agent_count} 个 Agent 引用，无法删除"
    en-US: "Model is referenced by {agent_count} agent(s)"
```

❌ 用通用码携带细节，前端只能看到通用文案：

```python
raise AppError(ErrorCode.COMMON_CONFLICT, message_args={"agent_count": 3})
```

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
