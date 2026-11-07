---
name: smoke-platform-sample
description: 验证 smoke_platform 登录态与业务请求链路的示例 Skill（健康检查 + CSRF 业务接口 + Traceid）。
---

# smoke-platform-sample

用于验证平台 `smoke_platform` 的会话链路：`session-manager` 取登录态 → 带 Cookie/CSRF 请求业务接口。

**名称区分**：Skill 名是 `smoke-platform-sample`（横杠），平台 key 是 `smoke_platform`（下划线）。调用工具时只用 Skill 名；脚本里的 `PLATFORM` 常量才是平台 key。

## 执行方式

模型只调用 `execute_skill`：

```text
execute_skill(skill_key="smoke-platform-sample")
```

脚本会：

1. 通过 `session-manager get-state --skill-name smoke-platform-sample` 取当前执行会话；
2. 读取 `platforms.smoke_platform.cookies` 和 `requestProfile.baseUrl`；
3. 按 `requestProfile.csrf` 的 cookie/header 映射带 CSRF 头；
4. 依次请求 `/v1/rtt`（健康）、`/api/v1/ping`（CSRF 业务）、`/api/v1/targets`；
5. 校验平台回显的 `Traceid`，把结果写到 `SKILL_OUTPUT_DIR/smoke-platform-result.json`。

## 本地联调

```bash
python3 tools/fake-business-platform/server.py \
  --platform mssw --host 0.0.0.0 --port 18080 --ak test-ak --sk test-sk

uv run python tools/fake-business-platform/smoke_check.py
```

`smoke_check.py` 会用真实 `MSSAdapter` 登录、生成 state 文件、执行本 Skill 并断言结果。
