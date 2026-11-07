# Skill Templates

新平台 Skill 模板。当前只维护 Python 业务 Skill：

| 模板 | 适用场景 |
|---|---|
| [`business-skill-python/`](business-skill-python/) | 需要调用受保护业务平台、产出交付文件或持久状态的脚本 Skill |

所有模板都必须包含合法的 `SKILL.md`；需要平台登录态时包含 `muad.skill.json` 并用 `platforms` 声明平台 key。

## 开发规则

- 登录态只从 `session-manager get-state --skill-name <skill>` 获取；不要写死 baseUrl、Cookie、Token、CSRF。
- 交付物写 `SKILL_OUTPUT_DIR`（每次执行独立）；状态/缓存/锁写 `SKILL_DATA_DIR`（同一 user + Agent 跨执行持久）。
- Skill 包根只读，不要写包根或 `/tmp`。
- 失败写 stderr 并返回非零退出码；成功 stdout 只保留一个机器可读 JSON。
- 模型只调用 `execute_skill`；多 Phase 的旧 Skill 用一层薄分发器（`scripts/run.py --phase N`）路由，
  不要让模型在 `execute_skill` / `run_skill_script` 之间做选择。
- 平台 key（`muad.skill.json` 的 `platforms`）与 Skill 名（`SKILL.md` 的 `name`）是不同概念，不要长得像。

## 最小目录

```text
<skill-name>/
├── SKILL.md              # 必需：frontmatter + 指令
├── muad.skill.json       # 平台 key / entrypoint / author
├── scripts/              # 可选：业务脚本
└── references/           # 可选：参考资料
```

## 本地验证

- 包结构与导入校验：`validated_package` / `SkillPackage.load`（参考 `tests/skills/test_smoke_platform.py`）。
- 会话链路：`tools/fake-business-platform/` 提供 `smoke_platform` 模拟平台，`smoke_check.py` 走真实
  `MSSAdapter` 登录后执行 `skills/smoke-platform-sample`，可作为模板的端到端样例。
