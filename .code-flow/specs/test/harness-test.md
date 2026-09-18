---
id: harness-test
description: Agent Harness 通用平台规则：test
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-test-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend
      run build && npm --prefix e2e test
    cwd: .
    timeout: 1200
---

# harness-test

## Rules

- [RULE-test-001] 跨 API/DB/Runtime/Browser 的关键流程必须 E2E 且明确“不得 mock 的真实边界”（真实 PostgreSQL、真实 Redis 行为、真实 HTTP、真实浏览器渲染）；单元测试覆盖纯逻辑与状态机，契约测试覆盖枚举/错误码/迁移一致性。

## Conventions

标准 E2E 设施（不得自建临时浏览器脚本）：

- 浏览器用例放 `e2e/tests/*.spec.ts`（Playwright + 系统 Chrome channel），前端先 `npm --prefix apps/console-platform/frontend run build` 产出真实构建物。
- 后端用 `tests/e2e/app.py`：真实 uvicorn + api-kit 封套/会话原语 + 真实静态产物；E2E 中不得 mock 业务 API。
- 场景命令统一为 `npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --grep "<场景ID>"`；按域配置时用 `npm --prefix e2e test -- --config playwright.<domain>.config.ts --grep "<场景ID>"`。
- 外部依赖（模型/LLM 端点、第三方 API）用真实本地探针服务承载：`tests/e2e/openai_probe_app.py`（真实 HTTP 健康响应）与 Console/Vite 并列写入 `webServer` 数组；禁止在 E2E 中伪造外部响应。

✅ 真实边界（浏览器链路端到端）：

```bash
npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --grep "S-13"
# 断言真实 Chrome：localStorage 语言持久化 + /auth/me 请求头 X-Locale=en-US
```

❌ 不允许：

```ts
// 在 E2E 里伪造业务 API 响应，链路退化为 mock
await page.route('**/api/v1/**', (route) => route.fulfill({ json: { code: '0' } }));
```

- 用 jsdom / 组件单测冒充 E2E，或未渲染真实浏览器即标记 E2E 场景。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
