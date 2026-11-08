# business-skill-python

新平台业务 Skill 的最小模板：

```text
<skill-name>/
├── SKILL.md              # 必需
├── muad.skill.json       # 平台 key / entrypoint / author
├── scripts/run.py        # 业务脚本
└── references/           # 可选
```

要点：

- 平台登录态只从 `session-manager get-state` 来；不要写死 baseUrl/Cookie/CSRF。
- 交付物写 `SKILL_OUTPUT_DIR`，状态/缓存/锁写 `SKILL_DATA_DIR`。
- 失败写 stderr + 非零退出；成功 stdout 只保留一个 JSON。
- 多 Phase 的旧 Skill 用一层薄分发器（见 `docs/operations/legacy-skill-migration.md`），
  不要让模型在 `execute_skill` / `run_skill_script` 之间做选择。
