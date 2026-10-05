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

- 模型批量测试为 Console 侧 OpenAI 兼容探测：`GET {base_url}/models`（Bearer `api_key`），404/405 回退最小 chat（`max_tokens=1`）；超时 5s、受限并发 ≤4，禁止接入 Runtime 的模型调用实现（`ModelGateway` 只被 `tests/agent_runtime/test_model_recovery.py` 导入、不在生产链路；Runtime 真实调用链是 `apps/agent-runtime/src/muad_agent_runtime/application/executor.py:233` 的 `AuditedModelProvider` 包 `OpenAIProvider`）。
- `enabled=false` 短路为 `FAILED + MODEL_DISABLED` 且不发请求；401/403 → `CREDENTIAL_MISSING`；超时/连接失败/5xx → `MODEL_UNAVAILABLE`；其它 → `COMMON_INTERNAL_ERROR`；单项失败不改变整体 HTTP 200。
- 批量测试只更新 `last_test_status/last_test_at`，不递增 `revision`、不写 `model_invocation_audit`、不产生计费/上下文语义。
- **`protocol` 创建后不可变更**：`PUT /api/v1/models/{id}` 带 `protocol` 或改 `key` 均返回 422 `COMMON_VALIDATION_ERROR`（`tests/console_platform/test_models_api.py:159-167`）。✅ 只更新 `base_url`/`params` 等可变字段；❌ 让 `protocol` 可改（同一模型的存量大面积调用语义会无声切换）。
- **429 走有界重试而非直接失败**：provider 遇限流抛 `ModelRateLimitedError(retry_after=...)`（`packages/agent-core/src/muad_agent_core/model/openai_provider.py:189-190`），`ModelGateway` 按 `retry_after` 或 `base_delay * 2**attempt` 指数退避，超过 `max_retries` 或逼近 deadline 才抛 `MODEL_UNAVAILABLE`（`apps/agent-runtime/src/muad_agent_runtime/application/model_gateway.py:103-123`）。✅ 退避有上界（重试次数 + deadline 双约束）；❌ 无界重试或把 429 当终态失败。

❌ 错误（越层：批量测试探测不得复用 Runtime 的模型调用实现，`ModelGateway`/`AuditedModelProvider` 均由 Runtime 持有）：

```python
await runtime_model_gateway.probe(model)  # Runtime 侧实现，Console 批量测试不得调用
```

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
