---
name: business-skill-python-template
description: Python 业务 Skill 模板：session-manager 取登录态，requests 调业务接口，交付物写 SKILL_OUTPUT_DIR。
---

# Python 业务 Skill 模板

复制整个目录改三处常量即可：`PLATFORM`（平台 key）、`SKILL_NAME`（本 Skill 名）、`EXAMPLE_PATH`（业务接口）。

## 执行方式

模型只调用统一的 `execute`（`target="skill"`）：

```text
execute(target="skill", skill_key="business-skill-python-template", argv=["--company-id", "123"])
```

脚本从 `session-manager get-state --skill-name <skill>` 读取登录态（单平台/多平台都用这一种；平台集合由 Console 中该 Skill 关联的 `platforms` 决定）。`--platform` 是 agent 工作区/排查工具的单平台入口，不用于 Skill 脚本。

- `platforms.<PLATFORM>.cookies` → `Cookie` 请求头；
- `platforms.<PLATFORM>.requestProfile.baseUrl` → 业务地址；
- `requestProfile.hostHeader` → `Host` 请求头（平台按 vhost 路由，缺了会 404 / 403）；
- `requestProfile.csrf` 声明的 cookie/header 映射 → CSRF 请求头；
- `requestProfile.staticHeaders` → 附加静态头（如 `Timezone`）；
- `requestProfile.referer` → 有则加 `Referer`。

不要在脚本里硬编码 baseUrl、Cookie、Token 或 CSRF 值。

## 目录与输出

- `SKILL_OUTPUT_DIR`：每次执行独立，放本次要交付的报告/图片/结果 JSON；
- `SKILL_DATA_DIR`：同一 user + Agent 跨执行持久，放状态、缓存、锁；
- Skill 包根只读，不要写包根或 `/tmp`。

## 失败语义

失败写 stderr 并返回非零退出码（fail loud），stderr 最后一行输出结构化 `error` 信封（`code` / `message` / `retryable`）；成功时 stdout 只输出一个机器可读的 JSON。取登录态失败时，CLI 的 stderr 里有 `platform_sessions`；失败文案要从这里读实际失败的平台，不要写死平台名。

客户设备查询接入 `customer-devices` 结果契约时，确认无设备后返回 `NO_DEVICES`、`customer_id` 和空的 `devices`。需要选择时返回 `NEEDS_INPUT` 和已获授权的 `candidates`（每项含 `id`、`name`）。两种业务结果均正常退出。查询失败仍按上面的异常协议报告。

## 本地调试

沙箱外没有 `session-manager` 时，把同形状的 state 文件放到 `MUAD_LOCAL_SESSION_STATE` 指向的路径，
脚本会优先使用它；仓库内的 `tools/fake-business-platform/` 可以生成这种 state 并做端到端验证。
