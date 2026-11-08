---
name: business-skill-python-template
description: Python 业务 Skill 模板：session-manager 取登录态，requests 调业务接口，交付物写 SKILL_OUTPUT_DIR。
---

# Python 业务 Skill 模板

复制整个目录改三处常量即可：`PLATFORM`（平台 key）、`SKILL_NAME`（本 Skill 名）、`EXAMPLE_PATH`（业务接口）。

## 执行方式

模型只调用 `execute_skill`：

```text
execute_skill(skill_key="business-skill-python-template", argv=["--company-id", "123"])
```

脚本从 `session-manager get-state --skill-name <skill>` 读取登录态：

- `platforms.<PLATFORM>.cookies` → `Cookie` 请求头；
- `platforms.<PLATFORM>.requestProfile.baseUrl` → 业务地址；
- `requestProfile.csrf` 声明的 cookie/header 映射 → CSRF 请求头；
- `requestProfile.staticHeaders` → 附加静态头（如 `Timezone`）。

不要在脚本里硬编码 baseUrl、Cookie、Token 或 CSRF 值。

## 目录与输出

- `SKILL_OUTPUT_DIR`：每次执行独立，放本次要交付的报告/图片/结果 JSON；
- `SKILL_DATA_DIR`：同一 user + Agent 跨执行持久，放状态、缓存、锁；
- Skill 包根只读，不要写包根或 `/tmp`。

## 失败语义

失败写 stderr 并返回非零退出码（fail loud）；成功时 stdout 只输出一个机器可读的 JSON。

## 本地调试

沙箱外没有 `session-manager` 时，把同形状的 state 文件放到 `MUAD_LOCAL_SESSION_STATE` 指向的路径，
脚本会优先使用它；仓库内的 `tools/fake-business-platform/` 可以生成这种 state 并做端到端验证。
