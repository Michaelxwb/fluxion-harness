---
{"version":1,"requirement":"search-skills-empty-query"}
---

# Tasks: search-skills-empty-query

## TASK-001: search_skills 空查询列出全部，校验失败不再终止 Run

- **Status**: done
- **Depends**:
- **Source**: 2026-11-21 生产故障：用户问「我有哪些技能」，模型以空 `query` 调用 `search_skills`，`SkillToolError` 逃出 handler 被判成确定性失败，Run 首轮即 `RUN_NO_PROGRESS`
- **Scenarios**:
- **Spec-Refs**: artifact/harness-skill
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py, tests/agent_runtime/test_skill_tools.py, docs/06-Skill-SDK与Egress-Boundary详细设计.md
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "skill-tools",
    "argv": ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_skill_tools.py"],
    "timeout": 300
  }
]
```

### Description

- `search_skills` 的 `query` 改为可选；省略、空串或纯空白等于列出全部生效技能，结果仍由同一个 `bounded_catalog` 收口（`truncated` / `hint` 不变）。
- 非字符串 `query` **返回** `COMMON_VALIDATION_ERROR` 错误体，不再抛 `SkillToolError`。抛出的异常会被 pipeline 按 `SkillToolError.retryable=False` 判成确定性失败，`runner._track_failure` 随即置 `no_progress`，第一次失败就终止 Run；返回错误体按既有规则可纠正。
- 工具描述与 `harness-skill` 约定同步：明确「省略 query 列出全部」，并禁止拒绝空查询。

### Checklist

- [x] `search_skills` 省略/空 `query` 返回同一预算内的全部技能
- [x] 非字符串 `query` 返回可纠正错误体，不再抛异常
- [x] 工具描述与 Skill 规范同步
- [x] 声明的测试通过

### Spec Delta

```json
[]
```

### Log

- [2026-11-21] created
- [2026-11-20T17:08:32.654Z] started tests=f4d26d22e81e

### Verification

- 尚未执行
- 2026-11-20T17:09:09.768Z PASS commands=1/1 executed=1 deduped=0 elapsed=21003ms tests=f4d26d22e81e head=b73b0e8bd139 dirty=1
  - skill-tools PASS exit=0
