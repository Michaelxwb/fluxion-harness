# TASK-003 Spec Context

- Context-SHA256: `e19da46a57c6c50e224779d993d2e8fc9e728cee59f06a496b213462d18434fb`

## Required Rules
- `harness-api#RULE-api-001`: Console 与内部 API 使用统一封套：外部 `{code,msg,data,trace_id,request_id,timestamp}`；列表统一 `{items,page,page_size,total}`，`page>=1`、`1<=page_size<=100`；业务只抛 error code，`msg`/`http_status` 只来自 `config/api-messages.yaml`。
  - rule_sha256=615724c773399b4ce032ae7934787e55501ac643ed0a9d2648e135e5003c8ab6 verifier=harness-api#RULE-api-001; artifacts=04-project-platform.backend.design.md,04-project-platform.md
- `harness-i18n#RULE-i18n-001`: 后端错误消息与前端页面必须支持 zh-CN/en-US；新增业务仅新增配置/词条，不改框架代码；语言经 `X-Locale`/`Accept-Language` 协商。
  - rule_sha256=9c55063508523538b2ea446555d14c72d2deefcf6209c6624a019477974ff09c verifier=harness-i18n#RULE-i18n-001; artifacts=04-project-platform.backend.design.md,04-project-platform.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Browser、API、DB | 平台按 Schema 保存/展示；重复 key 拒绝 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-01"` | planned |
| E-01 | integration | API、Registry、DB | `PLATFORM_ADAPTER_NOT_FOUND`；事务未落库 | tests/console_platform/test_platforms_api.py（planned） | `uv run pytest -q tests/console_platform/test_platforms_api.py -k e01` | planned |
| E-03 | integration | API、DB | `COMMON_CONFLICT` + `message_args.key`；列表不变 | tests/console_platform/test_platforms_api.py（planned） | `uv run pytest -q tests/console_platform/test_platforms_api.py -k e03` | planned |
