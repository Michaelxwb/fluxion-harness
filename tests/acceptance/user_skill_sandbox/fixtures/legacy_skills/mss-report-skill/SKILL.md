---
name: mss-report-skill
description: 生成 MSSP 运营报告（最小改造后的旧 Skill）
platforms: [mssp]
---

# mss-report-skill（最小改造后）

旧 Skill 只做以下最小改造：

1. 报告写 `SKILL_OUTPUT_DIR`，不写 Skill 包根目录或 `/tmp`；
2. `cache` / `logs` / `companies` 写 `SKILL_DATA_DIR`，跨执行持久；
3. 平台地址来自 `session-manager` state 的 `requestProfile.baseUrl`；
4. 不在 Skill 内维护 Windows 计划任务或后台循环，调度交给平台。

运行方式保持历史 SKILL.md 的写法：`python scripts/run.py`（解释器参数由平台注入）。
