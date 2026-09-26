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

## Conventions

E2E 创建的业务数据（无删除端点的资源尤其如此）必须：用可识别 key 前缀（如 `e2e-`），并在 spec 的清理钩子或独立清理步骤中删除 DB 记录；否则会污染共享开发库，把真实页面变成"假数据看板"（05 遗留 70 条 `e2e-*` Skill 与用户下拉污染）。上传类资源配套用孤儿清理入口回收文件：`PYTHONPATH=apps/console-platform/backend/src uv run python -c "from muad_console_platform.cli import main; main(['cleanup-skill-orphans','--grace-seconds','0'])"`。

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

## Conventions

Runtime 验收真实环境基建（`tests/acceptance/runtime/`）：

- 用 module-scoped live stack 承载真实边界：Console/Runtime 为真实 uvicorn 服务、LLM/MCP 为本地 HTTP 探针、数据落真实 PostgreSQL/Redis/Artifact Store；禁止 `dependency_overrides`/mock 业务服务。
- 跨 Pod/崩溃场景用真实子进程（`SIGKILL` 后由另一实例 Reaper 回收、同 conversation 无 sticky session 接管）；进程内服务线程与 pytest-asyncio 并存时，须在套件边界清理 engine `lru_cache`，async 工具走独立线程/事件循环（禁止 `asyncio.run` 污染主循环）。
- 验收命令由 acceptance manifest runner 统一复验并写证据：相同 argv/cwd/timeout 复用，测试未收集、未执行或失败均视为 FAIL。

✅ 真实进程 + 真实探针：

```python
with httpx.stream("POST", f"{runtime.url}/v1/runs", json=payload) as stream:
    for line in stream.iter_lines():          # 真实 SSE
        ...
pod_a.kill()                                   # 真实进程终止 → Reaper 回收
```

❌ 直连 DB 冒充 E2E（声称真实 Gateway/HTTP/SSE，实际只 seed 表）：

```python
await session.execute(sa.update(RunRecord).where(...).values(status="RUNNING"))  # 无 HTTP/SSE/执行链
assert (await session.get(RunRecord, run_id)).status == "RUNNING"                # 恒真
```

## Conventions

验收反模式（Anti-Patterns）：

- ❌ 恒真断言：`assert reaped >= 0`、对可能为空集合 `all(...)`、`exclude={"api_key"}` 后再断言无 `api_key`——断言必须能真实失败。
- ❌ 命令与场景不符：`-k` 未命中任何测试（退出码 5）却标记 verified；或测试名/文件与 Acceptance Contract 声明的路径不一致。
- ❌ 验收层级注水：把 service/DB 级测试写成 E2E，或边界文案（真实 Gateway/进程/SSE）与测试实际行为不符。
- ❌ 空转用例：测试名为"跨 host 跳转拒绝"却请求 `/healthz`、`follow_redirects=False` 从未触发跳转。
- ❌ 把验收/Done Gate 命令的输出接进会**提前关闭的管道**（典型 `| head`）：SIGPIPE 会打断 pytest 收尾，`stop_live_stack`/`stop_audit_stack` 不执行，留下 uvicorn/`muad_*.main` 孤儿进程继续连同一个本地测试库 → 后续运行随机失败（如 `SKILL_ARTIFACT_UNAVAILABLE`、任务 `FAILED`），且失败点每次不同、单跑却都通过，极易误判为跨模块 flake。用 `> file` 或 `tail`（会读完输入）；每次运行前先确认无残留进程（`ps aux | grep -E "[u]vicorn|muad_(agent_worker|agent_runtime|console_platform|im_gateway)\.main"`）。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
