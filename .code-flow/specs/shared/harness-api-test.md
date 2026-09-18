---
id: harness-api-test
description: Agent Harness 通用平台规则：api、test
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-api-001
  type: manual
  config:
    checklist: 确认 Console/内部 API 使用统一封套、错误码来自 config/api-messages.yaml、分页符合 07 §11。
    owner: project-owner
- rule: RULE-test-001
  type: manual
  config:
    checklist: 确认关键流程 E2E 且明确不得 mock 的真实边界（PG/Redis/HTTP/浏览器）。
    owner: project-owner
---

# harness-api-test

## Rules

- [RULE-api-001] Console 与内部 API 使用统一封套：外部 `{code,msg,data,trace_id,request_id,timestamp}`；列表统一 `{items,page,page_size,total}`，`page>=1`、`1<=page_size<=100`；业务只抛 error code，`msg`/`http_status` 只来自 `config/api-messages.yaml`。
- [RULE-test-001] 跨 API/DB/Runtime/Browser 的关键流程必须 E2E 且明确“不得 mock 的真实边界”（真实 PostgreSQL、真实 Redis 行为、真实 HTTP、真实浏览器渲染）；单元测试覆盖纯逻辑与状态机，契约测试覆盖枚举/错误码/迁移一致性。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
