---
id: harness-model
description: Agent Harness 通用平台规则：model
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-model-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest
      -q tests/console_platform/test_agents_api.py -k disabled
    cwd: .
    timeout: 600
---

# harness-model

## Rules

- [RULE-model-001] `model_definition` 不设 `is_default`；每个 Agent 必须显式保存 `model_id`，不存在平台默认模型回退；模型调用只允许 OPENAI 兼容协议。

## Conventions

- 模型批量测试为 Console 侧 OpenAI 兼容探测：`GET {base_url}/models`（Bearer `api_key`），404/405 回退最小 chat（`max_tokens=1`）；超时 5s、受限并发 ≤4，禁止接入 Runtime `ModelGateway`。
- `enabled=false` 短路为 `FAILED + MODEL_DISABLED` 且不发请求；401/403 → `CREDENTIAL_MISSING`；超时/连接失败/5xx → `MODEL_UNAVAILABLE`；其它 → `COMMON_INTERNAL_ERROR`；单项失败不改变整体 HTTP 200。
- 批量测试只更新 `last_test_status/last_test_at`，不递增 `revision`、不写 `model_invocation_audit`、不产生计费/上下文语义。

❌ 错误：

```python
await runtime_model_gateway.probe(model)  # 越层：批量测试不得经 Runtime ModelGateway
```

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
