---
id: harness-ui
description: Agent Harness 通用平台规则：ui
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-ui-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q tests/frontend/test_console_shell_contract.py && npm --prefix apps/console-platform/frontend run build
    cwd: .
    timeout: 900
---

# harness-ui

## Rules

- [RULE-ui-001] Console 使用 React + TypeScript + Semi Design；列表页采用“左上操作 + 右上搜索筛选 + 列表 + 右下分页”，不重复页签标题/说明块；主展示字段即详情入口；菜单固定十项：概览/Agent/Skill/MCP/模型/用户/项目平台/后台任务/定时任务/运行审计。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
