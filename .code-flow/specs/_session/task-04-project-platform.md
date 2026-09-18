# TASK-012 Spec Context

- Context-SHA256: `e19da46a57c6c50e224779d993d2e8fc9e728cee59f06a496b213462d18434fb`

## Required Rules
- `harness-test#RULE-test-001`: 跨 API/DB/Runtime/Browser 的关键流程必须 E2E 且明确“不得 mock 的真实边界”（真实 PostgreSQL、真实 Redis 行为、真实 HTTP、真实浏览器渲染）；单元测试覆盖纯逻辑与状态机，契约测试覆盖枚举/错误码/迁移一致性。
  - rule_sha256=bab83fe9dee30b1c16035587a06594e3415bad90eb31beb58d329025775136ac verifier=harness-test#RULE-test-001; artifacts=04-project-platform.backend.design.md,04-project-platform.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | Browser、API、DB | 见 TASK-003 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-01"` | planned |
| S-02 | E2E | Browser、API、DB | 见 TASK-004 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-02"` | planned |
| S-03 | E2E | Browser、API、网络探测、UI | 见 TASK-005 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-03"` | planned |
| S-05 | E2E | Browser、adapter metadata、Form | 见 TASK-008 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-05"` | planned |
| S-06 | E2E | Browser、Secret API | 见 TASK-010 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-06"` | planned |
| S-07 | E2E | Browser、API、网络探测、UI | 见 TASK-010 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-07"` | planned |
| S-08 | E2E | Browser、API、DB | 见 TASK-011 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-08"` | planned |
| E-05 | E2E | PUT API、UI | 见 TASK-009 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "E-05"` | planned |
| E-06 | E2E | API、网络探测、UI | 见 TASK-010 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "E-06"` | planned |
