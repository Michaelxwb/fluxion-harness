---
id: harness-model
description: 模型定义与模型池
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

- 模型批量测试为 Console 侧 OpenAI 兼容探测：`GET {base_url}/models`（Bearer `api_key`），404/405 回退最小 chat（`max_tokens=1`）；超时 5s、受限并发 ≤4，禁止接入 Runtime 的模型调用实现（`ModelGateway` 只被 `tests/agent_runtime/test_model_recovery.py` 导入、不在生产链路；Runtime 真实调用链是 `apps/agent-runtime/src/muad_agent_runtime/application/executor.py` 的 `AuditedModelProvider` 包 `OpenAIProvider`）。
- `enabled=false` 短路为 `FAILED + MODEL_DISABLED` 且不发请求；401/403 → `CREDENTIAL_MISSING`；超时/连接失败/5xx → `MODEL_UNAVAILABLE`；其它 → `COMMON_INTERNAL_ERROR`；单项失败不改变整体 HTTP 200。
- 批量测试只更新 `last_test_status/last_test_at`，不递增 `revision`、不写 `model_invocation_audit`、不产生计费/上下文语义。
- **`protocol` 创建后不可变更**：`PUT /api/v1/models/{id}` 带 `protocol` 或改 `key` 均返回 422 `COMMON_VALIDATION_ERROR`（`tests/console_platform/test_models_api.py` 的 PUT 用例）。✅ 只更新 `name/model_id/provider_key/params/enabled` 等可变字段；❌ 让 `protocol` 可改（同一模型的存量大面积调用语义会无声切换）。`base_url`/`api_key` 已不在 `model_definition` 上（0027 物理删除），连接与密钥只能经成员凭据改。
- **429 走有界重试而非直接失败**：provider 遇限流抛 `ModelRateLimitedError(retry_after=...)`（`packages/agent-core/src/muad_agent_core/model/openai_provider.py` 的流式与完整调用两处 raise），`ModelGateway` 按 `retry_after` 或 `base_delay * 2**attempt` 指数退避，超过 `max_retries` 或逼近 deadline 才抛 `MODEL_UNAVAILABLE`（`apps/agent-runtime/src/muad_agent_runtime/application/model_gateway.py`）。✅ 退避有上界（重试次数 + deadline 双约束）；❌ 无界重试或把 429 当终态失败。
- **池化预占的输出预算是模型配置事实，不是用户请求参数**：`params.max_tokens` 必须是正整数；缺失/非法时 `reserved_tokens_for` 抛 `MODEL_OUTPUT_BUDGET_INVALID`（Runtime 带模型/Run 日志），不落 `COMMON_VALIDATION_ERROR`。✅ 用户看到「联系管理员补齐输出预算」并能定位到模型定义；❌ 让 IM 用户看到「请求参数校验失败」——用户的消息没问题，要改的是模型配置（`apps/agent-runtime/src/muad_agent_runtime/application/executor.py`）。

模型池与容量（model-pool-control，0021/0022/0027）：

- **逻辑模型、成员与凭据是三层，密钥只存凭据表**：`model_definition` 只声明 `provider_key`/型号/参数，`model_deployment` 是「逻辑模型 ↔ 物理凭据」的成员行，明文密钥只在 `control.model_credential.api_key`（`api_key_digest` 仅用于去重）。同一凭据在**同一模型下**只能挂一次；成员、模型、凭据必须同租户（复合 FK）。✅ 新增成员走 `model_deployment`；❌ 把 `base_url`/`api_key` 写回 `model_definition`（列已删除）。
- **调度与额度判定各有唯一实现**：成员选择按 `dispatch_count/weight` 加权均衡、跨实例用共享 `dispatch_epoch` 归一，`policy_hash` 标识冻结策略版本；额度判定只在 `ModelCapacityService`（池→quota_group→key→attempt 固定锁序），`model_allocator` 不复制窗口算法。选择与预占是两个短事务，中间不持锁、锁内不联网；预占失败按 `MODEL_CAPACITY_UNAVAILABLE` 明确回压，绝不超发（`apps/agent-runtime/src/muad_agent_runtime/application/model_allocator.py`、`model_capacity_service.py`）。
- **共享额度组的降额/新开限流维度必须先排空**：`runtime.model_call_lease` 仍有该组 `ACTIVE`/`UNKNOWN` 行时，改小任一限流或从「不设限」新开限流是显式冲突；`rpm`/`tpm` 为 `None` 表示该维度不设应用内限流（不是零额度）。删除是软删，仍被活跃凭据引用时显式冲突（`application/model_quota_service.py`）。

❌ 错误（越层：批量测试探测不得复用 Runtime 的模型调用实现，`ModelGateway`/`AuditedModelProvider` 均由 Runtime 持有）：

```python
await runtime_model_gateway.probe(model)  # Runtime 侧实现，Console 批量测试不得调用
```

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
